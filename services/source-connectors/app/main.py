from __future__ import annotations

import asyncio
import logging
import signal

from .batcher import Batcher
from .bronze import BronzeSink, MinioBronze, NullBronze
from .config import Config
from .connectors import STEAM_VOLATILE_FIELDS, CsvDirectoryConnector, MockSourceConnector, SteamReviewsConnector
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
    connectors: list[MockSourceConnector | SteamReviewsConnector] = [games, reviews]
    csv_import = CsvDirectoryConnector("csv-import", cfg.csv_import_dir, cfg.page_size) if cfg.csv_import_dir else None

    steam = (
        SteamReviewsConnector(
            "steam-reviews",
            cfg.steam_app_ids,
            max_pages_per_app=cfg.steam_max_pages,
            min_interval_s=cfg.steam_min_interval_s,
        )
        if cfg.steam_app_ids
        else None
    )
    if steam:
        connectors.append(steam)

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
    sources = [games.name, reviews.name]
    if csv_import:
        pollers.append(
            Poller(
                csv_import,
                store,
                id_field=cfg.csv_id_field,
                bronze=bronze,
                poll_interval_s=cfg.csv_interval_s,
                retry_delay_s=cfg.retry_delay_s,
            )
        )
        sources.append(csv_import.name)
    if steam:
        pollers.append(
            Poller(
                steam,
                store,
                id_field="recommendationid",
                ignore_fields=STEAM_VOLATILE_FIELDS,
                bronze=bronze,
                poll_interval_s=cfg.steam_interval_s,
                retry_delay_s=cfg.retry_delay_s,
            )
        )
        sources.append(steam.name)
    try:
        batcher = Batcher(
            store,
            sources,
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
