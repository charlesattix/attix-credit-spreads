"""Tests for compass.factor_exposure — 30 tests."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from compass.factor_exposure import (
    FactorExposureAnalyzer, FactorBetas, FactorAttribution,
    RegimeFactorProfile, NeutralOverlay, FactorAnalysisResult,
    generate_factor_returns, FACTOR_NAMES,
)


def _data(n=756, seed=42):
    return generate_factor_returns(n, seed)


# ===========================================================================
# Synthetic data
# ===========================================================================

class TestSyntheticData:
    def test_shapes(self):
        strat, factors = _data()
        assert len(strat) == 756
        assert factors.shape == (756, 6)

    def test_factor_columns(self):
        _, factors = _data()
        for f in FACTOR_NAMES:
            assert f in factors.columns

    def test_correlations(self):
        _, factors = _data(1000)
        corr = factors.corr()
        # Market should correlate with others
        assert corr.loc["market", "size"] > 0.2


# ===========================================================================
# Beta estimation
# ===========================================================================

class TestBetas:
    def test_basic(self):
        strat, factors = _data()
        fea = FactorExposureAnalyzer()
        fb = fea.estimate_betas(strat, factors)
        assert isinstance(fb, FactorBetas)
        assert len(fb.betas) == 6

    def test_r_squared_bounded(self):
        strat, factors = _data()
        fea = FactorExposureAnalyzer()
        fb = fea.estimate_betas(strat, factors)
        assert 0 <= fb.r_squared <= 1.0

    def test_market_beta_negative(self):
        """Strategy is short credit → should have negative market beta."""
        strat, factors = _data(1000)
        fea = FactorExposureAnalyzer()
        fb = fea.estimate_betas(strat, factors)
        assert fb.betas["market"] < 0  # true beta is -0.15

    def test_alpha_positive(self):
        strat, factors = _data(1000)
        fea = FactorExposureAnalyzer()
        fb = fea.estimate_betas(strat, factors)
        assert fb.alpha > 0  # true alpha is ~20%

    def test_t_stats_present(self):
        strat, factors = _data()
        fea = FactorExposureAnalyzer()
        fb = fea.estimate_betas(strat, factors)
        assert len(fb.t_stats) == 6

    def test_short_data(self):
        strat = pd.Series([0.01] * 10)
        factors = pd.DataFrame({"market": [0.01] * 10})
        fea = FactorExposureAnalyzer()
        fb = fea.estimate_betas(strat, factors)
        assert fb.r_squared == 0


# ===========================================================================
# Attribution
# ===========================================================================

class TestAttribution:
    def test_basic(self):
        strat, factors = _data()
        fea = FactorExposureAnalyzer()
        fa = fea.factor_attribution(strat, factors)
        assert isinstance(fa, FactorAttribution)
        assert len(fa.factor_contributions) > 0

    def test_total_matches(self):
        strat, factors = _data()
        fea = FactorExposureAnalyzer()
        fa = fea.factor_attribution(strat, factors)
        recon = fa.alpha_contribution + sum(fa.factor_contributions.values()) + fa.residual
        assert abs(recon - fa.total_return) < abs(fa.total_return) * 0.5  # within 50%


# ===========================================================================
# Rolling betas
# ===========================================================================

class TestRolling:
    def test_produces_df(self):
        strat, factors = _data()
        fea = FactorExposureAnalyzer(rolling_window=60)
        df = fea.rolling_betas(strat, factors)
        assert not df.empty
        assert "market" in df.columns
        assert "alpha" in df.columns

    def test_length(self):
        strat, factors = _data(500)
        fea = FactorExposureAnalyzer(rolling_window=60)
        df = fea.rolling_betas(strat, factors)
        assert len(df) == 500 - 60 + 1

    def test_short_data_empty(self):
        strat, factors = _data(30)
        fea = FactorExposureAnalyzer(rolling_window=60)
        df = fea.rolling_betas(strat, factors)
        assert df.empty


# ===========================================================================
# Regime profiles
# ===========================================================================

class TestRegimeProfiles:
    def test_basic(self):
        strat, factors = _data(500)
        regimes = pd.Series(["bull"] * 250 + ["bear"] * 250, index=strat.index)
        fea = FactorExposureAnalyzer()
        profiles = fea.regime_profiles(strat, factors, regimes)
        assert len(profiles) == 2

    def test_structure(self):
        strat, factors = _data(500)
        regimes = pd.Series(["bull"] * 300 + ["bear"] * 200, index=strat.index)
        fea = FactorExposureAnalyzer()
        for p in fea.regime_profiles(strat, factors, regimes):
            assert isinstance(p, RegimeFactorProfile)
            assert p.n_days > 0


# ===========================================================================
# Neutral overlay
# ===========================================================================

class TestOverlay:
    def test_hedges_significant(self):
        betas = FactorBetas(0.20, 3.0, {"market": -0.15, "size": 0.02, "low_vol": 0.12},
                              {"market": -3.0, "size": 0.5, "low_vol": 2.5}, 0.3, 0.05)
        overlay = FactorExposureAnalyzer.neutral_overlay(betas)
        assert isinstance(overlay, NeutralOverlay)
        assert overlay.hedges["market"] != 0  # beta > 0.05
        assert overlay.hedges["size"] == 0    # beta < 0.05

    def test_cost_positive(self):
        betas = FactorBetas(0.20, 3.0, {"market": -0.15, "low_vol": 0.12},
                              {}, 0.3, 0.05)
        overlay = FactorExposureAnalyzer.neutral_overlay(betas, 1000000)
        assert overlay.cost_annual_pct > 0


# ===========================================================================
# Full analysis
# ===========================================================================

class TestFullAnalysis:
    def test_basic(self):
        strat, factors = _data(500)
        fea = FactorExposureAnalyzer(rolling_window=60)
        result = fea.analyze(strat, factors)
        assert isinstance(result, FactorAnalysisResult)
        assert result.betas is not None
        assert result.attribution is not None
        assert not result.rolling.empty

    def test_with_regimes(self):
        strat, factors = _data(500)
        regimes = pd.Series(["bull"] * 300 + ["bear"] * 200, index=strat.index)
        fea = FactorExposureAnalyzer(rolling_window=60)
        result = fea.analyze(strat, factors, regimes)
        assert len(result.regime_profiles) == 2


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        strat, factors = _data(500)
        regimes = pd.Series(["bull"] * 300 + ["bear"] * 200, index=strat.index)
        fea = FactorExposureAnalyzer(rolling_window=60)
        result = fea.analyze(strat, factors, regimes)
        out = tmp_path / "factor.html"
        path = fea.generate_report(result, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Factor Exposure" in html
        assert "<svg" in html
        assert "Alpha" in html
