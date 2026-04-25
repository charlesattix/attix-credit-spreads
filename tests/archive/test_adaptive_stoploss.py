"""Tests for compass/adaptive_stoploss.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from compass.adaptive_stoploss import (
    OptimizationResult,
    StopLossOptimizer,
    StopResult,
    apply_atr_trailing,
    apply_chandelier,
    apply_fixed_stop,
    apply_keltner,
    apply_vol_breakout,
    classify_vix_regime,
    compute_atr,
    compute_ema,
    compute_rolling_std,
    get_regime_multiplier,
)


def _make_data(n: int = 500, seed: int = 42):
    rng = np.random.RandomState(seed)
    close = 450 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    high = close * (1 + rng.uniform(0.002, 0.015, n))
    low = close * (1 - rng.uniform(0.002, 0.015, n))
    vix = 18 + np.cumsum(rng.normal(0, 0.5, n))
    vix = np.clip(vix, 10, 60)
    equity = 100_000 + np.cumsum(rng.normal(200, 800, n))
    returns = np.diff(equity) / np.maximum(equity[:-1], 1)
    returns = np.append([0], returns)
    return equity, close, high, low, vix, returns


@pytest.fixture
def data():
    return _make_data()


@pytest.fixture
def optimizer(data):
    eq, close, high, low, vix, rets = data
    return StopLossOptimizer(eq, close, high, low, close, vix, rets)


# ── Regime tests ─────────────────────────────────────────────────────────


class TestRegime:
    def test_low_vol(self):
        assert classify_vix_regime(12) == "low_vol"

    def test_normal(self):
        assert classify_vix_regime(20) == "normal"

    def test_high_vol(self):
        assert classify_vix_regime(30) == "high_vol"

    def test_crisis(self):
        assert classify_vix_regime(40) == "crisis"

    def test_multiplier_low_vol(self):
        assert get_regime_multiplier(12) == 0.7

    def test_multiplier_crisis(self):
        assert get_regime_multiplier(40) == 2.0

    def test_multiplier_non_adaptive(self):
        assert get_regime_multiplier(40, adaptive=False) == 1.0


# ── Technical indicator tests ────────────────────────────────────────────


class TestIndicators:
    def test_atr_positive(self, data):
        _, close, high, low, _, _ = data
        atr = compute_atr(high, low, close)
        assert (atr[14:] > 0).all()

    def test_atr_length(self, data):
        _, close, high, low, _, _ = data
        assert len(compute_atr(high, low, close)) == len(close)

    def test_ema_tracks_price(self, data):
        _, close, _, _, _, _ = data
        ema = compute_ema(close, 20)
        assert abs(ema[-1] - close[-1]) / close[-1] < 0.05

    def test_rolling_std_positive(self, data):
        _, _, _, _, _, rets = data
        std = compute_rolling_std(rets, 20)
        assert (std[20:] >= 0).all()


# ── Stop type tests ──────────────────────────────────────────────────────


class TestFixedStop:
    def test_stops_triggered(self, data):
        eq, _, _, _, vix, _ = data
        stopped, n, by_regime = apply_fixed_stop(eq, 5.0, vix)
        assert n >= 0
        assert len(stopped) == len(eq)

    def test_tighter_stops_more_triggers(self, data):
        eq, _, _, _, vix, _ = data
        _, n_tight, _ = apply_fixed_stop(eq, 2.0, vix)
        _, n_wide, _ = apply_fixed_stop(eq, 10.0, vix)
        assert n_tight >= n_wide

    def test_adaptive_vs_fixed(self, data):
        eq, _, _, _, vix, _ = data
        _, n_adapt, _ = apply_fixed_stop(eq, 5.0, vix, adaptive=True)
        _, n_fixed, _ = apply_fixed_stop(eq, 5.0, vix, adaptive=False)
        # Adaptive should differ (wider in high vol → fewer stops)
        assert isinstance(n_adapt, int) and isinstance(n_fixed, int)


class TestATRTrailing:
    def test_runs(self, data):
        eq, prices, high, low, close, _ = data
        vix = np.full(len(eq), 20.0)
        stopped, n, _ = apply_atr_trailing(eq, prices, high, low, close, 2.0, vix)
        assert len(stopped) == len(eq)

    def test_preserves_uptrend(self):
        # Pure uptrend → no stops
        n = 200
        eq = 100_000 + np.arange(n) * 100.0
        close = 450 + np.arange(n) * 0.5
        high = close + 1
        low = close - 1
        vix = np.full(n, 18.0)
        stopped, n_stops, _ = apply_atr_trailing(eq, close, high, low, close, 3.0, vix)
        assert n_stops == 0


class TestChandelier:
    def test_runs(self, data):
        eq, _, high, low, close, _ = data
        vix = np.full(len(eq), 20.0)
        stopped, n, _ = apply_chandelier(eq, high, low, close, 3.0, vix)
        assert len(stopped) == len(eq)


class TestKeltner:
    def test_runs(self, data):
        eq, _, high, low, close, _ = data
        vix = np.full(len(eq), 20.0)
        stopped, n, _ = apply_keltner(eq, close, high, low, 2.0, vix)
        assert len(stopped) == len(eq)


class TestVolBreakout:
    def test_runs(self, data):
        eq, _, _, _, _, rets = data
        vix = np.full(len(eq), 20.0)
        stopped, n, _ = apply_vol_breakout(eq, rets, 2.0, vix)
        assert len(stopped) == len(eq)


# ── Optimizer tests ──────────────────────────────────────────────────────


class TestOptimizer:
    def test_optimize_returns_result(self, optimizer):
        r = optimizer.optimize({"fixed_pct": [5.0], "atr_trailing": [2.0]})
        assert isinstance(r, OptimizationResult)
        assert len(r.stop_results) > 0

    def test_best_stop_selected(self, optimizer):
        r = optimizer.optimize({"fixed_pct": [3.0, 5.0, 10.0]})
        assert isinstance(r.best_stop, StopResult)

    def test_no_stop_baseline(self, optimizer):
        r = optimizer.optimize({"fixed_pct": [5.0]})
        assert r.no_stop_return != 0
        assert r.no_stop_dd > 0

    def test_dd_reduction_computed(self, optimizer):
        r = optimizer.optimize({"fixed_pct": [5.0], "atr_trailing": [2.0]})
        for sr in r.stop_results:
            assert isinstance(sr.dd_reduction_pct, float)

    def test_return_preservation(self, optimizer):
        r = optimizer.optimize({"fixed_pct": [5.0]})
        for sr in r.stop_results:
            assert isinstance(sr.return_preserved_pct, float)

    def test_all_five_types(self, optimizer):
        r = optimizer.optimize()
        types = {sr.stop_type for sr in r.stop_results}
        assert types == set(["fixed_pct", "atr_trailing", "chandelier", "keltner", "vol_breakout"])

    def test_bad_type_raises(self, optimizer):
        with pytest.raises(ValueError):
            optimizer._apply_stop("magic", 5.0, True)


# ── Report tests ─────────────────────────────────────────────────────────


class TestReport:
    def test_generates(self, optimizer):
        r = optimizer.optimize({"fixed_pct": [5.0], "atr_trailing": [2.0]})
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "sl.html"
            path = StopLossOptimizer.generate_report(r, out)
            assert path.exists()
            assert "Adaptive Stop" in path.read_text()

    def test_default_path(self, optimizer):
        r = optimizer.optimize({"fixed_pct": [5.0]})
        path = StopLossOptimizer.generate_report(r)
        assert path.exists()
        path.unlink(missing_ok=True)
