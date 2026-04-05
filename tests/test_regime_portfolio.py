"""Tests for compass/regime_portfolio.py — Regime-Adaptive Portfolio."""

import numpy as np
import pandas as pd
import pytest

from compass.regime_portfolio import (
    STRATEGY_IDS, STATIC_WEIGHTS, STATIC_LEVERAGE, REGIME_ALLOCATION,
    SHORT_NAMES, DayState, MethodResult, ComparisonResult,
    generate_data, run_method, run_comparison, generate_report,
    _metrics, _yearly, _equity, _dynamic_leverage, TRADING_DAYS,
)


@pytest.fixture
def data():
    return generate_data(seed=42)

@pytest.fixture
def comparison(data):
    return run_comparison(seed=42)


class TestAllocationTables:
    def test_five_strategies(self):
        assert len(STRATEGY_IDS) == 5

    def test_static_weights_sum_to_one(self):
        assert abs(sum(STATIC_WEIGHTS.values()) - 1.0) < 0.01

    def test_regime_weights_sum_to_one(self):
        for regime, alloc in REGIME_ALLOCATION.items():
            total = sum(alloc["weights"].values())
            assert abs(total - 1.0) < 0.01, f"{regime} weights sum to {total}"

    def test_all_regimes_defined(self):
        for r in ["bull", "bear", "crash", "high_vol", "low_vol"]:
            assert r in REGIME_ALLOCATION

    def test_bull_high_leverage(self):
        assert REGIME_ALLOCATION["bull"]["leverage"] >= 2.0

    def test_crash_low_leverage(self):
        assert REGIME_ALLOCATION["crash"]["leverage"] <= 0.5

    def test_bear_low_leverage(self):
        assert REGIME_ALLOCATION["bear"]["leverage"] <= 1.0

    def test_high_vol_moderate_leverage(self):
        assert REGIME_ALLOCATION["high_vol"]["leverage"] == 1.0

    def test_bull_overweights_exp1220(self):
        assert REGIME_ALLOCATION["bull"]["weights"]["EXP-1220_DynLev"] >= 0.60

    def test_crash_underweights_exp1220(self):
        assert REGIME_ALLOCATION["crash"]["weights"]["EXP-1220_DynLev"] <= 0.30

    def test_bear_overweights_pairs(self):
        assert REGIME_ALLOCATION["bear"]["weights"]["CrossAsset_Pairs"] >= 0.20

    def test_high_vol_overweights_volterm(self):
        assert REGIME_ALLOCATION["high_vol"]["weights"]["VolTermStructure"] >= 0.15


class TestDataGeneration:
    def test_all_strategies(self, data):
        for sid in STRATEGY_IDS:
            assert sid in data["strat_returns"]

    def test_correct_length(self, data):
        assert data["n"] == int(6 * TRADING_DAYS)
        for sid in STRATEGY_IDS:
            assert len(data["strat_returns"][sid]) == data["n"]

    def test_regimes_correct_length(self, data):
        assert len(data["regimes"]) == data["n"]

    def test_valid_regimes(self, data):
        valid = {"bull", "bear", "crash", "high_vol", "low_vol"}
        for r in data["regimes"]:
            assert r in valid

    def test_vix_realistic(self, data):
        assert data["vix"].min() >= 8
        assert data["vix"].max() <= 90

    def test_crisis_in_regimes(self, data):
        assert "crash" in data["regimes"]

    def test_deterministic(self):
        d1 = generate_data(seed=99)
        d2 = generate_data(seed=99)
        np.testing.assert_array_equal(
            d1["strat_returns"]["EXP-1220_DynLev"],
            d2["strat_returns"]["EXP-1220_DynLev"])


class TestRunMethod:
    def test_static(self, data):
        m = run_method("static", data["strat_returns"], data["regimes"],
                       data["dates"], data["vix"], data["spy_returns"], data["trend"])
        assert m.name == "static"
        assert m.sharpe != 0
        assert m.avg_leverage == STATIC_LEVERAGE

    def test_regime_adaptive(self, data):
        m = run_method("regime_adaptive", data["strat_returns"], data["regimes"],
                       data["dates"], data["vix"], data["spy_returns"], data["trend"])
        assert m.name == "regime_adaptive"
        assert m.sharpe != 0

    def test_dynamic_sizing(self, data):
        m = run_method("dynamic_sizing", data["strat_returns"], data["regimes"],
                       data["dates"], data["vix"], data["spy_returns"], data["trend"])
        assert m.name == "dynamic_sizing"
        assert m.sharpe != 0

    def test_equity_starts_at_capital(self, data):
        m = run_method("static", data["strat_returns"], data["regimes"],
                       data["dates"], data["vix"], data["spy_returns"], data["trend"])
        assert m.equity[0] == 100_000

    def test_states_populated(self, data):
        m = run_method("regime_adaptive", data["strat_returns"], data["regimes"],
                       data["dates"], data["vix"], data["spy_returns"], data["trend"])
        assert len(m.states) == data["n"]

    def test_yearly_populated(self, data):
        m = run_method("static", data["strat_returns"], data["regimes"],
                       data["dates"], data["vix"], data["spy_returns"], data["trend"])
        assert len(m.yearly) >= 5


class TestComparison:
    def test_three_methods(self, comparison):
        assert len(comparison.methods) == 3
        assert "static" in comparison.methods
        assert "regime_adaptive" in comparison.methods
        assert "dynamic_sizing" in comparison.methods

    def test_winner_identified(self, comparison):
        assert comparison.winner_sharpe in comparison.methods
        assert comparison.winner_calmar in comparison.methods

    def test_regime_distribution(self, comparison):
        total = sum(comparison.regime_distribution.values())
        assert total == comparison.n_days


class TestDynamicLeverage:
    def test_crash_low(self):
        assert _dynamic_leverage(50, -0.05, "crash") == 0.5

    def test_high_vix_low(self):
        assert _dynamic_leverage(35, 0.0, "bull") == 0.5

    def test_low_vix_bull_high(self):
        assert _dynamic_leverage(12, 0.03, "low_vol") >= 2.0

    def test_default(self):
        assert _dynamic_leverage(20, 0.0, "bull") == 1.6

    def test_bear_trend_reduces(self):
        assert _dynamic_leverage(22, -0.05, "bear") == 0.8


class TestMetrics:
    def test_positive(self):
        rng = np.random.RandomState(1)
        m = _metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr"] > 0
        assert m["sharpe"] > 0

    def test_empty(self):
        assert _metrics(np.array([]))["sharpe"] == 0

    def test_equity_fn(self):
        eq = _equity(np.array([0.01, -0.005, 0.02]), 100_000)
        assert len(eq) == 4
        assert eq[0] == 100_000


class TestReport:
    def test_generates(self, comparison, tmp_path):
        out = tmp_path / "regime_port.html"
        generate_report(comparison, str(out))
        assert out.exists()
        c = out.read_text()
        assert "<!DOCTYPE html>" in c
        assert "Regime-Adaptive" in c

    def test_contains_methods(self, comparison, tmp_path):
        out = tmp_path / "r.html"
        generate_report(comparison, str(out))
        c = out.read_text()
        assert "Static" in c
        assert "Regime" in c
        assert "Dynamic" in c

    def test_contains_svg(self, comparison, tmp_path):
        out = tmp_path / "r.html"
        generate_report(comparison, str(out))
        assert "<svg" in out.read_text()

    def test_contains_yearly(self, comparison, tmp_path):
        out = tmp_path / "r.html"
        generate_report(comparison, str(out))
        assert "2020" in out.read_text()
