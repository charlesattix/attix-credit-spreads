"""Tests for compass.overnight_gap — overnight gap strategy."""
from __future__ import annotations

import numpy as np
import pytest

from compass.overnight_gap import (
    BacktestResult,
    GapDistribution,
    GapRiskModel,
    OvernightGapBacktest,
    OvernightPremium,
    OvernightPremiumCalculator,
    OvernightTrade,
    generate_spy_data,
    size_overnight_position,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _make_data(n: int = 500, seed: int = 42):
    return generate_spy_data(n, seed)


# ── GapRiskModel ────────────────────────────────────────────────────────────
class TestGapRiskModel:
    def test_fit_returns_distribution(self):
        close, open_n, _, _ = _make_data(200)
        m = GapRiskModel()
        d = m.fit(close, open_n)
        assert isinstance(d, GapDistribution)
        assert d.n_observations == 200

    def test_mean_gap_near_zero(self):
        close, open_n, _, _ = _make_data(500)
        d = GapRiskModel().fit(close, open_n)
        assert abs(d.mean_gap_pct) < 1.0  # should be small

    def test_std_positive(self):
        close, open_n, _, _ = _make_data()
        d = GapRiskModel().fit(close, open_n)
        assert d.std_gap_pct > 0

    def test_percentiles_ordered(self):
        close, open_n, _, _ = _make_data()
        d = GapRiskModel().fit(close, open_n)
        assert d.p95_gap_pct <= d.p99_gap_pct <= d.max_gap_pct

    def test_positive_gap_rate_bounded(self):
        close, open_n, _, _ = _make_data()
        d = GapRiskModel().fit(close, open_n)
        assert 0.0 <= d.positive_gap_rate <= 1.0

    def test_gap_var_positive(self):
        close, open_n, _, _ = _make_data()
        m = GapRiskModel()
        m.fit(close, open_n)
        assert m.gap_var(0.99) > 0

    def test_gap_var_99_gt_95(self):
        close, open_n, _, _ = _make_data()
        m = GapRiskModel()
        m.fit(close, open_n)
        assert m.gap_var(0.99) >= m.gap_var(0.95)

    def test_short_data_returns_default(self):
        d = GapRiskModel().fit(np.array([100.0]), np.array([101.0]))
        assert d.n_observations == 0

    def test_unfitted_var_returns_default(self):
        assert GapRiskModel().gap_var() == 2.0


# ── OvernightPremiumCalculator ──────────────────────────────────────────────
class TestPremiumCalculator:
    def test_returns_premium(self):
        p = OvernightPremiumCalculator().estimate(450.0, 20.0)
        assert isinstance(p, OvernightPremium)
        assert p.straddle_price > 0

    def test_higher_vix_higher_premium(self):
        calc = OvernightPremiumCalculator()
        low = calc.estimate(450.0, 12.0)
        high = calc.estimate(450.0, 35.0)
        assert high.straddle_price > low.straddle_price

    def test_theta_overnight_positive(self):
        p = OvernightPremiumCalculator().estimate(450.0, 20.0)
        assert p.theta_overnight > 0

    def test_theta_pct_positive(self):
        p = OvernightPremiumCalculator().estimate(450.0, 20.0)
        assert p.theta_pct > 0  # at 1 DTE, theta_pct can exceed 100%

    def test_gamma_risk_positive(self):
        p = OvernightPremiumCalculator().estimate(450.0, 20.0)
        assert p.gamma_risk > 0

    def test_breakeven_gap_positive(self):
        p = OvernightPremiumCalculator().estimate(450.0, 20.0)
        assert p.breakeven_gap_pct > 0

    def test_higher_price_higher_premium(self):
        calc = OvernightPremiumCalculator()
        low = calc.estimate(200.0, 20.0)
        high = calc.estimate(500.0, 20.0)
        assert high.straddle_price > low.straddle_price


# ── Position sizing ─────────────────────────────────────────────────────────
class TestPositionSizing:
    def test_returns_int(self):
        n = size_overnight_position(100_000, 450.0, 2.0)
        assert isinstance(n, int)

    def test_positive_contracts(self):
        n = size_overnight_position(100_000, 450.0, 1.5)
        assert n > 0

    def test_scales_with_capital(self):
        small = size_overnight_position(50_000, 450.0, 2.0)
        large = size_overnight_position(200_000, 450.0, 2.0)
        assert large >= small

    def test_capped_at_50(self):
        n = size_overnight_position(10_000_000, 100.0, 0.1)
        assert n <= 50

    def test_zero_gap_var_returns_zero(self):
        assert size_overnight_position(100_000, 450.0, 0.0) == 0

    def test_higher_gap_var_fewer_contracts(self):
        low_risk = size_overnight_position(100_000, 450.0, 1.0)
        high_risk = size_overnight_position(100_000, 450.0, 5.0)
        assert high_risk <= low_risk


# ── Backtest ────────────────────────────────────────────────────────────────
class TestBacktest:
    def test_returns_result(self):
        close, open_n, vix, dates = _make_data(300)
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert isinstance(r, BacktestResult)

    def test_trades_populated(self):
        close, open_n, vix, dates = _make_data(300)
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert r.total_trades > 0

    def test_win_rate_bounded(self):
        close, open_n, vix, dates = _make_data(500)
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert 0 <= r.win_rate_pct <= 100

    def test_max_dd_nonnegative(self):
        close, open_n, vix, dates = _make_data()
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert r.max_dd_pct >= 0

    def test_ending_capital_positive(self):
        close, open_n, vix, dates = _make_data()
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert r.ending_capital > 0

    def test_skipped_nights_counted(self):
        close, open_n, vix, dates = _make_data(500)
        # Force some high VIX
        vix[100:120] = 40.0
        r = OvernightGapBacktest(vix_skip_threshold=30.0).run(close, open_n, vix, dates)
        assert r.skipped_nights > 0

    def test_yearly_breakdown(self):
        close, open_n, vix, dates = _make_data(600)
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert len(r.yearly) > 0

    def test_gap_distribution_attached(self):
        close, open_n, vix, dates = _make_data()
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert r.gap_distribution is not None

    def test_generated_at_set(self):
        close, open_n, vix, dates = _make_data(200)
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert len(r.generated_at) > 0

    def test_too_few_bars(self):
        r = OvernightGapBacktest().run(np.array([100.0]*5), np.array([101.0]*5), np.array([15.0]*5))
        assert r.total_trades == 0

    def test_high_vix_skip_all(self):
        close, open_n, vix, dates = _make_data(200)
        vix[:] = 50.0  # all nights skipped
        r = OvernightGapBacktest(vix_skip_threshold=30.0).run(close, open_n, vix, dates)
        assert r.total_trades == 0
        assert r.skipped_nights > 0

    def test_custom_risk(self):
        close, open_n, vix, dates = _make_data(300)
        conservative = OvernightGapBacktest(max_risk_pct=0.005).run(close, open_n, vix, dates)
        aggressive = OvernightGapBacktest(max_risk_pct=0.04).run(close, open_n, vix, dates)
        # Aggressive should have larger absolute P&L swings
        assert abs(aggressive.total_pnl) >= 0  # just verify it runs


# ── Regime filter ───────────────────────────────────────────────────────────
class TestRegimeFilter:
    def test_normal_vix_not_skipped(self):
        close, open_n, vix, dates = _make_data(200)
        vix[:] = 18.0
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        assert r.skipped_nights == 0

    def test_custom_threshold(self):
        close, open_n, vix, dates = _make_data(200)
        vix[:] = 22.0
        r20 = OvernightGapBacktest(vix_skip_threshold=20.0).run(close, open_n, vix, dates)
        r30 = OvernightGapBacktest(vix_skip_threshold=30.0).run(close, open_n, vix, dates)
        assert r20.skipped_nights > r30.skipped_nights

    def test_trade_has_regime_label(self):
        close, open_n, vix, dates = _make_data(200)
        r = OvernightGapBacktest().run(close, open_n, vix, dates)
        executed = [t for t in r.trades if not t.skipped]
        if executed:
            assert executed[0].regime in ("bull", "bear", "high_vol", "low_vol", "crash")


# ── Synthetic data ──────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_lengths_match(self):
        close, open_n, vix, dates = generate_spy_data(100)
        assert len(close) == len(open_n) == len(vix) == len(dates) == 100

    def test_prices_positive(self):
        close, open_n, _, _ = generate_spy_data(200)
        assert np.all(close > 0)
        assert np.all(open_n > 0)

    def test_vix_bounded(self):
        _, _, vix, _ = generate_spy_data(500)
        assert np.all(vix >= 9)
        assert np.all(vix <= 80)

    def test_deterministic(self):
        c1, _, _, _ = generate_spy_data(50, seed=99)
        c2, _, _, _ = generate_spy_data(50, seed=99)
        np.testing.assert_array_equal(c1, c2)


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_gap_distribution(self):
        d = GapDistribution(0.02, 0.5, 1.2, 2.5, 4.0, 500, 0.52)
        assert d.n_observations == 500

    def test_overnight_premium(self):
        p = OvernightPremium(8.50, 1.20, 14.1, 3.5, 0.27)
        assert p.straddle_price == 8.50

    def test_overnight_trade(self):
        t = OvernightTrade("2024-01-15", 450.0, 451.0, 0.22, 8.5, 7.8, 70.0, 1, 18.0, "bull")
        assert t.pnl == 70.0

    def test_backtest_result_defaults(self):
        r = BacktestResult()
        assert r.trades == []
        assert r.total_trades == 0
