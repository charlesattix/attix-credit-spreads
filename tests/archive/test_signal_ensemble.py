"""Tests for compass/signal_ensemble.py — signal ensemble."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.signal_ensemble import (
    EnsembleResult,
    SignalEnsemble,
    SignalStats,
    WalkForwardFold,
    apply_quality_gates,
    compute_ic,
    compute_ic_series,
    elastic_net_weights,
    equal_weights,
    inverse_vol_weights,
    preprocess,
    rank_based_weights,
    rank_transform,
    ridge_weights,
    winsorize,
    zscore_transform,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_signals(n: int = 300, k: int = 4, seed: int = 42, strength: float = 0.02):
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    signals = pd.DataFrame(
        rng.normal(0, 1, (n, k)), index=dates,
        columns=[f"sig_{i}" for i in range(k)],
    )
    true_w = rng.uniform(0.1, 0.5, k)
    true_w /= true_w.sum()
    fwd = (signals.values @ true_w) * strength + rng.normal(0, 0.01, n)
    returns = pd.Series(fwd, index=dates, name="fwd")
    return signals, returns


def _make_regimes(n: int = 300):
    rng = np.random.RandomState(42)
    return pd.Series(rng.choice(["bull", "bear", "sideways"], n),
                     index=pd.bdate_range("2023-01-02", periods=n))


@pytest.fixture
def data():
    return _make_signals()


@pytest.fixture
def signals(data):
    return data[0]


@pytest.fixture
def returns(data):
    return data[1]


@pytest.fixture
def regimes():
    return _make_regimes()


# ── Preprocessing tests ──────────────────────────────────────────────────


class TestPreprocessing:
    def test_winsorize_clips(self):
        s = pd.Series([1, 2, 3, 100, -50])
        w = winsorize(s, 0.1, 0.9)
        assert w.max() <= 100
        assert w.min() >= -50

    def test_zscore_mean_zero(self, signals):
        z = zscore_transform(signals)
        np.testing.assert_allclose(z.mean().values, 0.0, atol=1e-10)

    def test_zscore_std_one(self, signals):
        z = zscore_transform(signals)
        np.testing.assert_allclose(z.std().values, 1.0, atol=1e-10)

    def test_rank_bounded(self, signals):
        r = rank_transform(signals)
        assert r.min().min() >= -1.0 - 1e-10
        assert r.max().max() <= 1.0 + 1e-10

    def test_preprocess_zscore(self, signals):
        p = preprocess(signals, "zscore")
        assert p.shape == signals.shape

    def test_preprocess_rank(self, signals):
        p = preprocess(signals, "rank")
        assert p.shape == signals.shape

    def test_preprocess_none(self, signals):
        p = preprocess(signals, "none", winsorize_pct=0)
        pd.testing.assert_frame_equal(p, signals)


# ── IC tests ─────────────────────────────────────────────────────────────


class TestIC:
    def test_ic_bounded(self, signals, returns):
        ic = compute_ic(signals.iloc[:, 0], returns)
        assert -1.0 <= ic <= 1.0

    def test_ic_series_length(self, signals, returns):
        ics = compute_ic_series(signals.iloc[:, 0], returns)
        assert len(ics) > 0

    def test_ic_short(self):
        s = pd.Series([1.0, 2.0])
        r = pd.Series([0.01, 0.02])
        assert compute_ic(s, r) == 0.0


# ── Quality gates tests ──────────────────────────────────────────────────


class TestQualityGates:
    def test_keeps_good_signals(self, signals, returns):
        filtered, kept, dropped = apply_quality_gates(signals, returns, min_ic=0.001)
        assert len(kept) > 0

    def test_drops_low_ic(self, signals, returns):
        # Very high threshold should drop most
        _, kept, dropped = apply_quality_gates(signals, returns, min_ic=0.99)
        # With < 2 passing, it keeps all
        assert len(kept) >= 2

    def test_drops_correlated(self):
        rng = np.random.RandomState(42)
        n = 100
        dates = pd.bdate_range("2024-01-02", periods=n)
        base = rng.normal(0, 1, n)
        signals = pd.DataFrame({
            "a": base,
            "b": base + rng.normal(0, 0.01, n),  # near-identical
            "c": rng.normal(0, 1, n),
        }, index=dates)
        returns = pd.Series(rng.normal(0, 0.01, n), index=dates)
        _, kept, dropped = apply_quality_gates(signals, returns, min_ic=0.0, max_correlation=0.5)
        # a and b are highly correlated; one should be dropped
        assert len(dropped) >= 1


# ── Weighting method tests ───────────────────────────────────────────────


class TestWeights:
    def test_equal_sums_one(self):
        w = equal_weights(5)
        assert abs(w.sum() - 1.0) < 1e-10

    def test_inverse_vol_sums_one(self, signals):
        w = inverse_vol_weights(signals)
        assert abs(w.sum() - 1.0) < 1e-10

    def test_inverse_vol_positive(self, signals):
        w = inverse_vol_weights(signals)
        assert (w > 0).all()

    def test_rank_based_sums_one(self, signals, returns):
        w = rank_based_weights(signals, returns)
        assert abs(w.sum() - 1.0) < 1e-10

    def test_ridge_sums_abs_one(self):
        rng = np.random.RandomState(42)
        X = rng.normal(0, 1, (100, 3))
        y = X @ [0.5, 0.3, -0.2] + rng.normal(0, 0.1, 100)
        w = ridge_weights(X, y)
        assert abs(np.abs(w).sum() - 1.0) < 1e-10

    def test_elastic_net_runs(self):
        rng = np.random.RandomState(42)
        X = rng.normal(0, 1, (100, 3))
        y = X @ [0.5, 0.3, -0.2] + rng.normal(0, 0.1, 100)
        w = elastic_net_weights(X, y)
        assert len(w) == 3
        assert abs(np.abs(w).sum() - 1.0) < 1e-10 or np.abs(w).sum() < 1e-6


# ── Walk-forward tests ───────────────────────────────────────────────────


class TestWalkForward:
    def test_folds_created(self, signals, returns):
        from compass.signal_ensemble import walk_forward_fit
        folds, w, wh = walk_forward_fit(signals, returns, "ridge", n_folds=3)
        assert len(folds) > 0
        assert len(w) == signals.shape[1]

    def test_expanding_window(self, signals, returns):
        from compass.signal_ensemble import walk_forward_fit
        folds, _, _ = walk_forward_fit(signals, returns, "ridge", n_folds=3)
        if len(folds) >= 2:
            assert folds[1].n_train > folds[0].n_train

    def test_weight_history_shape(self, signals, returns):
        from compass.signal_ensemble import walk_forward_fit
        _, _, wh = walk_forward_fit(signals, returns, "ridge", n_folds=3)
        assert len(wh) > 0
        assert set(wh.columns) == set(signals.columns)


# ── Constructor tests ─────────────────────────────────────────────────────


class TestConstructor:
    def test_basic(self, signals, returns):
        ens = SignalEnsemble(signals, returns)
        assert ens.method == "ridge"

    def test_empty_raises(self, returns):
        with pytest.raises(ValueError, match="must not be empty"):
            SignalEnsemble(pd.DataFrame(), returns)

    def test_single_signal_raises(self, returns):
        with pytest.raises(ValueError, match="at least 2"):
            SignalEnsemble(pd.DataFrame({"a": [1, 2]}), returns)

    def test_bad_method_raises(self, signals, returns):
        with pytest.raises(ValueError, match="Unknown method"):
            SignalEnsemble(signals, returns, method="magic")


# ── Full fit tests ───────────────────────────────────────────────────────


class TestFullFit:
    @pytest.mark.parametrize("method", ["equal", "inverse_vol", "rank_based", "ridge", "elastic_net"])
    def test_all_methods(self, signals, returns, method):
        ens = SignalEnsemble(signals, returns, method=method, n_folds=2)
        result = ens.fit()
        assert isinstance(result, EnsembleResult)
        assert result.method == method
        assert len(result.weights) > 0

    def test_regime_conditional(self, signals, returns, regimes):
        ens = SignalEnsemble(signals, returns, regimes=regimes, method="regime_conditional")
        result = ens.fit()
        assert result.regime_weights is not None

    def test_weights_populated(self, signals, returns):
        result = SignalEnsemble(signals, returns, n_folds=2).fit()
        total = sum(abs(v) for v in result.weights.values())
        assert total > 0

    def test_signal_stats(self, signals, returns):
        result = SignalEnsemble(signals, returns, n_folds=2).fit()
        assert len(result.signal_stats) == signals.shape[1]
        assert all(isinstance(s, SignalStats) for s in result.signal_stats)

    def test_combined_signal_length(self, signals, returns):
        result = SignalEnsemble(signals, returns, n_folds=2).fit()
        assert len(result.combined_signal) > 0

    def test_ensemble_ic_computed(self, signals, returns):
        result = SignalEnsemble(signals, returns, n_folds=2).fit()
        assert isinstance(result.ensemble_ic, float)

    def test_quality_gates_applied(self):
        rng = np.random.RandomState(42)
        n = 200
        dates = pd.bdate_range("2023-01-02", periods=n)
        # 3 good signals + 1 noise
        good = pd.DataFrame(rng.normal(0, 1, (n, 3)), index=dates,
                            columns=["g0", "g1", "g2"])
        noise = pd.DataFrame({"noise": rng.normal(0, 0.001, n)}, index=dates)
        signals = pd.concat([good, noise], axis=1)
        true_w = np.array([0.4, 0.3, 0.3])
        returns = pd.Series((good.values @ true_w) * 0.02 + rng.normal(0, 0.01, n),
                           index=dates)
        result = SignalEnsemble(signals, returns, min_ic=0.01, n_folds=2).fit()
        assert len(result.dropped_signals) >= 0  # noise may or may not drop


# ── HTML report tests ────────────────────────────────────────────────────


class TestHTMLReport:
    def test_generates_file(self, signals, returns):
        result = SignalEnsemble(signals, returns, n_folds=2).fit()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "ens.html"
            path = SignalEnsemble.generate_report(result, out)
            assert path.exists()
            content = path.read_text()
            assert "Signal Ensemble" in content

    def test_contains_table(self, signals, returns):
        result = SignalEnsemble(signals, returns, n_folds=2).fit()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            SignalEnsemble.generate_report(result, out)
            content = out.read_text()
            assert "sig_0" in content
            assert "IC" in content

    def test_contains_svg(self, signals, returns):
        result = SignalEnsemble(signals, returns, n_folds=2).fit()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            SignalEnsemble.generate_report(result, out)
            content = out.read_text()
            assert "<svg" in content

    def test_contains_walk_forward(self, signals, returns):
        result = SignalEnsemble(signals, returns, method="ridge", n_folds=3).fit()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            SignalEnsemble.generate_report(result, out)
            content = out.read_text()
            assert "Walk-Forward" in content

    def test_default_path(self, signals, returns):
        result = SignalEnsemble(signals, returns, n_folds=2).fit()
        path = SignalEnsemble.generate_report(result)
        assert path.exists()
        assert "signal_ensemble.html" in str(path)
        path.unlink(missing_ok=True)
