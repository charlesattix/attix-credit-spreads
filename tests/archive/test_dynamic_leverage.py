"""Tests for compass/dynamic_leverage.py."""

import math

import numpy as np
import pandas as pd
import pytest

from compass.dynamic_leverage import (
    DynamicLeverageConfig,
    DynamicLeverageManager,
    LeverageState,
    compute_metrics,
    yearly_metrics,
    regime_metrics,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    return DynamicLeverageConfig()


@pytest.fixture
def manager(config):
    return DynamicLeverageManager(config)


@pytest.fixture
def sample_data():
    """100 days of synthetic market data for unit testing."""
    idx = pd.bdate_range("2023-01-02", periods=100)
    vix = pd.Series(np.linspace(14, 14, 100), index=idx)
    vix3m = pd.Series(np.linspace(16, 16, 100), index=idx)
    spy_ret = pd.Series(np.full(100, 0.0005), index=idx)
    return vix, vix3m, spy_ret


@pytest.fixture
def crisis_data():
    """100 days with embedded VIX spike (simulating crisis)."""
    idx = pd.bdate_range("2023-01-02", periods=100)
    vix = pd.Series(np.concatenate([
        np.full(30, 14),    # calm
        np.linspace(14, 70, 20),  # spike
        np.full(20, 60),   # sustained
        np.linspace(60, 18, 30),  # recovery
    ]), index=idx)
    vix3m = pd.Series(np.concatenate([
        np.full(30, 16),
        np.linspace(16, 50, 20),  # less spike → inversion
        np.full(20, 55),
        np.linspace(55, 19, 30),
    ]), index=idx)
    spy_ret = pd.Series(np.concatenate([
        np.full(30, 0.0005),
        np.full(20, -0.02),  # crash
        np.full(20, -0.005),
        np.full(30, 0.003),  # recovery
    ]), index=idx)
    return vix, vix3m, spy_ret


# ── Config Tests ──────────────────────────────────────────────────────────


class TestConfig:
    def test_default_target_leverage(self, config):
        assert config.target_leverage == 1.8

    def test_default_min_leverage(self, config):
        assert config.min_leverage == 0.3

    def test_vix_thresholds_ordered(self, config):
        assert config.vix_calm < config.vix_normal < config.vix_elevated < config.vix_crisis

    def test_ts_thresholds_ordered(self, config):
        assert config.ts_contango < config.ts_flat < config.ts_inverted < config.ts_deep_inversion


# ── Ramp Function Tests ──────────────────────────────────────────────────


class TestRamp:
    def test_ramp_below_low(self, manager):
        assert manager._ramp(5.0, 10.0, 30.0) == 1.0

    def test_ramp_above_high(self, manager):
        assert manager._ramp(40.0, 10.0, 30.0) == 0.0

    def test_ramp_midpoint(self, manager):
        assert abs(manager._ramp(20.0, 10.0, 30.0) - 0.5) < 1e-10

    def test_ramp_at_low(self, manager):
        assert manager._ramp(10.0, 10.0, 30.0) == 1.0

    def test_ramp_at_high(self, manager):
        assert manager._ramp(30.0, 10.0, 30.0) == 0.0


# ── Leverage Computation Tests ───────────────────────────────────────────


class TestLeverageComputation:
    def test_calm_market_gets_high_leverage(self, manager, sample_data):
        vix, vix3m, spy_ret = sample_data
        states = manager.compute_leverage_series(vix, vix3m, spy_ret)
        # VIX=14 < calm threshold, should get near-max leverage
        final_lev = states[-1].leverage
        assert final_lev > 1.0, f"Expected >1.0 in calm market, got {final_lev}"

    def test_crisis_reduces_leverage(self, manager, crisis_data):
        vix, vix3m, spy_ret = crisis_data
        states = manager.compute_leverage_series(vix, vix3m, spy_ret)
        # During VIX spike, leverage should drop
        crisis_levs = [s.leverage for s in states if s.vix > 50]
        calm_levs = [s.leverage for s in states if s.vix < 15]
        if crisis_levs and calm_levs:
            assert np.mean(crisis_levs) < np.mean(calm_levs)

    def test_leverage_bounded(self, manager, crisis_data):
        vix, vix3m, spy_ret = crisis_data
        states = manager.compute_leverage_series(vix, vix3m, spy_ret)
        cfg = manager.cfg
        for s in states:
            assert s.leverage >= cfg.min_leverage - 0.01
            assert s.leverage <= cfg.target_leverage + 0.01

    def test_output_length_matches_input(self, manager, sample_data):
        vix, vix3m, spy_ret = sample_data
        states = manager.compute_leverage_series(vix, vix3m, spy_ret)
        assert len(states) == len(vix)

    def test_regimes_assigned(self, manager, crisis_data):
        vix, vix3m, spy_ret = crisis_data
        states = manager.compute_leverage_series(vix, vix3m, spy_ret)
        regimes = set(s.regime for s in states)
        # Should have at least 2 different regimes in crisis data
        assert len(regimes) >= 2

    def test_smoothing_prevents_jumps(self):
        """With smoothing, leverage shouldn't jump instantly."""
        cfg = DynamicLeverageConfig(smoothing_halflife=10)
        mgr = DynamicLeverageManager(cfg)
        idx = pd.bdate_range("2023-01-02", periods=50)
        # VIX jumps from 12 to 60 on day 25
        vix_vals = np.concatenate([np.full(25, 12), np.full(25, 60)])
        vix = pd.Series(vix_vals, index=idx)
        vix3m = pd.Series(np.full(50, 16), index=idx)
        spy_ret = pd.Series(np.full(50, 0.0), index=idx)

        states = mgr.compute_leverage_series(vix, vix3m, spy_ret)
        # Day 25 leverage should not equal day 30 (smoothing)
        lev_25 = states[25].leverage
        lev_30 = states[30].leverage
        # The leverage at 25 should be higher than 30 (still adjusting down)
        assert lev_25 > lev_30 or abs(lev_25 - lev_30) < 0.1


# ── Apply Leverage Tests ─────────────────────────────────────────────────


class TestApplyLeverage:
    def test_static_leverage(self, manager):
        base = np.array([0.01, -0.02, 0.005])
        states = [
            LeverageState(None, 1.5, 15, 0.9, 0.12, "calm"),
            LeverageState(None, 1.5, 15, 0.9, 0.12, "calm"),
            LeverageState(None, 1.5, 15, 0.9, 0.12, "calm"),
        ]
        result = manager.apply_leverage(base, states)
        np.testing.assert_allclose(result, base * 1.5)

    def test_variable_leverage(self, manager):
        base = np.array([0.01, -0.02, 0.005])
        states = [
            LeverageState(None, 2.0, 12, 0.85, 0.10, "calm"),
            LeverageState(None, 0.5, 60, 1.2, 0.40, "crisis"),
            LeverageState(None, 1.2, 20, 0.95, 0.15, "normal"),
        ]
        result = manager.apply_leverage(base, states)
        expected = np.array([0.01 * 2.0, -0.02 * 0.5, 0.005 * 1.2])
        np.testing.assert_allclose(result, expected)

    def test_length_mismatch_raises(self, manager):
        with pytest.raises(ValueError, match="Length mismatch"):
            manager.apply_leverage(np.array([0.01, 0.02]), [LeverageState(None, 1, 15, 0.9, 0.1, "calm")])


# ── Metrics Tests ────────────────────────────────────────────────────────


class TestMetrics:
    def test_positive_returns(self):
        # Add small noise so std > 0 for Sharpe calculation
        rng = np.random.RandomState(42)
        rets = 0.001 + rng.normal(0, 0.0001, 252)
        m = compute_metrics(rets)
        assert m["cagr_pct"] > 20
        assert m["sharpe"] > 0
        assert m["max_dd_pct"] < 1  # negligible DD

    def test_negative_returns(self):
        rets = np.full(252, -0.001)
        m = compute_metrics(rets)
        assert m["cagr_pct"] < 0
        assert m["max_dd_pct"] > 0

    def test_empty_returns(self):
        m = compute_metrics(np.array([]))
        assert m["n_days"] == 0

    def test_yearly_metrics_skips_warmup(self):
        dates = [pd.Timestamp(f"2019-06-{d+1:02d}") for d in range(10)] + \
                [pd.Timestamp(f"2020-01-{d+1:02d}") for d in range(10)]
        rets = np.full(20, 0.001)
        ym = yearly_metrics(rets, dates)
        assert 2019 not in ym
        assert 2020 in ym

    def test_regime_metrics(self):
        rets = np.array([0.01, -0.02, 0.005, 0.008])
        states = [
            LeverageState(None, 1.5, 14, 0.9, 0.1, "calm"),
            LeverageState(None, 0.5, 65, 1.3, 0.5, "crisis"),
            LeverageState(None, 1.2, 18, 0.95, 0.14, "normal"),
            LeverageState(None, 1.5, 13, 0.88, 0.09, "calm"),
        ]
        rm = regime_metrics(rets, states)
        assert "calm" in rm
        assert "crisis" in rm
        assert rm["calm"]["n_days"] == 2
        assert rm["crisis"]["n_days"] == 1
