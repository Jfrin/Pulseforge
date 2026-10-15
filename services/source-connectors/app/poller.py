from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from .connectors import SourceConnector, iter_pages

log = logging.getLogger("poller")


class CheckpointStore(Protocol):
    async def get_cursor(self, source: str) -> str | None: ...

    async def commit_page(
        self, source: str, *, fetched_cursor: str | None, next_cursor: str | None, records: list[dict[str, Any]]
    ) -> None: ...


class Poller:
    """Walks one connector from its saved checkpoint, committing after every page.

    The checkpoint is only advanced by `commit_page` (same transaction as the outbox write),
    and the next page is only fetched after that commit, so after any crash the poller
    resumes at the first page that was not durably committed.
    """

    def __init__(
        self,
        connector: SourceConnector,
        store: CheckpointStore,
        poll_interval_s: float = 15.0,
        retry_delay_s: float = 5.0,
    ) -> None:
        self._connector = connector
        self._store = store
        self._poll_interval_s = poll_interval_s
        self._retry_delay_s = retry_delay_s

    async def poll_once(self) -> int:
        """Run one pass until the source says it has no more. Returns records emitted."""
        name = self._connector.name
        start = await self._store.get_cursor(name)
        emitted = 0
        async for cursor, page in iter_pages(self._connector, start_cursor=start):
            await self._store.commit_page(
                name, fetched_cursor=cursor, next_cursor=page.next_cursor, records=page.records
            )
            emitted += len(page.records)
        return emitted

    async def run_forever(self, stop: asyncio.Event) -> None:
        name = self._connector.name
        while not stop.is_set():
            delay = self._poll_interval_s
            try:
                n = await self.poll_once()
                log.info("source=%s pass complete records=%d", name, n)
            except Exception as exc:  # checkpoint was not advanced past the failed page
                delay = self._retry_delay_s
                log.warning("source=%s pass failed (%s: %s); retrying in %.1fs", name, type(exc).__name__, exc, delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
