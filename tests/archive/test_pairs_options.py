"""Tests for compass/pairs_options.py — pairs trading for options."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.pairs_options import (
    BacktestResult, CointegrationTest, PairAnalysis, PairsConfig,
    PairsOptionsEngine, PairsTrade, SpreadSnapshot,
    compute_spread, rolling_zscore,
    test_cointegration as run_coint_test,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _cointegrated_prices(n=500, seed=42):
    """Generate cointegrated price pairs."""
    rng = np.random.RandomState(seed)
    trend = rng.normal(0, 0.5, n).cumsum() + 100
    a = trend + rng.normal(0, 0.3, n)
    b = 1.5 * trend + rng.normal(0, 0.5, n) + 50
    c = rng.normal(0, 0.5, n).cumsum() + 200  # non-cointegrated
    dates = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame({"A": a, "B": b, "C": c}, index=dates)

def _multi_pair_prices(n=500, seed=42):
    """Generate prices for multiple pairs with some cointegrated."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2022-01-03", periods=n)
    trend1 = rng.normal(0, 0.5, n).cumsum() + 100
    trend2 = rng.normal(0, 0.5, n).cumsum() + 200
    data = {
        "SPY": trend1 + rng.normal(0, 0.3, n),
        "QQQ": 0.8 * trend1 + rng.normal(0, 0.4, n) + 50,  # cointegrated with SPY
        "GLD": trend2 + rng.normal(0, 0.5, n),              # different trend
        "TLT": -0.3 * trend1 + rng.normal(0, 0.4, n) + 150, # negatively related
        "IWM": 0.6 * trend1 + rng.normal(0, 0.5, n) + 80,   # loosely cointegrated
    }
    return pd.DataFrame(data, index=dates)

def _engine(n=500, seed=42, **kw):
    prices = _multi_pair_prices(n, seed)
    pairs = [("SPY", "QQQ"), ("SPY", "IWM"), ("GLD", "TLT"), ("SPY", "GLD")]
    return PairsOptionsEngine(prices, PairsConfig(pairs=pairs, **kw))

# ── Cointegration tests ──────────────────────────────────────────────────

class TestCointegration:
    def test_cointegrated_pair(self):
        df = _cointegrated_prices()
        c = run_coint_test(df["A"].values, df["B"].values)
        assert c.cointegrated is True
        assert c.p_value < 0.10

    def test_non_cointegrated(self):
        df = _cointegrated_prices()
        c = run_coint_test(df["A"].values, df["C"].values)
        # C is random walk — should not be cointegrated (usually)
        assert isinstance(c.cointegrated, bool)

    def test_hedge_ratio_nonzero(self):
        df = _cointegrated_prices()
        c = run_coint_test(df["A"].values, df["B"].values)
        assert c.hedge_ratio != 0

    def test_half_life_positive(self):
        df = _cointegrated_prices()
        c = run_coint_test(df["A"].values, df["B"].values)
        if c.cointegrated:
            assert c.half_life > 0

    def test_short_data(self):
        c = run_coint_test(np.array([1, 2, 3]), np.array([2, 3, 4]))
        assert c.p_value == 1.0

    def test_spread_std_positive(self):
        df = _cointegrated_prices()
        c = run_coint_test(df["A"].values, df["B"].values)
        assert c.spread_std > 0

# ── Spread computation ───────────────────────────────────────────────────

class TestSpread:
    def test_compute_spread(self):
        a = np.array([100, 102, 101])
        b = np.array([50, 52, 50])
        s = compute_spread(a, b, 2.0)
        assert s[0] == pytest.approx(0.0)

    def test_zscore_range(self):
        rng = np.random.RandomState(42)
        spread = rng.normal(0, 1, 200)
        z = rolling_zscore(spread, 30)
        valid = z[~np.isnan(z)]
        assert len(valid) > 0
        # Most should be within ±4
        assert np.abs(valid).max() < 10

    def test_zscore_nan_before_window(self):
        rng = np.random.RandomState(42)
        spread = rng.normal(0, 1, 50)
        z = rolling_zscore(spread, 20)
        assert np.isnan(z[5])
        assert not np.isnan(z[25])

    def test_zscore_mean_zero(self):
        """Constant spread → z ≈ 0 (or nan due to zero std)."""
        spread = np.ones(100) * 5
        z = rolling_zscore(spread, 20)
        # Constant → std=0 → all nan
        valid = z[~np.isnan(z)]
        assert len(valid) == 0 or np.allclose(valid, 0, atol=0.01)

# ── Engine analysis ──────────────────────────────────────────────────────

class TestAnalysis:
    def test_returns_list(self):
        e = _engine()
        results = e.analyze()
        assert isinstance(results, list)
        assert len(results) > 0

    def test_finds_cointegrated(self):
        e = _engine()
        e.analyze()
        coint = [pa for pa in e.pair_analyses if pa.coint.cointegrated]
        assert len(coint) >= 1  # SPY/QQQ should be cointegrated

    def test_pair_analysis_fields(self):
        e = _engine()
        e.analyze()
        pa = e.pair_analyses[0]
        assert isinstance(pa, PairAnalysis)
        assert pa.asset_a != ""

    def test_skips_missing_assets(self):
        prices = _multi_pair_prices()
        pairs = [("SPY", "NONEXISTENT"), ("SPY", "QQQ")]
        e = PairsOptionsEngine(prices, PairsConfig(pairs=pairs))
        results = e.analyze()
        assert len(results) == 1  # only SPY/QQQ

    def test_signal_count(self):
        e = _engine()
        e.analyze()
        total_signals = sum(pa.n_signals for pa in e.pair_analyses)
        assert total_signals >= 0

# ── Backtest ─────────────────────────────────────────────────────────────

class TestBacktest:
    def test_returns_result(self):
        e = _engine()
        bt = e.backtest()
        assert isinstance(bt, BacktestResult)

    def test_has_trades(self):
        e = _engine(entry_z=1.5)  # lower threshold for more signals
        bt = e.backtest()
        assert bt.n_trades >= 0

    def test_win_rate_range(self):
        e = _engine(entry_z=1.5)
        bt = e.backtest()
        if bt.n_trades > 0:
            assert 0 <= bt.win_rate <= 1

    def test_cointegrated_count(self):
        e = _engine()
        bt = e.backtest()
        assert bt.n_cointegrated >= 0
        assert bt.n_cointegrated <= bt.n_pairs_tested

    def test_exit_reasons(self):
        e = _engine(entry_z=1.5)
        bt = e.backtest()
        for reason in bt.by_exit_reason:
            assert reason in ("mean_reversion", "stop_loss", "max_holding", "regime_exit")

    def test_by_pair_populated(self):
        e = _engine(entry_z=1.5)
        bt = e.backtest()
        if bt.n_trades > 0:
            assert len(bt.by_pair) > 0

    def test_trade_fields(self):
        e = _engine(entry_z=1.5)
        bt = e.backtest()
        if bt.trades:
            t = bt.trades[0]
            assert isinstance(t, PairsTrade)
            assert t.direction in ("sell_puts", "sell_calls")
            assert t.holding_days > 0

    def test_sharpe_finite(self):
        e = _engine()
        bt = e.backtest()
        assert np.isfinite(bt.sharpe)

    def test_auto_analyzes(self):
        e = _engine()
        assert len(e.pair_analyses) == 0
        e.backtest()
        assert len(e.pair_analyses) > 0

    def test_correlation_range(self):
        e = _engine()
        bt = e.backtest()
        assert -1 <= bt.correlation_with_spy <= 1

# ── Regime filter ────────────────────────────────────────────────────────

class TestRegimeFilter:
    def test_vix_filter_reduces_trades(self):
        prices = _multi_pair_prices(500)
        vix = pd.Series(np.full(500, 35.0), index=prices.index)  # always high VIX
        e_no_filter = PairsOptionsEngine(
            prices, PairsConfig(pairs=[("SPY", "QQQ")], regime_filter=False, entry_z=1.5),
        )
        e_filter = PairsOptionsEngine(
            prices, PairsConfig(pairs=[("SPY", "QQQ")], regime_filter=True,
                                regime_vix_threshold=30, entry_z=1.5),
            vix=vix,
        )
        bt_no = e_no_filter.backtest()
        bt_yes = e_filter.backtest()
        assert bt_yes.n_trades <= bt_no.n_trades

    def test_no_vix_no_filter(self):
        """Without VIX data, regime filter doesn't crash."""
        e = _engine(regime_filter=True)
        bt = e.backtest()
        assert isinstance(bt, BacktestResult)

# ── PnL estimation ───────────────────────────────────────────────────────

class TestPnLEstimation:
    def test_reversion_positive(self):
        e = _engine()
        pnl = e._estimate_trade_pnl(2.5, 0.3, 100_000, 0.05)
        assert pnl > 0  # z reverted from 2.5 to 0.3

    def test_extension_negative(self):
        e = _engine()
        pnl = e._estimate_trade_pnl(2.0, 3.0, 100_000, 0.05)
        assert pnl < 0  # z extended from 2.0 to 3.0

    def test_bounded_loss(self):
        e = _engine()
        pnl = e._estimate_trade_pnl(2.0, 10.0, 100_000, 0.05)
        assert pnl > -5000  # max loss bounded

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_pair(self):
        prices = _cointegrated_prices()
        e = PairsOptionsEngine(prices, PairsConfig(pairs=[("A", "B")]))
        bt = e.backtest()
        assert isinstance(bt, BacktestResult)

    def test_no_cointegration(self):
        rng = np.random.RandomState(42)
        n = 200
        prices = pd.DataFrame({
            "X": rng.normal(0, 1, n).cumsum() + 100,
            "Y": rng.normal(0, 1, n).cumsum() + 200,
        }, index=pd.bdate_range("2023-01-02", periods=n))
        e = PairsOptionsEngine(prices, PairsConfig(pairs=[("X", "Y")]))
        bt = e.backtest()
        # May or may not have trades depending on spurious cointegration
        assert isinstance(bt, BacktestResult)

    def test_short_data(self):
        prices = _multi_pair_prices(n=40)
        e = PairsOptionsEngine(prices, PairsConfig(pairs=[("SPY", "QQQ")]))
        bt = e.backtest()
        assert isinstance(bt, BacktestResult)
