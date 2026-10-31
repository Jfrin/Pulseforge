"""Steam metadata and official ISteamUserStats player counts, one app per page."""

import asyncio
import time
from typing import Any

import httpx2 as httpx

from .base import Page


class SteamGamesConnector:
    name = "steam-games"

    def __init__(self, app_ids: tuple[int, ...], *, min_interval_s: float = 1.5, client=None):
        if not app_ids or any(app <= 0 for app in app_ids) or min_interval_s < 0:
            raise ValueError("positive app ids and nonnegative interval required")
        self.app_ids = app_ids
        self.client = client or httpx.AsyncClient(timeout=20)
        self.interval = min_interval_s
        self.last_request = 0.0

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def fetch(self, cursor: str | None) -> Page:
        index = int(cursor) if cursor is not None else 0
        if index < 0 or index >= len(self.app_ids):
            raise ValueError("invalid Steam app cursor")
        app_id = self.app_ids[index]
        store = await self._get(
            "https://store.steampowered.com/api/appdetails", {"appids": app_id, "cc": "us", "l": "english"}
        )
        players = await self._get(
            "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", {"appid": app_id}
        )
        more = index + 1 < len(self.app_ids)
        return Page(
            records=[{"app_id": app_id, "store": store, "players": players}],
            next_cursor=str(index + 1) if more else None,
            has_more=more,
        )

    async def aclose(self) -> None:
        await self.client.aclose()
