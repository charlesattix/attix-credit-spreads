"""Tests for compass.live.vrp_contracts — stream specs + intent dataclasses (PR-B)."""

from __future__ import annotations

from compass.live.vrp_contracts import (
    STREAM_SPECS,
    VRP_STREAMS,
    CyclePlan,
    OrderIntent,
    OrderLeg,
    StreamResult,
    StreamStatus,
)
from compass.live.vrp_risk_parity import VRP_STREAMS as RP_STREAMS


def test_stream_specs_cover_all_eight_in_canonical_order():
    assert tuple(STREAM_SPECS.keys()) == VRP_STREAMS
    assert len(VRP_STREAMS) == 8


def test_canonical_order_matches_allocator_module():
    # Engine and cc3 allocator MUST agree on stream ordering (positional cov/weights).
    assert VRP_STREAMS == RP_STREAMS


def test_stream_tradeability_classification():
    tradeable = {s for s, sp in STREAM_SPECS.items() if sp.status is StreamStatus.TRADEABLE}
    blocked = {s for s, sp in STREAM_SPECS.items() if sp.status is StreamStatus.BLOCKED}
    deferred = {s for s, sp in STREAM_SPECS.items() if sp.status is StreamStatus.DEFERRED}
    assert tradeable == {"exp1220", "xlf_cs", "xli_cs", "qqq_cs"}
    assert blocked == {"gld_cal", "slv_cal"}      # ETF-vs-futures basis, no Alpaca futures
    assert deferred == {"v5_hedge", "cross_vol"}  # signal port pending PR-D


def test_blocked_streams_are_futures_basis():
    for sid in ("gld_cal", "slv_cal"):
        spec = STREAM_SPECS[sid]
        assert spec.structure == "etf_future_basis"
        assert any("=F" in s for s in spec.symbols)  # references a futures symbol


def test_order_intent_and_leg_construct():
    intent = OrderIntent(
        stream="exp1220", symbol="SPY", structure="bull_put",
        legs=(
            OrderLeg("sell", "option", "SPY260612P00475000", 2, strike=475.0, expiration="2026-06-12", right="P"),
            OrderLeg("buy", "option", "SPY260612P00470000", 2, strike=470.0, expiration="2026-06-12", right="P"),
        ),
        contracts=2, est_credit=1.5, est_max_loss=3.5,
    )
    assert intent.contracts == 2
    assert len(intent.legs) == 2
    assert intent.legs[0].side == "sell"


def test_cycle_plan_traded_streams():
    plan = CyclePlan(as_of=None, account_equity=100_000.0, vix_exposure=1.0, capital={})
    plan.intents.append(OrderIntent("exp1220", "SPY", "bull_put", (), 1))
    plan.intents.append(OrderIntent("qqq_cs", "QQQ", "bull_put", (), 1))
    assert plan.traded_streams == ["exp1220", "qqq_cs"]


def test_stream_result_defaults():
    r = StreamResult("xlf_cs")
    assert r.intents == []
    assert r.status == "no_entry"
