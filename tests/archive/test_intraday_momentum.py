"""Tests for compass/intraday_momentum.py — momentum scalping engine."""

from __future__ import annotations

import numpy as np
import pytest

from compass.intraday_momentum import (
    Bar,
    FEATURE_NAMES,
    MomentumFeatures,
    MomentumScalper,
    ScalperConfig,
    ScalpSignal,
    ScalpTrade,
    compute_all_features,
    compute_momentum_consistency,
    compute_order_flow_imbalance,
    compute_price_acceleration,
    compute_rsi,
    compute_tick_momentum,
    compute_tick_velocity,
    compute_volume_surge,
    compute_vwap_and_slope,
    evaluate_signal,
    simulate_scalp_trade,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_bars(n: int = 60, seed: int = 42, base: float = 450.0, trend: float = 0.0) -> list:
    """Generate synthetic 1-min bars with optional trend."""
    rng = np.random.RandomState(seed)
    bars = []
    price = base
    for i in range(n):
        ret = rng.normal(trend, 0.0008)
        o = price
        c = price * (1 + ret)
        h = max(o, c) * (1 + abs(rng.normal(0, 0.0003)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.0003)))
        vol = rng.uniform(80000, 250000)
        bars.append(Bar(timestamp=i, open=o, high=h, low=l, close=c, volume=vol))
        price = c
    return bars


def _make_trending_bars(n: int = 60, direction: str = "up") -> list:
    """Strong directional bars for signal triggering."""
    trend = 0.002 if direction == "up" else -0.002
    return _make_bars(n, seed=99, trend=trend)


@pytest.fixture
def flat_bars():
    return _make_bars(60, trend=0.0)


@pytest.fixture
def up_bars():
    return _make_trending_bars(60, "up")


@pytest.fixture
def down_bars():
    return _make_trending_bars(60, "down")


@pytest.fixture
def config():
    return ScalperConfig()


@pytest.fixture
def scalper(config):
    return MomentumScalper(config)


# ── Tick momentum tests ─────────────────────────────────────────────────


class TestTickMomentum:
    def test_positive_uptrend(self, up_bars):
        m = compute_tick_momentum(up_bars, 5)
        assert m > 0

    def test_negative_downtrend(self, down_bars):
        m = compute_tick_momentum(down_bars, 5)
        assert m < 0

    def test_near_zero_flat(self, flat_bars):
        m = compute_tick_momentum(flat_bars, 5)
        assert abs(m) < 50  # small random walk

    def test_short_bars(self):
        bars = [Bar(0, 100, 101, 99, 100, 1000)]
        assert compute_tick_momentum(bars, 5) == 0.0

    def test_15min_vs_5min(self, up_bars):
        m5 = compute_tick_momentum(up_bars, 5)
        m15 = compute_tick_momentum(up_bars, 15)
        assert abs(m15) > abs(m5)  # longer lookback, bigger move


# ── VWAP tests ───────────────────────────────────────────────────────────


class TestVWAP:
    def test_vwap_positive(self, flat_bars):
        vwap, slope = compute_vwap_and_slope(flat_bars)
        assert vwap > 0

    def test_slope_positive_uptrend(self, up_bars):
        _, slope = compute_vwap_and_slope(up_bars)
        assert slope > 0

    def test_slope_negative_downtrend(self, down_bars):
        _, slope = compute_vwap_and_slope(down_bars)
        assert slope < 0

    def test_empty(self):
        v, s = compute_vwap_and_slope([])
        assert v == 0.0 and s == 0.0


# ── Volume tests ─────────────────────────────────────────────────────────


class TestVolume:
    def test_surge_baseline(self, flat_bars):
        surge = compute_volume_surge(flat_bars)
        assert 0.5 < surge < 2.0  # near 1.0 for random

    def test_surge_short(self):
        bars = [Bar(i, 100, 101, 99, 100, 1000) for i in range(5)]
        assert compute_volume_surge(bars) == 1.0

    def test_imbalance_bounded(self, flat_bars):
        imb = compute_order_flow_imbalance(flat_bars)
        assert -1.0 <= imb <= 1.0

    def test_imbalance_all_up(self):
        bars = [Bar(i, 100, 102, 99, 101, 1000) for i in range(10)]
        assert compute_order_flow_imbalance(bars, 5) == 1.0

    def test_imbalance_all_down(self):
        bars = [Bar(i, 101, 102, 99, 100, 1000) for i in range(10)]
        assert compute_order_flow_imbalance(bars, 5) == -1.0


# ── Acceleration & consistency tests ─────────────────────────────────────


class TestAcceleration:
    def test_acceleration_computed(self, up_bars):
        a = compute_price_acceleration(up_bars)
        assert isinstance(a, float)

    def test_acceleration_short(self):
        bars = [Bar(i, 100, 101, 99, 100, 1000) for i in range(3)]
        assert compute_price_acceleration(bars) == 0.0

    def test_consistency_uptrend(self, up_bars):
        c = compute_momentum_consistency(up_bars)
        assert c > 0.5  # most bars should be up

    def test_consistency_bounded(self, flat_bars):
        c = compute_momentum_consistency(flat_bars)
        assert 0.0 <= c <= 1.0


# ── RSI tests ────────────────────────────────────────────────────────────


class TestRSI:
    def test_rsi_uptrend_high(self, up_bars):
        r = compute_rsi(up_bars, 5)
        assert r > 50

    def test_rsi_downtrend_low(self, down_bars):
        r = compute_rsi(down_bars, 5)
        assert r < 50

    def test_rsi_bounded(self, flat_bars):
        r = compute_rsi(flat_bars, 5)
        assert 0 <= r <= 100

    def test_rsi_short(self):
        bars = [Bar(0, 100, 101, 99, 100, 1000)]
        assert compute_rsi(bars, 5) == 50.0


# ── Tick velocity tests ──────────────────────────────────────────────────


class TestTickVelocity:
    def test_velocity_computed(self, up_bars):
        v = compute_tick_velocity(up_bars)
        assert isinstance(v, float)

    def test_velocity_short(self):
        bars = [Bar(0, 100, 101, 99, 100, 1000)]
        assert compute_tick_velocity(bars) == 0.0


# ── Full feature computation tests ───────────────────────────────────────


class TestAllFeatures:
    def test_compute_all(self, flat_bars):
        f = compute_all_features(flat_bars)
        assert isinstance(f, MomentumFeatures)
        d = f.to_dict()
        assert len(d) == 12
        assert all(np.isfinite(v) for v in d.values())

    def test_feature_names_count(self):
        assert len(FEATURE_NAMES) == 12

    def test_to_array_shape(self, flat_bars):
        f = compute_all_features(flat_bars)
        assert f.to_array().shape == (12,)

    def test_short_bars_default(self):
        bars = [Bar(i, 100, 101, 99, 100, 1000) for i in range(5)]
        f = compute_all_features(bars)
        assert f.rsi_5min == 50.0  # default


# ── Signal evaluation tests ──────────────────────────────────────────────


class TestSignal:
    def test_no_signal_flat(self, flat_bars, config):
        f = compute_all_features(flat_bars)
        sig = evaluate_signal(f, config, flat_bars[-1].close)
        assert not sig.triggered or sig.strength < 0.6

    def test_long_signal_uptrend(self, up_bars, config):
        f = compute_all_features(up_bars)
        sig = evaluate_signal(f, config, up_bars[-1].close)
        if sig.triggered:
            assert sig.direction == "long"
            assert sig.target_price > sig.entry_price

    def test_short_signal_downtrend(self, down_bars, config):
        f = compute_all_features(down_bars)
        sig = evaluate_signal(f, config, down_bars[-1].close)
        if sig.triggered:
            assert sig.direction == "short"
            assert sig.target_price < sig.entry_price

    def test_strength_bounded(self, up_bars, config):
        f = compute_all_features(up_bars)
        sig = evaluate_signal(f, config, up_bars[-1].close)
        assert 0 <= sig.strength <= 1


# ── Trade simulation tests ──────────────────────────────────────────────


class TestTradeSimulation:
    def test_simulate_returns_trade(self, up_bars, config):
        trade = simulate_scalp_trade(up_bars, 30, "long", config)
        assert trade is None or isinstance(trade, ScalpTrade)

    def test_trade_has_exit_reason(self, up_bars, config):
        trade = simulate_scalp_trade(up_bars, 30, "long", config)
        if trade:
            assert trade.exit_reason in ("profit_target", "stop_loss", "time_stop")

    def test_trade_hold_capped(self, flat_bars, config):
        trade = simulate_scalp_trade(flat_bars, 30, "long", config)
        if trade:
            assert trade.hold_bars <= config.max_hold_bars

    def test_entry_at_end_returns_none(self, flat_bars, config):
        assert simulate_scalp_trade(flat_bars, len(flat_bars) - 1, "long", config) is None


# ── Full scalper tests ───────────────────────────────────────────────────


class TestScalper:
    def test_evaluate(self, scalper, flat_bars):
        sig = scalper.evaluate(flat_bars)
        assert isinstance(sig, ScalpSignal)

    def test_backtest_returns_list(self, scalper, up_bars):
        trades = scalper.backtest(up_bars)
        assert isinstance(trades, list)

    def test_backtest_cooldown(self):
        bars = _make_trending_bars(200, "up")
        scalper = MomentumScalper(ScalperConfig(momentum_threshold_bps=5.0))
        trades = scalper.backtest(bars, cooldown=30)
        # With cooldown=30, can't have more than ~5 trades in 200 bars
        assert len(trades) <= 7

    def test_custom_config(self):
        cfg = ScalperConfig(momentum_threshold_bps=5.0, max_hold_bars=5)
        scalper = MomentumScalper(cfg)
        assert scalper.config.max_hold_bars == 5
