"""Tests for compass.bayesian_selector — Thompson Sampling strategy selection."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compass.bayesian_selector import (
    AllocationResult,
    BayesianBacktest,
    BayesianSelector,
    BayesianSelectorResult,
    ComparisonResult,
    NIGPosterior,
    RegretPoint,
    StrategyArm,
    equal_weight,
    generate_strategy_returns,
    markowitz,
    risk_parity,
)


def _data(n: int = 500, ns: int = 4, seed: int = 42) -> pd.DataFrame:
    return generate_strategy_returns(n, ns, seed)


# ── NIGPosterior ────────────────────────────────────────────────────────────
class TestNIGPosterior:
    def test_prior_defaults(self):
        p = NIGPosterior()
        assert p.mu == 0.0
        assert p.kappa == 1.0

    def test_update_shifts_mean(self):
        p = NIGPosterior()
        p.update(0.01)
        assert p.mu > 0  # shifted toward positive observation

    def test_kappa_increases(self):
        p = NIGPosterior()
        old_k = p.kappa
        p.update(0.01)
        assert p.kappa > old_k

    def test_alpha_increases(self):
        p = NIGPosterior()
        old_a = p.alpha
        p.update(0.01)
        assert p.alpha > old_a

    def test_batch_update(self):
        p = NIGPosterior()
        p.update_batch(np.array([0.01, 0.02, -0.005, 0.015]))
        assert p.n_observations == 4

    def test_sample_mean_returns_float(self):
        p = NIGPosterior()
        p.update_batch(np.array([0.01] * 10))
        rng = np.random.RandomState(42)
        s = p.sample_mean(rng)
        assert isinstance(s, float)

    def test_variance_positive(self):
        p = NIGPosterior()
        p.update_batch(np.array([0.01, -0.01, 0.02, -0.005]))
        assert p.variance_estimate() > 0

    def test_sharpe_estimate(self):
        p = NIGPosterior()
        p.update_batch(np.array([0.001] * 100))  # consistent positive
        assert p.sharpe_estimate() > 0

    def test_n_observations(self):
        p = NIGPosterior()
        assert p.n_observations == 0
        p.update(0.01)
        assert p.n_observations == 1

    def test_converges_to_sample_mean(self):
        """After many observations, posterior mean ≈ sample mean."""
        p = NIGPosterior()
        data = np.random.RandomState(42).randn(500) * 0.01 + 0.001
        p.update_batch(data)
        assert abs(p.mu - data.mean()) < 0.001


# ── BayesianSelector ───────────────────────────────────────────────────────
class TestBayesianSelector:
    def test_select_returns_weights(self):
        sel = BayesianSelector(["A", "B", "C"])
        alloc = sel.select()
        assert isinstance(alloc, AllocationResult)
        assert len(alloc.weights) == 3

    def test_weights_sum_to_one(self):
        sel = BayesianSelector(["A", "B", "C"])
        alloc = sel.select()
        assert sum(alloc.weights.values()) == pytest.approx(1.0, abs=0.01)

    def test_weights_positive(self):
        sel = BayesianSelector(["A", "B", "C"])
        alloc = sel.select()
        for w in alloc.weights.values():
            assert w > 0

    def test_update_changes_posterior(self):
        sel = BayesianSelector(["A", "B"])
        old_mu = sel.arms["A"].posterior.mu
        sel.update({"A": 0.05, "B": -0.01})
        assert sel.arms["A"].posterior.mu != old_mu

    def test_good_strategy_gets_more_weight(self):
        """After seeing consistent positive returns, strategy should get higher weight."""
        sel = BayesianSelector(["GOOD", "BAD"], seed=42)
        for _ in range(50):
            sel.update({"GOOD": 0.005, "BAD": -0.002})
        alloc = sel.select()
        assert alloc.weights["GOOD"] > alloc.weights["BAD"]

    def test_rankings(self):
        sel = BayesianSelector(["A", "B", "C"])
        for _ in range(30):
            sel.update({"A": 0.003, "B": 0.001, "C": -0.001})
        rankings = sel.get_rankings()
        assert rankings[0][0] == "A"  # highest Sharpe

    def test_posteriors(self):
        sel = BayesianSelector(["A", "B"])
        sel.update({"A": 0.01, "B": 0.01})
        posts = sel.get_posteriors()
        assert "mu" in posts["A"]
        assert "sharpe" in posts["A"]
        assert posts["A"]["n_obs"] == 1

    def test_sampled_values_in_result(self):
        sel = BayesianSelector(["X", "Y"])
        alloc = sel.select()
        assert "X" in alloc.sampled_values
        assert "Y" in alloc.sampled_values


# ── Baseline allocators ────────────────────────────────────────────────────
class TestBaselines:
    def test_equal_weight(self):
        w = equal_weight(["A", "B", "C"])
        assert len(w) == 3
        assert all(abs(v - 1/3) < 0.01 for v in w.values())

    def test_equal_weight_empty(self):
        assert equal_weight([]) == {}

    def test_risk_parity_sums_to_one(self):
        df = _data(200, 3)
        w = risk_parity(df)
        assert sum(w.values()) == pytest.approx(1.0, abs=0.01)

    def test_risk_parity_positive(self):
        w = risk_parity(_data(200, 3))
        assert all(v > 0 for v in w.values())

    def test_risk_parity_lower_vol_higher_weight(self):
        rng = np.random.RandomState(42)
        idx = pd.date_range("2024-01-01", periods=200, freq="B")
        df = pd.DataFrame({
            "LOW_VOL": rng.randn(200) * 0.005,
            "HIGH_VOL": rng.randn(200) * 0.02,
        }, index=idx)
        w = risk_parity(df)
        assert w["LOW_VOL"] > w["HIGH_VOL"]

    def test_markowitz_sums_to_one(self):
        w = markowitz(_data(200, 3))
        assert sum(w.values()) == pytest.approx(1.0, abs=0.02)

    def test_markowitz_positive(self):
        w = markowitz(_data(200, 3))
        assert all(v >= 0.04 for v in w.values())  # floor at 0.05


# ── Backtest ────────────────────────────────────────────────────────────────
class TestBacktest:
    def test_returns_result(self):
        r = BayesianBacktest(warmup=30).run(_data(200, 3))
        assert isinstance(r, BayesianSelectorResult)

    def test_four_comparisons(self):
        r = BayesianBacktest(warmup=30).run(_data(300, 3))
        assert len(r.comparisons) == 4
        methods = {c.method for c in r.comparisons}
        assert methods == {"thompson", "equal_weight", "risk_parity", "markowitz"}

    def test_regret_curve_populated(self):
        r = BayesianBacktest(warmup=30).run(_data(200, 3))
        assert len(r.regret_curve) > 0

    def test_regret_nonnegative_eventually(self):
        """Oracle should outperform Thompson (regret ≥ 0 at end)."""
        r = BayesianBacktest(warmup=30).run(_data(500, 3))
        assert r.total_regret >= -0.5  # some noise tolerance

    def test_best_method_set(self):
        r = BayesianBacktest(warmup=30).run(_data(300, 3))
        assert r.best_method in ("thompson", "equal_weight", "risk_parity", "markowitz")

    def test_thompson_sharpe(self):
        r = BayesianBacktest(warmup=30).run(_data(300, 3))
        assert isinstance(r.thompson_sharpe, float)

    def test_arms_updated(self):
        r = BayesianBacktest(warmup=30).run(_data(200, 3))
        for arm in r.arms:
            assert arm.posterior.n_observations > 0

    def test_generated_at(self):
        r = BayesianBacktest(warmup=30).run(_data(150, 3))
        assert len(r.generated_at) > 0

    def test_too_short(self):
        r = BayesianBacktest(warmup=30).run(_data(20, 2))
        assert len(r.comparisons) == 0

    def test_single_strategy(self):
        r = BayesianBacktest(warmup=30).run(_data(100, 1))
        assert len(r.comparisons) == 0  # needs ≥2


# ── Synthetic data ──────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_shape(self):
        df = generate_strategy_returns(100, 4)
        assert df.shape == (100, 4)

    def test_deterministic(self):
        a = generate_strategy_returns(50, 3, seed=99)
        b = generate_strategy_returns(50, 3, seed=99)
        pd.testing.assert_frame_equal(a, b)

    def test_columns_named(self):
        df = generate_strategy_returns(100, 4)
        assert all("EXP-" in c for c in df.columns)


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_strategy_arm(self):
        a = StrategyArm("EXP-880")
        assert a.n_selected == 0

    def test_allocation_result(self):
        r = AllocationResult({"A": 0.5, "B": 0.5}, "thompson")
        assert r.method == "thompson"

    def test_regret_point(self):
        p = RegretPoint(10, 0.05, 0.08, 0.03)
        assert p.regret == 0.03

    def test_comparison_result(self):
        c = ComparisonResult("thompson", 50.0, 8.0, 2.5, 5.0)
        assert c.sharpe == 2.5

    def test_result_defaults(self):
        r = BayesianSelectorResult()
        assert r.arms == []
        assert r.total_regret == 0
