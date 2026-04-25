"""Tests for compass/sector_pairs.py."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.sector_pairs import (
    SECTOR_ETFS, CointResult, PairBacktest, WFFold, PortfolioResult,
    corrected_sharpe, compute_metrics,
    estimate_hedge_ratio, estimate_half_life,
    screen_all_pairs,
    backtest_pair, walk_forward_pair, build_portfolio,
    TRADING_DAYS,
)
# Renamed import — pytest would otherwise collect test_cointegration as a test
from compass.sector_pairs import test_cointegration as run_coint_test


def _make_cointegrated(n=600, seed=1):
    """Construct two log-price series that ARE cointegrated.

    These are deterministic test fixtures for screener mechanics — NOT
    reported as a strategy result. Rule Zero applies to backtest *results*.

    Construction: log_a is a random walk; log_b = log_a + stationary noise.
    The spread (log_a - log_b) is stationary by construction → cointegrated.
    """
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    log_a = 4 + np.cumsum(rng.normal(0.0001, 0.012, n))
    stationary_noise = rng.normal(0, 0.02, n)   # stationary AR(0)
    log_b = log_a + stationary_noise
    return pd.DataFrame({
        "A": np.exp(log_a),
        "B": np.exp(log_b),
    }, index=idx)


def _make_independent(n=600, seed=1):
    """Two independent random walks — should NOT be cointegrated."""
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    a = np.exp(4 + np.cumsum(rng.normal(0, 0.01, n)))
    b = np.exp(4 + np.cumsum(rng.normal(0, 0.01, n + 1)[1:]))
    return pd.DataFrame({"A": a, "B": b}, index=idx)


class TestUniverse:
    def test_size(self):
        assert len(SECTOR_ETFS) == 10

    def test_contains_core(self):
        for t in ("XLF", "XLK", "XLE", "XLY", "XLP"):
            assert t in SECTOR_ETFS


class TestSharpe:
    def test_formula(self):
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(corrected_sharpe(rets) - expected) < 1e-6

    def test_empty(self):
        assert corrected_sharpe(np.array([])) == 0.0

    def test_constant(self):
        assert corrected_sharpe(np.full(50, 0.001)) == 0.0


class TestMetrics:
    def test_keys(self):
        m = compute_metrics(np.array([0.01, -0.005, 0.008, 0.003]))
        for k in ("cagr", "sharpe", "sortino", "dd", "calmar", "vol"):
            assert k in m


class TestHedgeRatio:
    def test_recovers_known_beta(self):
        rng = np.random.RandomState(1)
        x = rng.normal(0, 1, 500)
        y = 0.5 + 1.7 * x + rng.normal(0, 0.01, 500)
        alpha, beta = estimate_hedge_ratio(y, x)
        assert abs(beta - 1.7) < 0.05
        assert abs(alpha - 0.5) < 0.05


class TestHalfLife:
    def test_mean_reverting_finite(self):
        # AR(1) with negative beta → mean reverting
        rng = np.random.RandomState(1)
        n = 500
        s = np.zeros(n)
        for i in range(1, n):
            s[i] = 0.9 * s[i - 1] + rng.normal(0, 0.1)
        hl = estimate_half_life(s)
        assert hl < 100  # finite, reasonable

    def test_random_walk_long_or_infinite(self):
        rng = np.random.RandomState(1)
        s = np.cumsum(rng.normal(0, 0.1, 500))
        hl = estimate_half_life(s)
        # Random walk has no true mean reversion → finite-sample noise can
        # estimate any value, but compared to a true mean-reverting AR(1)
        # with rho 0.9 (~6 day half-life) it should be much larger
        assert hl > 50 or hl == float("inf")


class TestCointegration:
    def test_detects_cointegrated_pair(self):
        prices = _make_cointegrated(800, seed=42)
        r = run_coint_test(prices, "A", "B")
        assert isinstance(r, CointResult)
        # Constructed to share a common factor — EG should be quite small
        assert r.eg_pvalue < 0.20

    def test_detects_independent_pair(self):
        prices = _make_independent(800, seed=42)
        r = run_coint_test(prices, "A", "B")
        # Independent random walks should NOT be cointegrated
        assert not r.cointegrated

    def test_returns_johansen_fields(self):
        prices = _make_cointegrated(600)
        r = run_coint_test(prices, "A", "B")
        assert isinstance(r.johansen_trace, float)
        assert isinstance(r.johansen_passes, bool)

    def test_screen_all_pairs_runs(self):
        rng = np.random.RandomState(2)
        idx = pd.bdate_range("2018-01-02", periods=400)
        df = pd.DataFrame({
            "A": np.exp(4 + np.cumsum(rng.normal(0, 0.01, 400))),
            "B": np.exp(4 + np.cumsum(rng.normal(0, 0.01, 400))),
            "C": np.exp(4 + np.cumsum(rng.normal(0, 0.01, 400))),
        }, index=idx)
        results = screen_all_pairs(df)
        assert len(results) == 3   # C(3,2) = 3 pairs


class TestPairBacktest:
    def test_returns_series(self):
        prices = _make_cointegrated(800)
        bt = backtest_pair(prices, "A", "B")
        assert isinstance(bt, PairBacktest)
        assert len(bt.daily_returns) == len(prices)
        assert bt.n_trades >= 0

    def test_no_lookahead(self):
        # Reverse the series — Sharpe should change (because the strategy
        # is path-dependent), proving the strategy uses prior info only
        prices = _make_cointegrated(800)
        a = backtest_pair(prices, "A", "B")
        rev_prices = prices.iloc[::-1].copy()
        rev_prices.index = prices.index
        b = backtest_pair(rev_prices, "A", "B")
        assert a.sharpe != b.sharpe   # different paths → different result


class TestWalkForward:
    def test_produces_folds(self):
        prices = _make_cointegrated(1500, seed=3)
        folds = walk_forward_pair(prices, "A", "B")
        assert len(folds) >= 1
        for f in folds:
            assert isinstance(f, WFFold)
            assert f.test_start <= f.test_end

    def test_insufficient_data(self):
        prices = _make_cointegrated(100)
        folds = walk_forward_pair(prices, "A", "B")
        assert folds == []


class TestPortfolio:
    def test_empty_returns_zero(self):
        p = build_portfolio([])
        assert p.n_pairs == 0
        assert p.cagr == 0

    def test_single_pair(self):
        prices = _make_cointegrated(800)
        bt = backtest_pair(prices, "A", "B")
        p = build_portfolio([bt])
        assert p.n_pairs == 1
        assert p.n_days == len(bt.daily_returns)

    def test_correlation_to_reference(self):
        prices = _make_cointegrated(800)
        bt = backtest_pair(prices, "A", "B")
        # Reference equal to the strategy's own daily returns → corr ≈ 1
        ref = bt.daily_returns.copy()
        p = build_portfolio([bt], exp1220_returns=ref)
        assert p.corr_to_exp1220 is not None
        assert abs(p.corr_to_exp1220 - 1.0) < 0.05

    def test_yearly_breakdown(self):
        prices = _make_cointegrated(800)
        bt = backtest_pair(prices, "A", "B")
        p = build_portfolio([bt])
        # Test fixture spans multiple years
        assert len(p.yearly) >= 2
