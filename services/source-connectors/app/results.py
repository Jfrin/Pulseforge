from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CommitResult:
    """Outcome of committing one page: records that were new vs already seen."""

    new: int
    duplicates: int


@dataclass(frozen=True, slots=True)
class PassResult:
    """Outcome of one full polling pass."""

    new: int
    duplicates: int


@dataclass(frozen=True, slots=True)
class Batch:
    """A cut micro-batch. `trigger` is 'size' (hit the record limit) or 'time' (oldest record aged out)."""

    batch_id: str
    source: str
    trigger: str
    record_count: int


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """One row of the outbox table, as read by a relay (mirrors the table's columns)."""

    id: int
    event_type: str
    source: str
    payload: dict[str, Any]
