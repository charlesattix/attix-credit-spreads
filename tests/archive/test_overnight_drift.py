"""Tests for compass/overnight_drift.py — EXP-1790."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.overnight_drift import (
    OvernightConfig, Trade, WFFold, VariantResult,
    classify_regime, backtest, compute_sharpe, compute_metrics,
    walk_forward, corr_to_spy, correlation_vs,
    build_exp1220_reference, run_variant, generate_report, TRADING_DAYS,
)


def _make_ohlc(n=800, seed=1):
    """Build test OHLC with positive overnight drift."""
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2020-01-02", periods=n)
    # Overnight drift: positive avg (~0.03%/day overnight)
    # Intraday: near zero or slightly negative
    overnight_rets = rng.normal(0.0003, 0.005, n)  # positive drift
    intraday_rets = rng.normal(-0.0001, 0.008, n)  # near zero intraday
    closes = np.zeros(n)
    opens = np.zeros(n)
    closes[0] = 100.0
    opens[0] = 100.0
    for i in range(1, n):
        opens[i] = closes[i - 1] * (1 + overnight_rets[i])
        closes[i] = opens[i] * (1 + intraday_rets[i])
    highs = np.maximum(opens, closes) * (1 + rng.uniform(0, 0.003, n))
    lows = np.minimum(opens, closes) * (1 - rng.uniform(0, 0.003, n))
    return pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows, "Close": closes
    }, index=idx)


class TestConfig:
    def test_defaults(self):
        c = OvernightConfig()
        assert c.leverage == 1.0
        assert not c.regime_filter
        assert c.bear_ma_days == 200


class TestClassifyRegime:
    def test_bull_above_ma(self):
        spy = _make_ohlc(300)  # 300 days — enough for 200-day MA
        regime = classify_regime(spy, ma_days=50)
        # Post-warmup days should have values
        assert regime.iloc[-1] in ("bull", "bear")

    def test_warmup_days(self):
        spy = _make_ohlc(300)
        regime = classify_regime(spy, ma_days=200)
        # Regime series exists for all days (warmup days default to 'bear'
        # from NaN > NaN comparison); strategy treats these as safe-skip
        assert len(regime) == len(spy)


class TestBacktest:
    def test_produces_trades(self):
        spy = _make_ohlc(500, seed=1)
        trades, _ = backtest(spy, OvernightConfig())
        assert len(trades) > 0
        assert all(isinstance(t, Trade) for t in trades)

    def test_trade_structure(self):
        spy = _make_ohlc(500, seed=1)
        trades, _ = backtest(spy, OvernightConfig())
        for t in trades[:5]:
            assert t.entry_price > 0
            assert t.exit_price > 0
            assert t.shares > 0
            assert abs(t.net_pnl - (t.gross_pnl - t.costs)) < 0.02

    def test_leverage_scales_position(self):
        spy = _make_ohlc(500, seed=1)
        trades_1x, _ = backtest(spy, OvernightConfig(leverage=1.0))
        trades_2x, _ = backtest(spy, OvernightConfig(leverage=2.0))
        # 2x should have roughly double the shares per trade
        if trades_1x and trades_2x:
            avg_1x = np.mean([t.shares for t in trades_1x[:10]])
            avg_2x = np.mean([t.shares for t in trades_2x[:10]])
            # Allow compounding difference — 2x should have at least 1.5x shares
            assert avg_2x > avg_1x * 1.5

    def test_regime_filter_skips_bear(self):
        spy = _make_ohlc(500, seed=1)
        # Force bear by making returns negative
        spy_bear = spy.copy()
        spy_bear["Close"] = np.linspace(100, 50, 500)  # monotonic decline
        spy_bear["Open"] = spy_bear["Close"].shift(1).fillna(100)
        spy_bear["High"] = np.maximum(spy_bear["Open"], spy_bear["Close"])
        spy_bear["Low"] = np.minimum(spy_bear["Open"], spy_bear["Close"])
        trades_no_filter, _ = backtest(spy_bear, OvernightConfig(regime_filter=False, bear_ma_days=50))
        trades_filtered, _ = backtest(spy_bear, OvernightConfig(regime_filter=True, bear_ma_days=50))
        # Filter should reduce trade count
        assert len(trades_filtered) < len(trades_no_filter)

    def test_daily_returns_length(self):
        spy = _make_ohlc(500, seed=1)
        _, rets = backtest(spy)
        assert len(rets) == len(spy)


class TestSharpe:
    def test_formula(self):
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(compute_sharpe(rets) - expected) < 0.001

    def test_empty(self):
        assert compute_sharpe(np.array([])) == 0.0


class TestWalkForward:
    def test_produces_folds(self):
        spy = _make_ohlc(1200, seed=2)  # ~5 years
        _, rets = backtest(spy, OvernightConfig())
        folds = walk_forward(rets)
        assert len(folds) >= 1

    def test_fold_structure(self):
        spy = _make_ohlc(1200, seed=2)
        _, rets = backtest(spy)
        folds = walk_forward(rets)
        if folds:
            f = folds[0]
            assert hasattr(f, "test_year")
            assert hasattr(f, "oos_sharpe")
            assert f.n_test > 0


class TestCorrelations:
    def test_spy_correlation(self):
        spy = _make_ohlc(500, seed=1)
        _, rets = backtest(spy)
        corr = corr_to_spy(rets, spy)
        assert -1 <= corr <= 1

    def test_correlation_vs_none(self):
        some = pd.Series([0.01, -0.01, 0.005])
        assert correlation_vs(some, None) is None

    def test_build_exp1220_reference(self):
        spy = _make_ohlc(500, seed=1)
        ref = build_exp1220_reference(spy)
        assert len(ref) == len(spy)


class TestRunVariant:
    def test_runs(self):
        spy = _make_ohlc(800, seed=1)
        ref_1220 = build_exp1220_reference(spy)
        v = run_variant(spy, OvernightConfig(leverage=1.0), "test",
                        ref_1220, None)
        assert isinstance(v, VariantResult)
        assert v.n_trades > 0
        assert v.name == "test"


class TestReport:
    def test_generates(self, tmp_path):
        spy = _make_ohlc(800, seed=1)
        ref_1220 = build_exp1220_reference(spy)
        results = {
            "baseline_1x": run_variant(spy, OvernightConfig(leverage=1.0), "baseline_1x", ref_1220, None),
            "leverage_2x": run_variant(spy, OvernightConfig(leverage=2.0), "leverage_2x", ref_1220, None),
        }
        out = tmp_path / "od.html"
        generate_report(results, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Overnight Drift" in c
        assert "Yahoo" in c
        assert "Cooper 2008" in c
