"""compass/orchestrator/order_router.py — atomic multi-leg order submission.

This is Stage 3 of the orchestrator pipeline:

    SizedOrder  ──▶  order_router.submit()  ──▶  RoutedOrder
                       │
                       ├── consults broker_capability.yaml
                       ├── builds SpreadOrder (OCC symbols, idempotent id)
                       ├── submits ATOMIC MultilegOrderRequest only
                       └── reconciles vs. broker fills

Design rules (see ORCHESTRATOR_PROPOSAL.md §4.3 + §5.3)
-------------------------------------------------------
1. **Atomic multi-leg or refuse.** If the alpaca-py MLEG path is
   unavailable (older SDK, ImportError) the router returns status
   ``REJECTED_NO_MLEG`` and **does not** submit any single leg. The
   silent-legging fallback that lives inside
   ``alpaca_connector.submit_spread()`` is bypassed entirely — this
   module calls ``connector._trading_client.submit_order`` directly
   with a ``MultilegOrderRequest``. If even one leg cannot be sent
   together with the others, the whole order is dropped.

2. **Capability check before the wire.** Every leg's underlying is
   checked against the per-broker ``symbols.untradeable`` list and the
   ``asset_classes`` matrix in ``broker_capability.yaml``. Futures
   legs on Alpaca → ``REJECTED_BROKER_UNSUPPORTED``.

3. **Submission blackout.** Refuse to submit during 09:30:00–09:32:00 ET
   (open auction settle) and 15:58:00–16:00:00 ET (closing-cross). The
   policy is loaded from the YAML per broker. Status:
   ``REJECTED_BLACKOUT``.

4. **Idempotent client_order_id.**
   ``{date}-{stream}-{ticker}-{exp}-{shortK}-{longK}``. Re-running the
   router with the same SizedOrder produces the same id; the broker
   rejects duplicates so accidental double-fires are safe.

5. **Limit price.** For credit spreads the limit is the minimum net
   credit we will accept: ``max(0.05, expected_credit × 0.90)`` — 10%
   slippage tolerance against the sized expected credit.

6. **TIF = DAY.** No overnight resting orders from the router.

7. **Post-submission reconcile.** After all orders are sent we call
   ``connector.reconcile(intended)`` so the router's return value
   carries the actual broker-side state per OCC symbol (MATCH / UNDER /
   OVER / MISSING / ORPHAN).

This module is a **pure library**. Nothing here executes on import; it
is invoked by Vesper / pipeline.py. The orchestrator's __init__ does
not re-export this class so callers must import it explicitly.

See ORCHESTRATOR_PROPOSAL.md §4.3, Atlas integration guide §3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from compass.alpaca_connector import (
    AlpacaConnector,
    OptionLeg,
    SpreadOrder,
    build_occ_symbol,
)
from compass.orchestrator.types import SizedOrder

LOG = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Status literals + RoutedOrder
# ───────────────────────────────────────────────────────────────────────────

# Routed-order status values.
STATUS_SUBMITTED = "SUBMITTED"
STATUS_FILLED = "FILLED"
STATUS_PARTIAL = "PARTIAL"
STATUS_REJECTED_NO_MLEG = "REJECTED_NO_MLEG"
STATUS_REJECTED_BROKER_UNSUPPORTED = "REJECTED_BROKER_UNSUPPORTED"
STATUS_REJECTED_BLACKOUT = "REJECTED_BLACKOUT"
STATUS_REJECTED_SDK_NONE = "REJECTED_SDK_NONE"
STATUS_REJECTED_BAD_INPUT = "REJECTED_BAD_INPUT"
STATUS_REJECTED_EXCEPTION = "REJECTED_EXCEPTION"


@dataclass(frozen=True)
class RoutedOrder:
    """The router's verdict + broker handle for a single SizedOrder.

    Attributes
    ----------
    sized              The SizedOrder that came in (immutable upstream object).
    client_order_id    Deterministic, idempotent id built by the router.
    spread_order       The SpreadOrder we actually built (with OCC symbols),
                       or None if rejected before construction.
    broker_order_id    Alpaca-assigned id; empty string on rejection.
    status             One of the STATUS_* constants.
    reasons            Ordered list of human-readable strings explaining
                       the verdict (one per check that contributed).
    reconciliation     Per-OCC-symbol reconciliation entry from
                       connector.reconcile() (empty for rejected orders).
    submitted_at       ISO-8601 UTC; None if not submitted.
    """

    sized: SizedOrder
    client_order_id: str
    spread_order: Optional[SpreadOrder]
    broker_order_id: str
    status: str
    reasons: List[str] = field(default_factory=list)
    reconciliation: Dict[str, Dict] = field(default_factory=dict)
    submitted_at: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_SUBMITTED, STATUS_FILLED, STATUS_PARTIAL)


# ───────────────────────────────────────────────────────────────────────────
# OrderRouter
# ───────────────────────────────────────────────────────────────────────────

DEFAULT_CAPABILITY_PATH = Path(__file__).resolve().parent / "broker_capability.yaml"


class OrderRouter:
    """Atomic-multi-leg order submitter for the orchestrator pipeline.

    Parameters
    ----------
    connector:        live AlpacaConnector (must be authenticated).
    broker_id:        key into broker_capability.yaml -> brokers.
                      Defaults to "alpaca_paper".
    capability_path:  override path to the YAML capability file.
    now_provider:     callable returning a timezone-aware datetime for
                      the current moment. Defaults to
                      ``datetime.now(timezone.utc)``. Injectable for
                      tests.
    """

    def __init__(
        self,
        connector: AlpacaConnector,
        broker_id: str = "alpaca_paper",
        capability_path: Optional[Path] = None,
        now_provider=None,
    ):
        self.connector = connector
        self.broker_id = broker_id
        self._now = now_provider or (lambda: datetime.now(timezone.utc))

        path = Path(capability_path) if capability_path else DEFAULT_CAPABILITY_PATH
        with path.open() as fh:
            doc = yaml.safe_load(fh) or {}
        all_brokers = doc.get("brokers", {})
        if broker_id not in all_brokers:
            raise ValueError(
                f"broker_id {broker_id!r} not in {path}: known = {list(all_brokers)}"
            )
        self.capability: Dict = all_brokers[broker_id]

    # ── public api ──────────────────────────────────────────────────────

    def submit(self, sized_orders: List[SizedOrder]) -> List[RoutedOrder]:
        """Submit a batch of SizedOrders. Returns one RoutedOrder per input.

        The list is in the same order as the input. Failed orders carry
        a status starting with ``REJECTED_`` and never touched the wire.
        Successful ones have a non-empty ``broker_order_id`` plus a
        reconciliation entry per OCC symbol.
        """
        routed: List[RoutedOrder] = []
        intended: Dict[str, float] = {}

        for s in sized_orders:
            r = self._route_one(s)
            routed.append(r)
            if r.ok and r.spread_order is not None:
                for leg in r.spread_order.legs:
                    occ = build_occ_symbol(
                        leg.ticker, leg.expiration, leg.strike, leg.option_type
                    )
                    signed = leg.quantity if leg.side.upper() == "BUY" else -leg.quantity
                    intended[occ] = intended.get(occ, 0.0) + float(signed)

        # Post-submission reconciliation. One snapshot for the whole batch.
        recon_all: Dict[str, Dict] = {}
        if intended:
            try:
                recon_all = self.connector.reconcile(intended)
            except Exception as e:  # noqa: BLE001
                LOG.error("reconcile after submit failed: %s", e)
                recon_all = {}

        # Attach the per-order reconciliation slices.
        out: List[RoutedOrder] = []
        for r in routed:
            if not r.ok or r.spread_order is None:
                out.append(r)
                continue
            slice_: Dict[str, Dict] = {}
            for leg in r.spread_order.legs:
                occ = build_occ_symbol(
                    leg.ticker, leg.expiration, leg.strike, leg.option_type
                )
                if occ in recon_all:
                    slice_[occ] = recon_all[occ]
            out.append(
                RoutedOrder(
                    sized=r.sized,
                    client_order_id=r.client_order_id,
                    spread_order=r.spread_order,
                    broker_order_id=r.broker_order_id,
                    status=r.status,
                    reasons=r.reasons,
                    reconciliation=slice_,
                    submitted_at=r.submitted_at,
                )
            )
        return out

    # ── routing for one order ───────────────────────────────────────────

    def _route_one(self, s: SizedOrder) -> RoutedOrder:
        reasons: List[str] = []
        intent = s.gated.intent
        cid = self._client_order_id(s)

        # 1. SDK must be present.
        if getattr(self.connector, "_sdk", "none") == "none":
            return RoutedOrder(
                sized=s, client_order_id=cid, spread_order=None,
                broker_order_id="", status=STATUS_REJECTED_SDK_NONE,
                reasons=["alpaca SDK not loaded on connector"],
            )

        # 2. Capability — ticker tradability + asset class.
        cap_ok, cap_reason = self._capability_check(intent.ticker, intent.direction)
        if not cap_ok:
            return RoutedOrder(
                sized=s, client_order_id=cid, spread_order=None,
                broker_order_id="", status=STATUS_REJECTED_BROKER_UNSUPPORTED,
                reasons=[cap_reason],
            )

        # 3. Submission-window blackout.
        bl_reason = self._blackout_check()
        if bl_reason:
            return RoutedOrder(
                sized=s, client_order_id=cid, spread_order=None,
                broker_order_id="", status=STATUS_REJECTED_BLACKOUT,
                reasons=[bl_reason],
            )

        # 4. Build the SpreadOrder (legs + OCC symbols).
        try:
            spread = self._build_spread_order(s, cid)
        except ValueError as e:
            return RoutedOrder(
                sized=s, client_order_id=cid, spread_order=None,
                broker_order_id="", status=STATUS_REJECTED_BAD_INPUT,
                reasons=[f"could not build spread: {e}"],
            )

        # 5. Atomic MLEG submission — no fallback.
        broker_id, status, sub_reasons = self._submit_atomic_mleg(spread)
        reasons.extend(sub_reasons)

        return RoutedOrder(
            sized=s, client_order_id=cid, spread_order=spread,
            broker_order_id=broker_id, status=status, reasons=reasons,
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )

    # ── building blocks ─────────────────────────────────────────────────

    def _client_order_id(self, s: SizedOrder) -> str:
        """Idempotent id: {date}-{stream}-{ticker}-{exp}-{shortK}-{longK}.

        Strikes are rendered with no decimal point if integer, else with
        one decimal — to keep the id stable across float-imprecision.
        """
        intent = s.gated.intent
        return "-".join([
            intent.date,
            intent.stream,
            intent.ticker.upper(),
            s.expiration,
            _fmt_strike(s.short_strike),
            _fmt_strike(s.long_strike),
        ])

    def _capability_check(
        self, ticker: str, direction: Optional[str]
    ) -> Tuple[bool, str]:
        """True / reason — does the broker support this underlier + class?"""
        sym = (ticker or "").upper()
        if not sym:
            return False, "empty ticker"

        # Underliers explicitly blacklisted (e.g. futures roots).
        untradeable = {
            t.upper() for t in self.capability.get("symbols", {}).get("untradeable", [])
        }
        if sym in untradeable:
            return False, f"broker_unsupported: {sym} not tradable on {self.broker_id}"

        # Whitelist (when present): the symbol must appear.
        tradable = {
            t.upper() for t in self.capability.get("symbols", {}).get("tradable", [])
        }
        if tradable and sym not in tradable:
            return False, f"broker_unsupported: {sym} not in tradable list for {self.broker_id}"

        # Asset class — we route option spreads.
        if direction and "spread" in direction.lower():
            if not self.capability.get("asset_classes", {}).get("us_option", False):
                return False, f"broker_unsupported: us_option disabled for {self.broker_id}"
            if not self.capability.get("order_classes", {}).get("multi_leg_options", False):
                return False, f"broker_unsupported: multi_leg_options disabled for {self.broker_id}"
        return True, ""

    def _blackout_check(self) -> Optional[str]:
        """Return a reason string if we are inside a blackout window."""
        try:
            from zoneinfo import ZoneInfo  # py3.9+
            now_et = self._now().astimezone(ZoneInfo("America/New_York")).time()
        except Exception:  # noqa: BLE001 — tzdata absence is non-fatal
            return None
        for window in self.capability.get("submission_blackout_et", []) or []:
            try:
                start_s, end_s = window.split("-")
                start = _parse_hhmm(start_s)
                end = _parse_hhmm(end_s)
            except (ValueError, AttributeError):
                continue
            if start <= now_et < end:
                return f"submission blackout {window} ET"
        return None

    def _build_spread_order(self, s: SizedOrder, cid: str) -> SpreadOrder:
        """Map (direction, short_strike, long_strike) → 2-leg SpreadOrder."""
        intent = s.gated.intent
        direction = (intent.direction or "").lower()

        if "put" in direction:
            option_type = "P"
            strategy = "bull_put_spread"
            # Put credit spread: SELL short put (higher K), BUY long put (lower K).
            if s.short_strike <= s.long_strike:
                raise ValueError(
                    f"put credit spread requires short_strike > long_strike "
                    f"(got short={s.short_strike}, long={s.long_strike})"
                )
        elif "call" in direction:
            option_type = "C"
            strategy = "bear_call_spread"
            # Bear call spread: SELL short call (lower K), BUY long call (higher K).
            if s.short_strike >= s.long_strike:
                raise ValueError(
                    f"bear call spread requires short_strike < long_strike "
                    f"(got short={s.short_strike}, long={s.long_strike})"
                )
        else:
            raise ValueError(f"unsupported direction for credit spread: {intent.direction!r}")

        if s.contract_count <= 0:
            raise ValueError(f"contract_count must be > 0, got {s.contract_count}")

        legs = [
            OptionLeg(
                ticker=intent.ticker.upper(),
                expiration=s.expiration,
                strike=float(s.short_strike),
                option_type=option_type,
                side="SELL",
                quantity=int(s.contract_count),
            ),
            OptionLeg(
                ticker=intent.ticker.upper(),
                expiration=s.expiration,
                strike=float(s.long_strike),
                option_type=option_type,
                side="BUY",
                quantity=int(s.contract_count),
            ),
        ]

        net_credit = self._limit_price(s.expected_credit)
        return SpreadOrder(
            stream=intent.stream,
            strategy=strategy,
            legs=legs,
            net_credit=net_credit,
            client_order_id=cid,
            tif="DAY",
        )

    @staticmethod
    def _limit_price(expected_credit: float) -> float:
        """Minimum net credit we will accept = max(0.05, expected × 0.90)."""
        floor = 0.05
        slipped = float(expected_credit) * 0.90
        return round(max(floor, slipped), 2)

    # ── the actual submission ───────────────────────────────────────────

    def _submit_atomic_mleg(
        self, spread: SpreadOrder
    ) -> Tuple[str, str, List[str]]:
        """Submit the spread atomically as a single MultilegOrderRequest.

        Returns (broker_order_id, status, reasons).

        On any failure — ImportError of MLEG primitives, broker
        rejection, network exception — we return REJECTED_* with the
        diagnostic in `reasons`. **We never split into single legs.**
        """
        # Guarded import: if the installed alpaca-py is too old to
        # expose MultilegOrderRequest we fail closed.
        try:
            from alpaca.trading.requests import (
                MultilegOrderRequest,
                OptionLegRequest,
            )
            from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        except ImportError as e:
            return (
                "",
                STATUS_REJECTED_NO_MLEG,
                [
                    f"alpaca-py does not expose MultilegOrderRequest ({e}); "
                    "refusing to leg into the spread"
                ],
            )

        legs_req = [
            OptionLegRequest(
                symbol=build_occ_symbol(
                    leg.ticker, leg.expiration, leg.strike, leg.option_type
                ),
                side=OrderSide.BUY if leg.side.upper() == "BUY" else OrderSide.SELL,
                ratio_qty=1,  # ratio inside the spread; total qty is the parent qty
            )
            for leg in spread.legs
        ]
        # All legs in our credit spreads have the same contract_count;
        # the parent qty is that count, and ratio_qty=1 per leg.
        parent_qty = int(spread.legs[0].quantity) if spread.legs else 1

        tif = TimeInForce.DAY if spread.tif.upper() == "DAY" else TimeInForce.GTC
        try:
            req = MultilegOrderRequest(
                legs=legs_req,
                qty=parent_qty,
                time_in_force=tif,
                order_class=OrderClass.MLEG,
                limit_price=spread.net_credit,
                client_order_id=spread.client_order_id,
            )
        except Exception as e:  # noqa: BLE001
            return (
                "",
                STATUS_REJECTED_BAD_INPUT,
                [f"MultilegOrderRequest construction failed: {e}"],
            )

        try:
            resp = self.connector._trading_client.submit_order(order_data=req)
        except Exception as e:  # noqa: BLE001
            LOG.error("MLEG submit failed for %s: %s", spread.client_order_id, e)
            return ("", STATUS_REJECTED_EXCEPTION, [f"broker submit raised: {e}"])

        broker_id = str(getattr(resp, "id", "") or "")
        raw_status = str(getattr(resp, "status", "") or "").lower()
        status = _normalize_broker_status(raw_status)
        LOG.info(
            "MLEG submitted %s -> broker_id=%s status=%s",
            spread.client_order_id, broker_id, status,
        )
        return (broker_id, status, [f"broker_status={raw_status or 'unknown'}"])


# ───────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ───────────────────────────────────────────────────────────────────────────

def _fmt_strike(k: float) -> str:
    """Render a strike for use inside a client_order_id.

    Whole-dollar strikes render as the integer (e.g. 480.0 -> "480");
    fractional strikes render with one decimal (e.g. 480.5 -> "480.5").
    """
    k = float(k)
    if abs(k - round(k)) < 1e-6:
        return str(int(round(k)))
    return f"{k:.1f}"


def _parse_hhmm(s: str) -> time:
    """Parse "HH:MM" → datetime.time. Raises ValueError on bad input."""
    hh, mm = s.strip().split(":")
    return time(int(hh), int(mm))


def _normalize_broker_status(raw: str) -> str:
    """Map Alpaca's order-status strings onto our STATUS_* constants.

    Anything not explicitly mapped (e.g. "new", "accepted",
    "pending_new", "accepted_for_bidding") is treated as SUBMITTED so
    the reconcile pass decides the actual fill state.
    """
    if raw in ("filled",):
        return STATUS_FILLED
    if raw in ("partially_filled",):
        return STATUS_PARTIAL
    if raw in ("rejected", "canceled", "expired"):
        return STATUS_REJECTED_EXCEPTION
    return STATUS_SUBMITTED
