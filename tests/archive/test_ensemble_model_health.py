"""Tests for compass/ensemble_model_health.py — ensemble model health monitor."""
from __future__ import annotations
import numpy as np
import pytest
from compass.ensemble_model_health import (
    DisagreementAlert, DriftResult, HealthConfig, HealthReport,
    ModelHealthMonitor, OutcomeRecord, PredictionRecord,
    RetrainRecommendation,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _mon(**kw) -> ModelHealthMonitor:
    return ModelHealthMonitor(HealthConfig(**kw))

def _feed_good(mon: ModelHealthMonitor, n: int = 50, seed: int = 42):
    """Feed monitor with well-calibrated predictions and outcomes."""
    rng = np.random.RandomState(seed)
    for _ in range(n):
        prob = rng.uniform(0.55, 0.85)
        win = rng.random() < prob
        mon.record_prediction(prob, {"xgb": prob + rng.normal(0, 0.03),
                                      "rf": prob + rng.normal(0, 0.03)})
        mon.record_outcome(win, prob)

def _feed_bad(mon: ModelHealthMonitor, n: int = 50, seed: int = 99):
    """Feed with poorly calibrated (inverted) predictions."""
    rng = np.random.RandomState(seed)
    for _ in range(n):
        prob = rng.uniform(0.55, 0.85)
        win = rng.random() > prob  # inverted — high prob → loss
        mon.record_prediction(prob, {"xgb": prob + 0.15, "rf": prob - 0.15})
        mon.record_outcome(win, prob)

def _feed_with_features(mon: ModelHealthMonitor, n: int = 50, seed: int = 42):
    """Feed with feature data for drift detection."""
    rng = np.random.RandomState(seed)
    for _ in range(n):
        feats = {"vix": rng.normal(18, 3), "rsi": rng.normal(50, 10),
                 "iv_rank": rng.normal(40, 15)}
        prob = rng.uniform(0.55, 0.80)
        mon.record_prediction(prob, {"xgb": prob, "rf": prob}, features=feats)
        mon.record_outcome(rng.random() < prob, prob)

# ── Dataclass tests ──────────────────────────────────────────────────────

class TestDataclasses:
    def test_prediction_record(self):
        p = PredictionRecord(0.75, {"xgb": 0.80, "rf": 0.70}, 0.05)
        assert p.probability == pytest.approx(0.75)

    def test_outcome_record(self):
        o = OutcomeRecord(1, 0.75)
        assert o.actual == 1

    def test_drift_result(self):
        d = DriftResult("vix", 0.25, 0.01, True, 22.0, 18.0, 4.0, 3.0)
        assert d.drifted is True

    def test_disagreement_alert(self):
        a = DisagreementAlert("2024-01-01", 0.25, {"a": 0.8, "b": 0.5}, "warning")
        assert a.severity == "warning"

    def test_retrain_recommendation(self):
        r = RetrainRecommendation("AUC dropped", "critical", "auc", 0.70, 0.75, "2024-01-01")
        assert r.severity == "critical"

    def test_health_report(self):
        h = HealthReport(0.8, 0.85, 0.15, 0.90, 0.05, 50, 50,
                         0.10, 0.18, 0, 1, 10, 0.10, False, [], 85, "A")
        assert h.grade == "A"

    def test_health_config_defaults(self):
        c = HealthConfig()
        assert c.rolling_window == 100
        assert c.auc_drop_threshold == pytest.approx(0.05)
        assert c.disagreement_alert == pytest.approx(0.20)

# ── Recording ────────────────────────────────────────────────────────────

class TestRecording:
    def test_record_prediction(self):
        mon = _mon()
        mon.record_prediction(0.75, {"xgb": 0.80, "rf": 0.70})
        assert len(mon._predictions) == 1

    def test_record_outcome(self):
        mon = _mon()
        mon.record_prediction(0.75)
        mon.record_outcome(True)
        assert len(mon._outcomes) == 1

    def test_outcome_uses_last_prediction(self):
        mon = _mon()
        mon.record_prediction(0.82)
        mon.record_outcome(True)
        assert mon._outcomes[-1].predicted_prob == pytest.approx(0.82)

    def test_outcome_explicit_prob(self):
        mon = _mon()
        mon.record_outcome(False, predicted_prob=0.60)
        assert mon._outcomes[-1].predicted_prob == pytest.approx(0.60)

    def test_rolling_window_cap(self):
        mon = _mon(rolling_window=10)
        for i in range(20):
            mon.record_prediction(0.5 + i * 0.01)
            mon.record_outcome(True, 0.5)
        assert len(mon._predictions) == 10
        assert len(mon._outcomes) == 10

    def test_features_tracked(self):
        mon = _mon()
        mon.record_prediction(0.7, features={"vix": 20, "rsi": 55})
        assert "vix" in mon._live_features
        assert len(mon._live_features["vix"]) == 1

# ── Disagreement alerts ──────────────────────────────────────────────────

class TestDisagreement:
    def test_no_alert_low_disagreement(self):
        mon = _mon()
        alert = mon.record_prediction(0.75, {"xgb": 0.76, "rf": 0.74})
        assert alert is None

    def test_alert_high_disagreement(self):
        mon = _mon(disagreement_alert=0.10)
        alert = mon.record_prediction(0.75, {"xgb": 0.90, "rf": 0.60})
        assert alert is not None
        assert alert.severity in ("warning", "critical")

    def test_critical_severity(self):
        mon = _mon(disagreement_alert=0.10)
        alert = mon.record_prediction(0.60, {"xgb": 0.95, "rf": 0.25})
        assert alert is not None
        assert alert.severity == "critical"

    def test_disagreement_tracked(self):
        mon = _mon(disagreement_alert=0.05)
        mon.record_prediction(0.70, {"xgb": 0.90, "rf": 0.50})
        assert len(mon._disagreement_alerts) == 1

    def test_avg_disagreement(self):
        mon = _mon()
        mon.record_prediction(0.70, {"a": 0.80, "b": 0.60})
        mon.record_prediction(0.70, {"a": 0.75, "b": 0.65})
        avg = mon.avg_disagreement()
        assert avg > 0

    def test_max_disagreement(self):
        mon = _mon()
        mon.record_prediction(0.70, {"a": 0.80, "b": 0.60})  # std = 0.10
        mon.record_prediction(0.70, {"a": 0.90, "b": 0.50})  # std = 0.20
        assert mon.max_disagreement() >= 0.19

# ── Rolling accuracy ─────────────────────────────────────────────────────

class TestRollingAccuracy:
    def test_perfect_accuracy(self):
        mon = _mon()
        for _ in range(20):
            mon.record_prediction(0.80)
            mon.record_outcome(True, 0.80)
        assert mon.rolling_accuracy() == pytest.approx(1.0)

    def test_zero_accuracy(self):
        mon = _mon()
        for _ in range(20):
            mon.record_prediction(0.80)
            mon.record_outcome(False, 0.80)  # predicted win, actually loss
        assert mon.rolling_accuracy() == pytest.approx(0.0)

    def test_mixed_accuracy(self):
        mon = _mon()
        _feed_good(mon, 50)
        acc = mon.rolling_accuracy()
        assert 0.3 < acc < 1.0  # reasonable with random data

    def test_empty_returns_zero(self):
        assert _mon().rolling_accuracy() == 0.0

# ── Rolling AUC ──────────────────────────────────────────────────────────

class TestRollingAUC:
    def test_good_auc(self):
        mon = _mon(min_samples_for_auc=10)
        _feed_good(mon, 50)
        auc = mon.rolling_auc()
        assert auc > 0.5  # better than random

    def test_bad_predictions_low_auc(self):
        mon = _mon(min_samples_for_auc=10)
        _feed_bad(mon, 50)
        auc = mon.rolling_auc()
        assert auc < 0.55  # near or below random

    def test_too_few_samples(self):
        mon = _mon(min_samples_for_auc=100)
        _feed_good(mon, 10)
        assert mon.rolling_auc() == 0.0

    def test_single_class_returns_half(self):
        mon = _mon(min_samples_for_auc=5)
        for _ in range(10):
            mon.record_outcome(True, 0.8)
        assert mon.rolling_auc() == pytest.approx(0.5)

    def test_auc_range(self):
        mon = _mon(min_samples_for_auc=10)
        _feed_good(mon, 50)
        auc = mon.rolling_auc()
        assert 0 <= auc <= 1

# ── Rolling Brier score ──────────────────────────────────────────────────

class TestRollingBrier:
    def test_perfect_brier(self):
        mon = _mon()
        for _ in range(20):
            mon.record_outcome(True, 1.0)
            mon.record_outcome(False, 0.0)
        assert mon.rolling_brier() == pytest.approx(0.0)

    def test_worst_brier(self):
        mon = _mon()
        for _ in range(20):
            mon.record_outcome(True, 0.0)
            mon.record_outcome(False, 1.0)
        assert mon.rolling_brier() == pytest.approx(1.0)

    def test_mid_range_brier(self):
        mon = _mon()
        _feed_good(mon, 50)
        brier = mon.rolling_brier()
        assert 0 < brier < 0.5

    def test_empty_returns_zero(self):
        assert _mon().rolling_brier() == 0.0

# ── Feature drift (KS-test) ─────────────────────────────────────────────

class TestFeatureDrift:
    def test_no_drift_same_distribution(self):
        mon = _mon()
        rng = np.random.RandomState(42)
        train = rng.normal(18, 3, 200)
        mon.set_training_distributions({"vix": train})
        for v in rng.normal(18, 3, 50):
            mon.record_prediction(0.7, features={"vix": float(v)})
        results = mon.detect_drift(min_samples=20)
        vix_drift = [d for d in results if d.feature == "vix"]
        assert len(vix_drift) == 1
        assert vix_drift[0].drifted == False

    def test_drift_detected_shifted_distribution(self):
        mon = _mon()
        rng = np.random.RandomState(42)
        train = rng.normal(18, 3, 200)
        mon.set_training_distributions({"vix": train})
        # Live: mean shifted from 18 to 35
        for v in rng.normal(35, 3, 50):
            mon.record_prediction(0.7, features={"vix": float(v)})
        results = mon.detect_drift(min_samples=20)
        vix_drift = [d for d in results if d.feature == "vix"]
        assert vix_drift[0].drifted == True
        assert vix_drift[0].p_value < 0.05

    def test_drift_with_stats_only(self):
        mon = _mon()
        mon.set_training_stats({"vix": 18.0}, {"vix": 3.0})
        rng = np.random.RandomState(42)
        for v in rng.normal(35, 3, 50):
            mon.record_prediction(0.7, features={"vix": float(v)})
        results = mon.detect_drift(min_samples=20)
        assert len(results) >= 1

    def test_no_drift_insufficient_samples(self):
        mon = _mon()
        mon.set_training_distributions({"vix": np.array([18, 19, 20])})
        mon.record_prediction(0.7, features={"vix": 20.0})
        results = mon.detect_drift(min_samples=30)
        assert len(results) == 0

    def test_drift_result_has_stats(self):
        mon = _mon()
        rng = np.random.RandomState(42)
        mon.set_training_distributions({"vix": rng.normal(18, 3, 200)})
        for v in rng.normal(30, 3, 50):
            mon.record_prediction(0.7, features={"vix": float(v)})
        results = mon.detect_drift(min_samples=20)
        d = results[0]
        assert d.live_mean > 25
        assert d.train_mean < 25

    def test_multiple_features(self):
        mon = _mon()
        rng = np.random.RandomState(42)
        mon.set_training_distributions({
            "vix": rng.normal(18, 3, 200),
            "rsi": rng.normal(50, 10, 200),
        })
        for _ in range(50):
            mon.record_prediction(0.7, features={
                "vix": float(rng.normal(18, 3)),  # no drift
                "rsi": float(rng.normal(80, 5)),   # drifted
            })
        results = mon.detect_drift(min_samples=20)
        drifted = [d.feature for d in results if d.drifted]
        assert "rsi" in drifted

# ── Retrain recommendations ──────────────────────────────────────────────

class TestRetrainRecommendations:
    def test_no_retrain_healthy(self):
        mon = _mon(min_samples_for_auc=10, baseline_auc=0.70)
        _feed_good(mon, 50)
        recs = mon.check_retrain()
        auc_recs = [r for r in recs if r.metric_name == "auc_drop"]
        assert len(auc_recs) == 0

    def test_retrain_on_auc_drop(self):
        mon = _mon(min_samples_for_auc=10, baseline_auc=0.85, auc_drop_threshold=0.05)
        _feed_bad(mon, 50)
        recs = mon.check_retrain()
        auc_recs = [r for r in recs if r.metric_name == "auc_drop"]
        assert len(auc_recs) >= 1

    def test_retrain_on_feature_drift(self):
        mon = _mon(drift_feature_pct=0.15)
        rng = np.random.RandomState(42)
        mon.set_training_distributions({
            "a": rng.normal(0, 1, 200), "b": rng.normal(0, 1, 200),
            "c": rng.normal(0, 1, 200),
        })
        # Drift ALL features
        for _ in range(50):
            mon.record_prediction(0.7, features={
                "a": float(rng.normal(10, 1)),
                "b": float(rng.normal(10, 1)),
                "c": float(rng.normal(10, 1)),
            })
        recs = mon.check_retrain()
        drift_recs = [r for r in recs if r.metric_name == "feature_drift"]
        assert len(drift_recs) >= 1

    def test_retrain_on_disagreement(self):
        mon = _mon(disagreement_alert=0.10)
        for _ in range(50):
            mon.record_prediction(0.60, {"a": 0.90, "b": 0.30})
        recs = mon.check_retrain()
        dis_recs = [r for r in recs if r.metric_name == "disagreement"]
        assert len(dis_recs) >= 1

    def test_retrain_severity(self):
        mon = _mon(min_samples_for_auc=10, baseline_auc=0.90, auc_drop_threshold=0.05)
        _feed_bad(mon, 50)
        recs = mon.check_retrain()
        assert any(r.severity in ("warning", "critical") for r in recs)

# ── Health report ────────────────────────────────────────────────────────

class TestHealthReport:
    def test_report_fields(self):
        mon = _mon(min_samples_for_auc=10)
        _feed_good(mon, 50)
        r = mon.get_report()
        assert isinstance(r, HealthReport)
        assert 0 <= r.rolling_accuracy <= 1
        assert 0 <= r.rolling_auc <= 1
        assert 0 <= r.rolling_brier <= 1
        assert r.n_predictions == 50
        assert r.n_outcomes == 50

    def test_healthy_grade(self):
        mon = _mon(min_samples_for_auc=10, baseline_auc=0.60)
        _feed_good(mon, 50)
        r = mon.get_report()
        assert r.grade in ("A", "B")

    def test_unhealthy_grade(self):
        mon = _mon(min_samples_for_auc=10, baseline_auc=0.95, auc_drop_threshold=0.01)
        _feed_bad(mon, 50)
        r = mon.get_report()
        assert r.grade in ("C", "D", "F")

    def test_retrain_flag(self):
        mon = _mon(min_samples_for_auc=10, baseline_auc=0.90)
        _feed_bad(mon, 50)
        r = mon.get_report()
        assert r.retrain_recommended is True
        assert len(r.retrain_reasons) > 0

    def test_score_range(self):
        mon = _mon(min_samples_for_auc=10)
        _feed_good(mon, 50)
        r = mon.get_report()
        assert 0 <= r.health_score <= 100

    def test_empty_monitor_report(self):
        r = _mon().get_report()
        assert r.n_predictions == 0
        assert r.n_outcomes == 0
        assert r.rolling_accuracy == 0.0

# ── Baseline AUC ─────────────────────────────────────────────────────────

class TestBaselineAUC:
    def test_auto_baseline(self):
        mon = _mon(min_samples_for_auc=10)
        assert mon._baseline_auc is None
        _feed_good(mon, 20)
        assert mon._baseline_auc is not None
        assert mon._baseline_auc > 0.4

    def test_explicit_baseline(self):
        mon = _mon(baseline_auc=0.85)
        assert mon._baseline_auc == pytest.approx(0.85)

    def test_baseline_locks(self):
        mon = _mon(min_samples_for_auc=10)
        _feed_good(mon, 20)
        first = mon._baseline_auc
        _feed_good(mon, 20)
        assert mon._baseline_auc == pytest.approx(first)  # locked after first set

# ── Reset ────────────────────────────────────────────────────────────────

class TestReset:
    def test_reset_clears(self):
        mon = _mon()
        _feed_good(mon, 30)
        mon.reset()
        assert len(mon._predictions) == 0
        assert len(mon._outcomes) == 0
        assert len(mon._live_features) == 0

    def test_reset_clears_alerts(self):
        mon = _mon(disagreement_alert=0.05)
        mon.record_prediction(0.7, {"a": 0.9, "b": 0.5})
        mon.reset()
        assert len(mon._disagreement_alerts) == 0

# ── AUC computation ──────────────────────────────────────────────────────

class TestAUCComputation:
    def test_perfect_separation(self):
        actuals = np.array([0, 0, 0, 1, 1, 1])
        probs = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
        auc = ModelHealthMonitor._compute_auc(actuals, probs)
        assert auc == pytest.approx(1.0)

    def test_random_half(self):
        actuals = np.array([0, 1, 0, 1])
        probs = np.array([0.5, 0.5, 0.5, 0.5])
        auc = ModelHealthMonitor._compute_auc(actuals, probs)
        assert auc == pytest.approx(0.5)

    def test_inverted_zero(self):
        actuals = np.array([1, 1, 1, 0, 0, 0])
        probs = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
        auc = ModelHealthMonitor._compute_auc(actuals, probs)
        assert auc == pytest.approx(0.0)

    def test_single_class(self):
        auc = ModelHealthMonitor._compute_auc(np.array([1, 1, 1]), np.array([0.8, 0.7, 0.9]))
        assert auc == pytest.approx(0.5)
