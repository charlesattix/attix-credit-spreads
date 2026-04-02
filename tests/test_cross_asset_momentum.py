"""Tests for compass/cross_asset_momentum.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.cross_asset_momentum import (
    ASSETS,
    AnalysisResult,
    CrossAssetMomentum,
    LeadLagResult,
    MomentumFeature,
    OverlayResult,
    PositioningSignal,
    composite_signal_sharpe,
    compute_momentum,
    compute_returns,
    compute_trend_strength,
    compute_zscore,
    detect_lead_lag,
    extract_all_features,
    generate_signal,
    generate_signal_series,
    latest_features,
    overlay_on_trades,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_prices(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    spy = 450 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    gld = 180 * np.exp(np.cumsum(rng.normal(0.0001, 0.008, n)))
    tlt = 100 * np.exp(np.cumsum(rng.normal(-0.0001, 0.007, n)))
    hyg = 75 * np.exp(np.cumsum(rng.normal(0.0002, 0.005, n)))
    uso = 70 * np.exp(np.cumsum(rng.normal(0.0001, 0.015, n)))
    cper = 25 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, n)))
    uup = 28 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pd.DataFrame({
        "SPY": spy, "GLD": gld, "TLT": tlt, "HYG": hyg,
        "USO": uso, "CPER": cper, "UUP": uup,
    }, index=dates)


def _make_trades(n: int = 50, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-03-01", periods=n)
    return pd.DataFrame({
        "entry_date": dates,
        "pnl": rng.normal(100, 300, n),
        "win": (rng.random(n) > 0.4).astype(int),
    })


@pytest.fixture
def prices():
    return _make_prices()


@pytest.fixture
def trades():
    return _make_trades()


# ── Return/momentum tests ────────────────────────────────────────────────


class TestReturns:
    def test_compute_returns(self, prices):
        r = compute_returns(prices["SPY"])
        assert len(r) == len(prices)
        assert abs(r.iloc[0]) < 1e-10  # first return is 0

    def test_compute_momentum(self, prices):
        r = compute_returns(prices["SPY"])
        m = compute_momentum(r, 5)
        assert len(m) == len(r)

    def test_zscore_bounded(self, prices):
        z = compute_zscore(prices["SPY"])
        valid = z.dropna()
        assert valid.abs().max() < 10  # reasonable z-scores

    def test_trend_strength(self, prices):
        t = compute_trend_strength(prices["SPY"])
        assert len(t) == len(prices)


# ── Feature extraction tests ─────────────────────────────────────────────


class TestFeatures:
    def test_extract_all(self, prices):
        df = extract_all_features(prices)
        assert not df.empty
        assert any("GLD" in c for c in df.columns)

    def test_latest_features(self, prices):
        feats = latest_features(prices)
        assert len(feats) > 0
        for name, f in feats.items():
            assert isinstance(f, MomentumFeature)
            assert f.asset == name

    def test_feature_columns_per_asset(self, prices):
        df = extract_all_features(prices)
        for asset in ["GLD", "TLT", "USO"]:
            assert f"{asset}_mom_1d" in df.columns
            assert f"{asset}_zscore_20d" in df.columns

    def test_missing_asset(self):
        df = pd.DataFrame({"SPY": [100, 101, 102]})
        feats = latest_features(df)
        assert len(feats) == 0  # no cross-assets


# ── Lead-lag tests ───────────────────────────────────────────────────────


class TestLeadLag:
    def test_detect(self, prices):
        spy_ret = compute_returns(prices["SPY"])
        gld_ret = compute_returns(prices["GLD"])
        ll = detect_lead_lag(gld_ret, spy_ret)
        assert isinstance(ll, LeadLagResult)
        assert ll.optimal_lag >= 0

    def test_correlation_bounded(self, prices):
        spy_ret = compute_returns(prices["SPY"])
        for asset in ["GLD", "TLT", "USO"]:
            aret = compute_returns(prices[asset])
            ll = detect_lead_lag(aret, spy_ret)
            assert -1.0 <= ll.correlation_at_lag <= 1.0

    def test_short_data(self):
        s = pd.Series([0.01, -0.01], name="A")
        spy = pd.Series([0.005, 0.01])
        ll = detect_lead_lag(s, spy)
        assert not ll.is_significant

    def test_direction_assigned(self, prices):
        spy_ret = compute_returns(prices["SPY"])
        tlt_ret = compute_returns(prices["TLT"])
        ll = detect_lead_lag(tlt_ret, spy_ret)
        assert ll.direction in ("positive", "inverse")


# ── Signal generation tests ──────────────────────────────────────────────


class TestSignal:
    def test_generate_from_features(self, prices):
        feats = latest_features(prices)
        spy_ret = compute_returns(prices["SPY"])
        lls = [detect_lead_lag(compute_returns(prices[a]), spy_ret) for a in ASSETS if a in prices.columns]
        sig = generate_signal(feats, lls)
        assert isinstance(sig, PositioningSignal)
        assert sig.signal in ("bullish", "bearish", "neutral")
        assert 0 <= sig.confidence <= 1

    def test_signal_series(self, prices):
        sigs = generate_signal_series(prices)
        assert len(sigs) > 0
        assert all(isinstance(s, PositioningSignal) for s in sigs)

    def test_signal_has_assets(self, prices):
        feats = latest_features(prices)
        spy_ret = compute_returns(prices["SPY"])
        lls = [detect_lead_lag(compute_returns(prices[a]), spy_ret) for a in ASSETS if a in prices.columns]
        sig = generate_signal(feats, lls)
        # At least some contributing assets if significant leads exist
        assert isinstance(sig.contributing_assets, dict)

    def test_empty_features(self):
        sig = generate_signal({}, [])
        assert sig.signal == "neutral"
        assert sig.confidence == 0.0


# ── Overlay tests ────────────────────────────────────────────────────────


class TestOverlay:
    def test_overlay_on_trades(self, prices, trades):
        sigs = generate_signal_series(prices)
        ov = overlay_on_trades(sigs, trades)
        assert isinstance(ov, OverlayResult)
        assert ov.total_trades > 0

    def test_overlay_empty(self):
        ov = overlay_on_trades([], pd.DataFrame())
        assert ov.total_trades == 0

    def test_win_rates_bounded(self, prices, trades):
        sigs = generate_signal_series(prices)
        ov = overlay_on_trades(sigs, trades)
        assert 0 <= ov.confirmed_win_rate <= 1
        assert 0 <= ov.unconfirmed_win_rate <= 1


# ── Composite Sharpe tests ───────────────────────────────────────────────


class TestCompositeSharpe:
    def test_sharpe_computed(self, prices):
        sigs = generate_signal_series(prices)
        spy_ret = compute_returns(prices["SPY"])
        sh = composite_signal_sharpe(sigs, spy_ret)
        assert isinstance(sh, float)

    def test_sharpe_empty(self):
        assert composite_signal_sharpe([], pd.Series(dtype=float)) == 0.0


# ── Full analysis tests ──────────────────────────────────────────────────


class TestFullAnalysis:
    def test_returns_result(self, prices):
        cam = CrossAssetMomentum(prices)
        r = cam.analyze()
        assert isinstance(r, AnalysisResult)
        assert r.n_observations == len(prices)

    def test_with_trades(self, prices, trades):
        cam = CrossAssetMomentum(prices)
        r = cam.analyze(trades)
        assert r.overlay is not None

    def test_without_trades(self, prices):
        cam = CrossAssetMomentum(prices)
        r = cam.analyze()
        assert r.overlay is None

    def test_lead_lags_populated(self, prices):
        cam = CrossAssetMomentum(prices)
        r = cam.analyze()
        assert len(r.lead_lags) > 0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            CrossAssetMomentum(pd.DataFrame())


# ── HTML report tests ────────────────────────────────────────────────────


class TestReport:
    def test_generates(self, prices):
        cam = CrossAssetMomentum(prices)
        r = cam.analyze()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "cam.html"
            path = CrossAssetMomentum.generate_report(r, out)
            assert path.exists()
            content = path.read_text()
            assert "Cross-Asset" in content

    def test_contains_lead_lag(self, prices):
        cam = CrossAssetMomentum(prices)
        r = cam.analyze()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            CrossAssetMomentum.generate_report(r, out)
            assert "Lead-Lag" in out.read_text()

    def test_default_path(self, prices):
        cam = CrossAssetMomentum(prices)
        r = cam.analyze()
        path = CrossAssetMomentum.generate_report(r)
        assert path.exists()
        path.unlink(missing_ok=True)
