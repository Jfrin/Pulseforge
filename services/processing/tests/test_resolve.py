from datetime import UTC, datetime

import pytest

from app.models import Game, Lineage
from app.normalize import normalize_company
from app.resolve import Resolver, resolve_games


def game(external_id, title, developer=None, publisher=None, source="mock-games"):
    dev, dev_key = normalize_company(developer)
    pub, pub_key = normalize_company(publisher if publisher is not None else developer)
    return Game(
        source=source,
        external_id=external_id,
        title=title,
        kind="game",
        developer=dev,
        developer_key=dev_key,
        publisher=pub,
        publisher_key=pub_key,
        price_cents=None,
        currency=None,
        release_date=None,
        player_count=None,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        lineage=Lineage(batch_id="b", source=source, bronze_key=None, payload_hash="h"),
    )


def test_editions_dlc_and_bundles_of_one_game_collapse_into_one_product():
    r = Resolver()
    rows = [
        game("1", "Neon Harbor", "Vega Interactive"),
        game("2", "Neon Harbor: Deluxe Edition", "Vega Interactive Inc."),
        game("3", "NEON HARBOR Complete Edition", "VEGA INTERACTIVE"),
        game("4", "Neon Harbor - Season Pass", "VegaInteractive"),
        game("5", "Neon Harbor + Expansion Bundle", "Vega Interactive Ltd."),
    ]
    results = [r.resolve(g) for g in rows]
    assert len({res.product_id for res in results}) == 1
    assert [res.is_new for res in results] == [True, False, False, False, False]
    (product,) = r.products()
    assert [m.variant for m in product.members] == ["base", "edition", "edition", "dlc", "bundle"]
    assert product.title == "Neon Harbor"


def test_a_sequel_is_never_merged_with_the_original():
    r = Resolver()
    a = r.resolve(game("1", "Neon Harbor", "Vega Interactive"))
    b = r.resolve(game("2", "Neon Harbor 2", "Vega Interactive"))
    assert a.product_id != b.product_id and b.is_new


def test_subtitles_and_spinoffs_stay_separate():
    r = Resolver()
    a = r.resolve(game("1", "Halo"))
    b = r.resolve(game("2", "Halo Wars"))
    assert a.product_id != b.product_id


def test_the_same_title_from_unrelated_studios_stays_separate():
    r = Resolver()
    a = r.resolve(game("1", "Inferno", "Alpha Games"))
    b = r.resolve(game("2", "Inferno", "Beta Studios"))
    assert a.product_id != b.product_id


def test_a_missing_studio_does_not_block_a_match():
    r = Resolver()
    a = r.resolve(game("1", "Paper Lanterns", "Lindqvist Games"))
    b = r.resolve(game("2", "Paper Lanterns Remastered"))  # no developer info at all
    assert a.product_id == b.product_id


def test_a_dlc_that_arrives_before_its_base_game_still_joins_and_the_title_improves():
    r = Resolver()
    dlc = r.resolve(game("2", "Paper Lanterns - Season Pass", "Lindqvist Games"))
    base = r.resolve(game("1", "Paper Lanterns", "Lindqvist Games"))
    assert dlc.is_new and not base.is_new and dlc.product_id == base.product_id
    (product,) = r.products()
    assert product.title == "Paper Lanterns"


def test_resolving_the_same_record_twice_is_idempotent():
    r = Resolver()
    g = game("1", "Neon Harbor", "Vega Interactive")
    first, second = r.resolve(g), r.resolve(g)
    assert first.product_id == second.product_id and first.is_new and not second.is_new
    assert second.reasons == ("already_resolved",) and len(r.products()[0].members) == 1


def test_the_same_game_from_two_sources_becomes_one_product():
    r = Resolver()
    a = r.resolve(game("g-1", "Cinder Salt Stone", "Okafor Labs", source="mock-games"))
    b = r.resolve(game("csv-3", "Cinder, Salt & Stone", "OKAFOR LABS INC", source="csv-import"))
    assert a.product_id == b.product_id
    assert {m.source for m in r.products()[0].members} == {"mock-games", "csv-import"}


def test_near_matches_below_the_threshold_are_reported_as_possible_but_not_merged():
    r = Resolver()
    first = r.resolve(game("1", "Crimson Tide Riders"))
    second = r.resolve(game("2", "Crimson Tide Racers"))
    assert second.is_new and second.product_id != first.product_id
    assert [pid for pid, _ in second.possible] == [first.product_id]
    assert 80 <= second.possible[0][1] < 90


def test_resolutions_explain_themselves():
    r = Resolver()
    r.resolve(game("1", "Neon Harbor", "Vega Interactive"))
    res = r.resolve(game("2", "Neon Harbor: Deluxe Edition", "Vega Interactive Inc."))
    assert res.variant == "edition"
    assert {"title_similarity=100", "developer_or_publisher_agrees", "variant=edition"} <= set(res.reasons)


def test_product_ids_are_deterministic():
    a, b = Resolver(), Resolver()
    assert a.resolve(game("1", "Neon Harbor")).product_id == b.resolve(game("1", "Neon Harbor")).product_id


def test_resolve_games_pairs_each_game_with_its_resolution():
    pairs = resolve_games(Resolver(), [game("1", "A Game"), game("2", "A Game - Soundtrack")])
    assert [g.external_id for g, _ in pairs] == ["1", "2"] and pairs[0][1].product_id == pairs[1][1].product_id


@pytest.mark.parametrize("threshold, review", [(0, 0), (90, 95), (101, 80), (90, 0)])
def test_threshold_validation(threshold, review):
    with pytest.raises(ValueError):
        Resolver(threshold=threshold, review_threshold=review)
