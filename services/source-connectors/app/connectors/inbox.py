from __future__ import annotations

from typing import Any, Protocol

from .base import Page


class InboxReader(Protocol):
    async def read(self, source: str, after_seq: int, limit: int) -> list[tuple[int, dict[str, Any]]]: ...


class InboxConnector:
    """Presents pushed records (the webhook inbox) as an ordinary SourceConnector.

    Cursor = last sequence number read. This is an append-only feed, so even the last page carries
    a resume token: the next poll continues from there and picks up whatever arrived since.
    """

    def __init__(self, name: str, reader: InboxReader, page_size: int = 200) -> None:
        if page_size < 1:
            raise ValueError("page_size must be >= 1")
        self.name = name
        self._reader = reader
        self._page_size = page_size

    async def fetch(self, cursor: str | None) -> Page:
        after = self._parse(cursor)
        rows = await self._reader.read(self.name, after, self._page_size + 1)
        more = len(rows) > self._page_size
        rows = rows[: self._page_size]
        token = rows[-1][0] if rows else after
        return Page(records=[payload for _, payload in rows], next_cursor=str(token), has_more=more)

    @staticmethod
    def _parse(cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            value = int(cursor)
        except ValueError:
            raise ValueError(f"invalid cursor: {cursor!r}") from None
        if value < 0:
            raise ValueError(f"invalid cursor: {cursor!r}")
        return value
