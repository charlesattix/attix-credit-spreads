"""compass/orchestrator/entry_gate.py — Filter raw EXP-2690 intents through
   the macro/regime/event/portfolio gates that the live system must enforce.

Each SignalIntent is evaluated against a fixed sequence of gates
(ORCHESTRATOR_PROPOSAL.md §4.1, table). The output is a GatedSignal whose
`gate_status` is one of:

    ALLOW    — proceed to position_sizer; no caveats
    DEGRADE  — proceed, but with a multiplicative confidence haircut
               (e.g. CPI day → ×0.5, OpEx week monthly cycle → ×0.7)
    BLOCK    — drop; do not size or submit

DEGRADE gates accumulate (their multipliers compose). A BLOCK gate is
terminal — once any BLOCK fires, no further gates are evaluated for that
intent (but the reason is recorded).

Gates (in evaluation order):

    1.  market_closed           BLOCK
    2.  stale_signal            BLOCK   (signal.date older than 24h)
    3.  broker_unsupported      BLOCK   (e.g. GLD/SLV futures legs on Alpaca paper)
    4.  upstream_action         BLOCK   (action != OPEN — HOLD/NONE/BLOCKED/ERROR pass through unchanged)
    5.  already_open            BLOCK   (sleeve already has an open position)
    6.  param_drift             BLOCK   (delta/dte/width drift from canonical > tolerance)
    7.  vix_extreme             BLOCK   (VIX > sleeve's vix_block, default 40)
    8.  vix_term_inversion      BLOCK   (^VIX > ^VIX3M)
    9.  fomc_blackout           BLOCK
    10. nfp_blackout            BLOCK
    11. cpi_blackout            DEGRADE × 0.5
    12. opex_week               DEGRADE × 0.7 for monthly-cycle sleeves
    13. correlation_overload    DEGRADE × ρ-penalty if adding sleeve would push port corr > 0.6

All gates are pure functions of their inputs. The class holds only the
calendar, the canonical-param table, and configurable tolerances. There is
no I/O inside `evaluate()` — VIX, VIX3M, and market-clock state are passed
in via `MarketContext`, fetched once per pipeline run.

See ORCHESTRATOR_PROPOSAL.md §4.1 and Atlas integration guide for design context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from compass.orchestrator import calendars
from compass.orchestrator.canonical_params import (
    CanonicalRegistry,
    load_canonical_params,
)
from compass.orchestrator.portfolio_state import PortfolioState
from compass.orchestrator.types import GatedSignal, GateStatus, SignalIntent

LOG = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Canonical parameters
# ───────────────────────────────────────────────────────────────────────────
# Source of truth is ``canonical_params.yaml`` loaded via
# ``load_canonical_params()``. The registry exposes per-sleeve
# ``CanonicalParams`` dataclasses plus a ``validate_signal`` method that
# enforces the per-sleeve drift tolerances declared in the YAML
# (delta_tol / dte_tol / width_tol). See compass/orchestrator/canonical_params.py.


# Sleeves whose canonical execution requires instruments the broker can't
# trade in the current mode. Today: Alpaca paper has no futures, so the
# GLD/SLV calendar (which needs the front-month GC=F / SI=F leg) is blocked.
BROKER_UNSUPPORTED_STREAMS_ALPACA_PAPER: frozenset = frozenset({"gld_cal", "slv_cal"})


# Degrade multipliers (ORCHESTRATOR_PROPOSAL §4.1 table)
CPI_DEGRADE_MULTIPLIER = 0.5
OPEX_DEGRADE_MULTIPLIER = 0.7
CORRELATION_CAP = 0.6           # add'l correlation above this → degrade
CORRELATION_FLOOR_PENALTY = 0.5  # the penalty cap; ρ=1.0 → 0.5x; ρ=0.6 → 1.0x

# Drift tolerances (param_drift gate). Anything within tolerance ALLOWs.
DELTA_TOLERANCE = 0.05          # ±0.05 around canonical short delta
OTM_TOLERANCE_PCT = 0.02        # ±2 percentage points around canonical OTM
DTE_TOLERANCE_DAYS = 5          # ±5 calendar days
WIDTH_TOLERANCE_FRAC = 0.20     # ±20% of canonical width

STALE_SIGNAL_HOURS = 24.0


# ───────────────────────────────────────────────────────────────────────────
# MarketContext — inputs the gate consumes that are NOT in PortfolioState
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketContext:
    """Market-wide context passed into EntryGate.evaluate().

    Fetched once per pipeline run (by pipeline.py / run_orchestrator.py),
    NOT inside the gate — keeps `evaluate()` pure / testable.

    Attributes
    ----------
    now_utc          UTC timestamp at which the pipeline started.
    is_market_open   Alpaca clock + NYSE holiday calendar verdict.
    vix              Most recent ^VIX close (None if Yahoo unavailable).
    vix3m            Most recent ^VIX3M close (None if Yahoo unavailable).
    broker_mode      "alpaca_paper" | "alpaca_live"; used by the
                     broker-capability gate.
    """

    now_utc: datetime
    is_market_open: bool
    vix: Optional[float]
    vix3m: Optional[float]
    broker_mode: str = "alpaca_paper"


# ───────────────────────────────────────────────────────────────────────────
# EntryGate
# ───────────────────────────────────────────────────────────────────────────

class EntryGate:
    """Applies the gate chain to a batch of SignalIntents.

    Construction is cheap; the gate has no I/O — calendar lookups are
    delegated to ``compass.orchestrator.calendars`` (lazy-loaded CSVs).
    """

    def __init__(
        self,
        canonical_params: Optional[CanonicalRegistry] = None,
        unsupported_streams: Optional[Dict[str, frozenset]] = None,
    ):
        self.canonical_params = canonical_params or load_canonical_params()
        # Map: broker_mode → set of unsupported sleeve ids
        self.unsupported = unsupported_streams or {
            "alpaca_paper": BROKER_UNSUPPORTED_STREAMS_ALPACA_PAPER,
            "alpaca_live":  frozenset(),
        }

    # ── public entry point ─────────────────────────────────────────────

    def evaluate(
        self,
        intents: List[SignalIntent],
        portfolio: PortfolioState,
        market: MarketContext,
        today: Optional[date] = None,
    ) -> List[GatedSignal]:
        """Run every intent through the gate chain.

        Parameters
        ----------
        intents:    raw EXP-2690 output, already wrapped as SignalIntent.
        portfolio:  current PortfolioState (read-only).
        market:     MarketContext fetched once for this run.
        today:      date used for calendar lookups. Defaults to
                    market.now_utc.date() if not supplied.
        """
        if today is None:
            today = market.now_utc.date()

        # Fail-closed: if the portfolio snapshot couldn't load, refuse
        # every intent (we can't enforce already-open / correlation gates).
        if not portfolio.load_ok:
            return [
                GatedSignal(
                    intent=i,
                    gate_status="BLOCK",
                    gate_reasons=[f"portfolio_unavailable: {';'.join(portfolio.errors) or 'unknown'}"],
                    confidence_adj=1.0,
                )
                for i in intents
            ]

        out: List[GatedSignal] = []
        for intent in intents:
            out.append(self._evaluate_one(intent, portfolio, market, today))
        return out

    # ── per-intent gate chain ──────────────────────────────────────────

    def _evaluate_one(
        self,
        intent: SignalIntent,
        portfolio: PortfolioState,
        market: MarketContext,
        today: date,
    ) -> GatedSignal:
        # ── upstream-action shortcut ───────────────────────────────────
        # Entry gates exist to filter NEW entries. Non-OPEN intents bypass
        # the chain so close/hold logic isn't accidentally blocked by
        # already_open, param_drift, etc.
        #   OPEN              → full gate chain below
        #   CLOSE, HOLD       → ALLOW (pass-through; downstream handles)
        #   NONE, BLOCKED,
        #   ERROR             → BLOCK (upstream said nothing actionable)
        if intent.action in ("CLOSE", "HOLD"):
            return GatedSignal(
                intent=intent,
                gate_status="ALLOW",
                gate_reasons=[f"upstream_action: {intent.action} passes through entry gate"],
                confidence_adj=1.0,
            )
        if intent.action != "OPEN":
            return GatedSignal(
                intent=intent,
                gate_status="BLOCK",
                gate_reasons=[
                    f"upstream_action: action={intent.action!r} (only OPEN proceeds)"
                ],
                confidence_adj=1.0,
            )

        reasons: List[str] = []
        conf_adj = 1.0

        # Each gate function returns (status, reason_or_none, conf_multiplier).
        # BLOCK is terminal; DEGRADE multipliers compose; ALLOW is a no-op.
        gates = [
            self._gate_market_open(market),
            self._gate_stale_signal(intent, market),
            self._gate_broker_unsupported(intent, market),
            self._gate_already_open(intent, portfolio),
            self._gate_param_drift(intent),
            self._gate_vix_extreme(intent, market),
            self._gate_vix_term_inversion(intent, market),
            self._gate_fomc_blackout(today),
            self._gate_nfp_blackout(today),
            self._gate_cpi_blackout(today),
            self._gate_opex_week(intent, today),
            self._gate_correlation(intent, portfolio),
        ]

        for status, reason, mult in gates:
            if status == "BLOCK":
                if reason:
                    reasons.append(reason)
                return GatedSignal(
                    intent=intent,
                    gate_status="BLOCK",
                    gate_reasons=reasons,
                    confidence_adj=conf_adj,
                )
            if status == "DEGRADE":
                if reason:
                    reasons.append(reason)
                conf_adj *= mult

        final_status: GateStatus = "DEGRADE" if conf_adj < 1.0 else "ALLOW"
        return GatedSignal(
            intent=intent,
            gate_status=final_status,
            gate_reasons=reasons,
            confidence_adj=conf_adj,
        )

    # ── individual gates ───────────────────────────────────────────────
    # Every gate returns the tuple (status, reason_or_None, conf_multiplier).
    # `conf_multiplier` is only meaningful for DEGRADE; ALLOW/BLOCK ignore it.

    @staticmethod
    def _gate_market_open(market: MarketContext) -> Tuple[GateStatus, Optional[str], float]:
        if not market.is_market_open:
            return ("BLOCK", "market_closed", 1.0)
        return ("ALLOW", None, 1.0)

    @staticmethod
    def _gate_stale_signal(
        intent: SignalIntent, market: MarketContext
    ) -> Tuple[GateStatus, Optional[str], float]:
        try:
            sig_dt = datetime.strptime(intent.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return ("BLOCK", f"stale_signal: unparseable date '{intent.date}'", 1.0)
        age_h = (market.now_utc - sig_dt).total_seconds() / 3600.0
        if age_h > STALE_SIGNAL_HOURS:
            return (
                "BLOCK",
                f"stale_signal: {age_h:.1f}h old (threshold {STALE_SIGNAL_HOURS}h)",
                1.0,
            )
        return ("ALLOW", None, 1.0)

    def _gate_broker_unsupported(
        self, intent: SignalIntent, market: MarketContext
    ) -> Tuple[GateStatus, Optional[str], float]:
        bad = self.unsupported.get(market.broker_mode, frozenset())
        if intent.stream in bad:
            return (
                "BLOCK",
                f"broker_unsupported: {intent.stream} not tradable on {market.broker_mode}",
                1.0,
            )
        return ("ALLOW", None, 1.0)

    @staticmethod
    def _gate_already_open(
        intent: SignalIntent, portfolio: PortfolioState
    ) -> Tuple[GateStatus, Optional[str], float]:
        if portfolio.has_open_position(intent.stream):
            return (
                "BLOCK",
                f"already_open: sleeve {intent.stream!r} has an open position",
                1.0,
            )
        return ("ALLOW", None, 1.0)

    def _gate_param_drift(
        self, intent: SignalIntent
    ) -> Tuple[GateStatus, Optional[str], float]:
        """BLOCK if signal drifts beyond the per-sleeve tolerances declared
        in canonical_params.yaml. Delegates the actual comparison to
        ``CanonicalRegistry.validate_signal`` so the YAML's per-sleeve
        delta_tol / dte_tol / width_tol overrides are honoured.
        """
        if not self.canonical_params.has(intent.stream):
            return (
                "BLOCK",
                f"param_drift: no canonical params for stream {intent.stream!r}",
                1.0,
            )
        result = self.canonical_params.validate_signal(intent)
        if not result.ok:
            return ("BLOCK", "; ".join(result.reasons), 1.0)
        return ("ALLOW", None, 1.0)

    def _gate_vix_extreme(
        self, intent: SignalIntent, market: MarketContext
    ) -> Tuple[GateStatus, Optional[str], float]:
        if not self.canonical_params.has(intent.stream):
            return ("ALLOW", None, 1.0)
        canon = self.canonical_params.get(intent.stream)
        # VIX gating applies only to listed-option credit-spread structures
        # (PCS / CCS / IC). Calendar / iv_rv_pair / equity_etf sleeves do
        # not condition entry on VIX level.
        if not canon.is_credit_spread:
            return ("ALLOW", None, 1.0)
        vix_block = canon.vix_block
        if market.vix is None:                   # fail-closed if data missing
            return ("BLOCK", "vix_unavailable: cannot evaluate VIX gate", 1.0)
        if market.vix > vix_block:
            return (
                "BLOCK",
                f"vix_extreme: VIX={market.vix:.1f} > block={vix_block:.1f}",
                1.0,
            )
        return ("ALLOW", None, 1.0)

    @staticmethod
    def _gate_vix_term_inversion(
        intent: SignalIntent, market: MarketContext
    ) -> Tuple[GateStatus, Optional[str], float]:
        # Only put-credit-spread sleeves use the V+F term-structure overlay.
        if intent.direction != "put_credit_spread":
            return ("ALLOW", None, 1.0)
        if market.vix is None or market.vix3m is None:
            # Soft failure: missing VIX3M shouldn't block all PCS entries
            # (the data feed is more fragile than ^VIX itself). Degrade
            # confidence instead — same posture as ComboRegimeDetector's
            # "abstain on missing VIX3M" rule.
            return ("DEGRADE", "vix3m_unavailable: degrading 0.7x", 0.7)
        if market.vix > market.vix3m:
            return (
                "BLOCK",
                f"vix_term_inversion: VIX={market.vix:.2f} > VIX3M={market.vix3m:.2f}",
                1.0,
            )
        return ("ALLOW", None, 1.0)

    @staticmethod
    def _gate_fomc_blackout(today: date) -> Tuple[GateStatus, Optional[str], float]:
        """BLOCK if today is an FOMC statement day OR the day immediately
        before one (T-1 pre-announcement blackout)."""
        try:
            if calendars.is_fomc_today(today):
                return ("BLOCK", "fomc_blackout: FOMC statement day", 1.0)
            if calendars.is_fomc_today(today + timedelta(days=1)):
                return ("BLOCK", "fomc_blackout: T-1 (FOMC statement tomorrow)", 1.0)
        except (calendars.CalendarStaleError, calendars.CalendarMissingError) as exc:
            return ("BLOCK", f"fomc_blackout: calendar unavailable ({exc})", 1.0)
        return ("ALLOW", None, 1.0)

    @staticmethod
    def _gate_nfp_blackout(today: date) -> Tuple[GateStatus, Optional[str], float]:
        """BLOCK on NFP release day and the trading day before it."""
        try:
            if calendars.is_nfp_today(today):
                return ("BLOCK", "nfp_blackout: NFP release day", 1.0)
            if calendars.is_nfp_tomorrow(today):
                return ("BLOCK", "nfp_blackout: T-1 (NFP tomorrow)", 1.0)
        except (calendars.CalendarStaleError, calendars.CalendarMissingError) as exc:
            return ("BLOCK", f"nfp_blackout: calendar unavailable ({exc})", 1.0)
        return ("ALLOW", None, 1.0)

    @staticmethod
    def _gate_cpi_blackout(today: date) -> Tuple[GateStatus, Optional[str], float]:
        """DEGRADE on CPI release day (proposal § 4.1 — CPI × 0.5)."""
        try:
            if calendars.is_cpi_today(today):
                return (
                    "DEGRADE",
                    f"cpi_blackout: CPI release day → × {CPI_DEGRADE_MULTIPLIER}",
                    CPI_DEGRADE_MULTIPLIER,
                )
        except (calendars.CalendarStaleError, calendars.CalendarMissingError) as exc:
            return ("BLOCK", f"cpi_blackout: calendar unavailable ({exc})", 1.0)
        return ("ALLOW", None, 1.0)

    def _gate_opex_week(
        self, intent: SignalIntent, today: date
    ) -> Tuple[GateStatus, Optional[str], float]:
        """DEGRADE monthly-cycle sleeves during the week of monthly OPEX
        (3rd Friday). Monthly-cycle alignment is derived from the sleeve's
        canonical structure: PCS / CCS / IC sleeves trade the standard
        listed monthly cycle; calendar / iv_rv_pair / equity_etf do not.
        """
        if not self.canonical_params.has(intent.stream):
            return ("ALLOW", None, 1.0)
        canon = self.canonical_params.get(intent.stream)
        if not canon.is_credit_spread:
            return ("ALLOW", None, 1.0)
        if calendars.is_opex_week(today):
            return (
                "DEGRADE",
                f"opex_week: monthly cycle near OpEx → × {OPEX_DEGRADE_MULTIPLIER}",
                OPEX_DEGRADE_MULTIPLIER,
            )
        return ("ALLOW", None, 1.0)

    @staticmethod
    def _gate_correlation(
        intent: SignalIntent, portfolio: PortfolioState
    ) -> Tuple[GateStatus, Optional[str], float]:
        """Degrade if average correlation to currently-open sleeves exceeds CORRELATION_CAP.

        The penalty interpolates linearly from 1.0× at ρ=CORRELATION_CAP
        down to CORRELATION_FLOOR_PENALTY (0.5×) at ρ=1.0. Anything below
        the cap is ALLOW.
        """
        if not portfolio.open_streams:
            return ("ALLOW", None, 1.0)
        # Tolerate duck-typed PortfolioState stubs (used in tests) that
        # don't expose a correlation_matrix attribute.
        corr_matrix = getattr(portfolio, "correlation_matrix", None)
        if corr_matrix is None or getattr(corr_matrix, "empty", True):
            # No empirical correlation data — skip the gate rather than
            # over-block. position_sizer may still apply its own haircut.
            return ("ALLOW", None, 1.0)

        rhos = []
        for s in portfolio.open_streams:
            if s == intent.stream:
                continue
            rho = portfolio.correlation(intent.stream, s)
            if rho is not None:
                rhos.append(rho)
        if not rhos:
            return ("ALLOW", None, 1.0)

        avg_rho = sum(rhos) / len(rhos)
        if avg_rho <= CORRELATION_CAP:
            return ("ALLOW", None, 1.0)

        # Linear haircut: 1.0 at cap, FLOOR at 1.0
        span = max(1e-9, 1.0 - CORRELATION_CAP)
        frac = (avg_rho - CORRELATION_CAP) / span
        mult = 1.0 - frac * (1.0 - CORRELATION_FLOOR_PENALTY)
        mult = max(CORRELATION_FLOOR_PENALTY, min(1.0, mult))
        return (
            "DEGRADE",
            f"correlation_overload: avg ρ={avg_rho:.2f} vs cap={CORRELATION_CAP:.2f} → × {mult:.2f}",
            mult,
        )


# ───────────────────────────────────────────────────────────────────────────
# Module-level shim — the public contract consumed by pipeline.py + tests
# ───────────────────────────────────────────────────────────────────────────
#
# The orchestrator pipeline calls ``entry_gate.evaluate(intents, portfolio,
# date)`` — three positional args, no MarketContext. This shim builds a
# sensible default MarketContext (market open, no VIX data) and delegates
# to a process-wide EntryGate singleton. Callers that need a richer market
# snapshot can construct an EntryGate themselves and pass an explicit
# MarketContext.

_DEFAULT_GATE: Optional["EntryGate"] = None


def _get_default_gate() -> "EntryGate":
    global _DEFAULT_GATE
    if _DEFAULT_GATE is None:
        _DEFAULT_GATE = EntryGate()
    return _DEFAULT_GATE


def _coerce_today(date_arg) -> date:
    """Accept either a ``date`` or an ISO ``YYYY-MM-DD`` string."""
    if isinstance(date_arg, datetime):
        return date_arg.date()
    if isinstance(date_arg, date):
        return date_arg
    if isinstance(date_arg, str):
        return datetime.strptime(date_arg, "%Y-%m-%d").date()
    raise TypeError(
        f"expected date, datetime, or YYYY-MM-DD string; got {type(date_arg).__name__}"
    )


def _default_market_context(today: date, broker_mode: str = "alpaca_paper") -> MarketContext:
    """Build a permissive MarketContext for the module-level entry point.

    The default assumes the market is open (the pipeline already gates on
    its own clock check before invoking entry_gate) and that VIX data is
    unavailable. PCS sleeves consult the canonical-params vix_block, which
    fails closed when VIX is None — see _gate_vix_extreme. Callers that
    have a real market snapshot should build an EntryGate directly and
    pass a populated MarketContext.
    """
    return MarketContext(
        now_utc=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
        is_market_open=calendars.is_market_open(today),
        vix=None,
        vix3m=None,
        broker_mode=broker_mode,
    )


def evaluate(
    intents: List[SignalIntent],
    portfolio,
    date_arg,
    *,
    market: Optional[MarketContext] = None,
) -> List[GatedSignal]:
    """Module-level entry point — the contract consumed by pipeline.py.

    Parameters
    ----------
    intents:    list of EXP-2690 SignalIntent objects.
    portfolio:  PortfolioState (or any duck-typed stand-in that exposes
                load_ok, errors, open_streams, has_open_position, and
                correlation).
    date_arg:   the trading date as either a ``date`` or an ISO string.
    market:     optional MarketContext override. When omitted, a permissive
                default is built (market open, no VIX data, alpaca_paper
                broker mode).

    Returns
    -------
    List[GatedSignal] — one per input intent, preserving order.
    """
    today = _coerce_today(date_arg)
    ctx = market if market is not None else _default_market_context(today)
    return _get_default_gate().evaluate(intents, portfolio, ctx, today=today)


__all__ = [
    "BROKER_UNSUPPORTED_STREAMS_ALPACA_PAPER",
    "CORRELATION_CAP",
    "CORRELATION_FLOOR_PENALTY",
    "CPI_DEGRADE_MULTIPLIER",
    "EntryGate",
    "MarketContext",
    "OPEX_DEGRADE_MULTIPLIER",
    "STALE_SIGNAL_HOURS",
    "evaluate",
]
