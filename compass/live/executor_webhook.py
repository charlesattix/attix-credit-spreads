"""compass/live/executor_webhook.py — fill-event receiver for EXP-V8A.

The standalone Executor service (IBKR paper today) POSTs a notification to a
``callback_url`` registered per-account whenever an order fills or partially
fills. Payload shape is fixed and lives in
``executor/order_events/source_notifier.py::notify_source``:

    {
      "broker_order_id": "12345",
      "status": "filled" | "partially_filled",
      "filled_quantity": 5,
      "avg_fill_price": 0.97,
      "remaining_quantity": 0,
      "symbol": "SPY",
      "side": "sell" | "buy",
      "event_data": { /* raw broker event */ }
    }

This module exposes a FastAPI ``APIRouter`` + an ``app`` you can mount or run
standalone, plus a tiny SQLite journal so fills are persisted across restarts
and any in-process consumer (e.g. the V8A PositionMonitor) can drain new
events.

ADDITIVE + V8A-only. Nothing in the existing 8 experiments imports this. The
file ships a CLI: ``python -m compass.live.executor_webhook --port 8500`` so it
can run as a side-car next to the worker without changing the worker's main
loop.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# FastAPI is optional at import time (kept lazy below) but the names MUST be at
# module scope when present, because PEP 563 postponed annotations resolve via
# the module's globals — not the build_app() function's locals.
try:  # pragma: no cover
    from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
    _FASTAPI_OK = True
except ImportError:  # pragma: no cover
    APIRouter = FastAPI = Header = HTTPException = Request = None  # type: ignore[assignment,misc]
    _FASTAPI_OK = False

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.environ.get(
    "V8A_EXECUTOR_FILLS_DB",
    str(Path(__file__).resolve().parents[2] / "data" / "v8a_executor_fills.db"),
)

# Optional HMAC secret. If unset, signature check is skipped (use only when the
# webhook is bound to localhost or behind a private network).
_SHARED_SECRET_ENV = "EXECUTOR_WEBHOOK_SECRET"


# ── persistence ──────────────────────────────────────────────────────────────


def _ensure_schema(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS executor_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at REAL NOT NULL,
                broker_order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                symbol TEXT,
                side TEXT,
                filled_quantity INTEGER,
                avg_fill_price REAL,
                remaining_quantity INTEGER,
                payload_json TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS ix_fills_consumed ON executor_fills(consumed, id)"
        )
        con.commit()


def persist_fill(payload: Dict[str, Any], *, db_path: str = _DEFAULT_DB_PATH) -> int:
    """Append a fill to the journal. Returns the row id."""
    _ensure_schema(db_path)
    with closing(sqlite3.connect(db_path)) as con:
        cur = con.execute(
            """
            INSERT INTO executor_fills (
                received_at, broker_order_id, status, symbol, side,
                filled_quantity, avg_fill_price, remaining_quantity, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                str(payload.get("broker_order_id", "")),
                str(payload.get("status", "")),
                payload.get("symbol"),
                payload.get("side"),
                _int_or_none(payload.get("filled_quantity")),
                _float_or_none(payload.get("avg_fill_price")),
                _int_or_none(payload.get("remaining_quantity")),
                json.dumps(payload, default=str),
            ),
        )
        con.commit()
        return int(cur.lastrowid)


def drain_unconsumed(*, db_path: str = _DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Pop every unconsumed fill row, marking them consumed atomically. The
    PositionMonitor (or a periodic task) calls this each tick to ingest new
    fills into V8A's own trades DB."""
    _ensure_schema(db_path)
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = list(con.execute(
            "SELECT * FROM executor_fills WHERE consumed = 0 ORDER BY id"
        ))
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" for _ in ids)
            con.execute(
                f"UPDATE executor_fills SET consumed = 1 WHERE id IN ({placeholders})",
                ids,
            )
            con.commit()
        return [dict(r) for r in rows]


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── in-process subscribers (for tests + an in-worker consumer) ───────────────

_subscribers_lock = threading.Lock()
_subscribers: List[Callable[[Dict[str, Any]], None]] = []


def subscribe(handler: Callable[[Dict[str, Any]], None]) -> None:
    """Register an in-process handler called synchronously for each fill.
    Exceptions are caught and logged — one bad subscriber must not break the
    webhook response, or executor will DLQ + retry."""
    with _subscribers_lock:
        _subscribers.append(handler)


def _fanout(payload: Dict[str, Any]) -> None:
    with _subscribers_lock:
        handlers = list(_subscribers)
    for h in handlers:
        try:
            h(payload)
        except Exception:
            logger.exception("[executor_webhook] subscriber raised — ignoring")


# ── HMAC signature (optional) ────────────────────────────────────────────────


def _verify_signature(raw_body: bytes, header_sig: Optional[str]) -> bool:
    secret = os.environ.get(_SHARED_SECRET_ENV, "").encode()
    if not secret:
        return True  # signature optional unless secret is set
    if not header_sig:
        return False
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    # Header format ``sha256=<hex>`` (GitHub-style) — strip prefix if present.
    if header_sig.startswith("sha256="):
        header_sig = header_sig[len("sha256="):]
    return hmac.compare_digest(expected, header_sig)


# ── FastAPI surface ──────────────────────────────────────────────────────────


def build_app(*, db_path: str = _DEFAULT_DB_PATH):
    """Construct a small FastAPI app. The module-level FastAPI import is
    optional so the persistence helpers below can be used in tests / consumers
    without dragging FastAPI in. If the import failed, this raises."""
    if not _FASTAPI_OK:
        raise RuntimeError("fastapi is required to build the webhook app")

    _ensure_schema(db_path)
    app = FastAPI(title="EXP-V8A Executor Webhook", version="1.0.0")
    router = APIRouter()

    @app.get("/health")
    def _health() -> Dict[str, Any]:
        return {"status": "ok", "db_path": db_path}

    @router.post("/webhooks/executor/fills")
    async def fills(
        request: Request,
        x_signature: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        raw = await request.body()
        if not _verify_signature(raw, x_signature):
            raise HTTPException(status_code=401, detail="invalid signature")
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid JSON")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be an object")
        if "broker_order_id" not in payload or "status" not in payload:
            raise HTTPException(status_code=400, detail="missing broker_order_id/status")

        row_id = persist_fill(payload, db_path=db_path)
        _fanout(payload)
        logger.info(
            "[executor_webhook] fill broker_order_id=%s status=%s qty=%s @%s symbol=%s",
            payload.get("broker_order_id"), payload.get("status"),
            payload.get("filled_quantity"), payload.get("avg_fill_price"),
            payload.get("symbol"),
        )
        return {"ok": True, "row_id": row_id}

    app.include_router(router)
    return app


# Lazy module-level app — only built when FastAPI is importable.
try:  # pragma: no cover — depends on optional FastAPI install
    app = build_app()
except Exception:  # noqa: BLE001
    app = None


# ── CLI runner ────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="EXP-V8A executor fill webhook")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument("--db", default=_DEFAULT_DB_PATH)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
    )

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required to run the webhook server", file=sys.stderr)
        return 2
    server_app = build_app(db_path=args.db)
    uvicorn.run(server_app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
