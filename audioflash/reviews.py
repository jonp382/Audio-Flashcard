"""Persisting reviews: apply SRS + log review + save recording; override; undo
(agent.md §7, §8). Grading is NOT done here: callers pass an already-graded result,
which keeps grading separate from SRS (§13)."""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, scheduler, srs
from .database import get_card, get_deck, transaction
from .paths import DataPaths
from .timeutil import from_iso, to_iso

log = logging.getLogger(__name__)


class ReviewError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Grade:
    score: int
    feedback: str

    @property
    def is_correct(self) -> bool:
        return self.score >= 2


@dataclass(frozen=True)
class StepResult:
    """One step of a two-step card (agent.md §13): 'reading' or 'meaning'."""

    step: str
    transcript: str
    grade: Grade

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "transcript": self.transcript,
            "score": self.grade.score,
            "score_original": None,
            "overridden": False,
            "feedback": self.grade.feedback,
        }


def combine_steps(steps: list[StepResult]) -> tuple[str, Grade]:
    """Card-level transcript + grade: the LOWEST step score ("weakest link")."""
    transcript = " / ".join(s.transcript for s in steps)
    score = min(s.grade.score for s in steps)
    feedback = " ".join(f"{s.step.capitalize()}: {s.grade.feedback}" for s in steps)
    return transcript, Grade(score=score, feedback=feedback)


def deck_steps(deck: sqlite3.Row) -> srs.DeckSteps:
    return srs.DeckSteps(json.loads(deck["learning_steps"]), json.loads(deck["relearning_steps"]))


def _write_card_state(conn: sqlite3.Connection, card_id: int, cs: srs.CardState) -> None:
    conn.execute(
        "UPDATE cards SET state=?, learning_step=?, ease_factor=?, interval_days=?, repetitions=?, "
        "due_date=?, due_at=?, last_review=? WHERE id=?",
        (
            cs.state,
            cs.learning_step,
            cs.ease_factor,
            cs.interval_days,
            cs.repetitions,
            cs.due_date,
            cs.due_at,
            cs.last_review,
            card_id,
        ),
    )


def _snapshot_from_review(r: sqlite3.Row) -> srs.CardState:
    return srs.CardState(
        state=r["state_before"],
        learning_step=r["step_before"],
        ease_factor=r["ef_before"],
        interval_days=r["interval_before"],
        repetitions=r["reps_before"],
        due_date=r["due_date_before"],
        due_at=r["due_at_before"],
        last_review=r["last_review_before"],
    )


def schedule_after(before: srs.CardState, score: int, deck: sqlite3.Row, now: datetime, practice: bool) -> srs.CardState:
    """Practice answers (custom-study pass over cards already studied today) only
    change the schedule when missed; a correct practice answer leaves it untouched."""
    if practice and score >= 2:
        return before
    return srs.apply_review(before, score, deck_steps(deck), now)


def result_dict(conn: sqlite3.Connection, review_id: int) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    card = get_card(conn, r["card_id"])
    return {
        "review_id": r["id"],
        "card_id": r["card_id"],
        "deck_id": r["deck_id"],
        "transcript": r["transcript"],
        "score": r["score"],
        "score_original": r["score_original"],
        "overridden": bool(r["overridden"]),
        "is_correct": bool(r["is_correct"]),
        "feedback": r["feedback"],
        "expected": {
            "front": card["front"],
            "back": card["back"],
            "reading": card["reading"],
            "alt_answers": json.loads(card["alt_answers"]) if card["alt_answers"] else [],
        },
        "state": card["state"],
        "next_due": card["due_date"] or card["due_at"],
        "interval_days": card["interval_days"],
        "ease_factor": card["ease_factor"],
        "recording_id": r["recording_id"],
        "practice": bool(r["practice"]),
        "steps": json.loads(r["steps"]) if r["steps"] else None,
    }


# --- Recordings ------------------------------------------------------------------


def save_recording(
    conn: sqlite3.Connection, paths: DataPaths, card_id: int, ogg: bytes, now: datetime
) -> tuple[int, list[Path]]:
    """Insert row + write file. Must be called inside a transaction. Returns the new id
    and the files of pruned old recordings, to delete after commit."""
    cur = conn.execute(
        "INSERT INTO recordings(card_id, path, created_at) VALUES (?, '', ?)", (card_id, to_iso(now))
    )
    rec_id = cur.lastrowid
    rel = f"{int(card_id)}/{rec_id}.ogg"
    target = paths.recordings / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ogg)
    conn.execute("UPDATE recordings SET path = ? WHERE id = ?", (rel, rec_id))
    old = conn.execute(
        "SELECT id, path FROM recordings WHERE card_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?",
        (card_id, config.RECORDINGS_PER_CARD),
    ).fetchall()
    stale = []
    for row in old:
        conn.execute("DELETE FROM recordings WHERE id = ?", (row["id"],))
        stale.append(paths.recordings / row["path"])
    return rec_id, stale


def recording_path(conn: sqlite3.Connection, paths: DataPaths, recording_id: int) -> Path | None:
    row = conn.execute("SELECT path FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    if not row or not row["path"]:
        return None
    p = (paths.recordings / row["path"]).resolve()
    if paths.recordings.resolve() not in p.parents:
        return None
    return p


def delete_files(files: list[Path]) -> None:
    for f in files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            log.warning("could not delete %s", f, exc_info=True)


def delete_card_dirs(paths: DataPaths, card_ids: list[int]) -> None:
    for cid in card_ids:
        shutil.rmtree(paths.card_recordings(cid), ignore_errors=True)


# --- Review flow -------------------------------------------------------------------


def record_review(
    conn: sqlite3.Connection,
    paths: DataPaths,
    card_id: int,
    transcript: str,
    grade: Grade,
    ogg: bytes | None,
    now: datetime,
    steps: list[StepResult] | None = None,
) -> dict[str, Any]:
    """`steps` given: a two-step review; `transcript` and `grade` are the combined ones."""
    stale: list[Path] = []
    new_file: Path | None = None
    try:
        with transaction(conn):
            card = get_card(conn, card_id)
            if card is None:
                raise ReviewError("not_found", "Card not found")
            deck = get_deck(conn, card["deck_id"])
            before = srs.CardState.from_row(card)
            practice = scheduler.is_practice(card, now)
            after = schedule_after(before, grade.score, deck, now, practice)
            rec_id = None
            if ogg:
                rec_id, stale = save_recording(conn, paths, card_id, ogg, now)
                new_file = paths.recordings / f"{int(card_id)}/{rec_id}.ogg"
            _write_card_state(conn, card_id, after)
            cur = conn.execute(
                "INSERT INTO reviews(card_id, deck_id, reviewed_at, transcript, score, is_correct, feedback, "
                "state_after, interval_after, ease_after, state_before, step_before, ef_before, interval_before, "
                "reps_before, due_date_before, due_at_before, last_review_before, recording_id, practice, steps) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    card_id,
                    card["deck_id"],
                    to_iso(now),
                    transcript,
                    grade.score,
                    int(grade.is_correct),
                    grade.feedback,
                    after.state,
                    after.interval_days,
                    after.ease_factor,
                    before.state,
                    before.learning_step,
                    before.ease_factor,
                    before.interval_days,
                    before.repetitions,
                    before.due_date,
                    before.due_at,
                    before.last_review,
                    rec_id,
                    int(practice),
                    json.dumps([s.to_json() for s in steps], ensure_ascii=False) if steps else None,
                ),
            )
            review_id = cur.lastrowid
    except BaseException:
        if new_file is not None:
            delete_files([new_file])
        raise
    delete_files(stale)
    return result_dict(conn, review_id)


def override_grade(conn: sqlite3.Connection, review_id: int, correct: bool, step: str | None = None) -> dict[str, Any]:
    """Mark the review right (3) or wrong (1). On a two-step review, `step` overrides one
    step and the card score is recomputed as the lowest step score; no `step` overrides
    every step."""
    with transaction(conn):
        r = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
        if r is None:
            raise ReviewError("not_found", "Review not found")
        latest = conn.execute(
            "SELECT MAX(id) FROM reviews WHERE card_id = ?", (r["card_id"],)
        ).fetchone()[0]
        if latest != review_id:
            raise ReviewError("invalid_input", "Only the card's most recent review can be overridden")
        deck = get_deck(conn, r["deck_id"])
        steps = json.loads(r["steps"]) if r["steps"] else None
        if step is not None and (not steps or step not in {s["step"] for s in steps}):
            raise ReviewError("invalid_input", f"This review has no {step} step")
        score = 3 if correct else 1
        if steps:
            for s in steps:
                if step is None or s["step"] == step:
                    if s["score_original"] is None:
                        s["score_original"] = s["score"]
                    s["score"] = score
                    s["overridden"] = True
            score = min(s["score"] for s in steps)
        before = _snapshot_from_review(r)
        after = schedule_after(before, score, deck, from_iso(r["reviewed_at"]), bool(r["practice"]))
        _write_card_state(conn, r["card_id"], after)
        original = r["score_original"] if r["score_original"] is not None else r["score"]
        conn.execute(
            "UPDATE reviews SET score=?, score_original=?, overridden=1, is_correct=?, "
            "state_after=?, interval_after=?, ease_after=?, steps=? WHERE id=?",
            (
                score,
                original,
                int(score >= 2),
                after.state,
                after.interval_days,
                after.ease_factor,
                json.dumps(steps, ensure_ascii=False) if steps else None,
                review_id,
            ),
        )
    return result_dict(conn, review_id)


def last_review(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The review undo would revert next (the newest in the whole app), so the UI can
    ask before undoing one from another deck. Read-only."""
    r = conn.execute(
        "SELECT r.id, r.card_id, r.deck_id, r.reviewed_at, c.front, c.reading, d.name AS deck_name "
        "FROM reviews r JOIN cards c ON c.id = r.card_id JOIN decks d ON d.id = r.deck_id "
        "ORDER BY r.id DESC LIMIT 1"
    ).fetchone()
    if r is None:
        return None
    return {
        "review_id": r["id"],
        "card_id": r["card_id"],
        "deck_id": r["deck_id"],
        "deck_name": r["deck_name"],
        "front": r["front"],
        "reading": r["reading"],
        "reviewed_at": r["reviewed_at"],
    }


def undo_last_review(conn: sqlite3.Connection, paths: DataPaths, review_id: int | None = None) -> dict[str, int]:
    """`review_id`: only undo if that is still the newest review (the one the user
    confirmed), so a review logged in between is never undone by mistake."""
    files: list[Path] = []
    with transaction(conn):
        r = conn.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            raise ReviewError("not_found", "Nothing to undo")
        if review_id is not None and r["id"] != review_id:
            raise ReviewError("invalid_input", "Another review was made since; nothing was undone")
        card = get_card(conn, r["card_id"])
        if card["updated_at"] > r["reviewed_at"]:
            raise ReviewError("invalid_input", "The card was edited after this review; undo is not possible")
        _write_card_state(conn, r["card_id"], _snapshot_from_review(r))
        conn.execute("DELETE FROM reviews WHERE id = ?", (r["id"],))
        if r["recording_id"] is not None:
            p = recording_path(conn, paths, r["recording_id"])
            if p is not None:
                files.append(p)
            conn.execute("DELETE FROM recordings WHERE id = ?", (r["recording_id"],))
    delete_files(files)
    return {"card_id": r["card_id"], "deck_id": r["deck_id"]}
