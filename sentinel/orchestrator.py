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

from sentinel.gates_data_quality import (
    check_data_freshness,
    audit_signal_votes,
    check_regime_parity,
)
from sentinel.gates_account import check_account_gates
from sentinel.gates_execution import check_execution_gates

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
# G10-G12: Data quality gate runners
# ---------------------------------------------------------------------------


def _run_gate10_data_freshness(exp_id: str) -> GateOutcome:
    """Gate 10: Data freshness — BLOCK if critical data sources are stale."""
    try:
        result = check_data_freshness(exp_id)
        if result.blocked:
            return GateOutcome(
                "G10", "Data Freshness", GateResult.BLOCK,
                "; ".join(result.errors) or "Data freshness check failed",
                details={"warnings": result.warnings,
                         "vix_age_hours": result.vix_age_hours,
                         "vix3m_present": result.vix3m_present},
            )
        if result.warnings:
            return GateOutcome(
                "G10", "Data Freshness", GateResult.WARNING,
                "; ".join(result.warnings),
                details={"vix_age_hours": result.vix_age_hours,
                         "vix3m_present": result.vix3m_present},
            )
        return GateOutcome("G10", "Data Freshness", GateResult.PASS,
                           "All data sources fresh")
    except Exception as e:
        return GateOutcome("G10", "Data Freshness", GateResult.BLOCK,
                           f"Data freshness check error: {e}")


def _run_gate11_signal_votes(exp_id: str) -> GateOutcome:
    """Gate 11: Signal voting audit — requires runtime context, deferred."""
    return GateOutcome(
        "G11", "Signal Voting Audit", GateResult.PASS,
        "Deferred — fires in live scanner path with runtime vote context",
    )


def _run_gate12_regime_parity(exp_id: str) -> GateOutcome:
    """Gate 12: Regime parity — requires runtime context, deferred."""
    return GateOutcome(
        "G12", "Regime Parity", GateResult.PASS,
        "Deferred — fires in live scanner path with runtime regime context",
    )


# ---------------------------------------------------------------------------
# G13-G16: Account gate runners
# ---------------------------------------------------------------------------


def _run_gates_account(exp_id: str) -> List[GateOutcome]:
    """Gates 13-16: Account health, expired positions, concentration, orphans."""
    outcomes: List[GateOutcome] = []
    try:
        results = check_account_gates(exp_id)

        g13 = results.get("gate13")
        if g13 is not None and hasattr(g13, "severity"):
            if g13.severity in ("halt", "flatten"):
                outcomes.append(GateOutcome("G13", "Account Health",
                                            GateResult.HALT,
                                            getattr(g13, "message", "DD exceeded")))
            elif g13.severity == "warning":
                outcomes.append(GateOutcome("G13", "Account Health",
                                            GateResult.WARNING,
                                            getattr(g13, "message", "Warning")))
            else:
                outcomes.append(GateOutcome("G13", "Account Health",
                                            GateResult.PASS, "OK"))
        else:
            outcomes.append(GateOutcome("G13", "Account Health",
                                        GateResult.WARNING,
                                        "Skipped — no Alpaca account data"))

        g14 = results.get("gate14")
        if g14 is not None and hasattr(g14, "has_expired"):
            if g14.has_expired:
                outcomes.append(GateOutcome("G14", "Expired Positions",
                                            GateResult.WARNING,
                                            f"{len(g14.expired)} expired"))
            else:
                outcomes.append(GateOutcome("G14", "Expired Positions",
                                            GateResult.PASS, "OK"))

        g15 = results.get("gate15")
        if g15 is not None and hasattr(g15, "block_new_entries"):
            if g15.block_new_entries:
                outcomes.append(GateOutcome("G15", "Concentration",
                                            GateResult.BLOCK,
                                            "Concentration limit exceeded"))
            else:
                outcomes.append(GateOutcome("G15", "Concentration",
                                            GateResult.PASS, "OK"))

        g16 = results.get("gate16")
        if g16 is not None and hasattr(g16, "severity"):
            if g16.severity == "halt":
                outcomes.append(GateOutcome("G16", "Orphan Detection",
                                            GateResult.HALT,
                                            f"{len(g16.true_orphans)} orphans"))
            elif g16.severity == "warning":
                outcomes.append(GateOutcome("G16", "Orphan Detection",
                                            GateResult.WARNING,
                                            getattr(g16, "message", "Warning")))
            else:
                outcomes.append(GateOutcome("G16", "Orphan Detection",
                                            GateResult.PASS, "OK"))

        if results.get("halted") and not any(
            o.result >= GateResult.HALT for o in outcomes
        ):
            outcomes.append(GateOutcome("G13", "Account Health",
                                        GateResult.HALT,
                                        "Account gates triggered halt"))
    except Exception as e:
        outcomes.append(GateOutcome("G13", "Account Gates",
                                    GateResult.WARNING,
                                    f"Account gates error: {e}"))

    return outcomes or [GateOutcome("G13", "Account Gates",
                                    GateResult.PASS, "No account data")]


# ---------------------------------------------------------------------------
# G17-G20: Execution gate runners
# ---------------------------------------------------------------------------


def _run_gates_execution(exp_id: str) -> List[GateOutcome]:
    """Gates 17-20: Stop-loss quality, failures, calendar, P&L recon."""
    outcomes: List[GateOutcome] = []
    try:
        db_path = str(
            _PROJECT_ROOT / "data"
            / exp_id.lower().replace("-", "")
            / f"pilotai_{exp_id.lower().replace('-', '')}.db"
        )
        result = check_execution_gates(exp_id, db_path)

        if result.gate17 is not None:
            if not result.gate17.passed:
                sev = result.gate17.worst_severity or "warning"
                lvl = GateResult.HALT if sev == "halt" else GateResult.WARNING
                outcomes.append(GateOutcome("G17", "Stop-Loss Quality", lvl,
                                            f"{len(result.gate17.events)} slippage events"))
            else:
                outcomes.append(GateOutcome("G17", "Stop-Loss Quality",
                                            GateResult.PASS, "OK"))

        if result.gate18 is not None:
            if not result.gate18.passed:
                sev = result.gate18.worst_severity or "warning"
                lvl = GateResult.HALT if sev == "halt" else GateResult.WARNING
                outcomes.append(GateOutcome("G18", "Repeated Failures", lvl,
                                            f"Loss streak: {result.gate18.current_loss_streak}"))
            else:
                outcomes.append(GateOutcome("G18", "Repeated Failures",
                                            GateResult.PASS, "OK"))

        if result.gate19 is not None:
            if not result.gate19.passed:
                outcomes.append(GateOutcome("G19", "Market Calendar",
                                            GateResult.WARNING,
                                            f"{len(result.gate19.events)} off-hours events"))
            else:
                outcomes.append(GateOutcome("G19", "Market Calendar",
                                            GateResult.PASS, "OK"))

        if result.gate20 is not None:
            if not result.gate20.passed:
                sev = result.gate20.worst_severity or "warning"
                lvl = GateResult.HALT if sev == "halt" else GateResult.WARNING
                outcomes.append(GateOutcome("G20", "P&L Reconciliation", lvl,
                                            f"{len(result.gate20.discrepancies)} discrepancies"))
            else:
                outcomes.append(GateOutcome("G20", "P&L Reconciliation",
                                            GateResult.PASS, "OK"))
    except Exception as e:
        outcomes.append(GateOutcome("G17", "Execution Gates",
                                    GateResult.WARNING,
                                    f"Execution gates error: {e}"))

    return outcomes or [GateOutcome("G17", "Execution Gates",
                                    GateResult.PASS, "No execution data")]


# ---------------------------------------------------------------------------
# Health score calculation
# ---------------------------------------------------------------------------

# Gate weights for health score (higher = more important)
_GATE_WEIGHTS = {
    "G0": 10, "G1": 10, "G2": 15, "G3": 15,
    "G5": 5, "G8": 15, "G9": 15, "G21": 15,
    "G10": 10, "G11": 5, "G12": 10,
    "G13": 10, "G14": 5, "G15": 5, "G16": 5,
    "G17": 5, "G18": 5, "G19": 3, "G20": 5,
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

    Gate execution order (data quality first, then pre-scan, then account,
    then execution, then advisory gates):
        G10 → G11 → G12 → G0 → G1 → G2 → G3 → G13-16 → G17-20 → G5 → G8 → G9 → G21

    Any BLOCK stops further gate execution for this experiment.
    """
    skip = set(skip_gates or [])
    audit = ExperimentAudit(experiment_id=exp_id)

    # --- Helper to process a single outcome ---
    blocked = False

    def _process(outcome: GateOutcome) -> bool:
        """Append outcome; return True if BLOCK (stop chain)."""
        audit.gate_outcomes.append(outcome)
        if outcome.result == GateResult.BLOCK:
            audit.halted = False
            return True
        if outcome.result >= GateResult.HALT:
            audit.halted = True
            audit.halt_reason = f"[{outcome.gate_id}] {outcome.message}"
        return False

    def _run_single(gate_id: str, runner) -> bool:
        """Run a single-outcome gate. Returns True if chain should stop."""
        if gate_id in skip:
            return False
        try:
            outcome = runner()
        except Exception as e:
            lvl = GateResult.WARNING if gate_id == "G5" else GateResult.BLOCK
            outcome = GateOutcome(gate_id, "Error", lvl,
                                  f"Gate {gate_id} raised exception: {e}")
        return _process(outcome)

    # Phase 1: Data quality gates (fail-closed, run first)
    for gid, runner in [
        ("G10", lambda: _run_gate10_data_freshness(exp_id)),
        ("G11", lambda: _run_gate11_signal_votes(exp_id)),
        ("G12", lambda: _run_gate12_regime_parity(exp_id)),
    ]:
        if _run_single(gid, runner):
            blocked = True
            break

    # Phase 2: Pre-scan gates
    if not blocked:
        for gid, runner in [
            ("G0", lambda: _run_gate_registry(exp_id, registry)),
            ("G1", lambda: _run_gate_sentinel_state(exp_id, state)),
            ("G2", lambda: _run_gate_fingerprint(exp_id, state)),
            ("G3", lambda: _run_gate_monitor(exp_id, registry)),
        ]:
            if _run_single(gid, runner):
                blocked = True
                break

    # Phase 3: Account gates (G13-G16, multi-outcome)
    if not blocked:
        try:
            for outcome in _run_gates_account(exp_id):
                if outcome.gate_id in skip:
                    continue
                if _process(outcome):
                    blocked = True
                    break
        except Exception as e:
            _process(GateOutcome("G13", "Account Gates", GateResult.WARNING,
                                 f"Account gates error: {e}"))

    # Phase 4: Execution gates (G17-G20, multi-outcome)
    if not blocked:
        try:
            for outcome in _run_gates_execution(exp_id):
                if outcome.gate_id in skip:
                    continue
                if _process(outcome):
                    blocked = True
                    break
        except Exception as e:
            _process(GateOutcome("G17", "Execution Gates", GateResult.WARNING,
                                 f"Execution gates error: {e}"))

    # Phase 5: Advisory and drift gates
    if not blocked:
        for gid, runner in [
            ("G5", lambda: _run_gate_certification(exp_id, state)),
            ("G8", lambda: _run_gate_drift(exp_id)),
            ("G9", lambda: _run_gate_lifecycle(exp_id)),
            ("G21", lambda: _run_gate21_config_parity(exp_id, registry)),
        ]:
            if _run_single(gid, runner):
                break

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
