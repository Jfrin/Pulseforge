from __future__ import annotations

import hashlib
import os
from datetime import timedelta

from .events import timestamp, utcnow
from .scoring import review_stats


def detect(reviews, now, *, window_s=86400, negative_threshold=0.7, min_reviews=100, last_alert=None, cooldown_s=43200):
    if window_s <= 0 or min_reviews < 1 or not 0 <= negative_threshold <= 1 or cooldown_s < 0:
        raise ValueError("invalid detector settings")
    stats = review_stats(reviews, now, window_s)
    if stats["review_count"] < min_reviews or stats["negative_share"] < negative_threshold:
        return None
    if last_alert and timestamp(last_alert) + timedelta(seconds=cooldown_s) > now:
        return None
    return {
        **stats,
        "window_start": (now - timedelta(seconds=window_s)).isoformat(),
        "window_end": now.isoformat(),
        "threshold": negative_threshold,
    }


async def alert(tx, key, kind, detail, now, cooldown_s=43200):
    previous = await tx.get("alert_cooldowns", key)
    if previous and timestamp(previous) + timedelta(seconds=cooldown_s) > now:
        return
    alert_id = hashlib.sha256(f"{key}:{now.isoformat()}".encode()).hexdigest()[:24]
    body = {"alert_id": alert_id, "kind": kind, "created_at": now.isoformat(), **detail}
    await tx.put("alert_cooldowns", key, now.isoformat())
    await tx.put("results", alert_id, body)
    await tx.emit("AnomalyDetected", body)


async def handle(tx, event):
    now = utcnow()
    counters = await tx.get("metrics", "events", {})
    counters[event.type] = counters.get(event.type, 0) + 1
    await tx.put("metrics", "events", counters)
    payload = event.payload
    if event.type == "GovernanceChecked":
        for product in payload["products"]:
            pid = product["product_id"]
            reviews = await tx.get("reviews", pid, {})
            for review in product["reviews"]:
                reviews[f"{review['source']}:{review['review_id']}"] = review
            for tombstone in product.get("review_tombstones", []):
                reviews.pop(f"{tombstone['source']}:{tombstone['review_id']}", None)
            if product["governance"]["tombstone"]:
                reviews = {}
            window_s = float(os.getenv("DETECTOR_WINDOW_S", "86400"))
            reviews = {
                k: v for k, v in reviews.items() if timestamp(v["created_at"]) > now - timedelta(seconds=window_s * 2)
            }
            await tx.put("reviews", pid, reviews)
            key = f"review-bomb:{pid}"
            cooldown = float(os.getenv("DETECTOR_COOLDOWN_S", "43200"))
            detected = detect(
                list(reviews.values()),
                now,
                window_s=window_s,
                negative_threshold=float(os.getenv("DETECTOR_NEGATIVE_THRESHOLD", "0.7")),
                min_reviews=int(os.getenv("DETECTOR_MIN_REVIEWS", "100")),
                last_alert=await tx.get("alert_cooldowns", key),
                cooldown_s=cooldown,
            )
            await tx.put("metrics", pid, review_stats(list(reviews.values()), now, window_s))
            if detected:
                await alert(
                    tx,
                    key,
                    "review_bomb",
                    {"product_id": pid, "title": product.get("title"), "batch_id": payload["batch_id"], **detected},
                    now,
                    cooldown,
                )
    elif event.type == "SourceHealth":
        source = payload["source"]
        previous = await tx.get("source_health", source, {})
        failures = 0 if payload["success"] else previous.get("failures", 0) + 1
        await tx.put("source_health", source, {**payload, "failures": failures})
        reasons = []
        if failures >= 3:
            reasons.append("source_failures")
        if payload.get("duplicate_rate", 0) >= float(os.getenv("DUPLICATE_THRESHOLD", "0.8")):
            reasons.append("duplicate_spike")
        if payload.get("queue_lag", 0) >= int(os.getenv("QUEUE_LAG_THRESHOLD", "10000")):
            reasons.append("consumer_lag")
        for reason in reasons:
            await alert(tx, f"{reason}:{source}", reason, payload, now, 300)
    elif event.type == "EnrichmentCompleted":
        metrics = payload["enrichment"]
        previous = await tx.get("metrics", "enrichment", {})
        await tx.put("metrics", "enrichment", metrics)
        if metrics["circuit_trips"] or metrics["unavailable"]:
            await alert(tx, "enrichment:circuit", "source_circuit_open", metrics, now, 300)
        if previous.get("hit_rate", 1) - metrics["hit_rate"] >= 0.3:
            await alert(tx, "enrichment:hit_rate", "hit_rate_drop", metrics, now, 300)
    elif event.type == "PipelineFailed":
        await alert(tx, f"pipeline:{payload['service']}", "pipeline_failed", payload, now, 300)
