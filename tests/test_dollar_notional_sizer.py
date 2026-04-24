"""
Tests for compass.dollar_notional_sizer

All option prices are realistic hardcoded fixtures from real market levels.
NO synthetic data.  NO randomness.  Rule Zero compliant.

Fixture reference prices (approximate April 2026 levels):
  SPY ~540, QQQ ~460, XLF ~44, XLI ~130, GLD ~230, SLV ~28

Typical credit spread max-loss:
  SPY 5-wide put spread: max_loss = $500/contract (5 × 100)
  QQQ 5-wide put spread: max_loss = $500/contract
  XLF 2-wide put spread: max_loss = $200/contract
  XLI 3-wide put spread: max_loss = $300/contract
  GLD calendar: max_loss = $350/contract (debit paid)
  SLV calendar: max_loss = $150/contract (debit paid)
"""

import math
import pytest

from compass.dollar_notional_sizer import (
    DollarNotionalSizer,
    PortfolioSizingResult,
    SizingResult,
    SpreadQuote,
    size_from_risk_decision,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures — realistic hardcoded option prices
# ═══════════════════════════════════════════════════════════════════════════════


def _spy_quote() -> SpreadQuote:
    """SPY 28-DTE 5-wide put credit spread, ~5% OTM."""
    return SpreadQuote(
        stream="exp1220",
        net_credit=0.85,       # $0.85 credit per share = $85/contract
        max_loss=500.0,        # $5 wide × 100 = $500 max loss
        bid=0.82,
        ask=0.88,
        underlying_price=540.0,
        multiplier=100,
    )


def _qqq_quote() -> SpreadQuote:
    """QQQ 28-DTE 5-wide put credit spread, ~5% OTM."""
    return SpreadQuote(
        stream="qqq_cs",
        net_credit=0.92,
        max_loss=500.0,
        bid=0.89,
        ask=0.95,
        underlying_price=460.0,
        multiplier=100,
    )


def _xlf_quote() -> SpreadQuote:
    """XLF delta-targeted 2-wide put credit spread."""
    return SpreadQuote(
        stream="xlf_cs",
        net_credit=0.35,
        max_loss=200.0,
        bid=0.32,
        ask=0.38,
        underlying_price=44.0,
        multiplier=100,
    )


def _xli_quote() -> SpreadQuote:
    """XLI delta-targeted 3-wide put credit spread."""
    return SpreadQuote(
        stream="xli_cs",
        net_credit=0.48,
        max_loss=300.0,
        bid=0.45,
        ask=0.51,
        underlying_price=130.0,
        multiplier=100,
    )


def _gld_quote() -> SpreadQuote:
    """GLD calendar spread (debit)."""
    return SpreadQuote(
        stream="gld_cal",
        net_credit=-1.20,      # debit spread
        max_loss=350.0,
        bid=1.15,
        ask=1.25,
        underlying_price=230.0,
        multiplier=100,
    )


def _slv_quote() -> SpreadQuote:
    """SLV calendar spread (debit)."""
    return SpreadQuote(
        stream="slv_cal",
        net_credit=-0.65,
        max_loss=150.0,
        bid=0.60,
        ask=0.70,
        underlying_price=28.0,
        multiplier=100,
    )


def _all_quotes() -> dict:
    """All 6 main stream quotes."""
    return {
        "exp1220": _spy_quote(),
        "qqq_cs": _qqq_quote(),
        "xlf_cs": _xlf_quote(),
        "xli_cs": _xli_quote(),
        "gld_cal": _gld_quote(),
        "slv_cal": _slv_quote(),
    }


def _equal_weights_6() -> dict:
    """Equal weights for 6 streams (~16.7% each)."""
    streams = ["exp1220", "qqq_cs", "xlf_cs", "xli_cs", "gld_cal", "slv_cal"]
    w = 1.0 / len(streams)
    return {s: w for s in streams}


def _lw_weights() -> dict:
    """Realistic Ledoit-Wolf risk-parity weights (8 streams, sums to ~1.0)."""
    return {
        "exp1220": 0.25,
        "xlf_cs": 0.12,
        "xli_cs": 0.10,
        "qqq_cs": 0.15,
        "gld_cal": 0.10,
        "slv_cal": 0.05,
        "cross_vol": 0.13,
        "v5_hedge": 0.10,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SpreadQuote validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpreadQuote:

    def test_valid_credit_spread(self):
        q = _spy_quote()
        assert q.max_loss == 500.0
        assert q.net_credit == 0.85

    def test_valid_debit_spread(self):
        q = _gld_quote()
        assert q.net_credit < 0  # debit
        assert q.max_loss > 0

    def test_zero_max_loss_raises(self):
        with pytest.raises(ValueError, match="max_loss must be positive"):
            SpreadQuote(stream="bad", net_credit=0.50, max_loss=0.0)

    def test_negative_max_loss_raises(self):
        with pytest.raises(ValueError, match="max_loss must be positive"):
            SpreadQuote(stream="bad", net_credit=0.50, max_loss=-100.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Conservative rounding
# ═══════════════════════════════════════════════════════════════════════════════


class TestConservativeRounding:

    def setup_method(self):
        self.sizer = DollarNotionalSizer()

    def test_floor_basic(self):
        """4.7 contracts → 4 (conservative)."""
        assert self.sizer._conservative_round(4.7) == 4

    def test_floor_at_half(self):
        """4.5 → 4 (we do NOT round at .5 — conservative)."""
        assert self.sizer._conservative_round(4.5) == 4

    def test_round_up_at_threshold(self):
        """4.95 → 5 (above default 0.95 threshold)."""
        assert self.sizer._conservative_round(4.95) == 5

    def test_round_up_exact_threshold(self):
        """4.95 with threshold=0.95 → 5."""
        assert self.sizer._conservative_round(4.95) == 5

    def test_just_below_threshold(self):
        """4.94 → 4 (clearly below 0.95)."""
        assert self.sizer._conservative_round(4.94) == 4

    def test_exact_integer(self):
        """5.0 → 5."""
        assert self.sizer._conservative_round(5.0) == 5

    def test_zero(self):
        assert self.sizer._conservative_round(0.0) == 0

    def test_small_fraction(self):
        """0.3 → 0."""
        assert self.sizer._conservative_round(0.3) == 0

    def test_negative_returns_zero(self):
        assert self.sizer._conservative_round(-2.5) == 0

    def test_always_floor_mode(self):
        """round_up_threshold=1.0 means we never round up."""
        sizer = DollarNotionalSizer(round_up_threshold=1.0)
        assert sizer._conservative_round(4.99) == 4

    def test_large_number(self):
        """1000.94 → 1000."""
        assert self.sizer._conservative_round(1000.94) == 1000

    def test_large_number_rounds_up(self):
        """1000.96 → 1001."""
        assert self.sizer._conservative_round(1000.96) == 1001


# ═══════════════════════════════════════════════════════════════════════════════
# Single stream sizing
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingleStreamSizing:

    def setup_method(self):
        self.sizer = DollarNotionalSizer()

    def test_basic_spy_sizing(self):
        """$10,000 target / $500 max_loss = 20 contracts exactly."""
        contracts, frac = self.sizer.size_single_stream(10_000.0, _spy_quote())
        assert contracts == 20
        assert frac == 20.0

    def test_fractional_rounds_down(self):
        """$7,300 / $500 = 14.6 → 14 contracts (conservative)."""
        contracts, frac = self.sizer.size_single_stream(7_300.0, _spy_quote())
        assert contracts == 14
        assert abs(frac - 14.6) < 1e-9

    def test_fractional_rounds_up_near_threshold(self):
        """$7,475 / $500 = 14.95 → 15 contracts (at 0.95 threshold)."""
        contracts, frac = self.sizer.size_single_stream(7_475.0, _spy_quote())
        assert contracts == 15
        assert abs(frac - 14.95) < 1e-9

    def test_zero_target(self):
        contracts, frac = self.sizer.size_single_stream(0.0, _spy_quote())
        assert contracts == 0
        assert frac == 0.0

    def test_negative_target(self):
        contracts, frac = self.sizer.size_single_stream(-5000.0, _spy_quote())
        assert contracts == 0

    def test_small_target_below_one_contract(self):
        """$200 / $500 = 0.4 → 0 contracts."""
        contracts, frac = self.sizer.size_single_stream(200.0, _spy_quote())
        assert contracts == 0
        assert abs(frac - 0.4) < 1e-9

    def test_xlf_sizing(self):
        """$1,000 / $200 = 5 contracts."""
        contracts, frac = self.sizer.size_single_stream(1_000.0, _xlf_quote())
        assert contracts == 5
        assert frac == 5.0

    def test_slv_sizing(self):
        """$2,250 / $150 = 15 contracts."""
        contracts, frac = self.sizer.size_single_stream(2_250.0, _slv_quote())
        assert contracts == 15


# ═══════════════════════════════════════════════════════════════════════════════
# Portfolio sizing — core cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestPortfolioSizing:

    def setup_method(self):
        self.sizer = DollarNotionalSizer()

    def test_basic_100k_equal_weight(self):
        """$100K equity, 1× leverage, 6 equal streams.
        Allocatable = 100K - 5K buffer = 95K. Per stream: 95K × (1/6) = ~$15,833.
        SPY: floor(15833/500) = 31 contracts.
        """
        result = self.sizer.size_portfolio(
            account_equity=100_000.0,
            weights=_equal_weights_6(),
            leverage=1.0,
            quotes=_all_quotes(),
        )
        assert isinstance(result, PortfolioSizingResult)
        assert result.account_equity == 100_000.0
        assert len(result.stream_sizes) == 6
        assert result.total_margin_consumed > 0
        spy_size = result.stream_sizes["exp1220"]
        assert spy_size.contracts == 31

    def test_1m_with_leverage(self):
        """$1M equity, 2× leverage — T3 scenario.
        Allocatable: 1M × 2 - 50K buffer = $1.95M. Per stream: 1.95M/6 = $325K.
        SPY: floor(325K/500) = 650 contracts.
        """
        result = self.sizer.size_portfolio(
            account_equity=1_000_000.0,
            weights=_equal_weights_6(),
            leverage=2.0,
            quotes=_all_quotes(),
        )
        spy_size = result.stream_sizes["exp1220"]
        assert spy_size.contracts == 650
        assert result.total_margin_consumed > 0

    def test_leverage_capped_at_max(self):
        """Requested 5× but max is 3×."""
        result = self.sizer.size_portfolio(
            account_equity=100_000.0,
            weights=_equal_weights_6(),
            leverage=5.0,
            quotes=_all_quotes(),
        )
        assert result.leverage_capped
        assert any("clamped" in n for n in result.notes)

    def test_zero_equity(self):
        result = self.sizer.size_portfolio(
            account_equity=0.0,
            weights=_equal_weights_6(),
            leverage=1.0,
            quotes=_all_quotes(),
        )
        assert len(result.stream_sizes) == 0
        assert result.total_margin_consumed == 0.0

    def test_negative_equity(self):
        result = self.sizer.size_portfolio(
            account_equity=-50_000.0,
            weights=_equal_weights_6(),
            leverage=1.0,
            quotes=_all_quotes(),
        )
        assert len(result.stream_sizes) == 0

    def test_empty_weights(self):
        result = self.sizer.size_portfolio(
            account_equity=100_000.0,
            weights={},
            leverage=1.0,
            quotes=_all_quotes(),
        )
        assert len(result.stream_sizes) == 0
        assert result.total_margin_consumed == 0.0

    def test_missing_quote_stream_skipped(self):
        """Stream in weights but not in quotes → skipped, not error."""
        weights = {"exp1220": 0.50, "phantom_stream": 0.50}
        result = self.sizer.size_portfolio(
            account_equity=100_000.0,
            weights=weights,
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        assert "exp1220" in result.stream_sizes
        assert "phantom_stream" not in result.stream_sizes

    def test_zero_weight_stream_skipped(self):
        weights = {"exp1220": 0.50, "qqq_cs": 0.0}
        result = self.sizer.size_portfolio(
            account_equity=100_000.0,
            weights=weights,
            leverage=1.0,
            quotes=_all_quotes(),
        )
        assert "exp1220" in result.stream_sizes
        assert "qqq_cs" not in result.stream_sizes


# ═══════════════════════════════════════════════════════════════════════════════
# Weight clamping
# ═══════════════════════════════════════════════════════════════════════════════


class TestWeightClamping:

    def setup_method(self):
        self.sizer = DollarNotionalSizer(max_weight_per_stream=0.40)

    def test_single_stream_capped_at_40pct(self):
        """One stream at 80% → clamped to 40%, other stays 20%.
        Total = 0.60 ≤ 1.0 so no renormalization.
        """
        weights = {"exp1220": 0.80, "qqq_cs": 0.20}
        clamped = self.sizer._clamp_weights(weights)
        assert clamped["exp1220"] == 0.40
        assert clamped["qqq_cs"] == 0.20
        assert sum(clamped.values()) == pytest.approx(0.60, rel=1e-6)

    def test_weights_summing_above_one_renormalized(self):
        weights = {"exp1220": 0.60, "qqq_cs": 0.60}
        clamped = self.sizer._clamp_weights(weights)
        # Both clamped to 0.40, total=0.80 ≤ 1.0 → no renorm needed
        assert clamped["exp1220"] == 0.40
        assert clamped["qqq_cs"] == 0.40
        assert sum(clamped.values()) <= 1.0

    def test_negative_weights_dropped(self):
        weights = {"exp1220": 0.50, "qqq_cs": -0.10}
        clamped = self.sizer._clamp_weights(weights)
        assert "qqq_cs" not in clamped
        assert clamped["exp1220"] == pytest.approx(0.40)

    def test_lw_weights_valid(self):
        """Realistic LW weights should pass through without issue."""
        clamped = self.sizer._clamp_weights(_lw_weights())
        assert sum(clamped.values()) <= 1.0 + 1e-9
        for v in clamped.values():
            assert v <= 0.40 + 1e-9

    def test_many_small_weights_no_renorm(self):
        """Many streams under the cap, total < 1 → no renorm."""
        weights = {f"s{i}": 0.10 for i in range(8)}  # total 0.80
        clamped = self.sizer._clamp_weights(weights)
        assert sum(clamped.values()) == pytest.approx(0.80, rel=1e-6)

    def test_many_large_weights_renormalized(self):
        """5 streams at 0.40 → total 2.0 → renorm to sum=1.0."""
        weights = {f"s{i}": 0.50 for i in range(5)}
        clamped = self.sizer._clamp_weights(weights)
        # Each capped to 0.40 → total 2.0 → renormed to 0.20 each
        assert sum(clamped.values()) == pytest.approx(1.0, rel=1e-6)
        for v in clamped.values():
            assert v == pytest.approx(0.20, rel=1e-6)


# ═══════════════════════════════════════════════════════════════════════════════
# Margin buffer
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarginBuffer:

    def test_default_5pct_buffer(self):
        """$100K, 5% buffer, weight=0.30 → allocatable=95K, target=28.5K, 57 contracts."""
        sizer = DollarNotionalSizer(margin_buffer_pct=0.05)
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights={"exp1220": 0.30},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        sr = result.stream_sizes["exp1220"]
        # allocatable=95K, target=95K*0.30=28.5K, 28500/500=57
        assert sr.contracts == 57

    def test_zero_buffer(self):
        """$100K, 0% buffer, weight=0.30 → allocatable=100K, target=30K, 60 contracts."""
        sizer = DollarNotionalSizer(margin_buffer_pct=0.0)
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights={"exp1220": 0.30},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        sr = result.stream_sizes["exp1220"]
        assert sr.contracts == 60

    def test_large_buffer_reduces_capacity(self):
        """$100K, 50% buffer, weight=0.30 → allocatable=50K, target=15K, 30 contracts."""
        sizer = DollarNotionalSizer(margin_buffer_pct=0.50)
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights={"exp1220": 0.30},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        sr = result.stream_sizes["exp1220"]
        assert sr.contracts == 30


# ═══════════════════════════════════════════════════════════════════════════════
# Leverage scenarios
# ═══════════════════════════════════════════════════════════════════════════════


class TestLeverageScenarios:

    def test_1x_leverage(self):
        """$100K, 1×, w=0.30, buffer=5% → target=28.5K, 57 contracts."""
        sizer = DollarNotionalSizer()
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights={"exp1220": 0.30},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        assert result.stream_sizes["exp1220"].contracts == 57

    def test_2x_leverage_doubles_capacity(self):
        """$100K, 2×, w=0.30, buffer=5% → alloc=195K, target=58.5K, 117 contracts."""
        sizer = DollarNotionalSizer()
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights={"exp1220": 0.30},
            leverage=2.0,
            quotes={"exp1220": _spy_quote()},
        )
        assert result.stream_sizes["exp1220"].contracts == 117

    def test_3x_leverage_max_default(self):
        """$100K, 3×, w=0.30, buffer=5% → alloc=295K, target=88.5K, 177 contracts."""
        sizer = DollarNotionalSizer(max_leverage=3.0)
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights={"exp1220": 0.30},
            leverage=3.0,
            quotes={"exp1220": _spy_quote()},
        )
        assert result.stream_sizes["exp1220"].contracts == 177

    def test_custom_max_leverage_1_5x(self):
        """Requested 3× but cap is 1.5× → alloc=145K, target=43.5K, 87 contracts."""
        sizer = DollarNotionalSizer(max_leverage=1.5)
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights={"exp1220": 0.30},
            leverage=3.0,
            quotes={"exp1220": _spy_quote()},
        )
        assert result.leverage_capped
        assert result.stream_sizes["exp1220"].contracts == 87


# ═══════════════════════════════════════════════════════════════════════════════
# Min contracts policy
# ═══════════════════════════════════════════════════════════════════════════════


class TestMinContractsPolicy:

    def test_zero_below_min_default(self):
        """Small allocation that yields < min_contracts → zeroed.
        $10K, w=0.10, buffer=5% → alloc=9.5K, target=950, 1 contract < min 5 → 0.
        """
        sizer = DollarNotionalSizer(min_contracts=5, zero_below_min=True)
        result = sizer.size_portfolio(
            account_equity=10_000.0,
            weights={"exp1220": 0.10},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        sr = result.stream_sizes.get("exp1220")
        assert sr is not None
        assert sr.contracts == 0
        assert sr.capped_reason is not None

    def test_bump_to_min_when_not_zeroing(self):
        """Same scenario but zero_below_min=False → bumped to 5."""
        sizer = DollarNotionalSizer(min_contracts=5, zero_below_min=False)
        result = sizer.size_portfolio(
            account_equity=10_000.0,
            weights={"exp1220": 0.10},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        sr = result.stream_sizes["exp1220"]
        assert sr.contracts == 5

    def test_above_min_not_affected(self):
        """If sizing naturally yields >= min, no change."""
        sizer = DollarNotionalSizer(min_contracts=5, zero_below_min=True)
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights={"exp1220": 0.30},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        sr = result.stream_sizes["exp1220"]
        assert sr.contracts == 57  # well above min


# ═══════════════════════════════════════════════════════════════════════════════
# Budget exhaustion
# ═══════════════════════════════════════════════════════════════════════════════


class TestBudgetExhaustion:

    def test_total_margin_never_exceeds_allocatable(self):
        """Total margin across all streams must stay within budget."""
        sizer = DollarNotionalSizer(margin_buffer_pct=0.0)
        weights = {"exp1220": 0.50, "qqq_cs": 0.50}
        result = sizer.size_portfolio(
            account_equity=10_000.0,
            weights=weights,
            leverage=1.0,
            quotes=_all_quotes(),
        )
        # Both capped at 0.40 by weight clamp
        total_consumed = result.total_margin_consumed
        assert total_consumed <= 10_000.0 + 1e-6

    def test_budget_pressure_with_many_streams(self):
        """6 equal streams at $100K should consume close to allocatable."""
        sizer = DollarNotionalSizer()
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights=_equal_weights_6(),
            leverage=1.0,
            quotes=_all_quotes(),
        )
        assert result.total_margin_consumed <= 95_000.0 + 1e-6  # allocatable


# ═══════════════════════════════════════════════════════════════════════════════
# T3+ scale scenarios ($1M+)
# ═══════════════════════════════════════════════════════════════════════════════


class TestT3PlusScale:
    """Phase 9 prerequisite: dollar-notional sizing at $1M+ scale."""

    def test_1m_equity_2x_leverage_6_streams(self):
        """$1M at 2× leverage with equal weights — the T3 scenario."""
        sizer = DollarNotionalSizer()
        result = sizer.size_portfolio(
            account_equity=1_000_000.0,
            weights=_equal_weights_6(),
            leverage=2.0,
            quotes=_all_quotes(),
        )
        assert result.total_margin_consumed > 0
        for stream, sr in result.stream_sizes.items():
            assert sr.contracts > 0, f"{stream} got 0 contracts at $1M"

    def test_10m_equity_3x_leverage(self):
        """$10M at 3× — T4 scenario.
        Allocatable: 10M × 3 - 500K buffer = 29.5M.
        Per stream (eq6): ~4.917M. SPY: floor(4917K/500) = 9833.
        """
        sizer = DollarNotionalSizer(max_leverage=3.0)
        weights = _equal_weights_6()
        result = sizer.size_portfolio(
            account_equity=10_000_000.0,
            weights=weights,
            leverage=3.0,
            quotes=_all_quotes(),
        )
        spy_contracts = result.stream_sizes["exp1220"].contracts
        assert spy_contracts == 9833
        assert result.total_margin_consumed <= 10_000_000.0 * 3.0

    def test_50m_equity_slv_bottleneck(self):
        """$50M — SLV is capacity-bottlenecked per MASTERPLAN."""
        sizer = DollarNotionalSizer()
        weights = _lw_weights()
        quotes = _all_quotes()
        result = sizer.size_portfolio(
            account_equity=50_000_000.0,
            weights={k: v for k, v in weights.items() if k in quotes},
            leverage=3.0,
            quotes=quotes,
        )
        slv_contracts = result.stream_sizes["slv_cal"].contracts
        assert slv_contracts > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:

    def test_very_small_account(self):
        """$500 account — too small for most spreads."""
        sizer = DollarNotionalSizer()
        result = sizer.size_portfolio(
            account_equity=500.0,
            weights={"exp1220": 1.0},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        # $500 - $25 buffer = $475, clamped weight 0.40 = $190 → 0 contracts
        sr = result.stream_sizes.get("exp1220")
        if sr is not None:
            assert sr.contracts == 0

    def test_exactly_one_contract(self):
        """Account sized to yield exactly 1 contract with unclamped weight."""
        sizer = DollarNotionalSizer(margin_buffer_pct=0.0, max_weight_per_stream=1.0)
        result = sizer.size_portfolio(
            account_equity=500.0,
            weights={"exp1220": 1.0},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        sr = result.stream_sizes["exp1220"]
        assert sr.contracts == 1  # 500/500 = 1.0

    def test_single_stream_full_allocation(self):
        """Single stream with 100% weight and max_weight=1.0.
        $50K, buffer=0 → 50K/500 = 100 contracts.
        """
        sizer = DollarNotionalSizer(margin_buffer_pct=0.0, max_weight_per_stream=1.0)
        result = sizer.size_portfolio(
            account_equity=50_000.0,
            weights={"exp1220": 1.0},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        assert result.stream_sizes["exp1220"].contracts == 100

    def test_all_weights_zero(self):
        weights = {"exp1220": 0.0, "qqq_cs": 0.0}
        sizer = DollarNotionalSizer()
        result = sizer.size_portfolio(
            account_equity=100_000.0,
            weights=weights,
            leverage=1.0,
            quotes=_all_quotes(),
        )
        assert len(result.stream_sizes) == 0

    def test_very_expensive_spread(self):
        """Spread with high max_loss relative to allocation.
        $25K, buffer=0, max_weight=1.0 → 25K/10K = 2.5 → 2 (conservative).
        """
        expensive = SpreadQuote(
            stream="expensive",
            net_credit=5.00,
            max_loss=10_000.0,
            underlying_price=540.0,
        )
        sizer = DollarNotionalSizer(margin_buffer_pct=0.0, max_weight_per_stream=1.0)
        result = sizer.size_portfolio(
            account_equity=25_000.0,
            weights={"expensive": 1.0},
            leverage=1.0,
            quotes={"expensive": expensive},
        )
        sr = result.stream_sizes["expensive"]
        assert sr.contracts == 2

    def test_very_cheap_spread(self):
        """Spread with tiny max_loss yields many contracts.
        $10K, buffer=0, max_weight=1.0 → 10K/25 = 400 contracts.
        """
        cheap = SpreadQuote(
            stream="cheap",
            net_credit=0.05,
            max_loss=25.0,
            underlying_price=28.0,
        )
        sizer = DollarNotionalSizer(margin_buffer_pct=0.0, max_weight_per_stream=1.0)
        result = sizer.size_portfolio(
            account_equity=10_000.0,
            weights={"cheap": 1.0},
            leverage=1.0,
            quotes={"cheap": cheap},
        )
        assert result.stream_sizes["cheap"].contracts == 400


# ═══════════════════════════════════════════════════════════════════════════════
# Sizer configuration validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestSizerValidation:

    def test_invalid_max_leverage(self):
        with pytest.raises(ValueError, match="max_leverage must be positive"):
            DollarNotionalSizer(max_leverage=0.0)

    def test_invalid_negative_leverage(self):
        with pytest.raises(ValueError, match="max_leverage must be positive"):
            DollarNotionalSizer(max_leverage=-1.0)

    def test_invalid_margin_buffer(self):
        with pytest.raises(ValueError, match="margin_buffer_pct"):
            DollarNotionalSizer(margin_buffer_pct=1.0)

    def test_invalid_negative_margin_buffer(self):
        with pytest.raises(ValueError, match="margin_buffer_pct"):
            DollarNotionalSizer(margin_buffer_pct=-0.1)


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience wrapper
# ═══════════════════════════════════════════════════════════════════════════════


class TestConvenienceWrapper:

    def test_size_from_risk_decision(self):
        sizer = DollarNotionalSizer()
        result = size_from_risk_decision(
            sizer=sizer,
            account_equity=100_000.0,
            weights=_equal_weights_6(),
            leverage=1.5,
            quotes=_all_quotes(),
        )
        assert isinstance(result, PortfolioSizingResult)
        assert len(result.stream_sizes) == 6


# ═══════════════════════════════════════════════════════════════════════════════
# Idempotency and determinism
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:

    def test_same_inputs_same_output(self):
        """Sizer must be deterministic — no randomness."""
        sizer = DollarNotionalSizer()
        kwargs = dict(
            account_equity=500_000.0,
            weights={k: v for k, v in _lw_weights().items() if k in _all_quotes()},
            leverage=2.0,
            quotes=_all_quotes(),
        )
        r1 = sizer.size_portfolio(**kwargs)
        r2 = sizer.size_portfolio(**kwargs)
        for stream in r1.stream_sizes:
            assert r1.stream_sizes[stream].contracts == r2.stream_sizes[stream].contracts

    def test_independent_calls(self):
        """Sizer has no hidden state between calls."""
        sizer = DollarNotionalSizer()
        r1 = sizer.size_portfolio(
            account_equity=1_000_000.0,
            weights={"exp1220": 0.30},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        r2 = sizer.size_portfolio(
            account_equity=10_000.0,
            weights={"exp1220": 0.30},
            leverage=1.0,
            quotes={"exp1220": _spy_quote()},
        )
        assert r1.stream_sizes["exp1220"].contracts > r2.stream_sizes["exp1220"].contracts


# ═══════════════════════════════════════════════════════════════════════════════
# Conservative bias verification
# ═══════════════════════════════════════════════════════════════════════════════


class TestConservativeBias:
    """Verify the module consistently errs toward underfilling."""

    def test_margin_consumed_never_exceeds_allocatable(self):
        """Total margin consumed must never exceed allocatable capital."""
        sizer = DollarNotionalSizer()
        for leverage in [1.0, 1.5, 2.0, 3.0]:
            result = sizer.size_portfolio(
                account_equity=250_000.0,
                weights=_equal_weights_6(),
                leverage=leverage,
                quotes=_all_quotes(),
            )
            allocatable = 250_000.0 * min(leverage, 3.0) - 250_000.0 * 0.05
            assert result.total_margin_consumed <= allocatable + 1e-6, (
                f"Margin {result.total_margin_consumed} > allocatable "
                f"{allocatable} at {leverage}×"
            )

    def test_single_stream_conservative_rounding(self):
        """Across a range of dollar amounts, contracts × max_loss is close to target.
        Uses size_single_stream directly (no portfolio weight clamping).

        The round-up threshold (0.95) allows a small overshoot of at most
        (1 - threshold) × max_loss per contract when the fractional part
        is very close to an integer.
        """
        sizer = DollarNotionalSizer()
        quote = _spy_quote()
        max_overshoot = quote.max_loss * (1.0 - sizer.round_up_threshold)
        for target in [1_000, 2_500, 7_300, 10_000, 50_000]:
            contracts, _ = sizer.size_single_stream(float(target), quote)
            cost = contracts * quote.max_loss
            assert cost <= target + max_overshoot + 1e-6, (
                f"target=${target}: {contracts} contracts × "
                f"${quote.max_loss} = ${cost}, max_overshoot=${max_overshoot}"
            )

    def test_near_threshold_conservative(self):
        """Verify behaviour around the round-up boundary.
        $4,740 / $500 = 9.48 → 9 (below threshold).
        $4,975 / $500 = 9.95 → 10 (at threshold).
        Both must satisfy contracts × max_loss ≤ target + (max_loss × 0.05).
        """
        sizer = DollarNotionalSizer()
        quote = _spy_quote()
        # Below threshold
        c1, _ = sizer.size_single_stream(4_740.0, quote)
        assert c1 == 9
        assert c1 * quote.max_loss <= 4_740.0
        # At threshold — round up is allowed, but the overshoot is at most
        # (1 - threshold) × max_loss ≈ $25
        c2, _ = sizer.size_single_stream(4_975.0, quote)
        assert c2 == 10
        overshoot = c2 * quote.max_loss - 4_975.0
        assert overshoot <= quote.max_loss * (1 - sizer.round_up_threshold) + 1e-6
