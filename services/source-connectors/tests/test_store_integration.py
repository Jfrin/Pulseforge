"""Needs real Postgres (TEST_DATABASE_URL). Provided by the compose test override."""

import pytest

from app.connectors import InMemoryConnector
from app.poller import Poller


async def rows(store, sql):
    async with store.pool.connection() as conn:
        cur = await conn.execute(sql)
        return await cur.fetchall()


async def test_checkpoint_and_outbox_commit_together(store):
    await store.commit_page("s", fetched_cursor=None, next_cursor="3", records=[{"id": 1}])
    assert await store.get_cursor("s") == "3"
    out = await rows(store, "SELECT event_type, source, payload FROM outbox")
    assert len(out) == 1 and out[0][0] == "PageFetched" and out[0][2]["records"] == [{"id": 1}]


async def test_empty_page_updates_checkpoint_but_writes_no_event(store):
    await store.commit_page("s", fetched_cursor="3", next_cursor="3", records=[])
    assert await store.get_cursor("s") == "3"
    assert await rows(store, "SELECT 1 FROM outbox") == []


async def test_failure_after_checkpoint_write_rolls_the_checkpoint_back(store):
    await store.commit_page("s", fetched_cursor=None, next_cursor="3", records=[{"id": 1}])
    with pytest.raises(TypeError):  # un-serialisable record makes the outbox insert fail
        await store.commit_page("s", fetched_cursor="3", next_cursor="6", records=[{"id": object()}])
    assert await store.get_cursor("s") == "3"  # checkpoint did NOT move
    assert len(await rows(store, "SELECT 1 FROM outbox")) == 1  # and no half-written event


async def test_null_cursor_is_stored_and_read_back_as_none(store):
    await store.commit_page("s", fetched_cursor="9", next_cursor=None, records=[])
    assert await store.get_cursor("s") is None


class FailsOnThirdFetch:
    name = "src"

    def __init__(self, inner):
        self.inner, self.calls, self.fail = inner, 0, True

    async def fetch(self, cursor):
        self.calls += 1
        if self.fail and self.calls == 3:
            raise ConnectionError("boom")
        return await self.inner.fetch(cursor)


async def test_crash_mid_pass_then_restart_delivers_every_record_exactly_once(store):
    inner = InMemoryConnector("src", [{"id": i} for i in range(10)], page_size=3)
    flaky = FailsOnThirdFetch(inner)
    with pytest.raises(ConnectionError):
        await Poller(flaky, store).poll_once()
    assert await store.get_cursor("src") == "6"

    flaky.fail = False
    await Poller(flaky, store).poll_once()  # a "restarted" poller reading the same DB
    out = await rows(store, "SELECT payload FROM outbox ORDER BY id")
    got = [r["id"] for (p,) in out for r in p["records"]]
    assert got == list(range(10))
