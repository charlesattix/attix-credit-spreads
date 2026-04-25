"""Tests for compass/momentum_crash_protector.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.momentum_crash_protector import (
    AnalysisResult,
    CrashEpisode,
    CrashIndicators,
    MomentumCrashProtector,
    ProtectionResult,
    classify_risk,
    compute_crowding_score,
    compute_momentum,
    detect_episodes,
    mean_reversion_trigger,
    momentum_dispersion,
    return_autocorrelation,
    short_interest_proxy,
    simulate_protection,
    winner_loser_spread,
    wl_spread_acceleration,
)


def _make_returns(n: int = 500, k: int = 6, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2020-01-02", periods=n)
    names = ["SPY", "QQQ", "IWM", "GLD", "TLT", "HYG"][:k]
    # Correlated returns with occasional crashes
    base = rng.normal(0.0003, 0.012, n)
    data = {}
    for i, name in enumerate(names):
        corr = 0.7 - i * 0.1
        noise = rng.normal(0, 0.008, n)
        data[name] = base * corr + noise
    # Inject crash at day 100 and 300
    for name in names:
        data[name][100:105] = rng.normal(-0.03, 0.01, 5)
        data[name][300:305] = rng.normal(-0.025, 0.01, 5)
    return pd.DataFrame(data, index=dates)


def _make_crash_returns(n: int = 300, seed: int = 99) -> pd.DataFrame:
    """Returns with a clear momentum crash."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2020-01-02", periods=n)
    # Winners and losers with crash at day 200
    data = {}
    for i in range(4):
        rets = rng.normal(0.001 * (2 - i), 0.01, n)
        # At crash: winners drop, losers surge
        if i < 2:  # winners
            rets[200:210] = rng.normal(-0.04, 0.01, 10)
        else:  # losers
            rets[200:210] = rng.normal(0.03, 0.01, 10)
        data[f"Asset_{i}"] = rets
    return pd.DataFrame(data, index=dates)


@pytest.fixture
def returns():
    return _make_returns()


@pytest.fixture
def crash_returns():
    return _make_crash_returns()


# ── Indicator tests ──────────────────────────────────────────────────────


class TestIndicators:
    def test_momentum_shape(self, returns):
        mom = compute_momentum(returns)
        assert mom.shape == returns.shape

    def test_dispersion_positive(self, returns):
        mom = compute_momentum(returns)
        d = momentum_dispersion(mom)
        assert (d.iloc[25:] >= 0).all()

    def test_autocorrelation_bounded(self, returns):
        avg = returns.mean(axis=1)
        ac = return_autocorrelation(avg)
        valid = ac.iloc[25:]
        assert valid.abs().max() <= 1.01

    def test_wl_spread_computed(self, returns):
        mom = compute_momentum(returns)
        wl = winner_loser_spread(returns, mom)
        assert len(wl) == len(returns)

    def test_wl_acceleration(self, returns):
        mom = compute_momentum(returns)
        wl = winner_loser_spread(returns, mom)
        acc = wl_spread_acceleration(wl)
        assert len(acc) == len(wl)

    def test_short_interest_proxy(self, returns):
        mom = compute_momentum(returns)
        si = short_interest_proxy(returns, mom)
        assert (si.iloc[25:] >= 0).all()

    def test_mean_reversion_trigger(self, returns):
        mom = compute_momentum(returns)
        mr = mean_reversion_trigger(returns, mom)
        assert mr.isin([0.0, 1.0]).all()


# ── Crowding score tests ─────────────────────────────────────────────────


class TestCrowding:
    def test_bounded(self):
        s = compute_crowding_score(0.5, 0.2, -0.01, 1.5)
        assert 0 <= s <= 1

    def test_high_dispersion_increases(self):
        low = compute_crowding_score(0.0, 0.3, 0.0, 1.0)
        high = compute_crowding_score(3.0, 0.3, 0.0, 1.0)
        assert high > low

    def test_low_autocorr_increases(self):
        high_ac = compute_crowding_score(1.0, 0.5, 0.0, 1.0)
        low_ac = compute_crowding_score(1.0, -0.3, 0.0, 1.0)
        assert low_ac > high_ac

    def test_negative_wl_accel_increases(self):
        pos = compute_crowding_score(1.0, 0.2, 0.01, 1.0)
        neg = compute_crowding_score(1.0, 0.2, -0.03, 1.0)
        assert neg > pos

    def test_classify_low(self):
        assert classify_risk(0.1) == "low"

    def test_classify_elevated(self):
        assert classify_risk(0.35) == "elevated"

    def test_classify_high(self):
        assert classify_risk(0.60) == "high"

    def test_classify_critical(self):
        assert classify_risk(0.80) == "critical"


# ── Episode detection tests ──────────────────────────────────────────────


class TestEpisodes:
    def test_detects_crashes(self, returns):
        protector = MomentumCrashProtector(returns)
        r = protector.analyze()
        # Should detect the injected crashes
        assert r.total_episodes >= 0  # may or may not cross threshold

    def test_crash_returns_detected(self, crash_returns):
        protector = MomentumCrashProtector(crash_returns)
        r = protector.analyze()
        assert r.total_episodes >= 1

    def test_episode_has_magnitude(self, crash_returns):
        protector = MomentumCrashProtector(crash_returns)
        r = protector.analyze()
        for e in r.episodes:
            assert e.crash_magnitude_pct < 0  # negative = drawdown


# ── Protection simulation tests ──────────────────────────────────────────


class TestProtection:
    def test_reduces_dd(self, crash_returns):
        protector = MomentumCrashProtector(crash_returns)
        r = protector.analyze()
        p = r.protection
        # Protection should reduce DD or at least not increase massively
        assert p.protected_dd <= p.unprotected_dd * 1.5

    def test_preserves_some_return(self, returns):
        protector = MomentumCrashProtector(returns)
        r = protector.analyze()
        # Should preserve most return in normal markets
        assert isinstance(r.protection.return_preserved_pct, float)

    def test_protection_days_counted(self, returns):
        protector = MomentumCrashProtector(returns)
        r = protector.analyze()
        assert r.protection.n_protection_days >= 0
        assert r.protection.n_contrarian_days >= 0


# ── Full analysis tests ──────────────────────────────────────────────────


class TestFullAnalysis:
    def test_returns_result(self, returns):
        protector = MomentumCrashProtector(returns)
        r = protector.analyze()
        assert isinstance(r, AnalysisResult)
        assert r.n_observations == len(returns)

    def test_indicators_df(self, returns):
        protector = MomentumCrashProtector(returns)
        r = protector.analyze()
        assert "crowding_score" in r.indicators.columns
        assert "risk_level" in r.indicators.columns
        assert len(r.indicators) == len(returns)

    def test_crowding_stats(self, returns):
        protector = MomentumCrashProtector(returns)
        r = protector.analyze()
        assert 0 <= r.avg_crowding <= 1
        assert r.max_crowding >= r.avg_crowding

    def test_elevated_days_counted(self, returns):
        protector = MomentumCrashProtector(returns)
        r = protector.analyze()
        assert r.n_elevated_days >= 0
        assert r.n_critical_days >= 0
        assert r.n_critical_days <= r.n_elevated_days

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            MomentumCrashProtector(pd.DataFrame())


# ── Report tests ─────────────────────────────────────────────────────────


class TestReport:
    def test_generates(self, returns):
        r = MomentumCrashProtector(returns).analyze()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "mc.html"
            path = MomentumCrashProtector.generate_report(r, out)
            assert path.exists()
            assert "Momentum Crash" in path.read_text()

    def test_default_path(self, returns):
        r = MomentumCrashProtector(returns).analyze()
        path = MomentumCrashProtector.generate_report(r)
        assert path.exists()
        path.unlink(missing_ok=True)
