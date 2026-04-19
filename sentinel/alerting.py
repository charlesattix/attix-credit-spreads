"""
sentinel/alerting.py — Reliable alert delivery with rate limiting, dedup, and retries.

Severity tiers:
  HALT     — experiment halted, immediate Carlos attention required
  CRITICAL — action required within the hour
  WARNING  — investigate soon
  INFO     — status/informational

Features:
  - Rate limiting: max 5 alerts per experiment per hour (prevents orphan spam)
  - Dedup: same alert key within 1 hour is suppressed
  - Retry: Telegram failures retried 3x with exponential backoff
  - Fallback: undelivered alerts written to sentinel/db/failed_alerts.log
  - Alert lifecycle: open -> acknowledged -> resolved with time-to-resolution
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FAILED_ALERTS_LOG = Path(__file__).parent / "db" / "failed_alerts.log"


# ---------------------------------------------------------------------------
# Severity model
# ---------------------------------------------------------------------------

class Severity(IntEnum):
    """Alert severity levels, ordered by urgency."""
    INFO = 0
    WARNING = 1
    CRITICAL = 2
    HALT = 3


SEVERITY_EMOJI = {
    Severity.INFO: "",
    Severity.WARNING: "\u26a0\ufe0f",
    Severity.CRITICAL: "\U0001f534",
    Severity.HALT: "\U0001f6a8",
}

SEVERITY_LABEL = {
    Severity.INFO: "INFO",
    Severity.WARNING: "WARNING",
    Severity.CRITICAL: "CRITICAL",
    Severity.HALT: "HALT",
}


@dataclass
class Alert:
    """A single sentinel alert."""
    severity: Severity
    experiment_id: Optional[str]
    gate_id: str
    message: str
    details: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    delivered: bool = False
    delivery_attempts: int = 0

    @property
    def dedup_key(self) -> str:
        """Key for dedup: same gate + experiment + message = same alert."""
        return f"{self.gate_id}:{self.experiment_id or 'system'}:{self.message}"

    def to_telegram(self) -> str:
        """Format as HTML for Telegram."""
        emoji = SEVERITY_EMOJI.get(self.severity, "")
        label = SEVERITY_LABEL.get(self.severity, "UNKNOWN")
        exp = f" <b>{self.experiment_id}</b>" if self.experiment_id else ""
        lines = [f"{emoji} <b>SENTINEL {label}</b>{exp}"]
        lines.append(f"Gate: {self.gate_id}")
        lines.append(self.message)
        if self.details:
            lines.append(f"<i>{self.details}</i>")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "severity": SEVERITY_LABEL.get(self.severity, "UNKNOWN"),
            "experiment_id": self.experiment_id,
            "gate_id": self.gate_id,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
            "delivered": self.delivered,
        }


# ---------------------------------------------------------------------------
# Rate limiter + dedup
# ---------------------------------------------------------------------------

# Per-experiment: timestamps of alerts sent in the current hour window
_rate_buckets: Dict[str, List[float]] = defaultdict(list)
_RATE_LIMIT_PER_HOUR = 5

# Dedup: {dedup_key: last_sent_timestamp}
_dedup_cache: Dict[str, float] = {}
_DEDUP_WINDOW_SECS = 3600  # 1 hour


def _is_rate_limited(experiment_id: Optional[str]) -> bool:
    """Check if we've hit the per-experiment rate limit."""
    key = experiment_id or "__system__"
    now = time.time()
    cutoff = now - 3600
    # Prune old entries
    _rate_buckets[key] = [t for t in _rate_buckets[key] if t > cutoff]
    return len(_rate_buckets[key]) >= _RATE_LIMIT_PER_HOUR


def _record_sent(experiment_id: Optional[str]) -> None:
    key = experiment_id or "__system__"
    _rate_buckets[key].append(time.time())


def _is_duplicate(alert: Alert) -> bool:
    """Check if this alert was already sent within the dedup window."""
    key = alert.dedup_key
    last = _dedup_cache.get(key)
    if last and (time.time() - last) < _DEDUP_WINDOW_SECS:
        return True
    return False


def _record_dedup(alert: Alert) -> None:
    _dedup_cache[alert.dedup_key] = time.time()


def clear_rate_limits() -> None:
    """Reset all rate limiting state (for testing)."""
    _rate_buckets.clear()
    _dedup_cache.clear()


# ---------------------------------------------------------------------------
# Telegram delivery with retries
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2  # seconds: 2, 4, 8


def _send_telegram(text: str) -> bool:
    """Send via shared.telegram_alerts with retry logic."""
    try:
        from shared.telegram_alerts import send_message, is_configured
        if not is_configured():
            return False
    except ImportError:
        logger.warning("shared.telegram_alerts not importable")
        return False

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if send_message(text, parse_mode="HTML"):
                return True
        except Exception as e:
            logger.warning("Telegram attempt %d/%d failed: %s", attempt, _MAX_RETRIES, e)

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_BACKOFF_BASE ** attempt)

    return False


def _log_failed_alert(alert: Alert) -> None:
    """Write undelivered alert to fallback log file."""
    _FAILED_ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_FAILED_ALERTS_LOG, "a") as f:
            f.write(json.dumps(alert.to_dict()) + "\n")
    except Exception as e:
        logger.error("Failed to write alert to fallback log: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_alert(alert: Alert, *, force: bool = False) -> bool:
    """
    Send a single alert with rate limiting, dedup, retry, and fallback.

    Args:
        alert: The alert to send.
        force: If True, bypass rate limiting and dedup (for HALT severity).

    Returns True if delivered via Telegram, False otherwise.
    """
    # HALT severity always forces delivery
    if alert.severity == Severity.HALT:
        force = True

    if not force:
        if _is_duplicate(alert):
            logger.debug("Alert suppressed (dedup): %s", alert.dedup_key)
            return False

        if _is_rate_limited(alert.experiment_id):
            logger.warning(
                "Alert rate-limited for %s (>%d/hour): %s",
                alert.experiment_id or "system", _RATE_LIMIT_PER_HOUR, alert.message,
            )
            _log_failed_alert(alert)
            return False

    delivered = _send_telegram(alert.to_telegram())
    alert.delivered = delivered
    alert.delivery_attempts += 1

    if delivered:
        _record_sent(alert.experiment_id)
        _record_dedup(alert)
        logger.info("Alert delivered: [%s] %s — %s",
                     SEVERITY_LABEL.get(alert.severity), alert.experiment_id, alert.message)
    else:
        _log_failed_alert(alert)
        logger.error("Alert delivery failed after %d attempts: %s", _MAX_RETRIES, alert.message)

    return delivered


def send_alerts(alerts: List[Alert]) -> int:
    """Send multiple alerts. Returns count of successfully delivered."""
    delivered = 0
    for alert in sorted(alerts, key=lambda a: a.severity, reverse=True):
        if send_alert(alert):
            delivered += 1
    return delivered


def record_alert_to_db(alert: Alert) -> Optional[int]:
    """Persist alert to sentinel.db alerts_log. Returns row ID or None."""
    try:
        from sentinel.history import SentinelDB
        db = SentinelDB()
        severity_str = SEVERITY_LABEL.get(alert.severity, "info").lower()
        return db.record_alert(
            severity_str,
            f"[{alert.gate_id}] {alert.message}",
            experiment_id=alert.experiment_id,
        )
    except Exception as e:
        logger.error("Failed to record alert to DB: %s", e)
        return None


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def gate_alert(
    severity: Severity,
    gate_id: str,
    experiment_id: Optional[str],
    message: str,
    details: Optional[str] = None,
) -> Alert:
    """Create a gate result alert."""
    return Alert(
        severity=severity,
        experiment_id=experiment_id,
        gate_id=gate_id,
        message=message,
        details=details,
    )


def halt_alert(gate_id: str, experiment_id: str, message: str) -> Alert:
    """Convenience for HALT severity."""
    return gate_alert(Severity.HALT, gate_id, experiment_id, message)


def critical_alert(gate_id: str, experiment_id: Optional[str], message: str) -> Alert:
    """Convenience for CRITICAL severity."""
    return gate_alert(Severity.CRITICAL, gate_id, experiment_id, message)


def warning_alert(gate_id: str, experiment_id: Optional[str], message: str) -> Alert:
    """Convenience for WARNING severity."""
    return gate_alert(Severity.WARNING, gate_id, experiment_id, message)
