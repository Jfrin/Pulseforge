"""Flag, minimize and tombstone records, retaining immutable decision history."""

from __future__ import annotations

import copy
import json
import os
from datetime import timedelta
from pathlib import Path

from .events import timestamp, utcnow
from .scoring import review_stats


def load_policy():
    path = os.getenv("GOVERNANCE_POLICY_FILE", "/config/governance.json")
    return json.loads(Path(path).read_text())


def govern(raw, policy, now):
    product = copy.deepcopy(raw)
    source = product["source"]
    rule = policy.get("sources", {}).get(source, {})
    flags = set(product.get("governance", {}).get("flags", []))
    suppressed = product["product_id"] in policy.get(
        "suppression", []
    ) or f"{source}:{product['external_id']}" in policy.get("suppression", [])
    erased = product["product_id"] in policy.get("erasure", [])
    if not rule.get("allow", False):
        flags.add("source_terms_blocked")
    if product.get("adult_content"):
        flags.add("adult_content")
    if suppressed:
        flags.add("suppressed")
    if erased:
        flags.add("erased")
    if product.get("redirect_to"):
        flags.add("superseded")
    reviews, tombstones = [], list(product.get("review_tombstones", []))
    for review in product.get("reviews", []):
        review_rule = policy.get("sources", {}).get(review["source"], {})
        cutoff = now - timedelta(days=review_rule.get("retention_days", 30))
        reason = "retention_expired" if timestamp(review["created_at"]) < cutoff else None
        if not review_rule.get("allow", False):
            reason = "review_source_terms_blocked"
        if review.get("reviewer") in policy.get("suppression", []):
            reason = "reviewer_suppressed"
        if reason:
            flags.add(reason)
            tombstones.append({"review_id": review["review_id"], "source": review["source"], "reason": reason})
        else:
            # No reviewer identity or content leaves governance, including pseudonyms.
            reviews.append({k: review[k] for k in ("source", "review_id", "product_id", "created_at", "voted_up")})
    blocked = bool(flags & {"source_terms_blocked", "suppressed", "erased", "superseded"})
    if blocked:
        tombstones.extend(
            {"review_id": r["review_id"], "source": r["source"], "reason": "product_blocked"} for r in reviews
        )
        product = {
            k: product[k] for k in ("product_id", "source", "external_id", "lineage", "redirect_to") if k in product
        }
        reviews = []
    product["reviews"] = reviews
    product["review_tombstones"] = list({(r["source"], r["review_id"]): r for r in tombstones}.values())
    product.update(review_stats(reviews, now))
    product["governance"] = {
        "flags": sorted(flags),
        "tombstone": blocked,
        "policy_version": policy["version"],
        "checked_at": now.isoformat(),
        "retention_days": rule.get("retention_days", 30),
    }
    return product


async def check_product(tx, product, policy, now):
    checked = govern(product, policy, now)
    decision = checked["governance"]
    decision["audit_id"] = await tx.audit(checked["product_id"], dict(decision))
    await tx.put("records", checked["product_id"], checked)
    return checked


async def handle(tx, event):
    policy, now = load_policy(), utcnow()
    checked = [await check_product(tx, product, policy, now) for product in event.payload["products"]]
    await tx.emit("GovernanceChecked", {**event.payload, "products": checked})


async def sweep(tx, *, now=None, force=False):
    now, policy = now or utcnow(), load_policy()
    checked = []
    for product in (await tx.all("records")).values():
        decision = product["governance"]
        due = timestamp(decision["checked_at"]) + timedelta(days=30) <= now
        expired = any(
            timestamp(r["created_at"])
            + timedelta(days=policy.get("sources", {}).get(r["source"], {}).get("retention_days", 30))
            < now
            for r in product["reviews"]
        )
        changed = decision["policy_version"] != policy["version"]
        if force or due or expired or changed:
            checked.append(await check_product(tx, product, policy, now))
    if checked:
        await tx.emit(
            "GovernanceChecked",
            {"batch_id": f"governance-{now:%Y%m%dT%H%M%S%f}", "source": "governance-sweep", "products": checked},
        )
