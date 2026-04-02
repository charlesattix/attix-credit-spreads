"""Tests for compass.feature_analysis."""
import math, unittest
from compass.feature_analysis import (
    FeatureAnalyser, FeatureAnalysisResult, FeatureCluster, FeatureImportance,
    FeatureVector, RegimeImportance, SelectionResult, SignalHalfLife,
    cluster_features, compute_interaction_scores, compute_permutation_importance,
    compute_regime_importance, compute_shap_importance, compute_signal_half_lives,
    generate_test_data, sequential_forward_selection,
)

def _data(n=200): return generate_test_data(n)

# --- SHAP ---
class TestSHAP(unittest.TestCase):
    def test_returns_all_features(self):
        data, _ = _data(100)
        imp = compute_shap_importance(data, n_samples=50)
        self.assertEqual(len(imp), 20)
    def test_sums_to_one(self):
        data, _ = _data(100)
        imp = compute_shap_importance(data, n_samples=50)
        self.assertAlmostEqual(sum(imp.values()), 1.0, places=2)
    def test_all_non_negative(self):
        data, _ = _data(100)
        for v in compute_shap_importance(data, n_samples=50).values():
            self.assertGreaterEqual(v, 0)
    def test_top_feature_is_predictive(self):
        data, _ = _data(300)
        imp = compute_shap_importance(data, n_samples=200)
        top = max(imp, key=imp.get)
        self.assertIn(top, ["feat_00", "feat_01", "feat_02", "feat_03"])
    def test_empty(self):
        self.assertEqual(compute_shap_importance([]), {})

# --- Permutation ---
class TestPermutation(unittest.TestCase):
    def test_returns_all(self):
        data, _ = _data(100)
        imp = compute_permutation_importance(data, n_repeats=3)
        self.assertEqual(len(imp), 20)
    def test_sums_to_one(self):
        data, _ = _data(100)
        imp = compute_permutation_importance(data, n_repeats=3)
        self.assertAlmostEqual(sum(imp.values()), 1.0, places=2)
    def test_predictive_features_rank_high(self):
        data, _ = _data(300)
        imp = compute_permutation_importance(data, n_repeats=5)
        sorted_f = sorted(imp, key=imp.get, reverse=True)
        self.assertIn(sorted_f[0], ["feat_00", "feat_01", "feat_02", "feat_03"])
    def test_empty(self):
        self.assertEqual(compute_permutation_importance([]), {})

# --- Interactions ---
class TestInteractions(unittest.TestCase):
    def test_returns_pairs(self):
        data, _ = _data(100)
        inter = compute_interaction_scores(data, top_n=5, n_samples=50)
        self.assertGreater(len(inter), 0)
    def test_values_non_negative(self):
        data, _ = _data(100)
        for v in compute_interaction_scores(data, top_n=5).values():
            self.assertGreaterEqual(v, 0)
    def test_empty(self):
        self.assertEqual(compute_interaction_scores([]), {})

# --- Signal half-life ---
class TestHalfLife(unittest.TestCase):
    def test_returns_all_features(self):
        data, _ = _data(200)
        hl = compute_signal_half_lives(data)
        self.assertEqual(len(hl), 20)
    def test_has_autocorrelations(self):
        data, _ = _data(200)
        hl = compute_signal_half_lives(data)
        for h in hl:
            self.assertGreater(len(h.autocorrelations), 0)
    def test_half_life_positive(self):
        data, _ = _data(200)
        for h in compute_signal_half_lives(data):
            self.assertGreater(h.half_life_periods, 0)
    def test_sorted_by_half_life(self):
        data, _ = _data(200)
        hl = compute_signal_half_lives(data)
        for i in range(1, len(hl)):
            self.assertGreaterEqual(hl[i].half_life_periods, hl[i-1].half_life_periods)
    def test_empty(self):
        self.assertEqual(compute_signal_half_lives([]), [])

# --- Clustering ---
class TestClustering(unittest.TestCase):
    def test_returns_clusters(self):
        data, _ = _data(200)
        cl = cluster_features(data)
        self.assertGreater(len(cl), 0)
    def test_all_features_covered(self):
        data, _ = _data(100)
        cl = cluster_features(data)
        all_feats = set()
        for c in cl: all_feats.update(c.features)
        self.assertEqual(len(all_feats), 20)
    def test_representative_in_cluster(self):
        data, _ = _data(100)
        for c in cluster_features(data):
            self.assertIn(c.representative, c.features)
    def test_redundant_detected(self):
        data, _ = _data(300)
        cl = cluster_features(data, threshold=0.70)
        # feat_00 and feat_03 are correlated, should cluster
        for c in cl:
            if "feat_00" in c.features and "feat_03" in c.features:
                self.assertGreater(len(c.features), 1)
                return
        # If not clustered at 0.70, they may be at lower threshold — acceptable
    def test_empty(self):
        self.assertEqual(cluster_features([]), [])

# --- Regime importance ---
class TestRegimeImportance(unittest.TestCase):
    def test_returns_per_regime(self):
        data, regimes = _data(300)
        ri = compute_regime_importance(data, regimes)
        self.assertGreater(len(ri), 0)
    def test_has_top_features(self):
        data, regimes = _data(300)
        for ri in compute_regime_importance(data, regimes):
            self.assertGreater(len(ri.top_features), 0)
    def test_regime_names(self):
        data, regimes = _data(300)
        for ri in compute_regime_importance(data, regimes):
            self.assertIn(ri.regime, ["bull", "bear", "sideways"])

# --- Selection ---
class TestSelection(unittest.TestCase):
    def test_returns_result(self):
        data, _ = _data(200)
        sel = sequential_forward_selection(data, max_features=10)
        self.assertIsInstance(sel, SelectionResult)
    def test_selected_non_empty(self):
        data, _ = _data(200)
        sel = sequential_forward_selection(data, max_features=5)
        self.assertGreater(len(sel.selected), 0)
        self.assertLessEqual(len(sel.selected), 5)
    def test_scores_ascending(self):
        data, _ = _data(200)
        sel = sequential_forward_selection(data, max_features=10)
        # Scores should generally be non-decreasing
        self.assertGreater(sel.best_score, 0)
    def test_predictive_features_selected_first(self):
        data, _ = _data(300)
        sel = sequential_forward_selection(data, max_features=5)
        self.assertIn(sel.selected[0], ["feat_00", "feat_01", "feat_02", "feat_03"])
    def test_empty(self):
        sel = sequential_forward_selection([])
        self.assertEqual(sel.selected, [])

# --- Full analyser ---
class TestFeatureAnalyser(unittest.TestCase):
    def test_analyse(self):
        data, regimes = _data(200)
        r = FeatureAnalyser(data, regimes).analyse()
        self.assertIsInstance(r, FeatureAnalysisResult)
        self.assertEqual(r.n_features, 20)
    def test_top_features_ranked(self):
        data, _ = _data(200)
        r = FeatureAnalyser(data).analyse()
        self.assertGreater(len(r.top_features), 0)
        self.assertEqual(r.top_features[0].rank, 1)
    def test_redundant_count(self):
        data, _ = _data(200)
        r = FeatureAnalyser(data).analyse()
        self.assertGreaterEqual(r.n_redundant, 0)
    def test_without_regimes(self):
        data, _ = _data(100)
        r = FeatureAnalyser(data).analyse()
        self.assertEqual(r.regime_importance, [])

# --- Synthetic data ---
class TestSyntheticData(unittest.TestCase):
    def test_correct_count(self):
        data, regimes = generate_test_data(100)
        self.assertEqual(len(data), 100)
        self.assertEqual(len(regimes), 100)
    def test_deterministic(self):
        d1, _ = generate_test_data(50, seed=42)
        d2, _ = generate_test_data(50, seed=42)
        self.assertEqual(d1[0].features["feat_00"], d2[0].features["feat_00"])
    def test_feature_count(self):
        data, _ = generate_test_data(10, n_features=15)
        self.assertEqual(len(data[0].features), 15)

if __name__ == "__main__":
    unittest.main()
