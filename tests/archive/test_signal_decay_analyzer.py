"""Tests for compass.signal_decay_analyzer."""
import math, unittest
from compass.signal_decay_analyzer import (
    ACFResult, FullDecayResult, HalfLifeResult, ICDecayResult,
    R2DecayResult, RebalanceRecommendation, SignalDecayAnalysis,
    SignalDecayEngine, SignalSeries,
    compute_acf, compute_ic_decay, compute_r2_decay,
    fit_exponential_decay, generate_test_signals, recommend_rebalance,
)

def _signals(n=300): return generate_test_signals(n)

def _fast_signal(n=300):
    import random; rng = random.Random(42)
    rets = [rng.gauss(0.0003, 0.01) for _ in range(n)]
    v = 0.0; vals = []
    for r in rets:
        v = 0.2 * v + 0.8 * r * 50 + rng.gauss(0, 0.5)
        vals.append(v)
    return SignalSeries("fast", vals, rets)

def _slow_signal(n=300):
    import random; rng = random.Random(42)
    rets = [rng.gauss(0.0003, 0.01) for _ in range(n)]
    v = 0.0; vals = []
    for r in rets:
        v = 0.98 * v + 0.02 * r * 20 + rng.gauss(0, 0.05)
        vals.append(v)
    return SignalSeries("slow", vals, rets)

# --- ACF ---
class TestACF(unittest.TestCase):
    def test_returns_result(self):
        acf = compute_acf(_fast_signal())
        self.assertIsInstance(acf, ACFResult)
    def test_lag_count(self):
        acf = compute_acf(_fast_signal())
        self.assertEqual(len(acf.lags), len(acf.autocorrelations))
    def test_acf_range(self):
        for ac in compute_acf(_fast_signal()).autocorrelations:
            self.assertGreaterEqual(ac, -1); self.assertLessEqual(ac, 1)
    def test_lag1_positive_for_persistent(self):
        acf = compute_acf(_slow_signal())
        self.assertGreater(acf.autocorrelations[0], 0.3)
    def test_fast_decays_quickly(self):
        acf = compute_acf(_fast_signal())
        # Later lags should be smaller than lag-1
        self.assertGreater(abs(acf.autocorrelations[0]), abs(acf.autocorrelations[-1]))

# --- R² decay ---
class TestR2Decay(unittest.TestCase):
    def test_returns_result(self):
        r2 = compute_r2_decay(_fast_signal())
        self.assertIsInstance(r2, R2DecayResult)
    def test_r2_non_negative(self):
        for p in compute_r2_decay(_fast_signal()).points:
            self.assertGreaterEqual(p.r_squared, 0)
    def test_peak_horizon_positive(self):
        r2 = compute_r2_decay(_fast_signal())
        self.assertGreater(r2.peak_horizon, 0)
    def test_peak_r2_positive_for_signal(self):
        r2 = compute_r2_decay(_fast_signal())
        self.assertGreater(r2.peak_r2, 0)

# --- IC decay ---
class TestICDecay(unittest.TestCase):
    def test_returns_result(self):
        ic = compute_ic_decay(_fast_signal())
        self.assertIsInstance(ic, ICDecayResult)
    def test_ic_range(self):
        for p in compute_ic_decay(_fast_signal()).points:
            self.assertGreaterEqual(p.ic, -1); self.assertLessEqual(p.ic, 1)
    def test_peak_ic_exists(self):
        ic = compute_ic_decay(_fast_signal())
        self.assertGreater(abs(ic.peak_ic), 0)
    def test_ic_1d_populated(self):
        ic = compute_ic_decay(_fast_signal())
        self.assertIsInstance(ic.ic_1d, float)
    def test_fast_signal_ic_decays(self):
        ic = compute_ic_decay(_fast_signal())
        # |IC| at short horizon should exceed |IC| at long horizon
        self.assertGreater(ic.points[0].ic_abs, ic.points[-1].ic_abs)

# --- Half-life ---
class TestHalfLife(unittest.TestCase):
    def test_fast_signal_short_halflife(self):
        ic = compute_ic_decay(_fast_signal())
        hl = fit_exponential_decay(ic)
        if math.isfinite(hl.half_life_days):
            self.assertLess(hl.half_life_days, 30)
    def test_slow_signal_longer_halflife(self):
        ic_fast = compute_ic_decay(_fast_signal())
        ic_slow = compute_ic_decay(_slow_signal())
        hl_fast = fit_exponential_decay(ic_fast)
        hl_slow = fit_exponential_decay(ic_slow)
        self.assertGreaterEqual(hl_slow.half_life_days, hl_fast.half_life_days)
    def test_decay_rate_positive(self):
        ic = compute_ic_decay(_fast_signal())
        hl = fit_exponential_decay(ic)
        self.assertGreaterEqual(hl.decay_rate, 0)
    def test_category_valid(self):
        ic = compute_ic_decay(_fast_signal())
        hl = fit_exponential_decay(ic)
        self.assertIn(hl.category, ["fast", "medium", "slow"])
    def test_fit_quality_range(self):
        ic = compute_ic_decay(_fast_signal())
        hl = fit_exponential_decay(ic)
        self.assertGreaterEqual(hl.fit_quality, 0)
        self.assertLessEqual(hl.fit_quality, 1.01)
    def test_no_signal_infinite(self):
        import random; rng = random.Random(99)
        noise = SignalSeries("noise", [rng.gauss(0,1) for _ in range(300)],
                              [rng.gauss(0,0.01) for _ in range(300)])
        ic = compute_ic_decay(noise)
        hl = fit_exponential_decay(ic)
        # Noise should have very long or infinite half-life
        self.assertGreater(hl.half_life_days, 10)

# --- Rebalance ---
class TestRebalance(unittest.TestCase):
    def test_fast_daily(self):
        hl = HalfLifeResult("fast", 2.0, 0.35, 0.1, 0.8, "fast")
        rec = recommend_rebalance(hl)
        self.assertEqual(rec.optimal_rebalance_days, 1)
    def test_medium(self):
        hl = HalfLifeResult("med", 15.0, 0.05, 0.05, 0.7, "medium")
        rec = recommend_rebalance(hl)
        self.assertGreater(rec.optimal_rebalance_days, 3)
        self.assertLess(rec.optimal_rebalance_days, 15)
    def test_slow(self):
        hl = HalfLifeResult("slow", 50.0, 0.01, 0.02, 0.6, "slow")
        rec = recommend_rebalance(hl)
        self.assertGreaterEqual(rec.optimal_rebalance_days, 10)
    def test_infinite(self):
        hl = HalfLifeResult("inf", float('inf'), 0, 0, 0, "slow")
        rec = recommend_rebalance(hl)
        self.assertEqual(rec.optimal_rebalance_days, 20)
    def test_has_reasoning(self):
        rec = recommend_rebalance(HalfLifeResult("x", 5.0, 0.1, 0.05, 0.7, "medium"))
        self.assertGreater(len(rec.reasoning), 10)

# --- Full engine ---
class TestSignalDecayEngine(unittest.TestCase):
    def test_analyse(self):
        r = SignalDecayEngine(_signals()).analyse()
        self.assertIsInstance(r, FullDecayResult)
        self.assertEqual(r.n_signals, 6)
    def test_ranking_by_halflife(self):
        r = SignalDecayEngine(_signals()).analyse()
        self.assertEqual(len(r.ranking_by_half_life), 6)
        # Should be sorted ascending
        hls = [hl for _, hl in r.ranking_by_half_life]
        self.assertEqual(hls, sorted(hls))
    def test_ranking_by_ic(self):
        r = SignalDecayEngine(_signals()).analyse()
        ics = [ic for _, ic in r.ranking_by_peak_ic]
        self.assertEqual(ics, sorted(ics, reverse=True))
    def test_fastest_slowest(self):
        r = SignalDecayEngine(_signals()).analyse()
        self.assertIn(r.fastest_signal, [s.name for s in _signals()])
        self.assertIn(r.slowest_signal, [s.name for s in _signals()])
    def test_avg_halflife_positive(self):
        r = SignalDecayEngine(_signals()).analyse()
        self.assertGreater(r.avg_half_life, 0)
    def test_each_signal_analysed(self):
        r = SignalDecayEngine(_signals()).analyse()
        for a in r.analyses:
            self.assertIsInstance(a, SignalDecayAnalysis)
            self.assertIsNotNone(a.acf)
            self.assertIsNotNone(a.half_life)
            self.assertIsNotNone(a.rebalance)

# --- Synthetic data ---
class TestSyntheticData(unittest.TestCase):
    def test_count(self):
        sigs = generate_test_signals(200)
        self.assertEqual(len(sigs), 6)
        for s in sigs:
            self.assertEqual(len(s.values), 200)
    def test_deterministic(self):
        a = generate_test_signals(100, seed=42)
        b = generate_test_signals(100, seed=42)
        self.assertEqual(a[0].values[0], b[0].values[0])
    def test_has_known_names(self):
        names = {s.name for s in generate_test_signals(100)}
        self.assertIn("ml_ensemble", names)
        self.assertIn("momentum_20d", names)
        self.assertIn("random_noise", names)
    def test_returns_aligned(self):
        for s in generate_test_signals(100):
            self.assertEqual(len(s.values), len(s.forward_returns))

if __name__ == "__main__":
    unittest.main()
