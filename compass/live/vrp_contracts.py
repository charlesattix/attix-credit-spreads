"""compass/live/vrp_contracts.py — PR-B interface contracts for the VRP engine.

This is the stable seam between the VRP strategy engine (PR-B, cc1) and its
dependencies:

  * PR-A data feed (cc2)      → produces ``compass.live.vrp_data.VRPSnapshot``.
  * PR-C LW allocator (cc3)   → ``compass.live.vrp_risk_parity.compute_weights``.
  * PR-D VIX ladder (cc4)     → implements :class:`VixExposureProvider`.
  * PR-I per-stream returns    → implements :class:`ReturnsProvider`.
  * order placement (PR-E)     → implements :class:`OrderSink` (Alpaca adapter).

The engine emits :class:`OrderIntent` objects — a broker-agnostic description of
what to trade. Nothing here places a live order; an :class:`OrderSink` does that
(a recording fake in tests, the Alpaca adapter at the PR-E cutover). This keeps
``signal → sizing → order intent`` fully testable without Alpaca.

ADDITIVE: imported only by the other ``compass.live.vrp_*`` modules. No existing
experiment, ``main.py``, or config imports this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Mapping, Optional, Protocol, Tuple, runtime_checkable

import pandas as pd

from compass.live.vrp_data import VRPSnapshot

# ── Canonical stream universe ─────────────────────────────────────────────────
# Order MUST match compass/exp2850 build_v8a_cube and cc3's vrp_risk_parity
# VRP_STREAMS so weight vectors and the covariance align positionally.
VRP_STREAMS: Tuple[str, ...] = (
    "exp1220", "v5_hedge", "gld_cal", "slv_cal",
    "cross_vol", "xlf_cs", "xli_cs", "qqq_cs",
)


class StreamStatus(str, Enum):
    """Live execution status of a VRP stream on the current broker (Alpaca)."""

    TRADEABLE = "tradeable"   # full live entry engine exists (PR-B credit spreads)
    DEFERRED = "deferred"     # signal/structure not yet ported (build-plan PR-D)
    BLOCKED = "blocked"       # no Alpaca execution path at all (futures basis)


@dataclass(frozen=True)
class StreamSpec:
    """Static description of a VRP stream and its live execution status."""

    stream_id: str
    symbols: Tuple[str, ...]
    structure: str            # "bull_put" | "long_short_shares" | "rel_value_vol" | "etf_future_basis"
    status: StreamStatus
    owner: str                # which PR owns the live engine for this stream
    note: str = ""


#: The 8 streams of the VRP cube, classified by live tradeability (recon cc2/§2).
#: - 4 credit-spread streams: TRADEABLE here in PR-B.
#: - cross_vol / v5_hedge: DEFERRED (signal port is build-plan PR-D scope).
#: - gld_cal / slv_cal: BLOCKED — they are ETF-vs-front-month-FUTURES basis trades
#:   (NOT options calendars; recon cc2 §5), and Alpaca cannot trade futures (B1).
STREAM_SPECS: Dict[str, StreamSpec] = {
    "exp1220": StreamSpec("exp1220", ("SPY",), "bull_put", StreamStatus.TRADEABLE, "PR-B"),
    "v5_hedge": StreamSpec(
        "v5_hedge", ("SPY", "IWM", "EFA", "EEM", "QQQ", "TLT", "LQD", "HYG", "GLD", "USO", "DBA", "DBB", "UUP"),
        "long_short_shares", StreamStatus.DEFERRED, "PR-D",
        "crisis-alpha trend overlay; signal port pending (build-plan PR-D).",
    ),
    "gld_cal": StreamSpec(
        "gld_cal", ("GLD", "GC=F"), "etf_future_basis", StreamStatus.BLOCKED, "blocked",
        "GLD-vs-front-gold-future basis; Alpaca has no futures (recon cc2 B1).",
    ),
    "slv_cal": StreamSpec(
        "slv_cal", ("SLV", "SI=F"), "etf_future_basis", StreamStatus.BLOCKED, "blocked",
        "SLV-vs-front-silver-future basis; Alpaca has no futures (recon cc2 B1).",
    ),
    "cross_vol": StreamSpec(
        "cross_vol", ("SPY", "QQQ", "XLF", "XLI"), "rel_value_vol", StreamStatus.DEFERRED, "PR-D",
        "vega-matched IV-RV relative value; execution structure pending (build-plan PR-D).",
    ),
    "xlf_cs": StreamSpec("xlf_cs", ("XLF",), "bull_put", StreamStatus.TRADEABLE, "PR-B"),
    "xli_cs": StreamSpec("xli_cs", ("XLI",), "bull_put", StreamStatus.TRADEABLE, "PR-B"),
    "qqq_cs": StreamSpec("qqq_cs", ("QQQ",), "bull_put", StreamStatus.TRADEABLE, "PR-B"),
}


# ── Order intent (broker-agnostic) ────────────────────────────────────────────

@dataclass(frozen=True)
class OrderLeg:
    """One leg of an order. OCC ``symbol`` for options, ticker for equity."""

    side: str                       # "sell" | "buy"
    sec_type: str                   # "option" | "equity"
    symbol: str                     # OCC contract symbol (option) or ticker (equity)
    qty: int                        # contracts (option) or shares (equity)
    strike: Optional[float] = None
    expiration: Optional[str] = None   # "YYYY-MM-DD"
    right: Optional[str] = None        # "P" | "C"


@dataclass(frozen=True)
class OrderIntent:
    """What a stream wants to trade this cycle — a broker-agnostic instruction.

    Carries ``stream`` and ``symbol`` so downstream order placement / PnL
    attribution (build-plan PR-I) can tag fills per stream — the prerequisite for
    cc3's live covariance.
    """

    stream: str
    symbol: str
    structure: str                  # "bull_put" | "bear_call" | "long_shares" | ...
    legs: Tuple[OrderLeg, ...]
    contracts: int
    est_credit: Optional[float] = None
    est_max_loss: Optional[float] = None
    rationale: str = ""
    meta: Mapping[str, object] = field(default_factory=dict)


@dataclass
class StreamResult:
    """What one stream produced this cycle: intents + an explainable status.

    ``status`` is one of: ``entered`` (intents present), ``no_entry`` (tradeable
    but no qualifying signal), ``vix_gated``, ``no_capital``, ``degraded`` (data
    missing for the symbol), ``deferred`` (engine not yet ported — PR-D), or
    ``blocked`` (no Alpaca execution path — futures). ``reason`` is a short
    human-readable explanation for diagnostics/tests.
    """

    stream_id: str
    intents: List["OrderIntent"] = field(default_factory=list)
    status: str = "no_entry"
    reason: str = ""


@dataclass
class CyclePlan:
    """Result of one VRP scan cycle: the intents to place + full provenance."""

    as_of: Optional[datetime]
    account_equity: float
    vix_exposure: float
    capital: Mapping[str, float]            # {stream_id: dollars} after vol-scale × ladder
    intents: List[OrderIntent] = field(default_factory=list)
    stream_status: Dict[str, str] = field(default_factory=dict)   # stream_id -> status/reason
    degraded_symbols: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def traded_streams(self) -> List[str]:
        return sorted({i.stream for i in self.intents})


# ── Provider protocols (implemented by cc2 / cc3 / cc4 / PR-I / PR-E) ─────────

@runtime_checkable
class DataFeed(Protocol):
    """PR-A (cc2). ``compass.live.vrp_data.VRPDataFeed`` already satisfies this."""

    def snapshot(self, option_symbols: Optional[List[str]] = ..., dte_range: Tuple[int, int] = ...) -> VRPSnapshot: ...
    def get_bars(self, symbol: str, lookback: int = ...) -> pd.DataFrame: ...


@runtime_checkable
class VixExposureProvider(Protocol):
    """PR-D (cc4). Returns the VIX-ladder exposure multiplier in [0, 1].

    Live the implementation MUST override ``VIXLadder``'s permissive 1.0-on-NaN
    default with a conservative fallback (last-known VIX → halt-new-entries);
    see recon cc4 §5.3. The engine treats whatever scalar it returns as final.
    """

    def current_exposure_multiplier(self) -> float: ...


@runtime_checkable
class ReturnsProvider(Protocol):
    """PR-I. Supplies the realized per-stream daily-return matrix for cc3's
    covariance. Columns are stream ids (subset of :data:`VRP_STREAMS`), index is
    date. May be empty/short — cc3.compute_weights cold-starts to a prior."""

    def stream_returns(self, lookback: int = ...) -> pd.DataFrame: ...


@runtime_checkable
class StreamSignalGenerator(Protocol):
    """A per-stream entry-signal → order-intent generator (PR-B owns these)."""

    spec: StreamSpec

    def generate(self, snapshot: VRPSnapshot, capital: float) -> "StreamResult": ...


@runtime_checkable
class OrderSink(Protocol):
    """Places (or records) an :class:`OrderIntent`. The Alpaca adapter is wired
    only at the PR-E cutover; tests use a recording fake. Returns a broker-style
    result dict."""

    def submit(self, intent: OrderIntent) -> Dict[str, object]: ...
