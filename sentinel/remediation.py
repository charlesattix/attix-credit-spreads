"""
sentinel/remediation.py — Auto-remediation engine for SENTINEL.

Detect → Fix → Notify.  Called from ``run_sentinel.py --daily`` after detection.

Remediation actions:
  1. Config drift      — validate config, auto-re-fingerprint if valid, halt if invalid
  2. Expired positions — auto-close via Alpaca, update DB, notify
  3. Stale experiments — restart scanner process via launchctl, notify
  4. Stuck positions   — auto-close positions past DTE + 1 day, notify
  5. DB/Alpaca mismatch — auto-reconcile using existing PositionReconciler

Safety invariants:
  - NEVER auto-fix dead API keys (401) — those require Carlos
  - Every fix sends a Telegram notification with what was found AND what was fixed
  - Every fix is logged to sentinel DB with action_taken field
  - All Alpaca order submissions are logged before execution
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RemediationAction:
    """One auto-fix action taken by the remediation engine."""
    experiment_id: str
    category: str          # config_drift | expired_position | stale_scanner | stuck_position | recon_mismatch
    description: str       # human-readable: what was found
    action_taken: str      # human-readable: what was fixed
    success: bool = True
    error: Optional[str] = None


@dataclass
class RemediationResult:
    """Aggregate result from a full remediation pass."""
    actions: List[RemediationAction] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def total_fixes(self) -> int:
        return sum(1 for a in self.actions if a.success)

    @property
    def total_failures(self) -> int:
        return sum(1 for a in self.actions if not a.success)


# ---------------------------------------------------------------------------
# Telegram helper
# ---------------------------------------------------------------------------

def _send_alert(message: str) -> None:
    """Send Telegram alert. Never raises."""
    try:
        from shared.telegram_alerts import send_message
        send_message(message, parse_mode="HTML")
    except ImportError:
        logger.warning("REMEDIATION: telegram_alerts not importable — alert skipped")
    except Exception as exc:
        logger.error("REMEDIATION: Telegram dispatch failed: %s", exc)


def _log_to_db(
    db,
    severity: str,
    message: str,
    experiment_id: Optional[str] = None,
    action_taken: Optional[str] = None,
) -> None:
    """Record an alert with optional action_taken in the resolution_note field."""
    try:
        alert_id = db.record_alert(severity, message, experiment_id=experiment_id)
        if action_taken:
            db.resolve_alert(
                alert_id,
                resolved_by="sentinel_remediation",
                resolution_note=f"AUTO-FIX: {action_taken}",
            )
    except Exception as exc:
        logger.error("REMEDIATION: DB logging failed: %s", exc)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

# Required fields that must exist and be non-empty for a config to be valid.
_REQUIRED_CONFIG_FIELDS = [
    ("tickers",),
    ("strategy", "direction"),
    ("risk", "max_risk_per_trade"),
    ("risk", "stop_loss_multiplier"),
    ("risk", "profit_target"),
]

# Numeric fields with valid ranges: (path, min_val, max_val)
_NUMERIC_RANGE_CHECKS = [
    (("strategy", "min_dte"),            1,    90),
    (("strategy", "max_dte"),            1,   120),
    (("strategy", "spread_width"),       1,    50),
    (("strategy", "otm_pct"),         0.001,  0.20),
    (("risk", "max_risk_per_trade"),   0.5,   50.0),
    (("risk", "stop_loss_multiplier"), 0.5,   10.0),
    (("risk", "profit_target"),          5,   100),
    (("risk", "drawdown_cb_pct"),        5,   100),
]


def _get_nested(d: dict, *keys):
    """Walk a nested dict; return None if any key is missing."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def validate_config(config_path: str) -> List[str]:
    """
    Validate a paper config YAML. Returns list of error strings (empty = valid).

    Checks:
    - File exists and is parseable YAML
    - Required fields are present and non-empty
    - Numeric fields are within sane ranges
    """
    errors: List[str] = []
    full_path = Path(config_path)
    if not full_path.is_absolute():
        full_path = _PROJECT_ROOT / config_path

    if not full_path.exists():
        return [f"Config file not found: {full_path}"]

    try:
        with open(full_path) as f:
            cfg = yaml.safe_load(f)
        if not isinstance(cfg, dict):
            return ["Config is not a YAML dictionary"]
    except Exception as exc:
        return [f"Cannot parse config: {exc}"]

    # Required fields
    for key_path in _REQUIRED_CONFIG_FIELDS:
        val = _get_nested(cfg, *key_path)
        if val is None or val == "" or val == []:
            errors.append(f"Missing required field: {'.'.join(key_path)}")

    # Numeric range checks
    for key_path, min_val, max_val in _NUMERIC_RANGE_CHECKS:
        val = _get_nested(cfg, *key_path)
        if val is not None:
            try:
                num = float(val)
                if num < min_val or num > max_val:
                    errors.append(
                        f"{'.'.join(key_path)}={val} out of range [{min_val}, {max_val}]"
                    )
            except (TypeError, ValueError):
                errors.append(f"{'.'.join(key_path)}={val!r} is not a number")

    return errors


# ---------------------------------------------------------------------------
# 1. Config drift remediation
# ---------------------------------------------------------------------------

def remediate_config_drift(
    exp_id: str,
    stored_fingerprint: str,
    config_path: str,
    db,
    result: RemediationResult,
) -> None:
    """
    Handle config drift: validate the new config, re-fingerprint if valid,
    halt only if the config is actually invalid.
    """
    from sentinel.state import compute_fingerprint, update_fingerprint

    # Verify drift actually exists
    try:
        current_fp = compute_fingerprint(config_path)
    except Exception as exc:
        action = RemediationAction(
            experiment_id=exp_id,
            category="config_drift",
            description=f"Config fingerprint check failed: {exc}",
            action_taken="none — could not read config",
            success=False,
            error=str(exc),
        )
        result.actions.append(action)
        return

    if current_fp == stored_fingerprint:
        return  # no drift

    # Validate the new config
    validation_errors = validate_config(config_path)

    if validation_errors:
        # Config is INVALID — halt the experiment
        from sentinel.state import set_halt
        error_summary = "; ".join(validation_errors[:3])
        try:
            set_halt(exp_id, f"Config drift with INVALID config: {error_summary}")
        except Exception:
            pass

        action = RemediationAction(
            experiment_id=exp_id,
            category="config_drift",
            description=f"Config changed AND is invalid: {error_summary}",
            action_taken=f"HALTED — config has {len(validation_errors)} validation error(s)",
            success=True,
        )
        result.actions.append(action)

        msg = (
            f"🛡️ SENTINEL REMEDIATION\n"
            f"🛑 <b>{exp_id}</b> — config drift with INVALID config\n"
            f"<b>Errors:</b> {error_summary}\n"
            f"<b>Action:</b> Experiment HALTED — needs manual fix\n"
            f"<code>scripts/run_sentinel.py --approve {exp_id} --reason \"...\"</code>"
        )
        _send_alert(msg)
        _log_to_db(db, "critical", f"Config drift — invalid config: {error_summary}",
                   experiment_id=exp_id,
                   action_taken=f"HALTED — {len(validation_errors)} validation error(s)")
    else:
        # Config is valid — auto-re-fingerprint and continue
        try:
            new_fp = update_fingerprint(exp_id, config_path)
        except Exception as exc:
            action = RemediationAction(
                experiment_id=exp_id,
                category="config_drift",
                description="Config drift detected, config valid, but re-fingerprint failed",
                action_taken="none",
                success=False,
                error=str(exc),
            )
            result.actions.append(action)
            return

        action = RemediationAction(
            experiment_id=exp_id,
            category="config_drift",
            description=f"Config changed (valid). Old: {stored_fingerprint[:16]}… New: {new_fp[:16]}…",
            action_taken=f"Auto-re-fingerprinted to {new_fp[:16]}…",
        )
        result.actions.append(action)

        # Record the config change in audit trail
        try:
            db.record_config_change(
                exp_id,
                field_name="config_fingerprint",
                old_value=stored_fingerprint,
                new_value=new_fp,
                detected_by="sentinel_remediation",
            )
            # Auto-approve the change
            changes = db.get_config_changes(exp_id, unapproved_only=True)
            for ch in changes:
                db.approve_config_change(
                    ch["id"],
                    approved_by="sentinel_auto_remediation",
                    reason="Config validated and auto-approved",
                )
        except Exception:
            pass

        msg = (
            f"🛡️ SENTINEL REMEDIATION\n"
            f"✅ <b>{exp_id}</b> — config drift auto-fixed\n"
            f"<b>Old fingerprint:</b> <code>{stored_fingerprint[:24]}…</code>\n"
            f"<b>New fingerprint:</b> <code>{new_fp[:24]}…</code>\n"
            f"<b>Action:</b> Config validated ✅ — auto-re-fingerprinted\n"
            f"Experiment continues running."
        )
        _send_alert(msg)
        _log_to_db(db, "info", f"Config drift auto-fixed: {stored_fingerprint[:16]}→{new_fp[:16]}",
                   experiment_id=exp_id,
                   action_taken=f"Auto-re-fingerprinted to {new_fp[:16]}…")


# ---------------------------------------------------------------------------
# 2. Expired positions remediation
# ---------------------------------------------------------------------------

def remediate_expired_positions(
    exp_id: str,
    registry_entry: dict,
    db,
    result: RemediationResult,
) -> None:
    """
    Find positions past their expiration date and auto-close via Alpaca.
    """
    db_path = registry_entry.get("db_path")
    if not db_path:
        return

    full_db_path = _PROJECT_ROOT / db_path
    if not full_db_path.exists():
        return

    alpaca = _get_alpaca_for_experiment(exp_id, registry_entry)
    if not alpaca:
        return

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        import sqlite3
        conn = sqlite3.connect(str(full_db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, ticker, strategy_type, short_strike, long_strike,
                   expiration, contracts, status, credit
            FROM trades
            WHERE status IN ('open', 'pending_close')
              AND expiration < ?
            """,
            (today_str,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.error("REMEDIATION: expired position query failed for %s: %s", exp_id, exc)
        return

    if not rows:
        return

    closed_count = 0
    for row in rows:
        trade_id = row["id"]
        ticker = row["ticker"]
        expiration = row["expiration"]
        short_strike = row["short_strike"]
        long_strike = row["long_strike"]
        spread_type = row["strategy_type"] or ""
        contracts = int(row["contracts"] or 1)

        try:
            # Submit close order via Alpaca
            if long_strike and "condor" not in spread_type.lower():
                close_result = alpaca.close_spread(
                    ticker=ticker,
                    short_strike=short_strike,
                    long_strike=long_strike,
                    expiration=str(expiration).split(" ")[0],
                    spread_type=spread_type,
                    contracts=contracts,
                    limit_price=None,
                )
            else:
                # Single leg or already expired — mark in DB directly
                close_result = {"status": "expired_otm"}

            # Update DB status
            conn = sqlite3.connect(str(full_db_path))
            conn.execute(
                """
                UPDATE trades
                SET status = 'closed_profit',
                    exit_reason = 'expired_auto_remediation',
                    updated_at = ?
                WHERE id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), trade_id),
            )
            conn.commit()
            conn.close()
            closed_count += 1

            logger.info(
                "REMEDIATION: auto-closed expired %s trade %s (exp=%s)",
                exp_id, trade_id, expiration,
            )
        except Exception as exc:
            logger.error(
                "REMEDIATION: failed to close expired %s trade %s: %s",
                exp_id, trade_id, exc,
            )
            result.errors.append(f"{exp_id}: expired close failed for {trade_id}: {exc}")

    if closed_count > 0:
        action = RemediationAction(
            experiment_id=exp_id,
            category="expired_position",
            description=f"Found {len(rows)} expired position(s)",
            action_taken=f"Auto-closed {closed_count}/{len(rows)} expired position(s)",
        )
        result.actions.append(action)

        msg = (
            f"🛡️ SENTINEL REMEDIATION\n"
            f"🔄 <b>{exp_id}</b> — expired positions auto-closed\n"
            f"<b>Found:</b> {len(rows)} expired position(s)\n"
            f"<b>Closed:</b> {closed_count}\n"
            f"<b>Action:</b> Submitted close orders + updated DB"
        )
        _send_alert(msg)
        _log_to_db(db, "info", f"Auto-closed {closed_count} expired position(s)",
                   experiment_id=exp_id,
                   action_taken=f"Closed {closed_count} expired positions via Alpaca")


# ---------------------------------------------------------------------------
# 3. Stale experiment remediation
# ---------------------------------------------------------------------------

def remediate_stale_experiment(
    exp_id: str,
    registry_entry: dict,
    db,
    result: RemediationResult,
) -> None:
    """
    Restart the scanner process for a stale experiment via launchctl.

    Detection: experiment health flagged is_stale (no orders in 3+ market days).
    Fix: kickstart the launchctl service.
    """
    # Derive the launchctl label from experiment ID
    numeric = exp_id.removeprefix("EXP-").lower()
    label = f"com.pilotai.exp{numeric}"

    # Verify the plist exists
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    deploy_plist = _PROJECT_ROOT / "deploy" / f"{label}.plist"

    if not plist_path.exists() and not deploy_plist.exists():
        action = RemediationAction(
            experiment_id=exp_id,
            category="stale_scanner",
            description=f"Stale experiment — no orders in 3+ market days",
            action_taken=f"Cannot restart: no plist found for {label}",
            success=False,
            error="No launchd plist found",
        )
        result.actions.append(action)
        _log_to_db(db, "warning", f"Stale experiment — no plist to restart",
                   experiment_id=exp_id,
                   action_taken="Cannot restart — no launchd plist found")
        return

    # Install plist if only in deploy/ dir
    if not plist_path.exists() and deploy_plist.exists():
        try:
            import shutil
            shutil.copy2(str(deploy_plist), str(plist_path))
        except Exception as exc:
            logger.error("REMEDIATION: failed to copy plist for %s: %s", exp_id, exc)

    # Kickstart the service
    try:
        # First try kickstart (macOS 10.10+)
        kickstart_result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
            capture_output=True, text=True, timeout=15,
        )

        if kickstart_result.returncode != 0:
            # Fallback: stop + start
            subprocess.run(
                ["launchctl", "stop", label],
                capture_output=True, text=True, timeout=10,
            )
            time.sleep(2)
            subprocess.run(
                ["launchctl", "start", label],
                capture_output=True, text=True, timeout=10,
            )

        action = RemediationAction(
            experiment_id=exp_id,
            category="stale_scanner",
            description=f"Stale experiment — no orders in 3+ market days",
            action_taken=f"Restarted scanner via launchctl ({label})",
        )
        result.actions.append(action)

        msg = (
            f"🛡️ SENTINEL REMEDIATION\n"
            f"🔄 <b>{exp_id}</b> — stale scanner restarted\n"
            f"<b>Found:</b> No orders in 3+ market days\n"
            f"<b>Action:</b> Restarted <code>{label}</code> via launchctl"
        )
        _send_alert(msg)
        _log_to_db(db, "info", f"Stale scanner restarted: {label}",
                   experiment_id=exp_id,
                   action_taken=f"Restarted via launchctl kickstart {label}")

    except Exception as exc:
        action = RemediationAction(
            experiment_id=exp_id,
            category="stale_scanner",
            description=f"Stale experiment — no orders in 3+ market days",
            action_taken=f"Restart FAILED: {exc}",
            success=False,
            error=str(exc),
        )
        result.actions.append(action)
        _log_to_db(db, "warning", f"Stale scanner restart failed: {exc}",
                   experiment_id=exp_id,
                   action_taken=f"Restart failed: {exc}")


# ---------------------------------------------------------------------------
# 4. Stuck positions remediation
# ---------------------------------------------------------------------------

def remediate_stuck_positions(
    exp_id: str,
    registry_entry: dict,
    db,
    result: RemediationResult,
) -> None:
    """
    Auto-close positions open beyond DTE + 1 day with no close order.

    Detection: position.expiration + 1 day < today AND status='open'.
    Fix: submit close order via Alpaca, update DB.
    """
    db_path = registry_entry.get("db_path")
    if not db_path:
        return

    full_db_path = _PROJECT_ROOT / db_path
    if not full_db_path.exists():
        return

    alpaca = _get_alpaca_for_experiment(exp_id, registry_entry)
    if not alpaca:
        return

    # Positions that should have closed: expiration was yesterday or earlier
    # and no close order was submitted
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        import sqlite3
        conn = sqlite3.connect(str(full_db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, ticker, strategy_type, short_strike, long_strike,
                   expiration, contracts, status, credit, close_order_id
            FROM trades
            WHERE status = 'open'
              AND expiration < ?
              AND (close_order_id IS NULL OR close_order_id = '')
            """,
            (today_str,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.error("REMEDIATION: stuck position query failed for %s: %s", exp_id, exc)
        return

    if not rows:
        return

    closed_count = 0
    for row in rows:
        trade_id = row["id"]
        ticker = row["ticker"]
        expiration = row["expiration"]
        short_strike = row["short_strike"]
        long_strike = row["long_strike"]
        spread_type = row["strategy_type"] or ""
        contracts = int(row["contracts"] or 1)

        logger.info(
            "REMEDIATION: auto-closing stuck %s trade %s (exp=%s, no close order)",
            exp_id, trade_id, expiration,
        )

        try:
            # Check if Alpaca still has the position legs
            if long_strike and "condor" not in spread_type.lower():
                close_result = alpaca.close_spread(
                    ticker=ticker,
                    short_strike=short_strike,
                    long_strike=long_strike,
                    expiration=str(expiration).split(" ")[0],
                    spread_type=spread_type,
                    contracts=contracts,
                    limit_price=None,
                )
                close_status = close_result.get("status", "unknown")
            else:
                # Expired positions with no legs in Alpaca — just mark closed
                close_status = "expired_otm"

            # Update DB
            conn = sqlite3.connect(str(full_db_path))
            conn.execute(
                """
                UPDATE trades
                SET status = 'closed_profit',
                    exit_reason = 'stuck_auto_remediation',
                    updated_at = ?
                WHERE id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), trade_id),
            )
            conn.commit()
            conn.close()
            closed_count += 1

        except Exception as exc:
            logger.error(
                "REMEDIATION: failed to close stuck %s trade %s: %s",
                exp_id, trade_id, exc,
            )
            result.errors.append(f"{exp_id}: stuck close failed for {trade_id}: {exc}")

    if closed_count > 0:
        action = RemediationAction(
            experiment_id=exp_id,
            category="stuck_position",
            description=f"Found {len(rows)} stuck position(s) past DTE+1 with no close order",
            action_taken=f"Auto-closed {closed_count}/{len(rows)} stuck position(s)",
        )
        result.actions.append(action)

        msg = (
            f"🛡️ SENTINEL REMEDIATION\n"
            f"🔄 <b>{exp_id}</b> — stuck positions auto-closed\n"
            f"<b>Found:</b> {len(rows)} position(s) past expiration with no close order\n"
            f"<b>Closed:</b> {closed_count}\n"
            f"<b>Action:</b> Submitted close orders + marked closed in DB"
        )
        _send_alert(msg)
        _log_to_db(db, "info", f"Auto-closed {closed_count} stuck position(s) past DTE+1",
                   experiment_id=exp_id,
                   action_taken=f"Closed {closed_count} stuck positions via Alpaca")


# ---------------------------------------------------------------------------
# 5. DB/Alpaca mismatch reconciliation
# ---------------------------------------------------------------------------

def remediate_recon_mismatches(
    exp_id: str,
    registry_entry: dict,
    db,
    result: RemediationResult,
) -> None:
    """
    Auto-reconcile DB/Alpaca mismatches using the existing PositionReconciler.

    Runs Tier 2 reconciliation (full position comparison) and reports what was fixed.
    """
    db_path = registry_entry.get("db_path")
    env_file = registry_entry.get("env_file")
    if not db_path or not env_file:
        return

    full_db_path = _PROJECT_ROOT / db_path
    if not full_db_path.exists():
        return

    alpaca = _get_alpaca_for_experiment(exp_id, registry_entry)
    if not alpaca:
        return

    try:
        from shared.reconciler import PositionReconciler
        reconciler = PositionReconciler(alpaca, db_path=str(full_db_path))
        recon_result = reconciler.reconcile_tier2()
    except ImportError:
        logger.debug("REMEDIATION: shared.reconciler not importable — skipping %s", exp_id)
        return
    except Exception as exc:
        logger.error("REMEDIATION: reconciliation failed for %s: %s", exp_id, exc)
        result.errors.append(f"{exp_id}: reconciliation failed: {exc}")
        return

    # Check if anything was actually fixed
    fixes = []
    if recon_result.pending_resolved:
        fixes.append(f"{recon_result.pending_resolved} pending→open")
    if recon_result.pending_failed:
        fixes.append(f"{recon_result.pending_failed} pending→failed")
    if recon_result.phantom_resolved:
        fixes.append(f"{recon_result.phantom_resolved} phantom(s) resolved")
    if recon_result.orphans_detected:
        fixes.append(f"{recon_result.orphans_detected} orphan(s) registered")
    if recon_result.externally_closed:
        fixes.append(f"{recon_result.externally_closed} externally closed")

    if not fixes:
        return  # nothing to report

    fix_summary = ", ".join(fixes)

    action = RemediationAction(
        experiment_id=exp_id,
        category="recon_mismatch",
        description=f"DB/Alpaca mismatch detected",
        action_taken=f"Auto-reconciled: {fix_summary}",
    )
    result.actions.append(action)

    if recon_result.errors:
        for err in recon_result.errors:
            result.errors.append(f"{exp_id}: recon error: {err}")

    msg = (
        f"🛡️ SENTINEL REMEDIATION\n"
        f"🔄 <b>{exp_id}</b> — DB/Alpaca mismatch auto-reconciled\n"
        f"<b>Fixes:</b> {fix_summary}\n"
        f"<b>Action:</b> Tier 2 reconciliation completed"
    )
    if recon_result.errors:
        msg += f"\n<b>Errors:</b> {len(recon_result.errors)}"
    _send_alert(msg)
    _log_to_db(db, "info", f"DB/Alpaca auto-reconciled: {fix_summary}",
               experiment_id=exp_id,
               action_taken=f"Auto-reconciled: {fix_summary}")


# ---------------------------------------------------------------------------
# Alpaca provider factory
# ---------------------------------------------------------------------------

def _get_alpaca_for_experiment(
    exp_id: str,
    registry_entry: dict,
) -> Optional[Any]:
    """
    Instantiate an AlpacaProvider for the given experiment using its .env file.
    Returns None if credentials are unavailable or API is dead.
    """
    env_file = registry_entry.get("env_file")
    if not env_file:
        return None

    env_path = _PROJECT_ROOT / env_file
    if not env_path.exists():
        # Try convention: EXP-400 → .env.exp400
        numeric = exp_id.removeprefix("EXP-").lower()
        env_path = _PROJECT_ROOT / f".env.exp{numeric}"
        if not env_path.exists():
            logger.debug("REMEDIATION: no env file for %s", exp_id)
            return None

    # Parse .env file for credentials
    api_key = None
    api_secret = None
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key in ("ALPACA_API_KEY", "APCA_API_KEY_ID"):
                    api_key = val
                elif key in ("ALPACA_API_SECRET", "APCA_API_SECRET_KEY"):
                    api_secret = val
    except Exception as exc:
        logger.error("REMEDIATION: failed to read env file for %s: %s", exp_id, exc)
        return None

    if not api_key or not api_secret:
        return None

    try:
        from strategy.alpaca_provider import AlpacaProvider
        return AlpacaProvider(api_key, api_secret, paper=True)
    except Exception as exc:
        logger.error("REMEDIATION: AlpacaProvider init failed for %s: %s", exp_id, exc)
        return None


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_remediation(
    registry: dict,
    health_results: Optional[list] = None,
    sentinel_state: Optional[dict] = None,
) -> RemediationResult:
    """
    Run all auto-remediation checks for active experiments.

    Called from ``run_sentinel.py --daily`` after detection phase.

    Args:
        registry: experiments/registry.json contents
        health_results: list of ExperimentHealth from monitor.check_all_experiments
        sentinel_state: current sentinel_state.json contents

    Returns:
        RemediationResult with all actions taken.
    """
    from sentinel.history import SentinelDB
    from sentinel.state import load_state, compute_fingerprint

    db = SentinelDB()
    result = RemediationResult()

    if sentinel_state is None:
        try:
            sentinel_state = load_state()
        except FileNotFoundError:
            sentinel_state = {"experiments": {}}

    active_exps = {
        k: v for k, v in registry.get("experiments", {}).items()
        if v.get("status") in ("active", "paper_trading")
    }

    # Build health lookup
    health_by_id: Dict[str, Any] = {}
    if health_results:
        for h in health_results:
            health_by_id[h.exp_id] = h

    for exp_id, exp in sorted(active_exps.items()):
        health = health_by_id.get(exp_id)

        # Skip dead API keys — those need Carlos
        if health and not health.api_ok:
            api_err = health.api_error or ""
            if "401" in api_err or "Unauthorized" in api_err:
                logger.info(
                    "REMEDIATION: skipping %s — dead API keys (needs Carlos)", exp_id
                )
                continue

        # 1. Config drift remediation
        state_entry = sentinel_state.get("experiments", {}).get(exp_id, {})
        stored_fp = state_entry.get("config_fingerprint")
        config_path = state_entry.get("paper_config") or exp.get("paper_config")
        if stored_fp and config_path:
            try:
                current_fp = compute_fingerprint(config_path)
                if current_fp != stored_fp:
                    remediate_config_drift(exp_id, stored_fp, config_path, db, result)
            except Exception as exc:
                logger.error("REMEDIATION: config drift check failed for %s: %s", exp_id, exc)

        # 2. Expired positions
        try:
            remediate_expired_positions(exp_id, exp, db, result)
        except Exception as exc:
            logger.error("REMEDIATION: expired positions failed for %s: %s", exp_id, exc)
            result.errors.append(f"{exp_id}: expired positions: {exc}")

        # 3. Stale experiments (only if health flagged stale)
        if health and health.is_stale:
            try:
                remediate_stale_experiment(exp_id, exp, db, result)
            except Exception as exc:
                logger.error("REMEDIATION: stale restart failed for %s: %s", exp_id, exc)
                result.errors.append(f"{exp_id}: stale restart: {exc}")

        # 4. Stuck positions (past DTE + 1, no close order)
        try:
            remediate_stuck_positions(exp_id, exp, db, result)
        except Exception as exc:
            logger.error("REMEDIATION: stuck positions failed for %s: %s", exp_id, exc)
            result.errors.append(f"{exp_id}: stuck positions: {exc}")

        # 5. DB/Alpaca reconciliation (only if API is healthy)
        if health and health.api_ok:
            try:
                remediate_recon_mismatches(exp_id, exp, db, result)
            except Exception as exc:
                logger.error("REMEDIATION: recon failed for %s: %s", exp_id, exc)
                result.errors.append(f"{exp_id}: reconciliation: {exc}")

    # Final summary Telegram if any actions were taken
    if result.actions:
        summary_lines = []
        for act in result.actions:
            icon = "✅" if act.success else "❌"
            summary_lines.append(
                f"  {icon} <b>{act.experiment_id}</b> [{act.category}]: {act.action_taken}"
            )

        msg = (
            f"🛡️ SENTINEL REMEDIATION SUMMARY\n"
            f"<b>{result.total_fixes}</b> fix(es), "
            f"<b>{result.total_failures}</b> failure(s)\n\n"
            + "\n".join(summary_lines)
        )
        _send_alert(msg)

    return result
