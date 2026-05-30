"""Tests for compass.live.executor_order_sink — drop-in OrderSink for EXP-V8A
that routes intents through the standalone Executor REST service.

No real HTTP: we inject a ``FakeHttp`` session into ``ExecutorClient`` so the
tests run hermetically and the same fake is reusable for the runner-level
sink-switch test in ``test_vrp_runner_sink_switch``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from compass.live.executor_order_sink import (
    ExecutorClient,
    ExecutorHTTPError,
    ExecutorOrderSink,
    _sanitize_idempotency_key,
)
from compass.live.vrp_contracts import OrderIntent, OrderLeg


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = "" if body is None else str(body)

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeHttp:
    """Records every request the sink makes and replies from a programmable
    queue. ``queue`` is a list of ``(status_code, body)`` tuples consumed FIFO.

    Auto-serves the CSRF token GET (out-of-band so tests don't have to script
    it) — recorded into ``self.csrf_calls`` for assertion if needed, while
    ``self.calls`` keeps only the test-relevant write requests.
    """

    def __init__(self, queue: Optional[List] = None) -> None:
        self.queue: List = list(queue or [])
        self.calls: List[Dict[str, Any]] = []
        self.csrf_calls: List[Dict[str, Any]] = []
        self.headers: Dict[str, str] = {}

    def request(self, method: str, url: str, **kw) -> _FakeResponse:
        # CSRF token fetch — auto-serve so unit tests stay focused on the write
        # they care about. Real client makes ONE of these on first write.
        if method == "GET" and url.endswith("/auth/csrf-token"):
            self.csrf_calls.append({"method": method, "url": url})
            return _FakeResponse(200, {
                "csrf_token": "fake-csrf-token", "header_name": "X-CSRF-Token",
                "expires_in_seconds": 86400, "message": None,
            })
        self.calls.append({
            "method": method, "url": url,
            "json": kw.get("json"), "params": kw.get("params"),
            "headers": kw.get("headers") or {},
            "timeout": kw.get("timeout"),
        })
        if not self.queue:
            return _FakeResponse(200, {"ok": True})
        status, body = self.queue.pop(0)
        return _FakeResponse(status, body)


def _bull_put(
    *, stream: str = "exp1220", symbol: str = "SPY",
    contracts: int = 3, est_credit: Optional[float] = 1.5,
    short_strike: float = 475.0, long_strike: float = 470.0,
) -> OrderIntent:
    return OrderIntent(
        stream=stream, symbol=symbol, structure="bull_put",
        legs=(
            OrderLeg("sell", "option", f"{symbol}260612P00475000",
                     contracts, strike=short_strike,
                     expiration="2026-06-12", right="P"),
            OrderLeg("buy", "option", f"{symbol}260612P00470000",
                     contracts, strike=long_strike,
                     expiration="2026-06-12", right="P"),
        ),
        contracts=contracts, est_credit=est_credit, est_max_loss=3.5,
        rationale="vrp test signal",
    )


def _build_sink(http: FakeHttp, *, account_id: str = "ibkr_paper") -> ExecutorOrderSink:
    client = ExecutorClient(
        "http://exec.local", "test-key", timeout=5.0, http=http,
    )
    return ExecutorOrderSink(client, account_id=account_id, account_type="paper")


# ── tests ────────────────────────────────────────────────────────────────────

def test_sanitize_idempotency_key_keeps_safe_chars_and_replaces_dot():
    raw = "vrp-exp1220-SPY-2026-06-12-475.5-470.5"
    out = _sanitize_idempotency_key(raw)
    assert "." not in out
    assert out == "vrp-exp1220-SPY-2026-06-12-475_5-470_5"
    # length cap
    assert len(_sanitize_idempotency_key("x" * 500)) == 255


def test_submit_bull_put_posts_correct_spread_body():
    http = FakeHttp(queue=[(200, {
        "success": True, "order_id": "ord-1", "broker_order_id": "bk-1",
        "message": "submitted", "status": "open", "symbol": "SPY",
        "quantity": 3, "filled_quantity": 0,
        "timestamp": "2026-05-30T20:00:00Z",
        "leg_con_ids": [111, 222],
    })])
    sink = _build_sink(http)
    intent = _bull_put()

    result = sink.submit(intent)

    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://exec.local/v1/orders/spread"
    body = call["json"]
    assert body["account_id"] == "ibkr_paper"
    assert body["account_type"] == "paper"
    assert body["strategy"] == "bull_put_spread"
    assert body["order_type"] == "limit"
    assert body["net_credit"] == 1.5
    assert body["time_in_force"] == "day"

    legs = body["legs"]
    assert [leg["side"] for leg in legs] == ["sell_to_open", "buy_to_open"]
    assert [leg["option_type"] for leg in legs] == ["put", "put"]
    assert [leg["strike"] for leg in legs] == [475.0, 470.0]
    assert all(leg["symbol"] == "SPY" for leg in legs)
    assert all(leg["expiration"] == "2026-06-12" for leg in legs)
    assert all(leg["quantity"] == 3 for leg in legs)

    src = body["source"]
    assert src["model"] == "vrp_v8a"
    assert src["signal_id"] == "exp1220"
    assert src["metadata"]["stream"] == "exp1220"

    # idempotency_key is the sanitized coid — deterministic per intent
    assert body["idempotency_key"].startswith("vrp-exp1220-SPY-2026-06-12-")

    # Normalized response surface
    assert result["status"] == "submitted"
    assert result["order_id"] == "ord-1"
    assert result["broker_order_id"] == "bk-1"
    assert result["stream"] == "exp1220"
    assert result["ticker"] == "SPY"
    assert result["spread_type"] == "bull_put"
    assert result["contracts"] == 3
    assert result["client_order_id"].startswith("vrp-exp1220-SPY-2026-06-12-")
    assert result["leg_con_ids"] == [111, 222]


def test_submit_bear_call_maps_to_call_legs():
    http = FakeHttp(queue=[(200, {
        "success": True, "order_id": "ord-2", "message": "ok",
        "status": "open", "symbol": "QQQ", "quantity": 1,
        "timestamp": "2026-05-30T20:00:00Z",
    })])
    sink = _build_sink(http)
    intent = OrderIntent(
        stream="qqq_cs", symbol="QQQ", structure="bear_call",
        legs=(
            OrderLeg("sell", "option", "QQQ260612C00500000", 1,
                     strike=500.0, expiration="2026-06-12", right="C"),
            OrderLeg("buy", "option", "QQQ260612C00505000", 1,
                     strike=505.0, expiration="2026-06-12", right="C"),
        ),
        contracts=1, est_credit=0.85,
    )

    sink.submit(intent)

    body = http.calls[0]["json"]
    assert body["strategy"] == "bear_call_spread"
    assert [leg["option_type"] for leg in body["legs"]] == ["call", "call"]
    assert [leg["strike"] for leg in body["legs"]] == [500.0, 505.0]


def test_submit_without_est_credit_emits_market_order():
    http = FakeHttp(queue=[(200, {
        "success": True, "order_id": "ord-3", "message": "ok",
        "status": "open", "symbol": "SPY", "quantity": 1,
        "timestamp": "2026-05-30T20:00:00Z",
    })])
    sink = _build_sink(http)
    sink.submit(_bull_put(est_credit=None, contracts=1))
    body = http.calls[0]["json"]
    assert body["order_type"] == "market"
    assert "net_credit" not in body


def test_submit_rejects_unsupported_structure():
    http = FakeHttp()
    sink = _build_sink(http)
    shares = OrderIntent(
        stream="v5_hedge", symbol="TLT", structure="long_shares",
        legs=(OrderLeg("buy", "equity", "TLT", 100),), contracts=100,
    )
    with pytest.raises(NotImplementedError):
        sink.submit(shares)
    assert http.calls == []


def test_submit_returns_error_dict_when_strikes_missing():
    http = FakeHttp()
    sink = _build_sink(http)
    bad = OrderIntent(
        stream="exp1220", symbol="SPY", structure="bull_put",
        legs=(OrderLeg("sell", "option", "X", 1, strike=None,
                       expiration="2026-06-12"),),
        contracts=1,
    )
    out = sink.submit(bad)
    assert out["status"] == "error"
    assert "missing" in out["message"]
    assert http.calls == []


def test_submit_returns_error_dict_when_http_4xx():
    http = FakeHttp(queue=[(400, {"error": "bad strike"})])
    sink = _build_sink(http)
    out = sink.submit(_bull_put())
    assert out["status"] == "error"
    assert out["http_status"] == 400
    assert "bad strike" in out["message"]
    assert out["stream"] == "exp1220"
    assert out["client_order_id"].startswith("vrp-exp1220-")


def test_cancel_order_hits_delete_endpoint():
    http = FakeHttp(queue=[(200, {"success": True})])
    sink = _build_sink(http)
    sink.cancel_order("ord-abc")
    call = http.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"] == "http://exec.local/v1/orders/ord-abc"
    assert call["params"] == {"account_id": "ibkr_paper"}


def test_get_order_status_hits_status_endpoint():
    http = FakeHttp(queue=[(200, {"status": "filled", "filled_quantity": 3})])
    sink = _build_sink(http)
    out = sink.get_order_status("ord-abc")
    assert out["status"] == "filled"
    assert http.calls[0]["url"].endswith("/v1/orders/ord-abc/status")
    assert http.calls[0]["params"] == {"account_id": "ibkr_paper"}


def test_get_balance_returns_total_equity():
    http = FakeHttp(queue=[(200, {
        "total_equity": 100_000.0, "cash": 50_000.0,
        "buying_power": 200_000.0, "unrealized_pnl": 0.0,
        "realized_pnl_today": 0.0, "positions_count": 0,
    })])
    sink = _build_sink(http)
    bal = sink.get_balance()
    assert bal["total_equity"] == 100_000.0
    assert http.calls[0]["url"].endswith("/v1/portfolio/balance")


def test_get_positions_unwraps_list_or_dict():
    http = FakeHttp(queue=[
        (200, [{"symbol": "SPY", "quantity": 1}]),
    ])
    sink = _build_sink(http)
    out = sink.get_positions()
    assert len(out) == 1 and out[0]["symbol"] == "SPY"


def test_set_callback_url_patches_account():
    http = FakeHttp(queue=[(200, {"success": True})])
    sink = _build_sink(http)
    sink.set_callback_url("http://hooks.local/fills")
    call = http.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/v1/gateways/accounts/ibkr_paper/callback-url")
    assert call["json"] == {"callback_url": "http://hooks.local/fills"}


def test_from_env_requires_api_key_and_account(monkeypatch):
    monkeypatch.delenv("EXECUTOR_API_KEY", raising=False)
    monkeypatch.delenv("EXECUTOR_ACCOUNT_ID", raising=False)
    with pytest.raises(RuntimeError, match="EXECUTOR_API_KEY"):
        ExecutorOrderSink.from_env(http=FakeHttp())
    monkeypatch.setenv("EXECUTOR_API_KEY", "k")
    with pytest.raises(RuntimeError, match="EXECUTOR_ACCOUNT_ID"):
        ExecutorOrderSink.from_env(http=FakeHttp())


def test_from_env_builds_sink_with_paper_default(monkeypatch):
    monkeypatch.setenv("EXECUTOR_API_KEY", "k")
    monkeypatch.setenv("EXECUTOR_ACCOUNT_ID", "ibkr_paper")
    monkeypatch.delenv("EXECUTOR_ACCOUNT_TYPE", raising=False)
    sink = ExecutorOrderSink.from_env(http=FakeHttp())
    assert sink.account_id == "ibkr_paper"
    assert sink.account_type == "paper"


def test_executor_client_rejects_missing_creds():
    with pytest.raises(ValueError):
        ExecutorClient("", "k", http=FakeHttp())
    with pytest.raises(ValueError):
        ExecutorClient("http://x", "", http=FakeHttp())


def test_executor_http_error_carries_status_code():
    http = FakeHttp(queue=[(500, {"error": "boom"})])
    client = ExecutorClient("http://x", "k", http=http)
    with pytest.raises(ExecutorHTTPError) as exc:
        client.submit_spread({"x": 1})
    assert exc.value.status_code == 500


def test_first_write_fetches_and_caches_csrf_token():
    http = FakeHttp(queue=[
        (200, {"success": True, "order_id": "o-1", "message": "ok",
               "status": "open", "symbol": "SPY", "quantity": 1,
               "timestamp": "2026-05-30T20:00:00Z"}),
        (200, {"success": True, "order_id": "o-2", "message": "ok",
               "status": "open", "symbol": "SPY", "quantity": 1,
               "timestamp": "2026-05-30T20:00:00Z"}),
    ])
    sink = _build_sink(http)
    sink.submit(_bull_put())
    sink.submit(_bull_put(stream="qqq_cs"))
    # CSRF GET fired exactly ONCE for both writes — token is cached.
    assert len(http.csrf_calls) == 1
    assert http.calls[0]["headers"].get("X-CSRF-Token") == "fake-csrf-token"
    assert http.calls[1]["headers"].get("X-CSRF-Token") == "fake-csrf-token"
