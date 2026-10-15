"""Postgres persistence: checkpoints + outbox, written in ONE transaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SCHEMA = Path(__file__).with_name("schema.sql").read_text()


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
    ) -> None:
        """Advance the checkpoint and enqueue the page's event atomically.

        A crash can never skip a page (checkpoint moved, event lost) or replay one
        (event stored, checkpoint not moved).
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO checkpoints (source, cursor, last_success_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (source) DO UPDATE
                    SET cursor = EXCLUDED.cursor, last_success_at = now()
                    """,
                    (source, next_cursor),
                )
                if records:
                    payload = {
                        "source": source,
                        "cursor": fetched_cursor,
                        "next_cursor": next_cursor,
                        "records": records,
                    }
                    await conn.execute(
                        "INSERT INTO outbox (event_type, source, payload) VALUES ('PageFetched', %s, %s)",
                        (source, Jsonb(payload)),
                    )
