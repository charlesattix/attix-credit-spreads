"""Tests for compass/momentum_rotation.py — EXP-1830."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.momentum_rotation import (
    SECTOR_ETFS, OTHER_ASSETS, UNIVERSE,
    MomentumConfig, WFFold, VariantResult,
    compute_momentum_signal, compute_weights, apply_rebalance_hold,
    backtest, compute_sharpe, compute_metrics, walk_forward,
    corr_to, build_exp1220_reference, run_variant,
    TRADING_DAYS,
)


def _make_prices(n=600, assets=None, seed=1):
    """Build test price series with different drift per asset."""
    rng = np.random.RandomState(seed)
    assets = assets or UNIVERSE
    idx = pd.bdate_range("2018-01-02", periods=n)
    data = {}
    for i, tk in enumerate(assets):
        drift = 0.0002 + (i - len(assets) / 2) * 0.00008  # varies by asset
        rets = rng.normal(drift, 0.012, n)
        data[tk] = 100 * np.cumprod(1 + rets)
    return pd.DataFrame(data, index=idx)


class TestUniverse:
    def test_sector_count(self):
        assert len(SECTOR_ETFS) == 11
        assert "XLF" in SECTOR_ETFS
        assert "XLK" in SECTOR_ETFS

    def test_other_assets(self):
        assert "TLT" in OTHER_ASSETS
        assert "GLD" in OTHER_ASSETS

    def test_universe_size(self):
        assert len(UNIVERSE) == 13


class TestConfig:
    def test_defaults(self):
        c = MomentumConfig()
        assert c.lookback_months == [3, 6, 12]
        assert abs(sum(c.lookback_weights) - 1.0) < 0.01
        assert c.n_long == 3
        assert c.n_short == 3

    def test_long_only(self):
        c = MomentumConfig(n_short=0, allow_short=False)
        assert not c.allow_short
        assert c.n_short == 0


class TestMomentumSignal:
    def test_shape(self):
        prices = _make_prices(600)
        sig = compute_momentum_signal(prices, [3, 6, 12], [0.3, 0.4, 0.3])
        assert sig.shape == prices.shape

    def test_weights_must_sum_to_one(self):
        prices = _make_prices(300)
        with pytest.raises(ValueError, match="sum to"):
            compute_momentum_signal(prices, [3, 6], [0.5, 0.3])

    def test_lengths_must_match(self):
        prices = _make_prices(300)
        with pytest.raises(ValueError, match="same length"):
            compute_momentum_signal(prices, [3, 6, 12], [0.5, 0.5])

    def test_trending_asset_positive(self):
        """Monotonically increasing asset should have positive momentum."""
        n = 500
        idx = pd.bdate_range("2020-01-02", periods=n)
        prices = pd.DataFrame({"A": np.linspace(100, 200, n), "B": np.full(n, 100.0)}, index=idx)
        sig = compute_momentum_signal(prices, [3], [1.0])
        # Post-warmup, A should have positive signal
        assert sig["A"].iloc[-1] > 0

    def test_warmup_nan(self):
        prices = _make_prices(300)
        sig = compute_momentum_signal(prices, [12], [1.0])
        # First 12*21 = 252 rows should have NaN
        assert sig.iloc[100].isna().all()


class TestWeights:
    def test_long_short_structure(self):
        prices = _make_prices(600)
        sig = compute_momentum_signal(prices, [3, 6, 12], [0.3, 0.4, 0.3])
        cfg = MomentumConfig(n_long=3, n_short=3, allow_short=True)
        weights = compute_weights(sig, prices, cfg)
        # Post-warmup rows should have 3 positive and 3 negative weights
        post_warmup = weights.iloc[300]
        if not post_warmup.isna().all():
            positives = (post_warmup > 0).sum()
            negatives = (post_warmup < 0).sum()
            assert positives == 3
            assert negatives == 3

    def test_long_only_structure(self):
        prices = _make_prices(600)
        sig = compute_momentum_signal(prices, [3, 6, 12], [0.3, 0.4, 0.3])
        cfg = MomentumConfig(n_long=3, n_short=0, allow_short=False)
        weights = compute_weights(sig, prices, cfg)
        post_warmup = weights.iloc[300]
        if not post_warmup.isna().all():
            assert (post_warmup < 0).sum() == 0  # no shorts
            positives = (post_warmup > 0).sum()
            assert positives == 3

    def test_long_short_sums_to_zero(self):
        """Long-short market-neutral: net exposure ~0."""
        prices = _make_prices(600)
        sig = compute_momentum_signal(prices, [3, 6, 12], [0.3, 0.4, 0.3])
        cfg = MomentumConfig(n_long=3, n_short=3, allow_short=True)
        weights = compute_weights(sig, prices, cfg)
        row = weights.iloc[300]
        assert abs(row.sum()) < 0.01  # long and short cancel

    def test_long_only_sums_to_leverage(self):
        prices = _make_prices(600)
        sig = compute_momentum_signal(prices, [3, 6, 12], [0.3, 0.4, 0.3])
        cfg = MomentumConfig(n_long=3, n_short=0, allow_short=False, gross_leverage=1.0)
        weights = compute_weights(sig, prices, cfg)
        row = weights.iloc[300]
        assert abs(row.sum() - 1.0) < 0.01


class TestRebalanceHold:
    def test_holds_between_rebalances(self):
        idx = pd.bdate_range("2020-01-02", periods=50)
        raw = pd.DataFrame(np.random.RandomState(1).randn(50, 3),
                           index=idx, columns=["A", "B", "C"])
        held = apply_rebalance_hold(raw, rebalance_days=5)
        # Day 1 should equal day 0 (within hold period)
        assert (held.iloc[1] == held.iloc[0]).all()
        # Day 5 should be a rebalance (new weights)
        # At least one value should differ (probability 1 given random init)
        assert not (held.iloc[5] == held.iloc[0]).all()


class TestBacktest:
    def test_runs(self):
        prices = _make_prices(800)
        port_rets, weights = backtest(prices)
        assert len(port_rets) > 0
        assert len(weights) > 0

    def test_long_only_variant(self):
        prices = _make_prices(800)
        cfg = MomentumConfig(n_long=3, n_short=0, allow_short=False)
        port_rets, _ = backtest(prices, cfg)
        assert len(port_rets) > 0


class TestSharpe:
    def test_formula(self):
        rets = np.array([0.01, -0.005, 0.008, 0.003, -0.002])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(compute_sharpe(rets) - expected) < 0.001

    def test_empty(self):
        assert compute_sharpe(np.array([])) == 0.0

    def test_constant(self):
        assert compute_sharpe(np.full(100, 0.001)) == 0.0


class TestMetrics:
    def test_positive(self):
        rng = np.random.RandomState(1)
        m = compute_metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr"] > 0
        assert m["sharpe"] > 0

    def test_empty(self):
        assert compute_metrics(np.array([]))["cagr"] == 0


class TestWalkForward:
    def test_empty(self):
        assert walk_forward(pd.Series(dtype=float)) == []

    def test_produces_folds(self):
        prices = _make_prices(1500, seed=2)  # ~6 years
        port_rets, _ = backtest(prices)
        folds = walk_forward(port_rets)
        assert len(folds) >= 1


class TestCorrelations:
    def test_none_reference(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        s = pd.Series(np.random.RandomState(1).normal(0, 0.01, 100), index=idx)
        assert corr_to(s, None) is None

    def test_self_correlation(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        s = pd.Series(np.random.RandomState(1).normal(0, 0.01, 100), index=idx)
        c = corr_to(s, s)
        assert c is not None
        assert abs(c - 1.0) < 0.001

    def test_zero_variance(self):
        idx = pd.bdate_range("2020-01-02", periods=100)
        const = pd.Series(0.0, index=idx)
        other = pd.Series(np.random.RandomState(1).normal(0, 0.01, 100), index=idx)
        assert corr_to(const, other) is None


class TestRunVariant:
    def test_runs(self):
        prices = _make_prices(1000, seed=3)
        # Make sure SPY is in the universe for this test
        if "SPY" not in prices.columns:
            prices["SPY"] = prices.iloc[:, 0]  # reuse first asset as SPY
        exp1220_ref = build_exp1220_reference(prices)
        cfg = MomentumConfig(n_long=3, n_short=3, allow_short=True)
        v = run_variant(prices, cfg, "test", exp1220_ref, None)
        assert isinstance(v, VariantResult)
        assert v.name == "test"
        assert v.n_days > 0
        assert len(v.equity) > 0
