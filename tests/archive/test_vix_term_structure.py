"""Tests for compass.vix_term_structure."""
import math, unittest
from compass.vix_term_structure import (
    BacktestResult, MeanReversionSignal, RegimeStats, SizingRecommendation,
    TermStructureMetrics, VIXCurvePoint, VIXCurveSnapshot, VIXTermEngine,
    VIXTermResult, compute_regime_stats, compute_sizing,
    compute_term_structure, generate_mean_reversion_signals,
    generate_sample_data, run_backtest,
)

def _snap(spot=18.0, f1=19.0, f2=20.0, f3=21.0, ret=0.001, date="d0"):
    return VIXCurveSnapshot(date, spot, [VIXCurvePoint(1,f1), VIXCurvePoint(2,f2), VIXCurvePoint(3,f3)], ret)

def _contango(): return _snap(16, 17, 18.5, 19.5)
def _backwardation(): return _snap(35, 32, 30, 29, ret=-0.02)
def _flat(): return _snap(20, 20.1, 20.2, 20.3)
def _sample(n=200): return generate_sample_data(n)

# --- Term structure ---
class TestTermStructure(unittest.TestCase):
    def test_contango(self):
        m = compute_term_structure(_contango())
        self.assertEqual(m.regime, "contango")
        self.assertGreater(m.slope, 0)

    def test_backwardation(self):
        m = compute_term_structure(_backwardation())
        self.assertEqual(m.regime, "backwardation")
        self.assertLess(m.slope, 0)

    def test_flat(self):
        m = compute_term_structure(_flat())
        self.assertEqual(m.regime, "flat")

    def test_spot_to_front(self):
        m = compute_term_structure(_contango())
        self.assertGreater(m.spot_to_front, 0)

    def test_zscore_with_history(self):
        history = [0.03, 0.04, 0.02, 0.05, 0.03, 0.04, 0.02, 0.03, 0.04, 0.03]
        m = compute_term_structure(_contango(), history)
        self.assertNotEqual(m.slope_zscore, 0)

    def test_zscore_without_history(self):
        m = compute_term_structure(_contango())
        self.assertEqual(m.slope_zscore, 0)

    def test_extreme_detection(self):
        history = [0.01] * 20
        extreme = _snap(16, 17, 22, 25)  # very steep
        m = compute_term_structure(extreme, history)
        self.assertTrue(m.is_extreme)

# --- Regime stats ---
class TestRegimeStats(unittest.TestCase):
    def test_returns_three_regimes(self):
        data = _sample(100)
        engine = VIXTermEngine(data)
        r = engine.analyse()
        stats = r.regime_stats
        regimes = {s.regime for s in stats}
        self.assertEqual(regimes, {"contango", "flat", "backwardation"})

    def test_pct_sums_roughly(self):
        data = _sample(200)
        r = VIXTermEngine(data).analyse()
        total = sum(s.pct_of_total for s in r.regime_stats)
        self.assertAlmostEqual(total, 100.0, delta=1.0)

    def test_contango_dominant(self):
        data = _sample(300)
        r = VIXTermEngine(data).analyse()
        self.assertGreater(r.pct_contango, 30)

# --- Mean reversion signals ---
class TestMeanReversion(unittest.TestCase):
    def test_generates_signals(self):
        data = _sample(300)
        r = VIXTermEngine(data).analyse()
        self.assertGreater(len(r.signals), 0)

    def test_sell_premium_in_extreme_contango(self):
        data = _sample(300)
        r = VIXTermEngine(data).analyse()
        sells = [s for s in r.signals if s.direction == "sell_premium"]
        self.assertGreater(len(sells), 0)

    def test_buy_protection_in_backwardation(self):
        data = _sample(500)
        r = VIXTermEngine(data).analyse()
        buys = [s for s in r.signals if s.direction == "buy_protection"]
        self.assertGreater(len(buys), 0)

    def test_strength_range(self):
        data = _sample(300)
        r = VIXTermEngine(data).analyse()
        for s in r.signals:
            self.assertGreaterEqual(s.strength, 0)
            self.assertLessEqual(s.strength, 1)

    def test_no_signals_with_high_threshold(self):
        metrics = [compute_term_structure(_contango())]
        sigs = generate_mean_reversion_signals(metrics, zscore_threshold=100)
        self.assertEqual(len(sigs), 0)

# --- Sizing ---
class TestSizing(unittest.TestCase):
    def test_contango_scales_up(self):
        m = [compute_term_structure(_contango())]
        sizing = compute_sizing(m, base_size=0.10)
        self.assertGreater(sizing[0].final_size_pct, 0.10)

    def test_backwardation_scales_down(self):
        m = [compute_term_structure(_backwardation())]
        sizing = compute_sizing(m, base_size=0.10)
        self.assertLess(sizing[0].final_size_pct, 0.10)

    def test_all_positive(self):
        data = _sample(100)
        r = VIXTermEngine(data).analyse()
        for s in r.sizing:
            self.assertGreater(s.final_size_pct, 0)

# --- Backtest ---
class TestBacktest(unittest.TestCase):
    def test_returns_result(self):
        data = _sample(200)
        r = VIXTermEngine(data).analyse()
        self.assertIsInstance(r.backtest, BacktestResult)
        self.assertGreater(r.backtest.n_trades, 0)

    def test_win_rate_range(self):
        r = VIXTermEngine(_sample(300)).analyse()
        self.assertGreaterEqual(r.backtest.win_rate, 0)
        self.assertLessEqual(r.backtest.win_rate, 1)

    def test_contango_pnl_positive(self):
        r = VIXTermEngine(_sample(300)).analyse()
        self.assertGreater(r.backtest.contango_pnl, 0)

    def test_sharpe_finite(self):
        r = VIXTermEngine(_sample(200)).analyse()
        self.assertTrue(math.isfinite(r.backtest.sharpe))

    def test_max_dd_non_negative(self):
        r = VIXTermEngine(_sample(200)).analyse()
        self.assertGreaterEqual(r.backtest.max_dd, 0)

    def test_empty(self):
        bt = run_backtest([], [])
        self.assertEqual(bt.n_trades, 0)

# --- Synthetic data ---
class TestSyntheticData(unittest.TestCase):
    def test_correct_count(self):
        self.assertEqual(len(generate_sample_data(100)), 100)

    def test_deterministic(self):
        a = generate_sample_data(50, seed=42)
        b = generate_sample_data(50, seed=42)
        self.assertEqual(a[0].spot_vix, b[0].spot_vix)

    def test_futures_sorted(self):
        for s in generate_sample_data(50):
            months = [f.month for f in s.futures]
            self.assertEqual(months, sorted(months))

    def test_vix_positive(self):
        for s in generate_sample_data(100):
            self.assertGreater(s.spot_vix, 0)

# --- Full engine ---
class TestVIXTermEngine(unittest.TestCase):
    def test_analyse(self):
        r = VIXTermEngine(_sample(200)).analyse()
        self.assertIsInstance(r, VIXTermResult)
        self.assertEqual(r.n_days, 200)

    def test_avg_contango(self):
        r = VIXTermEngine(_sample(200)).analyse()
        self.assertIsInstance(r.avg_contango, float)

    def test_pct_contango_range(self):
        r = VIXTermEngine(_sample(300)).analyse()
        self.assertGreaterEqual(r.pct_contango, 0)
        self.assertLessEqual(r.pct_contango, 100)

    def test_sizing_populated(self):
        r = VIXTermEngine(_sample(100)).analyse()
        self.assertEqual(len(r.sizing), 100)

    def test_metrics_populated(self):
        r = VIXTermEngine(_sample(50)).analyse()
        self.assertEqual(len(r.metrics), 50)

if __name__ == "__main__":
    unittest.main()
