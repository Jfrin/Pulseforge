from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from email.utils import parsedate_to_datetime

import httpx2 as httpx

from .events import utcnow

TOKEN = """
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])
local old = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = math.min(capacity, tonumber(old[1] or capacity) + math.max(0, now-tonumber(old[2] or now))*rate)
local delay = 0
if tokens >= 1 then tokens = tokens-1 else delay=(1-tokens)/rate end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity/rate)+60)
return tostring(delay)
"""


def retry_after(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0.0, (parsedate_to_datetime(value) - utcnow()).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


class SourceUnavailable(Exception):
    pass


class ResilientClient:
    def __init__(self, redis, client, *, rate=5.0, attempts=3, cooldown=30):
        if rate <= 0 or attempts < 1 or cooldown < 1:
            raise ValueError("invalid resilience configuration")
        self.redis, self.client = redis, client
        self.rate, self.attempts, self.cooldown = rate, attempts, cooldown
        self.calls = 0
        self.cache_hits = 0
        self.circuit_trips = 0

    async def request(self, source, url, body, headers=None):
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        cache_key = f"cache:{source}:{digest}"
        cached = await self.redis.get(cache_key)
        if cached:
            self.cache_hits += 1
            return json.loads(cached)
        if await self.redis.exists(f"open:{source}"):
            raise SourceUnavailable("circuit open")
        for attempt in range(self.attempts):
            pause = await self.redis.ttl(f"pause:{source}")
            if pause > 0:
                # Defer this tier rather than bypassing a long Retry-After on replay.
                raise SourceUnavailable("source retry-after active")
            while True:
                delay = float(await self.redis.eval(TOKEN, 1, f"tokens:{source}", time.time(), self.rate, 1))
                if delay <= 0:
                    break
                await asyncio.sleep(delay)
            try:
                self.calls += 1
                response = await self.client.post(url, json=body, headers=headers or {})
                if response.status_code == 429:
                    wait = max(1, int(retry_after(response.headers.get("Retry-After"))) + 1)
                    await self.redis.set(f"pause:{source}", "1", ex=wait)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result.get("fields"), dict):
                    raise ValueError("adapter fields must be an object")
                await self.redis.delete(f"failures:{source}", f"open:{source}")
                await self.redis.set(cache_key, json.dumps(result), ex=3600)
                return result
            except (httpx.HTTPError, ValueError) as exc:
                count = await self.redis.incr(f"failures:{source}")
                await self.redis.expire(f"failures:{source}", self.cooldown * 2)
                if count >= 3:
                    await self.redis.set(f"open:{source}", "1", ex=self.cooldown)
                    self.circuit_trips += 1
                    raise SourceUnavailable("circuit tripped") from exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise SourceUnavailable(f"http {exc.response.status_code}") from exc
                if attempt + 1 == self.attempts:
                    raise SourceUnavailable("retries exhausted") from exc
                await asyncio.sleep(2**attempt + random.uniform(0, 0.5))
