import copy
import os

import pytest
import pytest_asyncio
from pulseforge.db import Database, Transaction


class MemoryTransaction:
    def __init__(self):
        self.state = {}
        self.events = []
        self.audit_rows = []

    async def get(self, namespace, key, default=None):
        return copy.deepcopy(self.state.get((namespace, key), default))

    async def put(self, namespace, key, value):
        self.state[namespace, key] = copy.deepcopy(value)

    async def all(self, namespace):
        return {k: copy.deepcopy(v) for (ns, k), v in self.state.items() if ns == namespace}

    async def emit(self, kind, payload):
        self.events.append((kind, copy.deepcopy(payload)))

    async def audit(self, record_id, decision):
        self.audit_rows.append((record_id, copy.deepcopy(decision)))
        return len(self.audit_rows)


@pytest.fixture
def memory():
    return MemoryTransaction()


@pytest_asyncio.fixture
async def db():
    database = Database(os.environ["TEST_DATABASE_URL"])
    await database.pool.open()
    async with database.pool.connection() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS core_tests")
    await database.pool.close()
    database = Database(os.environ["TEST_DATABASE_URL"] + "?options=-csearch_path%3Dcore_tests")
    await database.open()
    async with database.pool.connection() as conn:
        await conn.execute("TRUNCATE state,consumed,outbox,failures,dead_letters RESTART IDENTITY")
    yield database
    await database.pool.close()


@pytest_asyncio.fixture
async def tx(db):
    async with db.pool.connection() as conn, conn.transaction():
        yield Transaction(conn, "test")
