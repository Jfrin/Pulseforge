"""Signed webhook receiver: POST /webhooks/<name>

Verifies the HMAC signature (see signing.py) BEFORE parsing anything, stores the records durably
in the inbox, and only then answers 202. Senders that retry are fine: the dedup ledger drops
repeats downstream. The reason for a rejection goes to the log, never to the caller.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request

from .signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, verify

log = logging.getLogger("webhook")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MIN_SECRET_LENGTH = 16


class InboxWriter(Protocol):
    async def append(self, source: str, records: list[dict[str, Any]]) -> int: ...


def load_secrets(raw: str) -> dict[str, list[str]]:
    """Parse "name=secret,other=new-secret|old-secret" (several secrets per name = rotation)."""
    out: dict[str, list[str]] = {}
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        name, sep, rest = item.partition("=")
        name = name.strip()
        keys = [k for k in (s.strip() for s in rest.split("|")) if k]
        if not sep or not NAME_RE.match(name) or not keys:
            raise ValueError(f"bad webhook entry {name!r}: expected name=secret[|older-secret]")
        if any(len(k) < MIN_SECRET_LENGTH for k in keys):
            raise ValueError(f"webhook {name!r}: secrets must be at least {MIN_SECRET_LENGTH} characters")
        out[name] = keys
    return out


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-standard JSON constant {token}")


def _parse_records(body: bytes, max_records: int) -> list[dict[str, Any]]:
    try:
        data = json.loads(body, parse_constant=_reject_constant)
    except ValueError:
        raise HTTPException(422, "body must be valid JSON") from None
    records = [data] if isinstance(data, dict) else data
    if not isinstance(records, list) or not records or not all(isinstance(r, dict) for r in records):
        raise HTTPException(422, "body must be a JSON object or a non-empty array of objects")
    if len(records) > max_records:
        raise HTTPException(413, f"at most {max_records} records per request")
    return records


def create_app(
    inbox: InboxWriter,
    secrets: Mapping[str, Sequence[str]],
    *,
    tolerance_s: float = 300.0,
    max_body_bytes: int = 1_000_000,
    max_records: int = 1000,
    now: Callable[[], float] = time.time,
) -> FastAPI:
    app = FastAPI(title="PulseForge webhook receiver")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/{name}", status_code=202)
    async def receive(name: str, request: Request) -> dict[str, int]:
        keys = secrets.get(name)
        if keys is None:
            raise HTTPException(404, "unknown webhook")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_body_bytes:
            raise HTTPException(413, "body too large")
        body = await request.body()
        if len(body) > max_body_bytes:
            raise HTTPException(413, "body too large")
        try:
            verify(
                keys,
                request.headers.get(TIMESTAMP_HEADER),
                request.headers.get(SIGNATURE_HEADER),
                body,
                tolerance_s=tolerance_s,
                now=now(),
            )
        except SignatureError as exc:
            log.warning("webhook %s rejected: %s", name, exc)
            raise HTTPException(401, "invalid signature") from None
        accepted = await inbox.append(f"webhook-{name}", _parse_records(body, max_records))
        return {"accepted": accepted}

    return app
