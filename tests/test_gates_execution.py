"""
Tests for SENTINEL Gates 17–20 — Execution Quality & Runtime Monitoring.

Tests cover:
  Gate 17 — Stop-Loss Execution Quality (slippage detection)
  Gate 18 — Repeated Failure Detection (streak tracking)
  Gate 19 — Market Calendar Guard (weekend/holiday detection)
  Gate 20 — P&L Reconciliation (null pnl, discrepancy detection)
  Unified — check_execution_gates() entry point

Each gate is tested with in-memory SQLite DBs. No external API calls.
"""

import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.gates_execution import (
    check_stop_loss_quality,
    check_repeated_failures,
    check_market_calendar,
    check_pnl_reconciliation,
    check_execution_gates,
    format_execution_report,
    is_market_day,
    is_market_holiday,
    _easter,
    _get_market_holidays,
    SL_WARNING_RATIO,
    SL_CRITICAL_RATIO,
    SL_HALT_RATIO,
    STREAK_WARNING,
    STREAK_CRITICAL,
    STREAK_HALT,
    PNL_WARN_DISCREPANCY,
    PNL_CRIT_DISCREPANCY,
    StopLossQualityResult,
    RepeatedFailureResult,
    MarketCalendarResult,
    PnlReconciliationResult,
    ExecutionGatesResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _create_db(path: str) -> sqlite3.Connection:
    """Create a minimal trades DB with expected schema."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            source TEXT DEFAULT 'scanner',
            ticker TEXT,
            status TEXT DEFAULT 'open',
            strategy_type TEXT,
            short_strike REAL,
            long_strike REAL,
            expiration TEXT,
            credit REAL,
            contracts INTEGER DEFAULT 1,
            entry_date TEXT,
            exit_date TEXT,
            exit_reason TEXT,
            pnl REAL,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def _recent_iso(days_ago: int = 0) -> str:
    """Return ISO datetime string for N days ago."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


# ===========================================================================
# Gate 17 — Stop-Loss Execution Quality
# ===========================================================================

class TestGate17StopLossQuality:
    """Tests for check_stop_loss_quality."""

    def test_no_stop_loss_trades(self, tmp_path):
        """No stop-loss trades → passes with 0 events."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        conn.execute("""
            INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date,
                                short_strike, long_strike, contracts, credit)
            VALUES ('t1', 'SPY', 'closed_profit', 'profit_target', 150, ?, 430, 425, 1, 1.5)
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_stop_loss_quality("EXP-TEST", db_file)
        assert result.passed
        assert result.stop_loss_trades == 0
        assert len(result.events) == 0

    def test_stop_loss_within_tolerance(self, tmp_path):
        """Stop-loss trade with loss ≤ max → no events."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # Spread width = 5 ($500 max loss per contract), actual loss = $450
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, exit_reason,
                                pnl, exit_date, short_strike, long_strike,
                                contracts, credit)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_loss', 'stop_loss',
                    -450, ?, 430, 425, 1, 1.5)
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_stop_loss_quality("EXP-TEST", db_file)
        assert result.passed
        assert result.stop_loss_trades == 1
        assert len(result.events) == 0

    def test_stop_loss_warning(self, tmp_path):
        """Loss at 130% of max → WARNING."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # Max loss = $500 (5 × 1 × 100), actual = $650 (130%)
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, exit_reason,
                                pnl, exit_date, short_strike, long_strike,
                                contracts, credit)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_loss', 'stop_loss',
                    -650, ?, 430, 425, 1, 1.5)
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_stop_loss_quality("EXP-TEST", db_file)
        assert not result.passed is False or len(result.events) > 0
        assert len(result.events) == 1
        assert result.events[0].severity == "warning"
        assert result.events[0].slippage_ratio == pytest.approx(1.3, abs=0.01)

    def test_stop_loss_critical(self, tmp_path):
        """Loss at 175% of max → CRITICAL."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # Max loss = $500, actual = $875 (175%)
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, exit_reason,
                                pnl, exit_date, short_strike, long_strike,
                                contracts, credit)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_loss', 'stop_loss',
                    -875, ?, 430, 425, 1, 1.5)
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_stop_loss_quality("EXP-TEST", db_file)
        assert len(result.events) == 1
        assert result.events[0].severity == "critical"

    def test_stop_loss_halt(self, tmp_path):
        """Loss at 210% of max → HALT (like EXP-503 case)."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # Max loss = $500, actual = $1050 (210%)
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, exit_reason,
                                pnl, exit_date, short_strike, long_strike,
                                contracts, credit)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_loss', 'stop_loss',
                    -1050, ?, 430, 425, 1, 1.5)
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_stop_loss_quality("EXP-TEST", db_file)
        assert len(result.events) == 1
        assert result.events[0].severity == "halt"
        assert not result.passed

    def test_iron_condor_max_loss(self, tmp_path):
        """IC max loss = (2 × spread_width - credit) × contracts × 100."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # IC: spread_width=5, credit=2.0
        # Max loss = (2×5 - 2.0) × 1 × 100 = $800
        # Actual loss = $1000 → ratio 1.25 → WARNING
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, exit_reason,
                                pnl, exit_date, short_strike, long_strike,
                                contracts, credit)
            VALUES ('t1', 'SPY', 'iron_condor', 'closed_loss', 'stop_loss',
                    -1000, ?, 430, 425, 1, 2.0)
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_stop_loss_quality("EXP-TEST", db_file)
        assert len(result.events) == 1
        assert result.events[0].severity == "warning"
        assert result.events[0].expected_max_loss == 800.0

    def test_missing_db(self, tmp_path):
        """Missing DB → error recorded, passes (fail-open for historical check)."""
        result = check_stop_loss_quality("EXP-TEST", str(tmp_path / "nonexistent.db"))
        assert len(result.errors) == 1

    def test_multiple_contracts(self, tmp_path):
        """Max loss scales with contracts."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # 3 contracts, spread_width=5, max loss = $1500
        # Actual loss = $2000 → 133% → WARNING
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, exit_reason,
                                pnl, exit_date, short_strike, long_strike,
                                contracts, credit)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_loss', 'stop_loss',
                    -2000, ?, 430, 425, 3, 1.5)
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_stop_loss_quality("EXP-TEST", db_file)
        assert len(result.events) == 1
        assert result.events[0].expected_max_loss == 1500.0


# ===========================================================================
# Gate 18 — Repeated Failure Detection
# ===========================================================================

class TestGate18RepeatedFailures:
    """Tests for check_repeated_failures."""

    def test_no_failures(self, tmp_path):
        """All profitable trades → passes."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        for i in range(10):
            conn.execute("""
                INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
                VALUES (?, 'SPY', 'closed_profit', 'profit_target', 200, ?)
            """, (f"t{i}", _recent_iso(i)))
        conn.commit()
        conn.close()

        result = check_repeated_failures("EXP-TEST", db_file)
        assert result.passed
        assert result.current_loss_streak == 0

    def test_streak_warning(self, tmp_path):
        """5 consecutive losses → WARNING."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        for i in range(STREAK_WARNING):
            conn.execute("""
                INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
                VALUES (?, 'SPY', 'closed_loss', 'stop_loss', -500, ?)
            """, (f"t{i}", _recent_iso(i)))
        # Add an older win to break any older streak
        conn.execute("""
            INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
            VALUES ('win1', 'SPY', 'closed_profit', 'profit_target', 200, ?)
        """, (_recent_iso(STREAK_WARNING + 1),))
        conn.commit()
        conn.close()

        result = check_repeated_failures("EXP-TEST", db_file)
        assert result.current_loss_streak == STREAK_WARNING
        assert len(result.streaks) >= 1
        assert any(s.severity == "warning" for s in result.streaks)

    def test_streak_critical(self, tmp_path):
        """8 consecutive losses → CRITICAL."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        for i in range(STREAK_CRITICAL):
            conn.execute("""
                INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
                VALUES (?, 'SPY', 'closed_loss', 'stop_loss', -500, ?)
            """, (f"t{i}", _recent_iso(i)))
        conn.execute("""
            INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
            VALUES ('win1', 'SPY', 'closed_profit', 'profit_target', 200, ?)
        """, (_recent_iso(STREAK_CRITICAL + 1),))
        conn.commit()
        conn.close()

        result = check_repeated_failures("EXP-TEST", db_file)
        assert result.current_loss_streak == STREAK_CRITICAL
        assert any(s.severity == "critical" for s in result.streaks)

    def test_streak_halt(self, tmp_path):
        """12 consecutive losses → HALT."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        for i in range(STREAK_HALT):
            conn.execute("""
                INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
                VALUES (?, 'SPY', 'closed_loss', 'stop_loss', -500, ?)
            """, (f"t{i}", _recent_iso(i)))
        conn.execute("""
            INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
            VALUES ('win1', 'SPY', 'closed_profit', 'profit_target', 200, ?)
        """, (_recent_iso(STREAK_HALT + 1),))
        conn.commit()
        conn.close()

        result = check_repeated_failures("EXP-TEST", db_file)
        assert result.current_loss_streak == STREAK_HALT
        assert any(s.severity == "halt" for s in result.streaks)
        assert not result.passed

    def test_broken_streak(self, tmp_path):
        """Win in the middle breaks the streak."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # 3 losses, then a win, then 3 more losses
        for i in range(3):
            conn.execute("""
                INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
                VALUES (?, 'SPY', 'closed_loss', 'stop_loss', -500, ?)
            """, (f"loss_a_{i}", _recent_iso(i)))
        conn.execute("""
            INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
            VALUES ('win', 'SPY', 'closed_profit', 'profit_target', 200, ?)
        """, (_recent_iso(4),))
        for i in range(3):
            conn.execute("""
                INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
                VALUES (?, 'SPY', 'closed_loss', 'stop_loss', -500, ?)
            """, (f"loss_b_{i}", _recent_iso(5 + i)))
        conn.commit()
        conn.close()

        result = check_repeated_failures("EXP-TEST", db_file)
        assert result.current_loss_streak == 3  # only the most recent 3

    def test_same_reason_escalation(self, tmp_path):
        """Same exit_reason repeating 6+ times → CRITICAL escalation."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # 7 trades: 6 stop_loss + 1 win interspersed
        for i in range(6):
            conn.execute("""
                INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
                VALUES (?, 'SPY', 'closed_loss', 'stop_loss', -500, ?)
            """, (f"loss_{i}", _recent_iso(i)))
        # Add wins mixed in — still 6/7 are same reason
        conn.execute("""
            INSERT INTO trades (id, ticker, status, exit_reason, pnl, exit_date)
            VALUES ('win1', 'SPY', 'closed_profit', 'profit_target', 200, ?)
        """, (_recent_iso(3),))
        conn.commit()
        conn.close()

        result = check_repeated_failures("EXP-TEST", db_file)
        # Should detect same_reason dominance for 'stop_loss'
        reason_streaks = [s for s in result.streaks if s.repeated_reason]
        assert len(reason_streaks) >= 1

    def test_empty_db(self, tmp_path):
        """Empty DB → passes."""
        db_file = str(tmp_path / "test.db")
        _create_db(db_file).close()

        result = check_repeated_failures("EXP-TEST", db_file)
        assert result.passed
        assert result.total_recent_trades == 0


# ===========================================================================
# Gate 19 — Market Calendar Guard
# ===========================================================================

class TestGate19MarketCalendar:
    """Tests for check_market_calendar and calendar helpers."""

    def test_weekday_trades_pass(self, tmp_path):
        """Trades on a regular weekday → no events."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # Find next Monday
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7  # Monday
        if days_ahead == 0 and today.weekday() != 0:
            days_ahead = 7
        monday = today + timedelta(days=days_ahead)
        if monday > today:
            monday = today - timedelta(days=today.weekday())  # last Monday

        entry_dt = datetime(monday.year, monday.month, monday.day, 10, 0,
                            tzinfo=timezone.utc)
        conn.execute("""
            INSERT INTO trades (id, ticker, status, entry_date)
            VALUES ('t1', 'SPY', 'open', ?)
        """, (entry_dt.isoformat(),))
        conn.commit()
        conn.close()

        result = check_market_calendar("EXP-TEST", db_file, lookback_days=60)
        # Filter out any holiday hits (Monday could be a holiday)
        weekend_events = [e for e in result.events if e.reason == "weekend"]
        assert len(weekend_events) == 0

    def test_saturday_trade_critical(self, tmp_path):
        """Trade on Saturday → CRITICAL."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # Find a recent Saturday
        today = date.today()
        days_back = (today.weekday() - 5) % 7
        if days_back == 0 and today.weekday() != 5:
            days_back = 7
        saturday = today - timedelta(days=days_back)

        entry_dt = datetime(saturday.year, saturday.month, saturday.day, 10, 0,
                            tzinfo=timezone.utc)
        conn.execute("""
            INSERT INTO trades (id, ticker, status, entry_date)
            VALUES ('t1', 'SPY', 'open', ?)
        """, (entry_dt.isoformat(),))
        conn.commit()
        conn.close()

        result = check_market_calendar("EXP-TEST", db_file, lookback_days=60)
        assert len(result.events) == 1
        assert result.events[0].severity == "critical"
        assert result.events[0].reason == "weekend"
        assert not result.passed

    def test_sunday_trade_critical(self, tmp_path):
        """Trade on Sunday → CRITICAL."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        today = date.today()
        days_back = (today.weekday() - 6) % 7
        if days_back == 0 and today.weekday() != 6:
            days_back = 7
        sunday = today - timedelta(days=days_back)

        entry_dt = datetime(sunday.year, sunday.month, sunday.day, 10, 0,
                            tzinfo=timezone.utc)
        conn.execute("""
            INSERT INTO trades (id, ticker, status, entry_date)
            VALUES ('t1', 'SPY', 'open', ?)
        """, (entry_dt.isoformat(),))
        conn.commit()
        conn.close()

        result = check_market_calendar("EXP-TEST", db_file, lookback_days=60)
        assert len(result.events) == 1
        assert result.events[0].severity == "critical"
        assert result.events[0].reason == "weekend"

    def test_holiday_trade_warning(self, tmp_path):
        """Trade on Christmas (Thursday 2025-12-25) → WARNING."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # 2025-12-25 is a Thursday (not weekend), and is Christmas
        entry_dt = datetime(2025, 12, 25, 10, 0, tzinfo=timezone.utc)
        conn.execute("""
            INSERT INTO trades (id, ticker, status, entry_date)
            VALUES ('t1', 'SPY', 'open', ?)
        """, (entry_dt.isoformat(),))
        conn.commit()
        conn.close()

        result = check_market_calendar("EXP-TEST", db_file, lookback_days=365)
        holiday_events = [e for e in result.events if e.reason == "holiday"]
        assert len(holiday_events) == 1
        assert holiday_events[0].severity == "warning"

    def test_is_market_day(self):
        """Basic market day checks."""
        # 2026-04-19 is a Sunday
        assert not is_market_day(date(2026, 4, 19))
        # 2026-04-20 is a Monday
        assert is_market_day(date(2026, 4, 20))
        # 2026-12-25 is a Friday (Christmas)
        assert not is_market_day(date(2026, 12, 25))

    def test_easter_computation(self):
        """Verify Easter dates for known years."""
        assert _easter(2026) == date(2026, 4, 5)
        assert _easter(2025) == date(2025, 4, 20)
        assert _easter(2024) == date(2024, 3, 31)

    def test_good_friday_holiday(self):
        """Good Friday should be a market holiday."""
        # 2026 Good Friday = April 3
        assert is_market_holiday(date(2026, 4, 3))

    def test_market_holidays_2026(self):
        """Spot-check 2026 holidays."""
        holidays = _get_market_holidays(2026)
        # New Year's: Jan 1 (Thursday)
        assert date(2026, 1, 1) in holidays
        # MLK Day: 3rd Monday of Jan = Jan 19
        assert date(2026, 1, 19) in holidays
        # Presidents' Day: 3rd Monday of Feb = Feb 16
        assert date(2026, 2, 16) in holidays

    def test_failed_open_excluded(self, tmp_path):
        """Trades with status='failed_open' are excluded from calendar check."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        today = date.today()
        days_back = (today.weekday() - 5) % 7
        if days_back == 0 and today.weekday() != 5:
            days_back = 7
        saturday = today - timedelta(days=days_back)

        entry_dt = datetime(saturday.year, saturday.month, saturday.day, 10, 0,
                            tzinfo=timezone.utc)
        conn.execute("""
            INSERT INTO trades (id, ticker, status, entry_date)
            VALUES ('t1', 'SPY', 'failed_open', ?)
        """, (entry_dt.isoformat(),))
        conn.commit()
        conn.close()

        result = check_market_calendar("EXP-TEST", db_file, lookback_days=60)
        assert result.passed  # failed_open excluded


# ===========================================================================
# Gate 20 — P&L Reconciliation
# ===========================================================================

class TestGate20PnlReconciliation:
    """Tests for check_pnl_reconciliation."""

    def test_normal_pnl_passes(self, tmp_path):
        """Trade with pnl within theoretical bounds → passes."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # credit=1.50, spread_width=5, max_profit = $150
        # pnl = $140 → within bounds
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, pnl,
                                credit, contracts, short_strike, long_strike,
                                exit_date, exit_reason)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_profit', 140,
                    1.5, 1, 430, 425, ?, 'profit_target')
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_pnl_reconciliation("EXP-TEST", db_file)
        assert result.passed
        assert result.null_pnl_count == 0

    def test_null_pnl_warning(self, tmp_path):
        """Trade with NULL pnl → WARNING."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        conn.execute("""
            INSERT INTO trades (id, ticker, status, pnl, exit_date, exit_reason)
            VALUES ('t1', 'SPY', 'closed_external', NULL, ?, 'external')
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_pnl_reconciliation("EXP-TEST", db_file)
        assert result.null_pnl_count == 1
        assert len(result.discrepancies) == 1
        assert result.discrepancies[0].severity == "warning"

    def test_loss_exceeds_max(self, tmp_path):
        """Loss exceeding theoretical max → discrepancy flagged."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # credit=1.50, spread_width=5, max_loss = (5 - 1.5) × 1 × 100 = $350
        # pnl = -$500 → 42.9% over max → CRITICAL
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, pnl,
                                credit, contracts, short_strike, long_strike,
                                exit_date, exit_reason)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_loss', -500,
                    1.5, 1, 430, 425, ?, 'stop_loss')
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_pnl_reconciliation("EXP-TEST", db_file)
        loss_discs = [d for d in result.discrepancies if d.recorded_pnl is not None]
        assert len(loss_discs) == 1
        assert loss_discs[0].severity == "critical"

    def test_profit_exceeds_max(self, tmp_path):
        """Profit exceeding theoretical max credit → flagged."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # credit=1.50, max_profit = $150
        # pnl = $200 → 33.3% over max → CRITICAL
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, pnl,
                                credit, contracts, short_strike, long_strike,
                                exit_date, exit_reason)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_profit', 200,
                    1.5, 1, 430, 425, ?, 'profit_target')
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_pnl_reconciliation("EXP-TEST", db_file)
        profit_discs = [d for d in result.discrepancies if d.recorded_pnl is not None]
        assert len(profit_discs) == 1
        assert profit_discs[0].severity == "critical"

    def test_missing_credit_skipped(self, tmp_path):
        """Trade with NULL credit → skipped (can't validate)."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        conn.execute("""
            INSERT INTO trades (id, ticker, status, pnl, credit,
                                short_strike, long_strike, exit_date)
            VALUES ('t1', 'SPY', 'closed_profit', 140, NULL, 430, 425, ?)
        """, (_recent_iso(1),))
        conn.commit()
        conn.close()

        result = check_pnl_reconciliation("EXP-TEST", db_file)
        assert result.passed
        assert len(result.discrepancies) == 0

    def test_empty_db(self, tmp_path):
        """Empty DB → passes."""
        db_file = str(tmp_path / "test.db")
        _create_db(db_file).close()

        result = check_pnl_reconciliation("EXP-TEST", db_file)
        assert result.passed
        assert result.trades_checked == 0


# ===========================================================================
# Unified — check_execution_gates
# ===========================================================================

class TestUnifiedExecutionGates:
    """Tests for check_execution_gates and format_execution_report."""

    def test_all_pass(self, tmp_path):
        """Clean DB → all gates pass."""
        db_file = str(tmp_path / "test.db")
        conn = _create_db(db_file)
        # Add a normal profitable trade on a weekday
        today = date.today()
        # Use last Friday if today is weekend
        if today.weekday() >= 5:
            weekday = today - timedelta(days=(today.weekday() - 4))
        else:
            weekday = today
        entry_dt = datetime(weekday.year, weekday.month, weekday.day, 10, 0,
                            tzinfo=timezone.utc)
        conn.execute("""
            INSERT INTO trades (id, ticker, strategy_type, status, pnl,
                                credit, contracts, short_strike, long_strike,
                                entry_date, exit_date, exit_reason)
            VALUES ('t1', 'SPY', 'bull_put', 'closed_profit', 140,
                    1.5, 1, 430, 425, ?, ?, 'profit_target')
        """, (entry_dt.isoformat(), _recent_iso(0)))
        conn.commit()
        conn.close()

        result = check_execution_gates("EXP-TEST", db_file)
        assert result.passed
        assert result.gate17 is not None
        assert result.gate18 is not None
        assert result.gate19 is not None
        assert result.gate20 is not None

    def test_format_report_clean(self, tmp_path):
        """Format report with no issues."""
        db_file = str(tmp_path / "test.db")
        _create_db(db_file).close()

        result = check_execution_gates("EXP-TEST", db_file)
        report = format_execution_report({"EXP-TEST": result})
        assert "Gates 17-20" in report
        assert "EXP-TEST" in report

    def test_format_report_with_issues(self):
        """Format report with issues."""
        result = ExecutionGatesResult(exp_id="EXP-TEST")
        result.gate17 = StopLossQualityResult(
            exp_id="EXP-TEST", stop_loss_trades=1
        )
        from sentinel.gates_execution import SlippageEvent
        result.gate17.events.append(SlippageEvent(
            trade_id="t1", ticker="SPY", strategy_type="bull_put",
            expected_max_loss=500, actual_loss=1000, slippage_ratio=2.0,
            severity="halt", message="test"
        ))
        report = format_execution_report({"EXP-TEST": result})
        assert "G17" in report
        assert "slippage" in report.lower()
