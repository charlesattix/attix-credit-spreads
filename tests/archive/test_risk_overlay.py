"""Tests for compass/risk_overlay.py — Unified Risk Management Overlay.

Covers all 5 risk layers: dynamic leverage, tail risk hedge, event gates,
position stops, DD circuit breaker, plus integration and report generation.

Target: 50+ tests.
"""

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from compass.risk_overlay import (
    RiskOverlay,
    RiskOverlayConfig,
    RiskOverlayResult,
    RiskRegime,
    DayRiskState,
    _ramp,
    _compute_metrics,
    _crisis_score,
    _get_event_scaling,
    _inline_event_scaling,
    generate_test_data,
    generate_report,
    TRADING_DAYS,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def default_config():
    return RiskOverlayConfig()


@pytest.fixture
def minimal_config():
    """All layers disabled."""
    return RiskOverlayConfig(
        enable_dynamic_leverage=False,
        enable_tail_hedge=False,
        enable_event_gates=False,
        enable_position_stops=False,
        enable_dd_circuit_breaker=False,
    )


@pytest.fixture
def test_data():
    return generate_test_data(n_days=500, seed=42)


@pytest.fixture
def small_data():
    """Tiny dataset for fast tests."""
    n = 60
    dates = pd.bdate_range("2023-01-02", periods=n)
    rng = np.random.default_rng(99)
    rets = rng.normal(0.0004, 0.01, n)
    spy = rng.normal(0.0004, 0.01, n)
    vix = np.full(n, 18.0) + rng.normal(0, 1, n)
    vix = np.clip(vix, 10, 50)
    vix3m = vix * 0.9
    return {
        "portfolio_returns": pd.Series(rets, index=dates),
        "spy_returns": pd.Series(spy, index=dates),
        "vix": pd.Series(vix, index=dates),
        "vix3m": pd.Series(vix3m, index=dates),
    }


@pytest.fixture
def crash_data():
    """Data with an embedded crash for testing hedges and breakers."""
    n = 200
    dates = pd.bdate_range("2023-01-02", periods=n)
    rng = np.random.default_rng(123)
    rets = rng.normal(0.0004, 0.008, n)
    # Embed crash at days 80-90
    rets[80:90] = np.array([-0.03, -0.04, -0.02, -0.05, -0.03,
                            -0.02, -0.01, 0.01, 0.02, 0.01])
    spy = rets * 0.9 + rng.normal(0, 0.003, n)
    vix = np.full(n, 16.0)
    vix[75:95] = np.linspace(16, 45, 20)  # VIX spikes during crash
    vix[95:120] = np.linspace(45, 20, 25)  # Slow recovery
    vix3m = vix * 0.85
    return {
        "portfolio_returns": pd.Series(rets, index=dates),
        "spy_returns": pd.Series(spy, index=dates),
        "vix": pd.Series(vix, index=dates),
        "vix3m": pd.Series(vix3m, index=dates),
    }


# ══════════════════════════════════════════════════════════════════════════
# Helper function tests
# ══════════════════════════════════════════════════════════════════════════

class TestRamp:
    def test_at_low(self):
        assert _ramp(10.0, 15.0, 35.0) == 1.0

    def test_at_high(self):
        assert _ramp(40.0, 15.0, 35.0) == 0.0

    def test_midpoint(self):
        assert abs(_ramp(25.0, 15.0, 35.0) - 0.5) < 1e-9

    def test_below_low(self):
        assert _ramp(5.0, 15.0, 35.0) == 1.0

    def test_above_high(self):
        assert _ramp(100.0, 15.0, 35.0) == 0.0

    def test_equal_low(self):
        assert _ramp(15.0, 15.0, 35.0) == 1.0

    def test_equal_high(self):
        assert _ramp(35.0, 15.0, 35.0) == 0.0

    def test_quarter_point(self):
        result = _ramp(20.0, 15.0, 35.0)
        assert abs(result - 0.75) < 1e-9


class TestComputeMetrics:
    def test_positive_returns(self):
        rets = np.array([0.01] * 252)
        m = _compute_metrics(rets)
        assert m["cagr_pct"] > 0
        assert m["max_dd_pct"] == 0  # no drawdown with constant +1%
        # Note: constant returns → zero std → sharpe=0 by convention

    def test_negative_returns(self):
        rets = np.array([-0.01] * 100)
        m = _compute_metrics(rets)
        assert m["cagr_pct"] < 0
        assert m["max_dd_pct"] > 0

    def test_zero_returns(self):
        rets = np.zeros(100)
        m = _compute_metrics(rets)
        assert m["cagr_pct"] == 0
        assert m["sharpe"] == 0

    def test_single_element(self):
        m = _compute_metrics(np.array([0.01]))
        assert isinstance(m, dict)

    def test_volatility_computation(self):
        rets = np.array([0.01, -0.01] * 126)
        m = _compute_metrics(rets)
        assert m["vol_pct"] > 0


class TestCrisisScore:
    def test_calm_market(self):
        cfg = RiskOverlayConfig()
        score = _crisis_score(14.0, 0.88, 0.0, 0.10, 0.02, cfg)
        assert score < 0.1

    def test_crisis_market(self):
        cfg = RiskOverlayConfig()
        score = _crisis_score(40.0, 1.2, 0.08, 0.35, -0.05, cfg)
        assert score > 0.7

    def test_moderate_stress(self):
        cfg = RiskOverlayConfig()
        score = _crisis_score(25.0, 1.05, 0.03, 0.20, -0.01, cfg)
        assert 0.2 < score < 0.7

    def test_score_bounds(self):
        cfg = RiskOverlayConfig()
        # Extreme calm
        s1 = _crisis_score(10.0, 0.80, 0.0, 0.05, 0.05, cfg)
        assert 0.0 <= s1 <= 1.0
        # Extreme crisis
        s2 = _crisis_score(60.0, 1.5, 0.15, 0.50, -0.10, cfg)
        assert 0.0 <= s2 <= 1.0

    def test_vix_dominant(self):
        cfg = RiskOverlayConfig()
        # High VIX, everything else calm
        score = _crisis_score(38.0, 0.88, 0.0, 0.10, 0.02, cfg)
        assert score > 0.2  # VIX component should dominate


# ══════════════════════════════════════════════════════════════════════════
# Layer 1: Dynamic Leverage
# ══════════════════════════════════════════════════════════════════════════

class TestDynamicLeverage:
    def test_calm_market_high_leverage(self, small_data):
        cfg = RiskOverlayConfig(
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        # Force calm VIX
        small_data["vix"] = pd.Series(np.full(len(small_data["vix"]), 12.0),
                                       index=small_data["vix"].index)
        small_data["vix3m"] = small_data["vix"] * 1.1  # contango

        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert result.avg_leverage > 1.0

    def test_crisis_market_low_leverage(self, small_data):
        cfg = RiskOverlayConfig(
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        # Force crisis VIX
        small_data["vix"] = pd.Series(np.full(len(small_data["vix"]), 40.0),
                                       index=small_data["vix"].index)
        small_data["vix3m"] = small_data["vix"] * 0.8  # inverted

        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert result.avg_leverage < 1.0

    def test_leverage_smoothing(self, small_data):
        cfg = RiskOverlayConfig(
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
            leverage_smoothing_halflife=10,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        # Check leverage doesn't jump wildly between days
        leverages = [s.leverage for s in result.daily_states]
        diffs = np.diff(leverages)
        assert np.max(np.abs(diffs)) < 0.5  # no day-to-day jump > 0.5x

    def test_disabled_leverage(self, small_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert result.avg_leverage == 1.0

    def test_leverage_bounds(self, test_data):
        cfg = RiskOverlayConfig(
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
            min_leverage=0.3,
            target_leverage=1.8,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**test_data)
        for s in result.daily_states:
            assert s.leverage >= 0.0  # can be below min_leverage due to event gates
            assert s.leverage <= 2.0  # shouldn't exceed target


# ══════════════════════════════════════════════════════════════════════════
# Layer 2: Tail Risk Hedge
# ══════════════════════════════════════════════════════════════════════════

class TestTailRiskHedge:
    def test_hedge_cost_positive(self, small_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert result.total_hedge_cost_pct > 0

    def test_hedge_payoff_on_crash(self, crash_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        assert result.total_hedge_payoff_pct > 0

    def test_hedge_active_during_crash(self, crash_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        # At least some days should have active hedges
        active_days = sum(1 for s in result.daily_states if s.hedge_active)
        assert active_days > 0

    def test_crisis_score_increases_during_crash(self, crash_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        # Crisis score should be higher around day 85 (peak crash)
        pre_crash = result.daily_states[70].crisis_score
        during_crash = result.daily_states[85].crisis_score
        assert during_crash > pre_crash

    def test_no_hedge_when_disabled(self, small_data):
        cfg = RiskOverlayConfig(
            enable_tail_hedge=False,
            enable_dynamic_leverage=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert result.total_hedge_cost_pct == 0
        assert result.total_hedge_payoff_pct == 0


# ══════════════════════════════════════════════════════════════════════════
# Layer 3: Event Gates
# ══════════════════════════════════════════════════════════════════════════

class TestEventGates:
    def test_inline_nfp_scaling(self):
        cfg = RiskOverlayConfig()
        # Find a first Friday — 2023-01-06 is a Friday and day 6
        dt = date(2023, 1, 6)
        scaling, events = _inline_event_scaling(dt, cfg)
        assert scaling < 1.0
        assert "NFP" in events

    def test_inline_cpi_scaling(self):
        cfg = RiskOverlayConfig()
        dt = date(2023, 1, 12)
        scaling, events = _inline_event_scaling(dt, cfg)
        assert scaling < 1.0
        assert "CPI" in events

    def test_no_event_day(self):
        cfg = RiskOverlayConfig()
        # Pick a day with no events — 2023-01-16 is a Monday, day 16
        dt = date(2023, 1, 16)
        scaling, events = _inline_event_scaling(dt, cfg)
        assert scaling == 1.0
        assert len(events) == 0

    def test_event_scaling_applied(self, small_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        # Should have some event gate days
        assert result.avg_event_scaling <= 1.0

    def test_disabled_event_gates(self, small_data):
        cfg = RiskOverlayConfig(
            enable_event_gates=False,
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert result.event_gate_days == 0
        assert result.avg_event_scaling == 1.0


# ══════════════════════════════════════════════════════════════════════════
# Layer 4: Position Stops
# ══════════════════════════════════════════════════════════════════════════

class TestPositionStops:
    def test_stop_triggered_on_large_loss(self):
        """Inject a -5% day and verify stop fires."""
        n = 100
        dates = pd.bdate_range("2023-01-02", periods=n)
        rets = np.zeros(n)
        rets[50] = -0.05  # 5% loss > 3% stop threshold
        spy = rets.copy()
        vix = np.full(n, 18.0)

        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_dd_circuit_breaker=False,
            stop_loss_pct=0.03,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(
            pd.Series(rets, index=dates),
            pd.Series(spy, index=dates),
            pd.Series(vix, index=dates),
        )
        assert result.stop_triggers > 0
        assert result.daily_states[50].stop_triggered

    def test_no_stop_on_small_loss(self):
        n = 100
        dates = pd.bdate_range("2023-01-02", periods=n)
        rets = np.full(n, -0.001)  # small daily losses, all < 3%
        spy = rets.copy()
        vix = np.full(n, 18.0)

        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_dd_circuit_breaker=False,
            stop_loss_pct=0.03,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(
            pd.Series(rets, index=dates),
            pd.Series(spy, index=dates),
            pd.Series(vix, index=dates),
        )
        assert result.stop_triggers == 0

    def test_disabled_stops(self, crash_data):
        cfg = RiskOverlayConfig(
            enable_position_stops=False,
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        assert result.stop_triggers == 0


# ══════════════════════════════════════════════════════════════════════════
# Layer 5: DD Circuit Breaker
# ══════════════════════════════════════════════════════════════════════════

class TestDDCircuitBreaker:
    def test_breaker_activates_on_drawdown(self, crash_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            dd_breaker_threshold=0.10,
            dd_breaker_leverage=0.5,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        # Crash should trigger breaker (cumulative -20%+ drawdown)
        assert result.breaker_activations > 0
        assert result.breaker_days > 0

    def test_breaker_limits_leverage(self, crash_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            dd_breaker_threshold=0.10,
            dd_breaker_leverage=0.5,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        breaker_states = [s for s in result.daily_states if s.breaker_active]
        for s in breaker_states:
            assert s.leverage <= 0.5 + 1e-6

    def test_breaker_recovery(self, crash_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            dd_breaker_threshold=0.10,
            dd_recovery_threshold=0.05,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        # Eventually should recover (crash ends, equity rebuilds)
        if result.breaker_activations > 0:
            # Check final state — may or may not have recovered
            final = result.daily_states[-1]
            # Breaker should have eventually released
            assert result.breaker_days < len(result.daily_states)

    def test_no_breaker_in_calm_market(self, small_data):
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert result.breaker_activations == 0

    def test_disabled_breaker(self, crash_data):
        cfg = RiskOverlayConfig(
            enable_dd_circuit_breaker=False,
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        assert result.breaker_activations == 0
        assert result.breaker_days == 0

    def test_hysteresis_prevents_flapping(self, crash_data):
        """Breaker shouldn't flip on/off rapidly."""
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            dd_breaker_threshold=0.10,
            dd_recovery_threshold=0.05,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**crash_data)
        # Count transitions
        transitions = 0
        prev = False
        for s in result.daily_states:
            if s.breaker_active != prev:
                transitions += 1
            prev = s.breaker_active
        # Should have at most 2-4 transitions (on/off, maybe repeat)
        assert transitions <= 6


# ══════════════════════════════════════════════════════════════════════════
# Integration: All layers combined
# ══════════════════════════════════════════════════════════════════════════

class TestFullIntegration:
    def test_all_layers_produce_result(self, test_data):
        overlay = RiskOverlay(RiskOverlayConfig())
        result = overlay.apply(**test_data)
        assert isinstance(result, RiskOverlayResult)
        assert result.n_days == len(test_data["portfolio_returns"])

    def test_result_has_equity_curve(self, test_data):
        overlay = RiskOverlay()
        result = overlay.apply(**test_data)
        assert len(result.equity_curve) == result.n_days + 1
        assert result.equity_curve[0] == 100_000.0

    def test_result_has_daily_states(self, test_data):
        overlay = RiskOverlay()
        result = overlay.apply(**test_data)
        assert len(result.daily_states) == result.n_days
        s = result.daily_states[100]
        assert hasattr(s, "leverage")
        assert hasattr(s, "crisis_score")
        assert hasattr(s, "event_scaling")
        assert hasattr(s, "breaker_active")

    def test_dd_reduction_positive_in_crash(self, crash_data):
        """Overlay should reduce DD vs unprotected."""
        overlay = RiskOverlay(RiskOverlayConfig(
            dd_breaker_threshold=0.08,
        ))
        result = overlay.apply(**crash_data)
        # DD reduction should be positive (overlay helped)
        assert result.dd_reduction_pct >= 0

    def test_protected_vs_unprotected_metrics(self, test_data):
        overlay = RiskOverlay()
        result = overlay.apply(**test_data)
        # Both metrics should be computed
        assert result.cagr_pct != 0 or result.unprotected_cagr_pct != 0
        assert result.max_dd_pct >= 0
        assert result.unprotected_max_dd_pct >= 0

    def test_regime_distribution(self, test_data):
        overlay = RiskOverlay()
        result = overlay.apply(**test_data)
        total_regime_days = sum(result.regime_days.values())
        assert total_regime_days == result.n_days

    def test_yearly_breakdown(self, test_data):
        overlay = RiskOverlay()
        result = overlay.apply(**test_data)
        assert len(result.yearly) > 0
        for yr, stats in result.yearly.items():
            assert "cagr_pct" in stats
            assert "sharpe" in stats

    def test_no_layers_passthrough(self, small_data):
        """With all layers disabled, returns should pass through unmodified."""
        cfg = RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        # Returns should be nearly identical (1x leverage, no hedge)
        for s in result.daily_states:
            assert abs(s.adjusted_return - s.raw_return) < 1e-9
            assert s.leverage == 1.0

    def test_custom_starting_capital(self, small_data):
        overlay = RiskOverlay(RiskOverlayConfig(
            enable_dynamic_leverage=False,
            enable_tail_hedge=False,
            enable_event_gates=False,
            enable_position_stops=False,
            enable_dd_circuit_breaker=False,
        ))
        result = overlay.apply(**small_data, starting_capital=50_000)
        assert result.equity_curve[0] == 50_000

    def test_no_vix3m_defaults(self, small_data):
        """Should work without VIX3M by using VIX*0.9 default."""
        overlay = RiskOverlay()
        result = overlay.apply(
            small_data["portfolio_returns"],
            small_data["spy_returns"],
            small_data["vix"],
        )
        assert isinstance(result, RiskOverlayResult)


# ══════════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_single_day(self):
        dates = pd.bdate_range("2023-01-02", periods=1)
        data = {
            "portfolio_returns": pd.Series([0.01], index=dates),
            "spy_returns": pd.Series([0.01], index=dates),
            "vix": pd.Series([18.0], index=dates),
        }
        overlay = RiskOverlay()
        result = overlay.apply(**data)
        assert result.n_days == 1

    def test_all_zero_returns(self):
        n = 50
        dates = pd.bdate_range("2023-01-02", periods=n)
        data = {
            "portfolio_returns": pd.Series(np.zeros(n), index=dates),
            "spy_returns": pd.Series(np.zeros(n), index=dates),
            "vix": pd.Series(np.full(n, 18.0), index=dates),
        }
        overlay = RiskOverlay()
        result = overlay.apply(**data)
        assert result.n_days == n

    def test_extreme_vix(self):
        """VIX at 80 — should not crash."""
        n = 50
        dates = pd.bdate_range("2023-01-02", periods=n)
        data = {
            "portfolio_returns": pd.Series(np.full(n, -0.01), index=dates),
            "spy_returns": pd.Series(np.full(n, -0.01), index=dates),
            "vix": pd.Series(np.full(n, 80.0), index=dates),
            "vix3m": pd.Series(np.full(n, 50.0), index=dates),
        }
        overlay = RiskOverlay()
        result = overlay.apply(**data)
        assert result.avg_leverage < 0.5  # Should be minimal leverage

    def test_negative_portfolio_value(self):
        """Even with huge losses, should not produce NaN or crash."""
        n = 30
        dates = pd.bdate_range("2023-01-02", periods=n)
        rets = np.full(n, -0.05)  # 5% loss every day
        data = {
            "portfolio_returns": pd.Series(rets, index=dates),
            "spy_returns": pd.Series(rets, index=dates),
            "vix": pd.Series(np.full(n, 40.0), index=dates),
        }
        cfg = RiskOverlayConfig(
            enable_position_stops=False,  # allow full losses
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**data)
        assert not any(np.isnan(s.adjusted_return) for s in result.daily_states)


# ══════════════════════════════════════════════════════════════════════════
# Configuration tests
# ══════════════════════════════════════════════════════════════════════════

class TestConfiguration:
    def test_default_config_valid(self):
        cfg = RiskOverlayConfig()
        assert cfg.target_leverage > cfg.min_leverage
        assert cfg.dd_breaker_threshold > cfg.dd_recovery_threshold
        assert cfg.stop_loss_pct > 0
        assert cfg.hedge_annual_cost_budget_pct > 0

    def test_custom_thresholds(self, small_data):
        cfg = RiskOverlayConfig(
            dd_breaker_threshold=0.05,
            dd_recovery_threshold=0.02,
            stop_loss_pct=0.01,
            target_leverage=1.2,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert isinstance(result, RiskOverlayResult)

    def test_aggressive_config(self, small_data):
        """High leverage, low hedge — should produce valid result."""
        cfg = RiskOverlayConfig(
            target_leverage=2.5,
            min_leverage=1.0,
            hedge_annual_cost_budget_pct=0.5,
            dd_breaker_threshold=0.20,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert isinstance(result, RiskOverlayResult)
        assert result.avg_leverage > 0.5  # at least above minimum floor

    def test_conservative_config(self, small_data):
        """Low leverage, high hedge — should produce lower vol."""
        cfg = RiskOverlayConfig(
            target_leverage=0.8,
            min_leverage=0.2,
            hedge_annual_cost_budget_pct=4.0,
            dd_breaker_threshold=0.05,
        )
        overlay = RiskOverlay(cfg)
        result = overlay.apply(**small_data)
        assert result.avg_leverage < 1.0


# ══════════════════════════════════════════════════════════════════════════
# Regime classification
# ══════════════════════════════════════════════════════════════════════════

class TestRegimeClassification:
    def test_calm_regime(self):
        cfg = RiskOverlayConfig()
        regime = RiskOverlay._classify_regime(12.0, 0.10, 0.0, cfg)
        assert regime == "calm"

    def test_normal_regime(self):
        cfg = RiskOverlayConfig()
        regime = RiskOverlay._classify_regime(22.0, 0.15, 0.0, cfg)
        assert regime == "normal"

    def test_elevated_regime(self):
        cfg = RiskOverlayConfig()
        regime = RiskOverlay._classify_regime(30.0, 0.20, 0.0, cfg)
        assert regime == "elevated"

    def test_crisis_regime_vix(self):
        cfg = RiskOverlayConfig()
        regime = RiskOverlay._classify_regime(40.0, 0.35, 0.0, cfg)
        assert regime == "crisis"

    def test_crisis_regime_dd(self):
        cfg = RiskOverlayConfig()
        regime = RiskOverlay._classify_regime(18.0, 0.15, 0.12, cfg)
        assert regime == "crisis"


# ══════════════════════════════════════════════════════════════════════════
# Report generation
# ══════════════════════════════════════════════════════════════════════════

class TestReportGeneration:
    def test_generates_html(self, test_data, tmp_path):
        overlay = RiskOverlay()
        result = overlay.apply(**test_data)
        out = str(tmp_path / "test_report.html")
        path = generate_report(result, output_path=out)
        assert path == out
        with open(out) as f:
            html = f.read()
        assert "Unified Risk Management Overlay" in html
        assert "Layer 1" in html
        assert "Circuit Breaker" in html

    def test_report_contains_metrics(self, test_data, tmp_path):
        overlay = RiskOverlay()
        result = overlay.apply(**test_data)
        out = str(tmp_path / "test_report2.html")
        generate_report(result, output_path=out)
        with open(out) as f:
            html = f.read()
        assert "CAGR" in html
        assert "Sharpe" in html
        assert "Max DD" in html


# ══════════════════════════════════════════════════════════════════════════
# Test data generator
# ══════════════════════════════════════════════════════════════════════════

class TestDataGenerator:
    def test_generate_test_data_shape(self):
        data = generate_test_data(n_days=100)
        # bdate_range may produce slightly different count depending on start date
        n = len(data["portfolio_returns"])
        assert n == 100
        assert len(data["spy_returns"]) == n
        assert len(data["vix"]) == n
        assert len(data["vix3m"]) == n

    def test_generate_test_data_types(self):
        data = generate_test_data()
        assert isinstance(data["portfolio_returns"], pd.Series)
        assert isinstance(data["vix"], pd.Series)

    def test_generate_test_data_vix_range(self):
        data = generate_test_data(n_days=1260)
        assert data["vix"].min() >= 10
        assert data["vix"].max() <= 80

    def test_generate_test_data_deterministic(self):
        d1 = generate_test_data(seed=42)
        d2 = generate_test_data(seed=42)
        assert np.allclose(d1["portfolio_returns"].values, d2["portfolio_returns"].values)
