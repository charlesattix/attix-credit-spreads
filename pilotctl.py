#!/usr/bin/env python3
"""pilotctl — lightweight experiment manager using tmux.

Usage:
    python pilotctl.py start   [exp_name|all]
    python pilotctl.py stop    [exp_name|all]
    python pilotctl.py restart [exp_name|all]
    python pilotctl.py status  [exp_name|all]
    python pilotctl.py logs    <exp_name>
"""

import subprocess
import sys
from pathlib import Path

from experiments.manager import get_manager


def _resolve_targets(experiments: dict, target: str) -> list:
    """Return list of (name, config) tuples for the given target."""
    if target == "all":
        return list(experiments.items())
    if target not in experiments:
        print(f"Unknown experiment: {target}")
        print(f"Available: {', '.join(experiments.keys())}")
        sys.exit(1)
    return [(target, experiments[target])]


def _tmux_session_exists(session: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    return result.returncode == 0


def _run_preflight(config_file: str) -> bool:
    """Run preflight checks on the config file. Returns True if passed."""
    preflight = Path(__file__).parent / "scripts" / "preflight_check.py"
    if not preflight.exists():
        return True  # skip if preflight script not yet created
    result = subprocess.run(
        [sys.executable, str(preflight), config_file],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        return False
    return True


def cmd_start(experiments: dict, target: str) -> None:
    for name, cfg in _resolve_targets(experiments, target):
        session = cfg["tmux_session"]
        if _tmux_session_exists(session):
            print(f"[{name}] already running in tmux session '{session}'")
            continue
        if not _run_preflight(cfg.get("config_path", "")):
            print(f"[{name}] preflight FAILED — not starting")
            continue
        # SECURITY AUDIT #5: use list args to prevent shell injection via
        # config_file/env_file values containing shell metacharacters.
        cp = cfg.get("config_path") or ""
        if not cp or not Path(cp).exists():
            print(f"[{name}] config file not found: {cp!r}")
            continue
        ep = cfg.get("env_file") or ""
        if not ep or not Path(ep).exists():
            print(f"[{name}] env file not found: {ep!r}")
            continue
        config_path = Path(cp)
        env_path    = Path(ep)
        subprocess.run(
            [
                "tmux", "new-session", "-d", "-s", session,
                sys.executable, "main.py", "scheduler",
                "--config", str(config_path),
                "--env-file", str(env_path),
            ],
            check=True,
        )
        print(f"[{name}] started in tmux session '{session}'")


def cmd_stop(experiments: dict, target: str) -> None:
    for name, cfg in _resolve_targets(experiments, target):
        session = cfg["tmux_session"]
        if not _tmux_session_exists(session):
            print(f"[{name}] not running")
            continue
        subprocess.run(["tmux", "kill-session", "-t", session], check=True)
        print(f"[{name}] stopped")


def cmd_restart(experiments: dict, target: str) -> None:
    cmd_stop(experiments, target)
    cmd_start(experiments, target)


def cmd_status(experiments: dict, target: str) -> None:
    for name, cfg in _resolve_targets(experiments, target):
        session = cfg["tmux_session"]
        running = _tmux_session_exists(session)
        db_path = cfg.get("db_path", "")
        db_exists = bool(db_path) and Path(db_path).exists()
        status = "RUNNING" if running else "STOPPED"
        db_status = "exists" if db_exists else "missing"
        print(f"[{name}] {status}  (session: {session}, db: {db_status})")


def cmd_logs(experiments: dict, target: str) -> None:
    if target == "all":
        print("Specify a single experiment for logs (not 'all')")
        sys.exit(1)
    targets = _resolve_targets(experiments, target)
    _, cfg = targets[0]
    session = cfg["tmux_session"]
    if not _tmux_session_exists(session):
        print(f"[{target}] not running — no active session")
        sys.exit(1)
    subprocess.run(["tmux", "attach-session", "-t", session])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else "all"

    experiments = get_manager().live()

    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "status": cmd_status,
        "logs": cmd_logs,
    }

    if command not in commands:
        print(f"Unknown command: {command}")
        print(f"Available: {', '.join(commands.keys())}")
        sys.exit(1)

    commands[command](experiments, target)


if __name__ == "__main__":
    main()
