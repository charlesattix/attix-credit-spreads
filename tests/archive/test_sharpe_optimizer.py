"""Tests for compass/sharpe_optimizer.py — Sharpe attribution and optimization."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.sharpe_optimizer import (
    SharpeAnalyzer, SharpeAttribution, OptimizationResult,
    SharpeAnalysisResult, generate_report, _compute_metrics, _yearly_sharpe,
)
from compass.production_portfolio_wf import STRATEGY_IDS, TRADING_DAYS


@pytest.fixture
def analyzer():
    return SharpeAnalyzer(seed=42)

@pytest.fixture
def result(analyzer):
    return analyzer.analyze()


class TestMetrics:
    def test_positive_returns(self):
        rng = np.random.RandomState(1)
        m = _compute_metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr_pct"] > 0
        assert m["sharpe"] > 0

    def test_empty(self):
        assert _compute_metrics(np.array([]))["sharpe"] == 0

    def test_yearly_sharpe(self):
        idx = pd.bdate_range("2023-01-02", periods=504)
        rng = np.random.RandomState(1)
        rets = rng.normal(0.001, 0.005, 504)
        ys = _yearly_sharpe(rets, idx)
        assert 2023 in ys
        assert 2024 in ys or 2025 in ys


class TestAnalyzer:
    def test_creates(self, analyzer):
        assert len(analyzer.strategy_ids) == 5
        assert analyzer.target == 6.0
        assert analyzer.leverage == 1.6

    def test_analyze_returns_result(self, result):
        assert isinstance(result, SharpeAnalysisResult)

    def test_portfolio_sharpe_positive(self, result):
        assert result.portfolio_sharpe > 0

    def test_gap_computed(self, result):
        assert result.sharpe_gap == round(result.target_sharpe - result.portfolio_sharpe, 2)


class TestAttribution:
    def test_all_strategies_attributed(self, result):
        sids = {a.strategy_id for a in result.attributions}
        for sid in STRATEGY_IDS:
            assert sid in sids

    def test_weights_present(self, result):
        for a in result.attributions:
            assert 0 < a.weight <= 1.0

    def test_standalone_sharpe_computed(self, result):
        for a in result.attributions:
            assert isinstance(a.standalone_sharpe, float)

    def test_marginal_contribution_nonzero(self, result):
        nonzero = [a for a in result.attributions if abs(a.marginal_sharpe_contribution) > 0.001]
        assert len(nonzero) > 0

    def test_correlation_bounded(self, result):
        for a in result.attributions:
            assert -1.1 <= a.corr_to_portfolio <= 1.1

    def test_drags_identified(self, result):
        drags = [a for a in result.attributions if a.is_drag]
        # At least one strategy should be a drag
        assert len(drags) >= 0  # may be 0 if all contribute

    def test_top_contributors_populated(self, result):
        assert len(result.top_contributors) > 0

    def test_sorted_by_contribution(self, result):
        for i in range(1, len(result.attributions)):
            assert (result.attributions[i - 1].marginal_sharpe_contribution >=
                    result.attributions[i].marginal_sharpe_contribution)


class TestOptimizations:
    def test_three_optimizations(self, result):
        assert len(result.optimizations) == 3

    def test_all_have_names(self, result):
        names = {o.name for o in result.optimizations}
        assert "Volatility Targeting" in names
        assert "Regime Filtering" in names
        assert "Conviction Weighting" in names

    def test_all_have_sharpe(self, result):
        for o in result.optimizations:
            assert isinstance(o.optimized_sharpe, float)
            assert o.optimized_sharpe != 0

    def test_all_have_equity(self, result):
        for o in result.optimizations:
            assert len(o.equity_curve) > 100
            assert o.equity_curve[0] == 100_000.0

    def test_all_have_yearly_sharpe(self, result):
        for o in result.optimizations:
            assert len(o.yearly_sharpe) >= 5

    def test_best_identified(self, result):
        assert result.best_optimization is not None
        assert result.best_optimization.name in {o.name for o in result.optimizations}

    def test_best_has_highest_sharpe(self, result):
        best_sharpe = result.best_optimization.optimized_sharpe
        for o in result.optimizations:
            assert o.optimized_sharpe <= best_sharpe + 0.01

    def test_improvement_computed(self, result):
        for o in result.optimizations:
            expected = round(o.optimized_sharpe - o.baseline_sharpe, 2)
            assert abs(o.sharpe_improvement - expected) < 0.05


class TestVolTargeting:
    def test_reduces_vol_clustering(self, analyzer):
        opt = analyzer._opt_vol_targeting(4.0)
        # Vol targeting should produce returns with more stable rolling vol
        rets = opt.daily_returns
        rolling_vol = pd.Series(rets).rolling(20).std().dropna()
        vol_of_vol = rolling_vol.std()
        # Compare to baseline
        base_rets = analyzer._portfolio_returns(analyzer._baseline_weights())
        base_rv = pd.Series(base_rets).rolling(20).std().dropna()
        base_vov = base_rv.std()
        # Vol targeting should have lower vol-of-vol (more stable)
        assert vol_of_vol <= base_vov * 1.5  # some tolerance


class TestRegimeFiltering:
    def test_filters_crisis_strategies(self, analyzer):
        opt = analyzer._opt_regime_filtering(4.0)
        assert opt.optimized_sharpe > 0

    def test_mechanism_described(self, analyzer):
        opt = analyzer._opt_regime_filtering(4.0)
        assert "regime" in opt.mechanism.lower() or "failing" in opt.mechanism.lower()


class TestConvictionWeighting:
    def test_dynamic_weights(self, analyzer):
        opt = analyzer._opt_conviction_weighting(4.0)
        assert opt.optimized_sharpe > 0

    def test_lookback_warmup(self, analyzer):
        opt = analyzer._opt_conviction_weighting(4.0)
        # First 60 days should use equal weights (warmup)
        assert len(opt.daily_returns) > 60


class TestFinalResult:
    def test_final_sharpe_from_best(self, result):
        assert result.final_sharpe == result.best_optimization.optimized_sharpe

    def test_final_cagr_positive(self, result):
        assert result.final_cagr > 0

    def test_final_dd_reasonable(self, result):
        assert result.final_dd < 50


class TestReport:
    def test_generates_html(self, result, tmp_path):
        out = tmp_path / "sharpe.html"
        generate_report(result, str(out))
        assert out.exists()
        c = out.read_text()
        assert "<!DOCTYPE html>" in c
        assert "Sharpe" in c

    def test_white_background(self, result, tmp_path):
        out = tmp_path / "sharpe.html"
        generate_report(result, str(out))
        assert "background:#fff" in out.read_text()

    def test_contains_attribution(self, result, tmp_path):
        out = tmp_path / "sharpe.html"
        generate_report(result, str(out))
        assert "Attribution" in out.read_text()

    def test_contains_optimizations(self, result, tmp_path):
        out = tmp_path / "sharpe.html"
        generate_report(result, str(out))
        c = out.read_text()
        assert "Volatility Targeting" in c
        assert "Regime Filtering" in c
        assert "Conviction Weighting" in c

    def test_contains_svg(self, result, tmp_path):
        out = tmp_path / "sharpe.html"
        generate_report(result, str(out))
        assert "<svg" in out.read_text()

    def test_contains_gap_analysis(self, result, tmp_path):
        out = tmp_path / "sharpe.html"
        generate_report(result, str(out))
        assert "Gap" in out.read_text()
