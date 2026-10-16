"""Postgres persistence: checkpoint + dedup ledger + outbox, written in ONE transaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .dedup import Keyed, key_records
from .results import CommitResult

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
    ) -> CommitResult:
        """Advance the checkpoint, record new keys, and enqueue ONLY the new records, atomically.

        A crash can never skip a page (checkpoint moved, event lost) or replay one
        (event stored, checkpoint not moved). Re-polled or repeated records are
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
                    payload = {
                        "source": source,
                        "cursor": fetched_cursor,
                        "next_cursor": next_cursor,
                        "records": [k.record for k in fresh],
                        "keys": [{"external_id": k.external_id, "payload_hash": k.payload_hash} for k in fresh],
                    }
                    await conn.execute(
                        "INSERT INTO outbox (event_type, source, payload) VALUES ('PageFetched', %s, %s)",
                        (source, Jsonb(payload)),
                    )
        return CommitResult(new=len(fresh), duplicates=len(records) - len(fresh))
