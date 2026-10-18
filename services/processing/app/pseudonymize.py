"""Reviewer pseudonymization: keyed hash (HMAC-SHA256) of "<source>, <raw id>".

- Same reviewer in the same source always maps to the same pseudonym (so per-reviewer signals work).
- Namespaced by source: the same raw id in two sources is NOT linkable.
- Without the secret salt the mapping cannot be recomputed or reversed by guessing ids.
- Rotating the salt deliberately breaks linkability with everything produced before.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable

MIN_SALT_BYTES = 16
PseudonymizeFn = Callable[[str, str], str]


class Pseudonymizer:
    def __init__(self, salt: str | bytes) -> None:
        raw = salt.encode("utf-8") if isinstance(salt, str) else salt
        if len(raw) < MIN_SALT_BYTES:
            raise ValueError(f"salt must be at least {MIN_SALT_BYTES} bytes")
        self._salt = raw

    @classmethod
    def from_env(cls, var: str = "PSEUDONYM_SALT") -> Pseudonymizer:
        value = os.getenv(var)
        if not value:
            raise RuntimeError(f"{var} is not set; refusing to run without a pseudonymization salt")
        return cls(value)

    def __call__(self, source: str, raw_id: str) -> str:
        mac = hmac.new(self._salt, f"{source}\x1f{raw_id}".encode(), hashlib.sha256)
        return "p_" + mac.hexdigest()[:32]

    def __repr__(self) -> str:
        return "Pseudonymizer(<salt redacted>)"
