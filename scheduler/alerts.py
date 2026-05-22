"""
scheduler/alerts.py — Telegram with retry and file-based fallback.

Never loses an alert. If Telegram is down, writes to /data/logs/failed_alerts.jsonl.
Attempts: 1 send -> 3s wait -> 1 retry -> if still fails -> write to file.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger("scheduler.alerts")
_DATA_DIR = Path(os.environ.get("COMPASS_DATA_DIR", "/data"))
_FAILED_ALERTS_LOG = _DATA_DIR / "logs" / "failed_alerts.jsonl"


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message. Retries once. Falls back to file on failure.
    Returns True if sent successfully via Telegram, False otherwise."""
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    LOG.info("TELEGRAM: %s", message[:200])

    if not token or not chat_id:
        LOG.debug("Telegram not configured — writing to file")
        _write_failed_alert(message, "not_configured")
        return False

    for attempt in range(2):
        if attempt > 0:
            time.sleep(3)
        try:
            url  = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({
                "chat_id": chat_id,
                "text": message[:4000],  # Telegram 4096 char limit
                "parse_mode": parse_mode,
            }).encode()
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            LOG.warning("Telegram attempt %d failed: %s", attempt + 1, e)

    # Both attempts failed — write to file (never lose the alert)
    LOG.error("ALERT_FALLBACK: Telegram down, writing to %s", _FAILED_ALERTS_LOG)
    _write_failed_alert(message, "telegram_down")
    return False


def _write_failed_alert(message: str, reason: str) -> None:
    """Write an alert that couldn't be sent to Telegram to a JSONL file."""
    try:
        _FAILED_ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "reason": reason,
            "message": message,
        }
        with open(_FAILED_ALERTS_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        LOG.error("Could not write failed alert to file: %s", e)
