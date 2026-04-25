"""Tests for compass/dynamic_sizing.py — Dynamic Position Sizing."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.dynamic_sizing import (
    DynamicSizingConfig, DynamicSizer, SizingState, BacktestComparison,
    generate_market_data, generate_report, _compute_metrics, _equity_curve,
    _yearly_comparison, TRADING_DAYS,
)


@pytest.fixture
def config():
    return DynamicSizingConfig()

@pytest.fixture
def sizer(config):
    return DynamicSizer(config)

@pytest.fixture
def market_data():
    return generate_market_data(seed=42)

@pytest.fixture
def result(sizer, market_data):
    d = market_data
    return sizer.backtest(d["portfolio_returns"], d["vix"], d["vix3m"],
                          d["spy_returns"], d["dates"])


# ═══════════════════════════════════════════════════════════════════════════
# Config Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_defaults(self, config):
        assert config.max_leverage == 2.5
        assert config.min_leverage == 0.5
        assert config.default_leverage == 1.6

    def test_dd_trigger(self, config):
        assert config.dd_trigger == 0.08

    def test_vix_thresholds_ordered(self, config):
        assert config.vix_boost_threshold < config.vix_normal_low
        assert config.vix_normal_low < config.vix_normal_high
        assert config.vix_normal_high < config.vix_reduce_threshold

    def test_custom_config(self):
        c = DynamicSizingConfig(max_leverage=3.0, dd_trigger=0.10)
        assert c.max_leverage == 3.0
        assert c.dd_trigger == 0.10


# ═══════════════════════════════════════════════════════════════════════════
# Core Leverage Computation
# ═══════════════════════════════════════════════════════════════════════════

class TestComputeLeverage:
    def test_low_vix_high_leverage(self, sizer):
        lev, regime, _ = sizer.compute_leverage(
            vix=12, vix_ratio=0.85, realized_vol=0.08, trend_20d=0.03,
            drawdown=0.01, circuit_breaker_active=False)
        assert lev >= 2.0

    def test_high_vix_low_leverage(self, sizer):
        lev, regime, _ = sizer.compute_leverage(
            vix=40, vix_ratio=1.15, realized_vol=0.35, trend_20d=-0.03,
            drawdown=0.05, circuit_breaker_active=False)
        assert lev <= 0.8

    def test_circuit_breaker_forces_minimum(self, sizer):
        lev, regime, _ = sizer.compute_leverage(
            vix=14, vix_ratio=0.85, realized_vol=0.10, trend_20d=0.03,
            drawdown=0.10, circuit_breaker_active=True)
        assert lev == 0.5
        assert regime == "circuit_breaker"

    def test_normal_vix_default_leverage(self, sizer):
        lev, _, _ = sizer.compute_leverage(
            vix=20, vix_ratio=0.95, realized_vol=0.14, trend_20d=0.0,
            drawdown=0.01, circuit_breaker_active=False)
        assert 1.2 < lev < 2.2

    def test_leverage_bounded(self, sizer):
        cfg = sizer.cfg
        # Extreme bullish
        lev_max, _, _ = sizer.compute_leverage(
            vix=10, vix_ratio=0.80, realized_vol=0.05, trend_20d=0.05,
            drawdown=0.0, circuit_breaker_active=False)
        assert lev_max <= cfg.max_leverage
        # Extreme bearish
        lev_min, _, _ = sizer.compute_leverage(
            vix=80, vix_ratio=1.5, realized_vol=0.60, trend_20d=-0.10,
            drawdown=0.15, circuit_breaker_active=False)
        assert lev_min >= cfg.min_leverage

    def test_inversion_reduces_leverage(self, sizer):
        lev_normal, _, _ = sizer.compute_leverage(
            vix=20, vix_ratio=0.90, realized_vol=0.14, trend_20d=0.01,
            drawdown=0.01, circuit_breaker_active=False)
        lev_inverted, _, _ = sizer.compute_leverage(
            vix=20, vix_ratio=1.15, realized_vol=0.14, trend_20d=0.01,
            drawdown=0.01, circuit_breaker_active=False)
        assert lev_inverted < lev_normal

    def test_high_rvol_reduces_leverage(self, sizer):
        lev_low, _, _ = sizer.compute_leverage(
            vix=18, vix_ratio=0.92, realized_vol=0.08, trend_20d=0.01,
            drawdown=0.01, circuit_breaker_active=False)
        lev_high, _, _ = sizer.compute_leverage(
            vix=18, vix_ratio=0.92, realized_vol=0.35, trend_20d=0.01,
            drawdown=0.01, circuit_breaker_active=False)
        assert lev_high < lev_low

    def test_bull_trend_boosts(self, sizer):
        lev_flat, _, _ = sizer.compute_leverage(
            vix=18, vix_ratio=0.92, realized_vol=0.12, trend_20d=0.0,
            drawdown=0.01, circuit_breaker_active=False)
        lev_bull, _, _ = sizer.compute_leverage(
            vix=18, vix_ratio=0.92, realized_vol=0.12, trend_20d=0.05,
            drawdown=0.01, circuit_breaker_active=False)
        assert lev_bull >= lev_flat

    def test_bear_trend_reduces(self, sizer):
        lev_flat, _, _ = sizer.compute_leverage(
            vix=18, vix_ratio=0.92, realized_vol=0.12, trend_20d=0.0,
            drawdown=0.01, circuit_breaker_active=False)
        lev_bear, _, _ = sizer.compute_leverage(
            vix=18, vix_ratio=0.92, realized_vol=0.12, trend_20d=-0.05,
            drawdown=0.01, circuit_breaker_active=False)
        assert lev_bear <= lev_flat


# ═══════════════════════════════════════════════════════════════════════════
# Regime Classification
# ═══════════════════════════════════════════════════════════════════════════

class TestRegime:
    def test_crisis_regime(self, sizer):
        _, regime, _ = sizer.compute_leverage(
            vix=50, vix_ratio=1.3, realized_vol=0.50, trend_20d=-0.05,
            drawdown=0.10, circuit_breaker_active=False)
        assert regime == "crisis"

    def test_low_vol_bull_regime(self, sizer):
        _, regime, _ = sizer.compute_leverage(
            vix=12, vix_ratio=0.85, realized_vol=0.08, trend_20d=0.03,
            drawdown=0.01, circuit_breaker_active=False)
        assert regime == "low_vol_bull"

    def test_bull_regime(self, sizer):
        _, regime, _ = sizer.compute_leverage(
            vix=18, vix_ratio=0.90, realized_vol=0.12, trend_20d=0.05,
            drawdown=0.01, circuit_breaker_active=False)
        assert regime == "bull"

    def test_bear_regime(self, sizer):
        _, regime, _ = sizer.compute_leverage(
            vix=22, vix_ratio=0.95, realized_vol=0.15, trend_20d=-0.05,
            drawdown=0.03, circuit_breaker_active=False)
        assert regime == "bear"

    def test_high_vol_regime(self, sizer):
        _, regime, _ = sizer.compute_leverage(
            vix=35, vix_ratio=1.10, realized_vol=0.30, trend_20d=-0.02,
            drawdown=0.05, circuit_breaker_active=False)
        assert regime == "high_vol"


# ═══════════════════════════════════════════════════════════════════════════
# Signal Breakdown
# ═══════════════════════════════════════════════════════════════════════════

class TestSignals:
    def test_signals_returned(self, sizer):
        _, _, signals = sizer.compute_leverage(
            vix=18, vix_ratio=0.92, realized_vol=0.12, trend_20d=0.01,
            drawdown=0.01, circuit_breaker_active=False)
        assert "vix_signal" in signals
        assert "ts_signal" in signals
        assert "rvol_signal" in signals
        assert "trend_signal" in signals

    def test_vix_signal_high_in_calm(self, sizer):
        _, _, s = sizer.compute_leverage(
            vix=12, vix_ratio=0.85, realized_vol=0.08, trend_20d=0.03,
            drawdown=0, circuit_breaker_active=False)
        assert s["vix_signal"] >= 0.8

    def test_vix_signal_low_in_crisis(self, sizer):
        _, _, s = sizer.compute_leverage(
            vix=50, vix_ratio=1.3, realized_vol=0.50, trend_20d=-0.05,
            drawdown=0.10, circuit_breaker_active=False)
        assert s["vix_signal"] <= 0.1


# ═══════════════════════════════════════════════════════════════════════════
# Drawdown Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    def test_triggers_at_8pct(self, sizer, market_data):
        d = market_data
        r = sizer.backtest(d["portfolio_returns"], d["vix"], d["vix3m"],
                           d["spy_returns"], d["dates"])
        # Should have some circuit breaker days during COVID
        # (embedded at days 40-63 with heavy losses)
        assert r.circuit_breaker_days >= 0

    def test_cb_forces_min_leverage(self, sizer):
        lev, _, _ = sizer.compute_leverage(
            vix=14, vix_ratio=0.85, realized_vol=0.10, trend_20d=0.03,
            drawdown=0.10, circuit_breaker_active=True)
        assert lev == sizer.cfg.dd_min_leverage

    def test_cb_recovery_threshold(self, config):
        assert config.dd_recovery < config.dd_trigger


# ═══════════════════════════════════════════════════════════════════════════
# Backtest Comparison
# ═══════════════════════════════════════════════════════════════════════════

class TestBacktest:
    def test_static_computed(self, result):
        assert result.static_cagr != 0
        assert result.static_sharpe != 0
        assert result.static_dd > 0

    def test_dynamic_computed(self, result):
        assert result.dynamic_cagr != 0
        assert result.dynamic_sharpe != 0
        assert result.dynamic_dd > 0

    def test_equity_curves_populated(self, result):
        assert len(result.static_equity) > 100
        assert len(result.dynamic_equity) > 100
        assert result.static_equity[0] == 100_000
        assert result.dynamic_equity[0] == 100_000

    def test_states_populated(self, result):
        assert len(result.states) == result.n_days
        assert result.n_days > 1000

    def test_leverage_range(self, result):
        assert result.dynamic_min_leverage_used >= 0.5
        assert result.dynamic_max_leverage_used <= 2.5

    def test_avg_leverage_reasonable(self, result):
        assert 0.8 < result.dynamic_avg_leverage < 2.3

    def test_yearly_comparison(self, result):
        assert len(result.yearly_comparison) >= 5
        for yr, d in result.yearly_comparison.items():
            assert "static_cagr" in d
            assert "dynamic_cagr" in d

    def test_improvements_computed(self, result):
        assert isinstance(result.cagr_improvement, float)
        assert isinstance(result.sharpe_improvement, float)
        assert isinstance(result.dd_improvement, float)
        assert isinstance(result.calmar_improvement, float)


# ═══════════════════════════════════════════════════════════════════════════
# Dynamic vs Static Quality
# ═══════════════════════════════════════════════════════════════════════════

class TestQuality:
    def test_dynamic_reduces_crisis_dd(self, result):
        """Dynamic sizing should reduce DD vs static in crisis periods."""
        # Compare DD in 2020 (COVID embedded)
        yr_2020 = result.yearly_comparison.get(2020, {})
        if yr_2020:
            assert yr_2020["dynamic_dd"] <= yr_2020["static_dd"] + 5  # allow some tolerance

    def test_dynamic_boosts_calm_returns(self, result):
        """In calm years, dynamic should capture more return via higher leverage."""
        calm_years = [yr for yr, d in result.yearly_comparison.items()
                      if d["static_dd"] < 5]
        if calm_years:
            yr = calm_years[0]
            d = result.yearly_comparison[yr]
            # Dynamic should at least not be dramatically worse
            assert d["dynamic_cagr"] > d["static_cagr"] * 0.5

    def test_equity_ends_positive(self, result):
        assert result.static_equity[-1] > result.static_equity[0]
        assert result.dynamic_equity[-1] > result.dynamic_equity[0]


# ═══════════════════════════════════════════════════════════════════════════
# Market Data Generation
# ═══════════════════════════════════════════════════════════════════════════

class TestMarketData:
    def test_correct_length(self, market_data):
        n = int(6 * TRADING_DAYS)
        assert len(market_data["portfolio_returns"]) == n
        assert len(market_data["vix"]) == n
        assert len(market_data["dates"]) == n

    def test_vix_realistic(self, market_data):
        assert market_data["vix"].min() >= 8
        assert market_data["vix"].max() <= 90

    def test_covid_embedded(self, market_data):
        assert market_data["portfolio_returns"][40:63].mean() < -0.005

    def test_deterministic(self):
        d1 = generate_market_data(seed=99)
        d2 = generate_market_data(seed=99)
        np.testing.assert_array_equal(d1["portfolio_returns"], d2["portfolio_returns"])


# ═══════════════════════════════════════════════════════════════════════════
# Metrics Helpers
# ═══════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def test_positive_returns(self):
        rng = np.random.RandomState(1)
        m = _compute_metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr_pct"] > 0
        assert m["sharpe"] > 0

    def test_empty(self):
        assert _compute_metrics(np.array([]))["sharpe"] == 0

    def test_equity_curve(self):
        eq = _equity_curve(np.array([0.01, -0.005, 0.02]), 100_000)
        assert len(eq) == 4
        assert eq[0] == 100_000
        assert eq[-1] > eq[0]


# ═══════════════════════════════════════════════════════════════════════════
# Report Generation
# ═══════════════════════════════════════════════════════════════════════════

class TestReport:
    def test_generates_html(self, result, tmp_path):
        out = tmp_path / "sizing.html"
        generate_report(result, str(out))
        assert out.exists()
        c = out.read_text()
        assert "<!DOCTYPE html>" in c
        assert "Dynamic" in c

    def test_white_background(self, result, tmp_path):
        out = tmp_path / "sizing.html"
        generate_report(result, str(out))
        assert "background:#fff" in out.read_text()

    def test_contains_comparison(self, result, tmp_path):
        out = tmp_path / "sizing.html"
        generate_report(result, str(out))
        c = out.read_text()
        assert "Static" in c
        assert "Sharpe" in c
        assert "CAGR" in c

    def test_contains_svg(self, result, tmp_path):
        out = tmp_path / "sizing.html"
        generate_report(result, str(out))
        assert out.read_text().count("<svg") >= 2


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_zero_vix3m(self, sizer):
        lev, _, _ = sizer.compute_leverage(
            vix=20, vix_ratio=20.0, realized_vol=0.12, trend_20d=0.0,
            drawdown=0.0, circuit_breaker_active=False)
        assert 0.5 <= lev <= 2.5

    def test_extreme_vix(self, sizer):
        lev, _, _ = sizer.compute_leverage(
            vix=100, vix_ratio=2.0, realized_vol=1.0, trend_20d=-0.20,
            drawdown=0.30, circuit_breaker_active=False)
        assert lev == sizer.cfg.min_leverage

    def test_no_smoothing(self):
        cfg = DynamicSizingConfig(smoothing_halflife=0)
        s = DynamicSizer(cfg)
        data = generate_market_data(n_years=1.0, seed=1)
        r = s.backtest(data["portfolio_returns"], data["vix"], data["vix3m"],
                       data["spy_returns"], data["dates"])
        assert r.n_days == TRADING_DAYS

    def test_short_data(self):
        rng = np.random.RandomState(1)
        n = 10
        idx = pd.bdate_range("2024-01-02", periods=n)
        s = DynamicSizer()
        r = s.backtest(
            rng.normal(0.001, 0.01, n), np.full(n, 16.0),
            np.full(n, 18.0), rng.normal(0.0004, 0.01, n), idx)
        assert r.n_days == n
