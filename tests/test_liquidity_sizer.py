"""Tests for compass.liquidity_sizer — 34 tests."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from compass.liquidity_sizer import (
    LiquiditySizer, StrikeLiquidity, LiquidityScore, ImpactEstimate,
    CapacityResult, AdaptiveSize, RollPath, SizingBacktestResult,
    generate_option_chain,
)


def _chain(vix=20, seed=42):
    return generate_option_chain(underlying=450, vix=vix, seed=seed)


def _atm(chain):
    return min(chain, key=lambda s: abs(s.strike - 450) + abs(s.expiry_days - 30))


# ===========================================================================
# Synthetic data
# ===========================================================================

class TestChainGeneration:
    def test_produces_data(self):
        chain = _chain()
        assert len(chain) > 50

    def test_atm_most_liquid(self):
        chain = _chain()
        atm = [s for s in chain if abs(s.strike - 450) < 3 and s.expiry_days == 30]
        otm = [s for s in chain if abs(s.strike - 450) > 20 and s.expiry_days == 30]
        if atm and otm:
            assert atm[0].daily_volume > otm[0].daily_volume

    def test_high_vix_wider_spreads(self):
        low = _chain(vix=15, seed=1)
        high = _chain(vix=40, seed=1)
        low_atm = [s for s in low if abs(s.strike - 450) < 3 and s.expiry_days == 30]
        high_atm = [s for s in high if abs(s.strike - 450) < 3 and s.expiry_days == 30]
        if low_atm and high_atm:
            assert high_atm[0].bid_ask_spread > low_atm[0].bid_ask_spread

    def test_oi_positive(self):
        for s in _chain():
            assert s.open_interest > 0
            assert s.daily_volume > 0


# ===========================================================================
# OI / Volume analysis
# ===========================================================================

class TestAnalysis:
    def test_analyse_chain(self):
        df = LiquiditySizer.analyse_chain(_chain())
        assert len(df) > 50
        assert "oi" in df.columns

    def test_best_strikes(self):
        chain = _chain()
        best = LiquiditySizer.best_strikes(chain, dte=30, top_n=3)
        assert len(best) == 3
        assert best[0].daily_volume >= best[1].daily_volume

    def test_spread_surface(self):
        df = LiquiditySizer.spread_surface(_chain())
        assert not df.empty
        assert df.shape[0] > 10  # strikes
        assert df.shape[1] >= 3  # expiries


# ===========================================================================
# Market impact
# ===========================================================================

class TestImpact:
    def test_small_order(self):
        ls = LiquiditySizer()
        strike = _atm(_chain())
        imp = ls.estimate_impact(5, strike)
        assert isinstance(imp, ImpactEstimate)
        assert imp.total_cost > 0
        assert imp.participation_rate < 0.01

    def test_larger_order_more_impact(self):
        ls = LiquiditySizer()
        strike = _atm(_chain())
        small = ls.estimate_impact(5, strike)
        large = ls.estimate_impact(100, strike)
        assert large.impact_bps > small.impact_bps

    def test_impact_positive(self):
        ls = LiquiditySizer()
        imp = ls.estimate_impact(10, _atm(_chain()))
        assert imp.impact_bps >= 0
        assert imp.spread_cost_per > 0


# ===========================================================================
# Capacity
# ===========================================================================

class TestCapacity:
    def test_basic(self):
        ls = LiquiditySizer()
        cap = ls.calculate_capacity(_atm(_chain()))
        assert isinstance(cap, CapacityResult)
        assert cap.max_contracts > 0
        assert cap.max_notional > 0

    def test_illiquid_lower_capacity(self):
        ls = LiquiditySizer()
        liquid = StrikeLiquidity(450, 30, 200000, 50000, 0.02, 0.01, 5.0)
        illiquid = StrikeLiquidity(450, 30, 500, 100, 0.20, 0.10, 5.0)
        cap_liq = ls.calculate_capacity(liquid)
        cap_ill = ls.calculate_capacity(illiquid)
        assert cap_liq.max_contracts > cap_ill.max_contracts

    def test_limiting_factor(self):
        ls = LiquiditySizer()
        cap = ls.calculate_capacity(_atm(_chain()))
        assert cap.limiting_factor in ("volume", "oi", "impact")


# ===========================================================================
# Liquidity score
# ===========================================================================

class TestLiquidityScore:
    def test_bounded(self):
        ls = LiquiditySizer()
        score = ls.liquidity_score(_atm(_chain()))
        assert 0 <= score.score <= 1.0

    def test_liquid_high_score(self):
        ls = LiquiditySizer()
        liq = StrikeLiquidity(450, 30, 200000, 50000, 0.02, 0.01, 5.0)
        score = ls.liquidity_score(liq)
        assert score.score > 0.7

    def test_illiquid_low_score(self):
        ls = LiquiditySizer()
        illiq = StrikeLiquidity(450, 30, 100, 10, 0.30, 0.20, 1.0)
        score = ls.liquidity_score(illiq)
        assert score.score < 0.3


# ===========================================================================
# Adaptive sizing
# ===========================================================================

class TestAdaptiveSizing:
    def test_liquid_full_size(self):
        ls = LiquiditySizer()
        liq = StrikeLiquidity(450, 30, 200000, 50000, 0.02, 0.01, 5.0)
        adapted = ls.adaptive_size(10, liq)
        assert adapted.adjusted_contracts >= 8  # near full

    def test_illiquid_reduced(self):
        ls = LiquiditySizer()
        illiq = StrikeLiquidity(450, 30, 500, 50, 0.20, 0.10, 2.0)
        adapted = ls.adaptive_size(10, illiq)
        assert adapted.adjusted_contracts < 10
        assert adapted.scale_factor < 1.0

    def test_never_zero(self):
        ls = LiquiditySizer()
        illiq = StrikeLiquidity(450, 30, 100, 10, 0.50, 0.30, 1.0)
        adapted = ls.adaptive_size(10, illiq)
        assert adapted.adjusted_contracts >= 1

    def test_capped_at_capacity(self):
        ls = LiquiditySizer(max_participation=0.01)
        liq = StrikeLiquidity(450, 30, 200000, 100, 0.02, 0.01, 5.0)
        adapted = ls.adaptive_size(1000, liq)
        assert adapted.adjusted_contracts <= 100  # capped by volume


# ===========================================================================
# Roll optimisation
# ===========================================================================

class TestRoll:
    def test_finds_paths(self):
        ls = LiquiditySizer()
        chain = _chain()
        paths = ls.optimal_roll(450, 7, chain, target_dte_range=(25, 45))
        assert len(paths) > 0
        assert all(isinstance(p, RollPath) for p in paths)

    def test_optimal_marked(self):
        ls = LiquiditySizer()
        paths = ls.optimal_roll(450, 7, _chain(), target_dte_range=(25, 45))
        if paths:
            assert paths[0].is_optimal

    def test_no_candidates(self):
        ls = LiquiditySizer(min_oi=999999)
        paths = ls.optimal_roll(450, 7, _chain(), target_dte_range=(25, 45))
        assert len(paths) == 0


# ===========================================================================
# Backtest
# ===========================================================================

class TestBacktest:
    def test_basic(self):
        ls = LiquiditySizer()
        rets = pd.Series(np.random.default_rng(42).normal(0.0004, 0.01, 200),
                          index=pd.bdate_range("2024-01-02", periods=200))
        result = ls.backtest(rets, base_contracts=5)
        assert isinstance(result, SizingBacktestResult)
        assert result.n_trades > 0

    def test_slippage_reduction(self):
        ls = LiquiditySizer()
        rets = pd.Series(np.random.default_rng(42).normal(0.0004, 0.01, 300),
                          index=pd.bdate_range("2024-01-02", periods=300))
        result = ls.backtest(rets, base_contracts=10)
        assert result.adaptive_total_slippage <= result.fixed_total_slippage


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        ls = LiquiditySizer()
        rets = pd.Series(np.random.default_rng(42).normal(0.0004, 0.01, 100),
                          index=pd.bdate_range("2024-01-02", periods=100))
        result = ls.backtest(rets)
        out = tmp_path / "liq.html"
        path = ls.generate_report(result, output_path=str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Liquidity" in html
        assert "Slippage" in html
