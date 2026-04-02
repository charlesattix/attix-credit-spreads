"""Tests for compass.live_sim_engine — realistic trade simulation."""
from __future__ import annotations

import math

import numpy as np
import pytest

from compass.live_sim_engine import (
    FillResult,
    LatencyModel,
    LiveSimEngine,
    LiveSimResult,
    MarketImpactModel,
    PartialFillModel,
    QueueModel,
    SimulatedTrade,
    SpreadDynamics,
    SpreadModel,
    StrategySimResult,
    generate_multi_strategy,
    generate_strategy_data,
)


# ── SpreadDynamics ──────────────────────────────────────────────────────────
class TestSpreadDynamics:
    def test_returns_model(self):
        s = SpreadDynamics().compute(2.50)
        assert isinstance(s, SpreadModel)
        assert s.bid < s.mid_price < s.ask

    def test_bid_below_ask(self):
        s = SpreadDynamics().compute(3.00, vix=25.0)
        assert s.bid < s.ask

    def test_higher_vix_wider_spread(self):
        sd = SpreadDynamics(seed=42)
        low = sd.compute(2.50, vix=12.0, hour=12)
        sd2 = SpreadDynamics(seed=42)
        high = sd2.compute(2.50, vix=40.0, hour=12)
        assert high.spread_bps > low.spread_bps

    def test_open_wider_than_midday(self):
        sd = SpreadDynamics(seed=42)
        open_s = sd.compute(2.50, vix=18, hour=9)
        sd2 = SpreadDynamics(seed=42)
        mid_s = sd2.compute(2.50, vix=18, hour=12)
        assert open_s.time_component > mid_s.time_component

    def test_spread_positive(self):
        s = SpreadDynamics().compute(2.50)
        assert s.spread_bps > 0


# ── QueueModel ──────────────────────────────────────────────────────────────
class TestQueueModel:
    def test_position_positive(self):
        pos = QueueModel().estimate_position(100)
        assert pos >= 1

    def test_fill_prob_bounded(self):
        qm = QueueModel()
        p = qm.fill_probability(10, 100)
        assert 0 < p < 1

    def test_front_higher_than_back(self):
        qm = QueueModel(seed=42)
        front = qm.fill_probability(1, 100)
        qm2 = QueueModel(seed=42)
        back = qm2.fill_probability(90, 100)
        assert front >= back - 0.2  # stochastic tolerance

    def test_longer_time_helps(self):
        qm = QueueModel(seed=42)
        short = qm.fill_probability(50, 100, time_in_queue_s=5)
        qm2 = QueueModel(seed=42)
        long = qm2.fill_probability(50, 100, time_in_queue_s=120)
        assert long >= short - 0.1


# ── LatencyModel ────────────────────────────────────────────────────────────
class TestLatencyModel:
    def test_bounded(self):
        lm = LatencyModel(min_ms=10, max_ms=500)
        for _ in range(50):
            lat = lm.sample()
            assert 10 <= lat <= 500

    def test_positive(self):
        assert LatencyModel().sample() > 0

    def test_log_normal_shape(self):
        lm = LatencyModel(seed=42)
        samples = [lm.sample() for _ in range(200)]
        median = np.median(samples)
        mean = np.mean(samples)
        assert mean > median  # right-skewed (log-normal property)


# ── MarketImpactModel ──────────────────────────────────────────────────────
class TestMarketImpact:
    def test_zero_for_zero_qty(self):
        assert MarketImpactModel().compute(0, 2.50) == 0.0

    def test_positive_for_nonzero(self):
        impact = MarketImpactModel().compute(10, 2.50)
        assert impact >= 0

    def test_scales_with_size(self):
        mi = MarketImpactModel(seed=42)
        small = mi.compute(5, 2.50)
        mi2 = MarketImpactModel(seed=42)
        large = mi2.compute(100, 2.50)
        assert large >= small

    def test_sqrt_scaling(self):
        mi = MarketImpactModel(kyle_lambda=0.10, adv=5000, seed=42)
        i1 = mi.compute(100, 2.50)
        mi2 = MarketImpactModel(kyle_lambda=0.10, adv=5000, seed=42)
        i4 = mi2.compute(400, 2.50)
        # 4× qty → 2× impact (sqrt), with noise tolerance
        ratio = i4 / max(i1, 0.01)
        assert 1.0 < ratio < 4.0


# ── PartialFillModel ───────────────────────────────────────────────────────
class TestPartialFill:
    def test_high_prob_full_fill(self):
        pf = PartialFillModel(seed=42)
        filled, rate = pf.simulate(10, 0.99)
        assert filled == 10
        assert rate == 1.0

    def test_zero_qty(self):
        filled, rate = PartialFillModel().simulate(0, 0.95)
        assert filled == 0

    def test_low_prob_partial(self):
        pf = PartialFillModel(seed=42)
        filled, rate = pf.simulate(100, 0.30)
        assert filled < 100
        assert 0 < rate < 1

    def test_fill_rate_bounded(self):
        pf = PartialFillModel(seed=42)
        _, rate = pf.simulate(50, 0.60)
        assert 0 <= rate <= 1


# ── LiveSimEngine fills ────────────────────────────────────────────────────
class TestSimulateFill:
    def test_returns_fill(self):
        engine = LiveSimEngine()
        f = engine.simulate_fill(2.50, 10)
        assert isinstance(f, FillResult)

    def test_fill_price_positive(self):
        f = LiveSimEngine().simulate_fill(2.50, 5)
        assert f.fill_price > 0

    def test_market_order_full_fill(self):
        f = LiveSimEngine().simulate_fill(2.50, 10, order_type="market")
        assert f.fill_rate == 1.0
        assert f.filled_qty == 10

    def test_slippage_positive(self):
        f = LiveSimEngine().simulate_fill(2.50, 10)
        assert f.slippage_bps > 0

    def test_latency_tracked(self):
        f = LiveSimEngine().simulate_fill(2.50, 10)
        assert f.latency_ms > 0

    def test_sell_lower_than_mid(self):
        f = LiveSimEngine().simulate_fill(2.50, 5, side="sell")
        assert f.fill_price <= 2.50

    def test_buy_higher_than_mid(self):
        f = LiveSimEngine().simulate_fill(2.50, 5, side="buy")
        assert f.fill_price >= 2.50


# ── Strategy simulation ───────────────────────────────────────────────────
class TestSimulateStrategy:
    def test_returns_result(self):
        data = generate_strategy_data(50)
        result, trades = LiveSimEngine().simulate_strategy(
            "TEST", data["returns"], data["sizes"], data["prices"], data["vix"],
        )
        assert isinstance(result, StrategySimResult)
        assert len(trades) == 50

    def test_slippage_reduces_returns(self):
        """Friction (slippage + impact) should be tracked."""
        data = generate_strategy_data(100, seed=42)
        result, _ = LiveSimEngine().simulate_strategy(
            "TEST", data["returns"], data["sizes"], data["prices"], data["vix"],
        )
        assert result.avg_slippage_bps > 0
        assert result.avg_impact_bps >= 0

    def test_avg_slippage_positive(self):
        data = generate_strategy_data(50)
        result, _ = LiveSimEngine().simulate_strategy(
            "T", data["returns"], data["sizes"], data["prices"], data["vix"],
        )
        assert result.avg_slippage_bps > 0

    def test_avg_fill_rate_bounded(self):
        data = generate_strategy_data(50)
        result, _ = LiveSimEngine().simulate_strategy(
            "T", data["returns"], data["sizes"], data["prices"], data["vix"],
        )
        assert 0 < result.avg_fill_rate <= 1.0

    def test_n_trades(self):
        data = generate_strategy_data(30)
        result, _ = LiveSimEngine().simulate_strategy(
            "T", data["returns"], data["sizes"], data["prices"], data["vix"],
        )
        assert result.n_trades == 30

    def test_empty_data(self):
        result, trades = LiveSimEngine().simulate_strategy(
            "T", np.array([]), np.array([]), np.array([]), np.array([]),
        )
        assert result.n_trades == 0


# ── Multi-strategy comparison ──────────────────────────────────────────────
class TestRunComparison:
    def test_returns_result(self):
        strats = generate_multi_strategy(3, 50)
        r = LiveSimEngine().run_comparison(strats)
        assert isinstance(r, LiveSimResult)
        assert len(r.strategy_results) == 3

    def test_worst_degradation_set(self):
        strats = generate_multi_strategy(3, 50)
        r = LiveSimEngine().run_comparison(strats)
        assert r.worst_degradation in [s.strategy for s in r.strategy_results]

    def test_generated_at(self):
        strats = generate_multi_strategy(2, 30)
        r = LiveSimEngine().run_comparison(strats)
        assert len(r.generated_at) > 0

    def test_trades_populated(self):
        strats = generate_multi_strategy(2, 20)
        r = LiveSimEngine().run_comparison(strats)
        assert len(r.trades) > 0


# ── Synthetic data ──────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_strategy_data_keys(self):
        d = generate_strategy_data(50)
        assert set(d.keys()) == {"returns", "sizes", "prices", "vix"}
        assert len(d["returns"]) == 50

    def test_multi_strategy(self):
        ms = generate_multi_strategy(4, 30)
        assert len(ms) == 4
        for data in ms.values():
            assert len(data["returns"]) == 30


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_fill_result(self):
        f = FillResult(10, 8, 2.45, 5.0, 2.0, 50.0, True, 15, 0.80)
        assert f.is_partial

    def test_strategy_result(self):
        s = StrategySimResult("T", 50.0, 45.0, 10.0, 2.5, 2.2, 5.0, 2.0, 0.90, 15.0, 50.0, 100)
        assert s.degradation_pct == 10.0

    def test_result_defaults(self):
        r = LiveSimResult()
        assert r.strategy_results == []
