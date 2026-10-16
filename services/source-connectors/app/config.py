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
        )
