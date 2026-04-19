"""
Tests for SENTINEL Gates 13–16 — Account Health & Position Management.

Tests cover:
  Gate 13 — Account Health Monitor (drawdown, buying power)
  Gate 14 — Expired Position Detection
  Gate 15 — Position Concentration Guard
  Gate 16 — Orphan Detection v2 (leg matching)
  Unified — check_account_gates() entry point

Each gate is tested with in-memory SQLite DBs. No external API calls.
"""

import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.gates_account import (
    check_account_health,
    check_expired_positions,
    check_position_concentration,
    check_orphans_v2,
    check_account_gates,
    _parse_occ_symbol,
    _build_occ,
    _build_expected_symbols,
    AccountHealthResult,
    ExpiredPositionResult,
    ConcentrationResult,
    OrphanV2Result,
    DD_WARNING_PCT,
    DD_HALT_PCT,
    DD_FLATTEN_PCT,
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanner_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL,
            leg_type TEXT,
            occ_symbol TEXT,
            strike REAL,
            status TEXT DEFAULT 'open'
        )
    """)
    conn.commit()
    return conn


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary DB and return the path."""
    db_file = str(tmp_path / "test.db")
    conn = _create_db(db_file)
    conn.close()
    return db_file


def _insert_trade(
    db_path, trade_id, ticker="SPY", status="open", strategy_type="bull_put",
    short_strike=500, long_strike=490, expiration="2026-05-01",
    contracts=5, entry_date="2026-04-15T14:00:00+00:00",
):
    """Insert a trade record into the DB."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO trades (id, ticker, status, strategy_type, short_strike,
           long_strike, expiration, contracts, entry_date, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scanner')""",
        (trade_id, ticker, status, strategy_type, short_strike, long_strike,
         expiration, contracts, entry_date),
    )
    conn.commit()
    conn.close()


def _set_peak_equity(db_path, peak):
    """Set sentinel_peak_equity in scanner_state."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO scanner_state (key, value) VALUES ('sentinel_peak_equity', ?)",
        (str(peak),),
    )
    conn.commit()
    conn.close()


# ===========================================================================
# Gate 13 — Account Health Monitor
# ===========================================================================


class TestGate13AccountHealth:
    """Tests for check_account_health."""

    @patch("sentinel.history.SentinelDB")
    def test_ok_no_drawdown(self, mock_sdb, tmp_db):
        """Equity at peak → OK."""
        account = {"equity": 100000, "buying_power": 50000}
        result = check_account_health("EXP-TEST", account, tmp_db)
        assert result.severity == "ok"
        assert result.passed
        assert result.drawdown_pct == 0.0
        assert not result.block_new_entries

    @patch("sentinel.history.SentinelDB")
    def test_warning_drawdown(self, mock_sdb, tmp_db):
        """Equity 18% below peak → WARNING."""
        _set_peak_equity(tmp_db, 100000)
        account = {"equity": 82000, "buying_power": 40000}
        result = check_account_health("EXP-TEST", account, tmp_db)
        assert result.severity == "warning"
        assert result.passed  # warning still passes
        assert 0.17 < result.drawdown_pct < 0.19

    @patch("sentinel.history.SentinelDB")
    def test_halt_drawdown(self, mock_sdb, tmp_db):
        """Equity 30% below peak → HALT."""
        _set_peak_equity(tmp_db, 100000)
        account = {"equity": 70000, "buying_power": 30000}
        result = check_account_health("EXP-TEST", account, tmp_db)
        assert result.severity == "halt"
        assert not result.passed
        assert 0.29 < result.drawdown_pct < 0.31

    @patch("sentinel.history.SentinelDB")
    def test_flatten_drawdown(self, mock_sdb, tmp_db):
        """Equity 50% below peak → FLATTEN."""
        _set_peak_equity(tmp_db, 100000)
        account = {"equity": 50000, "buying_power": 20000}
        result = check_account_health("EXP-TEST", account, tmp_db)
        assert result.severity == "flatten"
        assert not result.passed
        assert result.drawdown_pct >= DD_FLATTEN_PCT

    @patch("sentinel.history.SentinelDB")
    def test_peak_updated_on_new_high(self, mock_sdb, tmp_db):
        """Peak equity should update when equity exceeds previous peak."""
        _set_peak_equity(tmp_db, 100000)
        account = {"equity": 110000, "buying_power": 60000}
        result = check_account_health("EXP-TEST", account, tmp_db)
        assert result.peak_equity == 110000
        assert result.peak_updated
        assert result.drawdown_pct == 0.0

    @patch("sentinel.history.SentinelDB")
    def test_buying_power_blocks(self, mock_sdb, tmp_db):
        """Low buying power should block new entries."""
        config = {"strategy": {"spread_width": 12}}
        account = {"equity": 100000, "buying_power": 500}  # < $1200 min trade
        result = check_account_health("EXP-TEST", account, tmp_db, config=config)
        assert result.block_new_entries
        assert result.min_trade_cost == 1200.0

    @patch("sentinel.history.SentinelDB")
    def test_buying_power_ok(self, mock_sdb, tmp_db):
        """Sufficient buying power should not block."""
        config = {"strategy": {"spread_width": 12}}
        account = {"equity": 100000, "buying_power": 5000}
        result = check_account_health("EXP-TEST", account, tmp_db, config=config)
        assert not result.block_new_entries

    @patch("sentinel.history.SentinelDB")
    def test_exp503_scenario(self, mock_sdb, tmp_db):
        """Reproduce EXP-503's -56% drawdown detection."""
        _set_peak_equity(tmp_db, 122403)
        account = {"equity": 53520, "buying_power": 18580}
        result = check_account_health("EXP-503", account, tmp_db)
        assert result.severity == "flatten"
        assert result.drawdown_pct > 0.50


# ===========================================================================
# Gate 14 — Expired Position Detection
# ===========================================================================


class TestGate14ExpiredPositions:
    """Tests for check_expired_positions."""

    def test_no_expired(self, tmp_db):
        """No expired positions → OK."""
        _insert_trade(tmp_db, "t1", expiration="2099-12-31")
        result = check_expired_positions("EXP-TEST", tmp_db, today="2026-04-19")
        assert not result.has_expired
        assert result.severity == "ok"

    def test_detects_expired(self, tmp_db):
        """Position past expiration → CRITICAL."""
        _insert_trade(tmp_db, "t1", expiration="2026-04-17", status="open")
        result = check_expired_positions("EXP-TEST", tmp_db, today="2026-04-19")
        assert result.has_expired
        assert len(result.expired) == 1
        assert result.expired[0].trade_id == "t1"
        assert result.expired[0].days_expired == 2
        assert result.severity == "critical"

    def test_auto_close_marks_expired(self, tmp_db):
        """auto_close=True should update status in DB."""
        _insert_trade(tmp_db, "t1", expiration="2026-04-17", status="open")
        result = check_expired_positions("EXP-TEST", tmp_db, today="2026-04-19", auto_close=True)
        assert result.expired[0].action_taken == "marked_expired"

        # Verify DB was updated
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT status FROM trades WHERE id = 't1'").fetchone()
        conn.close()
        assert row[0] == "closed_expired"

    def test_auto_close_false_no_update(self, tmp_db):
        """auto_close=False should not modify DB."""
        _insert_trade(tmp_db, "t1", expiration="2026-04-17", status="open")
        check_expired_positions("EXP-TEST", tmp_db, today="2026-04-19", auto_close=False)

        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT status FROM trades WHERE id = 't1'").fetchone()
        conn.close()
        assert row[0] == "open"  # unchanged

    def test_ignores_future_expiration(self, tmp_db):
        """Positions not yet expired should be ignored."""
        _insert_trade(tmp_db, "t1", expiration="2026-04-20", status="open")
        result = check_expired_positions("EXP-TEST", tmp_db, today="2026-04-19")
        assert not result.has_expired

    def test_exp800_scenario(self, tmp_db):
        """Reproduce EXP-800: 3 expired positions from 04-17 still open on 04-19."""
        _insert_trade(tmp_db, "bc1", strategy_type="bear_call", expiration="2026-04-17",
                       short_strike=645, long_strike=657, contracts=5)
        _insert_trade(tmp_db, "bc2", strategy_type="bear_call", expiration="2026-04-17",
                       short_strike=663, long_strike=675, contracts=5)
        _insert_trade(tmp_db, "ic1", strategy_type="iron_condor", expiration="2026-04-17",
                       short_strike=642, long_strike=630, contracts=7, status="pending_open")
        _insert_trade(tmp_db, "bp1", strategy_type="bull_put", expiration="2026-05-01",
                       short_strike=666, long_strike=654, contracts=10)

        result = check_expired_positions("EXP-800", tmp_db, today="2026-04-19")
        assert len(result.expired) == 3  # bc1, bc2, ic1 — not bp1
        assert result.severity == "critical"

    def test_multiple_statuses_caught(self, tmp_db):
        """Expired pending_close should also be caught."""
        _insert_trade(tmp_db, "t1", expiration="2026-04-17", status="pending_close")
        result = check_expired_positions("EXP-TEST", tmp_db, today="2026-04-19")
        assert len(result.expired) == 1

    def test_already_closed_ignored(self, tmp_db):
        """Closed trades should not be flagged."""
        _insert_trade(tmp_db, "t1", expiration="2026-04-17", status="closed_profit")
        result = check_expired_positions("EXP-TEST", tmp_db, today="2026-04-19")
        assert not result.has_expired


# ===========================================================================
# Gate 15 — Position Concentration Guard
# ===========================================================================


class TestGate15PositionConcentration:
    """Tests for check_position_concentration."""

    def test_no_positions(self, tmp_db):
        """No positions → passes."""
        result = check_position_concentration("EXP-TEST", tmp_db)
        assert result.passed
        assert result.open_positions == 0

    def test_same_expiry_violation(self, tmp_db):
        """4 positions on same expiration → critical."""
        for i in range(4):
            _insert_trade(tmp_db, f"t{i}", expiration="2026-05-01")
        result = check_position_concentration("EXP-TEST", tmp_db, max_same_expiry=3)
        violations = [v for v in result.violations if v.check == "same_expiry"]
        assert len(violations) == 1
        assert violations[0].severity == "critical"
        assert result.block_new_entries

    def test_same_expiry_ok(self, tmp_db):
        """3 positions on same expiration → OK (at limit)."""
        for i in range(3):
            _insert_trade(tmp_db, f"t{i}", expiration="2026-05-01")
        result = check_position_concentration("EXP-TEST", tmp_db, max_same_expiry=3)
        violations = [v for v in result.violations if v.check == "same_expiry"]
        assert len(violations) == 0

    def test_different_expiries_ok(self, tmp_db):
        """Positions spread across expirations → OK."""
        for i in range(5):
            _insert_trade(tmp_db, f"t{i}", expiration=f"2026-05-{i+1:02d}")
        result = check_position_concentration("EXP-TEST", tmp_db, max_same_expiry=3)
        violations = [v for v in result.violations if v.check == "same_expiry"]
        assert len(violations) == 0

    def test_portfolio_risk_violation(self, tmp_db):
        """Total risk > 50% of equity → critical."""
        # 5 trades × $12 width × 10 contracts = $60,000 risk on $100k account = 60%
        for i in range(5):
            _insert_trade(
                tmp_db, f"t{i}", short_strike=500, long_strike=488,
                contracts=10, expiration=f"2026-05-{i+1:02d}",
            )
        result = check_position_concentration(
            "EXP-TEST", tmp_db, account_equity=100000,
        )
        violations = [v for v in result.violations if v.check == "portfolio_risk"]
        assert len(violations) == 1
        assert result.block_new_entries

    def test_orphan_records_excluded(self, tmp_db):
        """Orphan and synthetic records should be excluded from checks."""
        _insert_trade(tmp_db, "orphan-SPY123", expiration="2026-05-01")
        _insert_trade(tmp_db, "synthetic-monitor-SPY123", expiration="2026-05-01")
        _insert_trade(tmp_db, "t1", expiration="2026-05-01")
        result = check_position_concentration("EXP-TEST", tmp_db)
        assert result.open_positions == 1  # only real trade

    def test_exp503_scenario(self, tmp_db):
        """Reproduce EXP-503: 8 trades on same expiry with $12 width × 14 contracts."""
        for i in range(8):
            _insert_trade(
                tmp_db, f"t{i}", strategy_type="bear_call",
                short_strike=670, long_strike=682,
                expiration="2026-04-17", contracts=14,
            )
        result = check_position_concentration(
            "EXP-503", tmp_db, account_equity=100000,
            max_same_expiry=3,
        )
        # Should have same_expiry violation (8 > 3)
        expiry_violations = [v for v in result.violations if v.check == "same_expiry"]
        assert len(expiry_violations) == 1
        assert expiry_violations[0].current_value == 8

        # Should also have portfolio_risk violation
        # 8 × $12 × 14 × 100 = $134,400 on $100k = 134%
        risk_violations = [v for v in result.violations if v.check == "portfolio_risk"]
        assert len(risk_violations) == 1
        assert result.block_new_entries


# ===========================================================================
# Gate 16 — Orphan Detection v2
# ===========================================================================


class TestGate16OrphanV2:
    """Tests for check_orphans_v2."""

    def test_no_positions(self, tmp_db):
        """No Alpaca positions → OK."""
        result = check_orphans_v2("EXP-TEST", [], tmp_db)
        assert result.severity == "ok"
        assert result.matched_legs == 0

    def test_spread_legs_matched(self, tmp_db):
        """Spread legs should match to parent trade, not be flagged as orphans."""
        # Insert a bull_put spread: short 666, long 654
        _insert_trade(
            tmp_db, "t1", ticker="SPY", strategy_type="bull_put",
            short_strike=666, long_strike=654, expiration="2026-05-01",
        )

        # Alpaca has both legs
        alpaca_positions = [
            {"symbol": "SPY   260501P00666000"},
            {"symbol": "SPY   260501P00654000"},
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert result.matched_legs == 2
        assert len(result.true_orphans) == 0
        assert result.severity == "ok"

    def test_bear_call_legs_matched(self, tmp_db):
        """Bear call spread legs should match."""
        _insert_trade(
            tmp_db, "t1", ticker="SPY", strategy_type="bear_call",
            short_strike=670, long_strike=682, expiration="2026-04-24",
        )
        alpaca_positions = [
            {"symbol": "SPY   260424C00670000"},
            {"symbol": "SPY   260424C00682000"},
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert result.matched_legs == 2
        assert len(result.true_orphans) == 0

    def test_true_orphan_detected(self, tmp_db):
        """Position with no matching trade → true orphan."""
        # No trades in DB
        alpaca_positions = [
            {"symbol": "AAPL  260501C00200000"},
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert len(result.true_orphans) == 1
        assert result.severity == "critical"

    def test_mixed_matched_and_orphan(self, tmp_db):
        """Some legs match, some are true orphans."""
        _insert_trade(
            tmp_db, "t1", ticker="SPY", strategy_type="bull_put",
            short_strike=500, long_strike=490, expiration="2026-05-01",
        )
        alpaca_positions = [
            {"symbol": "SPY   260501P00500000"},  # matches short leg
            {"symbol": "SPY   260501P00490000"},  # matches long leg
            {"symbol": "AAPL  260501C00200000"},  # true orphan
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert result.matched_legs == 2
        assert len(result.true_orphans) == 1

    def test_equity_positions_ignored(self, tmp_db):
        """Short equity tickers (<= 10 chars) should be ignored."""
        alpaca_positions = [
            {"symbol": "SPY"},
            {"symbol": "IBIT"},
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert result.total_alpaca_positions == 0  # filtered out

    def test_needs_investigation_same_ticker_expiry(self, tmp_db):
        """Position with same ticker+expiry as a trade but different strike → investigate."""
        _insert_trade(
            tmp_db, "t1", ticker="SPY", strategy_type="bear_call",
            short_strike=670, long_strike=682, expiration="2026-05-01",
        )
        # Different strike on same ticker+expiry
        alpaca_positions = [
            {"symbol": "SPY   260501C00700000"},
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert len(result.needs_investigation) == 1
        assert len(result.true_orphans) == 0

    def test_five_orphans_triggers_halt(self, tmp_db):
        """5+ true orphans → halt severity."""
        alpaca_positions = [
            {"symbol": f"AAPL  26050{i}C00200000"} for i in range(1, 6)
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert result.severity == "halt"
        assert not result.passed

    def test_iron_condor_all_four_legs(self, tmp_db):
        """Iron condor should match all 4 legs."""
        _insert_trade(
            tmp_db, "t1", ticker="SPY", strategy_type="iron_condor",
            short_strike=500, long_strike=490, expiration="2026-05-01",
            contracts=7,
        )
        # IC has put and call sides — short/long strikes apply to both
        alpaca_positions = [
            {"symbol": "SPY   260501P00500000"},
            {"symbol": "SPY   260501P00490000"},
            {"symbol": "SPY   260501C00500000"},
            {"symbol": "SPY   260501C00490000"},
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert result.matched_legs == 4
        assert len(result.true_orphans) == 0

    def test_trade_legs_table_used(self, tmp_db):
        """If trade_legs table has OCC symbols, those should match."""
        _insert_trade(
            tmp_db, "t1", ticker="SPY", strategy_type="bull_put",
            short_strike=500, long_strike=490, expiration="2026-05-01",
        )
        # Add explicit legs
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) VALUES (?, ?, ?)",
            ("t1", "short", "SPY   260501P00500000"),
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) VALUES (?, ?, ?)",
            ("t1", "long", "SPY   260501P00490000"),
        )
        conn.commit()
        conn.close()

        alpaca_positions = [
            {"symbol": "SPY   260501P00500000"},
            {"symbol": "SPY   260501P00490000"},
        ]
        result = check_orphans_v2("EXP-TEST", alpaca_positions, tmp_db)
        assert result.matched_legs == 2
        assert len(result.true_orphans) == 0


# ===========================================================================
# OCC Symbol Helpers
# ===========================================================================


class TestOCCHelpers:
    """Tests for OCC symbol parsing and building."""

    def test_parse_occ_symbol(self):
        parsed = _parse_occ_symbol("SPY   260501P00666000")
        assert parsed is not None
        assert parsed["ticker"] == "SPY"
        assert parsed["expiration"] == "2026-05-01"
        assert parsed["put_call"] == "P"
        assert parsed["strike"] == 666.0

    def test_parse_occ_call(self):
        parsed = _parse_occ_symbol("SPY   260424C00670000")
        assert parsed["put_call"] == "C"
        assert parsed["strike"] == 670.0
        assert parsed["expiration"] == "2026-04-24"

    def test_parse_ibit(self):
        parsed = _parse_occ_symbol("IBIT  260501P00039000")
        assert parsed is not None
        assert parsed["ticker"] == "IBIT"
        assert parsed["strike"] == 39.0

    def test_parse_short_symbol(self):
        """Symbols < 15 chars should return None."""
        assert _parse_occ_symbol("SPY") is None
        assert _parse_occ_symbol("") is None
        assert _parse_occ_symbol(None) is None

    def test_build_occ(self):
        occ = _build_occ("SPY", "2026-05-01", "P", 666.0)
        assert occ == "SPY   260501P00666000"

    def test_build_occ_ibit(self):
        occ = _build_occ("IBIT", "2026-05-01", "P", 39.0)
        assert occ == "IBIT  260501P00039000"

    def test_roundtrip(self):
        """Build → parse → build should produce identical result."""
        original = _build_occ("SPY", "2026-04-24", "C", 670.0)
        parsed = _parse_occ_symbol(original)
        rebuilt = _build_occ(parsed["ticker"], parsed["expiration"],
                             parsed["put_call"], parsed["strike"])
        assert original == rebuilt


# ===========================================================================
# Unified Entry Point
# ===========================================================================


class TestCheckAccountGates:
    """Tests for check_account_gates unified function."""

    @patch("sentinel.history.SentinelDB")
    @patch("sentinel.gates_account._do_halt")
    def test_halt_on_severe_drawdown(self, mock_halt, mock_sdb, tmp_db):
        """Gate 13 halt should propagate to unified result."""
        _set_peak_equity(tmp_db, 100000)
        result = check_account_gates(
            "EXP-TEST", tmp_db,
            alpaca_account={"equity": 60000, "buying_power": 20000},
        )
        assert result["halted"]
        mock_halt.assert_called_once()

    @patch("sentinel.history.SentinelDB")
    def test_skip_gates(self, mock_sdb, tmp_db):
        """Skipped gates should not appear in results."""
        result = check_account_gates(
            "EXP-TEST", tmp_db,
            alpaca_account={"equity": 100000, "buying_power": 50000},
            skip_gates=[14, 15, 16],
        )
        assert "gate13" in result
        assert "gate14" not in result
        assert "gate15" not in result
        assert "gate16" not in result

    @patch("sentinel.history.SentinelDB")
    def test_block_on_buying_power(self, mock_sdb, tmp_db):
        """Buying power insufficient → block_new_entries."""
        config = {"strategy": {"spread_width": 12}}
        result = check_account_gates(
            "EXP-TEST", tmp_db,
            alpaca_account={"equity": 100000, "buying_power": 500},
            config=config,
            skip_gates=[14, 15, 16],
        )
        assert result["block_new_entries"]

    @patch("sentinel.history.SentinelDB")
    def test_all_gates_ok(self, mock_sdb, tmp_db):
        """All gates pass → no halt, no block."""
        _insert_trade(tmp_db, "t1", expiration="2099-12-31")
        result = check_account_gates(
            "EXP-TEST", tmp_db,
            alpaca_account={"equity": 100000, "buying_power": 50000},
            alpaca_positions=[],
        )
        assert not result["halted"]
        assert not result["block_new_entries"]

    @patch("sentinel.history.SentinelDB")
    def test_expired_positions_cleaned(self, mock_sdb, tmp_db):
        """Gate 14 should clean expired positions."""
        _insert_trade(tmp_db, "t1", expiration="2026-04-17")
        result = check_account_gates(
            "EXP-TEST", tmp_db,
            alpaca_account={"equity": 100000, "buying_power": 50000},
            skip_gates=[16],
        )
        g14 = result["gate14"]
        assert g14.has_expired
        assert len(g14.expired) == 1
