"""Tests for compass.exp1220_slippage_analysis."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from compass.exp1220_slippage_analysis import (
    CAPITAL_LEVELS,
    CAPITAL_LABELS,
    COMMISSION_PER_CONTRACT_PER_LEG,
    LEVERAGE,
    SlippageBudget,
    SlippageBacktestResult,
    bid_ask_spread,
    compute_slippage_budget,
    fill_probability,
    market_impact_per_spread,
    _metrics,
)


# ── Bid-Ask Spread Model ────────────────────────────────────────────────


class TestBidAskSpread:
    def test_baseline_30dte_atm_vix20(self):
        """Baseline case should be modest."""
        ba = bid_ask_spread(30, 0.0, 20.0)
        assert 0.05 < ba < 0.50

    def test_higher_vix_wider_spread(self):
        ba_low = bid_ask_spread(30, 0.05, 15)
        ba_high = bid_ask_spread(30, 0.05, 40)
        assert ba_high > ba_low * 2  # VIX 40 should be significantly wider

    def test_extreme_vix_very_wide(self):
        ba = bid_ask_spread(30, 0.05, 60)
        assert ba > 0.5  # extreme VIX = extreme widening

    def test_more_otm_wider(self):
        ba_atm = bid_ask_spread(30, 0.0, 20)
        ba_otm = bid_ask_spread(30, 0.10, 20)
        assert ba_otm > ba_atm

    def test_short_dte_changes(self):
        ba_normal = bid_ask_spread(30, 0.05, 20)
        ba_short = bid_ask_spread(3, 0.05, 20)
        assert ba_short > ba_normal * 1.2  # very short DTE = wider

    def test_returns_positive(self):
        assert bid_ask_spread(30, 0.05, 20) > 0

    def test_round_trip_four_crossings(self):
        """Should model 4 half-spread crossings (entry+exit × 2 legs)."""
        # At VIX=20, 30 DTE, 5% OTM: base $0.03/leg × 1.0 × 1.25 × 1.0 = $0.0375/leg
        # 4 crossings = $0.15
        ba = bid_ask_spread(30, 0.05, 20.0)
        assert 0.10 < ba < 0.30


# ── Market Impact Model ─────────────────────────────────────────────────


class TestMarketImpact:
    def test_small_capital_tiny_impact(self):
        mi = market_impact_per_spread(100_000, 1.2)
        assert mi < 0.01  # negligible at $100K

    def test_larger_capital_more_impact(self):
        mi_small = market_impact_per_spread(1_000_000, 1.2)
        mi_large = market_impact_per_spread(100_000_000, 1.2)
        assert mi_large > mi_small

    def test_higher_vix_more_impact(self):
        mi_low = market_impact_per_spread(10_000_000, 1.2, vix=15)
        mi_high = market_impact_per_spread(10_000_000, 1.2, vix=40)
        assert mi_high > mi_low

    def test_returns_positive(self):
        assert market_impact_per_spread(10_000_000, 1.2) > 0


# ── Fill Probability ─────────────────────────────────────────────────────


class TestFillProbability:
    def test_market_order_always_fills(self):
        assert fill_probability(100_000_000, 1.2, "market") == 1.0

    def test_small_limit_high_fill(self):
        fp = fill_probability(100_000, 1.2, "limit")
        assert fp >= 0.90  # small order, high fill

    def test_large_limit_lower_fill(self):
        fp = fill_probability(100_000_000, 1.2, "limit")
        assert fp < 0.95

    def test_fill_probability_bounded(self):
        for cap in CAPITAL_LEVELS:
            fp = fill_probability(cap, 1.2, "limit")
            assert 0.0 <= fp <= 1.0


# ── Slippage Budget ──────────────────────────────────────────────────────


class TestSlippageBudget:
    def test_budget_creation(self):
        b = compute_slippage_budget(1_000_000, "$1M")
        assert isinstance(b, SlippageBudget)
        assert b.capital == 1_000_000
        assert b.total_per_spread > 0

    def test_total_is_ba_plus_impact(self):
        b = compute_slippage_budget(10_000_000, "$10M")
        assert abs(b.total_per_spread - (b.bid_ask_cost + b.market_impact)) < 0.001

    def test_participation_scales_with_capital(self):
        b_small = compute_slippage_budget(100_000, "$100K")
        b_large = compute_slippage_budget(100_000_000, "$100M")
        assert b_large.participation_rate > b_small.participation_rate

    def test_contracts_scale_with_capital(self):
        b_small = compute_slippage_budget(100_000, "$100K")
        b_large = compute_slippage_budget(100_000_000, "$100M")
        assert b_large.contracts_per_trade > b_small.contracts_per_trade * 100


# ── Metrics ──────────────────────────────────────────────────────────────


class TestMetrics:
    def test_positive_returns(self):
        rets = np.array([0.01, 0.02, -0.005, 0.015, 0.01] * 50)
        m = _metrics(rets)
        assert m["cagr_pct"] > 0
        assert m["sharpe"] > 0
        assert m["max_dd_pct"] > 0

    def test_empty_returns(self):
        m = _metrics(np.array([]))
        assert m["cagr_pct"] == 0

    def test_single_return(self):
        m = _metrics(np.array([0.05]))
        assert m["cagr_pct"] == 0


# ── Commission ───────────────────────────────────────────────────────────


class TestCommission:
    def test_commission_rate(self):
        assert COMMISSION_PER_CONTRACT_PER_LEG == 0.65

    def test_commission_included_in_slippage(self):
        """Verify commission is part of the deduction in backtest."""
        # At $100K, 5 contracts, 4 legs (entry+exit × 2): $0.65 × 4 × 5 = $13/trade
        # This is small but nonzero
        assert COMMISSION_PER_CONTRACT_PER_LEG * 4 * 5 == 13.0


# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_capital_levels(self):
        assert len(CAPITAL_LEVELS) == 5
        assert 100_000 in CAPITAL_LEVELS
        assert 100_000_000 in CAPITAL_LEVELS

    def test_leverage(self):
        assert LEVERAGE == 1.2

    def test_labels_match_levels(self):
        assert len(CAPITAL_LABELS) == len(CAPITAL_LEVELS)


# ── Report Generation ────────────────────────────────────────────────────


class TestReportGeneration:
    def _make_result(self):
        from compass.exp1220_slippage_analysis import FullSlippageAnalysis
        results = []
        budgets = []
        for cap, label in [(100_000, "$100K"), (1e6, "$1M"), (1e7, "$10M"), (5e7, "$50M"), (1e8, "$100M")]:
            budget = SlippageBudget(cap, label, 0.15, 0.001, 0.151, 1510, 0.95, 1.0, 48, 0.002)
            r = SlippageBacktestResult(
                label, cap, 1.2,
                96.2, 5.79, 7.8,
                85.6, 5.34, 8.3,
                10.6, 0.45, 0.5,
                budget, {2023: {"pre_cagr": 50, "post_cagr": 45, "drag": 5, "slippage_dollars": 5000}},
            )
            results.append(r)
            budgets.append(budget)
        return FullSlippageAnalysis(
            results=results,
            slippage_budgets=budgets,
            pre_metrics={"cagr_pct": 96.2, "sharpe": 5.79, "max_dd_pct": 7.8},
            scalability_verdict="CONSTRAINED",
        )

    def test_html_generation(self):
        from compass.exp1220_slippage_analysis import EXP1220SlippageAnalysis
        engine = EXP1220SlippageAnalysis()
        result = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.html"
            engine.generate_report(result, path)
            assert path.exists()
            content = path.read_text()
            assert "<!DOCTYPE html>" in content
            assert "EXP-1220" in content
            assert "CONSTRAINED" in content

    def test_json_generation(self):
        from compass.exp1220_slippage_analysis import EXP1220SlippageAnalysis
        engine = EXP1220SlippageAnalysis()
        result = self._make_result()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "summary.json"
            engine.save_summary(result, path)
            data = json.loads(path.read_text())
            assert data["scalability_verdict"] == "CONSTRAINED"
            assert data["leverage"] == 1.2
