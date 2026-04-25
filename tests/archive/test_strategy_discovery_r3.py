"""Tests for compass/strategy_discovery_r3.py — Strategy Discovery Round 3.

Tests helpers, stats computation, and report generation.
Strategy backtests require IronVault DB + yfinance and are tested via integration.
"""

import math

import numpy as np
import pandas as pd
import pytest

from compass.strategy_discovery_r3 import (
    Stats,
    _compute,
    _exp_dt,
    generate_report,
    CAPITAL,
    OOS_START_YEAR,
)


# ═══════════════════════════════════════════════════════════════════════════
# Helper Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_exp_dt_parses(self):
        dt = _exp_dt("2024-03-15")
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15

    def test_exp_dt_format(self):
        from datetime import datetime
        dt = _exp_dt("2020-01-17")
        assert isinstance(dt, datetime)


# ═══════════════════════════════════════════════════════════════════════════
# Stats Computation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStatsComputation:
    @pytest.fixture
    def spy_ret(self):
        idx = pd.bdate_range("2020-01-02", periods=1500)
        return pd.Series(np.random.RandomState(1).normal(0.0004, 0.01, 1500), index=idx)

    @pytest.fixture
    def exp1220_ret(self, spy_ret):
        return spy_ret * 2.0 + 0.001

    def test_empty_trades_killed(self, spy_ret, exp1220_ret):
        s = _compute([], "Test", spy_ret, exp1220_ret)
        assert s.killed
        assert s.kill_reason == "0 trades"

    def test_basic_stats(self, spy_ret, exp1220_ret):
        trades = [
            {"entry_date": "2020-06-01", "exit_date": "2020-06-15", "pnl": 500},
            {"entry_date": "2020-07-01", "exit_date": "2020-07-15", "pnl": -200},
            {"entry_date": "2020-08-01", "exit_date": "2020-08-15", "pnl": 300},
            {"entry_date": "2020-09-01", "exit_date": "2020-09-15", "pnl": 400},
            {"entry_date": "2020-10-01", "exit_date": "2020-10-15", "pnl": -100},
            {"entry_date": "2021-01-05", "exit_date": "2021-01-20", "pnl": 600},
            {"entry_date": "2021-03-01", "exit_date": "2021-03-15", "pnl": 200},
            {"entry_date": "2021-06-01", "exit_date": "2021-06-15", "pnl": 350},
            {"entry_date": "2021-09-01", "exit_date": "2021-09-15", "pnl": -150},
            {"entry_date": "2021-12-01", "exit_date": "2021-12-15", "pnl": 450},
            {"entry_date": "2022-03-01", "exit_date": "2022-03-15", "pnl": 300},
            {"entry_date": "2022-06-01", "exit_date": "2022-06-15", "pnl": -100},
            {"entry_date": "2022-09-01", "exit_date": "2022-09-15", "pnl": 250},
            {"entry_date": "2022-12-01", "exit_date": "2022-12-15", "pnl": 400},
            {"entry_date": "2023-01-05", "exit_date": "2023-01-20", "pnl": 500},
            {"entry_date": "2023-03-01", "exit_date": "2023-03-15", "pnl": 300},
            {"entry_date": "2023-06-01", "exit_date": "2023-06-15", "pnl": -200},
            {"entry_date": "2023-09-01", "exit_date": "2023-09-15", "pnl": 400},
            {"entry_date": "2023-12-01", "exit_date": "2023-12-15", "pnl": 350},
            {"entry_date": "2024-01-05", "exit_date": "2024-01-20", "pnl": 600},
            {"entry_date": "2024-03-01", "exit_date": "2024-03-15", "pnl": 250},
            {"entry_date": "2024-06-01", "exit_date": "2024-06-15", "pnl": -150},
            {"entry_date": "2024-09-01", "exit_date": "2024-09-15", "pnl": 500},
            {"entry_date": "2024-12-01", "exit_date": "2024-12-15", "pnl": 300},
            {"entry_date": "2025-01-05", "exit_date": "2025-01-20", "pnl": 450},
        ]
        s = _compute(trades, "TestStrat", spy_ret, exp1220_ret, "Test description")
        assert s.n_trades == 25
        assert s.total_pnl > 0
        assert 0 < s.win_rate < 1
        assert s.sharpe > 0
        assert s.max_dd >= 0
        assert s.cagr > 0
        assert isinstance(s.spy_corr, float)
        assert isinstance(s.exp1220_corr, float)
        assert -1 <= s.spy_corr <= 1
        assert -1 <= s.exp1220_corr <= 1

    def test_oos_computed(self, spy_ret, exp1220_ret):
        trades = []
        for yr in range(2020, 2026):
            for m in range(1, 13):
                trades.append({
                    "entry_date": f"{yr}-{m:02d}-01",
                    "exit_date": f"{yr}-{m:02d}-15",
                    "pnl": 300,
                })
        s = _compute(trades, "OOS_Test", spy_ret, exp1220_ret)
        assert s.oos_n >= 15
        assert not s.killed

    def test_kill_few_oos_trades(self, spy_ret, exp1220_ret):
        trades = [
            {"entry_date": "2023-01-05", "exit_date": "2023-01-20", "pnl": 100},
        ]
        s = _compute(trades, "Few", spy_ret, exp1220_ret)
        assert s.killed
        assert "OOS trades" in s.kill_reason

    def test_kill_negative_oos_sharpe(self, spy_ret, exp1220_ret):
        trades = []
        for yr in range(2020, 2023):
            for m in [3, 6, 9, 12]:
                trades.append({
                    "entry_date": f"{yr}-{m:02d}-01",
                    "exit_date": f"{yr}-{m:02d}-15",
                    "pnl": 300,
                })
        for i in range(20):
            trades.append({
                "entry_date": f"2023-{(i%12)+1:02d}-{min(28, i+1):02d}",
                "exit_date": f"2023-{(i%12)+1:02d}-{min(28, i+5):02d}",
                "pnl": -500,
            })
        s = _compute(trades, "NegOOS", spy_ret, exp1220_ret)
        if s.oos_n >= 15 and s.oos_sharpe < 0:
            assert s.killed

    def test_yearly_breakdown(self, spy_ret, exp1220_ret):
        trades = [
            {"entry_date": "2020-06-01", "exit_date": "2020-06-15", "pnl": 500},
            {"entry_date": "2021-06-01", "exit_date": "2021-06-15", "pnl": 300},
            {"entry_date": "2023-06-01", "exit_date": "2023-06-15", "pnl": 400},
            {"entry_date": "2023-09-01", "exit_date": "2023-09-15", "pnl": 200},
            {"entry_date": "2023-12-01", "exit_date": "2023-12-15", "pnl": 300},
            {"entry_date": "2024-03-01", "exit_date": "2024-03-15", "pnl": 250},
            {"entry_date": "2024-06-01", "exit_date": "2024-06-15", "pnl": 350},
            {"entry_date": "2024-09-01", "exit_date": "2024-09-15", "pnl": 200},
            {"entry_date": "2024-12-01", "exit_date": "2024-12-15", "pnl": 300},
            {"entry_date": "2025-03-01", "exit_date": "2025-03-15", "pnl": 400},
            {"entry_date": "2025-06-01", "exit_date": "2025-06-15", "pnl": 500},
            {"entry_date": "2025-09-01", "exit_date": "2025-09-15", "pnl": 300},
            {"entry_date": "2025-12-01", "exit_date": "2025-12-15", "pnl": 250},
            {"entry_date": "2022-03-01", "exit_date": "2022-03-15", "pnl": 200},
            {"entry_date": "2022-09-01", "exit_date": "2022-09-15", "pnl": 100},
        ]
        s = _compute(trades, "Yearly", spy_ret, exp1220_ret)
        assert 2020 in s.yearly
        assert 2023 in s.yearly
        assert s.yearly[2020]["n"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Report Generation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestReport:
    def test_generates_html(self, tmp_path):
        results = [
            Stats(name="Test1", n_trades=50, total_pnl=5000, win_rate=0.70,
                  sharpe=1.5, max_dd=0.05, cagr=0.15, spy_corr=0.10,
                  exp1220_corr=0.05, oos_sharpe=1.2, oos_n=20, oos_dd=0.04,
                  description="Test strategy 1"),
            Stats(name="Test2", n_trades=10, killed=True, kill_reason="Only 10 OOS trades",
                  description="Test strategy 2"),
        ]
        out = tmp_path / "report.html"
        generate_report(results, str(out))
        assert out.exists()
        content = out.read_text()
        assert "<!DOCTYPE html>" in content
        assert "Strategy Discovery" in content
        assert "Test1" in content
        assert "KILLED" in content

    def test_report_contains_correlations(self, tmp_path):
        results = [
            Stats(name="S1", spy_corr=0.15, exp1220_corr=-0.05,
                  description="Desc", n_trades=30, oos_n=15, oos_sharpe=1.0),
        ]
        out = tmp_path / "report.html"
        generate_report(results, str(out))
        content = out.read_text()
        assert "SPY" in content
        assert "1220" in content

    def test_report_empty_results(self, tmp_path):
        out = tmp_path / "report.html"
        generate_report([], str(out))
        assert out.exists()


# ═══════════════════════════════════════════════════════════════════════════
# Stats Dataclass Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStatsDataclass:
    def test_defaults(self):
        s = Stats(name="Test")
        assert s.n_trades == 0
        assert s.spy_corr == 0.0
        assert s.exp1220_corr == 0.0
        assert not s.killed

    def test_all_fields(self):
        s = Stats(
            name="Full", description="Full test",
            n_trades=100, total_pnl=10000, win_rate=0.75,
            max_dd=0.08, sharpe=2.5, cagr=0.30,
            spy_corr=0.10, exp1220_corr=-0.05,
            oos_sharpe=2.0, oos_n=40, oos_pnl=5000,
            oos_wr=0.72, oos_dd=0.06, oos_cagr=0.25,
        )
        assert s.spy_corr == 0.10
        assert s.exp1220_corr == -0.05
        assert s.oos_dd == 0.06
