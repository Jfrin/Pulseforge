from datetime import UTC, datetime

import pytest

from app.pipeline import process_batch
from app.pseudonymize import Pseudonymizer

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
PSEUDO = Pseudonymizer("a-test-salt-that-is-long-enough")


def payload(source, records, id_field="external_id", bronze="bronze/b.json"):
    keys = [
        {"external_id": str(r.get(id_field, "")), "payload_hash": f"h{i}", "bronze_key": bronze}
        for i, r in enumerate(records)
    ]
    return {"batch_id": "b-1", "source": source, "records": records, "keys": keys}


def test_a_mixed_games_batch_is_split_into_silver_and_rejects_with_lineage():
    records = [
        {"external_id": "g-1", "title": "Neon Harbor", "price_cents": "1999"},
        {"external_id": "g-2", "title": ""},
        {"external_id": "g-3", "title": "Paper Lanterns"},
    ]
    result = process_batch(payload("mock-games", records), PSEUDO, now=NOW)
    assert result.summary() == {"games": 2, "reviews": 0, "rejects": 1}
    assert [g.external_id for g in result.games] == ["g-1", "g-3"]
    assert [g.lineage.payload_hash for g in result.games] == ["h0", "h2"]  # keys stay aligned with records
    (reject,) = result.rejects
    assert (reject.external_id, reject.payload_hash, reject.bronze_key, reject.batch_id) == (
        "g-2",
        "h1",
        "bronze/b.json",
        "b-1",
    )
    assert {(i.field, i.code) for i in reject.issues} == {("title", "missing")}


def test_reviews_are_pseudonymized_end_to_end_and_raw_ids_never_reach_the_output():
    records = [
        {
            "review_id": "r-1",
            "game_id": "g-1",
            "reviewer_id": "RAW-REVIEWER-1",
            "voted_up": True,
            "created_at": "2026-10-18T10:00:00Z",
        },
        {
            "review_id": "r-2",
            "game_id": "g-1",
            "reviewer_id": "RAW-REVIEWER-1",
            "voted_up": False,
            "created_at": "2026-10-18T10:05:00Z",
        },
        {
            "review_id": "r-3",
            "game_id": "g-1",
            "reviewer_id": "RAW-REVIEWER-2",
            "voted_up": False,
            "created_at": "2026-10-18T10:06:00Z",
        },
    ]
    result = process_batch(payload("mock-reviews", records, id_field="review_id"), PSEUDO, now=NOW)
    assert len(result.reviews) == 3
    assert result.reviews[0].reviewer == result.reviews[1].reviewer != result.reviews[2].reviewer
    assert "RAW-REVIEWER" not in result.model_dump_json()


def test_steam_batches_use_the_steam_mapper():
    records = [{"recommendationid": "1", "app_id": 570, "voted_up": True, "timestamp_created": 1_792_324_800,
                "author": {"steamid": "7656"}}]  # fmt: skip
    result = process_batch(payload("steam-reviews", records, id_field="recommendationid"), PSEUDO, now=NOW)
    assert result.summary() == {"games": 0, "reviews": 1, "rejects": 0}


def test_a_source_without_a_mapper_rejects_everything_but_loses_nothing():
    result = process_batch(payload("webhook-acme", [{"id": 1}, {"id": 2}], id_field="id"), PSEUDO, now=NOW)
    assert result.summary() == {"games": 0, "reviews": 0, "rejects": 2}
    assert all(i.code == "no_mapper" for r in result.rejects for i in r.issues)
    assert [r.external_id for r in result.rejects] == ["1", "2"]


def test_a_crashing_mapper_rejects_that_record_and_the_batch_continues(monkeypatch):
    from app import mappers, pipeline

    def boom(rec, lineage, pseudonymize, now):
        if rec["external_id"] == "bad":
            raise RuntimeError("bug")
        return mappers.map_game(rec, lineage, pseudonymize, now)

    monkeypatch.setattr(pipeline, "mapper_for", lambda source: boom)
    records = [{"external_id": "ok", "title": "Fine"}, {"external_id": "bad", "title": "Boom"}]
    result = process_batch(payload("mock-games", records), PSEUDO, now=NOW)
    assert [g.external_id for g in result.games] == ["ok"]
    assert [(r.external_id, i.code) for r in result.rejects for i in r.issues] == [("bad", "mapper_error")]


def test_an_empty_batch_is_fine():
    assert process_batch(payload("mock-games", []), PSEUDO, now=NOW).summary() == {
        "games": 0,
        "reviews": 0,
        "rejects": 0,
    }


def test_misaligned_records_and_keys_are_refused():
    bad = payload("mock-games", [{"external_id": "g", "title": "X"}])
    bad["keys"] = []
    with pytest.raises(ValueError):
        process_batch(bad, PSEUDO, now=NOW)


def test_payloads_that_are_not_batch_events_are_refused():
    with pytest.raises(ValueError):
        process_batch({"records": []}, PSEUDO, now=NOW)
