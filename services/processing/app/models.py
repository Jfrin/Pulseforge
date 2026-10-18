"""Silver-layer schemas. Everything here is normalized, validated, and free of raw reviewer ids."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Lineage(_Frozen):
    """Where a silver record came from: the batch event, the raw bronze object, and the exact payload."""

    batch_id: str
    source: str
    bronze_key: str | None
    payload_hash: str


class Issue(_Frozen):
    """A validation finding. `detail` never carries raw values (they may be personal data)."""

    field: str
    code: str
    severity: Literal["error", "warning"]
    detail: str | None = None


class Game(_Frozen):
    source: str
    external_id: str
    title: str
    kind: Literal["game", "dlc", "bundle", "unknown"]
    developer: str | None
    developer_key: str | None
    publisher: str | None
    publisher_key: str | None
    price_cents: int | None
    currency: str | None
    release_date: date | None
    player_count: int | None
    observed_at: datetime | None
    lineage: Lineage
    warnings: tuple[Issue, ...] = ()


class Review(_Frozen):
    """Review text is deliberately dropped (data minimisation): only its length is kept."""

    source: str
    review_id: str
    game_ref: str
    reviewer: str  # pseudonym, never the raw id
    voted_up: bool
    created_at: datetime
    playtime_minutes: int | None
    language: str | None
    text_length: int | None
    lineage: Lineage
    warnings: tuple[Issue, ...] = ()


class Reject(_Frozen):
    """A record that failed validation. Kept (never silently dropped) and traceable via its lineage."""

    source: str
    batch_id: str
    external_id: str | None
    payload_hash: str | None
    bronze_key: str | None
    issues: tuple[Issue, ...]


class BatchResult(BaseModel):
    batch_id: str
    source: str
    games: list[Game] = Field(default_factory=list)
    reviews: list[Review] = Field(default_factory=list)
    rejects: list[Reject] = Field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {"games": len(self.games), "reviews": len(self.reviews), "rejects": len(self.rejects)}
