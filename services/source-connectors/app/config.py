from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    mock_source_url: str
    poll_interval_s: float
    catalog_interval_s: float
    retry_delay_s: float
    page_size: int
    batch_size: int
    batch_max_age_s: float
    batch_tick_s: float
    minio_endpoint: str | None
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    csv_import_dir: str | None
    csv_id_field: str
    csv_interval_s: float
    steam_app_ids: tuple[int, ...]
    steam_interval_s: float
    steam_max_pages: int
    steam_min_interval_s: float

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            database_url=os.environ["DATABASE_URL"],
            mock_source_url=os.getenv("MOCK_SOURCE_URL", "http://mock-source:8000"),
            poll_interval_s=float(os.getenv("POLL_INTERVAL_S", "15")),
            catalog_interval_s=float(os.getenv("CATALOG_INTERVAL_S", "300")),
            retry_delay_s=float(os.getenv("RETRY_DELAY_S", "5")),
            page_size=int(os.getenv("PAGE_SIZE", "200")),
            batch_size=int(os.getenv("BATCH_SIZE", "500")),
            batch_max_age_s=float(os.getenv("BATCH_MAX_AGE_S", "300")),
            batch_tick_s=float(os.getenv("BATCH_TICK_S", "5")),
            minio_endpoint=os.getenv("MINIO_ENDPOINT") or None,  # unset = bronze landing off
            minio_access_key=os.getenv("MINIO_ACCESS_KEY", "pulseforge"),
            minio_secret_key=os.getenv("MINIO_SECRET_KEY", "pulseforge-minio"),
            minio_bucket=os.getenv("MINIO_BUCKET", "pulseforge"),
            minio_secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
            csv_import_dir=os.getenv("CSV_IMPORT_DIR") or None,  # unset = CSV import off
            csv_id_field=os.getenv("CSV_ID_FIELD", "external_id"),
            csv_interval_s=float(os.getenv("CSV_INTERVAL_S", "30")),
            steam_app_ids=tuple(int(x) for x in os.getenv("STEAM_APP_IDS", "").replace(" ", "").split(",") if x),
            steam_interval_s=float(os.getenv("STEAM_INTERVAL_S", "300")),
            steam_max_pages=int(os.getenv("STEAM_MAX_PAGES", "10")),
            steam_min_interval_s=float(os.getenv("STEAM_MIN_INTERVAL_S", "1.5")),
        )
