"""
scheduler/main.py — APScheduler persistent service (Approach B).

Architecture: single long-running process.
  - APScheduler BackgroundScheduler fires jobs on their cron triggers.
  - FastAPI serves /health and /status over HTTP (required by Railway health checks).
  - All jobs import from scheduler.jobs; alerts from scheduler.alerts.

Cron schedule (all times ET / America/New_York):
  job_pre_market_check       08:00  Mon-Fri
  job_event_gate_check       09:20  Mon-Fri
  job_signal_generator       09:25  Mon-Fri
  job_circuit_breaker_check  Every 30 min  09:00-15:30  Mon-Fri
  job_monitor_poll           Every 5 min   09:30-16:00  Mon-Fri
  job_post_market            16:30  Mon-Fri
  job_weekly_summary         16:35  Fri
  job_data_freshness_check   17:00  Mon-Fri
  job_heartbeat              Every 4 hours (all days)
  job_log_rotate             02:00  daily
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from datetime import datetime

import pytz
import uvicorn
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.alerts import send_telegram
from scheduler.api import app as fastapi_app
from scheduler.jobs import (
    job_circuit_breaker_check,
    job_data_freshness_check,
    job_event_gate_check,
    job_heartbeat,
    job_log_rotate,
    job_monitor_poll,
    job_post_market,
    job_pre_market_check,
    job_signal_generator,
    job_weekly_summary,
)

# ── Logging ──────────────────────────────────────────────────────────────────
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
LOG = logging.getLogger("scheduler.main")

ET = pytz.timezone("America/New_York")
_START_TIME = datetime.utcnow()


# ── APScheduler event listeners ──────────────────────────────────────────────

def on_job_error(event) -> None:
    job_id = event.job_id
    exc    = event.exception
    tb     = event.traceback
    LOG.error("JOB ERROR: %s — %s", job_id, exc)
    send_telegram(
        f"SCHEDULER: job '{job_id}' crashed\n"
        f"{type(exc).__name__}: {exc}\n"
        f"Check Railway logs for full traceback."
    )


def on_job_missed(event) -> None:
    job_id    = event.job_id
    scheduled = event.scheduled_run_time
    lateness_s = (datetime.now(ET) - scheduled).total_seconds()
    LOG.warning("JOB MISSED: %s at %s (%.0fs late)", job_id, scheduled, lateness_s)
    send_telegram(
        f"[MISFIRE] job '{job_id}' missed window at {scheduled.strftime('%H:%M ET')} "
        f"({lateness_s:.0f}s late). Check Railway logs."
    )


# ── Scheduler builder ────────────────────────────────────────────────────────

def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=ET)

    # ── Pre-market check: 08:00 ET Mon-Fri ─────────────────────────────────
    scheduler.add_job(
        job_pre_market_check,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ET),
        id="pre_market_check",
        name="Pre-market health check",
        misfire_grace_time=300,
    )

    # ── Event gate check: 09:20 ET Mon-Fri ─────────────────────────────────
    scheduler.add_job(
        job_event_gate_check,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=20, timezone=ET),
        id="event_gate_check",
        name="Event gate check (FOMC/CPI)",
        misfire_grace_time=120,
    )

    # ── Signal generator: 09:25 ET Mon-Fri ─────────────────────────────────
    scheduler.add_job(
        job_signal_generator,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=25, timezone=ET),
        id="signal_generator",
        name="EXP-2830 signal generator",
        misfire_grace_time=300,
        max_instances=1,
    )

    # ── Circuit breaker: every 30 min 09:00-15:30 ET Mon-Fri ───────────────
    scheduler.add_job(
        job_circuit_breaker_check,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="0,30",
            timezone=ET,
        ),
        id="circuit_breaker_check",
        name="VIX circuit breaker check",
        misfire_grace_time=120,
    )

    # ── Monitor poll: every 5 min 09:30-16:00 ET Mon-Fri ───────────────────
    scheduler.add_job(
        job_monitor_poll,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="30,35,40,45,50,55",
            timezone=ET,
        ),
        id="monitor_poll_930_1555",
        name="Position monitor poll (09:30-15:55)",
        misfire_grace_time=60,
    )
    scheduler.add_job(
        job_monitor_poll,
        CronTrigger(
            day_of_week="mon-fri",
            hour=16,
            minute=0,
            timezone=ET,
        ),
        id="monitor_poll_1600",
        name="Position monitor poll (16:00)",
        misfire_grace_time=60,
    )

    # ── Post-market: 16:30 ET Mon-Fri ──────────────────────────────────────
    scheduler.add_job(
        job_post_market,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=ET),
        id="post_market",
        name="Post-market equity snapshot",
        misfire_grace_time=300,
    )

    # ── Weekly summary: Friday 16:35 ET ────────────────────────────────────
    scheduler.add_job(
        job_weekly_summary,
        CronTrigger(day_of_week="fri", hour=16, minute=35, timezone=ET),
        id="weekly_summary",
        name="Friday weekly performance summary",
        misfire_grace_time=600,
    )

    # ── Data freshness check: 17:00 ET Mon-Fri ─────────────────────────────
    scheduler.add_job(
        job_data_freshness_check,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=ET),
        id="data_freshness_check",
        name="Data freshness check",
        misfire_grace_time=300,
    )

    # ── Heartbeat: every 4 hours ────────────────────────────────────────────
    scheduler.add_job(
        job_heartbeat,
        CronTrigger(hour="*/4", minute=0, timezone=ET),
        id="heartbeat",
        name="4-hour heartbeat ping",
        misfire_grace_time=600,
    )

    # ── Log rotation: 02:00 ET daily ───────────────────────────────────────
    scheduler.add_job(
        job_log_rotate,
        CronTrigger(hour=2, minute=0, timezone=ET),
        id="log_rotate",
        name="Log rotation (30-day)",
        misfire_grace_time=3600,
    )

    # ── Event listeners ─────────────────────────────────────────────────────
    scheduler.add_listener(on_job_error, EVENT_JOB_ERROR)
    scheduler.add_listener(on_job_missed, EVENT_JOB_MISSED)

    return scheduler


# ── Graceful shutdown ────────────────────────────────────────────────────────

_scheduler_ref: BackgroundScheduler | None = None


def _shutdown_handler(signum, frame) -> None:
    LOG.info("Received signal %d — shutting down gracefully", signum)
    send_telegram(
        f"[COMPASS-SCHEDULER] Service stopping (signal {signum}).\n"
        f"Uptime: {_uptime_str()} | Railway will restart if configured."
    )
    if _scheduler_ref is not None:
        _scheduler_ref.shutdown(wait=False)
    sys.exit(0)


def _uptime_str() -> str:
    delta = datetime.utcnow() - _START_TIME
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    global _scheduler_ref

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    LOG.info("=" * 60)
    LOG.info("compass-scheduler starting — Railway Cron V2")
    LOG.info("=" * 60)

    # Start APScheduler
    scheduler = build_scheduler()
    _scheduler_ref = scheduler
    scheduler.start()

    job_count = len(scheduler.get_jobs())
    LOG.info("APScheduler running: %d jobs registered", job_count)

    # Startup Telegram alert
    send_telegram(
        f"[COMPASS-SCHEDULER] Service started on Railway.\n"
        f"APScheduler running, {job_count} jobs registered.\n"
        f"Start time: {_START_TIME.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Signal generator fires next trading day at 09:25 ET.\n"
        f"Uptime counter: 0h (just started)"
    )

    # Start FastAPI in foreground (blocks until process exits)
    port = int(os.environ.get("PORT", "8080"))
    LOG.info("Starting FastAPI on port %d", port)
    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level=log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
