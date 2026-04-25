"""Tests for compass/vrp_harvester.py — volatility risk premium harvester."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.vrp_harvester import (
    BacktestResult, GammaScalpResult, HarvestTrade, TenorVRP,
    TermStructure, VRPConfig, VRPHarvester,
    classify_regime_from_vix, compute_vrp, gamma_scalp_pnl,
    implied_vol_proxy, realised_vol,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _market(n=500, seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = 430 + rng.normal(0, 1.5, n).cumsum()
    vix = 18 + rng.normal(0, 2, n).cumsum() * 0.1
    vix = np.clip(vix, 10, 50)
    regime = np.array(["bull"] * (n // 4) + ["neutral"] * (n // 4)
                      + ["bear"] * (n // 4) + ["high_vol"] * (n - 3 * (n // 4)), dtype=object)
    return pd.DataFrame({"close": close, "vix": vix, "regime": regime}, index=dates)

def _harvester(n=500, seed=42, **kw):
    return VRPHarvester(_market(n, seed), VRPConfig(**kw))

# ── Core computations ────────────────────────────────────────────────────

class TestRealisedVol:
    def test_returns_array(self):
        rets = np.random.RandomState(42).normal(0, 0.01, 100)
        rv = realised_vol(rets, 20)
        assert len(rv) == 100
    def test_nan_before_window(self):
        rv = realised_vol(np.random.randn(50) * 0.01, 20)
        assert np.isnan(rv[5])
        assert not np.isnan(rv[25])
    def test_positive(self):
        rv = realised_vol(np.random.randn(100) * 0.01, 20)
        valid = rv[~np.isnan(rv)]
        assert np.all(valid >= 0)

class TestImpliedVolProxy:
    def test_returns_array(self):
        vix = np.array([18.0, 20.0, 25.0])
        iv = implied_vol_proxy(vix, 21)
        assert len(iv) == 3
    def test_positive(self):
        iv = implied_vol_proxy(np.array([20.0]), 21)
        assert iv[0] > 0
    def test_short_tenor_higher(self):
        iv_short = implied_vol_proxy(np.array([20.0]), 5)
        iv_long = implied_vol_proxy(np.array([20.0]), 42)
        assert iv_short[0] > iv_long[0]

class TestComputeVRP:
    def test_positive_when_iv_gt_rv(self):
        iv = np.array([0.20, 0.25])
        rv = np.array([0.15, 0.18])
        vrp = compute_vrp(iv, rv)
        assert np.all(vrp > 0)
    def test_negative_when_rv_gt_iv(self):
        vrp = compute_vrp(np.array([0.15]), np.array([0.20]))
        assert vrp[0] < 0

class TestGammaScalp:
    def test_positive_pnl(self):
        changes = np.array([1.0, -1.5, 2.0, -0.5])
        pnl, cost, n = gamma_scalp_pnl(changes, 0.01, 0.5, 1)
        assert pnl > 0
    def test_cost_scales_with_frequency(self):
        changes = np.array([1.0] * 20)
        _, c1, n1 = gamma_scalp_pnl(changes, 0.01, 1.0, 1)
        _, c5, n5 = gamma_scalp_pnl(changes, 0.01, 1.0, 5)
        assert c1 > c5
    def test_rebalance_count(self):
        changes = np.array([1.0] * 20)
        _, _, n = gamma_scalp_pnl(changes, 0.01, 1.0, 5)
        assert n == 4

class TestRegimeClassifier:
    def test_crash(self):
        assert classify_regime_from_vix(40) == "crash"
    def test_high_vol(self):
        assert classify_regime_from_vix(30) == "high_vol"
    def test_neutral(self):
        assert classify_regime_from_vix(22) == "neutral"
    def test_bull(self):
        assert classify_regime_from_vix(15) == "bull"

# ── Term structure analysis ──────────────────────────────────────────────

class TestAnalyze:
    def test_returns_list(self):
        h = _harvester(200)
        ts = h.analyze()
        assert isinstance(ts, list)
        assert len(ts) > 0
    def test_term_structure_has_tenors(self):
        h = _harvester(200)
        ts = h.analyze()
        assert len(ts[0].tenors) == 4
    def test_optimal_tenor_valid(self):
        h = _harvester(200)
        ts = h.analyze()
        for t in ts:
            assert t.optimal_tenor in ("1W", "2W", "1M", "2M")
    def test_curve_shape_valid(self):
        h = _harvester(200)
        ts = h.analyze()
        for t in ts:
            assert t.curve_shape in ("contango", "flat", "backwardation")
    def test_tenor_vrp_fields(self):
        h = _harvester(200)
        ts = h.analyze()
        tv = ts[0].tenors[0]
        assert isinstance(tv, TenorVRP)
        assert tv.implied_vol > 0
    def test_signal_values(self):
        h = _harvester(200)
        ts = h.analyze()
        for t in ts:
            assert t.overall_signal in ("sell_vol", "neutral")

# ── Backtest ─────────────────────────────────────────────────────────────

class TestBacktest:
    def test_returns_result(self):
        h = _harvester(500)
        bt = h.backtest()
        assert isinstance(bt, BacktestResult)
    def test_has_trades(self):
        h = _harvester(500)
        bt = h.backtest()
        assert bt.n_trades > 0
    def test_win_rate_range(self):
        h = _harvester(500)
        bt = h.backtest()
        assert 0 <= bt.win_rate <= 1
    def test_sharpe_finite(self):
        h = _harvester(500)
        bt = h.backtest()
        assert np.isfinite(bt.sharpe)
    def test_max_dd_negative(self):
        h = _harvester(500)
        bt = h.backtest()
        assert bt.max_dd <= 0
    def test_by_tenor_populated(self):
        h = _harvester(500)
        bt = h.backtest()
        assert len(bt.by_tenor) > 0
    def test_by_regime_populated(self):
        h = _harvester(500)
        bt = h.backtest()
        assert len(bt.by_regime) > 0
    def test_trades_have_fields(self):
        h = _harvester(500)
        bt = h.backtest()
        t = bt.trades[0]
        assert isinstance(t, HarvestTrade)
        assert t.tenor in ("1W", "2W", "1M", "2M")
    def test_scalp_pnl_tracked(self):
        h = _harvester(500)
        bt = h.backtest()
        assert isinstance(bt.total_scalp_pnl, float)
    def test_cost_tracked(self):
        h = _harvester(500)
        bt = h.backtest()
        assert bt.total_cost >= 0
    def test_correlation_range(self):
        h = _harvester(500)
        bt = h.backtest()
        assert -1 <= bt.correlation_with_spy <= 1
    def test_vrp_positive_pct_range(self):
        h = _harvester(500)
        bt = h.backtest()
        assert 0 <= bt.vrp_positive_pct <= 1
    def test_auto_analyzes(self):
        h = _harvester(500)
        assert len(h.term_structures) == 0
        h.backtest()
        assert len(h.term_structures) > 0
    def test_regime_sizing_affects_bear(self):
        """Bear regime should have smaller positions."""
        h = _harvester(500)
        bt = h.backtest()
        bear_trades = [t for t in bt.trades if t.regime == "bear"]
        bull_trades = [t for t in bt.trades if t.regime == "bull"]
        if bear_trades and bull_trades:
            avg_bear = np.mean([t.position_size for t in bear_trades])
            avg_bull = np.mean([t.position_size for t in bull_trades])
            assert avg_bear <= avg_bull

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_short_data(self):
        h = _harvester(n=50)
        ts = h.analyze()
        assert isinstance(ts, list)
    def test_flat_vix(self):
        df = _market(200)
        df["vix"] = 18.0
        h = VRPHarvester(df)
        bt = h.backtest()
        assert isinstance(bt, BacktestResult)
    def test_no_regime_column(self):
        df = _market(200).drop(columns=["regime"])
        h = VRPHarvester(df)
        bt = h.backtest()
        assert bt.n_trades >= 0
    def test_crash_regime_no_trades(self):
        """Crash sizing = 0 → no trades in crash."""
        df = _market(100)
        df["regime"] = "crash"
        h = VRPHarvester(df)
        bt = h.backtest()
        assert bt.n_trades == 0
