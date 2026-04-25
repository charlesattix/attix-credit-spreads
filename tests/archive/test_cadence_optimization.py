"""Tests for compass/cadence_optimization.py."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.cadence_optimization import (
    CadenceMetrics, WFFold, compute_trade_metrics, walk_forward_cadence,
    generate_report, TRADING_DAYS,
)


def _make_trades(n=40, cooldown=7, seed=42, avg_pnl=70):
    rng = np.random.RandomState(seed)
    trades = []
    base = pd.Timestamp("2020-03-01")
    for i in range(n):
        entry = base + pd.Timedelta(days=i * cooldown + rng.randint(0, 3))
        hold = 10 + rng.randint(0, 15)
        exit_dt = entry + pd.Timedelta(days=hold)
        trades.append({
            "entry_date": entry.strftime("%Y-%m-%d"),
            "exit_date": exit_dt.strftime("%Y-%m-%d"),
            "pnl": avg_pnl + rng.normal(0, 120),
            "hold_days": hold, "contracts": 2,
            "credit": 0.55, "vix": 18.0, "exit_reason": "profit",
            "cooldown": cooldown,
        })
    return trades


class TestComputeMetrics:
    def test_basic(self):
        m = compute_trade_metrics(_make_trades(), "test", hedge_per_holdday=0)
        assert m.n_trades == 40
        assert m.gross_pnl != 0
        assert m.hedge_cost == 0

    def test_with_hedge(self):
        trades = _make_trades()
        m_no = compute_trade_metrics(trades, "no", hedge_per_holdday=0)
        m_hd = compute_trade_metrics(trades, "hd", hedge_per_holdday=17.30)
        assert m_hd.net_pnl < m_no.net_pnl
        assert m_hd.hedge_cost > 0

    def test_empty(self):
        m = compute_trade_metrics([], "empty", 0)
        assert m.n_trades == 0

    def test_equity_length(self):
        m = compute_trade_metrics(_make_trades(10), "t", 0)
        assert len(m.equity) == 10  # one value per trade

    def test_concurrent(self):
        m = compute_trade_metrics(_make_trades(30), "t", 0)
        assert m.max_concurrent >= 1
        assert 0 <= m.capital_util_pct <= 100

    def test_yearly(self):
        m = compute_trade_metrics(_make_trades(80, cooldown=5), "t", hedge_per_holdday=10)
        assert len(m.yearly) >= 1
        for yr, d in m.yearly.items():
            assert "gross" in d and "hedge" in d and "net" in d

    def test_hedge_proportional_to_hold(self):
        short = _make_trades(20)
        for t in short: t["hold_days"] = 5
        long_t = _make_trades(20)
        for t in long_t: t["hold_days"] = 25
        ms = compute_trade_metrics(short, "short", hedge_per_holdday=17.30)
        ml = compute_trade_metrics(long_t, "long", hedge_per_holdday=17.30)
        assert ml.hedge_cost > ms.hedge_cost

    def test_sharpe_annualised(self):
        m = compute_trade_metrics(_make_trades(50, avg_pnl=200), "t", 0)
        assert m.sharpe > 0  # positive avg pnl → positive sharpe

    def test_cagr_computed(self):
        m = compute_trade_metrics(_make_trades(50, avg_pnl=200), "t", 0)
        assert m.cagr_pct > 0


class TestWalkForward:
    def test_produces_folds(self):
        trades = _make_trades(80, cooldown=5)  # spans 2020-2021
        folds = walk_forward_cadence(trades, hedge_daily=0)
        assert len(folds) >= 1

    def test_empty(self):
        assert walk_forward_cadence([], 0) == []

    def test_fold_fields(self):
        trades = _make_trades(80, cooldown=5)
        folds = walk_forward_cadence(trades, hedge_daily=0)
        if folds:
            f = folds[0]
            assert hasattr(f, "oos_sharpe")
            assert hasattr(f, "oos_pnl")
            assert f.oos_trades > 0

    def test_hedge_reduces_oos_pnl(self):
        trades = _make_trades(80, cooldown=5, avg_pnl=150)
        folds_no = walk_forward_cadence(trades, hedge_daily=0)
        folds_hd = walk_forward_cadence(trades, hedge_daily=17.30)
        if folds_no and folds_hd:
            pnl_no = sum(f.oos_pnl for f in folds_no)
            pnl_hd = sum(f.oos_pnl for f in folds_hd)
            assert pnl_hd < pnl_no


class TestReport:
    def test_generates(self, tmp_path):
        m = compute_trade_metrics(_make_trades(), "7d", 17.30)
        wf = walk_forward_cadence(_make_trades(), 17.30)
        out = tmp_path / "cadence_opt.html"
        generate_report({"7d": m}, {"7d": wf}, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Cadence" in c
        assert "Walk-Forward" in c

    def test_empty(self, tmp_path):
        out = tmp_path / "empty.html"
        generate_report({}, {}, str(out))
        assert out.exists()

    def test_contains_hedge_info(self, tmp_path):
        m = compute_trade_metrics(_make_trades(), "test", 17.30)
        out = tmp_path / "h.html"
        generate_report({"test": m}, {"test": []}, str(out))
        assert "17.30" in out.read_text()
