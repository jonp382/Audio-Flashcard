"""The app's own deck file (.afdeck): a whole deck with its progress, as JSON.

Export carries everything needed to bring a deck back after a wipe: deck settings, the
source file's column names / Anki header, and per card every content field, the whole
original import row (all Anki columns: audio, pitch, furigana, ...), the schedule and
the full review history. Recordings (audio files) are not included.

Import always creates a NEW deck. Files are untrusted input, so everything is validated
before anything is written, and the whole import is one transaction.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .database import transaction
from .timeutil import from_iso, to_iso

FORMAT = "audio-flashcard-deck"
VERSION = 1
SUFFIX = ".afdeck"
MAX_BYTES = 64 * 1024 * 1024

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

State = Literal["new", "learning", "review", "relearning"]

CARD_SCHEDULE = (
    "state", "learning_step", "ease_factor", "interval_days", "repetitions",
    "due_date", "due_at", "last_review", "created_at", "updated_at",
)  # fmt: skip
REVIEW_FIELDS = (
    "reviewed_at", "transcript", "score", "score_original", "overridden", "is_correct", "feedback",
    "state_after", "interval_after", "ease_after", "state_before", "step_before", "ef_before",
    "interval_before", "reps_before", "due_date_before", "due_at_before", "last_review_before", "practice",
)  # fmt: skip


class DeckFileError(Exception):
    pass


def _iso(v: str | None) -> str | None:
    if v is None:
        return v
    try:
        from_iso(v)
    except (ValueError, TypeError) as e:
        raise ValueError(f"not a timestamp: {v!r}") from e
    return v


def _day(v: str | None) -> str | None:
    if v is not None and not _DATE_RE.match(v):
        raise ValueError(f"not a date: {v!r}")
    return v


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class StepModel(_Strict):
    step: Literal["reading", "meaning"]
    transcript: str
    score: int = Field(ge=0, le=3)
    score_original: int | None = Field(default=None, ge=0, le=3)
    overridden: bool = False
    feedback: str


class ReviewModel(_Strict):
    reviewed_at: str
    transcript: str
    score: int = Field(ge=0, le=3)
    score_original: int | None = Field(default=None, ge=0, le=3)
    overridden: bool
    is_correct: bool
    feedback: str
    state_after: State
    interval_after: int = Field(ge=0)
    ease_after: float = Field(gt=0)
    state_before: State
    step_before: int | None = Field(default=None, ge=0)
    ef_before: float = Field(gt=0)
    interval_before: int = Field(ge=0)
    reps_before: int = Field(ge=0)
    due_date_before: str | None = None
    due_at_before: str | None = None
    last_review_before: str | None = None
    practice: bool = False
    steps: list[StepModel] | None = None

    _ts = field_validator("reviewed_at", "due_at_before", "last_review_before")(_iso)
    _d = field_validator("due_date_before")(_day)


class CardModel(_Strict):
    front: str = Field(min_length=1)
    back: str = Field(min_length=1)
    reading: str | None = None
    alt_answers: list[str] = []
    sentence: str | None = None
    sentence_meaning: str | None = None
    notes: str | None = None
    source_fields: list[str] | None = None
    state: State
    learning_step: int | None = Field(default=None, ge=0)
    ease_factor: float = Field(gt=0)
    interval_days: int = Field(ge=0)
    repetitions: int = Field(ge=0)
    due_date: str | None = None
    due_at: str | None = None
    last_review: str | None = None
    created_at: str
    updated_at: str
    reviews: list[ReviewModel] = []

    _ts = field_validator("due_at", "last_review", "created_at", "updated_at")(_iso)
    _d = field_validator("due_date")(_day)


class ExtensionModel(_Strict):
    day: str
    extra_new: int = Field(ge=0)
    practice_since: str | None = None

    _d = field_validator("day")(_day)
    _ts = field_validator("practice_since")(_iso)


class DeckModel(_Strict):
    name: str = Field(min_length=1, max_length=200)
    language: Literal["ja", "en"]
    new_per_day: int = Field(ge=0, le=9999)
    reviews_per_day: int = Field(ge=0, le=99999)
    learning_steps: list[int] = Field(max_length=20)
    relearning_steps: list[int] = Field(max_length=20)
    created_at: str
    source_info: dict[str, Any] | None = None

    _ts = field_validator("created_at")(_iso)

    @field_validator("learning_steps", "relearning_steps")
    @classmethod
    def _steps(cls, v: list[int]) -> list[int]:
        if any(s <= 0 for s in v):
            raise ValueError("steps must be positive minutes")
        return v


class DeckFile(_Strict):
    format: Literal["audio-flashcard-deck"]
    version: int
    exported_at: str
    deck: DeckModel
    cards: list[CardModel]
    study_extensions: list[ExtensionModel] = []


# --- export ---------------------------------------------------------------------------


def export_deck(conn: sqlite3.Connection, deck_id: int, now: datetime) -> dict[str, Any]:
    deck = conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
    if deck is None:
        raise DeckFileError("Deck not found")
    reviews_by_card: dict[int, list[dict[str, Any]]] = {}
    for r in conn.execute("SELECT * FROM reviews WHERE deck_id = ? ORDER BY id", (deck_id,)):
        rv = {k: r[k] for k in REVIEW_FIELDS}
        for k in ("overridden", "is_correct", "practice"):
            rv[k] = bool(rv[k])
        rv["steps"] = json.loads(r["steps"]) if r["steps"] else None
        reviews_by_card.setdefault(r["card_id"], []).append(rv)
    cards = []
    for c in conn.execute("SELECT * FROM cards WHERE deck_id = ? ORDER BY created_at, id", (deck_id,)):
        card = {
            "front": c["front"],
            "back": c["back"],
            "reading": c["reading"],
            "alt_answers": json.loads(c["alt_answers"]) if c["alt_answers"] else [],
            "sentence": c["sentence"],
            "sentence_meaning": c["sentence_meaning"],
            "notes": c["notes"],
            "source_fields": json.loads(c["source_fields"]) if c["source_fields"] else None,
            **{k: c[k] for k in CARD_SCHEDULE},
            "reviews": reviews_by_card.get(c["id"], []),
        }
        cards.append(card)
    extensions = [
        {"day": e["day"], "extra_new": e["extra_new"], "practice_since": e["practice_since"]}
        for e in conn.execute("SELECT * FROM study_extensions WHERE deck_id = ? ORDER BY day", (deck_id,))
    ]
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": to_iso(now),
        "deck": {
            "name": deck["name"],
            "language": deck["language"],
            "new_per_day": deck["new_per_day"],
            "reviews_per_day": deck["reviews_per_day"],
            "learning_steps": json.loads(deck["learning_steps"]),
            "relearning_steps": json.loads(deck["relearning_steps"]),
            "created_at": deck["created_at"],
            "source_info": json.loads(deck["source_info"]) if deck["source_info"] else None,
        },
        "cards": cards,
        "study_extensions": extensions,
    }


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


# --- import ---------------------------------------------------------------------------


def parse(raw: bytes) -> DeckFile:
    if len(raw) > MAX_BYTES:
        raise DeckFileError("The file is too large to be a deck file.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DeckFileError("This is not an Audio Flashcard deck file.") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise DeckFileError("This is not an Audio Flashcard deck file.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise DeckFileError("This deck file was made by a newer version of the app.")
    try:
        return DeckFile.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise DeckFileError(f"The deck file is damaged ({where}: {first['msg']}).") from e


def import_deck(conn: sqlite3.Connection, f: DeckFile, now: datetime) -> dict[str, Any]:
    """Create a new deck from a parsed file. Returns {"deck_id", "cards", "reviews"}."""
    names = {r[0] for r in conn.execute("SELECT name FROM decks")}
    name = f.deck.name
    if name in names:
        base = f"{name} (restored {now.astimezone().strftime('%Y-%m-%d')})"
        name, n = base, 2
        while name in names:
            name, n = f"{base} {n}", n + 1
    d = f.deck
    n_reviews = 0
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO decks(name, language, new_per_day, reviews_per_day, learning_steps, relearning_steps, "
            "created_at, source_info) VALUES (?,?,?,?,?,?,?,?)",
            (
                name,
                d.language,
                d.new_per_day,
                d.reviews_per_day,
                json.dumps(d.learning_steps),
                json.dumps(d.relearning_steps),
                d.created_at,
                json.dumps(d.source_info, ensure_ascii=False) if d.source_info is not None else None,
            ),
        )
        deck_id = cur.lastrowid
        for c in f.cards:
            try:
                cur = conn.execute(
                    "INSERT INTO cards(deck_id, front, back, reading, alt_answers, sentence, sentence_meaning, notes, "
                    "source_fields, state, learning_step, ease_factor, interval_days, repetitions, due_date, due_at, "
                    "last_review, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        deck_id,
                        c.front,
                        c.back,
                        c.reading,
                        json.dumps(c.alt_answers, ensure_ascii=False) if c.alt_answers else None,
                        c.sentence,
                        c.sentence_meaning,
                        c.notes,
                        json.dumps(c.source_fields, ensure_ascii=False) if c.source_fields is not None else None,
                        *(getattr(c, k) for k in CARD_SCHEDULE),
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise DeckFileError(f"The deck file has the same card twice: {c.front}") from e
            card_id = cur.lastrowid
            for r in c.reviews:
                conn.execute(
                    f"INSERT INTO reviews(card_id, deck_id, {', '.join(REVIEW_FIELDS)}, steps) "
                    f"VALUES (?, ?, {', '.join('?' * len(REVIEW_FIELDS))}, ?)",
                    (
                        card_id,
                        deck_id,
                        *(int(v) if isinstance(v, bool) else v for v in (getattr(r, k) for k in REVIEW_FIELDS)),
                        json.dumps([s.model_dump() for s in r.steps], ensure_ascii=False) if r.steps else None,
                    ),
                )
                n_reviews += 1
        for e in f.study_extensions:
            conn.execute(
                "INSERT INTO study_extensions(deck_id, day, extra_new, practice_since) VALUES (?,?,?,?) "
                "ON CONFLICT(deck_id, day) DO NOTHING",
                (deck_id, e.day, e.extra_new, e.practice_since),
            )
    return {"deck_id": deck_id, "cards": len(f.cards), "reviews": n_reviews}
