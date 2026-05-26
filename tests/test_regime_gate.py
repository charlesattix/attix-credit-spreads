"""Tests for shared.regime_gate — EXP-3303b Per-Stream Selective Regime Gate."""

from __future__ import annotations

import pytest

from shared.regime_gate import (
    DEFAULT_GATED_REGIMES,
    DEFAULT_SENSITIVE_TICKERS,
    should_gate_for_regime,
)


@pytest.fixture
def enabled_config() -> dict:
    return {
        "risk": {
            "regime_gate": {
                "enabled": True,
                "gated_regimes": ["transition", "high_stress"],
                "sensitive_tickers": ["SPY", "QQQ"],
            }
        }
    }


def test_gate_skips_spy_during_transition(enabled_config):
    skip, reason = should_gate_for_regime(
        regime="transition", ticker="SPY", config=enabled_config
    )
    assert skip is True
    assert "SPY" in reason
    assert "transition" in reason


def test_gate_skips_qqq_during_high_stress(enabled_config):
    skip, _ = should_gate_for_regime(
        regime="high_stress", ticker="QQQ", config=enabled_config
    )
    assert skip is True


def test_gate_passes_xlf_during_transition(enabled_config):
    # Sector ETFs are NOT gated — full size during regime transitions.
    skip, reason = should_gate_for_regime(
        regime="transition", ticker="XLF", config=enabled_config
    )
    assert skip is False
    assert reason == ""


def test_gate_passes_spy_during_bull_regime(enabled_config):
    # Normal regimes do NOT trigger the gate.
    skip, _ = should_gate_for_regime(
        regime="bull", ticker="SPY", config=enabled_config
    )
    assert skip is False


def test_gate_disabled_passes_all(enabled_config):
    disabled = {"risk": {"regime_gate": {"enabled": False}}}
    skip, _ = should_gate_for_regime(
        regime="transition", ticker="SPY", config=disabled
    )
    assert skip is False


def test_gate_default_disabled():
    # No config at all → gate is implicitly disabled.
    skip, _ = should_gate_for_regime(regime="transition", ticker="SPY")
    assert skip is False


def test_ticker_match_is_case_insensitive(enabled_config):
    skip, _ = should_gate_for_regime(
        regime="transition", ticker="spy", config=enabled_config
    )
    assert skip is True


def test_explicit_overrides_take_precedence(enabled_config):
    # Even with enabled config, kwargs override.
    skip, _ = should_gate_for_regime(
        regime="bull",
        ticker="SPY",
        config=enabled_config,
        gated_regimes=["bull"],  # gate bull regime explicitly
    )
    assert skip is True


def test_none_regime_does_not_gate(enabled_config):
    # regime=None (regime detection failed) does NOT trigger the gate.
    skip, _ = should_gate_for_regime(
        regime=None, ticker="SPY", config=enabled_config
    )
    assert skip is False


def test_custom_sensitive_tickers(enabled_config):
    # Adding IWM to the sensitive list should gate it.
    skip, _ = should_gate_for_regime(
        regime="transition",
        ticker="IWM",
        config=enabled_config,
        sensitive_tickers=["IWM"],
    )
    assert skip is True


def test_defaults_match_instruction_file():
    # Instruction file specifies SPX-sensitive streams and regime transitions.
    assert "transition" in DEFAULT_GATED_REGIMES
    assert "high_stress" in DEFAULT_GATED_REGIMES
    assert "SPY" in DEFAULT_SENSITIVE_TICKERS
    assert "QQQ" in DEFAULT_SENSITIVE_TICKERS
