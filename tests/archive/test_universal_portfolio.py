"""Tests for compass.universal_portfolio — Cover's Universal Portfolio via EG."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from compass.universal_portfolio import (
    EGState,
    ExponentialGradient,
    MethodResult,
    RegretAnalysis,
    UniversalPortfolioBacktest,
    UniversalPortfolioResult,
    best_crp,
    generate_strategy_returns,
)


def _data(n=500, ns=4, seed=42):
    return generate_strategy_returns(n, ns, seed)


# ── ExponentialGradient ─────────────────────────────────────────────────────
class TestExponentialGradient:
    def test_initial_equal_weight(self):
        eg = ExponentialGradient(4)
        w = eg.get_weights()
        np.testing.assert_allclose(w, [0.25] * 4, atol=0.01)

    def test_weights_sum_to_one(self):
        eg = ExponentialGradient(5)
        for _ in range(10):
            eg.update(np.random.randn(5) * 0.01)
        assert eg.get_weights().sum() == pytest.approx(1.0, abs=0.01)

    def test_weights_positive(self):
        eg = ExponentialGradient(4)
        for _ in range(20):
            eg.update(np.random.randn(4) * 0.01)
        assert np.all(eg.get_weights() > 0)

    def test_shifts_toward_winner(self):
        eg = ExponentialGradient(3, learning_rate=0.5)
        for _ in range(50):
            eg.update(np.array([0.02, -0.01, 0.005]))
        w = eg.get_weights()
        assert w[0] > w[1]  # strategy 0 consistently wins

    def test_returns_portfolio_return(self):
        eg = ExponentialGradient(3)
        ret = eg.update(np.array([0.01, -0.005, 0.002]))
        assert isinstance(ret, float)

    def test_time_step_increments(self):
        eg = ExponentialGradient(3)
        assert eg.time_step == 0
        eg.update(np.array([0.01, 0.01, 0.01]))
        assert eg.time_step == 1

    def test_reset(self):
        eg = ExponentialGradient(3)
        eg.update(np.array([0.05, -0.01, 0.02]))
        eg.reset()
        w = eg.get_weights()
        np.testing.assert_allclose(w, [1/3] * 3, atol=0.01)
        assert eg.time_step == 0

    def test_handles_zero_returns(self):
        eg = ExponentialGradient(3)
        ret = eg.update(np.array([0.0, 0.0, 0.0]))
        assert math.isfinite(ret)
        assert eg.get_weights().sum() == pytest.approx(1.0, abs=0.01)

    def test_handles_large_returns(self):
        eg = ExponentialGradient(3, learning_rate=0.1)
        eg.update(np.array([0.10, -0.08, 0.05]))
        w = eg.get_weights()
        assert np.all(np.isfinite(w))
        assert w.sum() == pytest.approx(1.0, abs=0.01)

    def test_many_strategies(self):
        eg = ExponentialGradient(15)
        for _ in range(30):
            eg.update(np.random.randn(15) * 0.01)
        assert len(eg.get_weights()) == 15
        assert eg.get_weights().sum() == pytest.approx(1.0, abs=0.01)


# ── Best CRP ────────────────────────────────────────────────────────────────
class TestBestCRP:
    def test_returns_weights_and_log_wealth(self):
        ret = np.random.randn(100, 3) * 0.01 + 0.001
        w, lw = best_crp(ret)
        assert len(w) == 3
        assert isinstance(lw, float)

    def test_weights_sum_to_one(self):
        ret = np.random.randn(100, 3) * 0.01
        w, _ = best_crp(ret)
        assert w.sum() == pytest.approx(1.0, abs=0.05)

    def test_single_strategy(self):
        ret = np.random.randn(100, 1) * 0.01 + 0.001
        w, lw = best_crp(ret)
        assert w[0] == pytest.approx(1.0)

    def test_positive_log_wealth_for_positive_returns(self):
        ret = np.abs(np.random.randn(100, 3)) * 0.005 + 0.001
        _, lw = best_crp(ret)
        assert lw > 0

    def test_high_dimension(self):
        ret = np.random.randn(100, 8) * 0.01
        w, lw = best_crp(ret, n_grid=30)
        assert len(w) == 8
        assert math.isfinite(lw)


# ── Full backtest ───────────────────────────────────────────────────────────
class TestBacktest:
    def test_returns_result(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        assert isinstance(r, UniversalPortfolioResult)

    def test_eg_history_populated(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        assert len(r.eg_history) == 200

    def test_regret_computed(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        assert r.regret is not None
        assert isinstance(r.regret.regret, float)

    def test_regret_per_round_small(self):
        """EG should have sublinear regret → regret/T → 0."""
        r = UniversalPortfolioBacktest().run(_data(500, 3))
        assert r.regret.regret_per_round < 0.1  # should be small

    def test_four_comparisons(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        assert len(r.comparisons) == 4
        methods = {c.method for c in r.comparisons}
        assert "exponential_gradient" in methods
        assert "equal_weight" in methods
        assert "risk_parity" in methods
        assert "thompson_sampling" in methods

    def test_best_method_set(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        assert r.best_method in {c.method for c in r.comparisons}

    def test_n_strategies(self):
        r = UniversalPortfolioBacktest().run(_data(200, 5))
        assert r.n_strategies == 5

    def test_sharpe_finite(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        for c in r.comparisons:
            assert math.isfinite(c.sharpe)

    def test_max_dd_nonneg(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        for c in r.comparisons:
            assert c.max_dd_pct >= 0

    def test_log_wealth_consistent(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        eg = next(c for c in r.comparisons if c.method == "exponential_gradient")
        # log(final_wealth) should ≈ log_wealth
        assert abs(math.log(max(eg.final_wealth, 1e-10)) - eg.log_wealth) < 0.1

    def test_generated_at(self):
        r = UniversalPortfolioBacktest().run(_data(100, 3))
        assert len(r.generated_at) > 0

    def test_too_short(self):
        r = UniversalPortfolioBacktest().run(_data(10, 3))
        assert len(r.comparisons) == 0

    def test_single_strategy(self):
        r = UniversalPortfolioBacktest().run(_data(100, 1))
        assert len(r.comparisons) == 0

    def test_crp_weights_in_regret(self):
        r = UniversalPortfolioBacktest().run(_data(200, 3))
        assert len(r.regret.crp_weights) == 3
        assert sum(r.regret.crp_weights.values()) == pytest.approx(1.0, abs=0.1)


# ── Synthetic data ──────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_shape(self):
        df = generate_strategy_returns(100, 5)
        assert df.shape == (100, 5)

    def test_deterministic(self):
        a = generate_strategy_returns(50, 3, seed=99)
        b = generate_strategy_returns(50, 3, seed=99)
        pd.testing.assert_frame_equal(a, b)


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_eg_state(self):
        s = EGState(5, {"A": 0.5, "B": 0.5}, 0.003, 1.015, 0.02)
        assert s.t == 5

    def test_regret_analysis(self):
        r = RegretAnalysis(0.15, 0.18, 0.03, {"A": 0.6, "B": 0.4}, 0.00015)
        assert r.regret == 0.03

    def test_method_result(self):
        m = MethodResult("eg", 1.5, 50.0, 8.0, 2.5, 5.0, 0.3, 0.41)
        assert m.sharpe == 2.5

    def test_result_defaults(self):
        r = UniversalPortfolioResult()
        assert r.eg_history == []
        assert r.best_method == ""
