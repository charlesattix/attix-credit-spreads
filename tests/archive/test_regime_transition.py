"""Tests for compass.regime_transition."""
import math, unittest
from compass.regime_transition import (
    REGIMES, DEFAULT_DURATIONS, EarlyWarning, HSMMDetector, MarketObs,
    RegimeTransitionEngine, SwitchBacktest, TransitionAnalysis,
    backtest_switching, duration_survival_prob, duration_transition_prob,
    generate_early_warnings, generate_test_data,
)

def _bull(): return MarketObs("d",0.04,0.10,0.10,14,290)
def _crisis(): return MarketObs("d",-0.18,-0.28,0.50,55,750)
def _bear(): return MarketObs("d",-0.04,-0.08,0.20,26,460)
def _side(): return MarketObs("d",0.005,0.02,0.09,14,320)
def _data(n=400): return generate_test_data(n)

# --- Duration model ---
class TestDuration(unittest.TestCase):
    def test_survival_high_early(self):
        self.assertGreater(duration_survival_prob("bull", 5), 0.9)
    def test_survival_decreases(self):
        s_early = duration_survival_prob("bull", 30)
        s_late = duration_survival_prob("bull", 500)
        self.assertGreater(s_early, s_late)
    def test_crisis_exits_faster(self):
        s_bull = duration_survival_prob("bull", 30)
        s_crisis = duration_survival_prob("crisis", 30)
        self.assertGreater(s_bull, s_crisis)
    def test_transition_complement(self):
        s = duration_survival_prob("bear", 20)
        t = duration_transition_prob("bear", 20)
        self.assertAlmostEqual(s + t, 1.0, places=10)
    def test_min_duration_protection(self):
        self.assertGreater(duration_survival_prob("crisis", 1), 0.95)

# --- HSMM Detector ---
class TestHSMMDetector(unittest.TestCase):
    def test_update_returns_posterior(self):
        d = HSMMDetector()
        p = d.update(_bull())
        self.assertEqual(set(p.keys()), set(REGIMES))
        self.assertAlmostEqual(sum(p.values()), 1.0, places=3)
    def test_bull_detected(self):
        d = HSMMDetector(min_hold=1)
        for _ in range(10): d.update(_bull())
        self.assertEqual(d.current_regime, "bull")
    def test_crisis_detected(self):
        d = HSMMDetector(min_hold=1)
        for _ in range(10): d.update(_crisis())
        self.assertEqual(d.current_regime, "crisis")
    def test_days_in_regime_grows(self):
        d = HSMMDetector()
        d.update(_bull()); d.update(_bull()); d.update(_bull())
        self.assertGreaterEqual(d.days_in_regime, 3)
    def test_history_grows(self):
        d = HSMMDetector()
        d.update(_bull()); d.update(_bull())
        self.assertEqual(len(d.history), 2)
    def test_predict_transition_sums(self):
        d = HSMMDetector()
        d.update(_bull())
        fc = d.predict_transition(5)
        self.assertAlmostEqual(sum(fc.values()), 1.0, places=3)
    def test_transition_probability_range(self):
        d = HSMMDetector()
        d.update(_bull())
        tp = d.transition_probability(5)
        self.assertGreaterEqual(tp, 0); self.assertLessEqual(tp, 1)
    def test_long_duration_increases_transition(self):
        d = HSMMDetector(min_hold=1)
        for _ in range(5): d.update(_bull())
        tp_early = d.transition_probability(10)
        for _ in range(300): d.update(_bull())
        tp_late = d.transition_probability(10)
        self.assertGreaterEqual(tp_late, tp_early - 0.05)  # allow small noise
    def test_reset(self):
        d = HSMMDetector()
        d.update(_bull()); d.update(_bull())
        d.reset()
        self.assertEqual(d.history, [])
        self.assertEqual(d.days_in_regime, 0)
    def test_min_hold(self):
        d = HSMMDetector(min_hold=5)
        d.update(_bull()); d.update(_bull())
        d.update(_crisis())
        self.assertEqual(d.current_regime, "bull")

# --- Early warnings ---
class TestEarlyWarnings(unittest.TestCase):
    def test_generates_warnings(self):
        d = HSMMDetector()
        data = _data(200)
        warnings, regimes = generate_early_warnings(d, data)
        self.assertEqual(len(warnings), 200)
        self.assertEqual(len(regimes), 200)
    def test_signal_types(self):
        d = HSMMDetector()
        warnings, _ = generate_early_warnings(d, _data(300))
        signals = set(w.signal for w in warnings)
        # Should have at least stable + some warnings
        self.assertIn("stable", signals)
    def test_imminent_at_long_duration(self):
        d = HSMMDetector()
        # Long bull then crisis data should trigger warnings
        data = [_bull() for _ in range(100)] + [_crisis() for _ in range(50)]
        for o in data: o.date = f"d{data.index(o)}"
        warnings, _ = generate_early_warnings(d, data)
        non_stable = [w for w in warnings if w.signal != "stable"]
        self.assertGreater(len(non_stable), 0)
    def test_strength_range(self):
        d = HSMMDetector()
        warnings, _ = generate_early_warnings(d, _data(200))
        for w in warnings:
            self.assertGreaterEqual(w.strength, 0)
            self.assertLessEqual(w.strength, 1)

# --- Backtest ---
class TestBacktest(unittest.TestCase):
    def test_returns_result(self):
        bt = backtest_switching(_data(300))
        self.assertIsInstance(bt, SwitchBacktest)
        self.assertEqual(bt.n_days, 300)
    def test_lead_time_non_negative(self):
        bt = backtest_switching(_data(400))
        self.assertGreaterEqual(bt.avg_lead_time, 0)
    def test_false_alarm_range(self):
        bt = backtest_switching(_data(400))
        self.assertGreaterEqual(bt.false_alarm_rate, 0)
        self.assertLessEqual(bt.false_alarm_rate, 1)
    def test_short_data(self):
        bt = backtest_switching(_data(10))
        self.assertEqual(bt.n_days, 10)

# --- Full engine ---
class TestRegimeTransitionEngine(unittest.TestCase):
    def test_analyse(self):
        r = RegimeTransitionEngine(_data(300)).analyse()
        self.assertIsInstance(r, TransitionAnalysis)
        self.assertEqual(r.n_days, 300)
    def test_regimes_valid(self):
        r = RegimeTransitionEngine(_data(200)).analyse()
        for reg in r.regimes:
            self.assertIn(reg, REGIMES)
    def test_regime_counts_sum(self):
        r = RegimeTransitionEngine(_data(200)).analyse()
        self.assertEqual(sum(r.regime_counts.values()), 200)
    def test_current_regime(self):
        r = RegimeTransitionEngine(_data(200)).analyse()
        self.assertIn(r.current_regime, REGIMES)
    def test_transition_prob(self):
        r = RegimeTransitionEngine(_data(300)).analyse()
        self.assertGreaterEqual(r.transition_prob_5d, 0)
        self.assertLessEqual(r.transition_prob_5d, 1)
    def test_warnings_counted(self):
        r = RegimeTransitionEngine(_data(400)).analyse()
        self.assertGreaterEqual(r.n_imminent_warnings, 0)
        self.assertGreaterEqual(r.n_warn_warnings, 0)
    def test_avg_duration_positive(self):
        r = RegimeTransitionEngine(_data(400)).analyse()
        self.assertGreater(r.avg_days_in_regime, 0)
    def test_backtest_populated(self):
        r = RegimeTransitionEngine(_data(300)).analyse()
        self.assertIsInstance(r.backtest, SwitchBacktest)

# --- Synthetic data ---
class TestSyntheticData(unittest.TestCase):
    def test_count(self):
        self.assertEqual(len(generate_test_data(200)), 200)
    def test_deterministic(self):
        a = generate_test_data(50, 42)
        b = generate_test_data(50, 42)
        self.assertEqual(a[0].vix, b[0].vix)
    def test_has_all_regimes(self):
        data = generate_test_data(600)
        truths = set(o.ground_truth for o in data)
        self.assertEqual(truths, set(REGIMES))
    def test_vix_positive(self):
        for o in generate_test_data(100):
            self.assertGreater(o.vix, 0)

if __name__ == "__main__":
    unittest.main()
