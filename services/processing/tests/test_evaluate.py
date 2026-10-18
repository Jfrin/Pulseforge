import httpx2 as httpx

from app import evaluate


def row(i, title, dev):
    return {"external_id": f"g-{i}", "title": title, "type": "game", "developer": dev, "publisher": dev,
            "price_cents": 1999, "currency": "USD", "release_date": "2020-01-01", "player_count": 10}  # fmt: skip


ROWS = [
    row(1, "Neon Harbor", "Vega Interactive"),
    row(2, "Neon Harbor: Deluxe Edition", "Vega Interactive Inc."),
    row(3, "NEON HARBOR Complete Edition", "VEGA INTERACTIVE"),
    row(4, "Neon Harbor - Season Pass", "VegaInteractive"),
    row(5, "Neon Harbor + Expansion Bundle", "Vega Interactive Ltd."),
    row(6, "Cinder Salt Stone", "Okafor Labs"),
    row(7, "Cinder Salt Stone (Definitive Edition)", "OKAFOR LABS"),
    row(8, "Paper Lanterns", "Lindqvist Games"),
    row(9, "Paper Lanterns Remastered", "Lindqvist Games Inc."),
    row(10, "Paper Lanterns 2", "Lindqvist Games"),
]
TRUTH = {
    "g-1": "c1", "g-2": "c1", "g-3": "c1", "g-4": "c1", "g-5": "c1",
    "g-6": "c2", "g-7": "c2", "g-8": "c3", "g-9": "c3", "g-10": "c4",
}  # fmt: skip


def fake_api(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/games":
        return httpx.Response(200, json={"items": ROWS, "next_cursor": None, "has_more": False})
    if request.url.path == "/admin/ground-truth":
        return httpx.Response(200, json=TRUTH)
    return httpx.Response(404)


def client():
    return httpx.Client(base_url="http://mock", transport=httpx.MockTransport(fake_api))


def test_a_clean_catalog_is_resolved_perfectly():
    games = evaluate.normalize_games(ROWS)
    report = evaluate.evaluate(games, TRUTH)
    m = report.metrics
    assert (m.precision, m.recall) == (1.0, 1.0) and m.predicted_clusters == m.true_clusters == 4
    assert report.false_merges == [] and report.false_splits == []


def test_a_too_low_threshold_over_merges_and_the_report_shows_which_titles():
    rows = [row(1, "Dark Forest", "Alpha Games"), row(2, "Dark Forests", "Beta Studios")]
    truth = {"g-1": "c1", "g-2": "c2"}
    report = evaluate.evaluate(evaluate.normalize_games(rows), truth, threshold=70)
    assert report.metrics.precision == 0.0 and len(report.false_merges) == 1
    assert "wrongly MERGED" in report.render() and "Dark Forest" in report.render()


def test_fetch_all_follows_cursors():
    pages = {
        None: ({"items": [1, 2], "next_cursor": "c", "has_more": True}),
        "c": {"items": [3], "next_cursor": None, "has_more": False},
    }

    def handler(request):
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    http = httpx.Client(base_url="http://mock", transport=httpx.MockTransport(handler))
    assert evaluate.fetch_all(http, "/games") == [1, 2, 3]


def test_the_cli_prints_a_report(capsys):
    assert evaluate.main([], client=client()) == 0
    out = capsys.readouterr().out
    assert "precision 1.0000" in out and "recall 1.0000" in out
