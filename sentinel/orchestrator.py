"""
sentinel/orchestrator.py — THE BRAIN.

Central orchestrator that ties all gates together. Runs all gates in order
for every experiment, aggregates results into a health score, and records
every gate run in sentinel.db.

Gate results:
  PASS     — gate passed, no issues
  WARNING  — non-blocking issue, investigate soon
  BLOCK    — prevent this scan, log reason, alert
  HALT     — halt experiment, prevent all future scans until manual review
  CRITICAL — halt + immediate Telegram alert to Carlos

Health score: 0-100 per experiment
  Each gate contributes to the score. PASS=full points, WARNING=half,
  BLOCK/HALT/CRITICAL=0.

Gate 21 (CONFIG PARITY) is integrated here — detects drift between
backtest JSON and paper YAML configs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Gate result model
# ---------------------------------------------------------------------------

class GateResult(IntEnum):
    PASS = 0
    WARNING = 1
    BLOCK = 2
    HALT = 3
    CRITICAL = 4


RESULT_LABEL = {
    GateResult.PASS: "PASS",
    GateResult.WARNING: "WARNING",
    GateResult.BLOCK: "BLOCK",
    GateResult.HALT: "HALT",
    GateResult.CRITICAL: "CRITICAL",
}


@dataclass
class GateOutcome:
    """Result of running a single gate on a single experiment."""
    gate_id: str
    gate_name: str
    result: GateResult
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "gate_id": self.gate_id,
            "gate_name": self.gate_name,
            "result": RESULT_LABEL.get(self.result, "UNKNOWN"),
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class ExperimentAudit:
    """Full audit result for one experiment across all gates."""
    experiment_id: str
    gate_outcomes: List[GateOutcome] = field(default_factory=list)
    health_score: int = 100
    halted: bool = False
    halt_reason: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def worst_result(self) -> GateResult:
        if not self.gate_outcomes:
            return GateResult.PASS
        return max(o.result for o in self.gate_outcomes)

    @property
    def warnings(self) -> List[GateOutcome]:
        return [o for o in self.gate_outcomes if o.result == GateResult.WARNING]

    @property
    def failures(self) -> List[GateOutcome]:
        return [o for o in self.gate_outcomes if o.result >= GateResult.BLOCK]

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "health_score": self.health_score,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "worst_result": RESULT_LABEL.get(self.worst_result, "PASS"),
            "gate_count": len(self.gate_outcomes),
            "pass_count": sum(1 for o in self.gate_outcomes if o.result == GateResult.PASS),
            "warning_count": len(self.warnings),
            "failure_count": len(self.failures),
            "gates": [o.to_dict() for o in self.gate_outcomes],
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Gate 21: CONFIG PARITY — Backtest vs Paper drift detection
# ---------------------------------------------------------------------------

# Critical parameters: if these differ between backtest and paper, the
# backtest results are INVALID.
# bt_paths: list of paths to try in order (supports both champion.json nested
# format and run_optimization.py flat format).
_CRITICAL_PARITY_FIELDS = {
    "spread_width": {
        "bt_paths": [("strategy_params", "credit_spread", "spread_width"), ("spread_width",)],
        "paper_paths": [("strategy", "spread_width")],
        "tolerance": 0,  # exact match required
    },
    "max_risk_per_trade": {
        "bt_paths": [("strategy_params", "credit_spread", "max_risk_pct"), ("max_risk_per_trade",)],
        "paper_paths": [("risk", "max_risk_per_trade")],
        "tolerance": 0,
        "bt_scale_if_small": True,  # auto-detect: <1 means fraction, multiply by 100
    },
    "target_dte": {
        "bt_paths": [("strategy_params", "credit_spread", "target_dte"), ("target_dte",)],
        "paper_paths": [("strategy", "target_dte")],
        "tolerance": 0,
    },
    "otm_pct": {
        "bt_paths": [("strategy_params", "credit_spread", "otm_pct"), ("otm_pct",)],
        "paper_paths": [("strategy", "otm_pct")],
        "tolerance": 0,
    },
    "stop_loss_multiplier": {
        "bt_paths": [("strategy_params", "credit_spread", "stop_loss_multiplier"), ("stop_loss_multiplier",)],
        "paper_paths": [("risk", "stop_loss_multiplier")],
        "tolerance": 0,
    },
    "profit_target": {
        "bt_paths": [("strategy_params", "credit_spread", "profit_target_pct"), ("profit_target",)],
        "paper_paths": [("risk", "profit_target")],
        "bt_scale_if_small": True,  # auto-detect: <1 means fraction, multiply by 100
        "tolerance": 0,
    },
    "direction": {
        "bt_paths": [("strategy_params", "credit_spread", "direction"), ("direction",)],
        "paper_paths": [("strategy", "direction")],
        "tolerance": 0,
        "aliases": {"regime_adaptive": "both"},  # equivalent values
    },
}

# Non-critical but monitored parameters
_MONITORED_PARITY_FIELDS = {
    "min_dte": {
        "bt_paths": [("strategy_params", "credit_spread", "min_dte"), ("min_dte",)],
        "paper_paths": [("strategy", "min_dte")],
        "tolerance_pct": 10,
    },
    "drawdown_cb_pct": {
        "bt_paths": [("drawdown_cb_pct",)],
        "paper_paths": [("risk", "drawdown_cb_pct")],
        "tolerance_pct": 10,
    },
    "max_contracts": {
        "bt_paths": [("max_contracts",)],
        "paper_paths": [("risk", "max_contracts")],
        "tolerance_pct": 20,
    },
    "ic_spread_width": {
        "bt_paths": [("strategy_params", "iron_condor", "spread_width")],
        "paper_paths": [("strategy", "iron_condor", "spread_width")],
        "tolerance_pct": 10,
    },
    "ic_max_risk": {
        "bt_paths": [("strategy_params", "iron_condor", "max_risk_pct")],
        "paper_paths": [("strategy", "iron_condor", "max_risk_pct")],
        "bt_scale_if_small": True,
        "tolerance_pct": 10,
    },
}


def _get_nested(d: dict, path: tuple) -> Any:
    """Walk a nested dict by key tuple. Returns None if any key missing."""
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _values_match(bt_val: Any, paper_val: Any, spec: dict) -> bool:
    """Compare two values considering scaling and aliases."""
    if bt_val is None and paper_val is None:
        return True  # Both missing — no comparison possible
    if bt_val is None or paper_val is None:
        return False  # One side has a value, the other doesn't — drift

    # Apply scaling: auto-detect if bt value is a fraction (<1) while paper is percentage
    if spec.get("bt_scale_if_small") and isinstance(bt_val, (int, float)) and isinstance(paper_val, (int, float)):
        if abs(bt_val) < 1 and abs(paper_val) >= 1:
            bt_val = bt_val * 100
    scale = spec.get("bt_scale")
    if scale and isinstance(bt_val, (int, float)):
        bt_val = bt_val * scale

    # Check aliases
    aliases = spec.get("aliases", {})
    bt_str = str(bt_val).lower()
    paper_str = str(paper_val).lower()
    if bt_str in aliases and aliases[bt_str] == paper_str:
        return True
    if paper_str in aliases and aliases[paper_str] == bt_str:
        return True

    # Numeric tolerance
    tol_pct = spec.get("tolerance_pct")
    tol = spec.get("tolerance", None)
    if isinstance(bt_val, (int, float)) and isinstance(paper_val, (int, float)):
        if tol is not None and tol == 0:
            return abs(bt_val - paper_val) < 0.01  # floating point tolerance
        if tol_pct is not None:
            ref = max(abs(bt_val), abs(paper_val), 0.01)
            return abs(bt_val - paper_val) / ref * 100 <= tol_pct

    # String comparison
    return bt_str == paper_str


@dataclass
class ParityDiff:
    """A single parameter difference between backtest and paper configs."""
    field_name: str
    bt_value: Any
    paper_value: Any
    critical: bool
    message: str


def check_config_parity(
    bt_config: dict,
    paper_config: dict,
) -> Tuple[List[ParityDiff], GateResult]:
    """
    Compare backtest JSON config against paper YAML config.

    Returns (list of diffs, worst gate result).
    """
    diffs: List[ParityDiff] = []

    def _resolve_bt_val(spec: dict) -> Any:
        """Try each bt_path in order, return first non-None value."""
        for bp in spec.get("bt_paths", []):
            val = _get_nested(bt_config, bp)
            if val is not None:
                return val
        return None

    def _display_val(val: Any, spec: dict) -> Any:
        """Scale value for human-readable display."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            if spec.get("bt_scale_if_small") and abs(val) < 1:
                return val * 100
            if spec.get("bt_scale"):
                return val * spec["bt_scale"]
        return val

    # Check critical fields
    for name, spec in _CRITICAL_PARITY_FIELDS.items():
        bt_val = _resolve_bt_val(spec)
        paper_val = None
        for pp in spec["paper_paths"]:
            paper_val = _get_nested(paper_config, pp)
            if paper_val is not None:
                break

        if bt_val is None:
            continue  # Field not in backtest config, skip

        if not _values_match(bt_val, paper_val, spec):
            display_bt = _display_val(bt_val, spec)
            diffs.append(ParityDiff(
                field_name=name,
                bt_value=display_bt,
                paper_value=paper_val,
                critical=True,
                message=f"CRITICAL PARITY: {name} — backtest={display_bt}, paper={paper_val}",
            ))

    # Check monitored fields
    for name, spec in _MONITORED_PARITY_FIELDS.items():
        bt_val = _resolve_bt_val(spec)
        paper_val = None
        for pp in spec["paper_paths"]:
            paper_val = _get_nested(paper_config, pp)
            if paper_val is not None:
                break

        if bt_val is None:
            continue

        if not _values_match(bt_val, paper_val, spec):
            display_bt = _display_val(bt_val, spec)
            diffs.append(ParityDiff(
                field_name=name,
                bt_value=display_bt,
                paper_value=paper_val,
                critical=False,
                message=f"PARITY DRIFT: {name} — backtest={display_bt}, paper={paper_val}",
            ))

    # Determine worst result
    if any(d.critical for d in diffs):
        worst = GateResult.CRITICAL
    elif diffs:
        worst = GateResult.WARNING
    else:
        worst = GateResult.PASS

    return diffs, worst


# ---------------------------------------------------------------------------
# Gate runner infrastructure
# ---------------------------------------------------------------------------


def _run_gate21_config_parity(
    exp_id: str,
    registry: dict,
) -> GateOutcome:
    """Gate 21: Compare backtest and paper config parity."""
    reg_exp = registry.get("experiments", {}).get(exp_id, {})
    bt_path = reg_exp.get("backtest_config")
    paper_path = reg_exp.get("paper_config")

    if not bt_path:
        return GateOutcome(
            gate_id="G21",
            gate_name="Config Parity",
            result=GateResult.WARNING,
            message="No backtest config registered — parity check skipped",
        )

    bt_file = _PROJECT_ROOT / bt_path
    paper_file = _PROJECT_ROOT / paper_path if paper_path else None

    if not bt_file.exists():
        return GateOutcome(
            gate_id="G21",
            gate_name="Config Parity",
            result=GateResult.WARNING,
            message=f"Backtest config not found: {bt_path}",
        )

    if not paper_file or not paper_file.exists():
        return GateOutcome(
            gate_id="G21",
            gate_name="Config Parity",
            result=GateResult.WARNING,
            message=f"Paper config not found: {paper_path}",
        )

    try:
        with open(bt_file) as f:
            bt_config = json.load(f)
    except Exception as e:
        return GateOutcome(
            gate_id="G21",
            gate_name="Config Parity",
            result=GateResult.BLOCK,
            message=f"Failed to load backtest config: {e}",
        )

    try:
        import yaml
        with open(paper_file) as f:
            paper_config = yaml.safe_load(f)
    except Exception as e:
        return GateOutcome(
            gate_id="G21",
            gate_name="Config Parity",
            result=GateResult.BLOCK,
            message=f"Failed to load paper config: {e}",
        )

    diffs, worst = check_config_parity(bt_config, paper_config)

    if not diffs:
        return GateOutcome(
            gate_id="G21",
            gate_name="Config Parity",
            result=GateResult.PASS,
            message="Backtest and paper configs match on all checked parameters",
        )

    critical_diffs = [d for d in diffs if d.critical]
    warn_diffs = [d for d in diffs if not d.critical]
    msg_parts = []
    if critical_diffs:
        msg_parts.append(f"{len(critical_diffs)} CRITICAL parity violations")
    if warn_diffs:
        msg_parts.append(f"{len(warn_diffs)} monitored drifts")

    return GateOutcome(
        gate_id="G21",
        gate_name="Config Parity",
        result=worst,
        message=" + ".join(msg_parts),
        details={
            "diffs": [
                {
                    "field": d.field_name,
                    "backtest": d.bt_value,
                    "paper": d.paper_value,
                    "critical": d.critical,
                }
                for d in diffs
            ],
        },
    )


def _run_gate_monitor(
    exp_id: str,
    registry: dict,
) -> GateOutcome:
    """Wrap sentinel.monitor health checks as a gate."""
    try:
        from sentinel.monitor import check_experiment, _find_env_file
        reg_exp = registry.get("experiments", {}).get(exp_id, {})
        account_id = reg_exp.get("account_id")
        status = reg_exp.get("status", "unknown")
        env_file = _find_env_file(_PROJECT_ROOT, exp_id) if account_id else None

        health = check_experiment(exp_id, account_id, status, env_file)

        if health.is_ghost:
            return GateOutcome("G3", "Alpaca Health", GateResult.HALT,
                               f"Ghost: API unreachable — {health.api_error}")
        if health.is_orphan:
            return GateOutcome("G3", "Alpaca Health", GateResult.CRITICAL,
                               f"Orphan: retired account holds ${health.equity:,.0f}")
        if health.is_duplicate:
            return GateOutcome("G3", "Alpaca Health", GateResult.CRITICAL,
                               f"Duplicate account {health.account_id}")
        if health.is_stale:
            age = health.last_order_age_days or "?"
            return GateOutcome("G3", "Alpaca Health", GateResult.WARNING,
                               f"Stale: no trades in {age} market days")
        if not health.api_ok and health.api_error:
            return GateOutcome("G3", "Alpaca Health", GateResult.WARNING,
                               f"API error: {health.api_error}")

        return GateOutcome("G3", "Alpaca Health", GateResult.PASS,
                           f"OK — equity ${health.equity:,.0f}" if health.equity else "OK")
    except Exception as e:
        return GateOutcome("G3", "Alpaca Health", GateResult.BLOCK,
                           f"Monitor check failed: {e}")


def _run_gate_fingerprint(
    exp_id: str,
    state: dict,
) -> GateOutcome:
    """Gate 2: Config fingerprint drift check."""
    try:
        from sentinel.state import check_fingerprint
        exp_state = state.get("experiments", {}).get(exp_id, {})
        if not exp_state.get("config_fingerprint"):
            return GateOutcome("G2", "Config Fingerprint", GateResult.PASS,
                               "No fingerprint enrolled — skipped")
        if check_fingerprint(state, exp_id):
            return GateOutcome("G2", "Config Fingerprint", GateResult.PASS,
                               "Fingerprint matches")
        return GateOutcome("G2", "Config Fingerprint", GateResult.HALT,
                           "Config fingerprint mismatch — file changed since certification")
    except Exception as e:
        return GateOutcome("G2", "Config Fingerprint", GateResult.BLOCK,
                           f"Fingerprint check error: {e}")


def _run_gate_registry(
    exp_id: str,
    registry: dict,
) -> GateOutcome:
    """Gate 0: Registry status check."""
    reg_exp = registry.get("experiments", {}).get(exp_id, {})
    status = reg_exp.get("status", "unknown")
    if status in ("active", "paper_trading"):
        return GateOutcome("G0", "Registry Status", GateResult.PASS,
                           f"Status: {status}")
    if status == "paused":
        return GateOutcome("G0", "Registry Status", GateResult.WARNING,
                           "Experiment paused — dry run only")
    return GateOutcome("G0", "Registry Status", GateResult.BLOCK,
                       f"Registry status '{status}' blocks scanning")


def _run_gate_sentinel_state(
    exp_id: str,
    state: dict,
) -> GateOutcome:
    """Gate 1: Sentinel state status check."""
    exp_state = state.get("experiments", {}).get(exp_id, {})
    status = exp_state.get("status", "active")
    if status == "active":
        return GateOutcome("G1", "Sentinel State", GateResult.PASS,
                           "Experiment active in sentinel state")
    if status == "halted":
        reason = exp_state.get("halt_reason", "no reason given")
        return GateOutcome("G1", "Sentinel State", GateResult.HALT,
                           f"Halted: {reason}")
    if status == "paused":
        return GateOutcome("G1", "Sentinel State", GateResult.WARNING,
                           "Paused — dry run only")
    return GateOutcome("G1", "Sentinel State", GateResult.BLOCK,
                       f"Sentinel status '{status}' blocks scanning")


def _run_gate_certification(
    exp_id: str,
    state: dict,
) -> GateOutcome:
    """Gate 5: Deployment certification check."""
    exp_state = state.get("experiments", {}).get(exp_id, {})
    cert_time = exp_state.get("sentinel_certified_at")
    if cert_time:
        return GateOutcome("G5", "Certification", GateResult.PASS,
                           f"Certified at {cert_time}")
    return GateOutcome("G5", "Certification", GateResult.WARNING,
                       "Not yet SENTINEL-certified")


def _run_gate_lifecycle(
    exp_id: str,
) -> GateOutcome:
    """Gate 9: Position lifecycle check."""
    try:
        from sentinel.runtime import check_position_lifecycle
        result = check_position_lifecycle(exp_id)
        if result is None:
            return GateOutcome("G9", "Position Lifecycle", GateResult.PASS,
                               "No DB found — skipped")
        if result.has_critical:
            stuck_msgs = [s.message for s in result.stuck if s.severity == "critical"]
            return GateOutcome("G9", "Position Lifecycle", GateResult.CRITICAL,
                               f"{len(stuck_msgs)} critical stuck positions",
                               details={"stuck": [s.__dict__ for s in result.stuck]})
        if not result.passed:
            return GateOutcome("G9", "Position Lifecycle", GateResult.WARNING,
                               f"{len(result.stuck)} positions need attention")
        return GateOutcome("G9", "Position Lifecycle", GateResult.PASS,
                           f"{result.total_open} open positions, none stuck")
    except Exception as e:
        return GateOutcome("G9", "Position Lifecycle", GateResult.BLOCK,
                           f"Lifecycle check error: {e}")


def _run_gate_drift(
    exp_id: str,
) -> GateOutcome:
    """Gate 8: Runtime drift check."""
    try:
        from sentinel.runtime import check_runtime_drift
        alerts = check_runtime_drift(exp_id)
        if alerts is None:
            return GateOutcome("G8", "Runtime Drift", GateResult.PASS,
                               "No data available — skipped")
        if not alerts:
            return GateOutcome("G8", "Runtime Drift", GateResult.PASS,
                               "No drift detected")

        worst_sev = max(a.severity for a in alerts)
        if worst_sev == "halt":
            result = GateResult.HALT
        elif worst_sev == "critical":
            result = GateResult.CRITICAL
        else:
            result = GateResult.WARNING

        return GateOutcome("G8", "Runtime Drift", result,
                           f"{len(alerts)} drift alerts",
                           details={"alerts": [
                               {"metric": a.metric, "severity": a.severity,
                                "message": a.message}
                               for a in alerts
                           ]})
    except Exception as e:
        return GateOutcome("G8", "Runtime Drift", GateResult.BLOCK,
                           f"Drift check error: {e}")


# ---------------------------------------------------------------------------
# Health score calculation
# ---------------------------------------------------------------------------

# Gate weights for health score (higher = more important)
_GATE_WEIGHTS = {
    "G0": 10, "G1": 10, "G2": 15, "G3": 15,
    "G5": 5, "G8": 15, "G9": 15, "G21": 15,
}

_RESULT_SCORE = {
    GateResult.PASS: 1.0,
    GateResult.WARNING: 0.5,
    GateResult.BLOCK: 0.0,
    GateResult.HALT: 0.0,
    GateResult.CRITICAL: 0.0,
}


def _compute_health_score(outcomes: List[GateOutcome]) -> int:
    """Compute 0-100 health score from gate outcomes."""
    if not outcomes:
        return 100

    total_weight = 0
    weighted_score = 0.0

    for o in outcomes:
        w = _GATE_WEIGHTS.get(o.gate_id, 10)
        total_weight += w
        weighted_score += w * _RESULT_SCORE.get(o.result, 0.0)

    if total_weight == 0:
        return 100

    return round(weighted_score / total_weight * 100)


# ---------------------------------------------------------------------------
# Recording gate runs to sentinel.db
# ---------------------------------------------------------------------------


def _record_gate_runs(
    exp_id: str,
    outcomes: List[GateOutcome],
    health_score: int,
) -> None:
    """Store gate run results in sentinel.db."""
    try:
        from sentinel.history import SentinelDB
        db = SentinelDB()

        # Record as a snapshot note with all gate results
        notes = json.dumps({
            "health_score": health_score,
            "gates": [o.to_dict() for o in outcomes],
        })
        db.record_snapshot(
            exp_id,
            notes=f"orchestrator_audit: score={health_score}",
        )

        # Record individual alerts for failures
        for o in outcomes:
            if o.result >= GateResult.BLOCK:
                severity = "critical" if o.result >= GateResult.CRITICAL else "warning"
                db.record_alert(
                    severity,
                    f"[{o.gate_id} {o.gate_name}] {o.message}",
                    experiment_id=exp_id,
                )
    except Exception as e:
        logger.error("Failed to record gate runs to DB for %s: %s", exp_id, e)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def audit_experiment(
    exp_id: str,
    registry: dict,
    state: dict,
    *,
    skip_gates: Optional[List[str]] = None,
) -> ExperimentAudit:
    """
    Run ALL gates for a single experiment and return the audit result.

    Gate execution order: G0 → G1 → G2 → G3 → G5 → G8 → G9 → G21
    Any BLOCK stops further gate execution for this experiment.
    """
    skip = set(skip_gates or [])
    audit = ExperimentAudit(experiment_id=exp_id)
    gate_runners = [
        ("G0", lambda: _run_gate_registry(exp_id, registry)),
        ("G1", lambda: _run_gate_sentinel_state(exp_id, state)),
        ("G2", lambda: _run_gate_fingerprint(exp_id, state)),
        ("G3", lambda: _run_gate_monitor(exp_id, registry)),
        ("G5", lambda: _run_gate_certification(exp_id, state)),
        ("G8", lambda: _run_gate_drift(exp_id)),
        ("G9", lambda: _run_gate_lifecycle(exp_id)),
        ("G21", lambda: _run_gate21_config_parity(exp_id, registry)),
    ]

    for gate_id, runner in gate_runners:
        if gate_id in skip:
            continue

        try:
            outcome = runner()
        except Exception as e:
            # Fail-closed: unknown exception → BLOCK (G5 is advisory, override below)
            error_result = GateResult.WARNING if gate_id == "G5" else GateResult.BLOCK
            outcome = GateOutcome(
                gate_id=gate_id,
                gate_name="Error",
                result=error_result,
                message=f"Gate {gate_id} raised exception: {e}",
            )

        audit.gate_outcomes.append(outcome)

        # BLOCK stops further gates
        if outcome.result == GateResult.BLOCK:
            audit.halted = False  # blocked, not halted
            break

        # HALT/CRITICAL: mark experiment for halt
        if outcome.result >= GateResult.HALT:
            audit.halted = True
            audit.halt_reason = f"[{outcome.gate_id}] {outcome.message}"

    # Compute health score
    audit.health_score = _compute_health_score(audit.gate_outcomes)

    # Record to DB
    _record_gate_runs(exp_id, audit.gate_outcomes, audit.health_score)

    return audit


def audit_all_experiments(
    *,
    skip_gates: Optional[List[str]] = None,
    halt_on_critical: bool = True,
) -> List[ExperimentAudit]:
    """
    Run all gates for every active experiment in the registry.

    If halt_on_critical is True, experiments with HALT/CRITICAL results
    will be halted in sentinel_state.json.

    Returns list of ExperimentAudit results.
    """
    try:
        registry = _load_registry()
        state = _load_state()
    except Exception as e:
        logger.error("Failed to load registry/state: %s", e)
        error_audit = ExperimentAudit(experiment_id="SYSTEM")
        error_audit.gate_outcomes.append(GateOutcome(
            gate_id="G0",
            gate_name="Registry/State Load",
            result=GateResult.CRITICAL,
            message=f"Cannot load registry or state: {e}",
        ))
        error_audit.health_score = 0
        error_audit.halted = True
        error_audit.halt_reason = f"Registry/state load failure: {e}"
        return [error_audit]

    active_ids = [
        k for k, v in registry.get("experiments", {}).items()
        if v.get("status") in ("active", "paper_trading")
    ]

    results: List[ExperimentAudit] = []
    alerts_to_send: List = []

    for exp_id in sorted(active_ids):
        logger.info("Auditing %s...", exp_id)
        audit = audit_experiment(exp_id, registry, state, skip_gates=skip_gates)
        results.append(audit)

        # Halt if needed
        if audit.halted and halt_on_critical:
            try:
                from sentinel.state import set_halt
                set_halt(exp_id, audit.halt_reason or "orchestrator gate failure")
                logger.critical("HALTED %s: %s", exp_id, audit.halt_reason)
            except Exception as e:
                logger.error("Failed to halt %s: %s", exp_id, e)

        # Collect alerts for critical/halt outcomes
        for o in audit.gate_outcomes:
            if o.result >= GateResult.CRITICAL:
                try:
                    from sentinel.alerting import critical_alert, halt_alert, send_alert, record_alert_to_db
                    if o.result == GateResult.HALT:
                        alert = halt_alert(o.gate_id, exp_id, o.message)
                    else:
                        alert = critical_alert(o.gate_id, exp_id, o.message)
                    send_alert(alert, force=True)
                    record_alert_to_db(alert)
                except Exception as e:
                    logger.error("Failed to send alert for %s %s: %s", exp_id, o.gate_id, e)

        logger.info(
            "  %s: score=%d worst=%s gates=%d",
            exp_id, audit.health_score,
            RESULT_LABEL.get(audit.worst_result, "?"),
            len(audit.gate_outcomes),
        )

    return results


def format_audit_report(audits: List[ExperimentAudit]) -> str:
    """Format audit results as an HTML Telegram message."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total = len(audits)
    healthy = sum(1 for a in audits if a.health_score >= 80)
    halted = sum(1 for a in audits if a.halted)
    avg_score = round(sum(a.health_score for a in audits) / total) if total else 0

    if halted:
        header = f"\U0001f6a8 <b>SENTINEL AUDIT — {halted} HALTED</b>"
    elif avg_score < 70:
        header = f"\u26a0\ufe0f <b>SENTINEL AUDIT — Avg Score {avg_score}</b>"
    else:
        header = f"\u2705 <b>SENTINEL AUDIT — All Clear</b>"

    lines = [header, f"<i>{now_str}</i>", ""]

    for a in sorted(audits, key=lambda x: x.health_score):
        icon = "\U0001f534" if a.health_score < 50 else ("\u26a0\ufe0f" if a.health_score < 80 else "\U0001f7e2")
        status = "HALTED" if a.halted else f"Score {a.health_score}"
        lines.append(f"  {icon} <b>{a.experiment_id}</b>: {status}")
        for o in a.failures:
            lines.append(f"      \U0001f534 [{o.gate_id}] {o.message}")
        for o in a.warnings:
            lines.append(f"      \u26a0\ufe0f [{o.gate_id}] {o.message}")

    lines.append("")
    lines.append(f"<i>{healthy}/{total} healthy · Avg score {avg_score}</i>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_registry() -> dict:
    registry_path = _PROJECT_ROOT / "experiments" / "registry.json"
    with open(registry_path) as f:
        return json.load(f)


def _load_state() -> dict:
    state_path = _PROJECT_ROOT / "sentinel_state.json"
    if state_path.exists():
        with open(state_path) as f:
            return json.load(f)
    return {}
