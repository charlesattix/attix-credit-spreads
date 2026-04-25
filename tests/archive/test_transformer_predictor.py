"""Tests for compass/transformer_predictor.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.transformer_predictor import (
    FoldResult,
    PredictorResult,
    TransformerConfig,
    TransformerPredictor,
    TransformerWeights,
    causal_mask,
    compute_signal_sharpe,
    evaluate_weights,
    gelu,
    init_weights,
    layer_norm,
    multi_head_attention,
    perturb_weights,
    positional_encoding,
    prepare_features,
    sigmoid,
    softmax,
    train_evolutionary,
    transformer_forward,
)


def _make_ohlcv(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2020-01-02", periods=n)
    close = 400 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.003, n)),
        "high": close * (1 + rng.uniform(0.002, 0.015, n)),
        "low": close * (1 - rng.uniform(0.002, 0.015, n)),
        "close": close,
        "volume": rng.uniform(2e6, 5e6, n),
        "vix": 18 + rng.normal(0, 3, n),
    }, index=dates)


@pytest.fixture
def ohlcv():
    return _make_ohlcv()


@pytest.fixture
def cfg():
    return TransformerConfig(seq_len=10, d_model=16, n_heads=2, n_layers=2, d_ff=32, n_features=8)


# ── Component tests ──────────────────────────────────────────────────────


class TestComponents:
    def test_positional_encoding_shape(self):
        pe = positional_encoding(20, 64)
        assert pe.shape == (20, 64)

    def test_pe_bounded(self):
        pe = positional_encoding(20, 64)
        assert np.abs(pe).max() <= 1.01

    def test_causal_mask_shape(self):
        m = causal_mask(10)
        assert m.shape == (10, 10)

    def test_causal_mask_lower_tri(self):
        m = causal_mask(5)
        assert m[0, 1] == 0  # can't attend to future
        assert m[1, 0] == 1  # can attend to past
        assert m[4, 4] == 1  # can attend to self

    def test_softmax_sums_one(self):
        x = np.array([[1.0, 2.0, 3.0]])
        s = softmax(x)
        assert abs(s.sum() - 1.0) < 1e-6

    def test_gelu_shape(self):
        x = np.random.randn(10)
        assert gelu(x).shape == (10,)

    def test_sigmoid_bounded(self):
        x = np.array([-100, 0, 100])
        s = sigmoid(x)
        assert (s >= 0).all() and (s <= 1).all()

    def test_layer_norm_zero_mean(self):
        x = np.random.randn(5, 16)
        normed = layer_norm(x)
        assert abs(normed.mean(axis=-1)).max() < 1e-5


# ── Weight tests ─────────────────────────────────────────────────────────


class TestWeights:
    def test_init(self, cfg):
        w = init_weights(cfg)
        assert w.W_in.shape == (cfg.n_features, cfg.d_model)
        assert len(w.W_q) == cfg.n_layers
        assert w.W_out.shape == (cfg.d_model, 1)

    def test_perturb_changes(self, cfg):
        w1 = init_weights(cfg)
        w2 = perturb_weights(w1, cfg, scale=0.1, seed=99)
        assert not np.allclose(w1.W_in, w2.W_in)

    def test_perturb_small(self, cfg):
        w1 = init_weights(cfg)
        w2 = perturb_weights(w1, cfg, scale=0.001, seed=99)
        assert np.allclose(w1.W_in, w2.W_in, atol=0.01)


# ── Forward pass tests ───────────────────────────────────────────────────


class TestForward:
    def test_output_bounded(self, cfg):
        w = init_weights(cfg)
        x = np.random.randn(cfg.seq_len, cfg.n_features)
        prob = transformer_forward(x, w, cfg)
        assert 0.0 <= prob <= 1.0

    def test_deterministic(self, cfg):
        w = init_weights(cfg)
        x = np.random.RandomState(1).randn(cfg.seq_len, cfg.n_features)
        p1 = transformer_forward(x, w, cfg)
        p2 = transformer_forward(x, w, cfg)
        assert p1 == p2

    def test_different_inputs(self, cfg):
        w = init_weights(cfg)
        x1 = np.random.RandomState(1).randn(cfg.seq_len, cfg.n_features)
        x2 = np.random.RandomState(2).randn(cfg.seq_len, cfg.n_features)
        p1 = transformer_forward(x1, w, cfg)
        p2 = transformer_forward(x2, w, cfg)
        assert p1 != p2  # different inputs → different outputs


# ── Attention test ───────────────────────────────────────────────────────


class TestAttention:
    def test_shape(self, cfg):
        x = np.random.randn(cfg.seq_len, cfg.d_model)
        w = init_weights(cfg)
        mask = causal_mask(cfg.seq_len)
        out = multi_head_attention(x, w.W_q[0], w.W_k[0], w.W_v[0], w.W_o[0], cfg.n_heads, mask)
        assert out.shape == (cfg.seq_len, cfg.d_model)


# ── Feature preparation tests ────────────────────────────────────────────


class TestFeatures:
    def test_shape(self, ohlcv):
        X, y = prepare_features(ohlcv, seq_len=10)
        assert X.shape[1] == 10
        assert X.shape[2] == 8
        assert len(y) == len(X)

    def test_target_binary(self, ohlcv):
        _, y = prepare_features(ohlcv, seq_len=10)
        assert set(np.unique(y)).issubset({0.0, 1.0})

    def test_length(self, ohlcv):
        X, y = prepare_features(ohlcv, seq_len=10)
        # n - seq_len - 1 samples
        assert len(X) == len(ohlcv) - 10 - 1


# ── Training tests ───────────────────────────────────────────────────────


class TestTraining:
    def test_evaluate(self, cfg):
        w = init_weights(cfg)
        X = np.random.randn(20, cfg.seq_len, cfg.n_features)
        y = np.random.randint(0, 2, 20).astype(float)
        acc, loss = evaluate_weights(w, cfg, X, y)
        assert 0 <= acc <= 1
        assert loss >= 0

    def test_train_improves_or_maintains(self, cfg):
        X = np.random.RandomState(42).randn(50, cfg.seq_len, cfg.n_features)
        y = np.random.RandomState(42).randint(0, 2, 50).astype(float)
        initial = init_weights(cfg)
        acc_before, _ = evaluate_weights(initial, cfg, X[:30], y[:30])
        trained = train_evolutionary(X, y, cfg, n_iterations=5, population=3)
        acc_after, _ = evaluate_weights(trained, cfg, X[:30], y[:30])
        assert acc_after >= acc_before - 0.05  # should not get much worse


# ── Signal Sharpe test ───────────────────────────────────────────────────


class TestSignalSharpe:
    def test_sharpe_float(self):
        probs = np.array([0.6, 0.4, 0.7, 0.3, 0.8])
        returns = np.array([0.01, -0.005, 0.02, -0.01, 0.015])
        sh = compute_signal_sharpe(probs, returns)
        assert isinstance(sh, float)

    def test_sharpe_empty(self):
        assert compute_signal_sharpe(np.array([0.5, 0.5]), np.array([0.01, 0.01])) == 0.0


# ── Full predictor tests ─────────────────────────────────────────────────


class TestPredictor:
    def test_train_and_evaluate(self, ohlcv):
        cfg = TransformerConfig(seq_len=10, d_model=16, n_heads=2, n_layers=2, d_ff=32)
        tp = TransformerPredictor(cfg)
        r = tp.train_and_evaluate(ohlcv, n_folds=2, n_iterations=3)
        assert isinstance(r, PredictorResult)
        assert len(r.folds) > 0
        assert 0 <= r.avg_oos_accuracy <= 1

    def test_predict(self, cfg):
        tp = TransformerPredictor(cfg)
        w = init_weights(cfg)
        X = np.random.randn(5, cfg.seq_len, cfg.n_features)
        probs = tp.predict(X, w)
        assert len(probs) == 5
        assert all(0 <= p <= 1 for p in probs)

    def test_short_data(self):
        df = pd.DataFrame({"close": [100, 101], "open": [99, 100], "high": [102, 103],
                           "low": [98, 99], "volume": [1e6, 1e6]})
        tp = TransformerPredictor()
        r = tp.train_and_evaluate(df)
        assert r.avg_oos_accuracy == 0.5


# ── Report tests ─────────────────────────────────────────────────────────


class TestReport:
    def test_generates(self, ohlcv):
        cfg = TransformerConfig(seq_len=10, d_model=16, n_heads=2, n_layers=2, d_ff=32)
        r = TransformerPredictor(cfg).train_and_evaluate(ohlcv, n_folds=2, n_iterations=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "tf.html"
            path = TransformerPredictor.generate_report(r, out)
            assert path.exists()
            assert "Transformer" in path.read_text()

    def test_default_path(self, ohlcv):
        cfg = TransformerConfig(seq_len=10, d_model=16, n_heads=2, n_layers=2, d_ff=32)
        r = TransformerPredictor(cfg).train_and_evaluate(ohlcv, n_folds=2, n_iterations=3)
        path = TransformerPredictor.generate_report(r)
        assert path.exists()
        path.unlink(missing_ok=True)
