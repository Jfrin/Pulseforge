import os

import pytest
import pytest_asyncio

from app.store import Store


@pytest_asyncio.fixture
async def store():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set (run via docker compose test override)")
    s = Store(dsn)
    await s.open()
    await s.migrate()
    async with s.pool.connection() as conn:
        await conn.execute("TRUNCATE checkpoints, outbox, seen_records RESTART IDENTITY")
    yield s
    await s.close()
