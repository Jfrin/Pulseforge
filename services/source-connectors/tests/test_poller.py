import asyncio

import pytest

from app.connectors import InMemoryConnector
from app.dedup import key_records
from app.poller import Poller
from app.results import CommitResult


class FakeStore:
    """In-memory stand-in with the same dedup semantics as the Postgres store."""

    def __init__(self):
        self.cursors: dict[str, str | None] = {}
        self.events: list[dict] = []
        self.seen: set[tuple[str, str, str]] = set()

    async def get_cursor(self, source):
        return self.cursors.get(source)

    async def commit_page(self, source, *, fetched_cursor, next_cursor, records, id_field="external_id"):
        self.cursors[source] = next_cursor
        fresh = [k for k in key_records(records, id_field) if (source, k.external_id, k.payload_hash) not in self.seen]
        self.seen.update((source, k.external_id, k.payload_hash) for k in fresh)
        if fresh:
            self.events.append({"cursor": fetched_cursor, "records": [k.record for k in fresh]})
        return CommitResult(new=len(fresh), duplicates=len(records) - len(fresh))


def conn(n=10, page_size=3, **kw):
    return InMemoryConnector("src", [{"id": i} for i in range(n)], page_size=page_size, **kw)


def poller(connector, store, **kw):
    return Poller(connector, store, id_field="id", **kw)


def ids(store):
    return [r["id"] for e in store.events for r in e["records"]]


async def test_full_pass_emits_every_record_once_and_resets_snapshot_checkpoint():
    store = FakeStore()
    res = await poller(conn(), store).poll_once()
    assert res.new == 10 and res.duplicates == 0 and ids(store) == list(range(10))
    assert store.cursors["src"] is None  # snapshot finished: next pass starts over


async def test_repolling_an_unchanged_snapshot_emits_nothing():
    store = FakeStore()
    p = poller(conn(), store)
    await p.poll_once()
    second = await p.poll_once()
    assert second.new == 0 and second.duplicates == 10
    assert ids(store) == list(range(10))  # no new events


async def test_changed_record_is_emitted_again_as_a_new_version():
    store = FakeStore()
    source = conn()
    p = poller(source, store)
    await p.poll_once()
    source._records[3]["v"] = 2
    res = await p.poll_once()
    assert res.new == 1 and res.duplicates == 9


async def test_resumes_from_saved_checkpoint():
    store = FakeStore()
    store.cursors["src"] = "6"
    await poller(conn(), store).poll_once()
    assert ids(store) == [6, 7, 8, 9]


class FailsOnThirdFetch:
    name = "src"

    def __init__(self, inner):
        self.inner, self.calls, self.fail = inner, 0, True

    async def fetch(self, cursor):
        self.calls += 1
        if self.fail and self.calls == 3:
            raise ConnectionError("boom")
        return await self.inner.fetch(cursor)


async def test_failure_mid_pass_keeps_checkpoint_at_failed_page_then_completes():
    store = FakeStore()
    flaky = FailsOnThirdFetch(conn())
    p = poller(flaky, store)
    with pytest.raises(ConnectionError):
        await p.poll_once()
    assert store.cursors["src"] == "6" and ids(store) == [0, 1, 2, 3, 4, 5]

    flaky.fail = False
    await p.poll_once()
    assert ids(store) == list(range(10))  # nothing skipped, nothing duplicated


async def test_incremental_feed_keeps_resume_token_and_emits_nothing_when_caught_up():
    store = FakeStore()
    feed = conn(resume_token_at_end=True)
    p = poller(feed, store)
    await p.poll_once()
    assert store.cursors["src"] == "10"
    assert (await p.poll_once()).new == 0
    assert len(store.events) == 4  # 3+3+3+1 records; the empty pass added no event

    feed._records.extend({"id": i} for i in range(10, 13))  # new records appear upstream
    await p.poll_once()
    assert ids(store) == list(range(13))


async def test_run_forever_retries_after_error_and_stops_on_event():
    store = FakeStore()
    flaky = FailsOnThirdFetch(conn())
    p = poller(flaky, store, poll_interval_s=0.01, retry_delay_s=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(p.run_forever(stop))
    await asyncio.sleep(0.05)
    flaky.fail = False
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert set(ids(store)) == set(range(10))
