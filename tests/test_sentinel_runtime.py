"""
Comprehensive tests for SENTINEL Runtime Gates 6–9.

Tests cover:
  Gate 6  — Trade Sizing Validator
  Gate 7  — Orphan / Unmanaged Position Detector
  Gate 8  — Live-vs-Backtest Drift Tracker
  Gate 9  — Position Lifecycle Monitor
  Unified — post_scan_check() entry point

Each gate is tested with mock data and in-memory SQLite DBs.
No external API calls or file system state required.
"""

import json
import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Fixtures — in-memory DB with trades schema
# ---------------------------------------------------------------------------

def _create_trades_db(path: str) -> sqlite3.Connection:
    """Create a minimal trades DB with the expected schema."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            source TEXT,
            ticker TEXT,
            status TEXT DEFAULT 'open',
            contracts INTEGER DEFAULT 1,
            pnl REAL,
            credit REAL,
            entry_date TEXT,
            exit_date TEXT,
            expiration TEXT,
            short_strike REAL,
            long_strike REAL,
            created_at TEXT,
            updated_at TEXT,
            strategy_type TEXT DEFAULT 'bull_put',
            metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL,
            leg_type TEXT,
            occ_symbol TEXT,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanner_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_dedup (
            trade_id TEXT,
            severity TEXT,
            alert_time TEXT,
            PRIMARY KEY (trade_id, severity)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT,
            event_type TEXT,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


# OCC symbol helpers for Gate 7 tests
def _occ(ticker: str, date: str = "260418", put_call: str = "P", strike: str = "00520000") -> str:
    """Build a fake OCC symbol. Must be > 10 chars for Gate 7 filter."""
    # Pad ticker to 6 chars
    padded = ticker.ljust(6)
    return f"{padded}{date}{put_call}{strike}"


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary trades DB and return (path, connection)."""
    db_path = str(tmp_path / "trades.db")
    conn = _create_trades_db(db_path)
    yield db_path, conn
    conn.close()


@pytest.fixture
def base_config():
    """Standard paper config dict."""
    return {
        "risk": {
            "max_risk_per_trade": 0.08,
            "max_contracts": 50,
        },
        "strategy": {
            "spread_width": 5,
        },
    }


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _hours_ago(h: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=h)
    return dt.isoformat()


def _minutes_ago(m: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=m)
    return dt.isoformat()


def _days_ago(d: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=d)
    return dt.isoformat()


# ===========================================================================
# Gate 6 — Trade Sizing Validator
# ===========================================================================

class TestGate6SizingValidator:
    """Test check_trade_sizing()."""

    def test_sizing_ok_exact_match(self, tmp_db, base_config):
        """Exact match: expected == actual → severity 'ok'."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        # equity=100000, risk=8%, width=5 → expected = floor(100000*0.08/500) = 16
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('t1', 'SPY', 'open', 16, ?)", (_now_iso(),)
        )
        conn.commit()

        result = check_trade_sizing("EXP-400", 100000.0, base_config, db_path=db_path)

        assert result.trades_checked == 1
        assert result.deviations[0].severity == "ok"
        assert result.deviations[0].expected_contracts == 16
        assert result.deviations[0].actual_contracts == 16
        assert result.deviations[0].deviation_pct == 0.0

    def test_sizing_ok_within_tolerance(self, tmp_db, base_config):
        """15% deviation is still OK."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        # expected=16, actual=14 → deviation = 2/16 = 12.5% < 15% → ok
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('t1', 'SPY', 'open', 14, ?)", (_now_iso(),)
        )
        conn.commit()

        result = check_trade_sizing("EXP-400", 100000.0, base_config, db_path=db_path)
        assert result.deviations[0].severity == "ok"
        assert result.deviations[0].deviation_pct < 0.15

    def test_sizing_warning_threshold(self, tmp_db, base_config):
        """16-35% deviation → WARNING."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        # expected=16, actual=12 → deviation = 4/16 = 25% → warning
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('t1', 'SPY', 'open', 12, ?)", (_now_iso(),)
        )
        conn.commit()

        result = check_trade_sizing("EXP-400", 100000.0, base_config, db_path=db_path)
        assert result.deviations[0].severity == "warning"
        assert 0.15 < result.deviations[0].deviation_pct <= 0.35

    def test_sizing_critical_threshold(self, tmp_db, base_config):
        """Over 35% deviation → CRITICAL."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        # expected=16, actual=5 → deviation = 11/16 = 68.75% → critical
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('t1', 'SPY', 'open', 5, ?)", (_now_iso(),)
        )
        conn.commit()

        result = check_trade_sizing("EXP-400", 100000.0, base_config, db_path=db_path)
        assert result.deviations[0].severity == "critical"
        assert not result.passed

    def test_sizing_halt_zero_contracts(self, tmp_db, base_config):
        """0 contracts placed → HALT."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('t1', 'SPY', 'open', 0, ?)", (_now_iso(),)
        )
        conn.commit()

        result = check_trade_sizing("EXP-400", 100000.0, base_config, db_path=db_path)
        assert result.deviations[0].severity == "halt"
        assert not result.passed

    def test_sizing_halt_zero_expected(self, tmp_db, base_config):
        """Formula returns 0 (very low equity) → HALT."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('t1', 'SPY', 'open', 1, ?)", (_now_iso(),)
        )
        conn.commit()

        # Equity too low: 100 * 0.08 / 500 = 0.016 → floor = 0
        result = check_trade_sizing("EXP-400", 100.0, base_config, db_path=db_path)
        assert result.deviations[0].severity == "halt"

    def test_sizing_respects_max_contracts(self, tmp_db):
        """Max contracts cap is applied."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        config = {
            "risk": {"max_risk_per_trade": 0.08, "max_contracts": 10},
            "strategy": {"spread_width": 5},
        }
        # equity=100000 → formula gives 16, but max_contracts=10
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('t1', 'SPY', 'open', 10, ?)", (_now_iso(),)
        )
        conn.commit()

        result = check_trade_sizing("EXP-400", 100000.0, config, db_path=db_path)
        assert result.deviations[0].expected_contracts == 10
        assert result.deviations[0].severity == "ok"

    def test_sizing_scan_start_time_filter(self, tmp_db, base_config):
        """Only trades after scan_start_time are checked."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        old_time = _hours_ago(2)
        new_time = _now_iso()
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('old', 'SPY', 'open', 1, ?)", (old_time,)
        )
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('new', 'SPY', 'open', 16, ?)", (new_time,)
        )
        conn.commit()

        recent = _minutes_ago(5)
        result = check_trade_sizing(
            "EXP-400", 100000.0, base_config,
            db_path=db_path, scan_start_time=recent,
        )
        # Only 'new' trade should be checked
        assert result.trades_checked == 1

    def test_sizing_missing_config_fields(self, tmp_db):
        """Missing risk_per_trade or spread_width → error, no crash."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        result = check_trade_sizing("EXP-400", 100000.0, {}, db_path=db_path)
        assert len(result.errors) > 0

    def test_sizing_negative_equity(self, tmp_db, base_config):
        """Negative equity → error."""
        from sentinel.runtime import check_trade_sizing

        db_path, _ = tmp_db
        result = check_trade_sizing("EXP-400", -5000.0, base_config, db_path=db_path)
        assert len(result.errors) > 0

    def test_sizing_risk_pct_as_integer(self, tmp_db):
        """Risk given as 8 (not 0.08) is auto-converted."""
        from sentinel.runtime import check_trade_sizing

        db_path, conn = tmp_db
        config = {
            "risk": {"max_risk_per_trade": 8, "max_contracts": 50},
            "strategy": {"spread_width": 5},
        }
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date) "
            "VALUES ('t1', 'SPY', 'open', 16, ?)", (_now_iso(),)
        )
        conn.commit()

        result = check_trade_sizing("EXP-400", 100000.0, config, db_path=db_path)
        assert result.deviations[0].expected_contracts == 16
        assert result.deviations[0].severity == "ok"


# ===========================================================================
# Gate 7 — Orphan / Unmanaged Position Detector
# ===========================================================================

class TestGate7OrphanDetector:
    """Test check_orphan_positions().

    Gate 7 uses OCC symbols (>10 chars) for matching:
      - Alpaca positions filtered to options only (symbol len > 10)
      - DB symbols from trade_legs.occ_symbol or trades.metadata JSON
    """

    def test_no_orphans_no_ghosts(self, tmp_db):
        """Clean state: Alpaca and DB match perfectly via trade_legs."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        spy_occ = _occ("SPY")
        conn.execute(
            "INSERT INTO trades (id, ticker, status) VALUES ('t1', 'SPY', 'open')"
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) "
            "VALUES ('t1', 'short', ?)", (spy_occ,)
        )
        conn.commit()

        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": spy_occ}],
            db_path=db_path,
        )
        assert result.passed
        assert result.orphans == []
        assert result.ghosts == []
        assert len(result.alerts) == 0

    def test_orphan_detected(self, tmp_db):
        """Position in Alpaca but not in DB → orphan WARNING."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        spy_occ = _occ("SPY")
        aapl_occ = _occ("AAPL")
        conn.execute(
            "INSERT INTO trades (id, ticker, status) VALUES ('t1', 'SPY', 'open')"
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) "
            "VALUES ('t1', 'short', ?)", (spy_occ,)
        )
        conn.commit()

        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": spy_occ}, {"symbol": aapl_occ}],
            db_path=db_path,
        )
        assert aapl_occ.upper() in result.orphans
        assert len(result.alerts) == 1
        assert result.alerts[0]["severity"] == "warning"

    def test_ghost_detected(self, tmp_db):
        """Position in DB (trade_legs) but not in Alpaca → ghost CRITICAL."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        spy_occ = _occ("SPY")
        aapl_occ = _occ("AAPL")
        conn.execute(
            "INSERT INTO trades (id, ticker, status) VALUES ('t1', 'SPY', 'open')"
        )
        conn.execute(
            "INSERT INTO trades (id, ticker, status) VALUES ('t2', 'AAPL', 'open')"
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) "
            "VALUES ('t1', 'short', ?)", (spy_occ,)
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) "
            "VALUES ('t2', 'short', ?)", (aapl_occ,)
        )
        conn.commit()

        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": spy_occ}],  # AAPL missing from Alpaca
            db_path=db_path,
        )
        assert len(result.ghosts) >= 1
        # Ghost alert should be critical
        ghost_alerts = [a for a in result.alerts if "ghost" in a["message"].lower()]
        assert ghost_alerts[0]["severity"] == "critical"

    def test_orphan_consecutive_escalation(self, tmp_db):
        """Orphan persists ≥3 scans → escalates to CRITICAL."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        aapl_occ = _occ("AAPL")
        # Pre-seed consecutive count to 2
        conn.execute(
            "INSERT INTO scanner_state (key, value) VALUES (?, '2')",
            ("sentinel_orphan_counts_EXP-400",),
        )
        conn.commit()

        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": aapl_occ}],  # orphan (not in DB)
            db_path=db_path,
        )
        assert result.consecutive_scans == 3  # 2 + 1
        assert result.alerts[0]["severity"] == "critical"
        assert "unresolved" in result.alerts[0]["message"].lower()

    def test_orphan_halt_threshold(self, tmp_db):
        """≥5 simultaneous orphans → HALT."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        # 5 orphan positions with OCC-length symbols
        positions = [{"symbol": _occ(f"SYM{i}", strike=f"{i:08d}")} for i in range(5)]

        result = check_orphan_positions("EXP-400", positions, db_path=db_path)
        assert result.halt_required
        assert result.alerts[0]["severity"] == "halt"

    def test_orphan_counter_resets_on_clear(self, tmp_db):
        """Counter resets to 0 when no orphans found."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        spy_occ = _occ("SPY")
        conn.execute(
            "INSERT INTO scanner_state (key, value) VALUES (?, '5')",
            ("sentinel_orphan_counts_EXP-400",),
        )
        conn.execute(
            "INSERT INTO trades (id, ticker, status) VALUES ('t1', 'SPY', 'open')"
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) "
            "VALUES ('t1', 'short', ?)", (spy_occ,)
        )
        conn.commit()

        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": spy_occ}],  # matches DB — no orphans
            db_path=db_path,
        )
        assert result.consecutive_scans == 0

    def test_pending_trades_tracked_via_trade_legs(self, tmp_db):
        """pending_open/pending_close trades with trade_legs are tracked."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        spy_occ = _occ("SPY")
        conn.execute(
            "INSERT INTO trades (id, ticker, status) VALUES ('t1', 'SPY', 'pending_open')"
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) "
            "VALUES ('t1', 'short', ?)", (spy_occ,)
        )
        conn.commit()

        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": spy_occ}],
            db_path=db_path,
        )
        # SPY is in both sets → no orphan, no ghost
        assert result.passed

    def test_metadata_fallback(self, tmp_db):
        """When trade_legs is empty, Gate 7 falls back to metadata JSON."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        spy_occ = _occ("SPY")
        metadata = json.dumps({"short_leg_symbol": spy_occ})
        conn.execute(
            "INSERT INTO trades (id, ticker, status, metadata) "
            "VALUES ('t1', 'SPY', 'open', ?)", (metadata,)
        )
        # No trade_legs rows — force metadata fallback by emptying trade_legs
        conn.commit()

        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": spy_occ}],
            db_path=db_path,
        )
        assert result.passed

    def test_short_symbols_filtered_out(self, tmp_db):
        """Alpaca equity positions (short symbols like 'SPY') are filtered out."""
        from sentinel.runtime import check_orphan_positions

        db_path, _ = tmp_db
        # Short symbol positions (equity, not options) should be ignored
        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": "SPY"}, {"symbol": "AAPL"}],  # len < 10
            db_path=db_path,
        )
        assert result.orphans == []
        assert result.passed


# ===========================================================================
# Gate 8 — Live-vs-Backtest Drift Tracker
# ===========================================================================

class TestGate8DriftTracker:
    """Test detect_drift() and _classify_severity()."""

    def test_no_drift_within_tolerance(self):
        """All metrics within bounds → no alerts."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400",
            window_size=30,
            total_closed=30,
            win_rate=72.0,  # baseline 78% → gap 6pp < 10pp threshold
            avg_loss=2000.0,  # baseline 2100 → ratio 0.95 < 1.5x
            peak_drawdown_pct=20.0,  # baseline 41.5% → ratio 0.48 < 0.8
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        assert len(alerts) == 0

    def test_win_rate_warning(self):
        """Win rate 10-15pp below baseline → WARNING."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            win_rate=66.0,  # baseline 78% → gap 12pp
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        wr_alerts = [a for a in alerts if a.metric == "win_rate"]
        assert len(wr_alerts) == 1
        assert wr_alerts[0].severity == "warning"

    def test_win_rate_critical(self):
        """Win rate 15-20pp below → CRITICAL."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            win_rate=60.0,  # baseline 78% → gap 18pp
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        wr_alerts = [a for a in alerts if a.metric == "win_rate"]
        assert len(wr_alerts) == 1
        assert wr_alerts[0].severity == "critical"

    def test_win_rate_halt(self):
        """Win rate ≥20pp below → HALT."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            win_rate=57.0,  # baseline 78% → gap 21pp
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        wr_alerts = [a for a in alerts if a.metric == "win_rate"]
        assert wr_alerts[0].severity == "halt"

    def test_avg_loss_warning(self):
        """Avg loss 1.5-2x baseline → WARNING."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            avg_loss=3500.0,  # baseline 2100 → ratio 1.67
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        al_alerts = [a for a in alerts if a.metric == "avg_loss"]
        assert len(al_alerts) == 1
        assert al_alerts[0].severity == "warning"

    def test_avg_loss_critical(self):
        """Avg loss 2-3x baseline → CRITICAL."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            avg_loss=5000.0,  # baseline 2100 → ratio 2.38
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        al_alerts = [a for a in alerts if a.metric == "avg_loss"]
        assert al_alerts[0].severity == "critical"

    def test_avg_loss_halt(self):
        """Avg loss ≥3x baseline → HALT."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            avg_loss=6500.0,  # baseline 2100 → ratio 3.1
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        al_alerts = [a for a in alerts if a.metric == "avg_loss"]
        assert al_alerts[0].severity == "halt"

    def test_drawdown_warning(self):
        """DD at 80-100% of MC worst → WARNING."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            peak_drawdown_pct=35.0,  # baseline 41.5% → ratio 0.84
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        dd_alerts = [a for a in alerts if a.metric == "drawdown"]
        assert len(dd_alerts) == 1
        assert dd_alerts[0].severity == "warning"

    def test_drawdown_halt(self):
        """DD at ≥110% of MC worst → HALT."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            peak_drawdown_pct=46.0,  # baseline 41.5% → ratio 1.11
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        dd_alerts = [a for a in alerts if a.metric == "drawdown"]
        assert dd_alerts[0].severity == "halt"

    def test_minimum_sample_size(self):
        """Below 10 trades → no alerts even with severe drift."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=8, total_closed=8,
            win_rate=40.0,  # 38pp below baseline!
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        assert len(alerts) == 0

    def test_low_confidence_downgrade(self):
        """10-19 trades → severity downgraded by one tier."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=15, total_closed=15,
            win_rate=57.0,  # gap=21pp → raw HALT → downgraded to CRITICAL
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        wr_alerts = [a for a in alerts if a.metric == "win_rate"]
        assert wr_alerts[0].severity == "critical"  # downgraded from halt
        assert wr_alerts[0].low_confidence is True

    def test_severity_classify_at_20_trades(self):
        """At exactly 20 trades, no downgrade applied."""
        from sentinel.runtime import _classify_severity
        assert _classify_severity("halt", 20) == "halt"
        assert _classify_severity("critical", 20) == "critical"

    def test_severity_classify_at_10_trades(self):
        """At 10 trades (minimum), downgrades applied."""
        from sentinel.runtime import _classify_severity
        assert _classify_severity("halt", 10) == "critical"
        assert _classify_severity("critical", 10) == "warning"
        assert _classify_severity("warning", 10) == "info"

    def test_severity_classify_below_minimum(self):
        """Below 10 trades, suppressed entirely."""
        from sentinel.runtime import _classify_severity
        assert _classify_severity("halt", 5) == ""
        assert _classify_severity("critical", 9) == ""

    def test_multiple_metrics_breach(self):
        """Multiple metrics can fire simultaneously."""
        from sentinel.runtime import RuntimeMetrics, detect_drift

        metrics = RuntimeMetrics(
            exp_id="EXP-400", window_size=30, total_closed=30,
            win_rate=57.0,        # -21pp → halt
            avg_loss=6500.0,      # 3.1x → halt
            peak_drawdown_pct=46.0,  # 111% → halt
        )
        baseline = {"win_rate": 78.0, "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5}

        alerts = detect_drift(metrics, baseline)
        assert len(alerts) == 3
        metrics_hit = {a.metric for a in alerts}
        assert metrics_hit == {"win_rate", "avg_loss", "drawdown"}


# ===========================================================================
# Gate 9 — Position Lifecycle Monitor
# ===========================================================================

class TestGate9LifecycleMonitor:
    """Test check_position_lifecycle()."""

    def test_all_healthy(self, tmp_db):
        """No stuck trades → passed."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'open', ?, ?)",
            (_minutes_ago(5), _minutes_ago(5)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        assert result.passed
        assert result.total_open == 1

    def test_pending_open_warning(self, tmp_db):
        """pending_open > 30 min → WARNING."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'pending_open', ?, ?)",
            (_minutes_ago(45), _minutes_ago(45)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        assert len(result.stuck) == 1
        assert result.stuck[0].severity == "warning"
        assert result.stuck[0].status == "pending_open"

    def test_pending_open_critical(self, tmp_db):
        """pending_open > 2 hours → CRITICAL."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'pending_open', ?, ?)",
            (_hours_ago(3), _hours_ago(3)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        assert result.has_critical
        assert result.stuck[0].severity == "critical"

    def test_pending_close_warning(self, tmp_db):
        """pending_close > 30 min → WARNING."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'pending_close', ?, ?)",
            (_minutes_ago(45), _minutes_ago(45)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        assert result.stuck[0].severity == "warning"

    def test_pending_close_critical(self, tmp_db):
        """pending_close > 2 hours → CRITICAL."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'pending_close', ?, ?)",
            (_hours_ago(3), _hours_ago(3)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        assert result.has_critical

    def test_needs_investigation_immediate_warning(self, tmp_db):
        """needs_investigation → warning immediately (threshold=0 min)."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'needs_investigation', ?, ?)",
            (_minutes_ago(5), _minutes_ago(5)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        # needs_investigation warns at 0 min, crits at 240 min
        assert len(result.stuck) == 1

    def test_needs_investigation_critical(self, tmp_db):
        """needs_investigation > 4 hours → CRITICAL."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'needs_investigation', ?, ?)",
            (_hours_ago(5), _hours_ago(5)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        assert result.has_critical
        assert result.stuck[0].severity == "critical"

    def test_open_no_management_24h(self, tmp_db):
        """Open trade with no update in 24h → WARNING."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'open', ?, ?)",
            (_days_ago(2), _days_ago(2)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        stale = [s for s in result.stuck if s.status == "open_no_management"]
        assert len(stale) == 1
        assert stale[0].severity == "warning"

    def test_multiple_stuck_trades(self, tmp_db):
        """Multiple stuck trades in different states."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'pending_open', 1, ?, ?, ?)",
            (_hours_ago(3), _hours_ago(3), _hours_ago(3)),
        )
        conn.execute(
            "INSERT INTO trades (id, ticker, status, contracts, entry_date, created_at, updated_at) "
            "VALUES ('t2', 'AAPL', 'pending_close', 1, ?, ?, ?)",
            (_hours_ago(1), _hours_ago(1), _hours_ago(1)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        assert len(result.stuck) == 2
        severities = {s.severity for s in result.stuck}
        assert "critical" in severities  # pending_open 3h > 2h threshold
        assert "warning" in severities   # pending_close 1h > 30m threshold

    def test_closed_trades_not_flagged(self, tmp_db):
        """Closed trades should not appear in lifecycle check."""
        from sentinel.runtime import check_position_lifecycle

        db_path, conn = tmp_db
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO trades (id, ticker, status, created_at, updated_at) "
            "VALUES ('t1', 'SPY', 'closed_profit', ?, ?)",
            (_days_ago(10), _days_ago(10)),
        )
        conn.commit()

        with patch("sentinel.runtime._resolve_db_path", return_value=Path(db_path)):
            result = check_position_lifecycle("EXP-400", now=now)

        assert result.passed


# ===========================================================================
# Lifecycle helper: _minutes_since
# ===========================================================================

class TestMinutesSince:
    def test_valid_timestamp(self):
        from sentinel.runtime import _minutes_since
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(minutes=45)).isoformat()
        result = _minutes_since(ts, now)
        assert abs(result - 45.0) < 1.0  # within 1 minute precision

    def test_none_input(self):
        from sentinel.runtime import _minutes_since
        assert _minutes_since(None, datetime.now(timezone.utc)) is None

    def test_invalid_string(self):
        from sentinel.runtime import _minutes_since
        assert _minutes_since("not-a-date", datetime.now(timezone.utc)) is None


# ===========================================================================
# Unified entry point: post_scan_check
# ===========================================================================

class TestPostScanCheck:
    """Test the unified post_scan_check() entry point."""

    def test_disabled_skips_all(self):
        """runtime_gates_enabled=False skips everything."""
        from sentinel.runtime import post_scan_check

        result = post_scan_check(
            "EXP-400", "/fake/db", {},
            runtime_gates_enabled=False,
        )
        assert result.get("gates_skipped") is True

    def test_skip_specific_gates(self, tmp_db, base_config):
        """skip_gates=[6, 7] skips those two gates."""
        from sentinel.runtime import post_scan_check

        db_path, _ = tmp_db

        with patch("sentinel.runtime._resolve_db_path") as mock_resolve, \
             patch("sentinel.runtime._get_baseline", return_value=None):
            mock_resolve.return_value = Path(db_path)
            result = post_scan_check(
                "EXP-400", db_path, base_config,
                account_equity=100000.0,
                alpaca_positions=[{"symbol": "SPY"}],
                skip_gates=[6, 7],
            )

        assert "gate6" not in result
        assert "gate7" not in result

    def test_gate6_not_run_without_equity(self, tmp_db, base_config):
        """Gate 6 requires account_equity."""
        from sentinel.runtime import post_scan_check

        db_path, _ = tmp_db

        with patch("sentinel.runtime._resolve_db_path") as mock_resolve, \
             patch("sentinel.runtime._get_baseline", return_value=None):
            mock_resolve.return_value = Path(db_path)
            result = post_scan_check(
                "EXP-400", db_path, base_config,
                # account_equity not provided
            )

        assert "gate6" not in result

    def test_gate7_not_run_without_positions(self, tmp_db, base_config):
        """Gate 7 requires alpaca_positions."""
        from sentinel.runtime import post_scan_check

        db_path, _ = tmp_db

        with patch("sentinel.runtime._resolve_db_path") as mock_resolve, \
             patch("sentinel.runtime._get_baseline", return_value=None):
            mock_resolve.return_value = Path(db_path)
            result = post_scan_check(
                "EXP-400", db_path, base_config,
                # alpaca_positions not provided
            )

        assert "gate7" not in result


# ===========================================================================
# Format helpers
# ===========================================================================

class TestFormatters:
    def test_format_age_minutes(self):
        from sentinel.runtime import _format_age
        assert _format_age(30) == "30m"

    def test_format_age_hours(self):
        from sentinel.runtime import _format_age
        assert _format_age(180) == "3.0h"

    def test_format_age_days(self):
        from sentinel.runtime import _format_age
        assert _format_age(2880) == "2.0d"

    def test_format_drift_report_empty(self):
        from sentinel.runtime import format_drift_report
        text = format_drift_report({})
        assert "No active experiments" in text

    def test_format_drift_report_clean(self):
        from sentinel.runtime import format_drift_report
        text = format_drift_report({"EXP-400": []})
        assert "EXP-400" in text
        assert "✅" in text

    def test_format_lifecycle_report_clean(self):
        from sentinel.runtime import format_lifecycle_report, LifecycleResult
        results = {"EXP-400": LifecycleResult(exp_id="EXP-400", total_open=5)}
        text = format_lifecycle_report(results)
        assert "EXP-400" in text
        assert "healthy" in text


# ===========================================================================
# Edge cases and error handling
# ===========================================================================

class TestEdgeCases:
    def test_gate6_with_no_open_trades(self, tmp_db, base_config):
        """No open trades → 0 checked, no errors."""
        from sentinel.runtime import check_trade_sizing

        db_path, _ = tmp_db
        result = check_trade_sizing("EXP-400", 100000.0, base_config, db_path=db_path)
        assert result.trades_checked == 0
        assert result.passed

    def test_gate7_empty_alpaca(self, tmp_db):
        """Empty Alpaca positions + open DB trades with trade_legs → ghosts."""
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        spy_occ = _occ("SPY")
        conn.execute(
            "INSERT INTO trades (id, ticker, status) VALUES ('t1', 'SPY', 'open')"
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol) "
            "VALUES ('t1', 'short', ?)", (spy_occ,)
        )
        conn.commit()

        result = check_orphan_positions("EXP-400", [], db_path=db_path)
        assert len(result.ghosts) >= 1

    def test_gate7_empty_db(self, tmp_db):
        """Open Alpaca OCC positions + empty DB → all are orphans."""
        from sentinel.runtime import check_orphan_positions

        db_path, _ = tmp_db
        spy_occ = _occ("SPY")
        aapl_occ = _occ("AAPL")
        result = check_orphan_positions(
            "EXP-400",
            [{"symbol": spy_occ}, {"symbol": aapl_occ}],
            db_path=db_path,
        )
        assert len(result.orphans) == 2

    def test_runtime_metrics_zero_trades(self):
        """RuntimeMetrics with no data."""
        from sentinel.runtime import RuntimeMetrics
        m = RuntimeMetrics(exp_id="EXP-400", window_size=0)
        assert m.win_rate is None
        assert m.avg_loss is None

    def test_drift_alert_dataclass(self):
        """DriftAlert stores all fields correctly."""
        from sentinel.runtime import DriftAlert
        a = DriftAlert(
            exp_id="EXP-400", metric="win_rate", severity="warning",
            message="test", live_value=60.0, baseline_value=78.0,
        )
        assert a.low_confidence is False
        assert a.exp_id == "EXP-400"
