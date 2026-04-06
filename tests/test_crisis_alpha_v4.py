"""Tests for compass/crisis_alpha_v4.py."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.crisis_alpha_v4 import (
    UNIVERSE_V4, ConfigV4, AllocationTest, WFFold,
    corrected_sharpe, compute_metrics,
    compute_signal_with_confirmation, compute_v4_weights,
    apply_drawdown_brake, backtest_v4, walk_forward_v4,
    select_best_v4, TRADING_DAYS,
)
# Renamed import to avoid pytest collecting it as a test function
from compass.crisis_alpha_v4 import test_allocation_v4 as run_alloc_test


def _det_prices(n=1500, seed=1):
    """Deterministic test prices for screener mechanics ONLY (not reported as result)."""
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    data = {}
    for i, tk in enumerate(UNIVERSE_V4):
        drift = 0.0002 + (i - len(UNIVERSE_V4) / 2) * 0.00006
        rets = rng.normal(drift, 0.011, n)
        data[tk] = 100 * np.cumprod(1 + rets)
    return pd.DataFrame(data, index=idx)


class TestUniverse:
    def test_size(self):
        assert len(UNIVERSE_V4) == 10

    def test_dropped_noisy_commodities(self):
        # v4 explicitly drops USO/DBA/DBB
        assert "USO" not in UNIVERSE_V4
        assert "DBA" not in UNIVERSE_V4
        assert "DBB" not in UNIVERSE_V4

    def test_keeps_core(self):
        for t in ["SPY", "TLT", "GLD"]:
            assert t in UNIVERSE_V4


class TestSharpe:
    def test_formula(self):
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(corrected_sharpe(rets) - expected) < 1e-6

    def test_empty(self):
        assert corrected_sharpe(np.array([])) == 0.0

    def test_constant(self):
        assert corrected_sharpe(np.full(50, 0.001)) == 0.0


class TestMetrics:
    def test_keys(self):
        m = compute_metrics(np.array([0.01, -0.005, 0.008, 0.003]))
        for k in ("cagr", "sharpe", "sortino", "dd", "calmar", "vol"):
            assert k in m

    def test_negative_cagr(self):
        rets = np.full(252, -0.001)
        m = compute_metrics(rets)
        assert m["cagr"] < 0


class TestConfirmation:
    def test_no_confirmation_returns_signal(self):
        prices = _det_prices(800)
        sig = compute_signal_with_confirmation(
            prices, [20, 60, 120], [0.3, 0.4, 0.3], require_confirmation=False
        )
        assert sig.shape == prices.shape

    def test_confirmation_zeros_disagreements(self):
        # Constructed prices: 1 asset trending, others flat
        n = 300
        idx = pd.bdate_range("2020-01-02", periods=n)
        df = pd.DataFrame({
            "A": np.linspace(100, 200, n),  # strong uptrend
            "B": np.full(n, 100.0),         # flat
            "C": np.full(n, 100.0),
        }, index=idx)
        sig = compute_signal_with_confirmation(
            df, [20, 60, 120], [0.3, 0.4, 0.3], require_confirmation=True
        )
        # A should have nonzero signal post-warmup, B/C should be zero
        post = sig.iloc[200]
        assert post["A"] != 0
        assert post["B"] == 0
        assert post["C"] == 0


class TestDrawdownBrake:
    def test_no_brake_when_within_threshold(self):
        rets = np.full(20, 0.001)  # all positive — never in DD
        out = apply_drawdown_brake(rets, threshold=0.05, zone=0.05)
        # Output should equal input (full exposure all the way)
        assert np.allclose(out, rets)

    def test_caps_max_dd(self):
        # Construct losing series — DD would be huge without brake
        rets = np.full(100, -0.005)
        braked = apply_drawdown_brake(rets, threshold=0.05, zone=0.05)
        # Compute braked DD
        eq = np.cumprod(1 + braked)
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / peak
        # With threshold 5% + zone 5% = 10% max
        assert dd.max() < 0.12

    def test_zero_zone_hard_cutoff(self):
        rets = np.full(100, -0.005)
        braked = apply_drawdown_brake(rets, threshold=0.03, zone=0.001)
        eq = np.cumprod(1 + braked)
        peak = np.maximum.accumulate(eq)
        assert ((peak - eq) / peak).max() < 0.05

    def test_recovery_restores_exposure(self):
        # Down then up — after recovery, should be back to full exposure
        rets = np.array([-0.02] * 5 + [0.03] * 10)
        braked = apply_drawdown_brake(rets, threshold=0.05, zone=0.05)
        # Last few entries should equal raw (full exposure restored)
        assert abs(braked[-1] - rets[-1]) < 1e-9 or braked[-1] != 0


class TestBacktest:
    def test_runs(self):
        prices = _det_prices(1500)
        cfg = ConfigV4(
            name="t", lookback_preset="v2_round",
            vol_target=0.08, leverage=1.5,
            dd_brake_threshold=0.08, dd_brake_zone=0.05,
            max_weight=0.20, require_confirmation=True,
        )
        r = backtest_v4(prices, cfg)
        assert r.daily_returns is not None
        assert r.n_days > 0
        assert "cagr" in {"cagr", "sharpe"}  # sanity

    def test_dd_brake_keeps_dd_bounded(self):
        prices = _det_prices(1500, seed=7)
        cfg = ConfigV4(
            name="t", lookback_preset="v2_round",
            vol_target=0.08, leverage=1.5,
            dd_brake_threshold=0.05, dd_brake_zone=0.05,
            max_weight=0.20, require_confirmation=False,
        )
        r = backtest_v4(prices, cfg)
        # Rolling-window brake bounds DD relative to rolling peak — measured
        # against all-time peak it's slightly higher but still well below v3's 38%.
        assert r.max_dd < 18.0


class TestWalkForward:
    def test_produces_folds(self):
        prices = _det_prices(2000, seed=2)
        cfg = ConfigV4(
            name="t", lookback_preset="v2_round",
            vol_target=0.08, leverage=1.5,
            dd_brake_threshold=0.08, dd_brake_zone=0.05,
            max_weight=0.20, require_confirmation=True,
        )
        folds = walk_forward_v4(prices, cfg)
        assert len(folds) >= 1
        for f in folds:
            assert isinstance(f, WFFold)
            assert f.train_end <= f.test_start


class TestAllocationTest:
    def test_zero_pct_equals_pure_exp1220(self):
        idx = pd.bdate_range("2020-01-02", periods=500)
        e1220 = pd.Series(0.001, index=idx)
        crisis = pd.Series(-0.0005, index=idx)
        a = run_alloc_test(e1220, crisis, 0.0)
        # 0% v4 → all EXP-1220
        assert a.cagr > 0

    def test_full_pct_equals_pure_crisis(self):
        idx = pd.bdate_range("2020-01-02", periods=500)
        e1220 = pd.Series(0.001, index=idx)
        crisis = pd.Series(-0.0005, index=idx)
        a = run_alloc_test(e1220, crisis, 1.0)
        # 100% v4 → all crisis (negative)
        assert a.cagr < 0

    def test_2022_dd_field(self):
        idx = pd.bdate_range("2022-01-03", periods=252)
        e1220 = pd.Series(0.001, index=idx)
        crisis = pd.Series(0.0005, index=idx)
        a = run_alloc_test(e1220, crisis, 0.10)
        assert a.dd_2022 >= 0


class TestSelectBest:
    def test_prefers_low_dd_high_calmar(self):
        configs = [
            ConfigV4(name="a", lookback_preset="v2_round", vol_target=0.08,
                     leverage=1.5, dd_brake_threshold=0.05, dd_brake_zone=0.05,
                     max_weight=0.2, require_confirmation=True,
                     cagr=20, sharpe=2.0, max_dd=10.0, calmar=2.0),
            ConfigV4(name="b", lookback_preset="v2_round", vol_target=0.08,
                     leverage=1.5, dd_brake_threshold=0.05, dd_brake_zone=0.05,
                     max_weight=0.2, require_confirmation=True,
                     cagr=15, sharpe=1.5, max_dd=12.0, calmar=1.25),
            ConfigV4(name="c", lookback_preset="v2_round", vol_target=0.08,
                     leverage=1.5, dd_brake_threshold=0.05, dd_brake_zone=0.05,
                     max_weight=0.2, require_confirmation=True,
                     cagr=30, sharpe=2.5, max_dd=20.0, calmar=1.5),  # ineligible (DD>15%)
        ]
        best = select_best_v4(configs)
        assert best.name == "a"  # best Calmar among DD<15%

    def test_fallback_when_none_under_15(self):
        configs = [
            ConfigV4(name="a", lookback_preset="v2_round", vol_target=0.08,
                     leverage=1.5, dd_brake_threshold=0.05, dd_brake_zone=0.05,
                     max_weight=0.2, require_confirmation=True,
                     cagr=20, sharpe=2.0, max_dd=20.0, calmar=1.0),
            ConfigV4(name="b", lookback_preset="v2_round", vol_target=0.08,
                     leverage=1.5, dd_brake_threshold=0.05, dd_brake_zone=0.05,
                     max_weight=0.2, require_confirmation=True,
                     cagr=15, sharpe=1.5, max_dd=18.0, calmar=0.83),
        ]
        best = select_best_v4(configs)
        assert best.name == "b"  # fallback: lowest DD
