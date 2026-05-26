"""
app.py — Attix Paper Trading Dashboard

FastAPI web app serving:
  GET  /                                 — Live HTML dashboard (session required)
  GET  /login                            — Login form (public)
  POST /login                            — Submit password, set session cookie
  GET  /logout                           — Clear session cookie
  GET  /api/v1/health                    — Health check (public)
  GET  /api/v1/experiments               — All experiments (X-API-Key or session)
  GET  /api/v1/experiments/{id}/trades   — Trade history (X-API-Key or session)
  GET  /api/v1/experiments/{id}/positions — Open positions (X-API-Key or session)
  GET  /api/v1/summary                   — Combined summary (X-API-Key or session)
  POST /api/admin/push-data              — Data push from sync script (X-API-Key)
  POST /api/admin/push-sentinel          — Sentinel data push (X-API-Key)
  GET  /api/v1/sentinel                  — Latest Sentinel snapshot (X-API-Key or session)
  POST /api/v1/watchdog-status           — External watchdog push (X-API-Key only)
  GET  /api/v1/watchdog-status           — Latest watchdog status (X-API-Key or session)

Environment variables:
  ATTIX_ROOT         — path to attix-credit-spreads repo (default: parent dir)
  DASHBOARD_API_KEY  — API key for /api/ endpoints (default: dev-attix-2026)
  DASHBOARD_PASSWORD — Password for the login form (default: attix-dev-2026!)
  SECRET_KEY         — HMAC signing key for session tokens (default: dev value)
  PORT               — listen port (default: 8000)
  STARTING_EQUITY    — account size for % calculations (default: 100000)

Run locally:
  cd ~/projects/pilotai-credit-spreads
  uvicorn web_dashboard.app:app --reload --port 8000
"""

from __future__ import annotations

import html as _html
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Optional

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECS,
    check_password,
    make_token,
    verify_token,
)
from .data import (
    get_all_experiments,
    get_positions,
    get_trades,
    query_all_live,
    query_experiment,
    summary_all,
    PUSHED_DATA_PATH,
    load_pushed_data,
)
from experiments.manager import get_manager
from experiments.registry import LIVE_STATUSES
from .html import (
    render_dashboard,
    render_login_page,
    render_positions_page,
    render_registry_page,
    render_sentinel_page,
    render_trades_page,
)
import web_dashboard.html as _html_module

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

# SECURITY AUDIT #4: fail fast if DASHBOARD_API_KEY is not set — no hardcoded fallback.
_API_KEY = os.environ.get("DASHBOARD_API_KEY")
if not _API_KEY:
    raise RuntimeError("DASHBOARD_API_KEY environment variable must be set before starting")
_RATE_LIMIT    = 120      # requests per 60s per API key
_IP_RATE_LIMIT = 200      # requests per 60s per source IP (SECURITY AUDIT #10)
_RATE_WINDOW   = 60.0

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Attix Paper Trading Dashboard",
    description="Live dashboard for paper trading experiments",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# SECURITY AUDIT #6: restrict CORS to the configured dashboard origin.
# Set DASHBOARD_ORIGIN env var to the exact deployed URL (e.g. https://attix-dashboard-production.up.railway.app).
# Falls back to no cross-origin access if unset.
_CORS_ORIGINS = [o.strip() for o in os.environ.get("DASHBOARD_ORIGIN", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key"],
)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response. SECURITY AUDIT #12."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Only sent over HTTPS; harmless over HTTP (browsers ignore it there).
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Dashboard uses inline <style>/<script> blocks; tighten with nonces
        # if those are ever moved to external files.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'"
        )
        return response


app.add_middleware(_SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Session auth — cookie-based for browser routes
# ---------------------------------------------------------------------------

class _NotAuthenticated(Exception):
    """Raised when a browser route needs a valid session but none is present."""


@app.exception_handler(_NotAuthenticated)
async def _handle_not_authenticated(request: Request, _exc: _NotAuthenticated):
    next_path = request.url.path
    return RedirectResponse(url=f"/login?next={next_path}", status_code=302)


def _session_ok(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    return bool(token and verify_token(token))


async def require_session(request: Request) -> None:
    """Dependency for browser routes: redirect to /login if no valid session."""
    if not _session_ok(request):
        raise _NotAuthenticated()


# ---------------------------------------------------------------------------
# API key auth + rate limiting (also accepts valid session cookie)
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_rate_windows: dict[str, deque] = defaultdict(deque)


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, respecting Railway / nginx reverse-proxy headers.
    SECURITY AUDIT #10.
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _check_rate(key: str, request: Request | None = None) -> None:
    """Sliding-window rate limiter by API key, with an additional per-IP layer.
    SECURITY AUDIT #10: per-IP limit catches credential-stuffing / key enumeration.
    """
    now = time.time()
    # Per-key bucket
    win = _rate_windows[key]
    while win and win[0] < now - _RATE_WINDOW:
        win.popleft()
    if len(win) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    win.append(now)
    # Per-IP bucket (higher ceiling — one IP may legitimately hold multiple keys)
    if request is not None:
        ip_key = f"ip:{_get_client_ip(request)}"
        ip_win = _rate_windows[ip_key]
        while ip_win and ip_win[0] < now - _RATE_WINDOW:
            ip_win.popleft()
        if len(ip_win) >= _IP_RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        ip_win.append(now)


def require_api_key(
    request: Request,
    api_key: Optional[str] = Depends(_api_key_header),
) -> str:
    """Accept X-API-Key header OR a valid session cookie (for browser tools)."""
    if api_key and api_key == _API_KEY:
        _check_rate(api_key, request)
        return api_key
    if _session_ok(request):
        return "session"
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing X-API-Key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def require_api_key_only(
    request: Request,
    api_key: Optional[str] = Depends(_api_key_header),
) -> str:
    """Accept only X-API-Key header — no session cookie.
    Used on admin endpoints so CSRF via browser session is impossible.
    SECURITY AUDIT #13.
    """
    if api_key and api_key == _API_KEY:
        _check_rate(api_key, request)
        return api_key
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing X-API-Key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


# ---------------------------------------------------------------------------
# Cache (simple in-memory, TTL 60s for dashboard, 30s for API)
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    result = fn()
    _cache[key] = (time.time(), result)
    return result


# ---------------------------------------------------------------------------
# Routes — public (no auth)
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, error: str = ""):
    """Show the login form. If already authenticated, redirect to /."""
    if _session_ok(request):
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(content=render_login_page(error), status_code=200)


@app.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(
    request: Request,
    password: str = Form(...),
    next: str = "/",
):
    """Validate password; on success set session cookie and redirect."""
    if not check_password(password):
        logger.warning("[auth] Failed login attempt from %s", request.client.host if request.client else "unknown")
        return HTMLResponse(
            content=render_login_page("Incorrect password. Please try again."),
            status_code=401,
        )
    # Success — issue signed session cookie
    token = make_token()
    safe_next = next if next.startswith("/") else "/"
    response = RedirectResponse(url=safe_next, status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL_SECS,
        httponly=True,
        samesite="lax",
        secure=not os.environ.get("INSECURE_COOKIES"),  # secure in prod, off locally
    )
    logger.info("[auth] Successful login from %s", request.client.host if request.client else "unknown")
    return response


@app.get("/logout", include_in_schema=False)
async def logout():
    """Clear the session cookie and redirect to /login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Routes — session required (browser)
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request, _: None = Depends(require_session)):
    """Live paper trading dashboard — session required."""
    try:
        all_stats = _cached("dashboard_stats", 60.0, query_all_live)
        # Log alpaca presence for first experiment (Railway diagnostics)
        if all_stats:
            first = all_stats[0]
            alp = first.get("alpaca")
            logger.info(
                "[dashboard] exp=%s has_alpaca=%s alpaca_equity=%s",
                first.get("id"),
                alp is not None,
                alp.get("equity") if alp else None,
            )
        html = render_dashboard(all_stats)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        logger.exception("Dashboard render failed")
        return HTMLResponse(
            content=f"<pre>Dashboard error: {_html.escape(str(e))}</pre>",
            status_code=500,
        )


@app.get("/positions", response_class=HTMLResponse, include_in_schema=False)
async def positions_page(request: Request, _: None = Depends(require_session)):
    """Open positions across all live experiments — session required."""
    try:
        all_stats = _cached("dashboard_stats", 60.0, query_all_live)
        html = render_positions_page(all_stats)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        logger.exception("Positions page render failed")
        return HTMLResponse(content=f"<pre>Positions error: {_html.escape(str(e))}</pre>", status_code=500)


@app.get("/trades", response_class=HTMLResponse, include_in_schema=False)
async def trades_page(request: Request, _: None = Depends(require_session)):
    """Recent trades across all live experiments — session required."""
    try:
        all_stats = _cached("dashboard_stats", 60.0, query_all_live)
        html = render_trades_page(all_stats)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        logger.exception("Trades page render failed")
        return HTMLResponse(content=f"<pre>Trades error: {_html.escape(str(e))}</pre>", status_code=500)


@app.get("/api/v1/health")
async def health():
    """Health check — no auth required."""
    try:
        mgr = get_manager()
        mgr.reload()
        live_count = len(mgr.by_status(*LIVE_STATUSES))
        # Alpaca key discovery diagnostic
        alpaca_diag = {}
        try:
            from web_dashboard.alpaca_live import discover_experiment_keys
            keys = discover_experiment_keys()
            alpaca_diag = {k: True for k in sorted(keys.keys())}
        except Exception as ae:
            alpaca_diag = {"error": str(ae)}
        return {
            "status":           "ok",
            "live_experiments": live_count,
            "live_ids":         sorted([e["id"] for e in mgr.by_status(*LIVE_STATUSES)]),
            "alpaca_keys_found": alpaca_diag,
            "registry_version": mgr._registry.get("schema_version"),
            "timestamp":        datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": str(e)},
        )


# ---------------------------------------------------------------------------
# Routes — authenticated
# ---------------------------------------------------------------------------

@app.get("/api/v1/experiments")
async def list_experiments(_key: str = Depends(require_api_key)):
    """All experiments from registry, augmented with live Alpaca equity."""
    mgr = get_manager()
    mgr.reload()
    # Deep-copy so we don't mutate the cached registry dicts
    exps = [dict(e) for e in get_all_experiments()]

    try:
        from .alpaca_live import get_all_live_alpaca
        live = _cached("alpaca_live_all", 60.0, get_all_live_alpaca)
        for exp in exps:
            norm_id = exp.get("id", "").upper().replace("-", "")
            alpaca_data = live.get(norm_id)
            if alpaca_data and not alpaca_data.get("error"):
                exp["live_equity"]        = alpaca_data.get("equity")
                exp["live_unrealized_pl"] = alpaca_data.get("unrealized_pl")
                exp["live_cash"]          = alpaca_data.get("cash")
                exp["alpaca_fetched_at"]  = alpaca_data.get("fetched_at")
    except Exception as exc:
        logger.warning("[api] /experiments Alpaca augment failed: %s", exc)

    return {
        "schema_version": mgr._registry.get("schema_version"),
        "last_updated":   mgr._registry.get("last_updated"),
        "count":          len(exps),
        "experiments":    exps,
    }


@app.get("/api/v1/experiments/{exp_id}/trades")
async def experiment_trades(
    exp_id: str,
    limit: int = Query(default=100, ge=1, le=1000),  # SECURITY AUDIT #9
    _key: str = Depends(require_api_key),
):
    """Trade history for one experiment.

    Returns local DB closed trades when available, merged with Alpaca order
    history (last 30 days) in a separate field for API consumers.
    """
    exp = get_manager().get(exp_id.upper())
    if not exp:
        raise HTTPException(status_code=404, detail=f"{exp_id} not found in registry")
    # SECURITY AUDIT #8: restrict access to paper_trading experiments only to prevent IDOR.
    if exp.get("status") not in LIVE_STATUSES:
        raise HTTPException(status_code=404, detail=f"{exp_id} not found in registry")

    trades = get_trades(exp, limit=limit)

    # Also expose raw Alpaca orders for consumers that want the full picture
    alpaca_orders: list = []
    try:
        from .alpaca_live import get_live_alpaca
        alpaca_data = get_live_alpaca(exp["id"])
        if alpaca_data and not alpaca_data.get("error"):
            alpaca_orders = alpaca_data.get("orders") or []
    except Exception as exc:
        logger.warning("[api] trades Alpaca orders error for %s: %s", exp_id, exc)

    return {
        "experiment_id": exp["id"],
        "name":          exp["name"],
        "count":         len(trades),
        "trades":        trades,
        "alpaca_orders": alpaca_orders[:limit],
    }


@app.get("/api/v1/experiments/{exp_id}/positions")
async def experiment_positions(
    exp_id: str,
    _key: str = Depends(require_api_key),
):
    """Open positions for one experiment (live from Alpaca when keys available)."""
    exp = get_manager().get(exp_id.upper())
    if not exp:
        raise HTTPException(status_code=404, detail=f"{exp_id} not found in registry")
    # SECURITY AUDIT #8: restrict access to paper_trading experiments only to prevent IDOR.
    if exp.get("status") not in LIVE_STATUSES:
        raise HTTPException(status_code=404, detail=f"{exp_id} not found in registry")

    source = "local_db"
    positions = []
    try:
        from .alpaca_live import get_live_alpaca
        alpaca_data = get_live_alpaca(exp["id"])
        if alpaca_data and not alpaca_data.get("error"):
            positions = alpaca_data.get("positions") or []
            source = "alpaca_live"
    except Exception as exc:
        logger.warning("[api] positions Alpaca error for %s: %s", exp_id, exc)

    if source != "alpaca_live":
        positions = get_positions(exp)

    return {
        "experiment_id": exp["id"],
        "name":          exp["name"],
        "count":         len(positions),
        "source":        source,
        "positions":     positions,
    }


@app.get("/api/v1/summary")
async def summary(_key: str = Depends(require_api_key)):
    """Combined P&L summary across all live experiments."""
    return _cached("summary", 30.0, summary_all)


# ---------------------------------------------------------------------------
# Routes — Registry page
# ---------------------------------------------------------------------------

@app.get("/registry", response_class=HTMLResponse, include_in_schema=False)
async def registry_page(request: Request, _: None = Depends(require_session)):
    """Experiment registry management page — session required."""
    try:
        import sys
        from pathlib import Path
        _proj = Path(__file__).resolve().parent.parent
        if str(_proj) not in sys.path:
            sys.path.insert(0, str(_proj))
        from experiments.registry import validate
        mgr = get_manager()
        mgr.reload()
        registry = mgr._registry
        errors = validate(registry)
        validation = {
            "valid": len(errors) == 0,
            "error_count": len(errors),
            "errors": errors,
        }
        html = render_registry_page(registry, validation=validation)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        logger.exception("Registry page render failed")
        return HTMLResponse(
            content=f"<pre>Registry error: {_html.escape(str(e))}</pre>",
            status_code=500,
        )


@app.post("/api/v1/registry/{exp_id}/transition")
async def registry_transition(
    exp_id: str,
    request: Request,
    _key: str = Depends(require_api_key),
):
    """Transition an experiment to a new status."""
    body = await request.json()
    new_status = body.get("status")
    reason = body.get("reason")
    if not new_status:
        raise HTTPException(status_code=400, detail="Missing 'status' in request body")
    try:
        exp = get_manager().transition(exp_id, new_status, reason=reason or "")
        _cache.clear()
        return {"status": "ok", "experiment": exp}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/registry/validate")
async def registry_validate(_key: str = Depends(require_api_key)):
    """Run validation on the registry and return errors."""
    import sys
    from pathlib import Path
    _proj = Path(__file__).resolve().parent.parent
    if str(_proj) not in sys.path:
        sys.path.insert(0, str(_proj))
    from experiments.registry import validate
    mgr = get_manager()
    mgr.reload()
    errors = validate(mgr._registry)
    return {
        "status": "ok" if not errors else "errors",
        "error_count": len(errors),
        "errors": errors,
    }


@app.get("/api/v1/registry/sync")
async def registry_sync(_key: str = Depends(require_api_key)):
    """Find orphan env files, DBs, and active-but-not-running experiments."""
    import sys
    from pathlib import Path
    _proj = Path(__file__).resolve().parent.parent
    if str(_proj) not in sys.path:
        sys.path.insert(0, str(_proj))
    from experiments.registry import (
        find_orphan_env_files,
        find_orphan_dbs,
        find_active_not_running,
    )
    mgr = get_manager()
    mgr.reload()
    registry = mgr._registry
    return {
        "status": "ok",
        "orphan_env_files": find_orphan_env_files(registry),
        "orphan_dbs": find_orphan_dbs(registry),
        "active_not_running": find_active_not_running(registry),
    }


# ---------------------------------------------------------------------------
# Admin — data push (for Railway sync from local Mac)
# ---------------------------------------------------------------------------

@app.post("/api/admin/push-data")
async def push_data(request: Request, _key: str = Depends(require_api_key_only)):
    """
    Accept a full dashboard data snapshot from the local sync script.
    Stores as JSON file so the dashboard can render even without SQLite DBs.
    """
    import json as _json
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    body["pushed_at"] = datetime.now(timezone.utc).isoformat()
    PUSHED_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUSHED_DATA_PATH.write_text(_json.dumps(body, indent=2))
    _cache.clear()  # bust cache so next request uses fresh data
    logger.info(f"Received pushed data: {len(_json.dumps(body))} bytes")
    return {"status": "ok", "message": "Data received", "pushed_at": body["pushed_at"]}


# ---------------------------------------------------------------------------
# Admin — Sentinel data push
# ---------------------------------------------------------------------------

SENTINEL_DATA_PATH = PUSHED_DATA_PATH.parent / "sentinel_dashboard.json"


@app.post("/api/admin/push-sentinel")
async def push_sentinel(request: Request, _key: str = Depends(require_api_key_only)):
    """
    Accept a Sentinel dashboard snapshot from sync_sentinel_data.py.
    Stores as data/sentinel_dashboard.json.
    """
    import json as _json

    # Guard against oversized payloads (OOM protection for Railway container)
    raw = await request.body()
    if len(raw) > 10_000_000:  # 10 MB
        raise HTTPException(status_code=413, detail="Payload too large (>10MB)")
    body = _json.loads(raw)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    body["pushed_at"] = datetime.now(timezone.utc).isoformat()
    SENTINEL_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    serialized = _json.dumps(body, indent=2)
    SENTINEL_DATA_PATH.write_text(serialized)
    logger.info(f"Received Sentinel data: {len(raw)} bytes")
    return {"status": "ok", "message": "Sentinel data received", "pushed_at": body["pushed_at"]}


@app.get("/api/v1/sentinel")
async def get_sentinel(_key: str = Depends(require_api_key)):
    """Return the latest Sentinel dashboard snapshot."""
    import json as _json
    if not SENTINEL_DATA_PATH.exists():
        return {"error": "No sentinel data available", "experiments": {}}
    try:
        with open(SENTINEL_DATA_PATH) as f:
            return _json.load(f)
    except Exception as e:
        logger.error(f"Failed to read sentinel data: {e}")
        return {"error": str(e), "experiments": {}}


# ---------------------------------------------------------------------------
# Watchdog status — external VPS watchdog pushes status here
# ---------------------------------------------------------------------------

# In-memory store; persists for the lifetime of the process.
_watchdog_status: dict = {}


@app.post("/api/v1/watchdog-status")
async def push_watchdog_status(
    request: Request,
    _key: str = Depends(require_api_key_only),
):
    """Accept a watchdog status JSON from the external VPS watchdog script."""
    import json as _json

    raw = await request.body()
    if len(raw) > 1_000_000:  # 1 MB guard
        raise HTTPException(status_code=413, detail="Payload too large")
    body = _json.loads(raw)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    body["received_at"] = datetime.now(timezone.utc).isoformat()
    _watchdog_status.clear()
    _watchdog_status.update(body)

    # Make the latest status available to the HTML renderer
    _html_module._watchdog_status = _watchdog_status

    logger.info("[watchdog] Status received: overall=%s", body.get("overall"))
    return {"status": "ok", "received_at": body["received_at"]}


@app.get("/api/v1/watchdog-status")
async def get_watchdog_status(_key: str = Depends(require_api_key)):
    """Return the latest watchdog status snapshot."""
    if not _watchdog_status:
        return {"status": "unknown", "detail": "No watchdog data received yet"}
    return _watchdog_status


# ---------------------------------------------------------------------------
# Scan heartbeats — worker pushes after each scan, sentinel reads these
# ---------------------------------------------------------------------------

# In-memory: {exp_id: {scan_slot, scan_time, opportunities_found, status, received_at}}
_scan_heartbeats: dict[str, dict] = {}


@app.post("/api/v1/experiments/{exp_id}/heartbeat")
async def push_scan_heartbeat(
    exp_id: str,
    request: Request,
    _key: str = Depends(require_api_key_only),
):
    """Worker posts here after each scan completes. Sentinel reads it to check recency."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    body["received_at"] = datetime.now(timezone.utc).isoformat()
    body["exp_id"] = exp_id.upper()
    _scan_heartbeats[exp_id.upper()] = body
    logger.info("[heartbeat] %s scan heartbeat received: slot=%s", exp_id, body.get("scan_slot"))
    return {"status": "ok", "exp_id": exp_id.upper(), "received_at": body["received_at"]}


@app.get("/api/v1/experiments/{exp_id}/heartbeat")
async def get_scan_heartbeat(
    exp_id: str,
    _key: str = Depends(require_api_key),
):
    """Return the latest scan heartbeat for an experiment."""
    hb = _scan_heartbeats.get(exp_id.upper())
    if not hb:
        raise HTTPException(status_code=404, detail=f"No heartbeat recorded for {exp_id}")
    return hb


@app.get("/api/v1/scan-heartbeats")
async def get_all_scan_heartbeats(_key: str = Depends(require_api_key)):
    """Return all experiment scan heartbeats."""
    return {"count": len(_scan_heartbeats), "heartbeats": _scan_heartbeats}


# ---------------------------------------------------------------------------
# Sentinel Dashboard — server-rendered HTML (uses render_sentinel_page from html.py)
# ---------------------------------------------------------------------------

@app.get("/sentinel", response_class=HTMLResponse, include_in_schema=False)
@app.get("/sentinel/", response_class=HTMLResponse, include_in_schema=False)
async def sentinel_dashboard(request: Request, _: None = Depends(require_session)):
    """Serve the Sentinel dashboard with health scores, gates, and alerts.

    Data sources (checked in order):
      1. sentinel_dashboard.json — pushed from Mac via sync_sentinel_data.py
         (primary source on Railway where local files don't exist)
      2. sentinel_state.json + sentinel.db — local fallback for dev
    """
    import json as _json
    from pathlib import Path as _Path

    try:
        _proj = _Path(__file__).resolve().parent.parent

        # Load registry (always available — committed to git)
        reg_path = _proj / "experiments" / "registry.json"
        registry = {"experiments": {}}
        if reg_path.exists():
            try:
                registry = _json.loads(reg_path.read_text())
            except Exception:
                pass

        # Primary: pushed sentinel data (sentinel_dashboard.json)
        # This is the only source on Railway since local files are ephemeral.
        sentinel_state = {}
        alerts = []
        snapshots = {}

        if SENTINEL_DATA_PATH.exists():
            try:
                pushed = _json.loads(SENTINEL_DATA_PATH.read_text())
                sentinel_state = pushed  # has .experiments, .alerts, .config_integrity
                alerts = pushed.get("alerts", [])
            except Exception:
                pass

        # Fallback: local sentinel_state.json (dev only)
        if not sentinel_state.get("experiments"):
            state_path = _proj / "sentinel_state.json"
            if state_path.exists():
                try:
                    sentinel_state = _json.loads(state_path.read_text())
                except Exception:
                    pass

            # Local sentinel.db alerts (dev only)
            db_path = _proj / "sentinel" / "db" / "sentinel.db"
            if db_path.exists():
                try:
                    import sys
                    if str(_proj) not in sys.path:
                        sys.path.insert(0, str(_proj))
                    from sentinel.history import SentinelDB
                    db = SentinelDB(str(db_path))
                    alerts = db.get_all_alerts(limit=50)
                except Exception as e:
                    logger.warning(f"Could not load sentinel DB: {e}")

        return HTMLResponse(
            content=render_sentinel_page(sentinel_state, alerts, snapshots, registry)
        )
    except Exception as e:
        logger.exception("Sentinel dashboard render failed")
        return HTMLResponse(
            content=f"<pre>Sentinel error: {_html.escape(str(e))}</pre>",
            status_code=500,
        )


# ---------------------------------------------------------------------------
# Startup log
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _on_startup():
    from .data import PROJECT_ROOT, REGISTRY_PATH
    from .alpaca_live import discover_experiment_keys
    logger.info("=" * 60)
    logger.info("Attix Paper Trading Dashboard starting")
    logger.info(f"  ATTIX_ROOT  : {PROJECT_ROOT}")
    logger.info(f"  Registry      : {REGISTRY_PATH} (exists={REGISTRY_PATH.exists()})")
    # SECURITY AUDIT #4: do not log whether default key is in use (key is always required now).
    logger.info("  API key set   : yes")
    import os as _os
    logger.info(f"  Dashboard pw  : {'custom' if _os.environ.get('DASHBOARD_PASSWORD') else 'default (dev)'}")
    logger.info(f"  Secret key    : {'custom' if _os.environ.get('SECRET_KEY') else 'default (dev — INSECURE)'}")
    alpaca_keys = discover_experiment_keys()
    logger.info(f"  Alpaca keys   : {len(alpaca_keys)} experiments configured ({', '.join(sorted(alpaca_keys))})")
    logger.info("=" * 60)

    if REGISTRY_PATH.exists():
        try:
            mgr = get_manager()
            mgr.reload()
            live = mgr.by_status(*LIVE_STATUSES)
            logger.info(f"  Live experiments: {[e['id'] for e in live]}")
        except Exception as e:
            logger.warning(f"  Could not load registry: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("web_dashboard.app:app", host="0.0.0.0", port=port, reload=False)


# ---------------------------------------------------------------------------
# Per-experiment trade sync (worker → dashboard)
# ---------------------------------------------------------------------------

EXPERIMENT_DATA_DIR = PUSHED_DATA_PATH.parent / "experiment_trades"
EXPERIMENT_PORTFOLIO_DIR = PUSHED_DATA_PATH.parent / "experiment_portfolio"


@app.post("/api/v1/experiments/{exp_id}/sync-trades")
async def sync_experiment_trades(
    exp_id: str,
    request: Request,
    _key: str = Depends(require_api_key_only),
):
    """Accept trade data push from the worker for a single experiment."""
    import json as _json
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    EXPERIMENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    norm = exp_id.upper().replace("-", "")
    path = EXPERIMENT_DATA_DIR / f"{norm}.json"
    body["synced_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(_json.dumps(body, indent=2))
    _cache.clear()
    return {"status": "ok", "experiment": exp_id, "synced_at": body["synced_at"]}


@app.post("/api/v1/experiments/{exp_id}/push-portfolio")
async def push_experiment_portfolio(
    exp_id: str,
    request: Request,
    _key: str = Depends(require_api_key_only),
):
    """Accept Alpaca portfolio snapshot push from the worker for a single experiment."""
    import json as _json
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    EXPERIMENT_PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
    norm = exp_id.upper().replace("-", "")
    path = EXPERIMENT_PORTFOLIO_DIR / f"{norm}.json"
    body["pushed_at"] = datetime.now(timezone.utc).isoformat()
    body["exp_id"] = exp_id.upper()
    path.write_text(_json.dumps(body, indent=2))
    _cache.clear()
    return {"status": "ok", "experiment": exp_id, "pushed_at": body["pushed_at"]}
