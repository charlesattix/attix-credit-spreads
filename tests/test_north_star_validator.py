"""Tests for compass/north_star_validator.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from compass.north_star_validator import (
    BootstrapCI,
    CPCVResult,
    NorthStarValidator,
    PortfolioConfig,
    TestResult,
    ValidationResult,
    portfolio_return_dd,
    run_bootstrap,
    run_correlation_stress,
    run_cost_sensitivity,
    run_cpcv,
    run_leverage_frontier,
    run_regime_analysis,
    run_weight_sensitivity,
)


@pytest.fixture
def config():
    return PortfolioConfig()


@pytest.fixture
def validator(config):
    return NorthStarValidator(config)


# ── Portfolio math tests ─────────────────────────────────────────────────


class TestPortfolioMath:
    def test_base_metrics(self, config):
        cagr, dd, sh = portfolio_return_dd(config)
        assert cagr > 10
        assert dd > 0
        assert sh > 5

    def test_cost_reduces_return(self, config):
        c1, _, _ = portfolio_return_dd(config, cost_multiplier=1.0)
        c3, _, _ = portfolio_return_dd(config, cost_multiplier=3.0)
        assert c3 < c1

    def test_higher_corr_increases_dd(self, config):
        _, dd_base, _ = portfolio_return_dd(config)
        stressed = {k: 0.8 for k in config.correlations}
        _, dd_stress, _ = portfolio_return_dd(config, corr_override=stressed)
        assert dd_stress > dd_base

    def test_weight_override(self, config):
        w = {"ML-CS-860": 1.0, "Regime-Lev": 0, "Intraday-MR": 0, "Combined-750": 0}
        cagr, _, _ = portfolio_return_dd(config, weight_override=w)
        assert abs(cagr - (21.5 - 0.05)) < 1  # single strategy CAGR minus cost


# ── CPCV tests ───────────────────────────────────────────────────────────


class TestCPCV:
    def test_returns_result(self, config):
        r = run_cpcv(config, n_folds=5)
        assert isinstance(r, CPCVResult)
        assert len(r.fold_sharpes) == 5

    def test_mean_above_zero(self, config):
        r = run_cpcv(config, n_folds=10)
        assert r.mean_sharpe > 0

    def test_pass_flag(self, config):
        r = run_cpcv(config)
        assert isinstance(r.passed, bool)


# ── Bootstrap tests ──────────────────────────────────────────────────────


class TestBootstrap:
    def test_returns_cis(self, config):
        cis = run_bootstrap(config, n_samples=500)
        assert len(cis) == 3
        names = {ci.metric for ci in cis}
        assert names == {"cagr", "dd", "sharpe"}

    def test_ci_ordered(self, config):
        cis = run_bootstrap(config, n_samples=500)
        for ci in cis:
            assert ci.ci_lower <= ci.mean <= ci.ci_upper

    def test_sharpe_excludes_zero(self, config):
        cis = run_bootstrap(config, n_samples=1000)
        sharpe_ci = next(ci for ci in cis if ci.metric == "sharpe")
        assert sharpe_ci.excludes_zero  # should be well above zero


# ── Weight sensitivity tests ─────────────────────────────────────────────


class TestWeightSensitivity:
    def test_max_change_reasonable(self, config):
        change, passed = run_weight_sensitivity(config, n_trials=100)
        assert change >= 0
        assert isinstance(passed, bool)

    def test_small_perturbation_small_change(self, config):
        change, _ = run_weight_sensitivity(config, perturbation_pct=1.0, n_trials=100)
        assert change < 10  # 1% weight change → <10% Sharpe change


# ── Leverage frontier tests ──────────────────────────────────────────────


class TestLeverage:
    def test_frontier_length(self, config):
        f = run_leverage_frontier(config)
        assert len(f) > 10

    def test_linear_scaling(self, config):
        f = run_leverage_frontier(config)
        at_1 = next(x for x in f if abs(x["leverage"] - 1.0) < 0.01)
        at_2 = next(x for x in f if abs(x["leverage"] - 2.0) < 0.01)
        assert abs(at_2["cagr"] / at_1["cagr"] - 2.0) < 0.01

    def test_dd_flag(self, config):
        f = run_leverage_frontier(config)
        for point in f:
            assert point["dd_under_12"] == (point["dd"] <= 12)


# ── Regime analysis tests ────────────────────────────────────────────────


class TestRegime:
    def test_all_four_regimes(self, config):
        r = run_regime_analysis(config)
        assert set(r.keys()) == {"bull", "bear", "sideways", "crisis"}

    def test_bull_best(self, config):
        r = run_regime_analysis(config)
        assert r["bull"]["cagr"] > r["bear"]["cagr"]

    def test_crisis_lowest(self, config):
        r = run_regime_analysis(config)
        assert r["crisis"]["cagr"] <= r["bull"]["cagr"]

    def test_has_positive_flag(self, config):
        r = run_regime_analysis(config)
        for v in r.values():
            assert "positive" in v


# ── Cost sensitivity tests ───────────────────────────────────────────────


class TestCost:
    def test_costs_reduce_cagr(self, config):
        r = run_cost_sensitivity(config)
        cagrs = [c["cagr"] for c in r]
        assert cagrs == sorted(cagrs, reverse=True)

    def test_profitable_at_1x(self, config):
        r = run_cost_sensitivity(config)
        assert r[0]["profitable"]

    def test_length(self, config):
        r = run_cost_sensitivity(config)
        assert len(r) == 5


# ── Correlation stress tests ─────────────────────────────────────────────


class TestCorrelationStress:
    def test_dd_increases(self, config):
        dd_stress, dd_lev, passed = run_correlation_stress(config)
        _, dd_base, _ = portfolio_return_dd(config)
        assert dd_stress >= dd_base * 0.9  # stress DD should be higher or close

    def test_pass_flag(self, config):
        _, _, passed = run_correlation_stress(config)
        assert isinstance(passed, bool)


# ── Full validator tests ─────────────────────────────────────────────────


class TestValidator:
    def test_validate(self, validator):
        r = validator.validate()
        assert isinstance(r, ValidationResult)
        assert len(r.tests) == 7

    def test_counts(self, validator):
        r = validator.validate()
        assert r.n_passed + r.n_failed == 7

    def test_base_metrics(self, validator):
        r = validator.validate()
        assert r.base_cagr > 0
        assert r.base_dd > 0
        assert r.base_sharpe > 0

    def test_cpcv_in_result(self, validator):
        r = validator.validate()
        assert len(r.cpcv.fold_sharpes) >= 5

    def test_bootstrap_in_result(self, validator):
        r = validator.validate()
        assert len(r.bootstrap_cis) == 3


# ── Report tests ─────────────────────────────────────────────────────────


class TestReport:
    def test_generates(self, validator):
        r = validator.validate()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "nsv.html"
            path = NorthStarValidator.generate_report(r, out)
            assert path.exists()
            content = path.read_text()
            assert "North Star Validation" in content
            assert "PASS" in content or "FAIL" in content

    def test_default_path(self, validator):
        r = validator.validate()
        path = NorthStarValidator.generate_report(r)
        assert path.exists()
        path.unlink(missing_ok=True)
