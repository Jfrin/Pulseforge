import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.state import MockState


def hard_only_ids(state):
    return {v.rsplit("-", 1)[-1] for v in state.ground_truth.values() if v.count("-") == 2}


def test_easy_is_the_default_and_has_none_of_the_hard_only_products():
    state = MockState(1, 50)
    assert state.difficulty == "easy" and hard_only_ids(state) == set()


def test_hard_mode_adds_sequels_spin_offs_and_namesakes_as_separate_products():
    easy, hard = MockState(42, 200), MockState(42, 200, "hard")
    assert len(hard.games) > len(easy.games)
    assert {"seq", "spin", "twin"} <= hard_only_ids(hard)


def test_a_namesake_shares_its_title_with_a_different_product():
    hard = MockState(42, 200, "hard")
    products_by_title = {}
    for g in hard.games:
        products_by_title.setdefault(g["title"], set()).add(hard.ground_truth[g["external_id"]])
    assert any(len(ids) > 1 for ids in products_by_title.values())


def test_the_same_seed_gives_the_same_hard_catalog():
    a, b = MockState(7, 30, "hard"), MockState(7, 30, "hard")
    assert [g["title"] for g in a.games] == [g["title"] for g in b.games]


def test_an_unknown_difficulty_is_rejected():
    with pytest.raises(ValueError):
        MockState(1, 5, "brutal")


def test_the_reset_endpoint_switches_difficulty():
    client = TestClient(create_app(seed=1, base_games=30))
    easy = client.get("/games", params={"limit": 500}).json()["items"]
    reply = client.post("/admin/reset", params={"difficulty": "hard"})
    assert reply.json() == {"status": "reset", "difficulty": "hard"}
    hard = client.get("/games", params={"limit": 500}).json()["items"]
    assert len(hard) > len(easy)
    assert client.post("/admin/reset", params={"difficulty": "brutal"}).status_code == 422


def test_create_app_accepts_a_difficulty():
    client = TestClient(create_app(seed=1, base_games=10, difficulty="hard"))
    assert client.post("/admin/reset").json()["difficulty"] == "hard"  # a plain reset keeps the current setting
