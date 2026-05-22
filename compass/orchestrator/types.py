"""compass/orchestrator/types.py — Data contracts for the orchestrator pipeline.

The orchestrator has three stages, each emitting a NEW immutable dataclass
that is a strict superset of its predecessor's fields. Nothing mutates the
upstream object; this makes every stage diffable, testable, and replayable
from the JSONL audit logs.

    SignalIntent           (from EXP-2690, unchanged)
       │
       ▼  entry_gate.evaluate()
    GatedSignal            (+ gate_status, gate_reasons)
       │
       ▼  position_sizer.size()
    SizedOrder             (+ contract_count, risk_allocation, strikes…)
       │
       ▼  order_router.submit()
    SpreadOrder            (defined in compass.alpaca_connector — re-used)

PipelineResult is the run-level summary written at the end of each daily run.

See ORCHESTRATOR_PROPOSAL.md §3.2 + Appendix A for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


# ───────────────────────────────────────────────────────────────────────────
# Status literals
# ───────────────────────────────────────────────────────────────────────────

GateStatus = Literal["ALLOW", "BLOCK", "DEGRADE"]
"""Outcome of entry_gate evaluation for a single intent."""

IntentAction = Literal["OPEN", "HOLD", "BLOCKED", "NONE", "ERROR", "CLOSE"]
"""Action emitted by EXP-2690 signal generators."""

PipelineStatus = Literal["OK", "PARTIAL", "FAILED"]
"""Run-level outcome reported in PipelineResult."""


# ───────────────────────────────────────────────────────────────────────────
# SignalIntent — EXP-2690 schema, frozen
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalIntent:
    """Raw intent emitted by a single EXP-2690 signal generator for one stream.

    Field names mirror the dict schema in
    compass.exp2690_signal_generators (lines 18-32 of that module). The
    orchestrator wraps each dict into this dataclass and never mutates it.
    """

    stream: str                          # sleeve id, e.g. "exp1220", "qqq_cs"
    date: str                            # ISO date YYYY-MM-DD
    ticker: str                          # primary underlier
    action: IntentAction                 # OPEN / HOLD / BLOCKED / NONE / ERROR
    direction: Optional[str]             # put_credit_spread / calendar / long / ...
    delta: Optional[float]               # target short delta (options) or None
    dte: Optional[int]                   # target days-to-expiration or None
    width: Optional[float]               # spread width (options) or None
    weight: float                        # sleeve's portfolio weight (EXP-2600)
    confidence: float                    # [0, 1] regime + overlay adjustment
    notes: str                           # human-readable reason
    legs: Optional[List[Dict]] = None    # optional multi-leg detail

    @classmethod
    def from_dict(cls, d: Dict) -> "SignalIntent":
        """Build from the raw dict that EXP-2690 emits.

        Unknown keys are dropped; missing optional keys default to None.
        """
        return cls(
            stream=d["stream"],
            date=d["date"],
            ticker=d["ticker"],
            action=d.get("action", "NONE"),
            direction=d.get("direction"),
            delta=d.get("delta"),
            dte=d.get("dte"),
            width=d.get("width"),
            weight=float(d.get("weight", 0.0)),
            confidence=float(d.get("confidence", 1.0)),
            notes=d.get("notes", ""),
            legs=d.get("legs"),
        )


# ───────────────────────────────────────────────────────────────────────────
# GatedSignal — post entry_gate
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GatedSignal:
    """A SignalIntent with the entry_gate verdict attached.

    - gate_status:    ALLOW   — proceed to sizing
                      BLOCK   — drop; do not size or submit
                      DEGRADE — proceed with confidence_adj < 1.0
    - gate_reasons:   ordered list of human-readable strings; one per
                      gate that contributed to the verdict (empty for ALLOW
                      with no degrades applied).
    - confidence_adj: multiplier applied by DEGRADE gates (cumulative
                      product). 1.0 means no degradation.
    """

    intent: SignalIntent
    gate_status: GateStatus
    gate_reasons: List[str] = field(default_factory=list)
    confidence_adj: float = 1.0

    @property
    def effective_confidence(self) -> float:
        """Intent confidence after DEGRADE multipliers."""
        return self.intent.confidence * self.confidence_adj


# ───────────────────────────────────────────────────────────────────────────
# SizedOrder — post position_sizer
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SizedOrder:
    """A GatedSignal turned into a concrete, broker-ready spread sketch.

    Strike + credit are resolved against the live option chain; contract
    count + risk allocation respect per-sleeve risk-per-trade, the
    portfolio gross cap, and any correlation haircut.

    The next stage (order_router) turns this into a SpreadOrder with OCC
    symbols and a client_order_id; nothing about strike selection happens
    downstream of here.
    """

    gated: GatedSignal
    contract_count: int                  # final number of spreads to submit
    risk_allocation: float               # dollar at-risk for this order ($)
    short_strike: float
    long_strike: float
    expected_credit: float               # per-contract, in dollars
    max_loss_dollars: float              # per-contract worst-case loss
    expiration: str                      # ISO date YYYY-MM-DD
    port_weight_consumed: float = 0.0    # fraction of portfolio gross cap used
    sizing_reasons: List[str] = field(default_factory=list)

    @property
    def total_credit_dollars(self) -> float:
        """Gross credit collected across all contracts (per-contract × N × 100)."""
        return self.expected_credit * self.contract_count * 100.0

    @property
    def total_max_loss_dollars(self) -> float:
        """Total worst-case loss across all contracts."""
        return self.max_loss_dollars * self.contract_count * 100.0


# ───────────────────────────────────────────────────────────────────────────
# PipelineResult — run-level summary
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PipelineResult:
    """Summary of a single orchestrator run, written at the end of each cron.

    Counts are reported per stage so an operator can see at a glance:
    "9 intents in → 6 ALLOW + 2 DEGRADE + 1 BLOCK → 7 sized → 6 submitted →
    5 filled".

    `errors` collects hard failures (auth, broker outage, schema mismatch);
    `warnings` collects soft issues that did not abort the run.
    """

    run_id: str                          # e.g. "2026-05-22T13:25:00Z"
    date: str                            # trading date (ISO)
    mode: str                            # paper / live / dry-run / replay / cleanup / reconcile
    status: PipelineStatus
    n_intents: int
    n_allow: int
    n_block: int
    n_degrade: int
    n_sized: int                         # SizedOrders produced (≤ n_allow + n_degrade)
    n_submitted: int                     # orders that reached the broker
    n_filled: int                        # orders confirmed filled
    n_rejected: int                      # orders the broker refused
    gross_risk_dollars: float            # sum of SizedOrder.risk_allocation
    portfolio_equity: float              # equity snapshot at run start
    duration_seconds: float
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    per_stream_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # ^ {stream: {"intents": n, "allow": n, "block": n, "degrade": n,
    #             "sized": n, "submitted": n, "filled": n}}

    @property
    def ok(self) -> bool:
        return self.status == "OK"
