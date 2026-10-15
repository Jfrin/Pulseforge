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

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            database_url=os.environ["DATABASE_URL"],
            mock_source_url=os.getenv("MOCK_SOURCE_URL", "http://mock-source:8000"),
            poll_interval_s=float(os.getenv("POLL_INTERVAL_S", "15")),
            catalog_interval_s=float(os.getenv("CATALOG_INTERVAL_S", "300")),
            retry_delay_s=float(os.getenv("RETRY_DELAY_S", "5")),
            page_size=int(os.getenv("PAGE_SIZE", "200")),
        )
