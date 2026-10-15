import asyncio

import pytest

from app.connectors import InMemoryConnector
from app.poller import Poller


class FakeStore:
    def __init__(self):
        self.cursors: dict[str, str | None] = {}
        self.events: list[dict] = []

    async def get_cursor(self, source):
        return self.cursors.get(source)

    async def commit_page(self, source, *, fetched_cursor, next_cursor, records):
        self.cursors[source] = next_cursor
        if records:
            self.events.append({"cursor": fetched_cursor, "records": records})


def conn(n=10, page_size=3, **kw):
    return InMemoryConnector("src", [{"id": i} for i in range(n)], page_size=page_size, **kw)


def ids(store):
    return [r["id"] for e in store.events for r in e["records"]]


async def test_full_pass_emits_every_record_once_and_resets_snapshot_checkpoint():
    store = FakeStore()
    n = await Poller(conn(), store).poll_once()
    assert n == 10 and ids(store) == list(range(10))
    assert store.cursors["src"] is None  # snapshot finished: next pass starts over


async def test_resumes_from_saved_checkpoint():
    store = FakeStore()
    store.cursors["src"] = "6"
    await Poller(conn(), store).poll_once()
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
    poller = Poller(flaky, store)
    with pytest.raises(ConnectionError):
        await poller.poll_once()
    assert store.cursors["src"] == "6" and ids(store) == [0, 1, 2, 3, 4, 5]

    flaky.fail = False
    await poller.poll_once()
    assert ids(store) == list(range(10))  # nothing skipped, nothing duplicated


async def test_incremental_feed_keeps_resume_token_and_emits_nothing_when_caught_up():
    store = FakeStore()
    feed = conn(resume_token_at_end=True)
    poller = Poller(feed, store)
    await poller.poll_once()
    assert store.cursors["src"] == "10"
    assert await poller.poll_once() == 0
    assert len(store.events) == 4  # 3+3+3+1 records; the empty pass added no event

    feed._records.extend({"id": i} for i in range(10, 13))  # new records appear upstream
    await poller.poll_once()
    assert ids(store) == list(range(13))


async def test_run_forever_retries_after_error_and_stops_on_event():
    store = FakeStore()
    flaky = FailsOnThirdFetch(conn())
    poller = Poller(flaky, store, poll_interval_s=0.01, retry_delay_s=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(poller.run_forever(stop))
    await asyncio.sleep(0.05)
    flaky.fail = False
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert set(ids(store)) == set(range(10))
