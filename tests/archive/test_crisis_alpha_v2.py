"""Tests for compass/crisis_alpha_v2.py — EXP-1780 v2 grid search."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.crisis_alpha_v2 import (
    ASSET_UNIVERSE_V2, LOOKBACK_PRESETS, WEIGHTING_METHODS, LEVERAGE_LEVELS,
    ConfigResult, V2Result,
    compute_momentum_signal, compute_weights,
    backtest_config, run_full_grid, generate_report,
    TRADING_DAYS,
)


def _make_prices(n=800, seed=1, assets=None):
    """Deterministic test prices — not synthetic for backtest use, only for unit tests."""
    rng = np.random.RandomState(seed)
    assets = assets or ASSET_UNIVERSE_V2
    idx = pd.bdate_range("2020-01-02", periods=n)
    data = {}
    for i, tk in enumerate(assets):
        drift = 0.0002 + i * 0.00005
        rets = rng.normal(drift, 0.011, n)
        data[tk] = 100 * np.cumprod(1 + rets)
    return pd.DataFrame(data, index=idx)


class TestUniverse:
    def test_11_assets(self):
        assert len(ASSET_UNIVERSE_V2) == 11

    def test_all_required_tickers(self):
        for tk in ["SPY", "IWM", "EFA", "EEM", "TLT", "LQD", "HYG",
                   "GLD", "USO", "DBC", "UUP"]:
            assert tk in ASSET_UNIVERSE_V2

    def test_4_lookback_presets(self):
        assert len(LOOKBACK_PRESETS) == 4
        assert "v1_default" in LOOKBACK_PRESETS
        assert "v2_round" in LOOKBACK_PRESETS

    def test_3_weighting_methods(self):
        assert "equal_signal" in WEIGHTING_METHODS
        assert "risk_parity" in WEIGHTING_METHODS
        assert "vol_target" in WEIGHTING_METHODS

    def test_leverage_levels(self):
        assert 1.0 in LEVERAGE_LEVELS
        assert 1.5 in LEVERAGE_LEVELS
        assert 2.0 in LEVERAGE_LEVELS

    def test_preset_weights_sum_to_one(self):
        for name, (lookbacks, weights) in LOOKBACK_PRESETS.items():
            assert len(lookbacks) == len(weights), f"{name}: length mismatch"
            assert abs(sum(weights) - 1.0) < 0.01, f"{name}: weights don't sum to 1"


class TestMomentumSignal:
    def test_shape(self):
        prices = _make_prices(300)
        lookbacks, weights = LOOKBACK_PRESETS["v1_default"]
        sig = compute_momentum_signal(prices, lookbacks, weights)
        assert sig.shape == prices.shape

    def test_mismatched_lengths_raises(self):
        prices = _make_prices(300)
        with pytest.raises(ValueError, match="same length"):
            compute_momentum_signal(prices, [20, 60], [0.5])


class TestWeightings:
    def test_equal_signal(self):
        prices = _make_prices(400)
        lookbacks, lw = LOOKBACK_PRESETS["v1_default"]
        sig = compute_momentum_signal(prices, lookbacks, lw)
        w = compute_weights(prices, sig, "equal_signal", leverage=1.0)
        assert w.shape == prices.shape
        # Weights should be non-negative (long-only for equal_signal)
        assert (w >= 0).all().all()

    def test_risk_parity(self):
        prices = _make_prices(400)
        lookbacks, lw = LOOKBACK_PRESETS["v1_default"]
        sig = compute_momentum_signal(prices, lookbacks, lw)
        w = compute_weights(prices, sig, "risk_parity", leverage=1.0)
        assert w.shape == prices.shape
        assert (w >= 0).all().all()

    def test_vol_target(self):
        prices = _make_prices(400)
        lookbacks, lw = LOOKBACK_PRESETS["v1_default"]
        sig = compute_momentum_signal(prices, lookbacks, lw)
        w = compute_weights(prices, sig, "vol_target", leverage=1.5)
        assert w.shape == prices.shape
        # vol_target allows shorts
        # Just verify bounded gross exposure after warmup
        gross = w.iloc[250:].abs().sum(axis=1)
        assert gross.max() <= 1.5 + 1e-6

    def test_unknown_method_raises(self):
        prices = _make_prices(400)
        lookbacks, lw = LOOKBACK_PRESETS["v1_default"]
        sig = compute_momentum_signal(prices, lookbacks, lw)
        with pytest.raises(ValueError, match="Unknown weighting"):
            compute_weights(prices, sig, "bogus_method", leverage=1.0)

    def test_leverage_scales_weights(self):
        prices = _make_prices(400)
        lookbacks, lw = LOOKBACK_PRESETS["v1_default"]
        sig = compute_momentum_signal(prices, lookbacks, lw)
        w_1x = compute_weights(prices, sig, "equal_signal", leverage=1.0)
        w_2x = compute_weights(prices, sig, "equal_signal", leverage=2.0)
        # After warmup, 2x should have larger gross exposure
        assert w_2x.iloc[250:].abs().sum(axis=1).mean() > w_1x.iloc[250:].abs().sum(axis=1).mean()


class TestBacktestConfig:
    def test_basic(self):
        prices = _make_prices(800, seed=42)
        result = backtest_config(prices, "v1_default", "equal_signal", 1.0)
        assert isinstance(result, ConfigResult)
        assert result.n_assets == 11
        assert result.leverage == 1.0
        assert result.weighting == "equal_signal"

    def test_all_presets(self):
        prices = _make_prices(800, seed=1)
        for preset in LOOKBACK_PRESETS.keys():
            r = backtest_config(prices, preset, "equal_signal", 1.0)
            assert r.lookback_preset == preset

    def test_all_weightings(self):
        prices = _make_prices(800, seed=1)
        for wm in WEIGHTING_METHODS:
            r = backtest_config(prices, "v1_default", wm, 1.0)
            assert r.weighting == wm

    def test_leverage_sweep(self):
        prices = _make_prices(800, seed=1)
        results = [backtest_config(prices, "v1_default", "equal_signal", lv)
                   for lv in LEVERAGE_LEVELS]
        # Higher leverage should generally give larger absolute CAGR
        assert len(results) == 3

    def test_passes_target_flag(self):
        r = ConfigResult(
            name="x", lookback_preset="v1", weighting="eq", leverage=1.0,
            n_assets=11, cagr=10.0, sharpe=1.0, sortino=1.0, max_dd=10.0,
            calmar=1.0, vol=10.0, corr_to_spy=-0.1, crisis_avg_outperf=5.0,
            passes_target=True, yearly={}, wf_folds=[], equity=[],
        )
        assert r.passes_target

    def test_corr_computed(self):
        prices = _make_prices(800, seed=1)
        r = backtest_config(prices, "v1_default", "equal_signal", 1.0)
        assert -1 <= r.corr_to_spy <= 1

    def test_yearly_populated(self):
        prices = _make_prices(800, seed=1)
        r = backtest_config(prices, "v1_default", "equal_signal", 1.0)
        assert len(r.yearly) >= 1


class TestGridSearch:
    def test_runs_all_configs(self):
        prices = _make_prices(800, seed=1)
        result = run_full_grid(prices)
        expected = len(LOOKBACK_PRESETS) * len(WEIGHTING_METHODS) * len(LEVERAGE_LEVELS)
        assert len(result.all_configs) == expected

    def test_best_selected(self):
        prices = _make_prices(800, seed=1)
        result = run_full_grid(prices)
        assert result.best is not None
        assert result.best in result.all_configs

    def test_crisis_metrics_for_best(self):
        prices = _make_prices(800, seed=1)
        result = run_full_grid(prices)
        assert isinstance(result.crisis_metrics_best, list)

    def test_universe_size_reported(self):
        prices = _make_prices(800, seed=1)
        result = run_full_grid(prices)
        assert result.universe_size == 11


class TestReport:
    def test_generates(self, tmp_path):
        prices = _make_prices(800, seed=1)
        result = run_full_grid(prices)
        out = tmp_path / "v2.html"
        generate_report(result, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Crisis Alpha" in c
        assert "Grid Search" in c

    def test_contains_universe_info(self, tmp_path):
        prices = _make_prices(800, seed=2)
        result = run_full_grid(prices)
        out = tmp_path / "v2.html"
        generate_report(result, str(out))
        c = out.read_text()
        assert "11" in c
        assert "Yahoo" in c

    def test_contains_grid_table(self, tmp_path):
        prices = _make_prices(800, seed=3)
        result = run_full_grid(prices)
        out = tmp_path / "v2.html"
        generate_report(result, str(out))
        c = out.read_text()
        assert "equal_signal" in c
        assert "risk_parity" in c
        assert "vol_target" in c

    def test_contains_crisis_attribution(self, tmp_path):
        prices = _make_prices(800, seed=4)
        result = run_full_grid(prices)
        out = tmp_path / "v2.html"
        generate_report(result, str(out))
        assert "Crisis Period" in out.read_text()
