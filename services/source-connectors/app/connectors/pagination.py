from __future__ import annotations

from collections.abc import AsyncIterator

from .base import Page, SourceConnector


async def iter_pages(
    connector: SourceConnector,
    start_cursor: str | None = None,
    max_pages: int | None = None,
) -> AsyncIterator[tuple[str | None, Page]]:
    """Walk a connector page by page, yielding `(cursor_used, page)`.

    Yielding the cursor used lets a poller checkpoint exactly where it is.
    Raises if a connector returns the cursor it was just given (infinite loop guard).
    """
    cursor = start_cursor
    count = 0
    while True:
        page = await connector.fetch(cursor)
        yield cursor, page
        count += 1
        if not page.has_more:
            return
        if max_pages is not None and count >= max_pages:
            return
        if page.next_cursor == cursor:
            raise RuntimeError(f"connector {connector.name!r} returned the same cursor {cursor!r}; refusing to loop")
        cursor = page.next_cursor
