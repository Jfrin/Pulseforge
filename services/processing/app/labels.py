"""Load a hand-labeled CSV for entity-resolution evaluation.

Columns:  title, label            (required)
          developer, publisher    (optional, used as evidence)
          release_date            (optional, YYYY-MM-DD; lets the resolver tell spin-offs from editions)
          external_id             (optional; defaults to row-<line number>)

Rows that share a `label` are the SAME product (editions, remasters, DLC, bundles of one game); rows with
different labels are different products. Lines starting with # are comments.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from .mappers import map_game
from .models import Game, Lineage

REQUIRED = ("title", "label")
SOURCE = "labeled"


def load_labeled_games(path: str | Path) -> tuple[list[Game], dict[str, str]]:
    games: list[Game] = []
    truth: dict[str, str] = {}
    now = datetime.now(UTC)
    with open(path, encoding="utf-8-sig", newline="") as f:
        lines = (line for line in f if not line.lstrip().startswith("#"))
        reader = csv.DictReader(lines)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing required column(s): {', '.join(missing)}")
        for n, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            title = (row.get("title") or "").strip()
            label = (row.get("label") or "").strip()
            if not title or not label:
                raise ValueError(f"{path}: data row {n - 1}: both title and label are required")
            external_id = (row.get("external_id") or "").strip() or f"row-{n}"
            if external_id in truth:
                raise ValueError(f"{path}: duplicate external_id {external_id!r}")
            record = {
                "external_id": external_id,
                "title": title,
                "developer": row.get("developer"),
                "publisher": row.get("publisher"),
                "release_date": row.get("release_date"),
            }
            lineage = Lineage(batch_id="labels", source=SOURCE, bronze_key=None, payload_hash="")
            game, _ = map_game(record, lineage, lambda _s, raw: raw, now)
            if not isinstance(game, Game):
                raise ValueError(f"{path}: data row {n - 1}: not a usable game row")
            games.append(game)
            truth[external_id] = label
    return games, truth
