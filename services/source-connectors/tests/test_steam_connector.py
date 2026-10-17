import time

import httpx2 as httpx
import pytest

from app.connectors import STEAM_VOLATILE_FIELDS, SourceConnector, SteamReviewsConnector
from tests.contract import check_connector_contract


def review(rid, up=True):
    return {"recommendationid": str(rid), "voted_up": up, "author": {"steamid": f"s{rid}", "playtime_forever": 5}}


# (appid, steam cursor) -> (reviews, cursor to send back). An empty page ends an app.
CHAIN = {
    ("570", "*"): ([review(1), review(2, False)], "c1"),
    ("570", "c1"): ([review(3)], "c2"),
    ("570", "c2"): ([], "c2"),
    ("730", "*"): ([review(9)], "d1"),
    ("730", "d1"): ([], "d1"),
}


def api(request: httpx.Request) -> httpx.Response:
    appid = request.url.path.rsplit("/", 1)[-1]
    reviews, nxt = CHAIN[(appid, request.url.params["cursor"])]
    return httpx.Response(200, json={"success": 1, "query_summary": {}, "reviews": reviews, "cursor": nxt})


def make(handler=api, apps=(570, 730), **kw):
    client = httpx.AsyncClient(base_url="https://store.steampowered.com", transport=httpx.MockTransport(handler))
    kw.setdefault("min_interval_s", 0)
    return SteamReviewsConnector("steam", list(apps), page_size=2, client=client, **kw)


async def pages(c):
    out, cursor = [], None
    while True:
        page = await c.fetch(cursor)
        out.append(page)
        if not page.has_more:
            return out
        cursor = page.next_cursor


async def test_satisfies_the_connector_contract():
    c = make()
    assert isinstance(c, SourceConnector)
    records = await check_connector_contract(c)
    assert [r["recommendationid"] for r in records] == ["1", "2", "3", "9"]


async def test_walks_pages_then_apps_and_adds_the_app_id():
    ps = await pages(make())
    assert [p.next_cursor for p in ps] == ["570|1|c1", "570|2|c2", "730|0|*", "730|1|d1", None]
    records = [r for p in ps for r in p.records]
    assert [(r["recommendationid"], r["app_id"]) for r in records] == [("1", 570), ("2", 570), ("3", 570), ("9", 730)]
    assert ps[-1].has_more is False


async def test_requests_use_the_documented_parameters():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return api(request)

    await make(handler).fetch(None)
    assert seen == [
        {
            "json": "1",
            "filter": "recent",
            "language": "all",
            "cursor": "*",
            "num_per_page": "2",
            "purchase_type": "all",
            "review_type": "all",
            "filter_offtopic_activity": "0",  # include off-topic activity: that is where review bombs live
        }
    ]


async def test_steam_cursors_with_special_characters_survive_the_round_trip():
    weird = "AoJ+/=%3D&x"
    cursors = []

    def handler(request):
        cursors.append(request.url.params["cursor"])
        body = {"success": 1, "reviews": [review(1)], "cursor": weird if len(cursors) == 1 else "end"}
        return httpx.Response(200, json=body)

    c = make(handler, apps=(570,))
    first = await c.fetch(None)
    assert first.next_cursor == f"570|1|{weird}"
    await c.fetch(first.next_cursor)
    assert cursors[1] == weird


async def test_max_pages_per_app_caps_the_walk():
    ps = await pages(make(max_pages_per_app=1))
    assert [p.next_cursor for p in ps] == ["730|0|*", None]  # app 570 stopped after its first page


async def test_a_cursor_that_stops_advancing_ends_the_app():
    def handler(request):
        return httpx.Response(200, json={"success": 1, "reviews": [review(1)], "cursor": request.url.params["cursor"]})

    page = await make(handler, apps=(570, 730)).fetch(None)
    assert page.next_cursor == "730|0|*"


async def test_success_zero_raises():
    c = make(lambda req: httpx.Response(200, json={"success": 0}))
    with pytest.raises(RuntimeError):
        await c.fetch(None)


async def test_http_errors_are_raised_not_swallowed():
    c = make(lambda req: httpx.Response(429, headers={"Retry-After": "30"}))
    with pytest.raises(httpx.HTTPStatusError):
        await c.fetch(None)


@pytest.mark.parametrize("cursor", ["garbage", "999|0|*", "570|x|*", "570|-1|*", "570|0|"])
async def test_invalid_cursors_raise(cursor):
    with pytest.raises(ValueError):
        await make().fetch(cursor)


def test_constructor_validation():
    for kw in ({"apps": ()}, {"page_size": 0}, {"page_size": 101}, {"max_pages_per_app": 0}):
        apps = kw.pop("apps", (570,))
        with pytest.raises(ValueError):
            SteamReviewsConnector("steam", apps, **kw)


async def test_requests_are_spaced_by_min_interval():
    c = make(min_interval_s=0.05)
    start = time.monotonic()
    await c.fetch(None)
    await c.fetch("570|1|c1")
    assert time.monotonic() - start >= 0.045


def test_volatile_fields_cover_what_churns_on_old_reviews():
    assert "votes_up" in STEAM_VOLATILE_FIELDS and "author.playtime_forever" in STEAM_VOLATILE_FIELDS
