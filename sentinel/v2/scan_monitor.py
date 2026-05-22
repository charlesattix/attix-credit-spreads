"""
sentinel/v2/scan_monitor.py — Scan execution monitor.

Independently tracks whether expected scan slots ran. Does not depend on
the scanner being alive — it reads the watchdog_runs table that the watchdog
writes after each scan execution attempt.

Called by: SentinelWatchdog every 5 minutes during market hours.
Also called at market close (16:00 ET) for end-of-day scan audit.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

LOG = logging.getLogger("sentinel.v2.scan_monitor")
ET = ZoneInfo("America/New_York")

# Expected scan slots (ET times, weekdays only)
# These must match SentinelWatchdog's scheduled jobs
SCAN_SLOTS: list[time] = [
    time(9, 25),
    time(10, 0),
    time(10, 30),
    time(11, 0),
    time(11, 30),
    time(12, 0),
    time(12, 30),
    time(13, 0),
    time(13, 30),
    time(14, 0),
    time(14, 30),
    time(15, 0),
    time(15, 30),
]

# Grace period: how long after a slot's scheduled time before we alert
SLOT_GRACE_MINUTES = 30

# How many consecutive missed slots before escalating
CONSECUTIVE_MISS_WARN = 2
CONSECUTIVE_MISS_CRIT = 4


@dataclass
class SlotResult:
    slot_time: time
    expected_at: datetime
    ran: bool
    outcome: str | None    # 'ok' | 'gate_blocked' | 'scanner_error' | 'skipped' | None
    lateness_minutes: float | None


@dataclass
class ScanMonitorReport:
    as_of: datetime
    market_day: bool
    slots_expected: int
    slots_ran: int
    slots_missed: int
    consecutive_misses: int
    status: str             # 'ok' | 'warn' | 'critical'
    message: str
    slot_results: list[SlotResult]


def _get_db_path() -> str:
    return os.environ.get("SENTINEL_DB_PATH", "sentinel/db/sentinel.db")


def _slots_due_today(now_et: datetime) -> list[time]:
    """Return scan slots that should have run by now (plus grace period)."""
    cutoff = (now_et - timedelta(minutes=SLOT_GRACE_MINUTES)).time()
    return [s for s in SCAN_SLOTS if s <= cutoff]


def _query_runs_today(db_path: str, today: date) -> list[dict]:
    """Read watchdog_runs table for today's scan executions."""
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT scan_slot, outcome, run_time, duration_s
                FROM watchdog_runs
                WHERE date(run_time) = ?
                ORDER BY run_time
                """,
                (today.isoformat(),),
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # Table may not exist yet (first run)
        return []
    except Exception as exc:
        LOG.warning("scan_monitor: DB read failed: %s", exc)
        return []


def check_scan_execution(db_path: str | None = None) -> ScanMonitorReport:
    """
    Check whether expected scan slots have run today.
    Returns a ScanMonitorReport with status and message.
    """
    now_et = datetime.now(ET)
    today = now_et.date()
    db_path = db_path or _get_db_path()

    # Skip weekends
    if today.weekday() >= 5:
        return ScanMonitorReport(
            as_of=now_et, market_day=False,
            slots_expected=0, slots_ran=0, slots_missed=0,
            consecutive_misses=0, status="ok",
            message="Weekend — no scans expected",
            slot_results=[],
        )

    due_slots = _slots_due_today(now_et)
    if not due_slots:
        return ScanMonitorReport(
            as_of=now_et, market_day=True,
            slots_expected=0, slots_ran=0, slots_missed=0,
            consecutive_misses=0, status="ok",
            message="No scan slots due yet today",
            slot_results=[],
        )

    db_runs = _query_runs_today(db_path, today)
    # Build a set of slots that ran: HH:MM string
    ran_slots = {r["scan_slot"] for r in db_runs if r["outcome"] in ("ok", "gate_blocked")}

    slot_results = []
    for slot in due_slots:
        slot_key = slot.strftime("%H:%M")
        ran = slot_key in ran_slots
        row = next((r for r in db_runs if r["scan_slot"] == slot_key), None)
        expected_at = datetime.combine(today, slot).replace(tzinfo=ET)
        actual_at = None
        if row:
            try:
                actual_at = datetime.fromisoformat(row["run_time"])
                if actual_at.tzinfo is None:
                    actual_at = actual_at.replace(tzinfo=ET)
            except Exception:
                actual_at = None
        lateness = (actual_at - expected_at).total_seconds() / 60 if actual_at else None

        slot_results.append(SlotResult(
            slot_time=slot,
            expected_at=expected_at,
            ran=ran,
            outcome=row["outcome"] if row else None,
            lateness_minutes=lateness,
        ))

    slots_ran = sum(1 for s in slot_results if s.ran)
    slots_missed = sum(1 for s in slot_results if not s.ran)

    # Consecutive misses: count from the end of the list
    consecutive = 0
    for sr in reversed(slot_results):
        if not sr.ran:
            consecutive += 1
        else:
            break

    if consecutive >= CONSECUTIVE_MISS_CRIT:
        status = "critical"
        msg = (
            f"CRITICAL: {consecutive} consecutive scan slots missed today "
            f"({slots_ran}/{len(due_slots)} ran) — scanner may be down"
        )
    elif consecutive >= CONSECUTIVE_MISS_WARN:
        status = "warn"
        msg = (
            f"WARNING: {consecutive} consecutive scan slots missed "
            f"({slots_ran}/{len(due_slots)} ran)"
        )
    elif slots_missed > 0:
        status = "info"
        msg = f"INFO: {slots_missed} scan slot(s) missed ({slots_ran}/{len(due_slots)} ran)"
    else:
        status = "ok"
        msg = f"All {slots_ran} scan slots ran on schedule"

    return ScanMonitorReport(
        as_of=now_et, market_day=True,
        slots_expected=len(due_slots),
        slots_ran=slots_ran,
        slots_missed=slots_missed,
        consecutive_misses=consecutive,
        status=status,
        message=msg,
        slot_results=slot_results,
    )
