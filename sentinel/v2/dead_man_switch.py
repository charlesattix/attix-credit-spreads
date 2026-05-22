"""
sentinel/v2/dead_man_switch.py — External dead man's switch via healthchecks.io.

The watchdog calls push_heartbeat() every 4 hours. If the watchdog dies,
healthchecks.io sends an alert to Carlos independently of Railway, Telegram,
or any internal system component.

Env var required: HEALTHCHECKS_PING_URL=https://hc-ping.com/{UUID}

To signal an active failure (system alive but something critical): push_failure()
"""

from __future__ import annotations

import logging
import os
import urllib.request

LOG = logging.getLogger("sentinel.v2.dead_man_switch")


def _ping_url(suffix: str = "") -> str | None:
    base = os.environ.get("HEALTHCHECKS_PING_URL", "").rstrip("/")
    if not base:
        return None
    return f"{base}{suffix}"


def push_heartbeat() -> bool:
    """
    Push a liveness ping to healthchecks.io.
    Call every 4 hours. Returns True if ping was accepted.
    Never raises — heartbeat failure must not crash the watchdog.
    """
    url = _ping_url()
    if not url:
        LOG.debug("HEALTHCHECKS_PING_URL not set — dead man's switch disabled")
        return False
    try:
        urllib.request.urlopen(url, timeout=10)
        LOG.info("dead_man_switch: heartbeat OK → %s", url[:60])
        return True
    except Exception as exc:
        LOG.error("dead_man_switch: heartbeat FAILED: %s", exc)
        return False


def push_failure(reason: str) -> bool:
    """
    Signal an active failure to healthchecks.io via the /fail endpoint.
    healthchecks.io will immediately send an alert even if the ping cadence
    hasn't expired yet. Use for CRITICAL gate fires that warrant instant
    external notification.
    """
    url = _ping_url("/fail")
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url,
            data=reason[:1000].encode(),
            method="POST",
            headers={"Content-Type": "text/plain"},
        )
        urllib.request.urlopen(req, timeout=10)
        LOG.warning("dead_man_switch: failure signal sent: %s", reason[:120])
        return True
    except Exception as exc:
        LOG.error("dead_man_switch: failure signal FAILED: %s", exc)
        return False


def push_start() -> bool:
    """Signal the start of a long operation (for duration tracking in healthchecks.io)."""
    url = _ping_url("/start")
    if not url:
        return False
    try:
        urllib.request.urlopen(url, timeout=5)
        return True
    except Exception:
        return False
