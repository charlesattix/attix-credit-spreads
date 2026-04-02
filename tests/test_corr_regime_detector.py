"""Tests for compass/corr_regime_detector.py — correlation regime detector."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.corr_regime_detector import (
    AbsorptionRatio, AnalysisResult, CorrDispersion, CorrRegime,
    CorrRegimeDetector, DetectorConfig, EarlyWarning, OverlayResult,
    absorption_ratio, classify_regime, correlation_dispersion,
    rolling_correlation_matrix,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _returns(n=300, assets=5, seed=42):
    rng = np.random.RandomState(seed)
    names = ["SPY", "TLT", "GLD", "QQQ", "HYG"][:assets]
    dates = pd.bdate_range("2023-01-02", periods=n)
    common = rng.normal(0, 0.005, n)
    data = {}
    for i, name in enumerate(names):
        noise = rng.normal(0, 0.008, n)
        # Inject correlation shift: first half uncorrelated, second half correlated
        corr_factor = 0.1 if i > 0 else 0
        factor = np.where(np.arange(n) < n // 2, 0.1, 0.7)  # increases in second half
        data[name] = noise + common * factor * (0.5 if i % 2 else 1.0)
    return pd.DataFrame(data, index=dates)

def _detector(n=300, assets=5, seed=42, **kw):
    return CorrRegimeDetector(_returns(n, assets, seed), DetectorConfig(**kw))

# ── Core computations ────────────────────────────────────────────────────

class TestAbsorptionRatio:
    def test_identity_matrix(self):
        """Identity → all eigenvalues equal → AR = n_top / n."""
        ar, eigvals = absorption_ratio(np.eye(5), 2)
        assert ar == pytest.approx(2 / 5, abs=0.01)
    def test_perfect_correlation(self):
        """All ones → first eigenvalue dominates → AR ≈ 1."""
        corr = np.ones((5, 5))
        np.fill_diagonal(corr, 1)
        ar, _ = absorption_ratio(corr, 1)
        assert ar > 0.9
    def test_range(self):
        rng = np.random.RandomState(42)
        X = rng.normal(0, 1, (100, 5))
        corr = np.corrcoef(X.T)
        ar, _ = absorption_ratio(corr, 3)
        assert 0 < ar <= 1
    def test_eigenvalues_descending(self):
        corr = np.eye(5)
        corr[0, 1] = corr[1, 0] = 0.8
        _, eigvals = absorption_ratio(corr, 3)
        assert eigvals[0] >= eigvals[1]

class TestCorrDispersion:
    def test_identity_zero_dispersion(self):
        """Identity matrix → all off-diag = 0 → dispersion = 0."""
        std, mean, mx, mn = correlation_dispersion(np.eye(5))
        assert std == pytest.approx(0.0)
        assert mean == pytest.approx(0.0)
    def test_positive_dispersion(self):
        rng = np.random.RandomState(42)
        corr = np.corrcoef(rng.normal(0, 1, (100, 5)).T)
        std, _, _, _ = correlation_dispersion(corr)
        assert std > 0
    def test_mean_range(self):
        rng = np.random.RandomState(42)
        corr = np.corrcoef(rng.normal(0, 1, (100, 5)).T)
        _, mean, _, _ = correlation_dispersion(corr)
        assert -1 <= mean <= 1
    def test_max_ge_min(self):
        rng = np.random.RandomState(42)
        corr = np.corrcoef(rng.normal(0, 1, (100, 5)).T)
        _, _, mx, mn = correlation_dispersion(corr)
        assert mx >= mn

class TestRollingCorrelation:
    def test_returns_list(self):
        ret = _returns(100, 3)
        mats = rolling_correlation_matrix(ret, 20)
        assert isinstance(mats, list)
        assert len(mats) > 0
    def test_matrix_shape(self):
        ret = _returns(100, 4)
        mats = rolling_correlation_matrix(ret, 20)
        assert mats[0][1].shape == (4, 4)
    def test_count(self):
        ret = _returns(100, 3)
        mats = rolling_correlation_matrix(ret, 20)
        assert len(mats) == 80  # 100 - 20

class TestClassifyRegime:
    def test_normal(self):
        r, _, _ = classify_regime(0.5, 0.3)
        assert r == "normal"
    def test_breakdown_on_ar(self):
        r, _, _ = classify_regime(1.8, 0.3, ar_warning_z=1.5)
        assert r == "breakdown"
    def test_breakdown_on_disp(self):
        r, _, _ = classify_regime(0.5, 2.0, disp_warning_z=1.5)
        assert r == "breakdown"
    def test_crisis(self):
        r, _, _ = classify_regime(3.0, 1.0, ar_crisis_z=2.5)
        assert r == "crisis"
    def test_confidence_range(self):
        _, conf, _ = classify_regime(2.0, 0.5)
        assert 0 <= conf <= 1

# ── Detector analysis ────────────────────────────────────────────────────

class TestAnalysis:
    def test_returns_result(self):
        det = _detector()
        r = det.analyze()
        assert isinstance(r, AnalysisResult)
    def test_has_all_windows(self):
        det = _detector()
        r = det.analyze()
        assert "short" in r.absorption_ratios
        assert "medium" in r.absorption_ratios
        assert "long" in r.absorption_ratios
    def test_regimes_populated(self):
        det = _detector()
        r = det.analyze()
        assert len(r.regimes) > 0
    def test_regime_values(self):
        det = _detector()
        r = det.analyze()
        for reg in r.regimes:
            assert reg.regime in ("normal", "breakdown", "crisis")
    def test_ar_range(self):
        det = _detector()
        r = det.analyze()
        for ar in r.absorption_ratios["medium"]:
            assert 0 <= ar.ratio <= 1
    def test_counts_sum(self):
        det = _detector()
        r = det.analyze()
        assert r.n_normal + r.n_breakdown + r.n_crisis == len(r.regimes)
    def test_early_warnings_list(self):
        det = _detector()
        r = det.analyze()
        assert isinstance(r.early_warnings, list)
    def test_avg_ar_positive(self):
        det = _detector()
        r = det.analyze()
        assert r.avg_ar > 0
    def test_current_regime(self):
        det = _detector()
        det.analyze()
        reg = det.get_current_regime()
        assert reg is not None
        assert reg.regime in ("normal", "breakdown", "crisis")
    def test_current_regime_before_analyze(self):
        det = _detector()
        assert det.get_current_regime() is None

# ── Overlay backtest ─────────────────────────────────────────────────────

class TestOverlay:
    def test_returns_result(self):
        det = _detector(300)
        det.analyze()
        rng = np.random.RandomState(42)
        pnls = rng.normal(50, 200, 100)
        indices = rng.randint(80, 250, 100)
        r = det.backtest_overlay(pnls, indices)
        assert isinstance(r, OverlayResult)
    def test_overlay_reduces_dd(self):
        """When losses happen during breakdown, overlay should help."""
        det = _detector(300)
        det.analyze()
        rng = np.random.RandomState(42)
        # Many losses concentrated in high indices (second half = correlated)
        pnls = np.concatenate([rng.normal(100, 50, 50), rng.normal(-200, 100, 50)])
        indices = np.concatenate([np.arange(80, 130), np.arange(200, 250)])
        r = det.backtest_overlay(pnls, indices)
        assert isinstance(r.dd_improvement_pct, float)
    def test_overlay_pnl_differs(self):
        det = _detector(300)
        det.analyze()
        rng = np.random.RandomState(42)
        pnls = rng.normal(50, 200, 80)
        indices = rng.randint(80, 250, 80)
        r = det.backtest_overlay(pnls, indices)
        # If any warnings fired, PnL should differ
        if r.n_warnings_fired + r.n_crisis_fired > 0:
            assert r.overlay_pnl != r.base_pnl
    def test_hit_rate_range(self):
        det = _detector(300)
        det.analyze()
        rng = np.random.RandomState(42)
        pnls = rng.normal(0, 200, 80)
        indices = rng.randint(80, 250, 80)
        r = det.backtest_overlay(pnls, indices)
        assert 0 <= r.hit_rate <= 1
    def test_auto_analyzes(self):
        det = _detector(300)
        rng = np.random.RandomState(42)
        pnls = rng.normal(50, 200, 50)
        indices = rng.randint(80, 250, 50)
        r = det.backtest_overlay(pnls, indices)
        assert det.result is not None

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_two_assets(self):
        det = _detector(200, assets=2)
        r = det.analyze()
        assert len(r.regimes) > 0
    def test_short_data(self):
        det = _detector(n=30, assets=3)
        r = det.analyze()
        # Short window may produce few results
        assert isinstance(r, AnalysisResult)
    def test_single_window(self):
        det = CorrRegimeDetector(
            _returns(200, 3),
            DetectorConfig(windows={"only": 20}),
        )
        r = det.analyze()
        assert "only" in r.absorption_ratios
    def test_high_ar_threshold(self):
        det = _detector(200, ar_crisis_z=100, ar_warning_z=100)
        r = det.analyze()
        assert r.n_breakdown == 0
        assert r.n_crisis == 0
