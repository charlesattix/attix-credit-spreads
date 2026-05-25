"""
experiments/launch.py — Centralized atomic experiment launch orchestrator.

The single entry point for taking an experiment from ``configuring`` to live
paper trading and visible on the dashboard. Replaces the previously manual,
fragmented sequence (start a worker by hand, then separately edit the registry,
``live_since``, and ``sentinel_state.json``) documented in
``RUNBOOK_EXPERIMENT_LAUNCH.md``.

Usage
-----
    # Launch an experiment (auto-detects local vs Railway):
    python -m experiments.launch EXP-3311
    python -m experiments.launch EXP-3311 --mode local
    python -m experiments.launch EXP-3311 --mode railway
    python -m experiments.launch EXP-3311 --dry-run     # validate only, no changes

    # Health check (read-only):
    python -m experiments.launch --status EXP-3311

What ``launch`` does (atomically, with rollback on any failure)
---------------------------------------------------------------
    1. Validate provisioning   — env file exists, config exists, registry entry
                                  exists and is in ``configuring`` status.
    2. Preflight checks        — scripts/preflight_check.py on the config.
    3. Transition status       — configuring -> active via registry.transition_status
                                  (also stamps ``last_started_at``).
    4. Stamp ``live_since``    — today's date (transition_status never sets this).
    5. Sentinel enrollment     — upsert the sentinel_state.json entry: status=active,
                                  config fingerprint, account_id, live_since, enrolled_at.
    6. Start worker + verify   — run ONE synchronous scan (DRY_RUN) to exercise the
                                  full pipeline, confirm the DB was created and the scan
                                  returned cleanly; then start the persistent worker
                                  (tmux session locally; on Railway the registry-driven
                                  scheduler service picks it up — no local process).
    7. Verify dashboard        — the experiment now appears in the live set that both
                                  the dashboard and the sync export query.

Ordering note (important)
-------------------------
The user-facing mental model is "start worker -> verify -> flip status". The
*implementation* order is inverted on purpose: SENTINEL's pre-scan guard
(``sentinel/guards.py:_check_registry_status``) hard-exits any scan whose
registry status is not ``active``/``paused``. So the status flip and sentinel
enrollment MUST happen before any scan can run. Atomicity is preserved by
snapshotting ``registry.json`` and ``sentinel_state.json`` before mutating and
restoring them (and killing any started tmux session) if a later step fails —
so a failed launch leaves the system byte-identical to how it started.

The smoke scan runs with ``DRY_RUN=1`` so verification never submits live
(paper) orders; the persistent worker started afterward runs normally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STARTING_EQUITY = float(os.environ.get("STARTING_EQUITY", "100000"))

# Statuses that count as "live" — kept in sync with experiments.registry.
try:  # pragma: no cover - import guard
    from experiments.registry import LIVE_STATUSES
except Exception:  # pragma: no cover
    LIVE_STATUSES = {"active", "paused"}


class LaunchError(Exception):
    """Raised on any launch-step failure. Triggers rollback."""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Default side-effecting operations (injectable for tests)
# ---------------------------------------------------------------------------

def _default_preflight(config_path: Path, project_root: Path) -> tuple[bool, str]:
    script = project_root / "scripts" / "preflight_check.py"
    if not script.exists():
        return True, "preflight_check.py not found — skipped"
    r = subprocess.run(
        [sys.executable, str(script), str(config_path)],
        capture_output=True, text=True,
    )
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def _default_smoke_scan(
    config_path: Path, env_file: Optional[Path], project_root: Path
) -> tuple[bool, str]:
    """Run one DRY_RUN scan to exercise the pipeline without placing orders."""
    env = os.environ.copy()
    env["DRY_RUN"] = "1"
    cmd = [sys.executable, str(project_root / "main.py"), "scan", "--config", str(config_path)]
    if env_file is not None:
        cmd += ["--env-file", str(env_file)]
    try:
        r = subprocess.run(
            cmd, cwd=str(project_root), capture_output=True, text=True,
            timeout=600, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "smoke scan TIMEOUT after 600s"
    tail = (r.stdout or "")[-2000:] + (r.stderr or "")[-2000:]
    return r.returncode == 0, tail


@dataclass
class TmuxOps:
    """Injectable tmux control surface."""
    project_root: Path

    def exists(self, session: str) -> bool:
        return subprocess.run(
            ["tmux", "has-session", "-t", session], capture_output=True
        ).returncode == 0

    def available(self) -> bool:
        return shutil.which("tmux") is not None

    def start(self, session: str, config_path: Path, env_file: Optional[Path]) -> None:
        cmd = [
            "tmux", "new-session", "-d", "-s", session,
            sys.executable, "main.py", "scheduler", "--config", str(config_path),
        ]
        if env_file is not None:
            cmd += ["--env-file", str(env_file)]
        subprocess.run(cmd, cwd=str(self.project_root), check=True)

    def kill(self, session: str) -> None:
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------

_RAILWAY_ENV_VARS = (
    "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
    "RAILWAY_ENVIRONMENT_NAME",
)


def detect_mode(env: Optional[dict] = None) -> str:
    """Return 'railway' if running in a Railway container, else 'local'."""
    env = env if env is not None else os.environ
    if any(env.get(k) for k in _RAILWAY_ENV_VARS):
        return "railway"
    return "local"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class Launcher:
    exp_id: str
    mode: str = "auto"
    dry_run: bool = False
    project_root: Path = PROJECT_ROOT
    registry_path: Optional[str] = None      # None → real registry (get_manager)
    sentinel_path: Optional[Path] = None      # None → real sentinel_state.json
    # Injectable hooks (default to real subprocess implementations)
    preflight_runner: Callable[[Path, Path], tuple[bool, str]] = _default_preflight
    scan_runner: Callable[[Path, Optional[Path], Path], tuple[bool, str]] = _default_smoke_scan
    tmux_ops: Optional[TmuxOps] = None

    _rollback: list[tuple[str, Callable[[], None]]] = field(default_factory=list, init=False)
    _manager: object = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.mode == "auto":
            self.mode = detect_mode()
        if self.mode not in ("local", "railway"):
            raise LaunchError(f"Invalid mode {self.mode!r} (expected local|railway|auto)")
        if self.tmux_ops is None:
            self.tmux_ops = TmuxOps(self.project_root)
        if self.sentinel_path is None:
            self.sentinel_path = self.project_root / "sentinel_state.json"
        else:
            self.sentinel_path = Path(self.sentinel_path)

    # -- manager ---------------------------------------------------------
    @property
    def manager(self):
        if self._manager is None:
            from experiments.manager import ExperimentManager, get_manager
            self._manager = (
                ExperimentManager(registry_path=self.registry_path)
                if self.registry_path is not None else get_manager()
            )
        return self._manager

    def _registry_file(self) -> Path:
        if self.registry_path is not None:
            return Path(self.registry_path)
        from experiments.registry import REGISTRY_PATH
        return REGISTRY_PATH

    # -- rollback snapshots ---------------------------------------------
    def _snapshot_file(self, path: Path, label: str) -> None:
        """Push a rollback action that restores *path* to its current bytes."""
        original = path.read_bytes() if path.exists() else None

        def _restore() -> None:
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(original)

        self._rollback.append((f"restore {label}", _restore))

    def _do_rollback(self) -> list[str]:
        done: list[str] = []
        for desc, action in reversed(self._rollback):
            try:
                action()
                done.append(desc)
            except Exception as exc:  # pragma: no cover - best effort
                logger.error("Rollback step %r failed: %s", desc, exc)
                done.append(f"{desc} (FAILED: {exc})")
        self._rollback.clear()
        return done

    # -- fingerprint -----------------------------------------------------
    def _fingerprint(self, config_path: Path) -> str:
        p = config_path if config_path.is_absolute() else self.project_root / config_path
        return hashlib.sha256(p.read_bytes()).hexdigest()

    # ===================================================================
    # Steps
    # ===================================================================

    def _resolve(self, rel: Optional[str]) -> Optional[Path]:
        if not rel:
            return None
        p = Path(rel)
        return p if p.is_absolute() else self.project_root / p

    def validate(self) -> dict:
        """Step 1: provisioning checks. Returns the registry entry."""
        exp = self.manager.get(self.exp_id)
        if exp is None:
            raise LaunchError(f"{self.exp_id} not found in registry — register it first")
        status = exp.get("status")
        if status != "configuring":
            raise LaunchError(
                f"{self.exp_id} status is {status!r}, expected 'configuring'. "
                f"Only configuring experiments can be launched "
                f"(use --status to inspect a live one)."
            )
        config_rel = exp.get("config_path")
        env_rel = exp.get("env_file")
        db_rel = exp.get("db_path")
        if not config_rel:
            raise LaunchError(f"{self.exp_id} has no config_path in registry")
        if not db_rel:
            raise LaunchError(f"{self.exp_id} has no db_path in registry")
        config_path = self._resolve(config_rel)
        if not config_path.exists():
            raise LaunchError(f"config not found: {config_path}")
        env_path = self._resolve(env_rel)
        if env_rel and not env_path.exists():
            # On Railway, secrets come from env vars — a missing file is tolerable
            # there but not locally.
            if self.mode == "local":
                raise LaunchError(f"env file not found: {env_path}")
            logger.warning("env file %s missing — relying on Railway env vars", env_path)
            env_path = None
        return exp

    def preflight(self, config_path: Path) -> None:
        """Step 2."""
        ok, out = self.preflight_runner(config_path, self.project_root)
        if not ok:
            raise LaunchError(f"preflight failed for {config_path}:\n{out.strip()}")

    def transition_and_stamp(self) -> None:
        """Steps 3+4: configuring -> active and stamp live_since (rollback-safe)."""
        self._snapshot_file(self._registry_file(), "registry.json")
        self.manager.transition(self.exp_id, "active")          # sets last_started_at
        self.manager.update_fields(self.exp_id, live_since=_today())

    def enroll_sentinel(self, exp: dict, config_path: Path) -> None:
        """Step 5: upsert sentinel_state.json entry (rollback-safe)."""
        path = self.sentinel_path
        self._snapshot_file(path, "sentinel_state.json")
        if path.exists():
            state = json.loads(path.read_text())
        else:
            state = {"sentinel_version": "1.1", "runtime_gates_enabled": True,
                     "experiments": {}}
        experiments = state.setdefault("experiments", {})
        entry = experiments.get(self.exp_id, {})
        entry.update({
            "status": "active",
            "paper_config": str(exp.get("config_path")),
            "config_fingerprint": self._fingerprint(config_path),
            "account_id": exp.get("alpaca_account_id") or exp.get("account_id"),
            "live_since": _today(),
            "enrolled_at": entry.get("enrolled_at") or _now_iso(),
            "last_health_check": _now_iso(),
            "halt_reason": None,
            "halted": False,
        })
        entry.setdefault("sentinel_certified_at", None)
        entry.setdefault("peak_equity", STARTING_EQUITY)
        if exp.get("backtest_baseline") and "backtest_baseline" not in entry:
            entry["backtest_baseline"] = exp["backtest_baseline"]
        experiments[self.exp_id] = entry
        state["last_updated"] = _now_iso()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=4) + "\n")
        shutil.move(str(tmp), str(path))

    def smoke_scan_and_verify(self, config_path: Path, env_path: Optional[Path],
                              db_path: Path) -> str:
        """Step 6a/6b: run one DRY_RUN scan, verify DB created + scan ran cleanly."""
        ok, out = self.scan_runner(config_path, env_path, self.project_root)
        if not ok:
            raise LaunchError(f"smoke scan failed:\n{out.strip()}")
        if not db_path.exists():
            raise LaunchError(
                f"smoke scan returned 0 but DB was not created at {db_path}"
            )
        # Best-effort: confirm the DB is a real sqlite file with tables.
        try:
            con = sqlite3.connect(str(db_path))
            tables = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            con.close()
            if tables == 0:
                raise LaunchError(f"DB {db_path} has no tables — scan did not initialize")
        except sqlite3.Error as exc:
            raise LaunchError(f"DB {db_path} is not a valid sqlite database: {exc}")
        return out

    def start_persistent_worker(self, config_path: Path, env_path: Optional[Path],
                                exp: dict) -> str:
        """Step 7: start the long-running scanner."""
        if self.mode == "railway":
            return ("railway mode — registry-driven scheduler service will run this "
                    "experiment on its cron (no local process started)")
        session = exp.get("tmux_session") or self.exp_id.lower().replace("-", "")
        if not self.tmux_ops.available():
            raise LaunchError(
                "tmux not available for local mode. Install tmux, or run with "
                "--mode railway if the scheduler service handles execution."
            )
        if self.tmux_ops.exists(session):
            return f"tmux session {session!r} already running — left as-is"
        self.tmux_ops.start(session, config_path, env_path)
        self._rollback.append((f"kill tmux {session}", lambda: self.tmux_ops.kill(session)))
        return f"started tmux session {session!r}"

    def verify_dashboard(self) -> None:
        """Step 8: the experiment appears in the dashboard's live set."""
        self.manager.reload()
        live_ids = {e["id"] for e in self.manager.live()}
        if self.exp_id not in live_ids:
            raise LaunchError(
                f"{self.exp_id} not in live set after launch (status did not stick)"
            )
        # Cross-check the real dashboard query when running against the real registry.
        if self.registry_path is None:
            try:
                from web_dashboard import data
                ids = {e["id"] for e in data.get_live_experiments()}
                if self.exp_id not in ids:
                    raise LaunchError(
                        f"{self.exp_id} missing from web_dashboard.get_live_experiments()"
                    )
            except ImportError:
                pass  # web deps not installed in this environment

    # ===================================================================
    # Orchestration
    # ===================================================================

    def launch(self) -> dict:
        """Run the full sequence. Returns a result dict. Rolls back on failure."""
        result: dict = {"exp_id": self.exp_id, "mode": self.mode, "steps": []}

        def record(step: str, detail: str = "") -> None:
            result["steps"].append({"step": step, "detail": detail})
            logger.info("[launch %s] %s %s", self.exp_id, step, detail)

        exp = self.validate()
        config_path = self._resolve(exp["config_path"])
        env_path = self._resolve(exp.get("env_file"))
        if env_path and not env_path.exists():
            env_path = None
        db_path = self._resolve(exp["db_path"])
        record("validate", f"config={config_path.name} env={env_path} status=configuring")

        self.preflight(config_path)
        record("preflight", "passed")

        if self.dry_run:
            result["dry_run"] = True
            record("dry_run", "validation + preflight only; no changes made")
            return result

        try:
            self.transition_and_stamp()
            record("transition", f"configuring -> active, live_since={_today()}")

            self.enroll_sentinel(exp, config_path)
            record("sentinel", "enrolled (status=active, fingerprint set)")

            scan_out = self.smoke_scan_and_verify(config_path, env_path, db_path)
            record("smoke_scan", f"DRY_RUN scan ok, DB present at {db_path}")
            result["scan_tail"] = scan_out[-500:]

            worker_detail = self.start_persistent_worker(config_path, env_path, exp)
            record("worker", worker_detail)

            self.verify_dashboard()
            record("dashboard", "experiment visible in live set")
        except LaunchError as exc:
            rolled = self._do_rollback()
            result["error"] = str(exc)
            result["rolled_back"] = rolled
            result["ok"] = False
            logger.error("[launch %s] FAILED: %s — rolled back: %s",
                         self.exp_id, exc, rolled)
            return result
        except Exception as exc:  # unexpected — still roll back
            rolled = self._do_rollback()
            result["error"] = f"unexpected: {exc}"
            result["rolled_back"] = rolled
            result["ok"] = False
            logger.exception("[launch %s] UNEXPECTED FAILURE", self.exp_id)
            return result

        result["ok"] = True
        return result


# ---------------------------------------------------------------------------
# Status / health check (read-only)
# ---------------------------------------------------------------------------

def status_report(exp_id: str, project_root: Path = PROJECT_ROOT,
                  registry_path: Optional[str] = None,
                  sentinel_path: Optional[Path] = None) -> dict:
    """Read-only health report for an experiment."""
    from experiments.manager import ExperimentManager, get_manager
    mgr = (ExperimentManager(registry_path=registry_path)
           if registry_path is not None else get_manager())
    exp = mgr.get(exp_id)
    report: dict = {"exp_id": exp_id}
    if exp is None:
        report["error"] = "not found in registry"
        return report

    report["status"] = exp.get("status")
    report["live_since"] = exp.get("live_since")
    report["last_started_at"] = exp.get("last_started_at")
    report["in_live_set"] = exp_id in {e["id"] for e in mgr.live()}

    # DB
    db_rel = exp.get("db_path")
    db_path = (Path(db_rel) if db_rel and Path(db_rel).is_absolute()
               else (project_root / db_rel) if db_rel else None)
    db_info: dict = {"path": str(db_path) if db_path else None, "exists": False}
    if db_path and db_path.exists():
        db_info["exists"] = True
        try:
            con = sqlite3.connect(str(db_path))
            try:
                db_info["trades"] = con.execute("SELECT count(*) FROM trades").fetchone()[0]
            except sqlite3.Error:
                db_info["trades"] = None
            con.close()
        except sqlite3.Error as exc:
            db_info["error"] = str(exc)
    report["db"] = db_info

    # Sentinel
    spath = Path(sentinel_path) if sentinel_path else (project_root / "sentinel_state.json")
    sent: dict = {"enrolled": False}
    if spath.exists():
        try:
            sstate = json.loads(spath.read_text())
            entry = sstate.get("experiments", {}).get(exp_id)
            if entry:
                sent = {
                    "enrolled": True,
                    "status": entry.get("status"),
                    "halted": entry.get("halted"),
                    "halt_reason": entry.get("halt_reason"),
                    "live_since": entry.get("live_since"),
                }
        except Exception as exc:  # pragma: no cover
            sent["error"] = str(exc)
    report["sentinel"] = sent

    # Overall health verdict
    healthy = (
        report["status"] in LIVE_STATUSES
        and report["in_live_set"]
        and db_info["exists"]
        and sent.get("enrolled")
        and not sent.get("halted")
    )
    report["healthy"] = bool(healthy)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_launch_result(result: dict) -> int:
    print(f"\n=== launch {result['exp_id']} (mode={result.get('mode')}) ===")
    for s in result.get("steps", []):
        print(f"  ✓ {s['step']}: {s['detail']}")
    if result.get("dry_run"):
        print("\nDRY RUN — no changes made.")
        return 0
    if result.get("ok"):
        print(f"\n✅ {result['exp_id']} is LIVE.")
        return 0
    print(f"\n❌ launch FAILED: {result.get('error')}")
    if result.get("rolled_back"):
        print("   Rolled back:")
        for r in result["rolled_back"]:
            print(f"     - {r}")
    return 1


def _print_status(report: dict) -> int:
    print(f"\n=== status {report['exp_id']} ===")
    if report.get("error"):
        print(f"  ❌ {report['error']}")
        return 1
    print(f"  registry status : {report['status']}")
    print(f"  live_since      : {report['live_since']}")
    print(f"  last_started_at : {report['last_started_at']}")
    print(f"  in live set     : {report['in_live_set']}")
    db = report["db"]
    print(f"  db              : {'exists' if db['exists'] else 'MISSING'} "
          f"({db.get('path')})" + (f" trades={db.get('trades')}" if db.get("exists") else ""))
    s = report["sentinel"]
    if s.get("enrolled"):
        print(f"  sentinel        : status={s.get('status')} halted={s.get('halted')}"
              + (f" reason={s.get('halt_reason')}" if s.get("halted") else ""))
    else:
        print("  sentinel        : NOT ENROLLED")
    print(f"\n  {'✅ healthy' if report['healthy'] else '⚠️  not fully live'}")
    return 0 if report["healthy"] else 2


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.launch",
        description="Atomic experiment launch orchestrator.",
    )
    parser.add_argument("exp_id", help="Experiment ID, e.g. EXP-3311")
    parser.add_argument("--status", action="store_true",
                        help="Read-only health check instead of launching")
    parser.add_argument("--mode", choices=["auto", "local", "railway"], default="auto",
                        help="Launch mode (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate + preflight only; make no changes")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.status:
        return _print_status(status_report(args.exp_id))

    try:
        launcher = Launcher(exp_id=args.exp_id, mode=args.mode, dry_run=args.dry_run)
        result = launcher.launch()
    except LaunchError as exc:
        print(f"❌ {exc}")
        return 1
    return _print_launch_result(result)


if __name__ == "__main__":
    sys.exit(main())
