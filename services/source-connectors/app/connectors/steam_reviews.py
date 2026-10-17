"""Steam user reviews, from Valve's documented endpoint (no API key):

    GET https://store.steampowered.com/appreviews/<appid>?json=1&filter=recent&cursor=<cursor>

Facts this connector is built around:
- `filter=recent` pages newest-first and ends with an EMPTY page; short pages do NOT mean the end.
- the first cursor is "*", each response carries the cursor for the next page.
- num_per_page is capped at 100 by Steam.
- Steam hides reviews flagged as off-topic activity unless filter_offtopic_activity=0. Those are
  exactly the review bombs we want to see, so they are included.
- the review payload does not contain the app id, so the connector adds `app_id` to every record.

Cursor = "<appid>|<page number>|<steam cursor>". One pass reads up to `max_pages_per_app` of the
newest pages for each app, then returns next_cursor=None (snapshot semantics); the dedup ledger
drops reviews already seen. Be polite: requests are spaced by `min_interval_s`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

import httpx2 as httpx

from .base import Page

# Churn constantly on already-ingested reviews; excluded from the identity hash so a vote or an
# extra hour played does not re-emit the review as a "new version".
STEAM_VOLATILE_FIELDS = (
    "votes_up",
    "votes_funny",
    "weighted_vote_score",
    "comment_count",
    "author.playtime_forever",
    "author.playtime_last_two_weeks",
    "author.last_played",
)


class SteamReviewsConnector:
    def __init__(
        self,
        name: str,
        app_ids: Sequence[int],
        *,
        base_url: str = "https://store.steampowered.com",
        page_size: int = 100,
        max_pages_per_app: int = 10,
        language: str = "all",
        min_interval_s: float = 1.5,
        user_agent: str = "PulseForge/0.1",
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        if not app_ids:
            raise ValueError("at least one app id is required")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100 (Steam's cap)")
        if max_pages_per_app < 1:
            raise ValueError("max_pages_per_app must be >= 1")
        self.name = name
        self._apps = [int(a) for a in app_ids]
        self._page_size = page_size
        self._max_pages = max_pages_per_app
        self._language = language
        self._min_interval_s = min_interval_s
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout_s, headers={"User-Agent": user_agent}
        )
        self._lock = asyncio.Lock()
        self._last_request = float("-inf")

    async def fetch(self, cursor: str | None) -> Page:
        app_id, page_no, steam_cursor = self._parse(cursor)
        body = await self._get(app_id, steam_cursor)
        reviews = body.get("reviews") or []
        records = [{**r, "app_id": app_id} for r in reviews]
        next_steam = body.get("cursor")
        page_no += 1

        keep_paging = bool(reviews) and bool(next_steam) and next_steam != steam_cursor and page_no < self._max_pages
        if keep_paging:
            return Page(records=records, next_cursor=f"{app_id}|{page_no}|{next_steam}", has_more=True)
        following = self._next_app(app_id)
        if following is not None:
            return Page(records=records, next_cursor=f"{following}|0|*", has_more=True)
        return Page(records=records, next_cursor=None, has_more=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    def _parse(self, cursor: str | None) -> tuple[int, int, str]:
        if cursor is None:
            return self._apps[0], 0, "*"
        app_s, _, rest = cursor.partition("|")
        page_s, _, steam_cursor = rest.partition("|")
        try:
            app_id, page_no = int(app_s), int(page_s)
        except ValueError:
            raise ValueError(f"invalid cursor: {cursor!r}") from None
        if app_id not in self._apps or page_no < 0 or not steam_cursor:
            raise ValueError(f"invalid cursor: {cursor!r}")
        return app_id, page_no, steam_cursor

    def _next_app(self, app_id: int) -> int | None:
        i = self._apps.index(app_id) + 1
        return self._apps[i] if i < len(self._apps) else None

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self._min_interval_s - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _get(self, app_id: int, steam_cursor: str) -> dict[str, Any]:
        await self._throttle()
        resp = await self._client.get(
            f"/appreviews/{app_id}",
            params={
                "json": 1,
                "filter": "recent",
                "language": self._language,
                "cursor": steam_cursor,
                "num_per_page": self._page_size,
                "purchase_type": "all",
                "review_type": "all",
                "filter_offtopic_activity": 0,
            },
        )
        resp.raise_for_status()  # 429s etc: retry/backoff is the caller's job
        body: dict[str, Any] = resp.json()
        if body.get("success") != 1:
            raise RuntimeError(f"steam returned success={body.get('success')!r} for app {app_id}")
        return body
