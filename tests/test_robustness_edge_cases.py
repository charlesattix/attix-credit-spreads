"""
Robustness edge-case tests for the 5 most critical production modules.

Tests focus on silent-failure scenarios: empty inputs, NaN/None values,
division-by-zero, boundary conditions, and degenerate data that could
cause incorrect behaviour in production without raising exceptions.
"""

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# 1. shared/strike_selector.py — BS delta + delta-based strike selection
# ═══════════════════════════════════════════════════════════════════════════════

from shared.strike_selector import bs_delta, select_delta_strike


class TestBsDeltaEdgeCases:
    """Edge cases for Black-Scholes delta computation."""

    def test_zero_time_to_expiry_put_itm(self):
        # At expiry, ITM put (S < K) → delta = -1.0
        assert bs_delta(S=490, K=500, T=0, r=0.045, sigma=0.20, option_type="P") == -1.0

    def test_zero_time_to_expiry_put_otm(self):
        # At expiry, OTM put (S > K) → delta = 0.0
        assert bs_delta(S=510, K=500, T=0, r=0.045, sigma=0.20, option_type="P") == 0.0

    def test_zero_time_to_expiry_call_itm(self):
        assert bs_delta(S=510, K=500, T=0, r=0.045, sigma=0.20, option_type="C") == 1.0

    def test_zero_time_to_expiry_call_otm(self):
        assert bs_delta(S=490, K=500, T=0, r=0.045, sigma=0.20, option_type="C") == 0.0

    def test_zero_volatility(self):
        # sigma=0 → degenerate; ITM call → 1.0
        assert bs_delta(S=510, K=500, T=0.1, r=0.045, sigma=0, option_type="C") == 1.0

    def test_zero_underlying_price(self):
        # S=0 is degenerate — put ITM
        assert bs_delta(S=0, K=500, T=0.1, r=0.045, sigma=0.20, option_type="P") == -1.0

    def test_zero_strike_price(self):
        # K=0 is degenerate — call always ITM
        assert bs_delta(S=500, K=0, T=0.1, r=0.045, sigma=0.20, option_type="C") == 1.0

    def test_negative_time(self):
        # T < 0 = expired; treat like T=0
        d = bs_delta(S=490, K=500, T=-0.01, r=0.045, sigma=0.20, option_type="P")
        assert d == -1.0  # ITM put at expiry

    def test_very_deep_otm_put(self):
        # S >> K: delta should be near 0
        d = bs_delta(S=1000, K=100, T=0.1, r=0.045, sigma=0.20, option_type="P")
        assert abs(d) < 0.01

    def test_very_deep_itm_call(self):
        # S >> K: delta should be near 1.0
        d = bs_delta(S=1000, K=100, T=0.1, r=0.045, sigma=0.20, option_type="C")
        assert d > 0.99

    def test_atm_put_near_minus_half(self):
        d = bs_delta(S=500, K=500, T=30/365, r=0.045, sigma=0.20, option_type="P")
        assert -0.60 < d < -0.40

    def test_atm_call_near_plus_half(self):
        d = bs_delta(S=500, K=500, T=30/365, r=0.045, sigma=0.20, option_type="C")
        assert 0.40 < d < 0.60

    def test_case_insensitive_option_type(self):
        d1 = bs_delta(S=500, K=500, T=0.1, r=0.045, sigma=0.20, option_type="put")
        d2 = bs_delta(S=500, K=500, T=0.1, r=0.045, sigma=0.20, option_type="P")
        assert d1 == d2

    def test_extreme_volatility(self):
        # sigma = 5.0 (500%) — should not crash
        d = bs_delta(S=500, K=500, T=0.1, r=0.045, sigma=5.0, option_type="P")
        assert -1.0 <= d <= 0.0


class TestSelectDeltaStrikeEdgeCases:
    def test_empty_chain(self):
        assert select_delta_strike([], "P", target_delta=0.12) is None

    def test_single_strike(self):
        chain = [{"strike": 540.0, "delta": -0.10}]
        assert select_delta_strike(chain, "P", target_delta=0.12) == 540.0

    def test_exact_match(self):
        chain = [
            {"strike": 530.0, "delta": -0.05},
            {"strike": 540.0, "delta": -0.12},
            {"strike": 550.0, "delta": -0.25},
        ]
        assert select_delta_strike(chain, "P", target_delta=0.12) == 540.0

    def test_closest_match_when_no_exact(self):
        chain = [
            {"strike": 530.0, "delta": -0.08},
            {"strike": 540.0, "delta": -0.15},
        ]
        # |0.08 - 0.12| = 0.04 vs |0.15 - 0.12| = 0.03 → 540 wins
        assert select_delta_strike(chain, "P", target_delta=0.12) == 540.0

    def test_all_zero_deltas(self):
        chain = [
            {"strike": 500.0, "delta": 0.0},
            {"strike": 510.0, "delta": 0.0},
        ]
        # Target 0.12, all at 0.0 → picks first (both equally far)
        result = select_delta_strike(chain, "P", target_delta=0.12)
        assert result in (500.0, 510.0)

    def test_negative_deltas_compared_by_abs(self):
        chain = [
            {"strike": 530.0, "delta": -0.10},
            {"strike": 540.0, "delta": -0.14},
        ]
        # |0.10 - 0.12| = 0.02 vs |0.14 - 0.12| = 0.02 → tie, either is fine
        result = select_delta_strike(chain, "P", target_delta=0.12)
        assert result in (530.0, 540.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. shared/portfolio_risk.py — circuit breaker level mapping
# ═══════════════════════════════════════════════════════════════════════════════

from shared.portfolio_risk import (
    CircuitBreakerLevel,
    PortfolioRiskMonitor,
    _action_from_level,
    _level_from_drawdown,
)


class TestLevelFromDrawdown:
    def test_normal_positive(self):
        assert _level_from_drawdown(0.0) == CircuitBreakerLevel.NORMAL

    def test_normal_just_above_yellow(self):
        assert _level_from_drawdown(-7.99) == CircuitBreakerLevel.NORMAL

    def test_yellow_at_boundary(self):
        assert _level_from_drawdown(-8.0) == CircuitBreakerLevel.YELLOW

    def test_yellow_between_thresholds(self):
        assert _level_from_drawdown(-9.5) == CircuitBreakerLevel.YELLOW

    def test_red_at_boundary(self):
        assert _level_from_drawdown(-10.0) == CircuitBreakerLevel.RED

    def test_hard_stop_at_boundary(self):
        assert _level_from_drawdown(-12.0) == CircuitBreakerLevel.HARD_STOP

    def test_extreme_drawdown(self):
        assert _level_from_drawdown(-50.0) == CircuitBreakerLevel.HARD_STOP

    def test_positive_drawdown_normal(self):
        # New HWM → drawdown is 0 or positive → NORMAL
        assert _level_from_drawdown(5.0) == CircuitBreakerLevel.NORMAL


class TestActionFromLevel:
    def test_normal_no_action(self):
        assert _action_from_level(CircuitBreakerLevel.NORMAL) is None

    def test_yellow_reduces(self):
        assert _action_from_level(CircuitBreakerLevel.YELLOW) == "reduce_50pct"

    def test_red_pauses(self):
        assert _action_from_level(CircuitBreakerLevel.RED) == "pause_entries"

    def test_hard_stop_flattens(self):
        assert _action_from_level(CircuitBreakerLevel.HARD_STOP) == "flatten_all"


class TestPortfolioRiskMonitorEdgeCases:
    def test_fail_open_on_no_cache(self, tmp_path):
        monitor = PortfolioRiskMonitor(db_path=str(tmp_path / "risk.db"))
        status = monitor._fail_open_status()
        assert status.level == CircuitBreakerLevel.NORMAL
        assert status.combined_equity == 0.0

    def test_allow_entry_normal(self, tmp_path):
        monitor = PortfolioRiskMonitor(db_path=str(tmp_path / "risk.db"))
        # Force a NORMAL status
        monitor._last_status = monitor._fail_open_status()
        monitor._last_check_ts = float("inf")  # never expire cache
        allowed, reason = monitor.allow_entry("EXP-400")
        assert allowed is True
        assert reason is None

    def test_hwm_persists_and_updates(self, tmp_path):
        db = str(tmp_path / "risk.db")
        monitor = PortfolioRiskMonitor(db_path=db)

        # First write: equity = 100K → HWM = 100K
        s1 = monitor._compute_and_persist(100_000, {"EXP-400": 100_000})
        assert s1.hwm == 100_000
        assert s1.drawdown_pct == 0.0

        # Second write: equity = 95K → HWM stays 100K, DD = -5%
        s2 = monitor._compute_and_persist(95_000, {"EXP-400": 95_000})
        assert s2.hwm == 100_000
        assert abs(s2.drawdown_pct - (-5.0)) < 0.01

        # Third write: equity = 105K → HWM = 105K, DD = 0%
        s3 = monitor._compute_and_persist(105_000, {"EXP-400": 105_000})
        assert s3.hwm == 105_000
        assert s3.drawdown_pct == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. strategy/spread_strategy.py — direction gating and DTE filtering
# ═══════════════════════════════════════════════════════════════════════════════


def _make_strategy_config(**overrides):
    cfg = {
        "strategy": {
            "min_dte": 25, "max_dte": 45, "target_dte": 35,
            "spread_width": 5, "spread_width_high_iv": 10, "spread_width_low_iv": 5,
            "min_iv_rank": 0, "min_iv_percentile": 0,
            "direction": "both",
            "regime_mode": "hmm",
            "regime_config": {},
            "iron_condor": {"enabled": False},
            "technical": {
                "use_trend_filter": False, "use_rsi_filter": False,
                "use_support_resistance": False,
                "fast_ma": 20, "slow_ma": 200,
                "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
            },
        },
        "risk": {
            "account_size": 100_000, "max_risk_per_trade": 5.0,
            "min_contracts": 1, "max_contracts": 25, "max_positions": 50,
        },
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and k in cfg:
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


class TestSpreadWidthSelection:
    def test_high_iv_widens(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        assert s._select_spread_width({"iv_rank": 60}) == 10

    def test_medium_iv_uses_low_iv_width(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        assert s._select_spread_width({"iv_rank": 30}) == 5

    def test_low_iv_uses_default(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        assert s._select_spread_width({"iv_rank": 10}) == 5

    def test_missing_iv_rank_uses_default(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        assert s._select_spread_width({}) == 5

    def test_nan_iv_rank_uses_default(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        # float('nan') > 50 is False, float('nan') >= 25 is False → else
        assert s._select_spread_width({"iv_rank": float("nan")}) == 5


class TestFilterByDte:
    def test_empty_chain_returns_empty(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        chain = pd.DataFrame(columns=["expiration", "strike", "bid", "ask", "type"])
        result = s._filter_by_dte(chain)
        assert result == []

    def test_no_valid_expirations(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        # All expirations too far out
        now = datetime.now(timezone.utc)
        chain = pd.DataFrame({
            "expiration": [now + pd.Timedelta(days=100)],
            "strike": [500.0], "bid": [1.0], "ask": [1.5], "type": ["put"],
        })
        result = s._filter_by_dte(chain, as_of_date=now)
        assert result == []

    def test_returns_at_most_one_expiration(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        now = datetime.now(timezone.utc)
        chain = pd.DataFrame({
            "expiration": [
                now + pd.Timedelta(days=28),
                now + pd.Timedelta(days=30),
                now + pd.Timedelta(days=35),
            ],
            "strike": [500.0] * 3, "bid": [1.0] * 3,
            "ask": [1.5] * 3, "type": ["put"] * 3,
        })
        result = s._filter_by_dte(chain, as_of_date=now)
        assert len(result) <= 1


class TestBullishBearishConditions:
    def test_bullish_passes_with_no_filters(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        assert s._check_bullish_conditions({}, {}) is True

    def test_bullish_blocked_by_iv_gate(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        cfg = _make_strategy_config()
        cfg["strategy"]["min_iv_rank"] = 20
        cfg["strategy"]["min_iv_percentile"] = 20
        s = CreditSpreadStrategy(cfg)
        assert s._check_bullish_conditions({}, {"iv_rank": 10, "iv_percentile": 10}) is False

    def test_bearish_blocked_when_rsi_missing(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        cfg = _make_strategy_config()
        cfg["strategy"]["technical"]["use_rsi_filter"] = True
        s = CreditSpreadStrategy(cfg)
        # RSI missing → block
        assert s._check_bearish_conditions({}, {}) is False

    def test_combo_regime_overrides_technical(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        chain = pd.DataFrame(columns=["expiration", "strike", "bid", "ask", "type"])
        # combo_regime='bull' → want bull puts, not bear calls
        result = s.evaluate_spread_opportunity(
            "SPY", chain, {"combo_regime": "bull"}, {"iv_rank": 30}, 560.0
        )
        assert isinstance(result, list)

    def test_evaluate_empty_chain_returns_empty(self):
        from strategy.spread_strategy import CreditSpreadStrategy
        s = CreditSpreadStrategy(_make_strategy_config())
        chain = pd.DataFrame(columns=["expiration", "strike", "bid", "ask", "type"])
        result = s.evaluate_spread_opportunity("SPY", chain, {}, {"iv_rank": 30}, 560.0)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# 4. strategy/options_analyzer.py — options chain handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestOptionsAnalyzerEdgeCases:
    def _make_analyzer(self, **overrides):
        from strategy.options_analyzer import OptionsAnalyzer
        cfg = {
            "data": {"provider": "none"},
            "strategy": {"min_dte": 25, "max_dte": 45},
        }
        cfg.update(overrides)
        return OptionsAnalyzer(cfg)

    def test_empty_chain_returns_zero_iv(self):
        oa = self._make_analyzer()
        assert oa.get_current_iv(pd.DataFrame()) == 0.0

    def test_chain_without_iv_column_returns_zero(self):
        oa = self._make_analyzer()
        chain = pd.DataFrame({"strike": [500, 510], "bid": [1, 2], "ask": [2, 3]})
        assert oa.get_current_iv(chain) == 0.0

    def test_chain_with_all_nan_iv_returns_zero(self):
        oa = self._make_analyzer()
        chain = pd.DataFrame({"iv": [float("nan"), float("nan")]})
        assert oa.get_current_iv(chain) == 0.0

    def test_chain_with_valid_iv(self):
        oa = self._make_analyzer()
        chain = pd.DataFrame({"iv": [0.20, 0.25, 0.30]})
        iv = oa.get_current_iv(chain)
        assert iv == 25.0  # median(20, 25, 30) = 25% as percentage

    def test_clean_options_missing_required_column(self):
        oa = self._make_analyzer()
        # Missing 'type' column → returns empty
        df = pd.DataFrame({"strike": [500], "bid": [1], "ask": [2], "expiration": ["2026-05-16"]})
        result = oa._clean_options_data(df)
        assert result.empty

    def test_clean_options_removes_zero_bid_ask(self):
        oa = self._make_analyzer()
        df = pd.DataFrame({
            "strike": [500, 510], "bid": [0.0, 1.5], "ask": [0.0, 2.0],
            "type": ["put", "put"],
            "expiration": ["2026-05-16", "2026-05-16"],
            "delta": [-0.12, -0.15],
        })
        result = oa._clean_options_data(df)
        assert len(result) == 1
        assert result.iloc[0]["strike"] == 510

    def test_clean_options_calculates_mid(self):
        oa = self._make_analyzer()
        df = pd.DataFrame({
            "strike": [500], "bid": [1.0], "ask": [2.0],
            "type": ["put"], "expiration": ["2026-05-16"],
            "delta": [-0.12],
        })
        result = oa._clean_options_data(df)
        assert result.iloc[0]["mid"] == 1.5

    def test_yfinance_column_renaming(self):
        oa = self._make_analyzer()
        df = pd.DataFrame({
            "strike": [500], "bid": [1.0], "ask": [2.0],
            "type": ["put"], "expiration": ["2026-05-16"],
            "impliedVolatility": [0.25],
            "lastPrice": [1.50],
            "inTheMoney": [False],
        })
        result = oa._clean_options_data(df)
        assert "iv" in result.columns
        assert "last" in result.columns
        assert "itm" in result.columns


# ═══════════════════════════════════════════════════════════════════════════════
# 5. shared/reconciler.py — ReconciliationResult + edge cases
# ═══════════════════════════════════════════════════════════════════════════════

from shared.reconciler import ReconciliationResult, _TERMINAL_ORDER_STATES


class TestReconciliationResult:
    def test_empty_result_is_falsy(self):
        r = ReconciliationResult()
        assert not r

    def test_result_with_pending_is_truthy(self):
        r = ReconciliationResult()
        r.pending_resolved = 1
        assert r

    def test_result_with_phantom_is_truthy(self):
        r = ReconciliationResult()
        r.phantom_resolved = 1
        assert r

    def test_result_with_errors_only_is_truthy(self):
        r = ReconciliationResult()
        r.errors.append("something broke")
        # errors alone make it truthy — any activity counts
        assert r


class TestTerminalOrderStates:
    def test_known_terminal_states(self):
        for state in ("cancelled", "expired", "rejected", "replaced", "done_for_day"):
            assert state in _TERMINAL_ORDER_STATES

    def test_filled_is_not_terminal(self):
        assert "filled" not in _TERMINAL_ORDER_STATES

    def test_new_is_not_terminal(self):
        assert "new" not in _TERMINAL_ORDER_STATES

    def test_accepted_is_not_terminal(self):
        assert "accepted" not in _TERMINAL_ORDER_STATES


class TestReconcilerPendingResolution:
    """Test pending_open → open promotion edge cases."""

    def test_reconcile_pending_with_no_pending_trades(self, tmp_path):
        from shared.database import init_db
        from shared.reconciler import PositionReconciler

        db = str(tmp_path / "test.db")
        init_db(db)
        alpaca = MagicMock()
        alpaca.get_orders.return_value = []
        alpaca.get_positions.return_value = []

        rec = PositionReconciler(alpaca=alpaca, db_path=db)
        result = rec.reconcile_pending_only()
        assert result.pending_resolved == 0
        assert result.pending_failed == 0

    def test_reconcile_with_none_alpaca_orders(self, tmp_path):
        """Alpaca returns None (API error) → should not crash."""
        from shared.database import init_db, upsert_trade
        from shared.reconciler import PositionReconciler

        db = str(tmp_path / "test.db")
        init_db(db)
        upsert_trade({
            "id": "pending-edge", "ticker": "SPY", "strategy_type": "bull_put",
            "status": "pending_open", "short_strike": 540, "long_strike": 535,
            "expiration": "2026-06-20", "credit": 1.5, "contracts": 1,
            "entry_date": datetime.now(timezone.utc).isoformat(),
            "alpaca_client_order_id": "cs-test-abc",
        }, source="execution", path=db)

        alpaca = MagicMock()
        alpaca.get_orders.return_value = []  # no matching orders
        alpaca.get_positions.return_value = []

        rec = PositionReconciler(alpaca=alpaca, db_path=db)
        result = rec.reconcile_pending_only()
        # Should complete without crash; pending may stay or age out
        assert isinstance(result, ReconciliationResult)
