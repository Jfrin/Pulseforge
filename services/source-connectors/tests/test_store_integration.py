"""Needs real Postgres (TEST_DATABASE_URL). Provided by the compose test override."""

import psycopg
import pytest

from app.connectors import InMemoryConnector
from app.poller import Poller


async def rows(store, sql):
    async with store.pool.connection() as conn:
        cur = await conn.execute(sql)
        return await cur.fetchall()


async def commit(store, records, *, fetched=None, nxt="3"):
    return await store.commit_page("s", fetched_cursor=fetched, next_cursor=nxt, records=records, id_field="id")


async def test_checkpoint_ledger_and_outbox_commit_together(store):
    res = await commit(store, [{"id": 1}])
    assert res.new == 1 and res.duplicates == 0
    assert await store.get_cursor("s") == "3"
    out = await rows(store, "SELECT event_type, source, payload FROM outbox")
    assert len(out) == 1 and out[0][0] == "PageFetched"
    assert out[0][2]["records"] == [{"id": 1}]
    assert out[0][2]["keys"][0]["external_id"] == "1"
    assert len(await rows(store, "SELECT 1 FROM seen_records")) == 1


async def test_same_record_again_is_a_noop(store):
    await commit(store, [{"id": 1, "v": 1}])
    res = await commit(store, [{"id": 1, "v": 1}], fetched="3", nxt="6")
    assert res.new == 0 and res.duplicates == 1
    assert len(await rows(store, "SELECT 1 FROM outbox")) == 1  # no second event
    assert len(await rows(store, "SELECT 1 FROM seen_records")) == 1
    assert await store.get_cursor("s") == "6"  # checkpoint still advances


async def test_changed_payload_with_same_id_is_a_new_version(store):
    await commit(store, [{"id": 1, "v": 1}])
    res = await commit(store, [{"id": 1, "v": 2}], fetched="3", nxt="6")
    assert res.new == 1
    assert len(await rows(store, "SELECT 1 FROM seen_records")) == 2
    assert len(await rows(store, "SELECT 1 FROM outbox")) == 2


async def test_repeat_inside_one_page_counts_as_duplicate(store):
    res = await commit(store, [{"id": 1}, {"id": 1}])
    assert res.new == 1 and res.duplicates == 1


async def test_same_id_in_different_sources_is_independent(store):
    await store.commit_page("a", fetched_cursor=None, next_cursor=None, records=[{"id": 1}], id_field="id")
    res = await store.commit_page("b", fetched_cursor=None, next_cursor=None, records=[{"id": 1}], id_field="id")
    assert res.new == 1


async def test_empty_page_updates_checkpoint_but_writes_no_event(store):
    await commit(store, [], fetched="3", nxt="3")
    assert await store.get_cursor("s") == "3"
    assert await rows(store, "SELECT 1 FROM outbox") == []
    assert await rows(store, "SELECT 1 FROM seen_records") == []


async def test_failed_outbox_write_rolls_back_checkpoint_and_ledger(store):
    await commit(store, [{"id": 1}])
    with pytest.raises(psycopg.Error):  # NaN is accepted by json.dumps but rejected by Postgres JSONB
        await commit(store, [{"id": 2, "x": float("nan")}], fetched="3", nxt="6")
    assert await store.get_cursor("s") == "3"  # checkpoint did NOT move
    assert len(await rows(store, "SELECT 1 FROM seen_records")) == 1  # ledger rolled back too
    assert len(await rows(store, "SELECT 1 FROM outbox")) == 1  # and no half-written event


async def test_null_cursor_is_stored_and_read_back_as_none(store):
    await commit(store, [], fetched="9", nxt=None)
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
        await Poller(flaky, store, id_field="id").poll_once()
    assert await store.get_cursor("src") == "6"

    flaky.fail = False
    await Poller(flaky, store, id_field="id").poll_once()  # a "restarted" poller reading the same DB
    out = await rows(store, "SELECT payload FROM outbox ORDER BY id")
    got = [r["id"] for (p,) in out for r in p["records"]]
    assert got == list(range(10))

    again = await Poller(flaky, store, id_field="id").poll_once()  # snapshot restarts from the top
    assert again.new == 0 and again.duplicates == 10
    assert len(await rows(store, "SELECT 1 FROM outbox")) == len(out)
