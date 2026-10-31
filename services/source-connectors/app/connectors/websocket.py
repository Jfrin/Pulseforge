"""Resumable JSON websocket feed: send {cursor}, receive {id, data} objects."""

import asyncio
import json

from websockets.asyncio.client import connect

from .base import Page


class WebSocketConnector:
    def __init__(self, name: str, url: str, *, max_events: int = 200, max_seconds: float = 10):
        if max_events < 1 or max_seconds <= 0:
            raise ValueError("positive event count and listening window required")
        self.name, self.url = name, url
        self.max_events, self.max_seconds = max_events, max_seconds

    async def fetch(self, cursor: str | None) -> Page:
        records = []
        last = cursor
        try:
            async with asyncio.timeout(self.max_seconds), connect(self.url, max_size=1024 * 1024) as socket:
                await socket.send(json.dumps({"cursor": cursor}))
                async for message in socket:
                    item = json.loads(message)
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                        raise ValueError("websocket messages require a nonempty string id")
                    records.append({"event_id": item["id"], "data": item.get("data")})
                    last = item["id"]
                    if len(records) >= self.max_events:
                        break
        except TimeoutError:
            pass
        return Page(records=records, next_cursor=last, has_more=len(records) == self.max_events and last != cursor)
