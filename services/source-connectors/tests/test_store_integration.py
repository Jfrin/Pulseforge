"""Needs real Postgres (TEST_DATABASE_URL). Provided by the compose test override."""

import psycopg
import pytest

from app.batcher import Batcher
from app.connectors import InMemoryConnector
from app.poller import Poller


async def rows(store, sql):
    async with store.pool.connection() as conn:
        cur = await conn.execute(sql)
        return await cur.fetchall()


async def commit(store, records, *, fetched=None, nxt="3"):
    return await store.commit_page("s", fetched_cursor=fetched, next_cursor=nxt, records=records, id_field="id")


# ---------------- checkpoint + ledger + staging ----------------


async def test_checkpoint_ledger_and_staging_commit_together(store):
    res = await commit(store, [{"id": 1}])
    assert res.new == 1 and res.duplicates == 0
    assert await store.get_cursor("s") == "3"
    assert len(await rows(store, "SELECT 1 FROM seen_records")) == 1
    pending = await rows(store, "SELECT external_id, payload FROM pending_records")
    assert pending == [("1", {"id": 1})]
    assert await rows(store, "SELECT 1 FROM outbox") == []  # events only appear when a batch is cut


async def test_same_record_again_is_a_noop(store):
    await commit(store, [{"id": 1, "v": 1}])
    res = await commit(store, [{"id": 1, "v": 1}], fetched="3", nxt="6")
    assert res.new == 0 and res.duplicates == 1
    assert len(await rows(store, "SELECT 1 FROM pending_records")) == 1
    assert len(await rows(store, "SELECT 1 FROM seen_records")) == 1
    assert await store.get_cursor("s") == "6"  # checkpoint still advances


async def test_changed_payload_with_same_id_is_a_new_version(store):
    await commit(store, [{"id": 1, "v": 1}])
    res = await commit(store, [{"id": 1, "v": 2}], fetched="3", nxt="6")
    assert res.new == 1
    assert len(await rows(store, "SELECT 1 FROM seen_records")) == 2
    assert len(await rows(store, "SELECT 1 FROM pending_records")) == 2


async def test_repeat_inside_one_page_counts_as_duplicate(store):
    res = await commit(store, [{"id": 1}, {"id": 1}])
    assert res.new == 1 and res.duplicates == 1


async def test_same_id_in_different_sources_is_independent(store):
    await store.commit_page("a", fetched_cursor=None, next_cursor=None, records=[{"id": 1}], id_field="id")
    res = await store.commit_page("b", fetched_cursor=None, next_cursor=None, records=[{"id": 1}], id_field="id")
    assert res.new == 1


async def test_empty_page_updates_checkpoint_but_stages_nothing(store):
    await commit(store, [], fetched="3", nxt="3")
    assert await store.get_cursor("s") == "3"
    assert await rows(store, "SELECT 1 FROM pending_records") == []
    assert await rows(store, "SELECT 1 FROM seen_records") == []


async def test_failed_staging_rolls_back_checkpoint_and_ledger(store):
    await commit(store, [{"id": 1}])
    with pytest.raises(psycopg.Error):  # NaN is accepted by json.dumps but rejected by Postgres JSONB
        await commit(store, [{"id": 2, "x": float("nan")}], fetched="3", nxt="6")
    assert await store.get_cursor("s") == "3"  # checkpoint did NOT move
    assert len(await rows(store, "SELECT 1 FROM seen_records")) == 1  # ledger rolled back too
    assert len(await rows(store, "SELECT 1 FROM pending_records")) == 1  # and nothing half-staged


async def test_null_cursor_is_stored_and_read_back_as_none(store):
    await commit(store, [], fetched="9", nxt=None)
    assert await store.get_cursor("s") is None


async def test_any_unseen_reflects_the_ledger(store):
    recs = [{"id": 1}, {"id": 2}]
    assert await store.any_unseen("s", recs, id_field="id") is True
    await commit(store, recs)
    assert await store.any_unseen("s", recs, id_field="id") is False
    assert await store.any_unseen("s", [*recs, {"id": 3}], id_field="id") is True
    assert await store.any_unseen("s", [{"id": 1, "v": 2}], id_field="id") is True  # changed payload = new version
    assert await store.any_unseen("s", [], id_field="id") is False
    assert await store.any_unseen("other", recs, id_field="id") is True  # the ledger is per source


async def test_ignored_fields_make_a_churned_record_a_duplicate(store):
    ign = ("votes",)
    first = {"id": 1, "votes": 1}
    await store.commit_page(
        "s", fetched_cursor=None, next_cursor=None, records=[first], id_field="id", ignore_fields=ign
    )
    res = await store.commit_page(
        "s", fetched_cursor=None, next_cursor=None, records=[{"id": 1, "votes": 9}], id_field="id", ignore_fields=ign
    )
    assert res.new == 0 and res.duplicates == 1
    assert await store.any_unseen("s", [{"id": 1, "votes": 42}], id_field="id", ignore_fields=ign) is False
    assert await store.any_unseen("s", [{"id": 1, "votes": 42}], id_field="id") is True


# ---------------- micro-batching ----------------


async def test_nothing_is_cut_while_below_size_and_young(store):
    await commit(store, [{"id": i} for i in range(3)])
    assert await store.flush_batch("s", max_size=500, max_age_s=3600) is None
    assert len(await rows(store, "SELECT 1 FROM pending_records")) == 3
    assert await rows(store, "SELECT 1 FROM outbox") == []


async def test_size_trigger_cuts_a_batch_and_emits_batch_created(store):
    await commit(store, [{"id": i} for i in range(5)])
    batch = await store.flush_batch("s", max_size=5, max_age_s=3600)
    assert batch is not None and batch.trigger == "size" and batch.record_count == 5
    assert await rows(store, "SELECT 1 FROM pending_records") == []
    ((event_type, source, payload),) = await rows(store, "SELECT event_type, source, payload FROM outbox")
    assert (event_type, source) == ("BatchCreated", "s")
    assert payload["batch_id"] == batch.batch_id and payload["trigger"] == "size"
    assert [r["id"] for r in payload["records"]] == [0, 1, 2, 3, 4]  # order preserved
    assert len(payload["keys"]) == 5
    assert len(await rows(store, "SELECT 1 FROM batches")) == 1


async def test_time_trigger_cuts_a_partial_batch_once_the_oldest_record_is_old_enough(store):
    await commit(store, [{"id": i} for i in range(3)])
    assert await store.flush_batch("s", max_size=500, max_age_s=3600) is None
    async with store.pool.connection() as conn:
        await conn.execute("UPDATE pending_records SET created_at = now() - interval '10 minutes'")
    batch = await store.flush_batch("s", max_size=500, max_age_s=300)
    assert batch is not None and batch.trigger == "time" and batch.record_count == 3


async def test_a_batch_never_exceeds_max_size_and_the_rest_stays_staged(store):
    await commit(store, [{"id": i} for i in range(7)])
    batch = await store.flush_batch("s", max_size=5, max_age_s=3600)
    assert batch is not None and batch.record_count == 5
    assert len(await rows(store, "SELECT 1 FROM pending_records")) == 2


async def test_batches_are_per_source(store):
    await store.commit_page("a", fetched_cursor=None, next_cursor=None, records=[{"id": 1}], id_field="id")
    await store.commit_page("b", fetched_cursor=None, next_cursor=None, records=[{"id": 1}], id_field="id")
    batch = await store.flush_batch("a", max_size=1, max_age_s=3600)
    assert batch is not None and batch.source == "a"
    assert len(await rows(store, "SELECT 1 FROM pending_records WHERE source = 'b'")) == 1


async def test_batcher_drains_a_backlog_into_size_batches_then_a_time_batch(store):
    await commit(store, [{"id": i} for i in range(12)])
    out = await Batcher(store, ["s"], max_size=5, max_age_s=0).flush_all_due()
    assert [(b.trigger, b.record_count) for b in out] == [("size", 5), ("size", 5), ("time", 2)]
    assert await rows(store, "SELECT 1 FROM pending_records") == []
    assert len(await rows(store, "SELECT 1 FROM outbox")) == 3


async def test_bronze_key_travels_from_staging_into_the_batch_event(store):
    x, y = "bronze/s/x.json", "bronze/s/y.json"
    await store.commit_page(
        "s", fetched_cursor=None, next_cursor="2", records=[{"id": 1}, {"id": 2}], id_field="id", bronze_key=x
    )
    await store.commit_page("s", fetched_cursor="2", next_cursor="3", records=[{"id": 3}], id_field="id", bronze_key=y)
    batch = await store.flush_batch("s", max_size=3, max_age_s=3600)
    assert batch is not None
    ((payload,),) = await rows(store, "SELECT payload FROM outbox")
    assert payload["bronze_keys"] == [x, y]
    assert [k["bronze_key"] for k in payload["keys"]] == [x, x, y]


async def test_records_without_a_bronze_key_give_an_empty_lineage_list(store):
    await commit(store, [{"id": 1}])
    await store.flush_batch("s", max_size=1, max_age_s=3600)
    ((payload,),) = await rows(store, "SELECT payload FROM outbox")
    assert payload["bronze_keys"] == [] and payload["keys"][0]["bronze_key"] is None


# ---------------- poller end to end ----------------


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
    staged = await rows(store, "SELECT payload FROM pending_records ORDER BY id")
    assert [p["id"] for (p,) in staged] == list(range(10))

    again = await Poller(flaky, store, id_field="id").poll_once()  # snapshot restarts from the top
    assert again.new == 0 and again.duplicates == 10
    assert len(await rows(store, "SELECT 1 FROM pending_records")) == 10

    out = await Batcher(store, ["src"], max_size=4, max_age_s=0).flush_all_due()
    assert sum(b.record_count for b in out) == 10
