"""Tests for compass.live.vrp_sinks — order sinks + per-stream tagging (PR-B)."""

from __future__ import annotations

import pytest

from compass.live.vrp_contracts import OrderIntent, OrderLeg
from compass.live.vrp_sinks import (
    AlpacaOrderSink,
    RecordingOrderSink,
    stream_client_order_id,
)
from tests.vrp_fixtures import MockAlpacaProvider


def _bull_put(stream="exp1220", symbol="SPY", contracts=2) -> OrderIntent:
    return OrderIntent(
        stream=stream, symbol=symbol, structure="bull_put",
        legs=(
            OrderLeg("sell", "option", f"{symbol}260612P00475000", contracts, strike=475.0,
                     expiration="2026-06-12", right="P"),
            OrderLeg("buy", "option", f"{symbol}260612P00470000", contracts, strike=470.0,
                     expiration="2026-06-12", right="P"),
        ),
        contracts=contracts, est_credit=1.5, est_max_loss=3.5,
    )


def test_client_order_id_is_deterministic_and_stream_tagged():
    intent = _bull_put()
    coid = stream_client_order_id(intent)
    assert coid == stream_client_order_id(intent)          # deterministic
    assert coid.startswith("vrp-exp1220-SPY-2026-06-12-")   # stream + symbol + exp tagged
    assert "470" in coid and "475" in coid


def test_recording_sink_records_without_placing():
    sink = RecordingOrderSink()
    intent = _bull_put()
    result = sink.submit(intent)
    assert result["status"] == "recorded"
    assert result["stream"] == "exp1220"
    assert sink.submitted == [intent]


def test_alpaca_sink_maps_bull_put_to_provider_call():
    provider = MockAlpacaProvider()
    sink = AlpacaOrderSink(provider)
    result = sink.submit(_bull_put(contracts=3))
    assert result["status"] == "submitted"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["ticker"] == "SPY"
    assert call["short_strike"] == 475.0
    assert call["long_strike"] == 470.0
    assert call["spread_type"] == "bull_put"
    assert call["contracts"] == 3
    assert call["limit_price"] == 1.5
    assert call["client_order_id"].startswith("vrp-exp1220-")


def test_alpaca_sink_rejects_unsupported_structure():
    provider = MockAlpacaProvider()
    sink = AlpacaOrderSink(provider)
    shares = OrderIntent(
        stream="v5_hedge", symbol="TLT", structure="long_shares",
        legs=(OrderLeg("buy", "equity", "TLT", 100),), contracts=100,
    )
    with pytest.raises(NotImplementedError):
        sink.submit(shares)
    assert provider.calls == []
