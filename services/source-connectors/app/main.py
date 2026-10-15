from __future__ import annotations

import asyncio
import logging
import signal

from .config import Config
from .connectors import MockSourceConnector
from .poller import Poller
from .store import Store


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.from_env()

    store = Store(cfg.database_url)
    await store.open()
    await store.migrate()

    games = MockSourceConnector("mock-games", cfg.mock_source_url, "/games", cfg.page_size)
    reviews = MockSourceConnector("mock-reviews", cfg.mock_source_url, "/reviews", cfg.page_size)
    connectors = [games, reviews]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # Snapshot catalogs re-emit everything each pass, so poll them slowly; feeds poll fast.
    pollers = [
        Poller(games, store, cfg.catalog_interval_s, cfg.retry_delay_s),
        Poller(reviews, store, cfg.poll_interval_s, cfg.retry_delay_s),
    ]
    try:
        await asyncio.gather(*(p.run_forever(stop) for p in pollers))
    finally:
        for c in connectors:
            await c.aclose()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
