"""compass.orchestrator — live-trading orchestrator (entry_gate → position_sizer → order_router).

See ORCHESTRATOR_PROPOSAL.md for the full design. This package is the
deliberate seam between EXP-2690 signal generators (intent) and the
Alpaca broker (execution).
"""

from compass.orchestrator.types import (
    GateStatus,
    SignalIntent,
    GatedSignal,
    SizedOrder,
    PipelineResult,
)

__all__ = [
    "GateStatus",
    "SignalIntent",
    "GatedSignal",
    "SizedOrder",
    "PipelineResult",
]
