"""
experiments/manager.py — High-level ExperimentManager.

Single entry point for all experiment data access. Wraps experiments/registry.py
I/O with a high-level API. This is purely additive — nothing existing changes.

Usage:
    from experiments.manager import get_manager
    mgr = get_manager()
    active = mgr.active()
    baseline = mgr.baseline("EXP-400")
"""
from __future__ import annotations

from typing import Any, Optional

from experiments.registry import (
    LIVE_STATUSES,
    VALID_STATUSES,
    VALID_TRANSITIONS,
    load_registry,
    save_registry,
    transition_status,
    validate,
    _now_iso,
)

_manager: "ExperimentManager | None" = None


class ExperimentManager:
    """Single entry point for all experiment data access.

    Wraps experiments/registry.py I/O with a high-level API.
    This is purely additive — nothing existing changes.
    """

    def __init__(self, registry_path: Optional[str] = None) -> None:
        self._registry_path = registry_path
        self.reload()

    # ------------------------------------------------------------------
    # Core I/O
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-read registry.json from disk."""
        if self._registry_path is not None:
            import json
            from pathlib import Path
            try:
                with open(self._registry_path, encoding="utf-8") as fh:
                    self._registry = json.load(fh)
            except Exception:
                self._registry = {"schema_version": "3.0", "last_updated": "", "experiments": {}}
        else:
            self._registry = load_registry()

    def _experiments(self) -> dict[str, dict]:
        return self._registry.get("experiments", {})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, exp_id: str) -> dict | None:
        """Get single experiment by ID."""
        return self._experiments().get(exp_id)

    def all(self) -> dict[str, dict]:
        """All experiments as {exp_id: dict}."""
        return dict(self._experiments())

    def active(self) -> list[dict]:
        """Experiments with status 'active'."""
        return [e for e in self._experiments().values() if e.get("status") == "active"]

    def live(self) -> list[dict]:
        """Experiments with status in LIVE_STATUSES (active, paused)."""
        return [e for e in self._experiments().values() if e.get("status") in LIVE_STATUSES]

    def by_status(self, *statuses: str) -> list[dict]:
        """Filter experiments by one or more statuses."""
        return [e for e in self._experiments().values() if e.get("status") in statuses]

    def by_ticker(self, ticker: str) -> list[dict]:
        """Filter experiments by ticker (exact match)."""
        return [e for e in self._experiments().values() if e.get("ticker") == ticker]

    def by_creator(self, creator: str) -> list[dict]:
        """Filter experiments by created_by."""
        return [e for e in self._experiments().values() if e.get("created_by") == creator]

    # ------------------------------------------------------------------
    # Field accessors
    # ------------------------------------------------------------------

    def baseline(self, exp_id: str) -> dict | None:
        """Get backtest_baseline for an experiment."""
        exp = self.get(exp_id)
        if exp is None:
            return None
        return exp.get("backtest_baseline")

    def baselines_map(self) -> dict[str, dict]:
        """Return {exp_id: baseline} for all experiments that have one."""
        return {
            exp_id: exp["backtest_baseline"]
            for exp_id, exp in self._experiments().items()
            if exp.get("backtest_baseline")
        }

    def accounts_map(self) -> dict[str, str]:
        """Return {exp_id: alpaca_account_id} for active experiments."""
        result = {}
        for exp_id, exp in self._experiments().items():
            if exp.get("status") == "active":
                account_id = exp.get("alpaca_account_id")
                if account_id:
                    result[exp_id] = account_id
        return result

    def env_file(self, exp_id: str) -> str | None:
        """Get env_file path for an experiment."""
        exp = self.get(exp_id)
        return exp.get("env_file") if exp else None

    def config_path(self, exp_id: str) -> str | None:
        """Get config_path for an experiment."""
        exp = self.get(exp_id)
        return exp.get("config_path") if exp else None

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def transition(self, exp_id: str, new_status: str, reason: str = "") -> dict:
        """Transition experiment status.

        When using the default registry path, delegates to registry.transition_status
        (which handles its own save). When using a custom registry_path (e.g. in tests),
        performs the transition inline so writes go to the custom path only.
        """
        if self._registry_path is None:
            return transition_status(exp_id, new_status, reason=reason, registry=self._registry)

        # Custom path: perform transition inline to avoid writing to default REGISTRY_PATH.
        from datetime import datetime, timezone
        exp = self._registry.get("experiments", {}).get(exp_id)
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
        self._save()
        return exp

    def register(self, exp_data: dict) -> None:
        """Add a new experiment, validate, and save."""
        exp_id = exp_data.get("id")
        if not exp_id:
            raise ValueError("exp_data must have an 'id' field")
        if exp_id in self._experiments():
            raise ValueError(f"Experiment {exp_id} already exists")
        self._experiments()[exp_id] = exp_data
        errors = validate(self._registry)
        if errors:
            del self._experiments()[exp_id]
            raise ValueError(f"Validation failed: {errors}")
        self._save()

    def update_fields(self, exp_id: str, **fields: Any) -> dict:
        """Update specific fields on an experiment and save."""
        exp = self.get(exp_id)
        if exp is None:
            raise ValueError(f"Experiment {exp_id} not found")
        exp.update(fields)
        self._save()
        return exp

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save(self) -> None:
        if self._registry_path is not None:
            import json
            self._registry["last_updated"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
            with open(self._registry_path, "w", encoding="utf-8") as fh:
                json.dump(self._registry, fh, indent=4)
                fh.write("\n")
        else:
            save_registry(self._registry)


def get_manager() -> ExperimentManager:
    """Return (or create) the module-level singleton ExperimentManager."""
    global _manager
    if _manager is None:
        _manager = ExperimentManager()
    return _manager
