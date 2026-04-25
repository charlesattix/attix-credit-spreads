"""Tests for compass/combined_portfolio_v2.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from compass.combined_portfolio_v2 import (
    AllocationResult,
    AlphaStream,
    CombinedPortfolioV2,
    DEFAULT_CORRELATIONS,
    DEFAULT_STREAMS,
    OPTIONAL_STREAMS,
    PortfolioResult,
    diversification_ratio,
    find_optimal,
    leverage_sweep,
    portfolio_metrics,
    sweep_allocations,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def two_streams():
    return list(DEFAULT_STREAMS)


@pytest.fixture
def four_streams():
    return list(DEFAULT_STREAMS) + list(OPTIONAL_STREAMS)


@pytest.fixture
def correlations():
    return dict(DEFAULT_CORRELATIONS)


# ── AlphaStream tests ────────────────────────────────────────────────────


class TestAlphaStream:
    def test_cs880_defaults(self):
        s = DEFAULT_STREAMS[0]
        assert s.name == "CS-880"
        assert s.cagr_pct == 76.9
        assert s.max_dd_pct == 10.2

    def test_intraday_defaults(self):
        s = DEFAULT_STREAMS[1]
        assert s.name == "Intraday-1000"
        assert s.sharpe == 9.92
        assert s.max_dd_pct == 1.2


# ── Portfolio metrics tests ──────────────────────────────────────────────


class TestPortfolioMetrics:
    def test_single_stream(self, two_streams, correlations):
        weights = {"CS-880": 1.0, "Intraday-1000": 0.0}
        cagr, dd, sh, so = portfolio_metrics(two_streams, weights, correlations)
        assert abs(cagr - 76.9) < 0.1
        assert abs(dd - 10.2) < 0.1

    def test_equal_weight(self, two_streams, correlations):
        weights = {"CS-880": 0.5, "Intraday-1000": 0.5}
        cagr, dd, sh, so = portfolio_metrics(two_streams, weights, correlations)
        assert cagr == pytest.approx((76.9 + 10.6) / 2, rel=0.01)
        # DD should be LESS than weighted average (diversification)
        weighted_dd = 0.5 * 10.2 + 0.5 * 1.2
        assert dd < weighted_dd

    def test_70_30(self, two_streams, correlations):
        weights = {"CS-880": 0.7, "Intraday-1000": 0.3}
        cagr, dd, sh, so = portfolio_metrics(two_streams, weights, correlations)
        assert cagr > 30  # weighted avg ~57%

    def test_empty_weights(self, two_streams, correlations):
        cagr, dd, sh, so = portfolio_metrics(two_streams, {}, correlations)
        assert cagr == 0.0

    def test_sharpe_positive(self, two_streams, correlations):
        weights = {"CS-880": 0.6, "Intraday-1000": 0.4}
        _, _, sh, _ = portfolio_metrics(two_streams, weights, correlations)
        assert sh > 0

    def test_sortino_above_sharpe(self, two_streams, correlations):
        weights = {"CS-880": 0.6, "Intraday-1000": 0.4}
        _, _, sh, so = portfolio_metrics(two_streams, weights, correlations)
        assert so >= sh  # sortino ≥ sharpe by construction


# ── Diversification ratio tests ──────────────────────────────────────────


class TestDiversification:
    def test_ratio_above_one(self, two_streams):
        weights = {"CS-880": 0.5, "Intraday-1000": 0.5}
        # Combined DD should be less than weighted individual DDs
        combined_dd = 5.0  # hypothetical
        dr = diversification_ratio(two_streams, weights, combined_dd)
        # weighted avg DD = 5.7, combined 5.0 → ratio > 1
        assert dr > 1.0

    def test_ratio_one_for_single(self, two_streams):
        weights = {"CS-880": 1.0, "Intraday-1000": 0.0}
        dr = diversification_ratio(two_streams, weights, 10.2)
        assert abs(dr - 1.0) < 0.01

    def test_ratio_zero_dd(self, two_streams):
        weights = {"CS-880": 0.5, "Intraday-1000": 0.5}
        dr = diversification_ratio(two_streams, weights, 0.0)
        assert dr == 1.0


# ── Sweep tests ──────────────────────────────────────────────────────────


class TestSweep:
    def test_two_stream_sweep(self, two_streams, correlations):
        results = sweep_allocations(two_streams, correlations, step=0.10)
        assert len(results) > 5
        assert all(isinstance(r, AllocationResult) for r in results)

    def test_four_stream_sweep(self, four_streams, correlations):
        results = sweep_allocations(four_streams, correlations)
        assert len(results) > 100  # MC generates many

    def test_weights_sum_one(self, two_streams, correlations):
        results = sweep_allocations(two_streams, correlations, step=0.10)
        for r in results:
            total = sum(r.weights.values())
            assert abs(total - 1.0) < 0.01

    def test_cagr_bounded(self, two_streams, correlations):
        results = sweep_allocations(two_streams, correlations, step=0.10)
        max_solo = max(s.cagr_pct for s in two_streams)
        for r in results:
            assert r.combined_cagr <= max_solo + 0.1  # can't exceed best solo


# ── Find optimal tests ───────────────────────────────────────────────────


class TestFindOptimal:
    def test_returns_three(self, two_streams, correlations):
        allocs = sweep_allocations(two_streams, correlations, step=0.10)
        opt, msh, mcd = find_optimal(allocs)
        assert isinstance(opt, AllocationResult)
        assert isinstance(msh, AllocationResult)
        assert isinstance(mcd, AllocationResult)

    def test_max_sharpe_highest(self, two_streams, correlations):
        allocs = sweep_allocations(two_streams, correlations, step=0.05)
        _, msh, _ = find_optimal(allocs)
        for a in allocs:
            assert a.combined_sharpe <= msh.combined_sharpe + 0.001

    def test_constrained_dd(self, two_streams, correlations):
        allocs = sweep_allocations(two_streams, correlations, step=0.05)
        _, _, mcd = find_optimal(allocs, dd_constraint=8.0)
        assert mcd.combined_dd <= 8.0 or mcd.combined_dd == min(a.combined_dd for a in allocs)

    def test_empty(self):
        opt, msh, mcd = find_optimal([])
        assert opt.combined_cagr == 0


# ── Leverage tests ───────────────────────────────────────────────────────


class TestLeverage:
    def test_sweep_length(self):
        results = leverage_sweep(50.0, 5.0)
        assert len(results) > 10

    def test_linear_scaling(self):
        results = leverage_sweep(50.0, 5.0)
        at_2x = next(r for r in results if abs(r["leverage"] - 2.0) < 0.01)
        assert abs(at_2x["cagr"] - 100.0) < 0.1
        assert abs(at_2x["dd"] - 10.0) < 0.1

    def test_within_dd_flag(self):
        results = leverage_sweep(50.0, 5.0)
        at_2x = next(r for r in results if abs(r["leverage"] - 2.0) < 0.01)
        assert at_2x["within_12_dd"] is True
        at_3x = next(r for r in results if abs(r["leverage"] - 3.0) < 0.01)
        assert at_3x["within_12_dd"] is False  # 15% > 12%


# ── Full optimiser tests ─────────────────────────────────────────────────


class TestCombinedPortfolioV2:
    def test_two_stream(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        assert isinstance(result, PortfolioResult)
        assert len(result.streams) == 2

    def test_four_stream(self):
        pf = CombinedPortfolioV2(include_optional=True)
        result = pf.optimize()
        assert len(result.streams) == 4

    def test_diversification_benefit(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        assert result.optimal_allocation.diversification_ratio > 1.0

    def test_dd_reduction(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        # Combined DD should be less than CS-880 standalone
        assert result.optimal_allocation.combined_dd < 10.2

    def test_can_hit_100(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        assert isinstance(result.can_hit_100_cagr, bool)
        if result.can_hit_100_cagr:
            assert result.leverage_for_100 > 1.0

    def test_custom_streams(self):
        streams = [
            AlphaStream("A", cagr_pct=30, max_dd_pct=5, sharpe=6),
            AlphaStream("B", cagr_pct=20, max_dd_pct=3, sharpe=7),
        ]
        pf = CombinedPortfolioV2(streams=streams)
        result = pf.optimize()
        assert result.optimal_allocation.combined_cagr > 0

    def test_contributions_present(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        contribs = result.optimal_allocation.per_stream_contribution
        assert "CS-880" in contribs
        assert "Intraday-1000" in contribs

    def test_leverage_results(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        assert len(result.leverage_results) > 5


# ── HTML report tests ────────────────────────────────────────────────────


class TestHTMLReport:
    def test_generates_file(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "cpv2.html"
            path = CombinedPortfolioV2.generate_report(result, out)
            assert path.exists()
            content = path.read_text()
            assert "Combined Portfolio" in content

    def test_contains_streams(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            CombinedPortfolioV2.generate_report(result, out)
            content = out.read_text()
            assert "CS-880" in content
            assert "Intraday-1000" in content

    def test_contains_leverage(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            CombinedPortfolioV2.generate_report(result, out)
            content = out.read_text()
            assert "Leverage" in content

    def test_default_path(self):
        pf = CombinedPortfolioV2()
        result = pf.optimize()
        path = CombinedPortfolioV2.generate_report(result)
        assert path.exists()
        path.unlink(missing_ok=True)
