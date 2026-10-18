"""Server-Sent Events stream wrapped as a SourceConnector.

Each fetch() opens the stream (sending Last-Event-ID when it has a cursor), collects events for at
most `max_seconds` or `max_events`, and returns them. Cursor = id of the last event, so the next
connection resumes where this one stopped (needs a server that honours Last-Event-ID). Streams
whose events carry no id cannot be resumed: those records are deduplicated by content instead.

Records are an envelope: {"event_id", "event", "data"}; data is parsed JSON when it is JSON.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx

from .base import Page


@dataclass(frozen=True, slots=True)
class SseEvent:
    id: str | None
    event: str
    data: str


async def parse_sse(lines: AsyncIterator[str]) -> AsyncGenerator[SseEvent, None]:
    """Parse an SSE line stream. A blank line dispatches the event; the last id persists (per spec)."""
    last_id: str | None = None
    event = "message"
    data: list[str] = []
    async for line in lines:
        if line == "":
            if data:
                yield SseEvent(last_id or None, event, "\n".join(data))
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value or "message"
        elif field == "id" and "\x00" not in value:
            last_id = value


class SseConnector:
    def __init__(
        self,
        name: str,
        url: str,
        *,
        max_events: int = 200,
        max_seconds: float = 10.0,
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if max_events < 1 or max_seconds <= 0:
            raise ValueError("max_events must be >= 1 and max_seconds > 0")
        self.name = name
        self._url = url
        self._max_events = max_events
        self._max_seconds = max_seconds
        self._headers = dict(headers or {})
        # No per-request timeout: the overall window below bounds connect, wait and read together.
        self._client = client or httpx.AsyncClient(timeout=None)

    async def fetch(self, cursor: str | None) -> Page:
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache", **self._headers}
        if cursor is not None:
            headers["Last-Event-ID"] = cursor
        records: list[dict[str, Any]] = []
        last_id = cursor
        full = False
        try:
            async with asyncio.timeout(self._max_seconds):
                async with self._client.stream("GET", self._url, headers=headers) as resp:
                    resp.raise_for_status()
                    async with aclosing(parse_sse(resp.aiter_lines())) as events:
                        async for ev in events:
                            records.append(self._to_record(ev))
                            if ev.id is not None:
                                last_id = ev.id
                            if len(records) >= self._max_events:
                                full = True
                                break
        except TimeoutError:
            pass  # the listening window elapsed: hand over what arrived (a half-received event is dropped)
        return Page(records=records, next_cursor=last_id, has_more=full and last_id is not None)

    @staticmethod
    def _to_record(ev: SseEvent) -> dict[str, Any]:
        data: Any
        try:
            data = json.loads(ev.data)
        except ValueError:
            data = ev.data
        return {"event_id": ev.id, "event": ev.event, "data": data}

    async def aclose(self) -> None:
        await self._client.aclose()
