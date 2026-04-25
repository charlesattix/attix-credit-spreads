"""Tests for compass/regime_performance.py — Regime Performance Analysis."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.regime_performance import (
    Regime, ALL_REGIMES, STRATEGY_IDS, STRATEGY_PROFILES,
    RegimeMetrics, StrategyRegimeProfile, RegimeWeights, RegimeAnalysisResult,
    RegimeAnalyzer, generate_regime_returns, generate_report,
    _compute_metrics, TRADING_DAYS,
)


@pytest.fixture
def returns_and_regimes():
    return generate_regime_returns(seed=42)

@pytest.fixture
def analyzer(returns_and_regimes):
    rets, regs = returns_and_regimes
    return RegimeAnalyzer(strategy_returns=rets, regime_labels=regs)

@pytest.fixture
def result(analyzer):
    return analyzer.analyze()


class TestRegimeDefinitions:
    def test_six_regimes(self):
        assert len(ALL_REGIMES) == 6

    def test_regime_names(self):
        assert Regime.BULL in ALL_REGIMES
        assert Regime.CRISIS in ALL_REGIMES
        assert Regime.RECOVERY in ALL_REGIMES

    def test_five_strategies(self):
        assert len(STRATEGY_IDS) == 5

    def test_all_strategies_have_regime_mult(self):
        for sid, prof in STRATEGY_PROFILES.items():
            for r in ALL_REGIMES:
                assert r in prof["regime_mult"], f"{sid} missing {r}"
                assert r in prof["regime_vol_mult"], f"{sid} missing vol {r}"
                assert r in prof["win_rate"], f"{sid} missing wr {r}"
                assert r in prof["avg_trade_days"], f"{sid} missing atd {r}"


class TestReturnGeneration:
    def test_all_strategies_returned(self, returns_and_regimes):
        rets, _ = returns_and_regimes
        for sid in STRATEGY_IDS:
            assert sid in rets

    def test_regime_labels_match_length(self, returns_and_regimes):
        rets, regs = returns_and_regimes
        n = len(rets[STRATEGY_IDS[0]])
        assert len(regs) == n

    def test_all_regimes_present(self, returns_and_regimes):
        _, regs = returns_and_regimes
        present = set(regs.values)
        for r in [Regime.BULL, Regime.BEAR, Regime.CRISIS]:
            assert r in present, f"{r} not found in regime labels"

    def test_deterministic(self):
        r1, g1 = generate_regime_returns(seed=99)
        r2, g2 = generate_regime_returns(seed=99)
        np.testing.assert_array_equal(r1[STRATEGY_IDS[0]].values,
                                      r2[STRATEGY_IDS[0]].values)

    def test_crisis_period_has_crisis_label(self, returns_and_regimes):
        _, regs = returns_and_regimes
        # Days 40-55 should be crisis (VIX 15→82)
        crisis_days = sum(1 for i in range(40, 55) if regs.iloc[i] == Regime.CRISIS)
        assert crisis_days > 5


class TestRegimeAnalyzer:
    def test_creates_with_defaults(self):
        a = RegimeAnalyzer(seed=42)
        assert len(a.strategy_ids) == 5

    def test_analyze_returns_result(self, result):
        assert isinstance(result, RegimeAnalysisResult)

    def test_all_strategies_analyzed(self, result):
        for sid in STRATEGY_IDS:
            assert sid in result.strategies

    def test_regime_distribution_sums_to_100(self, result):
        total = sum(result.regime_distribution.values())
        assert abs(total - 100) < 1.0

    def test_regime_weights_for_all_regimes(self, result):
        for r in ALL_REGIMES:
            assert r in result.regime_weights

    def test_weights_sum_to_one(self, result):
        for r, rw in result.regime_weights.items():
            total = sum(rw.weights.values())
            assert abs(total - 1.0) < 0.02, f"{r} weights sum to {total}"

    def test_weights_bounded(self, result):
        for r, rw in result.regime_weights.items():
            for sid, w in rw.weights.items():
                assert w >= 0.04, f"{r}/{sid} weight {w} below min"
                assert w <= 0.52, f"{r}/{sid} weight {w} above max"

    def test_failure_map_populated(self, result):
        assert len(result.failure_map) == 5

    def test_heatmap_data_populated(self, result):
        assert len(result.heatmap_data) == 5
        for sid, regime_sharpes in result.heatmap_data.items():
            assert len(regime_sharpes) > 0


class TestStrategyRegimeProfile:
    def test_exp1220_has_metrics(self, result):
        sp = result.strategies["EXP-1220"]
        assert len(sp.metrics) > 0
        assert sp.strategy_name == "EXP-1220 Dynamic Leverage"

    def test_exp1220_crisis_is_failing(self, result):
        sp = result.strategies["EXP-1220"]
        # EXP-1220 has crisis_mult=-0.5, should fail in crisis
        if Regime.CRISIS in sp.metrics and sp.metrics[Regime.CRISIS].n_days > 10:
            assert sp.metrics[Regime.CRISIS].is_failing

    def test_cross_asset_crisis_not_failing(self, result):
        sp = result.strategies["CrossAsset"]
        # CrossAsset has crisis_mult=1.8, should do well
        if Regime.CRISIS in sp.metrics and sp.metrics[Regime.CRISIS].n_days > 10:
            assert not sp.metrics[Regime.CRISIS].is_failing

    def test_best_worst_regime_identified(self, result):
        for sid, sp in result.strategies.items():
            assert sp.best_regime != ""
            assert sp.worst_regime != ""


class TestRegimeWeights:
    def test_crisis_underweights_exp1220(self, result):
        crisis_w = result.regime_weights[Regime.CRISIS]
        # EXP-1220 fails in crisis, should be underweighted
        assert crisis_w.weights["EXP-1220"] < 0.30

    def test_crisis_overweights_cross_asset(self, result):
        crisis_w = result.regime_weights[Regime.CRISIS]
        # CrossAsset excels in crisis
        assert crisis_w.weights["CrossAsset"] > crisis_w.weights["EXP-1220"]

    def test_bull_overweights_exp1220(self, result):
        bull_w = result.regime_weights[Regime.BULL]
        # EXP-1220 shines in bull markets
        assert bull_w.weights["EXP-1220"] > 0.15

    def test_rationale_populated(self, result):
        for r, rw in result.regime_weights.items():
            assert len(rw.rationale) > 10


class TestAdaptivePortfolio:
    def test_positive_cagr(self, result):
        assert result.overall_portfolio_metrics["cagr_pct"] > 0

    def test_positive_sharpe(self, result):
        assert result.overall_portfolio_metrics["sharpe"] > 0

    def test_reasonable_dd(self, result):
        assert result.overall_portfolio_metrics["max_dd_pct"] < 50


class TestMetrics:
    def test_basic(self):
        rng = np.random.RandomState(1)
        rets = rng.normal(0.001, 0.005, 252)
        m = _compute_metrics(rets)
        assert m["cagr_pct"] > 0
        assert m["sharpe"] > 0

    def test_empty(self):
        m = _compute_metrics(np.array([]))
        assert m["cagr_pct"] == 0


class TestReport:
    def test_generates_html(self, result, tmp_path):
        out = tmp_path / "regime.html"
        generate_report(result, str(out))
        assert out.exists()
        content = out.read_text()
        assert "<!DOCTYPE html>" in content
        assert "Regime Performance" in content

    def test_white_background(self, result, tmp_path):
        out = tmp_path / "regime.html"
        generate_report(result, str(out))
        content = out.read_text()
        assert "background:#fff" in content

    def test_contains_heatmap(self, result, tmp_path):
        out = tmp_path / "regime.html"
        generate_report(result, str(out))
        assert "Heatmap" in out.read_text()

    def test_contains_weight_matrix(self, result, tmp_path):
        out = tmp_path / "regime.html"
        generate_report(result, str(out))
        assert "Weight Matrix" in out.read_text()

    def test_contains_failure_map(self, result, tmp_path):
        out = tmp_path / "regime.html"
        generate_report(result, str(out))
        assert "Failure Map" in out.read_text()

    def test_contains_all_regimes(self, result, tmp_path):
        out = tmp_path / "regime.html"
        generate_report(result, str(out))
        content = out.read_text()
        for r in ALL_REGIMES:
            assert r in content

    def test_contains_all_strategies(self, result, tmp_path):
        out = tmp_path / "regime.html"
        generate_report(result, str(out))
        content = out.read_text()
        assert "EXP-1220" in content
        assert "Cross-Asset" in content
