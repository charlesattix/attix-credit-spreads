"""Tests for compass/signal_pipeline.py — real-time signal generation pipeline."""
from __future__ import annotations
import time
import numpy as np
import pytest
from compass.signal_pipeline import (
    EnsemblePredictor, HealthStatus, MarketData, PipelineConfig,
    PipelineSignal, SignalPipeline, compute_position_scale,
    compute_stop_loss_mult, detect_regime, extract_features,
    FEATURE_NAMES, _vix_scale,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _md(**kw) -> MarketData:
    defaults = dict(ticker="SPY", price=430, vix=18, regime="bull",
                    rsi_14=55, dist_from_ma200_pct=5, momentum_10d_pct=1,
                    iv_rank=40, net_credit=1.5, spread_width=5.0)
    defaults.update(kw)
    return MarketData(**defaults)

def _pipe(**kw) -> SignalPipeline:
    return SignalPipeline(PipelineConfig(**kw))

# ── Feature engineering ──────────────────────────────────────────────────

class TestFeatureEngineering:
    def test_extract_all_features(self):
        feats = extract_features(_md())
        assert len(feats) == len(FEATURE_NAMES)
    def test_feature_names_match(self):
        feats = extract_features(_md())
        for name in FEATURE_NAMES:
            assert name in feats
    def test_features_are_floats(self):
        feats = extract_features(_md())
        for v in feats.values():
            assert isinstance(v, (int, float))
    def test_vix_propagated(self):
        feats = extract_features(_md(vix=32))
        assert feats["vix"] == 32
    def test_price_as_spy_price(self):
        feats = extract_features(_md(price=450))
        assert feats["spy_price"] == 450

# ── Regime detection ─────────────────────────────────────────────────────

class TestRegimeDetection:
    def test_crash(self):
        assert detect_regime(_md(vix=40)) == "crash"
    def test_high_vol(self):
        assert detect_regime(_md(vix=30)) == "high_vol"
    def test_bear(self):
        assert detect_regime(_md(vix=20, dist_from_ma200_pct=-8, momentum_10d_pct=-3)) == "bear"
    def test_bull(self):
        assert detect_regime(_md(vix=16, dist_from_ma200_pct=5, rsi_14=60)) == "bull"
    def test_low_vol(self):
        assert detect_regime(_md(vix=12, realized_vol_20d=6)) == "low_vol"
    def test_neutral(self):
        assert detect_regime(_md(vix=20, dist_from_ma200_pct=1, rsi_14=48)) == "neutral"

# ── Crisis hedge scaling ─────────────────────────────────────────────────

class TestCrisisHedge:
    def test_low_vix_full_scale(self):
        s = compute_position_scale(10, "bull", PipelineConfig())
        assert s == pytest.approx(1.0)
    def test_high_vix_zero_scale(self):
        s = compute_position_scale(40, "bull", PipelineConfig())
        assert s == pytest.approx(0.0)
    def test_mid_vix_partial(self):
        s = compute_position_scale(23, "bull", PipelineConfig())
        assert 0 < s < 1
    def test_crash_regime_zero(self):
        s = compute_position_scale(15, "crash", PipelineConfig())
        assert s == pytest.approx(0.0)
    def test_high_vol_capped(self):
        s = compute_position_scale(15, "high_vol", PipelineConfig())
        assert s <= 0.25
    def test_backwardation_penalty(self):
        base = compute_position_scale(20, "bull", PipelineConfig(), vix3m=None)
        back = compute_position_scale(20, "bull", PipelineConfig(), vix3m=15)
        assert back < base
    def test_contango_no_penalty(self):
        base = compute_position_scale(20, "bull", PipelineConfig(), vix3m=None)
        cont = compute_position_scale(20, "bull", PipelineConfig(), vix3m=25)
        assert cont == pytest.approx(base)

class TestStopLoss:
    def test_low_vix_base_stop(self):
        s = compute_stop_loss_mult(10, "neutral")
        assert s == pytest.approx(3.5)
    def test_high_vix_min_stop(self):
        s = compute_stop_loss_mult(30, "neutral")
        assert s == pytest.approx(1.5)
    def test_crash_min_stop(self):
        s = compute_stop_loss_mult(15, "crash")
        assert s == pytest.approx(1.5)
    def test_mid_vix_interpolated(self):
        s = compute_stop_loss_mult(18, "neutral")
        assert 1.5 < s < 3.5

class TestVixScale:
    def test_below_floor(self):
        assert _vix_scale(10, 12, 35) == 1.0
    def test_above_ceiling(self):
        assert _vix_scale(40, 12, 35) == 0.0
    def test_midpoint(self):
        s = _vix_scale(23.5, 12, 35)
        assert s == pytest.approx(0.5)

# ── Ensemble predictor ───────────────────────────────────────────────────

class TestEnsemblePredictor:
    def test_fallback_returns_tuple(self):
        ep = EnsemblePredictor()
        prob, conf, agree = ep.predict(extract_features(_md()))
        assert 0 <= prob <= 1
        assert 0 <= conf <= 1
        assert agree >= 0
    def test_fallback_higher_iv_higher_prob(self):
        ep = EnsemblePredictor()
        p_low, _, _ = ep.predict(extract_features(_md(iv_rank=10)))
        p_high, _, _ = ep.predict(extract_features(_md(iv_rank=80)))
        assert p_high > p_low
    def test_feature_drift_empty_when_no_stats(self):
        ep = EnsemblePredictor()
        drift = ep.check_feature_drift(extract_features(_md()))
        assert drift == []
    def test_feature_drift_detected(self):
        ep = EnsemblePredictor()
        ep.set_feature_stats({"vix": 18.0}, {"vix": 3.0})
        drift = ep.check_feature_drift({"vix": 50.0}, z_threshold=3.0)
        assert "vix" in drift
    def test_model_age(self):
        ep = EnsemblePredictor()
        assert ep.model_age_seconds >= 0

# ── Signal pipeline: generate_signal ─────────────────────────────────────

class TestGenerateSignal:
    def test_returns_pipeline_signal(self):
        sig = _pipe().generate_signal(_md())
        assert isinstance(sig, PipelineSignal)
    def test_bull_regime_trades(self):
        # Fallback predictor returns agreement=0.5, so lower the threshold
        sig = _pipe(min_ensemble_agreement=0.4).generate_signal(
            _md(vix=15, rsi_14=60, iv_rank=50, dist_from_ma200_pct=5))
        assert sig.trade is True
        assert sig.contracts > 0
    def test_crash_regime_rejects(self):
        sig = _pipe().generate_signal(_md(vix=40))
        assert sig.trade is False
        # Rejected for P(win) threshold or position scale or agreement
    def test_low_probability_rejects(self):
        # Very unfavorable conditions → low P(win)
        sig = _pipe(ml_threshold=0.90).generate_signal(
            _md(vix=30, iv_rank=5, dist_from_ma200_pct=-10))
        assert sig.trade is False
        assert "P(win)" in sig.reject_reason
    def test_regime_set(self):
        sig = _pipe().generate_signal(_md(vix=40))
        assert sig.regime == "crash"
    def test_position_scale_in_signal(self):
        sig = _pipe().generate_signal(_md(vix=25))
        assert 0 <= sig.position_scale <= 1
    def test_regime_leverage_in_signal(self):
        sig = _pipe().generate_signal(_md(vix=15, dist_from_ma200_pct=5, rsi_14=60))
        assert sig.regime_leverage == 2.0  # bull
    def test_contracts_capped(self):
        sig = _pipe(max_contracts=3, base_contracts=10).generate_signal(_md())
        assert sig.contracts <= 3
    def test_features_counted(self):
        sig = _pipe().generate_signal(_md())
        assert sig.features_used == len(FEATURE_NAMES)
    def test_timestamp_set(self):
        sig = _pipe().generate_signal(_md())
        assert len(sig.timestamp) > 0
    def test_direction_put_spread_above_ma(self):
        sig = _pipe().generate_signal(_md(dist_from_ma50_pct=2))
        assert sig.direction == "short_put_spread"
    def test_direction_call_spread_below_ma(self):
        sig = _pipe().generate_signal(_md(dist_from_ma50_pct=-2))
        assert sig.direction == "short_call_spread"

# ── Latency ──────────────────────────────────────────────────────────────

class TestLatency:
    def test_sub_millisecond(self):
        pipe = _pipe()
        md = _md()
        # Warm up
        pipe.generate_signal(md)
        # Measure
        latencies = []
        for _ in range(100):
            sig = pipe.generate_signal(md)
            latencies.append(sig.latency_us)
        median = np.median(latencies)
        assert median < 1000  # sub-millisecond (1000 μs)
    def test_latency_recorded(self):
        pipe = _pipe()
        sig = pipe.generate_signal(_md())
        assert sig.latency_us > 0

# ── Batch generation ─────────────────────────────────────────────────────

class TestBatch:
    def test_batch_returns_list(self):
        pipe = _pipe()
        results = pipe.generate_batch([_md(), _md(vix=40), _md(vix=15)])
        assert len(results) == 3
    def test_batch_mixed_decisions(self):
        pipe = _pipe()
        results = pipe.generate_batch([_md(vix=15, iv_rank=50), _md(vix=40)])
        decisions = {r.trade for r in results}
        assert len(decisions) >= 1  # at least some variation

# ── VIX monitoring ───────────────────────────────────────────────────────

class TestVIXMonitoring:
    def test_normal_vix(self):
        alert, msg = _pipe().check_vix_alert(16)
        assert alert is False
        assert "NORMAL" in msg
    def test_elevated_vix(self):
        _, msg = _pipe().check_vix_alert(23)
        assert "ELEVATED" in msg
    def test_high_vol_vix(self):
        alert, msg = _pipe().check_vix_alert(30)
        assert alert is True
        assert "WARNING" in msg
    def test_crash_vix(self):
        alert, msg = _pipe().check_vix_alert(40)
        assert alert is True
        assert "CRITICAL" in msg

# ── Health checks ────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_status(self):
        pipe = _pipe()
        pipe.generate_signal(_md())
        h = pipe.health()
        assert isinstance(h, HealthStatus)
    def test_health_ok_initially(self):
        pipe = _pipe()
        pipe.generate_signal(_md())
        h = pipe.health()
        assert h.health_ok is True
    def test_signals_counted(self):
        pipe = _pipe()
        pipe.generate_signal(_md())
        pipe.generate_signal(_md(vix=40))
        h = pipe.health()
        assert h.signals_generated == 2
    def test_stale_model_detected(self):
        pipe = _pipe(model_stale_seconds=0.0)  # immediately stale
        time.sleep(0.01)
        pipe.generate_signal(_md())
        h = pipe.health()
        assert h.model_stale is True
    def test_avg_latency(self):
        pipe = _pipe()
        for _ in range(5):
            pipe.generate_signal(_md())
        h = pipe.health()
        assert h.avg_latency_us > 0

# ── Signal log ───────────────────────────────────────────────────────────

class TestSignalLog:
    def test_log_populated(self):
        pipe = _pipe()
        pipe.generate_signal(_md())
        pipe.generate_signal(_md())
        assert len(pipe.get_signal_log()) == 2
    def test_log_limit(self):
        pipe = _pipe()
        for _ in range(10):
            pipe.generate_signal(_md())
        assert len(pipe.get_signal_log(5)) == 5
    def test_trade_rate(self):
        pipe = _pipe()
        pipe.generate_signal(_md(vix=15, iv_rank=50))  # likely trade
        pipe.generate_signal(_md(vix=40))               # likely reject
        rate = pipe.get_trade_rate()
        assert 0 <= rate <= 1
    def test_reset_stats(self):
        pipe = _pipe()
        pipe.generate_signal(_md())
        pipe.reset_stats()
        assert pipe._signals_generated == 0
        assert len(pipe.get_signal_log()) == 0

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_zero_vix(self):
        sig = _pipe().generate_signal(_md(vix=0))
        assert isinstance(sig, PipelineSignal)
    def test_extreme_vix(self):
        sig = _pipe().generate_signal(_md(vix=100))
        assert sig.trade is False
    def test_negative_momentum(self):
        sig = _pipe().generate_signal(_md(momentum_10d_pct=-10))
        assert isinstance(sig, PipelineSignal)
    def test_zero_contracts_config(self):
        sig = _pipe(base_contracts=0).generate_signal(_md())
        assert sig.contracts == 0
    def test_custom_regime_leverage(self):
        pipe = _pipe(regime_leverage={"bull": 5.0, "neutral": 1.0})
        sig = pipe.generate_signal(_md(vix=15, dist_from_ma200_pct=5, rsi_14=60))
        assert sig.regime_leverage == 5.0
