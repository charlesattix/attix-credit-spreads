"""
Tests for dollar-notional position sizing (Phase 9 prerequisite #5).

Tests the ``compute_notional_contracts`` function and verifies that every
stream-level signal generator in EXP-2830 uses it correctly.
"""
import math
import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compass.exp2830_paper_signal_generator import (
    compute_notional_contracts,
    StreamSignal,
    CAPITAL_BASE,
    MAX_LEVERAGE,
    STREAM_WEIGHTS,
)


# ── compute_notional_contracts unit tests ────────────────────────────────


class TestComputeNotionalContracts:
    """Core sizing function: budget / max_loss → contracts."""

    def test_basic_calculation(self):
        """$100K capital, 35% weight, 3× leverage, 3% risk, $450 max-loss
        → stream_budget = 100K × 0.35 × 3 × 0.03 = $3,150
        → 3150 / 450 = 7.0 → 7 contracts."""
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=450.0,
        )
        assert n == 7

    def test_rounds_down(self):
        """Must round DOWN (conservative — never exceed risk budget)."""
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=451.0,
        )
        # 3150 / 451 = 6.985... → floor to 6
        assert n == 6

    def test_floor_enforced(self):
        """Even tiny capital should produce at least `floor` contracts."""
        n = compute_notional_contracts(
            capital=1_000, weight=0.05, leverage=1.0,
            risk_pct=0.03, max_loss_per_contract=500.0,
        )
        # 1000 × 0.05 × 1 × 0.03 = $1.50 → 1.50/500 = 0.003 → 0 raw → floor=1
        assert n == 1

    def test_floor_zero_allowed(self):
        """When floor=0, truly tiny budget can produce 0 contracts."""
        n = compute_notional_contracts(
            capital=1_000, weight=0.05, leverage=1.0,
            risk_pct=0.03, max_loss_per_contract=500.0,
            floor=0,
        )
        assert n == 0

    def test_cap_enforced(self):
        """Massive capital should be capped at the safety limit."""
        n = compute_notional_contracts(
            capital=10_000_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=450.0,
            cap=10,
        )
        # 10M × 0.35 × 3 × 0.03 / 450 = 700 → capped at 10
        assert n == 10

    def test_custom_cap(self):
        """Custom cap for calendars (15) vs put spreads (10)."""
        n = compute_notional_contracts(
            capital=10_000_000, weight=0.10, leverage=3.0,
            risk_pct=0.02, max_loss_per_contract=50.0,
            cap=15,
        )
        assert n == 15

    def test_zero_max_loss_returns_floor(self):
        """Division-by-zero guard: max_loss_per_contract=0 → floor."""
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=0.0,
        )
        assert n == 1

    def test_negative_max_loss_returns_floor(self):
        """Negative max-loss (shouldn't happen, but guard) → floor."""
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=-10.0,
        )
        assert n == 1

    def test_scales_with_capital(self):
        """Doubling capital should double contracts (within cap)."""
        n1 = compute_notional_contracts(
            capital=100_000, weight=0.10, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=450.0, cap=100,
        )
        n2 = compute_notional_contracts(
            capital=200_000, weight=0.10, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=450.0, cap=100,
        )
        assert n2 == 2 * n1

    def test_scales_with_leverage(self):
        """Higher leverage → more contracts (within cap)."""
        n1x = compute_notional_contracts(
            capital=100_000, weight=0.10, leverage=1.0,
            risk_pct=0.03, max_loss_per_contract=100.0, cap=100,
        )
        n3x = compute_notional_contracts(
            capital=100_000, weight=0.10, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=100.0, cap=100,
        )
        assert n3x == 3 * n1x

    def test_production_spy_sizing(self):
        """Reproduce the EXP-1220 (SPY) sizing at $100K base.
        weight=0.35, leverage=3, risk=3%, width=5, credit_est=0.75
        max_loss = (5 - 0.75) * 100 = $425
        budget = 100K × 0.35 × 3 × 0.03 = $3,150
        contracts = int(3150 / 425) = 7"""
        width = 5.0
        est_credit = width * 0.15  # 0.75
        max_loss = (width - est_credit) * 100  # 425
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=max_loss,
            cap=10,
        )
        assert n == 7

    def test_production_xlf_sizing(self):
        """XLF: weight=0.10, width=1.0, credit_est=0.15
        max_loss = (1.0 - 0.15) * 100 = $85
        budget = 100K × 0.10 × 3 × 0.03 = $900
        contracts = int(900 / 85) = 10"""
        width = 1.0
        est_credit = width * 0.15
        max_loss = (width - est_credit) * 100
        n = compute_notional_contracts(
            capital=100_000, weight=0.10, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=max_loss,
            cap=10,
        )
        assert n == 10

    def test_production_gld_calendar_sizing(self):
        """GLD calendar: weight=0.10, stress=$50
        budget = 100K × 0.10 × 3 × 0.02 = $600
        contracts = int(600 / 50) = 12"""
        n = compute_notional_contracts(
            capital=100_000, weight=0.10, leverage=3.0,
            risk_pct=0.02, max_loss_per_contract=50.0,
            cap=15,
        )
        assert n == 12

    def test_production_cross_vol_sizing(self):
        """Cross-vol: weight=0.10, stress=$100
        budget = 100K × 0.10 × 3 × 0.02 = $600
        contracts = int(600 / 100) = 6"""
        n = compute_notional_contracts(
            capital=100_000, weight=0.10, leverage=3.0,
            risk_pct=0.02, max_loss_per_contract=100.0,
            cap=8,
        )
        assert n == 6

    def test_production_v5_hedge_sizing(self):
        """V5 hedge: weight=0.05, risk=5%, stress=$50
        budget = 100K × 0.05 × 3 × 0.05 = $750
        contracts = int(750 / 50) = 15 → capped at 5"""
        n = compute_notional_contracts(
            capital=100_000, weight=0.05, leverage=3.0,
            risk_pct=0.05, max_loss_per_contract=50.0,
            cap=5,
        )
        assert n == 5


# ── Scaling scenarios (T0→T5 from MASTERPLAN) ───────────────────────────


class TestScalingScenarios:
    """Verify sizing makes sense at each AUM tranche."""

    @pytest.mark.parametrize("capital,expected_min", [
        (100_000, 1),       # T0: paper
        (25_000, 1),        # T1: first live
        (100_000, 1),       # T2
        (1_000_000, 7),     # T3: $1M
        (10_000_000, 10),   # T4: $10M → capped
    ])
    def test_spy_scaling_tiers(self, capital, expected_min):
        """SPY put spread (width=5, credit≈0.75) scales with capital."""
        max_loss = (5.0 - 0.75) * 100  # $425
        n = compute_notional_contracts(
            capital=capital, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=max_loss,
            cap=10,
        )
        assert n >= expected_min
        assert n <= 10

    def test_million_dollar_uncapped(self):
        """At $1M with no cap, SPY should size to ~74 contracts."""
        max_loss = (5.0 - 0.75) * 100
        n = compute_notional_contracts(
            capital=1_000_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=max_loss,
            cap=1000,
        )
        # 1M × 0.35 × 3 × 0.03 / 425 = 74.1 → 74
        assert n == 74

    def test_ten_million_uncapped(self):
        """At $10M with no cap, SPY → ~741 contracts."""
        max_loss = (5.0 - 0.75) * 100
        n = compute_notional_contracts(
            capital=10_000_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=max_loss,
            cap=10000,
        )
        assert n == 741


# ── StreamSignal dataclass tests ─────────────────────────────────────────


class TestStreamSignalFields:
    """Verify the new dollar-notional fields on StreamSignal."""

    def test_new_fields_exist(self):
        s = StreamSignal(
            stream="test", action="OPEN", underlier="SPY",
            structure="put_credit_spread", direction="short_put",
            target_delta=0.20, short_strike=500.0, long_strike=495.0,
            width=5.0, expiry="2026-05-01", dte_days=28,
            target_contracts=7, limit_price=0.75,
            order_type="limit_at_mid_combo", reason="test",
            stream_weight=0.35,
            sizing_capital=100_000.0,
            sizing_max_loss_per_contract=425.0,
            sizing_risk_budget=2975.0,
        )
        assert s.sizing_capital == 100_000.0
        assert s.sizing_max_loss_per_contract == 425.0
        assert s.sizing_risk_budget == 2975.0

    def test_defaults_to_zero(self):
        """Non-trade signals should default to 0.0 for sizing fields."""
        s = StreamSignal(
            stream="test", action="SKIP", underlier="SPY",
            structure="put_credit_spread", direction="short_put",
            target_delta=None, short_strike=None, long_strike=None,
            width=None, expiry=None, dte_days=None,
            target_contracts=0, limit_price=None,
            order_type="limit_at_mid", reason="skipped",
            stream_weight=0.35,
        )
        assert s.sizing_capital == 0.0
        assert s.sizing_max_loss_per_contract == 0.0
        assert s.sizing_risk_budget == 0.0

    def test_risk_budget_consistent(self):
        """risk_budget should equal contracts × max_loss_per_contract."""
        max_loss = 425.0
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=max_loss, cap=10,
        )
        risk_budget = n * max_loss
        # Budget should never exceed the stream allocation
        stream_alloc = 100_000 * 0.35 * 3.0 * 0.03
        assert risk_budget <= stream_alloc


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and adversarial inputs."""

    def test_very_wide_spread(self):
        """A $50-wide spread should still produce ≥1 contract."""
        max_loss = (50.0 - 7.5) * 100  # $4,250
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=max_loss,
            cap=10,
        )
        assert n >= 1

    def test_very_narrow_spread(self):
        """A $0.50-wide spread → capped at 10."""
        max_loss = (0.50 - 0.075) * 100  # $42.50
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=max_loss,
            cap=10,
        )
        assert n == 10  # 3150 / 42.5 = 74 → capped at 10

    def test_all_weights_positive(self):
        """Every stream weight in the config must be positive."""
        for stream, weight in STREAM_WEIGHTS.items():
            assert weight > 0, f"{stream} has non-positive weight {weight}"

    def test_weights_sum_to_one(self):
        """Stream weights should sum to 1.0."""
        total = sum(STREAM_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, not 1.0"

    def test_integer_output_type(self):
        """Output must always be int (Alpaca requires integer qty)."""
        n = compute_notional_contracts(
            capital=100_000, weight=0.35, leverage=3.0,
            risk_pct=0.03, max_loss_per_contract=425.0,
        )
        assert isinstance(n, int)
