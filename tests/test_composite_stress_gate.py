"""Tests for shared/composite_stress_gate.py."""
from __future__ import annotations

import pytest

from shared.composite_stress_gate import (
    DEFAULT_SENSITIVE_TICKERS,
    DEFAULT_THETA,
    should_gate_for_composite_stress,
)


class TestGateBehavior:
    def test_disabled_by_default(self):
        skip, reason = should_gate_for_composite_stress("SPY", config={})
        assert skip is False
        assert reason == ""

    def test_fires_when_stress_above_theta(self):
        skip, reason = should_gate_for_composite_stress(
            "SPY",
            config={"risk": {"composite_stress_gate": {"enabled": True}}},
            stress_value=3.0,
        )
        assert skip is True
        assert "SPY" in reason
        assert "stress=3.000" in reason

    def test_does_not_fire_when_stress_below_theta(self):
        skip, reason = should_gate_for_composite_stress(
            "SPY",
            config={"risk": {"composite_stress_gate": {"enabled": True}}},
            stress_value=1.0,
        )
        assert skip is False
        assert reason == ""

    def test_does_not_fire_for_insensitive_ticker(self):
        # XLF not in sensitive list -> never gated regardless of stress.
        skip, reason = should_gate_for_composite_stress(
            "XLF",
            config={"risk": {"composite_stress_gate": {"enabled": True}}},
            stress_value=10.0,
        )
        assert skip is False

    def test_none_stress_does_not_fire_rule_zero(self):
        """When live composite is unavailable, do NOT gate (fail open;
        label-based regime_gate remains the safety net)."""
        skip, reason = should_gate_for_composite_stress(
            "SPY",
            config={"risk": {"composite_stress_gate": {"enabled": True}}},
            stress_value=None,
        )
        assert skip is False
        assert reason == ""

    def test_custom_theta_via_config(self):
        # theta=1.0 → stress=1.5 should fire even though default is 2.5.
        skip, _ = should_gate_for_composite_stress(
            "SPY",
            config={"risk": {"composite_stress_gate": {"enabled": True, "theta": 1.0}}},
            stress_value=1.5,
        )
        assert skip is True

    def test_custom_theta_via_kwarg(self):
        skip, _ = should_gate_for_composite_stress(
            "SPY",
            config={"risk": {"composite_stress_gate": {"enabled": True}}},
            theta=5.0,
            stress_value=3.0,
        )
        assert skip is False  # 3.0 < 5.0

    def test_custom_sensitive_tickers(self):
        skip, _ = should_gate_for_composite_stress(
            "XLF",
            config={"risk": {"composite_stress_gate": {"enabled": True}}},
            sensitive_tickers=["XLF", "XLI"],
            stress_value=3.0,
        )
        assert skip is True

    def test_ticker_case_insensitive(self):
        skip, _ = should_gate_for_composite_stress(
            "spy",
            config={"risk": {"composite_stress_gate": {"enabled": True}}},
            stress_value=3.0,
        )
        assert skip is True

    def test_threshold_exactly_at_theta_fires(self):
        skip, _ = should_gate_for_composite_stress(
            "SPY",
            enabled=True,
            stress_value=DEFAULT_THETA,
        )
        assert skip is True

    def test_consults_live_calculator_when_stress_not_provided(self, monkeypatch):
        # Stub out the live module so we don't hit Polygon.
        import shared.composite_stress_gate as gate_module
        from compass import live_composite_stress

        monkeypatch.setattr(
            live_composite_stress, "get_current_composite_stress", lambda: 4.2
        )
        skip, reason = should_gate_for_composite_stress(
            "SPY",
            enabled=True,
            stress_value=None,  # forces live lookup
            sensitive_tickers=DEFAULT_SENSITIVE_TICKERS,
        )
        # 4.2 > 2.5 default theta -> fire
        assert skip is True
        assert "4.200" in reason
