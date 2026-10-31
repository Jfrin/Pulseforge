from __future__ import annotations

import os
from collections import defaultdict

import httpx2 as httpx
from redis.asyncio import Redis

from .events import timestamp, utcnow
from .resilience import ResilientClient, SourceUnavailable

FIELDS = ("title", "developer", "publisher", "price_cents", "currency", "release_date", "player_count")


def confidence(evidence, now):
    result = {}
    for field, observations in evidence.items():
        selected = observations[0]["value"]
        agreement = sum(item["value"] == selected for item in observations) / len(observations)
        newest = max(timestamp(item["observed_at"]) for item in observations)
        age_days = max(0.0, (now - newest).total_seconds() / 86400)
        result[field] = round(agreement * (1 / (1 + age_days / 30)), 4)
    return result


async def enrich(products, client, base_url, api_key=""):
    output = []
    unavailable = set()
    for raw in products:
        product = dict(raw)
        if product.get("redirect_to"):
            product.update(confidence={}, field_lineage={}, missing_fields=[])
            output.append(product)
            continue
        evidence = defaultdict(list)
        for field in FIELDS:
            if product.get(field) is not None:
                evidence[field].append(
                    {
                        "source": product.get("source"),
                        "value": product[field],
                        "observed_at": product.get("observed_at") or utcnow().isoformat(),
                    }
                )
        attempted = successes = 0
        for tier in ("a", "b", "c"):
            missing = [field for field in FIELDS if product.get(field) is None]
            if not missing:
                break
            attempted += 1
            query = {"external_id": product["external_id"], "source": product["source"], "fields": missing}
            try:
                response = await client.request(
                    tier, f"{base_url}/enrich/{tier}", query, {"X-API-Key": api_key} if tier == "c" else {}
                )
            except SourceUnavailable:
                unavailable.add(tier)
                continue
            successes += 1
            for field, value in response["fields"].items():
                if field not in FIELDS or value is None:
                    continue
                evidence[field].append({"source": tier, "value": value, "observed_at": response["observed_at"]})
                if product.get(field) is None:
                    product[field] = value
        if attempted and not successes:
            raise SourceUnavailable("all enrichment tiers unavailable; batch will retry")
        product["confidence"] = confidence(evidence, utcnow())
        product["field_lineage"] = dict(evidence)
        product["missing_fields"] = [f for f in FIELDS if product.get(f) is None]
        output.append(product)
    return output, sorted(unavailable)


async def handle(tx, event):
    redis = Redis.from_url(os.environ["REDIS_URL"])
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            client = ResilientClient(redis, http, rate=float(os.getenv("SOURCE_RATE", "5")))
            products, unavailable = await enrich(
                event.payload["products"], client, os.environ["ADAPTER_URL"], os.getenv("ENRICHMENT_API_KEY", "")
            )
        metrics = {
            "api_calls": client.calls,
            "cache_hits": client.cache_hits,
            "circuit_trips": client.circuit_trips,
            "api_calls_saved": max(0, len(products) * 3 - client.calls),
            "unavailable": unavailable,
            "hit_rate": sum(not p["missing_fields"] for p in products) / len(products) if products else 1.0,
        }
        await tx.put("metrics", event.payload["batch_id"], metrics)
        await tx.emit("EnrichmentCompleted", {**event.payload, "products": products, "enrichment": metrics})
    finally:
        await redis.aclose()
