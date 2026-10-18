"""Per-source field mappers: raw record -> canonical Game or Review, plus validation findings.

Policy: a missing or malformed REQUIRED field is an error and the record is rejected (kept, with its
lineage, never dropped silently); a malformed OPTIONAL field becomes null plus a warning.
Issue details never contain raw values.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .models import Game, Issue, Lineage, Review
from .normalize import (
    clean_text,
    is_missing,
    normalize_company,
    normalize_currency,
    normalize_kind,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_int,
)
from .pseudonymize import PseudonymizeFn

CLOCK_SKEW = timedelta(minutes=5)
MapOutcome = tuple[Game | Review | None, list[Issue]]
Mapper = Callable[[dict[str, Any], Lineage, PseudonymizeFn, datetime], MapOutcome]


def _required(issues: list[Issue], field: str, raw: Any, parsed: Any) -> None:
    if parsed is None:
        code = "missing" if is_missing(raw) else "malformed"
        issues.append(Issue(field=field, code=code, severity="error"))


def _optional_int(issues: list[Issue], field: str, raw: Any, *, minimum: int | None = None) -> int | None:
    if is_missing(raw):
        return None
    value = parse_int(raw)
    if value is None:
        issues.append(Issue(field=field, code="malformed", severity="warning"))
        return None
    if minimum is not None and value < minimum:
        issues.append(Issue(field=field, code="out_of_range", severity="warning", detail=f"must be >= {minimum}"))
        return None
    return value


def _has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def _not_future(issues: list[Issue], field: str, when: datetime | None, now: datetime) -> None:
    if when is not None and when > now + CLOCK_SKEW:
        issues.append(Issue(field=field, code="in_future", severity="error"))


# ---------------------------------------------------------------- games (mock-games, csv-import)
def map_game(rec: dict[str, Any], lineage: Lineage, pseudonymize: PseudonymizeFn, now: datetime) -> MapOutcome:
    issues: list[Issue] = []
    external_id = clean_text(rec.get("external_id"))
    title = clean_text(rec.get("title"))
    _required(issues, "external_id", rec.get("external_id"), external_id)
    _required(issues, "title", rec.get("title"), title)

    developer, developer_key = normalize_company(rec.get("developer"))
    publisher, publisher_key = normalize_company(rec.get("publisher"))
    price_cents = _optional_int(issues, "price_cents", rec.get("price_cents"), minimum=0)
    player_count = _optional_int(issues, "player_count", rec.get("player_count"), minimum=0)

    raw_currency = rec.get("currency")
    currency = normalize_currency(raw_currency)
    if not is_missing(raw_currency) and currency is None:
        issues.append(Issue(field="currency", code="malformed", severity="warning"))

    raw_release = rec.get("release_date")
    release_date = parse_date(raw_release)
    if not is_missing(raw_release) and release_date is None:
        issues.append(Issue(field="release_date", code="malformed", severity="warning"))

    raw_observed = rec.get("updated_at")
    observed_at = parse_datetime(raw_observed)
    if not is_missing(raw_observed) and observed_at is None:
        issues.append(Issue(field="updated_at", code="malformed", severity="warning"))

    if _has_errors(issues) or external_id is None or title is None:
        return None, issues
    game = Game(
        source=lineage.source,
        external_id=external_id,
        title=title,
        kind=normalize_kind(rec.get("type")),  # type: ignore[arg-type]
        developer=developer,
        developer_key=developer_key,
        publisher=publisher,
        publisher_key=publisher_key,
        price_cents=price_cents,
        currency=currency,
        release_date=release_date,
        player_count=player_count,
        observed_at=observed_at,
        lineage=lineage,
        warnings=tuple(issues),
    )
    return game, issues


# ---------------------------------------------------------------- reviews
def _finish_review(
    issues: list[Issue],
    lineage: Lineage,
    pseudonymize: PseudonymizeFn,
    *,
    review_id: str | None,
    game_ref: str | None,
    raw_reviewer: str | None,
    voted_up: bool | None,
    created_at: datetime | None,
    playtime_minutes: int | None,
    language: str | None,
    text_length: int | None,
) -> MapOutcome:
    if _has_errors(issues) or None in (review_id, game_ref, raw_reviewer, voted_up, created_at):
        return None, issues
    assert review_id and game_ref and raw_reviewer and voted_up is not None and created_at  # for mypy
    review = Review(
        source=lineage.source,
        review_id=review_id,
        game_ref=game_ref,
        reviewer=pseudonymize(lineage.source, raw_reviewer),
        voted_up=voted_up,
        created_at=created_at,
        playtime_minutes=playtime_minutes,
        language=language,
        text_length=text_length,
        lineage=lineage,
        warnings=tuple(issues),
    )
    return review, issues


def map_mock_review(rec: dict[str, Any], lineage: Lineage, pseudonymize: PseudonymizeFn, now: datetime) -> MapOutcome:
    issues: list[Issue] = []
    review_id = clean_text(rec.get("review_id"))
    game_ref = clean_text(rec.get("game_id"))
    reviewer = clean_text(rec.get("reviewer_id"))
    voted_up = parse_bool(rec.get("voted_up"))
    created_at = parse_datetime(rec.get("created_at"))
    _required(issues, "review_id", rec.get("review_id"), review_id)
    _required(issues, "game_id", rec.get("game_id"), game_ref)
    _required(issues, "reviewer_id", rec.get("reviewer_id"), reviewer)
    _required(issues, "voted_up", rec.get("voted_up"), voted_up)
    _required(issues, "created_at", rec.get("created_at"), created_at)
    _not_future(issues, "created_at", created_at, now)

    playtime: int | None = None
    hours = rec.get("playtime_hours")
    if not is_missing(hours):
        if isinstance(hours, bool) or not isinstance(hours, int | float) or hours < 0:
            issues.append(Issue(field="playtime_hours", code="malformed", severity="warning"))
        else:
            playtime = round(hours * 60)

    text = rec.get("text")
    return _finish_review(
        issues, lineage, pseudonymize,
        review_id=review_id, game_ref=game_ref, raw_reviewer=reviewer, voted_up=voted_up,
        created_at=created_at, playtime_minutes=playtime, language=None,
        text_length=len(text) if isinstance(text, str) else None,
    )  # fmt: skip


def map_steam_review(rec: dict[str, Any], lineage: Lineage, pseudonymize: PseudonymizeFn, now: datetime) -> MapOutcome:
    issues: list[Issue] = []
    raw_author = rec.get("author")
    author: dict[str, Any] = raw_author if isinstance(raw_author, dict) else {}
    review_id = clean_text(rec.get("recommendationid"))
    game_ref = clean_text(rec.get("app_id"))
    reviewer = clean_text(author.get("steamid"))
    voted_up = parse_bool(rec.get("voted_up"))
    created_at = parse_datetime(rec.get("timestamp_created"))
    _required(issues, "recommendationid", rec.get("recommendationid"), review_id)
    _required(issues, "app_id", rec.get("app_id"), game_ref)
    _required(issues, "author.steamid", author.get("steamid"), reviewer)
    _required(issues, "voted_up", rec.get("voted_up"), voted_up)
    _required(issues, "timestamp_created", rec.get("timestamp_created"), created_at)
    _not_future(issues, "timestamp_created", created_at, now)

    playtime = _optional_int(issues, "author.playtime_at_review", author.get("playtime_at_review"), minimum=0)
    text = rec.get("review")
    return _finish_review(
        issues, lineage, pseudonymize,
        review_id=review_id, game_ref=game_ref, raw_reviewer=reviewer, voted_up=voted_up,
        created_at=created_at, playtime_minutes=playtime, language=clean_text(rec.get("language")),
        text_length=len(text) if isinstance(text, str) else None,
    )  # fmt: skip


# ---------------------------------------------------------------- registry
MAPPERS: dict[str, Mapper] = {
    "mock-games": map_game,
    "csv-import": map_game,  # assumes CSV imports carry game rows; register another mapper for other shapes
    "mock-reviews": map_mock_review,
    "steam-reviews": map_steam_review,
}


def register_mapper(source: str, mapper: Mapper) -> None:
    """Teach the pipeline a new source: this is the whole 'field mapper' step of adding a connector."""
    MAPPERS[source] = mapper


def mapper_for(source: str) -> Mapper | None:
    return MAPPERS.get(source)
