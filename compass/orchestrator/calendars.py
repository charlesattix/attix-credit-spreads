"""compass/orchestrator/calendars.py — Macro-event + market calendar helpers.

Loads FOMC / NFP / CPI release dates from per-year CSV files in the
``compass/orchestrator/calendars/`` data directory and exposes the simple
day-relative predicates the entry_gate needs:

    is_fomc_today(d)         is_fomc_day_plus_one(d)
    is_nfp_today(d)          is_nfp_tomorrow(d)
    is_cpi_today(d)
    is_opex_week(d)          is_third_friday(d)
    is_market_open(d)

OPEX is derived (3rd Friday of each month, US listed options convention) —
no CSV is needed.

Market-open status uses pandas_market_calendars NYSE if available; falls
back to a simple weekday + US-federal-holiday heuristic otherwise.

Module design
-------------
- CSVs are loaded lazily and cached on first access.
- Each loader returns a ``frozenset[date]`` of release dates.
- Fail-closed: if a CSV is empty, missing, or its newest entry is more
  than ``MAX_DATA_AGE_DAYS`` older than today the loader raises
  ``CalendarStaleError`` — the orchestrator must treat that as fatal
  rather than silently trade through unknown event days.
- Coexistence with the ``calendars/`` data directory works via PEP 420:
  a regular module (this file) takes precedence over a namespace package
  of the same name when both are present in the parent package.

See ORCHESTRATOR_PROPOSAL.md §4.1 (gates 9-12) for how these helpers are
consumed.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, Iterable, Optional

LOG = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────────────────────

# Directory containing the per-year CSV files.
DATA_DIR: Path = Path(__file__).resolve().parent / "calendars"

# A CSV is "stale" if its newest row is more than this many days behind
# today. The orchestrator must refuse to trade if any feed is stale —
# otherwise we'd silently trade through unknown FOMC/NFP/CPI days.
MAX_DATA_AGE_DAYS: int = 100

# Supported event kinds (used as filename prefix: ``<kind>_<YYYY>.csv``).
SUPPORTED_KINDS: frozenset = frozenset({"fomc", "nfp", "cpi"})


class CalendarStaleError(RuntimeError):
    """Raised when a calendar CSV is empty, missing, or older than
    ``MAX_DATA_AGE_DAYS``. Fail-closed — never swallow this."""


class CalendarMissingError(RuntimeError):
    """Raised when a calendar CSV cannot be located for a requested year."""


# ───────────────────────────────────────────────────────────────────────────
# Internal loaders
# ───────────────────────────────────────────────────────────────────────────

def _coerce_date(d) -> date:
    """Accept either a ``date`` instance or an ISO-8601 ``YYYY-MM-DD`` string."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return datetime.strptime(d, "%Y-%m-%d").date()
    raise TypeError(f"expected date, datetime, or YYYY-MM-DD string; got {type(d).__name__}")


def _parse_csv(path: Path) -> FrozenSet[date]:
    """Parse a single CSV file → frozenset of ``date`` objects.

    Skips blank lines, lines beginning with ``#``, and the header row.
    The header row is detected by the literal first column ``date``.
    """
    if not path.is_file():
        raise CalendarMissingError(f"calendar file not found: {path}")

    dates: set = set()
    with path.open(newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row:
                continue
            first = row[0].strip()
            if not first or first.startswith("#"):
                continue
            if first.lower() == "date":
                continue
            try:
                dates.add(datetime.strptime(first, "%Y-%m-%d").date())
            except ValueError:
                LOG.warning("calendars: skipping unparseable row in %s: %r", path.name, row)
    return frozenset(dates)


@lru_cache(maxsize=None)
def _load_event_dates(kind: str, year: int) -> FrozenSet[date]:
    """Load the per-year date set for ``kind`` (fomc/nfp/cpi).

    Cached for the lifetime of the process. Tests that need a fresh load
    can call :func:`clear_cache`.
    """
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"unsupported calendar kind {kind!r}; expected one of {sorted(SUPPORTED_KINDS)}")

    path = DATA_DIR / f"{kind}_{year}.csv"
    dates = _parse_csv(path)
    if not dates:
        raise CalendarStaleError(f"{path.name} contained zero dates — refusing to trade through it")
    return dates


def _check_freshness(kind: str, year: int, today: date) -> None:
    """Raise ``CalendarStaleError`` if the latest date for ``kind`` is older
    than ``MAX_DATA_AGE_DAYS`` relative to ``today``.

    Note: a future-dated calendar (e.g. the 2027 file loaded in 2026) is
    not "stale" — its latest entry is in the future, so the age is
    negative. We only fail when the data clearly lags reality.
    """
    dates = _load_event_dates(kind, year)
    newest = max(dates)
    age = (today - newest).days
    if age > MAX_DATA_AGE_DAYS:
        raise CalendarStaleError(
            f"{kind}_{year}.csv newest entry {newest.isoformat()} is "
            f"{age} days old (> {MAX_DATA_AGE_DAYS}) — refusing to trade "
            f"with stale macro calendar"
        )


def _event_dates_for(kind: str, today: date) -> FrozenSet[date]:
    """Return all event dates for the year of ``today`` (with freshness check).

    For predicates like ``is_fomc_day_plus_one`` we also include the
    previous year's dates, so a January-1 lookback still resolves the
    December FOMC.
    """
    cur = _load_event_dates(kind, today.year)
    _check_freshness(kind, today.year, today)
    # Pull in last year's file so 'X days ago' predicates work across
    # year boundaries. If the file isn't present that's OK — we silently
    # skip it (older years legitimately fall off the disk).
    try:
        prev = _load_event_dates(kind, today.year - 1)
    except (CalendarMissingError, CalendarStaleError):
        prev = frozenset()
    return cur | prev


def clear_cache() -> None:
    """Invalidate the LRU caches. Intended for tests."""
    _load_event_dates.cache_clear()


# ───────────────────────────────────────────────────────────────────────────
# Public predicates — day-relative
# ───────────────────────────────────────────────────────────────────────────

def is_fomc_today(d) -> bool:
    """True if ``d`` is a published FOMC statement / press-conference day."""
    d = _coerce_date(d)
    return d in _event_dates_for("fomc", d)


def is_fomc_day_plus_one(d) -> bool:
    """True if ``d`` is the calendar day immediately AFTER a published
    FOMC statement day. Used by sleeves that wait one day post-FOMC
    before re-entering."""
    d = _coerce_date(d)
    return (d - timedelta(days=1)) in _event_dates_for("fomc", d)


def is_nfp_today(d) -> bool:
    """True if ``d`` is a BLS NFP release date."""
    d = _coerce_date(d)
    return d in _event_dates_for("nfp", d)


def is_nfp_tomorrow(d) -> bool:
    """True if the next calendar day is a BLS NFP release date — i.e. the
    pre-NFP T-1 blackout. Used by entry_gate gate 10 (nfp_blackout) to
    refuse same-week entries the afternoon before NFP."""
    d = _coerce_date(d)
    return (d + timedelta(days=1)) in _event_dates_for("nfp", d)


def is_cpi_today(d) -> bool:
    """True if ``d`` is a BLS CPI release date."""
    d = _coerce_date(d)
    return d in _event_dates_for("cpi", d)


def is_third_friday(d) -> bool:
    """True if ``d`` is the 3rd Friday of its month — standard US listed
    options monthly expiration day."""
    d = _coerce_date(d)
    if d.weekday() != 4:                       # 4 = Friday
        return False
    # Day-of-month range for 3rd Friday is always 15..21.
    return 15 <= d.day <= 21


def is_opex_week(d) -> bool:
    """True if ``d`` falls in the same Mon-Fri trading week as monthly
    OPEX (3rd Friday)."""
    d = _coerce_date(d)
    # Walk to Friday of the current week (Mon=0..Fri=4).
    friday_offset = 4 - d.weekday()
    friday_of_week = d + timedelta(days=friday_offset)
    return is_third_friday(friday_of_week)


# ───────────────────────────────────────────────────────────────────────────
# Market-open helper
# ───────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _nyse_calendar():
    """Return an instantiated pandas_market_calendars NYSE calendar, or
    None if the package is unavailable. Cached for the process lifetime."""
    try:
        import pandas_market_calendars as mcal  # type: ignore
    except ImportError:
        LOG.info("pandas_market_calendars not available; using weekday + "
                 "federal-holiday fallback for is_market_open")
        return None
    return mcal.get_calendar("NYSE")


# US federal market holidays observed by NYSE — used by the fallback
# path when pandas_market_calendars isn't installed. Hand-maintained
# small set — covers the relevant 2026 dates so unit tests can exercise
# is_market_open without the optional dep.
_FALLBACK_HOLIDAYS: frozenset = frozenset({
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
})


def is_market_open(d) -> bool:
    """True if the US equity market is open on ``d`` (date-level only;
    no intraday session check).

    Uses pandas_market_calendars NYSE if available; otherwise a static
    weekday + holiday fallback. Both implementations are date-only —
    intraday halts and early closes are out of scope here.
    """
    d = _coerce_date(d)
    cal = _nyse_calendar()
    if cal is not None:
        schedule = cal.schedule(start_date=d, end_date=d)
        return not schedule.empty
    # Fallback: weekday + small holiday set.
    if d.weekday() >= 5:           # Sat=5, Sun=6
        return False
    return d not in _FALLBACK_HOLIDAYS


# ───────────────────────────────────────────────────────────────────────────
# Bulk accessors — convenient for tests / diagnostics
# ───────────────────────────────────────────────────────────────────────────

def fomc_dates(year: int) -> FrozenSet[date]:
    """All published FOMC statement days for the given year."""
    return _load_event_dates("fomc", year)


def nfp_dates(year: int) -> FrozenSet[date]:
    """All BLS NFP release dates for the given year."""
    return _load_event_dates("nfp", year)


def cpi_dates(year: int) -> FrozenSet[date]:
    """All BLS CPI release dates for the given year."""
    return _load_event_dates("cpi", year)


__all__ = [
    "DATA_DIR",
    "MAX_DATA_AGE_DAYS",
    "CalendarStaleError",
    "CalendarMissingError",
    "is_fomc_today",
    "is_fomc_day_plus_one",
    "is_nfp_today",
    "is_nfp_tomorrow",
    "is_cpi_today",
    "is_third_friday",
    "is_opex_week",
    "is_market_open",
    "fomc_dates",
    "nfp_dates",
    "cpi_dates",
    "clear_cache",
]
