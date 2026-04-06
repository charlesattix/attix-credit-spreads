"""Tests for compass/phase5_optimization.py."""

import numpy as np
import pytest

from compass.phase5_optimization import (
    generate_returns, compute_metrics, apply_weights,
    run_single_experiment, run_equal_weight, run_optimized,
    run_full_comparison, generate_report,
    EXPERIMENT_RETURNS_PROFILE, EXPERIMENT_CORRELATIONS, TRADING_DAYS,
)


class TestGenerateReturns:
    def test_correct_length(self):
        r = generate_returns(n_years=1.0)
        assert len(r["EXP-400"]) == TRADING_DAYS

    def test_all_experiments(self):
        r = generate_returns()
        for eid in ["EXP-400", "EXP-401", "EXP-503", "EXP-600"]:
            assert eid in r

    def test_deterministic(self):
        r1 = generate_returns(seed=99)
        r2 = generate_returns(seed=99)
        for eid in r1:
            np.testing.assert_array_equal(r1[eid], r2[eid])

    def test_covid_negative(self):
        r = generate_returns(n_years=6.0)
        for eid in r:
            # COVID period (days 40-63) should be net negative
            assert r[eid][40:63].mean() < 0

    def test_correlations_positive(self):
        r = generate_returns(n_years=6.0, seed=1)
        corr_matrix = np.corrcoef([r["EXP-400"], r["EXP-401"],
                                    r["EXP-503"], r["EXP-600"]])
        # All pairs should have positive correlation (configured 0.15-0.55)
        for i in range(4):
            for j in range(i+1, 4):
                assert corr_matrix[i, j] > 0


class TestComputeMetrics:
    def test_positive_sharpe(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 252))
        assert m["sharpe"] > 0
        assert m["cagr_pct"] > 0

    def test_empty(self):
        assert compute_metrics(np.array([]))["sharpe"] == 0

    def test_single_day(self):
        assert compute_metrics(np.array([0.01]))["sharpe"] == 0

    def test_has_all_fields(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 100))
        for key in ["cagr_pct", "sharpe", "max_dd_pct", "calmar", "sortino", "vol_pct"]:
            assert key in m


class TestApplyWeights:
    def test_basic(self):
        returns = {"A": np.array([0.01, 0.02]), "B": np.array([0.00, -0.01])}
        port = apply_weights(returns, {"A": 0.6, "B": 0.4})
        assert abs(port[0] - 0.006) < 0.001
        assert abs(port[1] - (0.012 - 0.004)) < 0.001

    def test_missing_weight(self):
        returns = {"A": np.array([0.01]), "B": np.array([0.01])}
        port = apply_weights(returns, {"A": 1.0})  # no B
        assert abs(port[0] - 0.01) < 0.001


class TestSingleExperiment:
    def test_runs(self):
        returns = generate_returns(n_years=1.0)
        run = run_single_experiment(returns, "EXP-400")
        assert run.method == "single"
        assert run.weights["EXP-400"] == 1.0
        assert run.weights["EXP-401"] == 0.0
        assert len(run.equity) == TRADING_DAYS + 1


class TestEqualWeight:
    def test_weights_equal(self):
        returns = generate_returns(n_years=1.0)
        run = run_equal_weight(returns)
        for w in run.weights.values():
            assert abs(w - 0.25) < 0.01

    def test_method(self):
        returns = generate_returns(n_years=1.0)
        run = run_equal_weight(returns)
        assert run.method == "equal"


class TestOptimized:
    @pytest.fixture
    def returns(self):
        return generate_returns(n_years=2.0, seed=1)

    def test_max_sharpe(self, returns):
        run = run_optimized(returns, "max_sharpe", "NEUTRAL_MACRO")
        assert run.method == "max_sharpe"
        assert abs(sum(run.weights.values()) - 1.0) < 0.01

    def test_risk_parity(self, returns):
        run = run_optimized(returns, "risk_parity", "NEUTRAL_MACRO")
        assert run.method == "risk_parity"
        # Risk parity should give lower-vol experiments more weight
        assert run.weights["EXP-600"] > run.weights["EXP-503"]

    def test_erc(self, returns):
        run = run_optimized(returns, "equal_risk_contribution", "NEUTRAL_MACRO")
        assert run.method == "equal_risk_contribution"

    def test_min_variance(self, returns):
        run = run_optimized(returns, "min_variance", "NEUTRAL_MACRO")
        assert run.method == "min_variance"

    def test_regime_tilts(self, returns):
        bull = run_optimized(returns, "max_sharpe", "BULL_MACRO")
        bear = run_optimized(returns, "max_sharpe", "BEAR_MACRO")
        # Bull should favor momentum (EXP-503), bear should favor defensive (EXP-600)
        assert bull.weights["EXP-503"] >= bear.weights["EXP-503"]
        assert bear.weights["EXP-600"] >= bull.weights["EXP-600"]

    def test_event_scaling(self, returns):
        normal = run_optimized(returns, "max_sharpe", "NEUTRAL_MACRO", event_scaling=1.0)
        scaled = run_optimized(returns, "max_sharpe", "NEUTRAL_MACRO", event_scaling=0.85)
        # Scaled weights should sum to ~0.85
        assert abs(sum(scaled.scaled_weights.values()) - 0.85) < 0.01
        # Raw weights should still sum to ~1.0
        assert abs(sum(scaled.weights.values()) - 1.0) < 0.01

    def test_min_weight_enforced(self, returns):
        run = run_optimized(returns, "max_sharpe", "NEUTRAL_MACRO")
        for w in run.weights.values():
            assert w >= 0.04  # min_weight=0.05 with small rounding tolerance


class TestFullComparison:
    def test_runs(self):
        result = run_full_comparison(seed=42)
        assert len(result.single_runs) == 4
        assert result.equal_weight is not None
        assert len(result.optimized_runs) >= 16  # 4 methods × 3 regimes + 4 event-scaled

    def test_best_selected(self):
        result = run_full_comparison(seed=42)
        assert result.best_run is not None
        # Best should be the one with highest Sharpe
        all_runs = result.single_runs + [result.equal_weight] + result.optimized_runs
        best_sharpe = max(r.metrics["sharpe"] for r in all_runs)
        assert abs(result.best_run.metrics["sharpe"] - best_sharpe) < 0.01


class TestReport:
    def test_generates(self, tmp_path):
        result = run_full_comparison(seed=42)
        out = tmp_path / "p5.html"
        generate_report(result, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Phase 5" in c
        assert "EXP-400" in c
        assert "EXP-401" in c
        assert "EXP-503" in c
        assert "EXP-600" in c

    def test_contains_methods(self, tmp_path):
        result = run_full_comparison(seed=42)
        out = tmp_path / "p.html"
        generate_report(result, str(out))
        c = out.read_text()
        assert "max_sharpe" in c
        assert "risk_parity" in c
        assert "min_variance" in c
        assert "equal_risk_contribution" in c

    def test_contains_regimes(self, tmp_path):
        result = run_full_comparison(seed=42)
        out = tmp_path / "p.html"
        generate_report(result, str(out))
        c = out.read_text()
        assert "BULL_MACRO" in c
        assert "BEAR_MACRO" in c
        assert "NEUTRAL_MACRO" in c

    def test_contains_correlation(self, tmp_path):
        result = run_full_comparison(seed=42)
        out = tmp_path / "p.html"
        generate_report(result, str(out))
        assert "Correlation" in out.read_text()

    def test_contains_svg(self, tmp_path):
        result = run_full_comparison(seed=42)
        out = tmp_path / "p.html"
        generate_report(result, str(out))
        assert "<svg" in out.read_text()
