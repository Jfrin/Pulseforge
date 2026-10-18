"""HMAC request signing for webhooks (Stripe-style).

The sender signs  "<timestamp>.<raw body>"  with a shared secret and sends

    X-PulseForge-Timestamp: <unix seconds>
    X-PulseForge-Signature: sha256=<hex>

The timestamp is part of the signed message and must be recent, so a captured request cannot be
replayed later. Several secrets are accepted at once so a secret can be rotated without downtime.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Sequence

TIMESTAMP_HEADER = "X-PulseForge-Timestamp"
SIGNATURE_HEADER = "X-PulseForge-Signature"


class SignatureError(Exception):
    """The request is not authentic. The message is for logs, never for the caller."""


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(
    secrets: Sequence[str],
    timestamp_header: str | None,
    signature_header: str | None,
    body: bytes,
    *,
    tolerance_s: float = 300.0,
    now: float | None = None,
) -> None:
    if not timestamp_header or not signature_header:
        raise SignatureError("missing signature headers")
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        raise SignatureError("malformed timestamp") from None
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_s:
        raise SignatureError("timestamp outside tolerance")
    for secret in secrets:
        if hmac.compare_digest(sign(secret, timestamp, body), signature_header):
            return
    raise SignatureError("signature mismatch")
