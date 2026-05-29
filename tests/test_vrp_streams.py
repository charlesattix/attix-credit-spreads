"""Tests for compass.live.vrp_streams — per-stream signal → order intent (PR-B)."""

from __future__ import annotations

import pytest

from compass.live.vrp_contracts import STREAM_SPECS, StreamStatus
from compass.live.vrp_streams import (
    CreditSpreadStream,
    InactiveStream,
    build_default_registry,
)
from tests.vrp_fixtures import make_snapshot


def _spy_stream() -> CreditSpreadStream:
    return CreditSpreadStream(STREAM_SPECS["exp1220"])


# ── credit-spread entry signal ────────────────────────────────────────────────

def test_credit_spread_emits_bull_put_intent():
    snap = make_snapshot(vix=18.0)
    res = _spy_stream().generate(snap, capital=10_000.0)
    assert res.status == "entered"
    assert len(res.intents) == 1
    intent = res.intents[0]
    assert intent.structure == "bull_put"
    assert intent.symbol == "SPY"
    # legs: sell short put (higher strike) + buy long put (lower strike), $5 wide.
    sell = next(leg for leg in intent.legs if leg.side == "sell")
    buy = next(leg for leg in intent.legs if leg.side == "buy")
    assert sell.right == "P" and buy.right == "P"
    assert sell.strike - buy.strike == pytest.approx(5.0)
    assert intent.est_credit > 0
    assert intent.est_max_loss == pytest.approx((sell.strike - buy.strike) - intent.est_credit)


def test_short_strike_is_near_five_pct_otm():
    snap = make_snapshot(spots={"SPY": 500.0}, vix=18.0)
    res = _spy_stream().generate(snap, capital=10_000.0)
    sell = next(leg for leg in res.intents[0].legs if leg.side == "sell")
    # otm_pct=0.95 of 500 → ~475.
    assert sell.strike == pytest.approx(475.0, abs=2.0)


def test_contracts_scale_with_capital():
    snap = make_snapshot(spots={"SPY": 500.0}, vix=18.0)
    stream = _spy_stream()
    # per-spread risk = (5 - 1.5) * 100 = $350 with the fixture's pricing.
    res_small = stream.generate(snap, capital=350.0)
    res_big = stream.generate(snap, capital=3_500.0)
    assert res_small.intents[0].contracts == 1
    assert res_big.intents[0].contracts == 10


def test_capital_below_one_spread_yields_no_capital():
    snap = make_snapshot(spots={"SPY": 500.0}, vix=18.0)
    res = _spy_stream().generate(snap, capital=100.0)  # < $350 one-spread risk
    assert res.status == "no_capital"
    assert res.intents == []


def test_zero_capital_no_entry():
    res = _spy_stream().generate(make_snapshot(vix=18.0), capital=0.0)
    assert res.status == "no_capital"


def test_vix_gate_blocks_entry():
    snap = make_snapshot(spots={"SPY": 500.0}, vix=45.0)  # > vix_max_entry 40
    res = _spy_stream().generate(snap, capital=10_000.0)
    assert res.status == "vix_gated"
    assert res.intents == []


def test_missing_chain_degrades():
    # Snapshot without SPY (e.g. provider dropped it) → degraded, no crash.
    snap = make_snapshot(spots={"QQQ": 430.0}, vix=18.0)
    res = _spy_stream().generate(snap, capital=10_000.0)
    assert res.status == "degraded"


def test_credit_spread_rejects_non_tradeable_spec():
    with pytest.raises(ValueError):
        CreditSpreadStream(STREAM_SPECS["gld_cal"])  # BLOCKED spec


# ── inactive streams ──────────────────────────────────────────────────────────

def test_blocked_stream_reports_blocked():
    res = InactiveStream(STREAM_SPECS["gld_cal"]).generate(make_snapshot(), capital=10_000.0)
    assert res.status == "blocked"
    assert res.intents == []


def test_deferred_stream_reports_deferred():
    res = InactiveStream(STREAM_SPECS["v5_hedge"]).generate(make_snapshot(), capital=10_000.0)
    assert res.status == "deferred"
    assert res.intents == []


def test_inactive_rejects_tradeable_spec():
    with pytest.raises(ValueError):
        InactiveStream(STREAM_SPECS["exp1220"])


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_has_all_eight_with_correct_types():
    reg = build_default_registry()
    assert tuple(reg.keys()) == tuple(STREAM_SPECS.keys())
    for sid, gen in reg.items():
        if STREAM_SPECS[sid].status is StreamStatus.TRADEABLE:
            assert isinstance(gen, CreditSpreadStream)
        else:
            assert isinstance(gen, InactiveStream)
