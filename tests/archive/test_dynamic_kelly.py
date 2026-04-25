"""Tests for compass.dynamic_kelly — adaptive Kelly position sizing."""
from __future__ import annotations

import math

import numpy as np
import pytest

from compass.dynamic_kelly import (
    REGIME_KELLY_MULT,
    DynamicKellyBacktest,
    DynamicKellyResult,
    DynamicKellyTracker,
    KellyEstimate,
    SizingComparison,
    apply_regime_fraction,
    classify_regime,
    generate_strategy_data,
    kelly_fraction,
    rolling_kelly,
)


def _data(n=600, seed=42):
    return generate_strategy_data(n, seed)


# ── kelly_fraction ──────────────────────────────────────────────────────────
class TestKellyFraction:
    def test_positive_edge(self):
        # 60% win rate, 1:1 payoff → f = 0.6 - 0.4/1 = 0.20
        assert kelly_fraction(0.60, 1.0) == pytest.approx(0.20)

    def test_no_edge_zero(self):
        # 50% win rate, 1:1 → f = 0
        assert kelly_fraction(0.50, 1.0) == pytest.approx(0.0)

    def test_high_payoff(self):
        # 40% WR, 3:1 payoff → f = 0.4 - 0.6/3 = 0.2
        assert kelly_fraction(0.40, 3.0) == pytest.approx(0.20)

    def test_negative_edge_zero(self):
        assert kelly_fraction(0.30, 1.0) == 0.0

    def test_zero_payoff_zero(self):
        assert kelly_fraction(0.60, 0.0) == 0.0

    def test_perfect_win_rate(self):
        assert kelly_fraction(1.0, 2.0) == 0.0  # edge case: wr=1

    def test_typical_credit_spread(self):
        # 80% WR, 0.5 payoff (win $50, lose $100) → f = 0.8 - 0.2/0.5 = 0.40
        f = kelly_fraction(0.80, 0.50)
        assert f == pytest.approx(0.40)


# ── rolling_kelly ───────────────────────────────────────────────────────────
class TestRollingKelly:
    def test_lengths(self):
        ret, _, _ = _data(200)
        k, wr, pr = rolling_kelly(ret, 20)
        assert len(k) == len(wr) == len(pr) == 200

    def test_zero_before_window(self):
        ret, _, _ = _data(100)
        k, _, _ = rolling_kelly(ret, 30)
        assert k[10] == 0.0

    def test_kelly_nonnegative(self):
        ret, _, _ = _data(300)
        k, _, _ = rolling_kelly(ret, 20)
        assert np.all(k >= 0)

    def test_win_rate_bounded(self):
        ret, _, _ = _data(200)
        _, wr, _ = rolling_kelly(ret, 20)
        assert np.all(wr >= 0) and np.all(wr <= 1)

    def test_positive_returns_high_kelly(self):
        rng = np.random.RandomState(42)
        ret = np.abs(rng.randn(100)) * 0.01 + 0.001
        ret[30] = -0.002  # one loss for defined payoff ratio
        k, wr, _ = rolling_kelly(ret, 20)
        assert wr[50] > 0.8


# ── classify_regime ─────────────────────────────────────────────────────────
class TestRegime:
    def test_crash(self):
        assert classify_regime(40.0, -0.02) == "crash"

    def test_high_vol(self):
        assert classify_regime(28.0, 0.0) == "high_vol"

    def test_bear(self):
        assert classify_regime(20.0, -0.08) == "bear"

    def test_low_vol(self):
        assert classify_regime(12.0, 0.02) == "low_vol"

    def test_bull(self):
        assert classify_regime(18.0, 0.03) == "bull"


# ── apply_regime_fraction ──────────────────────────────────────────────────
class TestRegimeFraction:
    def test_crash_lowest(self):
        crash = apply_regime_fraction(0.40, "crash")
        bull = apply_regime_fraction(0.40, "bull")
        assert crash < bull

    def test_bounded(self):
        for regime in REGIME_KELLY_MULT:
            f = apply_regime_fraction(0.50, regime)
            assert 0.0 <= f <= 1.0

    def test_zero_kelly_stays_zero(self):
        assert apply_regime_fraction(0.0, "bull") == 0.0

    def test_unknown_regime_moderate(self):
        f = apply_regime_fraction(0.40, "unknown_regime")
        assert 0.0 < f < 0.40


# ── DynamicKellyTracker ───────────────────────────────────────────────────
class TestTracker:
    def test_returns_estimates(self):
        ret, vix, dates = _data(300)
        t = DynamicKellyTracker()
        est = t.compute(ret, vix, dates)
        assert len(est) > 0

    def test_estimate_fields(self):
        ret, vix, dates = _data(300)
        est = DynamicKellyTracker().compute(ret, vix, dates)
        e = est[0]
        assert isinstance(e, KellyEstimate)
        assert 0 <= e.win_rate <= 1
        assert e.full_kelly >= 0
        assert e.fractional_kelly >= 0
        assert e.regime in REGIME_KELLY_MULT

    def test_fractional_leq_full(self):
        ret, vix, dates = _data(400)
        for e in DynamicKellyTracker().compute(ret, vix, dates):
            assert e.fractional_kelly <= e.full_kelly + 0.001

    def test_too_short(self):
        ret, vix, dates = _data(50)
        assert DynamicKellyTracker().compute(ret, vix, dates) == []

    def test_custom_windows(self):
        ret, vix, dates = _data(300)
        t = DynamicKellyTracker(windows=(10, 30, 60))
        est = t.compute(ret, vix, dates)
        assert len(est) > 0

    def test_crisis_reduces_kelly(self):
        ret, vix, dates = _data(600)
        est = DynamicKellyTracker().compute(ret, vix, dates)
        crisis = [e for e in est if e.regime == "crash"]
        bull = [e for e in est if e.regime == "bull"]
        if crisis and bull:
            avg_crisis = np.mean([e.fractional_kelly for e in crisis])
            avg_bull = np.mean([e.fractional_kelly for e in bull])
            assert avg_crisis <= avg_bull + 0.01


# ── Backtest ────────────────────────────────────────────────────────────────
class TestBacktest:
    def test_returns_result(self):
        ret, vix, dates = _data(300)
        r = DynamicKellyBacktest().run(ret, vix, dates)
        assert isinstance(r, DynamicKellyResult)

    def test_five_comparisons(self):
        ret, vix, dates = _data(400)
        r = DynamicKellyBacktest().run(ret, vix, dates)
        assert len(r.comparisons) == 5
        methods = {c.method for c in r.comparisons}
        assert "dynamic_kelly" in methods
        assert "fixed_kelly" in methods
        assert "risk_parity" in methods
        assert "equal_weight" in methods
        assert "fixed_5pct" in methods

    def test_best_method_set(self):
        ret, vix, dates = _data(400)
        r = DynamicKellyBacktest().run(ret, vix, dates)
        assert r.best_method in {c.method for c in r.comparisons}

    def test_kelly_history(self):
        ret, vix, dates = _data(400)
        r = DynamicKellyBacktest().run(ret, vix, dates)
        assert len(r.kelly_history) > 0

    def test_sharpe_finite(self):
        ret, vix, dates = _data(400)
        r = DynamicKellyBacktest().run(ret, vix, dates)
        for c in r.comparisons:
            assert math.isfinite(c.sharpe)

    def test_max_dd_nonneg(self):
        ret, vix, dates = _data(400)
        r = DynamicKellyBacktest().run(ret, vix, dates)
        for c in r.comparisons:
            assert c.max_dd_pct >= 0

    def test_costs_tracked(self):
        ret, vix, dates = _data(400)
        r = DynamicKellyBacktest(cost_bps=10.0).run(ret, vix, dates)
        dk = next(c for c in r.comparisons if c.method == "dynamic_kelly")
        assert dk.total_cost > 0

    def test_generated_at(self):
        ret, vix, dates = _data(200)
        r = DynamicKellyBacktest().run(ret, vix, dates)
        assert len(r.generated_at) > 0

    def test_too_short(self):
        ret, vix, dates = _data(50)
        r = DynamicKellyBacktest().run(ret, vix, dates)
        assert len(r.comparisons) == 0

    def test_avg_size_bounded(self):
        ret, vix, dates = _data(400)
        r = DynamicKellyBacktest(min_size=0.02, max_size=0.30).run(ret, vix, dates)
        dk = next(c for c in r.comparisons if c.method == "dynamic_kelly")
        assert dk.avg_position_size >= 0.02
        assert dk.avg_position_size <= 0.30


# ── Synthetic data ──────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_lengths(self):
        r, v, d = generate_strategy_data(100)
        assert len(r) == len(v) == len(d) == 100

    def test_deterministic(self):
        a = generate_strategy_data(50, seed=99)
        b = generate_strategy_data(50, seed=99)
        np.testing.assert_array_equal(a[0], b[0])

    def test_vix_bounded(self):
        _, vix, _ = generate_strategy_data(500)
        assert np.all(vix >= 9) and np.all(vix <= 70)


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_kelly_estimate(self):
        k = KellyEstimate("d", 0.6, 0.01, 0.005, 2.0, 0.30, 0.15, "bull", 20)
        assert k.full_kelly == 0.30

    def test_sizing_comparison(self):
        s = SizingComparison("dynamic_kelly", 50.0, 8.0, 2.5, 5.0, 0.10, 100.0)
        assert s.sharpe == 2.5

    def test_result_defaults(self):
        r = DynamicKellyResult()
        assert r.kelly_history == []
        assert r.best_method == ""
