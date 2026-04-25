"""Tests for compass.portfolio_attribution — 30 tests."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from compass.portfolio_attribution import (
    PortfolioAttributionEngine, StrategySelectionAlpha, TimingAlpha,
    SizingAlpha, HedgeCostBenefit, ExecutionCostAttr, FactorAttribution,
    MonthlyAttribution, FullAttribution, generate_portfolio_data,
)


def _data(n=500, seed=42):
    return generate_portfolio_data(n, seed)


class TestSyntheticData:
    def test_keys(self):
        data = _data()
        for k in ["credit_spread", "iron_condor", "vol_harvest",
                    "regimes", "dynamic_weights", "market", "hedge_active"]:
            assert k in data

    def test_lengths(self):
        data = _data()
        assert len(data["credit_spread"]) == 500
        assert data["dynamic_weights"].shape == (500, 3)


class TestStrategySelection:
    def test_basic(self):
        data = _data()
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        result = PortfolioAttributionEngine.strategy_selection(strats, data["dynamic_weights"])
        assert len(result) == 3
        assert all(isinstance(s, StrategySelectionAlpha) for s in result)

    def test_sorted_desc(self):
        data = _data()
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        result = PortfolioAttributionEngine.strategy_selection(strats, data["dynamic_weights"])
        contribs = [s.contribution for s in result]
        assert contribs == sorted(contribs, reverse=True)

    def test_pct_sums_near_one(self):
        data = _data()
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        result = PortfolioAttributionEngine.strategy_selection(strats, data["dynamic_weights"])
        total = sum(s.pct_of_total for s in result)
        assert abs(total - 1.0) < 0.1


class TestTimingAlpha:
    def test_basic(self):
        data = _data()
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        t = PortfolioAttributionEngine.timing_alpha(strats, data["dynamic_weights"], data["regimes"])
        assert isinstance(t, TimingAlpha)
        assert t.n_regime_switches > 0

    def test_has_best_worst(self):
        data = _data()
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        t = PortfolioAttributionEngine.timing_alpha(strats, data["dynamic_weights"], data["regimes"])
        assert t.best_regime != ""
        assert t.worst_regime != ""

    def test_timing_is_difference(self):
        data = _data()
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        t = PortfolioAttributionEngine.timing_alpha(strats, data["dynamic_weights"], data["regimes"])
        assert t.timing_alpha == pytest.approx(t.timing_return - t.static_return, abs=1e-8)


class TestSizingAlpha:
    def test_basic(self):
        data = _data()
        port = data["credit_spread"] * 0.5 + data["iron_condor"] * 0.25 + data["vol_harvest"] * 0.25
        sizes = pd.Series(1.0, index=port.index)
        s = PortfolioAttributionEngine.sizing_alpha(port, sizes)
        assert isinstance(s, SizingAlpha)
        assert s.sizing_alpha == pytest.approx(0.0, abs=1e-8)  # fixed size = no alpha

    def test_dynamic_differs(self):
        data = _data()
        port = data["credit_spread"]
        sizes = pd.Series(np.where(data["regimes"] == "bull", 1.5, 0.5), index=port.index)
        s = PortfolioAttributionEngine.sizing_alpha(port, sizes)
        assert s.sizing_alpha != 0  # dynamic should differ from fixed


class TestHedgeCostBenefit:
    def test_basic(self):
        eng = PortfolioAttributionEngine()
        data = _data()
        port = data["credit_spread"]
        h = eng.hedge_attribution(port, data["hedge_active"], 0.20, 0.10)
        assert isinstance(h, HedgeCostBenefit)
        assert h.drawdown_saved == pytest.approx(0.10)
        assert h.net_benefit > 0  # DD saved exceeds cost

    def test_no_hedge(self):
        eng = PortfolioAttributionEngine()
        data = _data()
        port = data["credit_spread"]
        no_hedge = pd.Series(0.0, index=port.index)
        h = eng.hedge_attribution(port, no_hedge, 0.15, 0.15)
        assert h.hedge_cost == 0.0
        assert h.drawdown_saved == 0.0


class TestExecutionCost:
    def test_basic(self):
        eng = PortfolioAttributionEngine()
        e = eng.execution_attribution(100, 1.50, 0.10)
        assert isinstance(e, ExecutionCostAttr)
        assert e.total_execution_cost > 0

    def test_zero_trades(self):
        eng = PortfolioAttributionEngine()
        e = eng.execution_attribution(0, 1.50, 0.10)
        assert e.total_execution_cost == 0.0


class TestFactorAttribution:
    def test_basic(self):
        data = _data()
        port = data["credit_spread"]
        f = PortfolioAttributionEngine.factor_attribution(port, data["market"])
        assert isinstance(f, FactorAttribution)
        assert 0 <= f.r_squared <= 1.0

    def test_beta_reasonable(self):
        data = _data(500)
        port = data["credit_spread"]
        f = PortfolioAttributionEngine.factor_attribution(port, data["market"])
        assert -2 < f.market_beta < 2


class TestMonthlyAttribution:
    def test_basic(self):
        eng = PortfolioAttributionEngine()
        data = _data(500)
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        months = eng.monthly_attribution(strats, data["dynamic_weights"],
                                           data["market"], data["hedge_active"], data["regimes"])
        assert len(months) > 10
        assert all(isinstance(m, MonthlyAttribution) for m in months)

    def test_has_month_label(self):
        eng = PortfolioAttributionEngine()
        data = _data(300)
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        months = eng.monthly_attribution(strats, data["dynamic_weights"],
                                           data["market"], data["hedge_active"], data["regimes"])
        if months:
            assert len(months[0].month) > 0


class TestFullAttribution:
    def test_basic(self):
        eng = PortfolioAttributionEngine()
        data = _data(500)
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        result = eng.attribute(strats, data["dynamic_weights"], data["regimes"],
                                data["market"], data["hedge_active"])
        assert isinstance(result, FullAttribution)
        assert len(result.strategy_selection) == 3
        assert len(result.monthly) > 0
        assert np.isfinite(result.sharpe)


class TestReport:
    def test_creates_file(self, tmp_path):
        eng = PortfolioAttributionEngine()
        data = _data(500)
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        result = eng.attribute(strats, data["dynamic_weights"], data["regimes"],
                                data["market"], data["hedge_active"])
        out = tmp_path / "attr.html"
        path = eng.generate_report(result, str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Attribution" in html
        assert "<svg" in html

    def test_contains_waterfall(self, tmp_path):
        eng = PortfolioAttributionEngine()
        data = _data(500)
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        result = eng.attribute(strats, data["dynamic_weights"], data["regimes"],
                                data["market"], data["hedge_active"])
        out = tmp_path / "attr.html"
        eng.generate_report(result, str(out))
        assert "Waterfall" in out.read_text()

    def test_contains_monthly(self, tmp_path):
        eng = PortfolioAttributionEngine()
        data = _data(500)
        strats = {k: data[k] for k in ["credit_spread", "iron_condor", "vol_harvest"]}
        result = eng.attribute(strats, data["dynamic_weights"], data["regimes"],
                                data["market"], data["hedge_active"])
        out = tmp_path / "attr.html"
        eng.generate_report(result, str(out))
        assert "Monthly" in out.read_text()
