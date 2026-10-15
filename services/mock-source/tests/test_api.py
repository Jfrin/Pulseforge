import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app(seed=1, base_games=20))


def walk(client, path, **params):
    items, cursor = [], None
    while True:
        q = {**params, "limit": 37}
        if cursor is not None:
            q["cursor"] = cursor
        r = client.get(path, params=q)
        assert r.status_code == 200, r.text
        body = r.json()
        items += body["items"]
        if not body["has_more"]:
            return items
        cursor = body["next_cursor"]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_games_walk_matches_ground_truth_and_has_no_duplicates(client):
    games = walk(client, "/games")
    truth = client.get("/admin/ground-truth").json()
    ids = [g["external_id"] for g in games]
    assert len(ids) == len(set(ids)) == len(truth)


def test_naming_noise_exists(client):
    truth = client.get("/admin/ground-truth").json()
    assert len(set(truth.values())) < len(truth)  # editions/DLC/bundles share a canonical product


def test_same_seed_same_catalog():
    a = TestClient(create_app(seed=5, base_games=10)).get("/games").json()["items"]
    b = TestClient(create_app(seed=5, base_games=10)).get("/games").json()["items"]
    assert [g["title"] for g in a] == [g["title"] for g in b]


def test_invalid_cursor_is_400(client):
    assert client.get("/games", params={"cursor": "garbage"}).status_code == 400


@pytest.mark.parametrize("limit", [0, 501])
def test_limit_bounds(client, limit):
    assert client.get("/games", params={"limit": limit}).status_code == 422


def test_reviews_are_time_ordered(client):
    ts = [r["created_at"] for r in walk(client, "/reviews")]
    assert ts == sorted(ts)


def test_unknown_game_reviews_404(client):
    assert client.get("/reviews", params={"game_id": "nope"}).status_code == 404


def test_error_injection_spares_health_and_admin(client):
    client.post("/admin/chaos", json={"error_rate": 1})
    assert client.get("/games").status_code == 500
    assert client.get("/health").status_code == 200
    assert client.post("/admin/chaos", json={"error_rate": 0}).status_code == 200
    assert client.get("/games").status_code == 200


def test_rate_limit_sends_retry_after(client):
    client.post("/admin/chaos", json={"rate_limit_rate": 1, "retry_after_s": 7})
    r = client.get("/games")
    assert r.status_code == 429 and r.headers["retry-after"] == "7"


def test_chaos_rates_must_fit(client):
    assert client.post("/admin/chaos", json={"error_rate": 0.7, "rate_limit_rate": 0.7}).status_code == 422


def test_duplicate_injection_repeats_a_record(client):
    client.post("/admin/chaos", json={"duplicate_rate": 1})
    items = client.get("/games", params={"limit": 5}).json()["items"]
    ids = [g["external_id"] for g in items]
    assert len(items) == 6 and len(set(ids)) == 5


def test_review_bomb_shifts_negative_share(client):
    gid = "g-00001"
    before = walk(client, "/reviews", game_id=gid)
    r = client.post("/admin/review-bomb", json={"game_id": gid, "count": 150, "negative_share": 0.9})
    assert r.json()["injected"] == 150
    after = walk(client, "/reviews", game_id=gid)
    assert len(after) == len(before) + 150
    burst = after[-150:]
    assert sum(not x["voted_up"] for x in burst) / 150 > 0.75
    ts = [x["created_at"] for x in walk(client, "/reviews")]
    assert ts == sorted(ts)


def test_reset_restores_catalog(client):
    client.post("/admin/review-bomb", json={"game_id": "g-00001", "count": 10})
    n_before = len(walk(client, "/reviews"))
    client.post("/admin/reset")
    assert len(walk(client, "/reviews")) == n_before - 10
