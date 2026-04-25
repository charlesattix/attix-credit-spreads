"""Tests for compass.sentiment_alpha — 32 tests."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from compass.sentiment_alpha import (
    SentimentAlpha, SentimentReading, ContrarianSignal,
    SentimentBacktestResult,
)


def _data(n=500, seed=42):
    return SentimentAlpha.generate_synthetic_data(n, seed)


# ===========================================================================
# Percentile computation
# ===========================================================================

class TestPercentile:
    def test_bounded(self):
        s = pd.Series(np.random.default_rng(42).normal(20, 5, 300))
        p = SentimentAlpha.rolling_percentile(s, 100)
        valid = p.dropna()
        assert valid.min() >= 0
        assert valid.max() <= 100

    def test_high_value_high_pctile(self):
        s = pd.Series(list(range(100)) + [999])
        p = SentimentAlpha.rolling_percentile(s, 100)
        assert p.iloc[-1] > 90

    def test_low_value_low_pctile(self):
        s = pd.Series([999] * 100 + [0])
        p = SentimentAlpha.rolling_percentile(s, 100)
        assert p.iloc[-1] < 10


# ===========================================================================
# Individual source percentiles
# ===========================================================================

class TestSourcePercentiles:
    def test_vix(self):
        vix, _, _, _ = _data(300)
        sa = SentimentAlpha(lookback=100)
        p = sa.vix_percentile(vix).dropna()
        assert len(p) > 100
        assert 0 <= p.min() and p.max() <= 100

    def test_put_call(self):
        _, pc, _, _ = _data(300)
        sa = SentimentAlpha(lookback=100)
        p = sa.put_call_percentile(pc).dropna()
        assert len(p) > 100

    def test_aaii(self):
        _, _, aaii, _ = _data(300)
        sa = SentimentAlpha(lookback=100)
        p = sa.aaii_percentile(aaii).dropna()
        assert len(p) > 100


# ===========================================================================
# Composite score
# ===========================================================================

class TestComposite:
    def test_bounded(self):
        vix, pc, aaii, _ = _data(400)
        sa = SentimentAlpha(lookback=100)
        vp = sa.vix_percentile(vix)
        pp = sa.put_call_percentile(pc)
        ap = sa.aaii_percentile(aaii)
        score = sa.composite_score(vp, pp, ap).dropna()
        assert score.min() >= 0
        assert score.max() <= 100

    def test_high_vix_low_score(self):
        """High VIX = fear → low composite (fear end)."""
        vp = pd.Series([95.0])   # high VIX percentile
        pp = pd.Series([90.0])   # high put-call
        ap = pd.Series([10.0])   # low AAII bullishness
        sa = SentimentAlpha()
        score = sa.composite_score(vp, pp, ap)
        assert float(score.iloc[0]) < 20  # should be fear territory

    def test_low_vix_high_score(self):
        """Low VIX = greed → high composite."""
        vp = pd.Series([5.0])
        pp = pd.Series([10.0])
        ap = pd.Series([90.0])
        sa = SentimentAlpha()
        score = sa.composite_score(vp, pp, ap)
        assert float(score.iloc[0]) > 80

    def test_custom_weights(self):
        sa = SentimentAlpha(weights={"vix": 1.0, "put_call": 0.0, "aaii": 0.0})
        vp = pd.Series([90.0])
        pp = pd.Series([50.0])
        ap = pd.Series([50.0])
        score = sa.composite_score(vp, pp, ap)
        assert float(score.iloc[0]) == pytest.approx(10.0)  # 100 - 90


# ===========================================================================
# Readings
# ===========================================================================

class TestReadings:
    def test_produces_readings(self):
        vix, pc, aaii, _ = _data(400)
        sa = SentimentAlpha(lookback=100)
        readings = sa.compute_readings(vix, pc, aaii)
        assert len(readings) > 100
        assert all(isinstance(r, SentimentReading) for r in readings)

    def test_score_in_readings(self):
        vix, pc, aaii, _ = _data(300)
        sa = SentimentAlpha(lookback=100)
        readings = sa.compute_readings(vix, pc, aaii)
        for r in readings:
            assert 0 <= r.composite_score <= 100


# ===========================================================================
# Contrarian signals
# ===========================================================================

class TestSignals:
    def test_generates_signals(self):
        vix, pc, aaii, _ = _data(500)
        sa = SentimentAlpha(lookback=100, fear_threshold=15, greed_threshold=85)
        readings = sa.compute_readings(vix, pc, aaii)
        signals = sa.generate_signals(readings)
        assert len(signals) == len(readings)

    def test_signal_values(self):
        vix, pc, aaii, _ = _data(500)
        sa = SentimentAlpha(lookback=100, fear_threshold=15, greed_threshold=85)
        readings = sa.compute_readings(vix, pc, aaii)
        signals = sa.generate_signals(readings)
        for s in signals:
            assert s.signal in (-1, 0, 1)
            assert 0 <= s.strength <= 1

    def test_fear_generates_buy(self):
        """Manually craft extreme fear reading → should get buy signal."""
        reading = SentimentReading(
            date=pd.Timestamp("2026-01-01"),
            vix_percentile=95, put_call_ratio=1.3, put_call_percentile=92,
            aaii_spread=-30, aaii_percentile=5, composite_score=5.0)
        sa = SentimentAlpha(fear_threshold=10, greed_threshold=90)
        signals = sa.generate_signals([reading])
        assert signals[0].signal == 1
        assert len(signals[0].drivers) > 0

    def test_greed_generates_sell(self):
        reading = SentimentReading(
            date=pd.Timestamp("2026-01-01"),
            vix_percentile=5, put_call_ratio=0.55, put_call_percentile=8,
            aaii_spread=35, aaii_percentile=95, composite_score=95.0)
        sa = SentimentAlpha(fear_threshold=10, greed_threshold=90)
        signals = sa.generate_signals([reading])
        assert signals[0].signal == -1

    def test_neutral_in_middle(self):
        reading = SentimentReading(
            date=pd.Timestamp("2026-01-01"),
            vix_percentile=50, put_call_ratio=0.85, put_call_percentile=50,
            aaii_spread=5, aaii_percentile=50, composite_score=50.0)
        sa = SentimentAlpha()
        signals = sa.generate_signals([reading])
        assert signals[0].signal == 0

    def test_signal_series(self):
        vix, pc, aaii, _ = _data(400)
        sa = SentimentAlpha(lookback=100)
        series = sa.signal_series(vix, pc, aaii)
        assert isinstance(series, pd.Series)
        assert set(series.dropna().unique()).issubset({-1.0, 0.0, 1.0})


# ===========================================================================
# Backtest
# ===========================================================================

class TestBacktest:
    def test_basic(self):
        vix, pc, aaii, rets = _data(500)
        sa = SentimentAlpha(lookback=100, fear_threshold=15, greed_threshold=85)
        result = sa.backtest(vix, pc, aaii, rets)
        assert isinstance(result, SentimentBacktestResult)
        assert result.n_signals >= 0

    def test_has_signals(self):
        vix, pc, aaii, rets = _data(800)
        sa = SentimentAlpha(lookback=100, fear_threshold=20, greed_threshold=80)
        result = sa.backtest(vix, pc, aaii, rets)
        assert result.n_signals > 0
        assert result.n_buys + result.n_sells == result.n_signals

    def test_win_rate_bounded(self):
        vix, pc, aaii, rets = _data(800)
        sa = SentimentAlpha(lookback=100, fear_threshold=20, greed_threshold=80)
        result = sa.backtest(vix, pc, aaii, rets)
        if result.n_signals > 0:
            assert 0 <= result.win_rate <= 1

    def test_short_data(self):
        vix = pd.Series([20.0] * 10)
        pc = pd.Series([0.85] * 10)
        aaii = pd.Series([5.0] * 10)
        rets = pd.Series([0.001] * 10)
        sa = SentimentAlpha()
        result = sa.backtest(vix, pc, aaii, rets)
        assert result.n_signals == 0


# ===========================================================================
# Synthetic data
# ===========================================================================

class TestSyntheticData:
    def test_shapes(self):
        vix, pc, aaii, rets = _data(500)
        assert len(vix) == 500
        assert len(pc) == 500
        assert len(aaii) == 500
        assert len(rets) == 500

    def test_vix_range(self):
        vix, _, _, _ = _data(1000)
        assert vix.min() >= 10
        assert vix.max() <= 80

    def test_put_call_range(self):
        _, pc, _, _ = _data(1000)
        assert pc.min() >= 0.4
        assert pc.max() <= 2.0


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        vix, pc, aaii, rets = _data(500)
        sa = SentimentAlpha(lookback=100, fear_threshold=20, greed_threshold=80)
        readings = sa.compute_readings(vix, pc, aaii)
        result = sa.backtest(vix, pc, aaii, rets)
        out = tmp_path / "sent.html"
        path = sa.generate_report(result, readings, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Sentiment" in html
        assert "<svg" in html
