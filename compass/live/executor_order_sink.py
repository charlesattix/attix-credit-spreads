"""compass/live/executor_order_sink.py — ExecutorOrderSink for EXP-V8A.

Drop-in replacement for :class:`AlpacaOrderSink`. Routes broker-agnostic
:class:`~compass.live.vrp_contracts.OrderIntent` objects through the standalone
Executor REST service (which talks to IBKR / Tradier), instead of the Alpaca
SDK. Same protocol (``submit(intent) -> Dict[str, object]``), same deterministic
``stream_client_order_id`` for retries + PnL attribution.

Reads three env vars (none touched unless the runner asks for ``SINK_TYPE=executor``):

  * ``EXECUTOR_BASE_URL``    — REST root (default ``http://localhost:38002``)
  * ``EXECUTOR_API_KEY``     — sent as ``X-API-Key`` header (required)
  * ``EXECUTOR_ACCOUNT_ID``  — passed to all executor calls (required)
  * ``EXECUTOR_ACCOUNT_TYPE``— ``paper`` (default) or ``live``
  * ``EXECUTOR_TIMEOUT_S``   — HTTP timeout (default 15.0)

ADDITIVE + INERT BY DEFAULT. No existing experiment or module imports this. The
runner picks it only when ``vrp_engine.sink_type == "executor"`` (or the
``SINK_TYPE`` env var is set to ``executor``). Mapping details live in
``docs/V8A_EXECUTOR_SINK_MAPPING.md``.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from compass.live.vrp_contracts import OrderIntent
from compass.live.vrp_sinks import stream_client_order_id

logger = logging.getLogger(__name__)

# PR-B engine emits only credit-spread structures — mirror AlpacaOrderSink scope.
_SUPPORTED_SPREADS: Tuple[str, ...] = ("bull_put", "bear_call")

# bull_put → put leg, bear_call → call leg; net_credit reported as a positive
# number when the sell-leg premium exceeds the buy-leg premium (executor spec).
_STRUCTURE_MAP: Dict[str, Tuple[str, str]] = {
    "bull_put": ("bull_put_spread", "put"),
    "bear_call": ("bear_call_spread", "call"),
}

# Executor's idempotency_key validator: ^[a-zA-Z0-9_-]+$, max 255 chars. Our
# stream_client_order_id is mostly compatible (only fractional strikes inject
# a '.'), so we sanitize defensively rather than reshape the coid format.
_IDEMPOTENCY_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _sanitize_idempotency_key(coid: str) -> str:
    return _IDEMPOTENCY_SAFE.sub("_", coid)[:255]


class ExecutorClient:
    """Thin sync HTTP client for the Executor REST API. ``http`` is injectable
    so unit tests can pass a fake session — no real sockets in tests.
    """

    # Write endpoints are CSRF-protected; we lazy-fetch + cache a token.
    _CSRF_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 15.0,
        http: Optional[Any] = None,
    ) -> None:
        if not base_url:
            raise ValueError("ExecutorClient requires base_url")
        if not api_key:
            raise ValueError("ExecutorClient requires api_key")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = float(timeout)
        self._csrf_token: Optional[str] = None
        if http is None:
            import requests  # lazy — keeps the import optional at module-load
            http = requests.Session()
            http.headers.update({"X-API-Key": api_key, "Accept": "application/json"})
        else:
            # When a fake is injected, still make sure the header is set so
            # callers can inspect it. Best-effort: not all fakes expose .headers.
            headers = getattr(http, "headers", None)
            if headers is not None:
                headers.setdefault("X-API-Key", api_key)
        self._http = http

    # ---- low-level ----------------------------------------------------------
    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _fetch_csrf_token(self) -> Optional[str]:
        """Fetch + cache a CSRF token. Returns None silently if the endpoint is
        unreachable — caller will surface the 403 from the actual write."""
        try:
            resp = self._http.request(
                "GET", self._url("/auth/csrf-token"), timeout=self.timeout,
            )
            if resp.status_code >= 400:
                return None
            data = resp.json() or {}
            tok = data.get("csrf_token")
            if isinstance(tok, str) and tok:
                self._csrf_token = tok
                return tok
        except Exception:  # noqa: BLE001 — graceful degradation
            pass
        return None

    def _request(self, method: str, path: str, **kw) -> Any:
        url = self._url(path)
        kw.setdefault("timeout", self.timeout)
        # Inject CSRF token for write requests (cached after first fetch).
        if method.upper() in self._CSRF_WRITE_METHODS:
            headers = dict(kw.get("headers") or {})
            if not self._csrf_token:
                self._fetch_csrf_token()
            if self._csrf_token:
                headers.setdefault("X-CSRF-Token", self._csrf_token)
            if headers:
                kw["headers"] = headers
        resp = self._http.request(method, url, **kw)
        # If the cached CSRF token is stale, fetch once and retry once.
        if resp.status_code == 403 and method.upper() in self._CSRF_WRITE_METHODS:
            new_tok = self._fetch_csrf_token()
            if new_tok:
                headers = dict(kw.get("headers") or {})
                headers["X-CSRF-Token"] = new_tok
                kw["headers"] = headers
                resp = self._http.request(method, url, **kw)
        if resp.status_code >= 400:
            body = ""
            try:
                body = resp.text[:500]
            except Exception:
                pass
            raise ExecutorHTTPError(
                f"{method} {path} → {resp.status_code}: {body}",
                status_code=resp.status_code,
                body=body,
            )
        # Many endpoints return JSON; some PATCHes return SuccessResponse JSON
        # too. Empty bodies → return None.
        try:
            return resp.json()
        except Exception:
            return None

    # ---- high-level ---------------------------------------------------------
    def submit_spread(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/v1/orders/spread", json=body) or {}

    def cancel_order(self, order_id: str, account_id: str) -> Dict[str, Any]:
        return self._request(
            "DELETE", f"/v1/orders/{order_id}",
            params={"account_id": account_id},
        ) or {}

    def get_order_status(self, order_id: str, account_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"/v1/orders/{order_id}/status",
            params={"account_id": account_id},
        ) or {}

    def get_balance(self, account_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", "/v1/portfolio/balance",
            params={"account_id": account_id},
        ) or {}

    def get_positions(self, account_id: str) -> List[Dict[str, Any]]:
        out = self._request(
            "GET", "/v1/portfolio/positions",
            params={"account_id": account_id},
        )
        # Endpoint may return a bare list or a dict {positions: [...]}.
        if isinstance(out, list):
            return out
        if isinstance(out, dict):
            return out.get("positions", out.get("data", []))
        return []

    def set_callback_url(self, account_id: str, callback_url: Optional[str]) -> Dict[str, Any]:
        return self._request(
            "PATCH", f"/v1/gateways/accounts/{account_id}/callback-url",
            json={"callback_url": callback_url},
        ) or {}


class ExecutorHTTPError(RuntimeError):
    """HTTP-level executor error — surfaced to callers as a non-success result
    dict in :meth:`ExecutorOrderSink.submit` so the runner can carry on with
    other intents in the cycle instead of crashing the scan."""

    def __init__(self, msg: str, *, status_code: int, body: str = "") -> None:
        super().__init__(msg)
        self.status_code = status_code
        self.body = body


class ExecutorOrderSink:
    """Submits credit-spread intents via the Executor REST service.

    Identical public surface to :class:`AlpacaOrderSink` (``submit`` is the only
    method the engine calls), plus convenience helpers (``cancel_order``,
    ``get_order_status``, ``get_positions``, ``get_balance``) so the runner can
    drive the same broker for housekeeping (the AlpacaProvider exposes the same
    helpers via separate methods today).
    """

    def __init__(
        self,
        client: ExecutorClient,
        *,
        account_id: str,
        account_type: str = "paper",
        source_model: str = "vrp_v8a",
    ) -> None:
        if not account_id:
            raise ValueError("ExecutorOrderSink requires account_id")
        if account_type not in ("paper", "live"):
            raise ValueError(f"account_type must be 'paper' or 'live', got {account_type!r}")
        self._client = client
        self.account_id = account_id
        self.account_type = account_type
        self.source_model = source_model

    # ---------------------------------------------------------------- factory
    @classmethod
    def from_env(cls, *, http: Optional[Any] = None) -> "ExecutorOrderSink":
        """Build a sink from ``EXECUTOR_*`` env vars (validates presence)."""
        base_url = os.environ.get("EXECUTOR_BASE_URL", "http://localhost:38002")
        api_key = os.environ.get("EXECUTOR_API_KEY", "")
        account_id = os.environ.get("EXECUTOR_ACCOUNT_ID", "")
        account_type = os.environ.get("EXECUTOR_ACCOUNT_TYPE", "paper")
        timeout = float(os.environ.get("EXECUTOR_TIMEOUT_S", "15.0"))
        if not api_key:
            raise RuntimeError("EXECUTOR_API_KEY env var is required for SINK_TYPE=executor")
        if not account_id:
            raise RuntimeError("EXECUTOR_ACCOUNT_ID env var is required for SINK_TYPE=executor")
        client = ExecutorClient(base_url, api_key, timeout=timeout, http=http)
        return cls(client, account_id=account_id, account_type=account_type)

    # ---------------------------------------------------------------- submit
    def submit(self, intent: OrderIntent) -> Dict[str, object]:
        if intent.structure not in _SUPPORTED_SPREADS:
            raise NotImplementedError(
                f"ExecutorOrderSink supports {_SUPPORTED_SPREADS}, not '{intent.structure}' "
                f"(stream {intent.stream}). Equity/calendar/cross-vol execution is a later PR."
            )
        short_strike = _leg_strike(intent, "sell")
        long_strike = _leg_strike(intent, "buy")
        expiration = next((leg_.expiration for leg_ in intent.legs if leg_.expiration), None)
        if short_strike is None or long_strike is None or expiration is None:
            return {
                "status": "error",
                "message": "intent missing strikes/expiration",
                "stream": intent.stream,
            }

        strategy, opt_type = _STRUCTURE_MAP[intent.structure]
        coid = stream_client_order_id(intent)
        body: Dict[str, Any] = {
            "account_id": self.account_id,
            "account_type": self.account_type,
            "strategy": strategy,
            "legs": [
                {
                    "symbol": intent.symbol,
                    "option_type": opt_type,
                    "strike": float(short_strike),
                    "expiration": expiration,
                    "side": "sell_to_open",
                    "quantity": int(intent.contracts),
                },
                {
                    "symbol": intent.symbol,
                    "option_type": opt_type,
                    "strike": float(long_strike),
                    "expiration": expiration,
                    "side": "buy_to_open",
                    "quantity": int(intent.contracts),
                },
            ],
            "time_in_force": "day",
            "source": {
                "model": self.source_model,
                "signal_id": intent.stream,
                "metadata": {
                    "stream": intent.stream,
                    "rationale": intent.rationale or "",
                },
            },
            "idempotency_key": _sanitize_idempotency_key(coid),
        }
        if intent.est_credit is not None:
            body["order_type"] = "limit"
            # Executor expects positive net_credit (premium received).
            body["net_credit"] = float(intent.est_credit)
        else:
            body["order_type"] = "market"

        logger.info(
            "[vrp][exec] submit %s %s %s/%s exp %s x%d (coid=%s)",
            intent.stream, intent.symbol, short_strike, long_strike,
            expiration, intent.contracts, coid,
        )
        try:
            raw = self._client.submit_spread(body)
        except ExecutorHTTPError as exc:
            logger.error("[vrp][exec] submit failed: %s", exc)
            return {
                "status": "error",
                "message": str(exc),
                "stream": intent.stream,
                "client_order_id": coid,
                "http_status": exc.status_code,
            }
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash the cycle
            logger.error("[vrp][exec] submit unexpected error: %s", exc, exc_info=True)
            return {
                "status": "error",
                "message": f"{type(exc).__name__}: {exc}",
                "stream": intent.stream,
                "client_order_id": coid,
            }

        return _normalize_submit_response(raw, intent=intent, coid=coid)

    # ------------------------------------------------------------- helpers
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return self._client.cancel_order(order_id, self.account_id)

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        return self._client.get_order_status(order_id, self.account_id)

    def get_positions(self) -> List[Dict[str, Any]]:
        return self._client.get_positions(self.account_id)

    def get_balance(self) -> Dict[str, Any]:
        return self._client.get_balance(self.account_id)

    def set_callback_url(self, url: Optional[str]) -> Dict[str, Any]:
        return self._client.set_callback_url(self.account_id, url)


def _leg_strike(intent: OrderIntent, side: str) -> Optional[float]:
    for leg_ in intent.legs:
        if leg_.side == side and leg_.strike is not None:
            return float(leg_.strike)
    return None


def _normalize_submit_response(
    raw: Dict[str, Any],
    *,
    intent: OrderIntent,
    coid: str,
) -> Dict[str, object]:
    """Normalize the executor's :class:`OrderResponse` to the dict shape callers
    of :class:`AlpacaOrderSink.submit` already accept (``status``,
    ``order_id``, ``stream``, ``client_order_id``)."""
    success = bool(raw.get("success", False))
    status = "submitted" if success else "error"
    # Executor's OrderStatus enum maps to broker-side state; carry it through.
    order_status = raw.get("status")
    return {
        "status": status,
        "order_id": raw.get("order_id"),
        "broker_order_id": raw.get("broker_order_id"),
        "order_status": order_status,
        "ticker": intent.symbol,
        "spread_type": intent.structure,
        "contracts": intent.contracts,
        "filled_quantity": raw.get("filled_quantity", 0),
        "average_fill_price": raw.get("average_fill_price"),
        "stream": intent.stream,
        "client_order_id": coid,
        "message": raw.get("message", ""),
        "leg_con_ids": raw.get("leg_con_ids"),
        "submitted_at": raw.get("timestamp"),
    }
