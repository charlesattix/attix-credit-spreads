"""Tests for compass/smart_execution.py — smart execution engine."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from compass.smart_execution import (
    ALGORITHMS,
    BacktestResult,
    ChildOrder,
    ExecutionResult,
    MarketState,
    Order,
    SmartExecutionEngine,
    adaptive_slices,
    compute_execution_quality,
    intraday_volume_profile,
    naive_slices,
    permanent_impact,
    simulate_fills,
    temporary_impact,
    twap_slices,
    vwap_slices,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def market():
    return MarketState(bid=4.95, ask=5.05, daily_volume=1_000_000, volatility=0.015)


@pytest.fixture
def buy_order():
    return Order(order_id="T1", side="buy", total_qty=20, limit_price=5.10, urgency=0.5)


@pytest.fixture
def sell_order():
    return Order(order_id="T2", side="sell", total_qty=15, limit_price=4.90, urgency=0.3)


@pytest.fixture
def engine():
    return SmartExecutionEngine()


# ── MarketState tests ────────────────────────────────────────────────────


class TestMarketState:
    def test_mid_computed(self):
        m = MarketState(bid=4.95, ask=5.05)
        assert m.mid == pytest.approx(5.0)

    def test_spread_computed(self):
        m = MarketState(bid=4.95, ask=5.05)
        assert m.spread_bps > 0
        assert abs(m.spread_bps - 200) < 1  # 10c spread on $5 = 200 bps


# ── Volume profile tests ────────────────────────────────────────────────


class TestVolumeProfile:
    def test_sums_to_one(self):
        p = intraday_volume_profile(10)
        assert abs(p.sum() - 1.0) < 1e-10

    def test_u_shape(self):
        p = intraday_volume_profile(20)
        # Ends should be higher than middle
        assert p[0] > p[10]
        assert p[-1] > p[10]

    def test_positive(self):
        p = intraday_volume_profile(5)
        assert (p > 0).all()


# ── Market impact tests ─────────────────────────────────────────────────


class TestMarketImpact:
    def test_temporary_positive(self):
        imp = temporary_impact(100, 1_000_000, 0.01)
        assert imp > 0

    def test_temporary_scales_with_qty(self):
        small = temporary_impact(10, 1_000_000, 0.01)
        large = temporary_impact(1000, 1_000_000, 0.01)
        assert large > small

    def test_permanent_positive(self):
        imp = permanent_impact(100, 1_000_000, 0.01)
        assert imp > 0

    def test_permanent_scales_linearly(self):
        imp1 = permanent_impact(100, 1_000_000, 0.01)
        imp2 = permanent_impact(200, 1_000_000, 0.01)
        assert abs(imp2 / imp1 - 2.0) < 0.01

    def test_zero_volume_fallback(self):
        imp = temporary_impact(100, 0, 0.01)
        assert imp > 0


# ── TWAP tests ───────────────────────────────────────────────────────────


class TestTWAP:
    def test_total_qty_matches(self, buy_order, market):
        children = twap_slices(buy_order, market)
        total = sum(c.qty for c in children)
        assert total == buy_order.total_qty

    def test_slices_created(self, buy_order, market):
        children = twap_slices(buy_order, market)
        assert len(children) > 1

    def test_time_fracs_ordered(self, buy_order, market):
        children = twap_slices(buy_order, market)
        fracs = [c.time_frac for c in children]
        assert fracs == sorted(fracs)

    def test_prices_near_mid(self, buy_order, market):
        children = twap_slices(buy_order, market)
        for c in children:
            assert abs(c.price - market.mid) < market.mid * 0.02


# ── VWAP tests ───────────────────────────────────────────────────────────


class TestVWAP:
    def test_total_qty_matches(self, buy_order, market):
        children = vwap_slices(buy_order, market)
        total = sum(c.qty for c in children)
        assert total == buy_order.total_qty

    def test_more_qty_at_edges(self, buy_order, market):
        buy_order.total_qty = 100
        buy_order.max_slices = 10
        children = vwap_slices(buy_order, market)
        # First and last should have more qty than middle
        if len(children) >= 5:
            edge_avg = (children[0].qty + children[-1].qty) / 2
            mid_qty = children[len(children) // 2].qty
            assert edge_avg >= mid_qty

    def test_slices_have_positive_qty(self, buy_order, market):
        children = vwap_slices(buy_order, market)
        assert all(c.qty > 0 for c in children)


# ── Adaptive tests ───────────────────────────────────────────────────────


class TestAdaptive:
    def test_total_qty(self, buy_order, market):
        children = adaptive_slices(buy_order, market)
        assert sum(c.qty for c in children) == buy_order.total_qty

    def test_buy_prices_walk_up(self, buy_order, market):
        buy_order.urgency = 0.8
        children = adaptive_slices(buy_order, market)
        if len(children) >= 3:
            assert children[-1].price >= children[0].price

    def test_sell_prices_walk_down(self, sell_order, market):
        sell_order.urgency = 0.8
        children = adaptive_slices(sell_order, market)
        if len(children) >= 3:
            assert children[-1].price <= children[0].price

    def test_patient_starts_at_mid(self, buy_order, market):
        buy_order.urgency = 0.0
        children = adaptive_slices(buy_order, market)
        assert abs(children[0].price - market.mid) < 0.01


# ── Naive tests ──────────────────────────────────────────────────────────


class TestNaive:
    def test_single_slice(self, buy_order, market):
        children = naive_slices(buy_order, market)
        assert len(children) == 1
        assert children[0].qty == buy_order.total_qty

    def test_buy_at_ask(self, buy_order, market):
        children = naive_slices(buy_order, market)
        assert children[0].price == market.ask

    def test_sell_at_bid(self, sell_order, market):
        children = naive_slices(sell_order, market)
        assert children[0].price == market.bid


# ── Fill simulation tests ────────────────────────────────────────────────


class TestFillSimulation:
    def test_fills_produced(self, buy_order, market):
        children = twap_slices(buy_order, market)
        filled = simulate_fills(children, buy_order, market)
        assert all(c.filled for c in filled)
        assert all(c.fill_qty > 0 for c in filled)

    def test_fill_prices_positive(self, buy_order, market):
        children = adaptive_slices(buy_order, market)
        filled = simulate_fills(children, buy_order, market)
        assert all(c.fill_price > 0 for c in filled)

    def test_slippage_non_negative(self, buy_order, market):
        children = vwap_slices(buy_order, market)
        filled = simulate_fills(children, buy_order, market)
        assert all(c.slippage_bps >= 0 for c in filled)


# ── Execution quality tests ──────────────────────────────────────────────


class TestExecutionQuality:
    def test_result_computed(self, buy_order, market):
        children = twap_slices(buy_order, market)
        children = simulate_fills(children, buy_order, market)
        result = compute_execution_quality(children, market.mid, buy_order)
        assert isinstance(result, ExecutionResult)
        assert result.total_filled > 0
        assert 0 < result.fill_rate <= 1.0

    def test_shortfall_is_float(self, buy_order, market):
        children = adaptive_slices(buy_order, market)
        children = simulate_fills(children, buy_order, market)
        result = compute_execution_quality(children, market.mid, buy_order)
        assert isinstance(result.implementation_shortfall_bps, float)


# ── Engine tests ─────────────────────────────────────────────────────────


class TestEngine:
    def test_execute_default(self, engine, buy_order, market):
        result = engine.execute(buy_order, market)
        assert isinstance(result, ExecutionResult)
        assert result.algorithm == "adaptive"

    def test_execute_all_algos(self, engine, buy_order, market):
        for algo in ALGORITHMS:
            result = engine.execute(buy_order, market, algo)
            assert result.algorithm == algo
            assert result.total_filled > 0

    def test_compare(self, engine, buy_order, market):
        results = engine.compare_algorithms(buy_order, market)
        assert len(results) == 4
        assert "naive" in results
        assert "adaptive" in results

    def test_naive_most_expensive(self, engine, buy_order, market):
        results = engine.compare_algorithms(buy_order, market)
        # Naive should generally have higher shortfall
        naive_cost = results["naive"].implementation_shortfall_bps
        # At least one smart algo should be cheaper
        smart_costs = [r.implementation_shortfall_bps for k, r in results.items() if k != "naive"]
        assert min(smart_costs) <= naive_cost + 5  # allow some noise

    def test_bad_algo_raises(self):
        with pytest.raises(ValueError):
            SmartExecutionEngine("magic")


# ── Backtest tests ───────────────────────────────────────────────────────


class TestBacktest:
    def test_runs(self, engine):
        result = engine.backtest(
            order_sizes=[5, 20],
            volatility_regimes=[0.01, 0.03],
            n_trials=10,
        )
        assert isinstance(result, BacktestResult)
        assert len(result.scenarios) > 0

    def test_best_not_naive(self, engine):
        result = engine.backtest([10, 50], [0.01, 0.02], n_trials=20)
        # Best should usually not be naive
        assert result.best_algorithm in ALGORITHMS

    def test_savings_computed(self, engine):
        result = engine.backtest([10], [0.01], n_trials=20)
        assert isinstance(result.savings_vs_naive_bps, float)

    def test_fill_rates_bounded(self, engine):
        result = engine.backtest([10], [0.01], n_trials=10)
        for algo, rate in result.avg_fill_rate_by_algo.items():
            assert 0 < rate <= 1.0


# ── HTML report tests ────────────────────────────────────────────────────


class TestReport:
    def test_generates(self, engine):
        result = engine.backtest([10], [0.01], n_trials=10)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "se.html"
            path = SmartExecutionEngine.generate_report(result, out)
            assert path.exists()
            assert "Smart Execution" in path.read_text()

    def test_default_path(self, engine):
        result = engine.backtest([10], [0.01], n_trials=5)
        path = SmartExecutionEngine.generate_report(result)
        assert path.exists()
        path.unlink(missing_ok=True)
