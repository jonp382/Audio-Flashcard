"""Session queue, daily limits and custom study (agent.md §7). Computed fresh every call.

Custom study ("continue studying") never creates a separate deck. It has two parts,
both stored per deck per local day in ``study_extensions``:

- extra new cards: raises today's new-card limit; those cards go through the normal
  schedule exactly like any other new card.
- practice pass over cards already studied today: each such card is shown once more.
  These answers are logged with ``practice = 1``: a correct answer leaves the schedule
  alone (answering again minutes later says nothing about long-term memory), a miss is
  applied as a real lapse. Practice answers never count against daily limits.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from . import config
from .database import card_to_dict
from .timeutil import local_date, local_day_bounds_utc, to_iso


def _today(now: datetime) -> str:
    return local_date(now).isoformat()


def extension(conn: sqlite3.Connection, deck_id: int, now: datetime) -> dict[str, Any]:
    row = conn.execute(
        "SELECT extra_new, practice_since FROM study_extensions WHERE deck_id = ? AND day = ?",
        (deck_id, _today(now)),
    ).fetchone()
    return {"extra_new": row["extra_new"], "practice_since": row["practice_since"]} if row else {"extra_new": 0, "practice_since": None}


def extend(conn: sqlite3.Connection, deck_id: int, now: datetime, extra_new: int, practice: bool) -> None:
    """Add extra new cards for today and/or (re)start a practice pass. Caller wraps in a transaction."""
    conn.execute(
        "INSERT INTO study_extensions(deck_id, day, extra_new, practice_since) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(deck_id, day) DO UPDATE SET extra_new = extra_new + excluded.extra_new, "
        "practice_since = COALESCE(excluded.practice_since, practice_since)",
        (deck_id, _today(now), extra_new, to_iso(now) if practice else None),
    )


def done_today(conn: sqlite3.Connection, deck_id: int, now: datetime) -> dict[str, int]:
    """Reviews logged today (local date), by state_before. Undone reviews are deleted
    so they drop out automatically; practice answers never count."""
    start, end = local_day_bounds_utc(local_date(now))
    rows = conn.execute(
        "SELECT state_before, COUNT(*) AS n FROM reviews "
        "WHERE deck_id = ? AND reviewed_at >= ? AND reviewed_at < ? AND practice = 0 GROUP BY state_before",
        (deck_id, start, end),
    ).fetchall()
    by = {r["state_before"]: r["n"] for r in rows}
    return {"new": by.get("new", 0), "review": by.get("review", 0)}


def _remaining(deck: sqlite3.Row, conn: sqlite3.Connection, now: datetime) -> tuple[int, int]:
    done = done_today(conn, deck["id"], now)
    extra = extension(conn, deck["id"], now)["extra_new"]
    return (
        max(0, deck["new_per_day"] + extra - done["new"]),
        max(0, deck["reviews_per_day"] - done["review"]),
    )


def is_practice(card: sqlite3.Row, now: datetime) -> bool:
    """True if answering this card now is NOT part of its normal schedule (it is not due,
    not even within the learn-ahead window). Only the practice pass shows such cards."""
    if card["state"] == "new":
        return False
    if card["state"] == "review":
        return (card["due_date"] or "") > _today(now)
    ahead = to_iso(now + timedelta(minutes=config.LEARN_AHEAD_MINUTES))
    return (card["due_at"] or "") > ahead


def practice_candidates(conn: sqlite3.Connection, deck_id: int, now: datetime) -> list[sqlite3.Row]:
    """Cards studied today, not answered since the practice pass started, and not due
    (due ones come through the normal queue). Ordered by when they were first studied."""
    since = extension(conn, deck_id, now)["practice_since"]
    if since is None:
        return []
    start, end = local_day_bounds_utc(local_date(now))
    rows = conn.execute(
        "SELECT c.*, MIN(r.reviewed_at) AS first_today FROM cards c JOIN reviews r ON r.card_id = c.id "
        "WHERE c.deck_id = ? AND r.reviewed_at >= ? AND r.reviewed_at < ? "
        "AND NOT EXISTS (SELECT 1 FROM reviews r2 WHERE r2.card_id = c.id AND r2.reviewed_at > ?) "
        "GROUP BY c.id ORDER BY first_today, c.id",
        (deck_id, start, end, since),
    ).fetchall()
    return [r for r in rows if is_practice(r, now)]


def counts(conn: sqlite3.Connection, deck: sqlite3.Row, now: datetime) -> dict[str, int]:
    """Cards due now after limits: new (blue), learning (red), review (green), plus the
    practice cards left in an active practice pass."""
    deck_id = deck["id"]
    now_iso = to_iso(now)
    new_left, rev_left = _remaining(deck, conn, now)
    learning = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE deck_id = ? AND state IN ('learning','relearning') AND due_at <= ?",
        (deck_id, now_iso),
    ).fetchone()[0]
    review = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE deck_id = ? AND state = 'review' AND due_date <= ?",
        (deck_id, _today(now)),
    ).fetchone()[0]
    new = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id = ? AND state = 'new'", (deck_id,)).fetchone()[0]
    return {
        "new": min(new, new_left),
        "learning": learning,
        "review": min(review, rev_left),
        "practice": len(practice_candidates(conn, deck_id, now)),
    }


def custom_study_options(conn: sqlite3.Connection, deck: sqlite3.Row, now: datetime) -> dict[str, int]:
    deck_id = deck["id"]
    new_left, _ = _remaining(deck, conn, now)
    unseen = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id = ? AND state = 'new'", (deck_id,)).fetchone()[0]
    start, end = local_day_bounds_utc(local_date(now))
    studied = conn.execute(
        "SELECT c.* FROM cards c WHERE c.deck_id = ? AND EXISTS (SELECT 1 FROM reviews r WHERE r.card_id = c.id "
        "AND r.reviewed_at >= ? AND r.reviewed_at < ?)",
        (deck_id, start, end),
    ).fetchall()
    return {
        "new_available": max(0, unseen - new_left),  # new cards beyond today's current limit
        "studied_today": sum(1 for c in studied if is_practice(c, now)),
        "extra_new_today": extension(conn, deck_id, now)["extra_new"],
    }


def next_card(conn: sqlite3.Connection, deck: sqlite3.Row, now: datetime) -> dict[str, Any]:
    deck_id = deck["id"]
    now_iso = to_iso(now)
    new_left, rev_left = _remaining(deck, conn, now)

    def first(sql: str, params: tuple) -> sqlite3.Row | None:
        return conn.execute(sql, params).fetchone()

    card = first(
        "SELECT * FROM cards WHERE deck_id = ? AND state IN ('learning','relearning') AND due_at <= ? "
        "ORDER BY due_at, id LIMIT 1",
        (deck_id, now_iso),
    )
    if card is None and rev_left > 0:
        card = first(
            "SELECT * FROM cards WHERE deck_id = ? AND state = 'review' AND due_date <= ? "
            "ORDER BY due_date, id LIMIT 1",
            (deck_id, _today(now)),
        )
    if card is None and new_left > 0:
        card = first(
            "SELECT * FROM cards WHERE deck_id = ? AND state = 'new' ORDER BY created_at, id LIMIT 1",
            (deck_id,),
        )
    if card is None:
        ahead = to_iso(now + timedelta(minutes=config.LEARN_AHEAD_MINUTES))
        card = first(
            "SELECT * FROM cards WHERE deck_id = ? AND state IN ('learning','relearning') AND due_at <= ? "
            "ORDER BY due_at, id LIMIT 1",
            (deck_id, ahead),
        )
    practice = False
    if card is None:
        cands = practice_candidates(conn, deck_id, now)
        if cands:
            card, practice = cands[0], True

    next_due = None
    if card is None:
        row = first(
            "SELECT MIN(due_at) AS d FROM cards WHERE deck_id = ? AND state IN ('learning','relearning')",
            (deck_id,),
        )
        next_due = row["d"] if row else None

    out_card = None
    if card is not None:
        out_card = card_to_dict(card)
        out_card.pop("first_today", None)
        out_card["practice"] = practice
    return {"card": out_card, "counts": counts(conn, deck, now), "next_learning_due_at": next_due}
