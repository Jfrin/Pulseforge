"""Three cost-ordered adapters, one field-enrichment contract. Tier C is a keyed fixture stub."""

import json
import os
from pathlib import Path
from typing import Protocol

import httpx2 as httpx
from fastapi import FastAPI, Header, HTTPException
from pulseforge.events import utcnow
from pydantic import BaseModel, Field


class Query(BaseModel):
    external_id: str
    source: str
    fields: list[str] = Field(default_factory=list)


class Adapter(Protocol):
    async def fetch(self, query: Query) -> dict: ...


async def mock_game(query):
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{os.environ['MOCK_SOURCE_URL']}/games/{query.external_id}")
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()


class CheapAdapter:
    async def fetch(self, query):
        if not query.source.startswith("mock-"):
            return {}
        data = await mock_game(query)
        return {k: data[k] for k in ("title", "developer", "publisher") if k in data}


class StoreAdapter:
    async def fetch(self, query):
        if query.source.startswith("mock-"):
            data = await mock_game(query)
            return {k: data[k] for k in ("price_cents", "currency", "release_date", "player_count") if k in data}
        if not query.source.startswith("steam-") or not query.external_id.isdigit():
            return {}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": query.external_id, "cc": "us", "l": "english"},
            )
            response.raise_for_status()
            entry = response.json().get(query.external_id, {})
            if not entry.get("success"):
                return {}
            data = entry["data"]
            price = data.get("price_overview", {})
            fields = {
                "title": data.get("name"),
                "developer": ", ".join(data.get("developers", [])) or None,
                "publisher": ", ".join(data.get("publishers", [])) or None,
                "price_cents": price.get("final", 0 if data.get("is_free") else None),
                "currency": price.get("currency"),
            }
            if "player_count" in query.fields:
                response = await client.get(
                    "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
                    params={"appid": query.external_id},
                )
                response.raise_for_status()
                fields["player_count"] = response.json().get("response", {}).get("player_count")
            return fields


class KeyedStubAdapter:
    async def fetch(self, query):
        path = os.getenv("KEYED_STUB_FILE")
        fixtures = json.loads(Path(path).read_text()) if path else {}
        return fixtures.get(f"{query.source}:{query.external_id}", {})


app = FastAPI(title="PulseForge source adapters")
adapters: dict[str, Adapter] = {"a": CheapAdapter(), "b": StoreAdapter(), "c": KeyedStubAdapter()}


@app.get("/health")
@app.get("/ready")
async def health():
    return {"status": "ok"}


@app.post("/enrich/{tier}")
async def enrich(tier: str, query: Query, x_api_key: str = Header(default="")):
    if tier not in adapters:
        raise HTTPException(404, "unknown adapter")
    if tier == "c" and (not os.getenv("ENRICHMENT_API_KEY") or x_api_key != os.environ["ENRICHMENT_API_KEY"]):
        raise HTTPException(401, "API key required")
    try:
        fields = await adapters[tier].fetch(query)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            exc.response.status_code,
            "upstream error",
            headers={"Retry-After": exc.response.headers.get("Retry-After", "1")},
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(503, "upstream unavailable") from exc
    return {
        "fields": fields,
        "observed_at": utcnow().isoformat(),
        "source": tier,
        "cost": {"a": 1, "b": 3, "c": 10}[tier],
    }
