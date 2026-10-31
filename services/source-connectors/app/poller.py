from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from .bronze import BronzeSink, NullBronze
from .connectors import SourceConnector, iter_pages
from .results import CommitResult, PassResult

log = logging.getLogger("poller")


class CheckpointStore(Protocol):
    async def get_cursor(self, source: str) -> str | None: ...

    async def any_unseen(
        self,
        source: str,
        records: list[dict[str, Any]],
        id_field: str = ...,
        ignore_fields: Sequence[str] = ...,
    ) -> bool: ...

    async def commit_page(
        self,
        source: str,
        *,
        fetched_cursor: str | None,
        next_cursor: str | None,
        records: list[dict[str, Any]],
        id_field: str = ...,
        bronze_key: str | None = ...,
        ignore_fields: Sequence[str] = ...,
    ) -> CommitResult: ...


class Poller:
    """Walks one connector from its saved checkpoint, landing then committing every page.

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
        ignore_fields: Sequence[str] = (),
        bronze: BronzeSink | None = None,
        poll_interval_s: float = 15.0,
        retry_delay_s: float = 5.0,
    ) -> None:
        self._connector = connector
        self._store = store
        self._id_field = id_field
        self._ignore_fields = tuple(ignore_fields)
        self._bronze: BronzeSink = bronze or NullBronze()
        self._poll_interval_s = poll_interval_s
        self._retry_delay_s = retry_delay_s
        self.before_fetch: Callable[[], Awaitable[None]] | None = None
        self.report: Callable[[str, bool, int, int], Awaitable[None]] | None = None

    async def poll_once(self) -> PassResult:
        """Run one pass until the source says it has no more."""
        name = self._connector.name
        start = await self._store.get_cursor(name)
        new = duplicates = 0
        async for cursor, page in iter_pages(self._connector, start_cursor=start, before_fetch=self.before_fetch):
            # Land the raw page FIRST. If object storage is down the pass fails here, the checkpoint
            # stays put and the page is fetched again later: no record ever skips bronze.
            # Pages with nothing new (a snapshot source re-read) are not landed again.
            bronze_key = None
            if page.records and await self._store.any_unseen(name, page.records, self._id_field, self._ignore_fields):
                bronze_key = await self._bronze.land_page(name, fetched_cursor=cursor, page=page)
            result = await self._store.commit_page(
                name,
                fetched_cursor=cursor,
                next_cursor=page.next_cursor,
                records=page.records,
                id_field=self._id_field,
                bronze_key=bronze_key,
                ignore_fields=self._ignore_fields,
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
                if self.report:
                    await self.report(name, True, res.new, res.duplicates)
            except Exception as exc:  # checkpoint was not advanced past the failed page
                delay = self._retry_delay_s
                log.warning("source=%s pass failed (%s: %s); retrying in %.1fs", name, type(exc).__name__, exc, delay)
                if self.report:
                    try:
                        await self.report(name, False, 0, 0)
                    except Exception:
                        log.exception("could not persist source health")
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
