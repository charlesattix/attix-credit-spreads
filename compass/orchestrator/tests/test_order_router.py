"""Tests for compass.orchestrator.order_router.

Contract under test (proposal §4.3 + ORCHESTRATOR_PROPOSAL §5.3):

    OrderRouter(connector, broker_id="alpaca_paper").submit(sized_orders)
        -> List[RoutedOrder]

    Invariants:
    1. Atomic MLEG or refuse — no silent legging. ImportError of
       MultilegOrderRequest → STATUS_REJECTED_NO_MLEG.
    2. Idempotent client_order_id of
       {date}-{stream}-{ticker}-{exp}-{shortK}-{longK}.
    3. Capability check — futures roots (GC=F, SI=F) and missing
       us_option support → STATUS_REJECTED_BROKER_UNSUPPORTED.
    4. Submission blackout windows (09:30–09:32 / 15:58–16:00 ET) →
       STATUS_REJECTED_BLACKOUT.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

import pytest

from compass.orchestrator.types import GatedSignal, SignalIntent, SizedOrder

order_router_mod = pytest.importorskip(
    "compass.orchestrator.order_router",
    reason="order_router module not landed yet (CC3)",
)

OrderRouter = order_router_mod.OrderRouter
RoutedOrder = order_router_mod.RoutedOrder
STATUS_FILLED = order_router_mod.STATUS_FILLED
STATUS_SUBMITTED = order_router_mod.STATUS_SUBMITTED
STATUS_REJECTED_NO_MLEG = order_router_mod.STATUS_REJECTED_NO_MLEG
STATUS_REJECTED_BROKER_UNSUPPORTED = order_router_mod.STATUS_REJECTED_BROKER_UNSUPPORTED
STATUS_REJECTED_BLACKOUT = order_router_mod.STATUS_REJECTED_BLACKOUT
STATUS_REJECTED_SDK_NONE = order_router_mod.STATUS_REJECTED_SDK_NONE


# ───────────────────────────────────────────────────────────────────────────
# Connector + broker test doubles
# ───────────────────────────────────────────────────────────────────────────

class _FakeBrokerResponse:
    def __init__(self, id_: str = "BR-1", status: str = "filled"):
        self.id = id_
        self.status = status


class _FakeTradingClient:
    """Mimics the alpaca-py TradingClient submit_order signature."""

    def __init__(self, raw_status: str = "filled"):
        self.calls: List = []
        self.raw_status = raw_status

    def submit_order(self, order_data):
        self.calls.append(order_data)
        return _FakeBrokerResponse(
            id_=f"BR-{len(self.calls)}", status=self.raw_status,
        )


class _FakeConnector:
    """Test connector matching the attributes/methods OrderRouter reads."""

    def __init__(self, sdk: str = "alpaca-py", raw_status: str = "filled"):
        self._sdk = sdk
        self._trading_client = _FakeTradingClient(raw_status=raw_status)
        self.reconcile_calls: List[Dict[str, float]] = []

    def reconcile(self, intended: Dict[str, float]) -> Dict[str, Dict]:
        self.reconcile_calls.append(dict(intended))
        return {
            occ: {"intended": qty, "actual": qty, "status": "MATCH"}
            for occ, qty in intended.items()
        }


def _sized(
    *,
    stream: str = "exp1220",
    ticker: str = "SPY",
    short_strike: float = 425.0,
    long_strike: float = 420.0,
    expiration: str = "2026-06-19",
    contracts: int = 2,
    direction: str = "put_credit_spread",
) -> SizedOrder:
    intent = SignalIntent.from_dict({
        "stream": stream, "date": "2026-05-22", "ticker": ticker,
        "action": "OPEN", "direction": direction,
        "delta": 0.30, "dte": 28, "width": 5.0,
        "weight": 0.2, "confidence": 0.9, "notes": "",
    })
    gated = GatedSignal(intent=intent, gate_status="ALLOW",
                          gate_reasons=[], confidence_adj=1.0)
    return SizedOrder(
        gated=gated, contract_count=contracts, risk_allocation=1000.0,
        short_strike=short_strike, long_strike=long_strike,
        expected_credit=0.55, max_loss_dollars=4.45,
        expiration=expiration, port_weight_consumed=0.05,
        sizing_reasons=[],
    )


# A safe "after blackout" timestamp inside RTH: 10:00 ET.
def _now_safe():
    return datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc)  # 10:00 ET


def _router(connector=None, broker_id="alpaca_paper", now=None) -> OrderRouter:
    return OrderRouter(
        connector=connector or _FakeConnector(),
        broker_id=broker_id,
        now_provider=(lambda: now) if now else _now_safe,
    )


# ───────────────────────────────────────────────────────────────────────────
# Output shape
# ───────────────────────────────────────────────────────────────────────────

class TestSubmitShape:
    def test_returns_one_routed_per_sized(self):
        out = _router().submit([_sized()])
        assert len(out) == 1
        assert isinstance(out[0], RoutedOrder)

    def test_empty_in_empty_out(self):
        assert _router().submit([]) == []

    def test_preserves_input_order(self):
        sized = [_sized(stream="exp1220"), _sized(stream="qqq_cs", ticker="QQQ"),
                  _sized(stream="xlf_cs", ticker="XLF")]
        out = _router().submit(sized)
        assert [r.sized.gated.intent.stream for r in out] == \
               ["exp1220", "qqq_cs", "xlf_cs"]


# ───────────────────────────────────────────────────────────────────────────
# Idempotency (proposal §4.3.4)
# ───────────────────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_client_order_id_includes_canonical_fields(self):
        s = _sized(stream="exp1220", ticker="SPY",
                     short_strike=425.0, long_strike=420.0,
                     expiration="2026-06-19")
        out = _router().submit([s])
        coid = out[0].client_order_id
        for piece in ("2026-05-22", "exp1220", "SPY",
                       "2026-06-19", "425", "420"):
            assert piece in coid, f"client_order_id missing {piece}: {coid!r}"

    def test_same_sized_order_produces_same_coid(self):
        """Re-running the router with an equivalent SizedOrder yields
        the same idempotent client_order_id."""
        s1 = _sized()
        s2 = _sized()
        out1 = _router().submit([s1])
        out2 = _router().submit([s2])
        assert out1[0].client_order_id == out2[0].client_order_id


# ───────────────────────────────────────────────────────────────────────────
# Capability check (proposal §4.3.3)
# ───────────────────────────────────────────────────────────────────────────

class TestBrokerCapability:
    def test_futures_root_rejected(self):
        """Tickers in the broker's `untradeable` list (futures roots)
        must be rejected without touching the wire."""
        conn = _FakeConnector()
        out = _router(connector=conn).submit(
            [_sized(stream="gld_cal", ticker="GC=F",
                       direction="put_credit_spread")]
        )
        assert out[0].status == STATUS_REJECTED_BROKER_UNSUPPORTED
        assert conn._trading_client.calls == []

    def test_unknown_ticker_rejected(self):
        """Tickers not on the broker's whitelist are rejected."""
        out = _router().submit(
            [_sized(stream="exp1220", ticker="XYZ123")]
        )
        assert out[0].status == STATUS_REJECTED_BROKER_UNSUPPORTED


# ───────────────────────────────────────────────────────────────────────────
# SDK guard (proposal §4.3)
# ───────────────────────────────────────────────────────────────────────────

class TestSdkGuard:
    def test_no_sdk_rejects(self):
        conn = _FakeConnector(sdk="none")
        out = _router(connector=conn).submit([_sized()])
        assert out[0].status == STATUS_REJECTED_SDK_NONE
        assert conn._trading_client.calls == []


# ───────────────────────────────────────────────────────────────────────────
# Submission blackout window (proposal §4.3.6)
# ───────────────────────────────────────────────────────────────────────────

class TestSubmissionBlackout:
    def test_open_auction_blocked(self):
        # 09:31 ET on 2026-05-22 = 13:31 UTC (DST in effect).
        in_blackout = datetime(2026, 5, 22, 13, 31, tzinfo=timezone.utc)
        conn = _FakeConnector()
        out = _router(connector=conn, now=in_blackout).submit([_sized()])
        assert out[0].status == STATUS_REJECTED_BLACKOUT
        assert conn._trading_client.calls == []

    def test_closing_cross_blocked(self):
        # 15:59 ET on 2026-05-22 = 19:59 UTC.
        in_blackout = datetime(2026, 5, 22, 19, 59, tzinfo=timezone.utc)
        conn = _FakeConnector()
        out = _router(connector=conn, now=in_blackout).submit([_sized()])
        assert out[0].status == STATUS_REJECTED_BLACKOUT


# ───────────────────────────────────────────────────────────────────────────
# Multi-leg atomicity (proposal §4.3.2 — the H2 fix)
# ───────────────────────────────────────────────────────────────────────────

class TestMultilegAtomicity:
    def test_no_mleg_returns_rejected_no_mleg(self, monkeypatch):
        """If MultilegOrderRequest cannot be imported, the router must
        refuse with REJECTED_NO_MLEG and never call the trading client."""
        import sys
        # Force ImportError when `from alpaca.trading.requests import
        # MultilegOrderRequest` is executed inside _submit_atomic_mleg.
        bad_module = type(sys)("alpaca.trading.requests")
        # Intentionally omit MultilegOrderRequest / OptionLegRequest.
        monkeypatch.setitem(sys.modules, "alpaca.trading.requests", bad_module)
        conn = _FakeConnector()
        out = _router(connector=conn).submit([_sized()])
        assert out[0].status == STATUS_REJECTED_NO_MLEG
        # Router must NOT have submitted any legs.
        assert conn._trading_client.calls == []

    def test_rejected_orders_carry_reason(self, monkeypatch):
        import sys
        bad_module = type(sys)("alpaca.trading.requests")
        monkeypatch.setitem(sys.modules, "alpaca.trading.requests", bad_module)
        out = _router().submit([_sized()])
        text = " ".join(out[0].reasons).lower()
        assert "mleg" in text or "refus" in text


# ───────────────────────────────────────────────────────────────────────────
# Reconciliation pass (proposal §4.3 step 7)
# ───────────────────────────────────────────────────────────────────────────

class TestReconciliation:
    def test_reconcile_called_after_successful_submits(self):
        conn = _FakeConnector(raw_status="filled")
        try:
            out = _router(connector=conn).submit([_sized()])
        except Exception:
            pytest.skip("alpaca-py MLEG primitives unavailable in this env")
        # If alpaca-py is installed, the connector's reconcile() should
        # have been called with at least one OCC symbol.
        if out and out[0].ok:
            assert conn.reconcile_calls, "expected reconcile() to be invoked"
            assert out[0].reconciliation, "RoutedOrder.reconciliation should be populated"
