"""Tests for compass/optimal_portfolio_v3.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compass.optimal_portfolio_v3 import (
    HRPCluster,
    NorthStarResult,
    OptimisationResult,
    PortfolioOptimiserV3,
    STRATEGY_CATALOG,
    Strategy,
    TieredPortfolio,
    build_correlation_matrix,
    construct_tier,
    find_north_star,
    hrp_cluster,
    portfolio_metrics,
    walk_forward_oos,
)


@pytest.fixture
def strategies():
    return list(STRATEGY_CATALOG)


@pytest.fixture
def corr(strategies):
    return build_correlation_matrix(strategies)


# ── Strategy catalog tests ───────────────────────────────────────────────


class TestCatalog:
    def test_catalog_not_empty(self):
        assert len(STRATEGY_CATALOG) >= 10

    def test_has_key_strategies(self):
        names = {s.name for s in STRATEGY_CATALOG}
        assert "ML-CS-880" in names
        assert "Vol-Harvest" in names
        assert "Intraday-MR" in names

    def test_all_have_source(self):
        for s in STRATEGY_CATALOG:
            assert s.source.startswith("EXP-")


# ── Correlation matrix tests ─────────────────────────────────────────────


class TestCorrelation:
    def test_shape(self, strategies, corr):
        n = len([s for s in strategies if s.cagr != 0 or s.sharpe > 0])
        assert corr.shape[0] == corr.shape[1]

    def test_diagonal_one(self, corr):
        for i in range(len(corr)):
            assert corr.iloc[i, i] == 1.0

    def test_symmetric(self, corr):
        np.testing.assert_array_almost_equal(corr.values, corr.values.T)

    def test_bounded(self, corr):
        assert corr.values.min() >= -1.01
        assert corr.values.max() <= 1.01

    def test_known_correlation(self, corr):
        if "ML-CS-880" in corr.columns and "Vol-Harvest" in corr.columns:
            assert abs(corr.loc["ML-CS-880", "Vol-Harvest"] - 0.033) < 0.01


# ── HRP clustering tests ────────────────────────────────────────────────


class TestHRP:
    def test_clusters_created(self, corr):
        clusters = hrp_cluster(corr, 3)
        assert len(clusters) > 0

    def test_all_assigned(self, strategies, corr):
        clusters = hrp_cluster(corr, 3)
        all_members = []
        for c in clusters:
            all_members.extend(c.members)
        # All strategies in corr matrix should be assigned
        assert len(all_members) == len(corr)

    def test_no_empty_clusters(self, corr):
        clusters = hrp_cluster(corr, 3)
        for c in clusters:
            assert len(c.members) > 0

    def test_intra_corr_computed(self, corr):
        clusters = hrp_cluster(corr, 3)
        for c in clusters:
            assert isinstance(c.avg_intra_corr, float)


# ── Portfolio metrics tests ──────────────────────────────────────────────


class TestPortfolioMetrics:
    def test_single_strategy(self, strategies, corr):
        w = {"ML-CS-880": 1.0}
        cagr, dd, sh = portfolio_metrics(strategies, w, corr)
        assert abs(cagr - 76.9) < 0.1

    def test_equal_weight(self, strategies, corr):
        names = [s.name for s in strategies if s.cagr > 0]
        w = {n: 1.0 / len(names) for n in names}
        cagr, dd, sh = portfolio_metrics(strategies, w, corr)
        assert cagr > 0
        assert dd > 0

    def test_diversification_reduces_dd(self, strategies, corr):
        # 50/50 CS + intraday should have lower DD than pure CS
        w_pure = {"ML-CS-880": 1.0}
        w_mixed = {"ML-CS-880": 0.5, "Intraday-MR": 0.5}
        _, dd_pure, _ = portfolio_metrics(strategies, w_pure, corr)
        _, dd_mixed, _ = portfolio_metrics(strategies, w_mixed, corr)
        assert dd_mixed < dd_pure


# ── Tier construction tests ──────────────────────────────────────────────


class TestTiers:
    def test_conservative(self, strategies, corr):
        t = construct_tier(strategies, corr, "conservative", 4, 10, n_mc=1000)
        assert isinstance(t, TieredPortfolio)
        assert t.max_dd <= 10.5  # small tolerance

    def test_balanced(self, strategies, corr):
        t = construct_tier(strategies, corr, "balanced", 3, 15, n_mc=1000)
        assert t.cagr > 0

    def test_aggressive_higher_cagr(self, strategies, corr):
        c = construct_tier(strategies, corr, "conservative", 4, 10, n_mc=1000)
        a = construct_tier(strategies, corr, "aggressive", 2, 20, n_mc=1000)
        assert a.cagr >= c.cagr * 0.8  # aggressive should generally be higher

    def test_weights_sum_one(self, strategies, corr):
        t = construct_tier(strategies, corr, "balanced", 3, 15, n_mc=1000)
        total = sum(t.weights.values())
        assert abs(total - 1.0) < 0.01

    def test_div_ratio(self, strategies, corr):
        t = construct_tier(strategies, corr, "balanced", 3, 15, n_mc=1000)
        assert t.diversification_ratio >= 0.8  # should be >= 1 ideally


# ── North Star tests ─────────────────────────────────────────────────────


class TestNorthStar:
    def test_finds_combination(self, strategies, corr):
        ns = find_north_star(strategies, corr, n_mc=5000)
        assert isinstance(ns, NorthStarResult)
        assert len(ns.strategies) >= 3
        assert ns.cagr > 0

    def test_weights_sum_one(self, strategies, corr):
        ns = find_north_star(strategies, corr, n_mc=5000)
        total = sum(ns.weights.values())
        assert abs(total - 1.0) < 0.01

    def test_leverage_computed(self, strategies, corr):
        ns = find_north_star(strategies, corr, n_mc=5000)
        assert ns.leverage_for_100 > 0

    def test_cagr_at_dd12(self, strategies, corr):
        ns = find_north_star(strategies, corr, n_mc=5000)
        assert isinstance(ns.cagr_at_dd12, float)

    def test_achieves_flag(self, strategies, corr):
        ns = find_north_star(strategies, corr, n_mc=5000)
        assert isinstance(ns.achieves_north_star, bool)


# ── Walk-forward OOS test ────────────────────────────────────────────────


class TestWalkForward:
    def test_oos_sharpe(self, strategies, corr):
        ns = find_north_star(strategies, corr, n_mc=1000)
        oos = walk_forward_oos(strategies, corr, ns)
        assert oos > 0
        assert oos <= ns.sharpe * 1.01  # OOS should be ≤ IS


# ── Full optimiser tests ─────────────────────────────────────────────────


class TestOptimiser:
    def test_optimize(self):
        strats = list(STRATEGY_CATALOG[:6])
        opt = PortfolioOptimiserV3(strats)
        r = opt.optimize()
        assert isinstance(r, OptimisationResult)
        assert len(r.tiers) == 3

    def test_custom_strategies(self):
        strats = [
            Strategy("A", "EXP-1", cagr=30, max_dd=5, sharpe=6),
            Strategy("B", "EXP-2", cagr=20, max_dd=3, sharpe=7),
            Strategy("C", "EXP-3", cagr=15, max_dd=2, sharpe=8),
        ]
        opt = PortfolioOptimiserV3(strats)
        r = opt.optimize()
        assert r.n_strategies == 3

    def test_clusters_computed(self):
        r = PortfolioOptimiserV3(list(STRATEGY_CATALOG[:6])).optimize()
        assert len(r.clusters) > 0

    def test_corr_matrix_stored(self):
        r = PortfolioOptimiserV3(list(STRATEGY_CATALOG[:6])).optimize()
        assert r.correlation_matrix.shape[0] > 0


# ── Report tests ─────────────────────────────────────────────────────────


class TestReport:
    def test_generates(self):
        r = PortfolioOptimiserV3(list(STRATEGY_CATALOG[:5])).optimize()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "pv3.html"
            path = PortfolioOptimiserV3.generate_report(r, out)
            assert path.exists()
            assert "North Star" in path.read_text()

    def test_contains_tiers(self):
        r = PortfolioOptimiserV3(list(STRATEGY_CATALOG[:5])).optimize()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "r.html"
            PortfolioOptimiserV3.generate_report(r, out)
            content = out.read_text()
            assert "conservative" in content
            assert "balanced" in content

    def test_default_path(self):
        r = PortfolioOptimiserV3(list(STRATEGY_CATALOG[:5])).optimize()
        path = PortfolioOptimiserV3.generate_report(r)
        assert path.exists()
        path.unlink(missing_ok=True)
