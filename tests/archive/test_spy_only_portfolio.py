"""Tests for compass/spy_only_portfolio.py — SPY-Only Production Portfolio."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.spy_only_portfolio import (
    sharpe_correct, compute_metrics, FoldResult, WFResult,
    walk_forward_validate, build_multi_asset_estimate, Comparison,
    generate_report, TRADING_DAYS,
)


class TestSharpeCorrect:
    def test_positive(self):
        rng = np.random.RandomState(1)
        rets = rng.normal(0.001, 0.005, 252)
        s = sharpe_correct(rets)
        assert s > 0

    def test_formula(self):
        """Verify: mean(daily) * sqrt(252) / std(daily, ddof=1)."""
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        mu = rets.mean()
        sigma = rets.std(ddof=1)
        expected = mu / sigma * math.sqrt(TRADING_DAYS)
        assert abs(sharpe_correct(rets) - expected) < 0.001

    def test_uses_ddof1(self):
        """Must use sample std (ddof=1), not population std."""
        rets = np.array([0.01, -0.005, 0.008])
        # ddof=1 gives larger std → lower Sharpe than ddof=0
        pop_std = rets.std(ddof=0)
        sample_std = rets.std(ddof=1)
        assert sample_std > pop_std
        s = sharpe_correct(rets)
        wrong = rets.mean() / pop_std * math.sqrt(TRADING_DAYS)
        assert s < wrong  # correct formula gives lower value

    def test_empty(self):
        assert sharpe_correct(np.array([])) == 0.0

    def test_single(self):
        assert sharpe_correct(np.array([0.01])) == 0.0

    def test_negative(self):
        rng = np.random.RandomState(1)
        assert sharpe_correct(rng.normal(-0.002, 0.005, 252)) < 0

    def test_constant_returns_zero(self):
        assert sharpe_correct(np.full(100, 0.001)) == 0.0


class TestComputeMetrics:
    def test_positive(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 504))
        assert m["cagr"] > 0
        assert m["sharpe"] > 0
        assert m["dd"] >= 0
        assert m["vol"] > 0

    def test_empty(self):
        assert compute_metrics(np.array([]))["sharpe"] == 0

    def test_dd_computed(self):
        rets = np.array([0.05, -0.10, 0.03])  # 10% drawdown
        m = compute_metrics(rets)
        assert m["dd"] > 5


class TestWalkForward:
    def test_empty_trades(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        spy = pd.DataFrame({"Close": np.full(100, 450), "Open": np.full(100, 450)}, index=idx)
        wf = walk_forward_validate([], spy)
        assert wf.n_folds == 0
        assert wf.combined_sharpe == 0

    def test_basic(self):
        """Trades spanning multiple years should produce folds."""
        trades = []
        rng = np.random.RandomState(42)
        for yr in range(2020, 2026):
            for m in range(1, 13):
                trades.append({
                    "entry_date": f"{yr}-{m:02d}-01",
                    "exit_date": f"{yr}-{m:02d}-15",
                    "pnl": 200 + rng.normal(0, 100),
                    "strategy": "credit_spread",
                    "hold_days": 14,
                })
        idx = pd.bdate_range("2020-01-02", periods=1512)
        spy = pd.DataFrame({"Close": np.linspace(300, 500, 1512),
                            "Open": np.linspace(300, 500, 1512)}, index=idx)
        wf = walk_forward_validate(trades, spy, leverage=1.6)
        assert wf.n_folds >= 4
        assert isinstance(wf.combined_sharpe, float)

    def test_equity_curve(self):
        trades = [{"entry_date": f"2020-{m:02d}-01", "exit_date": f"2020-{m:02d}-15",
                    "pnl": 300, "strategy": "credit_spread", "hold_days": 14}
                   for m in range(1, 13)]
        idx = pd.bdate_range("2020-01-02", periods=252)
        spy = pd.DataFrame({"Close": np.full(252, 450), "Open": np.full(252, 450)}, index=idx)
        wf = walk_forward_validate(trades, spy)
        assert wf.equity[0] == 100_000


class TestMultiAssetEstimate:
    def test_has_gaps(self):
        ma = build_multi_asset_estimate()
        assert ma["data_gaps"] == 3

    def test_has_metrics(self):
        ma = build_multi_asset_estimate()
        assert ma["cagr"] > 0
        assert ma["sharpe"] > 0


class TestComparison:
    def test_dataclass(self):
        c = Comparison(
            spy_only={"cagr": 30, "sharpe": 2.5, "dd": 10},
            multi_asset={"cagr": 66, "sharpe": 5.1, "dd": 7.5},
            delta_cagr=-36, delta_sharpe=-2.6, delta_dd=2.5)
        assert c.delta_cagr < 0


class TestReport:
    def test_generates(self, tmp_path):
        wf = WFResult(folds=[], n_folds=0, combined_sharpe=2.5, combined_cagr=30,
                      combined_dd=8, combined_sortino=3.0, combined_calmar=3.75,
                      combined_vol=12, all_dd_ok=True, all_years_profitable=True,
                      equity=[100_000, 110_000, 105_000, 115_000],
                      daily_rets=np.array([0.001]*100), per_strategy={})
        comp = Comparison({"cagr": 30, "sharpe": 2.5, "dd": 8, "calmar": 3.75,
                           "n_strategies": 3, "data_gaps": 0},
                          build_multi_asset_estimate(), -36.2, -2.6, 0.5)
        out = tmp_path / "spy.html"
        generate_report(wf, comp, str(out))
        assert out.exists()
        c = out.read_text()
        assert "SPY-Only" in c
        assert "IronVault" in c

    def test_contains_comparison(self, tmp_path):
        wf = WFResult([], 0, 2.5, 30, 8, 3, 3.75, 12, True, True,
                      [100_000], np.array([]), {})
        comp = Comparison({"cagr": 30}, build_multi_asset_estimate(), -36, -2.6, 0.5)
        out = tmp_path / "s.html"
        generate_report(wf, comp, str(out))
        assert "Multi-Asset" in out.read_text()

    def test_contains_hedge_cost_note(self, tmp_path):
        wf = WFResult([], 0, 2.5, 30, 8, 3, 3.75, 12, True, True,
                      [100_000], np.array([]), {})
        comp = Comparison({"cagr": 30}, build_multi_asset_estimate(), -36, -2.6, 0.5)
        out = tmp_path / "s.html"
        generate_report(wf, comp, str(out))
        assert "4.36" in out.read_text()  # real hedge cost reference
