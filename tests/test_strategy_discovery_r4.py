"""Tests for compass/strategy_discovery_r4.py — Strategy Discovery Round 4."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.strategy_discovery_r4 import (
    Stats, _compute, _exp_dt, generate_report, CAPITAL, OOS_START_YEAR,
)


@pytest.fixture
def spy_ret():
    idx = pd.bdate_range("2020-01-02", periods=1500)
    return pd.Series(np.random.RandomState(1).normal(0.0004, 0.01, 1500), index=idx)

@pytest.fixture
def exp1220_ret(spy_ret):
    return spy_ret * 2.0 + 0.001


class TestHelpers:
    def test_exp_dt(self):
        assert _exp_dt("2024-03-15").year == 2024


class TestCompute:
    def test_empty(self, spy_ret, exp1220_ret):
        s = _compute([], "T", spy_ret, exp1220_ret)
        assert s.killed and s.kill_reason == "0 trades"

    def test_basic(self, spy_ret, exp1220_ret):
        rng = np.random.RandomState(42)
        trades = [{"entry_date": f"20{20+i//12}-{(i%12)+1:02d}-01",
                    "exit_date": f"20{20+i//12}-{(i%12)+1:02d}-15",
                    "pnl": 300 + rng.normal(0, 50)}
                   for i in range(60)]
        s = _compute(trades, "Test", spy_ret, exp1220_ret, "H", "D")
        assert s.n_trades == 60
        assert s.total_pnl > 0
        assert s.sharpe > 0
        assert not s.killed

    def test_kill_few_oos(self, spy_ret, exp1220_ret):
        trades = [{"entry_date": "2023-01-05", "exit_date": "2023-01-20", "pnl": 100}]
        s = _compute(trades, "Few", spy_ret, exp1220_ret)
        assert s.killed

    def test_yearly(self, spy_ret, exp1220_ret):
        trades = [{"entry_date": f"{yr}-06-01", "exit_date": f"{yr}-06-15", "pnl": 300}
                   for yr in range(2020, 2026)] * 5
        s = _compute(trades, "Y", spy_ret, exp1220_ret)
        assert 2020 in s.yearly

    def test_correlations(self, spy_ret, exp1220_ret):
        rng = np.random.RandomState(7)
        trades = [{"entry_date": f"20{20+i//12}-{(i%12)+1:02d}-01",
                    "exit_date": f"20{20+i//12}-{(i%12)+1:02d}-15",
                    "pnl": 200 + rng.normal(0, 80)}
                   for i in range(60)]
        s = _compute(trades, "C", spy_ret, exp1220_ret)
        assert -1 <= s.spy_corr <= 1
        assert -1 <= s.exp1220_corr <= 1

    def test_capacity(self, spy_ret, exp1220_ret):
        trades = [{"entry_date": f"20{20+i//12}-{(i%12)+1:02d}-01",
                    "exit_date": f"20{20+i//12}-{(i%12)+1:02d}-15", "pnl": 200}
                   for i in range(60)]
        s = _compute(trades, "Cap", spy_ret, exp1220_ret, capacity="$50M")
        assert s.capacity == "$50M"


class TestReport:
    def test_generates(self, spy_ret, exp1220_ret, tmp_path):
        results = [Stats(name="T1", n_trades=50, total_pnl=5000, win_rate=0.70,
                         sharpe=1.5, max_dd=0.05, cagr=0.15, spy_corr=0.10,
                         exp1220_corr=0.05, oos_sharpe=1.2, oos_n=20, hypothesis="H1",
                         description="D1", capacity="$10M"),
                   Stats(name="T2", killed=True, kill_reason="0 trades",
                         hypothesis="H2", description="D2")]
        out = tmp_path / "r4.html"
        generate_report(results, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Round 4" in c and "T1" in c and "KILLED" in c

    def test_empty(self, tmp_path):
        out = tmp_path / "r4.html"
        generate_report([], str(out))
        assert out.exists()


class TestStats:
    def test_defaults(self):
        s = Stats(name="T")
        assert s.n_trades == 0 and not s.killed

    def test_hypothesis(self):
        s = Stats(name="T", hypothesis="Test hypothesis")
        assert s.hypothesis == "Test hypothesis"
