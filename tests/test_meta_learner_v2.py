"""Tests for compass.meta_learner_v2."""
import unittest
from compass.meta_learner_v2 import (
    GBConfig, GBMetaLearner, MetaLearnerEngine, MetaLearnerResult,
    N_SIGNALS, SIGNAL_NAMES, SignalVector, WFResult,
    best_individual_signal, generate_test_data, simple_average_signal,
    walk_forward_validate, _compute_auc, _sigmoid,
)

def _sv(label=0.01, **sig_overrides):
    sigs = {s: 0.0 for s in SIGNAL_NAMES}
    sigs.update(sig_overrides)
    return SignalVector("d0", sigs, label, 1 if label > 0 else 0)

def _data(n=300): return generate_test_data(n)

# --- Sigmoid ---
class TestSigmoid(unittest.TestCase):
    def test_zero(self): self.assertAlmostEqual(_sigmoid(0), 0.5)
    def test_large(self): self.assertAlmostEqual(_sigmoid(10), 1.0, places=3)
    def test_neg(self): self.assertAlmostEqual(_sigmoid(-10), 0.0, places=3)

# --- AUC ---
class TestAUC(unittest.TestCase):
    def test_perfect(self):
        self.assertAlmostEqual(_compute_auc([0,0,1,1], [0.1,0.2,0.8,0.9]), 1.0)
    def test_random(self):
        auc = _compute_auc([0,1,0,1], [0.5,0.5,0.5,0.5])
        self.assertGreaterEqual(auc, 0); self.assertLessEqual(auc, 1)
    def test_empty(self): self.assertEqual(_compute_auc([], []), 0.5)

# --- Simple average ---
class TestSimpleAverage(unittest.TestCase):
    def test_returns_float(self):
        sigs = {s: 0.5 for s in SIGNAL_NAMES}
        self.assertIsInstance(simple_average_signal(sigs), float)
    def test_range(self):
        sigs = {s: 1.0 for s in SIGNAL_NAMES}
        p = simple_average_signal(sigs)
        self.assertGreater(p, 0); self.assertLess(p, 1)

# --- Best individual ---
class TestBestIndividual(unittest.TestCase):
    def test_returns_name(self):
        name, corr = best_individual_signal(_data(200))
        self.assertIn(name, SIGNAL_NAMES)
        self.assertGreater(corr, 0)
    def test_base_ensemble_is_top(self):
        name, _ = best_individual_signal(_data(400))
        self.assertEqual(name, "base_ensemble")

# --- GBMetaLearner ---
class TestGBMetaLearner(unittest.TestCase):
    def test_train_predict(self):
        data = _data(200)
        ml = GBMetaLearner(GBConfig(n_estimators=20))
        ml.train(data)
        p = ml.predict_proba(data[0].signals)
        self.assertGreaterEqual(p, 0); self.assertLessEqual(p, 1)
    def test_predict_binary(self):
        ml = GBMetaLearner(GBConfig(n_estimators=10))
        ml.train(_data(100))
        self.assertIn(ml.predict(_data(100)[50].signals), [0, 1])
    def test_feature_importance_sums(self):
        ml = GBMetaLearner(GBConfig(n_estimators=30))
        ml.train(_data(200))
        fi = ml.feature_importance()
        self.assertAlmostEqual(sum(fi.values()), 1.0, places=2)
    def test_importance_all_non_negative(self):
        ml = GBMetaLearner(GBConfig(n_estimators=30))
        ml.train(_data(200))
        for v in ml.feature_importance().values():
            self.assertGreaterEqual(v, 0)
    def test_untrained_returns_05(self):
        self.assertAlmostEqual(GBMetaLearner().predict_proba({}), 0.5)
    def test_top_importance_is_predictive(self):
        ml = GBMetaLearner(GBConfig(n_estimators=60))
        ml.train(_data(400))
        fi = ml.feature_importance()
        top = max(fi, key=fi.get)
        self.assertIn(top, ["base_ensemble", "regime_score", "sentiment_composite"])
    def test_empty_data(self):
        ml = GBMetaLearner()
        ml.train([])
        self.assertFalse(ml._trained)

# --- Walk-forward ---
class TestWalkForward(unittest.TestCase):
    def test_returns_result(self):
        wf = walk_forward_validate(_data(300), n_folds=3, config=GBConfig(n_estimators=15))
        self.assertIsInstance(wf, WFResult)
        self.assertGreater(wf.n_folds, 0)
    def test_meta_auc_range(self):
        wf = walk_forward_validate(_data(300), n_folds=3, config=GBConfig(n_estimators=15))
        for f in wf.folds:
            self.assertGreaterEqual(f.meta_auc, 0)
            self.assertLessEqual(f.meta_auc, 1)
    def test_fold_sizes(self):
        wf = walk_forward_validate(_data(300), n_folds=3, config=GBConfig(n_estimators=10))
        for i in range(1, len(wf.folds)):
            self.assertGreater(wf.folds[i].train_size, wf.folds[i-1].train_size)
    def test_all_three_methods(self):
        wf = walk_forward_validate(_data(300), n_folds=3, config=GBConfig(n_estimators=10))
        for f in wf.folds:
            self.assertGreater(f.meta_auc, 0)
            self.assertGreater(f.avg_auc, 0)
            self.assertGreater(f.best_single_auc, 0)

# --- Full engine ---
class TestMetaLearnerEngine(unittest.TestCase):
    def test_analyse(self):
        r = MetaLearnerEngine(_data(300), GBConfig(n_estimators=15)).analyse()
        self.assertIsInstance(r, MetaLearnerResult)
        self.assertEqual(r.n_signals, N_SIGNALS)
    def test_top_5(self):
        r = MetaLearnerEngine(_data(300), GBConfig(n_estimators=20)).analyse()
        self.assertEqual(len(r.top_5_signals), 5)
    def test_best_individual(self):
        r = MetaLearnerEngine(_data(300), GBConfig(n_estimators=15)).analyse()
        self.assertIn(r.best_individual[0], SIGNAL_NAMES)
    def test_beats_flags(self):
        r = MetaLearnerEngine(_data(300), GBConfig(n_estimators=20)).analyse()
        self.assertIsInstance(r.meta_beats_average, bool)
        self.assertIsInstance(r.meta_beats_best_individual, bool)
    def test_n_samples(self):
        r = MetaLearnerEngine(_data(200), GBConfig(n_estimators=10)).analyse()
        self.assertEqual(r.n_samples, 200)

# --- Synthetic data ---
class TestSyntheticData(unittest.TestCase):
    def test_count(self):
        self.assertEqual(len(generate_test_data(100)), 100)
    def test_deterministic(self):
        a = generate_test_data(50, seed=42)
        b = generate_test_data(50, seed=42)
        self.assertEqual(a[0].signals["base_ensemble"], b[0].signals["base_ensemble"])
    def test_all_signals_present(self):
        data = generate_test_data(10)
        for d in data:
            self.assertEqual(set(d.signals.keys()), set(SIGNAL_NAMES))
    def test_labels_binary(self):
        for d in generate_test_data(50):
            self.assertIn(d.label_binary, [0, 1])
    def test_both_labels(self):
        labels = set(d.label_binary for d in generate_test_data(100))
        self.assertEqual(labels, {0, 1})

if __name__ == "__main__":
    unittest.main()
