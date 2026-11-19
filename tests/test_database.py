import os
import sqlite3
import time
from datetime import datetime

import pytest

from audioflash import database
from audioflash.database import MIGRATIONS, SCHEMA_VERSION


def test_fresh_migration(paths):
    c = database.connect(paths.db)
    assert database.migrate(c) == SCHEMA_VERSION
    assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"decks", "cards", "reviews", "recordings", "settings"} <= tables
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    # idempotent
    assert database.migrate(c) == SCHEMA_VERSION
    c.close()


@pytest.mark.parametrize("start", range(SCHEMA_VERSION))
def test_upgrade_from_each_previous_version(paths, start):
    c = database.connect(paths.db)
    for v in range(start):
        c.executescript(f"BEGIN;{MIGRATIONS[v]}PRAGMA user_version={v + 1};COMMIT;")
    assert database.migrate(c) == SCHEMA_VERSION
    c.close()


def test_newer_db_refused(paths):
    c = database.connect(paths.db)
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError):
        database.migrate(c)
    c.close()


def test_failed_migration_rolls_back(paths, monkeypatch):
    monkeypatch.setattr(database, "MIGRATIONS", ["CREATE TABLE a(x); THIS IS NOT SQL;"])
    monkeypatch.setattr(database, "SCHEMA_VERSION", 1)
    c = database.connect(paths.db)
    with pytest.raises(sqlite3.Error):
        database.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    assert c.execute("SELECT name FROM sqlite_master WHERE name='a'").fetchone() is None
    c.close()


def test_backup_none_when_no_db(paths):
    assert database.backup(paths.db, paths.backups) is None


def test_backup_created_and_pruned_to_7(paths, conn):
    conn.execute("INSERT INTO settings(key, value) VALUES ('k', '1')")
    made = []
    for i in range(10):
        made.append(database.backup(paths.db, paths.backups, now=datetime(2026, 1, 1, 0, 0, i)))
    # same-second backups get a -n suffix and still sort newest-last
    made.append(database.backup(paths.db, paths.backups, now=datetime(2026, 1, 1, 0, 0, 9)))
    left = sorted(paths.backups.glob("app-*.db"))
    assert len(left) == 7
    assert set(left) == set(made[-7:])
    b = sqlite3.connect(left[-1])
    assert b.execute("SELECT value FROM settings WHERE key='k'").fetchone()[0] == "1"
    b.close()


def test_backup_before_migration_in_startup_order(paths):
    """A pre-existing (old) DB is backed up as-is before migrate() runs."""
    c = sqlite3.connect(paths.db)
    c.execute("CREATE TABLE legacy(x)")
    c.commit()
    c.close()
    b = database.backup(paths.db, paths.backups)
    c = database.connect(paths.db)
    database.migrate(c)
    c.close()
    old = sqlite3.connect(b)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 0
    assert old.execute("SELECT name FROM sqlite_master WHERE name='decks'").fetchone() is None
    old.close()


def test_settings_defaults_and_roundtrip(conn):
    s = database.get_settings(conn)
    assert s["llm_model"] == "qwen3:8b" and s["record_mode"] == "toggle"
    database.set_settings(conn, {"record_mode": "hold", "ollama_port": 1234})
    s = database.get_settings(conn)
    assert s["record_mode"] == "hold" and s["ollama_port"] == 1234


def test_foreign_key_cascade(conn):
    conn.execute("INSERT INTO decks(id, name, language, created_at) VALUES (1,'d','ja','x')")
    conn.execute("INSERT INTO cards(id, deck_id, front, back, created_at, updated_at) VALUES (1,1,'f','b','x','x')")
    conn.execute("INSERT INTO recordings(card_id, path, created_at) VALUES (1,'1/1.ogg','x')")
    conn.execute("DELETE FROM decks WHERE id=1")
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 0


def test_unique_card_per_deck(conn):
    conn.execute("INSERT INTO decks(id, name, language, created_at) VALUES (1,'d','ja','x')")
    conn.execute("INSERT INTO cards(deck_id, front, back, created_at, updated_at) VALUES (1,'f','b','x','x')")
    conn.execute("INSERT INTO cards(deck_id, front, back, created_at, updated_at) VALUES (1,'f','c','x','x')")
    with pytest.raises(sqlite3.IntegrityError):  # NULL reading counts as one value
        conn.execute("INSERT INTO cards(deck_id, front, back, created_at, updated_at) VALUES (1,'f','b','x','x')")


def test_language_check(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO decks(name, language, created_at) VALUES ('d','fr','x')")


def test_migration_4_rebuilds_cards_keeping_history(paths):
    """v3 -> v4 drops UNIQUE(deck_id, front) by rebuilding cards; reviews and
    recordings that point at cards must survive (no cascade delete)."""
    c = database.connect(paths.db)
    for v in range(3):
        c.executescript(f"BEGIN;{MIGRATIONS[v]}PRAGMA user_version={v + 1};COMMIT;")
    c.execute("INSERT INTO decks(id, name, language, created_at) VALUES (1, 'D', 'ja', 't')")
    c.execute(
        "INSERT INTO cards(id, deck_id, front, back, reading, state, created_at, updated_at) "
        "VALUES (7, 1, '方', 'direction', 'ほう', 'review', 't', 't')"
    )
    c.execute(
        "INSERT INTO reviews(card_id, deck_id, reviewed_at, transcript, score, is_correct, feedback, state_after, "
        "interval_after, ease_after, state_before, ef_before, interval_before, reps_before) "
        "VALUES (7, 1, 't', 'ほう', 3, 1, 'ok', 'review', 1, 2.5, 'new', 2.5, 0, 0)"
    )
    c.execute("INSERT INTO recordings(card_id, path, created_at) VALUES (7, '7/1.ogg', 't')")
    assert database.migrate(c) == SCHEMA_VERSION
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    card = c.execute("SELECT * FROM cards WHERE id = 7").fetchone()
    assert (card["front"], card["reading"], card["state"]) == ("方", "ほう", "review")
    assert card["sentence"] is None and card["sentence_meaning"] is None
    assert c.execute("SELECT COUNT(*) FROM reviews WHERE card_id = 7").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM recordings WHERE card_id = 7").fetchone()[0] == 1
    # same front with another reading or meaning: allowed; all three the same: refused
    ins = "INSERT INTO cards(deck_id, front, back, reading, created_at, updated_at) VALUES (1, '方', ?, ?, 't', 't')"
    c.execute(ins, ("person", "かた"))
    c.execute(ins, ("side", "ほう"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(ins, ("direction", "ほう"))
    # cascade still works after the rebuild
    c.execute("DELETE FROM cards WHERE id = 7")
    assert c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    c.close()
