"""CSV / TSV / Anki "Notes in Plain Text" import (agent.md §9 Import).

Parsing is pure (no DB) except ``import_into_deck``.
"""

from __future__ import annotations

import csv
import html
import io
import json
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


_BOLD_OPEN_RE = re.compile(r"<\s*b\s*>", re.I)
_BOLD_CLOSE_RE = re.compile(r"<\s*/\s*b\s*>", re.I)


def clean_sentence(s: str) -> str:
    """Like clean_field, but keeps <b>…</b> (Anki decks bold the card's word in the
    example sentence). The UI renders it as text, never as HTML."""
    s = _BOLD_OPEN_RE.sub("\x01", s)
    s = _BOLD_CLOSE_RE.sub("\x02", s)
    s = clean_field(s).replace("<", "").replace(">", "")
    return s.replace("\x01", "<b>").replace("\x02", "</b>")


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


OPTIONAL_COLUMNS = ("reading", "sentence", "sentence_meaning", "notes")


def resolve_mapping(parsed: ParsedFile, mapping: dict | None) -> dict[str, int | None]:
    """-> {"front": i, "back": i} plus each of OPTIONAL_COLUMNS as i|None"""
    if mapping is None:
        lower = [c.strip().lower() for c in parsed.columns]
        if "front" in lower and "back" in lower:
            out: dict[str, int | None] = {"front": lower.index("front"), "back": lower.index("back")}
            for k in OPTIONAL_COLUMNS:
                out[k] = lower.index(k) if k in lower else None
            return out
        raise ImportError_("Choose which columns are front and back.")
    width = len(parsed.columns)

    def idx(key: str, required: bool) -> int | None:
        v = mapping.get(key)
        if v is None and not required:
            return None
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < width:
            raise ImportError_(f"Invalid column for {key}.")
        return v

    out = {"front": idx("front", True), "back": idx("back", True)}
    for k in OPTIONAL_COLUMNS:
        out[k] = idx(k, False)
    if out["front"] == out["back"]:
        raise ImportError_("Front and back must be different columns.")
    return out


# --- import / re-import -------------------------------------------------------------
#
# Re-importing a file into a deck that already has its cards updates them in place
# (schedule and history untouched), adds new rows, and reports the deck's cards that are
# no longer in the file, which the user keeps or removes. So the deck never has to be
# deleted to pick up new fields.

CONTENT_COLUMNS = ("back", "sentence", "sentence_meaning", "notes")


@dataclass
class ImportRow:
    rowno: int
    front: str
    back: str
    reading: str | None
    sentence: str | None
    sentence_meaning: str | None
    notes: str | None
    source: list[str]  # the whole original row, every column, as in the file


@dataclass
class ImportPlan:
    new: list[ImportRow]
    updates: list[tuple[int, ImportRow, dict[str, Any]]]  # (card_id, row, changed fields)
    unchanged: int
    missing: list[dict[str, Any]]  # deck cards not in the file
    skipped_duplicates: int
    errors: list[dict[str, Any]]
    mapped: set[str]  # optional columns that were mapped (only those are updated)

    def summary(self) -> dict[str, Any]:
        return {
            "new": len(self.new),
            "updated": len(self.updates),
            "unchanged": self.unchanged,
            "skipped_duplicates": self.skipped_duplicates,
            "errors": self.errors,
            "missing": self.missing,
        }


def read_rows(parsed: ParsedFile, mapping: dict | None) -> tuple[list[ImportRow], list[dict[str, Any]], set[str]]:
    m = resolve_mapping(parsed, mapping)
    fi, bi, ri = m["front"], m["back"], m["reading"]
    si, smi, ni = m["sentence"], m["sentence_meaning"], m["notes"]
    mapped = {k for k in OPTIONAL_COLUMNS if m[k] is not None}
    first_row = 2 if parsed.has_header else 1
    rows: list[ImportRow] = []
    errors: list[dict[str, Any]] = []
    for n, row in enumerate(parsed.rows):
        rowno = first_row + n
        front, back = clean_field(row[fi]), clean_field(row[bi])
        reading = clean_field(row[ri]) if ri is not None else None
        if ri is None:
            front, reading = split_furigana(front)
        if not front or not back:
            errors.append({"row": rowno, "reason": "empty front or back"})
            continue
        rows.append(
            ImportRow(
                rowno,
                front,
                back,
                reading or None,
                (clean_sentence(row[si]) or None) if si is not None else None,
                (clean_field(row[smi]) or None) if smi is not None else None,
                (clean_field(row[ni]) or None) if ni is not None else None,
                list(row),
            )
        )
    return rows, errors, mapped


def _key(front: str, reading: str | None, back: str) -> tuple[str, str, str]:
    return (front, reading or "", back)


def plan_import(conn: sqlite3.Connection, parsed: ParsedFile, deck_id: int, mapping: dict | None) -> ImportPlan:
    rows, errors, mapped = read_rows(parsed, mapping)
    # Rows repeated in the file (same front + reading + back) are skipped.
    seen: set[tuple[str, str, str]] = set()
    unique: list[ImportRow] = []
    dupes = 0
    for r in rows:
        k = _key(r.front, r.reading, r.back)
        if k in seen:
            dupes += 1
            errors.append({"row": r.rowno, "reason": f"duplicate: {r.front}" + (f" ({r.reading})" if r.reading else "")})
            continue
        seen.add(k)
        unique.append(r)

    cards = conn.execute(
        "SELECT id, front, reading, back, sentence, sentence_meaning, notes, source_fields FROM cards "
        "WHERE deck_id = ? ORDER BY created_at, id",
        (deck_id,),
    ).fetchall()
    by_key = {_key(c["front"], c["reading"], c["back"]): c for c in cards}
    matched: dict[int, sqlite3.Row] = {}  # row index -> card
    used: set[int] = set()

    # Pass 1: exact front + reading + back.
    for i, r in enumerate(unique):
        c = by_key.get(_key(r.front, r.reading, r.back))
        if c is not None:
            matched[i] = c
            used.add(c["id"])

    # Pass 2: same front + reading but the meaning text changed. Match when it's the only
    # candidate on both sides, else tell candidates apart by the example sentence.
    groups_rows: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(unique):
        if i not in matched:
            groups_rows.setdefault((r.front, r.reading or ""), []).append(i)
    for (front, reading), idxs in groups_rows.items():
        cands = [c for c in cards if c["id"] not in used and c["front"] == front and (c["reading"] or "") == reading]
        if len(cands) == 1 and len(idxs) == 1:
            matched[idxs[0]] = cands[0]
            used.add(cands[0]["id"])
            continue
        for i in idxs:
            sent = unique[i].sentence
            hits = [c for c in cands if c["id"] not in used and sent and c["sentence"] == sent]
            if len(hits) == 1:
                matched[i] = hits[0]
                used.add(hits[0]["id"])

    new: list[ImportRow] = []
    updates: list[tuple[int, ImportRow, dict[str, Any]]] = []
    unchanged = 0
    for i, r in enumerate(unique):
        c = matched.get(i)
        if c is None:
            new.append(r)
            continue
        changes: dict[str, Any] = {}
        for col in CONTENT_COLUMNS:
            if col != "back" and col not in mapped:
                continue  # an unmapped column never wipes what the card has
            if getattr(r, col) != c[col]:
                changes[col] = getattr(r, col)
        source = json.dumps(r.source, ensure_ascii=False)
        if source != c["source_fields"]:
            changes["source_fields"] = source
        if changes:
            updates.append((c["id"], r, changes))
        else:
            unchanged += 1

    missing = [
        {"card_id": c["id"], "front": c["front"], "reading": c["reading"], "back": c["back"]}
        for c in cards
        if c["id"] not in used
    ]
    return ImportPlan(new, updates, unchanged, missing, dupes, errors, mapped)


def import_into_deck(
    conn: sqlite3.Connection,
    parsed: ParsedFile,
    deck_id: int,
    mapping: dict | None,
    now: str | None = None,
    missing: str = "keep",
) -> dict[str, Any]:
    """Apply the plan. `missing`: 'keep' or 'remove' the deck's cards not in the file.
    Returns the summary plus `imported` (new cards), `removed` and `removed_ids`."""
    if missing not in ("keep", "remove"):
        raise ImportError_("missing must be 'keep' or 'remove'")
    base = now or now_iso()
    with transaction(conn):
        plan = plan_import(conn, parsed, deck_id, mapping)
        errors = list(plan.errors)
        updated = 0
        for card_id, r, changes in plan.updates:
            changes = {**changes, "updated_at": base}
            try:
                conn.execute(
                    f"UPDATE cards SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                    (*changes.values(), card_id),
                )
                updated += 1
            except sqlite3.IntegrityError:
                errors.append({"row": r.rowno, "reason": f"would duplicate another card: {r.front}"})
        for r in plan.new:
            # created_at keeps file order (queue orders new cards by created_at, id).
            conn.execute(
                "INSERT INTO cards(deck_id, front, back, reading, sentence, sentence_meaning, notes, source_fields, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    deck_id,
                    r.front,
                    r.back,
                    r.reading,
                    r.sentence,
                    r.sentence_meaning,
                    r.notes,
                    json.dumps(r.source, ensure_ascii=False),
                    base,
                    base,
                ),
            )
        removed_ids: list[int] = []
        if missing == "remove" and plan.missing:
            removed_ids = [m["card_id"] for m in plan.missing]
            conn.executemany("DELETE FROM cards WHERE id = ?", [(i,) for i in removed_ids])
        conn.execute(
            "UPDATE decks SET source_info = ? WHERE id = ?",
            (
                json.dumps(
                    {"columns": parsed.columns, "meta": parsed.meta, "delimiter": parsed.delimiter},
                    ensure_ascii=False,
                ),
                deck_id,
            ),
        )
    out = plan.summary()
    out.update(
        {
            "imported": len(plan.new),
            "updated": updated,
            "errors": errors,
            "removed": len(removed_ids),
            "removed_ids": removed_ids,
        }
    )
    return out
