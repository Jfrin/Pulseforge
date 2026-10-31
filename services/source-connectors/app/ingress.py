"""Push-style sources (signed webhooks, SSE streams), switched on by environment variables.

    WEBHOOK_SOURCES   name=secret[|older-secret],other=secret   -> POST /webhooks/<name>
    WEBHOOK_PORT      default 8080
    WEBHOOK_ID_FIELD  record field used as external id (default "id")
    WEBHOOK_INTERVAL_S  how often the inbox is drained (default 2)
    SSE_SOURCES       name=https://host/events,other=https://...
    SSE_ID_FIELD      default "event_id";  SSE_INTERVAL_S default 5

Webhook sources are named "webhook-<name>", SSE sources "sse-<name>". Unset = nothing starts.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI

from .bronze import BronzeSink
from .connectors.inbox import InboxConnector
from .connectors.sse import SseConnector
from .connectors.websocket import WebSocketConnector
from .inbox import Inbox
from .poller import Poller
from .store import Store
from .web import serve
from .webhook import NAME_RE, create_app, load_secrets


def parse_pairs(raw: str) -> dict[str, str]:
    """Parse "name=value,other=value" (the value may itself contain '=')."""
    out: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        name, sep, value = item.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not NAME_RE.match(name) or not value:
            raise ValueError(f"bad entry {name!r}: expected name=value")
        out[name] = value
    return out


@dataclass
class Ingress:
    pollers: list[Poller] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    webhook_app: FastAPI | None = None
    webhook_port: int = 8080
    sse: list[SseConnector] = field(default_factory=list)

    def background(self, stop: asyncio.Event) -> list[Coroutine[Any, Any, None]]:
        return [serve(self.webhook_app, self.webhook_port, stop)] if self.webhook_app else []

    async def aclose(self) -> None:
        for connector in self.sse:
            await connector.aclose()


async def build_ingress(store: Store, bronze: BronzeSink, *, retry_delay_s: float = 5.0) -> Ingress:
    out = Ingress()

    secrets = load_secrets(os.getenv("WEBHOOK_SOURCES", ""))
    if secrets:
        inbox = Inbox(store.pool)
        await inbox.migrate()
        out.webhook_app = create_app(inbox, secrets)
        out.webhook_port = int(os.getenv("WEBHOOK_PORT", "8080"))
        for name in secrets:
            connector = InboxConnector(f"webhook-{name}", inbox)
            out.pollers.append(
                Poller(
                    connector,
                    store,
                    id_field=os.getenv("WEBHOOK_ID_FIELD", "id"),
                    bronze=bronze,
                    poll_interval_s=float(os.getenv("WEBHOOK_INTERVAL_S", "2")),
                    retry_delay_s=retry_delay_s,
                )
            )
            out.sources.append(connector.name)

    for name, url in parse_pairs(os.getenv("SSE_SOURCES", "")).items():
        sse = SseConnector(f"sse-{name}", url)
        out.sse.append(sse)
        out.pollers.append(
            Poller(
                sse,
                store,
                id_field=os.getenv("SSE_ID_FIELD", "event_id"),
                bronze=bronze,
                poll_interval_s=float(os.getenv("SSE_INTERVAL_S", "5")),
                retry_delay_s=retry_delay_s,
            )
        )
        out.sources.append(sse.name)
    for name, url in parse_pairs(os.getenv("WEBSOCKET_SOURCES", "")).items():
        websocket = WebSocketConnector(f"websocket-{name}", url)
        out.pollers.append(
            Poller(websocket, store, id_field="event_id", bronze=bronze, poll_interval_s=5, retry_delay_s=retry_delay_s)
        )
        out.sources.append(websocket.name)
    return out
