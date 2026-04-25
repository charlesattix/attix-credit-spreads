"""Tests for compass/intraday_vol_clustering.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.intraday_vol_clustering import (
    ClusterResult,
    ClusterSignal,
    OverlayResult,
    SessionProfile,
    VolBlock,
    VolClusterEngine,
    analyze_session,
    compute_block_vol,
    compute_ewma_vol,
    detect_block_regime,
    expansion_predicts_eod_auc,
    generate_session_signal,
    overlay_on_trades,
    simulate_sessions_from_daily,
    vol_autocorrelation,
)


def _make_session(n: int = 78, seed: int = 42, vol: float = 0.001) -> np.ndarray:
    rng = np.random.RandomState(seed)
    returns = rng.normal(0, vol, n)
    prices = 450 * np.exp(np.cumsum(returns))
    return np.insert(prices, 0, 450.0)  # n+1 prices


def _make_daily(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = 450 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.003, n)),
        "high": close * (1 + rng.uniform(0.002, 0.015, n)),
        "low": close * (1 - rng.uniform(0.002, 0.015, n)),
        "close": close,
        "volume": rng.uniform(2e6, 5e6, n),
    }, index=dates)


def _make_trades(n: int = 40, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-03-01", periods=n)
    return pd.DataFrame({"entry_date": dates, "win": (rng.random(n) > 0.35).astype(int)})


@pytest.fixture
def session_prices():
    return _make_session()


@pytest.fixture
def daily():
    return _make_daily()


@pytest.fixture
def trades():
    return _make_trades()


@pytest.fixture
def engine():
    return VolClusterEngine()


# ── Block vol tests ──────────────────────────────────────────────────────


class TestBlockVol:
    def test_positive(self):
        rng = np.random.RandomState(1)
        returns = rng.normal(0, 0.001, 5)
        assert compute_block_vol(returns) > 0

    def test_zero_for_constant(self):
        assert compute_block_vol(np.zeros(5)) == 0.0

    def test_short(self):
        assert compute_block_vol(np.array([0.01])) == 0.0


# ── EWMA tests ───────────────────────────────────────────────────────────


class TestEWMA:
    def test_length(self):
        returns = np.random.RandomState(1).normal(0, 0.001, 50)
        ewma = compute_ewma_vol(returns)
        assert len(ewma) == 50

    def test_positive(self):
        returns = np.random.RandomState(1).normal(0, 0.001, 50)
        ewma = compute_ewma_vol(returns)
        assert (ewma >= 0).all()

    def test_responds_to_spike(self):
        returns = np.zeros(50)
        returns[25] = 0.05  # big spike
        ewma = compute_ewma_vol(returns)
        assert ewma[26] > ewma[24]  # vol rises after spike


# ── Autocorrelation tests ────────────────────────────────────────────────


class TestAutocorrelation:
    def test_high_for_clustered(self):
        # Clustered: repeat each value
        vols = np.array([1, 1, 1, 5, 5, 5, 1, 1, 1, 5, 5, 5, 2, 2, 2, 2])
        ac = vol_autocorrelation(vols)
        assert ac > 0.3

    def test_bounded(self):
        rng = np.random.RandomState(1)
        vols = rng.uniform(0, 1, 50)
        ac = vol_autocorrelation(vols)
        assert -1.0 <= ac <= 1.0

    def test_short(self):
        assert vol_autocorrelation(np.array([1, 2])) == 0.0


# ── Regime detection tests ───────────────────────────────────────────────


class TestRegime:
    def test_expansion(self):
        assert detect_block_regime(10.0, 5.0, 2.0) == "expansion"  # z=2.5

    def test_contraction(self):
        assert detect_block_regime(3.0, 5.0, 2.0) == "contraction"  # z=-1.0

    def test_normal(self):
        assert detect_block_regime(5.5, 5.0, 2.0) == "normal"

    def test_zero_std(self):
        assert detect_block_regime(5.0, 5.0, 0.0) == "normal"


# ── Session analysis tests ───────────────────────────────────────────────


class TestSession:
    def test_analyze(self, session_prices):
        s = analyze_session(session_prices)
        assert isinstance(s, SessionProfile)
        assert len(s.blocks) > 0

    def test_session_vol_positive(self, session_prices):
        s = analyze_session(session_prices)
        assert s.session_vol > 0

    def test_autocorrelation_computed(self, session_prices):
        s = analyze_session(session_prices)
        assert isinstance(s.vol_autocorrelation, float)

    def test_short_session(self):
        s = analyze_session(np.array([100, 101]))
        assert len(s.blocks) == 0

    def test_expansion_count(self, session_prices):
        s = analyze_session(session_prices)
        assert s.n_expansions >= 0
        assert s.n_contractions >= 0
        assert s.n_expansions + s.n_contractions <= len(s.blocks)


# ── Signal tests ─────────────────────────────────────────────────────────


class TestSignal:
    def test_generate(self, session_prices):
        s = analyze_session(session_prices)
        sig = generate_session_signal(s)
        assert isinstance(sig, ClusterSignal)
        assert sig.signal in ("sell_premium", "avoid", "neutral")
        assert 0 <= sig.confidence <= 1.0

    def test_empty_session(self):
        s = SessionProfile(None, [], 0, 0, 0, 0, 0, 0, 0, "normal")
        sig = generate_session_signal(s)
        assert sig.signal == "neutral"


# ── Simulation from daily ────────────────────────────────────────────────


class TestSimulation:
    def test_sessions_generated(self, daily):
        sessions = simulate_sessions_from_daily(daily)
        assert len(sessions) == len(daily)
        assert all(isinstance(s, SessionProfile) for s in sessions)

    def test_sessions_have_blocks(self, daily):
        sessions = simulate_sessions_from_daily(daily)
        assert all(len(s.blocks) > 0 for s in sessions)


# ── AUC test ─────────────────────────────────────────────────────────────


class TestAUC:
    def test_bounded(self, daily):
        sessions = simulate_sessions_from_daily(daily)
        auc = expansion_predicts_eod_auc(sessions)
        assert 0.0 <= auc <= 1.0


# ── Overlay tests ────────────────────────────────────────────────────────


class TestOverlay:
    def test_overlay(self, daily, trades):
        sessions = simulate_sessions_from_daily(daily)
        signals = [generate_session_signal(s) for s in sessions]
        ov = overlay_on_trades(signals, trades)
        assert isinstance(ov, OverlayResult)

    def test_empty(self):
        ov = overlay_on_trades([], pd.DataFrame())
        assert ov.total_trades == 0


# ── Full engine tests ────────────────────────────────────────────────────


class TestEngine:
    def test_analyze_daily(self, engine, daily):
        r = engine.analyze_daily(daily)
        assert isinstance(r, ClusterResult)
        assert r.n_sessions == len(daily)

    def test_with_trades(self, engine, daily, trades):
        r = engine.analyze_daily(daily, trades)
        assert r.overlay is not None

    def test_analyze_bars(self, engine, session_prices):
        s = engine.analyze_bars(session_prices)
        assert isinstance(s, SessionProfile)


# ── Report tests ─────────────────────────────────────────────────────────


class TestReport:
    def test_generates(self, engine, daily):
        r = engine.analyze_daily(daily)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "vc.html"
            path = VolClusterEngine.generate_report(r, out)
            assert path.exists()
            assert "Vol Clustering" in path.read_text()

    def test_default_path(self, engine, daily):
        r = engine.analyze_daily(daily)
        path = VolClusterEngine.generate_report(r)
        assert path.exists()
        path.unlink(missing_ok=True)
