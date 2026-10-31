import httpx2 as httpx
import pytest

from app.connectors.steam_games import SteamGamesConnector


async def test_metadata_and_official_player_counts_keep_raw_responses():
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "store.steampowered.com":
            app_id = request.url.params["appids"]
            return httpx.Response(200, json={app_id: {"success": True, "data": {"name": "Game"}}})
        return httpx.Response(200, json={"response": {"player_count": 12, "result": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        connector = SteamGamesConnector((570, 730), min_interval_s=0, client=client)
        first = await connector.fetch(None)
        assert first.has_more and first.next_cursor == "1"
        assert first.records[0]["players"]["response"]["player_count"] == 12
        assert first.records[0]["store"]["570"]["data"]["name"] == "Game"
        assert not (await connector.fetch(first.next_cursor)).has_more
        assert calls[1].url.host == "api.steampowered.com"
        with pytest.raises(ValueError):
            await connector.fetch("-1")


async def test_official_api_failure_does_not_advance_a_page():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        connector = SteamGamesConnector((570,), min_interval_s=0, client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await connector.fetch(None)
