"""Small, pure parsing/normalization helpers. None means "absent or unusable"; callers decide severity."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Any

_LEGAL_SUFFIXES = frozenset(
    {
        "inc", "incorporated", "ltd", "limited", "llc", "llp", "gmbh", "co", "corp", "corporation",
        "company", "sa", "srl", "bv", "ab", "oy", "as", "pty", "plc", "kk",
    }
)  # fmt: skip
_TRUE = frozenset({"true", "1", "yes", "y", "t"})
_FALSE = frozenset({"false", "0", "no", "n", "f"})
KINDS = ("game", "dlc", "bundle")


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def clean_text(value: Any) -> str | None:
    """NFKC-normalize, collapse whitespace, strip; empty becomes None."""
    if value is None:
        return None
    text = " ".join(unicodedata.normalize("NFKC", str(value)).split())
    return text or None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def company_key(name: str) -> str | None:
    """Matching key for studio names: case, accents, punctuation, spacing and legal suffixes vanish."""
    tokens = re.findall(r"\w+", _fold(name.replace("&", " and ")))
    core = [t for t in tokens if t not in _LEGAL_SUFFIXES] or tokens
    return "".join(core) or None


def normalize_company(value: Any) -> tuple[str | None, str | None]:
    display = clean_text(value)
    return display, (company_key(display) if display else None)


def normalize_kind(value: Any) -> str:
    text = clean_text(value)
    kind = text.lower() if text else ""
    return kind if kind in KINDS else "unknown"


def normalize_currency(value: Any) -> str | None:
    text = clean_text(value)
    return text.upper() if text and re.fullmatch(r"[A-Za-z]{3}", text) else None


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?\d+(\.0+)?", text):
            return int(float(text))
    return None


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def parse_datetime(value: Any) -> datetime | None:
    """Aware UTC datetime from ISO strings (naive = UTC) or epoch seconds/milliseconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        seconds = value / 1000 if abs(value) > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
