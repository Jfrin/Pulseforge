"""Bronze layer: every fetched page is landed in object storage exactly as received, never modified.

Layout:  bronze/<source>/dt=YYYY-MM-DD/<timestamp>-<rand>.json
Each object is immutable history; keys are unique per landing, so nothing is ever overwritten.
Works against MinIO or any S3-compatible store.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from datetime import UTC, datetime
from typing import Protocol

from minio import Minio

from .connectors import Page


class BronzeSink(Protocol):
    async def land_page(
        self, source: str, *, fetched_cursor: str | None, page: Page, fetched_at: datetime | None = None
    ) -> str | None:
        """Persist the raw page and return its object key (None if there was nothing to land)."""
        ...


class NullBronze:
    """Landing switched off (no object store configured)."""

    async def land_page(
        self, source: str, *, fetched_cursor: str | None, page: Page, fetched_at: datetime | None = None
    ) -> str | None:
        return None


class MinioBronze:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        *,
        secure: bool = False,
        client: Minio | None = None,
    ) -> None:
        self._bucket = bucket
        self._client = client or Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    async def ensure_bucket(self) -> None:
        await asyncio.to_thread(self._ensure_bucket)

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def land_page(
        self, source: str, *, fetched_cursor: str | None, page: Page, fetched_at: datetime | None = None
    ) -> str | None:
        if not page.records:
            return None  # empty polls of a quiet feed are not worth an object
        fetched_at = fetched_at or datetime.now(UTC)
        key = f"bronze/{source}/dt={fetched_at:%Y-%m-%d}/{fetched_at:%Y%m%dT%H%M%S%f}Z-{uuid.uuid4().hex[:8]}.json"
        body = json.dumps(
            {
                "source": source,
                "fetched_at": fetched_at.isoformat(),
                "request": {"cursor": fetched_cursor},
                "response": {
                    "records": page.records,
                    "next_cursor": page.next_cursor,
                    "has_more": page.has_more,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await asyncio.to_thread(self._put, key, body)
        return key

    def _put(self, key: str, body: bytes) -> None:
        self._client.put_object(self._bucket, key, io.BytesIO(body), len(body), content_type="application/json")
