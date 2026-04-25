"""Tests for compass/tail_risk_hedge.py — Dynamic Tail Risk Hedge.

Covers: crisis detection, SPY puts, VIX calls, portfolio delta,
cost budget, leverage management, stress tests, report generation.
"""

import math

import numpy as np
import pandas as pd
import pytest

from compass.tail_risk_hedge import (
    TailRiskHedgeConfig,
    TailRiskHedgeEngine,
    HedgeDayState,
    BacktestResult,
    CrisisScenario,
    ScenarioResult,
    generate_market_data,
    get_crisis_scenarios,
    generate_report,
    run_full_analysis,
    _compute_full_metrics,
    _yearly_breakdown,
    _build_crisis_path,
    TRADING_DAYS,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def config():
    return TailRiskHedgeConfig()


@pytest.fixture
def engine(config):
    return TailRiskHedgeEngine(config)


@pytest.fixture
def calm_data():
    """100 days of calm market (VIX ~14, steady returns)."""
    idx = pd.bdate_range("2023-01-02", periods=100)
    return {
        "portfolio_returns": pd.Series(np.full(100, 0.002), index=idx),
        "spy_returns": pd.Series(np.full(100, 0.001), index=idx),
        "vix": pd.Series(np.full(100, 14.0), index=idx),
        "vix3m": pd.Series(np.full(100, 16.0), index=idx),
    }


@pytest.fixture
def crisis_data():
    """100 days with embedded VIX spike and crash."""
    idx = pd.bdate_range("2023-01-02", periods=100)
    vix = np.concatenate([
        np.full(30, 14.0),
        np.linspace(14, 70, 20),
        np.full(20, 55.0),
        np.linspace(55, 18, 30),
    ])
    vix3m = np.concatenate([
        np.full(30, 16.0),
        np.linspace(16, 40, 20),
        np.full(20, 45.0),
        np.linspace(45, 19, 30),
    ])
    port_ret = np.concatenate([
        np.full(30, 0.002),
        np.full(20, -0.025),
        np.full(20, -0.005),
        np.full(30, 0.003),
    ])
    spy_ret = np.concatenate([
        np.full(30, 0.001),
        np.full(20, -0.02),
        np.full(20, -0.003),
        np.full(30, 0.002),
    ])
    return {
        "portfolio_returns": pd.Series(port_ret, index=idx),
        "spy_returns": pd.Series(spy_ret, index=idx),
        "vix": pd.Series(vix, index=idx),
        "vix3m": pd.Series(vix3m, index=idx),
    }


@pytest.fixture
def full_data():
    """6 years of calibrated market data."""
    return generate_market_data(n_years=6.0, seed=42)


# ═══════════════════════════════════════════════════════════════════════════
# Config Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestConfig:
    def test_default_leverage(self, config):
        assert config.normal_leverage == 1.6
        assert config.crisis_leverage == 0.4
        assert config.min_leverage == 0.2

    def test_cost_budget(self, config):
        assert config.annual_cost_budget_pct == 2.0

    def test_put_params(self, config):
        assert config.put_buy_vix_threshold == 20.0
        assert config.put_base_pct == 0.60

    def test_vix_call_params(self, config):
        assert config.vix_call_base_pct == 0.40
        assert config.vix_call_payoff_multiplier == 20.0

    def test_ts_thresholds(self, config):
        assert config.ts_inversion_threshold == 1.0
        assert config.ts_deep_inversion > config.ts_inversion_threshold

    def test_crisis_thresholds_ordered(self, config):
        assert config.vix_crisis_threshold > config.vix_elevated_threshold
        assert config.dd_crisis_threshold > config.dd_elevated_threshold

    def test_hedge_ratios(self, config):
        assert config.target_hedge_ratio < config.crisis_hedge_ratio
        assert 0 < config.target_hedge_ratio <= 1.0
        assert 0 < config.crisis_hedge_ratio <= 1.0

    def test_custom_config(self):
        cfg = TailRiskHedgeConfig(normal_leverage=2.0, annual_cost_budget_pct=3.0)
        assert cfg.normal_leverage == 2.0
        assert cfg.annual_cost_budget_pct == 3.0


# ═══════════════════════════════════════════════════════════════════════════
# Crisis Score Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrisisScore:
    def test_calm_market_low_score(self, engine):
        score = engine._crisis_score(vix=14, vix_ratio=0.88, drawdown=0.01,
                                     realized_vol=0.10, momentum_10d=0.02)
        assert score < 0.1

    def test_crisis_market_high_score(self, engine):
        score = engine._crisis_score(vix=50, vix_ratio=1.3, drawdown=0.12,
                                     realized_vol=0.40, momentum_10d=-0.05)
        assert score > 0.8

    def test_elevated_market_medium_score(self, engine):
        score = engine._crisis_score(vix=25, vix_ratio=1.05, drawdown=0.04,
                                     realized_vol=0.20, momentum_10d=-0.01)
        assert 0.2 < score < 0.8

    def test_score_bounded(self, engine):
        assert 0 <= engine._crisis_score(0, 0.5, 0, 0.01, 0.10) <= 1
        assert 0 <= engine._crisis_score(100, 2.0, 0.50, 1.0, -0.20) <= 1

    def test_vix_spike_increases_score(self, engine):
        base = engine._crisis_score(14, 0.88, 0.01, 0.10, 0.01)
        spike = engine._crisis_score(40, 0.88, 0.01, 0.10, 0.01)
        assert spike > base

    def test_term_structure_inversion_increases_score(self, engine):
        contango = engine._crisis_score(20, 0.85, 0.01, 0.10, 0.01)
        inverted = engine._crisis_score(20, 1.20, 0.01, 0.10, 0.01)
        assert inverted > contango

    def test_drawdown_increases_score(self, engine):
        lo = engine._crisis_score(20, 0.90, 0.01, 0.12, 0.01)
        hi = engine._crisis_score(20, 0.90, 0.08, 0.12, 0.01)
        assert hi > lo

    def test_negative_momentum_increases_score(self, engine):
        pos = engine._crisis_score(20, 0.90, 0.03, 0.12, 0.02)
        neg = engine._crisis_score(20, 0.90, 0.03, 0.12, -0.04)
        assert neg > pos


# ═══════════════════════════════════════════════════════════════════════════
# Portfolio Delta Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPortfolioDelta:
    def test_returns_leverage_with_short_history(self):
        delta = TailRiskHedgeEngine._estimate_portfolio_delta(
            1.6, np.array([0.01]), np.array([0.01]), 20)
        assert delta == 1.6  # fallback

    def test_positive_beta_increases_delta(self):
        spy = np.random.RandomState(1).normal(0, 0.01, 50)
        port = spy * 1.5 + np.random.RandomState(2).normal(0, 0.002, 50)
        delta = TailRiskHedgeEngine._estimate_portfolio_delta(1.6, spy, port, 20)
        assert delta > 1.0

    def test_uncorrelated_lower_delta(self):
        rng = np.random.RandomState(42)
        spy = rng.normal(0, 0.01, 50)
        port = rng.normal(0.001, 0.01, 50)  # independent
        delta = TailRiskHedgeEngine._estimate_portfolio_delta(1.6, spy, port, 20)
        # Uncorrelated → beta near 0 → delta near 0
        assert delta < 2.0

    def test_delta_clamped(self):
        # Huge beta should be clamped
        spy = np.array([0.01] * 20)
        port = np.array([0.05] * 20)  # 5x beta
        delta = TailRiskHedgeEngine._estimate_portfolio_delta(1.6, spy, port, 20)
        assert delta <= 1.6 * 3.0  # max clamp


# ═══════════════════════════════════════════════════════════════════════════
# Hedge Allocation & Cost Budget Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHedgeAllocation:
    def test_daily_budget_from_annual(self, engine):
        _, _, _, budget = engine._compute_hedge_allocation(
            crisis_score=0.5, vix=20, vix_ratio=0.9,
            portfolio_value=100_000, portfolio_delta=1.6)
        expected = 100_000 * 0.02 / 252
        assert abs(budget - expected) < 0.01

    def test_total_cost_within_budget(self, engine):
        """Combined put + VIX call cost should not exceed daily budget."""
        put_cost, vix_cost, _, budget = engine._compute_hedge_allocation(
            crisis_score=1.0, vix=50, vix_ratio=1.2,
            portfolio_value=100_000, portfolio_delta=2.0)
        assert put_cost + vix_cost <= budget + 0.01

    def test_higher_crisis_score_higher_hedge_ratio(self, engine):
        _, _, ratio_calm, _ = engine._compute_hedge_allocation(
            0.0, 14, 0.88, 100_000, 1.6)
        _, _, ratio_crisis, _ = engine._compute_hedge_allocation(
            1.0, 50, 1.2, 100_000, 1.6)
        assert ratio_crisis > ratio_calm

    def test_inversion_boosts_cost(self, engine):
        c1, v1, _, _ = engine._compute_hedge_allocation(
            0.5, 25, 0.90, 100_000, 1.6)
        c2, v2, _, _ = engine._compute_hedge_allocation(
            0.5, 25, 1.10, 100_000, 1.6)
        assert (c2 + v2) > (c1 + v1)

    def test_low_vix_favours_puts(self, engine):
        put_cost, vix_cost, _, _ = engine._compute_hedge_allocation(
            0.3, 12, 0.85, 100_000, 1.6)
        # At low VIX, put_frac = 0.60 > vix_frac = 0.40
        if put_cost + vix_cost > 0:
            assert put_cost >= vix_cost * 0.9  # roughly 60/40 split

    def test_high_vix_shifts_to_vix_calls(self, engine):
        put_low, vix_low, _, _ = engine._compute_hedge_allocation(
            0.5, 12, 0.85, 100_000, 1.6)
        put_high, vix_high, _, _ = engine._compute_hedge_allocation(
            0.5, 40, 0.95, 100_000, 1.6)
        # At high VIX, put fraction decreases
        if put_high + vix_high > 0 and put_low + vix_low > 0:
            ratio_low = put_low / (put_low + vix_low)
            ratio_high = put_high / (put_high + vix_high)
            assert ratio_high <= ratio_low


# ═══════════════════════════════════════════════════════════════════════════
# Put Payoff Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPutPayoff:
    def test_no_payoff_on_up_day(self, engine):
        assert engine._put_payoff(10.0, 0.01, 100_000) == 0.0

    def test_no_payoff_on_small_drop(self, engine):
        assert engine._put_payoff(10.0, -0.003, 100_000) == 0.0

    def test_payoff_on_crash(self, engine):
        payoff = engine._put_payoff(10.0, -0.05, 100_000)
        assert payoff > 0

    def test_bigger_crash_bigger_payoff(self, engine):
        small = engine._put_payoff(10.0, -0.02, 100_000)
        big = engine._put_payoff(10.0, -0.08, 100_000)
        assert big > small

    def test_payoff_capped(self, engine):
        payoff = engine._put_payoff(1000.0, -0.50, 100_000)
        assert payoff <= 100_000 * 0.08

    def test_convexity_bonus_above_3pct(self, engine):
        p3 = engine._put_payoff(10.0, -0.03, 100_000)
        p5 = engine._put_payoff(10.0, -0.05, 100_000)
        # 5% drop should be disproportionately more than 5/3 * p3
        ratio = p5 / max(p3, 0.01)
        assert ratio > 5 / 3


# ═══════════════════════════════════════════════════════════════════════════
# VIX Call Payoff Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestVIXCallPayoff:
    def test_no_payoff_when_vix_drops(self, engine):
        assert engine._vix_call_payoff(10.0, 14.0, 16.0, 100_000) == 0.0

    def test_no_payoff_on_small_vix_move(self, engine):
        assert engine._vix_call_payoff(10.0, 14.5, 14.0, 100_000) == 0.0

    def test_payoff_on_vix_spike(self, engine):
        payoff = engine._vix_call_payoff(10.0, 40.0, 15.0, 100_000)
        assert payoff > 0

    def test_bigger_spike_bigger_payoff(self, engine):
        small = engine._vix_call_payoff(10.0, 25.0, 15.0, 100_000)
        big = engine._vix_call_payoff(10.0, 60.0, 15.0, 100_000)
        assert big > small

    def test_payoff_capped(self, engine):
        payoff = engine._vix_call_payoff(1000.0, 200.0, 10.0, 100_000)
        assert payoff <= 100_000 * 0.10

    def test_massive_convexity_on_huge_spike(self, engine):
        """VIX 14→80 should produce enormous payoff relative to cost."""
        payoff = engine._vix_call_payoff(5.0, 80.0, 14.0, 100_000)
        assert payoff > 5.0 * 10  # at least 10x the cost


# ═══════════════════════════════════════════════════════════════════════════
# Leverage Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLeverage:
    def test_normal_in_calm(self, engine):
        assert engine._target_leverage(0.0) == 1.6

    def test_crisis_at_max(self, engine):
        assert engine._target_leverage(1.0) == 0.4

    def test_interpolated(self, engine):
        lev = engine._target_leverage(0.5)
        assert 0.4 < lev < 1.6

    def test_monotonically_decreasing(self, engine):
        scores = [0.0, 0.1, 0.15, 0.3, 0.5, 0.7, 0.8, 1.0]
        levs = [engine._target_leverage(s) for s in scores]
        for i in range(1, len(levs)):
            assert levs[i] <= levs[i - 1]


# ═══════════════════════════════════════════════════════════════════════════
# Market Data Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMarketData:
    def test_length(self):
        data = generate_market_data(n_years=1.0, seed=1)
        assert len(data["portfolio_returns"]) == TRADING_DAYS

    def test_all_same_length(self):
        data = generate_market_data(n_years=2.0, seed=1)
        n = len(data["portfolio_returns"])
        for key in ["spy_returns", "vix", "vix3m"]:
            assert len(data[key]) == n

    def test_vix_realistic_range(self):
        data = generate_market_data(n_years=6.0, seed=42)
        assert data["vix"].min() >= 8
        assert data["vix"].max() <= 90

    def test_deterministic(self):
        d1 = generate_market_data(seed=123)
        d2 = generate_market_data(seed=123)
        np.testing.assert_array_equal(d1["portfolio_returns"].values,
                                      d2["portfolio_returns"].values)

    def test_covid_embedded(self):
        data = generate_market_data(n_years=6.0, seed=42)
        assert data["portfolio_returns"].values[40:63].mean() < -0.01

    def test_bear_embedded(self):
        data = generate_market_data(n_years=6.0, seed=42)
        assert data["portfolio_returns"].values[500:690].mean() < 0


# ═══════════════════════════════════════════════════════════════════════════
# Crisis Scenario Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrisisScenarios:
    def test_all_defined(self):
        s = get_crisis_scenarios()
        for name in ["COVID_2020", "BEAR_2022", "FLASH_CRASH", "CHINA_2015", "VOLMAGEDDON_2018"]:
            assert name in s

    def test_covid_23_days(self):
        assert get_crisis_scenarios()["COVID_2020"].n_days == 23

    def test_flash_crash_1_day(self):
        assert get_crisis_scenarios()["FLASH_CRASH"].n_days == 1

    def test_paths_correct_length(self):
        for name, s in get_crisis_scenarios().items():
            assert len(s.spy_shocks) == s.n_days
            assert len(s.vix_path) == s.n_days
            assert len(s.vix3m_path) == s.n_days

    def test_covid_total_return(self):
        s = get_crisis_scenarios()["COVID_2020"]
        total = float(np.prod(1 + s.spy_shocks) - 1)
        assert -0.40 < total < -0.28

    def test_covid_vix_spikes(self):
        assert get_crisis_scenarios()["COVID_2020"].vix_path.max() > 60


# ═══════════════════════════════════════════════════════════════════════════
# Backtest — Calm Market
# ═══════════════════════════════════════════════════════════════════════════


class TestBacktestCalm:
    def test_positive_returns(self, engine, calm_data):
        r = engine.backtest(calm_data)
        assert r.total_return_pct > 0

    def test_leverage_near_normal(self, engine, calm_data):
        r = engine.backtest(calm_data)
        assert r.avg_leverage > 1.2

    def test_low_dd(self, engine, calm_data):
        r = engine.backtest(calm_data)
        assert r.max_dd_pct < 5

    def test_mostly_normal_regime(self, engine, calm_data):
        r = engine.backtest(calm_data)
        assert r.normal_days > 70

    def test_hedge_active(self, engine, calm_data):
        r = engine.backtest(calm_data)
        active = sum(1 for s in r.states if s.hedge_active)
        assert active == len(r.states)

    def test_equity_curve_length(self, engine, calm_data):
        r = engine.backtest(calm_data)
        assert len(r.equity_curve) == 101

    def test_portfolio_delta_tracked(self, engine, calm_data):
        r = engine.backtest(calm_data)
        assert all(s.portfolio_delta > 0 for s in r.states)

    def test_hedge_ratio_tracked(self, engine, calm_data):
        r = engine.backtest(calm_data)
        assert all(0 < s.hedge_ratio <= 1.0 for s in r.states)

    def test_budget_tracked(self, engine, calm_data):
        r = engine.backtest(calm_data)
        assert all(s.daily_hedge_spent <= s.daily_hedge_budget + 0.01 for s in r.states)


# ═══════════════════════════════════════════════════════════════════════════
# Backtest — Crisis Market
# ═══════════════════════════════════════════════════════════════════════════


class TestBacktestCrisis:
    def test_leverage_reduced(self, engine, crisis_data):
        r = engine.backtest(crisis_data)
        crisis_levs = [s.leverage for s in r.states if s.regime == "crisis"]
        if crisis_levs:
            assert np.mean(crisis_levs) < 1.2

    def test_crisis_detected(self, engine, crisis_data):
        r = engine.backtest(crisis_data)
        assert r.crisis_days > 0 or r.elevated_days > 0

    def test_ts_inversion_detected(self, engine, crisis_data):
        r = engine.backtest(crisis_data)
        assert sum(1 for s in r.states if s.ts_inverted) > 0

    def test_crisis_score_spikes(self, engine, crisis_data):
        r = engine.backtest(crisis_data)
        crash_scores = [r.states[i].crisis_score for i in range(35, min(65, len(r.states)))]
        assert max(crash_scores) > 0.3

    def test_vix_call_payoff_during_spike(self, engine, crisis_data):
        r = engine.backtest(crisis_data)
        vix_payoffs = [s.vix_call_payoff for s in r.states]
        assert max(vix_payoffs) > 0  # VIX spiked → calls pay off

    def test_hedge_ratio_increases_in_crisis(self, engine, crisis_data):
        r = engine.backtest(crisis_data)
        calm_ratios = [s.hedge_ratio for s in r.states[:25]]
        crisis_ratios = [s.hedge_ratio for s in r.states[35:55]]
        if crisis_ratios:
            assert np.mean(crisis_ratios) >= np.mean(calm_ratios)


# ═══════════════════════════════════════════════════════════════════════════
# Full 6-Year Backtest
# ═══════════════════════════════════════════════════════════════════════════


class TestFullBacktest:
    def test_cagr_above_target(self, full_data, engine):
        r = engine.backtest(full_data)
        assert r.cagr_pct >= 40, f"CAGR {r.cagr_pct}% below 40%"

    def test_positive_sharpe(self, full_data, engine):
        r = engine.backtest(full_data)
        assert r.sharpe > 1.0

    def test_yearly_returns_populated(self, full_data, engine):
        r = engine.backtest(full_data)
        assert len(r.yearly_returns) >= 5

    def test_yearly_dd_populated(self, full_data, engine):
        r = engine.backtest(full_data)
        assert len(r.yearly_dd) >= 5

    def test_cost_within_budget(self, full_data, engine):
        r = engine.backtest(full_data)
        assert r.annual_cost_within_budget, \
            f"Hedge cost {r.total_hedge_cost_pct:.2f}% exceeds 2% budget"

    def test_equity_ends_higher(self, full_data, engine):
        r = engine.backtest(full_data)
        assert r.equity_curve[-1] > r.equity_curve[0]

    def test_lengths_correct(self, full_data, engine):
        r = engine.backtest(full_data)
        n = len(full_data["portfolio_returns"])
        assert len(r.daily_returns) == n
        assert len(r.states) == n

    def test_vix_call_payoff_populated(self, full_data, engine):
        r = engine.backtest(full_data)
        assert r.vix_call_payoff_total_pct >= 0


# ═══════════════════════════════════════════════════════════════════════════
# Stress Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStressTests:
    def test_all_scenarios_run(self, engine):
        results = engine._run_stress_tests()
        assert len(results) == 5

    def test_hedged_dd_less_than_unhedged(self, engine):
        for name, sr in engine._run_stress_tests().items():
            assert sr.hedged_dd_pct <= sr.unhedged_dd_pct + 1.0, \
                f"{name}: hedged {sr.hedged_dd_pct}% > unhedged {sr.unhedged_dd_pct}%"

    def test_covid_significantly_reduced(self, engine):
        covid = engine._run_stress_tests()["COVID_2020"]
        assert covid.hedged_dd_pct < covid.unhedged_dd_pct
        assert covid.dd_reduction_pct > 10, \
            f"COVID DD reduction only {covid.dd_reduction_pct}%"

    def test_flash_crash_reduced(self, engine):
        flash = engine._run_stress_tests()["FLASH_CRASH"]
        assert flash.hedged_dd_pct <= flash.unhedged_dd_pct

    def test_dd_reduction_positive_for_major_crashes(self, engine):
        for name, sr in engine._run_stress_tests().items():
            if sr.unhedged_dd_pct > 10:
                assert sr.dd_reduction_pct > 0, f"{name}: no reduction"

    def test_equity_curves_populated(self, engine):
        for name, sr in engine._run_stress_tests().items():
            assert len(sr.hedged_equity) > 0
            assert len(sr.unhedged_equity) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Metrics Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMetrics:
    def test_basic(self):
        rets = np.array([0.01, 0.02, -0.01, 0.015, 0.005])
        idx = pd.bdate_range("2023-01-02", periods=5)
        eq = [100_000]
        for r in rets:
            eq.append(eq[-1] * (1 + r))
        m = _compute_full_metrics(rets, idx, eq, 100_000)
        assert m["total_return_pct"] > 0
        assert "sharpe" in m and "max_dd_pct" in m

    def test_yearly_breakdown(self):
        n = 504
        idx = pd.bdate_range("2023-01-02", periods=n)
        rets = np.full(n, 0.001)
        eq = [100_000]
        for r in rets:
            eq.append(eq[-1] * (1 + r))
        yr_ret, yr_dd = _yearly_breakdown(rets, idx, eq)
        assert len(yr_ret) >= 1
        assert all(v > 0 for v in yr_ret.values())

    def test_empty_returns(self):
        m = _compute_full_metrics(np.array([]), pd.DatetimeIndex([]), [100_000], 100_000)
        assert m["cagr_pct"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Report Generation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestReport:
    def test_generates_html(self, engine, calm_data, tmp_path):
        r = engine.backtest(calm_data)
        out = tmp_path / "test_report.html"
        generate_report(r, str(out))
        assert out.exists()
        content = out.read_text()
        assert "<!DOCTYPE html>" in content
        assert "Dynamic Tail Risk Hedge" in content

    def test_contains_metrics(self, engine, calm_data, tmp_path):
        r = engine.backtest(calm_data)
        out = tmp_path / "report.html"
        generate_report(r, str(out))
        content = out.read_text()
        for term in ["CAGR", "Sharpe", "Max DD", "Hedge Cost", "Delta"]:
            assert term in content

    def test_contains_scenarios(self, engine, calm_data, tmp_path):
        r = engine.backtest(calm_data)
        out = tmp_path / "report.html"
        generate_report(r, str(out))
        assert "Crisis Stress Tests" in out.read_text()

    def test_contains_svg_charts(self, engine, calm_data, tmp_path):
        r = engine.backtest(calm_data)
        out = tmp_path / "report.html"
        generate_report(r, str(out))
        content = out.read_text()
        assert content.count("<svg") >= 4  # equity, dd, leverage, crisis, delta

    def test_budget_status_shown(self, engine, calm_data, tmp_path):
        r = engine.backtest(calm_data)
        out = tmp_path / "report.html"
        generate_report(r, str(out))
        assert "Budget" in out.read_text()


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_zero_vix3m(self, engine):
        score = engine._crisis_score(vix=20, vix_ratio=20.0, drawdown=0.01,
                                     realized_vol=0.10, momentum_10d=0.01)
        assert 0 <= score <= 1

    def test_extreme_vix(self, engine):
        assert engine._crisis_score(200, 2.0, 0.50, 1.0, -0.20) == 1.0

    def test_all_negative_returns(self, engine):
        idx = pd.bdate_range("2023-01-02", periods=10)
        data = {
            "portfolio_returns": pd.Series(np.full(10, -0.05), index=idx),
            "spy_returns": pd.Series(np.full(10, -0.05), index=idx),
            "vix": pd.Series(np.full(10, 60.0), index=idx),
            "vix3m": pd.Series(np.full(10, 40.0), index=idx),
        }
        r = engine.backtest(data)
        assert r.max_dd_pct > 0
        assert r.equity_curve[-1] > 0

    def test_single_day(self, engine):
        idx = pd.bdate_range("2023-01-02", periods=1)
        data = {
            "portfolio_returns": pd.Series([0.01], index=idx),
            "spy_returns": pd.Series([0.005], index=idx),
            "vix": pd.Series([14.0], index=idx),
            "vix3m": pd.Series([16.0], index=idx),
        }
        r = engine.backtest(data)
        assert len(r.states) == 1

    def test_no_smoothing(self):
        cfg = TailRiskHedgeConfig(leverage_smoothing_days=0)
        e = TailRiskHedgeEngine(cfg)
        idx = pd.bdate_range("2023-01-02", periods=5)
        data = {
            "portfolio_returns": pd.Series([0.01, -0.05, -0.05, 0.01, 0.01], index=idx),
            "spy_returns": pd.Series([0.005, -0.03, -0.03, 0.005, 0.005], index=idx),
            "vix": pd.Series([14, 50, 55, 20, 15], index=idx),
            "vix3m": pd.Series([16, 35, 38, 21, 17], index=idx),
        }
        r = e.backtest(data)
        assert len(r.states) == 5

    def test_custom_budget(self):
        cfg = TailRiskHedgeConfig(annual_cost_budget_pct=5.0)
        e = TailRiskHedgeEngine(cfg)
        _, _, _, budget = e._compute_hedge_allocation(0.5, 20, 0.9, 100_000, 1.6)
        assert abs(budget - 100_000 * 0.05 / 252) < 0.01


# ═══════════════════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    def test_full_pipeline(self, full_data, engine):
        r = engine.backtest(full_data)
        assert r.cagr_pct > 0
        assert r.sharpe > 0
        assert len(r.scenario_results) == 5
        assert len(r.states) > 1000

    def test_hedge_reduces_covid_dd(self, engine):
        data = generate_market_data(n_years=6.0, seed=42)
        r = engine.backtest(data)

        port_ret = data["portfolio_returns"].values
        unhedged = np.cumprod(1 + port_ret * 1.6)
        hwm = np.maximum.accumulate(unhedged)
        unhedged_dd = float((1 - unhedged / hwm).max()) * 100

        assert r.max_dd_pct < unhedged_dd, \
            f"Hedged {r.max_dd_pct:.1f}% >= unhedged {unhedged_dd:.1f}%"

    def test_leverage_responds_to_crisis(self, engine, crisis_data):
        r = engine.backtest(crisis_data)
        pre = np.mean([s.leverage for s in r.states[:25]])
        during = np.mean([s.leverage for s in r.states[40:65]])
        assert during < pre

    def test_recovery_ramps_up(self, engine, crisis_data):
        r = engine.backtest(crisis_data)
        recovery = np.mean([s.leverage for s in r.states[-10:]])
        crisis = np.mean([s.leverage for s in r.states[45:55]])
        assert recovery > crisis

    def test_dual_instrument_both_active(self, engine, crisis_data):
        """Both SPY puts and VIX calls should be active."""
        r = engine.backtest(crisis_data)
        put_days = sum(1 for s in r.states if s.put_cost > 0)
        vix_days = sum(1 for s in r.states if s.vix_call_cost > 0)
        assert put_days > 0
        assert vix_days > 0


# ═══════════════════════════════════════════════════════════════════════════
# Crisis Path Builder Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrisisPathBuilder:
    def test_zero_days(self):
        r = _build_crisis_path(0, 0, 1, 14, 80, 16, 45)
        assert r.n_days == 0

    def test_single_day(self):
        r = _build_crisis_path(-0.10, 1, 1, 14, 80, 16, 45)
        assert r.n_days == 1 and len(r.spy_shocks) == 1

    def test_multi_day_total(self):
        r = _build_crisis_path(-0.30, 20, 1, 14, 80, 16, 45)
        total = float(np.prod(1 + r.spy_shocks) - 1)
        assert abs(total - (-0.30)) < 0.05

    def test_vix_paths_length(self):
        r = _build_crisis_path(-0.20, 15, 1, 14, 60, 16, 40)
        assert len(r.vix_path) == 15 and len(r.vix3m_path) == 15
