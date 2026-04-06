"""Tests for compass/intraday_mr.py — Overnight gap fade."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.intraday_mr import (
    MRConfig, Trade, WFFold, BacktestResult,
    backtest, compute_sharpe, compute_metrics, walk_forward,
    corr_to_spy, correlation_to_reference,
    build_exp1220_reference, generate_report, TRADING_DAYS,
)


def _make_ohlc(n=500, seed=1):
    """Build real-looking OHLC (numpy random used for tests only)."""
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2020-01-02", periods=n)
    closes = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, n))
    # Gaps: open = prev_close * (1 + gap_noise)
    gaps = rng.normal(0, 0.005, n)
    opens = np.zeros(n)
    opens[0] = closes[0]
    for i in range(1, n):
        opens[i] = closes[i-1] * (1 + gaps[i])
    highs = np.maximum(opens, closes) * (1 + rng.uniform(0, 0.005, n))
    lows = np.minimum(opens, closes) * (1 - rng.uniform(0, 0.005, n))
    return pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows, "Close": closes
    }, index=idx)


class TestConfig:
    def test_defaults(self):
        c = MRConfig()
        assert c.gap_threshold_pct == 0.5
        assert c.max_gap_pct == 5.0
        assert c.enable_long
        assert c.enable_short


class TestSharpe:
    def test_formula(self):
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(compute_sharpe(rets) - expected) < 0.001

    def test_empty(self):
        assert compute_sharpe(np.array([])) == 0.0


class TestBacktest:
    def test_produces_trades(self):
        spy = _make_ohlc(500, seed=1)
        # Force more gaps by setting a low threshold
        cfg = MRConfig(gap_threshold_pct=0.3)
        trades, rets = backtest(spy, cfg)
        assert len(trades) > 0
        assert all(isinstance(t, Trade) for t in trades)

    def test_daily_returns_length(self):
        spy = _make_ohlc(500, seed=1)
        _, rets = backtest(spy)
        assert len(rets) == len(spy)

    def test_direction_matches_gap(self):
        spy = _make_ohlc(500, seed=1)
        trades, _ = backtest(spy, MRConfig(gap_threshold_pct=0.3))
        for t in trades:
            if t.gap_pct < 0:
                assert t.direction == "long"
            else:
                assert t.direction == "short"

    def test_skip_extreme_gaps(self):
        """Force an extreme gap and verify it's skipped."""
        idx = pd.bdate_range("2020-01-02", periods=5)
        df = pd.DataFrame({
            "Open":  [100, 100, 100, 110, 100],   # 10% gap on day 4
            "High":  [101, 101, 101, 111, 101],
            "Low":    [99,  99,  99, 109,  99],
            "Close": [100, 100, 100, 110, 100],
        }, index=idx)
        trades, _ = backtest(df, MRConfig(max_gap_pct=5.0))
        # Day 4's 10% gap should be skipped
        assert all(abs(t.gap_pct) < 5.0 for t in trades)

    def test_costs_applied(self):
        spy = _make_ohlc(500, seed=1)
        trades, _ = backtest(spy, MRConfig(gap_threshold_pct=0.3))
        for t in trades:
            assert t.costs > 0
            # Allow for floating-point rounding (cents)
            assert abs(t.net_pnl - (t.gross_pnl - t.costs)) < 0.02

    def test_disable_long(self):
        spy = _make_ohlc(500, seed=1)
        trades, _ = backtest(spy, MRConfig(enable_long=False, gap_threshold_pct=0.3))
        assert all(t.direction == "short" for t in trades)

    def test_disable_short(self):
        spy = _make_ohlc(500, seed=1)
        trades, _ = backtest(spy, MRConfig(enable_short=False, gap_threshold_pct=0.3))
        assert all(t.direction == "long" for t in trades)


class TestWalkForward:
    def test_empty(self):
        assert walk_forward([], pd.Series(dtype=float)) == []

    def test_produces_folds(self):
        spy = _make_ohlc(1000, seed=2)  # ~4 years
        trades, rets = backtest(spy, MRConfig(gap_threshold_pct=0.3))
        folds = walk_forward(trades, rets)
        # 4 years → 3 folds (2021, 2022, 2023)
        assert len(folds) >= 1

    def test_fold_structure(self):
        spy = _make_ohlc(1000, seed=3)
        trades, rets = backtest(spy, MRConfig(gap_threshold_pct=0.3))
        folds = walk_forward(trades, rets)
        if folds:
            f = folds[0]
            assert hasattr(f, "test_year")
            assert hasattr(f, "oos_sharpe")
            assert f.n_test_trades > 0


class TestCorrelations:
    def test_spy_correlation(self):
        spy = _make_ohlc(500, seed=1)
        _, rets = backtest(spy, MRConfig(gap_threshold_pct=0.3))
        corr = corr_to_spy(rets, spy)
        assert -1 <= corr <= 1

    def test_build_exp1220_reference(self):
        spy = _make_ohlc(500, seed=1)
        ref = build_exp1220_reference(spy)
        assert len(ref) == len(spy)

    def test_correlation_to_reference_empty(self):
        empty = pd.Series(dtype=float)
        some = pd.Series([0.01, -0.01, 0.005])
        # correlation_to_reference returns None for short empty reference
        assert correlation_to_reference(some, empty) is None

    def test_correlation_handles_none(self):
        some = pd.Series([0.01, -0.01, 0.005])
        assert correlation_to_reference(some, None) is None


class TestMetrics:
    def test_positive(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr"] > 0
        assert m["sharpe"] > 0

    def test_empty(self):
        m = compute_metrics(np.array([]))
        assert m["sharpe"] == 0


class TestReport:
    def test_generates(self, tmp_path):
        spy = _make_ohlc(600, seed=4)
        trades, rets = backtest(spy, MRConfig(gap_threshold_pct=0.3))
        m = compute_metrics(rets.values)
        result = BacktestResult(
            trades=trades, n_trades=len(trades),
            n_wins=sum(1 for t in trades if t.net_pnl > 0),
            win_rate=0.5,
            cagr=m["cagr"] * 100, sharpe=m["sharpe"],
            sortino=m["sortino"], max_dd=m["dd"] * 100, calmar=m["calmar"],
            vol=m["vol"] * 100,
            total_pnl=sum(t.gross_pnl for t in trades),
            total_costs=sum(t.costs for t in trades),
            net_pnl=sum(t.net_pnl for t in trades),
            daily_returns=rets, equity=[100_000],
            yearly={2020: {"cagr": 5, "sharpe": 1, "dd": 2, "n_trades": 10}},
            wf_folds=[], corr_to_exp1220=0.1, corr_to_exp1780=-0.05,
            corr_to_spy=0.15,
        )
        out = tmp_path / "mr.html"
        generate_report(result, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Intraday Mean Reversion" in c
        assert "Yahoo" in c
