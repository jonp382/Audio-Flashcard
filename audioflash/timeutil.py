"""Time helpers. Timestamps are ISO 8601 UTC with a ``Z`` suffix; "today" is the
machine's local calendar date (agent.md §7)."""

from __future__ import annotations

from datetime import date, datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """Aware datetime -> ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (UTC)."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_date(dt: datetime) -> date:
    """Local calendar date of an aware datetime."""
    return dt.astimezone().date()


def local_today(now: datetime | None = None) -> date:
    return local_date(now or utc_now())


def local_day_bounds_utc(day: date) -> tuple[str, str]:
    """[start, end) of a local calendar day as UTC ISO strings, for range queries."""
    start = datetime(day.year, day.month, day.day).astimezone()
    end = datetime.fromordinal(day.toordinal() + 1).astimezone()
    return to_iso(start), to_iso(end)
