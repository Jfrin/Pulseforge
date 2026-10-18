"""Turn one BatchCreated event payload into silver records, rejects, and lineage."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .mappers import mapper_for
from .models import BatchResult, Game, Issue, Lineage, Reject, Review
from .normalize import clean_text
from .pseudonymize import PseudonymizeFn

log = logging.getLogger("processing")


def process_batch(payload: dict[str, Any], pseudonymize: PseudonymizeFn, *, now: datetime | None = None) -> BatchResult:
    """`payload` is the BatchCreated event body: batch_id, source, records[], keys[] (aligned with records)."""
    now = now or datetime.now(UTC)
    try:
        batch_id, source = str(payload["batch_id"]), str(payload["source"])
    except KeyError as exc:
        raise ValueError(f"not a BatchCreated payload: missing {exc.args[0]!r}") from None
    records = payload.get("records") or []
    keys = payload.get("keys") or []
    if len(keys) != len(records):
        raise ValueError("BatchCreated payload has records and keys of different lengths")

    mapper = mapper_for(source)
    result = BatchResult(batch_id=batch_id, source=source)
    for record, key in zip(records, keys, strict=True):
        lineage = Lineage(
            batch_id=batch_id,
            source=source,
            bronze_key=key.get("bronze_key"),
            payload_hash=str(key.get("payload_hash", "")),
        )
        mapped: Game | Review | None
        if mapper is None:
            mapped, issues = None, [Issue(field="source", code="no_mapper", severity="error")]
        else:
            try:
                mapped, issues = mapper(record, lineage, pseudonymize, now)
            except Exception:  # a mapper bug must not take the whole batch down
                log.exception("mapper for %s crashed (payload_hash=%s)", source, lineage.payload_hash)
                mapped, issues = None, [Issue(field="record", code="mapper_error", severity="error")]
        if isinstance(mapped, Game):
            result.games.append(mapped)
        elif isinstance(mapped, Review):
            result.reviews.append(mapped)
        else:
            result.rejects.append(
                Reject(
                    source=source,
                    batch_id=batch_id,
                    external_id=clean_text(key.get("external_id")),
                    payload_hash=lineage.payload_hash or None,
                    bronze_key=lineage.bronze_key,
                    issues=tuple(issues),
                )
            )
    return result
