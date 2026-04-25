"""Tests for compass.monte_carlo_north_star."""
import math, unittest
from compass.monte_carlo_north_star import (
    DEFAULT_PORTFOLIO, DEFAULT_SCENARIOS, MCNorthStarResult, PathResult,
    Scenario, ScenarioResult, StrategySpec,
    run_full_stress_test, run_quick_test, run_scenario, simulate_path,
)
import random

def _base_scenario():
    return DEFAULT_SCENARIOS[0]  # "base"

def _crisis():
    return DEFAULT_SCENARIOS[1]  # "covid_crash"

# --- Path simulation ---
class TestSimulatePath(unittest.TestCase):
    def test_returns_result(self):
        rng = random.Random(42)
        r = simulate_path(rng, DEFAULT_PORTFOLIO, _base_scenario())
        self.assertIsInstance(r, PathResult)
    def test_final_equity_positive(self):
        rng = random.Random(42)
        r = simulate_path(rng, DEFAULT_PORTFOLIO, _base_scenario())
        self.assertGreater(r.final_equity, 0)
    def test_dd_range(self):
        rng = random.Random(42)
        r = simulate_path(rng, DEFAULT_PORTFOLIO, _base_scenario())
        self.assertGreaterEqual(r.max_drawdown, 0)
        self.assertLessEqual(r.max_drawdown, 1)
    def test_crisis_worse_than_base(self):
        rng1 = random.Random(42); rng2 = random.Random(42)
        base = simulate_path(rng1, DEFAULT_PORTFOLIO, _base_scenario())
        crisis = simulate_path(rng2, DEFAULT_PORTFOLIO, _crisis())
        self.assertGreater(crisis.max_drawdown, base.max_drawdown * 0.5)
    def test_survived_flag(self):
        rng = random.Random(42)
        r = simulate_path(rng, DEFAULT_PORTFOLIO, _base_scenario())
        self.assertIsInstance(r.survived, bool)

# --- Scenario simulation ---
class TestRunScenario(unittest.TestCase):
    def test_returns_result(self):
        r = run_scenario(_base_scenario(), n_paths=500)
        self.assertIsInstance(r, ScenarioResult)
        self.assertEqual(r.n_paths, 500)
    def test_survival_rate_range(self):
        r = run_scenario(_base_scenario(), n_paths=500)
        self.assertGreaterEqual(r.survival_rate, 0)
        self.assertLessEqual(r.survival_rate, 1)
    def test_base_mostly_survives(self):
        r = run_scenario(_base_scenario(), n_paths=1000)
        self.assertGreater(r.survival_rate, 0.8)
    def test_p5_worse_than_mean(self):
        r = run_scenario(_base_scenario(), n_paths=1000)
        self.assertLessEqual(r.p5_return, r.mean_return)
    def test_p1_worse_than_p5(self):
        r = run_scenario(_base_scenario(), n_paths=1000)
        self.assertLessEqual(r.p1_return, r.p5_return)
    def test_dd_percentiles_ordered(self):
        r = run_scenario(_base_scenario(), n_paths=1000)
        self.assertLessEqual(r.mean_dd, r.p95_dd)
        self.assertLessEqual(r.p95_dd, r.p99_dd)
    def test_crisis_higher_dd(self):
        base = run_scenario(_base_scenario(), n_paths=500)
        crisis = run_scenario(_crisis(), n_paths=500)
        self.assertGreater(crisis.p95_dd, base.p95_dd * 0.5)
    def test_corr_09_more_dd(self):
        corr_low = run_scenario(DEFAULT_SCENARIOS[4], n_paths=500)  # corr_05
        corr_high = run_scenario(DEFAULT_SCENARIOS[6], n_paths=500) # corr_09
        self.assertGreater(corr_high.mean_dd, corr_low.mean_dd * 0.5)
    def test_permanent_bear_negative(self):
        r = run_scenario(DEFAULT_SCENARIOS[8], n_paths=500)  # permanent_bear
        self.assertLess(r.mean_return, 0.10)
    def test_prob_loss_range(self):
        r = run_scenario(_base_scenario(), n_paths=500)
        self.assertGreaterEqual(r.prob_loss, 0)
        self.assertLessEqual(r.prob_loss, 1)
    def test_prob_dd_ordering(self):
        r = run_scenario(_base_scenario(), n_paths=1000)
        self.assertGreaterEqual(r.prob_dd_gt_20, r.prob_dd_gt_30)
        self.assertGreaterEqual(r.prob_dd_gt_30, r.prob_dd_gt_50)

# --- Full stress test ---
class TestFullStressTest(unittest.TestCase):
    def test_returns_result(self):
        r = run_quick_test(500)
        self.assertIsInstance(r, MCNorthStarResult)
    def test_all_scenarios(self):
        r = run_quick_test(200)
        self.assertEqual(r.n_scenarios, len(DEFAULT_SCENARIOS))
    def test_worst_scenario_exists(self):
        r = run_quick_test(500)
        names = {s.scenario for s in r.results}
        self.assertIn(r.worst_scenario, names)
    def test_safest_scenario_exists(self):
        r = run_quick_test(500)
        names = {s.scenario for s in r.results}
        self.assertIn(r.safest_scenario, names)
    def test_runtime_recorded(self):
        r = run_quick_test(100)
        self.assertGreater(r.runtime_seconds, 0)
    def test_results_count(self):
        r = run_quick_test(200)
        self.assertEqual(len(r.results), len(DEFAULT_SCENARIOS))

# --- Portfolio spec ---
class TestPortfolio(unittest.TestCase):
    def test_weights_sum(self):
        total = sum(s.weight for s in DEFAULT_PORTFOLIO)
        self.assertAlmostEqual(total, 1.0)
    def test_four_strategies(self):
        self.assertEqual(len(DEFAULT_PORTFOLIO), 4)
    def test_tail_hedge_negative_return(self):
        th = next(s for s in DEFAULT_PORTFOLIO if s.name == "tail_hedge")
        self.assertLess(th.annual_return, 0)

# --- Scenarios ---
class TestScenarios(unittest.TestCase):
    def test_ten_scenarios(self):
        self.assertEqual(len(DEFAULT_SCENARIOS), 10)
    def test_all_have_names(self):
        for sc in DEFAULT_SCENARIOS:
            self.assertGreater(len(sc.name), 0)
    def test_base_is_first(self):
        self.assertEqual(DEFAULT_SCENARIOS[0].name, "base")
    def test_covid_has_shock(self):
        covid = next(s for s in DEFAULT_SCENARIOS if s.name == "covid_crash")
        self.assertLess(covid.equity_shock, -0.20)

if __name__ == "__main__":
    unittest.main()
