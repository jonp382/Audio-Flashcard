"""SQLite connection, schema/migrations, backups and plain queries (agent.md §8).

One connection per bridge call; never shared across threads.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import config
from .timeutil import to_iso, utc_now

# Each entry upgrades user_version N -> N+1. Never edit an applied step; append.
MIGRATIONS: list[str] = [
    # 0 -> 1: initial schema
    """
    CREATE TABLE decks (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        language TEXT NOT NULL CHECK (language IN ('ja','en')),
        new_per_day INTEGER NOT NULL DEFAULT 20,
        reviews_per_day INTEGER NOT NULL DEFAULT 200,
        learning_steps TEXT NOT NULL DEFAULT '[1,10]',
        relearning_steps TEXT NOT NULL DEFAULT '[10]',
        created_at TEXT NOT NULL
    );
    CREATE TABLE cards (
        id INTEGER PRIMARY KEY,
        deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        front TEXT NOT NULL,
        back TEXT NOT NULL,
        reading TEXT,
        alt_answers TEXT,
        state TEXT NOT NULL DEFAULT 'new'
            CHECK (state IN ('new','learning','review','relearning')),
        learning_step INTEGER,
        ease_factor REAL NOT NULL DEFAULT 2.5,
        interval_days INTEGER NOT NULL DEFAULT 0,
        repetitions INTEGER NOT NULL DEFAULT 0,
        due_date TEXT,
        due_at TEXT,
        last_review TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (deck_id, front)
    );
    CREATE INDEX idx_cards_due_date ON cards (deck_id, state, due_date);
    CREATE INDEX idx_cards_due_at ON cards (deck_id, state, due_at);
    CREATE TABLE reviews (
        id INTEGER PRIMARY KEY,
        card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
        deck_id INTEGER NOT NULL,
        reviewed_at TEXT NOT NULL,
        transcript TEXT NOT NULL,
        score INTEGER NOT NULL,
        score_original INTEGER,
        overridden INTEGER NOT NULL DEFAULT 0,
        is_correct INTEGER NOT NULL,
        feedback TEXT NOT NULL,
        state_after TEXT NOT NULL,
        interval_after INTEGER NOT NULL,
        ease_after REAL NOT NULL,
        state_before TEXT NOT NULL,
        step_before INTEGER,
        ef_before REAL NOT NULL,
        interval_before INTEGER NOT NULL,
        reps_before INTEGER NOT NULL,
        due_date_before TEXT,
        due_at_before TEXT,
        last_review_before TEXT,
        recording_id INTEGER
    );
    CREATE INDEX idx_reviews_deck_day ON reviews (deck_id, reviewed_at);
    CREATE INDEX idx_reviews_card ON reviews (card_id, id);
    CREATE TABLE recordings (
        id INTEGER PRIMARY KEY,
        card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_recordings_card ON recordings (card_id, id);
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 1 -> 2: custom study ("continue studying" without a separate deck)
    """
    ALTER TABLE reviews ADD COLUMN practice INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE study_extensions (
        deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        extra_new INTEGER NOT NULL DEFAULT 0,
        practice_since TEXT,
        PRIMARY KEY (deck_id, day)
    );
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, rollback on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"database is newer (v{version}) than this app (v{SCHEMA_VERSION})")
    while version < SCHEMA_VERSION:
        # executescript commits implicitly, so wrap the step and the version bump together.
        script = f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version + 1};\nCOMMIT;"
        try:
            conn.executescript(script)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        version += 1
    return version


_BACKUP_RE = re.compile(r"^app-(\d{8}-\d{6})(?:-(\d+))?\.db$")


def _backup_key(p: Path) -> tuple[str, int]:
    m = _BACKUP_RE.match(p.name)
    return m.group(1), int(m.group(2) or 0)


def backup(db_path: Path, backups_dir: Path, keep: int = config.BACKUPS_TO_KEEP, now: datetime | None = None) -> Path | None:
    """Online backup of an existing DB, then prune to the newest ``keep``."""
    if not db_path.exists():
        return None
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    dest = backups_dir / f"app-{stamp}.db"
    n = 1
    while dest.exists():
        dest = backups_dir / f"app-{stamp}-{n}.db"
        n += 1
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    existing = sorted((p for p in backups_dir.glob("app-*.db") if _BACKUP_RE.match(p.name)), key=_backup_key)
    for old in existing[:-keep] if keep > 0 else existing:
        old.unlink(missing_ok=True)
    return dest


def now_iso() -> str:
    return to_iso(utc_now())


# --- Row helpers ---------------------------------------------------------------


def deck_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["learning_steps"] = json.loads(d["learning_steps"])
    d["relearning_steps"] = json.loads(d["relearning_steps"])
    return d


def card_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["alt_answers"] = json.loads(d["alt_answers"]) if d.get("alt_answers") else []
    return d


def get_deck(conn: sqlite3.Connection, deck_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()


def get_card(conn: sqlite3.Connection, card_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()


# --- Settings ------------------------------------------------------------------


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    out = dict(config.DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in out:
            out[row["key"]] = json.loads(row["value"])
    return out


def set_settings(conn: sqlite3.Connection, patch: dict[str, Any]) -> None:
    with transaction(conn):
        for k, v in patch.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, json.dumps(v)),
            )
