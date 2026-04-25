"""Tests for compass/dynamic_hedging.py — dynamic hedging engine."""
from __future__ import annotations
import numpy as np
import pytest
from compass.dynamic_hedging import (
    BacktestResult, CrossHedge, DynamicHedgingEngine, HedgeAction,
    HedgeConfig, HedgePlan, PortfolioState, TailHedge,
    compute_var, kelly_rebalance_trigger, optimal_hedge_ratio,
    price_otm_put,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _engine(**kw):
    return DynamicHedgingEngine(HedgeConfig(**kw))

def _returns(n=500, seed=42, mu=0.0003, sigma=0.01):
    return np.random.RandomState(seed).normal(mu, sigma, n)

def _regimes(n=500):
    r = np.array(["neutral"] * n, dtype=object)
    r[:n//4] = "bull"; r[n//4:n//2] = "neutral"
    r[n//2:3*n//4] = "bear"; r[3*n//4:] = "high_vol"
    return r

# ── Core computations ────────────────────────────────────────────────────

class TestVaR:
    def test_positive_var(self):
        rets = _returns(500, sigma=0.02)
        var = compute_var(rets, 0.05)
        assert var > 0
    def test_higher_confidence_higher_var(self):
        rets = _returns(500, sigma=0.02)
        v5 = compute_var(rets, 0.05)
        v1 = compute_var(rets, 0.01)
        assert v1 >= v5
    def test_short_data(self):
        assert compute_var(np.array([0.01, -0.01]), 0.05) == 0.0

class TestOptimalHedgeRatio:
    def test_perfect_correlation(self):
        rng = np.random.RandomState(42)
        p = rng.normal(0, 0.01, 200)
        h = p * 1.5 + rng.normal(0, 0.001, 200)
        ratio = optimal_hedge_ratio(p, h)
        assert 0.5 < ratio < 1.0  # should be ~0.67
    def test_zero_correlation(self):
        rng = np.random.RandomState(42)
        p = rng.normal(0, 0.01, 200)
        h = rng.normal(0, 0.01, 200)
        ratio = optimal_hedge_ratio(p, h)
        assert abs(ratio) < 0.5
    def test_short_data_returns_one(self):
        assert optimal_hedge_ratio(np.array([0.01]), np.array([0.01])) == 1.0

class TestKellyTrigger:
    def test_triggers_on_large_gamma(self):
        assert kelly_rebalance_trigger(5, 10, 2.0, 5.0) is True
    def test_no_trigger_small_delta(self):
        assert kelly_rebalance_trigger(1, 20, 0.1, 1.0) is False
    def test_threshold_exact(self):
        # delta=9, gamma drift pushes to 10.5 > threshold 10
        assert kelly_rebalance_trigger(9, 10, 1.0, 1.0, 1.5) is True

class TestPutPricing:
    def test_otm_put_positive(self):
        p = price_otm_put(430, 410, 30, 0.20)
        assert p > 0
    def test_deeper_otm_cheaper(self):
        p1 = price_otm_put(430, 420, 30, 0.20)
        p2 = price_otm_put(430, 400, 30, 0.20)
        assert p1 > p2
    def test_higher_vol_more_expensive(self):
        p_low = price_otm_put(430, 410, 30, 0.10)
        p_high = price_otm_put(430, 410, 30, 0.40)
        assert p_high > p_low
    def test_expired_intrinsic(self):
        p = price_otm_put(430, 440, 0, 0.20)
        assert p == pytest.approx(10.0)
    def test_deep_otm_near_zero(self):
        p = price_otm_put(430, 300, 7, 0.15)
        assert p < 0.1

# ── Portfolio state ──────────────────────────────────────────────────────

class TestPortfolioState:
    def test_add_snapshot(self):
        e = _engine()
        s = e.add_portfolio_snapshot(delta=30, gamma=5, vega=200)
        assert isinstance(s, PortfolioState)
        assert s.delta == 30
    def test_state_updated(self):
        e = _engine()
        e.add_portfolio_snapshot(delta=10)
        e.add_portfolio_snapshot(delta=25)
        assert e._state.delta == 25

# ── Hedge computation ────────────────────────────────────────────────────

class TestComputeHedges:
    def test_returns_plan(self):
        e = _engine()
        e.add_portfolio_snapshot(delta=30, gamma=5)
        plan = e.compute_hedges(regime="neutral", vix=18)
        assert isinstance(plan, HedgePlan)
    def test_delta_hedge_generated(self):
        e = _engine(delta_threshold=5)
        e.add_portfolio_snapshot(delta=30)
        plan = e.compute_hedges()
        delta_actions = [a for a in plan.actions if a.action_type == "delta_hedge"]
        assert len(delta_actions) >= 1
    def test_no_hedge_small_delta(self):
        e = _engine(delta_threshold=50)
        e.add_portfolio_snapshot(delta=5)
        plan = e.compute_hedges()
        delta_actions = [a for a in plan.actions if a.action_type == "delta_hedge"]
        assert len(delta_actions) == 0
    def test_tail_hedge_with_returns(self):
        e = _engine()
        e.add_portfolio_snapshot(delta=5, portfolio_value=100_000)
        rets = _returns(200, sigma=0.02)
        plan = e.compute_hedges(portfolio_returns=rets)
        tail_actions = [a for a in plan.actions if a.action_type == "tail_put"]
        assert len(tail_actions) >= 1
    def test_vix_overlay_on_backwardation(self):
        e = _engine(vix_call_trigger=1.0)
        e.add_portfolio_snapshot(delta=5)
        plan = e.compute_hedges(vix=25, vix_term_ratio=0.90)
        vix_actions = [a for a in plan.actions if a.action_type == "vix_call"]
        assert len(vix_actions) >= 1
    def test_no_vix_overlay_in_contango(self):
        e = _engine()
        e.add_portfolio_snapshot(delta=5)
        plan = e.compute_hedges(vix=18, vix_term_ratio=1.10)
        vix_actions = [a for a in plan.actions if a.action_type == "vix_call"]
        assert len(vix_actions) == 0
    def test_cross_hedge_on_large_delta(self):
        e = _engine(delta_threshold=5)
        e.add_portfolio_snapshot(delta=50)
        plan = e.compute_hedges()
        cross = [a for a in plan.actions if a.action_type == "cross_hedge"]
        assert len(cross) >= 1
    def test_regime_hedge_ratio(self):
        e = _engine()
        e.add_portfolio_snapshot(delta=20)
        bull = e.compute_hedges(regime="bull")
        e.add_portfolio_snapshot(delta=20)
        crash = e.compute_hedges(regime="crash")
        assert bull.hedge_ratio < crash.hedge_ratio
    def test_residual_delta_reduced(self):
        e = _engine(delta_threshold=5)
        e.add_portfolio_snapshot(delta=40)
        plan = e.compute_hedges()
        assert abs(plan.residual_delta) < abs(40)  # should be reduced
    def test_cost_positive(self):
        e = _engine(delta_threshold=5)
        e.add_portfolio_snapshot(delta=30)
        plan = e.compute_hedges()
        assert plan.total_cost >= 0

# ── Rebalance trigger ────────────────────────────────────────────────────

class TestRebalanceTrigger:
    def test_triggers_above_threshold(self):
        e = _engine(delta_threshold=10)
        trigger, reason = e.should_rebalance(15, 1.0)
        assert trigger is True
    def test_no_trigger_below(self):
        e = _engine(delta_threshold=20)
        trigger, _ = e.should_rebalance(5, 0.5)
        assert trigger is False
    def test_kelly_anticipation(self):
        e = _engine(delta_threshold=10, kelly_rebalance_mult=2.0)
        # delta=8, but gamma=3 with 5pt move → drift = 15 → triggers
        trigger, _ = e.should_rebalance(8, 3.0, expected_daily_move=0.012)
        assert trigger is True

# ── Backtest ─────────────────────────────────────────────────────────────

class TestBacktest:
    def test_returns_result(self):
        e = _engine()
        bt = e.backtest(_returns(300), _regimes(300))
        assert isinstance(bt, BacktestResult)
    def test_dd_reduced(self):
        rets = _returns(500, sigma=0.015)
        # Inject a crash
        rets[200:210] = -0.03
        e = _engine()
        bt = e.backtest(rets, _regimes(500))
        assert abs(bt.hedged_dd) <= abs(bt.unhedged_dd)
    def test_hedge_cost_positive(self):
        e = _engine()
        bt = e.backtest(_returns(300), _regimes(300))
        assert bt.total_hedge_cost > 0
    def test_cost_annual_pct_reasonable(self):
        e = _engine()
        bt = e.backtest(_returns(500), _regimes(500))
        assert bt.hedge_cost_annual_pct < 0.10  # < 10% annual
    def test_by_regime_populated(self):
        e = _engine()
        bt = e.backtest(_returns(500), _regimes(500))
        assert len(bt.by_regime) > 0
    def test_bear_regime_more_hedging(self):
        e = _engine()
        bt = e.backtest(_returns(500), _regimes(500))
        if "bear" in bt.by_regime and "bull" in bt.by_regime:
            assert bt.by_regime["bear"]["hedge_ratio"] > bt.by_regime["bull"]["hedge_ratio"]
    def test_short_data(self):
        e = _engine()
        bt = e.backtest(_returns(10), _regimes(10))
        assert bt.n_periods == 0
    def test_rebalance_count(self):
        e = _engine(delta_threshold=5)
        bt = e.backtest(_returns(500), _regimes(500))
        assert bt.n_rebalances > 0

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_zero_delta(self):
        e = _engine()
        e.add_portfolio_snapshot(delta=0)
        plan = e.compute_hedges()
        assert plan.rebalance_triggered is False
    def test_extreme_delta(self):
        e = _engine(max_hedge_shares=100)
        e.add_portfolio_snapshot(delta=10000)
        plan = e.compute_hedges()
        delta_actions = [a for a in plan.actions if a.action_type == "delta_hedge"]
        if delta_actions:
            assert delta_actions[0].quantity <= 100
    def test_no_state_compute(self):
        e = _engine()
        plan = e.compute_hedges()
        assert plan is not None  # auto-creates default state
    def test_negative_delta(self):
        e = _engine(delta_threshold=5)
        e.add_portfolio_snapshot(delta=-30)
        plan = e.compute_hedges()
        delta_actions = [a for a in plan.actions if a.action_type == "delta_hedge"]
        if delta_actions:
            assert delta_actions[0].direction == "buy"
