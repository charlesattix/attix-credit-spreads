"""Smoke tests for the cross_vol signal pipeline (EXP-2020 → EXP-2690).

Validates:
  1. exp2690 cross_vol_signals returns valid schema
  2. exp2020 generate_today_signals delegates to exp2690 (not duplicated)
  3. Monday-only cadence enforced
  4. Backward-compatible vol_arb_signals alias works
  5. GENERATOR_REGISTRY has cross_vol key

These tests use real Yahoo/VIX data where available and gracefully
handle BLOCKED signals when data is missing (CI environments).
"""
from __future__ import annotations

import pytest
from datetime import datetime


# ── Fixtures ─────────────────────────────────────────────────────────────

MONDAY = datetime(2024, 1, 15)    # known Monday
TUESDAY = datetime(2024, 1, 16)   # known Tuesday

REQUIRED_FIELDS = {
    "stream", "date", "ticker", "action", "direction",
    "delta", "dte", "width", "weight", "confidence", "notes",
}

VALID_ACTIONS = {"OPEN", "NONE", "BLOCKED", "HOLD", "ERROR"}


# ── Tests ────────────────────────────────────────────────────────────────

class TestCrossVolSignalSchema:
    """exp2690 cross_vol_signals returns well-formed signal dicts."""

    def test_returns_list(self):
        from compass.exp2690_signal_generators import cross_vol_signals
        result = cross_vol_signals(MONDAY)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_required_fields_present(self):
        from compass.exp2690_signal_generators import cross_vol_signals
        for sig in cross_vol_signals(MONDAY):
            missing = REQUIRED_FIELDS - set(sig.keys())
            assert not missing, f"missing fields: {missing}"

    def test_stream_is_cross_vol(self):
        from compass.exp2690_signal_generators import cross_vol_signals
        for sig in cross_vol_signals(MONDAY):
            assert sig["stream"] == "cross_vol"

    def test_action_is_valid(self):
        from compass.exp2690_signal_generators import cross_vol_signals
        for sig in cross_vol_signals(MONDAY):
            assert sig["action"] in VALID_ACTIONS

    def test_date_matches_input(self):
        from compass.exp2690_signal_generators import cross_vol_signals
        for sig in cross_vol_signals(MONDAY):
            assert sig["date"] == "2024-01-15"

    def test_open_signal_has_legs(self):
        from compass.exp2690_signal_generators import cross_vol_signals
        for sig in cross_vol_signals(MONDAY):
            if sig["action"] == "OPEN":
                assert isinstance(sig.get("legs"), list)
                assert len(sig["legs"]) == 2
                sides = {leg["side"] for leg in sig["legs"]}
                assert "long_straddle" in sides
                assert "short_straddle" in sides


class TestCrossVolCadence:
    """Cross-vol signals only fire on Mondays."""

    def test_tuesday_returns_none_action(self):
        from compass.exp2690_signal_generators import cross_vol_signals
        sigs = cross_vol_signals(TUESDAY)
        assert len(sigs) == 1
        assert sigs[0]["action"] == "NONE"
        assert "not a Monday" in sigs[0]["notes"]

    def test_monday_is_not_none(self):
        from compass.exp2690_signal_generators import cross_vol_signals
        sigs = cross_vol_signals(MONDAY)
        # On Monday we expect OPEN or BLOCKED (data-dependent), never NONE
        assert sigs[0]["action"] != "NONE"


class TestExp2020Delegation:
    """exp2020.generate_today_signals is a thin wrapper, not a reimplementation."""

    def test_delegates_to_exp2690(self):
        from compass.exp2020_cross_vol_arb import generate_today_signals
        from compass.exp2690_signal_generators import cross_vol_signals
        assert generate_today_signals(MONDAY) == cross_vol_signals(MONDAY)


class TestRegistryIntegrity:
    """GENERATOR_REGISTRY has the correct cross_vol entry."""

    def test_cross_vol_in_registry(self):
        from compass.exp2690_signal_generators import GENERATOR_REGISTRY
        assert "cross_vol" in GENERATOR_REGISTRY

    def test_vol_arb_not_in_registry(self):
        from compass.exp2690_signal_generators import GENERATOR_REGISTRY
        assert "vol_arb" not in GENERATOR_REGISTRY

    def test_backward_compat_alias(self):
        from compass.exp2690_signal_generators import (
            cross_vol_signals, vol_arb_signals,
        )
        assert vol_arb_signals is cross_vol_signals

    def test_registry_has_all_8_streams(self):
        from compass.exp2690_signal_generators import GENERATOR_REGISTRY
        expected = {
            "exp1220", "xlf_cs", "xli_cs", "qqq_cs",
            "gld_cal", "slv_cal", "cross_vol", "v5_hedge",
        }
        assert set(GENERATOR_REGISTRY.keys()) == expected
