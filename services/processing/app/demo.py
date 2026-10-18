"""`docker compose run --rm processing` shows the core on a small synthetic batch."""

from __future__ import annotations

import os
from typing import Any

from .pipeline import process_batch
from .pseudonymize import Pseudonymizer


def _payload(batch_id: str, source: str, records: list[dict[str, Any]], id_field: str, bronze: str) -> dict[str, Any]:
    keys = [
        {"external_id": r[id_field], "payload_hash": f"{batch_id}-{i}", "bronze_key": bronze}
        for i, r in enumerate(records)
    ]
    return {"batch_id": batch_id, "source": source, "records": records, "keys": keys}


def build_games_payload() -> dict[str, Any]:
    records: list[dict[str, Any]] = [
        {
            "external_id": "g-1",
            "title": "  Neon   Harbor ",
            "type": "Game",
            "developer": "Vega Interactive Inc.",
            "publisher": "VEGA INTERACTIVE",
            "price_cents": "1999",
            "currency": "usd",
            "release_date": "2021-03-12",
            "player_count": 1840,
        },
        {
            "external_id": "g-2",
            "title": "Neon Harbor: Deluxe Edition",
            "type": "game",
            "developer": "VegaInteractive",
            "price_cents": 2999,
            "currency": "USD",
            "release_date": "2021-09-30",
        },
        {"external_id": "g-3", "title": "", "price_cents": 999},
        {"external_id": "g-4", "title": "Paper Lanterns", "price_cents": "free", "release_date": "someday"},
    ]
    return _payload("demo-games", "mock-games", records, "external_id", "bronze/demo/games.json")


def build_reviews_payload() -> dict[str, Any]:
    records: list[dict[str, Any]] = [
        {
            "review_id": "r-1",
            "game_id": "g-1",
            "reviewer_id": "7656119800000001",
            "voted_up": False,
            "text": "refunded",
            "playtime_hours": 1.5,
            "created_at": "2026-10-01T12:00:00Z",
        },
        {
            "review_id": "r-2",
            "game_id": "g-1",
            "reviewer_id": "",
            "voted_up": True,
            "created_at": "2026-10-01T12:05:00Z",
        },
    ]
    return _payload("demo-reviews", "mock-reviews", records, "review_id", "bronze/demo/reviews.json")


def main() -> None:
    pseudo = Pseudonymizer(os.getenv("PSEUDONYM_SALT", "demo-only-salt-not-for-production"))
    for payload in (build_games_payload(), build_reviews_payload()):
        result = process_batch(payload, pseudo)
        print(f"{result.batch_id}: {result.summary()}")
        for game in result.games:
            print(f"  game   {game.external_id} {game.title!r} dev_key={game.developer_key} price={game.price_cents}")
        for review in result.reviews:
            print(f"  review {review.review_id} reviewer={review.reviewer} up={review.voted_up}")
        for reject in result.rejects:
            print(f"  REJECT {reject.external_id}: " + ", ".join(f"{i.field}:{i.code}" for i in reject.issues))


if __name__ == "__main__":
    main()
