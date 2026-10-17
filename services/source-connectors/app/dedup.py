"""Record identity: (external_id, payload_hash).

The hash covers the raw payload, so an unchanged record re-polled is a duplicate, while a record
whose content changed gets a new hash and counts as a new version. Fields that churn without
meaning anything (vote counts, playtime...) can be excluded with dotted paths ("author.playtime");
the stored record is still the full, untouched payload.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("dedup")


def _without(record: dict[str, Any], paths: Sequence[str]) -> dict[str, Any]:
    out = copy.deepcopy(record)
    for path in paths:
        *parents, leaf = path.split(".")
        node: Any = out
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(leaf, None)
    return out


def payload_hash(record: dict[str, Any], ignore_fields: Sequence[str] = ()) -> str:
    hashed = _without(record, ignore_fields) if ignore_fields else record
    canonical = json.dumps(hashed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Keyed:
    external_id: str
    payload_hash: str
    record: dict[str, Any]


def key_records(records: list[dict[str, Any]], id_field: str, ignore_fields: Sequence[str] = ()) -> list[Keyed]:
    """Attach identity to each record and drop repeats inside the page (first one wins).

    A record without `id_field` still gets a stable identity derived from its content,
    so one malformed record cannot block the pipeline (validation handles it later).
    """
    seen: set[tuple[str, str]] = set()
    out: list[Keyed] = []
    missing = 0
    for rec in records:
        h = payload_hash(rec, ignore_fields)
        raw = rec.get(id_field)
        if raw is None or raw == "":
            missing += 1
            ext = f"hash:{h[:16]}"
        else:
            ext = str(raw)
        key = (ext, h)
        if key in seen:
            continue
        seen.add(key)
        out.append(Keyed(ext, h, rec))
    if missing:
        log.warning("%d record(s) missing id field %r; keyed by content hash", missing, id_field)
    return out
