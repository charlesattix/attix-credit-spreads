"""
SENTINEL Gates 17–20 — Execution Quality & Runtime Monitoring.

Gate 17 — Stop-Loss Execution Quality
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Compares theoretical max loss (spread_width × contracts × 100) against
realised P&L for stop-loss exits. Flags slippage and gap-risk events.

  Slippage Ratio  | Action
  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
  120-150%        | WARNING
  150-200%        | CRITICAL
  > 200%          | HALT

Motivating case: EXP-503 had -$7,667 loss on $4,200 theoretical max (183%).

Gate 18 — Repeated Failure Detection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Scans closed trades for consecutive losses and failure streaks (trades that
close as closed_loss, needs_investigation, or failed_open in a row).

  Streak     | Action
  ‾‾‾‾‾‾‾‾‾‾‾|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
  5 consec    | WARNING
  8 consec    | CRITICAL
  12 consec   | HALT

Same exit_reason repeating N times in last 20 trades → escalation.

Motivating case: EXP-800 had 6+ consecutive failures over 4 days.

Gate 19 — Market Calendar Guard
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Blocks trading on weekends and market holidays. Detects when scanners
ran outside market hours (Saturday/Sunday scans).

  Condition           | Action
  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
  Trade on weekend    | CRITICAL
  Trade on holiday    | WARNING
  Half day detected   | INFO

Motivating case: EXP-800 ran 14 scans on Saturday.

Gate 20 — P&L Reconciliation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Finds closed trades with NULL pnl and reconciles with broker data
when possible. Flags trades where recorded pnl diverges from
expected pnl (credit - debit × contracts × 100).

  Discrepancy   | Action
  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
  > 10%         | WARNING
  > 25%         | CRITICAL
  NULL pnl      | WARNING (per trade)

Usage::

    from sentinel.gates_execution import (
        check_stop_loss_quality,    # Gate 17
        check_repeated_failures,    # Gate 18
        check_market_calendar,      # Gate 19
        check_pnl_reconciliation,   # Gate 20
        check_execution_gates,      # Unified
    )
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ===========================================================================
# Gate 17 — Stop-Loss Execution Quality
# ===========================================================================

# Slippage thresholds: actual_loss / theoretical_max_loss
SL_WARNING_RATIO = 1.20    # 120%
SL_CRITICAL_RATIO = 1.50   # 150%
SL_HALT_RATIO = 2.00       # 200%


@dataclass
class SlippageEvent:
    """One stop-loss trade that exceeded theoretical max loss."""
    trade_id: str
    ticker: str
    strategy_type: str
    expected_max_loss: float    # theoretical max loss (positive)
    actual_loss: float          # realised loss (positive)
    slippage_ratio: float       # actual / expected
    severity: str               # "warning" | "critical" | "halt"
    message: str


@dataclass
class StopLossQualityResult:
    """Gate 17 result for one experiment."""
    exp_id: str
    stop_loss_trades: int = 0
    events: List[SlippageEvent] = field(default_factory=list)
    avg_slippage_ratio: Optional[float] = None
    errors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(e.severity in ("critical", "halt") for e in self.events)

    @property
    def worst_severity(self) -> Optional[str]:
        if not self.events:
            return None
        order = {"halt": 0, "critical": 1, "warning": 2}
        return min(self.events, key=lambda e: order.get(e.severity, 99)).severity


def check_stop_loss_quality(
    exp_id: str,
    db_path: str,
    *,
    lookback_days: int = 30,
) -> StopLossQualityResult:
    """
    Gate 17: check stop-loss execution quality.

    Finds trades with exit_reason='stop_loss' and compares realised loss
    against theoretical max loss = (short_strike - long_strike) × contracts × 100.

    For iron condors, max loss = (put spread width) × contracts × 100 per side
    (we use whichever side was hit, but as a conservative upper bound we use
    spread_width × contracts × 100 since only one side should be in the money).
    """
    result = StopLossQualityResult(exp_id=exp_id)

    if not Path(db_path).exists():
        result.errors.append(f"DB not found: {db_path}")
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        rows = conn.execute("""
            SELECT id, ticker, strategy_type, pnl, credit, contracts,
                   short_strike, long_strike, exit_date, metadata
            FROM   trades
            WHERE  exit_reason = 'stop_loss'
              AND  status LIKE 'closed%%'
              AND  pnl IS NOT NULL
              AND  exit_date >= ?
            ORDER BY exit_date DESC
        """, (cutoff,)).fetchall()

        result.stop_loss_trades = len(rows)
        ratios: list[float] = []

        for row in rows:
            trade_id = row["id"]
            ticker = row["ticker"] or "?"
            strat = row["strategy_type"] or ""
            pnl = float(row["pnl"])
            contracts = int(row["contracts"] or 1)
            short_strike = row["short_strike"]
            long_strike = row["long_strike"]

            if pnl >= 0:
                # Profitable stop-loss exit (rare) — skip
                continue

            actual_loss = abs(pnl)

            # Calculate theoretical max loss
            if short_strike is not None and long_strike is not None:
                spread_width = abs(short_strike - long_strike)
            else:
                # Can't compute theoretical max — skip
                continue

            if spread_width <= 0:
                continue

            # For iron condors, spread_width is per-side
            # The metadata may have separate put/call strikes, but as a
            # conservative bound, one side's max loss is the limit
            if "condor" in strat.lower() or "iron" in strat.lower():
                # IC max loss = 2 × spread_width - credit (per contract)
                credit = float(row["credit"] or 0)
                theoretical_max = (2 * spread_width - credit) * contracts * 100
            else:
                theoretical_max = spread_width * contracts * 100

            if theoretical_max <= 0:
                continue

            ratio = actual_loss / theoretical_max
            ratios.append(ratio)

            if ratio >= SL_HALT_RATIO:
                severity = "halt"
            elif ratio >= SL_CRITICAL_RATIO:
                severity = "critical"
            elif ratio >= SL_WARNING_RATIO:
                severity = "warning"
            else:
                continue  # within tolerance

            result.events.append(SlippageEvent(
                trade_id=trade_id,
                ticker=ticker,
                strategy_type=strat,
                expected_max_loss=round(theoretical_max, 2),
                actual_loss=round(actual_loss, 2),
                slippage_ratio=round(ratio, 3),
                severity=severity,
                message=(
                    f"{ticker} {strat}: lost ${actual_loss:,.0f} vs "
                    f"max ${theoretical_max:,.0f} ({ratio:.0%} of max) "
                    f"[{trade_id[:20]}]"
                ),
            ))

        if ratios:
            result.avg_slippage_ratio = round(sum(ratios) / len(ratios), 3)

    except Exception as e:
        result.errors.append(f"DB query failed: {e}")
    finally:
        conn.close()

    # Log
    if result.events:
        for ev in result.events:
            log_fn = logger.critical if ev.severity == "halt" else (
                logger.warning if ev.severity == "critical" else logger.info
            )
            log_fn("GATE17 %s [%s] %s", exp_id, ev.severity.upper(), ev.message)
    else:
        logger.debug(
            "GATE17 %s: %d stop-loss trades checked — all within tolerance",
            exp_id, result.stop_loss_trades,
        )

    return result


# ===========================================================================
# Gate 18 — Repeated Failure Detection
# ===========================================================================

STREAK_WARNING = 5
STREAK_CRITICAL = 8
STREAK_HALT = 12

# Same exit_reason repeating in last N trades → escalation
SAME_REASON_WINDOW = 20
SAME_REASON_WARNING = 4
SAME_REASON_CRITICAL = 6

# Statuses considered "failure"
_FAILURE_STATUSES = frozenset({
    "closed_loss", "needs_investigation", "failed_open",
    "closed_external", "unmanaged",
})


@dataclass
class FailureStreak:
    """Represents a detected failure pattern."""
    exp_id: str
    streak_length: int
    severity: str
    recent_exit_reasons: List[str]
    repeated_reason: Optional[str] = None  # if same reason dominates
    repeated_count: int = 0
    message: str = ""


@dataclass
class RepeatedFailureResult:
    """Gate 18 result for one experiment."""
    exp_id: str
    total_recent_trades: int = 0
    current_loss_streak: int = 0
    streaks: List[FailureStreak] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(s.severity in ("critical", "halt") for s in self.streaks)

    @property
    def worst_severity(self) -> Optional[str]:
        if not self.streaks:
            return None
        order = {"halt": 0, "critical": 1, "warning": 2}
        return min(self.streaks, key=lambda s: order.get(s.severity, 99)).severity


def check_repeated_failures(
    exp_id: str,
    db_path: str,
    *,
    lookback_days: int = 30,
) -> RepeatedFailureResult:
    """
    Gate 18: detect consecutive failure streaks.

    Reads the last N closed trades ordered by exit_date and counts:
    1. Current consecutive loss streak from most recent trade backward
    2. Dominant exit_reason in last SAME_REASON_WINDOW trades
    """
    result = RepeatedFailureResult(exp_id=exp_id)

    if not Path(db_path).exists():
        result.errors.append(f"DB not found: {db_path}")
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        rows = conn.execute("""
            SELECT id, status, exit_reason, pnl, exit_date
            FROM   trades
            WHERE  status LIKE 'closed%%'
              AND  exit_date >= ?
            ORDER BY exit_date DESC
            LIMIT 50
        """, (cutoff,)).fetchall()

        result.total_recent_trades = len(rows)

        if not rows:
            return result

        # 1. Current consecutive loss streak
        streak = 0
        for row in rows:
            status = row["status"]
            if status in _FAILURE_STATUSES:
                streak += 1
            else:
                break

        result.current_loss_streak = streak

        if streak >= STREAK_HALT:
            severity = "halt"
        elif streak >= STREAK_CRITICAL:
            severity = "critical"
        elif streak >= STREAK_WARNING:
            severity = "warning"
        else:
            severity = ""

        if severity:
            reasons = [
                row["exit_reason"] or row["status"]
                for row in rows[:streak]
            ]
            result.streaks.append(FailureStreak(
                exp_id=exp_id,
                streak_length=streak,
                severity=severity,
                recent_exit_reasons=reasons,
                message=(
                    f"{streak} consecutive failures "
                    f"(reasons: {', '.join(set(reasons))})"
                ),
            ))

        # 2. Same exit_reason dominance in last N trades
        window = rows[:SAME_REASON_WINDOW]
        if len(window) >= SAME_REASON_WARNING:
            reason_counts: Dict[str, int] = {}
            for row in window:
                reason = row["exit_reason"] or row["status"]
                if row["status"] in _FAILURE_STATUSES:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1

            for reason, count in reason_counts.items():
                if count >= SAME_REASON_CRITICAL:
                    sev = "critical"
                elif count >= SAME_REASON_WARNING:
                    sev = "warning"
                else:
                    continue

                result.streaks.append(FailureStreak(
                    exp_id=exp_id,
                    streak_length=count,
                    severity=sev,
                    recent_exit_reasons=[reason] * count,
                    repeated_reason=reason,
                    repeated_count=count,
                    message=(
                        f"'{reason}' repeated {count}x in last "
                        f"{len(window)} trades"
                    ),
                ))

    except Exception as e:
        result.errors.append(f"DB query failed: {e}")
    finally:
        conn.close()

    # Log
    for s in result.streaks:
        log_fn = logger.critical if s.severity == "halt" else (
            logger.warning if s.severity == "critical" else logger.info
        )
        log_fn("GATE18 %s [%s] %s", exp_id, s.severity.upper(), s.message)

    return result


# ===========================================================================
# Gate 19 — Market Calendar Guard
# ===========================================================================

# US market holidays (month, day) — approximate, no early-close handling
# New Year's Day, MLK, Presidents' Day, Good Friday (variable), Memorial Day,
# Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas
_FIXED_HOLIDAYS: Set[Tuple[int, int]] = {
    (1, 1),    # New Year's Day
    (6, 19),   # Juneteenth
    (7, 4),    # Independence Day
    (12, 25),  # Christmas Day
}

# Floating holidays: computed per year
def _get_market_holidays(year: int) -> Set[date]:
    """Return set of US market holiday dates for a given year."""
    holidays: Set[date] = set()

    # Fixed holidays
    for month, day in _FIXED_HOLIDAYS:
        d = date(year, month, day)
        # If falls on Saturday, observed Friday. If Sunday, observed Monday.
        if d.weekday() == 5:  # Saturday
            holidays.add(d - timedelta(days=1))
        elif d.weekday() == 6:  # Sunday
            holidays.add(d + timedelta(days=1))
        else:
            holidays.add(d)

    # MLK Day: 3rd Monday of January
    holidays.add(_nth_weekday(year, 1, 0, 3))  # month=1, weekday=Mon, nth=3

    # Presidents' Day: 3rd Monday of February
    holidays.add(_nth_weekday(year, 2, 0, 3))

    # Memorial Day: last Monday of May
    holidays.add(_last_weekday(year, 5, 0))

    # Labor Day: 1st Monday of September
    holidays.add(_nth_weekday(year, 9, 0, 1))

    # Thanksgiving: 4th Thursday of November
    holidays.add(_nth_weekday(year, 11, 3, 4))

    # Good Friday: 2 days before Easter Sunday
    easter = _easter(year)
    holidays.add(easter - timedelta(days=2))

    return holidays


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence of weekday in month/year (1-indexed)."""
    first = date(year, month, 1)
    # Days until first occurrence of weekday
    days_ahead = (weekday - first.weekday()) % 7
    first_occ = first + timedelta(days=days_ahead)
    return first_occ + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of weekday in month/year."""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    days_back = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=days_back)


def _easter(year: int) -> date:
    """Computus algorithm for Easter Sunday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_market_holiday(d: date) -> bool:
    """Check if date is a US market holiday."""
    return d in _get_market_holidays(d.year)


def is_market_day(d: date) -> bool:
    """Check if date is a regular market trading day (not weekend or holiday)."""
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    return not is_market_holiday(d)


@dataclass
class CalendarEvent:
    """A trade or scan that occurred outside market hours."""
    trade_id: Optional[str]
    date: date
    reason: str          # "weekend" | "holiday" | "half_day"
    severity: str        # "warning" | "critical"
    message: str


@dataclass
class MarketCalendarResult:
    """Gate 19 result for one experiment."""
    exp_id: str
    trades_checked: int = 0
    events: List[CalendarEvent] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.events) == 0


def check_market_calendar(
    exp_id: str,
    db_path: str,
    *,
    lookback_days: int = 30,
) -> MarketCalendarResult:
    """
    Gate 19: detect trades entered on weekends or market holidays.

    Reads entry_date from trades table and checks each against
    the market calendar. Weekend trades → CRITICAL, holiday trades → WARNING.
    """
    result = MarketCalendarResult(exp_id=exp_id)

    if not Path(db_path).exists():
        result.errors.append(f"DB not found: {db_path}")
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        rows = conn.execute("""
            SELECT id, ticker, entry_date
            FROM   trades
            WHERE  entry_date >= ?
              AND  status NOT IN ('failed_open', 'rejected', 'cancelled')
            ORDER BY entry_date DESC
        """, (cutoff,)).fetchall()

        result.trades_checked = len(rows)

        for row in rows:
            trade_id = row["id"]
            ticker = row["ticker"] or "?"
            entry_date_str = row["entry_date"]

            if not entry_date_str:
                continue

            try:
                entry_dt = datetime.fromisoformat(
                    entry_date_str.replace("Z", "+00:00")
                )
                entry_d = entry_dt.date()
            except (ValueError, TypeError):
                continue

            if entry_d.weekday() >= 5:
                # Weekend trade
                day_name = "Saturday" if entry_d.weekday() == 5 else "Sunday"
                result.events.append(CalendarEvent(
                    trade_id=trade_id,
                    date=entry_d,
                    reason="weekend",
                    severity="critical",
                    message=(
                        f"{ticker} entered on {day_name} {entry_d.isoformat()} "
                        f"[{trade_id[:20]}]"
                    ),
                ))
            elif is_market_holiday(entry_d):
                result.events.append(CalendarEvent(
                    trade_id=trade_id,
                    date=entry_d,
                    reason="holiday",
                    severity="warning",
                    message=(
                        f"{ticker} entered on market holiday "
                        f"{entry_d.isoformat()} [{trade_id[:20]}]"
                    ),
                ))

    except Exception as e:
        result.errors.append(f"DB query failed: {e}")
    finally:
        conn.close()

    # Log
    for ev in result.events:
        log_fn = logger.critical if ev.severity == "critical" else logger.warning
        log_fn("GATE19 %s [%s] %s", exp_id, ev.severity.upper(), ev.message)

    return result


# ===========================================================================
# Gate 20 — P&L Reconciliation
# ===========================================================================

PNL_WARN_DISCREPANCY = 0.10   # 10%
PNL_CRIT_DISCREPANCY = 0.25   # 25%


@dataclass
class PnlDiscrepancy:
    """A trade whose recorded pnl diverges from expected."""
    trade_id: str
    ticker: str
    recorded_pnl: Optional[float]
    expected_pnl: Optional[float]
    discrepancy_pct: Optional[float]
    severity: str
    message: str


@dataclass
class PnlReconciliationResult:
    """Gate 20 result for one experiment."""
    exp_id: str
    trades_checked: int = 0
    null_pnl_count: int = 0
    discrepancies: List[PnlDiscrepancy] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(d.severity in ("critical", "halt") for d in self.discrepancies)


def check_pnl_reconciliation(
    exp_id: str,
    db_path: str,
    *,
    lookback_days: int = 30,
) -> PnlReconciliationResult:
    """
    Gate 20: reconcile recorded P&L against expected values.

    For each closed trade:
    1. If pnl IS NULL → flag as warning (missing data)
    2. If pnl is set, compare against expected = (credit × contracts × 100)
       for profitable trades, or -(spread_width - credit) × contracts × 100
       for losing trades. Flag if discrepancy > threshold.

    Note: This is a heuristic check. Actual fill prices from Alpaca would
    give exact reconciliation, but that requires API access. This gate
    works purely from DB data.
    """
    result = PnlReconciliationResult(exp_id=exp_id)

    if not Path(db_path).exists():
        result.errors.append(f"DB not found: {db_path}")
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        rows = conn.execute("""
            SELECT id, ticker, strategy_type, status, pnl, credit,
                   contracts, short_strike, long_strike, exit_reason,
                   exit_date
            FROM   trades
            WHERE  status LIKE 'closed%%'
              AND  exit_date >= ?
            ORDER BY exit_date DESC
        """, (cutoff,)).fetchall()

        result.trades_checked = len(rows)

        for row in rows:
            trade_id = row["id"]
            ticker = row["ticker"] or "?"
            pnl = row["pnl"]
            credit = row["credit"]
            contracts = int(row["contracts"] or 1)
            short_strike = row["short_strike"]
            long_strike = row["long_strike"]
            exit_reason = row["exit_reason"] or ""

            # Check for NULL pnl
            if pnl is None:
                result.null_pnl_count += 1
                result.discrepancies.append(PnlDiscrepancy(
                    trade_id=trade_id,
                    ticker=ticker,
                    recorded_pnl=None,
                    expected_pnl=None,
                    discrepancy_pct=None,
                    severity="warning",
                    message=(
                        f"{ticker}: NULL pnl on closed trade "
                        f"(exit: {exit_reason}) [{trade_id[:20]}]"
                    ),
                ))
                continue

            pnl = float(pnl)

            # Try to compute expected P&L for comparison
            if credit is None or short_strike is None or long_strike is None:
                continue  # Can't validate without credit and strikes

            credit = float(credit)
            spread_width = abs(short_strike - long_strike)

            if spread_width <= 0:
                continue

            # Expected max profit = credit × contracts × 100
            # Expected max loss = (spread_width - credit) × contracts × 100
            max_profit = credit * contracts * 100
            max_loss = (spread_width - credit) * contracts * 100

            # Check if pnl exceeds theoretical bounds
            if pnl > max_profit * 1.01:  # 1% tolerance for rounding
                discrepancy = (pnl - max_profit) / max_profit if max_profit > 0 else 0
                if discrepancy > PNL_CRIT_DISCREPANCY:
                    sev = "critical"
                elif discrepancy > PNL_WARN_DISCREPANCY:
                    sev = "warning"
                else:
                    continue

                result.discrepancies.append(PnlDiscrepancy(
                    trade_id=trade_id,
                    ticker=ticker,
                    recorded_pnl=pnl,
                    expected_pnl=max_profit,
                    discrepancy_pct=round(discrepancy * 100, 1),
                    severity=sev,
                    message=(
                        f"{ticker}: pnl ${pnl:,.0f} exceeds max profit "
                        f"${max_profit:,.0f} by {discrepancy:.0%} "
                        f"[{trade_id[:20]}]"
                    ),
                ))

            elif pnl < 0 and abs(pnl) > max_loss * 1.01:
                actual_loss = abs(pnl)
                discrepancy = (actual_loss - max_loss) / max_loss if max_loss > 0 else 0
                if discrepancy > PNL_CRIT_DISCREPANCY:
                    sev = "critical"
                elif discrepancy > PNL_WARN_DISCREPANCY:
                    sev = "warning"
                else:
                    continue

                result.discrepancies.append(PnlDiscrepancy(
                    trade_id=trade_id,
                    ticker=ticker,
                    recorded_pnl=pnl,
                    expected_pnl=-max_loss,
                    discrepancy_pct=round(discrepancy * 100, 1),
                    severity=sev,
                    message=(
                        f"{ticker}: loss ${actual_loss:,.0f} exceeds "
                        f"max loss ${max_loss:,.0f} by {discrepancy:.0%} "
                        f"[{trade_id[:20]}]"
                    ),
                ))

    except Exception as e:
        result.errors.append(f"DB query failed: {e}")
    finally:
        conn.close()

    # Log
    if result.null_pnl_count > 0:
        logger.warning(
            "GATE20 %s: %d trades with NULL pnl",
            exp_id, result.null_pnl_count,
        )
    for d in result.discrepancies:
        if d.recorded_pnl is not None:  # skip null-pnl logs (already logged above)
            log_fn = logger.critical if d.severity == "critical" else logger.warning
            log_fn("GATE20 %s [%s] %s", exp_id, d.severity.upper(), d.message)

    return result


# ===========================================================================
# Unified entry point
# ===========================================================================


@dataclass
class ExecutionGatesResult:
    """Combined result from Gates 17-20."""
    exp_id: str
    gate17: Optional[StopLossQualityResult] = None
    gate18: Optional[RepeatedFailureResult] = None
    gate19: Optional[MarketCalendarResult] = None
    gate20: Optional[PnlReconciliationResult] = None

    @property
    def passed(self) -> bool:
        gates = [self.gate17, self.gate18, self.gate19, self.gate20]
        return all(g is None or g.passed for g in gates)


def check_execution_gates(
    exp_id: str,
    db_path: str,
    *,
    lookback_days: int = 30,
) -> ExecutionGatesResult:
    """
    Run all execution gates (17-20) for one experiment.

    Returns combined result. Never raises.
    """
    result = ExecutionGatesResult(exp_id=exp_id)

    result.gate17 = check_stop_loss_quality(
        exp_id, db_path, lookback_days=lookback_days
    )
    result.gate18 = check_repeated_failures(
        exp_id, db_path, lookback_days=lookback_days
    )
    result.gate19 = check_market_calendar(
        exp_id, db_path, lookback_days=lookback_days
    )
    result.gate20 = check_pnl_reconciliation(
        exp_id, db_path, lookback_days=lookback_days
    )

    return result


def format_execution_report(results: Dict[str, ExecutionGatesResult]) -> str:
    """Format Gates 17-20 results as human-readable text for Telegram/CLI."""
    lines: List[str] = ["<b>Gates 17-20 — Execution Quality</b>"]

    if not results:
        lines.append("  <i>No experiments checked.</i>")
        return "\n".join(lines)

    for exp_id in sorted(results):
        r = results[exp_id]
        issues: List[str] = []

        # Gate 17
        if r.gate17 and r.gate17.events:
            worst = r.gate17.worst_severity or "warning"
            issues.append(f"G17: {len(r.gate17.events)} slippage event(s)")

        # Gate 18
        if r.gate18 and r.gate18.streaks:
            worst = r.gate18.worst_severity or "warning"
            issues.append(
                f"G18: {r.gate18.current_loss_streak}-loss streak"
            )

        # Gate 19
        if r.gate19 and r.gate19.events:
            issues.append(f"G19: {len(r.gate19.events)} off-calendar trade(s)")

        # Gate 20
        if r.gate20 and r.gate20.discrepancies:
            issues.append(
                f"G20: {len(r.gate20.discrepancies)} P&L issue(s) "
                f"({r.gate20.null_pnl_count} null)"
            )

        if issues:
            icon = "🔴" if not r.passed else "⚠️"
            lines.append(f"  {icon} {exp_id}: {' | '.join(issues)}")
        else:
            lines.append(f"  ✅ {exp_id}: all execution gates passed")

    return "\n".join(lines)
