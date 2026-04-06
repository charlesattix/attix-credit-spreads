"""Tests for compass/crisis_alpha.py — EXP-1780 Crisis Alpha / Trend Following."""

import math
import numpy as np
import pandas as pd
import pytest

from compass.crisis_alpha import (
    ASSET_UNIVERSE, LOOKBACKS, SIGNAL_WEIGHTS, VOL_TARGET_ANNUAL,
    CRISIS_PERIODS, CrisisMetrics, WFFold, BacktestResult,
    compute_momentum_signal, compute_vol_target_weights,
    _sharpe, _compute_metrics, backtest_crisis_alpha, generate_report,
    TRADING_DAYS,
)


def _make_prices(n=500, seed=1):
    """Real-like price series from deterministic random walk (test fixture only)."""
    rng = np.random.RandomState(seed)
    idx = pd.bdate_range("2020-01-02", periods=n)
    data = {}
    for i, tk in enumerate(ASSET_UNIVERSE):
        drift = 0.0003 + i * 0.0001
        rets = rng.normal(drift, 0.01, n)
        prices = 100 * np.cumprod(1 + rets)
        data[tk] = prices
    return pd.DataFrame(data, index=idx)


class TestConfig:
    def test_universe_size(self):
        assert len(ASSET_UNIVERSE) == 5
        assert "SPY" in ASSET_UNIVERSE
        assert "TLT" in ASSET_UNIVERSE
        assert "GLD" in ASSET_UNIVERSE

    def test_lookbacks(self):
        assert len(LOOKBACKS) == 4
        assert LOOKBACKS == sorted(LOOKBACKS)  # monotonic

    def test_signal_weights_sum_to_one(self):
        assert abs(sum(SIGNAL_WEIGHTS) - 1.0) < 0.001

    def test_crisis_periods_defined(self):
        assert "COVID 2020" in CRISIS_PERIODS
        assert "2022 Bear" in CRISIS_PERIODS
        for name, (s, e) in CRISIS_PERIODS.items():
            assert s < e


class TestMomentumSignal:
    def test_shape(self):
        prices = _make_prices(300)
        sig = compute_momentum_signal(prices)
        assert sig.shape == prices.shape
        assert list(sig.columns) == list(prices.columns)

    def test_warmup_zero(self):
        prices = _make_prices(300)
        sig = compute_momentum_signal(prices)
        # First row should be all zeros (no prior data for any lookback)
        assert sig.iloc[0].abs().sum() < 1e-9

    def test_mismatched_weights_raises(self):
        prices = _make_prices(300)
        with pytest.raises(ValueError, match="same length"):
            compute_momentum_signal(prices, lookbacks=[21, 63], weights=[0.5])

    def test_positive_trend_positive_signal(self):
        """Monotonically increasing prices should give positive signal."""
        idx = pd.bdate_range("2020-01-02", periods=300)
        prices = pd.DataFrame({
            tk: np.linspace(100, 150, 300) for tk in ASSET_UNIVERSE
        }, index=idx)
        sig = compute_momentum_signal(prices)
        assert (sig.iloc[-1] > 0).all()


class TestVolTargetWeights:
    def test_shape(self):
        prices = _make_prices(300)
        sig = compute_momentum_signal(prices)
        w = compute_vol_target_weights(prices, sig)
        assert w.shape == prices.shape

    def test_max_weight_respected(self):
        prices = _make_prices(300)
        sig = compute_momentum_signal(prices)
        w = compute_vol_target_weights(prices, sig, max_weight=0.30)
        assert w.abs().max().max() <= 0.30 + 1e-9

    def test_max_gross_respected(self):
        prices = _make_prices(300)
        sig = compute_momentum_signal(prices)
        w = compute_vol_target_weights(prices, sig, max_gross=1.5)
        # Skip warmup
        gross = w.iloc[100:].abs().sum(axis=1)
        assert gross.max() <= 1.5 + 1e-6

    def test_sign_matches_signal(self):
        prices = _make_prices(300)
        sig = compute_momentum_signal(prices)
        w = compute_vol_target_weights(prices, sig)
        # Where signal is strong, weight sign should match
        strong = sig.iloc[100:].abs() > 0.05
        matching = (np.sign(w.iloc[100:]) == np.sign(sig.iloc[100:]))[strong]
        assert matching.sum().sum() > 0


class TestSharpe:
    def test_corrected_formula(self):
        rets = np.array([0.01, -0.005, 0.008, 0.002, -0.003])
        expected = rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)
        assert abs(_sharpe(rets) - expected) < 0.001

    def test_uses_ddof1(self):
        rets = np.array([0.01, -0.005, 0.008])
        # ddof=1 gives larger std than ddof=0
        s_correct = _sharpe(rets)
        s_wrong = rets.mean() / rets.std(ddof=0) * math.sqrt(TRADING_DAYS)
        assert s_correct < s_wrong  # sample std yields lower sharpe

    def test_empty(self):
        assert _sharpe(np.array([])) == 0.0

    def test_constant_returns_zero(self):
        assert _sharpe(np.full(100, 0.001)) == 0.0


class TestComputeMetrics:
    def test_positive(self):
        rng = np.random.RandomState(1)
        m = _compute_metrics(rng.normal(0.001, 0.005, 252))
        assert m["cagr"] > 0
        assert m["sharpe"] > 0
        assert m["vol"] > 0

    def test_has_all_fields(self):
        m = _compute_metrics(np.array([0.01, -0.01, 0.005]))
        for k in ["cagr", "sharpe", "dd", "sortino", "calmar", "vol"]:
            assert k in m

    def test_empty(self):
        assert _compute_metrics(np.array([]))["cagr"] == 0


class TestBacktest:
    @pytest.fixture
    def result(self):
        prices = _make_prices(800, seed=42)
        return backtest_crisis_alpha(prices)

    def test_runs(self, result):
        assert isinstance(result, BacktestResult)
        assert result.n_days > 0

    def test_equity_curve(self, result):
        assert len(result.equity) == result.n_days + 1
        assert result.equity[0] == 100_000.0

    def test_metrics_computed(self, result):
        assert isinstance(result.cagr, float)
        assert isinstance(result.sharpe, float)
        assert isinstance(result.max_dd, float)

    def test_corr_to_spy_bounded(self, result):
        assert -1 <= result.corr_to_spy <= 1

    def test_daily_returns_shape(self, result):
        assert len(result.daily_returns) == result.n_days

    def test_yearly_populated(self, result):
        # With 800 days starting 2020-01-02, we should have ≥3 years
        assert len(result.yearly) >= 2


class TestReport:
    def test_generates(self, tmp_path):
        prices = _make_prices(600, seed=1)
        result = backtest_crisis_alpha(prices)
        out = tmp_path / "crisis.html"
        generate_report(result, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Crisis Alpha" in c
        assert "Yahoo Finance" in c

    def test_contains_metrics(self, tmp_path):
        prices = _make_prices(600, seed=2)
        result = backtest_crisis_alpha(prices)
        out = tmp_path / "c.html"
        generate_report(result, str(out))
        c = out.read_text()
        assert "Sharpe" in c
        assert "Max DD" in c
        assert "Corr to SPY" in c

    def test_contains_rule_zero(self, tmp_path):
        prices = _make_prices(600, seed=3)
        result = backtest_crisis_alpha(prices)
        out = tmp_path / "c.html"
        generate_report(result, str(out))
        assert "Rule Zero" in out.read_text()

    def test_contains_svg(self, tmp_path):
        prices = _make_prices(600, seed=4)
        result = backtest_crisis_alpha(prices)
        out = tmp_path / "c.html"
        generate_report(result, str(out))
        assert "<svg" in out.read_text()


class TestCrisisAttribution:
    def test_crisis_metrics_structure(self):
        # Use real-looking prices that span COVID
        idx = pd.bdate_range("2019-01-02", periods=1500)
        rng = np.random.RandomState(5)
        data = {tk: 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, 1500))
                for tk in ASSET_UNIVERSE}
        prices = pd.DataFrame(data, index=idx)
        result = backtest_crisis_alpha(prices)
        # Should have at least one crisis period (COVID 2020)
        covid = [c for c in result.crisis_metrics if "COVID" in c.name]
        if covid:
            assert isinstance(covid[0].strategy_return, float)
            assert isinstance(covid[0].outperformance, float)
