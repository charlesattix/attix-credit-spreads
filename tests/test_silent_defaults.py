"""
Tests for RSI/VIX/Regime silent-default fixes.

Verifies that missing RSI, VIX, or regime data blocks trading
instead of silently defaulting to neutral values.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.spread_strategy import CreditSpreadStrategy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_strategy(**overrides):
    """Create a minimal CreditSpreadStrategy for testing."""
    config = {
        "strategy": {
            "regime_mode": "combo",
            "spread_width": 5,
            "spread_width_high_iv": 15,
            "spread_width_low_iv": 10,
            "technical": {
                "use_trend_filter": False,
                "use_rsi_filter": True,
                "rsi_overbought": 55,
                "rsi_oversold": 45,
            },
            "iron_condor": {"enabled": False},
            **overrides,
        },
        "risk": {"max_risk_per_trade": 0.08},
    }
    with patch("strategy.spread_strategy.CreditSpreadStrategy.__init__", return_value=None):
        s = CreditSpreadStrategy.__new__(CreditSpreadStrategy)
    s.config = config
    s.strategy_params = config["strategy"]
    s.risk_params = config["risk"]
    s.regime_mode = config["strategy"].get("regime_mode", "combo")
    s._combo_regime_detector = None
    s.default_spread_width = 5
    s.spread_width_high_iv = 15
    s.spread_width_low_iv = 10
    return s


# ===========================================================================
# RSI = None blocks entries (Gate: spread_strategy.py)
# ===========================================================================


class TestRSINoneBlocksEntry:
    """Missing RSI should block all entry types."""

    def test_bull_entry_blocked_when_rsi_none(self):
        """_check_bullish_conditions returns False when RSI is missing."""
        s = _make_strategy()
        signals = {"rsi": None}
        iv_data = {"iv_rank": 30, "iv_percentile": 50}
        assert s._check_bullish_conditions(signals, iv_data) is False

    def test_bull_entry_blocked_when_rsi_absent(self):
        """_check_bullish_conditions returns False when RSI key missing."""
        s = _make_strategy()
        signals = {}  # no 'rsi' key at all
        iv_data = {"iv_rank": 30, "iv_percentile": 50}
        assert s._check_bullish_conditions(signals, iv_data) is False

    def test_bear_entry_blocked_when_rsi_none(self):
        """_check_bearish_conditions returns False when RSI is missing."""
        s = _make_strategy()
        signals = {"rsi": None}
        iv_data = {"iv_rank": 30, "iv_percentile": 50}
        assert s._check_bearish_conditions(signals, iv_data) is False

    def test_bear_entry_blocked_when_rsi_absent(self):
        """_check_bearish_conditions returns False when RSI key missing."""
        s = _make_strategy()
        signals = {}
        iv_data = {"iv_rank": 30, "iv_percentile": 50}
        assert s._check_bearish_conditions(signals, iv_data) is False

    def test_bull_entry_passes_with_valid_rsi(self):
        """_check_bullish_conditions passes with valid RSI below overbought."""
        s = _make_strategy()
        signals = {"rsi": 48}
        iv_data = {"iv_rank": 30, "iv_percentile": 50}
        assert s._check_bullish_conditions(signals, iv_data) is True

    def test_bear_entry_passes_with_valid_rsi(self):
        """_check_bearish_conditions passes with valid RSI above oversold."""
        s = _make_strategy()
        signals = {"rsi": 50}
        iv_data = {"iv_rank": 30, "iv_percentile": 50}
        assert s._check_bearish_conditions(signals, iv_data) is True

    def test_rsi_filter_disabled_allows_none(self):
        """When use_rsi_filter=False, missing RSI should not block."""
        s = _make_strategy()
        s.strategy_params["technical"]["use_rsi_filter"] = False
        signals = {}  # no RSI
        iv_data = {"iv_rank": 30, "iv_percentile": 50}
        assert s._check_bullish_conditions(signals, iv_data) is True
        assert s._check_bearish_conditions(signals, iv_data) is True


# ===========================================================================
# Iron Condor RSI check
# ===========================================================================


class TestIronCondorRSINone:
    """Iron condor filter should return empty list when RSI is missing."""

    def test_ic_blocked_when_rsi_none(self):
        """find_iron_condors returns [] when RSI is None."""
        s = _make_strategy()
        signals = {"rsi": None}
        import pandas as pd
        chain = pd.DataFrame()
        result = s.find_iron_condors("SPY", chain, 550.0, signals, iv_data={})
        assert result == []

    def test_ic_blocked_when_rsi_absent(self):
        """find_iron_condors returns [] when RSI key is missing."""
        s = _make_strategy()
        signals = {}
        import pandas as pd
        chain = pd.DataFrame()
        result = s.find_iron_condors("SPY", chain, 550.0, signals, iv_data={})
        assert result == []


# ===========================================================================
# Regime = None blocks trading (Gate: main.py)
# ===========================================================================


class TestRegimeNoneBlocks:
    """ComboRegimeDetector failure should set regime=None, not 'neutral'."""

    def test_combo_regime_none_falls_back_to_condition_checks(self):
        """When combo_regime is None, strategy falls back to condition checks."""
        s = _make_strategy()
        signals = {"combo_regime": None, "rsi": 50}
        iv_data = {"iv_rank": 30, "iv_percentile": 50}

        # With combo_regime=None, evaluate_spread_opportunity falls back to
        # _check_bullish/bearish_conditions (line 111-116 of spread_strategy.py)
        # This means RSI check applies — if RSI is also None, both return False
        combo_regime = signals.get("combo_regime")
        assert combo_regime is None

        # Falls through to condition checks — RSI=50 passes bull check
        assert s._check_bullish_conditions(signals, iv_data) is True

    def test_both_none_blocks_everything(self):
        """When both regime and RSI are None, no entries allowed."""
        s = _make_strategy()
        signals = {"combo_regime": None}  # no RSI either
        iv_data = {"iv_rank": 30, "iv_percentile": 50}

        combo_regime = signals.get("combo_regime")
        assert combo_regime is None
        assert s._check_bullish_conditions(signals, iv_data) is False
        assert s._check_bearish_conditions(signals, iv_data) is False


# ===========================================================================
# VIX = None in snapshot (Gate: snapshot_builder.py / live_snapshot.py)
# ===========================================================================


class TestVIXNoneInSnapshot:
    """VIX should be None when data is unavailable, not 20.0."""

    def test_snapshot_builder_vix_none_when_no_data(self):
        """build_live_market_snapshot returns vix=None when vix_data is None."""
        import pandas as pd
        from shared.snapshot_builder import build_live_market_snapshot

        price_data = pd.DataFrame({
            "Open": [550.0], "High": [555.0], "Low": [548.0],
            "Close": [553.0], "Volume": [1000000],
        })
        iv_data = {"iv_rank": 30.0}
        technical_signals = {"rsi": 50, "trend": "bullish"}

        snapshot = build_live_market_snapshot(
            ticker="SPY",
            price_data=price_data,
            current_price=553.0,
            iv_data=iv_data,
            technical_signals=technical_signals,
            regime=None,
            vix_data=None,  # VIX unavailable
        )
        assert snapshot.vix is None

    def test_snapshot_builder_vix_set_when_data_available(self):
        """build_live_market_snapshot returns actual VIX when data available."""
        import pandas as pd
        from shared.snapshot_builder import build_live_market_snapshot

        price_data = pd.DataFrame({
            "Open": [550.0], "High": [555.0], "Low": [548.0],
            "Close": [553.0], "Volume": [1000000],
        })
        vix_data = pd.DataFrame({"Close": [25.5, 26.0, 24.0]})
        iv_data = {"iv_rank": 30.0}
        technical_signals = {"rsi": 50, "trend": "bullish"}

        snapshot = build_live_market_snapshot(
            ticker="SPY",
            price_data=price_data,
            current_price=553.0,
            iv_data=iv_data,
            technical_signals=technical_signals,
            regime="bull",
            vix_data=vix_data,
        )
        assert snapshot.vix == 24.0


# ===========================================================================
# Signal scorer RSI = None
# ===========================================================================


class TestSignalScorerRSINone:
    """Signal scorer should not crash or inflate score when RSI is missing."""

    def test_scorer_handles_none_rsi(self):
        """score_signal should not add RSI bonus when RSI is None."""
        from shared.signal_scorer import score_signal

        leg1 = MagicMock()
        leg1.strike = 550.0
        leg2 = MagicMock()
        leg2.strike = 545.0

        signal = MagicMock()
        signal.strategy_name = "iron_condor"
        signal.ticker = "SPY"
        signal.net_credit = 1.50
        signal.max_loss = 3.50
        signal.max_profit = 1.50
        signal.legs = [leg1, leg2]
        signal.metadata = {"spread_type": "iron_condor"}

        tech_no_rsi = {"trend": "neutral"}
        tech_with_rsi = {"trend": "neutral", "rsi": 50}

        score_none = score_signal(signal, iv_rank=30.0, technical_signals=tech_no_rsi)
        score_50 = score_signal(signal, iv_rank=30.0, technical_signals=tech_with_rsi)

        # Score with RSI=50 should be >= score without RSI (RSI in 40-60 gives bonus)
        assert score_50 >= score_none


# ===========================================================================
# Realtime pipeline default model
# ===========================================================================


class TestRealtimePipelineDefaults:
    """Realtime pipeline default model should return no_trade on missing data."""

    def test_default_model_no_trade_when_vix_missing(self):
        """_default_model returns no_trade when VIX is missing."""
        from compass.realtime_pipeline import ModelInference

        result = ModelInference._default_model({"rsi_14": 50, "iv_rank": 50})
        assert result[0] == "no_trade"

    def test_default_model_no_trade_when_rsi_missing(self):
        """_default_model returns no_trade when RSI is missing."""
        from compass.realtime_pipeline import ModelInference

        result = ModelInference._default_model({"vix": 20, "iv_rank": 50})
        assert result[0] == "no_trade"

    def test_default_model_works_with_all_features(self):
        """_default_model works normally when all features present."""
        from compass.realtime_pipeline import ModelInference

        result = ModelInference._default_model({
            "vix": 18, "rsi_14": 50, "iv_rank": 50, "momentum_5d_pct": 1,
        })
        assert result[0] in ("bull_put", "bear_call", "no_trade")
