"""Durable inbox for pushed records (webhooks).

The receiver only has to verify, store, and answer 202. A normal poller then drains the inbox
through InboxConnector, so pushed data gets the same checkpoints, bronze landing, dedup and
batching as polled data.

Appends are serialised with an advisory lock: sequence numbers are then assigned in commit order,
so a reader that has seen seq N can never miss a row with a lower seq that commits later.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_inbox (
    seq         BIGSERIAL   PRIMARY KEY,
    source      TEXT        NOT NULL,
    payload     JSONB       NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS webhook_inbox_source_seq ON webhook_inbox (source, seq);
"""

APPEND_LOCK_KEY = 7_463_001


class Inbox:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def migrate(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(SCHEMA)

    async def append(self, source: str, records: list[dict[str, Any]]) -> int:
        """Durably store records (all or nothing). Returns how many were stored."""
        if not records:
            return 0
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(%s)", (APPEND_LOCK_KEY,))
                async with conn.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO webhook_inbox (source, payload) VALUES (%s, %s)",
                        [(source, Jsonb(r)) for r in records],
                    )
        return len(records)

    async def read(self, source: str, after_seq: int, limit: int) -> list[tuple[int, dict[str, Any]]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT seq, payload FROM webhook_inbox WHERE source = %s AND seq > %s ORDER BY seq LIMIT %s",
                (source, after_seq, limit),
            )
            rows = await cur.fetchall()
        return [(int(seq), payload) for seq, payload in rows]
