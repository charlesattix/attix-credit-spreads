"""Tests for compass/hedge_cost_reality.py."""

import numpy as np
import pytest

from compass.hedge_cost_reality import (
    MonthlyHedgeCost, RealCostSummary, BacktestComparison,
    summarise_costs, run_backtest_comparison, _metrics,
    generate_report, query_monthly_hedge_costs,
)


class TestMonthlyHedgeCost:
    def test_dataclass(self):
        c = MonthlyHedgeCost(2022, 1, 475.0, 451.0, 1.05, 35, "2022-02-18",
                             0.221, 2.31, 18.5, "2022-01-15")
        assert c.year == 2022 and c.put_price == 1.05

class TestSummariseCosts:
    def _make_costs(self, n=24, base_cost=2.0):
        return [MonthlyHedgeCost(2020 + i//12, (i%12)+1, 450, 427, 1.0, 35,
                "2020-01-17", 0.22, base_cost + (i%5)*0.3, 18, f"2020-{(i%12)+1:02d}-15")
                for i in range(n)]

    def test_basic(self):
        s = summarise_costs(self._make_costs())
        assert s.n_months_sampled == 24
        assert s.avg_annual_cost_pct > 0

    def test_empty(self):
        s = summarise_costs([])
        assert s.n_months_sampled == 0
        assert s.avg_annual_cost_pct == 0

    def test_assumed_cost(self):
        s = summarise_costs(self._make_costs())
        assert s.assumed_cost_pct == 2.0

    def test_ratio_computed(self):
        s = summarise_costs(self._make_costs(base_cost=4.0))
        assert s.actual_vs_assumed_ratio > 1.5

    def test_yearly_costs(self):
        s = summarise_costs(self._make_costs())
        assert len(s.yearly_costs) >= 1

class TestBacktestComparison:
    def test_runs(self):
        bt = run_backtest_comparison(3.0, 2.0)
        assert bt.assumed_cagr != 0
        assert bt.real_cagr != 0

    def test_higher_cost_lower_cagr(self):
        bt = run_backtest_comparison(5.0, 2.0)
        # Higher real cost should reduce CAGR
        assert bt.real_cagr < bt.assumed_cagr

    def test_equity_curves(self):
        bt = run_backtest_comparison(2.5, 2.0)
        assert len(bt.assumed_equity) > 100
        assert len(bt.real_equity) > 100

    def test_deltas(self):
        bt = run_backtest_comparison(3.0, 2.0)
        assert isinstance(bt.cagr_delta, float)
        assert isinstance(bt.sharpe_delta, float)

    def test_yearly(self):
        bt = run_backtest_comparison(2.5, 2.0)
        assert len(bt.yearly) >= 5

class TestMetrics:
    def test_positive(self):
        rng = np.random.RandomState(1)
        m = _metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr_pct"] > 0

    def test_empty(self):
        assert _metrics(np.array([]))["sharpe"] == 0

class TestRealQuery:
    def test_query_returns_list(self):
        costs = query_monthly_hedge_costs()
        assert isinstance(costs, list)
        if costs:
            assert isinstance(costs[0], MonthlyHedgeCost)
            assert costs[0].put_price > 0
            assert costs[0].spy_price > 0

    def test_costs_realistic(self):
        costs = query_monthly_hedge_costs()
        for c in costs:
            assert 0 < c.put_price < c.spy_price * 0.20
            assert 0 < c.annualised_cost_pct < 30

class TestReport:
    def test_generates(self, tmp_path):
        costs = [MonthlyHedgeCost(2022, 1, 475, 451, 1.05, 35, "2022-02-18",
                                  0.22, 2.3, 18.5, "2022-01-15")]
        summary = summarise_costs(costs)
        bt = run_backtest_comparison(2.3, 2.0)
        out = tmp_path / "hedge.html"
        generate_report(summary, bt, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Reality Check" in c
        assert "IronVault" in c

    def test_contains_finding(self, tmp_path):
        costs = [MonthlyHedgeCost(2022, m, 475, 451, 1.0, 35, "2022-02-18",
                                  0.21, 2.5, 20, f"2022-{m:02d}-15") for m in range(1, 13)]
        summary = summarise_costs(costs)
        bt = run_backtest_comparison(2.5, 2.0)
        out = tmp_path / "h.html"
        generate_report(summary, bt, str(out))
        assert "Key Finding" in out.read_text()
