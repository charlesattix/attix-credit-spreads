"""Tests for compass/portfolio_stress_test.py."""

import json
import math
import numpy as np
import pandas as pd
import pytest

from compass.portfolio_stress_test import (
    block_bootstrap_paths, path_max_dd, path_terminal_return, mc_summary,
    replay_crisis, correlation_in_window, correlation_stability, avg_pairwise_corr,
    find_worst_real_drawdowns, build_exp1660_daily_returns,
    BLOCK_SIZE, HORIZON_DAYS, CAPITAL, CRISIS_PERIODS, DEFAULT_WEIGHTS,
)


def _make_df(n=500, seed=1):
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    return pd.DataFrame({
        "EXP-1220": rng.normal(0.001, 0.005, n),
        "EXP-1780": rng.normal(0.0003, 0.008, n),
        "EXP-1820": rng.normal(0.0005, 0.004, n),
        "EXP-1660": rng.normal(0.0004, 0.003, n),
    }, index=idx)


class TestBlockBootstrap:
    def test_shape(self):
        rets = np.random.RandomState(1).normal(0, 0.01, 500)
        paths = block_bootstrap_paths(rets, n_paths=100, block_size=20, horizon=252)
        assert paths.shape == (100, 252)

    def test_deterministic(self):
        rets = np.random.RandomState(1).normal(0, 0.01, 500)
        a = block_bootstrap_paths(rets, n_paths=50, seed=42)
        b = block_bootstrap_paths(rets, n_paths=50, seed=42)
        assert np.allclose(a, b)

    def test_different_seeds(self):
        rets = np.random.RandomState(1).normal(0, 0.01, 500)
        a = block_bootstrap_paths(rets, n_paths=50, seed=1)
        b = block_bootstrap_paths(rets, n_paths=50, seed=2)
        assert not np.allclose(a, b)


class TestPathMetrics:
    def test_max_dd_zero_for_monotonic(self):
        path = np.full(100, 0.001)  # all positive
        assert path_max_dd(path) < 0.01

    def test_max_dd_for_decline(self):
        path = np.array([-0.01] * 50 + [0.001] * 50)
        dd = path_max_dd(path)
        # ~40% drawdown over 50 days of -1%
        assert dd > 30

    def test_terminal_return(self):
        path = np.array([0.01, 0.01, 0.01])
        ret = path_terminal_return(path)
        expected = (1.01 ** 3 - 1) * 100
        assert abs(ret - expected) < 0.001


class TestMCSummary:
    def test_keys(self):
        rng = np.random.RandomState(1)
        paths = rng.normal(0.001, 0.01, (200, 252))
        s = mc_summary(paths)
        for k in ["median_return_pct", "p5_return_pct", "p95_dd_pct",
                  "p99_dd_pct", "worst_dd_pct", "prob_loss",
                  "prob_dd_over_20", "prob_dd_over_30", "n_paths"]:
            assert k in s

    def test_p5_below_median(self):
        rng = np.random.RandomState(1)
        paths = rng.normal(0.001, 0.01, (500, 252))
        s = mc_summary(paths)
        assert s["p5_return_pct"] < s["median_return_pct"]

    def test_worst_dd_max_of_distribution(self):
        rng = np.random.RandomState(1)
        paths = rng.normal(0, 0.01, (200, 252))
        s = mc_summary(paths)
        assert s["worst_dd_pct"] >= s["p99_dd_pct"]
        assert s["p99_dd_pct"] >= s["p95_dd_pct"]


class TestReplayCrisis:
    def test_basic(self):
        df = _make_df(500)
        r = replay_crisis(df, DEFAULT_WEIGHTS, "2018-06-01", "2018-08-01")
        assert r is not None
        assert "total_return_pct" in r
        assert "max_dd_pct" in r
        assert r["n_days"] > 0

    def test_outside_range(self):
        df = _make_df(500)
        r = replay_crisis(df, DEFAULT_WEIGHTS, "2030-01-01", "2030-06-01")
        assert r is None


class TestCorrelation:
    def test_window(self):
        df = _make_df(500)
        c = correlation_in_window(df, "2018-06-01", "2018-12-31")
        assert c is not None
        assert c.shape == (4, 4)

    def test_avg_pairwise(self):
        df = _make_df(500)
        c = df.corr()
        avg = avg_pairwise_corr(c)
        assert -1 <= avg <= 1

    def test_stability_runs(self):
        df = _make_df(2000, seed=2)
        # extend to cover crisis ranges
        idx = pd.bdate_range("2014-01-02", periods=2000)
        df.index = idx
        out = correlation_stability(df)
        assert "FULL SAMPLE" in out


class TestWorstDD:
    def test_returns_episodes(self):
        rng = np.random.RandomState(3)
        idx = pd.bdate_range("2020-01-02", periods=500)
        s = pd.Series(rng.normal(0, 0.01, 500), index=idx)
        eps = find_worst_real_drawdowns(s, top_n=3)
        assert len(eps) <= 3
        for e in eps:
            assert "dd_pct" in e
            assert e["dd_pct"] >= 0

    def test_sorted_descending(self):
        rng = np.random.RandomState(4)
        idx = pd.bdate_range("2020-01-02", periods=500)
        s = pd.Series(rng.normal(0, 0.01, 500), index=idx)
        eps = find_worst_real_drawdowns(s, top_n=5)
        if len(eps) > 1:
            for i in range(1, len(eps)):
                assert eps[i - 1]["dd_pct"] >= eps[i]["dd_pct"]


class TestExp1660Builder:
    def test_loads_real_json(self, tmp_path):
        # Build a minimal trades file
        data = {
            "XLF": {
                "trades": [
                    {"exit_date": "2020-03-16", "pnl": 1000.0},
                    {"exit_date": "2020-04-01", "pnl": -500.0},
                ]
            }
        }
        p = tmp_path / "t.json"
        p.write_text(json.dumps(data))
        cal = pd.bdate_range("2020-01-02", periods=200)
        s = build_exp1660_daily_returns(str(p), cal, capital=100_000)
        assert s.loc["2020-03-16"] == 0.01
        assert s.loc["2020-04-01"] == -0.005


class TestConstants:
    def test_weights_sum_to_one(self):
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 0.001

    def test_crisis_periods_valid(self):
        for label, (s, e) in CRISIS_PERIODS.items():
            assert s < e
