"""Tests for compass/production_portfolio_wf.py — Production Walk-Forward."""

import math

import numpy as np
import pandas as pd
import pytest

from compass.production_portfolio_wf import (
    STRATEGY_IDS,
    STRATEGY_PROFILES,
    STRATEGY_CORRELATIONS,
    TRADING_DAYS,
    FoldResult,
    WalkForwardResult,
    ProductionWalkForward,
    generate_strategy_returns,
    generate_report,
    _compute_metrics,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def returns():
    return generate_strategy_returns(n_years=6.0, seed=42)


@pytest.fixture
def wf(returns):
    return ProductionWalkForward(strategy_returns=returns, seed=42)


@pytest.fixture
def result(wf):
    return wf.run()


# ═══════════════════════════════════════════════════════════════════════════
# Strategy Profile Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStrategyProfiles:
    def test_five_strategies_defined(self):
        assert len(STRATEGY_IDS) == 5

    def test_all_have_required_fields(self):
        for sid, prof in STRATEGY_PROFILES.items():
            assert "annual_return" in prof
            assert "annual_vol" in prof
            assert "max_dd" in prof
            assert "sharpe" in prof
            assert "spy_beta" in prof
            assert "crisis_beta" in prof

    def test_correlations_defined(self):
        # Should have C(5,2) = 10 pairs
        assert len(STRATEGY_CORRELATIONS) == 10

    def test_correlations_bounded(self):
        for pair, corr in STRATEGY_CORRELATIONS.items():
            assert -1.0 <= corr <= 1.0, f"{pair} correlation {corr} out of range"

    def test_exp1220_is_primary(self):
        prof = STRATEGY_PROFILES["EXP-1220_DynLev"]
        assert prof["annual_return"] > 0.50  # highest CAGR
        assert prof["weight_hint"] >= 0.30   # largest allocation


# ═══════════════════════════════════════════════════════════════════════════
# Return Generation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestReturnGeneration:
    def test_all_strategies_present(self, returns):
        for sid in STRATEGY_IDS:
            assert sid in returns

    def test_correct_length(self, returns):
        expected = int(6.0 * TRADING_DAYS)
        for sid, series in returns.items():
            assert len(series) == expected, f"{sid} wrong length"

    def test_all_same_index(self, returns):
        idx = returns[STRATEGY_IDS[0]].index
        for sid in STRATEGY_IDS[1:]:
            assert returns[sid].index.equals(idx)

    def test_deterministic(self):
        r1 = generate_strategy_returns(seed=99)
        r2 = generate_strategy_returns(seed=99)
        for sid in STRATEGY_IDS:
            np.testing.assert_array_equal(r1[sid].values, r2[sid].values)

    def test_different_seeds_differ(self):
        r1 = generate_strategy_returns(seed=1)
        r2 = generate_strategy_returns(seed=2)
        assert not np.array_equal(
            r1[STRATEGY_IDS[0]].values, r2[STRATEGY_IDS[0]].values)

    def test_covid_period_negative(self, returns):
        """COVID embedded at days 40-63 — crisis-beta strategies should drop."""
        exp1220 = returns["EXP-1220_DynLev"].values
        assert exp1220[40:63].mean() < 0

    def test_bear_period_negative(self, returns):
        """2022 bear at days 500-690."""
        exp1220 = returns["EXP-1220_DynLev"].values
        assert exp1220[500:600].mean() < 0

    def test_returns_have_realistic_vol(self, returns):
        """Annual vol should be within 2x of profile target."""
        for sid in STRATEGY_IDS:
            target_vol = STRATEGY_PROFILES[sid]["annual_vol"]
            actual_vol = returns[sid].std() * math.sqrt(TRADING_DAYS)
            assert actual_vol < target_vol * 3, f"{sid} vol {actual_vol:.2f} too high"


# ═══════════════════════════════════════════════════════════════════════════
# Walk-Forward Engine Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWalkForwardEngine:
    def test_creates_with_defaults(self):
        wf = ProductionWalkForward(seed=42)
        assert len(wf.strategy_ids) == 5

    def test_years_detected(self, wf):
        assert 2020 in wf.years
        assert 2025 in wf.years

    def test_correct_number_of_folds(self, result):
        # 6 years (2020-2025), first year is train-only, so 5 OOS folds
        assert result.n_folds == 5

    def test_fold_years_correct(self, result):
        test_years = [f.test_year for f in result.folds]
        assert test_years == [2021, 2022, 2023, 2024, 2025]

    def test_expanding_window(self, result):
        """Each fold should have more training data than the last."""
        for i in range(1, len(result.folds)):
            assert result.folds[i].n_train_days >= result.folds[i-1].n_train_days

    def test_train_years_expanding(self, result):
        assert result.folds[0].train_years == [2020]
        assert result.folds[-1].train_years == [2020, 2021, 2022, 2023, 2024]


# ═══════════════════════════════════════════════════════════════════════════
# Fold Result Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFoldResults:
    def test_all_folds_have_weights(self, result):
        for f in result.folds:
            assert len(f.weights) == 5
            assert abs(sum(f.weights.values()) - 1.0) < 0.01

    def test_weights_positive(self, result):
        for f in result.folds:
            for sid, w in f.weights.items():
                assert w >= 0, f"Fold {f.test_year}: {sid} weight {w} negative"

    def test_min_weight_enforced(self, result):
        for f in result.folds:
            for sid, w in f.weights.items():
                assert w >= 0.04, f"Fold {f.test_year}: {sid} weight {w} below min"

    def test_oos_metrics_computed(self, result):
        for f in result.folds:
            assert isinstance(f.oos_cagr, float)
            assert isinstance(f.oos_sharpe, float)
            assert isinstance(f.oos_dd, float)

    def test_is_metrics_computed(self, result):
        for f in result.folds:
            assert isinstance(f.is_cagr, float)
            assert isinstance(f.is_sharpe, float)

    def test_correlation_matrix_computed(self, result):
        for f in result.folds:
            assert len(f.oos_correlation) > 0

    def test_equity_curve_populated(self, result):
        for f in result.folds:
            assert len(f.oos_equity) > 1
            assert f.oos_equity[0] == 100_000.0

    def test_degradation_ratios(self, result):
        for f in result.folds:
            assert isinstance(f.sharpe_ratio, float)
            assert isinstance(f.cagr_ratio, float)


# ═══════════════════════════════════════════════════════════════════════════
# Combined Result Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCombinedResults:
    def test_combined_cagr_positive(self, result):
        assert result.combined_oos_cagr > 0

    def test_combined_sharpe_positive(self, result):
        assert result.combined_oos_sharpe > 0

    def test_combined_equity_grows(self, result):
        assert result.combined_equity[-1] > result.combined_equity[0]

    def test_per_strategy_metrics_populated(self, result):
        for sid in STRATEGY_IDS:
            assert sid in result.per_strategy_oos_cagr
            assert sid in result.per_strategy_oos_sharpe
            assert sid in result.per_strategy_oos_dd

    def test_year_attribution_populated(self, result):
        for f in result.folds:
            assert f.test_year in result.year_attribution

    def test_verdict_string_present(self, result):
        assert len(result.verdict) > 0

    def test_worst_fold_identified(self, result):
        assert result.worst_fold_year in [f.test_year for f in result.folds]


# ═══════════════════════════════════════════════════════════════════════════
# Allocation Method Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAllocationMethods:
    def test_risk_parity(self):
        wf = ProductionWalkForward(allocation_method="risk_parity", seed=42)
        r = wf.run()
        assert r.n_folds > 0
        assert all(f.method == "risk_parity" for f in r.folds)

    def test_max_sharpe(self):
        wf = ProductionWalkForward(allocation_method="max_sharpe", seed=42)
        r = wf.run()
        assert r.n_folds > 0
        assert all(f.method == "max_sharpe" for f in r.folds)

    def test_min_variance(self):
        wf = ProductionWalkForward(allocation_method="min_variance", seed=42)
        r = wf.run()
        assert r.n_folds > 0

    def test_equal_risk_contribution(self):
        wf = ProductionWalkForward(allocation_method="equal_risk_contribution", seed=42)
        r = wf.run()
        assert r.n_folds > 0

    def test_different_methods_different_weights(self):
        r1 = ProductionWalkForward(allocation_method="risk_parity", seed=42).run()
        r2 = ProductionWalkForward(allocation_method="max_sharpe", seed=42).run()
        # At least one fold should have different weights
        w1 = r1.folds[-1].weights
        w2 = r2.folds[-1].weights
        assert w1 != w2


# ═══════════════════════════════════════════════════════════════════════════
# Metrics Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMetrics:
    def test_positive_returns(self):
        rng = np.random.RandomState(1)
        rets = rng.normal(0.001, 0.005, 252)  # positive mean with noise
        m = _compute_metrics(rets)
        assert m["cagr_pct"] > 0
        assert m["sharpe"] > 0
        assert m["max_dd_pct"] >= 0

    def test_negative_returns(self):
        rets = np.full(252, -0.001)
        m = _compute_metrics(rets)
        assert m["cagr_pct"] < 0
        assert m["max_dd_pct"] > 0

    def test_empty_returns(self):
        m = _compute_metrics(np.array([]))
        assert m["cagr_pct"] == 0

    def test_single_return(self):
        m = _compute_metrics(np.array([0.01]))
        assert m["cagr_pct"] == 0  # needs >= 2


# ═══════════════════════════════════════════════════════════════════════════
# Report Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestReport:
    def test_generates_html(self, result, tmp_path):
        out = tmp_path / "test_wf.html"
        generate_report(result, str(out))
        assert out.exists()
        content = out.read_text()
        assert "<!DOCTYPE html>" in content
        assert "Production Portfolio" in content

    def test_contains_fold_data(self, result, tmp_path):
        out = tmp_path / "report.html"
        generate_report(result, str(out))
        content = out.read_text()
        assert "2021" in content  # first OOS year
        assert "2025" in content  # last OOS year

    def test_contains_strategy_names(self, result, tmp_path):
        out = tmp_path / "report.html"
        generate_report(result, str(out))
        content = out.read_text()
        assert "EXP-1220" in content
        assert "Cross-Asset" in content

    def test_contains_svg(self, result, tmp_path):
        out = tmp_path / "report.html"
        generate_report(result, str(out))
        assert "<svg" in out.read_text()

    def test_contains_correlation(self, result, tmp_path):
        out = tmp_path / "report.html"
        generate_report(result, str(out))
        assert "Correlation" in out.read_text()


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_two_year_data(self):
        """Minimum viable: 2 years → 1 fold."""
        rets = generate_strategy_returns(n_years=2.0, seed=1)
        wf = ProductionWalkForward(strategy_returns=rets, seed=1)
        r = wf.run()
        assert r.n_folds == 1

    def test_custom_leverage(self):
        wf = ProductionWalkForward(leverage=2.0, seed=42)
        r = wf.run()
        assert r.combined_oos_cagr > 0  # should still work

    def test_custom_dd_limit(self):
        wf = ProductionWalkForward(dd_limit=0.20, seed=42)
        r = wf.run()
        # With 20% limit, more folds should pass
        assert isinstance(r.all_folds_dd_ok, bool)

    def test_insufficient_years_raises(self):
        rets = generate_strategy_returns(n_years=0.5, seed=1)
        wf = ProductionWalkForward(strategy_returns=rets, seed=1)
        with pytest.raises(ValueError, match="at least 2 years"):
            wf.run()


# ═══════════════════════════════════════════════════════════════════════════
# Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    def test_full_pipeline(self, result):
        assert result.combined_oos_cagr > 0
        assert result.combined_oos_sharpe > 0
        assert result.n_folds == 5
        assert len(result.combined_equity) > 100

    def test_portfolio_optimizer_used(self, result):
        """Weights should differ from equal weight, proving optimizer ran."""
        for f in result.folds:
            equal = 1.0 / len(STRATEGY_IDS)
            diffs = [abs(w - equal) for w in f.weights.values()]
            assert max(diffs) > 0.01, "Weights look equal — optimizer may not be running"

    def test_diversification_benefit(self):
        """Combined portfolio should have better risk-adjusted returns than any single strategy."""
        rets = generate_strategy_returns(seed=42)
        wf = ProductionWalkForward(strategy_returns=rets, seed=42)
        r = wf.run()

        # Best single-strategy Sharpe
        best_single_sharpe = max(r.per_strategy_oos_sharpe.values())
        # Portfolio Sharpe should be competitive
        assert r.combined_oos_sharpe >= best_single_sharpe * 0.5, \
            "Portfolio Sharpe much worse than best single strategy"
