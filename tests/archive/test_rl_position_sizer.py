"""Tests for compass.rl_position_sizer."""
import unittest
from compass.rl_position_sizer import (
    ACTIONS, N_ACTIONS, EnvState, QLearningAgent, RLSizerResult, SizerResult,
    TradeStep, TrainResult, backtest_sizer, compare_all_sizers,
    compute_reward, discretise_state, fixed_size, generate_episodes,
    half_kelly_size, kelly_size, regime_size, run_full_analysis, train_agent,
)

def _step(pnl=0.01, sig=0.7, vol=1, heat=0.3):
    return TradeStep("d0", pnl, sig, vol, heat)

def _episodes(n=20, steps=30): return generate_episodes(n, steps)

# --- State discretisation ---
class TestDiscretise(unittest.TestCase):
    def test_zero_dd(self):
        s = discretise_state(0.0, 1, 0.5, 0.3)
        self.assertEqual(s.dd_bucket, 0)
    def test_high_dd(self):
        s = discretise_state(0.15, 1, 0.5, 0.3)
        self.assertEqual(s.dd_bucket, 4)
    def test_signal_quartiles(self):
        self.assertEqual(discretise_state(0, 0, 0.1, 0).signal_bucket, 0)
        self.assertEqual(discretise_state(0, 0, 0.9, 0).signal_bucket, 3)
    def test_vol_passthrough(self):
        self.assertEqual(discretise_state(0, 2, 0.5, 0).vol_bucket, 2)
    def test_key_is_tuple(self):
        s = discretise_state(0.05, 1, 0.6, 0.4)
        self.assertIsInstance(s.key(), tuple)
        self.assertEqual(len(s.key()), 4)

# --- Reward ---
class TestReward(unittest.TestCase):
    def test_positive_pnl(self):
        r = compute_reward(0.02, 0.5, 0.0)
        self.assertGreater(r, 0)
    def test_dd_penalty(self):
        r_low = compute_reward(0.01, 0.5, 0.02)
        r_high = compute_reward(0.01, 0.5, 0.10)
        self.assertGreater(r_low, r_high)
    def test_zero_size_zero_reward(self):
        r = compute_reward(0.05, 0.0, 0.0)
        self.assertEqual(r, 0.0)

# --- Q-Learning Agent ---
class TestQLearning(unittest.TestCase):
    def test_select_action_range(self):
        agent = QLearningAgent()
        state = discretise_state(0, 1, 0.5, 0.3)
        a = agent.select_action(state)
        self.assertGreaterEqual(a, 0)
        self.assertLess(a, N_ACTIONS)
    def test_update_changes_q(self):
        agent = QLearningAgent()
        s = discretise_state(0, 1, 0.5, 0.3)
        s2 = discretise_state(0, 1, 0.6, 0.4)
        q_before = list(agent._get_q(s.key()))
        agent.update(s, 5, 0.1, s2)
        q_after = agent._get_q(s.key())
        self.assertNotEqual(q_before[5], q_after[5])
    def test_get_size_range(self):
        agent = QLearningAgent()
        state = discretise_state(0, 1, 0.5, 0.3)
        size = agent.get_size(state)
        self.assertIn(size, ACTIONS)
    def test_training_flag(self):
        agent = QLearningAgent()
        agent.set_training(False)
        self.assertFalse(agent._training)
    def test_exploitation_mode(self):
        agent = QLearningAgent(epsilon=0.0)
        # Set one Q value high
        s = discretise_state(0, 1, 0.5, 0.3)
        agent.q_table[s.key()] = [0.0] * N_ACTIONS
        agent.q_table[s.key()][7] = 10.0
        a = agent.select_action(s)
        self.assertEqual(a, 7)

# --- Training ---
class TestTraining(unittest.TestCase):
    def test_returns_result(self):
        eps = _episodes(10, 20)
        agent = QLearningAgent(seed=42)
        tr = train_agent(agent, eps, n_epochs=2)
        self.assertIsInstance(tr, TrainResult)
        self.assertGreater(tr.n_steps, 0)
    def test_states_visited(self):
        eps = _episodes(10, 20)
        agent = QLearningAgent(seed=42)
        train_agent(agent, eps, n_epochs=3)
        self.assertGreater(agent.n_states_visited, 0)
    def test_epsilon_decays(self):
        eps = _episodes(5, 10)
        agent = QLearningAgent(seed=42)
        tr = train_agent(agent, eps, n_epochs=5)
        self.assertLess(tr.final_epsilon, 0.15)
    def test_reward_improves(self):
        eps = _episodes(20, 40)
        agent = QLearningAgent(seed=42)
        tr = train_agent(agent, eps, n_epochs=10)
        # Last quarter should have >= first quarter rewards (learned something)
        self.assertGreaterEqual(tr.avg_reward_last_quarter, tr.avg_reward_first_quarter - 0.01)

# --- Baseline sizers ---
class TestBaselines(unittest.TestCase):
    def test_kelly_positive_edge(self):
        k = kelly_size(0.65, 0.02, 0.015)
        self.assertGreater(k, 0)
        self.assertLessEqual(k, 1)
    def test_kelly_no_edge(self):
        self.assertEqual(kelly_size(0.40, 0.01, 0.02), 0.0)
    def test_half_kelly(self):
        k = kelly_size(0.65, 0.02, 0.015)
        hk = half_kelly_size(0.65, 0.02, 0.015)
        self.assertAlmostEqual(hk, k * 0.5)
    def test_fixed(self):
        self.assertEqual(fixed_size(0.10), 0.10)
    def test_regime_low_vol(self):
        self.assertGreater(regime_size(0, 0.8), regime_size(2, 0.8))
    def test_regime_high_signal(self):
        self.assertGreater(regime_size(1, 0.9), regime_size(1, 0.1))

# --- Backtest ---
class TestBacktest(unittest.TestCase):
    def test_returns_result(self):
        eps = _episodes(10, 20)
        r = backtest_sizer("test", eps, lambda s, dd, eq: 0.10)
        self.assertIsInstance(r, SizerResult)
        self.assertGreater(r.n_trades, 0)
    def test_dd_non_negative(self):
        r = backtest_sizer("test", _episodes(5, 20), lambda s, dd, eq: 0.10)
        self.assertGreaterEqual(r.max_dd, 0)
    def test_zero_size_zero_return(self):
        r = backtest_sizer("zero", _episodes(5, 10), lambda s, dd, eq: 0.0)
        self.assertAlmostEqual(r.total_return, 0.0, places=5)

# --- Comparison ---
class TestComparison(unittest.TestCase):
    def test_returns_all_sizers(self):
        eps = _episodes(20, 30)
        agent = QLearningAgent(seed=42)
        train_agent(agent, eps[:12], n_epochs=3)
        results = compare_all_sizers(eps[:12], eps[12:], agent)
        self.assertEqual(len(results), 5)
        names = {r.name for r in results}
        self.assertEqual(names, {"RL Q-Learning", "Kelly", "Half-Kelly", "Fixed 10%", "Regime-Based"})
    def test_sorted_by_sharpe(self):
        eps = _episodes(20, 30)
        agent = QLearningAgent(seed=42)
        train_agent(agent, eps[:12], n_epochs=3)
        results = compare_all_sizers(eps[:12], eps[12:], agent)
        sharpes = [r.sharpe for r in results]
        self.assertEqual(sharpes, sorted(sharpes, reverse=True))

# --- Full analysis ---
class TestFullAnalysis(unittest.TestCase):
    def test_returns_result(self):
        eps = _episodes(30, 40)
        r = run_full_analysis(eps, n_epochs=3)
        self.assertIsInstance(r, RLSizerResult)
    def test_has_comparison(self):
        r = run_full_analysis(_episodes(20, 30), n_epochs=2)
        self.assertEqual(len(r.comparison), 5)
    def test_rl_rank(self):
        r = run_full_analysis(_episodes(20, 30), n_epochs=3)
        self.assertGreaterEqual(r.rl_rank, 1)
        self.assertLessEqual(r.rl_rank, 5)
    def test_best_sizer_exists(self):
        r = run_full_analysis(_episodes(20, 30), n_epochs=2)
        names = {c.name for c in r.comparison}
        self.assertIn(r.best_sizer, names)

# --- Synthetic data ---
class TestSyntheticData(unittest.TestCase):
    def test_correct_count(self):
        eps = generate_episodes(10, 20)
        self.assertEqual(len(eps), 10)
        self.assertEqual(len(eps[0]), 20)
    def test_deterministic(self):
        a = generate_episodes(5, 10, seed=42)
        b = generate_episodes(5, 10, seed=42)
        self.assertEqual(a[0][0].trade_pnl_pct, b[0][0].trade_pnl_pct)
    def test_signal_range(self):
        for ep in generate_episodes(5, 20):
            for step in ep:
                self.assertGreaterEqual(step.signal_strength, 0)
                self.assertLessEqual(step.signal_strength, 1)
    def test_vol_regime_range(self):
        for ep in generate_episodes(5, 20):
            for step in ep:
                self.assertIn(step.vol_regime, [0, 1, 2])

if __name__ == "__main__":
    unittest.main()
