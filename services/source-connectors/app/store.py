"""Postgres persistence: checkpoint + dedup ledger + outbox, written in ONE transaction."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .dedup import Keyed, key_records
from .results import Batch, CommitResult

SCHEMA = Path(__file__).with_name("schema.sql").read_text()

CHECKPOINT_UPSERT = """
INSERT INTO checkpoints (source, cursor, last_success_at)
VALUES (%s, %s, now())
ON CONFLICT (source) DO UPDATE
SET cursor = EXCLUDED.cursor, last_success_at = now()
"""

# Inserts only keys never seen before and returns exactly those: the idempotent upsert.
LEDGER_INSERT = """
INSERT INTO seen_records (source, external_id, payload_hash)
SELECT %s, t.e, t.h FROM unnest(%s::text[], %s::text[]) AS t(e, h)
ON CONFLICT DO NOTHING
RETURNING external_id, payload_hash
"""


PENDING_INSERT = """
INSERT INTO pending_records (source, external_id, payload_hash, payload, bronze_key)
VALUES (%s, %s, %s, %s, %s)
"""

PENDING_SELECT = """
SELECT id, external_id, payload_hash, payload,
       EXTRACT(EPOCH FROM (now() - created_at))::float8 AS age_s, bronze_key
FROM pending_records
WHERE source = %s
ORDER BY id
LIMIT %s
FOR UPDATE SKIP LOCKED
"""


class Store:
    def __init__(self, dsn: str, max_size: int = 4) -> None:
        self.pool = AsyncConnectionPool(dsn, min_size=1, max_size=max_size, open=False)

    async def open(self) -> None:
        await self.pool.open()
        await self.pool.wait()

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA)

    async def get_cursor(self, source: str) -> str | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT cursor FROM checkpoints WHERE source = %s", (source,))
            row = await cur.fetchone()
        return row[0] if row else None

    async def commit_page(
        self,
        source: str,
        *,
        fetched_cursor: str | None,
        next_cursor: str | None,
        records: list[dict[str, Any]],
        id_field: str = "external_id",
        bronze_key: str | None = None,
    ) -> CommitResult:
        """Advance the checkpoint, record new keys, and stage ONLY the new records, atomically.

        A crash can never skip a page (checkpoint moved, records lost) or replay one
        (records staged, checkpoint not moved). Re-polled or repeated records are
        filtered out by the ledger, so downstream never sees them twice.
        """
        keyed = key_records(records, id_field)
        fresh: list[Keyed] = []
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(CHECKPOINT_UPSERT, (source, next_cursor))
                if keyed:
                    cur = await conn.execute(
                        LEDGER_INSERT,
                        (source, [k.external_id for k in keyed], [k.payload_hash for k in keyed]),
                    )
                    inserted = {(e, h) for e, h in await cur.fetchall()}
                    fresh = [k for k in keyed if (k.external_id, k.payload_hash) in inserted]
                if fresh:
                    async with conn.cursor() as cur:
                        await cur.executemany(
                            PENDING_INSERT,
                            [(source, k.external_id, k.payload_hash, Jsonb(k.record), bronze_key) for k in fresh],
                        )
        return CommitResult(new=len(fresh), duplicates=len(records) - len(fresh))

    async def flush_batch(self, source: str, *, max_size: int, max_age_s: float) -> Batch | None:
        """Cut one batch from the staged records if a trigger fires, else return None.

        Triggers: `size` (max_size records waiting) or `time` (oldest waiting record is at least
        max_age_s old). Staged rows are removed, the batch is recorded and the BatchCreated event
        is written to the outbox in ONE transaction. Concurrent flushers skip locked rows.
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(PENDING_SELECT, (source, max_size))
                rows = await cur.fetchall()
                if not rows:
                    return None
                if len(rows) >= max_size:
                    trigger = "size"
                elif rows[0][4] >= max_age_s:
                    trigger = "time"
                else:
                    return None

                batch_id = str(uuid.uuid4())
                await conn.execute("DELETE FROM pending_records WHERE id = ANY(%s)", ([r[0] for r in rows],))
                await conn.execute(
                    "INSERT INTO batches (batch_id, source, record_count, trigger) VALUES (%s, %s, %s, %s)",
                    (batch_id, source, len(rows), trigger),
                )
                payload = {
                    "batch_id": batch_id,
                    "source": source,
                    "trigger": trigger,
                    "record_count": len(rows),
                    "records": [r[3] for r in rows],
                    "keys": [{"external_id": r[1], "payload_hash": r[2], "bronze_key": r[5]} for r in rows],
                    "bronze_keys": sorted({r[5] for r in rows if r[5]}),
                }
                await conn.execute(
                    "INSERT INTO outbox (event_type, source, payload) VALUES ('BatchCreated', %s, %s)",
                    (source, Jsonb(payload)),
                )
        return Batch(batch_id=batch_id, source=source, trigger=trigger, record_count=len(rows))
