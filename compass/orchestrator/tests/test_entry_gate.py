"""Tests for compass.orchestrator.entry_gate.

EntryGate is a class (see ORCHESTRATOR_PROPOSAL.md §4.1). The tests
exercise the documented gate semantics: shape of output, fail-closed on
broken portfolio snapshot, terminal BLOCK on action != OPEN, param-drift
BLOCK on egregiously wrong delta/DTE.

These tests deliberately do NOT assert on specific gate-reason strings
(those evolve). They DO assert on the GateStatus enum and on
confidence_adj invariants.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import pytest

from compass.orchestrator.types import GatedSignal, SignalIntent

entry_gate_mod = pytest.importorskip(
    "compass.orchestrator.entry_gate",
    reason="entry_gate module not landed yet (CC1)",
)
EntryGate = entry_gate_mod.EntryGate
MarketContext = entry_gate_mod.MarketContext


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

class _StubPortfolio:
    """Quacks like PortfolioState well enough for entry_gate tests."""

    def __init__(self, open_streams=(), equity=100_000.0, load_ok=True):
        self.equity = equity
        self.load_ok = load_ok
        self.open_streams = frozenset(open_streams)
        self.stream_market_value: Dict[str, float] = {}
        self.stream_weights: Dict[str, float] = {}
        self.errors: List[str] = []

    def has_open_position(self, stream: str) -> bool:
        return stream in self.open_streams

    def weight_of(self, stream: str) -> float:
        return self.stream_weights.get(stream, 0.0)

    def correlation(self, a: str, b: str):
        return None

    def gross_dollar_at_risk(self) -> float:
        return 0.0


def _market(now=None, vix=18.0, vix3m=20.0, market_open=True,
              broker_mode="alpaca_paper") -> MarketContext:
    return MarketContext(
        now_utc=now or datetime(2026, 5, 22, 13, 25, tzinfo=timezone.utc),
        is_market_open=market_open,
        vix=vix,
        vix3m=vix3m,
        broker_mode=broker_mode,
    )


def _intent(stream="exp1220", action="OPEN", **kw) -> SignalIntent:
    base = dict(
        stream=stream, date="2026-05-22", ticker="SPY", action=action,
        direction="put_credit_spread", delta=None, dte=28, width=5.0,
        weight=0.2, confidence=0.9, notes="",
    )
    base.update(kw)
    return SignalIntent.from_dict(base)


# ───────────────────────────────────────────────────────────────────────────
# Shape tests
# ───────────────────────────────────────────────────────────────────────────

class TestEvaluateShape:
    def test_returns_one_gated_per_intent(self):
        intents = [_intent(stream=s) for s in ("exp1220", "qqq_cs", "xlf_cs")]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(), _market(), today=date(2026, 5, 22),
        )
        assert len(out) == 3
        assert all(isinstance(g, GatedSignal) for g in out)

    def test_preserves_intent_order(self):
        intents = [_intent(stream=s) for s in ("exp1220", "qqq_cs", "xlf_cs")]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(), _market(), today=date(2026, 5, 22),
        )
        assert [g.intent.stream for g in out] == ["exp1220", "qqq_cs", "xlf_cs"]

    def test_gate_status_in_valid_set(self):
        intents = [_intent(stream=s) for s in ("exp1220", "qqq_cs")]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(), _market(), today=date(2026, 5, 22),
        )
        for g in out:
            assert g.gate_status in {"ALLOW", "BLOCK", "DEGRADE"}

    def test_empty_input_returns_empty_list(self):
        out = EntryGate().evaluate(
            [], _StubPortfolio(), _market(), today=date(2026, 5, 22),
        )
        assert out == []


# ───────────────────────────────────────────────────────────────────────────
# Specific gates
# ───────────────────────────────────────────────────────────────────────────

class TestSpecificGates:
    def test_already_open_blocks_new_open(self):
        intents = [_intent(stream="exp1220", action="OPEN")]
        pf = _StubPortfolio(open_streams=("exp1220",))
        out = EntryGate().evaluate(intents, pf, _market(), today=date(2026, 5, 22))
        assert out[0].gate_status == "BLOCK"

    def test_action_none_blocks(self):
        intents = [_intent(action="NONE")]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(), _market(), today=date(2026, 5, 22),
        )
        assert out[0].gate_status == "BLOCK"

    def test_block_carries_reason(self):
        intents = [_intent(action="NONE")]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(), _market(), today=date(2026, 5, 22),
        )
        assert out[0].gate_reasons, "BLOCK must populate gate_reasons"

    def test_param_drift_blocks(self):
        """exp1220 canonical is 5%-OTM (delta None); a Δ=0.55 emission is
        outside any reasonable tolerance and must be blocked."""
        intents = [_intent(stream="xlf_cs", action="OPEN", delta=0.55, dte=35,
                            width=2.0)]
        # xlf_cs canonical delta = 0.30; tolerance ±0.05 → 0.55 fails.
        out = EntryGate().evaluate(
            intents, _StubPortfolio(), _market(), today=date(2026, 5, 22),
        )
        assert out[0].gate_status == "BLOCK"

    def test_vix_extreme_blocks(self):
        """vix > vix_block (40) must BLOCK options sleeves."""
        intents = [_intent(stream="exp1220", action="OPEN", delta=None, dte=28)]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(),
            _market(vix=55.0, vix3m=40.0),
            today=date(2026, 5, 22),
        )
        assert out[0].gate_status == "BLOCK"

    def test_market_closed_blocks(self):
        intents = [_intent(stream="exp1220", action="OPEN")]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(),
            _market(market_open=False),
            today=date(2026, 5, 22),
        )
        assert out[0].gate_status == "BLOCK"

    def test_broker_unsupported_blocks(self):
        """gld_cal / slv_cal require futures legs Alpaca paper can't trade."""
        intents = [_intent(stream="gld_cal", action="OPEN", ticker="GLD",
                            direction="calendar", delta=None, dte=None,
                            width=None)]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(),
            _market(broker_mode="alpaca_paper"),
            today=date(2026, 5, 22),
        )
        assert out[0].gate_status == "BLOCK"


# ───────────────────────────────────────────────────────────────────────────
# Fail-closed semantics
# ───────────────────────────────────────────────────────────────────────────

class TestFailClosed:
    def test_portfolio_load_failure_blocks_everything(self):
        intents = [_intent(stream=s) for s in ("exp1220", "qqq_cs")]
        pf = _StubPortfolio(load_ok=False)
        out = EntryGate().evaluate(intents, pf, _market(), today=date(2026, 5, 22))
        assert all(g.gate_status == "BLOCK" for g in out)


# ───────────────────────────────────────────────────────────────────────────
# Degrade composition
# ───────────────────────────────────────────────────────────────────────────

class TestDegradeComposition:
    def test_confidence_adj_one_for_allow(self):
        # exp1220 with canonical params and benign market: should ALLOW.
        intents = [_intent(stream="xlf_cs", action="OPEN", delta=0.30, dte=35,
                            width=2.0)]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(), _market(vix=18.0, vix3m=20.0),
            today=date(2026, 5, 22),
        )
        for g in out:
            if g.gate_status == "ALLOW":
                assert g.confidence_adj == pytest.approx(1.0)

    def test_degrade_adj_strictly_less_than_one(self):
        intents = [_intent(stream=s) for s in ("exp1220", "qqq_cs", "xlf_cs")]
        out = EntryGate().evaluate(
            intents, _StubPortfolio(), _market(), today=date(2026, 5, 22),
        )
        for g in out:
            if g.gate_status == "DEGRADE":
                assert 0.0 < g.confidence_adj < 1.0
                assert g.gate_reasons
