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
from .html import (
    render_dashboard,
    render_login_page,
    render_positions_page,
    render_registry_page,
    render_trades_page,
)

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


@app.get("/positions", response_class=HTMLResponse, include_in_schema=False)
async def positions_page(request: Request, _: None = Depends(require_session)):
    """Open positions across all live experiments — session required."""
    try:
        all_stats = _cached("dashboard_stats", 60.0, query_all_live)
        html = render_positions_page(all_stats)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        logger.exception("Positions page render failed")
        return HTMLResponse(content=f"<pre>Positions error: {e}</pre>", status_code=500)


@app.get("/trades", response_class=HTMLResponse, include_in_schema=False)
async def trades_page(request: Request, _: None = Depends(require_session)):
    """Recent trades across all live experiments — session required."""
    try:
        all_stats = _cached("dashboard_stats", 60.0, query_all_live)
        html = render_trades_page(all_stats)
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        logger.exception("Trades page render failed")
        return HTMLResponse(content=f"<pre>Trades error: {e}</pre>", status_code=500)


@app.get("/api/v1/health")
async def health():
    """Health check — no auth required."""
    try:
        registry = load_registry()
        live_count = sum(
            1 for e in registry["experiments"].values()
            if e.get("status") in ("active", "paper_trading")
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
    if exp.get("status") not in ("active", "paper_trading"):
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
    if exp.get("status") not in ("active", "paper_trading"):
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
        from experiments.registry import load_registry as load_exp_registry, validate
        registry = load_exp_registry()
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
            content=f"<pre>Registry error: {e}</pre>",
            status_code=500,
        )


@app.post("/api/v1/registry/{exp_id}/transition")
async def registry_transition(
    exp_id: str,
    request: Request,
    _key: str = Depends(require_api_key),
):
    """Transition an experiment to a new status."""
    import sys
    from pathlib import Path
    _proj = Path(__file__).resolve().parent.parent
    if str(_proj) not in sys.path:
        sys.path.insert(0, str(_proj))
    from experiments.registry import load_registry as load_exp_registry, transition_status
    body = await request.json()
    new_status = body.get("status")
    reason = body.get("reason")
    if not new_status:
        raise HTTPException(status_code=400, detail="Missing 'status' in request body")
    try:
        registry = load_exp_registry()
        exp = transition_status(exp_id, new_status, reason=reason, registry=registry)
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
    from experiments.registry import load_registry as load_exp_registry, validate
    registry = load_exp_registry()
    errors = validate(registry)
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
        load_registry as load_exp_registry,
        find_orphan_env_files,
        find_orphan_dbs,
        find_active_not_running,
    )
    registry = load_exp_registry()
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
# Sentinel Dashboard — server-rendered HTML
# ---------------------------------------------------------------------------

from fastapi.responses import HTMLResponse


@app.get("/sentinel", response_class=HTMLResponse)
@app.get("/sentinel/", response_class=HTMLResponse)
async def sentinel_dashboard():
    """Serve the Sentinel dashboard as a self-contained HTML page."""
    import json as _json

    # Load data
    data = {}
    if SENTINEL_DATA_PATH.exists():
        try:
            with open(SENTINEL_DATA_PATH) as f:
                data = _json.load(f)
        except Exception:
            pass

    experiments = data.get("experiments", {})
    config = data.get("config_integrity", {})
    alerts = data.get("alerts", [])
    generated = data.get("generated_at", "—")

    # Build experiment rows
    exp_rows = ""
    for eid, exp in sorted(experiments.items()):
        status = exp.get("status", "unknown")
        metrics = exp.get("metrics", {})
        baseline = exp.get("baseline", {})
        gates = exp.get("gates", {})
        live_wr = metrics.get("win_rate")
        bt_wr = baseline.get("win_rate")
        pnl = metrics.get("total_pnl", 0) or 0
        # Orphan/stuck from gates
        g7 = gates.get("gate7_orphans", {})
        g9 = gates.get("gate9_lifecycle", {})
        orphans = len(g7.get("metrics", {}).get("orphans", [])) if isinstance(g7.get("metrics"), dict) else 0
        stuck = len(g9.get("metrics", {}).get("stuck", [])) if isinstance(g9.get("metrics"), dict) else 0
        g6 = gates.get("gate6_sizing", {})
        sizing_dev = g6.get("metrics", {}).get("deviation_pct") if isinstance(g6.get("metrics"), dict) else None

        # Status pill
        if status == "halted":
            s_cls, s_txt = "p-halt", "HALTED"
        elif orphans > 0 or stuck > 0 or (sizing_dev and abs(sizing_dev) > 30):
            s_cls, s_txt = "p-crit", "CRITICAL"
        elif (sizing_dev and abs(sizing_dev) > 15) or (bt_wr and live_wr and (live_wr - bt_wr) < -10):
            s_cls, s_txt = "p-warn", "WARNING"
        elif bt_wr and live_wr and (live_wr - bt_wr) < -8:
            s_cls, s_txt = "p-warn", "WATCH"
        else:
            s_cls, s_txt = "p-pass", "OK"

        # WR drift
        if live_wr is not None and bt_wr is not None:
            drift = live_wr - bt_wr
            drift_str = f"{drift:+.0f} pts"
            drift_cls = "red" if drift < -15 else ("yellow" if drift < -8 else ("green" if drift > 5 else "muted"))
        else:
            drift_str = "—"
            drift_cls = "muted"

        wr_str = f"{live_wr:.0f}%" if live_wr is not None else "—"

        # P&L: realized, unrealized, total
        realized_pnl = metrics.get("realized_pnl", pnl)
        unrealized_pnl = metrics.get("unrealized_pnl")
        total_pnl_val = metrics.get("total_pnl", pnl)

        real_cls = "green" if realized_pnl > 0 else ("red" if realized_pnl < 0 else "muted")
        real_str = f"${realized_pnl:+,.0f}" if realized_pnl else "$0"

        if unrealized_pnl is not None:
            unreal_cls = "green" if unrealized_pnl > 0 else ("red" if unrealized_pnl < 0 else "muted")
            unreal_str = f"${unrealized_pnl:+,.0f}"
        else:
            unreal_cls = "muted"
            unreal_str = "—"

        total_cls = "green" if total_pnl_val > 0 else ("red" if total_pnl_val < 0 else "muted")
        total_str = f"${total_pnl_val:+,.0f}" if total_pnl_val else "$0"

        # Issues
        issues = []
        if orphans > 0:
            issues.append(f"{orphans} orphans")
        if stuck > 0:
            issues.append(f"{stuck} stuck")
        if sizing_dev and abs(sizing_dev) > 15:
            issues.append(f"Sizing {sizing_dev:+.0f}%")
        if bt_wr and live_wr and (live_wr - bt_wr) < -8:
            issues.append("WR drifting")
        issue_str = " · ".join(issues) if issues else "—"
        issue_cls = "" if issues else "muted"

        exp_rows += f"""<tr>
            <td class="bold">{eid}</td>
            <td><span class="p {s_cls}">{s_txt}</span></td>
            <td>{wr_str}</td>
            <td class="{drift_cls}">{drift_str}</td>
            <td class="{real_cls}">{real_str}</td>
            <td class="{unreal_cls}">{unreal_str}</td>
            <td class="{total_cls} bold">{total_str}</td>
            <td class="{issue_cls}">{issue_str}</td>
        </tr>"""

    # Config rows — config_integrity is a list of {check, status, detail}
    config_rows = ""
    if isinstance(config, list):
        for item in config:
            name = item.get("check", "—")
            st = item.get("status", "pass").lower()
            detail = item.get("detail", "")
            cls = "p-pass" if st == "pass" else ("p-warn" if st == "warning" else "p-crit")
            txt = st.upper()
            config_rows += f'<tr><td>{name}</td><td><span class="p {cls}">{txt}</span></td><td class="muted">{detail}</td></tr>'
    else:
        checks = [
            ("Config Schema", config.get("schema_valid", True)),
            ("Registry Integrity", config.get("registry_valid", True)),
            ("Config Drift", config.get("no_drift", True)),
            ("DB Schema", config.get("db_valid", True)),
            ("Certification", config.get("all_certified", True)),
        ]
        for name, ok in checks:
            cls = "p-pass" if ok else "p-crit"
            txt = "PASS" if ok else "FAIL"
            config_rows += f'<tr><td>{name}</td><td><span class="p {cls}">{txt}</span></td><td></td></tr>'

    # Alert rows
    alert_rows = ""
    for a in (alerts or [])[:20]:
        sev = a.get("severity", "info").lower()
        sev_cls = {"critical": "p-crit", "warning": "p-warn", "halt": "p-halt"}.get(sev, "p-pass")
        sev_txt = sev.upper()[:4]
        ts = a.get("timestamp", "")[-8:-3] if a.get("timestamp") else "—"
        exp_id = a.get("experiment_id", "—")
        msg = a.get("message", "—")
        alert_rows += f"""<tr>
            <td class="mono muted">{ts}</td>
            <td><span class="p {sev_cls}">{sev_txt}</span></td>
            <td class="bold">{exp_id}</td>
            <td>{msg}</td>
        </tr>"""

    if not alert_rows:
        alert_rows = '<tr><td colspan="4" class="muted" style="text-align:center;padding:1rem">No alerts</td></tr>'

    # Count stats
    total = len(experiments)
    passing = sum(1 for e in experiments.values() if e.get("status") != "halted" and e.get("orphan_count", 0) == 0)
    halted = sum(1 for e in experiments.values() if e.get("status") == "halted")
    alert_count = len(alerts or [])
    total_pnl = sum(e.get("metrics", {}).get("total_pnl", 0) or 0 for e in experiments.values())
    total_realized = sum(e.get("metrics", {}).get("realized_pnl", 0) or 0 for e in experiments.values())
    total_unrealized = sum(e.get("metrics", {}).get("unrealized_pnl", 0) or 0 for e in experiments.values() if e.get("metrics", {}).get("unrealized_pnl") is not None)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SENTINEL Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #fff; color: #1a1a2e; padding: 2.5rem; line-height: 1.55; }}
  .page {{ max-width: 860px; margin: 0 auto; }}
  .header {{ margin-bottom: 2.5rem; padding-bottom: 1.2rem; border-bottom: 2px solid #1a1a2e; display: flex; justify-content: space-between; align-items: baseline; }}
  .header h1 {{ font-size: 1.4rem; font-weight: 800; }}
  .meta {{ font-size: 0.78rem; color: #888; }}
  .top-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.8rem; margin-bottom: 2rem; }}
  .top-card {{ padding: 1rem; border-radius: 8px; border: 1px solid #eee; }}
  .top-card .label {{ font-size: 0.65rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin-bottom: 0.3rem; }}
  .top-card .val {{ font-size: 1.5rem; font-weight: 800; }}
  .top-card .sub {{ font-size: 0.72rem; color: #888; margin-top: 0.15rem; }}
  .top-card.green {{ border-left: 3px solid #10b981; }}
  .top-card.red {{ border-left: 3px solid #e94560; }}
  .top-card.yellow {{ border-left: 3px solid #f59e0b; }}
  .top-card.blue {{ border-left: 3px solid #3b82f6; }}
  .section {{ margin-bottom: 2.8rem; }}
  .section-header {{ margin-bottom: 1.2rem; }}
  .section-header h2 {{ font-size: 1.05rem; font-weight: 700; margin-bottom: 0.3rem; }}
  .section-header p {{ font-size: 0.8rem; color: #666; line-height: 1.5; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  th {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 2px solid #ddd; font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: #999; }}
  td {{ padding: 0.5rem 0.6rem; border-bottom: 1px solid #f3f3f3; }}
  tr:hover {{ background: #fafafa; }}
  .p {{ display: inline-block; padding: 0.12em 0.5em; border-radius: 3px; font-size: 0.67rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.02em; }}
  .p-pass {{ background: #d1fae5; color: #065f46; }}
  .p-warn {{ background: #fef3c7; color: #92400e; }}
  .p-crit {{ background: #fee2e2; color: #991b1b; }}
  .p-halt {{ background: #fce7f3; color: #831843; }}
  .red {{ color: #e94560; font-weight: 600; }}
  .green {{ color: #10b981; font-weight: 600; }}
  .yellow {{ color: #d97706; font-weight: 600; }}
  .muted {{ color: #999; }}
  .mono {{ font-family: ui-monospace, monospace; font-size: 0.78rem; }}
  .bold {{ font-weight: 700; }}
  .divider {{ border: none; border-top: 1px solid #eee; margin: 2.5rem 0; }}
  .footer {{ font-size: 0.7rem; color: #bbb; margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #eee; }}
  @media (max-width: 600px) {{ .top-row {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <h1>🛡️ SENTINEL</h1>
    <span class="meta">Updated: {generated}</span>
  </div>

  <div class="top-row">
    <div class="top-card green">
      <div class="label">Experiments OK</div>
      <div class="val">{passing} / {total}</div>
    </div>
    <div class="top-card {'red' if alert_count > 0 else 'green'}">
      <div class="label">Alerts</div>
      <div class="val">{alert_count}</div>
    </div>
    <div class="top-card {'yellow' if halted > 0 else 'green'}">
      <div class="label">Halted</div>
      <div class="val">{halted}</div>
    </div>
    <div class="top-card blue">
      <div class="label">Total P&amp;L</div>
      <div class="val">${total_pnl:+,.0f}</div>
      <div class="sub">Realized ${total_realized:+,.0f} · Unrealized ${total_unrealized:+,.0f}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-header">
      <h2>Experiments</h2>
      <p><strong>What:</strong> Health of each experiment at a glance. <strong>Why:</strong> If something is wrong, you see it here first.</p>
    </div>
    <table>
      <thead><tr><th>Experiment</th><th>Status</th><th>Win Rate</th><th>vs Backtest</th><th>Realized</th><th>Unrealized</th><th>Total P&amp;L</th><th>Issues</th></tr></thead>
      <tbody>{exp_rows if exp_rows else '<tr><td colspan="8" class="muted" style="text-align:center">No data yet — run sync_sentinel_data.py</td></tr>'}</tbody>
    </table>
  </div>

  <hr class="divider">

  <div class="section">
    <div class="section-header">
      <h2>Config Integrity</h2>
      <p><strong>What:</strong> Valid configs, matching registry, correct DB schema, approved certification. <strong>Why:</strong> Bad config = bad trades.</p>
    </div>
    <table>
      <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody>{config_rows}</tbody>
    </table>
  </div>

  <hr class="divider">

  <div class="section">
    <div class="section-header">
      <h2>Recent Alerts</h2>
      <p><strong>What:</strong> Every alert Sentinel has fired. <strong>Why:</strong> Single place to see what happened and whether it's addressed.</p>
    </div>
    <table>
      <thead><tr><th>Time</th><th>Severity</th><th>Experiment</th><th>Alert</th></tr></thead>
      <tbody>{alert_rows}</tbody>
    </table>
  </div>

  <div class="footer">SENTINEL v2.0 · Data synced from Mac Studio · <a href="/api/v1/sentinel" style="color:#3b82f6">Raw JSON</a></div>
</div>
</body>
</html>"""

    return HTMLResponse(content=html)


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
                    if e.get("status") in ("active", "paper_trading")]
            logger.info(f"  Live experiments: {[e['id'] for e in live]}")
        except Exception as e:
            logger.warning(f"  Could not load registry: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("web_dashboard.app:app", host="0.0.0.0", port=port, reload=False)
