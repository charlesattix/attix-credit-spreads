"""Tests for compass/rl_portfolio_manager.py — RL portfolio manager."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.rl_portfolio_manager import (
    BacktestResult, LinearPolicy, PortfolioEnv, RLConfig,
    RLPortfolioManager, TrainResult, _softmax,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _returns(n=300, strategies=5, seed=42):
    rng = np.random.RandomState(seed)
    names = [f"strat_{i}" for i in range(strategies)]
    dates = pd.bdate_range("2022-01-03", periods=n)
    data = rng.normal(0.0003, 0.01, (n, strategies))
    return pd.DataFrame(data, index=dates, columns=names)

def _mgr(n=300, strategies=5, seed=42, **kw):
    defaults = dict(n_episodes=10, episode_length=30, seed=seed)
    defaults.update(kw)
    ret = _returns(n, strategies, seed)
    regimes = np.array(["bull"] * (n // 3) + ["neutral"] * (n // 3) + ["bear"] * (n - 2 * (n // 3)))
    vix = np.random.RandomState(seed).uniform(12, 30, n)
    return RLPortfolioManager(ret, RLConfig(**defaults), regimes, vix)

# ── Softmax ──────────────────────────────────────────────────────────────

class TestSoftmax:
    def test_sums_to_one(self):
        p = _softmax(np.array([1.0, 2.0, 3.0]))
        assert p.sum() == pytest.approx(1.0)
    def test_positive(self):
        p = _softmax(np.array([-1, 0, 1]))
        assert np.all(p > 0)
    def test_largest_highest(self):
        p = _softmax(np.array([1, 2, 10]))
        assert p[2] > p[0]

# ── Linear policy ────────────────────────────────────────────────────────

class TestLinearPolicy:
    def test_forward_returns_probs_value(self):
        pol = LinearPolicy(10, 5)
        state = np.random.randn(10)
        probs, val = pol.forward(state)
        assert probs.shape == (5,)
        assert probs.sum() == pytest.approx(1.0)
        assert isinstance(val, float)
    def test_sample_returns_weights(self):
        pol = LinearPolicy(10, 5)
        rng = np.random.RandomState(42)
        w, lp, v = pol.sample_action(np.random.randn(10), rng)
        assert w.shape == (5,)
        assert w.sum() == pytest.approx(1.0, abs=0.01)
        assert isinstance(lp, float)
    def test_update_changes_weights(self):
        pol = LinearPolicy(10, 5, seed=42)
        w_before = pol.W_policy.copy()
        states = np.random.randn(10, 10)
        actions = np.random.dirichlet(np.ones(5), 10)
        advantages = np.random.randn(10)
        old_lps = np.random.randn(10)
        returns = np.random.randn(10)
        pol.update(states, actions, advantages, old_lps, returns, 0.01, 0.01, 0.2, 0.01)
        assert not np.allclose(pol.W_policy, w_before)

# ── Environment ──────────────────────────────────────────────────────────

class TestEnvironment:
    def test_reset_returns_state(self):
        ret = np.random.randn(100, 3) * 0.01
        env = PortfolioEnv(ret, np.full(100, "bull"), np.full(100, 18), 30)
        state = env.reset(np.random.RandomState(42))
        assert state.shape == (10,)
    def test_step_returns_tuple(self):
        ret = np.random.randn(100, 3) * 0.01
        env = PortfolioEnv(ret, np.full(100, "bull"), np.full(100, 18), 30)
        env.reset(np.random.RandomState(42))
        w = np.array([0.4, 0.3, 0.3])
        ns, reward, done = env.step(w)
        assert ns.shape == (10,)
        assert isinstance(reward, float)
        assert isinstance(done, bool)
    def test_episode_ends(self):
        ret = np.random.randn(100, 3) * 0.01
        env = PortfolioEnv(ret, np.full(100, "bull"), np.full(100, 18), 10)
        env.reset(np.random.RandomState(42))
        for _ in range(20):
            _, _, done = env.step(np.ones(3) / 3)
            if done:
                break
        assert done is True
    def test_dd_penalty_in_reward(self):
        """Negative returns should produce negative reward via DD penalty."""
        ret = np.full((50, 2), -0.05)  # big losses
        env = PortfolioEnv(ret, np.full(50, "bear"), np.full(50, 30), 10, dd_penalty=5.0)
        env.reset(np.random.RandomState(42))
        _, reward, _ = env.step(np.array([0.5, 0.5]))
        assert reward < 0

# ── Training ─────────────────────────────────────────────────────────────

class TestTraining:
    def test_train_returns_result(self):
        mgr = _mgr(100, 3, n_episodes=5, episode_length=20)
        r = mgr.train()
        assert isinstance(r, TrainResult)
    def test_reward_history(self):
        mgr = _mgr(100, 3, n_episodes=10, episode_length=20)
        r = mgr.train()
        assert len(r.reward_history) == 10
    def test_loss_histories(self):
        mgr = _mgr(100, 3, n_episodes=5, episode_length=20)
        r = mgr.train()
        assert len(r.policy_loss_history) > 0
        assert len(r.value_loss_history) > 0
    def test_best_reward_tracked(self):
        mgr = _mgr(100, 3, n_episodes=5, episode_length=20)
        r = mgr.train()
        assert r.best_episode_reward >= max(r.reward_history)
    def test_learning_improves(self):
        """Later episodes should generally have higher reward (on average)."""
        mgr = _mgr(200, 3, n_episodes=30, episode_length=20)
        r = mgr.train()
        early = np.mean(r.reward_history[:10])
        late = np.mean(r.reward_history[-10:])
        # Not guaranteed but agent shouldn't collapse
        assert late > early - 1.0  # allow some variance

# ── Backtest ─────────────────────────────────────────────────────────────

class TestBacktest:
    def test_returns_result(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        bt = mgr.backtest()
        assert isinstance(bt, BacktestResult)
    def test_auto_trains(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        assert mgr.train_result is None
        mgr.backtest()
        assert mgr.train_result is not None
    def test_has_allocations(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        bt = mgr.backtest()
        assert len(bt.allocations) > 0
    def test_allocation_weights_sum(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        bt = mgr.backtest()
        for a in bt.allocations:
            assert a.weights.sum() == pytest.approx(1.0, abs=0.01)
    def test_baselines_computed(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        bt = mgr.backtest()
        assert isinstance(bt.ew_sharpe, float)
        assert isinstance(bt.rp_sharpe, float)
        assert isinstance(bt.iv_sharpe, float)
    def test_sharpe_improvement_computed(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        bt = mgr.backtest()
        assert isinstance(bt.sharpe_improvement_vs_ew, float)
    def test_oos_fraction(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        bt = mgr.backtest()
        assert bt.oos_fraction == pytest.approx(0.25)
    def test_dd_finite(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        bt = mgr.backtest()
        assert np.isfinite(bt.rl_dd)
        assert np.isfinite(bt.ew_dd)
    def test_win_rate_range(self):
        mgr = _mgr(200, 4, n_episodes=10, episode_length=30)
        bt = mgr.backtest()
        assert 0 <= bt.rl_win_rate <= 1

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_strategy(self):
        mgr = _mgr(100, 1, n_episodes=5, episode_length=20)
        bt = mgr.backtest()
        assert isinstance(bt, BacktestResult)
    def test_short_data(self):
        mgr = _mgr(30, 3, n_episodes=3, episode_length=10)
        bt = mgr.backtest()
        assert isinstance(bt, BacktestResult)
    def test_no_regimes(self):
        ret = _returns(100, 3)
        mgr = RLPortfolioManager(ret, RLConfig(n_episodes=5, episode_length=20))
        bt = mgr.backtest()
        assert isinstance(bt, BacktestResult)
    def test_many_strategies(self):
        mgr = _mgr(100, 10, n_episodes=5, episode_length=20)
        r = mgr.train()
        assert isinstance(r, TrainResult)
