"""Tests for VIX/VIX3M data pipeline and regime integration."""
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.fetch_vix_data import (
    _init_db,
    _upsert_rows,
    load_vix3m_from_cache,
    load_vix_from_cache,
)


class TestVixDailySchema:
    """Test DB schema creation and upserts."""

    def test_init_creates_table(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = _init_db(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "vix_daily" in table_names
        conn.close()

    def test_upsert_inserts_new_rows(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = _init_db(db_path)
        rows = [
            {"date": "2024-01-02", "vix_close": 13.2, "vix3m_close": 14.5},
            {"date": "2024-01-03", "vix_close": 14.1, "vix3m_close": 15.0},
        ]
        written = _upsert_rows(conn, rows)
        assert written == 2
        result = conn.execute("SELECT COUNT(*) FROM vix_daily").fetchone()[0]
        assert result == 2
        conn.close()

    def test_upsert_merges_partial_data(self, tmp_path):
        """VIX and VIX3M fetched separately should merge on upsert."""
        db_path = tmp_path / "test.db"
        conn = _init_db(db_path)

        # First: VIX only
        _upsert_rows(conn, [{"date": "2024-01-02", "vix_close": 13.2, "vix3m_close": None}])
        # Second: VIX3M only
        _upsert_rows(conn, [{"date": "2024-01-02", "vix_close": None, "vix3m_close": 14.5}])

        row = conn.execute("SELECT vix_close, vix3m_close FROM vix_daily WHERE date='2024-01-02'").fetchone()
        assert row[0] == 13.2  # VIX preserved
        assert row[1] == 14.5  # VIX3M merged
        conn.close()

    def test_upsert_overwrites_on_conflict(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = _init_db(db_path)
        _upsert_rows(conn, [{"date": "2024-01-02", "vix_close": 13.2, "vix3m_close": 14.5}])
        _upsert_rows(conn, [{"date": "2024-01-02", "vix_close": 15.0, "vix3m_close": 16.0}])
        row = conn.execute("SELECT vix_close, vix3m_close FROM vix_daily WHERE date='2024-01-02'").fetchone()
        assert row[0] == 15.0
        assert row[1] == 16.0
        conn.close()

    def test_empty_upsert(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = _init_db(db_path)
        written = _upsert_rows(conn, [])
        assert written == 0
        conn.close()


class TestCacheLoaders:
    """Test load_vix3m_from_cache and load_vix_from_cache."""

    def test_load_vix3m_from_populated_db(self, tmp_path):
        import pandas as pd
        db_path = tmp_path / "test.db"
        conn = _init_db(db_path)
        _upsert_rows(conn, [
            {"date": "2024-01-02", "vix_close": 13.2, "vix3m_close": 14.5},
            {"date": "2024-01-03", "vix_close": 14.1, "vix3m_close": 15.0},
            {"date": "2024-01-04", "vix_close": 12.0, "vix3m_close": None},  # no VIX3M
        ])
        conn.close()

        result = load_vix3m_from_cache(db_path)
        assert len(result) == 2  # only rows with VIX3M
        assert result[pd.Timestamp("2024-01-02")] == 14.5
        assert result[pd.Timestamp("2024-01-03")] == 15.0

    def test_load_vix_from_populated_db(self, tmp_path):
        import pandas as pd
        db_path = tmp_path / "test.db"
        conn = _init_db(db_path)
        _upsert_rows(conn, [
            {"date": "2024-01-02", "vix_close": 13.2, "vix3m_close": 14.5},
        ])
        conn.close()

        result = load_vix_from_cache(db_path)
        assert len(result) == 1
        assert result[pd.Timestamp("2024-01-02")] == 13.2

    def test_load_from_missing_db(self, tmp_path):
        result = load_vix3m_from_cache(tmp_path / "nonexistent.db")
        assert result == {}

    def test_load_from_empty_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = _init_db(db_path)
        conn.close()
        result = load_vix3m_from_cache(db_path)
        assert result == {}


class TestVixStructureSignal:
    """Test that VIX3M data enables the vix_structure signal in ComboRegimeDetector."""

    def _make_price_data(self, n_days=250, base_price=500.0, trend=0.0):
        """Create synthetic SPY price data."""
        import pandas as pd
        dates = pd.bdate_range(end="2024-06-01", periods=n_days)
        prices = [base_price + trend * i for i in range(n_days)]
        return pd.DataFrame({"Close": prices}, index=dates)

    def test_vix_structure_abstains_without_vix3m(self):
        """Without VIX3M data, vix_structure abstains → bear can't fire."""
        from compass.regime import ComboRegimeDetector
        import pandas as pd

        config = {
            "signals": ["price_vs_ma200", "rsi_momentum", "vix_structure"],
            "ma_slow_period": 80,
            "rsi_bull_threshold": 55.0,
            "rsi_bear_threshold": 45.0,
            "bear_requires_unanimous": True,
            "cooldown_days": 0,
        }
        detector = ComboRegimeDetector(config)

        # Strongly bearish price data (declining)
        price_data = self._make_price_data(250, base_price=600, trend=-1.0)
        vix_by_date = {d: 30.0 for d in price_data.index}

        regimes = detector.compute_regime_series(price_data, vix_by_date, vix3m_by_date=None)
        last_regime = list(regimes.values())[-1]

        # Without VIX3M, vix_structure abstains → only 2/3 bear votes → NOT unanimous → neutral
        assert last_regime == "neutral", (
            f"Expected neutral (vix_structure should abstain), got {last_regime}"
        )

    def test_vix_structure_votes_with_vix3m(self):
        """With VIX3M data showing backwardation, vix_structure votes BEAR → unanimous."""
        from compass.regime import ComboRegimeDetector
        import pandas as pd

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
        detector = ComboRegimeDetector(config)

        # Strongly bearish price + high VIX + backwardation (VIX > VIX3M)
        price_data = self._make_price_data(250, base_price=600, trend=-1.0)
        vix_by_date = {d: 35.0 for d in price_data.index}
        vix3m_by_date = {d: 28.0 for d in price_data.index}  # VIX/VIX3M = 1.25 > 1.05 → BEAR

        regimes = detector.compute_regime_series(price_data, vix_by_date, vix3m_by_date)
        last_regime = list(regimes.values())[-1]

        assert last_regime == "bear", (
            f"Expected bear (all 3 signals should vote bear), got {last_regime}"
        )

    def test_contango_votes_bull(self):
        """With contango (VIX < VIX3M), vix_structure votes BULL."""
        from compass.regime import ComboRegimeDetector
        import pandas as pd

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
        detector = ComboRegimeDetector(config)

        # Bullish price + low VIX + contango (VIX < VIX3M)
        price_data = self._make_price_data(250, base_price=400, trend=1.0)
        vix_by_date = {d: 14.0 for d in price_data.index}
        vix3m_by_date = {d: 18.0 for d in price_data.index}  # VIX/VIX3M = 0.78 < 0.95 → BULL

        regimes = detector.compute_regime_series(price_data, vix_by_date, vix3m_by_date)
        last_regime = list(regimes.values())[-1]

        assert last_regime == "bull", (
            f"Expected bull (all 3 signals should vote bull), got {last_regime}"
        )


class TestScannerVix3mIntegration:
    """Test that the scanner's _detect_regime uses VIX3M when available."""

    @pytest.fixture(autouse=True)
    def _import_scanner(self):
        """Import _detect_regime with sentinel guard mocked out."""
        # exp700_ml_scanner calls pre_scan_check at import time — mock it
        with patch("sentinel.guards.pre_scan_check"):
            sys.path.insert(0, str(ROOT / "scripts"))
            # Force reimport if already cached with sentinel blocking
            if "exp700_ml_scanner" in sys.modules:
                del sys.modules["exp700_ml_scanner"]
            import exp700_ml_scanner
            self._detect_regime = exp700_ml_scanner._detect_regime

    def test_detect_regime_uses_real_vix3m(self):
        """When vix3m is in vix_feats, it should use real ratio, not proxy."""
        import pandas as pd

        # Create synthetic SPY data - bearish
        dates = pd.bdate_range(end="2024-06-01", periods=120)
        spy_df = pd.DataFrame({
            "Close": [550 - i * 0.5 for i in range(120)],
            "High": [551 - i * 0.5 for i in range(120)],
            "Low": [549 - i * 0.5 for i in range(120)],
        }, index=dates)

        regime_config = {
            "signals": ["price_vs_ma200", "rsi_momentum", "vix_structure"],
            "ma_slow_period": 80,
            "rsi_bull_threshold": 55.0,
            "rsi_bear_threshold": 45.0,
            "vix_structure_bull": 0.95,
            "vix_structure_bear": 1.05,
            "bear_requires_unanimous": True,
        }

        # With VIX3M showing backwardation → bear vote
        vix_feats = {
            "vix": 35.0,
            "vix3m": 28.0,  # ratio = 1.25 > 1.05 → bear
            "vix_percentile_20d": 50.0,
            "vix_percentile_50d": 50.0,
            "vix_percentile_100d": 50.0,
        }
        regime = self._detect_regime(spy_df, vix_feats, regime_config)
        assert regime == "bear", f"Expected bear with VIX3M backwardation, got {regime}"

    def test_detect_regime_falls_back_to_proxy(self):
        """Without vix3m in vix_feats, should use percentile proxy."""
        import pandas as pd

        dates = pd.bdate_range(end="2024-06-01", periods=120)
        spy_df = pd.DataFrame({
            "Close": [500] * 120,
            "High": [501] * 120,
            "Low": [499] * 120,
        }, index=dates)

        regime_config = {
            "ma_slow_period": 80,
            "rsi_bull_threshold": 55.0,
            "rsi_bear_threshold": 45.0,
            "bear_requires_unanimous": True,
        }

        vix_feats = {
            "vix": 20.0,
            "vix_percentile_50d": 50.0,
        }
        # Should not crash — proxy path works
        regime = self._detect_regime(spy_df, vix_feats, regime_config)
        assert regime in ("bull", "bear", "neutral")
