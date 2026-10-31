import copy
import io
import json
from datetime import UTC, datetime, timedelta

import pyarrow.parquet as pq
import pytest
from pulseforge import analytics, governance
from pulseforge.events import Event
from pulseforge.exports import encode, gold_rows
from pulseforge.scoring import review_stats, score

from app.handler import handle as process

NOW = datetime(2026, 10, 31, tzinfo=UTC)


def review(n, *, age=1, positive=False):
    return {
        "source": "mock-reviews",
        "review_id": str(n),
        "product_id": "p1",
        "reviewer": "p_secret",
        "created_at": (NOW - timedelta(hours=age)).isoformat(),
        "voted_up": positive,
    }


def product(reviews=None):
    return {
        "product_id": "p1",
        "source": "mock-games",
        "external_id": "g1",
        "title": "Example",
        "reviews": reviews or [],
        "lineage": {"batch_id": "b1", "bronze_key": "bronze/mock-games/test.json"},
    }


POLICY = {
    "version": "1",
    "sources": {
        "mock-games": {"allow": True, "retention_days": 30},
        "mock-reviews": {"allow": True, "retention_days": 30},
    },
}


def test_stacked_score_uses_baseline_and_all_signals():
    reviews = [review(i, age=25, positive=True) for i in range(100)]
    reviews += [review(i + 100) for i in range(400)]
    result = score(
        review_stats(reviews, NOW),
        {"player_count": 0, "price_cents": 500, "patch_event": True},
        {"player_count": 100, "price_cents": 1000},
    )
    assert result["controversy_score"] == 100
    assert all(value == 1 for value in result["signals"].values())
    assert score(review_stats([], NOW), {}, {})["controversy_score"] == 0


@pytest.mark.parametrize("count,negative,expected", [(99, 99, False), (100, 69, False), (100, 70, True)])
def test_detector_threshold_and_minimum(count, negative, expected):
    reviews = [review(i, positive=i >= negative) for i in range(count)]
    assert bool(analytics.detect(reviews, NOW)) is expected


def test_detector_window_baseline_cooldown_and_future():
    reviews = [review(i) for i in range(100)] + [review(101, age=25, positive=True), review(102, age=-1)]
    found = analytics.detect(reviews, NOW)
    assert found["review_count"] == 100 and found["baseline_share"] == 0
    assert analytics.detect(reviews, NOW, last_alert=(NOW - timedelta(hours=11)).isoformat()) is None
    assert analytics.detect(reviews, NOW, last_alert=(NOW - timedelta(hours=12)).isoformat())
    assert analytics.detect([review(i, age=24) for i in range(100)], NOW) is None


def test_governance_retention_privacy_and_tombstones():
    original = product([review(1), review(2, age=31 * 24)])
    checked = governance.govern(original, POLICY, NOW)
    assert len(checked["reviews"]) == 1
    assert "reviewer" not in checked["reviews"][0]
    assert checked["review_tombstones"][0]["reason"] == "retention_expired"
    assert original["reviews"][0]["reviewer"] == "p_secret"
    policy = {**POLICY, "suppression": ["p1"]}
    blocked = governance.govern(original, policy, NOW)
    assert blocked["governance"]["tombstone"] and not blocked["reviews"]
    assert "title" not in blocked and blocked["lineage"] == original["lineage"]
    assert governance.govern(original, {"version": "1"}, NOW)["governance"]["tombstone"]


def test_adult_and_erasure_flags():
    raw = {**product(), "adult_content": True}
    assert "adult_content" in governance.govern(raw, POLICY, NOW)["governance"]["flags"]
    checked = governance.govern(raw, {**POLICY, "erasure": ["p1"]}, NOW)
    assert checked["governance"]["tombstone"] and "title" not in checked


@pytest.mark.asyncio
async def test_scheduled_suppression_recheck_and_audit(memory, monkeypatch):
    policy = copy.deepcopy(POLICY)
    monkeypatch.setattr(governance, "load_policy", lambda: policy)
    await governance.check_product(memory, product(), policy, NOW)
    policy["suppression"] = ["p1"]
    await governance.sweep(memory, now=NOW + timedelta(days=29))
    assert len(memory.audit_rows) == 1
    await governance.sweep(memory, now=NOW + timedelta(days=30))
    assert len(memory.audit_rows) == 2
    assert memory.events[-1][0] == "GovernanceChecked"
    assert memory.events[-1][1]["products"][0]["governance"]["tombstone"]


@pytest.mark.asyncio
async def test_later_reviewer_suppression_removes_existing_review(memory, monkeypatch):
    policy = copy.deepcopy(POLICY)
    monkeypatch.setattr(governance, "load_policy", lambda: policy)
    await governance.check_product(memory, product([review(1)]), policy, NOW)
    assert "reviewer" not in memory.state["records", "p1"]["reviews"][0]
    assert memory.state["policy_inputs", "p1"]["reviews"][0]["reviewer"] == "p_secret"

    policy.update(version="2", suppression=["p_secret"])
    await governance.sweep(memory, now=NOW + timedelta(hours=1))
    checked = memory.state["records", "p1"]
    assert checked["review_count"] == 0
    assert checked["review_tombstones"] == [
        {"review_id": "1", "source": "mock-reviews", "reason": "reviewer_suppressed"}
    ]
    assert memory.state["policy_inputs", "p1"]["reviews"] == []
    assert "p_secret" not in json.dumps(memory.events)


def test_gold_csv_parquet_are_aggregate_only_and_have_lineage():
    checked = governance.govern(product([review(1)]), POLICY, NOW)
    checked["governance"]["audit_id"] = 17
    checked["review_text"] = "must never export this"
    rows = gold_rows({"batch_id": "batch", "products": [checked]})
    csv, parquet = encode(rows)
    assert "reviewer" not in csv.decode() and "must never export" not in csv.decode()
    restored = pq.read_table(io.BytesIO(parquet)).to_pylist()
    assert restored == rows
    assert restored[0]["audit_id"] == 17
    assert json.loads(restored[0]["lineage"])["record"]["bronze_key"].startswith("bronze/")
    assert pq.read_table(io.BytesIO(encode([])[1])).num_rows == 0


@pytest.mark.asyncio
async def test_processing_persists_resolution_across_batches_and_restart(tx, monkeypatch):
    monkeypatch.setenv("PSEUDONYM_SALT", "test-salt-at-least-sixteen-bytes")

    async def send(batch, records):
        await process(
            tx,
            Event(
                type="BatchCreated",
                source="mock-games",
                payload={
                    "batch_id": batch,
                    "source": "mock-games",
                    "records": records,
                    "keys": [
                        {"external_id": r["external_id"], "payload_hash": batch, "bronze_key": "bronze/test"}
                        for r in records
                    ],
                },
            ),
        )

    await send("one", [{"external_id": "a", "title": "Space Journey", "developer": "Studio"}])
    await send("two", [{"external_id": "b", "title": "Space Journey Deluxe Edition", "developer": "Studio"}])
    products = await tx.all("products")
    assert len(products) == 1
    assert len(next(iter(products.values()))["members"]) == 2
    cur = await tx.conn.execute("SELECT event_type FROM outbox")
    assert await cur.fetchall() == [("ProductsScored",), ("ProductsScored",)]


@pytest.mark.asyncio
async def test_analytics_dedup_cooldown_and_source_anomalies(memory, monkeypatch):
    monkeypatch.setattr(analytics, "utcnow", lambda: NOW)
    p = governance.govern(product([review(i) for i in range(100)]), POLICY, NOW)
    event = Event(type="GovernanceChecked", source="governance", payload={"batch_id": "b1", "products": [p]})
    await analytics.handle(memory, event)
    await analytics.handle(memory, event)
    assert len(memory.events) == 1
    assert memory.events[0][1]["review_count"] == 100
    for _ in range(3):
        await analytics.handle(
            memory, Event(type="SourceHealth", source="mock", payload={"source": "mock", "success": False})
        )
    assert memory.events[-1][1]["kind"] == "source_failures"


@pytest.mark.asyncio
async def test_late_catalog_resolution_retires_provisional_review_product(tx, monkeypatch):
    monkeypatch.setenv("PSEUDONYM_SALT", "test-salt-at-least-sixteen-bytes")
    monkeypatch.setattr("app.handler.utcnow", lambda: NOW)

    async def send(source, batch, records):
        await process(
            tx,
            Event(
                type="BatchCreated",
                source=source,
                payload={
                    "batch_id": batch,
                    "source": source,
                    "records": records,
                    "keys": [{"external_id": str(i), "payload_hash": f"{batch}-{i}"} for i, _ in enumerate(records)],
                },
            ),
        )

    await send(
        "mock-reviews",
        "reviews",
        [{"review_id": "1", "game_id": "b", "reviewer_id": "raw-id", "voted_up": False, "created_at": NOW.isoformat()}],
    )
    old_pid = next(iter(await tx.all("products")))
    await send(
        "mock-games",
        "games",
        [
            {"external_id": "a", "title": "Space Journey", "developer": "Studio"},
            {"external_id": "b", "title": "Space Journey Deluxe Edition", "developer": "Studio"},
        ],
    )
    products = await tx.all("products")
    new_pid = products[old_pid]["redirect_to"]
    assert new_pid != old_pid and len(products[new_pid]["reviews"]) == 1
    checked = governance.govern(products[old_pid], POLICY, NOW)
    assert checked["governance"]["tombstone"] and checked["review_count"] == 0
    assert "raw-id" not in json.dumps(products)


@pytest.mark.asyncio
async def test_review_move_recomputes_old_product_and_removes_analytics_assignment(tx, memory, monkeypatch):
    monkeypatch.setenv("PSEUDONYM_SALT", "test-salt-at-least-sixteen-bytes")
    monkeypatch.setattr("app.handler.utcnow", lambda: NOW)
    monkeypatch.setattr(analytics, "utcnow", lambda: NOW)

    async def send(source, batch, records):
        await process(
            tx,
            Event(
                type="BatchCreated",
                source=source,
                payload={
                    "batch_id": batch,
                    "source": source,
                    "records": records,
                    "keys": [{"external_id": str(i), "payload_hash": f"{batch}-{i}"} for i, _ in enumerate(records)],
                },
            ),
        )
        cur = await tx.conn.execute("SELECT payload FROM outbox ORDER BY id DESC LIMIT 1")
        return (await cur.fetchone())[0]["products"]

    games = await send(
        "mock-games", "games", [{"external_id": "a", "title": "Alpha"}, {"external_id": "b", "title": "Beta"}]
    )
    old_pid = next(p["product_id"] for p in games if p["external_id"] == "a")
    new_pid = next(p["product_id"] for p in games if p["external_id"] == "b")
    original = {
        "review_id": "r1",
        "game_id": "a",
        "reviewer_id": "raw-id",
        "voted_up": False,
        "created_at": NOW.isoformat(),
    }
    first = await send("mock-reviews", "first", [original])
    first_checked = [governance.govern(p, POLICY, NOW) for p in first]
    await analytics.handle(
        memory,
        Event(type="GovernanceChecked", source="governance", payload={"batch_id": "first", "products": first_checked}),
    )
    assert memory.state["metrics", old_pid]["review_count"] == 1

    updated = await send("mock-reviews", "edit", [{**original, "game_id": "b"}])
    old = next(p for p in updated if p["product_id"] == old_pid)
    assert old["review_count"] == 0 and "redirect_to" not in old
    assert old["review_tombstones"] == [{"source": "mock-reviews", "review_id": "r1", "reason": "reassigned"}]
    assert next(p for p in updated if p["product_id"] == new_pid)["review_count"] == 1
    updated_checked = [governance.govern(p, POLICY, NOW) for p in updated]
    await analytics.handle(
        memory,
        Event(type="GovernanceChecked", source="governance", payload={"batch_id": "edit", "products": updated_checked}),
    )
    assert memory.state["metrics", old_pid]["review_count"] == 0
    assert memory.state["metrics", new_pid]["review_count"] == 1


@pytest.mark.asyncio
async def test_review_move_does_not_redirect_provisional_product(tx, monkeypatch):
    monkeypatch.setenv("PSEUDONYM_SALT", "test-salt-at-least-sixteen-bytes")
    monkeypatch.setattr("app.handler.utcnow", lambda: NOW)
    review_data = {
        "review_id": "r1",
        "game_id": "a",
        "reviewer_id": "raw-id",
        "voted_up": False,
        "created_at": NOW.isoformat(),
    }
    for batch, data in [("first", review_data), ("edit", {**review_data, "game_id": "b"})]:
        await process(
            tx,
            Event(
                type="BatchCreated",
                source="mock-reviews",
                payload={
                    "batch_id": batch,
                    "source": "mock-reviews",
                    "records": [data],
                    "keys": [{"external_id": "r1", "payload_hash": batch}],
                },
            ),
        )
    products = list((await tx.all("products")).values())
    old = next(p for p in products if p["external_id"] == "a")
    assert old["review_count"] == 0 and "redirect_to" not in old
    assert old["review_tombstones"][0]["reason"] == "reassigned"
