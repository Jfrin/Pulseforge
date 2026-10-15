from __future__ import annotations

from typing import Any

from .base import Page


class InMemoryConnector:
    """Deterministic connector over a list. Test double and reference implementation."""

    def __init__(
        self, name: str, records: list[dict[str, Any]], page_size: int = 100, resume_token_at_end: bool = False
    ) -> None:
        if page_size < 1:
            raise ValueError("page_size must be >= 1")
        self.name = name
        self._records = records
        self._page_size = page_size
        self._resume = resume_token_at_end

    async def fetch(self, cursor: str | None) -> Page:
        try:
            offset = int(cursor) if cursor is not None else 0
        except ValueError:
            raise ValueError(f"invalid cursor: {cursor!r}") from None
        if offset < 0:
            raise ValueError(f"invalid cursor: {cursor!r}")
        end = offset + self._page_size
        chunk = [dict(r) for r in self._records[offset:end]]
        more = end < len(self._records)
        token = str(end) if more else (str(offset + len(chunk)) if self._resume else None)
        return Page(records=chunk, next_cursor=token, has_more=more)
