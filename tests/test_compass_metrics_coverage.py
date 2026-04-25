"""Tests for compass/metrics.py — canonical Sharpe, CAGR, DD, Sortino, Calmar, vol."""

import numpy as np
import pytest

from compass.metrics import (
    TRADING_DAYS,
    annualized_sharpe,
    annualized_vol,
    cagr,
    calmar_ratio,
    full_metrics,
    max_drawdown,
    sortino_ratio,
)


class TestAnnualizedSharpe:
    def test_positive_returns(self):
        rng = np.random.RandomState(1)
        r = rng.normal(0.003, 0.005, 252)  # positive drift + noise
        sharpe = annualized_sharpe(r, rf_annual=0.0)
        assert sharpe > 5

    def test_negative_returns(self):
        rng = np.random.RandomState(2)
        r = rng.normal(-0.005, 0.005, 252)
        sharpe = annualized_sharpe(r, rf_annual=0.0)
        assert sharpe < -5

    def test_zero_vol_returns_zero(self):
        r = np.array([0.0] * 252)
        assert annualized_sharpe(r) == 0.0

    def test_single_return_returns_zero(self):
        assert annualized_sharpe(np.array([0.01])) == 0.0

    def test_empty_returns_zero(self):
        assert annualized_sharpe(np.array([])) == 0.0

    def test_risk_free_rate_lowers_sharpe(self):
        rng = np.random.RandomState(3)
        r = rng.normal(0.002, 0.008, 252)
        s_no_rf = annualized_sharpe(r, rf_annual=0.0)
        s_with_rf = annualized_sharpe(r, rf_annual=0.05)
        assert s_with_rf < s_no_rf

    def test_accepts_list(self):
        r = [0.002, -0.001, 0.003, 0.001, -0.002] * 50
        sharpe = annualized_sharpe(np.array(r))
        assert isinstance(sharpe, float)


class TestSortinoRatio:
    def test_all_positive_falls_back_to_sharpe(self):
        r = np.array([0.001] * 100)
        sortino = sortino_ratio(r, rf_annual=0.0)
        sharpe = annualized_sharpe(r, rf_annual=0.0)
        assert abs(sortino - sharpe) < 0.01  # only 1 negative return possible

    def test_with_downside(self):
        rng = np.random.RandomState(42)
        r = rng.normal(0.001, 0.01, 252)
        s = sortino_ratio(r, rf_annual=0.0)
        assert isinstance(s, float)
        assert s != 0.0

    def test_empty_returns_zero(self):
        assert sortino_ratio(np.array([])) == 0.0

    def test_higher_than_sharpe_for_positive_skew(self):
        # Positive skew: small losses, large wins → Sortino > Sharpe
        rng = np.random.RandomState(10)
        losses = rng.uniform(-0.002, 0, 100)
        wins = rng.uniform(0, 0.01, 100)
        r = np.concatenate([losses, wins])
        rng.shuffle(r)
        s = sortino_ratio(r, rf_annual=0.0)
        sh = annualized_sharpe(r, rf_annual=0.0)
        assert s >= sh


class TestCAGR:
    def test_positive_growth(self):
        r = np.array([0.001] * 252)  # ~28% CAGR
        c = cagr(r)
        assert 0.25 < c < 0.35

    def test_flat(self):
        r = np.array([0.0] * 252)
        assert abs(cagr(r)) < 0.001

    def test_negative_growth(self):
        r = np.array([-0.005] * 252)
        assert cagr(r) < 0

    def test_empty_returns_zero(self):
        assert cagr(np.array([])) == 0.0

    def test_total_loss_returns_minus_one(self):
        r = np.array([-1.0])  # lose everything
        assert cagr(r) == -1.0


class TestMaxDrawdown:
    def test_no_drawdown(self):
        r = np.array([0.01] * 100)
        assert max_drawdown(r) == 0.0

    def test_known_drawdown(self):
        # Start at 1.0, go to 1.10, drop to 0.88, recover
        r = np.array([0.10, -0.20, 0.10])
        dd = max_drawdown(r)
        # Peak at 1.10, trough at 0.88 → DD = (1.10 - 0.88) / 1.10 = 0.20
        assert abs(dd - 0.20) < 0.01

    def test_empty_returns_zero(self):
        assert max_drawdown(np.array([])) == 0.0


class TestCalmarRatio:
    def test_positive(self):
        rng = np.random.RandomState(99)
        r = rng.normal(0.001, 0.005, 504)  # ~2 years
        cal = calmar_ratio(r)
        assert isinstance(cal, float)

    def test_no_drawdown_returns_zero(self):
        r = np.array([0.01] * 100)
        assert calmar_ratio(r) == 0.0

    def test_empty_returns_zero(self):
        assert calmar_ratio(np.array([])) == 0.0


class TestAnnualizedVol:
    def test_known_vol(self):
        # Daily vol = 0.01 → annualized = 0.01 * √252 ≈ 0.1587
        r = np.array([0.01, -0.01] * 126)
        vol = annualized_vol(r)
        assert abs(vol - 0.01 * np.sqrt(252)) < 0.001

    def test_empty_returns_zero(self):
        assert annualized_vol(np.array([])) == 0.0


class TestFullMetrics:
    def test_returns_all_keys(self):
        rng = np.random.RandomState(7)
        r = rng.normal(0.0005, 0.008, 504)
        m = full_metrics(r)
        for key in ["cagr_pct", "sharpe", "max_dd_pct", "calmar", "sortino", "vol_pct", "total_ret_pct", "n_days"]:
            assert key in m
        assert m["n_days"] == 504

    def test_empty_returns_zeros(self):
        m = full_metrics(np.array([]))
        assert m["sharpe"] == 0
        assert m["n_days"] == 0

    def test_values_reasonable(self):
        rng = np.random.RandomState(55)
        r = rng.normal(0.002, 0.005, 252)
        m = full_metrics(r, rf_annual=0.0)
        assert m["cagr_pct"] > 20
        assert m["sharpe"] > 3
        assert m["max_dd_pct"] > 0  # some drawdown with noisy returns
