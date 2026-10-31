"""Live BatchCreated consumer. Resolver history and silver state belong to this service."""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta

from psycopg.types.json import Jsonb
from pulseforge.events import Event, timestamp, utcnow
from pulseforge.scoring import review_stats, score

from .models import Game
from .pipeline import process_batch
from .pseudonymize import Pseudonymizer
from .resolve import Resolver


async def handle(tx, event):
    now = utcnow()
    result = process_batch(event.payload, Pseudonymizer(os.environ["PSEUDONYM_SALT"]), now=now)
    catalog = await tx.get("silver", "catalog", {"order": [], "first": {}, "latest": {}})
    resolver = Resolver()
    for key in catalog["order"]:
        resolver.resolve(Game.model_validate(catalog["first"][key]))
    affected = set()
    for game in result.games:
        key = f"{game.source}:{game.external_id}"
        data = game.model_dump(mode="json")
        if key not in catalog["first"]:
            catalog["order"].append(key)
            catalog["first"][key] = data
        catalog["latest"][key] = data
        affected.add(resolver.resolve(game).product_id)
    assignments = resolver.assignments()
    reviews = await tx.get("silver", "reviews", {})
    old_assignments = {key: review.get("product_id") for key, review in reviews.items()}
    old_refs = {key: review["game_ref"] for key, review in reviews.items()}
    redirects = {}
    moved_reviews = {}
    for review in result.reviews:
        reviews[f"{review.source}:{review.review_id}"] = review.model_dump(mode="json")
    # Bound silver review retention; raw content is never present in silver.
    keep_after = now - timedelta(days=int(os.getenv("SILVER_RETENTION_DAYS", "30")))
    reviews = {k: v for k, v in reviews.items() if timestamp(v["created_at"]) >= keep_after}
    for key, review in reviews.items():
        source = {"mock-reviews": "mock-games", "steam-reviews": "steam-games"}.get(review["source"], review["source"])
        pid = assignments.get((source, review["game_ref"]))
        if pid is None:
            pid = "p_" + hashlib.sha256(f"{source}\x1f{review['game_ref']}".encode()).hexdigest()[:12]
        old_pid = old_assignments.get(key)
        if old_pid and old_pid != pid:
            if old_refs[key] == review["game_ref"]:
                redirects[old_pid] = pid
            moved_reviews.setdefault(old_pid, []).append(
                {"source": review["source"], "review_id": review["review_id"], "reason": "reassigned"}
            )
        review["product_id"] = pid
        # Re-emit affected aggregates when late catalog records resolve earlier reviews.
        affected.add(pid)
    products = {}
    for key in catalog["order"]:
        game = catalog["latest"][key]
        pid = assignments[(game["source"], game["external_id"])]
        products.setdefault(pid, {**game, "product_id": pid, "members": []})
        products[pid]["members"].append({"source": game["source"], "external_id": game["external_id"]})
    for canonical in resolver.products():
        products[canonical.product_id]["title"] = canonical.title
    # A review edit can change its game reference without retiring the old catalog product.
    # Recompute that product and tell downstream consumers to remove the old assignment.
    remaining = {review["product_id"] for review in reviews.values()}
    retired = {pid for pid in redirects if pid not in products and pid not in remaining}
    affected.update(set(moved_reviews) - retired)
    output = []
    for pid in sorted(affected):
        related = [r for r in reviews.values() if r["product_id"] == pid]
        previous = await tx.get("products", pid, {})
        if pid not in products:
            if related:
                ref = related[0]
                products[pid] = {
                    "product_id": pid,
                    "title": None,
                    "source": ref["source"],
                    "external_id": ref["game_ref"],
                    "lineage": ref["lineage"],
                }
            else:
                products[pid] = dict(previous)
                products[pid].pop("review_tombstones", None)
        product = products[pid]
        stats = review_stats(related, now)
        product.update(stats)
        product.update(score(stats, product, previous))
        product["reviews"] = related
        if pid in moved_reviews:
            product["review_tombstones"] = moved_reviews[pid]
        await tx.put("products", pid, product)
        output.append(product)
    for old_pid, new_pid in redirects.items():
        previous = await tx.get("products", old_pid)
        if previous and old_pid in retired:
            previous.update(redirect_to=new_pid, reviews=[], review_count=0, negative_count=0, negative_share=0)
            await tx.put("products", old_pid, previous)
            output.append(previous)
    for index, reject in enumerate(result.rejects):
        # Preserve the single-record envelope for replay after a mapper/data fix.
        key_index = next(
            i
            for i, k in enumerate(event.payload["keys"])
            if str(k.get("payload_hash", "")) == (reject.payload_hash or "")
        )
        payload = {
            **event.payload,
            "records": [event.payload["records"][key_index]],
            "keys": [event.payload["keys"][key_index]],
            "record_count": 1,
        }
        rejected = Event(id=f"{event.id}:reject:{index}", type="BatchCreated", source=event.source, payload=payload)
        await tx.conn.execute(
            "INSERT INTO dead_letters(id,event,reason) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (rejected.id, Jsonb(rejected.model_dump(mode="json")), "validation_failed"),
        )
    await tx.put("silver", "catalog", catalog)
    await tx.put("silver", "reviews", reviews)
    await tx.emit(
        "ProductsScored",
        {
            "batch_id": result.batch_id,
            "source": result.source,
            "products": output,
            "rejects": [r.model_dump(mode="json") for r in result.rejects],
        },
    )
