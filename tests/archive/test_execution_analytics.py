"""Tests for compass.execution_analytics — 42 tests."""
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from compass.execution_analytics import (
    ExecutionAnalytics, SlippageEstimate, MarketImpactEstimate,
    SpreadWidthAnalysis, OrderRoutingRec, CapacityEstimate,
    ShortfallResult, QualityScore, VenueResult, ExecutionReport,
)


# ===========================================================================
# 1. Slippage model
# ===========================================================================

class TestBidAskEstimate:
    def test_atm_low_vix(self):
        ba = ExecutionAnalytics.estimate_bid_ask(15, 30, 1.0)
        assert 0.01 < ba < 0.10  # tight ATM

    def test_high_vix_wider(self):
        low = ExecutionAnalytics.estimate_bid_ask(15, 30, 1.0)
        high = ExecutionAnalytics.estimate_bid_ask(40, 30, 1.0)
        assert high > low * 1.5

    def test_otm_wider(self):
        atm = ExecutionAnalytics.estimate_bid_ask(20, 30, 1.0)
        otm = ExecutionAnalytics.estimate_bid_ask(20, 30, 0.90)
        assert otm > atm

    def test_open_wider_than_midday(self):
        open_ba = ExecutionAnalytics.estimate_bid_ask(20, 30, 1.0, time_of_day=0.0)
        mid_ba = ExecutionAnalytics.estimate_bid_ask(20, 30, 1.0, time_of_day=0.5)
        assert open_ba > mid_ba

    def test_positive(self):
        for vix in [12, 20, 35, 50]:
            for dte in [7, 30, 60]:
                assert ExecutionAnalytics.estimate_bid_ask(vix, dte, 1.0) > 0


class TestSlippageEstimate:
    def test_basic(self):
        ea = ExecutionAnalytics()
        se = ea.estimate_slippage(20, 30, 1.0, 5.0, 1.5)
        assert isinstance(se, SlippageEstimate)
        assert se.slippage_per_contract > 0
        assert se.slippage_pct > 0

    def test_narrow_spread_more_slippage_pct(self):
        ea = ExecutionAnalytics()
        narrow = ea.estimate_slippage(20, 30, 1.0, 1.0, 0.30)
        wide = ea.estimate_slippage(20, 30, 1.0, 10.0, 3.0)
        assert narrow.slippage_pct > wide.slippage_pct

    def test_slippage_surface(self):
        ea = ExecutionAnalytics()
        df = ea.slippage_surface(vix_range=[15, 25], dte_range=[14, 30])
        assert len(df) == 4
        assert "slippage_pct" in df.columns


# ===========================================================================
# 2. Market impact
# ===========================================================================

class TestMarketImpact:
    def test_small_order(self):
        ea = ExecutionAnalytics()
        mi = ea.market_impact(1e6)
        assert isinstance(mi, MarketImpactEstimate)
        assert mi.total_impact_bps > 0
        assert mi.total_impact_bps < 100

    def test_larger_order_more_impact(self):
        ea = ExecutionAnalytics()
        small = ea.market_impact(1e6)
        large = ea.market_impact(100e6)
        assert large.total_impact_bps > small.total_impact_bps

    def test_impact_at_scale(self):
        ea = ExecutionAnalytics()
        impacts = ea.impact_at_scale()
        assert len(impacts) == 4
        bps = [m.total_impact_bps for m in impacts]
        assert bps == sorted(bps)  # monotonically increasing

    def test_participation_rate(self):
        ea = ExecutionAnalytics()
        mi = ea.market_impact(1e6)
        assert 0 < mi.participation_rate < 1

    def test_permanent_lt_temporary(self):
        ea = ExecutionAnalytics()
        mi = ea.market_impact(10e6)
        assert mi.permanent_impact_bps < mi.temporary_impact_bps

    def test_billion_dollar_impact(self):
        ea = ExecutionAnalytics()
        mi = ea.market_impact(1e9)
        assert mi.total_impact_bps > 10  # significant at $1B


# ===========================================================================
# 3. Spread width analysis
# ===========================================================================

class TestSpreadWidth:
    def test_basic(self):
        ea = ExecutionAnalytics()
        results = ea.spread_width_analysis()
        assert len(results) == 5  # $1, $2, $3, $5, $10
        assert all(isinstance(r, SpreadWidthAnalysis) for r in results)

    def test_wider_less_slippage_pct(self):
        ea = ExecutionAnalytics()
        results = ea.spread_width_analysis()
        assert results[0].slippage_pct > results[-1].slippage_pct

    def test_wider_more_capacity(self):
        ea = ExecutionAnalytics()
        results = ea.spread_width_analysis()
        assert results[-1].max_aum_millions > results[0].max_aum_millions

    def test_net_premium_positive(self):
        ea = ExecutionAnalytics()
        for r in ea.spread_width_analysis():
            assert r.net_premium > 0  # should still be profitable after slippage

    def test_custom_widths(self):
        ea = ExecutionAnalytics()
        results = ea.spread_width_analysis(widths=[2, 5, 10])
        assert len(results) == 3


# ===========================================================================
# 4. Smart order routing
# ===========================================================================

class TestOrderRouting:
    def test_low_urgency(self):
        rec = ExecutionAnalytics.order_routing_recommendation(20, 0.3, 5)
        assert rec.order_type == "limit"

    def test_high_urgency(self):
        rec = ExecutionAnalytics.order_routing_recommendation(20, 0.9, 5)
        assert rec.order_type == "market"

    def test_large_size(self):
        rec = ExecutionAnalytics.order_routing_recommendation(20, 0.3, 100)
        assert rec.order_type == "market"

    def test_high_vix_limit(self):
        rec = ExecutionAnalytics.order_routing_recommendation(40, 0.3, 5)
        assert rec.order_type == "limit"
        assert rec.limit_offset > 0.01

    def test_fill_rate_bounded(self):
        rec = ExecutionAnalytics.order_routing_recommendation(20, 0.5, 10)
        assert 0 < rec.expected_fill_rate <= 1.0


# ===========================================================================
# 5. Partial fills
# ===========================================================================

class TestPartialFill:
    def test_market_order_high_fill(self):
        fp = ExecutionAnalytics.fill_probability(0.0, 20, 5)
        assert fp > 0.95

    def test_limit_lower_fill(self):
        market = ExecutionAnalytics.fill_probability(0.0, 20, 5)
        limit = ExecutionAnalytics.fill_probability(3.0, 20, 5)
        assert limit < market

    def test_high_vix_helps_limits(self):
        low_vix = ExecutionAnalytics.fill_probability(2.0, 15, 10)
        high_vix = ExecutionAnalytics.fill_probability(2.0, 35, 10)
        assert high_vix >= low_vix

    def test_bounded(self):
        for offset in [0, 1, 3, 5]:
            fp = ExecutionAnalytics.fill_probability(offset, 20, 10)
            assert 0.1 <= fp <= 0.99


# ===========================================================================
# 6. Capacity estimation
# ===========================================================================

class TestCapacity:
    def test_basic(self):
        ea = ExecutionAnalytics()
        cap = ea.estimate_capacity("credit_spread_5w")
        assert isinstance(cap, CapacityEstimate)
        assert cap.max_aum_millions > 0
        assert cap.break_even_aum > 0

    def test_wider_more_capacity(self):
        ea = ExecutionAnalytics()
        narrow = ea.estimate_capacity("credit_spread_1w", 25)
        wide = ea.estimate_capacity("credit_spread_5w", 50)
        assert wide.max_aum_millions >= narrow.max_aum_millions

    def test_short_dte_least_capacity(self):
        ea = ExecutionAnalytics()
        caps = ea.capacity_by_strategy()
        sdte = [c for c in caps if c.strategy == "short_dte"][0]
        cs5 = [c for c in caps if c.strategy == "credit_spread_5w"][0]
        assert sdte.max_aum_millions < cs5.max_aum_millions

    def test_all_strategies(self):
        ea = ExecutionAnalytics()
        caps = ea.capacity_by_strategy()
        assert len(caps) == 6


# ===========================================================================
# Post-trade analytics (retained)
# ===========================================================================

class TestShortfall:
    def test_basic(self):
        sf = ExecutionAnalytics.implementation_shortfall(100, 100.02, 100.05, 100.10, 400, 500, "buy")
        assert sf.total_bps != 0

    def test_zero(self):
        sf = ExecutionAnalytics.implementation_shortfall(100, 100, 100, 100, 100, 100, "buy")
        assert sf.total_bps == 0.0


class TestQuality:
    def test_good(self):
        qs = ExecutionAnalytics.quality_score(2.0, 1.0, 1.0)
        assert qs.score > 70


# ===========================================================================
# Full analysis
# ===========================================================================

class TestFullAnalysis:
    def test_basic(self):
        ea = ExecutionAnalytics()
        report = ea.full_analysis()
        assert isinstance(report, ExecutionReport)
        assert len(report.slippage_model) > 0
        assert len(report.impact_model) > 0
        assert len(report.width_analysis) > 0
        assert len(report.capacity) > 0


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        ea = ExecutionAnalytics()
        report = ea.full_analysis()
        out = tmp_path / "exec.html"
        path = ea.generate_report(report, output_path=str(out))
        assert Path(path).exists()
        html = out.read_text()
        assert "Execution Analytics" in html
        assert "Market Impact" in html
        assert "Spread Width" in html
        assert "Capacity" in html
