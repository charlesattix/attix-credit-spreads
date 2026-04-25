"""Tests for compass/earnings_iv_crush.py — EXP-1800."""

import math
from datetime import datetime
import numpy as np
import pandas as pd
import pytest

from compass.earnings_iv_crush import (
    EventIVConfig, EventTrade, WFFold, BacktestResult,
    build_event_calendar, compute_sharpe, compute_metrics,
    build_daily_returns, walk_forward, correlation_to,
    build_exp1220_reference, generate_report,
    _next_td, _prev_td, TRADING_DAYS, CAPITAL,
)


class TestConfig:
    def test_defaults(self):
        c = EventIVConfig()
        assert c.entry_days_before == 1
        assert c.exit_days_after == 1
        assert c.target_dte_min < c.target_dte_max
        assert 0 < c.strangle_width_pct < c.hedge_width_pct

    def test_custom(self):
        c = EventIVConfig(entry_days_before=3, risk_pct_per_trade=0.01)
        assert c.entry_days_before == 3
        assert c.risk_pct_per_trade == 0.01


class TestEventCalendar:
    def test_fomc_only(self):
        events = build_event_calendar(2020, 2025, include_fomc=True,
                                       include_cpi=False, include_nfp=False)
        assert len(events) > 40  # 6 years × ~8 FOMC = 48+
        assert all(ev[0] == "FOMC" for ev in events)

    def test_cpi_only(self):
        events = build_event_calendar(2020, 2022, include_fomc=False,
                                       include_cpi=True, include_nfp=False)
        # 3 years × 12 months = 36
        assert len(events) == 36
        assert all(ev[0] == "CPI" for ev in events)

    def test_nfp_only(self):
        events = build_event_calendar(2020, 2022, include_fomc=False,
                                       include_cpi=False, include_nfp=True)
        assert len(events) == 36
        assert all(ev[0] == "NFP" for ev in events)

    def test_all_events(self):
        events = build_event_calendar(2020, 2022)
        # FOMC (~24) + CPI (36) + NFP (36) ≈ 96
        assert len(events) > 80

    def test_sorted_by_date(self):
        events = build_event_calendar(2020, 2022)
        for i in range(1, len(events)):
            assert events[i-1][1] <= events[i][1]

    def test_empty_year_range(self):
        events = build_event_calendar(2030, 2030, include_fomc=True,
                                       include_cpi=False, include_nfp=False)
        assert len(events) == 0

    def test_nfp_is_friday(self):
        events = build_event_calendar(2021, 2021, include_fomc=False,
                                       include_cpi=False, include_nfp=True)
        for _, dt in events:
            assert dt.weekday() == 4  # Friday

    def test_cpi_is_tuesday(self):
        events = build_event_calendar(2021, 2021, include_fomc=False,
                                       include_cpi=True, include_nfp=False)
        for _, dt in events:
            assert dt.weekday() == 1  # Tuesday (2nd Tuesday approximation)


class TestSharpe:
    def test_formula(self):
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(compute_sharpe(rets) - expected) < 0.001

    def test_empty(self):
        assert compute_sharpe(np.array([])) == 0.0

    def test_single(self):
        assert compute_sharpe(np.array([0.01])) == 0.0

    def test_constant(self):
        assert compute_sharpe(np.full(100, 0.001)) == 0.0


class TestComputeMetrics:
    def test_positive(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr"] > 0
        assert m["sharpe"] > 0

    def test_empty(self):
        m = compute_metrics(np.array([]))
        assert m["cagr"] == 0
        assert m["sharpe"] == 0

    def test_all_fields(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 252))
        for k in ["cagr", "sharpe", "dd", "sortino", "calmar"]:
            assert k in m


class TestDailyReturns:
    def test_empty_trades(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        spy = pd.DataFrame({"Close": np.full(100, 450)}, index=idx)
        daily = build_daily_returns([], spy)
        assert (daily == 0).all()

    def test_with_trades(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        spy = pd.DataFrame({"Close": np.full(100, 450)}, index=idx)
        trades = [
            EventTrade(event_type="FOMC", event_date="2020-01-10",
                       entry_date="2020-01-09", exit_date="2020-01-13",
                       expiration="2020-01-17", dte=8,
                       spot_entry=450, spot_exit=452,
                       put_short_strike=441, put_long_strike=427,
                       call_short_strike=459, call_long_strike=473,
                       entry_credit=0.5, exit_cost=0.2,
                       contracts=2, gross_pnl=60, commission=10,
                       net_pnl=50, return_pct=0.0005,
                       exit_reason="event_close"),
        ]
        daily = build_daily_returns(trades, spy)
        # Exit date 2020-01-13 should have the return
        exit_ts = pd.Timestamp("2020-01-13")
        assert daily.loc[exit_ts] == 0.0005


class TestWalkForward:
    def test_empty(self):
        idx = pd.bdate_range("2020-01-02", periods=10)
        assert walk_forward(pd.Series(0.0, index=idx), []) == []

    def test_insufficient_data(self):
        idx = pd.bdate_range("2020-01-02", periods=5)
        assert walk_forward(pd.Series(0.0, index=idx), []) == []


class TestCorrelation:
    def test_none_reference(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        s = pd.Series(np.random.RandomState(1).normal(0, 0.01, 100), index=idx)
        assert correlation_to(s, None) is None

    def test_same_series(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        s = pd.Series(np.random.RandomState(1).normal(0, 0.01, 100), index=idx)
        corr = correlation_to(s, s)
        assert corr is not None
        assert abs(corr - 1.0) < 0.001

    def test_zero_variance(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        const = pd.Series(0.0, index=idx)
        rand = pd.Series(np.random.RandomState(1).normal(0, 0.01, 100), index=idx)
        assert correlation_to(const, rand) is None


class TestExp1220Reference:
    def test_shape(self):
        idx = pd.bdate_range("2020-01-02", periods=500)
        rng = np.random.RandomState(1)
        spy = pd.DataFrame({"Close": 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, 500))},
                           index=idx)
        ref = build_exp1220_reference(spy)
        assert len(ref) == len(spy)

    def test_positive_theta_baseline(self):
        """On zero-return days, reference should be positive (theta decay)."""
        idx = pd.bdate_range("2020-01-02", periods=100)
        spy = pd.DataFrame({"Close": np.full(100, 450.0)}, index=idx)
        ref = build_exp1220_reference(spy)
        # All days have zero returns → theta baseline
        assert (ref > 0).all()


class TestNextTd:
    def test_finds_today(self):
        idx = pd.bdate_range("2020-01-02", periods=10)
        td_set = set(idx.strftime("%Y-%m-%d"))
        dt = pd.Timestamp("2020-01-02").to_pydatetime()
        assert _next_td(dt, td_set) == dt

    def test_skips_weekend(self):
        idx = pd.bdate_range("2020-01-02", periods=10)
        td_set = set(idx.strftime("%Y-%m-%d"))
        # Saturday should map to next Monday
        sat = pd.Timestamp("2020-01-04").to_pydatetime()  # Saturday
        result = _next_td(sat, td_set)
        assert result is not None
        assert result.weekday() < 5


class TestPrevTd:
    def test_finds_today(self):
        idx = pd.bdate_range("2020-01-02", periods=10)
        td_set = set(idx.strftime("%Y-%m-%d"))
        dt = pd.Timestamp("2020-01-06").to_pydatetime()  # Monday
        assert _prev_td(dt, td_set) == dt


class TestReport:
    def test_generates(self, tmp_path):
        idx = pd.bdate_range("2020-01-02", periods=500)
        spy = pd.DataFrame({"Close": 100 * np.cumprod(1 + np.random.RandomState(1).normal(0, 0.01, 500))}, index=idx)
        result = BacktestResult(
            trades=[], n_trades=0, n_wins=0, win_rate=0,
            cagr=0, sharpe=0, sortino=0, max_dd=0, calmar=0,
            total_pnl=0, gross_pnl=0, total_commission=0,
            daily_returns=pd.Series(0.0, index=idx),
            equity=[100_000, 100_100],
            yearly={2020: {"cagr": 1.0, "sharpe": 0.5, "dd": 1.0, "n_trades": 5}},
            wf_folds=[],
            corr_to_spy=0.0, corr_to_exp1220=0.1,
            n_events_total=50, n_events_traded=0,
            skip_reasons={"no_condor": 50},
        )
        out = tmp_path / "ev.html"
        generate_report(result, str(out))
        assert out.exists()
        c = out.read_text()
        assert "EXP-1800" in c
        assert "Event IV Crush" in c
        assert "DATA REALITY" in c
        assert "single-stock options" in c.lower()

    def test_empty_trades_still_works(self, tmp_path):
        idx = pd.bdate_range("2020-01-02", periods=10)
        result = BacktestResult(
            trades=[], n_trades=0, n_wins=0, win_rate=0,
            cagr=0, sharpe=0, sortino=0, max_dd=0, calmar=0,
            total_pnl=0, gross_pnl=0, total_commission=0,
            daily_returns=pd.Series(0.0, index=idx),
            equity=[100_000],
            yearly={}, wf_folds=[],
            corr_to_spy=0, corr_to_exp1220=None,
            n_events_total=0, n_events_traded=0,
            skip_reasons={},
        )
        out = tmp_path / "ev.html"
        generate_report(result, str(out))
        assert out.exists()
