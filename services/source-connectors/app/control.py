"""Broker backlog gates every page fetch; telemetry goes through the connector outbox."""

import asyncio
import os
import time

import httpx2 as httpx
from psycopg.types.json import Jsonb


class Control:
    def __init__(self, store, stop=None):
        self.store = store
        self.stop = stop or asyncio.Event()
        self.url = os.getenv("BROKER_MANAGEMENT_URL")
        self.client = httpx.AsyncClient(
            timeout=10, auth=(os.getenv("BROKER_USER", "pulseforge"), os.getenv("BROKER_PASSWORD", "pulseforge"))
        )
        self.limit = int(os.getenv("QUEUE_LAG_THRESHOLD", "10000"))
        self.last_check = 0.0
        self.lag = 0

    async def wait(self):
        if not self.url:
            return
        while True:
            if time.monotonic() - self.last_check >= 5:
                response = await self.client.get(f"{self.url}/api/queues/%2F")
                response.raise_for_status()
                self.lag = sum(q.get("messages", 0) for q in response.json())
                async with self.store.pool.connection() as conn:
                    cur = await conn.execute("SELECT count(*) FROM outbox WHERE published_at IS NULL")
                    self.lag += (await cur.fetchone())[0]
                self.last_check = time.monotonic()
            if self.lag < self.limit:
                return
            await self.report("backpressure", False, 0, 0)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=5)
                raise asyncio.CancelledError
            except TimeoutError:
                pass

    async def report(self, source, success, new, duplicates):
        payload = {
            "source": source,
            "success": success,
            "new": new,
            "duplicates": duplicates,
            "duplicate_rate": duplicates / (new + duplicates) if new + duplicates else 0,
            "queue_lag": self.lag,
        }
        async with self.store.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO outbox(event_type,source,payload) VALUES ('SourceHealth',%s,%s)", (source, Jsonb(payload))
            )

    async def aclose(self):
        await self.client.aclose()
