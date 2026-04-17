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
    load_registry,
    query_all_live,
    query_experiment,
    summary_all,
    PUSHED_DATA_PATH,
    load_pushed_data,
)
from .html import render_dashboard, render_login_page

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
            content=f"<pre>Dashboard error: {e}</pre>",
            status_code=500,
        )


@app.get("/api/v1/health")
async def health():
    """Health check — no auth required."""
    try:
        registry = load_registry()
        live_count = sum(
            1 for e in registry["experiments"].values()
            if e.get("status") == "paper_trading"
        )
        return {
            "status":           "ok",
            "live_experiments": live_count,
            "registry_version": registry.get("schema_version"),
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
    """All experiments from registry (all statuses)."""
    registry = _cached("registry", 30.0, load_registry)
    exps = get_all_experiments(registry)
    return {
        "schema_version": registry.get("schema_version"),
        "last_updated":   registry.get("last_updated"),
        "count":          len(exps),
        "experiments":    exps,
    }


@app.get("/api/v1/experiments/{exp_id}/trades")
async def experiment_trades(
    exp_id: str,
    limit: int = Query(default=100, ge=1, le=1000),  # SECURITY AUDIT #9
    _key: str = Depends(require_api_key),
):
    """Trade history for one experiment (closed trades, newest first)."""
    registry = _cached("registry", 30.0, load_registry)
    exp = registry["experiments"].get(exp_id.upper())
    if not exp:
        raise HTTPException(status_code=404, detail=f"{exp_id} not found in registry")
    # SECURITY AUDIT #8: restrict access to paper_trading experiments only to prevent IDOR.
    if exp.get("status") != "paper_trading":
        raise HTTPException(status_code=404, detail=f"{exp_id} not found in registry")
    trades = get_trades(exp, limit=limit)
    return {
        "experiment_id": exp["id"],
        "name":          exp["name"],
        "count":         len(trades),
        "trades":        trades,
    }


@app.get("/api/v1/experiments/{exp_id}/positions")
async def experiment_positions(
    exp_id: str,
    _key: str = Depends(require_api_key),
):
    """Open positions for one experiment."""
    registry = _cached("registry", 30.0, load_registry)
    exp = registry["experiments"].get(exp_id.upper())
    if not exp:
        raise HTTPException(status_code=404, detail=f"{exp_id} not found in registry")
    # SECURITY AUDIT #8: restrict access to paper_trading experiments only to prevent IDOR.
    if exp.get("status") != "paper_trading":
        raise HTTPException(status_code=404, detail=f"{exp_id} not found in registry")
    positions = get_positions(exp)
    return {
        "experiment_id": exp["id"],
        "name":          exp["name"],
        "count":         len(positions),
        "positions":     positions,
    }


@app.get("/api/v1/summary")
async def summary(_key: str = Depends(require_api_key)):
    """Combined P&L summary across all live experiments."""
    return _cached("summary", 30.0, summary_all)


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
# Startup log
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _on_startup():
    from .data import PROJECT_ROOT, REGISTRY_PATH
    logger.info("=" * 60)
    logger.info("Attix Paper Trading Dashboard starting")
    logger.info(f"  ATTIX_ROOT  : {PROJECT_ROOT}")
    logger.info(f"  Registry      : {REGISTRY_PATH} (exists={REGISTRY_PATH.exists()})")
    # SECURITY AUDIT #4: do not log whether default key is in use (key is always required now).
    logger.info("  API key set   : yes")
    import os as _os
    logger.info(f"  Dashboard pw  : {'custom' if _os.environ.get('DASHBOARD_PASSWORD') else 'default (dev)'}")
    logger.info(f"  Secret key    : {'custom' if _os.environ.get('SECRET_KEY') else 'default (dev — INSECURE)'}")
    logger.info("=" * 60)

    if REGISTRY_PATH.exists():
        try:
            registry = load_registry()
            live = [e for e in registry["experiments"].values()
                    if e.get("status") == "paper_trading"]
            logger.info(f"  Live experiments: {[e['id'] for e in live]}")
        except Exception as e:
            logger.warning(f"  Could not load registry: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("web_dashboard.app:app", host="0.0.0.0", port=port, reload=False)
