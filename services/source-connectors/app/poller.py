from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from .connectors import SourceConnector, iter_pages
from .results import CommitResult, PassResult

log = logging.getLogger("poller")


class CheckpointStore(Protocol):
    async def get_cursor(self, source: str) -> str | None: ...

    async def commit_page(
        self,
        source: str,
        *,
        fetched_cursor: str | None,
        next_cursor: str | None,
        records: list[dict[str, Any]],
        id_field: str = ...,
    ) -> CommitResult: ...


class Poller:
    """Walks one connector from its saved checkpoint, committing after every page.

    The checkpoint is only advanced by `commit_page` (same transaction as the ledger and
    outbox writes), and the next page is only fetched after that commit, so after any crash
    the poller resumes at the first page that was not durably committed.
    """

    def __init__(
        self,
        connector: SourceConnector,
        store: CheckpointStore,
        *,
        id_field: str = "external_id",
        poll_interval_s: float = 15.0,
        retry_delay_s: float = 5.0,
    ) -> None:
        self._connector = connector
        self._store = store
        self._id_field = id_field
        self._poll_interval_s = poll_interval_s
        self._retry_delay_s = retry_delay_s

    async def poll_once(self) -> PassResult:
        """Run one pass until the source says it has no more."""
        name = self._connector.name
        start = await self._store.get_cursor(name)
        new = duplicates = 0
        async for cursor, page in iter_pages(self._connector, start_cursor=start):
            result = await self._store.commit_page(
                name,
                fetched_cursor=cursor,
                next_cursor=page.next_cursor,
                records=page.records,
                id_field=self._id_field,
            )
            new += result.new
            duplicates += result.duplicates
        return PassResult(new=new, duplicates=duplicates)

    async def run_forever(self, stop: asyncio.Event) -> None:
        name = self._connector.name
        while not stop.is_set():
            delay = self._poll_interval_s
            try:
                res = await self.poll_once()
                log.info("source=%s pass complete new=%d duplicates=%d", name, res.new, res.duplicates)
            except Exception as exc:  # checkpoint was not advanced past the failed page
                delay = self._retry_delay_s
                log.warning("source=%s pass failed (%s: %s); retrying in %.1fs", name, type(exc).__name__, exc, delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
