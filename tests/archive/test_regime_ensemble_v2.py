"""Tests for compass.regime_ensemble_v2."""
import math, unittest
from compass.regime_ensemble_v2 import (
    REGIMES, DETECTOR_NAMES, MarketObs, HMMDetector, RuleDetector,
    VolClusterDetector, MarkovSwitchDetector, MetaLearner, EnsembleOutput,
    RegimeEnsembleV2, ComparisonResult, DetectorScore,
    generate_test_data, _score_detector,
)

def _bull(): return MarketObs("d",0.04,0.10,0.10,14,1.0,290)
def _crisis(): return MarketObs("d",-0.18,-0.28,0.50,55,-0.1,750)
def _bear(): return MarketObs("d",-0.04,-0.08,0.20,26,0.1,460)
def _sideways(): return MarketObs("d",0.005,0.01,0.08,13,0.7,310)

# --- Individual detectors ---
class TestHMM(unittest.TestCase):
    def test_returns_probs(self):
        d = HMMDetector()
        p = d.predict(_bull())
        self.assertEqual(set(p.keys()), set(REGIMES))
    def test_sums_to_one(self):
        d = HMMDetector()
        p = d.predict(_bull())
        self.assertAlmostEqual(sum(p.values()), 1.0, places=3)
    def test_bull_highest_for_bull(self):
        d = HMMDetector()
        for _ in range(5): d.predict(_bull())
        p = d.predict(_bull())
        self.assertIn(max(p, key=p.get), ["bull", "sideways"])
    def test_crisis_highest_for_crisis(self):
        d = HMMDetector()
        for _ in range(5): d.predict(_crisis())
        p = d.predict(_crisis())
        self.assertEqual(max(p, key=p.get), "crisis")

class TestRules(unittest.TestCase):
    def test_returns_probs(self):
        p = RuleDetector().predict(_bull())
        self.assertAlmostEqual(sum(p.values()), 1.0, places=3)
    def test_bull(self):
        p = RuleDetector().predict(_bull())
        self.assertIn(max(p, key=p.get), ["bull", "sideways"])
    def test_crisis(self):
        p = RuleDetector().predict(_crisis())
        self.assertEqual(max(p, key=p.get), "crisis")

class TestVolCluster(unittest.TestCase):
    def test_returns_probs(self):
        d = VolClusterDetector()
        p = d.predict(_bull())
        self.assertAlmostEqual(sum(p.values()), 1.0, places=3)
    def test_high_vol_biases_crisis(self):
        d = VolClusterDetector()
        for _ in range(30): d.predict(_bull())
        p = d.predict(_crisis())
        self.assertGreater(p["crisis"], p["sideways"])

class TestMarkovSwitch(unittest.TestCase):
    def test_returns_probs(self):
        p = MarkovSwitchDetector().predict(_bull())
        self.assertAlmostEqual(sum(p.values()), 1.0, places=3)
    def test_converges(self):
        d = MarkovSwitchDetector()
        for _ in range(20): d.predict(_bull())
        p = d.predict(_bull())
        self.assertIn(max(p, key=p.get), ["bull", "sideways"])

# --- Meta-learner ---
class TestMetaLearner(unittest.TestCase):
    def test_combine(self):
        ml = MetaLearner()
        probs = {det: {r: 0.25 for r in REGIMES} for det in DETECTOR_NAMES}
        probs["hmm"]["bull"] = 0.80
        out = ml.combine("d0", probs)
        self.assertIsInstance(out, EnsembleOutput)
        self.assertIn(out.consensus, REGIMES)
    def test_confidence_range(self):
        ml = MetaLearner()
        probs = {det: {r: 0.25 for r in REGIMES} for det in DETECTOR_NAMES}
        out = ml.combine("d0", probs)
        self.assertGreaterEqual(out.confidence, 0)
        self.assertLessEqual(out.confidence, 1)
    def test_agreement_rate(self):
        ml = MetaLearner()
        probs = {det: {"bull": 0.9, "bear": 0.05, "sideways": 0.03, "crisis": 0.02} for det in DETECTOR_NAMES}
        out = ml.combine("d0", probs)
        self.assertAlmostEqual(out.agreement_rate, 1.0)
    def test_disagreement_flag(self):
        ml = MetaLearner()
        probs = {"hmm": {"bull": 0.9, "bear": 0.05, "sideways": 0.03, "crisis": 0.02},
                 "rules": {"bear": 0.9, "bull": 0.05, "sideways": 0.03, "crisis": 0.02},
                 "vol_cluster": {"sideways": 0.9, "bull": 0.05, "bear": 0.03, "crisis": 0.02},
                 "markov_switch": {"crisis": 0.9, "bull": 0.05, "bear": 0.03, "sideways": 0.02}}
        out = ml.combine("d0", probs)
        self.assertTrue(out.is_disagreement)
    def test_min_hold(self):
        ml = MetaLearner(min_hold=5)
        p_bull = {det: {"bull": 0.9, "bear": 0.03, "sideways": 0.04, "crisis": 0.03} for det in DETECTOR_NAMES}
        p_crisis = {det: {"crisis": 0.9, "bull": 0.03, "bear": 0.04, "sideways": 0.03} for det in DETECTOR_NAMES}
        ml.combine("d0", p_bull)
        ml.combine("d1", p_bull)
        out = ml.combine("d2", p_crisis)
        self.assertEqual(out.consensus, "bull")  # held by min_hold
    def test_adapt_weights(self):
        ml = MetaLearner()
        for _ in range(10):
            ml.update_accuracy("hmm", "bull", True)
            ml.update_accuracy("rules", "bull", False)
        ml.adapt_weights()
        self.assertGreater(ml.weights["hmm"], ml.weights["rules"])

# --- Score detector ---
class TestScoreDetector(unittest.TestCase):
    def test_perfect(self):
        pred = ["bull"] * 10
        truth = ["bull"] * 10
        s = _score_detector("test", pred, truth)
        self.assertAlmostEqual(s.accuracy, 1.0)
    def test_all_wrong(self):
        pred = ["crisis"] * 10
        truth = ["bull"] * 10
        s = _score_detector("test", pred, truth)
        self.assertAlmostEqual(s.accuracy, 0.0)
    def test_false_alarm(self):
        pred = ["crisis"] * 5 + ["bull"] * 5
        truth = ["bull"] * 10
        s = _score_detector("test", pred, truth)
        self.assertGreater(s.false_alarm_rate, 0)
    def test_empty(self):
        s = _score_detector("test", [], [])
        self.assertEqual(s.accuracy, 0)

# --- Synthetic data ---
class TestSyntheticData(unittest.TestCase):
    def test_length(self):
        self.assertEqual(len(generate_test_data(500)), 500)
    def test_deterministic(self):
        a = generate_test_data(50, 42)
        b = generate_test_data(50, 42)
        self.assertEqual(a[0].vix, b[0].vix)
    def test_has_all_regimes(self):
        data = generate_test_data(800)
        regimes = set(o.ground_truth for o in data)
        self.assertEqual(regimes, set(REGIMES))
    def test_ground_truth(self):
        for o in generate_test_data(100):
            self.assertIn(o.ground_truth, REGIMES)

# --- Full ensemble ---
class TestRegimeEnsembleV2(unittest.TestCase):
    def test_classify_single(self):
        e = RegimeEnsembleV2()
        out = e.classify(_bull())
        self.assertIn(out.consensus, REGIMES)
    def test_classify_series(self):
        e = RegimeEnsembleV2()
        data = generate_test_data(200)
        outputs, comp = e.classify_series(data)
        self.assertEqual(len(outputs), 200)
        self.assertIsInstance(comp, ComparisonResult)
    def test_ensemble_fewer_transitions(self):
        e = RegimeEnsembleV2()
        _, comp = e.classify_series(generate_test_data(500))
        max_ind = max(s.n_transitions for s in comp.individual_scores)
        self.assertLessEqual(comp.ensemble_score.n_transitions, max_ind)
    def test_whipsaw_reduction(self):
        _, comp = RegimeEnsembleV2().classify_series(generate_test_data(500))
        self.assertGreaterEqual(comp.whipsaw_reduction_pct, 0)
    def test_accuracy_positive(self):
        _, comp = RegimeEnsembleV2().classify_series(generate_test_data(500))
        self.assertGreater(comp.ensemble_score.accuracy, 0)
    def test_agreement_timeline(self):
        _, comp = RegimeEnsembleV2().classify_series(generate_test_data(100))
        self.assertEqual(len(comp.agreement_timeline), 100)
        for a in comp.agreement_timeline:
            self.assertGreaterEqual(a, 0); self.assertLessEqual(a, 1)
    def test_reset(self):
        e = RegimeEnsembleV2()
        e.classify(_bull())
        e.reset()
        out = e.classify(_bull())
        self.assertIn(out.consensus, REGIMES)
    def test_detector_votes_present(self):
        out = RegimeEnsembleV2().classify(_bull())
        self.assertEqual(set(out.detector_votes.keys()), set(DETECTOR_NAMES))

if __name__ == "__main__":
    unittest.main()
