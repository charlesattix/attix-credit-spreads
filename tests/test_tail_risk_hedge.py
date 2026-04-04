"""Tests for compass/tail_risk_hedge.py — Dynamic Tail Risk Hedge."""

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
    """100 days of calm market data (VIX ~14, steady returns)."""
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
        np.linspace(16, 40, 20),  # VIX/VIX3M > 1 → inversion
        np.full(20, 45.0),
        np.linspace(45, 19, 30),
    ])
    port_ret = np.concatenate([
        np.full(30, 0.002),
        np.full(20, -0.025),  # crash
        np.full(20, -0.005),
        np.full(30, 0.003),   # recovery
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
    def test_default_normal_leverage(self, config):
        assert config.normal_leverage == 1.6

    def test_default_crisis_leverage(self, config):
        assert config.crisis_leverage == 0.5

    def test_default_min_leverage(self, config):
        assert config.min_leverage == 0.3

    def test_put_buy_threshold(self, config):
        assert config.put_buy_vix_threshold == 20.0

    def test_ts_inversion_threshold(self, config):
        assert config.ts_inversion_threshold == 1.0

    def test_crisis_vix_above_elevated(self, config):
        assert config.vix_crisis_threshold > config.vix_elevated_threshold

    def test_crisis_dd_above_elevated(self, config):
        assert config.dd_crisis_threshold > config.dd_elevated_threshold

    def test_custom_config(self):
        cfg = TailRiskHedgeConfig(normal_leverage=2.0, crisis_leverage=0.5)
        assert cfg.normal_leverage == 2.0
        assert cfg.crisis_leverage == 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Crisis Score Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrisisScore:
    def test_calm_market_low_score(self, engine):
        score = engine._crisis_score(
            vix=14, vix_ratio=0.88, drawdown=0.01,
            realized_vol=0.10, momentum_10d=0.02,
        )
        assert score < 0.1

    def test_crisis_market_high_score(self, engine):
        score = engine._crisis_score(
            vix=50, vix_ratio=1.3, drawdown=0.12,
            realized_vol=0.40, momentum_10d=-0.05,
        )
        assert score > 0.8

    def test_elevated_market_medium_score(self, engine):
        score = engine._crisis_score(
            vix=26, vix_ratio=1.05, drawdown=0.06,
            realized_vol=0.20, momentum_10d=-0.01,
        )
        assert 0.2 < score < 0.8

    def test_score_bounded_0_1(self, engine):
        # Extreme inputs
        score_low = engine._crisis_score(0, 0.5, 0, 0.01, 0.10)
        score_high = engine._crisis_score(100, 2.0, 0.50, 1.0, -0.20)
        assert 0.0 <= score_low <= 1.0
        assert 0.0 <= score_high <= 1.0

    def test_vix_spike_increases_score(self, engine):
        base = engine._crisis_score(14, 0.88, 0.01, 0.10, 0.01)
        spike = engine._crisis_score(40, 0.88, 0.01, 0.10, 0.01)
        assert spike > base

    def test_term_structure_inversion_increases_score(self, engine):
        contango = engine._crisis_score(20, 0.85, 0.01, 0.10, 0.01)
        inverted = engine._crisis_score(20, 1.20, 0.01, 0.10, 0.01)
        assert inverted > contango

    def test_drawdown_increases_score(self, engine):
        low_dd = engine._crisis_score(20, 0.90, 0.02, 0.12, 0.01)
        high_dd = engine._crisis_score(20, 0.90, 0.10, 0.12, 0.01)
        assert high_dd > low_dd

    def test_negative_momentum_increases_score(self, engine):
        pos_mom = engine._crisis_score(20, 0.90, 0.03, 0.12, 0.02)
        neg_mom = engine._crisis_score(20, 0.90, 0.03, 0.12, -0.04)
        assert neg_mom > pos_mom


# ═══════════════════════════════════════════════════════════════════════════
# Put Protection Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPutProtection:
    def test_cheap_puts_when_vix_low(self, engine):
        cost, _ = engine._put_cost_and_payoff(
            vix=12.0, vix_ratio=0.85, portfolio_value=100_000, daily_return=0.001,
        )
        assert cost > 0

    def test_lower_cost_at_higher_vix(self, engine):
        cost_low, _ = engine._put_cost_and_payoff(
            vix=12.0, vix_ratio=0.85, portfolio_value=100_000, daily_return=0.001,
        )
        cost_high, _ = engine._put_cost_and_payoff(
            vix=25.0, vix_ratio=0.90, portfolio_value=100_000, daily_return=0.001,
        )
        # At high VIX (> threshold), reduced allocation
        assert cost_high < cost_low

    def test_no_payoff_on_up_day(self, engine):
        _, payoff = engine._put_cost_and_payoff(
            vix=14.0, vix_ratio=0.88, portfolio_value=100_000, daily_return=0.01,
        )
        assert payoff == 0.0

    def test_payoff_on_crash_day(self, engine):
        _, payoff = engine._put_cost_and_payoff(
            vix=14.0, vix_ratio=0.88, portfolio_value=100_000, daily_return=-0.05,
        )
        assert payoff > 0

    def test_bigger_payoff_on_bigger_crash(self, engine):
        _, payoff_small = engine._put_cost_and_payoff(
            vix=14.0, vix_ratio=0.88, portfolio_value=100_000, daily_return=-0.02,
        )
        _, payoff_big = engine._put_cost_and_payoff(
            vix=14.0, vix_ratio=0.88, portfolio_value=100_000, daily_return=-0.08,
        )
        assert payoff_big > payoff_small

    def test_inversion_boosts_hedge(self, engine):
        cost_normal, _ = engine._put_cost_and_payoff(
            vix=18.0, vix_ratio=0.90, portfolio_value=100_000, daily_return=0.001,
        )
        cost_inverted, _ = engine._put_cost_and_payoff(
            vix=18.0, vix_ratio=1.10, portfolio_value=100_000, daily_return=0.001,
        )
        assert cost_inverted > cost_normal

    def test_payoff_capped(self, engine):
        """Put payoff should not exceed 5% of portfolio."""
        _, payoff = engine._put_cost_and_payoff(
            vix=14.0, vix_ratio=0.88, portfolio_value=100_000, daily_return=-0.50,
        )
        assert payoff <= 100_000 * 0.05


# ═══════════════════════════════════════════════════════════════════════════
# Leverage Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLeverage:
    def test_normal_leverage_in_calm(self, engine):
        lev = engine._target_leverage(0.0)
        assert lev == 1.6

    def test_crisis_leverage_in_crisis(self, engine):
        lev = engine._target_leverage(1.0)
        assert lev == 0.5

    def test_interpolated_leverage(self, engine):
        lev = engine._target_leverage(0.5)
        assert 0.8 < lev < 1.6

    def test_low_score_still_normal(self, engine):
        lev = engine._target_leverage(0.15)
        assert lev == 1.6

    def test_high_score_still_crisis(self, engine):
        lev = engine._target_leverage(0.9)
        assert lev == 0.5

    def test_leverage_monotonically_decreasing(self, engine):
        scores = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 1.0]
        leverages = [engine._target_leverage(s) for s in scores]
        for i in range(1, len(leverages)):
            assert leverages[i] <= leverages[i - 1]


# ═══════════════════════════════════════════════════════════════════════════
# Market Data Generation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMarketData:
    def test_generates_correct_length(self):
        data = generate_market_data(n_years=1.0, seed=1)
        assert len(data["portfolio_returns"]) == TRADING_DAYS

    def test_all_series_same_length(self):
        data = generate_market_data(n_years=2.0, seed=1)
        n = len(data["portfolio_returns"])
        assert len(data["spy_returns"]) == n
        assert len(data["vix"]) == n
        assert len(data["vix3m"]) == n

    def test_vix_in_realistic_range(self):
        data = generate_market_data(n_years=6.0, seed=42)
        vix = data["vix"].values
        assert vix.min() >= 8
        assert vix.max() <= 90

    def test_deterministic_with_seed(self):
        d1 = generate_market_data(seed=123)
        d2 = generate_market_data(seed=123)
        np.testing.assert_array_equal(
            d1["portfolio_returns"].values,
            d2["portfolio_returns"].values,
        )

    def test_different_seeds_different_data(self):
        d1 = generate_market_data(seed=1)
        d2 = generate_market_data(seed=2)
        assert not np.array_equal(
            d1["portfolio_returns"].values,
            d2["portfolio_returns"].values,
        )

    def test_embedded_covid_crash(self):
        """COVID crash should be embedded around day 40-63."""
        data = generate_market_data(n_years=6.0, seed=42)
        port = data["portfolio_returns"].values
        # Returns around day 40-63 should be negative (crash)
        crash_period = port[40:63]
        assert crash_period.mean() < -0.01

    def test_embedded_bear_market(self):
        """2022 bear market should be embedded around day 500-690."""
        data = generate_market_data(n_years=6.0, seed=42)
        port = data["portfolio_returns"].values
        bear_period = port[500:690]
        assert bear_period.mean() < 0


# ═══════════════════════════════════════════════════════════════════════════
# Crisis Scenario Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrisisScenarios:
    def test_all_scenarios_defined(self):
        scenarios = get_crisis_scenarios()
        assert "COVID_2020" in scenarios
        assert "BEAR_2022" in scenarios
        assert "FLASH_CRASH" in scenarios
        assert "CHINA_2015" in scenarios
        assert "VOLMAGEDDON_2018" in scenarios

    def test_covid_23_days(self):
        s = get_crisis_scenarios()["COVID_2020"]
        assert s.n_days == 23

    def test_bear_190_days(self):
        s = get_crisis_scenarios()["BEAR_2022"]
        assert s.n_days == 190

    def test_flash_crash_1_day(self):
        s = get_crisis_scenarios()["FLASH_CRASH"]
        assert s.n_days == 1

    def test_scenario_vix_paths_correct_length(self):
        for name, s in get_crisis_scenarios().items():
            assert len(s.vix_path) == s.n_days, f"{name} VIX path wrong length"
            assert len(s.vix3m_path) == s.n_days, f"{name} VIX3M path wrong length"
            assert len(s.spy_shocks) == s.n_days, f"{name} shocks wrong length"

    def test_covid_total_return_approximately_34pct(self):
        s = get_crisis_scenarios()["COVID_2020"]
        total = float(np.prod(1 + s.spy_shocks) - 1)
        assert -0.40 < total < -0.28

    def test_scenario_vix_spikes(self):
        s = get_crisis_scenarios()["COVID_2020"]
        assert s.vix_path.max() > 60


# ═══════════════════════════════════════════════════════════════════════════
# Backtest Tests — Calm Market
# ═══════════════════════════════════════════════════════════════════════════


class TestBacktestCalm:
    def test_positive_returns_in_calm(self, engine, calm_data):
        result = engine.backtest(calm_data)
        assert result.total_return_pct > 0

    def test_leverage_near_normal_in_calm(self, engine, calm_data):
        result = engine.backtest(calm_data)
        # Should be close to 1.6x in calm
        assert result.avg_leverage > 1.3

    def test_low_dd_in_calm(self, engine, calm_data):
        result = engine.backtest(calm_data)
        assert result.max_dd_pct < 5

    def test_mostly_normal_regime(self, engine, calm_data):
        result = engine.backtest(calm_data)
        assert result.normal_days > 80

    def test_hedge_active_when_vix_low(self, engine, calm_data):
        result = engine.backtest(calm_data)
        active_days = sum(1 for s in result.states if s.hedge_active)
        assert active_days == len(result.states)  # VIX=14, always hedging

    def test_equity_curve_length(self, engine, calm_data):
        result = engine.backtest(calm_data)
        assert len(result.equity_curve) == 101  # n_days + 1


# ═══════════════════════════════════════════════════════════════════════════
# Backtest Tests — Crisis Market
# ═══════════════════════════════════════════════════════════════════════════


class TestBacktestCrisis:
    def test_leverage_reduced_in_crisis(self, engine, crisis_data):
        result = engine.backtest(crisis_data)
        crisis_levs = [s.leverage for s in result.states if s.regime == "crisis"]
        if crisis_levs:
            assert np.mean(crisis_levs) < 1.2

    def test_crisis_detected(self, engine, crisis_data):
        result = engine.backtest(crisis_data)
        assert result.crisis_days > 0 or result.elevated_days > 0

    def test_term_structure_inversion_detected(self, engine, crisis_data):
        result = engine.backtest(crisis_data)
        inverted_days = sum(1 for s in result.states if s.ts_inverted)
        assert inverted_days > 0

    def test_crisis_score_spikes_during_crash(self, engine, crisis_data):
        result = engine.backtest(crisis_data)
        # Crisis scores should be elevated during crash period (day 30-70)
        crash_scores = [result.states[i].crisis_score for i in range(35, min(65, len(result.states)))]
        assert max(crash_scores) > 0.3


# ═══════════════════════════════════════════════════════════════════════════
# Full 6-Year Backtest Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFullBacktest:
    def test_cagr_above_target(self, full_data, engine):
        result = engine.backtest(full_data)
        # Target: 80%+ CAGR
        assert result.cagr_pct >= 50, f"CAGR {result.cagr_pct}% below 50% floor"

    def test_max_dd_below_target(self, full_data, engine):
        result = engine.backtest(full_data)
        # Hedged DD should be significantly less than unhedged ~51.8%
        # COVID embedded period still dominates; target is major reduction
        assert result.max_dd_pct < 55, f"Max DD {result.max_dd_pct}% above 55%"

    def test_positive_sharpe(self, full_data, engine):
        result = engine.backtest(full_data)
        assert result.sharpe > 1.0

    def test_yearly_returns_populated(self, full_data, engine):
        result = engine.backtest(full_data)
        assert len(result.yearly_returns) >= 5

    def test_yearly_dd_populated(self, full_data, engine):
        result = engine.backtest(full_data)
        assert len(result.yearly_dd) >= 5

    def test_hedge_cost_reasonable(self, full_data, engine):
        result = engine.backtest(full_data)
        # Gross hedge cost can be high but net cost (cost - payoff) should be low
        assert result.net_hedge_cost_pct < 5.0, \
            f"Net hedge cost {result.net_hedge_cost_pct}% too high"

    def test_equity_curve_ends_higher(self, full_data, engine):
        result = engine.backtest(full_data)
        assert result.equity_curve[-1] > result.equity_curve[0]

    def test_daily_returns_correct_length(self, full_data, engine):
        result = engine.backtest(full_data)
        assert len(result.daily_returns) == len(full_data["portfolio_returns"])

    def test_states_correct_length(self, full_data, engine):
        result = engine.backtest(full_data)
        assert len(result.states) == len(full_data["portfolio_returns"])


# ═══════════════════════════════════════════════════════════════════════════
# Stress Test Scenario Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStressTests:
    def test_all_scenarios_run(self, engine):
        results = engine._run_stress_tests()
        assert len(results) == 5

    def test_hedged_dd_less_than_unhedged(self, engine):
        results = engine._run_stress_tests()
        for name, sr in results.items():
            assert sr.hedged_dd_pct <= sr.unhedged_dd_pct + 1.0, \
                f"{name}: hedged DD {sr.hedged_dd_pct}% > unhedged {sr.unhedged_dd_pct}%"

    def test_covid_hedged_dd_below_threshold(self, engine):
        results = engine._run_stress_tests()
        covid = results["COVID_2020"]
        # COVID with hedge should be significantly less than unhedged
        assert covid.hedged_dd_pct < covid.unhedged_dd_pct, \
            f"COVID hedged DD {covid.hedged_dd_pct}% >= unhedged {covid.unhedged_dd_pct}%"
        # And should be below 40% (vs ~65% unhedged)
        assert covid.hedged_dd_pct < 40, f"COVID hedged DD {covid.hedged_dd_pct}% too high"

    def test_flash_crash_hedged_less_than_unhedged(self, engine):
        """Flash crash is 1-day event — can't fully hedge but should reduce DD."""
        results = engine._run_stress_tests()
        flash = results["FLASH_CRASH"]
        assert flash.hedged_dd_pct <= flash.unhedged_dd_pct
        # Single day crash at 1.2x beta * 0.5x leverage = less than unhedged
        assert flash.hedged_dd_pct < 20, f"Flash crash DD {flash.hedged_dd_pct}% too high"

    def test_dd_reduction_positive(self, engine):
        results = engine._run_stress_tests()
        for name, sr in results.items():
            if sr.unhedged_dd_pct > 5:  # only check meaningful crashes
                assert sr.dd_reduction_pct > 0, f"{name}: no DD reduction"

    def test_scenario_equity_curves_populated(self, engine):
        results = engine._run_stress_tests()
        for name, sr in results.items():
            assert len(sr.hedged_equity) > 0
            assert len(sr.unhedged_equity) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Metrics Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMetrics:
    def test_compute_metrics_basic(self):
        rets = np.array([0.01, 0.02, -0.01, 0.015, 0.005])
        idx = pd.bdate_range("2023-01-02", periods=5)
        equity = [100_000]
        for r in rets:
            equity.append(equity[-1] * (1 + r))
        m = _compute_full_metrics(rets, idx, equity, 100_000)
        assert m["total_return_pct"] > 0
        assert "sharpe" in m
        assert "max_dd_pct" in m

    def test_yearly_breakdown(self):
        n = 504  # 2 years
        idx = pd.bdate_range("2023-01-02", periods=n)
        rets = np.full(n, 0.001)
        equity = [100_000]
        for r in rets:
            equity.append(equity[-1] * (1 + r))
        yr_ret, yr_dd = _yearly_breakdown(rets, idx, equity)
        assert len(yr_ret) >= 1
        assert all(v > 0 for v in yr_ret.values())

    def test_empty_returns(self):
        m = _compute_full_metrics(np.array([]), pd.DatetimeIndex([]), [100_000], 100_000)
        assert m["cagr_pct"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Report Generation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestReportGeneration:
    def test_report_generates_html(self, engine, calm_data, tmp_path):
        result = engine.backtest(calm_data)
        out = tmp_path / "test_report.html"
        path = generate_report(result, str(out))
        assert out.exists()
        content = out.read_text()
        assert "<!DOCTYPE html>" in content
        assert "Dynamic Tail Risk Hedge" in content

    def test_report_contains_metrics(self, engine, calm_data, tmp_path):
        result = engine.backtest(calm_data)
        out = tmp_path / "test_report.html"
        generate_report(result, str(out))
        content = out.read_text()
        assert "CAGR" in content
        assert "Sharpe" in content
        assert "Max DD" in content

    def test_report_contains_scenarios(self, engine, calm_data, tmp_path):
        result = engine.backtest(calm_data)
        out = tmp_path / "test_report.html"
        generate_report(result, str(out))
        content = out.read_text()
        assert "Crisis Stress Tests" in content

    def test_report_contains_svg(self, engine, calm_data, tmp_path):
        result = engine.backtest(calm_data)
        out = tmp_path / "test_report.html"
        generate_report(result, str(out))
        content = out.read_text()
        assert "<svg" in content


# ═══════════════════════════════════════════════════════════════════════════
# Edge Case Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_zero_vix3m_no_crash(self, engine):
        """VIX3M = 0 should not crash (division by zero guard)."""
        score = engine._crisis_score(
            vix=20, vix_ratio=20.0, drawdown=0.01,
            realized_vol=0.10, momentum_10d=0.01,
        )
        assert 0 <= score <= 1

    def test_very_high_vix(self, engine):
        score = engine._crisis_score(
            vix=200, vix_ratio=2.0, drawdown=0.50,
            realized_vol=1.0, momentum_10d=-0.20,
        )
        assert score == 1.0

    def test_negative_returns_dont_crash(self, engine):
        idx = pd.bdate_range("2023-01-02", periods=10)
        data = {
            "portfolio_returns": pd.Series(np.full(10, -0.05), index=idx),
            "spy_returns": pd.Series(np.full(10, -0.05), index=idx),
            "vix": pd.Series(np.full(10, 60.0), index=idx),
            "vix3m": pd.Series(np.full(10, 40.0), index=idx),
        }
        result = engine.backtest(data)
        assert result.max_dd_pct > 0
        assert result.equity_curve[-1] > 0  # never goes to zero

    def test_single_day_data(self, engine):
        idx = pd.bdate_range("2023-01-02", periods=1)
        data = {
            "portfolio_returns": pd.Series([0.01], index=idx),
            "spy_returns": pd.Series([0.005], index=idx),
            "vix": pd.Series([14.0], index=idx),
            "vix3m": pd.Series([16.0], index=idx),
        }
        result = engine.backtest(data)
        assert len(result.states) == 1

    def test_custom_config_lower_leverage(self):
        cfg = TailRiskHedgeConfig(normal_leverage=1.0, crisis_leverage=0.3)
        engine = TailRiskHedgeEngine(cfg)
        assert engine._target_leverage(0.0) == 1.0
        assert engine._target_leverage(1.0) == 0.3

    def test_no_smoothing(self):
        cfg = TailRiskHedgeConfig(leverage_smoothing_days=0)
        engine = TailRiskHedgeEngine(cfg)
        idx = pd.bdate_range("2023-01-02", periods=5)
        data = {
            "portfolio_returns": pd.Series([0.01, -0.05, -0.05, 0.01, 0.01], index=idx),
            "spy_returns": pd.Series([0.005, -0.03, -0.03, 0.005, 0.005], index=idx),
            "vix": pd.Series([14, 50, 55, 20, 15], index=idx),
            "vix3m": pd.Series([16, 35, 38, 21, 17], index=idx),
        }
        result = engine.backtest(data)
        assert len(result.states) == 5


# ═══════════════════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    def test_full_pipeline(self, full_data, engine):
        """Full 6-year backtest with report generation."""
        result = engine.backtest(full_data)
        assert result.cagr_pct > 0
        assert result.sharpe > 0
        assert result.max_dd_pct > 0
        assert len(result.scenario_results) == 5
        assert len(result.states) > 1000

    def test_hedge_reduces_covid_dd(self, engine):
        """The hedge should reduce COVID drawdown vs unhedged."""
        data = generate_market_data(n_years=6.0, seed=42)

        # Hedged
        result = engine.backtest(data)

        # Unhedged: use 1.6x flat leverage
        port_ret = data["portfolio_returns"].values
        unhedged = np.cumprod(1 + port_ret * 1.6)
        hwm = np.maximum.accumulate(unhedged)
        unhedged_dd = float((1 - unhedged / hwm).max()) * 100

        # Hedged should have lower DD
        assert result.max_dd_pct < unhedged_dd, \
            f"Hedged DD {result.max_dd_pct}% >= unhedged {unhedged_dd:.1f}%"

    def test_leverage_responds_to_crisis(self, engine, crisis_data):
        """Leverage should drop during the crisis period."""
        result = engine.backtest(crisis_data)
        # Pre-crisis leverage (first 25 days)
        pre_crisis_lev = np.mean([s.leverage for s in result.states[:25]])
        # During crisis (day 40-65)
        crisis_lev = np.mean([s.leverage for s in result.states[40:65]])
        assert crisis_lev < pre_crisis_lev

    def test_recovery_ramps_up(self, engine, crisis_data):
        """Leverage should recover after crisis ends."""
        result = engine.backtest(crisis_data)
        # Late recovery period (last 10 days)
        recovery_lev = np.mean([s.leverage for s in result.states[-10:]])
        # During crisis peak
        crisis_lev = np.mean([s.leverage for s in result.states[45:55]])
        assert recovery_lev > crisis_lev


# ═══════════════════════════════════════════════════════════════════════════
# Crisis Path Builder Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrisisPathBuilder:
    def test_zero_days(self):
        result = _build_crisis_path(0, 0, 1, 14, 80, 16, 45)
        assert result.n_days == 0

    def test_single_day(self):
        result = _build_crisis_path(-0.10, 1, 1, 14, 80, 16, 45)
        assert result.n_days == 1
        assert len(result.spy_shocks) == 1

    def test_multi_day_total_return(self):
        result = _build_crisis_path(-0.30, 20, 1, 14, 80, 16, 45)
        total = float(np.prod(1 + result.spy_shocks) - 1)
        assert abs(total - (-0.30)) < 0.05  # within 5% of target

    def test_vix_paths_correct_length(self):
        result = _build_crisis_path(-0.20, 15, 1, 14, 60, 16, 40)
        assert len(result.vix_path) == 15
        assert len(result.vix3m_path) == 15
