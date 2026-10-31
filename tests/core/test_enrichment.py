import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx2 as httpx
import pytest
from pulseforge.enrichment import confidence, enrich
from pulseforge.resilience import ResilientClient, SourceUnavailable, retry_after
from redis.asyncio import Redis


@pytest.mark.asyncio
async def test_waterfall_only_missing_fields_cascade_and_stop():
    client = AsyncMock()
    client.request.side_effect = [
        {"fields": {"developer": "Studio"}, "observed_at": "2026-10-31T00:00:00Z"},
        {"fields": {"publisher": "Publisher"}, "observed_at": "2026-10-31T00:00:00Z"},
    ]
    product = {
        "source": "mock-games",
        "external_id": "one",
        "title": "Game",
        "price_cents": 0,
        "currency": "USD",
        "release_date": "2020-01-01",
        "player_count": 0,
    }
    output, unavailable = await enrich([product], client, "http://adapters")
    assert client.request.await_count == 2 and not unavailable
    assert client.request.await_args_list[0].args[2]["fields"] == ["developer", "publisher"]
    assert client.request.await_args_list[1].args[2]["fields"] == ["publisher"]
    assert not output[0]["missing_fields"] and "developer" not in product


@pytest.mark.asyncio
async def test_circuit_skip_and_all_down_pause():
    client = AsyncMock()
    client.request.side_effect = SourceUnavailable("open")
    with pytest.raises(SourceUnavailable, match="all enrichment"):
        await enrich([{"source": "mock", "external_id": "1"}], client, "http://adapters")
    assert client.request.await_count == 3


def test_confidence_agreement_and_age():
    now = datetime(2026, 10, 31, tzinfo=UTC)
    old = (now - timedelta(days=30)).isoformat()
    result = confidence({"title": [{"value": "A", "observed_at": old}, {"value": "B", "observed_at": old}]}, now)
    assert result["title"] == 0.25
    assert retry_after("10") == 10 and retry_after("nonsense") == 0


@pytest.mark.asyncio
async def test_redis_idempotency_cache_and_retry_after():
    redis = Redis.from_url(os.environ["REDIS_URL"])
    await redis.flushdb()
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path == "/throttled":
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, json={"fields": {"title": "Game"}, "observed_at": "2026-10-31T00:00:00Z"})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = ResilientClient(redis, http, rate=100)
            assert await client.request("a", "http://test/ok", {}) == await client.request("a", "http://test/ok", {})
            assert client.calls == 1 and client.cache_hits == 1
            with pytest.raises(SourceUnavailable):
                await client.request("b", "http://test/throttled", {})
            with pytest.raises(SourceUnavailable, match="retry-after"):
                await client.request("b", "http://test/throttled", {})
            assert len(calls) == 2 and await redis.ttl("pause:b") > 50
    finally:
        await redis.aclose()
