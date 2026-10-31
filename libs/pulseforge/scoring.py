"""Explainable stacked signals; missing baselines contribute no evidence."""

from datetime import timedelta

from .events import timestamp


def review_stats(reviews, now, window_s=86400):
    window = timedelta(seconds=window_s)
    current = [r for r in reviews if now - window < timestamp(r["created_at"]) <= now]
    baseline = [r for r in reviews if now - 2 * window < timestamp(r["created_at"]) <= now - window]
    return {
        "review_count": len(current),
        "negative_count": sum(not r["voted_up"] for r in current),
        "negative_share": sum(not r["voted_up"] for r in current) / len(current) if current else 0.0,
        "baseline_count": len(baseline),
        "baseline_share": sum(not r["voted_up"] for r in baseline) / len(baseline) if baseline else None,
    }


def score(stats, product, previous):
    baseline = stats["baseline_count"]
    before = previous.get("player_count")
    players = product.get("player_count")
    signals = {
        "review_spike": min(1.0, max(0.0, stats["review_count"] / baseline - 1) / 3) if baseline else 0.0,
        "negative_swing": max(0.0, stats["negative_share"] - stats["baseline_share"])
        if stats["baseline_share"] is not None
        else 0.0,
        "player_drop": max(0.0, (before - players) / before) if before and players is not None else 0.0,
        "price_event": float(
            previous.get("price_cents") is not None
            and product.get("price_cents") is not None
            and previous["price_cents"] != product["price_cents"]
        ),
        "patch_event": float(bool(product.get("patch_event"))),
    }
    weights = {"review_spike": 30, "negative_swing": 35, "player_drop": 20, "price_event": 10, "patch_event": 5}
    return {"controversy_score": round(sum(weights[k] * v for k, v in signals.items()), 2), "signals": signals}
