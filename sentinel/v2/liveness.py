"""
sentinel/v2/liveness.py — System liveness aggregator.

Answers: "Has ANYTHING happened in the last 24 hours on a market day?"

Evidence sources (in priority order):
  1. watchdog_runs table in sentinel.db — last successful scan heartbeat
  2. Trade DB writes — max(created_at) from pilotai_exp*.db
  3. sentinel_state.json — last_health_check timestamp

If ALL of these show >24h silence on a market day → CRITICAL.

Called by: SentinelWatchdog every hour.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOG = logging.getLogger("sentinel.v2.liveness")
ET = ZoneInfo("America/New_York")

LIVENESS_THRESHOLD_HOURS = 24


@dataclass
class LivenessReport:
    as_of: datetime
    threshold_hours: int
    evidence: dict[str, datetime | None]
    latest_activity: datetime | None
    hours_since_activity: float
    alive: bool
    message: str


def check_liveness(
    db_path: str | None = None,
    data_dir: str | None = None,
    active_exp_ids: list[str] | None = None,
) -> LivenessReport:
    """
    Aggregate all evidence sources and determine if the system is alive.

    Returns a LivenessReport. alive=False means ALL evidence sources show
    >24h of silence on a market day — catch-all for total system failure.
    """
    now_et = datetime.now(ET)
    db_path = db_path or os.environ.get("SENTINEL_DB_PATH", "sentinel/db/sentinel.db")
    data_dir = data_dir or os.environ.get("DATA_DIR", "data")
    active_exp_ids = active_exp_ids or []

    evidence: dict[str, datetime | None] = {}

    # 1. Last successful watchdog scan
    evidence["watchdog_scan"] = _last_watchdog_scan(db_path)

    # 2. sentinel_state.json last_health_check
    evidence["sentinel_state"] = _last_health_check()

    # 3. Trade DB writes per active experiment
    for exp_id in active_exp_ids:
        db = _resolve_trade_db(exp_id, data_dir)
        if db and Path(db).exists():
            ts = _last_trade_db_write(db)
            if ts:
                evidence[f"trade_db_{exp_id}"] = ts

    latest = max((v for v in evidence.values() if v is not None), default=None)
    hours_since = (now_et - latest).total_seconds() / 3600 if latest else 999.0

    alive = hours_since < LIVENESS_THRESHOLD_HOURS

    if alive:
        source = next(k for k, v in evidence.items() if v == latest)
        msg = (
            f"System alive — last activity {hours_since:.1f}h ago "
            f"(source: {source})"
        )
    else:
        age_str = f"{hours_since:.0f}h" if hours_since < 999 else "NEVER"
        msg = (
            f"SYSTEM LIVENESS: no activity in {age_str} on market day "
            f"(threshold: {LIVENESS_THRESHOLD_HOURS}h) — ALL systems may be dead"
        )

    return LivenessReport(
        as_of=now_et,
        threshold_hours=LIVENESS_THRESHOLD_HOURS,
        evidence=evidence,
        latest_activity=latest,
        hours_since_activity=hours_since,
        alive=alive,
        message=msg,
    )


# ── Evidence helpers ──────────────────────────────────────────────────────────

def _last_watchdog_scan(db_path: str) -> datetime | None:
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            row = conn.execute(
                "SELECT max(run_time) FROM watchdog_runs WHERE outcome = 'ok'"
            ).fetchone()
            if row and row[0]:
                ts = datetime.fromisoformat(row[0])
                return ts if ts.tzinfo else ts.replace(tzinfo=ET)
    except Exception:
        pass
    return None


def _last_health_check() -> datetime | None:
    try:
        from sentinel.state import load_state
        state = load_state()
        for exp_data in state.get("experiments", {}).values():
            if not isinstance(exp_data, dict):
                continue
            ts_str = exp_data.get("last_health_check")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    return ts if ts.tzinfo else ts.replace(tzinfo=ET)
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _last_trade_db_write(db_path: str) -> datetime | None:
    try:
        with sqlite3.connect(db_path, timeout=3) as conn:
            row = conn.execute(
                "SELECT max(created_at) FROM trades"
            ).fetchone()
            if row and row[0]:
                ts = datetime.fromisoformat(row[0])
                return ts if ts.tzinfo else ts.replace(tzinfo=ET)
    except Exception:
        pass
    return None


def _resolve_trade_db(exp_id: str, data_dir: str) -> str | None:
    num = exp_id.replace("EXP-", "").replace("exp", "").lower()
    path = Path(data_dir) / f"pilotai_exp{num}.db"
    return str(path) if path.exists() else None
