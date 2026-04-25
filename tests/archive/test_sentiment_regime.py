"""Tests for compass.sentiment_regime."""
import unittest
from compass.sentiment_regime import (
    SENTIMENT_REGIMES, CUSUMChangepoint, ContrarianSignal,
    NormalisedComponents, SentimentObs, SentimentRegimeEngine,
    SentimentRegimeResult, TimingBacktest, VsVIXComparison,
    backtest_timing_filter, classify_sentiment, compare_vs_vix,
    detect_cusum_changepoints, generate_contrarian_signals,
    generate_test_data, normalise_observation,
)

def _obs(**kw):
    defaults = dict(date="d0", put_call_ratio=0.85, vix_term_slope=0.04,
                    skew_index=130, credit_spread_bps=350, spy_return_5d=0.001)
    defaults.update(kw)
    return SentimentObs(**defaults)

def _fear_obs(): return _obs(put_call_ratio=1.3, vix_term_slope=-0.05, skew_index=160, credit_spread_bps=700)
def _greed_obs(): return _obs(put_call_ratio=0.60, vix_term_slope=0.08, skew_index=110, credit_spread_bps=250)
def _sample(n=300): return generate_test_data(n)

# --- Normalisation ---
class TestNormalise(unittest.TestCase):
    def test_returns_components(self):
        history = [_obs()] * 30
        nc = normalise_observation(_obs(), history)
        self.assertIsInstance(nc, NormalisedComponents)
    def test_composite_range(self):
        history = [_obs()] * 30
        nc = normalise_observation(_obs(), history)
        self.assertGreaterEqual(nc.composite, -1)
        self.assertLessEqual(nc.composite, 1)
    def test_fear_obs_negative(self):
        history = [_obs()] * 30
        nc = normalise_observation(_fear_obs(), history)
        self.assertLess(nc.composite, 0)
    def test_greed_obs_positive(self):
        history = [_obs()] * 30
        nc = normalise_observation(_greed_obs(), history)
        self.assertGreater(nc.composite, 0)
    def test_all_components_bounded(self):
        history = [_obs()] * 30
        nc = normalise_observation(_fear_obs(), history)
        for score in [nc.pc_score, nc.vts_score, nc.skew_score, nc.credit_score]:
            self.assertGreaterEqual(score, -1)
            self.assertLessEqual(score, 1)

# --- Classification ---
class TestClassify(unittest.TestCase):
    def test_extreme_fear(self):
        self.assertEqual(classify_sentiment(-0.8), "extreme_fear")
    def test_fear(self):
        self.assertEqual(classify_sentiment(-0.4), "fear")
    def test_neutral(self):
        self.assertEqual(classify_sentiment(0.0), "neutral")
    def test_greed(self):
        self.assertEqual(classify_sentiment(0.4), "greed")
    def test_extreme_greed(self):
        self.assertEqual(classify_sentiment(0.8), "extreme_greed")
    def test_all_regimes_valid(self):
        for v in [-1, -0.5, -0.3, 0, 0.3, 0.5, 1]:
            self.assertIn(classify_sentiment(v), SENTIMENT_REGIMES)

# --- CUSUM ---
class TestCUSUM(unittest.TestCase):
    def test_detects_shift(self):
        data = _sample(300)
        engine = SentimentRegimeEngine(data)
        r = engine.analyse()
        self.assertGreater(len(r.changepoints), 0)
    def test_has_direction(self):
        data = _sample(300)
        r = SentimentRegimeEngine(data).analyse()
        for cp in r.changepoints:
            self.assertIn(cp.direction, ["bullish_shift", "bearish_shift"])
    def test_short_data(self):
        cps = detect_cusum_changepoints([0.1]*10, ["d"]*10)
        self.assertEqual(cps, [])
    def test_constant_no_changepoints(self):
        cps = detect_cusum_changepoints([0.0]*100, [f"d{i}" for i in range(100)])
        self.assertEqual(len(cps), 0)

# --- Contrarian signals ---
class TestContrarian(unittest.TestCase):
    def test_buy_at_fear(self):
        composites = [0.0]*10 + [-0.7]*5 + [0.0]*10
        dates = [f"d{i}" for i in range(25)]
        sigs = generate_contrarian_signals(composites, dates, extreme_threshold=0.5)
        buys = [s for s in sigs if s.signal == "buy"]
        self.assertGreater(len(buys), 0)
    def test_reduce_at_greed(self):
        composites = [0.0]*10 + [0.8]*5 + [0.0]*10
        dates = [f"d{i}" for i in range(25)]
        sigs = generate_contrarian_signals(composites, dates, extreme_threshold=0.5)
        reduces = [s for s in sigs if s.signal == "reduce"]
        self.assertGreater(len(reduces), 0)
    def test_cooldown(self):
        composites = [-0.8]*20
        dates = [f"d{i}" for i in range(20)]
        sigs = generate_contrarian_signals(composites, dates, cooldown=10)
        self.assertLessEqual(len(sigs), 2)
    def test_no_signal_moderate(self):
        composites = [0.2]*20
        dates = [f"d{i}" for i in range(20)]
        sigs = generate_contrarian_signals(composites, dates)
        self.assertEqual(len(sigs), 0)
    def test_strength_range(self):
        composites = [-0.9, 0.9]
        dates = ["d0", "d1"]
        for s in generate_contrarian_signals(composites, dates, cooldown=0):
            self.assertGreaterEqual(s.strength, 0)
            self.assertLessEqual(s.strength, 1)

# --- Backtest ---
class TestBacktest(unittest.TestCase):
    def test_returns_result(self):
        data = _sample(200)
        r = SentimentRegimeEngine(data).analyse()
        self.assertIsInstance(r.backtest, TimingBacktest)
    def test_n_days_correct(self):
        data = _sample(100)
        r = SentimentRegimeEngine(data).analyse()
        self.assertEqual(r.backtest.n_days, 100)
    def test_empty(self):
        bt = backtest_timing_filter([], [])
        self.assertEqual(bt.n_days, 0)
    def test_accuracy_range(self):
        data = _sample(300)
        r = SentimentRegimeEngine(data).analyse()
        self.assertGreaterEqual(r.backtest.fear_buy_accuracy, 0)
        self.assertLessEqual(r.backtest.fear_buy_accuracy, 1)

# --- VIX comparison ---
class TestVsVIX(unittest.TestCase):
    def test_returns_comparison(self):
        data = _sample(200)
        r = SentimentRegimeEngine(data).analyse()
        self.assertIsInstance(r.vs_vix, VsVIXComparison)
    def test_composite_has_changepoints(self):
        data = _sample(300)
        r = SentimentRegimeEngine(data).analyse()
        self.assertGreaterEqual(r.vs_vix.composite_changepoints, 0)

# --- Synthetic data ---
class TestSyntheticData(unittest.TestCase):
    def test_correct_count(self):
        self.assertEqual(len(generate_test_data(100)), 100)
    def test_deterministic(self):
        a = generate_test_data(50, seed=42)
        b = generate_test_data(50, seed=42)
        self.assertEqual(a[0].put_call_ratio, b[0].put_call_ratio)
    def test_has_variety(self):
        data = generate_test_data(300)
        pcs = [d.put_call_ratio for d in data]
        self.assertGreater(max(pcs) - min(pcs), 0.2)

# --- Full engine ---
class TestSentimentRegimeEngine(unittest.TestCase):
    def test_analyse(self):
        r = SentimentRegimeEngine(_sample(200)).analyse()
        self.assertIsInstance(r, SentimentRegimeResult)
        self.assertEqual(r.n_days, 200)
    def test_regimes_valid(self):
        r = SentimentRegimeEngine(_sample(200)).analyse()
        for reg in r.regimes:
            self.assertIn(reg, SENTIMENT_REGIMES)
    def test_regime_counts_sum(self):
        r = SentimentRegimeEngine(_sample(200)).analyse()
        self.assertEqual(sum(r.regime_counts.values()), 200)
    def test_current_regime(self):
        r = SentimentRegimeEngine(_sample(100)).analyse()
        self.assertIn(r.current_regime, SENTIMENT_REGIMES)
    def test_composites_length(self):
        r = SentimentRegimeEngine(_sample(150)).analyse()
        self.assertEqual(len(r.composites), 150)
    def test_signals_generated(self):
        r = SentimentRegimeEngine(_sample(400)).analyse()
        self.assertGreater(len(r.signals), 0)

if __name__ == "__main__":
    unittest.main()
