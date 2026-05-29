"""Tests for compass.live.vrp_strategy — VRPMultiStreamStrategy (PR-B).

Exercises the full signal → sizing → order-intent path with in-memory stand-ins
for cc2 (data), cc3 (allocator, real math), cc4 (ladder). No live Alpaca.
"""

from __future__ import annotations

import pytest

from compass.live.vrp_sinks import AlpacaOrderSink, RecordingOrderSink
from compass.live.vrp_strategy import VRPMultiStreamStrategy
from tests.vrp_fixtures import (
    FakeFeed,
    FixedVixExposure,
    MockAlpacaProvider,
    make_snapshot,
)

EQUITY = 100_000.0


def _strategy(snapshot, vix_mult=1.0, equity=EQUITY) -> VRPMultiStreamStrategy:
    return VRPMultiStreamStrategy(
        FakeFeed(snapshot),
        account_equity=equity,
        vix_provider=FixedVixExposure(vix_mult),
    )


def test_active_streams_are_the_four_credit_spreads():
    strat = _strategy(make_snapshot())
    assert set(strat.active_streams) == {"exp1220", "xlf_cs", "xli_cs", "qqq_cs"}


def test_plan_cycle_allocates_over_active_streams_and_emits_intents():
    strat = _strategy(make_snapshot(vix=18.0), vix_mult=1.0)
    plan = strat.plan_cycle()

    # capital only over the 4 active streams; cold-start prior → equal 25% each.
    assert set(plan.capital.keys()) == {"exp1220", "xlf_cs", "xli_cs", "qqq_cs"}
    assert all(v > 0 for v in plan.capital.values())
    assert sum(plan.capital.values()) == pytest.approx(EQUITY, rel=0.01)  # scale≈1, vix=1

    # all four tradeable streams find a spread (deep fixture chains).
    assert set(plan.traded_streams) == {"exp1220", "xlf_cs", "xli_cs", "qqq_cs"}
    # status recorded for ALL eight streams.
    assert set(plan.stream_status.keys()) == {
        "exp1220", "v5_hedge", "gld_cal", "slv_cal", "cross_vol", "xlf_cs", "xli_cs", "qqq_cs",
    }
    assert plan.stream_status["gld_cal"].startswith("blocked")
    assert plan.stream_status["v5_hedge"].startswith("deferred")
    assert plan.stream_status["exp1220"].startswith("entered")


def test_reset_cycle_called_each_plan():
    feed = FakeFeed(make_snapshot())
    strat = VRPMultiStreamStrategy(feed, account_equity=EQUITY, vix_provider=FixedVixExposure(1.0))
    strat.plan_cycle()
    strat.plan_cycle()
    assert feed.reset_calls == 2


def test_vix_ladder_zero_halts_new_entries():
    strat = _strategy(make_snapshot(vix=18.0), vix_mult=0.0)
    plan = strat.plan_cycle()
    assert plan.capital == {}
    assert plan.intents == []
    assert any("no new entries" in n.lower() for n in plan.notes)
    # active streams report no_capital (allocator gave them nothing).
    assert plan.stream_status["exp1220"].startswith("no_capital")


def test_ladder_multiplier_scales_capital():
    full = _strategy(make_snapshot(vix=18.0), vix_mult=1.0).plan_cycle()
    half = _strategy(make_snapshot(vix=18.0), vix_mult=0.5).plan_cycle()
    assert sum(half.capital.values()) == pytest.approx(0.5 * sum(full.capital.values()), rel=0.01)


def test_per_stream_vix_gate_independent_of_ladder():
    # ladder mult 1.0 (capital flows) but snapshot VIX 45 → per-stream hard gate.
    strat = _strategy(make_snapshot(vix=45.0), vix_mult=1.0)
    plan = strat.plan_cycle()
    assert plan.intents == []
    assert plan.stream_status["qqq_cs"].startswith("vix_gated")


def test_degraded_symbol_skips_only_that_stream():
    # No XLF chain in the snapshot → xlf_cs degrades; the rest still trade.
    snap = make_snapshot(spots={"SPY": 500.0, "QQQ": 430.0, "XLI": 130.0}, vix=18.0)
    plan = _strategy(snap, vix_mult=1.0).plan_cycle()
    assert plan.stream_status["xlf_cs"].startswith("degraded")
    assert "exp1220" in plan.traded_streams
    assert "qqq_cs" in plan.traded_streams


def test_zero_equity_no_allocation():
    plan = _strategy(make_snapshot(vix=18.0), equity=0.0).plan_cycle()
    assert plan.capital == {}
    assert plan.intents == []


def test_account_equity_callable_is_supported():
    snap = make_snapshot(vix=18.0)
    strat = VRPMultiStreamStrategy(
        FakeFeed(snap), account_equity=lambda: 50_000.0, vix_provider=FixedVixExposure(1.0)
    )
    plan = strat.plan_cycle()
    assert sum(plan.capital.values()) == pytest.approx(50_000.0, rel=0.01)


# ── execute_cycle / order sinks ───────────────────────────────────────────────

def test_execute_cycle_defaults_to_recording_sink_never_live():
    strat = _strategy(make_snapshot(vix=18.0), vix_mult=1.0)
    plan, results = strat.execute_cycle()  # no sink → RecordingOrderSink
    assert len(results) == len(plan.intents) > 0
    assert all(r["status"] == "recorded" for r in results)


def test_execute_cycle_with_recording_sink_records_all_intents():
    strat = _strategy(make_snapshot(vix=18.0), vix_mult=1.0)
    sink = RecordingOrderSink()
    plan, _ = strat.execute_cycle(sink=sink)
    assert sink.submitted == plan.intents


def test_execute_cycle_with_alpaca_sink_submits_each_intent():
    strat = _strategy(make_snapshot(vix=18.0), vix_mult=1.0)
    provider = MockAlpacaProvider()
    plan, results = strat.execute_cycle(sink=AlpacaOrderSink(provider))
    assert len(provider.calls) == len(plan.intents) > 0
    assert all(c["spread_type"] == "bull_put" for c in provider.calls)
    # each order carries a unique per-stream client id.
    coids = {c["client_order_id"] for c in provider.calls}
    assert len(coids) == len(provider.calls)
