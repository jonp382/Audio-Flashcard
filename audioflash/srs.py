"""SM-2 scheduling with Anki-style learning/relearning steps (agent.md §7).

Pure functions only: no DB, no clock reads. Callers pass ``now`` (aware UTC) and
optionally ``today`` (local date; derived from ``now`` if omitted).
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta

from . import config
from .timeutil import local_date, to_iso

STATES = ("new", "learning", "review", "relearning")


@dataclass(frozen=True)
class CardState:
    state: str = "new"
    learning_step: int | None = None
    ease_factor: float = config.INITIAL_EASE
    interval_days: int = 0
    repetitions: int = 0
    due_date: str | None = None  # local date, review cards
    due_at: str | None = None  # UTC ISO, learning/relearning cards
    last_review: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row) -> "CardState":
        return cls(
            state=row["state"],
            learning_step=row["learning_step"],
            ease_factor=row["ease_factor"],
            interval_days=row["interval_days"],
            repetitions=row["repetitions"],
            due_date=row["due_date"],
            due_at=row["due_at"],
            last_review=row["last_review"],
        )


@dataclass(frozen=True)
class DeckSteps:
    learning_steps: list[int]
    relearning_steps: list[int]


# SM-2 quality for our 0-3 score.
SCORE_TO_QUALITY = {0: 0, 1: 1, 2: 3, 3: 5}


def update_ease(ef: float, q: int) -> float:
    ef2 = ef + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    return max(config.MIN_EASE, round(ef2, 6))


def _steps_transition(
    cs: CardState, score: int, steps: list[int], now: datetime, today: date, step_state: str
) -> CardState:
    """Again/hard/good over a list of minute steps. ``step_state`` is 'learning' or
    'relearning'. Graduation goes to review with interval 1 day."""
    i = cs.learning_step or 0
    if cs.state not in ("learning", "relearning"):
        i = 0

    def at_step(idx: int) -> CardState:
        minutes = steps[idx] if steps else 0
        return replace(
            cs,
            state=step_state,
            learning_step=idx,
            due_at=to_iso(now + timedelta(minutes=minutes)),
            due_date=None,
        )

    def graduate() -> CardState:
        reps = 1 if step_state == "learning" else cs.repetitions
        return replace(
            cs,
            state="review",
            learning_step=None,
            interval_days=1,
            repetitions=reps,
            due_date=(today + timedelta(days=1)).isoformat(),
            due_at=None,
        )

    if not steps:
        return graduate() if score >= 2 else at_step(0)
    i = min(i, len(steps) - 1)
    if score <= 1:
        return at_step(0)
    if score == 2:
        return at_step(i)
    if i + 1 >= len(steps):
        return graduate()
    return at_step(i + 1)


def apply_review(
    cs: CardState,
    score: int,
    deck: DeckSteps,
    now: datetime,
    today: date | None = None,
) -> CardState:
    if score not in SCORE_TO_QUALITY:
        raise ValueError(f"score must be 0-3, got {score}")
    today = today or local_date(now)
    stamped = replace(cs, last_review=to_iso(now))

    if cs.state in ("new", "learning"):
        return _steps_transition(stamped, score, deck.learning_steps, now, today, "learning")
    if cs.state == "relearning":
        return _steps_transition(stamped, score, deck.relearning_steps, now, today, "relearning")
    if cs.state != "review":
        raise ValueError(f"unknown state {cs.state!r}")

    q = SCORE_TO_QUALITY[score]
    ef = update_ease(cs.ease_factor, q)
    if q >= 3:
        reps = cs.repetitions + 1
        if reps == 2:
            interval = 6
        else:
            prev = max(cs.interval_days, 1)
            interval = max(round(prev * ef), prev + 1)
        return replace(
            stamped,
            ease_factor=ef,
            repetitions=reps,
            interval_days=interval,
            due_date=(today + timedelta(days=interval)).isoformat(),
            due_at=None,
            learning_step=None,
        )
    # Lapse.
    lapsed = replace(stamped, ease_factor=ef, repetitions=0)
    if not deck.relearning_steps:
        return replace(
            lapsed,
            state="review",
            learning_step=None,
            interval_days=1,
            due_date=(today + timedelta(days=1)).isoformat(),
            due_at=None,
        )
    return replace(
        lapsed,
        state="relearning",
        learning_step=0,
        due_date=None,
        due_at=to_iso(now + timedelta(minutes=deck.relearning_steps[0])),
    )


# --- Step strings ("1m 10m") ---------------------------------------------------

_STEP_RE = re.compile(r"^(\d+(?:\.\d+)?)([mhd]?)$")
_UNIT_MIN = {"": 1, "m": 1, "h": 60, "d": 1440}


def parse_steps(text: str) -> list[int]:
    """'1m 10m' / '1h' / '1d' -> minutes. Raises ValueError on bad input."""
    out: list[int] = []
    for tok in text.replace(",", " ").split():
        m = _STEP_RE.match(tok.strip().lower())
        if not m:
            raise ValueError(f"invalid step {tok!r}")
        minutes = math.ceil(float(m.group(1)) * _UNIT_MIN[m.group(2)])
        if minutes <= 0:
            raise ValueError(f"step must be positive: {tok!r}")
        out.append(minutes)
    return out


def format_steps(steps: list[int]) -> str:
    parts = []
    for s in steps:
        if s % 1440 == 0:
            parts.append(f"{s // 1440}d")
        elif s % 60 == 0:
            parts.append(f"{s // 60}h")
        else:
            parts.append(f"{s}m")
    return " ".join(parts)


def validate_steps(value) -> list[int]:
    """Accept a list of positive ints or a step string."""
    if isinstance(value, str):
        return parse_steps(value)
    if isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in value):
        return list(value)
    raise ValueError("steps must be a list of positive minutes or a string like '1m 10m'")
