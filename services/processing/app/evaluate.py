"""Measure entity resolution against the mock source's ground truth.

    docker compose --profile tools run --rm processing python -m app.evaluate

Pulls /games and /admin/ground-truth from the mock source, normalizes the games, resolves them, and
prints pairwise precision/recall plus examples of wrong merges and splits.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx

from .mappers import map_game
from .metrics import Metrics, find_errors, pairwise_metrics
from .models import Game, Lineage
from .resolve import Resolver

SOURCE = "mock-games"


def fetch_all(client: httpx.Client, path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 500}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(path, params=params)
        resp.raise_for_status()
        body = resp.json()
        items += body["items"]
        if not body["has_more"]:
            return items
        cursor = body["next_cursor"]


def normalize_games(raw: list[dict[str, Any]]) -> list[Game]:
    now = datetime.now(UTC)
    games: list[Game] = []
    for rec in raw:
        lineage = Lineage(batch_id="evaluation", source=SOURCE, bronze_key=None, payload_hash="")
        game, _ = map_game(rec, lineage, lambda _s, raw_id: raw_id, now)
        if isinstance(game, Game):
            games.append(game)
    return games


@dataclass
class Report:
    metrics: Metrics
    games: dict[tuple[str, str], Game]
    false_merges: list[tuple[tuple[str, str], tuple[str, str]]]
    false_splits: list[tuple[tuple[str, str], tuple[str, str]]]
    threshold: float

    def render(self) -> str:
        m = self.metrics
        lines = [
            f"entity resolution on {m.items} records (threshold {self.threshold:g})",
            f"  precision {m.precision:.4f}   recall {m.recall:.4f}   F1 {m.f1:.4f}",
            f"  products {m.predicted_clusters} predicted vs {m.true_clusters} true",
            f"  pairs: {m.correct_pairs} correct of {m.predicted_pairs} predicted, {m.true_pairs} true",
        ]

        def show(title: str, pairs: list[tuple[tuple[str, str], tuple[str, str]]]) -> None:
            if not pairs:
                return
            lines.append(title)
            for a, b in pairs:
                ga, gb = self.games[a], self.games[b]
                lines.append(f"  {ga.title!r} ({ga.developer})  <->  {gb.title!r} ({gb.developer})")

        show("wrongly MERGED (hurts precision):", self.false_merges)
        show("wrongly SPLIT (hurts recall):", self.false_splits)
        return "\n".join(lines)


def evaluate(
    games: list[Game], truth_by_external_id: dict[str, str], threshold: float = 90.0, examples: int = 8
) -> Report:
    resolver = Resolver(threshold=threshold, review_threshold=max(1.0, threshold - 10.0))
    for game in games:
        resolver.resolve(game)
    predicted = resolver.assignments()
    truth = {
        (g.source, g.external_id): truth_by_external_id[g.external_id]
        for g in games
        if g.external_id in truth_by_external_id
    }
    merges, splits = find_errors(predicted, truth, limit=examples)
    return Report(
        metrics=pairwise_metrics(predicted, truth),
        games={(g.source, g.external_id): g for g in games},
        false_merges=merges,
        false_splits=splits,
        threshold=threshold,
    )


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate entity resolution against the mock source ground truth")
    parser.add_argument("--url", default=os.getenv("MOCK_SOURCE_URL", "http://mock-source:8000"))
    parser.add_argument("--threshold", type=float, default=90.0)
    parser.add_argument("--examples", type=int, default=8)
    args = parser.parse_args(argv)

    http = client or httpx.Client(base_url=args.url, timeout=30.0)
    games = normalize_games(fetch_all(http, "/games"))
    truth_resp = http.get("/admin/ground-truth")
    truth_resp.raise_for_status()
    print(evaluate(games, truth_resp.json(), args.threshold, args.examples).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
