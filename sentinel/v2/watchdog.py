"""
sentinel/v2/watchdog.py — Sentinel v3: API-based watchdog.

Monitors via HTTP API calls only — no filesystem reads, no shared volumes.
Designed for Railway where each service runs in an isolated container.

Three checks every 5 min during market hours (9:00-16:30 ET, Mon-Fri):
  1. Alpaca heartbeat — direct GET /v2/account per active experiment
  2. Scan recency   — dashboard heartbeat endpoint (worker pushes after each scan)
  3. Trade recency  — dashboard trades endpoint; YELLOW >3 days, RED >5 days

Entry point: python -m sentinel.v2.watchdog
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests as _requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import fastapi
import uvicorn

from sentinel.alerting import Alert, Severity, send_alert

LOG = logging.getLogger("sentinel.v3.watchdog")
ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Config from env vars
# ---------------------------------------------------------------------------

_DASHBOARD_URL = os.environ.get("RAILWAY_SERVICE_ATTIX_CREDIT_SPREADS_URL", "")
_DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")
_REQUEST_TIMEOUT = 10  # seconds per HTTP call

# ---------------------------------------------------------------------------
# In-memory state — check results stored here, served via /health
# ---------------------------------------------------------------------------

_check_results: dict[str, Any] = {
    "alpaca_heartbeat": {"status": "pending", "experiments_checked": 0, "failures": []},
    "scan_recency":     {"status": "pending", "stale_experiments": []},
    "trade_recency":    {"status": "pending", "yellow": [], "red": []},
}
_last_check: datetime | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_market_hours() -> bool:
    """True if current ET time is Mon-Fri 09:00-16:30."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def _dashboard_headers() -> dict[str, str]:
    return {"X-API-Key": _DASHBOARD_API_KEY}


def _dashboard_base() -> str:
    url = _DASHBOARD_URL.strip()
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    return url.rstrip("/")


def _send_alert(message: str, severity: Severity, exp_id: str | None = None) -> None:
    try:
        alert = Alert(
            severity=severity,
            experiment_id=exp_id or "__WATCHDOG__",
            gate_id="G_WATCHDOG_V3",
            message=message,
        )
        send_alert(alert, force=(severity >= Severity.CRITICAL))
    except Exception as exc:
        LOG.error("alert dispatch failed: %s", exc)


# ---------------------------------------------------------------------------
# Dashboard API helpers
# ---------------------------------------------------------------------------

def _get_active_experiments() -> list[dict]:
    """Fetch active experiments from dashboard API."""
    base = _dashboard_base()
    if not base or not _DASHBOARD_API_KEY:
        LOG.warning("RAILWAY_SERVICE_ATTIX_CREDIT_SPREADS_URL or DASHBOARD_API_KEY not set")
        return []
    try:
        resp = _requests.get(
            f"{base}/api/v1/experiments",
            headers=_dashboard_headers(),
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        exps = data.get("experiments", [])
        return [e for e in exps if e.get("status") == "active"]
    except Exception as exc:
        LOG.error("failed to fetch experiments from dashboard: %s", exc)
        return []


def _alpaca_creds_for(exp_id: str) -> tuple[str, str] | None:
    """Look up ALPACA_API_KEY_{SUFFIX} / ALPACA_API_SECRET_{SUFFIX} from env."""
    suffix = exp_id.upper().replace("-", "")
    key    = os.environ.get(f"ALPACA_API_KEY_{suffix}", "")
    secret = os.environ.get(f"ALPACA_API_SECRET_{suffix}", "")
    if key and secret:
        return key, secret
    return None


# ---------------------------------------------------------------------------
# Check 1: Alpaca Heartbeat
# ---------------------------------------------------------------------------

def check_alpaca_heartbeat(experiments: list[dict]) -> dict:
    failures: list[dict] = []

    for exp in experiments:
        exp_id = exp.get("id", "")
        creds = _alpaca_creds_for(exp_id)
        if not creds:
            LOG.debug("no Alpaca creds for %s — skipping", exp_id)
            continue

        key, secret = creds
        try:
            resp = _requests.get(
                "https://paper-api.alpaca.markets/v2/account",
                headers={
                    "APCA-API-KEY-ID":     key,
                    "APCA-API-SECRET-KEY": secret,
                },
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                failures.append({"exp_id": exp_id, "reason": f"HTTP {resp.status_code}"})
                _send_alert(
                    f"[ALPACA] {exp_id}: account unreachable (HTTP {resp.status_code})",
                    Severity.CRITICAL, exp_id,
                )
                continue

            acct = resp.json()
            status = acct.get("status", "")
            equity = float(acct.get("equity", 0) or 0)

            if status != "ACTIVE":
                failures.append({"exp_id": exp_id, "reason": f"account status={status}"})
                _send_alert(
                    f"[ALPACA] {exp_id}: account status={status} (expected ACTIVE)",
                    Severity.CRITICAL, exp_id,
                )
            elif equity == 0:
                failures.append({"exp_id": exp_id, "reason": "equity=0"})
                _send_alert(
                    f"[ALPACA] {exp_id}: account equity is 0",
                    Severity.CRITICAL, exp_id,
                )
            else:
                LOG.info("[alpaca] %s ok — status=%s equity=%.2f", exp_id, status, equity)

        except Exception as exc:
            failures.append({"exp_id": exp_id, "reason": str(exc)})
            _send_alert(
                f"[ALPACA] {exp_id}: exception checking account: {exc}",
                Severity.CRITICAL, exp_id,
            )

    status = "ok" if not failures else "critical"
    return {
        "status": status,
        "experiments_checked": len(experiments),
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Check 2: Scan Recency
# ---------------------------------------------------------------------------

def check_scan_recency(experiments: list[dict]) -> dict:
    base = _dashboard_base()
    stale: list[str] = []
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(minutes=15)

    for exp in experiments:
        exp_id = exp.get("id", "")
        if not base or not _DASHBOARD_API_KEY:
            break
        try:
            resp = _requests.get(
                f"{base}/api/v1/experiments/{exp_id}/heartbeat",
                headers=_dashboard_headers(),
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 404:
                # No heartbeat yet — only alert if market hours
                if _is_market_hours():
                    stale.append(exp_id)
                    _send_alert(
                        f"[SCAN] {exp_id}: no scan heartbeat recorded yet during market hours",
                        Severity.CRITICAL, exp_id,
                    )
                continue
            resp.raise_for_status()
            hb = resp.json()
            scan_time_str = hb.get("scan_time")
            if not scan_time_str:
                stale.append(exp_id)
                continue

            scan_time = datetime.fromisoformat(scan_time_str.replace("Z", "+00:00"))
            if scan_time.tzinfo is None:
                scan_time = scan_time.replace(tzinfo=timezone.utc)

            if scan_time < cutoff:
                stale.append(exp_id)
                age_min = int((now_utc - scan_time).total_seconds() / 60)
                _send_alert(
                    f"[SCAN] {exp_id}: last scan {age_min}m ago (>{15}m threshold)",
                    Severity.CRITICAL, exp_id,
                )
            else:
                LOG.info("[scan] %s ok — last scan at %s", exp_id, scan_time_str)

        except Exception as exc:
            LOG.error("[scan] error checking heartbeat for %s: %s", exp_id, exc)

    status = "ok" if not stale else "critical"
    return {"status": status, "stale_experiments": stale}


# ---------------------------------------------------------------------------
# Check 3: Trade Recency
# ---------------------------------------------------------------------------

def check_trade_recency(experiments: list[dict]) -> dict:
    base = _dashboard_base()
    yellow: list[str] = []
    red: list[str] = []
    now_utc = datetime.now(timezone.utc)

    for exp in experiments:
        exp_id = exp.get("id", "")
        live_since_str = exp.get("live_since") or exp.get("created_at") or exp.get("enrolled_at")

        # How long has this experiment been live?
        live_days: float | None = None
        if live_since_str:
            try:
                live_since = datetime.fromisoformat(live_since_str.replace("Z", "+00:00"))
                if live_since.tzinfo is None:
                    live_since = live_since.replace(tzinfo=timezone.utc)
                live_days = (now_utc - live_since).total_seconds() / 86400
            except Exception:
                pass

        if not base or not _DASHBOARD_API_KEY:
            break
        try:
            resp = _requests.get(
                f"{base}/api/v1/experiments/{exp_id}/trades",
                headers=_dashboard_headers(),
                params={"limit": 1},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            trades = data.get("trades", [])

            if not trades:
                # Zero trades ever
                if live_days is not None and live_days > 5:
                    red.append(exp_id)
                    _send_alert(
                        f"[TRADES] {exp_id}: ZERO trades after {live_days:.0f} days live",
                        Severity.CRITICAL, exp_id,
                    )
                else:
                    LOG.info("[trades] %s has no trades yet (live %.1f days)", exp_id, live_days or 0)
                continue

            # Find most recent trade timestamp
            last_ts_str: str | None = None
            for t in trades:
                ts = t.get("closed_at") or t.get("open_time") or t.get("timestamp")
                if ts and (last_ts_str is None or ts > last_ts_str):
                    last_ts_str = ts

            if not last_ts_str:
                LOG.warning("[trades] %s: trades returned but no timestamp field found", exp_id)
                continue

            last_ts = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age_days = (now_utc - last_ts).total_seconds() / 86400

            if age_days > 5:
                red.append(exp_id)
                _send_alert(
                    f"[TRADES] {exp_id}: no trade in {age_days:.1f} days (RED >5d threshold)",
                    Severity.CRITICAL, exp_id,
                )
            elif age_days > 3:
                yellow.append(exp_id)
                _send_alert(
                    f"[TRADES] {exp_id}: no trade in {age_days:.1f} days (YELLOW >3d threshold)",
                    Severity.WARNING, exp_id,
                )
            else:
                LOG.info("[trades] %s ok — last trade %.1f days ago", exp_id, age_days)

        except Exception as exc:
            LOG.error("[trades] error checking trades for %s: %s", exp_id, exc)

    overall = "ok"
    if red:
        overall = "critical"
    elif yellow:
        overall = "yellow"
    return {"status": overall, "yellow": yellow, "red": red}


# ---------------------------------------------------------------------------
# Scheduled job — all three checks
# ---------------------------------------------------------------------------

def job_run_checks() -> None:
    global _last_check

    if not _is_market_hours():
        return

    LOG.info("sentinel v3: running checks")

    experiments = _get_active_experiments()
    if not experiments:
        LOG.warning("sentinel v3: no active experiments found — check dashboard connection")

    _check_results["alpaca_heartbeat"] = check_alpaca_heartbeat(experiments)
    _check_results["scan_recency"]     = check_scan_recency(experiments)
    _check_results["trade_recency"]    = check_trade_recency(experiments)
    _last_check = datetime.now(timezone.utc)

    LOG.info(
        "sentinel v3: checks done — alpaca=%s scan=%s trades=%s",
        _check_results["alpaca_heartbeat"]["status"],
        _check_results["scan_recency"]["status"],
        _check_results["trade_recency"]["status"],
    )


# ---------------------------------------------------------------------------
# FastAPI health endpoint
# ---------------------------------------------------------------------------

app = fastapi.FastAPI(title="Sentinel v3 Watchdog")


@app.get("/health")
def health():
    return {
        "alive":      True,
        "last_check": _last_check.isoformat() if _last_check else None,
        "checks":     _check_results,
    }


@app.post("/api/trigger-check")
def trigger_check():
    """Manually trigger an immediate check cycle (for testing)."""
    import threading
    threading.Thread(target=job_run_checks, daemon=True).start()
    return {"status": "triggered"}


# ---------------------------------------------------------------------------
# Scheduler + main
# ---------------------------------------------------------------------------

def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=ET)
    # Every 5 minutes during market hours (09:00-16:30 ET, Mon-Fri)
    sched.add_job(
        job_run_checks,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5", timezone=ET),
        id="sentinel_v3_checks",
        misfire_grace_time=60,
        coalesce=True,
    )
    return sched


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not _DASHBOARD_API_KEY:
        LOG.warning("DASHBOARD_API_KEY not set — sentinel cannot reach dashboard API")
    if not _DASHBOARD_URL:
        LOG.warning("RAILWAY_SERVICE_ATTIX_CREDIT_SPREADS_URL not set — sentinel cannot reach dashboard")

    scheduler = build_scheduler()
    scheduler.start()

    LOG.info("Sentinel v3 watchdog started — monitoring via API calls only")

    _send_alert(
        "[SENTINEL V3] Watchdog started on Railway.\n"
        f"Dashboard URL: {_DASHBOARD_URL or 'NOT SET'}\n"
        "Checks: Alpaca heartbeat, scan recency, trade recency\n"
        "Schedule: every 5 min during market hours (Mon-Fri 9:00-16:30 ET)",
        Severity.INFO,
    )

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
