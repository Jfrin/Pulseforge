"""CSV import connector: ingests every *.csv in a directory, in filename order.

Cursor = "<filename>@<byte offset>", so a crash resumes mid-file without re-parsing it, and
large files are never re-scanned from the top. Records are the raw cells as strings, untouched
(short rows get None for the missing cells, long rows keep the surplus under "_extra").

A finished pass returns next_cursor=None (snapshot semantics): the next pass re-reads everything
and the dedup ledger drops what was already seen, so edited or newly dropped files are picked up.
Identical rows collapse into one record, by design.
"""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

from .base import Page


class CsvDirectoryConnector:
    def __init__(self, name: str, directory: str | Path, page_size: int = 200, pattern: str = "*.csv") -> None:
        if page_size < 1:
            raise ValueError("page_size must be >= 1")
        self.name = name
        self._dir = Path(directory)
        self._page_size = page_size
        self._pattern = pattern

    async def fetch(self, cursor: str | None) -> Page:
        return await asyncio.to_thread(self._fetch, cursor)  # file I/O stays off the event loop

    # ------------------------------------------------------------------
    def _files(self) -> list[Path]:
        return sorted((p for p in self._dir.glob(self._pattern) if p.is_file()), key=lambda p: p.name)

    def _start(self, cursor: str | None, files: list[Path]) -> tuple[int, int] | None:
        """Resolve a cursor to (index into files, byte offset); None when nothing is left to read."""
        if cursor is None:
            return 0, 0
        fname, sep, raw = cursor.rpartition("@")
        try:
            offset = int(raw)
        except ValueError:
            offset = -1
        if not sep or not fname or offset < 0:
            raise ValueError(f"invalid cursor: {cursor!r}")
        names = [p.name for p in files]
        if fname in names:
            return names.index(fname), offset
        for i, n in enumerate(names):  # the file vanished: carry on with the next one alphabetically
            if n > fname:
                return i, 0
        return None

    def _fetch(self, cursor: str | None) -> Page:
        files = self._files()
        if not files:
            return Page(records=[], next_cursor=None, has_more=False)
        start = self._start(cursor, files)
        if start is None:
            return Page(records=[], next_cursor=None, has_more=False)
        idx, offset = start
        records, pos, eof = self._read(files[idx], offset)
        if not eof:
            return Page(records=records, next_cursor=f"{files[idx].name}@{pos}", has_more=True)
        if idx + 1 < len(files):
            return Page(records=records, next_cursor=f"{files[idx + 1].name}@0", has_more=True)
        return Page(records=records, next_cursor=None, has_more=False)

    def _read(self, path: Path, offset: int) -> tuple[list[dict[str, Any]], int, bool]:
        """Read up to page_size rows starting at `offset` (0 = right after the header)."""
        with open(path, encoding="utf-8", newline="") as f:
            lines = iter(f.readline, "")  # readline (not next()) keeps f.tell() usable
            header = next(csv.reader(lines), None)
            if not header:
                return [], 0, True  # empty file
            header[0] = header[0].lstrip("\ufeff")  # Excel's BOM
            if offset:
                f.seek(offset)
            reader = csv.reader(lines)
            records: list[dict[str, Any]] = []
            while len(records) < self._page_size:
                row = next(reader, None)
                if row is None:
                    break
                if not row:  # blank line
                    continue
                records.append(self._to_record(header, row))
            pos = f.tell()
            eof = f.readline() == ""
        return records, pos, eof

    @staticmethod
    def _to_record(header: list[str], row: list[str]) -> dict[str, Any]:
        rec: dict[str, Any] = dict(zip(header, row, strict=False))
        if len(row) > len(header):
            rec["_extra"] = row[len(header) :]
        for h in header[len(row) :]:
            rec[h] = None
        return rec
