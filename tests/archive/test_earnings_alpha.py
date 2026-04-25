"""Tests for compass.earnings_alpha."""
import unittest
from compass.earnings_alpha import (
    BacktestResult, CalendarEntry, ClusterAnalysis, CrushEntrySignal,
    EarningsAlphaEngine, EarningsAlphaResult, EarningsEvent,
    IVExpansionSignal, SectorCluster,
    analyse_sector_clustering, backtest_post_earnings,
    build_earnings_calendar, detect_iv_expansion,
    generate_crush_entries, generate_sample_events,
)

def _ev(ticker="AAPL", pre_iv=0.35, iv_rank=70, crush=0.30, move=0.03,
        credit=1.0, result=0.80, sector="Technology", **kw):
    return EarningsEvent(
        ticker=ticker, date="2024-01-25", sector=sector,
        pre_iv=pre_iv, pre_iv_rank=iv_rank,
        post_iv=pre_iv * (1 - crush), iv_crush_pct=crush,
        realised_move=move, implied_move=pre_iv * 0.25,
        spread_credit=credit, spread_result=result, **kw)

def _sample(n=100): return generate_sample_events(n)

# --- Calendar ---
class TestCalendar(unittest.TestCase):
    def test_strong_signal(self):
        # Need baseline events so expansion is detectable
        evts = [_ev(iv_rank=60, pre_iv=0.25), _ev(iv_rank=60, pre_iv=0.25),
                _ev(iv_rank=80, pre_iv=0.50)]
        cal = build_earnings_calendar(evts, iv_rank_threshold=50, iv_expansion_threshold=0.10)
        self.assertEqual(cal[2].signal, "strong_sell_vol")

    def test_skip_low_iv(self):
        evts = [_ev(iv_rank=20, pre_iv=0.12)]
        cal = build_earnings_calendar(evts)
        self.assertEqual(cal[0].signal, "skip")

    def test_returns_all(self):
        cal = build_earnings_calendar(_sample(50))
        self.assertEqual(len(cal), 50)

    def test_recommended_width(self):
        evts = [_ev(iv_rank=80, pre_iv=0.50)]
        cal = build_earnings_calendar(evts, iv_expansion_threshold=0.0)
        self.assertEqual(cal[0].recommended_width, 10.0)

# --- IV Expansion ---
class TestIVExpansion(unittest.TestCase):
    def test_detects_elevated(self):
        evts = [_ev(pre_iv=0.15), _ev(pre_iv=0.15), _ev(pre_iv=0.50, iv_rank=80)]
        sigs = detect_iv_expansion(evts, expansion_threshold=0.20)
        elevated = [s for s in sigs if s.is_elevated]
        self.assertGreater(len(elevated), 0)

    def test_not_elevated_low_rank(self):
        evts = [_ev(pre_iv=0.50, iv_rank=30)]
        sigs = detect_iv_expansion(evts)
        self.assertFalse(sigs[0].is_elevated)

    def test_expansion_pct(self):
        sigs = detect_iv_expansion(_sample(50))
        for s in sigs:
            self.assertTrue(-5.0 <= s.expansion_pct <= 5.0)

# --- Crush Entries ---
class TestCrushEntries(unittest.TestCase):
    def test_generates_signals(self):
        entries = generate_crush_entries(_sample(100))
        self.assertGreater(len(entries), 0)

    def test_put_spread_on_up_move(self):
        evts = [_ev(crush=0.40, iv_rank=60, move=0.05)]
        entries = generate_crush_entries(evts, min_crush_pct=0.10)
        self.assertEqual(entries[0].entry_type, "put_credit_spread")

    def test_call_spread_on_down_move(self):
        evts = [_ev(crush=0.40, iv_rank=60, move=-0.05)]
        entries = generate_crush_entries(evts, min_crush_pct=0.10)
        self.assertEqual(entries[0].entry_type, "call_credit_spread")

    def test_skips_low_crush(self):
        evts = [_ev(crush=0.05, iv_rank=60)]
        entries = generate_crush_entries(evts, min_crush_pct=0.15)
        self.assertEqual(len(entries), 0)

    def test_strength_range(self):
        for e in generate_crush_entries(_sample(100)):
            self.assertGreaterEqual(e.signal_strength, 0)
            self.assertLessEqual(e.signal_strength, 1)

# --- Backtest ---
class TestBacktest(unittest.TestCase):
    def test_returns_result(self):
        bt = backtest_post_earnings(_sample(100))
        self.assertIsInstance(bt, BacktestResult)
        self.assertGreater(bt.n_trades, 0)

    def test_win_rate_range(self):
        bt = backtest_post_earnings(_sample(200))
        self.assertGreaterEqual(bt.win_rate, 0)
        self.assertLessEqual(bt.win_rate, 1)

    def test_profit_factor_positive(self):
        bt = backtest_post_earnings(_sample(200))
        self.assertGreater(bt.profit_factor, 0)

    def test_sharpe_finite(self):
        bt = backtest_post_earnings(_sample(200))
        self.assertTrue(isinstance(bt.sharpe, float))

    def test_filters_low_iv(self):
        evts = [_ev(iv_rank=20, crush=0.05, credit=0.5, result=0.3)]
        bt = backtest_post_earnings(evts, min_iv_rank=50)
        self.assertEqual(bt.n_trades, 0)

    def test_max_dd_non_negative(self):
        bt = backtest_post_earnings(_sample(100))
        self.assertGreaterEqual(bt.max_dd, 0)

    def test_empty(self):
        bt = backtest_post_earnings([])
        self.assertEqual(bt.n_trades, 0)

# --- Sector Clustering ---
class TestClustering(unittest.TestCase):
    def test_returns_analysis(self):
        ca = analyse_sector_clustering(_sample(100))
        self.assertIsInstance(ca, ClusterAnalysis)
        self.assertGreater(len(ca.sectors), 0)

    def test_all_sectors_covered(self):
        evts = _sample(100)
        sectors = set(e.sector for e in evts)
        ca = analyse_sector_clustering(evts)
        result_sectors = set(s.sector for s in ca.sectors)
        self.assertEqual(sectors, result_sectors)

    def test_best_sector(self):
        ca = analyse_sector_clustering(_sample(200))
        self.assertIn(ca.best_sector, ["Technology", "Consumer", "Financials",
                                        "Healthcare", "Energy"])

    def test_win_rate_range(self):
        ca = analyse_sector_clustering(_sample(100))
        for s in ca.sectors:
            self.assertGreaterEqual(s.win_rate, 0)
            self.assertLessEqual(s.win_rate, 1)

# --- Synthetic Data ---
class TestSyntheticData(unittest.TestCase):
    def test_correct_count(self):
        self.assertEqual(len(generate_sample_events(50)), 50)

    def test_deterministic(self):
        a = generate_sample_events(20, seed=42)
        b = generate_sample_events(20, seed=42)
        self.assertEqual(a[0].pre_iv, b[0].pre_iv)

    def test_has_variety(self):
        evts = generate_sample_events(100)
        tickers = set(e.ticker for e in evts)
        self.assertGreater(len(tickers), 5)

    def test_iv_positive(self):
        for e in generate_sample_events(100):
            self.assertGreater(e.pre_iv, 0)

# --- Full Engine ---
class TestEarningsAlphaEngine(unittest.TestCase):
    def test_analyse(self):
        r = EarningsAlphaEngine(_sample(100)).analyse()
        self.assertIsInstance(r, EarningsAlphaResult)
        self.assertEqual(r.n_events, 100)

    def test_actionable_signals(self):
        r = EarningsAlphaEngine(_sample(200)).analyse()
        self.assertGreater(r.n_actionable_signals, 0)

    def test_avg_crush_positive(self):
        r = EarningsAlphaEngine(_sample(100)).analyse()
        self.assertGreater(r.avg_iv_crush, 0)

    def test_low_correlation(self):
        r = EarningsAlphaEngine(_sample(100)).analyse()
        self.assertLess(r.correlation_to_spy, 0.3)

    def test_backtest_populated(self):
        r = EarningsAlphaEngine(_sample(200)).analyse()
        self.assertGreater(r.backtest.n_trades, 0)

    def test_clusters_populated(self):
        r = EarningsAlphaEngine(_sample(200)).analyse()
        self.assertGreater(len(r.clusters.sectors), 0)

if __name__ == "__main__":
    unittest.main()
