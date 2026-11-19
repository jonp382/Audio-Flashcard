"""CSV / TSV / Anki "Notes in Plain Text" import (agent.md §9 Import).

Parsing is pure (no DB) except ``import_into_deck``.
"""

from __future__ import annotations

import csv
import html
import io
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .database import now_iso, transaction

ALLOWED_SUFFIXES = (".csv", ".tsv", ".txt")
_SEPARATORS = {"tab": "\t", "comma": ",", "semicolon": ";", "pipe": "|", "space": " ", "colon": ":"}


class ImportError_(Exception):
    pass


@dataclass
class ParsedFile:
    columns: list[str]
    rows: list[list[str]]
    encoding: str
    delimiter: str
    anki_export: bool
    has_header: bool = False
    meta: dict[str, str] = field(default_factory=dict)


def decode_bytes(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ImportError_("Could not read the file as UTF-8 or Shift-JIS (cp932).")


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t,;").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in ("\t", ",", ";")}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def _separator_value(v: str) -> str:
    v = v.strip()
    return _SEPARATORS.get(v.lower(), v[:1] if v else "\t")


_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<\s*br\s*/?\s*>|<\s*/\s*(div|p)\s*>", re.I)
_SOUND_RE = re.compile(r"\[sound:[^\]]*\]")
_WS_RE = re.compile(r"\s+")


def clean_field(s: str) -> str:
    s = _SOUND_RE.sub("", s)
    s = _BR_RE.sub(" ", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s).replace("\xa0", " ")
    return _WS_RE.sub(" ", s).strip()


# 話[はな]す -> front 話す, reading はなす. Anki puts a space before a kanji group that
# follows other text ("今日[きょう]は 天気[てんき]"); that space is a separator, not content.
_FURI_RE = re.compile(r" ?([^\s\[\]]+?)\[([^\]]+)\]")
_KANJI_RUN_RE = re.compile(r"([㐀-䶿一-鿿豈-﫿々〆ヶ]+)$")


def split_furigana(field_: str) -> tuple[str, str | None]:
    if "[" not in field_ or not _FURI_RE.search(field_):
        return field_, None
    front_parts, reading_parts = [], []
    pos = 0
    for m in _FURI_RE.finditer(field_):
        before = field_[pos : m.start()]
        front_parts.append(before)
        reading_parts.append(before)
        base, furi = m.group(1), m.group(2)
        # The reading covers only the trailing kanji run of ``base``; any kana before it stays.
        km = _KANJI_RUN_RE.search(base)
        if km and km.start() > 0:
            prefix = base[: km.start()]
            front_parts.append(base)
            reading_parts.append(prefix + furi)
        else:
            front_parts.append(base)
            reading_parts.append(furi)
        pos = m.end()
    front_parts.append(field_[pos:])
    reading_parts.append(field_[pos:])
    return "".join(front_parts).strip(), "".join(reading_parts).strip()


def parse_text(text: str, encoding: str = "utf-8") -> ParsedFile:
    lines = text.splitlines()
    meta: dict[str, str] = {}
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        line = lines[i][1:]
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v
        i += 1
    anki = bool(meta)
    body = "\n".join(lines[i:])
    delimiter = _separator_value(meta["separator"]) if "separator" in meta else _sniff_delimiter(body[:4096])
    reader = csv.reader(io.StringIO(body), delimiter=delimiter)
    rows = [[c for c in r] for r in reader if any(c.strip() for c in r)]
    width = max((len(r) for r in rows), default=0)

    has_header = False
    if "columns" in meta:
        columns = [c.strip() for c in meta["columns"].split(delimiter)]
    elif rows and {"front", "back"} <= {c.strip().lower() for c in rows[0]}:
        columns = [c.strip() for c in rows[0]]
        rows = rows[1:]
        has_header = True
    else:
        columns = []
    width = max(width, len(columns))
    columns = columns + [f"Column {n + 1}" for n in range(len(columns), width)]
    rows = [r + [""] * (width - len(r)) for r in rows]
    return ParsedFile(columns, rows, encoding, delimiter, anki, has_header, meta)


def parse_file(path: Path) -> ParsedFile:
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ImportError_("Only .csv, .tsv and .txt files can be imported.")
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ImportError_(f"Could not read the file: {e}") from e
    text, enc = decode_bytes(raw)
    return parse_text(text, enc)


def preview(parsed: ParsedFile, n: int = 10) -> dict[str, Any]:
    delim_name = {"\t": "tab", ",": "comma", ";": "semicolon"}.get(parsed.delimiter, parsed.delimiter)
    return {
        "columns": parsed.columns,
        "rows": [[clean_field(c) for c in r] for r in parsed.rows[:n]],
        "total_rows": len(parsed.rows),
        "detected": {"encoding": parsed.encoding, "delimiter": delim_name, "anki_export": parsed.anki_export},
    }


def resolve_mapping(parsed: ParsedFile, mapping: dict | None) -> tuple[int, int, int | None]:
    if mapping is None:
        lower = [c.strip().lower() for c in parsed.columns]
        if "front" in lower and "back" in lower:
            return lower.index("front"), lower.index("back"), (lower.index("reading") if "reading" in lower else None)
        raise ImportError_("Choose which columns are front and back.")
    width = len(parsed.columns)

    def idx(key: str, required: bool) -> int | None:
        v = mapping.get(key)
        if v is None and not required:
            return None
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < width:
            raise ImportError_(f"Invalid column for {key}.")
        return v

    f, b, r = idx("front", True), idx("back", True), idx("reading", False)
    if f == b:
        raise ImportError_("Front and back must be different columns.")
    return f, b, r


def import_into_deck(
    conn: sqlite3.Connection, parsed: ParsedFile, deck_id: int, mapping: dict | None, now: str | None = None
) -> dict[str, Any]:
    fi, bi, ri = resolve_mapping(parsed, mapping)
    imported, dupes = 0, 0
    errors: list[dict[str, Any]] = []
    first_row = 2 if parsed.has_header else 1
    with transaction(conn):
        existing = {r[0] for r in conn.execute("SELECT front FROM cards WHERE deck_id = ?", (deck_id,))}
        base = now or now_iso()
        for n, row in enumerate(parsed.rows):
            rowno = first_row + n
            front, back = clean_field(row[fi]), clean_field(row[bi])
            reading = clean_field(row[ri]) if ri is not None else None
            if ri is None:
                front, furi = split_furigana(front)
                reading = furi
            if not front or not back:
                errors.append({"row": rowno, "reason": "empty front or back"})
                continue
            if front in existing:
                dupes += 1
                errors.append({"row": rowno, "reason": f"duplicate: {front}"})
                continue
            existing.add(front)
            # created_at keeps file order (queue orders new cards by created_at, id).
            conn.execute(
                "INSERT INTO cards(deck_id, front, back, reading, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (deck_id, front, back, reading or None, base, base),
            )
            imported += 1
    return {"imported": imported, "skipped_duplicates": dupes, "errors": errors}
