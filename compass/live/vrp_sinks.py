"""compass/live/vrp_sinks.py — order sinks for the VRP engine (PR-B).

An :class:`~compass.live.vrp_contracts.OrderSink` turns a broker-agnostic
:class:`OrderIntent` into either a recorded plan (tests / dry-run) or a live
Alpaca order. The engine depends only on the ``OrderSink`` protocol, so
``signal → sizing → intent`` is fully testable without Alpaca.

  * :class:`RecordingOrderSink` — records intents, places nothing. Default for
    tests and for a dry-run cycle.
  * :class:`AlpacaOrderSink` — maps credit-spread intents to
    ``AlpacaProvider.submit_credit_spread``. It is **not auto-wired** anywhere;
    it is constructed explicitly only at the PR-E cutover. Each order carries a
    deterministic ``client_order_id`` that tags the fill to its stream — the
    attribution hook cc3's live covariance (build-plan PR-I) consumes.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from compass.live.vrp_contracts import OrderIntent

logger = logging.getLogger(__name__)

_SUPPORTED_SPREADS = ("bull_put", "bear_call")


def stream_client_order_id(intent: OrderIntent) -> str:
    """Deterministic per-stream client order id (idempotency + PnL attribution).

    Format: ``vrp-<stream>-<symbol>-<exp>-<shortK>-<longK>``. Stable for a given
    intent so retries dedupe and fills can be attributed to their stream even
    before the ``trades.stream`` DB column (build-plan PR-I) exists.
    """
    legs = sorted(intent.legs, key=lambda leg_: (leg_.side, leg_.strike or 0.0))
    strikes = "-".join(f"{leg_.strike:g}" for leg_ in legs if leg_.strike is not None)
    exp = next((leg_.expiration for leg_ in intent.legs if leg_.expiration), "na")
    return f"vrp-{intent.stream}-{intent.symbol}-{exp}-{strikes}"


class RecordingOrderSink:
    """Records intents instead of placing orders. Safe for tests / dry-runs."""

    def __init__(self) -> None:
        self.submitted: List[OrderIntent] = []

    def submit(self, intent: OrderIntent) -> Dict[str, object]:
        self.submitted.append(intent)
        return {
            "status": "recorded",
            "stream": intent.stream,
            "symbol": intent.symbol,
            "structure": intent.structure,
            "contracts": intent.contracts,
            "client_order_id": stream_client_order_id(intent),
        }


class AlpacaOrderSink:
    """Submits credit-spread intents via an injected ``AlpacaProvider``.

    Constructed explicitly at the PR-E cutover — never instantiated by the engine
    by default. Only credit-spread structures are supported in PR-B; other
    structures (shares, calendars, cross-vol) raise ``NotImplementedError`` until
    their engines land (build-plan PR-D / futures decision).
    """

    def __init__(self, provider) -> None:
        # provider: strategy.alpaca_provider.AlpacaProvider (duck-typed for tests)
        self._provider = provider

    def submit(self, intent: OrderIntent) -> Dict[str, object]:
        if intent.structure not in _SUPPORTED_SPREADS:
            raise NotImplementedError(
                f"AlpacaOrderSink supports {_SUPPORTED_SPREADS}, not '{intent.structure}' "
                f"(stream {intent.stream}). Equity/calendar/cross-vol execution is a later PR."
            )
        short_strike = _leg_strike(intent, "sell")
        long_strike = _leg_strike(intent, "buy")
        expiration = next((leg_.expiration for leg_ in intent.legs if leg_.expiration), None)
        if short_strike is None or long_strike is None or expiration is None:
            return {"status": "error", "message": "intent missing strikes/expiration", "stream": intent.stream}

        coid = stream_client_order_id(intent)
        logger.info("[vrp] submit %s %s %s/%s exp %s x%d (coid=%s)",
                    intent.stream, intent.symbol, short_strike, long_strike,
                    expiration, intent.contracts, coid)
        result = self._provider.submit_credit_spread(
            ticker=intent.symbol,
            short_strike=short_strike,
            long_strike=long_strike,
            expiration=expiration,
            spread_type=intent.structure,
            contracts=intent.contracts,
            limit_price=intent.est_credit,
            client_order_id=coid,
        )
        if isinstance(result, dict):
            result.setdefault("stream", intent.stream)
            result.setdefault("client_order_id", coid)
        return result


def _leg_strike(intent: OrderIntent, side: str) -> Optional[float]:
    for leg_ in intent.legs:
        if leg_.side == side and leg_.strike is not None:
            return float(leg_.strike)
    return None
