"""Tests for compass/exp1220_standalone.py."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.exp1220_standalone import (
    sharpe_correct, MethodMetrics,
    method_buggy, method_trade_level, method_with_real_hedge,
    generate_report, TRADING_DAYS,
)


def _make_trades(n=50, avg_pnl=200, seed=42):
    rng = np.random.RandomState(seed)
    trades = []
    for i in range(n):
        yr = 2020 + i // 10
        m = (i % 12) + 1
        trades.append({
            "entry_date": f"{yr}-{m:02d}-01",
            "exit_date": f"{yr}-{m:02d}-{min(28, 10 + i % 18)}",
            "pnl": avg_pnl + rng.normal(0, 150),
            "credit": 0.65, "vix": 18.0, "hold_days": 12 + rng.randint(0, 10),
            "contracts": 2, "exit_reason": "profit",
        })
    return trades


class TestSharpeCorrect:
    def test_uses_ddof1(self):
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(sharpe_correct(rets) - expected) < 0.001

    def test_empty(self):
        assert sharpe_correct(np.array([])) == 0.0


class TestMethodBuggy:
    def test_runs(self):
        idx = pd.bdate_range("2020-01-02", periods=1260)
        m = method_buggy(_make_trades(), idx)
        assert m.name == "buggy"
        assert isinstance(m.sharpe, float)

    def test_dilution_effect(self):
        """Buggy method should show much lower Sharpe than trade-level."""
        idx = pd.bdate_range("2020-01-02", periods=1260)
        trades = _make_trades(50, avg_pnl=300)
        buggy = method_buggy(trades, idx)
        correct = method_trade_level(trades)
        # Buggy dilutes → lower Sharpe
        assert buggy.sharpe < correct.sharpe


class TestMethodTradeLevel:
    def test_positive_trades(self):
        m = method_trade_level(_make_trades(50, avg_pnl=300))
        assert m.cagr_pct > 0
        assert m.sharpe > 0
        assert m.total_pnl > 0

    def test_empty(self):
        m = method_trade_level([])
        assert m.n_trades == 0

    def test_win_rate(self):
        m = method_trade_level(_make_trades())
        assert 0 < m.win_rate < 1

    def test_yearly_computed(self):
        m = method_trade_level(_make_trades())
        assert 2020 in m.yearly

    def test_equity_grows(self):
        m = method_trade_level(_make_trades(50, avg_pnl=400))
        assert m.equity[-1] > m.equity[0]

    def test_dd_nonnegative(self):
        m = method_trade_level(_make_trades())
        assert m.max_dd_pct >= 0


class TestMethodWithHedge:
    def test_lower_than_unhedged(self):
        trades = _make_trades(50, avg_pnl=300)
        unhedged = method_trade_level(trades)
        hedged = method_with_real_hedge(trades)
        assert hedged.total_pnl < unhedged.total_pnl

    def test_hedge_cost_proportional_to_hold(self):
        short_trades = _make_trades(10, avg_pnl=500)
        for t in short_trades:
            t["hold_days"] = 5
        long_trades = _make_trades(10, avg_pnl=500)
        for t in long_trades:
            t["hold_days"] = 25
        short_h = method_with_real_hedge(short_trades)
        long_h = method_with_real_hedge(long_trades)
        # Longer hold = more hedge cost = lower PnL
        assert long_h.total_pnl < short_h.total_pnl

    def test_yearly_has_hedge_cost(self):
        m = method_with_real_hedge(_make_trades())
        for yr, d in m.yearly.items():
            assert "hedge_cost" in d

    def test_empty(self):
        m = method_with_real_hedge([])
        assert m.n_trades == 0


class TestReport:
    def test_generates(self, tmp_path):
        trades = _make_trades()
        idx = pd.bdate_range("2020-01-02", periods=1260)
        methods = [method_buggy(trades, idx), method_trade_level(trades),
                   method_with_real_hedge(trades)]
        out = tmp_path / "report.html"
        generate_report(methods, trades, str(out))
        assert out.exists()
        c = out.read_text()
        assert "BUG DIAGNOSIS" in c
        assert "FIX" in c
        assert "buggy" in c

    def test_contains_comparison(self, tmp_path):
        trades = _make_trades(30)
        idx = pd.bdate_range("2020-01-02", periods=756)
        methods = [method_buggy(trades, idx), method_trade_level(trades),
                   method_with_real_hedge(trades)]
        out = tmp_path / "r.html"
        generate_report(methods, trades, str(out))
        assert "trade_level" in out.read_text()
        assert "with_hedge" in out.read_text()
