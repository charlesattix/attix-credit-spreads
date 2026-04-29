"""shared/market_calendar.py — Minimal market-day-aware time helpers.

Currently exports just :func:`trading_hours_between`, used by Sentinel
Gate 24 (stale-halt nag) to age halts in trading hours rather than
wall-clock hours.

Intentionally lightweight: NYSE regular session only (Mon-Fri 09:30-16:00
America/New_York). Holidays are NOT modelled here; per the Branch 8
spec for G24, "false positives during holiday weeks are acceptable" and
keeping this self-contained avoids pulling in a heavy market-calendar
dependency.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
_SESSION_OPEN_H, _SESSION_OPEN_M = 9, 30
_SESSION_CLOSE_H, _SESSION_CLOSE_M = 16, 0


def trading_hours_between(start: datetime, end: datetime) -> float:
    """Return the number of NYSE regular-session hours between *start* and *end*.

    Trading session is Mon-Fri 09:30-16:00 America/New_York. Holidays are
    not modelled — this is intentionally a heuristic suitable for stale-halt
    ageing, not for accounting-grade calendars.

    Naive datetimes are interpreted as UTC. Returns 0.0 when ``end <= start``.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    if end <= start:
        return 0.0

    start_ny = start.astimezone(_NY)
    end_ny = end.astimezone(_NY)

    total_seconds = 0.0
    cursor_date = start_ny.date()
    end_date = end_ny.date()
    while cursor_date <= end_date:
        if cursor_date.weekday() < 5:
            session_open = datetime(
                cursor_date.year, cursor_date.month, cursor_date.day,
                _SESSION_OPEN_H, _SESSION_OPEN_M, tzinfo=_NY,
            )
            session_close = datetime(
                cursor_date.year, cursor_date.month, cursor_date.day,
                _SESSION_CLOSE_H, _SESSION_CLOSE_M, tzinfo=_NY,
            )
            day_start = max(start_ny, session_open)
            day_end = min(end_ny, session_close)
            if day_end > day_start:
                total_seconds += (day_end - day_start).total_seconds()
        cursor_date += timedelta(days=1)
    return total_seconds / 3600.0
