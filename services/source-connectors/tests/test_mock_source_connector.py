import httpx2 as httpx
import pytest

from app.connectors import MockSourceConnector, SourceConnector
from tests.contract import check_connector_contract


def make(handler, **kw):
    client = httpx.AsyncClient(base_url="http://mock", transport=httpx.MockTransport(handler))
    return MockSourceConnector("mock-games", "http://mock", "/games", page_size=2, client=client, **kw)


def fake_api(request: httpx.Request) -> httpx.Response:
    data = [{"external_id": f"g-{i}"} for i in range(5)]
    offset = int(request.url.params.get("cursor", "0"))
    limit = int(request.url.params["limit"])
    chunk = data[offset : offset + limit]
    more = offset + limit < len(data)
    return httpx.Response(
        200, json={"items": chunk, "next_cursor": str(offset + limit) if more else None, "has_more": more}
    )


async def test_maps_response_to_page_and_satisfies_contract():
    c = make(fake_api)
    assert isinstance(c, SourceConnector)
    records = await check_connector_contract(c)
    assert [r["external_id"] for r in records] == [f"g-{i}" for i in range(5)]


async def test_sends_cursor_and_limit():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return fake_api(req)

    c = make(handler)
    await c.fetch(None)
    await c.fetch("2")
    assert seen == [{"limit": "2"}, {"limit": "2", "cursor": "2"}]


async def test_http_errors_are_raised_not_swallowed():
    c = make(lambda req: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await c.fetch(None)
