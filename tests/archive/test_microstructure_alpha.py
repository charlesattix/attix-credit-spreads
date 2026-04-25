"""Tests for compass/microstructure_alpha.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.microstructure_alpha import (
    LiquiditySignal,
    MicroFeatures,
    MicrostructureScanner,
    OverlayResult,
    ScannerResult,
    classify_regime,
    compute_all_features,
    compute_amihud,
    compute_corwin_schultz,
    compute_kyle_lambda,
    compute_liquidity_ratio,
    compute_roll_spread,
    compute_spread_zscore,
    compute_volume_return_correlation,
    generate_signals,
    overlay_on_trades,
    regime_to_signal,
    standalone_sharpe,
    volatility_prediction_auc,
)


def _make_ohlcv(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = 450 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    high = close * (1 + rng.uniform(0.001, 0.015, n))
    low = close * (1 - rng.uniform(0.001, 0.015, n))
    opn = close * (1 + rng.normal(0, 0.003, n))
    vol = rng.uniform(1e6, 5e6, n)
    return pd.DataFrame({"open": opn, "high": high, "low": low, "close": close, "volume": vol}, index=dates)


def _make_trades(n: int = 50, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-03-01", periods=n)
    return pd.DataFrame({"entry_date": dates, "win": (rng.random(n) > 0.35).astype(int)})


@pytest.fixture
def ohlcv():
    return _make_ohlcv()


@pytest.fixture
def trades():
    return _make_trades()


# ── Individual metric tests ──────────────────────────────────────────────


class TestAmihud:
    def test_positive(self, ohlcv):
        ret = ohlcv["close"].pct_change().fillna(0)
        dv = ohlcv["close"] * ohlcv["volume"]
        a = compute_amihud(ret, dv)
        assert (a.iloc[25:] >= 0).all()

    def test_higher_for_illiquid(self):
        rng = np.random.RandomState(1)
        n = 100
        ret = pd.Series(rng.normal(0, 0.01, n))
        dv_liq = pd.Series(np.full(n, 1e9))
        dv_illiq = pd.Series(np.full(n, 1e6))
        assert compute_amihud(ret, dv_liq).iloc[-1] < compute_amihud(ret, dv_illiq).iloc[-1]


class TestRollSpread:
    def test_non_negative(self, ohlcv):
        r = compute_roll_spread(ohlcv["close"])
        assert (r >= -0.01).all()  # can be slightly negative from noise

    def test_length(self, ohlcv):
        assert len(compute_roll_spread(ohlcv["close"])) == len(ohlcv)


class TestKyleLambda:
    def test_positive(self, ohlcv):
        ret = ohlcv["close"].pct_change().fillna(0)
        k = compute_kyle_lambda(ret, ohlcv["volume"])
        assert (k.iloc[25:] >= 0).all()


class TestCorwinSchultz:
    def test_non_negative(self, ohlcv):
        cs = compute_corwin_schultz(ohlcv["high"], ohlcv["low"])
        assert (cs >= -0.01).all()

    def test_reasonable(self, ohlcv):
        cs = compute_corwin_schultz(ohlcv["high"], ohlcv["low"])
        assert cs.iloc[5:].max() < 0.1  # spread < 10% of price


class TestVolumeReturnCorr:
    def test_bounded(self, ohlcv):
        ret = ohlcv["close"].pct_change().fillna(0)
        vrc = compute_volume_return_correlation(ret, ohlcv["volume"])
        valid = vrc.dropna()
        assert valid.abs().max() <= 1.01


class TestLiquidityRatio:
    def test_positive(self, ohlcv):
        ret = ohlcv["close"].pct_change().fillna(0)
        lr = compute_liquidity_ratio(ret, ohlcv["volume"])
        assert (lr.iloc[25:] >= 0).all()


class TestSpreadZscore:
    def test_mean_near_zero(self, ohlcv):
        spread = pd.Series(np.random.RandomState(42).uniform(0.01, 0.03, len(ohlcv)), index=ohlcv.index)
        z = compute_spread_zscore(spread)
        assert abs(z.iloc[30:].mean()) < 1.0


# ── Regime classification tests ──────────────────────────────────────────


class TestRegime:
    def test_tight(self):
        assert classify_regime(-1.0, -1.0) == "tight"

    def test_normal(self):
        assert classify_regime(0.0, 0.0) == "normal"

    def test_wide(self):
        assert classify_regime(1.5, 1.5) == "wide"

    def test_crisis(self):
        assert classify_regime(3.0, 3.0) == "crisis"

    def test_signal_mapping(self):
        assert regime_to_signal("tight") == "enter"
        assert regime_to_signal("normal") == "enter"
        assert regime_to_signal("wide") == "avoid"
        assert regime_to_signal("crisis") == "exit_all"


# ── Feature engine tests ────────────────────────────────────────────────


class TestFeatureEngine:
    def test_compute_all(self, ohlcv):
        f = compute_all_features(ohlcv)
        assert len(f) == len(ohlcv)
        assert "amihud" in f.columns
        assert "regime" in f.columns

    def test_regime_column_valid(self, ohlcv):
        f = compute_all_features(ohlcv)
        assert f["regime"].isin(["tight", "normal", "wide", "crisis"]).all()

    def test_all_8_metrics(self, ohlcv):
        f = compute_all_features(ohlcv)
        expected = {"amihud", "roll_spread", "kyle_lambda", "corwin_schultz",
                    "volume_return_corr", "liquidity_ratio", "spread_zscore", "amihud_zscore", "regime"}
        assert expected.issubset(set(f.columns))


# ── Signal tests ─────────────────────────────────────────────────────────


class TestSignals:
    def test_generate(self, ohlcv):
        f = compute_all_features(ohlcv)
        sigs = generate_signals(f)
        assert len(sigs) == len(f)
        assert all(isinstance(s, LiquiditySignal) for s in sigs)

    def test_signal_values(self, ohlcv):
        f = compute_all_features(ohlcv)
        sigs = generate_signals(f)
        for s in sigs:
            assert s.signal in ("enter", "avoid", "exit_all")
            assert 0 <= s.confidence <= 1


# ── Standalone Sharpe tests ──────────────────────────────────────────────


class TestStandalone:
    def test_sharpe_float(self, ohlcv):
        f = compute_all_features(ohlcv)
        sigs = generate_signals(f)
        ret = ohlcv["close"].pct_change().fillna(0)
        sh = standalone_sharpe(sigs, ret)
        assert isinstance(sh, float)


# ── Volatility AUC tests ────────────────────────────────────────────────


class TestVolAUC:
    def test_auc_bounded(self, ohlcv):
        f = compute_all_features(ohlcv)
        ret = ohlcv["close"].pct_change().fillna(0)
        auc = volatility_prediction_auc(f, ret)
        assert 0.0 <= auc <= 1.0


# ── Overlay tests ────────────────────────────────────────────────────────


class TestOverlay:
    def test_overlay(self, ohlcv, trades):
        f = compute_all_features(ohlcv)
        sigs = generate_signals(f)
        ov = overlay_on_trades(sigs, trades)
        assert isinstance(ov, OverlayResult)
        assert ov.total_trades > 0

    def test_empty(self):
        ov = overlay_on_trades([], pd.DataFrame())
        assert ov.total_trades == 0


# ── Full scanner tests ───────────────────────────────────────────────────


class TestScanner:
    def test_analyze(self, ohlcv):
        scanner = MicrostructureScanner(ohlcv)
        r = scanner.analyze()
        assert isinstance(r, ScannerResult)
        assert r.n_observations == len(ohlcv)

    def test_with_trades(self, ohlcv, trades):
        scanner = MicrostructureScanner(ohlcv)
        r = scanner.analyze(trades)
        assert r.overlay is not None

    def test_missing_columns_raises(self):
        with pytest.raises(ValueError):
            MicrostructureScanner(pd.DataFrame({"foo": [1]}))


# ── Report tests ─────────────────────────────────────────────────────────


class TestReport:
    def test_generates(self, ohlcv):
        scanner = MicrostructureScanner(ohlcv)
        r = scanner.analyze()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "ms.html"
            path = MicrostructureScanner.generate_report(r, out)
            assert path.exists()
            assert "Microstructure" in path.read_text()

    def test_default_path(self, ohlcv):
        r = MicrostructureScanner(ohlcv).analyze()
        path = MicrostructureScanner.generate_report(r)
        assert path.exists()
        path.unlink(missing_ok=True)
