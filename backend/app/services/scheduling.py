"""Schedule math + tick evaluator for ScheduledSync rows.

Schedules are simple by design: daily-at-hour-UTC, or weekly-on-day-at-hour.
A more cron-like model can come later; the tick loop is the right shape
either way (poll a small index, enqueue, advance), so adding richer
recurrence is additive.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


def compute_next_run_at(
    *,
    frequency: str,
    hour_utc: int,
    day_of_week: Optional[int],
    now: Optional[datetime] = None,
) -> datetime:
    """Next run after `now`, strictly in the future."""
    now = now or datetime.utcnow()
    base = now.replace(minute=0, second=0, microsecond=0)

    if frequency == "daily":
        candidate = base.replace(hour=hour_utc)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if frequency == "weekly":
        target_dow = int(day_of_week) if day_of_week is not None else 0
        # Monday=0 .. Sunday=6
        days_ahead = (target_dow - now.weekday()) % 7
        candidate = base.replace(hour=hour_utc) + timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    raise ValueError(f"Unsupported frequency: {frequency!r}")


def validate_schedule_inputs(
    *, frequency: str, hour_utc: int, day_of_week: Optional[int]
) -> Optional[str]:
    """Return None on valid input, or a human-readable error message."""
    if frequency not in {"daily", "weekly"}:
        return "frequency must be 'daily' or 'weekly'."
    if not (0 <= int(hour_utc) <= 23):
        return "hour_utc must be in 0..23."
    if frequency == "weekly":
        if day_of_week is None or not (0 <= int(day_of_week) <= 6):
            return "day_of_week must be in 0..6 (Mon=0, Sun=6) for weekly schedules."
    return None
