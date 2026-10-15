from __future__ import annotations

import httpx2 as httpx

from .base import Page


class MockSourceConnector:
    """Polls one cursor-paginated endpoint of the mock source API (/games or /reviews)."""

    def __init__(
        self,
        name: str,
        base_url: str,
        path: str,
        page_size: int = 200,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.name = name
        self._path = path
        self._page_size = page_size
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def fetch(self, cursor: str | None) -> Page:
        params: dict[str, str | int] = {"limit": self._page_size}
        if cursor is not None:
            params["cursor"] = cursor
        resp = await self._client.get(self._path, params=params)
        resp.raise_for_status()  # retries/backoff are the caller's job
        body = resp.json()
        return Page(records=body["items"], next_cursor=body["next_cursor"], has_more=body["has_more"])

    async def aclose(self) -> None:
        await self._client.aclose()
