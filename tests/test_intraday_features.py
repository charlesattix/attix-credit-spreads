"""Tests for compass/intraday_features.py — intraday feature engineering."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compass.intraday_features import (
    BarData,
    DailyContext,
    FEATURE_NAMES,
    IntradayFeatureEngine,
    IntradayFeatures,
    QuoteSnapshot,
    compute_bid_ask_spread_bps,
    compute_buy_sell_imbalance,
    compute_distance_from_high,
    compute_intraday_range,
    compute_intraday_return,
    compute_momentum_5min,
    compute_momentum_alignment,
    compute_quote_imbalance,
    compute_relative_volume,
    compute_spread_vs_avg,
    compute_volume_acceleration,
    compute_vwap,
    compute_vwap_deviation,
    score_entry_quality,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_bars(n: int = 60, seed: int = 42, base: float = 450.0) -> list:
    rng = np.random.RandomState(seed)
    bars = []
    price = base
    for i in range(n):
        ret = rng.normal(0, 0.001)
        o = price
        c = price * (1 + ret)
        h = max(o, c) * (1 + abs(rng.normal(0, 0.0005)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.0005)))
        vol = rng.uniform(50000, 200000)
        bars.append(BarData(timestamp=i, open=o, high=h, low=l, close=c, volume=vol))
        price = c
    return bars


def _make_quote(bid: float = 449.95, ask: float = 450.05,
                bid_size: int = 500, ask_size: int = 300) -> QuoteSnapshot:
    return QuoteSnapshot(bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
                         last_price=(bid + ask) / 2)


def _make_context(daily_open: float = 449.50) -> DailyContext:
    return DailyContext(daily_open=daily_open, daily_momentum_5d=0.5,
                        avg_daily_volume=5_000_000, avg_spread_20d_bps=3.0)


@pytest.fixture
def bars():
    return _make_bars()


@pytest.fixture
def quote():
    return _make_quote()


@pytest.fixture
def context():
    return _make_context()


@pytest.fixture
def engine():
    return IntradayFeatureEngine()


# ── VWAP tests ───────────────────────────────────────────────────────────


class TestVWAP:
    def test_vwap_positive(self, bars):
        v = compute_vwap(bars)
        assert v > 0

    def test_vwap_near_price(self, bars):
        v = compute_vwap(bars)
        assert abs(v - 450) < 5  # should be near base price

    def test_vwap_empty(self):
        assert compute_vwap([]) == 0.0

    def test_vwap_deviation(self):
        assert abs(compute_vwap_deviation(450, 449)) > 0
        assert compute_vwap_deviation(449, 449) == pytest.approx(0.0, abs=0.01)

    def test_vwap_deviation_zero(self):
        assert compute_vwap_deviation(450, 0) == 0.0


# ── Price feature tests ─────────────────────────────────────────────────


class TestPriceFeatures:
    def test_intraday_return(self):
        assert compute_intraday_return(451, 450) == pytest.approx(100 / 450, rel=0.01)

    def test_intraday_return_zero_open(self):
        assert compute_intraday_return(450, 0) == 0.0

    def test_intraday_range(self, bars):
        r = compute_intraday_range(bars, 449.5)
        assert r > 0
        assert r < 5  # reasonable range for 1hr of 1-min bars

    def test_intraday_range_empty(self):
        assert compute_intraday_range([], 450) == 0.0

    def test_distance_from_high(self, bars):
        current = bars[-1].close
        d = compute_distance_from_high(current, bars)
        assert d >= 0  # can't be above high

    def test_distance_from_high_at_high(self):
        bars = [BarData(0, 100, 105, 99, 105, 1000)]
        assert compute_distance_from_high(105, bars) == pytest.approx(0.0)

    def test_distance_from_high_empty(self):
        assert compute_distance_from_high(450, []) == 0.0


# ── Volume feature tests ────────────────────────────────────────────────


class TestVolumeFeatures:
    def test_relative_volume(self, bars):
        rv = compute_relative_volume(bars, 5_000_000, 0.5)
        assert rv > 0

    def test_relative_volume_default(self):
        assert compute_relative_volume([], 5_000_000) == 1.0

    def test_volume_acceleration(self, bars):
        acc = compute_volume_acceleration(bars, lookback=15)
        assert isinstance(acc, float)

    def test_volume_acceleration_short(self):
        bars = [BarData(i, 100, 101, 99, 100, 1000) for i in range(5)]
        assert compute_volume_acceleration(bars, 15) == 0.0

    def test_buy_sell_imbalance_bounded(self, bars):
        imb = compute_buy_sell_imbalance(bars)
        assert -1.0 <= imb <= 1.0

    def test_buy_sell_imbalance_all_up(self):
        bars = [BarData(i, 100, 102, 99, 101, 1000) for i in range(10)]  # close > open
        assert compute_buy_sell_imbalance(bars) == 1.0

    def test_buy_sell_imbalance_all_down(self):
        bars = [BarData(i, 101, 102, 99, 100, 1000) for i in range(10)]  # close < open
        assert compute_buy_sell_imbalance(bars) == -1.0

    def test_buy_sell_imbalance_empty(self):
        assert compute_buy_sell_imbalance([]) == 0.0


# ── Microstructure tests ─────────────────────────────────────────────────


class TestMicrostructure:
    def test_spread_bps(self):
        q = _make_quote(bid=449.95, ask=450.05)
        bps = compute_bid_ask_spread_bps(q)
        assert 1 < bps < 5  # ~2.2 bps on $450

    def test_spread_bps_zero_mid(self):
        q = QuoteSnapshot(bid=0, ask=0, bid_size=0, ask_size=0, last_price=0)
        assert compute_bid_ask_spread_bps(q) == 0.0

    def test_spread_vs_avg(self):
        assert compute_spread_vs_avg(3.0, 3.0) == pytest.approx(1.0)
        assert compute_spread_vs_avg(6.0, 3.0) == pytest.approx(2.0)
        assert compute_spread_vs_avg(3.0, 0.0) == 1.0

    def test_quote_imbalance_balanced(self):
        q = _make_quote(bid_size=500, ask_size=500)
        assert compute_quote_imbalance(q) == pytest.approx(0.0)

    def test_quote_imbalance_bid_heavy(self):
        q = _make_quote(bid_size=1000, ask_size=0)
        assert compute_quote_imbalance(q) == 1.0

    def test_quote_imbalance_ask_heavy(self):
        q = _make_quote(bid_size=0, ask_size=1000)
        assert compute_quote_imbalance(q) == -1.0

    def test_quote_imbalance_empty(self):
        q = QuoteSnapshot(bid=0, ask=0, bid_size=0, ask_size=0, last_price=0)
        assert compute_quote_imbalance(q) == 0.0


# ── Momentum tests ───────────────────────────────────────────────────────


class TestMomentum:
    def test_momentum_5min(self, bars):
        m = compute_momentum_5min(bars, lookback=5)
        assert isinstance(m, float)

    def test_momentum_5min_short(self):
        bars = [BarData(0, 100, 101, 99, 100, 1000)]
        assert compute_momentum_5min(bars, 5) == 0.0

    def test_alignment_same_direction(self):
        assert compute_momentum_alignment(0.5, 1.0) == 1.0

    def test_alignment_opposite(self):
        assert compute_momentum_alignment(0.5, -1.0) == 0.0

    def test_alignment_neutral(self):
        assert compute_momentum_alignment(0.001, 0.0) == 0.5


# ── IntradayFeatures dataclass tests ─────────────────────────────────────


class TestIntradayFeatures:
    def test_to_dict(self):
        f = IntradayFeatures(vwap_deviation_pct=0.5, relative_volume=1.2)
        d = f.to_dict()
        assert len(d) == 12
        assert d["vwap_deviation_pct"] == 0.5
        assert d["relative_volume"] == 1.2

    def test_to_array(self):
        f = IntradayFeatures()
        arr = f.to_array()
        assert len(arr) == 12

    def test_feature_names_count(self):
        assert len(FEATURE_NAMES) == 12


# ── Entry quality score tests ────────────────────────────────────────────


class TestEntryQuality:
    def test_score_bounded(self):
        f = IntradayFeatures()
        s = score_entry_quality(f)
        assert 0 <= s <= 1

    def test_good_entry_high_score(self):
        f = IntradayFeatures(
            vwap_deviation_pct=-0.5,  # below VWAP
            relative_volume=1.5,       # high volume
            spread_vs_avg=0.5,         # tight spread
            momentum_alignment=1.0,    # confirming
            buy_sell_imbalance=0.5,    # bullish
        )
        assert score_entry_quality(f) > 0.6

    def test_bad_entry_low_score(self):
        f = IntradayFeatures(
            vwap_deviation_pct=2.0,    # way above VWAP
            relative_volume=0.3,       # low volume
            spread_vs_avg=3.0,         # wide spread
            momentum_alignment=0.0,    # divergent
            buy_sell_imbalance=-0.8,   # selling
        )
        assert score_entry_quality(f) < 0.4


# ── Full engine tests ────────────────────────────────────────────────────


class TestEngine:
    def test_compute_all(self, engine, bars, quote, context):
        f = engine.compute(bars, quote, context)
        assert isinstance(f, IntradayFeatures)
        d = f.to_dict()
        assert len(d) == 12
        # All should be finite
        assert all(np.isfinite(v) for v in d.values())

    def test_compute_empty_bars(self, engine, quote, context):
        f = engine.compute([], quote, context)
        assert isinstance(f, IntradayFeatures)

    def test_compute_from_dataframe(self, engine, quote, context):
        rng = np.random.RandomState(42)
        df = pd.DataFrame({
            "timestamp": range(30),
            "open": 450 + rng.normal(0, 0.5, 30),
            "high": 450.5 + rng.normal(0, 0.5, 30),
            "low": 449.5 + rng.normal(0, 0.5, 30),
            "close": 450 + rng.normal(0, 0.5, 30),
            "volume": rng.uniform(50000, 200000, 30),
        })
        f = engine.compute_from_dataframe(df, quote, context)
        assert isinstance(f, IntradayFeatures)
        assert f.vwap_deviation_pct != 0 or f.relative_volume != 1.0

    def test_custom_lookbacks(self, bars, quote, context):
        engine = IntradayFeatureEngine(volume_lookback=5, momentum_lookback=3)
        f = engine.compute(bars, quote, context)
        assert isinstance(f, IntradayFeatures)

    def test_reproducible(self, engine, bars, quote, context):
        f1 = engine.compute(bars, quote, context)
        f2 = engine.compute(bars, quote, context)
        assert f1.to_dict() == f2.to_dict()
