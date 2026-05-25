"""
sentinel/v2/watchdog.py — Sentinel v2 main watchdog process.

ARCHITECTURAL INVERSION:
  v1: scanner starts → calls sentinel (sentinel is reactive, goes silent if scanner dies)
  v2: watchdog starts → calls scanner (watchdog is proactive, always running)

Entry point: python -m sentinel.v2.watchdog

The watchdog owns the scan schedule. Scanners are subprocesses that the
watchdog calls, monitors, and reports on. Sentinel never goes silent because
the scanner dies — the watchdog is always running, always checking.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import fastapi
import uvicorn

from sentinel.v2.dead_man_switch import push_heartbeat, push_failure
from sentinel.v2.cadence_engine import check_all_active
from sentinel.v2.scan_monitor import check_scan_execution
from sentinel.v2.liveness import check_liveness
from sentinel.alerting import Alert, Severity, send_alert

LOG = logging.getLogger("sentinel.v2.watchdog")
ET = ZoneInfo("America/New_York")
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
SENTINEL_DB_PATH = os.environ.get("SENTINEL_DB_PATH", "sentinel/db/sentinel.db")


# ── Watchdog runs table (meta-monitoring) ─────────────────────────────────────

def _ensure_watchdog_table() -> None:
    Path(SENTINEL_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SENTINEL_DB_PATH, timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchdog_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_time    TEXT    NOT NULL,
                scan_slot   TEXT,
                exp_id      TEXT,
                outcome     TEXT    NOT NULL,
                duration_s  REAL,
                notes       TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wr_run_time ON watchdog_runs(run_time)"
        )
        conn.commit()


def _record_run(
    scan_slot: str | None,
    exp_id: str | None,
    outcome: str,
    duration_s: float | None = None,
    notes: str | None = None,
) -> None:
    try:
        with sqlite3.connect(SENTINEL_DB_PATH, timeout=5) as conn:
            conn.execute(
                """
                INSERT INTO watchdog_runs (run_time, scan_slot, exp_id, outcome, duration_s, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.now(ET).isoformat(), scan_slot, exp_id, outcome, duration_s, notes),
            )
            conn.commit()
    except Exception as exc:
        LOG.warning("watchdog: could not record run: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_exp_ids() -> list[str]:
    """Return exp_ids that are active (not halted) from sentinel_state.json."""
    try:
        from sentinel.state import load_state, list_active
        state = load_state()
        return list_active(state)
    except Exception as exc:
        LOG.error("watchdog: could not load state: %s", exc)
        return []


def _is_market_day(d: date | None = None) -> bool:
    d = d or datetime.now(ET).date()
    return d.weekday() < 5


def _send_watchdog_alert(message: str, severity: str = "INFO") -> None:
    """Send a Telegram alert tagged as watchdog origin."""
    sev_map = {
        "INFO":     Severity.INFO,
        "WARN":     Severity.WARNING,
        "WARNING":  Severity.WARNING,
        "CRITICAL": Severity.CRITICAL,
        "HALT":     Severity.HALT,
    }
    sev = sev_map.get(severity.upper(), Severity.INFO)
    try:
        alert = Alert(
            severity=sev,
            experiment_id="__WATCHDOG__",
            gate_id="G_WATCHDOG",
            message=message,
        )
        send_alert(alert, force=(sev >= Severity.CRITICAL))
    except Exception as exc:
        LOG.error("watchdog: alert dispatch failed: %s", exc)


# ── Pre-scan gate wrapper ─────────────────────────────────────────────────────

@dataclass
class _PreScanResult:
    blocked: bool
    reason: str = ""


def _run_pre_scan_gates(exp_id: str, config_path: str | None = None) -> _PreScanResult:
    """
    Wrap sentinel.guards.pre_scan_check, which calls sys.exit(1) on failure.
    We catch SystemExit so the watchdog process is not terminated.
    """
    try:
        from sentinel.guards import pre_scan_check
        pre_scan_check(exp_id, config_path)
        return _PreScanResult(blocked=False)
    except SystemExit:
        return _PreScanResult(blocked=True, reason="Pre-scan gate blocked (see logs)")
    except Exception as exc:
        return _PreScanResult(blocked=True, reason=f"Pre-scan gate error: {exc}")


# ── Scheduled jobs ─────────────────────────────────────────────────────────────

def job_run_scan(slot_name: str = "adhoc") -> None:
    """
    Execute one scan cycle across all active experiments.
    For each active exp:
      1. Run v1 pre-scan guards (G0-G3)
      2. Call main.py --run-once for this experiment
      3. Run v1 post-scan gates (G6-G9)
      4. Record result to watchdog_runs
    """
    if not _is_market_day():
        return

    active = _active_exp_ids()
    LOG.info("watchdog: scan slot %s — %d active experiments", slot_name, len(active))

    for exp_id in active:
        t0 = datetime.now(ET)
        outcome = "ok"
        notes = None
        try:
            # Pre-scan gates (v1 guards.py) — wrapped to catch sys.exit
            config_path = _resolve_config(exp_id)
            gate_result = _run_pre_scan_gates(exp_id, config_path)
            if gate_result.blocked:
                outcome = "gate_blocked"
                notes = gate_result.reason
                LOG.warning("watchdog: %s blocked by pre-scan gate: %s", exp_id, gate_result.reason)
                _record_run(slot_name, exp_id, outcome, notes=notes)
                continue

            # Run scanner as subprocess (isolates crashes from watchdog)
            env_file = _resolve_env_file(exp_id)
            result = subprocess.run(
                [sys.executable, "main.py", "--run-once",
                 "--config", config_path, "--env-file", env_file],
                capture_output=True, text=True, timeout=600,
                cwd=str(Path(__file__).parent.parent.parent),
            )
            if result.returncode != 0:
                outcome = "scanner_error"
                notes = f"exit={result.returncode}: {result.stderr[-500:]}"
                LOG.error("watchdog: scanner %s failed: %s", exp_id, notes)
                _send_watchdog_alert(
                    f"[SCAN ERROR] {exp_id} at {slot_name}: {notes[:300]}", "WARN"
                )
            else:
                # Post-scan gates (v1 runtime.py)
                db_path = _resolve_db(exp_id)
                try:
                    from sentinel.runtime import post_scan_check
                    rt = post_scan_check(exp_id, db_path=db_path, config={})
                    if rt.get("halted"):
                        outcome = "halt"
                        notes = "Runtime gate triggered halt"
                        push_failure(f"{exp_id} halted by runtime gate at {slot_name}")
                except Exception as rt_exc:
                    LOG.error("watchdog: post_scan_check failed for %s: %s", exp_id, rt_exc)

        except subprocess.TimeoutExpired:
            outcome = "scanner_timeout"
            notes = "Scanner exceeded 600s timeout"
            LOG.error("watchdog: %s timed out", exp_id)
            _send_watchdog_alert(
                f"[SCAN TIMEOUT] {exp_id} at {slot_name} — killed after 600s", "CRITICAL"
            )

        except Exception as exc:
            outcome = "watchdog_error"
            notes = str(exc)
            LOG.exception("watchdog: unexpected error running %s: %s", exp_id, exc)

        finally:
            duration = (datetime.now(ET) - t0).total_seconds()
            _record_run(slot_name, exp_id, outcome, duration_s=duration, notes=notes)


def job_check_scan_execution() -> None:
    """Every 5 minutes during market hours — check if expected scans ran."""
    if not _is_market_day():
        return
    now_et = datetime.now(ET)
    # Only check during trading hours (9:00 - 16:30)
    if not (9 <= now_et.hour < 17):
        return

    report = check_scan_execution(SENTINEL_DB_PATH)
    if report.status == "critical":
        LOG.error("scan_monitor: %s", report.message)
        _send_watchdog_alert(f"[SCAN MONITOR] {report.message}", "CRITICAL")
        push_failure(report.message)
    elif report.status == "warn":
        LOG.warning("scan_monitor: %s", report.message)
        _send_watchdog_alert(f"[SCAN MONITOR] {report.message}", "WARN")


def job_check_trade_cadence() -> None:
    """Daily at 17:00 ET — check trade cadence for all active experiments."""
    if not _is_market_day():
        return
    active = _active_exp_ids()
    results = check_all_active(active, as_of=datetime.now(ET).date())

    critical_msgs = [r.message for r in results if r.status == "critical"]
    warn_msgs     = [r.message for r in results if r.status == "warn"]

    for msg in critical_msgs:
        _send_watchdog_alert(f"[CADENCE CRITICAL] {msg}", "CRITICAL")
        push_failure(msg)

    for msg in warn_msgs:
        _send_watchdog_alert(f"[CADENCE WARN] {msg}", "WARN")

    if not critical_msgs and not warn_msgs:
        ok_count = sum(1 for r in results if r.status == "ok")
        LOG.info("cadence: all %d active experiments trading on cadence", ok_count)


def job_check_system_liveness() -> None:
    """Every hour — check if anything at all happened in the last 24h on a market day."""
    if not _is_market_day():
        return

    active = _active_exp_ids()
    report = check_liveness(
        db_path=SENTINEL_DB_PATH,
        data_dir=str(DATA_DIR),
        active_exp_ids=active,
    )

    if not report.alive:
        LOG.critical(report.message)
        _send_watchdog_alert(f"[LIVENESS CRITICAL] {report.message}", "CRITICAL")
        push_failure(report.message)
    else:
        LOG.debug("liveness: %s", report.message)


def job_push_dead_mans_switch() -> None:
    """Every 4 hours — push heartbeat to external dead man's switch."""
    ok = push_heartbeat()
    if not ok:
        LOG.warning("watchdog: dead man's switch ping failed (check HEALTHCHECKS_PING_URL)")


def job_daily_report() -> None:
    """16:30 ET — run full orchestrator audit and send daily report."""
    try:
        from sentinel.daily_report import run_daily_report
        run_daily_report(dry_run=False)
    except Exception as exc:
        LOG.exception("watchdog: daily report failed: %s", exc)
        _send_watchdog_alert(f"[DAILY REPORT FAILED] {exc}", "CRITICAL")


# ── Config resolution (per-experiment) ───────────────────────────────────────

def _resolve_config(exp_id: str) -> str:
    """Map experiment ID to its config file path."""
    mapping = {
        "EXP-400":  "configs/paper_champion.yaml",
        "EXP-401":  "configs/paper_exp401.yaml",
        "EXP-600":  "configs/paper_exp600.yaml",
        "EXP-1220": "configs/paper_exp1220.yaml",
    }
    return mapping.get(exp_id, f"configs/paper_{exp_id.lower().replace('-', '')}.yaml")


def _resolve_env_file(exp_id: str) -> str:
    mapping = {
        "EXP-400":  ".env.champion",
        "EXP-401":  ".env.exp401",
        "EXP-600":  ".env.exp600",
        "EXP-1220": ".env.exp1220",
    }
    return mapping.get(exp_id, f".env.{exp_id.lower().replace('-', '')}")


def _resolve_db(exp_id: str) -> str:
    num = exp_id.replace("EXP-", "").replace("exp", "").lower()
    return str(DATA_DIR / f"pilotai_exp{num}.db")


# ── FastAPI health endpoints ──────────────────────────────────────────────────

app = fastapi.FastAPI(title="Sentinel v2 Watchdog")


@app.get("/health")
def health():
    from sentinel.v2.liveness import _last_watchdog_scan, _last_health_check
    last_scan = _last_watchdog_scan(SENTINEL_DB_PATH)
    last_hc   = _last_health_check()
    return {
        "alive": True,
        "last_scan_et": last_scan.isoformat() if last_scan else None,
        "last_health_check": last_hc.isoformat() if last_hc else None,
        "active_experiments": _active_exp_ids(),
        "market_day": _is_market_day(),
    }


@app.get("/sentinel/status")
def sentinel_status():
    try:
        from sentinel.state import load_state
        state = load_state()
        scan_report = check_scan_execution(SENTINEL_DB_PATH)
        return {
            "experiments": state.get("experiments", {}),
            "scan_monitor": {
                "status": scan_report.status,
                "message": scan_report.message,
                "slots_ran": scan_report.slots_ran,
                "slots_expected": scan_report.slots_expected,
            },
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/sentinel/cadence")
def cadence_status():
    active = _active_exp_ids()
    results = check_all_active(active)
    return [
        {
            "exp_id": r.exp_id,
            "status": r.status,
            "message": r.message,
            "last_trade": r.last_trade_date.isoformat() if r.last_trade_date else None,
            "missed_periods": r.missed_periods,
        }
        for r in results
    ]


@app.post("/api/scan")
def manual_scan():
    """Trigger an immediate manual scan (with all gate checks)."""
    import threading
    t = threading.Thread(target=job_run_scan, args=("manual",), daemon=True)
    t.start()
    return {"status": "scan_triggered", "note": "Check /health for results"}


# ── Scheduler setup and main entry ───────────────────────────────────────────

def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=ET)

    # Scan slots (market hours, weekdays only)
    for slot_time, slot_name in [
        ("9:25",  "09:25"), ("10:00", "10:00"), ("10:30", "10:30"),
        ("11:00", "11:00"), ("11:30", "11:30"), ("12:00", "12:00"),
        ("12:30", "12:30"), ("13:00", "13:00"), ("13:30", "13:30"),
        ("14:00", "14:00"), ("14:30", "14:30"), ("15:00", "15:00"),
        ("15:30", "15:30"),
    ]:
        h, m = slot_time.split(":")
        sched.add_job(
            job_run_scan,
            CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m), timezone=ET),
            args=[slot_name],
            id=f"scan_{slot_name.replace(':', '')}",
            misfire_grace_time=120,
            coalesce=True,
        )

    # Scan execution monitor (every 5 min during market hours)
    sched.add_job(
        job_check_scan_execution,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5", timezone=ET),
        id="scan_monitor",
        misfire_grace_time=60,
        coalesce=True,
    )

    # Trade cadence check (daily at 17:00 ET)
    sched.add_job(
        job_check_trade_cadence,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=ET),
        id="cadence_check",
        coalesce=True,
    )

    # Daily report (16:30 ET)
    sched.add_job(
        job_daily_report,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=ET),
        id="daily_report",
        coalesce=True,
    )

    # System liveness (every hour)
    sched.add_job(
        job_check_system_liveness,
        CronTrigger(minute=0, timezone=ET),
        id="liveness_check",
        coalesce=True,
    )

    # Dead man's switch heartbeat (every 4 hours, at :05 to avoid top-of-hour contention)
    sched.add_job(
        job_push_dead_mans_switch,
        CronTrigger(hour="*/4", minute=5, timezone=ET),
        id="dead_mans_switch",
        coalesce=True,
    )

    return sched


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _ensure_watchdog_table()

    scheduler = build_scheduler()
    scheduler.start()

    # Auto-enroll any registry-active experiments missing from sentinel_state.json.
    # This catches experiments that were activated before the atomic launcher existed
    # or were enrolled outside the normal launch flow.
    try:
        from experiments.manager import get_manager
        from experiments.registry import LIVE_STATUSES
        from sentinel.state import load_state, save_state
        mgr = get_manager()
        mgr.reload()
        live_exps = {e["id"]: e for e in mgr.live()}
        state = load_state()
        enrolled = state.get("experiments", {})
        # Find experiments that are either missing entirely or enrolled but
        # not in 'active' status (e.g. stuck in 'configuring' from partial setup).
        missing = {eid for eid in live_exps if eid not in enrolled
                   or enrolled.get(eid, {}).get("status") != "active"}
        if missing:
            from datetime import datetime, timezone
            _now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            experiments = state.setdefault("experiments", {})
            for exp_id in sorted(missing):
                exp = live_exps[exp_id]
                experiments[exp_id] = {
                    "status": "active",
                    "paper_config": exp.get("config_path"),
                    "config_fingerprint": None,  # will be set by re-baseline below
                    "account_id": exp.get("alpaca_account_id"),
                    "live_since": exp.get("live_since") or _today,
                    "enrolled_at": _now,
                    "last_health_check": _now,
                    "halt_reason": None,
                    "halted": False,
                }
                LOG.info("watchdog: auto-enrolled %s from registry", exp_id)
            save_state(state)
    except Exception as exc:
        LOG.warning("watchdog: auto-enroll from registry failed: %s", exc)

    active = _active_exp_ids()

    # Re-baseline config fingerprints on startup so Gate 2 doesn't fire on
    # every deploy due to stale digests in sentinel_state.json.
    from sentinel.state import update_fingerprint
    for exp_id in active:
        try:
            config_path = _resolve_config(exp_id)
            update_fingerprint(exp_id, config_path)
            LOG.info("watchdog: re-baselined fingerprint for %s (%s)", exp_id, config_path)
        except Exception as exc:
            LOG.warning("watchdog: could not re-baseline fingerprint for %s: %s", exp_id, exc)

    scan_jobs = [j for j in scheduler.get_jobs() if j.id.startswith("scan_")]
    LOG.info(
        "Sentinel v2 watchdog started — %d jobs scheduled, %d active experiments",
        len(scheduler.get_jobs()), len(active),
    )

    _send_watchdog_alert(
        f"[SENTINEL V2] Watchdog started on Railway.\n"
        f"Active experiments: {', '.join(active) or 'none'}\n"
        f"Scan schedule: {len(scan_jobs)} daily slots\n"
        f"Dead man's switch: "
        f"{'ACTIVE' if os.environ.get('HEALTHCHECKS_PING_URL') else 'NOT CONFIGURED'}"
    )

    # Push initial heartbeat immediately on startup
    push_heartbeat()

    # Run FastAPI (blocks until process exits)
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
