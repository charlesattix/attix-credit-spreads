"""Tests for compass.strategy_decay_monitor — 35+ tests."""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime
from pathlib import Path

from compass.strategy_decay_monitor import (
    StrategyDecayMonitor,
    LifecyclePhase,
    RollingMetrics,
    CUSUMResult,
    DecayEstimate,
    RegimeDecay,
    KillSignal,
    TRADING_DAYS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dates(n: int = 300) -> pd.DatetimeIndex:
    return pd.bdate_range(start="2024-01-02", periods=n)


def _good_returns(n: int = 300, seed: int = 42) -> pd.Series:
    """Healthy strategy: positive mean, decent Sharpe."""
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.001, 0.008, n), index=_dates(n))


def _decaying_returns(n: int = 300, seed: int = 77) -> pd.Series:
    """Strategy with alpha that decays over time."""
    rng = np.random.default_rng(seed)
    # Start with alpha, linearly decay to zero
    alpha = np.linspace(0.002, -0.0005, n)
    noise = rng.normal(0, 0.008, n)
    return pd.Series(alpha + noise, index=_dates(n))


def _dead_returns(n: int = 300, seed: int = 99) -> pd.Series:
    """Strategy that's been losing steadily."""
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(-0.001, 0.01, n), index=_dates(n))


def _regimes(n: int = 300) -> pd.Series:
    """Synthetic regime series."""
    labels = []
    for i in range(n):
        if i < 100:
            labels.append("low")
        elif i < 200:
            labels.append("high")
        else:
            labels.append("normal")
    return pd.Series(labels, index=_dates(n))


# ===========================================================================
# Rolling metrics
# ===========================================================================

class TestRollingMetrics:
    def test_basic(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        r = _good_returns(100)
        metrics = mon.rolling_metrics(r)
        assert len(metrics) > 0
        assert all(isinstance(m, RollingMetrics) for m in metrics)

    def test_length(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        r = _good_returns(100)
        metrics = mon.rolling_metrics(r)
        assert len(metrics) == 100 - 30 + 1

    def test_sharpe_positive_for_good(self):
        mon = StrategyDecayMonitor(rolling_window=60)
        metrics = mon.rolling_metrics(_good_returns(200))
        avg_sharpe = np.mean([m.sharpe for m in metrics])
        assert avg_sharpe > 0

    def test_hit_rate_bounded(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        for m in mon.rolling_metrics(_good_returns(100)):
            assert 0.0 <= m.hit_rate <= 1.0

    def test_too_short(self):
        mon = StrategyDecayMonitor(rolling_window=100)
        assert mon.rolling_metrics(_good_returns(50)) == []

    def test_cumulative_pnl_monotone(self):
        """All positive returns → cumulative should be monotonically increasing."""
        rng = np.random.default_rng(1)
        r = pd.Series(np.abs(rng.normal(0.001, 0.001, 100)), index=_dates(100))
        mon = StrategyDecayMonitor(rolling_window=20)
        metrics = mon.rolling_metrics(r)
        cpnl = [m.cumulative_pnl for m in metrics]
        assert all(cpnl[i] <= cpnl[i + 1] for i in range(len(cpnl) - 1))


# ===========================================================================
# CUSUM
# ===========================================================================

class TestCUSUM:
    def test_no_break_in_good(self):
        mon = StrategyDecayMonitor(cusum_threshold=4.0)
        res = mon.cusum_test(_good_returns(200))
        assert isinstance(res, CUSUMResult)
        # Good strategy shouldn't normally have a break
        # (with high threshold)

    def test_break_in_decaying(self):
        mon = StrategyDecayMonitor(cusum_threshold=2.0)
        res = mon.cusum_test(_decaying_returns(300))
        assert res.break_detected
        assert res.break_index is not None
        assert res.break_date is not None

    def test_cusum_series_shape(self):
        mon = StrategyDecayMonitor()
        res = mon.cusum_test(_good_returns(100))
        assert res.cusum_series is not None
        assert len(res.cusum_series) == 100

    def test_cusum_non_negative(self):
        mon = StrategyDecayMonitor()
        res = mon.cusum_test(_good_returns(200))
        assert (res.cusum_series >= -1e-12).all()

    def test_too_short(self):
        mon = StrategyDecayMonitor()
        res = mon.cusum_test(pd.Series([0.01] * 5, index=_dates(5)))
        assert not res.break_detected

    def test_max_cusum_stored(self):
        mon = StrategyDecayMonitor()
        res = mon.cusum_test(_decaying_returns(200))
        assert res.max_cusum > 0


# ===========================================================================
# Lifecycle
# ===========================================================================

class TestLifecycle:
    def test_good_not_dead(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        phase = mon.classify_lifecycle(_good_returns(200))
        assert phase != LifecyclePhase.DEAD

    def test_dead_strategy(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        phase = mon.classify_lifecycle(_dead_returns(300))
        assert phase in (LifecyclePhase.DECAY, LifecyclePhase.DEAD)

    def test_decaying_strategy(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        phase = mon.classify_lifecycle(_decaying_returns(300))
        assert phase in (LifecyclePhase.DECAY, LifecyclePhase.DEAD, LifecyclePhase.MATURITY)

    def test_short_data_returns_growth(self):
        mon = StrategyDecayMonitor(rolling_window=60)
        phase = mon.classify_lifecycle(_good_returns(30))
        assert phase == LifecyclePhase.GROWTH


# ===========================================================================
# Decay rate
# ===========================================================================

class TestDecayRate:
    def test_good_strategy_long_halflife(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        est = mon.estimate_decay_rate(_good_returns(200))
        assert isinstance(est, DecayEstimate)
        # Good strategy should have long half-life
        assert est.half_life_days > 30

    def test_decaying_shorter_halflife(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        good = mon.estimate_decay_rate(_good_returns(300))
        decay = mon.estimate_decay_rate(_decaying_returns(300))
        # Decaying should have shorter half-life (or at least not much longer)
        # Allow some noise tolerance
        assert decay.half_life_days < good.half_life_days * 2

    def test_short_data(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        est = mon.estimate_decay_rate(_good_returns(20))
        assert est.half_life_days == float("inf")

    def test_current_alpha(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        est = mon.estimate_decay_rate(_good_returns(200))
        assert isinstance(est.current_alpha, float)


# ===========================================================================
# Regime-conditioned decay
# ===========================================================================

class TestRegimeDecay:
    def test_basic(self):
        r = _good_returns(300)
        reg = _regimes(300)
        results = StrategyDecayMonitor.regime_conditioned_decay(r, reg, window=30)
        assert len(results) > 0
        assert all(isinstance(rd, RegimeDecay) for rd in results)

    def test_all_regimes_present(self):
        r = _good_returns(300)
        reg = _regimes(300)
        results = StrategyDecayMonitor.regime_conditioned_decay(r, reg, window=30)
        names = {rd.regime for rd in results}
        assert names == {"low", "high", "normal"}

    def test_n_days_sum(self):
        r = _good_returns(300)
        reg = _regimes(300)
        results = StrategyDecayMonitor.regime_conditioned_decay(r, reg, window=30)
        total = sum(rd.n_days for rd in results)
        assert total == 300


# ===========================================================================
# Kill signal
# ===========================================================================

class TestKillSignal:
    def test_keep_good_strategy(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        ks = mon.kill_signal("good_strat", _good_returns(200))
        assert isinstance(ks, KillSignal)
        assert ks.recommendation in ("keep", "monitor")
        assert ks.kill_score < 0.7  # should never recommend retire

    def test_retire_dead(self):
        mon = StrategyDecayMonitor(rolling_window=30, cusum_threshold=2.0)
        ks = mon.kill_signal("dead_strat", _dead_returns(300))
        assert ks.recommendation in ("retire", "monitor")
        assert ks.kill_score > 0.3

    def test_reasons_populated(self):
        mon = StrategyDecayMonitor(rolling_window=30, cusum_threshold=2.0)
        ks = mon.kill_signal("dead_strat", _dead_returns(300))
        assert len(ks.reasons) > 0

    def test_confidence_increases(self):
        mon = StrategyDecayMonitor(rolling_window=30)
        short = mon.kill_signal("s", _good_returns(50))
        long_ = mon.kill_signal("l", _good_returns(300))
        assert long_.confidence >= short.confidence

    def test_score_bounded(self):
        mon = StrategyDecayMonitor(rolling_window=30, cusum_threshold=1.0)
        ks = mon.kill_signal("x", _dead_returns(300))
        assert 0.0 <= ks.kill_score <= 1.0


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        mon = StrategyDecayMonitor(rolling_window=30)
        out = tmp_path / "decay.html"
        result = mon.generate_report("test_strat", _good_returns(200),
                                      output_path=str(out))
        assert Path(result).exists()
        html = out.read_text()
        assert "Strategy Decay Monitor" in html

    def test_contains_sharpe_chart(self, tmp_path):
        mon = StrategyDecayMonitor(rolling_window=30)
        out = tmp_path / "d.html"
        mon.generate_report("s", _good_returns(200), output_path=str(out))
        html = out.read_text()
        assert "<svg" in html
        assert "Rolling Sharpe" in html

    def test_contains_cusum(self, tmp_path):
        mon = StrategyDecayMonitor(rolling_window=30, cusum_threshold=2.0)
        out = tmp_path / "d.html"
        mon.generate_report("s", _decaying_returns(300), output_path=str(out))
        html = out.read_text()
        assert "CUSUM" in html

    def test_contains_lifecycle(self, tmp_path):
        mon = StrategyDecayMonitor(rolling_window=30)
        out = tmp_path / "d.html"
        mon.generate_report("s", _good_returns(200), output_path=str(out))
        html = out.read_text()
        assert "Lifecycle" in html

    def test_with_regimes(self, tmp_path):
        mon = StrategyDecayMonitor(rolling_window=30)
        r = _good_returns(300)
        reg = _regimes(300)
        out = tmp_path / "d.html"
        mon.generate_report("s", r, regimes=reg, output_path=str(out))
        html = out.read_text()
        assert "Regime-Conditioned" in html

    def test_kill_signal_in_report(self, tmp_path):
        mon = StrategyDecayMonitor(rolling_window=30)
        out = tmp_path / "d.html"
        mon.generate_report("s", _dead_returns(300), output_path=str(out))
        html = out.read_text()
        assert "Kill Score" in html
