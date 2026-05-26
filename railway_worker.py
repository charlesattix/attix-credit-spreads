#!/usr/bin/env python3
"""
railway_worker.py — Multi-experiment orchestrator for Railway cloud deployment.

Reads all active experiments from the registry, spawns one
`python main.py scheduler` subprocess per experiment, and supervises them:
restarts on crash, shuts down cleanly on SIGTERM.

Env vars consumed:
    RAILWAY_VOLUME_MOUNT_PATH   — volume root (e.g. /app/data); if unset, uses data/
    ALPACA_API_KEY_EXP400       — per-experiment Railway credentials
    ALPACA_API_SECRET_EXP400    — (suffix = exp_id with dash removed, uppercased)
    ALPACA_API_KEY_EXP401, etc.

Process status is written to <volume>/.worker_status.json for the watchdog.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Logging — stream to stdout so Railway captures it
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] worker: %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("railway_worker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent
VOLUME_MOUNT = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").rstrip("/")

# Where heartbeats and status files live.  On Railway the volume IS data/.
DATA_DIR = Path(VOLUME_MOUNT) if VOLUME_MOUNT else PROJECT_DIR / "data"
STATUS_FILE = DATA_DIR / ".worker_status.json"

RESTART_DELAY_SECS = 15   # pause before restarting a crashed subprocess
SUPERVISE_TICK_SECS = 5   # main-loop polling interval

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_db_path(db_path: str) -> str:
    """Rewrite a relative db_path onto the volume mount when RAILWAY_VOLUME_MOUNT_PATH is set.

    e.g. "data/pilotai_exp400.db" -> "/app/data/pilotai_exp400.db"
         "data/exp503/pilotai_exp503.db" -> "/app/data/exp503/pilotai_exp503.db"
    """
    if not db_path:
        return db_path
    if VOLUME_MOUNT and db_path.startswith("data/"):
        relative = db_path[len("data/"):]
        return str(Path(VOLUME_MOUNT) / relative)
    return db_path


def exp_env_suffix(exp_id: str) -> str:
    """'EXP-400' -> 'EXP400' (Railway env var suffix convention)."""
    return exp_id.replace("-", "").upper()


# ---------------------------------------------------------------------------
# Env builder
# ---------------------------------------------------------------------------


def _load_env_file(env_file_abs: Path) -> Dict[str, str]:
    """Parse a .env file into a {key: value} dict (no dotenv dependency)."""
    result: Dict[str, str] = {}
    if not env_file_abs.exists():
        return result
    for line in env_file_abs.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip("'\"")
    return result


def build_subprocess_env(exp: dict) -> dict:
    """Build the full environment dict for a scheduler subprocess.

    Priority (highest -> lowest):
        1. Railway per-experiment env vars  (ALPACA_API_KEY_EXP400 -> ALPACA_API_KEY)
        2. Experiment's .env file values
        3. Current process environment
    """
    env = os.environ.copy()

    # Layer 2: .env file (overrides inherited env so each experiment gets its creds)
    env_file = exp.get("env_file", "")
    if env_file:
        env_file_abs = PROJECT_DIR / env_file
        file_vars = _load_env_file(env_file_abs)
        if file_vars:
            env.update(file_vars)
            logger.debug("[%s] Loaded %d vars from %s", exp.get("id"), len(file_vars), env_file)
        else:
            logger.warning("[%s] .env file not found or empty: %s", exp.get("id"), env_file_abs)

    # Layer 1: Railway per-experiment vars override everything
    exp_id = exp.get("id", "")
    suffix = exp_env_suffix(exp_id)  # e.g. "EXP400"

    railway_key = os.environ.get(f"ALPACA_API_KEY_{suffix}")
    railway_secret = os.environ.get(f"ALPACA_API_SECRET_{suffix}")

    if railway_key:
        env["ALPACA_API_KEY"] = railway_key
        logger.debug("[%s] Using Railway env var ALPACA_API_KEY_%s", exp_id, suffix)
    if railway_secret:
        env["ALPACA_API_SECRET"] = railway_secret
        logger.debug("[%s] Using Railway env var ALPACA_API_SECRET_%s", exp_id, suffix)

    # Identify experiment inside the subprocess (heartbeat, SENTINEL gate)
    env["EXPERIMENT_ID"] = exp_id

    return env


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------


def build_cmd(exp: dict) -> List[str]:
    """Return the argv list to launch the scheduler for an experiment."""
    config = exp.get("config_path", "")
    env_file = exp.get("env_file", "")
    db_path = resolve_db_path(exp.get("db_path", ""))

    cmd = [sys.executable, "main.py", "scheduler"]
    if config:
        cmd += ["--config", config]
    if env_file:
        cmd += ["--env-file", env_file]
    if db_path:
        cmd += ["--db", db_path]
    return cmd


# ---------------------------------------------------------------------------
# Per-experiment process wrapper
# ---------------------------------------------------------------------------


class ExperimentProcess:
    """Owns one long-running `main.py scheduler` subprocess."""

    def __init__(self, exp: dict) -> None:
        self.exp = exp
        self.exp_id: str = exp["id"]
        self.proc: Optional[subprocess.Popen] = None
        self.started_at: Optional[datetime] = None
        self.restart_count: int = 0

    # ------------------------------------------------------------------

    def start(self) -> None:
        cmd = build_cmd(self.exp)
        env = build_subprocess_env(self.exp)

        # Ensure DB parent dir exists (volume may not pre-create subdirs)
        db_path = resolve_db_path(self.exp.get("db_path", ""))
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        logger.info("[%s] Spawning: %s", self.exp_id, " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=sys.stdout,   # forward to Railway log stream
            stderr=sys.stderr,
        )
        self.started_at = datetime.now(timezone.utc)
        logger.info("[%s] Started — PID=%d", self.exp_id, self.proc.pid)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: int = 20) -> None:
        if self.proc and self.alive():
            logger.info("[%s] Sending SIGTERM to PID=%d", self.exp_id, self.proc.pid)
            self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout)
                logger.info("[%s] Stopped cleanly", self.exp_id)
            except subprocess.TimeoutExpired:
                logger.warning("[%s] SIGTERM timed out — sending SIGKILL", self.exp_id)
                self.proc.kill()
                self.proc.wait()

    def status_dict(self) -> dict:
        return {
            "exp_id": self.exp_id,
            "pid": self.proc.pid if self.proc else None,
            "alive": self.alive(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "restart_count": self.restart_count,
        }


# ---------------------------------------------------------------------------
# Status file (shared with watchdog via volume)
# ---------------------------------------------------------------------------


def write_status(procs: Dict[str, ExperimentProcess]) -> None:
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "processes": {eid: p.status_dict() for eid, p in procs.items()},
        }
        STATUS_FILE.write_text(json.dumps(payload, indent=2))
    except Exception as exc:
        logger.warning("Failed to write status file: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # Add project root to path so `from experiments.manager import ...` works
    sys.path.insert(0, str(PROJECT_DIR))
    from experiments.manager import get_manager  # noqa: PLC0415

    logger.info("=" * 60)
    logger.info("Attix Railway Worker starting")
    logger.info("PROJECT_DIR        = %s", PROJECT_DIR)
    logger.info("VOLUME_MOUNT       = %s", VOLUME_MOUNT or "(not set, using data/)")
    logger.info("DATA_DIR           = %s", DATA_DIR)
    logger.info("=" * 60)

    active = get_manager().active()
    if not active:
        logger.error("No active experiments found in registry. Exiting.")
        sys.exit(1)

    logger.info(
        "Launching schedulers for %d active experiments: %s",
        len(active),
        [e["id"] for e in active],
    )

    # Build process map
    procs: Dict[str, ExperimentProcess] = {
        e["id"]: ExperimentProcess(e) for e in active
    }

    # Start all subprocesses with a small stagger so log lines don't collide
    for p in procs.values():
        p.start()
        time.sleep(2)

    write_status(procs)

    # -----------------------------------------------------------------------
    # Graceful-shutdown handler
    # -----------------------------------------------------------------------

    _shutdown = False

    def _on_signal(signum: int, frame) -> None:  # type: ignore[type-arg]
        nonlocal _shutdown
        logger.info(
            "Received signal %s — initiating graceful shutdown",
            signal.Signals(signum).name,
        )
        _shutdown = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # -----------------------------------------------------------------------
    # Supervision loop
    # -----------------------------------------------------------------------

    logger.info("Supervision loop running (tick=%ds, restart_delay=%ds)",
                SUPERVISE_TICK_SECS, RESTART_DELAY_SECS)

    while not _shutdown:
        time.sleep(SUPERVISE_TICK_SECS)

        for exp_id, p in procs.items():
            if _shutdown:
                break
            if not p.alive():
                rc = p.proc.returncode if p.proc else "?"
                p.restart_count += 1
                logger.warning(
                    "[%s] Process exited (rc=%s). restart_count=%d. "
                    "Waiting %ds before restart…",
                    exp_id, rc, p.restart_count, RESTART_DELAY_SECS,
                )
                # Brief pause — don't tight-loop on a persistently crashing process
                time.sleep(RESTART_DELAY_SECS)
                if not _shutdown:
                    p.start()

        write_status(procs)

    # -----------------------------------------------------------------------
    # Shutdown: give subprocesses time to flush positions / heartbeats
    # -----------------------------------------------------------------------

    logger.info("Stopping all experiment subprocesses…")
    for p in procs.values():
        p.stop()
    write_status(procs)
    logger.info("Railway worker stopped cleanly.")


if __name__ == "__main__":
    main()
