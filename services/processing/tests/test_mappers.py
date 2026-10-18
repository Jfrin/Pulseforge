from datetime import UTC, datetime

from app.mappers import map_game, map_mock_review, map_steam_review, mapper_for, register_mapper
from app.models import Game, Lineage, Review
from app.pseudonymize import Pseudonymizer

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
PSEUDO = Pseudonymizer("a-test-salt-that-is-long-enough")


def lineage(source="mock-games"):
    return Lineage(batch_id="b1", source=source, bronze_key="bronze/x.json", payload_hash="h1")


def codes(issues):
    return {(i.field, i.code, i.severity) for i in issues}


# ---------------- games ----------------


def test_a_clean_game_maps_with_normalized_fields_and_lineage():
    rec = {"external_id": "g-1", "title": " Neon  Harbor ", "type": "Game", "developer": "Vega Interactive Inc.",
           "publisher": "VEGA INTERACTIVE", "price_cents": "1999", "currency": "usd", "release_date": "2021-03-12",
           "player_count": "1840", "updated_at": "2026-10-18T10:00:00Z"}  # fmt: skip
    game, issues = map_game(rec, lineage(), PSEUDO, NOW)
    assert isinstance(game, Game) and issues == []
    assert (game.title, game.kind, game.price_cents, game.currency, game.player_count) == (
        "Neon Harbor",
        "game",
        1999,
        "USD",
        1840,
    )
    assert game.developer_key == game.publisher_key == "vegainteractive"
    assert game.lineage.bronze_key == "bronze/x.json" and game.release_date.isoformat() == "2021-03-12"


def test_missing_required_fields_reject_the_game():
    game, issues = map_game({"external_id": "g-1", "title": "  "}, lineage(), PSEUDO, NOW)
    assert game is None and codes(issues) == {("title", "missing", "error")}
    game, issues = map_game({"title": "X"}, lineage(), PSEUDO, NOW)
    assert game is None and ("external_id", "missing", "error") in codes(issues)


def test_malformed_optional_fields_become_null_with_a_warning_and_the_game_survives():
    rec = {"external_id": "g-1", "title": "X", "price_cents": "free", "currency": "dollars", "release_date": "someday",
           "player_count": -5, "updated_at": "yesterday-ish"}  # fmt: skip
    game, issues = map_game(rec, lineage(), PSEUDO, NOW)
    assert isinstance(game, Game)
    assert (game.price_cents, game.currency, game.release_date, game.player_count, game.observed_at) == (None,) * 5
    assert {f for f, _, _ in codes(issues)} == {"price_cents", "currency", "release_date", "player_count", "updated_at"}
    assert all(s == "warning" for _, _, s in codes(issues)) and game.warnings == tuple(issues)


def test_negative_prices_are_out_of_range():
    game, issues = map_game({"external_id": "g", "title": "X", "price_cents": -1}, lineage(), PSEUDO, NOW)
    assert (
        isinstance(game, Game)
        and game.price_cents is None
        and ("price_cents", "out_of_range", "warning") in codes(issues)
    )


def test_unknown_game_types_become_unknown():
    game, _ = map_game({"external_id": "g", "title": "X", "type": "tool"}, lineage(), PSEUDO, NOW)
    assert isinstance(game, Game) and game.kind == "unknown"


# ---------------- mock reviews ----------------

REVIEW = {"review_id": "r-1", "game_id": "g-1", "reviewer_id": "7656119800000001", "voted_up": False,
          "text": "refunded", "playtime_hours": 1.5, "created_at": "2026-10-18T11:00:00Z"}  # fmt: skip


def test_a_mock_review_is_pseudonymized_and_its_text_is_dropped():
    review, issues = map_mock_review(REVIEW, lineage("mock-reviews"), PSEUDO, NOW)
    assert isinstance(review, Review) and issues == []
    assert review.reviewer == PSEUDO("mock-reviews", "7656119800000001")
    assert (review.voted_up, review.playtime_minutes, review.text_length, review.game_ref) == (False, 90, 8, "g-1")
    dumped = review.model_dump_json()
    assert "7656119800000001" not in dumped and "refunded" not in dumped


def test_review_required_fields_and_future_timestamps_are_errors():
    rec = {**REVIEW, "reviewer_id": "", "voted_up": "maybe", "created_at": "2026-10-18T13:00:00Z"}
    review, issues = map_mock_review(rec, lineage("mock-reviews"), PSEUDO, NOW)
    assert review is None
    assert codes(issues) == {
        ("reviewer_id", "missing", "error"),
        ("voted_up", "malformed", "error"),
        ("created_at", "in_future", "error"),
    }


def test_small_clock_skew_is_tolerated():
    rec = {**REVIEW, "created_at": "2026-10-18T12:03:00Z"}
    review, _ = map_mock_review(rec, lineage("mock-reviews"), PSEUDO, NOW)
    assert isinstance(review, Review)


def test_bad_playtime_is_only_a_warning():
    review, issues = map_mock_review({**REVIEW, "playtime_hours": -3}, lineage("mock-reviews"), PSEUDO, NOW)
    assert isinstance(review, Review) and review.playtime_minutes is None
    assert codes(issues) == {("playtime_hours", "malformed", "warning")}


# ---------------- steam reviews ----------------

STEAM = {"recommendationid": "123", "app_id": 570, "voted_up": True, "language": "english", "review": "great",
         "timestamp_created": 1_792_324_800, "votes_up": 4,
         "author": {"steamid": "76561198000000042", "playtime_at_review": 600, "playtime_forever": 900}}  # fmt: skip


def test_a_steam_review_maps_and_never_leaks_the_steam_id():
    review, issues = map_steam_review(STEAM, lineage("steam-reviews"), PSEUDO, NOW)
    assert isinstance(review, Review) and issues == []
    assert (review.review_id, review.game_ref, review.playtime_minutes, review.language) == (
        "123",
        "570",
        600,
        "english",
    )
    assert review.created_at == datetime.fromtimestamp(1_792_324_800, tz=UTC)
    assert review.reviewer == PSEUDO("steam-reviews", "76561198000000042")
    assert "76561198000000042" not in review.model_dump_json()


def test_a_steam_review_without_an_author_is_rejected():
    review, issues = map_steam_review({**STEAM, "author": None}, lineage("steam-reviews"), PSEUDO, NOW)
    assert review is None and ("author.steamid", "missing", "error") in codes(issues)


# ---------------- registry ----------------


def test_known_sources_have_mappers_and_unknown_ones_do_not():
    assert mapper_for("mock-games") is map_game and mapper_for("steam-reviews") is map_steam_review
    assert mapper_for("webhook-nobody-registered-this") is None


def test_a_new_source_is_one_register_call():
    register_mapper("test-only-source", map_game)
    try:
        assert mapper_for("test-only-source") is map_game
    finally:
        from app.mappers import MAPPERS

        MAPPERS.pop("test-only-source")
