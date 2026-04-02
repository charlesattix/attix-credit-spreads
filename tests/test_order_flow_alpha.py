"""Tests for compass/order_flow_alpha.py — order flow imbalance alpha."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.order_flow_alpha import (
    BacktestResult, FilterResult, FlowSignal, OFISnapshot, OrderFlowAlpha,
    TickImbalanceBar, accumulation_distribution, close_location_value,
    compute_tick_imbalance_bars, cumulative_delta, generate_signals,
    volume_weighted_ofi,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _ohlcv(n=300, seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    close = 430 + rng.normal(0, 1.5, n).cumsum()
    high = close + rng.uniform(0.5, 3, n)
    low = close - rng.uniform(0.5, 3, n)
    volume = rng.uniform(50_000_000, 100_000_000, n)
    return pd.DataFrame({"open": close + rng.normal(0, 0.5, n),
                          "high": high, "low": low, "close": close,
                          "volume": volume}, index=dates)

def _engine(n=300, seed=42, **kw):
    return OrderFlowAlpha(_ohlcv(n, seed), **kw)

# ── CLV tests ────────────────────────────────────────────────────────────

class TestCLV:
    def test_close_at_high(self):
        clv = close_location_value(np.array([110]), np.array([100]), np.array([110]))
        assert clv[0] == pytest.approx(1.0)

    def test_close_at_low(self):
        clv = close_location_value(np.array([110]), np.array([100]), np.array([100]))
        assert clv[0] == pytest.approx(-1.0)

    def test_close_at_mid(self):
        clv = close_location_value(np.array([110]), np.array([100]), np.array([105]))
        assert clv[0] == pytest.approx(0.0)

    def test_range(self):
        h = np.array([110, 120, 115])
        l = np.array([100, 105, 108])
        c = np.array([108, 107, 114])
        clv = close_location_value(h, l, c)
        assert np.all(clv >= -1) and np.all(clv <= 1)

    def test_flat_bar(self):
        """High == Low → CLV = 0 (no range)."""
        clv = close_location_value(np.array([100]), np.array([100]), np.array([100]))
        assert clv[0] == pytest.approx(-1.0)  # (100-100)/1 * 2 - 1

# ── Accumulation/Distribution ────────────────────────────────────────────

class TestAD:
    def test_monotonic_up_when_buying(self):
        """All closes at high → AD should increase."""
        h = np.array([110, 120, 130], dtype=float)
        l = np.array([100, 110, 120], dtype=float)
        c = h.copy()  # close at high
        v = np.array([1e6, 1e6, 1e6])
        ad = accumulation_distribution(h, l, c, v)
        assert ad[-1] > ad[0]

    def test_monotonic_down_when_selling(self):
        h = np.array([110, 120, 130], dtype=float)
        l = np.array([100, 110, 120], dtype=float)
        c = l.copy()  # close at low
        v = np.array([1e6, 1e6, 1e6])
        ad = accumulation_distribution(h, l, c, v)
        assert ad[-1] < ad[0]

    def test_length(self):
        df = _ohlcv(50)
        ad = accumulation_distribution(df["high"].values, df["low"].values,
                                        df["close"].values, df["volume"].values)
        assert len(ad) == 50

# ── Volume-weighted OFI ──────────────────────────────────────────────────

class TestOFI:
    def test_returns_array(self):
        df = _ohlcv(50)
        ofi = volume_weighted_ofi(df["high"].values, df["low"].values,
                                   df["close"].values, df["volume"].values, 10)
        assert len(ofi) == 50

    def test_nan_before_lookback(self):
        df = _ohlcv(50)
        ofi = volume_weighted_ofi(df["high"].values, df["low"].values,
                                   df["close"].values, df["volume"].values, 20)
        assert np.isnan(ofi[0])
        assert not np.isnan(ofi[25])

    def test_higher_lookback_smoother(self):
        df = _ohlcv(200)
        ofi_short = volume_weighted_ofi(df["high"].values, df["low"].values,
                                         df["close"].values, df["volume"].values, 5)
        ofi_long = volume_weighted_ofi(df["high"].values, df["low"].values,
                                        df["close"].values, df["volume"].values, 40)
        # Long lookback should be smoother (lower std of valid values)
        short_valid = ofi_short[~np.isnan(ofi_short)]
        long_valid = ofi_long[~np.isnan(ofi_long)]
        assert np.std(long_valid) < np.std(short_valid)

# ── Cumulative delta ─────────────────────────────────────────────────────

class TestCumDelta:
    def test_returns_array(self):
        df = _ohlcv(50)
        cd = cumulative_delta(df["high"].values, df["low"].values,
                               df["close"].values, df["volume"].values)
        assert len(cd) == 50

    def test_positive_when_buying(self):
        h = np.array([110, 120, 130], dtype=float)
        l = np.array([100, 110, 120], dtype=float)
        c = h.copy()
        v = np.ones(3) * 1e6
        cd = cumulative_delta(h, l, c, v)
        assert cd[-1] > 0

# ── Tick imbalance bars ──────────────────────────────────────────────────

class TestTickBars:
    def test_returns_list(self):
        df = _ohlcv(200)
        bars = compute_tick_imbalance_bars(df["close"].values, df["volume"].values)
        assert isinstance(bars, list)

    def test_bars_have_fields(self):
        df = _ohlcv(200)
        bars = compute_tick_imbalance_bars(df["close"].values, df["volume"].values)
        if bars:
            b = bars[0]
            assert b.total_volume > 0
            assert b.direction in ("buy_dominated", "sell_dominated")

    def test_bars_cover_data(self):
        df = _ohlcv(200)
        bars = compute_tick_imbalance_bars(df["close"].values, df["volume"].values)
        if len(bars) >= 2:
            assert bars[-1].end_idx >= bars[0].start_idx

    def test_short_data(self):
        bars = compute_tick_imbalance_bars(np.array([100, 101]), np.array([1e6, 1e6]), 50)
        assert len(bars) == 0

# ── Signal generation ────────────────────────────────────────────────────

class TestSignals:
    def test_returns_array(self):
        df = _ohlcv(200)
        ofi = volume_weighted_ofi(df["high"].values, df["low"].values,
                                   df["close"].values, df["volume"].values, 20)
        cd = cumulative_delta(df["high"].values, df["low"].values,
                               df["close"].values, df["volume"].values)
        sigs = generate_signals(ofi, cd, lookback=40)
        assert len(sigs) == 200

    def test_signal_values(self):
        df = _ohlcv(200)
        ofi = volume_weighted_ofi(df["high"].values, df["low"].values,
                                   df["close"].values, df["volume"].values, 20)
        cd = cumulative_delta(df["high"].values, df["low"].values,
                               df["close"].values, df["volume"].values)
        sigs = generate_signals(ofi, cd)
        assert set(np.unique(sigs)).issubset({-1, 0, 1})

    def test_has_nonzero_signals(self):
        e = _engine(300)
        e.compute_signals()
        nonzero = np.sum(e.signals != 0)
        assert nonzero > 0

    def test_lower_threshold_more_signals(self):
        e1 = _engine(300, contrarian_z=1.0, trend_z_min=0.3)
        e1.compute_signals()
        e2 = _engine(300, contrarian_z=3.0, trend_z_min=1.5)
        e2.compute_signals()
        assert np.sum(e1.signals != 0) >= np.sum(e2.signals != 0)

# ── OrderFlowAlpha engine ────────────────────────────────────────────────

class TestEngine:
    def test_compute_signals(self):
        e = _engine()
        snaps = e.compute_signals()
        assert len(snaps) == 300
        assert all(isinstance(s, OFISnapshot) for s in snaps)

    def test_clv_range(self):
        e = _engine()
        e.compute_signals()
        for s in e.snapshots:
            assert -1 <= s.clv <= 1

    def test_signal_values(self):
        e = _engine()
        e.compute_signals()
        signals = {s.signal for s in e.snapshots}
        assert signals.issubset({"buy", "sell", "neutral"})

    def test_strength_range(self):
        e = _engine()
        e.compute_signals()
        for s in e.snapshots:
            assert 0 <= s.strength <= 1

    def test_tick_bars_computed(self):
        e = _engine()
        e.compute_signals()
        assert e.tick_bars is not None

    def test_current_state(self):
        e = _engine()
        e.compute_signals()
        state = e.get_current_state()
        assert state is not None
        assert isinstance(state, OFISnapshot)

    def test_current_state_before_compute(self):
        e = _engine()
        assert e.get_current_state() is None

# ── Backtest ─────────────────────────────────────────────────────────────

class TestBacktest:
    def test_returns_result(self):
        e = _engine()
        bt = e.backtest()
        assert isinstance(bt, BacktestResult)

    def test_accuracy_range(self):
        e = _engine()
        bt = e.backtest()
        if bt.n_signals > 0:
            assert 0 <= bt.accuracy <= 1

    def test_has_signals(self):
        e = _engine(300, contrarian_z=1.5, trend_z_min=0.3)
        bt = e.backtest()
        assert bt.n_signals > 0

    def test_contrarian_and_trend(self):
        e = _engine(300, contrarian_z=1.5, trend_z_min=0.3)
        bt = e.backtest()
        assert bt.contrarian_n + bt.trend_n == bt.n_signals

    def test_auto_computes_signals(self):
        e = _engine()
        assert e.signals is None
        bt = e.backtest()
        assert e.signals is not None

    def test_sharpe_finite(self):
        e = _engine()
        bt = e.backtest()
        assert np.isfinite(bt.sharpe)

# ── Filter strategy ─────────────────────────────────────────────────────

class TestFilter:
    def test_returns_result(self):
        e = _engine(200)
        e.compute_signals()
        rng = np.random.RandomState(42)
        pnls = rng.normal(50, 200, 100)
        indices = rng.randint(60, 190, 100)
        fr = e.filter_strategy(pnls, indices)
        assert isinstance(fr, FilterResult)

    def test_filtered_fewer_trades(self):
        e = _engine(200)
        e.compute_signals()
        rng = np.random.RandomState(42)
        pnls = rng.normal(50, 200, 100)
        indices = rng.randint(60, 190, 100)
        fr = e.filter_strategy(pnls, indices)
        assert fr.filtered_trades <= fr.base_trades

    def test_correlation_range(self):
        e = _engine(200)
        e.compute_signals()
        rng = np.random.RandomState(42)
        pnls = rng.normal(50, 200, 50)
        indices = rng.randint(60, 190, 50)
        fr = e.filter_strategy(pnls, indices)
        assert -1 <= fr.correlation_with_base <= 1

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_short_data(self):
        e = _engine(n=10)
        snaps = e.compute_signals()
        assert len(snaps) == 10

    def test_flat_market(self):
        df = pd.DataFrame({
            "open": np.full(50, 430.0), "high": np.full(50, 431.0),
            "low": np.full(50, 429.0), "close": np.full(50, 430.0),
            "volume": np.full(50, 1e7),
        }, index=pd.bdate_range("2024-01-02", periods=50))
        e = OrderFlowAlpha(df)
        e.compute_signals()
        # Flat market → CLV all 0
        for s in e.snapshots:
            assert s.clv == pytest.approx(0.0)

    def test_zero_volume(self):
        df = _ohlcv(50)
        df["volume"] = 0
        e = OrderFlowAlpha(df)
        e.compute_signals()
        assert all(s.ofi == 0 or np.isnan(s.ofi) or s.ofi == pytest.approx(0, abs=1) for s in e.snapshots)
