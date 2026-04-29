"""
Sentinel cadence — single source of truth for "how often Sentinel runs".

Used by:
  - web_dashboard/html.py to size G3 + staleness thresholds against the real
    cadence (no more hard-coded "<2h" / ">=24h" magic numbers).
  - scripts/sync_sentinel_data.py meta-monitor to decide whether the push
    pipeline has gone silent.
  - tests, which can override the cadence to verify thresholds scale.

The default (3600s = hourly) matches the launchd plist
`com.attix.sentinel.plist` (StartInterval=3600). Override via the
SENTINEL_CADENCE_SECONDS env var if the cron interval changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Default Sentinel cron cadence in seconds. Matches launchd StartInterval.
DEFAULT_CADENCE_SECONDS = 3600


def expected_cadence_seconds() -> int:
    """
    Return the expected sentinel cadence in seconds.

    Reads SENTINEL_CADENCE_SECONDS env var if set, otherwise returns the
    default (3600s = hourly). Falls back to default on any parse error so
    a malformed env var can never silently zero-out staleness detection.
    """
    raw = os.environ.get("SENTINEL_CADENCE_SECONDS")
    if not raw:
        return DEFAULT_CADENCE_SECONDS
    try:
        val = int(raw)
        return val if val > 0 else DEFAULT_CADENCE_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_CADENCE_SECONDS


@dataclass(frozen=True)
class StalenessThresholds:
    """
    Severity boundaries (in hours since last_health_check) for G3 / push-age
    monitoring.

    Boundaries are derived from cadence so they scale automatically when the
    cron interval is changed. The shape:

        ok       : age < cadence + 1h            (one missed cycle is fine)
        warning  : cadence+1h .. cadence+12h     (a few cycles missed)
        critical : cadence+12h .. 48h            (sustained outage)
        halt     : age >= 48h                    (give up — clearly broken)

    For the default hourly cadence: ok <2h, warn 2-13h, crit 13-48h, halt >=48h.
    """
    ok_max_h: float
    warning_max_h: float
    critical_max_h: float

    @classmethod
    def from_cadence(cls, cadence_seconds: int | None = None) -> "StalenessThresholds":
        cad_h = (cadence_seconds or expected_cadence_seconds()) / 3600.0
        return cls(
            ok_max_h=cad_h + 1.0,
            warning_max_h=cad_h + 12.0,
            critical_max_h=48.0,
        )

    def severity_for_age(self, age_h: float) -> str:
        """Map an age (hours) to a severity bucket."""
        if age_h < self.ok_max_h:
            return "ok"
        if age_h < self.warning_max_h:
            return "warning"
        if age_h < self.critical_max_h:
            return "critical"
        return "halt"


def staleness_score_penalty(age_h: float, cadence_seconds: int | None = None) -> int:
    """
    Single-source staleness penalty for the health score (0-30).

    Smooth gradient — no cliffs, no double-counting:

        age <= cadence + 1h         : 0
        cadence+1h .. cadence+12h   : linear 0 → 10  (1 per hour)
        cadence+12h .. 48h          : linear 10 → 25 (≈0.4 per hour)
        > 48h                       : 25 (clamped — already capped before halt)

    The score function in web_dashboard/html.py applies *only* this penalty
    for staleness; it does not also deduct for the G3 gate severity. That
    eliminated the -25 cliff Charles diagnosed at the 24h boundary.
    """
    thr = StalenessThresholds.from_cadence(cadence_seconds)
    if age_h <= thr.ok_max_h:
        return 0
    if age_h < thr.warning_max_h:
        # 0 → 10 linearly across the warning band (≈1 per hour at hourly cadence)
        span = thr.warning_max_h - thr.ok_max_h
        return min(10, int(round((age_h - thr.ok_max_h) * (10.0 / span))))
    if age_h < thr.critical_max_h:
        # 10 → 25 linearly across the critical band
        span = thr.critical_max_h - thr.warning_max_h
        return min(25, 10 + int(round((age_h - thr.warning_max_h) * (15.0 / span))))
    return 25
