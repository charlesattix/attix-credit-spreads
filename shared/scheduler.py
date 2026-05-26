"""
Built-in trade scanner scheduler.

Runs scans at fixed market-hours intervals (America/New_York, weekdays only).
No external cron or APScheduler dependency — uses a simple sleep loop.

Schedule (all times ET, Mon-Fri):
  9:00            — pre-market status check
  9:15, 9:45      — open
  10:00 .. 15:30  — intraday every 30 min
  16:15           — post-market daily report
  16:30           — model retrain check
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple

import pytz

from shared.io_utils import atomic_json_write
from shared.metrics import metrics

logger = logging.getLogger(__name__)

# Scan timeout: abort hung scans after this many seconds
_SCAN_TIMEOUT_SECONDS = 600  # 10 minutes

# Heartbeat file location
_HEARTBEAT_PATH = Path(os.environ.get("DATA_DIR", "data")) / "heartbeat.json"

ET = pytz.timezone("America/New_York")

# Slot types
SLOT_SCAN = "scan"
SLOT_PRE_MARKET = "pre_market"
SLOT_DAILY_REPORT = "daily_report"
SLOT_RETRAIN = "retrain_check"      # 16:30 ET daily — ModelRetrainer check
SLOT_MACRO_WEEKLY = "macro_weekly"  # Friday 17:00 ET only

# (hour, minute, slot_type) in ET — scans every 5 min during market hours
SCAN_TIMES = [
    (9, 0, SLOT_PRE_MARKET),
] + [
    (h, m, SLOT_SCAN)
    for h in range(9, 16)
    for m in range(0 if h > 9 else 5, 60, 5)
    if not (h == 15 and m > 55)
] + [
    (16, 15, SLOT_DAILY_REPORT),
    (16, 30, SLOT_RETRAIN),
    (17, 0, SLOT_MACRO_WEEKLY),   # weekly macro snapshot — skipped on Mon–Thu
]


# Market-hours scan times only (hour, minute) — used by the backtester
MARKET_SCAN_TIMES = [(h, m) for h, m, s in SCAN_TIMES if s == SLOT_SCAN]


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5  # Mon=0 … Fri=4


def _next_scan_time(now_et: datetime) -> Tuple[datetime, str]:
    """Return the next scheduled scan time and its slot type."""
    today_date = now_et.date()

    # Check remaining slots today
    for hour, minute, slot_type in SCAN_TIMES:
        candidate = ET.localize(datetime(today_date.year, today_date.month, today_date.day, hour, minute))
        if candidate > now_et and _is_weekday(candidate):
            # SLOT_MACRO_WEEKLY only fires on Fridays (weekday() == 4)
            if slot_type == SLOT_MACRO_WEEKLY and candidate.weekday() != 4:
                continue
            return candidate, slot_type

    # No more slots today — find next weekday
    next_day = today_date + timedelta(days=1)
    while next_day.weekday() >= 5:  # skip weekends
        next_day += timedelta(days=1)

    first_hour, first_minute, first_slot = SCAN_TIMES[0]
    return (
        ET.localize(datetime(next_day.year, next_day.month, next_day.day, first_hour, first_minute)),
        first_slot,
    )


class ScanScheduler:
    """Runs a scan callback on the market-hours schedule."""

    def __init__(self, scan_fn, startup_delay: int = 30):
        """
        Args:
            scan_fn: Callable(slot_type: str) that performs the scan.
                     slot_type is one of SLOT_SCAN, SLOT_PRE_MARKET,
                     SLOT_DAILY_REPORT.
            startup_delay: Seconds to wait before entering the scan loop.
                           Gives co-located services (web server, healthcheck)
                           time to stabilise before CPU-heavy scans start.
        """
        self._scan_fn = scan_fn
        self._stop_event = threading.Event()
        self._startup_delay = startup_delay
        self._scan_count = 0

    def stop(self):
        """Signal the scheduler to stop after the current sleep."""
        self._stop_event.set()

    def run_forever(self):
        """Block and run scans on schedule until stop() is called or SIGTERM."""
        logger.info("Scheduler started — %d slots per trading day", len(SCAN_TIMES))

        if self._startup_delay > 0:
            logger.info("Startup delay: waiting %ds before first scan cycle", self._startup_delay)
            if self._stop_event.wait(timeout=self._startup_delay):
                logger.info("Scheduler stopping (signal received during startup delay)")
                return

        while not self._stop_event.is_set():
            now_et = datetime.now(ET)
            nxt, slot_type = _next_scan_time(now_et)
            wait_seconds = (nxt - now_et).total_seconds()

            logger.info(
                "Next slot at %s ET [%s] (in %.0f min)",
                nxt.strftime("%Y-%m-%d %H:%M"),
                slot_type,
                wait_seconds / 60,
            )

            # Sleep in short intervals so we can react to stop_event quickly
            if self._stop_event.wait(timeout=wait_seconds):
                logger.info("Scheduler stopping (signal received during wait)")
                break

            # Double-check we're in a valid window (guards against clock drift)
            now_et = datetime.now(ET)
            if not _is_weekday(now_et):
                continue

            # Run the scan with timeout protection
            had_error = False
            try:
                logger.info("=== Scheduled %s starting (%s ET) ===", slot_type, now_et.strftime("%H:%M"))
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self._scan_fn, slot_type)
                    try:
                        future.result(timeout=_SCAN_TIMEOUT_SECONDS)
                    except FuturesTimeoutError:
                        had_error = True
                        metrics.inc('scan_timeouts')
                        logger.error(
                            "Slot %s TIMED OUT after %ds — skipping to next slot",
                            slot_type, _SCAN_TIMEOUT_SECONDS,
                        )
                self._scan_count += 1
                logger.info("=== Scheduled %s complete ===", slot_type)
            except Exception:
                had_error = True
                logger.exception("Slot %s failed — will retry at next scheduled time", slot_type)
            finally:
                self._write_heartbeat(slot_type, error=had_error)

    def _write_heartbeat(self, slot_type: str, error: bool = False) -> None:
        """Write heartbeat file after each scan for external health monitoring."""
        now_et = datetime.now(ET)
        heartbeat = {
            "last_scan_time": now_et.strftime("%Y-%m-%d %H:%M:%S"),
            "last_scan_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_slot_type": slot_type,
            "scan_count": self._scan_count,
            "had_error": error,
            "pid": os.getpid(),
        }
        try:
            atomic_json_write(_HEARTBEAT_PATH, heartbeat)
        except Exception as e:
            logger.warning("Failed to write heartbeat: %s", e)
