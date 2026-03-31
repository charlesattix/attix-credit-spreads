"""Tests for compass/production_ensemble.py — production ensemble pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.production_ensemble import (
    EnsembleConfig,
    FeatureDrift,
    HealthAlert,
    ModelPrediction,
    PipelineResult,
    ProductionEnsemble,
    RetrainWindow,
    TradeResult,
    apply_disagreement_scaling,
    compute_auc,
    compute_disagreement,
    detect_feature_drift,
    extract_feature_importances,
    grade_confidence,
    _sharpe,
    _sortino,
    _max_dd_pct,
    _pf,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_trades(n: int = 200, seed: int = 42, win_rate: float = 0.58) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    years = np.repeat([2020, 2021, 2022, 2023, 2024], n // 5 + 1)[:n]
    dates = pd.bdate_range("2020-01-02", periods=n)
    wins = rng.random(n) < win_rate
    pnl = np.where(wins, rng.uniform(50, 500, n), rng.uniform(-400, -50, n))
    return pd.DataFrame({
        "entry_date": dates,
        "exit_date": dates + pd.Timedelta(days=2),
        "year": years,
        "pnl": pnl,
        "net_credit": rng.uniform(0.5, 3.0, n),
        "contracts": 5,
        "win": wins.astype(int),
        "regime": rng.choice(["bull", "bear", "sideways"], n),
        "vix": rng.uniform(12, 35, n),
        "vix_percentile_50d": rng.uniform(10, 95, n),
        "iv_rank": rng.uniform(5, 90, n),
        "rsi_14": rng.uniform(20, 80, n),
        "momentum_5d_pct": rng.normal(0, 2, n),
        "momentum_10d_pct": rng.normal(0, 3, n),
        "dte_at_entry": rng.randint(3, 30, n),
        "hold_days": rng.randint(1, 15, n),
        "day_of_week": rng.randint(0, 5, n),
        "spy_price": rng.uniform(350, 500, n),
        "realized_vol_20d": rng.uniform(5, 30, n),
    })


@pytest.fixture
def trades():
    return _make_trades()


@pytest.fixture
def config():
    return EnsembleConfig(retrain_frequency="quarterly")


@pytest.fixture
def pipeline(config):
    return ProductionEnsemble(config)


# ── Config tests ─────────────────────────────────────────────────────────


class TestConfig:
    def test_defaults(self):
        cfg = EnsembleConfig()
        assert cfg.retrain_frequency == "quarterly"
        assert cfg.min_threshold == 0.70
        assert len(cfg.confidence_tiers) == 3

    def test_custom(self):
        cfg = EnsembleConfig(min_threshold=0.80, slippage_bps=10.0)
        assert cfg.min_threshold == 0.80
        assert cfg.slippage_bps == 10.0


# ── Confidence grading tests ─────────────────────────────────────────────


class TestConfidenceGrading:
    def test_high_confidence(self):
        tiers = [(0.90, 1.0), (0.80, 0.75), (0.70, 0.50)]
        assert grade_confidence(0.95, tiers) == 1.0

    def test_medium_confidence(self):
        tiers = [(0.90, 1.0), (0.80, 0.75), (0.70, 0.50)]
        assert grade_confidence(0.85, tiers) == 0.75

    def test_low_confidence(self):
        tiers = [(0.90, 1.0), (0.80, 0.75), (0.70, 0.50)]
        assert grade_confidence(0.72, tiers) == 0.50

    def test_below_min(self):
        tiers = [(0.90, 1.0), (0.80, 0.75), (0.70, 0.50)]
        assert grade_confidence(0.60, tiers) == 0.0

    def test_exact_threshold(self):
        tiers = [(0.90, 1.0), (0.80, 0.75)]
        assert grade_confidence(0.90, tiers) == 1.0

    def test_empty_tiers(self):
        assert grade_confidence(0.95, []) == 0.0


# ── Disagreement tests ───────────────────────────────────────────────────


class TestDisagreement:
    def test_zero_when_unanimous(self):
        probs = {"A": 0.8, "B": 0.8, "C": 0.8}
        assert compute_disagreement(probs) < 1e-10

    def test_positive_when_split(self):
        probs = {"A": 0.9, "B": 0.5, "C": 0.3}
        assert compute_disagreement(probs) > 0

    def test_single_model(self):
        assert compute_disagreement({"A": 0.8}) == 0.0

    def test_empty(self):
        assert compute_disagreement({}) == 0.0

    def test_scaling_full_at_low(self):
        size = apply_disagreement_scaling(1.0, 0.05, 0.20)
        assert size == 1.0  # low disagreement → full

    def test_scaling_half_at_high(self):
        size = apply_disagreement_scaling(1.0, 0.25, 0.20)
        assert size == 0.5  # above max → half

    def test_scaling_intermediate(self):
        size = apply_disagreement_scaling(1.0, 0.15, 0.20)
        assert 0.5 < size < 1.0

    def test_scaling_preserves_base(self):
        size = apply_disagreement_scaling(0.75, 0.01, 0.20)
        assert size == 0.75


# ── Feature drift tests ─────────────────────────────────────────────────


class TestFeatureDrift:
    def test_no_drift_stable(self):
        early = {"a": 0.5, "b": 0.3, "c": 0.2}
        late = {"a": 0.48, "b": 0.32, "c": 0.20}
        drifts = detect_feature_drift(early, late)
        assert all(not d.drifted for d in drifts)

    def test_detects_drift(self):
        early = {"a": 0.5, "b": 0.3, "c": 0.2, "d": 0.01, "e": 0.01}
        late = {"a": 0.01, "b": 0.01, "c": 0.01, "d": 0.5, "e": 0.4}
        drifts = detect_feature_drift(early, late, threshold=0.3)
        assert any(d.drifted for d in drifts)

    def test_empty_early(self):
        assert detect_feature_drift({}, {"a": 0.5}) == []

    def test_rank_change_computed(self):
        early = {"a": 0.5, "b": 0.3, "c": 0.2}
        late = {"a": 0.2, "b": 0.3, "c": 0.5}
        drifts = detect_feature_drift(early, late)
        a_drift = next(d for d in drifts if d.feature == "a")
        assert a_drift.rank_change != 0


# ── Feature importance extraction ────────────────────────────────────────


class TestFeatureImportance:
    def test_extract_from_rf(self):
        from sklearn.ensemble import RandomForestClassifier
        rng = np.random.RandomState(42)
        X = rng.normal(0, 1, (50, 3))
        y = (X[:, 0] > 0).astype(int)
        rf = RandomForestClassifier(n_estimators=10, random_state=42)
        rf.fit(X, y)
        imp = extract_feature_importances({"rf": rf}, ["f0", "f1", "f2"])
        assert len(imp) == 3
        assert all(v >= 0 for v in imp.values())

    def test_no_importance_attr(self):
        imp = extract_feature_importances({"dummy": object()}, ["a", "b"])
        assert all(v == 0.0 for v in imp.values())


# ── AUC tests ────────────────────────────────────────────────────────────


class TestAUC:
    def test_perfect(self):
        preds = np.array([0.9, 0.8, 0.7, 0.2, 0.1])
        actuals = np.array([1, 1, 1, 0, 0])
        assert compute_auc(preds, actuals) == 1.0

    def test_random(self):
        rng = np.random.RandomState(42)
        preds = rng.random(100)
        actuals = rng.randint(0, 2, 100)
        auc = compute_auc(preds, actuals)
        assert 0.3 < auc < 0.7

    def test_no_positives(self):
        assert compute_auc(np.array([0.5, 0.3]), np.array([0, 0])) == 0.5

    def test_no_negatives(self):
        assert compute_auc(np.array([0.5, 0.8]), np.array([1, 1])) == 0.5


# ── Metrics tests ────────────────────────────────────────────────────────


class TestMetrics:
    def test_sharpe_positive(self):
        assert _sharpe(np.array([100, 50, 80, -20, 60])) > 0

    def test_sharpe_short(self):
        assert _sharpe(np.array([1])) == 0.0

    def test_sortino(self):
        assert _sortino(np.array([100, -20, 50])) > 0

    def test_max_dd(self):
        eq = np.array([100, 110, 95, 105])
        assert _max_dd_pct(eq) > 0

    def test_profit_factor(self):
        assert _pf(np.array([100, -50])) == pytest.approx(2.0)


# ── Full pipeline tests ─────────────────────────────────────────────────


class TestFullPipeline:
    def test_returns_result(self, pipeline, trades):
        result = pipeline.run(trades)
        assert isinstance(result, PipelineResult)
        assert result.n_trades > 0

    def test_trades_have_sizing(self, pipeline, trades):
        result = pipeline.run(trades)
        for t in result.trades:
            assert 0 < t.size_fraction <= 1.0

    def test_predictions_have_disagreement(self, pipeline, trades):
        result = pipeline.run(trades)
        for p in result.predictions:
            assert p.disagreement >= 0
            assert 0 <= p.ensemble_prob <= 1.0

    def test_retrain_windows_exist(self, pipeline, trades):
        result = pipeline.run(trades)
        assert len(result.retrain_windows) > 0

    def test_auc_tracked(self, pipeline, trades):
        result = pipeline.run(trades)
        for w in result.retrain_windows:
            assert 0 <= w.auc <= 1.0

    def test_equity_curve_length(self, pipeline, trades):
        result = pipeline.run(trades)
        assert len(result.equity_curve) == result.n_trades + 1

    def test_feature_drifts_computed(self, pipeline, trades):
        result = pipeline.run(trades)
        # May or may not have drifts depending on data
        assert isinstance(result.feature_drifts, list)

    def test_health_alerts_list(self, pipeline, trades):
        result = pipeline.run(trades)
        assert isinstance(result.health_alerts, list)
        for a in result.health_alerts:
            assert isinstance(a, HealthAlert)
            assert a.severity in ("warning", "critical")

    def test_comparison_sharpes(self, pipeline, trades):
        result = pipeline.run(trades)
        assert isinstance(result.static_sharpe, float)
        assert isinstance(result.retrained_sharpe, float)
        assert isinstance(result.disagreement_sharpe, float)

    def test_win_rate_bounded(self, pipeline, trades):
        result = pipeline.run(trades)
        assert 0 <= result.win_rate <= 1.0

    def test_no_disagreement_mode(self, trades):
        cfg = EnsembleConfig(disagreement_scale=False)
        pe = ProductionEnsemble(cfg)
        result = pe.run(trades)
        # All effective sizes should equal confidence tier (no disagreement reduction)
        for p in result.predictions:
            assert p.effective_size == p.confidence_tier

    def test_different_thresholds(self, trades):
        cfg_low = EnsembleConfig(min_threshold=0.50)
        cfg_high = EnsembleConfig(min_threshold=0.90)
        r_low = ProductionEnsemble(cfg_low).run(trades)
        r_high = ProductionEnsemble(cfg_high).run(trades)
        assert r_low.n_trades >= r_high.n_trades


# ── HTML report tests ────────────────────────────────────────────────────


class TestHTMLReport:
    def test_generates_file(self, pipeline, trades):
        result = pipeline.run(trades)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "pe.html"
            path = ProductionEnsemble.generate_report(result, out)
            assert path.exists()
            content = path.read_text()
            assert "Production Ensemble" in content

    def test_contains_comparison(self, pipeline, trades):
        result = pipeline.run(trades)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            ProductionEnsemble.generate_report(result, out)
            content = out.read_text()
            assert "Static Sharpe" in content
            assert "Retrained Sharpe" in content

    def test_contains_retrain_table(self, pipeline, trades):
        result = pipeline.run(trades)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            ProductionEnsemble.generate_report(result, out)
            content = out.read_text()
            assert "Retrain Windows" in content
            assert "AUC" in content

    def test_contains_feature_drift(self, pipeline, trades):
        result = pipeline.run(trades)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            ProductionEnsemble.generate_report(result, out)
            content = out.read_text()
            assert "Feature Drift" in content

    def test_default_path(self, pipeline, trades):
        result = pipeline.run(trades)
        path = ProductionEnsemble.generate_report(result)
        assert path.exists()
        assert "production_ensemble.html" in str(path)
        path.unlink(missing_ok=True)
