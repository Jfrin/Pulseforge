"""The contract every source implements.

Adding a source means writing a class that satisfies `SourceConnector`
(plus a field mapper later) and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Page:
    """One page of raw source output.

    `records` are raw payloads, untouched (bronze-layer material).
    `next_cursor` is an opaque token the connector understands; callers never parse it.

    When `has_more` is False, `next_cursor` is either None (a snapshot source finished a full
    pass: start over next time) or a resume token (an append-only feed: continue from here
    next time to pick up new records).
    """

    records: list[dict[str, Any]]
    next_cursor: str | None = None
    has_more: bool = False

    def __post_init__(self) -> None:
        if self.has_more and self.next_cursor is None:
            raise ValueError("has_more=True requires a next_cursor")


@runtime_checkable
class SourceConnector(Protocol):
    """`fetch(None)` returns the first page; `fetch(page.next_cursor)` the next.

    Contract:
    - Cursors are opaque strings owned by the connector.
    - Fetching the same cursor twice must be safe (re-polling is idempotent upstream).
    - Errors are raised, never swallowed; retry/backoff belongs to the caller.
    """

    name: str

    async def fetch(self, cursor: str | None) -> Page: ...
