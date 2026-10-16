from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from .results import Batch

log = logging.getLogger("batcher")


class BatchStore(Protocol):
    async def flush_batch(self, source: str, *, max_size: int, max_age_s: float) -> Batch | None: ...


class Batcher:
    """Cuts staged records into micro-batches: `max_size` records or `max_age_s` seconds, whichever first."""

    def __init__(
        self,
        store: BatchStore,
        sources: list[str],
        *,
        max_size: int = 500,
        max_age_s: float = 300.0,
        tick_s: float = 5.0,
    ) -> None:
        self._store = store
        self._sources = sources
        self._max_size = max_size
        self._max_age_s = max_age_s
        self._tick_s = tick_s

    async def flush_all_due(self) -> list[Batch]:
        """Cut every batch that is due right now (a backlog can yield several size batches in a row)."""
        out: list[Batch] = []
        for source in self._sources:
            while True:
                batch = await self._store.flush_batch(source, max_size=self._max_size, max_age_s=self._max_age_s)
                if batch is None:
                    break
                out.append(batch)
                log.info(
                    "source=%s batch=%s trigger=%s records=%d",
                    batch.source,
                    batch.batch_id,
                    batch.trigger,
                    batch.record_count,
                )
        return out

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.flush_all_due()
            except Exception as exc:  # staged records stay put; the next tick retries
                log.warning("batcher tick failed (%s: %s)", type(exc).__name__, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._tick_s)
            except TimeoutError:
                pass
