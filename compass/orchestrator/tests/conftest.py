"""Test fixtures shared by every orchestrator test file.

Provides:
    sample_intent_dicts       — raw EXP-2690-style dicts (one per stream)
    sample_signal_intents     — typed SignalIntent list
    sample_gated_signals      — typed GatedSignal list (ALLOW/BLOCK/DEGRADE mix)
    sample_sized_orders       — typed SizedOrder list
    fake_connector            — minimal AlpacaConnector stand-in
    audit_dir                 — tmp_path-based audit root
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Optional

import pandas as pd
import pytest

from compass.orchestrator.types import (
    GatedSignal,
    SignalIntent,
    SizedOrder,
)


@pytest.fixture
def sample_intent_dicts() -> List[Dict]:
    """Eight raw signals, one per EXP-2690 stream."""
    return [
        {"stream": "exp1220", "date": "2026-05-22", "ticker": "SPY",
         "action": "OPEN", "direction": "put_credit_spread",
         "delta": 0.18, "dte": 28, "width": 5.0,
         "weight": 0.20, "confidence": 0.9, "notes": "5%-OTM"},
        {"stream": "xlf_cs", "date": "2026-05-22", "ticker": "XLF",
         "action": "OPEN", "direction": "put_credit_spread",
         "delta": 0.30, "dte": 30, "width": 2.0,
         "weight": 0.10, "confidence": 0.8, "notes": ""},
        {"stream": "xli_cs", "date": "2026-05-22", "ticker": "XLI",
         "action": "OPEN", "direction": "put_credit_spread",
         "delta": 0.30, "dte": 30, "width": 2.0,
         "weight": 0.10, "confidence": 0.8, "notes": ""},
        {"stream": "qqq_cs", "date": "2026-05-22", "ticker": "QQQ",
         "action": "OPEN", "direction": "put_credit_spread",
         "delta": 0.20, "dte": 28, "width": 5.0,
         "weight": 0.15, "confidence": 0.85, "notes": ""},
        {"stream": "gld_cal", "date": "2026-05-22", "ticker": "GLD",
         "action": "HOLD", "direction": "calendar",
         "delta": None, "dte": None, "width": None,
         "weight": 0.05, "confidence": 0.5, "notes": "no signal"},
        {"stream": "slv_cal", "date": "2026-05-22", "ticker": "SLV",
         "action": "NONE", "direction": None,
         "delta": None, "dte": None, "width": None,
         "weight": 0.05, "confidence": 0.0, "notes": ""},
        {"stream": "cross_vol", "date": "2026-05-22", "ticker": "SPY",
         "action": "OPEN", "direction": "put_credit_spread",
         "delta": 0.20, "dte": 21, "width": 5.0,
         "weight": 0.15, "confidence": 0.6, "notes": "IV-RV"},
        {"stream": "v5_hedge", "date": "2026-05-22", "ticker": "GLD",
         "action": "OPEN", "direction": "long",
         "delta": None, "dte": None, "width": None,
         "weight": 0.20, "confidence": 0.7, "notes": "trend"},
    ]


@pytest.fixture
def sample_signal_intents(sample_intent_dicts) -> List[SignalIntent]:
    return [SignalIntent.from_dict(d) for d in sample_intent_dicts]


@pytest.fixture
def sample_gated_signals(sample_signal_intents) -> List[GatedSignal]:
    """ALLOW the OPEN signals, BLOCK the NONE, DEGRADE one with low conf."""
    out: List[GatedSignal] = []
    for intent in sample_signal_intents:
        if intent.action == "NONE":
            out.append(GatedSignal(intent=intent, gate_status="BLOCK",
                                     gate_reasons=["action=NONE"],
                                     confidence_adj=1.0))
        elif intent.stream == "cross_vol":
            out.append(GatedSignal(intent=intent, gate_status="DEGRADE",
                                     gate_reasons=["IV proxy"],
                                     confidence_adj=0.5))
        elif intent.action == "HOLD":
            out.append(GatedSignal(intent=intent, gate_status="BLOCK",
                                     gate_reasons=["action=HOLD"],
                                     confidence_adj=1.0))
        else:
            out.append(GatedSignal(intent=intent, gate_status="ALLOW",
                                     gate_reasons=[],
                                     confidence_adj=1.0))
    return out


@pytest.fixture
def sample_sized_orders(sample_gated_signals) -> List[SizedOrder]:
    out: List[SizedOrder] = []
    for g in sample_gated_signals:
        if g.gate_status not in ("ALLOW", "DEGRADE"):
            continue
        out.append(SizedOrder(
            gated=g,
            contract_count=2,
            risk_allocation=1000.0,
            short_strike=420.0,
            long_strike=415.0,
            expected_credit=0.55,
            max_loss_dollars=445.0,
            expiration="2026-06-19",
            port_weight_consumed=0.05,
            sizing_reasons=["per-trade=$1000"],
        ))
    return out


class _FakeSnapshot:
    """Mimics AccountSnapshot for PortfolioState.load."""
    def __init__(self, equity: float = 100_000.0, raw_error: Optional[str] = None):
        self.timestamp = "2026-05-22T13:25:00+00:00"
        self.equity = equity
        self.cash = equity * 0.5
        self.buying_power = equity
        self.portfolio_value = equity
        self.positions: List = []
        self.pending_orders: List = []
        self.raw_error = raw_error


class FakeConnector:
    """Minimal AlpacaConnector substitute.

    Records every method invocation in `calls` so tests can assert on
    the interaction pattern (e.g. cleanup must call cancel_all once).
    """

    def __init__(self,
                 equity: float = 100_000.0,
                 snapshot_error: Optional[str] = None,
                 reconcile_diff: Optional[Dict[str, Dict]] = None,
                 cancel_count: int = 0):
        self._equity = equity
        self._snapshot_error = snapshot_error
        self._reconcile_diff = reconcile_diff or {}
        self._cancel_count = cancel_count
        self.calls: List[tuple] = []

    def snapshot(self):
        self.calls.append(("snapshot",))
        return _FakeSnapshot(equity=self._equity, raw_error=self._snapshot_error)

    def cancel_all(self) -> int:
        self.calls.append(("cancel_all",))
        return self._cancel_count

    def reconcile(self, intended, tolerance: float = 0.0):
        self.calls.append(("reconcile", intended, tolerance))
        return dict(self._reconcile_diff)


@pytest.fixture
def fake_connector() -> FakeConnector:
    return FakeConnector()


@pytest.fixture
def audit_dir(tmp_path):
    """An audit root the pipeline can write into. Returns the str path
    so it can be passed straight to ``run()``."""
    p = tmp_path / "orchestrator"
    p.mkdir()
    return str(p)
