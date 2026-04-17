"""sentinel/state.py — Read/write sentinel_state.json.

sentinel_state.json is the SENTINEL coordination file. It lives at the project
root and is read by every scanner at startup to verify:
  - The experiment is still active (not halted)
  - The paper config fingerprint matches (no config drift)

Usage::

    from sentinel.state import load_state, get_experiment, set_halt, clear_halt, update_fingerprint

    state = load_state()
    exp = get_experiment(state, "EXP-400")   # raises KeyError if missing

    # Check before trading
    if exp["status"] != "active":
        raise RuntimeError(f"EXP-400 is {exp['status']}: {exp['halt_reason']}")

    # Halt an experiment (e.g. from daily health check)
    set_halt("EXP-400", "Alpaca API returned 401 — keys rotated")

    # Clear halt after manual review
    clear_halt("EXP-400")

    # Update fingerprint after approved config change
    update_fingerprint("EXP-400", "configs/paper_champion.yaml")
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# sentinel_state.json lives at the project root (one directory up from sentinel/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = _PROJECT_ROOT / "sentinel_state.json"

# Valid experiment statuses in sentinel_state.json
VALID_SENTINEL_STATUSES = {"active", "halted", "paused", "retired"}


# ---------------------------------------------------------------------------
# Core read/write
# ---------------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    """Load sentinel_state.json from disk. Raises FileNotFoundError if missing."""
    if not STATE_PATH.exists():
        raise FileNotFoundError(
            f"sentinel_state.json not found at {STATE_PATH}. "
            "Run `python scripts/init_sentinel.py` to initialise."
        )
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state: dict[str, Any]) -> None:
    """Atomically write sentinel_state.json (write to .tmp then rename)."""
    state["last_updated"] = _now_iso()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=4)
        f.write("\n")
    shutil.move(str(tmp), str(STATE_PATH))


# ---------------------------------------------------------------------------
# Experiment accessors
# ---------------------------------------------------------------------------

def get_experiment(state: dict[str, Any], exp_id: str) -> dict[str, Any]:
    """Return the sentinel entry for *exp_id*. Raises KeyError if not enrolled."""
    experiments = state.get("experiments", {})
    if exp_id not in experiments:
        raise KeyError(
            f"{exp_id} is not enrolled in sentinel_state.json. "
            "Enroll it before trading."
        )
    return experiments[exp_id]


def list_active(state: dict[str, Any]) -> list[str]:
    """Return list of experiment IDs with status == 'active'."""
    return [
        exp_id
        for exp_id, exp in state.get("experiments", {}).items()
        if exp.get("status") == "active"
    ]


# ---------------------------------------------------------------------------
# Halt / clear-halt
# ---------------------------------------------------------------------------

def set_halt(exp_id: str, reason: str) -> None:
    """Halt *exp_id*. Writes sentinel_state.json atomically."""
    state = load_state()
    exp = get_experiment(state, exp_id)
    exp["status"] = "halted"
    exp["halt_reason"] = reason
    save_state(state)


def clear_halt(exp_id: str) -> None:
    """Clear halt on *exp_id*, restoring status to 'active'."""
    state = load_state()
    exp = get_experiment(state, exp_id)
    if exp["status"] != "halted":
        raise ValueError(f"{exp_id} is not halted (status={exp['status']!r}).")
    exp["status"] = "active"
    exp["halt_reason"] = None
    save_state(state)


# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------

def compute_fingerprint(config_path: str | Path) -> str:
    """Return the SHA-256 hex digest of *config_path* contents."""
    path = Path(config_path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_fingerprint(state: dict[str, Any], exp_id: str) -> bool:
    """Return True if the on-disk config matches the enrolled fingerprint."""
    exp = get_experiment(state, exp_id)
    expected = exp.get("config_fingerprint")
    if not expected:
        return True  # no fingerprint enrolled — skip check
    actual = compute_fingerprint(exp["paper_config"])
    return actual == expected


def update_fingerprint(exp_id: str, config_path: str | None = None) -> str:
    """Recompute and store the fingerprint for *exp_id*. Returns new digest."""
    state = load_state()
    exp = get_experiment(state, exp_id)
    path = config_path or exp.get("paper_config")
    if not path:
        raise ValueError(f"{exp_id} has no paper_config enrolled.")
    digest = compute_fingerprint(path)
    exp["config_fingerprint"] = digest
    exp["paper_config"] = str(path)
    save_state(state)
    return digest


# ---------------------------------------------------------------------------
# Health-check timestamp
# ---------------------------------------------------------------------------

def record_health_check(exp_id: str) -> None:
    """Stamp last_health_check for *exp_id* with current UTC time."""
    state = load_state()
    exp = get_experiment(state, exp_id)
    exp["last_health_check"] = _now_iso()
    save_state(state)


# ---------------------------------------------------------------------------
# SENTINEL certification
# ---------------------------------------------------------------------------

def certify(exp_id: str) -> None:
    """Mark *exp_id* as SENTINEL-certified (all 10 pre-flight gates passed)."""
    state = load_state()
    exp = get_experiment(state, exp_id)
    exp["sentinel_certified_at"] = _now_iso()
    save_state(state)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
