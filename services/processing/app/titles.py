"""Title analysis for entity resolution.

Strips TRAILING edition / DLC / bundle markers ("Deluxe Edition", "Season Pass", "+ Expansion Bundle"...)
to get the base title, and extracts what must NOT be fuzzed away: sequel numbers. Only trailing markers
are stripped, so titles that merely contain such words ("Pack Rat", "Edition Zero") are left alone.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

STOPWORDS = frozenset({"the", "a", "an", "of", "and"})

_EDITION: tuple[tuple[str, ...], ...] = (
    ("game", "of", "the", "year", "edition"), ("game", "of", "the", "year"), ("goty", "edition"), ("goty",),
    ("deluxe", "edition"), ("definitive", "edition"), ("complete", "edition"), ("ultimate", "edition"),
    ("anniversary", "edition"), ("collectors", "edition"), ("gold", "edition"), ("premium", "edition"),
    ("special", "edition"), ("enhanced", "edition"), ("standard", "edition"), ("digital", "edition"),
    ("directors", "cut"), ("hd", "remaster"), ("remastered",), ("remaster",), ("deluxe",), ("definitive",),
    ("edition",),
)  # fmt: skip
_DLC: tuple[tuple[str, ...], ...] = (
    ("season", "pass"), ("expansion", "pack"), ("original", "soundtrack"), ("soundtrack",), ("ost",),
    ("cosmetic", "pack"), ("character", "pack"), ("skin", "pack"), ("dlc",), ("add", "on"), ("addon",),
    ("expansion",),
)  # fmt: skip
_BUNDLE: tuple[tuple[str, ...], ...] = (("expansion", "bundle"), ("bundle",))

VARIANT_RANK = {"base": 0, "edition": 1, "dlc": 2, "bundle": 3}
_MARKERS = sorted(
    [(p, "edition") for p in _EDITION] + [(p, "dlc") for p in _DLC] + [(p, "bundle") for p in _BUNDLE],
    key=lambda item: -len(item[0]),
)  # longest phrase first, so "game of the year edition" wins over "edition"
# Words that precede "Deluxe Edition" in real listings ("Super Deluxe Edition", "Digital Deluxe Edition").
# Only stripped when a marker was already found, so "Digital Dreams" stays intact.
_EDITION_MODIFIERS = frozenset({"super", "digital", "ultra", "limited"})
_MARKER_WORDS = frozenset(word for phrase, _ in _MARKERS for word in phrase)
_ROMAN_VALUES = {
    "ii": 2, "iii": 3, "iv": 4, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "xi": 11, "xii": 12, "xiii": 13,
    "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20,
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class TitleKey:
    match_key: str  # lowercase, accent-free, stopwords removed, markers stripped
    variant: str  # base | edition | dlc | bundle
    markers: tuple[str, ...]
    numerals: frozenset[str]  # sequel numbers: two titles that differ here are different products


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def tokenize(title: str) -> list[str]:
    folded = _fold(title.replace("&", " and ")).replace("'", "").replace("\u2019", "")
    return re.findall(r"\w+", folded)


def _arabize(tokens: list[str]) -> list[str]:
    """Roman sequel numbers become digits ("Neon Harbor II" == "Neon Harbor 2").

    A lone trailing V or X is NOT converted: "Mega Man X" must never equal "Mega Man 10".
    """
    return [str(_ROMAN_VALUES[t]) if t in _ROMAN_VALUES else t for t in tokens]


def _numerals(tokens: list[str]) -> frozenset[str]:
    found = {t for t in tokens if t.isdigit()}
    if len(tokens) > 1 and tokens[-1] in {"v", "x"}:  # "Final Fantasy X": a veto signal, kept as written
        found.add(tokens[-1])
    return frozenset(found)


def analyze_title(title: str) -> TitleKey:
    tokens = tokenize(title)
    markers: list[str] = []
    variant = "base"
    stripped = not all(t in _MARKER_WORDS for t in tokens)  # "Deluxe Edition" alone is a title, not a variant
    while stripped:
        stripped = False
        for phrase, kind in _MARKERS:
            k = len(phrase)
            if len(tokens) > k and tuple(tokens[-k:]) == phrase:  # never strip a title down to nothing
                tokens = tokens[:-k]
                markers.append(" ".join(phrase))
                variant = max(variant, kind, key=VARIANT_RANK.__getitem__)
                stripped = True
                break
    if variant == "edition":
        while len(tokens) > 1 and tokens[-1] in _EDITION_MODIFIERS:
            markers.append(tokens.pop())
    # "...Civilization V: The Complete Edition" leaves a trailing "the", which would hide the sequel number V
    while len(tokens) > 1 and tokens[-1] in STOPWORDS:
        tokens.pop()
    tokens = _arabize(tokens)
    significant = [t for t in tokens if t not in STOPWORDS] or tokens
    return TitleKey(
        match_key=" ".join(significant),
        variant=variant,
        markers=tuple(markers),
        numerals=_numerals(tokens),
    )
