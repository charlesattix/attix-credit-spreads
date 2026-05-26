#!/usr/bin/env python3
"""
railway_watchdog.py — Lightweight health monitor for Railway deployment.

Adapted from scripts/watchdog.py for Railway's subprocess model:
  - Processes managed by railway_worker.py (Railway-native)
  - Checks: worker process status, heartbeat staleness, Alpaca API, DB recency
  - Sends Telegram alerts on issues
  - Emits JSON to stdout on every cycle (visible in Railway logs)
  - Runs continuously; check interval: WATCHDOG_INTERVAL_SECS (default 300)

Env vars:
    RAILWAY_VOLUME_MOUNT_PATH   — volume root (must match railway_worker.py)
    ALPACA_API_KEY_EXP400       — per-experiment credentials (same as worker)
    WATCHDOG_INTERVAL_SECS      — override check interval (default 300)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Path setup — must happen before local imports
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from experiments.manager import get_manager  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] watchdog: %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("railway_watchdog")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ET = timezone(timedelta(hours=-4))   # EDT approximation
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (16, 0)
HEARTBEAT_STALE_MINUTES = 45
DB_STALE_HOURS = 24

VOLUME_MOUNT = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").rstrip("/")
DATA_DIR = Path(VOLUME_MOUNT) if VOLUME_MOUNT else PROJECT_DIR / "data"
STATUS_FILE = DATA_DIR / ".worker_status.json"

CHECK_INTERVAL_SECS = int(os.environ.get("WATCHDOG_INTERVAL_SECS", "300"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_et() -> datetime:
    return datetime.now(ET)


def is_market_hours(now: Optional[datetime] = None) -> bool:
    now = now or _now_et()
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t < MARKET_CLOSE


def resolve_db_path(db_path: str) -> str:
    """Rewrite relative db_path onto the volume mount (mirrors railway_worker.py)."""
    if not db_path:
        return db_path
    if VOLUME_MOUNT and db_path.startswith("data/"):
        return str(Path(VOLUME_MOUNT) / db_path[len("data/"):])
    return db_path


def exp_env_suffix(exp_id: str) -> str:
    """'EXP-400' -> 'EXP400'."""
    return exp_id.replace("-", "").upper()


def _parse_env_file(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip("'\"")
    return result


def _heartbeat_key_from_db(db_path: str) -> str:
    """Derive heartbeat filename key from db_path — mirrors main.py's _write_heartbeat().

    main.py writes: data/.last_scan_{basename(db).replace("pilotai_","").replace(".db","")}
    e.g. data/pilotai_exp400.db -> "exp400"
         data/exp503/pilotai_exp503.db -> "exp503"
    """
    resolved = resolve_db_path(db_path)
    base = Path(resolved).name  # e.g. "pilotai_exp400.db"
    key = base.replace("pilotai_", "").replace(".db", "")
    return key  # e.g. "exp400"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_worker_process(exp_id: str) -> Optional[Dict[str, Any]]:
    """Read railway_worker.py's status file and return process info for exp_id."""
    try:
        if not STATUS_FILE.exists():
            return None
        data = json.loads(STATUS_FILE.read_text())
        return data.get("processes", {}).get(exp_id)
    except Exception as exc:
        logger.warning("Could not read worker status file: %s", exc)
        return None


def check_heartbeat(exp: dict) -> Optional[datetime]:
    """Read the heartbeat file written by main.py's _write_heartbeat()."""
    db_path = exp.get("db_path", "")
    if not db_path:
        return None
    key = _heartbeat_key_from_db(db_path)
    hb_path = DATA_DIR / f".last_scan_{key}"
    if not hb_path.exists():
        return None
    try:
        return datetime.fromisoformat(hb_path.read_text().strip())
    except Exception:
        return None


def check_alpaca_api(exp: dict) -> bool:
    """Ping Alpaca paper /v2/account using experiment credentials."""
    exp_id = exp.get("id", "")
    suffix = exp_env_suffix(exp_id)
    base_url = "https://paper-api.alpaca.markets"

    # Try Railway per-experiment vars first
    key = os.environ.get(f"ALPACA_API_KEY_{suffix}")
    secret = os.environ.get(f"ALPACA_API_SECRET_{suffix}")

    # Fall back to .env file
    if not key or not secret:
        env_file = exp.get("env_file", "")
        if env_file:
            file_vars = _parse_env_file(PROJECT_DIR / env_file)
            key = key or file_vars.get("ALPACA_API_KEY", "")
            secret = secret or file_vars.get("ALPACA_API_SECRET", "")

    if not key or not secret:
        logger.debug("[%s] No Alpaca credentials found — skipping API check", exp_id)
        return False

    try:
        req = urllib.request.Request(
            f"{base_url}/v2/account",
            headers={
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning("[%s] Alpaca API check failed: %s", exp_id, exc)
        return False


def check_db_recency(db_path: str) -> Optional[str]:
    """Return timestamp of most recent trade row, or None if DB missing / empty."""
    import sqlite3  # noqa: PLC0415

    resolved = resolve_db_path(db_path)
    path = Path(resolved)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        row = conn.execute("SELECT MAX(created_at) FROM trades").fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception as exc:
        logger.warning("DB recency check failed for %s: %s", resolved, exc)
        return None


def send_telegram_alert(message: str) -> bool:
    try:
        from shared.telegram_alerts import send_message  # noqa: PLC0415
        return send_message(message)
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# One check cycle
# ---------------------------------------------------------------------------


def run_check() -> Dict[str, Any]:
    """Run one full watchdog cycle. Returns JSON-serialisable status dict."""
    now = _now_et()
    market_open = is_market_hours(now)

    # Use .live() so paused experiments are still monitored
    live_experiments = get_manager().live()

    results: Dict[str, Any] = {
        "timestamp": now.isoformat(),
        "market_hours": market_open,
        "experiments": {},
        "alerts": [],
    }

    for exp in live_experiments:
        exp_id = exp.get("id", "unknown")
        exp_result: Dict[str, Any] = {"status": exp.get("status")}

        # ------------------------------------------------------------------
        # 1. Worker process alive? (via shared status file on volume)
        # ------------------------------------------------------------------
        proc_info = check_worker_process(exp_id)
        if proc_info is not None:
            alive = proc_info.get("alive", False)
            exp_result["process_alive"] = alive
            exp_result["pid"] = proc_info.get("pid")
            exp_result["restart_count"] = proc_info.get("restart_count", 0)
            if not alive:
                msg = (
                    f"🔴 <b>WATCHDOG: {exp_id} subprocess is DOWN</b>\n\n"
                    f"Restarts so far: {proc_info.get('restart_count', 0)}\n"
                    f"railway_worker.py is attempting auto-restart."
                )
                results["alerts"].append(f"{exp_id}: process down")
                send_telegram_alert(msg)
        else:
            # Status file not written yet (worker just started) or volume issue
            exp_result["process_alive"] = None

        # ------------------------------------------------------------------
        # 2. Heartbeat staleness (only meaningful during market hours)
        # ------------------------------------------------------------------
        hb_ts = check_heartbeat(exp)
        if hb_ts is not None:
            if hb_ts.tzinfo is None:
                hb_ts = hb_ts.replace(tzinfo=ET)
            exp_result["last_heartbeat"] = hb_ts.isoformat()
            if market_open:
                age_min = (now - hb_ts).total_seconds() / 60
                exp_result["heartbeat_age_min"] = round(age_min, 1)
                if age_min > HEARTBEAT_STALE_MINUTES:
                    msg = (
                        f"⚠️ <b>WATCHDOG: {exp_id} scanner stale</b>\n\n"
                        f"Last heartbeat: {hb_ts.strftime('%H:%M ET')} "
                        f"({round(age_min)}m ago)\n"
                        f"Threshold: {HEARTBEAT_STALE_MINUTES}m"
                    )
                    results["alerts"].append(f"{exp_id}: heartbeat stale ({round(age_min)}m)")
                    send_telegram_alert(msg)
        else:
            exp_result["last_heartbeat"] = None
            if market_open:
                exp_result["heartbeat_missing"] = True

        # ------------------------------------------------------------------
        # 3. Alpaca API connectivity
        # ------------------------------------------------------------------
        if exp.get("env_file") or os.environ.get(f"ALPACA_API_KEY_{exp_env_suffix(exp_id)}"):
            try:
                api_ok = check_alpaca_api(exp)
                exp_result["alpaca_api_ok"] = api_ok
                if not api_ok:
                    msg = (
                        f"⚠️ <b>WATCHDOG: {exp_id} Alpaca API unreachable</b>\n\n"
                        f"Env file: <code>{exp.get('env_file', 'N/A')}</code>"
                    )
                    results["alerts"].append(f"{exp_id}: Alpaca API down")
                    send_telegram_alert(msg)
            except Exception as exc:
                exp_result["alpaca_error"] = str(exc)
                logger.error("[%s] Alpaca check raised: %s", exp_id, exc)

        # ------------------------------------------------------------------
        # 4. DB write recency
        # ------------------------------------------------------------------
        db_path = exp.get("db_path", "")
        if db_path:
            exp_result["last_trade"] = check_db_recency(db_path)
        else:
            exp_result["last_trade"] = None

        results["experiments"][exp_id] = exp_result

    return results


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    logger.info("=" * 60)
    logger.info("Attix Railway Watchdog starting")
    logger.info("VOLUME_MOUNT  = %s", VOLUME_MOUNT or "(not set, using data/)")
    logger.info("CHECK_INTERVAL= %ds", CHECK_INTERVAL_SECS)
    logger.info("STATUS_FILE   = %s", STATUS_FILE)
    logger.info("=" * 60)

    while True:
        try:
            result = run_check()
            print(json.dumps(result, indent=2, default=str), flush=True)

            alert_count = len(result.get("alerts", []))
            if alert_count:
                logger.warning("Cycle done: %d alert(s) — %s", alert_count, result["alerts"])
            else:
                exp_count = len(result.get("experiments", {}))
                logger.info("Cycle done: %d experiment(s) all OK", exp_count)

        except Exception as exc:
            error_payload = {
                "timestamp": _now_et().isoformat(),
                "error": str(exc),
                "status": "watchdog_error",
            }
            print(json.dumps(error_payload, indent=2), flush=True)
            try:
                send_telegram_alert(
                    f"🚨 <b>RAILWAY WATCHDOG ERROR</b>\n\n"
                    f"<code>{exc}</code>\n\n"
                    f"Watchdog will retry in {CHECK_INTERVAL_SECS}s."
                )
            except Exception:
                pass

        time.sleep(CHECK_INTERVAL_SECS)


if __name__ == "__main__":
    main()
