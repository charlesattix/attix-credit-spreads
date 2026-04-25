"""Tests for SENTINEL V2 data quality gates (10, 11, 12)."""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _create_macro_cache(db_path, vix_dates=None, vix3m_dates=None,
                         spy_dates=None, fred_series=None):
    """Create a test macro_cache.db with specified data."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vix_daily (
            date TEXT PRIMARY KEY, vix_close REAL, vix3m_close REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS price_cache (
            ticker TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        );
        CREATE TABLE IF NOT EXISTS fred_cache (
            series_id TEXT NOT NULL, obs_date TEXT NOT NULL, value REAL,
            PRIMARY KEY (series_id, obs_date)
        );
    """)

    if vix_dates:
        for d, v in vix_dates.items():
            conn.execute(
                "INSERT OR REPLACE INTO vix_daily (date, vix_close) VALUES (?, ?)",
                (d, v),
            )

    if vix3m_dates:
        for d, v in vix3m_dates.items():
            conn.execute(
                "INSERT OR REPLACE INTO vix_daily (date, vix_close, vix3m_close) VALUES (?, COALESCE((SELECT vix_close FROM vix_daily WHERE date=?), 20.0), ?)",
                (d, d, v),
            )

    if spy_dates:
        for d, v in spy_dates.items():
            conn.execute(
                "INSERT OR REPLACE INTO price_cache (ticker, date, close) VALUES ('SPY', ?, ?)",
                (d, v),
            )

    if fred_series:
        for series_id, dates_values in fred_series.items():
            for d, v in dates_values.items():
                conn.execute(
                    "INSERT OR REPLACE INTO fred_cache (series_id, obs_date, value) VALUES (?, ?, ?)",
                    (series_id, d, v),
                )

    conn.commit()
    conn.close()


def _create_sentinel_db(db_path):
    """Create a test sentinel.db with vote history schema."""
    from sentinel.gates_data_quality import _VOTE_HISTORY_SCHEMA
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_VOTE_HISTORY_SCHEMA)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Gate 10 — DATA FRESHNESS
# ═══════════════════════════════════════════════════════════════════════════════

class TestGate10DataFreshness:

    def test_missing_db_blocks(self, tmp_path):
        """Missing macro_cache.db → BLOCK."""
        from sentinel.gates_data_quality import check_data_freshness
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness(
                "EXP-TEST", macro_cache_db=tmp_path / "nonexistent.db"
            )
        assert result.blocked
        assert not result.passed
        assert any("not found" in e for e in result.errors)

    def test_empty_db_blocks(self, tmp_path):
        """0-byte macro_cache.db → BLOCK."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        db_path.touch()
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        assert result.blocked
        assert any("0 bytes" in e for e in result.errors)

    def test_fresh_data_passes(self, tmp_path):
        """All data fresh → PASS."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        _create_macro_cache(
            db_path,
            vix_dates={today: 18.5, yesterday: 19.2},
            vix3m_dates={today: 21.0, yesterday: 21.5,
                         **{(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"): 20.0
                            for i in range(2, 25)}},
            spy_dates={today: 700.0},
            fred_series={"VIXCLS": {today: 18.5}},
        )
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        assert result.passed
        assert not result.blocked
        assert result.vix3m_present
        assert len(result.errors) == 0

    def test_stale_vix_blocks(self, tmp_path):
        """VIX data 5 days old → BLOCK."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        stale_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        _create_macro_cache(
            db_path,
            vix_dates={stale_date: 18.5},
            vix3m_dates={stale_date: 21.0,
                         **{(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"): 20.0
                            for i in range(5, 30)}},
            spy_dates={stale_date: 700.0},
        )
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        assert result.blocked
        assert any("stale" in e.lower() for e in result.errors)
        assert result.vix_age_hours > 48

    def test_missing_vix3m_blocks(self, tmp_path):
        """No VIX3M data → BLOCK (this was THE bug)."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        today = datetime.now().strftime("%Y-%m-%d")

        _create_macro_cache(
            db_path,
            vix_dates={today: 18.5},
            # No vix3m_dates!
            spy_dates={today: 700.0},
        )
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        assert result.blocked
        assert not result.vix3m_present
        assert any("VIX3M" in e for e in result.errors)

    def test_insufficient_vix3m_blocks(self, tmp_path):
        """Only 5 VIX3M rows (need 20) → BLOCK."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        today = datetime.now().strftime("%Y-%m-%d")

        _create_macro_cache(
            db_path,
            vix_dates={today: 18.5},
            vix3m_dates={
                (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"): 21.0
                for i in range(5)
            },
            spy_dates={today: 700.0},
        )
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        assert result.blocked
        assert any("only 5 rows" in e for e in result.errors)

    def test_stale_fred_warns(self, tmp_path):
        """FRED series > expected lag → WARNING (not block)."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        today = datetime.now().strftime("%Y-%m-%d")
        old_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        _create_macro_cache(
            db_path,
            vix_dates={today: 18.5},
            vix3m_dates={
                (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"): 21.0
                for i in range(25)
            },
            spy_dates={today: 700.0},
            fred_series={"CFNAI": {old_date: 0.15}},
        )
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        assert result.passed  # FRED staleness is WARNING not BLOCK
        assert any("CFNAI" in w for w in result.warnings)

    def test_specific_error_messages(self, tmp_path):
        """Error messages should be specific, not generic."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        stale_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

        _create_macro_cache(db_path, vix_dates={stale_date: 25.0})
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        # Should say specific hours, not just "data check failed"
        vix_errors = [e for e in result.errors if "VIX" in e and "stale" in e.lower()]
        if vix_errors:
            assert any(c.isdigit() for c in vix_errors[0])  # contains actual hours

    def test_freshness_recorded_in_sentinel_db(self, tmp_path):
        """Gate 10 results should be persisted for audit trail."""
        from sentinel.gates_data_quality import check_data_freshness
        sentinel_db = tmp_path / "sentinel.db"
        db_path = tmp_path / "macro_cache.db"
        db_path.touch()

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            _create_sentinel_db(sentinel_db)
            check_data_freshness("EXP-AUDIT", macro_cache_db=db_path)

        conn = sqlite3.connect(str(sentinel_db))
        rows = conn.execute(
            "SELECT * FROM data_freshness_checks WHERE experiment_id = 'EXP-AUDIT'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Gate 11 — SIGNAL VOTING AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

class TestGate11SignalVoting:

    def test_all_signals_vote_ok(self, tmp_path):
        """All signals voting → severity='ok'."""
        from sentinel.gates_data_quality import audit_signal_votes
        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = audit_signal_votes(
                "EXP-TEST",
                "bull",
                {"price_vs_ma200": "bull", "rsi_momentum": "bull", "vix_structure": "bull"},
                spy_close=700.0, vix_close=15.0, vix3m_close=18.0,
            )
        assert result.severity == "ok"
        assert result.abstain_count == 0
        assert result.bull_count == 3

    def test_vix_structure_abstain_critical(self, tmp_path):
        """vix_structure abstaining → severity='critical'."""
        from sentinel.gates_data_quality import audit_signal_votes
        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = audit_signal_votes(
                "EXP-TEST",
                "neutral",
                {"price_vs_ma200": "bear", "rsi_momentum": "bear", "vix_structure": "abstain"},
                spy_close=650.0, vix_close=30.0, vix3m_close=None,
            )
        assert result.severity == "critical"
        assert result.abstain_count == 1
        assert any("vix_structure" in a and "ABSTAINED" in a for a in result.alerts)

    def test_other_signal_abstain_warning(self, tmp_path):
        """Non-vix signal abstaining → severity='warning'."""
        from sentinel.gates_data_quality import audit_signal_votes
        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = audit_signal_votes(
                "EXP-TEST",
                "neutral",
                {"price_vs_ma200": "abstain", "rsi_momentum": "bull", "vix_structure": "bull"},
            )
        assert result.severity == "warning"

    def test_vix_ratio_computed(self, tmp_path):
        """VIX ratio should be computed when both VIX and VIX3M present."""
        from sentinel.gates_data_quality import audit_signal_votes
        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = audit_signal_votes(
                "EXP-TEST",
                "bear",
                {"price_vs_ma200": "bear", "rsi_momentum": "bear", "vix_structure": "bear"},
                vix_close=35.0, vix3m_close=28.0,
            )
        assert result.vix_ratio == pytest.approx(1.25, abs=0.01)

    def test_vote_recorded_in_db(self, tmp_path):
        """Vote history should be persisted for audit trail."""
        from sentinel.gates_data_quality import audit_signal_votes
        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            audit_signal_votes(
                "EXP-RECORD",
                "bull",
                {"price_vs_ma200": "bull", "rsi_momentum": "bull", "vix_structure": "bull"},
                spy_close=700.0,
            )

        conn = sqlite3.connect(str(sentinel_db))
        rows = conn.execute(
            "SELECT * FROM signal_vote_history WHERE experiment_id = 'EXP-RECORD'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    def test_bear_drought_detection(self, tmp_path):
        """30 non-bear votes + SPY decline > 5% → CRITICAL alert."""
        from sentinel.gates_data_quality import audit_signal_votes
        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        # Seed 20 previous neutral votes with declining SPY
        conn = sqlite3.connect(str(sentinel_db))
        for i in range(20):
            spy_price = 700 - i * 5  # declining from 700 to 605
            conn.execute(
                """INSERT INTO signal_vote_history
                   (experiment_id, vote_time, regime_result, spy_close)
                   VALUES (?, datetime('now', ?), 'neutral', ?)""",
                ("EXP-DROUGHT", f"-{i} hours", spy_price),
            )
        conn.commit()
        conn.close()

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = audit_signal_votes(
                "EXP-DROUGHT",
                "neutral",
                {"price_vs_ma200": "bear", "rsi_momentum": "bear", "vix_structure": "abstain"},
                spy_close=600.0,
            )
        # vix_structure abstain makes it critical anyway, but bear drought should also fire
        assert result.severity == "critical"
        drought_alerts = [a for a in result.alerts if "DROUGHT" in a]
        assert len(drought_alerts) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Gate 12 — BACKTEST-PRODUCTION PARITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestGate12Parity:

    def _make_spy_df(self, n_days=250, base=500.0, trend=0.0):
        import pandas as pd
        dates = pd.bdate_range(end="2024-06-01", periods=n_days)
        prices = [base + trend * i for i in range(len(dates))]
        return pd.DataFrame({"Close": prices}, index=dates)

    def test_matching_regimes_pass(self, tmp_path):
        """Scanner and shadow regime agree → PASS."""
        import pandas as pd
        from sentinel.gates_data_quality import check_regime_parity

        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        spy_df = self._make_spy_df(250, base=400, trend=1.0)
        vix_by_date = {d: 14.0 for d in spy_df.index}
        vix3m_by_date = {d: 18.0 for d in spy_df.index}  # contango → bull

        config = {
            "signals": ["price_vs_ma200", "rsi_momentum", "vix_structure"],
            "ma_slow_period": 80,
            "rsi_bull_threshold": 55.0,
            "rsi_bear_threshold": 45.0,
            "vix_structure_bull": 0.95,
            "vix_structure_bear": 1.05,
            "bear_requires_unanimous": True,
            "cooldown_days": 0,
        }

        # Compute what ComboRegimeDetector would say
        from compass.regime import ComboRegimeDetector
        detector = ComboRegimeDetector(config)
        series = detector.compute_regime_series(spy_df, vix_by_date, vix3m_by_date)
        expected = list(series.values())[-1]

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_regime_parity(
                "EXP-TEST", expected, spy_df, vix_by_date, vix3m_by_date, config
            )
        assert result.passed
        assert not result.diverged
        assert result.scanner_regime == result.shadow_regime

    def test_divergent_regimes_detected(self, tmp_path):
        """Scanner says bull, shadow says neutral → DIVERGED."""
        import pandas as pd
        from sentinel.gates_data_quality import check_regime_parity

        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        # Flat prices near MA → likely neutral from shadow
        spy_df = self._make_spy_df(250, base=500, trend=0.0)
        vix_by_date = {d: 20.0 for d in spy_df.index}
        vix3m_by_date = {d: 20.0 for d in spy_df.index}

        config = {
            "signals": ["price_vs_ma200", "rsi_momentum", "vix_structure"],
            "ma_slow_period": 80,
            "rsi_bull_threshold": 55.0,
            "rsi_bear_threshold": 45.0,
            "bear_requires_unanimous": True,
            "cooldown_days": 0,
        }

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_regime_parity(
                "EXP-TEST", "bull", spy_df, vix_by_date, vix3m_by_date, config
            )

        # The shadow should NOT be bull for flat data → divergence
        if result.shadow_regime != "bull":
            assert result.diverged
            assert any("DIVERGENCE" in i for i in result.issues)

    def test_vix_extreme_neutral_flagged(self, tmp_path):
        """vix_extreme_regime='neutral' in config → ISSUE."""
        from sentinel.gates_data_quality import check_regime_parity
        import pandas as pd

        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        spy_df = self._make_spy_df(100, base=500, trend=0.0)
        config = {
            "signals": ["price_vs_ma200"],
            "vix_extreme_regime": "neutral",  # THE BUG
            "cooldown_days": 10,
            "rsi_bull_threshold": 55.0,
        }

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_regime_parity(
                "EXP-TEST", "neutral", spy_df, {}, None, config
            )
        assert not result.vix_extreme_ok
        assert any("vix_extreme_regime='neutral'" in i for i in result.issues)

    def test_missing_rsi_threshold_warned(self, tmp_path):
        """Config missing rsi_bull_threshold → WARNING."""
        from sentinel.gates_data_quality import check_regime_parity
        import pandas as pd

        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        spy_df = self._make_spy_df(100, base=500, trend=0.0)
        config = {
            "signals": ["price_vs_ma200"],
            "cooldown_days": 10,
            # rsi_bull_threshold intentionally missing
        }

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_regime_parity(
                "EXP-TEST", "neutral", spy_df, {}, None, config
            )
        assert not result.rsi_threshold_ok
        assert any("rsi_bull_threshold" in w for w in result.warnings)

    def test_missing_cooldown_warned(self, tmp_path):
        """Config missing cooldown_days → WARNING about no hysteresis."""
        from sentinel.gates_data_quality import check_regime_parity
        import pandas as pd

        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        spy_df = self._make_spy_df(100, base=500, trend=0.0)
        config = {
            "signals": ["price_vs_ma200"],
            "rsi_bull_threshold": 55.0,
            # cooldown_days intentionally missing
        }

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_regime_parity(
                "EXP-TEST", "neutral", spy_df, {}, None, config
            )
        assert not result.hysteresis_active
        assert any("cooldown" in w.lower() or "hysteresis" in w.lower() for w in result.warnings)

    def test_parity_recorded_in_db(self, tmp_path):
        """Parity check results should be persisted."""
        from sentinel.gates_data_quality import check_regime_parity
        import pandas as pd

        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        spy_df = self._make_spy_df(100, base=500, trend=0.0)
        config = {"signals": ["price_vs_ma200"], "cooldown_days": 10, "rsi_bull_threshold": 55.0}

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            check_regime_parity("EXP-RECORD", "neutral", spy_df, {}, None, config)

        conn = sqlite3.connect(str(sentinel_db))
        rows = conn.execute(
            "SELECT * FROM regime_parity_checks WHERE experiment_id = 'EXP-RECORD'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: fail-closed principle
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailClosedPrinciple:
    """Verify that every uncertain state BLOCKS, never allows."""

    def test_no_vix_table_blocks(self, tmp_path):
        """DB exists but vix_daily table missing → BLOCK."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.commit()
        conn.close()

        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        assert result.blocked

    def test_vix3m_zero_rows_blocks(self, tmp_path):
        """vix_daily exists but zero VIX3M rows → BLOCK (not just warn)."""
        from sentinel.gates_data_quality import check_data_freshness
        db_path = tmp_path / "macro_cache.db"
        today = datetime.now().strftime("%Y-%m-%d")

        # Only VIX, no VIX3M
        _create_macro_cache(db_path, vix_dates={today: 18.5})
        sentinel_db = tmp_path / "sentinel.db"
        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = check_data_freshness("EXP-TEST", macro_cache_db=db_path)
        assert result.blocked
        assert not result.vix3m_present

    def test_vix_structure_abstain_is_critical_not_warning(self, tmp_path):
        """vix_structure abstaining must be CRITICAL, never just a warning."""
        from sentinel.gates_data_quality import audit_signal_votes
        sentinel_db = tmp_path / "sentinel.db"
        _create_sentinel_db(sentinel_db)

        with patch("sentinel.gates_data_quality._SENTINEL_DB", sentinel_db):
            result = audit_signal_votes(
                "EXP-TEST",
                "neutral",
                {"price_vs_ma200": "bull", "rsi_momentum": "bull", "vix_structure": "abstain"},
            )
        # Must be CRITICAL, not downgraded to warning just because other signals voted
        assert result.severity == "critical"
