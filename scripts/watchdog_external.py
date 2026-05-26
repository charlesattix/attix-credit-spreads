#!/usr/bin/env python3
"""
scripts/watchdog_external.py — External infrastructure watchdog for Attix.

Runs on a VPS cron (NOT on Railway). Every 30 minutes it:
  1. Checks all 3 Railway services (Vesper, Sentinel, Dashboard) are responding
  2. Checks all 8 Alpaca paper accounts are accessible
  3. Sends Telegram alerts on any failure
  4. Writes status to data/watchdog_status.json
  5. POSTs the status JSON to the dashboard API

Usage (add to crontab):
    */30 * * * * cd /path/to/pilotai-credit-spreads && python scripts/watchdog_external.py

Dependencies: requests (pip install requests)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: 'requests' is required. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "451136954")

DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL",
    "https://attix-dashboard-production.up.railway.app",
)
DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "dev-attix-2026")

# Railway service health endpoints — set via env vars on your VPS
# if you know the exact URLs; otherwise override here.
VESPER_HEALTH_URL = os.environ.get(
    "VESPER_HEALTH_URL",
    "https://vesper-production.up.railway.app/health",
)
SENTINEL_HEALTH_URL = os.environ.get(
    "SENTINEL_HEALTH_URL",
    "https://sentinel-watchdog-production.up.railway.app/health",
)

SERVICES: dict[str, str] = {
    "vesper":    VESPER_HEALTH_URL,
    "sentinel":  SENTINEL_HEALTH_URL,
    "dashboard": f"{DASHBOARD_URL}/api/v1/health",
}

ALPACA_PAPER_BASE = "https://paper-api.alpaca.markets"
ALPACA_ACCOUNTS: dict[str, dict[str, str]] = {
    "EXP-400":  {"key": "PKHZBPRHIZ4FRRLNZZSEAPJJ23",  "secret": "FLnnxYnU3KcnNM9fMYqFPiFJ6ADq3nN6ipsQTn5ExYNo"},
    "EXP-401":  {"key": "PK7KLFZNLQA22Y6OMEGS2XJGJ4",  "secret": "DHkWajJrtXkNty8MWbU7v7WKm256r2CiMnJ3KiYeGKwb"},
    "EXP-503":  {"key": "PKSKGSOLW6SX7VKR2YIWXNB6MW",  "secret": "DfUwfZ59UrBBuXSTKyfoCnKCTJ4zBcLYk113XNWSefby"},
    # EXP-600 (IBIT Adaptive) retired 2026-05-26 — closed by Carlos. Account no longer monitored.
    "EXP-800":  {"key": "PKWAX5X42WPNHXNSOJM63C3BWI",  "secret": "8witcXD6DfCVPgDpPAB1vxfpMLpA6xs9uNG7Ttbii2AU"},
    "EXP-1220": {"key": "PKN545QBNANEWHYD3OEINFLYAB",  "secret": "EeGuu28ii2AZvS9aQLMwdvtZtoWSg3bWbnpwY8ZU6D7C"},
    "EXP-3309": {"key": "PKMLJBHZRFHZBKGK4YN6DO5IOO",  "secret": "8AtNSEM47dJFjhk8D9nMd1sPcAViCLXEqkj9Np9hZde3"},
    "EXP-3311": {"key": "PKQFFZVC76SBED4G7DTG5UG7YW",  "secret": "71uBJGHpUqtBnDZHPah5RdbSoThwZjQ2YFGWvgVzFGQ2"},
}

REQUEST_TIMEOUT = 15  # seconds
STATUS_FILE = Path(__file__).resolve().parent.parent / "data" / "watchdog_status.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger("watchdog_external")

if not TELEGRAM_BOT_TOKEN:
    print(
        "ERROR: TELEGRAM_BOT_TOKEN env var is not set. "
        "Set it before running this script.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Service health checks
# ---------------------------------------------------------------------------

def check_service(name: str, url: str) -> dict[str, Any]:
    """Check a single HTTP health endpoint. Returns status dict."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code < 500:
            return {"status": "ok", "last_ok": now_iso, "error": None, "http_status": resp.status_code}
        return {
            "status": "error",
            "last_ok": None,
            "error": f"HTTP {resp.status_code}",
            "http_status": resp.status_code,
        }
    except requests.exceptions.ConnectionError as exc:
        return {"status": "error", "last_ok": None, "error": f"Connection refused: {exc}", "http_status": None}
    except requests.exceptions.Timeout:
        return {"status": "error", "last_ok": None, "error": f"Timeout after {REQUEST_TIMEOUT}s", "http_status": None}
    except Exception as exc:
        return {"status": "error", "last_ok": None, "error": str(exc), "http_status": None}


def check_all_services() -> dict[str, dict[str, Any]]:
    results = {}
    for name, url in SERVICES.items():
        logger.info("Checking service: %s → %s", name, url)
        results[name] = check_service(name, url)
        status = results[name]["status"]
        logger.info("  %s: %s", name, status if status == "ok" else results[name]["error"])
    return results


# ---------------------------------------------------------------------------
# Alpaca account checks
# ---------------------------------------------------------------------------

def check_alpaca_account(exp_id: str, key: str, secret: str) -> dict[str, Any]:
    """Quick auth check — just hits /v2/account to confirm keys work."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.get(
            f"{ALPACA_PAPER_BASE}/v2/account",
            headers={
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "status": "ok",
                "last_ok": now_iso,
                "error": None,
                "equity": float(data.get("equity", 0)),
            }
        return {
            "status": "error",
            "last_ok": None,
            "error": f"HTTP {resp.status_code}: {resp.text[:120]}",
            "equity": None,
        }
    except requests.exceptions.Timeout:
        return {"status": "error", "last_ok": None, "error": f"Timeout after {REQUEST_TIMEOUT}s", "equity": None}
    except Exception as exc:
        return {"status": "error", "last_ok": None, "error": str(exc), "equity": None}


def check_all_alpaca() -> dict[str, dict[str, Any]]:
    results = {}
    for exp_id, creds in ALPACA_ACCOUNTS.items():
        logger.info("Checking Alpaca account: %s", exp_id)
        results[exp_id] = check_alpaca_account(exp_id, creds["key"], creds["secret"])
        status = results[exp_id]["status"]
        if status == "ok":
            equity = results[exp_id].get("equity")
            logger.info("  %s: ok  equity=$%.2f", exp_id, equity or 0)
        else:
            logger.warning("  %s: ERROR — %s", exp_id, results[exp_id]["error"])
    return results


# ---------------------------------------------------------------------------
# Alert logic
# ---------------------------------------------------------------------------

def _collect_failures(
    services: dict[str, dict],
    alpaca: dict[str, dict],
) -> list[str]:
    failures = []
    for name, s in services.items():
        if s["status"] != "ok":
            failures.append(f"Service <b>{name}</b>: {s['error']}")
    for exp_id, s in alpaca.items():
        if s["status"] != "ok":
            failures.append(f"Alpaca <b>{exp_id}</b>: {s['error']}")
    return failures


def maybe_alert(failures: list[str], prev_status: dict) -> int:
    """Send Telegram alert if there are failures. Returns number of alerts sent."""
    if not failures:
        return 0

    prev_failures = set()
    if prev_status.get("services"):
        for name, s in prev_status["services"].items():
            if s.get("status") != "ok":
                prev_failures.add(f"service:{name}")
    if prev_status.get("alpaca_accounts"):
        for exp_id, s in prev_status["alpaca_accounts"].items():
            if s.get("status") != "ok":
                prev_failures.add(f"alpaca:{exp_id}")

    # Build alert message
    lines = ["<b>🔴 Attix Watchdog ALERT</b>", ""]
    for f in failures:
        lines.append(f"• {f}")
    lines.append("")
    lines.append(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    text = "\n".join(lines)
    logger.warning("Sending Telegram alert: %d failure(s)", len(failures))
    sent = send_telegram(text)
    return 1 if sent else 0


# ---------------------------------------------------------------------------
# Status persistence
# ---------------------------------------------------------------------------

def load_prev_status() -> dict:
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text())
    except Exception:
        pass
    return {}


def save_status(status: dict) -> None:
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(status, indent=2))
        logger.info("Status written to %s", STATUS_FILE)
    except Exception as exc:
        logger.error("Failed to write status file: %s", exc)


# ---------------------------------------------------------------------------
# Dashboard POST
# ---------------------------------------------------------------------------

def post_to_dashboard(status: dict) -> bool:
    """POST status JSON to the dashboard watchdog endpoint."""
    url = f"{DASHBOARD_URL}/api/v1/watchdog-status"
    try:
        resp = requests.post(
            url,
            json=status,
            headers={"X-API-Key": DASHBOARD_API_KEY},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            logger.info("Status posted to dashboard OK")
            return True
        logger.warning("Dashboard POST returned HTTP %s: %s", resp.status_code, resp.text[:120])
        return False
    except Exception as exc:
        logger.warning("Dashboard POST failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=" * 60)
    logger.info("Attix External Watchdog starting")
    logger.info("=" * 60)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prev_status = load_prev_status()

    # --- Run checks ---
    services   = check_all_services()
    alpaca     = check_all_alpaca()

    # --- Determine overall ---
    svc_ok  = all(s["status"] == "ok" for s in services.values())
    alp_ok  = all(s["status"] == "ok" for s in alpaca.values())
    overall = "ok" if (svc_ok and alp_ok) else "error"

    # --- Preserve last_ok from prev status when still failing ---
    for name, s in services.items():
        if s["status"] != "ok" and prev_status.get("services", {}).get(name, {}).get("last_ok"):
            s["last_ok"] = prev_status["services"][name]["last_ok"]
    for exp_id, s in alpaca.items():
        if s["status"] != "ok" and prev_status.get("alpaca_accounts", {}).get(exp_id, {}).get("last_ok"):
            s["last_ok"] = prev_status["alpaca_accounts"][exp_id]["last_ok"]

    # --- Alerts ---
    failures     = _collect_failures(services, alpaca)
    alerts_sent  = maybe_alert(failures, prev_status)
    prev_alerts  = prev_status.get("alerts_sent", 0)

    status: dict[str, Any] = {
        "last_check":      now_iso,
        "services":        services,
        "alpaca_accounts": alpaca,
        "alerts_sent":     prev_alerts + alerts_sent,
        "overall":         overall,
    }

    # --- Persist locally ---
    save_status(status)

    # --- Push to dashboard ---
    post_to_dashboard(status)

    # --- Summary ---
    logger.info("=" * 60)
    logger.info("Overall: %s", overall.upper())
    if failures:
        for f in failures:
            logger.warning("  FAIL: %s", f)
    else:
        logger.info("  All checks passed")
    logger.info("=" * 60)

    sys.exit(0 if overall == "ok" else 1)


if __name__ == "__main__":
    main()
