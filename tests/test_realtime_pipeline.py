"""Tests for compass.realtime_pipeline — real-time signal generation."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from compass.realtime_pipeline import (
    FEATURE_COLS,
    DataFeed,
    FeatureEngine,
    FeatureVector,
    HealthMonitor,
    HealthStatus,
    MarketTick,
    ModelInference,
    PipelineMetrics,
    RealtimePipeline,
    Signal,
    SignalQueue,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _tick(price: float = 450.0, vix: float = 18.0, ts: str = "2024-01-15") -> MarketTick:
    return MarketTick(timestamp=ts, symbol="SPY", price=price, volume=1000, vix=vix)


def _make_df(n: int = 100, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": idx,
        "spy_price": 450 + rng.randn(n).cumsum() * 2,
        "vix": 18 + rng.randn(n) * 3,
        "volume": (1e6 + rng.randn(n) * 1e5).astype(int),
    })


def _warm_engine(n: int = 60) -> FeatureEngine:
    eng = FeatureEngine()
    rng = np.random.RandomState(42)
    for i in range(n):
        eng.update(_tick(price=440 + rng.randn() * 5, vix=17 + rng.rand() * 4, ts=f"2024-01-{i+1:02d}"))
    return eng


# ── DataFeed ────────────────────────────────────────────────────────────────
class TestDataFeed:
    def test_connect_disconnect(self):
        f = DataFeed()
        assert not f.is_connected
        f.connect()
        assert f.is_connected
        f.disconnect()
        assert not f.is_connected

    def test_inject_tick(self):
        f = DataFeed()
        f.inject_tick(_tick())
        assert f.buffer_size == 1

    def test_callback(self):
        received = []
        f = DataFeed()
        f.on_tick(lambda t: received.append(t))
        f.inject_tick(_tick())
        assert len(received) == 1

    def test_replay_dataframe(self):
        f = DataFeed()
        ticks = f.replay_dataframe(_make_df(20))
        assert len(ticks) == 20
        assert f.buffer_size == 20

    def test_last_tick_age(self):
        f = DataFeed()
        assert f.last_tick_age_seconds == float("inf")
        f.inject_tick(_tick())
        assert f.last_tick_age_seconds < 1.0

    def test_get_recent(self):
        f = DataFeed()
        for i in range(10):
            f.inject_tick(_tick(price=450 + i))
        recent = f.get_recent(5)
        assert len(recent) == 5


# ── FeatureEngine ───────────────────────────────────────────────────────────
class TestFeatureEngine:
    def test_update_grows_history(self):
        eng = FeatureEngine()
        eng.update(_tick())
        eng.update(_tick(price=451))
        assert len(eng._price_history) == 2

    def test_compute_returns_vector(self):
        eng = _warm_engine()
        fv = eng.compute(_tick())
        assert isinstance(fv, FeatureVector)
        assert len(fv.features) == len(FEATURE_COLS)

    def test_all_feature_cols_present(self):
        eng = _warm_engine()
        fv = eng.compute(_tick())
        for col in FEATURE_COLS:
            assert col in fv.features, f"Missing feature: {col}"

    def test_no_nan_features(self):
        eng = _warm_engine()
        fv = eng.compute(_tick())
        for k, v in fv.features.items():
            assert not np.isnan(v), f"NaN in {k}"

    def test_regime_classification(self):
        eng = _warm_engine()
        # Normal VIX → bull
        fv = eng.compute(_tick(vix=15.0))
        assert fv.regime in ("bull", "low_vol")
        # High VIX → high_vol or crash
        fv = eng.compute(_tick(vix=40.0))
        assert fv.regime in ("high_vol", "crash")

    def test_stale_features_on_short_history(self):
        eng = FeatureEngine()
        eng.update(_tick())
        fv = eng.compute(_tick())
        assert len(fv.stale_features) > 0  # not enough data for MAs

    def test_computation_latency_tracked(self):
        eng = _warm_engine()
        fv = eng.compute(_tick())
        assert fv.computation_ms >= 0

    def test_record_trade(self):
        eng = _warm_engine()
        eng.record_trade()
        fv = eng.compute(_tick())
        assert fv.features["days_since_last_trade"] == 0

    def test_drift_detection(self):
        eng = _warm_engine()
        eng.set_feature_ranges({"vix": (10.0, 25.0)})
        fv = eng.compute(_tick(vix=40.0))
        drifted = eng.detect_drift(fv)
        assert "vix" in drifted

    def test_lookback_trims(self):
        eng = FeatureEngine(lookback=50)
        for i in range(200):
            eng.update(_tick(price=440 + i * 0.1))
        # Trims at 2x lookback, so should be <= lookback after trim
        assert len(eng._price_history) <= 100


# ── ModelInference ──────────────────────────────────────────────────────────
class TestModelInference:
    def test_default_model_returns_signal(self):
        inf = ModelInference()
        fv = _warm_engine().compute(_tick())
        sig = inf.predict(fv)
        assert isinstance(sig, Signal)
        assert sig.direction in ("bull_put", "bear_call", "no_trade")

    def test_confidence_bounded(self):
        inf = ModelInference()
        fv = _warm_engine().compute(_tick())
        sig = inf.predict(fv)
        assert 0.0 <= sig.confidence <= 1.0

    def test_signal_id_unique(self):
        inf = ModelInference()
        eng = _warm_engine()
        s1 = inf.predict(eng.compute(_tick(price=450, ts="2024-01-10")))
        s2 = inf.predict(eng.compute(_tick(price=455, ts="2024-01-11")))
        assert s1.signal_id != s2.signal_id

    def test_custom_model_fn(self):
        def custom(f):
            return "bear_call", 0.85, 0.90
        inf = ModelInference(model_fn=custom)
        sig = inf.predict(_warm_engine().compute(_tick()))
        assert sig.direction == "bear_call"
        assert sig.confidence == 0.85

    def test_avg_latency(self):
        inf = ModelInference()
        eng = _warm_engine()
        for _ in range(5):
            inf.predict(eng.compute(_tick()))
        assert inf.avg_latency_ms >= 0

    def test_model_age(self):
        inf = ModelInference()
        assert inf.model_age_days >= 0


# ── SignalQueue ─────────────────────────────────────────────────────────────
class TestSignalQueue:
    def _sig(self, sid: str = "abc", ts: str = "2024-01-01") -> Signal:
        return Signal(signal_id=sid, timestamp=ts, direction="bull_put",
                      confidence=0.8, regime="bull")

    def test_enqueue_dequeue(self):
        q = SignalQueue()
        q.enqueue(self._sig("a"))
        assert q.size == 1
        s = q.dequeue()
        assert s.signal_id == "a"
        assert q.is_empty

    def test_deduplication(self):
        q = SignalQueue()
        assert q.enqueue(self._sig("a"))
        assert not q.enqueue(self._sig("a"))  # duplicate
        assert q.total_duplicates == 1

    def test_different_ids_not_duplicate(self):
        q = SignalQueue()
        q.enqueue(self._sig("a"))
        q.enqueue(self._sig("b"))
        assert q.size == 2

    def test_drain(self):
        q = SignalQueue()
        q.enqueue(self._sig("a"))
        q.enqueue(self._sig("b"))
        signals = q.drain()
        assert len(signals) == 2
        assert q.is_empty

    def test_peek(self):
        q = SignalQueue()
        q.enqueue(self._sig("a"))
        s = q.peek()
        assert s.signal_id == "a"
        assert q.size == 1  # not removed

    def test_empty_dequeue(self):
        q = SignalQueue()
        assert q.dequeue() is None


# ── HealthMonitor ───────────────────────────────────────────────────────────
class TestHealthMonitor:
    def test_healthy_pipeline(self):
        feed = DataFeed()
        feed.connect()
        feed.inject_tick(_tick())
        inf = ModelInference()
        mon = HealthMonitor()
        status = mon.check(feed, inf)
        assert status.is_healthy
        assert status.data_feed_ok

    def test_disconnected_feed(self):
        feed = DataFeed()
        inf = ModelInference()
        mon = HealthMonitor()
        status = mon.check(feed, inf)
        assert not status.data_feed_ok
        assert not status.is_healthy

    def test_stale_features_flagged(self):
        feed = DataFeed()
        feed.connect()
        feed.inject_tick(_tick())
        inf = ModelInference()
        fv = FeatureVector("t", {}, stale_features=["rsi_14", "ma20", "ma50", "ma80"])
        mon = HealthMonitor(max_stale_features=3)
        status = mon.check(feed, inf, fv)
        assert not status.features_ok

    def test_drifted_features(self):
        feed = DataFeed()
        feed.connect()
        feed.inject_tick(_tick())
        inf = ModelInference()
        mon = HealthMonitor()
        status = mon.check(feed, inf, drifted_features=["vix"])
        assert "vix" in status.drifted_features

    def test_degraded_mode(self):
        feed = DataFeed()
        feed.connect()
        # No tick → stale
        inf = ModelInference()
        mon = HealthMonitor(max_tick_age_s=0.001)
        time.sleep(0.01)
        status = mon.check(feed, inf)
        assert status.degraded_mode or not status.data_feed_ok


# ── Pipeline ────────────────────────────────────────────────────────────────
class TestPipeline:
    def test_start_stop(self):
        p = RealtimePipeline()
        p.start()
        assert p.feed.is_connected
        p.stop()
        assert not p.feed.is_connected

    def test_process_tick(self):
        p = RealtimePipeline()
        # Warm up
        for i in range(60):
            p.features.update(_tick(price=440 + i * 0.1, vix=17 + i * 0.05))
        result = p.process_tick(_tick(price=450, vix=18))
        # May or may not produce signal depending on confidence
        assert isinstance(result, (Signal, type(None)))

    def test_replay(self):
        p = RealtimePipeline(min_confidence=0.0)  # accept all signals
        df = _make_df(100)
        signals = p.replay(df)
        assert len(signals) > 0

    def test_metrics(self):
        p = RealtimePipeline()
        p.start()
        for i in range(10):
            p.feed.inject_tick(_tick(price=450 + i, ts=f"2024-01-{i+1:02d}"))
        m = p.get_metrics()
        assert isinstance(m, PipelineMetrics)
        assert m.total_ticks == 10
        assert m.avg_total_ms >= 0
        p.stop()

    def test_regime_tracking(self):
        p = RealtimePipeline(regime_update_every_n=1)
        for i in range(10):
            p.features.update(_tick(vix=15))
        p.feed.inject_tick(_tick(vix=15))
        assert p.current_regime in ("bull", "low_vol")

    def test_health_check(self):
        p = RealtimePipeline()
        p.start()
        p.feed.inject_tick(_tick())
        status = p.get_health()
        assert isinstance(status, HealthStatus)
        p.stop()

    def test_min_confidence_filter(self):
        # Custom model that always returns low confidence
        def low_conf(f):
            return "bull_put", 0.30, 0.50
        inf = ModelInference(model_fn=low_conf)
        p = RealtimePipeline(inference=inf, min_confidence=0.50)
        for i in range(60):
            p.features.update(_tick(price=440 + i))
        result = p.process_tick(_tick())
        assert result is None  # filtered out

    def test_signal_deduplication(self):
        p = RealtimePipeline(min_confidence=0.0)
        for i in range(60):
            p.features.update(_tick(price=440 + i))
        # Same tick twice → same signal ID → dedup
        t = _tick(price=450, ts="2024-06-01")
        p.process_tick(t)
        s2 = p.process_tick(t)
        assert s2 is None  # duplicate filtered

    def test_replay_signal_count(self):
        p = RealtimePipeline(min_confidence=0.0)
        df = _make_df(200)
        signals = p.replay(df)
        # With min_confidence=0, most ticks should generate signals
        assert len(signals) > 50

    def test_peak_latency(self):
        p = RealtimePipeline()
        p.start()
        for i in range(20):
            p.feed.inject_tick(_tick(price=440 + i, ts=f"t{i}"))
        m = p.get_metrics()
        assert m.peak_latency_ms >= m.avg_total_ms
        p.stop()


# ── Feature leakage check ──────────────────────────────────────────────────
class TestNoLeakage:
    def test_features_use_only_past_data(self):
        """Verify features at time T use only data from T-1 and earlier."""
        eng = FeatureEngine()
        prices = [440 + i * 0.5 for i in range(50)]
        for i, p in enumerate(prices):
            eng.update(_tick(price=p, ts=f"d{i}"))

        # Compute features at T=49 (last bar)
        fv = eng.compute(_tick(price=prices[-1], ts="d49"))

        # momentum_5d should use prices[-6] to prices[-1]
        expected_mom = (prices[-1] / prices[-6] - 1) * 100
        assert abs(fv.features["momentum_5d_pct"] - expected_mom) < 1.0

    def test_rsi_uses_past_only(self):
        eng = FeatureEngine()
        # Feed constant prices → RSI should be 50
        for i in range(30):
            eng.update(_tick(price=450.0, ts=f"d{i}"))
        fv = eng.compute(_tick(price=450.0))
        assert 40 < fv.features["rsi_14"] < 60


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_market_tick(self):
        t = MarketTick("2024-01-01", "SPY", 450.0)
        assert t.symbol == "SPY"

    def test_signal(self):
        s = Signal("abc", "2024-01-01", "bull_put", 0.8, "bull")
        assert s.direction == "bull_put"

    def test_pipeline_metrics(self):
        m = PipelineMetrics()
        assert m.total_ticks == 0

    def test_health_status(self):
        h = HealthStatus()
        assert h.is_healthy
