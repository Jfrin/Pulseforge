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
        self.bronze_keys: list[str | None] = []

    async def get_cursor(self, source):
        return self.cursors.get(source)

    def _unseen(self, source, records, id_field, ignore_fields):
        keyed = key_records(records, id_field, ignore_fields)
        return [k for k in keyed if (source, k.external_id, k.payload_hash) not in self.seen]

    async def any_unseen(self, source, records, id_field="external_id", ignore_fields=()):
        return bool(self._unseen(source, records, id_field, ignore_fields))

    async def commit_page(
        self,
        source,
        *,
        fetched_cursor,
        next_cursor,
        records,
        id_field="external_id",
        bronze_key=None,
        ignore_fields=(),
    ):
        self.cursors[source] = next_cursor
        self.bronze_keys.append(bronze_key)
        fresh = self._unseen(source, records, id_field, ignore_fields)
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


class FakeBronze:
    def __init__(self, fail=False):
        self.fail = fail
        self.landed: list[tuple] = []

    async def land_page(self, source, *, fetched_cursor, page, fetched_at=None):
        if self.fail:
            raise ConnectionError("object store down")
        key = f"bronze/{len(self.landed)}"
        self.landed.append((source, fetched_cursor, len(page.records)))
        return key


async def test_every_page_is_landed_in_bronze_and_its_key_reaches_the_store():
    store, bronze = FakeStore(), FakeBronze()
    await poller(conn(), store, bronze=bronze).poll_once()
    assert bronze.landed == [("src", None, 3), ("src", "3", 3), ("src", "6", 3), ("src", "9", 1)]
    assert store.bronze_keys == ["bronze/0", "bronze/1", "bronze/2", "bronze/3"]


async def test_bronze_outage_fails_the_pass_without_advancing_the_checkpoint():
    store = FakeStore()
    with pytest.raises(ConnectionError):
        await poller(conn(), store, bronze=FakeBronze(fail=True)).poll_once()
    assert store.cursors == {} and store.events == []  # nothing committed, nothing skipped


async def test_without_a_bronze_sink_pages_still_flow():
    store = FakeStore()
    await poller(conn(), store).poll_once()
    assert ids(store) == list(range(10)) and set(store.bronze_keys) == {None}


async def test_unchanged_pages_are_not_landed_again_but_a_changed_one_is():
    store, bronze = FakeStore(), FakeBronze()
    source = conn()
    p = poller(source, store, bronze=bronze)
    await p.poll_once()
    first = len(bronze.landed)
    assert first == 4

    await p.poll_once()  # snapshot re-read, nothing changed
    assert len(bronze.landed) == first

    source._records[4]["v"] = 2  # record 4 lives on the second page
    await p.poll_once()
    assert len(bronze.landed) == first + 1 and bronze.landed[-1][1] == "3"


async def test_volatile_fields_do_not_make_a_record_new_again():
    store = FakeStore()
    source = InMemoryConnector("src", [{"id": 1, "votes": 0, "meta": {"seen": 1, "x": "a"}}], page_size=5)
    p = poller(source, store, ignore_fields=("votes", "meta.seen"))
    assert (await p.poll_once()).new == 1

    source._records[0]["votes"] = 9
    source._records[0]["meta"]["seen"] = 2
    assert (await p.poll_once()).new == 0  # only ignored fields changed

    source._records[0]["meta"]["x"] = "b"
    assert (await p.poll_once()).new == 1  # a real change is still a new version
