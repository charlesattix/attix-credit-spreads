"""
experiments/registry.py — Registry library for experiment lifecycle management.

The experiment registry (experiments/registry.json) is the single source of
truth for ALL experiments.  This module provides the read/write API, status
transitions, validation, and orphan detection.

Schema v3.0 statuses:
    registered  → configuring → active → paused → active (resume)
                                       → stopped → active (restart)
                                       → retired (terminal)
                                       → failed → configuring (retry)
    completed   (terminal — research entries only)
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = PROJECT_ROOT / "experiments" / "registry.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "3.0"

VALID_STATUSES = {
    "registered", "configuring", "active", "paused",
    "stopped", "retired", "failed", "completed",
}

ACTIVE_STATUSES = {"active"}

LIVE_STATUSES = {"active", "paused"}  # experiments that have infrastructure

TERMINAL_STATUSES = {"retired", "completed"}

VALID_TRANSITIONS: dict[str, set[str]] = {
    "registered":  {"configuring", "retired"},
    "configuring": {"active", "registered", "retired"},
    "active":      {"paused", "stopped", "retired", "failed"},
    "paused":      {"active", "stopped", "retired"},
    "stopped":     {"active", "configuring", "retired"},
    "retired":     set(),
    "failed":      {"configuring", "retired"},
    "completed":   set(),
}

VALID_CREATORS = {"maximus", "charles"}

CREATOR_RANGES = {
    "maximus": (0, 100000),
    "charles": (0, 100000),
}

CREATOR_RANGE_EXCEPTIONS: set[str] = {"EXP-1220"}

# Research entry suffixes — excluded from orphan enforcement
_RESEARCH_SUFFIXES = ("-max", "-real", "-paper", "-validation")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_research_entry(exp_id: str) -> bool:
    """True for research/analysis experiments (e.g. EXP-810-max)."""
    return any(exp_id.endswith(s) for s in _RESEARCH_SUFFIXES)


def _exp_number(exp_id: str) -> Optional[int]:
    """Extract the numeric part of EXP-NNN. Returns None if non-numeric."""
    m = re.match(r"^EXP-(\d+)$", exp_id, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_file_for_exp(exp_id: str) -> Optional[str]:
    """Return relative path to .env.expNNN if it exists on disk."""
    num = exp_id.replace("EXP-", "").lower()
    path = PROJECT_ROOT / f".env.exp{num}"
    if path.exists():
        return f".env.exp{num}"
    return None


def _db_path_for_exp(exp_id: str) -> Optional[str]:
    """Return relative path to experiment DB if it exists on disk."""
    num = exp_id.replace("EXP-", "").lower()
    candidates = [
        f"data/exp{num}/pilotai_exp{num}.db",
        f"data/pilotai_exp{num}.db",
    ]
    for c in candidates:
        if (PROJECT_ROOT / c).exists():
            return c
    return None


def _infer_strategy_type(exp: dict) -> Optional[str]:
    """Infer strategy type from experiment metadata."""
    desc = (exp.get("description") or "") + " " + (exp.get("notes") or "")
    desc = desc.lower()
    if "leverage" in desc:
        return "leverage"
    if "straddle" in desc or "strangle" in desc:
        return "straddle_strangle"
    if "iron condor" in desc or "ic " in desc:
        return "iron_condor"
    if "ml" in desc or "xgboost" in desc or "ensemble" in desc:
        return "ml_credit_spread"
    if "credit spread" in desc or "bull put" in desc or "bear call" in desc:
        return "credit_spread"
    if "kelly" in desc:
        return "kelly_credit_spread"
    if "compass" in desc or "sector" in desc or "portfolio" in desc:
        return "portfolio"
    return None


# ---------------------------------------------------------------------------
# Core I/O
# ---------------------------------------------------------------------------


def load_registry() -> dict:
    """Load experiments/registry.json. Returns empty structure on error."""
    try:
        if REGISTRY_PATH.exists():
            with open(REGISTRY_PATH, encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        logger.warning("Registry: could not read %s: %s", REGISTRY_PATH, exc)
    return {"schema_version": SCHEMA_VERSION, "last_updated": "", "experiments": {}}


def save_registry(registry: dict) -> None:
    """Atomically write registry.json with file locking."""
    registry["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tmp_path = REGISTRY_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(registry, fh, indent=4)
            fh.write("\n")
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    tmp_path.rename(REGISTRY_PATH)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def get_experiment(exp_id: str, registry: Optional[dict] = None) -> Optional[dict]:
    """Return experiment dict or None."""
    if registry is None:
        registry = load_registry()
    return registry.get("experiments", {}).get(exp_id)


def get_experiments_by_status(*statuses: str, registry: Optional[dict] = None) -> list[dict]:
    """Return experiments matching any of the given statuses."""
    if registry is None:
        registry = load_registry()
    return [
        e for e in registry.get("experiments", {}).values()
        if e.get("status") in statuses
    ]


def get_active_experiments(registry: Optional[dict] = None) -> list[dict]:
    """Return experiments with status == 'active'."""
    return get_experiments_by_status("active", registry=registry)


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def transition_status(
    exp_id: str,
    new_status: str,
    *,
    reason: str = "",
    registry: Optional[dict] = None,
) -> dict:
    """
    Transition an experiment to a new status.

    Returns the updated experiment dict.
    Raises ValueError on invalid transition.
    """
    if registry is None:
        registry = load_registry()

    exp = registry.get("experiments", {}).get(exp_id)
    if not exp:
        raise ValueError(f"Experiment {exp_id} not found in registry")

    current = exp.get("status", "registered")
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Valid: {sorted(VALID_STATUSES)}")

    allowed = VALID_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition {exp_id} from '{current}' to '{new_status}'. "
            f"Allowed: {sorted(allowed) if allowed else 'none (terminal state)'}"
        )

    exp["status"] = new_status
    exp["updated_at"] = _now_iso()

    if new_status == "active":
        exp["last_started_at"] = _now_iso()
    elif new_status in ("stopped", "paused"):
        exp["last_stopped_at"] = _now_iso()
    elif new_status == "retired":
        exp["retired_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if reason:
            exp["retired_reason"] = reason
    elif new_status == "failed" and reason:
        exp["failure_reason"] = reason

    if reason and new_status not in ("retired", "failed"):
        exp["status_reason"] = reason

    save_registry(registry)
    return exp


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?")

REQUIRED_FIELDS = ["id", "name", "created_by", "status"]


def validate(registry: dict, *, strict: bool = False) -> list[str]:
    """Validate registry.json. Returns list of error strings (empty = valid)."""
    errors: list[str] = []
    experiments = registry.get("experiments", {})

    if not experiments:
        errors.append("No experiments found in registry.")
        return errors

    if not registry.get("schema_version"):
        errors.append("Registry missing 'schema_version'.")
    if not registry.get("last_updated"):
        errors.append("Registry missing 'last_updated'.")

    for exp_id, exp in experiments.items():
        pfx = f"[{exp_id}]"

        # ID key must match id field
        if exp.get("id") != exp_id:
            errors.append(f"{pfx} id field '{exp.get('id')}' != registry key '{exp_id}'.")

        # Required fields
        for field in REQUIRED_FIELDS:
            if not exp.get(field):
                errors.append(f"{pfx} Missing required field: '{field}'.")

        # Creator validation
        creator = exp.get("created_by", "")
        if creator not in VALID_CREATORS:
            errors.append(f"{pfx} Invalid created_by='{creator}'. Must be one of: {sorted(VALID_CREATORS)}.")

        # Status validation
        status = exp.get("status", "")
        if status not in VALID_STATUSES:
            errors.append(f"{pfx} Invalid status='{status}'. Must be one of: {sorted(VALID_STATUSES)}.")

        # ID range vs creator (skip research entries)
        num = _exp_number(exp_id)
        if num is not None and creator in CREATOR_RANGES and exp_id not in CREATOR_RANGE_EXCEPTIONS:
            lo, hi = CREATOR_RANGES[creator]
            if not (lo <= num <= hi):
                errors.append(f"{pfx} ID number {num} out of range for '{creator}' (allowed: {lo}–{hi}).")

        # Strict: active experiments must have config_path and env_file
        if strict and status == "active" and not is_research_entry(exp_id):
            if not exp.get("config_path"):
                errors.append(f"{pfx} Active experiment missing 'config_path'.")
            if not exp.get("env_file"):
                errors.append(f"{pfx} Active experiment missing 'env_file'.")
            if not exp.get("alpaca_account_id"):
                errors.append(f"{pfx} Active experiment missing 'alpaca_account_id'.")

        # Strict: retired experiments should have retired_date
        if strict and status == "retired":
            if not exp.get("retired_date"):
                errors.append(f"{pfx} Retired experiment missing 'retired_date'.")

        # Date format checks
        for date_field in ("retired_date",):
            val = exp.get(date_field)
            if val and not _DATE_RE.match(str(val)):
                errors.append(f"{pfx} '{date_field}' must be YYYY-MM-DD, got '{val}'.")

        superseded_by = exp.get("superseded_by")
        if superseded_by and not re.match(r"^EXP-\w+$", str(superseded_by), re.IGNORECASE):
            errors.append(f"{pfx} 'superseded_by' must be an EXP-ID, got '{superseded_by}'.")

    return errors


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------


def find_orphan_env_files(registry: Optional[dict] = None) -> list[str]:
    """Find .env.exp* files that have no registry entry."""
    if registry is None:
        registry = load_registry()
    experiments = registry.get("experiments", {})

    orphans = []
    for p in sorted(PROJECT_ROOT.glob(".env.exp*")):
        if p.name.endswith(".example"):
            continue
        # Extract experiment number from filename
        m = re.match(r"^\.env\.exp(\d+)$", p.name)
        if not m:
            continue
        num = m.group(1)
        # Try both EXP-036 and EXP-36 forms (registry may use either)
        candidates = {f"EXP-{num}", f"EXP-{int(num)}"}
        if not candidates & set(experiments.keys()):
            orphans.append(p.name)
    return orphans


def find_orphan_dbs(registry: Optional[dict] = None) -> list[str]:
    """Find experiment DB files that have no registry entry."""
    if registry is None:
        registry = load_registry()
    experiments = registry.get("experiments", {})

    orphans = []
    data_dir = PROJECT_ROOT / "data"
    if not data_dir.exists():
        return orphans

    for db_dir in sorted(data_dir.glob("exp*")):
        if not db_dir.is_dir():
            continue
        m = re.match(r"^exp(\d+)$", db_dir.name)
        if not m:
            continue
        num = m.group(1)
        candidates = {f"EXP-{num}", f"EXP-{int(num)}"}
        # Check if any DB file exists in this directory
        dbs = list(db_dir.glob("pilotai_*.db"))
        if dbs and not (candidates & set(experiments.keys())):
            orphans.append(str(dbs[0].relative_to(PROJECT_ROOT)))
    return orphans


def find_orphan_processes() -> list[dict]:
    """Find running scanner processes for experiments not in registry."""
    registry = load_registry()
    experiments = registry.get("experiments", {})
    active_ids = {
        e["id"] for e in experiments.values()
        if e.get("status") in LIVE_STATUSES
    }

    orphans = []
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            # Match scanner scripts and main.py with config args
            if "exp" not in line.lower() and "scanner" not in line.lower():
                continue
            if "python" not in line.lower():
                continue
            # Try to extract experiment ID from command line
            m = re.search(r"EXP-(\d+)", line, re.IGNORECASE)
            if not m:
                m = re.search(r"exp(\d+)", line)
            if m:
                num = m.group(1) if "EXP-" in line.upper() else m.group(1)
                exp_id = f"EXP-{int(num)}"
                if exp_id not in active_ids and exp_id in experiments:
                    # Running for a non-active experiment
                    orphans.append({"exp_id": exp_id, "process": line.strip()})
    except Exception as exc:
        logger.warning("Could not scan processes: %s", exc)
    return orphans


def find_active_not_running(registry: Optional[dict] = None) -> list[str]:
    """Find experiments that are 'active' but have no running scanner process."""
    if registry is None:
        registry = load_registry()

    active_exps = get_active_experiments(registry)
    if not active_exps:
        return []

    missing = []
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        )
        ps_output = result.stdout.lower()
        for exp in active_exps:
            exp_id = exp["id"]
            if is_research_entry(exp_id):
                continue
            num = exp_id.replace("EXP-", "").lower()
            # Check if any process references this experiment
            patterns = [
                f"exp{num}",
                f"exp-{num}",
                exp_id.lower(),
            ]
            found = any(p in ps_output for p in patterns)
            if not found:
                missing.append(exp_id)
    except Exception as exc:
        logger.warning("Could not scan processes: %s", exc)
    return missing


# ---------------------------------------------------------------------------
# Schema migration v2.1 → v3.0
# ---------------------------------------------------------------------------

_STATUS_MAP = {
    "paper_trading": "active",
    "in_development": "registered",
    "paper_trading_prep": "configuring",
    "data_collection": "registered",
    "backtesting": "configuring",
    "validated": "configuring",
    "awaiting_deploy": "configuring",
    "pending": "registered",
    # These stay the same
    "active": "active",
    "retired": "retired",
    "completed": "completed",
    "registered": "registered",
    "configuring": "configuring",
    "paused": "paused",
    "stopped": "stopped",
    "failed": "failed",
}


def migrate_v2_to_v3(registry: dict) -> dict:
    """Migrate registry from schema v2.1 to v3.0."""
    now = _now_iso()
    registry["schema_version"] = SCHEMA_VERSION

    for exp_id, exp in registry.get("experiments", {}).items():
        old_status = exp.get("status", "registered")
        new_status = _STATUS_MAP.get(old_status, old_status)
        exp["status"] = new_status

        # Rename paper_config → config_path (keep original for reference)
        if "paper_config" in exp and "config_path" not in exp:
            exp["config_path"] = exp.get("paper_config")

        # Rename account_id → alpaca_account_id
        if "account_id" in exp and "alpaca_account_id" not in exp:
            exp["alpaca_account_id"] = exp.get("account_id")

        # Add env_file if discoverable
        if "env_file" not in exp and not is_research_entry(exp_id):
            exp["env_file"] = _env_file_for_exp(exp_id)

        # Add db_path if discoverable
        if "db_path" not in exp and not is_research_entry(exp_id):
            exp["db_path"] = _db_path_for_exp(exp_id)

        # Infer strategy_type
        if "strategy_type" not in exp and not is_research_entry(exp_id):
            exp["strategy_type"] = _infer_strategy_type(exp)

        # Normalize created_at from created_date
        if "created_at" not in exp:
            cd = exp.get("created_date")
            if cd:
                exp["created_at"] = f"{cd}T00:00:00+00:00"
            else:
                exp["created_at"] = now

        # Set updated_at
        exp["updated_at"] = now

        # Set last_started_at from live_since for active experiments
        if new_status == "active" and "last_started_at" not in exp:
            ls = exp.get("live_since")
            if ls:
                exp["last_started_at"] = f"{ls}T00:00:00+00:00"

    return registry
