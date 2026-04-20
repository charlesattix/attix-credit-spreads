"""
Tests for EXP-800 production-readiness fixes.

Covers:
  - Market hours guard (_is_market_hours)
  - Expired position cleanup (_cleanup_expired_positions)
  - Open position count + consumed margin
  - Kelly sizer buying power check
  - VIX/RSI None handling in regime detection
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The scanner runs pre_scan_check() at import time which calls sys.exit(1)
# when EXP-800 is halted. Patch it to no-op for testing.
with patch("sentinel.guards.pre_scan_check"):
    from scripts.exp800_safe_kelly_scanner import (
        _is_market_hours,
        _cleanup_expired_positions,
        _get_open_position_count,
        _get_consumed_margin,
        _detect_regime,
        _compute_vix_features,
        _size_contracts,
    )


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------

def _create_exp800_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            ticker TEXT DEFAULT 'SPY',
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
            source TEXT DEFAULT 'scanner',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def _insert_trade(conn, trade_id, status="open", expiration="2026-05-01",
                  strategy_type="bull_put", short_strike=666, long_strike=654, contracts=5):
    conn.execute(
        "INSERT INTO trades (id, status, expiration, strategy_type, short_strike, long_strike, contracts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (trade_id, status, expiration, strategy_type, short_strike, long_strike, contracts),
    )
    conn.commit()


@pytest.fixture
def tmp_db(tmp_path):
    db_file = str(tmp_path / "exp800_test.db")
    conn = _create_exp800_db(db_file)
    conn.close()
    return Path(db_file)


# ===========================================================================
# Market Hours Guard
# ===========================================================================


class TestMarketHoursGuard:

    @patch("scripts.exp800_safe_kelly_scanner.datetime")
    def test_blocks_premarket(self, mock_dt):
        """9:15 AM ET should be blocked."""
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        # Wednesday 9:15 AM ET
        mock_dt.now.return_value = datetime(2026, 4, 22, 9, 15, tzinfo=et)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        ok, reason = _is_market_hours()
        assert not ok
        assert "pre_market" in reason

    @patch("scripts.exp800_safe_kelly_scanner.datetime")
    def test_allows_10am(self, mock_dt):
        """10:00 AM ET should be allowed."""
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        mock_dt.now.return_value = datetime(2026, 4, 22, 10, 0, tzinfo=et)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        ok, reason = _is_market_hours()
        assert ok

    @patch("scripts.exp800_safe_kelly_scanner.datetime")
    def test_blocks_weekend(self, mock_dt):
        """Saturday should be blocked."""
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        # Saturday 11:00 AM ET
        mock_dt.now.return_value = datetime(2026, 4, 25, 11, 0, tzinfo=et)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        ok, reason = _is_market_hours()
        assert not ok
        assert "weekend" in reason


# ===========================================================================
# Expired Position Cleanup
# ===========================================================================


class TestExpiredPositionCleanup:

    def test_marks_expired_trades(self, tmp_db):
        """Trades past expiration should be marked 'expired'."""
        conn = sqlite3.connect(str(tmp_db))
        _insert_trade(conn, "t1", status="open", expiration="2026-04-17")
        _insert_trade(conn, "t2", status="pending_open", expiration="2026-04-17")
        _insert_trade(conn, "t3", status="open", expiration="2026-05-01")  # not expired
        conn.close()

        count = _cleanup_expired_positions(tmp_db)
        assert count == 2

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT id, status, exit_date FROM trades ORDER BY id").fetchall()
        conn.close()

        assert rows[0] == ("t1", "expired", "2026-04-17")
        assert rows[1] == ("t2", "expired", "2026-04-17")
        assert rows[2][1] == "open"  # t3 unchanged

    def test_no_expired(self, tmp_db):
        """No expired trades should return 0."""
        conn = sqlite3.connect(str(tmp_db))
        _insert_trade(conn, "t1", status="open", expiration="2099-12-31")
        conn.close()
        assert _cleanup_expired_positions(tmp_db) == 0

    def test_already_closed_ignored(self, tmp_db):
        """Closed trades should not be touched."""
        conn = sqlite3.connect(str(tmp_db))
        _insert_trade(conn, "t1", status="closed", expiration="2026-04-17")
        conn.close()
        assert _cleanup_expired_positions(tmp_db) == 0


# ===========================================================================
# Open Position Count + Consumed Margin
# ===========================================================================


class TestPositionTracking:

    def test_open_position_count(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        _insert_trade(conn, "t1", status="open")
        _insert_trade(conn, "t2", status="pending_open")
        _insert_trade(conn, "t3", status="closed")
        _insert_trade(conn, "t4", status="failed_open")
        conn.close()
        assert _get_open_position_count(tmp_db) == 2

    def test_consumed_margin(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        # 5 contracts × $12 width × 100 = $6,000
        _insert_trade(conn, "t1", status="open", short_strike=666, long_strike=654, contracts=5)
        # 10 contracts × $12 width × 100 = $12,000
        _insert_trade(conn, "t2", status="open", short_strike=672, long_strike=660, contracts=10)
        # closed — should NOT count
        _insert_trade(conn, "t3", status="closed", short_strike=650, long_strike=638, contracts=5)
        conn.close()
        margin = _get_consumed_margin(tmp_db, spread_width=12)
        assert margin == 18_000.0  # $6k + $12k

    def test_consumed_margin_empty(self, tmp_db):
        assert _get_consumed_margin(tmp_db, spread_width=12) == 0.0


# ===========================================================================
# VIX / RSI None Handling in Regime Detection
# ===========================================================================


class TestRegimeNoneHandling:

    def test_regime_none_when_vix_missing(self):
        """Regime should return None when VIX data is unavailable."""
        import pandas as pd
        spy_df = pd.DataFrame({"Close": list(range(100, 200)), "High": list(range(101, 201)),
                                "Low": list(range(99, 199))})
        vix_feats = {"vix": None}
        result = _detect_regime(spy_df, vix_feats, {})
        assert result is None

    def test_regime_none_when_data_insufficient(self):
        """Regime should return None when price data < 50 bars."""
        import pandas as pd
        spy_df = pd.DataFrame({"Close": [100] * 30})
        vix_feats = {"vix": 20}
        result = _detect_regime(spy_df, vix_feats, {})
        assert result is None

    def test_vix_features_none_when_empty(self):
        """VIX features should be None when data is empty."""
        import pandas as pd
        feats = _compute_vix_features(pd.DataFrame())
        assert feats["vix"] is None
        assert feats["vix_percentile_50d"] is None


# ===========================================================================
# Kelly Sizer
# ===========================================================================


class TestKellySizer:

    def test_size_contracts_basic(self):
        """Basic Kelly sizing: $100k × 7% = $7k risk, ($12 - $2) × 100 = $1k max loss → 7 contracts."""
        contracts = _size_contracts(
            equity=100_000, kelly_pct=7.0, spread_width=12,
            credit_per_share=2.0, max_contracts=25,
        )
        assert contracts == 7

    def test_size_contracts_respects_max(self):
        """Should not exceed max_contracts."""
        contracts = _size_contracts(
            equity=1_000_000, kelly_pct=9.0, spread_width=12,
            credit_per_share=2.0, max_contracts=25,
        )
        assert contracts == 25

    def test_size_contracts_minimum_one(self):
        """Should return at least 1 contract."""
        contracts = _size_contracts(
            equity=1_000, kelly_pct=1.0, spread_width=12,
            credit_per_share=2.0, max_contracts=25,
        )
        assert contracts == 1
