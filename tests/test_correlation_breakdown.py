"""Tests for compass/correlation_breakdown.py — correlation breakdown analyzer.

Covers:
  - ContagionEvent, RegimeCorrelation, FragilityScore dataclasses
  - Conditional correlation matrices by regime
  - Contagion detection logic
  - Rolling conditional correlations
  - Diversification benefit quantification
  - Fragility score computation
  - Full analyze() pipeline
  - from_csv constructor
  - HTML report generation
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compass.correlation_breakdown import (
    REGIMES,
    ContagionEvent,
    CorrelationBreakdownAnalyzer,
    DiversificationBenefit,
    FragilityScore,
    RegimeCorrelation,
    RollingCorrelation,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_returns(n=200, assets=4, seed=42):
    """Generate synthetic daily returns with regime-dependent correlation."""
    rng = np.random.RandomState(seed)
    names = [f"asset_{i}" for i in range(assets)]
    dates = pd.bdate_range("2024-01-01", periods=n)

    # Base uncorrelated returns
    data = rng.normal(0, 0.01, (n, assets))
    # Inject correlation in second half (simulates stress)
    common = rng.normal(0, 0.01, n)
    for j in range(assets):
        data[n // 2:, j] += common[n // 2:] * 0.8

    df = pd.DataFrame(data, index=dates, columns=names)
    return df


def _make_regimes(index, seed=42):
    """Generate regime series aligned to index."""
    rng = np.random.RandomState(seed)
    n = len(index)
    regimes = np.array(["neutral"] * n, dtype=object)
    regimes[: n // 4] = "bull"
    regimes[n // 4: n // 2] = "neutral"
    regimes[n // 2: 3 * n // 4] = "bear"
    regimes[3 * n // 4:] = "high_vol"
    return pd.Series(regimes, index=index)


def _make_analyzer(n=200, assets=4, seed=42, **kwargs):
    """Create an analyzer with synthetic data."""
    ret = _make_returns(n=n, assets=assets, seed=seed)
    reg = _make_regimes(ret.index, seed=seed)
    return CorrelationBreakdownAnalyzer(ret, regimes=reg, **kwargs)


# ── Dataclass tests ──────────────────────────────────────────────────────


class TestDataclasses:
    def test_contagion_event_fields(self):
        e = ContagionEvent(
            date="2024-06-01", pair=("A", "B"),
            baseline_corr=0.2, stress_corr=0.8, delta=0.6, regime="bear",
        )
        assert e.pair == ("A", "B")
        assert e.delta == pytest.approx(0.6)

    def test_regime_correlation_fields(self):
        rc = RegimeCorrelation(
            regime="bull", matrix=np.eye(3),
            mean_corr=0.1, max_corr=0.5, n_obs=50, assets=["a", "b", "c"],
        )
        assert rc.regime == "bull"
        assert rc.n_obs == 50

    def test_fragility_score_fields(self):
        f = FragilityScore(
            score=42.5, eigenvalue_ratio=0.6,
            mean_stress_corr=0.7, mean_calm_corr=0.2,
            contagion_count=3, diversification_ratio=0.3,
        )
        assert f.score == pytest.approx(42.5)

    def test_rolling_correlation_fields(self):
        rc = RollingCorrelation(
            pair=("X", "Y"), dates=["2024-01-01"],
            values=[0.5], regime_labels=["bull"],
        )
        assert rc.pair == ("X", "Y")
        assert len(rc.values) == 1

    def test_diversification_benefit_fields(self):
        d = DiversificationBenefit(
            portfolio_vol=0.01, weighted_avg_vol=0.015,
            benefit_ratio=0.33, marginal_contributions={"A": 0.005},
        )
        assert d.benefit_ratio == pytest.approx(0.33)


# ── Conditional correlation tests ────────────────────────────────────────


class TestConditionalCorrelations:
    def test_returns_all_regimes(self):
        analyzer = _make_analyzer()
        result = analyzer.analyze()
        rc = result["regime_correlations"]
        for regime in REGIMES:
            assert regime in rc

    def test_matrix_shape(self):
        analyzer = _make_analyzer(assets=5)
        analyzer.analyze()
        for rc in analyzer.regime_correlations.values():
            assert rc.matrix.shape == (5, 5)

    def test_diagonal_is_one(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        for rc in analyzer.regime_correlations.values():
            np.testing.assert_allclose(np.diag(rc.matrix), 1.0, atol=1e-10)

    def test_mean_corr_range(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        for rc in analyzer.regime_correlations.values():
            assert -1.0 <= rc.mean_corr <= 1.0

    def test_stress_corr_higher_than_calm(self):
        """Bear/high_vol correlation should be higher due to injected common factor."""
        analyzer = _make_analyzer()
        analyzer.analyze()
        calm = analyzer.regime_correlations.get("bull")
        stress = analyzer.regime_correlations.get("bear")
        if calm and stress:
            assert stress.mean_corr > calm.mean_corr

    def test_skips_regime_with_few_obs(self):
        ret = _make_returns(n=20)
        reg = pd.Series(["bull"] * 18 + ["bear"] * 2, index=ret.index)
        analyzer = CorrelationBreakdownAnalyzer(ret, regimes=reg)
        analyzer.analyze()
        # bear has only 2 obs — should still be present (>=3 check: 2 < 3)
        assert "bear" not in analyzer.regime_correlations


# ── Contagion detection tests ────────────────────────────────────────────


class TestContagionDetection:
    def test_detects_contagion(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        assert len(analyzer.contagion_events) > 0

    def test_contagion_sorted_by_delta(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        deltas = [e.delta for e in analyzer.contagion_events]
        assert deltas == sorted(deltas, reverse=True)

    def test_contagion_threshold_filters(self):
        """Higher threshold → fewer events."""
        a_low = _make_analyzer(contagion_threshold=0.1)
        a_high = _make_analyzer(contagion_threshold=0.9)
        a_low.analyze()
        a_high.analyze()
        assert len(a_low.contagion_events) >= len(a_high.contagion_events)

    def test_contagion_event_has_pair(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        if analyzer.contagion_events:
            e = analyzer.contagion_events[0]
            assert len(e.pair) == 2
            assert e.delta > 0


# ── Rolling correlation tests ────────────────────────────────────────────


class TestRollingCorrelation:
    def test_produces_pairs(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        n_assets = len(analyzer.assets)
        expected_pairs = n_assets * (n_assets - 1) // 2
        assert len(analyzer.rolling_correlations) == expected_pairs

    def test_values_in_range(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        for rc in analyzer.rolling_correlations:
            vals = np.array(rc.values)
            assert np.all(vals >= -1.0 - 1e-10)
            assert np.all(vals <= 1.0 + 1e-10)

    def test_empty_with_short_data(self):
        ret = _make_returns(n=10)
        reg = _make_regimes(ret.index)
        analyzer = CorrelationBreakdownAnalyzer(ret, regimes=reg, window=60)
        analyzer.analyze()
        assert len(analyzer.rolling_correlations) == 0

    def test_regime_labels_populated(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        for rc in analyzer.rolling_correlations:
            assert len(rc.regime_labels) == len(rc.values)


# ── Diversification benefit tests ────────────────────────────────────────


class TestDiversificationBenefit:
    def test_benefit_ratio_positive(self):
        """Uncorrelated assets should provide diversification benefit."""
        analyzer = _make_analyzer()
        analyzer.analyze()
        assert analyzer.diversification is not None
        assert analyzer.diversification.benefit_ratio > 0

    def test_portfolio_vol_less_than_avg(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        d = analyzer.diversification
        assert d.portfolio_vol < d.weighted_avg_vol

    def test_marginal_contributions_sum(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        mc = analyzer.diversification.marginal_contributions
        total = sum(mc.values())
        assert total == pytest.approx(analyzer.diversification.portfolio_vol, abs=1e-6)

    def test_custom_weights(self):
        ret = _make_returns(assets=3)
        reg = _make_regimes(ret.index)
        weights = {"asset_0": 0.5, "asset_1": 0.3, "asset_2": 0.2}
        analyzer = CorrelationBreakdownAnalyzer(ret, regimes=reg, weights=weights)
        analyzer.analyze()
        assert analyzer.diversification is not None


# ── Fragility score tests ────────────────────────────────────────────────


class TestFragilityScore:
    def test_score_in_range(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        assert 0 <= analyzer.fragility.score <= 100

    def test_eigenvalue_ratio_positive(self):
        analyzer = _make_analyzer()
        analyzer.analyze()
        assert analyzer.fragility.eigenvalue_ratio > 0

    def test_high_correlation_means_higher_fragility(self):
        """Perfectly correlated assets should score higher fragility."""
        rng = np.random.RandomState(99)
        n = 200
        base = rng.normal(0, 0.01, n)
        # All assets = same series + tiny noise → very high correlation
        data = {f"a{i}": base + rng.normal(0, 0.0001, n) for i in range(4)}
        ret = pd.DataFrame(data, index=pd.bdate_range("2024-01-01", periods=n))
        reg = _make_regimes(ret.index)
        high_corr = CorrelationBreakdownAnalyzer(ret, regimes=reg)
        high_corr.analyze()

        low_corr = _make_analyzer()
        low_corr.analyze()

        assert high_corr.fragility.score > low_corr.fragility.score


# ── Full pipeline tests ─────────────────────────────────────────────────


class TestAnalyzePipeline:
    def test_analyze_returns_all_keys(self):
        analyzer = _make_analyzer()
        result = analyzer.analyze()
        assert "contagion_events" in result
        assert "regime_correlations" in result
        assert "fragility" in result
        assert "rolling_correlations" in result
        assert "diversification" in result

    def test_from_csv(self, tmp_path):
        ret = _make_returns()
        csv = tmp_path / "returns.csv"
        ret.to_csv(csv)
        analyzer = CorrelationBreakdownAnalyzer.from_csv(str(csv))
        result = analyzer.analyze()
        assert "fragility" in result

    def test_from_csv_with_regimes(self, tmp_path):
        ret = _make_returns()
        reg = _make_regimes(ret.index)
        ret_csv = tmp_path / "returns.csv"
        reg_csv = tmp_path / "regimes.csv"
        ret.to_csv(ret_csv)
        reg.to_frame("regime").to_csv(reg_csv)
        analyzer = CorrelationBreakdownAnalyzer.from_csv(str(ret_csv), str(reg_csv))
        analyzer.analyze()
        assert len(analyzer.regime_correlations) > 0

    def test_default_neutral_regime(self):
        ret = _make_returns()
        analyzer = CorrelationBreakdownAnalyzer(ret)
        analyzer.analyze()
        assert "neutral" in analyzer.regime_correlations


# ── Report generation tests ──────────────────────────────────────────────


class TestReport:
    def test_generates_html(self, tmp_path):
        analyzer = _make_analyzer()
        path = analyzer.generate_report(str(tmp_path / "report.html"))
        content = open(path).read()
        assert "<!DOCTYPE html>" in content
        assert "Correlation Breakdown" in content

    def test_report_contains_sections(self, tmp_path):
        analyzer = _make_analyzer()
        path = analyzer.generate_report(str(tmp_path / "report.html"))
        content = open(path).read()
        assert "Contagion Detection" in content
        assert "Conditional Correlation" in content
        assert "Fragility" in content
        assert "Diversification" in content

    def test_report_embeds_charts(self, tmp_path):
        analyzer = _make_analyzer()
        path = analyzer.generate_report(str(tmp_path / "report.html"))
        content = open(path).read()
        assert "data:image/png;base64," in content

    def test_report_auto_runs_analyze(self, tmp_path):
        analyzer = _make_analyzer()
        assert analyzer.fragility is None
        analyzer.generate_report(str(tmp_path / "report.html"))
        assert analyzer.fragility is not None

    def test_report_at_default_path(self):
        analyzer = _make_analyzer()
        path = analyzer.generate_report()
        assert "correlation_breakdown.html" in path
        assert open(path).read().startswith("<!DOCTYPE html>")
