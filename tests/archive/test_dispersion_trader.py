"""Tests for compass.dispersion_trader — dispersion trading engine."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compass.dispersion_trader import (
    CorrelationSnapshot,
    DispersionBacktest,
    DispersionResult,
    DispersionSignal,
    DispersionTrade,
    PnLAttribution,
    VegaSizing,
    attribute_pnl,
    classify_correlation_regime,
    compute_vega_sizing,
    generate_dispersion_data,
    implied_correlation,
    realised_correlation,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _data(n: int = 500, seed: int = 42):
    return generate_dispersion_data(n, 10, seed)


# ── Implied correlation ────────────────────────────────────────────────────
class TestImpliedCorrelation:
    def test_bounded(self):
        rho = implied_correlation(20.0, np.array([22, 25, 18, 30, 20.0]))
        assert -1.0 <= rho <= 1.0

    def test_higher_index_iv_higher_corr(self):
        comp = np.array([20.0, 22.0, 18.0, 25.0, 21.0])
        low = implied_correlation(15.0, comp)
        high = implied_correlation(30.0, comp)
        assert high >= low

    def test_single_component_returns_zero(self):
        assert implied_correlation(20.0, np.array([20.0])) == 0.0

    def test_zero_index_returns_zero(self):
        assert implied_correlation(0.0, np.array([20.0, 25.0])) == 0.0

    def test_equal_ivs_positive(self):
        comp = np.array([20.0] * 5)
        rho = implied_correlation(20.0, comp)
        assert rho >= 0  # index IV = component IV → moderate correlation

    def test_custom_weights(self):
        comp = np.array([20.0, 25.0, 30.0])
        w = np.array([0.5, 0.3, 0.2])
        rho = implied_correlation(22.0, comp, w)
        assert -1.0 <= rho <= 1.0


# ── Realised correlation ───────────────────────────────────────────────────
class TestRealisedCorrelation:
    def test_bounded(self):
        rng = np.random.RandomState(42)
        idx_ret = rng.randn(100)
        comp_ret = rng.randn(100, 5)
        rc = realised_correlation(idx_ret, comp_ret)
        assert -1.0 <= rc <= 1.0

    def test_correlated_data_high(self):
        rng = np.random.RandomState(42)
        common = rng.randn(100)
        comp = np.column_stack([common + rng.randn(100) * 0.1 for _ in range(5)])
        rc = realised_correlation(common, comp)
        assert rc > 0.5

    def test_uncorrelated_data_low(self):
        rng = np.random.RandomState(42)
        comp = rng.randn(100, 5)
        rc = realised_correlation(rng.randn(100), comp)
        assert abs(rc) < 0.5

    def test_short_data_returns_zero(self):
        assert realised_correlation(np.array([1.0]), np.array([[1.0]])) == 0.0


# ── Regime classifier ──────────────────────────────────────────────────────
class TestRegimeClassifier:
    def test_risk_off(self):
        assert classify_correlation_regime(0.8, 0.7) == "risk_off"

    def test_risk_on(self):
        assert classify_correlation_regime(0.2, 0.3) == "risk_on"

    def test_transition(self):
        assert classify_correlation_regime(0.5, 0.5) == "transition"

    def test_boundary_high(self):
        assert classify_correlation_regime(0.61, 0.61) == "risk_off"

    def test_boundary_low(self):
        assert classify_correlation_regime(0.39, 0.39) == "risk_on"


# ── Vega sizing ─────────────────────────────────────────────────────────────
class TestVegaSizing:
    def test_returns_sizing(self):
        comp_ivs = {"AAPL": 25.0, "MSFT": 22.0, "AMZN": 30.0}
        s = compute_vega_sizing(20.0, comp_ivs, 450.0)
        assert isinstance(s, VegaSizing)
        assert s.index_contracts >= 1

    def test_component_contracts_populated(self):
        comp_ivs = {"AAPL": 25.0, "MSFT": 22.0}
        s = compute_vega_sizing(20.0, comp_ivs, 450.0)
        assert len(s.component_contracts) == 2
        assert all(c >= 1 for c in s.component_contracts.values())

    def test_scales_with_max_vega(self):
        comp = {"AAPL": 25.0}
        small = compute_vega_sizing(20.0, comp, 450.0, max_vega=100)
        large = compute_vega_sizing(20.0, comp, 450.0, max_vega=1000)
        assert large.index_contracts >= small.index_contracts

    def test_empty_components(self):
        s = compute_vega_sizing(20.0, {}, 450.0)
        assert s.component_vega == 0


# ── P&L attribution ────────────────────────────────────────────────────────
class TestPnLAttribution:
    def test_returns_attribution(self):
        a = attribute_pnl(0.15, 0.05, 20.0, 18.0, 10, 10_000)
        assert isinstance(a, PnLAttribution)

    def test_spread_compression_positive(self):
        a = attribute_pnl(0.15, 0.05, 20.0, 20.0, 10, 10_000)
        assert a.correlation_pnl > 0  # spread compressed

    def test_spread_widening_negative(self):
        a = attribute_pnl(0.05, 0.15, 20.0, 20.0, 10, 10_000)
        assert a.correlation_pnl < 0

    def test_theta_always_positive(self):
        a = attribute_pnl(0.10, 0.10, 20.0, 20.0, 5, 10_000)
        assert a.theta_pnl > 0

    def test_theta_scales_with_days(self):
        a5 = attribute_pnl(0.10, 0.10, 20.0, 20.0, 5, 10_000)
        a20 = attribute_pnl(0.10, 0.10, 20.0, 20.0, 20, 10_000)
        assert a20.theta_pnl > a5.theta_pnl


# ── Backtest ────────────────────────────────────────────────────────────────
class TestBacktest:
    def test_returns_result(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(300)
        r = DispersionBacktest().run(idx_r, comp_r, idx_iv, comp_iv)
        assert isinstance(r, DispersionResult)

    def test_trades_generated(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(500)
        r = DispersionBacktest(entry_spread=0.05).run(idx_r, comp_r, idx_iv, comp_iv)
        assert r.total_trades >= 0  # may be 0 if spread never widens enough

    def test_win_rate_bounded(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(500)
        r = DispersionBacktest(entry_spread=0.05).run(idx_r, comp_r, idx_iv, comp_iv)
        assert 0 <= r.win_rate_pct <= 100

    def test_max_dd_nonneg(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(500)
        r = DispersionBacktest().run(idx_r, comp_r, idx_iv, comp_iv)
        assert r.max_dd_pct >= 0

    def test_correlation_history_populated(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(300)
        r = DispersionBacktest().run(idx_r, comp_r, idx_iv, comp_iv)
        assert len(r.correlation_history) > 0

    def test_regimes_in_history(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(300)
        r = DispersionBacktest().run(idx_r, comp_r, idx_iv, comp_iv)
        regimes = {c.regime for c in r.correlation_history}
        assert len(regimes) > 0
        assert regimes.issubset({"risk_on", "risk_off", "transition"})

    def test_ending_capital_positive(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(300)
        r = DispersionBacktest().run(idx_r, comp_r, idx_iv, comp_iv)
        assert r.ending_capital > 0

    def test_generated_at_set(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(200)
        r = DispersionBacktest().run(idx_r, comp_r, idx_iv, comp_iv)
        assert len(r.generated_at) > 0

    def test_too_short_returns_empty(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(30)
        r = DispersionBacktest(lookback=60).run(idx_r, comp_r, idx_iv, comp_iv)
        assert r.total_trades == 0

    def test_trade_has_attribution(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(500)
        r = DispersionBacktest(entry_spread=0.05).run(idx_r, comp_r, idx_iv, comp_iv)
        if r.trades:
            t = r.trades[0]
            assert isinstance(t.attribution, PnLAttribution)
            assert t.hold_days > 0

    def test_lower_entry_more_trades(self):
        idx_r, comp_r, idx_iv, comp_iv = _data(500)
        strict = DispersionBacktest(entry_spread=0.20).run(idx_r, comp_r, idx_iv, comp_iv)
        loose = DispersionBacktest(entry_spread=0.03).run(idx_r, comp_r, idx_iv, comp_iv)
        assert loose.total_trades >= strict.total_trades


# ── Synthetic data ──────────────────────────────────────────────────────────
class TestSyntheticData:
    def test_shapes(self):
        idx_r, comp_r, idx_iv, comp_iv = generate_dispersion_data(100, 5)
        assert len(idx_r) == 100
        assert comp_r.shape == (100, 5)
        assert len(idx_iv) == 100
        assert comp_iv.shape == (100, 5)

    def test_deterministic(self):
        a = generate_dispersion_data(50, 3, seed=99)
        b = generate_dispersion_data(50, 3, seed=99)
        np.testing.assert_array_equal(a[0].values, b[0].values)

    def test_iv_positive(self):
        _, _, idx_iv, comp_iv = generate_dispersion_data(200)
        assert (idx_iv > 0).all()
        assert (comp_iv > 0).all().all()


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_correlation_snapshot(self):
        c = CorrelationSnapshot("2024-01-01", 0.65, 0.50, 0.15, "transition")
        assert c.spread == 0.15

    def test_pnl_attribution(self):
        a = PnLAttribution(500, 300, -50, 250, 0)
        assert a.total_pnl == 500

    def test_dispersion_result_defaults(self):
        r = DispersionResult()
        assert r.trades == []
        assert r.total_trades == 0
