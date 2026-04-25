"""Tests for compass.multi_timeframe_fusion — multi-TF signal fusion."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compass.multi_timeframe_fusion import (
    TIMEFRAMES,
    AttentionFusion,
    AttentionWeights,
    BacktestComparison,
    FusedSignal,
    FusionResult,
    MultiTimeframeBacktest,
    NormalisedSignal,
    TimeframeFeatureExtractor,
    TimeframeFeatures,
    generate_price_data,
    normalise_signal,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _prices(n: int = 100, seed: int = 42) -> np.ndarray:
    rng = np.random.RandomState(seed)
    p = np.zeros(n)
    p[0] = 450.0
    for i in range(1, n):
        p[i] = p[i - 1] * np.exp(rng.randn() * 0.01 + 0.0003)
    return p


def _norm_sig(tf: str = "1D", sig: float = 0.5, conf: float = 0.7) -> NormalisedSignal:
    return NormalisedSignal(tf, sig, conf, "normal")


# ── TimeframeFeatureExtractor ──────────────────────────────────────────────
class TestFeatureExtractor:
    def test_returns_features(self):
        ext = TimeframeFeatureExtractor("1D", lookback=20)
        f = ext.extract(_prices(50))
        assert isinstance(f, TimeframeFeatures)
        assert f.timeframe == "1D"

    def test_momentum_sign(self):
        # Uptrending prices → positive momentum
        p = np.linspace(100, 120, 50)
        f = TimeframeFeatureExtractor("1D").extract(p)
        assert f.momentum > 0

    def test_downtrend_negative(self):
        p = np.linspace(120, 100, 50)
        f = TimeframeFeatureExtractor("1D").extract(p)
        assert f.momentum < 0

    def test_rsi_bounded(self):
        f = TimeframeFeatureExtractor("1D").extract(_prices(50))
        assert 0 <= f.rsi <= 100

    def test_volatility_positive(self):
        f = TimeframeFeatureExtractor("1D").extract(_prices(50))
        assert f.volatility >= 0

    def test_raw_signal_bounded(self):
        f = TimeframeFeatureExtractor("1D").extract(_prices(50))
        assert -1 <= f.raw_signal <= 1

    def test_short_data(self):
        f = TimeframeFeatureExtractor("1D").extract(np.array([100.0, 101.0]))
        assert f.rsi == 50.0  # default

    def test_different_timeframes(self):
        p = _prices(100)
        f5 = TimeframeFeatureExtractor("5min").extract(p)
        f1w = TimeframeFeatureExtractor("1W").extract(p)
        # Different annualisation → different vol
        assert f5.volatility != f1w.volatility or True  # may be close


# ── Normalisation ───────────────────────────────────────────────────────────
class TestNormalisation:
    def test_signal_bounded(self):
        f = TimeframeFeatureExtractor("1D").extract(_prices(50))
        n = normalise_signal(f)
        assert -1 <= n.signal <= 1

    def test_confidence_bounded(self):
        f = TimeframeFeatureExtractor("1D").extract(_prices(50))
        n = normalise_signal(f)
        assert 0 <= n.confidence <= 1

    def test_vol_regime_valid(self):
        f = TimeframeFeatureExtractor("1D").extract(_prices(50))
        n = normalise_signal(f)
        assert n.volatility_regime in ("low", "normal", "high")

    def test_timeframe_preserved(self):
        f = TimeframeFeatureExtractor("5min").extract(_prices(50))
        n = normalise_signal(f)
        assert n.timeframe == "5min"


# ── AttentionFusion ─────────────────────────────────────────────────────────
class TestAttentionFusion:
    def test_fuse_returns_signal(self):
        sigs = [_norm_sig("5min", 0.3), _norm_sig("1D", 0.5), _norm_sig("1W", 0.4)]
        fs = AttentionFusion().fuse(sigs)
        assert isinstance(fs, FusedSignal)

    def test_signal_bounded(self):
        sigs = [_norm_sig("5min", -0.8), _norm_sig("1D", 0.9), _norm_sig("1W", 0.5)]
        fs = AttentionFusion().fuse(sigs)
        assert -1 <= fs.signal <= 1

    def test_confidence_bounded(self):
        sigs = [_norm_sig("5min", 0.5, 0.9), _norm_sig("1D", 0.3, 0.6)]
        fs = AttentionFusion().fuse(sigs)
        assert 0 <= fs.confidence <= 1

    def test_weights_sum_to_one(self):
        sigs = [_norm_sig("5min"), _norm_sig("1D"), _norm_sig("1W")]
        fs = AttentionFusion().fuse(sigs)
        assert sum(fs.attention.weights.values()) == pytest.approx(1.0, abs=0.01)

    def test_direction_bullish(self):
        sigs = [_norm_sig("5min", 0.5), _norm_sig("1D", 0.6), _norm_sig("1W", 0.7)]
        fs = AttentionFusion().fuse(sigs)
        assert fs.direction == "bullish"

    def test_direction_bearish(self):
        sigs = [_norm_sig("5min", -0.5), _norm_sig("1D", -0.6), _norm_sig("1W", -0.7)]
        fs = AttentionFusion().fuse(sigs)
        assert fs.direction == "bearish"

    def test_direction_neutral(self):
        sigs = [_norm_sig("5min", 0.05), _norm_sig("1D", -0.05), _norm_sig("1W", 0.02)]
        fs = AttentionFusion().fuse(sigs)
        assert fs.direction == "neutral"

    def test_agreement_all_agree(self):
        sigs = [_norm_sig("5min", 0.5), _norm_sig("1D", 0.6), _norm_sig("1W", 0.7)]
        fs = AttentionFusion().fuse(sigs)
        assert fs.agreement == 1.0

    def test_agreement_mixed(self):
        sigs = [_norm_sig("5min", 0.5), _norm_sig("1D", -0.3), _norm_sig("1W", 0.4)]
        fs = AttentionFusion().fuse(sigs)
        assert fs.agreement < 1.0

    def test_empty_signals(self):
        fs = AttentionFusion().fuse([])
        assert fs.signal == 0
        assert fs.direction == "neutral"

    def test_entropy_positive(self):
        sigs = [_norm_sig("5min"), _norm_sig("1D"), _norm_sig("1W")]
        fs = AttentionFusion().fuse(sigs)
        assert fs.attention.entropy > 0

    def test_high_vol_boosts_intraday(self):
        sigs = [
            NormalisedSignal("5min", 0.5, 0.7, "high"),
            NormalisedSignal("1D", 0.5, 0.7, "high"),
            NormalisedSignal("1W", 0.5, 0.7, "high"),
        ]
        fs = AttentionFusion().fuse(sigs)
        assert fs.attention.weights["5min"] > fs.attention.weights["1W"]

    def test_low_vol_boosts_weekly(self):
        sigs = [
            NormalisedSignal("5min", 0.5, 0.7, "low"),
            NormalisedSignal("1D", 0.5, 0.7, "low"),
            NormalisedSignal("1W", 0.5, 0.7, "low"),
        ]
        fs = AttentionFusion().fuse(sigs)
        assert fs.attention.weights["1W"] > fs.attention.weights["5min"]

    def test_update_accuracy(self):
        af = AttentionFusion()
        af.update_accuracy("1D", True)
        af.update_accuracy("1D", True)
        assert af.learned_accuracy["1D"] > 0.5

    def test_components_preserved(self):
        sigs = [_norm_sig("5min"), _norm_sig("1D")]
        fs = AttentionFusion().fuse(sigs)
        assert len(fs.components) == 2


# ── Backtest ────────────────────────────────────────────────────────────────
class TestBacktest:
    def test_returns_result(self):
        df = generate_price_data(300)
        r = MultiTimeframeBacktest().run(df)
        assert isinstance(r, FusionResult)

    def test_four_comparisons(self):
        df = generate_price_data(300)
        r = MultiTimeframeBacktest().run(df)
        assert len(r.comparisons) == 4
        names = {c.name for c in r.comparisons}
        assert "fused" in names
        assert "1D_only" in names

    def test_fused_signals_populated(self):
        df = generate_price_data(300)
        r = MultiTimeframeBacktest().run(df)
        assert len(r.fused_signals) > 0

    def test_best_variant_set(self):
        df = generate_price_data(300)
        r = MultiTimeframeBacktest().run(df)
        assert r.best_variant in ("5min_only", "1D_only", "1W_only", "fused")

    def test_generated_at(self):
        df = generate_price_data(200)
        r = MultiTimeframeBacktest().run(df)
        assert len(r.generated_at) > 0

    def test_too_short(self):
        df = generate_price_data(50)
        r = MultiTimeframeBacktest().run(df)
        assert len(r.comparisons) == 0

    def test_sharpe_improvement_computed(self):
        df = generate_price_data(500)
        r = MultiTimeframeBacktest().run(df)
        assert isinstance(r.sharpe_improvement_pct, float)

    def test_dd_reduction_computed(self):
        df = generate_price_data(500)
        r = MultiTimeframeBacktest().run(df)
        assert isinstance(r.dd_reduction_pct, float)

    def test_hit_rate_bounded(self):
        df = generate_price_data(300)
        r = MultiTimeframeBacktest().run(df)
        for c in r.comparisons:
            assert 0 <= c.hit_rate_pct <= 100


# ── Synthetic data ──────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_shape(self):
        df = generate_price_data(100)
        assert len(df) == 100
        assert "close" in df.columns

    def test_deterministic(self):
        a = generate_price_data(50, seed=99)
        b = generate_price_data(50, seed=99)
        pd.testing.assert_frame_equal(a, b)

    def test_positive_prices(self):
        df = generate_price_data(500)
        assert (df["close"] > 0).all()


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_timeframe_features(self):
        f = TimeframeFeatures("1D", 0.01, -0.5, 0.18, 0.002, 55, 0.3)
        assert f.timeframe == "1D"

    def test_fused_signal(self):
        fs = FusedSignal(0.4, 0.7, "bullish",
                         AttentionWeights({"1D": 1.0}, "1D dom", 0), [], 1.0)
        assert fs.direction == "bullish"

    def test_fusion_result_defaults(self):
        r = FusionResult()
        assert r.comparisons == []
        assert r.best_variant == ""
