from __future__ import annotations

import asyncio
import logging
import signal

from .batcher import Batcher
from .bronze import BronzeSink, MinioBronze, NullBronze
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

    bronze: BronzeSink = NullBronze()
    if cfg.minio_endpoint:
        minio_bronze = MinioBronze(
            cfg.minio_endpoint,
            cfg.minio_access_key,
            cfg.minio_secret_key,
            cfg.minio_bucket,
            secure=cfg.minio_secure,
        )
        await minio_bronze.ensure_bucket()
        bronze = minio_bronze
    else:
        logging.getLogger("main").warning("MINIO_ENDPOINT not set: bronze landing is OFF")

    games = MockSourceConnector("mock-games", cfg.mock_source_url, "/games", cfg.page_size)
    reviews = MockSourceConnector("mock-reviews", cfg.mock_source_url, "/reviews", cfg.page_size)
    connectors = [games, reviews]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # Snapshot catalogs re-emit everything each pass, so poll them slowly; feeds poll fast.
    pollers = [
        Poller(
            games,
            store,
            id_field="external_id",
            bronze=bronze,
            poll_interval_s=cfg.catalog_interval_s,
            retry_delay_s=cfg.retry_delay_s,
        ),
        Poller(
            reviews,
            store,
            id_field="review_id",
            bronze=bronze,
            poll_interval_s=cfg.poll_interval_s,
            retry_delay_s=cfg.retry_delay_s,
        ),
    ]
    try:
        batcher = Batcher(
            store,
            [games.name, reviews.name],
            max_size=cfg.batch_size,
            max_age_s=cfg.batch_max_age_s,
            tick_s=cfg.batch_tick_s,
        )
        await asyncio.gather(batcher.run_forever(stop), *(p.run_forever(stop) for p in pollers))
    finally:
        for c in connectors:
            await c.aclose()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
