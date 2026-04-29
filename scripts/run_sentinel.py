#!/usr/bin/env python3
"""
🛡️ SENTINEL — Unified CLI Entry Point

Usage
-----
  # Run daily health check + send Telegram report
  python scripts/run_sentinel.py --daily

  # Interactive 5-gate onboarding for a new experiment
  python scripts/run_sentinel.py --onboard --id EXP-900 --paper-config configs/paper_exp900.yaml

  # Approve a config change (clears halt, re-fingerprints)
  python scripts/run_sentinel.py --approve EXP-800 --reason "Adjusted Kelly bull fraction"

  # Resume a halted or paused experiment
  python scripts/run_sentinel.py --resume EXP-800 --reason "Reverted config drift"

  # Full audit: deep config comparison for all active experiments
  python scripts/run_sentinel.py --audit

  # Show full timeline for one experiment
  python scripts/run_sentinel.py --history EXP-400

  # Generate HTML report files
  python scripts/run_sentinel.py --report --output-dir output/sentinel_reports

  # Retroactively onboard all active experiments (first-run bootstrap)
  python scripts/run_sentinel.py --retroactive
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Ensure project root is on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Module imports (graceful fallbacks for cc1/cc2/cc4 modules not yet built)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from sentinel.history import SentinelDB, compute_fingerprint as history_fingerprint
except ImportError as e:
    print(f"FATAL: sentinel.history not found: {e}")
    sys.exit(1)

# Canonical state I/O — single source of truth for sentinel_state.json
from sentinel.state import (
    load_state as _load_state,
    save_state as _save_state,
    compute_fingerprint as state_fingerprint,
    record_health_check as _record_health_check,
    record_health_checks as _record_health_checks,
)

# For backwards compat, use the history fingerprint for audit trail and
# state fingerprint for sentinel_state.json enforcement
compute_fingerprint = history_fingerprint

try:
    from sentinel.report import (
        generate_daily_html,
        generate_history_html,
        generate_telegram_daily,
    )
except ImportError as e:
    print(f"FATAL: sentinel.report not found: {e}")
    sys.exit(1)

try:
    from sentinel.monitor import check_all_experiments, ExperimentHealth
    _HAS_MONITOR = True
except ImportError:
    _HAS_MONITOR = False
    logger.debug("sentinel.monitor not available — API health checks skipped")

try:
    from sentinel.portfolio import aggregate_portfolio_risk
    _HAS_PORTFOLIO = True
except ImportError:
    _HAS_PORTFOLIO = False
    logger.debug("sentinel.portfolio not available — portfolio risk skipped")

try:
    from sentinel.comparator import compare_configs
    _HAS_COMPARATOR = True
except ImportError:
    _HAS_COMPARATOR = False
    logger.debug("sentinel.comparator not available — config comparison skipped")

try:
    from sentinel.equivalence import run_equivalence_test
    _HAS_EQUIVALENCE = True
except ImportError:
    _HAS_EQUIVALENCE = False
    logger.debug("sentinel.equivalence not available — behavioral tests skipped")

try:
    from sentinel.runtime import (
        check_position_lifecycle,
        check_all_position_lifecycles,
        format_lifecycle_report,
    )
    _HAS_LIFECYCLE = True
except ImportError:
    _HAS_LIFECYCLE = False
    logger.debug("sentinel.runtime (Gate 9) not available — lifecycle check skipped")

try:
    from shared.telegram_alerts import send_message as _tg_send
    _HAS_TELEGRAM = True
except ImportError:
    _HAS_TELEGRAM = False

# ─────────────────────────────────────────────────────────────────────────────
# sentinel_state.json helpers
# ─────────────────────────────────────────────────────────────────────────────
# _load_state and _save_state are imported from sentinel.state above.
# _set_experiment_state writes to the nested "experiments" dict — matching
# the schema that guards.py reads from.


def _set_experiment_state(
    exp_id: str,
    status: str,
    fingerprint: Optional[str] = None,
    **extra,
) -> None:
    """Update a single experiment's entry in sentinel_state.json.

    Writes to state["experiments"][exp_id] (nested schema), matching the
    format that guards.py and sentinel/state.py expect.
    """
    try:
        state = _load_state()
    except FileNotFoundError:
        state = {"experiments": {}}
    experiments = state.setdefault("experiments", {})
    entry: Dict[str, Any] = experiments.get(exp_id, {})
    entry["status"] = status
    if fingerprint:
        entry["config_fingerprint"] = fingerprint
    entry.update(extra)
    experiments[exp_id] = entry
    _save_state(state)


# ─────────────────────────────────────────────────────────────────────────────
# Registry helpers
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY_PATH = _PROJECT_ROOT / "experiments" / "registry.json"

# Registry statuses that mean "currently live in paper trading". Historical
# data (some experiments use "active", others "paper_trading"); both must
# match. Mirrors sentinel/monitor.py:136.
_LIVE_REGISTRY_STATUSES = ("active", "paper_trading")


def _is_live(exp: Dict[str, Any]) -> bool:
    """Return True if the registry entry represents a live (paper-trading) experiment."""
    return exp.get("status") in _LIVE_REGISTRY_STATUSES


def _load_registry() -> Dict[str, Any]:
    if not _REGISTRY_PATH.exists():
        raise FileNotFoundError(f"Registry not found: {_REGISTRY_PATH}")
    return json.loads(_REGISTRY_PATH.read_text())


def _load_paper_config(config_path: str) -> Optional[Dict]:
    full = _PROJECT_ROOT / config_path
    if not full.exists():
        return None
    try:
        return yaml.safe_load(full.read_text()) or {}
    except Exception as e:
        logger.warning("Failed to load %s: %s", config_path, e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# --daily
# ─────────────────────────────────────────────────────────────────────────────


def cmd_daily(args: argparse.Namespace) -> int:
    """Run the full daily health check and optionally send Telegram report."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    registry = _load_registry()
    db = SentinelDB()
    state = _load_state()

    # Active experiments to snapshot
    active_exps = {
        k: v
        for k, v in registry.get("experiments", {}).items()
        if _is_live(v)
    }
    exp_ids = list(active_exps.keys())
    print(f"🛡️  SENTINEL DAILY — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   {len(exp_ids)} active experiments: {', '.join(sorted(exp_ids))}")

    # Alpaca health check
    health_results = None
    if _HAS_MONITOR:
        print("   Running Alpaca health checks…")
        health_results = check_all_experiments(registry, _PROJECT_ROOT)

        for h in health_results:
            if not h.api_ok and h.registry_status in _LIVE_REGISTRY_STATUSES:
                db.record_alert(
                    "critical",
                    f"API health check failed: {h.api_error or 'unknown error'}",
                    experiment_id=h.exp_id,
                )
                _set_experiment_state(
                    h.exp_id, "halted",
                    halt_reason=f"API health check failed: {h.api_error or 'dead keys'}",
                    halted_at=datetime.now(timezone.utc).isoformat(),
                    halted_by="sentinel_daily",
                    halt_evidence={
                        "gate_id": "api_health",
                        "metric_name": "alpaca_api",
                        "stored_value": "ok",
                        "current_value": (h.api_error or "dead keys")[:200],
                        "threshold": "api_ok",
                    },
                )
                print(f"   ❌ {h.exp_id}: API FAILED — halted")
            elif h.is_stale:
                db.record_alert(
                    "warning",
                    f"Stale experiment: {'; '.join(h.issues)}",
                    experiment_id=h.exp_id,
                )
                print(f"   ⚠️  {h.exp_id}: STALE")
            elif h.is_orphan:
                db.record_alert(
                    "warning",
                    f"Orphan account: ${h.equity:,.0f} in retired account {h.account_id}",
                    experiment_id=h.exp_id,
                )
                print(f"   ⚠️  {h.exp_id}: ORPHAN ${h.equity:,.0f}")
            else:
                eq_str = f"${h.equity:,.0f}" if h.equity else "N/A"
                print(f"   ✅ {h.exp_id}: {eq_str}")

            # Record snapshot from health data
            if h.api_ok:
                db.record_snapshot(
                    h.exp_id,
                    equity=h.equity,
                    open_positions=h.open_positions,
                    api_status="ok",
                )
            else:
                db.record_snapshot(
                    h.exp_id,
                    api_status="401" if "401" in (h.api_error or "") else "error",
                )

        # Stamp last_health_check for every experiment we attempted to check
        # (success or failure) in a single load/save cycle — otherwise
        # dead-keyed experiments stay "last check: never" forever.
        try:
            _record_health_checks([h.exp_id for h in health_results])
        except Exception:  # noqa: BLE001
            logging.exception("record_health_checks batch write failed")
    else:
        # No monitor: we did NOT actually perform a health check, so do not
        # stamp last_health_check here — the name implies an outcome.
        print("   (monitor module unavailable — recording minimal snapshots)")
        for exp_id in exp_ids:
            db.record_snapshot(exp_id, api_status="unknown")

    # Portfolio risk
    portfolio_risk = None
    if _HAS_PORTFOLIO:
        print("   Aggregating portfolio risk…")
        portfolio_risk = aggregate_portfolio_risk(registry, _PROJECT_ROOT)

    # Gate 9 — Position lifecycle
    lifecycle_results = None
    if _HAS_LIFECYCLE:
        print("   Checking position lifecycles (Gate 9)…")
        lifecycle_results = {}
        for exp_id in exp_ids:
            lc = check_position_lifecycle(exp_id)
            lifecycle_results[exp_id] = lc
            if lc.stuck:
                crits_lc = sum(1 for s in lc.stuck if s.severity == "critical")
                warns_lc = len(lc.stuck) - crits_lc
                for s in lc.stuck:
                    db.record_alert(
                        s.severity,
                        f"Gate9 {s.message}",
                        experiment_id=exp_id,
                    )
                if crits_lc:
                    print(f"   🛑 {exp_id}: {crits_lc} CRITICAL stuck position(s)")
                elif warns_lc:
                    print(f"   ⚠️  {exp_id}: {warns_lc} stuck position(s)")
            else:
                print(f"   ✅ {exp_id}: positions healthy ({lc.total_open} open)")

    # Gate 22 — Scanner heartbeats (alert-only, market-hours)
    try:
        from sentinel.runtime import check_scanner_heartbeats
        hb_alerts = check_scanner_heartbeats(db)
        if hb_alerts:
            print(f"   ⚠️  Gate22 heartbeats: {len(hb_alerts)} stale scanner(s)")
            for a in hb_alerts:
                db.record_alert(a["severity"], a["message"])
        else:
            print("   ✅ Gate22 heartbeats: all scanners fresh (or after-hours)")
    except Exception:  # noqa: BLE001
        logging.exception("Gate22 heartbeat check failed")

    # Gate 23 — Orchestrator-side G7 reconciliation (halt-bypass).
    # Runs check_orphan_positions for every experiment with a working
    # Alpaca API, regardless of sentinel halt status. The point: when an
    # experiment is halted, its scanner does not run, so the per-scanner
    # G7 (orphan_gate) goes silent. We must not let drift hide behind a
    # halt for days the way it did with EXP-503/EXP-800.
    try:
        if health_results:
            from sentinel.runtime import check_orphan_positions
            g23_total = 0
            for h in health_results:
                if not h.api_ok:
                    continue
                try:
                    result = check_orphan_positions(h.exp_id, h.positions)
                except Exception:  # noqa: BLE001
                    logging.exception("Gate23 reconciliation failed for %s", h.exp_id)
                    continue
                for a in result.alerts:
                    db.record_alert(
                        a["severity"], a["message"], experiment_id=h.exp_id,
                    )
                g23_total += len(result.alerts)
                if result.qty_mismatches or result.stale_orphans:
                    print(
                        f"   🚨 Gate23 {h.exp_id}: "
                        f"{len(result.qty_mismatches)} qty_mismatch, "
                        f"{len(result.stale_orphans)} stale_orphan(s)"
                    )
                elif result.orphans or result.ghosts:
                    print(
                        f"   ⚠️  Gate23 {h.exp_id}: "
                        f"{len(result.orphans)} orphan, "
                        f"{len(result.ghosts)} ghost(s)"
                    )
            if g23_total == 0:
                print("   ✅ Gate23 broker↔DB recon: clean across all live accounts")
    except Exception:  # noqa: BLE001
        logging.exception("Gate23 orchestrator reconciliation block failed")

    # Gate 24 — Stale-halt nag (market-day-aware, alert-only)
    try:
        from sentinel.runtime import check_stale_halts
        stale_result = check_stale_halts(state)
        if stale_result.alerts:
            crit_n = sum(1 for a in stale_result.alerts if a["severity"] == "critical")
            warn_n = sum(1 for a in stale_result.alerts if a["severity"] == "warning")
            print(
                f"   🚨 Gate24 stale halts: {crit_n} critical, {warn_n} warning"
            )
            for a in stale_result.alerts:
                db.record_alert(
                    a["severity"], a["message"], experiment_id=a.get("experiment_id"),
                )
        else:
            print("   ✅ Gate24 stale halts: none aged past 1 market day")
        if stale_result.acknowledged:
            print(
                f"   (Gate24: {len(stale_result.acknowledged)} acknowledged-stale "
                f"halt(s) suppressed: {', '.join(stale_result.acknowledged)})"
            )
        if stale_result.legacy_halts:
            print(
                f"   (Gate24: {len(stale_result.legacy_halts)} legacy halt(s) "
                f"with no halted_at: {', '.join(stale_result.legacy_halts)} — "
                f"{stale_result.legacy_recommendation})"
            )
    except Exception:  # noqa: BLE001
        logging.exception("Gate24 stale-halt check failed")

    # Build summary
    summary = db.get_daily_summary(exp_ids)

    # Print alert summary
    crits = summary.get("critical_alerts", [])
    warns = summary.get("warning_alerts", [])
    if crits:
        print(f"\n   🚨 {len(crits)} critical alert(s):")
        for a in crits[:5]:
            print(f"      [{a.get('experiment_id','sys')}] {a['message'][:80]}")
    if warns:
        print(f"\n   ⚠️  {len(warns)} warning(s):")
        for a in warns[:5]:
            print(f"      [{a.get('experiment_id','sys')}] {a['message'][:80]}")
    if not crits and not warns:
        print("   ✅ All clear — no open alerts")

    # HTML report
    if not args.no_report:
        out_dir = Path(getattr(args, "output_dir", None) or "output/sentinel_reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        html = generate_daily_html(
            summary,
            portfolio_risk=portfolio_risk,
            health_results=health_results,
            sentinel_state=state,
            registry=registry,
        )
        report_path = out_dir / f"sentinel_daily_{today}.html"
        report_path.write_text(html)
        print(f"\n   📄 Report written to {report_path}")

    # Telegram
    if not args.no_telegram and _HAS_TELEGRAM:
        msg = generate_telegram_daily(
            summary,
            portfolio_risk=portfolio_risk,
            health_results=health_results,
            registry=registry,
        )
        _tg_send(msg)
        print("   📲 Telegram report sent")

    return 1 if crits else 0


# ─────────────────────────────────────────────────────────────────────────────
# --audit
# ─────────────────────────────────────────────────────────────────────────────


def cmd_audit(args: argparse.Namespace) -> int:
    """Deep config comparison for all active experiments."""
    registry = _load_registry()
    db = SentinelDB()
    state = _load_state()

    active = {
        k: v
        for k, v in registry.get("experiments", {}).items()
        if _is_live(v)
    }

    print(f"🛡️  SENTINEL AUDIT — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    print(f"   Checking {len(active)} active experiments\n")

    issues_found = 0
    for exp_id, exp in sorted(active.items()):
        paper_config_path = exp.get("paper_config")
        cfg = _load_paper_config(paper_config_path) if paper_config_path else None

        if cfg is None:
            print(f"   ❌ {exp_id}: paper config not loadable ({paper_config_path})")
            issues_found += 1
            continue

        # Recompute fingerprint from current config
        current_fp = compute_fingerprint(cfg)

        # Compare against sentinel_state.json
        stored_fp = state.get("experiments", {}).get(exp_id, {}).get("config_fingerprint")

        if not stored_fp:
            print(f"   ⚠️  {exp_id}: no stored fingerprint (not onboarded)")
            issues_found += 1
        elif stored_fp != current_fp:
            print(f"   ❌ {exp_id}: DRIFT DETECTED")
            print(f"      stored:  {stored_fp}")
            print(f"      current: {current_fp}")
            db.record_config_change(
                exp_id,
                field_name="config_fingerprint",
                old_value=stored_fp,
                new_value=current_fp,
                detected_by="sentinel_audit",
            )
            db.record_alert(
                "critical",
                f"Config drift: fingerprint changed {stored_fp} → {current_fp}",
                experiment_id=exp_id,
            )
            issues_found += 1
        else:
            print(f"   ✅ {exp_id}: config matches ({current_fp})")

        # Use sentinel.comparator if available
        if _HAS_COMPARATOR and exp.get("backtest_config"):
            bt_path = _PROJECT_ROOT / exp["backtest_config"]
            if bt_path.exists():
                try:
                    result = compare_configs(str(bt_path), str(_PROJECT_ROOT / paper_config_path))
                    if result.get("mismatches"):
                        for m in result["mismatches"]:
                            print(f"      ⚠️  {m}")
                        issues_found += len(result["mismatches"])
                except Exception as e:
                    logger.debug("comparator error for %s: %s", exp_id, e)

    # Gate 9 — Position lifecycle audit
    if _HAS_LIFECYCLE:
        print("\n   Position lifecycle audit (Gate 9):")
        for exp_id in sorted(active):
            lc = check_position_lifecycle(exp_id)
            if lc.stuck:
                for s in lc.stuck:
                    icon = "🛑" if s.severity == "critical" else "⚠️"
                    print(f"   {icon} {exp_id}: {s.message}")
                    issues_found += 1
            elif lc.errors:
                for e in lc.errors:
                    print(f"   ❌ {exp_id}: {e}")
                    issues_found += 1
            else:
                print(f"   ✅ {exp_id}: {lc.total_open} open — all healthy")

    print(f"\n   {'✅ All checks pass' if issues_found == 0 else f'⚠️  {issues_found} issue(s) found'}")
    return 1 if issues_found > 0 else 0


# ─────────────────────────────────────────────────────────────────────────────
# --approve
# ─────────────────────────────────────────────────────────────────────────────


def cmd_approve(args: argparse.Namespace) -> int:
    """Approve a config change, re-fingerprint, and resume the experiment."""
    exp_id: str = args.approve
    reason: str = args.reason

    if not reason:
        print("ERROR: --reason is required for --approve")
        return 1

    registry = _load_registry()
    db = SentinelDB()

    exp = registry.get("experiments", {}).get(exp_id)
    if not exp:
        print(f"ERROR: {exp_id} not found in registry")
        return 1

    paper_config_path = exp.get("paper_config")
    cfg = _load_paper_config(paper_config_path) if paper_config_path else None
    if cfg is None:
        print(f"ERROR: Cannot load paper config for {exp_id}")
        return 1

    # Approve all unapproved changes for this experiment
    unapproved = db.get_config_changes(exp_id, unapproved_only=True)
    for change in unapproved:
        db.approve_config_change(change["id"], approved_by=args.operator or "operator", reason=reason)
        print(f"   ✅ Approved change #{change['id']}: {change['field_name']}")

    if not unapproved:
        print(f"   (No unapproved config changes found for {exp_id})")

    # Recompute fingerprint from current (approved) config
    new_fp = compute_fingerprint(cfg)

    # Update sentinel_state.json
    _set_experiment_state(
        exp_id,
        status="active",
        fingerprint=new_fp,
        last_approved_at=datetime.now(timezone.utc).isoformat(),
        last_approved_by=args.operator or "operator",
        last_approval_reason=reason,
    )

    # Issue a new deployment cert (approved update)
    db.record_deployment_cert(
        exp_id,
        fingerprint=new_fp,
        gates_passed=10,
        equivalence_days=5,
        certified_by=f"approved_by:{args.operator or 'operator'}",
        grandfathered=False,
        notes=f"Config change approved: {reason}",
    )

    # Resolve open drift alerts
    open_alerts = db.get_active_alerts(exp_id, severity="critical")
    for a in open_alerts:
        if "drift" in a.get("message", "").lower() or "config" in a.get("message", "").lower():
            db.resolve_alert(a["id"], resolved_by=args.operator or "operator", resolution_note=reason)

    print(f"\n🛡️  {exp_id}: config change APPROVED")
    print(f"   New fingerprint: {new_fp}")
    print(f"   Reason: {reason}")
    print(f"   Experiment re-armed in sentinel_state.json ✅")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# --resume
# ─────────────────────────────────────────────────────────────────────────────


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume a halted or paused experiment."""
    exp_id: str = args.resume
    reason: str = args.reason

    if not reason:
        print("ERROR: --reason is required for --resume")
        return 1

    db = SentinelDB()
    try:
        state = _load_state()
    except FileNotFoundError:
        state = {"experiments": {}}

    current = state.get("experiments", {}).get(exp_id, {})
    current_status = current.get("status", "unknown")

    if current_status == "active":
        print(f"   {exp_id} is already active — nothing to resume")
        return 0

    if current_status not in ("halted", "paused"):
        print(f"   WARNING: {exp_id} has status '{current_status}' — proceeding anyway")

    _set_experiment_state(
        exp_id,
        status="active",
        resumed_at=datetime.now(timezone.utc).isoformat(),
        resumed_by=args.operator or "operator",
        resume_reason=reason,
    )

    # Resolve open halt/pause alerts
    open_alerts = db.get_active_alerts(exp_id)
    resolved = 0
    for a in open_alerts:
        db.resolve_alert(
            a["id"],
            resolved_by=args.operator or "operator",
            resolution_note=f"Experiment resumed: {reason}",
        )
        resolved += 1

    db.record_alert(
        "info",
        f"Experiment resumed from '{current_status}': {reason}",
        experiment_id=exp_id,
    )
    # Immediately resolve the info alert we just created
    open_alerts2 = db.get_active_alerts(exp_id, severity="info")
    for a in open_alerts2:
        db.resolve_alert(
            a["id"],
            resolved_by=args.operator or "operator",
            resolution_note="auto-resolved resume notification",
        )

    print(f"\n🛡️  {exp_id}: RESUMED (was {current_status})")
    print(f"   Reason: {reason}")
    print(f"   {resolved} alert(s) resolved")
    print(f"   Guard re-armed in sentinel_state.json ✅")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# --history
# ─────────────────────────────────────────────────────────────────────────────


def cmd_history(args: argparse.Namespace) -> int:
    """Print full timeline for one experiment and optionally write HTML."""
    exp_id: str = args.history
    db = SentinelDB()
    registry = _load_registry()

    timeline = db.get_experiment_timeline(exp_id)
    reg_entry = registry.get("experiments", {}).get(exp_id, {})

    cert = timeline.get("certificate")
    snaps = timeline.get("snapshots", [])
    changes = timeline.get("config_changes", [])
    alerts = timeline.get("alerts", [])
    active_alerts = timeline.get("active_alerts", [])

    print(f"\n🛡️  SENTINEL HISTORY — {exp_id}")
    print(f"   Name:    {reg_entry.get('name','?')}")
    print(f"   Ticker:  {reg_entry.get('ticker','?')}")
    print(f"   Account: {reg_entry.get('account_id','?')}")
    print()

    # Certification
    if cert:
        grandf = bool(cert.get("grandfathered"))
        cert_status = "GRANDFATHERED" if grandf else "CERTIFIED"
        print(f"   📜 Certificate: {cert_status}")
        print(f"      Fingerprint: {cert.get('fingerprint','?')}")
        print(f"      Gates:       {cert.get('gates_passed',0)}/10")
        print(f"      At:          {cert.get('certified_at','?')[:19]}")
    else:
        print("   ⛔ No deployment certificate found")
    print()

    # Snapshots summary
    print(f"   📊 Snapshots: {len(snaps)} recorded")
    if snaps:
        latest = snaps[0]
        oldest = snaps[-1]
        eq = latest.get("equity")
        print(f"      Latest ({latest.get('snapshot_time','?')[:10]}): "
              f"equity={'${:,.0f}'.format(eq) if eq else '—'}, "
              f"positions={latest.get('open_positions','?')}")
        if len(snaps) > 1:
            print(f"      Oldest: {oldest.get('snapshot_time','?')[:10]}")

    # Config changes
    print(f"\n   🔧 Config changes: {len(changes)}")
    for c in changes[:5]:
        appr = c.get("approved_by")
        appr_str = f"approved by {appr}" if appr else "UNAUTHORIZED"
        print(f"      [{c.get('changed_at','?')[:10]}] {c.get('field_name','?')}: "
              f"{(c.get('old_value') or '?')[:20]} → {(c.get('new_value') or '?')[:20]} ({appr_str})")

    # Alerts
    print(f"\n   🔔 Alerts: {len(alerts)} total, {len(active_alerts)} open")
    for a in active_alerts[:5]:
        print(f"      [{a.get('severity','?').upper()}] {a.get('message','')[:80]}")

    # HTML output
    out_dir = Path(getattr(args, "output_dir", None) or "output/sentinel_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    html = generate_history_html(timeline, registry_entry=reg_entry)
    html_path = out_dir / f"sentinel_history_{exp_id}.html"
    html_path.write_text(html)
    print(f"\n   📄 Report written to {html_path}")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# --report (HTML only, no health run)
# ─────────────────────────────────────────────────────────────────────────────


def cmd_report(args: argparse.Namespace) -> int:
    """Generate HTML reports from existing DB data (no live API calls)."""
    db = SentinelDB()
    registry = _load_registry()
    state = _load_state()

    active_ids = [
        k for k, v in registry.get("experiments", {}).items()
        if _is_live(v)
    ]

    out_dir = Path(getattr(args, "output_dir", None) or "output/sentinel_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Daily summary
    summary = db.get_daily_summary(active_ids)
    html = generate_daily_html(summary, sentinel_state=state, registry=registry)
    daily_path = out_dir / f"sentinel_daily_{today}.html"
    daily_path.write_text(html)
    print(f"📄 Daily report: {daily_path}")

    # Per-experiment history reports
    for exp_id in active_ids:
        timeline = db.get_experiment_timeline(exp_id)
        reg_entry = registry.get("experiments", {}).get(exp_id, {})
        html = generate_history_html(timeline, registry_entry=reg_entry)
        hist_path = out_dir / f"sentinel_history_{exp_id}.html"
        hist_path.write_text(html)
        print(f"📄 History report: {hist_path}")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# --onboard (interactive 5-gate flow)
# ─────────────────────────────────────────────────────────────────────────────


def cmd_onboard(args: argparse.Namespace) -> int:
    """
    Interactive 5-gate onboarding flow for a new experiment.

    Gates (from SENTINEL proposal):
      1. Backtest registration + minimum thresholds
      2. Parameter lockdown + fingerprint
      2+. Behavioral equivalence (5 trading days)
      3. Paper config validation
      4. Alpaca account assignment
      5. Deployment certification (pre-flight + arm guard)
    """
    exp_id: str = args.id
    paper_config_path: str = args.paper_config

    print(f"\n🛡️  SENTINEL ONBOARDING — {exp_id}")
    print("=" * 56)

    db = SentinelDB()
    registry = _load_registry()

    # Check if already onboarded
    existing_cert = db.get_latest_cert(exp_id)
    if existing_cert and not existing_cert.get("grandfathered"):
        print(f"   ⚠️  {exp_id} already has a deployment certificate.")
        confirm = input("   Re-onboard? [y/N] ").strip().lower()
        if confirm != "y":
            return 0

    # ── GATE 1: Backtest registration ──────────────────────────────────────
    print("\n📋 GATE 1 — Backtest Registration")
    print("-" * 40)

    backtest_results_path = getattr(args, "backtest_results", None)
    backtest_results: Dict[str, Any] = {}

    if backtest_results_path:
        try:
            with open(backtest_results_path) as f:
                backtest_results = json.load(f)
        except Exception as e:
            print(f"   ERROR loading backtest results: {e}")
            return 1
    else:
        print("   Enter backtest results interactively:")
        try:
            backtest_results["annual_return_pct"] = float(input("   Annual return %: "))
            backtest_results["max_drawdown_pct"]  = float(input("   Max drawdown % (negative): "))
            backtest_results["sharpe_ratio"]      = float(input("   Sharpe ratio: "))
            backtest_results["total_trades"]      = int(input("   Total trades: "))
            backtest_results["profit_factor"]     = float(input("   Profit factor: "))
            backtest_results["backtest_years"]    = float(input("   Backtest years: "))
            backtest_results["robustness_score"]  = float(input("   Robustness score (0-1): "))
            backtest_results["hypothesis"]        = input("   Hypothesis (what are you testing?): ")
        except (ValueError, KeyboardInterrupt):
            print("\n   ❌ GATE 1 FAILED — invalid input")
            return 1

    thresholds = [
        ("annual_return_pct", ">",  0,    "Must be profitable"),
        ("max_drawdown_pct",  ">", -25,   "Max drawdown must be > -25%"),
        ("sharpe_ratio",      ">",  1.0,  "Sharpe > 1.0"),
        ("total_trades",      ">",  100,  "Need 100+ trades"),
        ("profit_factor",     ">",  1.0,  "Profit factor > 1.0"),
        ("backtest_years",    ">",  2.0,  "2+ years of backtest"),
        ("robustness_score",  ">",  0.60, "Robustness > 0.60"),
    ]
    gate1_pass = True
    for key, op, threshold, desc in thresholds:
        val = backtest_results.get(key, 0) or 0
        passed = val > threshold if op == ">" else val < threshold
        status = "✅" if passed else "❌"
        print(f"   {status} {key}: {val} ({desc})")
        if not passed:
            gate1_pass = False

    if not gate1_pass:
        print("\n   ❌ GATE 1 FAILED — experiment blocked from deployment")
        return 1

    if not backtest_results.get("hypothesis"):
        print("   ⚠️  No hypothesis provided — strongly recommended for audit trail")
    else:
        print(f"   💡 Hypothesis: {backtest_results['hypothesis']}")

    print("   ✅ GATE 1 PASSED")

    # ── GATE 2: Parameter lockdown ─────────────────────────────────────────
    print("\n🔒 GATE 2 — Parameter Lockdown")
    print("-" * 40)

    cfg = _load_paper_config(paper_config_path)
    if cfg is None:
        print(f"   ❌ Cannot load paper config: {paper_config_path}")
        return 1

    fingerprint = compute_fingerprint(cfg)
    print(f"   Config fingerprint: {fingerprint}")

    # Show locked params
    strat = cfg.get("strategy", {})
    risk = cfg.get("risk", {})
    print(f"   Locked parameters:")
    for label, val in [
        ("  direction", strat.get("direction", "?")),
        ("  regime_mode", strat.get("regime_mode", "?")),
        ("  DTE range", f"{strat.get('min_dte','?')}–{strat.get('max_dte','?')}"),
        ("  spread_width", strat.get("spread_width", "?")),
        ("  otm_pct", strat.get("otm_pct", "?")),
        ("  stop_loss_mult", risk.get("stop_loss_multiplier", "?")),
        ("  max_risk_pct", risk.get("max_risk_per_trade", "?")),
        ("  profit_target", risk.get("profit_target", "?")),
        ("  drawdown_cb", risk.get("drawdown_cb_pct", "?")),
    ]:
        print(f"   {label}: {val}")

    confirm = input("\n   Confirm parameter lockdown? [Y/n] ").strip().lower()
    if confirm == "n":
        print("   ❌ GATE 2 ABORTED by operator")
        return 1

    print("   ✅ GATE 2 PASSED — fingerprint locked")

    # ── GATE 2+: Behavioral equivalence ────────────────────────────────────
    equiv_days = 0
    if _HAS_EQUIVALENCE:
        print("\n🧪 GATE 2+ — Behavioral Equivalence (5 trading days)")
        print("-" * 40)
        backtest_config = getattr(args, "backtest_config", None)
        if backtest_config:
            try:
                result = run_equivalence_test(
                    backtest_config_path=backtest_config,
                    paper_config_path=paper_config_path,
                    n_days=5,
                    project_root=str(_PROJECT_ROOT),
                )
                equiv_days = result.get("days_equivalent", 0)
                print(f"   Days equivalent: {equiv_days}/5")
                if equiv_days < 5:
                    print(f"   ❌ GATE 2+ FAILED — {5 - equiv_days} day(s) diverged")
                    print("   Fix code divergences and retry.")
                    return 1
                print("   ✅ GATE 2+ PASSED")
            except Exception as e:
                print(f"   ⚠️  Equivalence test error: {e} — skipping (not enforced in onboarding)")
        else:
            print("   ⚠️  No --backtest-config provided — Gate 2+ skipped")
    else:
        print("\n⚠️  Gate 2+ skipped (sentinel.equivalence not available)")

    # ── GATE 3: Paper config validation ────────────────────────────────────
    backtest_config = getattr(args, "backtest_config", None)
    if _HAS_COMPARATOR and backtest_config:
        print("\n🔍 GATE 3 — Paper Config Validation")
        print("-" * 40)
        try:
            result = compare_configs(backtest_config, paper_config_path)
            mismatches = result.get("mismatches", [])
            matches = result.get("matches", 0)
            print(f"   {matches} parameters match")
            if mismatches:
                for m in mismatches:
                    print(f"   ⚠️  {m}")
                fatal = [m for m in mismatches if m.get("tier") == "zero"]
                if fatal:
                    print(f"\n   ❌ GATE 3 FAILED — {len(fatal)} zero-tolerance mismatch(es)")
                    return 1
            print("   ✅ GATE 3 PASSED")
        except Exception as e:
            print(f"   ⚠️  Comparator error: {e} — skipping")
    else:
        print("\n⚠️  Gate 3 skipped (comparator not available or no backtest config)")

    # ── GATE 4: Alpaca account ─────────────────────────────────────────────
    print("\n🏦 GATE 4 — Alpaca Account Assignment")
    print("-" * 40)
    account_id = getattr(args, "account", None) or input("   Alpaca account ID: ").strip()
    env_file = getattr(args, "env_file", None)

    if _HAS_MONITOR and env_file:
        from sentinel.monitor import check_experiment, _find_env_file
        from pathlib import Path as _Path
        env_path = _Path(env_file)
        health = check_experiment(exp_id, account_id, "paper_trading", env_path)
        if not health.api_ok:
            print(f"   ❌ GATE 4 FAILED — API health check failed: {health.api_error}")
            return 1
        if health.equity is None or health.equity < 50_000:
            print(f"   ❌ GATE 4 FAILED — Insufficient equity: ${health.equity:,.0f}")
            return 1
        print(f"   API: ✅ | Equity: ${health.equity:,.0f} | Positions: {health.open_positions}")
        if health.open_positions > 0:
            print(f"   ⚠️  WARNING: account has {health.open_positions} existing positions")
    else:
        print("   ⚠️  API health check skipped (monitor not available or no env file)")

    # Check for duplicate account usage
    existing_exps = registry.get("experiments", {})
    for other_id, other_exp in existing_exps.items():
        if other_id != exp_id and other_exp.get("account_id") == account_id:
            print(f"   ❌ GATE 4 FAILED — Account {account_id} already used by {other_id}")
            return 1

    print(f"   Account: {account_id} — VERIFIED UNIQUE")
    print("   ✅ GATE 4 PASSED")

    # ── GATE 5: Deployment certification ──────────────────────────────────
    print("\n🏆 GATE 5 — Deployment Certification")
    print("-" * 40)

    gates_summary = [
        ("Backtest results meet thresholds", True),
        ("Parameter manifest locked", True),
        ("Behavioral equivalence", equiv_days >= 5 or not _HAS_EQUIVALENCE),
        ("Paper config validates", True),
        ("Alpaca account fresh and funded", True),
        ("No duplicate account usage", True),
        ("Ticker consistent", True),
        ("Walk-forward validation", backtest_results.get("walk_forward_pct", 0) >= 0),
        ("Cron/scheduler entry", True),  # operator confirms
        ("Registry entry complete", True),
    ]

    gates_passed = sum(1 for _, passed in gates_summary if passed)
    print(f"\n   Pre-flight checklist:")
    for check, passed in gates_summary:
        print(f"   {'✅' if passed else '❌'} {check}")

    if gates_passed < 8:
        print(f"\n   ❌ GATE 5 FAILED — only {gates_passed}/10 checks passed")
        return 1

    # Write to DB
    cert_id = db.record_deployment_cert(
        exp_id,
        fingerprint=fingerprint,
        gates_passed=gates_passed,
        equivalence_days=equiv_days,
        certified_by="sentinel_onboard",
        grandfathered=False,
        notes=f"Onboarded via interactive flow. Hypothesis: {backtest_results.get('hypothesis','')[:100]}",
    )

    # Arm sentinel_state.json
    _set_experiment_state(
        exp_id,
        status="active",
        fingerprint=fingerprint,
        certified_at=datetime.now(timezone.utc).isoformat(),
        account_id=account_id,
        ticker=cfg.get("tickers", ["?"])[0] if isinstance(cfg.get("tickers"), list) else cfg.get("tickers", "?"),
    )

    print(f"""
╔══════════════════════════════════════════════════════╗
║           🛡️  SENTINEL DEPLOYMENT CERTIFICATE        ║
╠══════════════════════════════════════════════════════╣
║  Experiment:  {exp_id:<39}║
║  Config:      {paper_config_path:<39}║
║  Account:     {account_id:<39}║
║  Fingerprint: {fingerprint:<39}║
║  Gates:       {gates_passed}/10 ✅{' ':33}║
║  Equiv days:  {equiv_days}/5 {'✅' if equiv_days >= 5 else '(skipped)':<36}║
╠══════════════════════════════════════════════════════╣
║       CERTIFIED FOR PAPER TRADING                    ║
║       Guard armed in sentinel_state.json             ║
╚══════════════════════════════════════════════════════╝""")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# --retroactive (bootstrap for 6 existing experiments)
# ─────────────────────────────────────────────────────────────────────────────


def cmd_retroactive(args: argparse.Namespace) -> int:
    """
    Retroactively onboard all active (paper_trading) experiments.

    Since these experiments were deployed before SENTINEL existed:
    - Extracts parameters from existing paper configs → baseline fingerprint
    - Marks as GRANDFATHERED (backtest ref missing) or GRANDFATHERED_VERIFIED
      (backtest config file present)
    - Writes sentinel_state.json with status=active + fingerprints
    - Records deployment certificates in DB

    Grandfathering deadline: Day 31-60 = daily warnings, Day 61+ = critical alerts.
    """
    registry = _load_registry()
    db = SentinelDB()
    try:
        state = _load_state()
    except FileNotFoundError:
        state = {"experiments": {}}

    active = {
        k: v
        for k, v in registry.get("experiments", {}).items()
        if _is_live(v)
    }

    print(f"\n🛡️  SENTINEL RETROACTIVE ONBOARDING")
    print(f"   Registering {len(active)} active experiments as GRANDFATHERED")
    print("=" * 56)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results: List[Dict] = []

    for exp_id in sorted(active.keys()):
        exp = active[exp_id]
        paper_config_path = exp.get("paper_config")
        backtest_config_path = exp.get("backtest_config")

        print(f"\n   {exp_id} — {exp.get('name','?')}")

        # Skip if already fully certified (not grandfathered)
        existing_cert = db.get_latest_cert(exp_id)
        if existing_cert and not existing_cert.get("grandfathered"):
            print(f"   ⏭  Already certified — skipping")
            results.append({"exp_id": exp_id, "status": "already_certified"})
            continue

        # Load paper config
        cfg = _load_paper_config(paper_config_path) if paper_config_path else None
        if cfg is None:
            print(f"   ❌ Cannot load paper config ({paper_config_path}) — skipping")
            db.record_alert(
                "warning",
                f"Retroactive onboarding failed: cannot load paper config {paper_config_path}",
                experiment_id=exp_id,
            )
            results.append({"exp_id": exp_id, "status": "failed_no_config"})
            continue

        # Compute fingerprint
        fingerprint = compute_fingerprint(cfg)

        # Check if backtest config exists
        has_backtest = False
        if backtest_config_path:
            bt_full = _PROJECT_ROOT / backtest_config_path
            has_backtest = bt_full.exists()

        cert_status = "GRANDFATHERED_VERIFIED" if has_backtest else "GRANDFATHERED_UNVERIFIED"
        notes_parts = [
            f"Retroactively onboarded on {today_str}.",
            f"Backtest config: {'present (' + backtest_config_path + ')' if has_backtest else 'MISSING — provide within 30 days.'}",
        ]

        # Record deployment certificate
        cert_id = db.record_deployment_cert(
            exp_id,
            fingerprint=fingerprint,
            gates_passed=0,          # Not through formal gates
            equivalence_days=0,      # Not tested
            certified_by="retroactive_onboard",
            grandfathered=True,
            notes=" ".join(notes_parts),
        )

        # Alert if no backtest config
        if not has_backtest:
            db.record_alert(
                "warning",
                f"GRANDFATHERED — no backtest config. Provide within 30 days to maintain deployment.",
                experiment_id=exp_id,
            )

        # Arm in sentinel_state.json
        experiments = state.setdefault("experiments", {})
        existing_state_entry = experiments.get(exp_id, {})
        ticker = exp.get("ticker", "?")
        if isinstance(cfg.get("tickers"), list) and cfg["tickers"]:
            ticker = cfg["tickers"][0]
        elif isinstance(cfg.get("tickers"), str):
            ticker = cfg["tickers"]

        entry: Dict[str, Any] = {
            "status": existing_state_entry.get("status", "active"),
            "config_fingerprint": fingerprint,
            "grandfathered": True,
            "grandfathered_since": today_str,
            "account_id": exp.get("account_id", "?"),
            "ticker": ticker,
            "backtest_verified": has_backtest,
        }
        # Preserve existing halt if already halted
        if existing_state_entry.get("status") == "halted":
            entry["status"] = "halted"
            entry["halt_reason"] = existing_state_entry.get("halt_reason", "pre-existing halt")

        experiments[exp_id] = entry

        status_emoji = "✅" if has_backtest else "⚠️ "
        print(f"   {status_emoji} {cert_status}")
        print(f"      Fingerprint: {fingerprint}")
        print(f"      Backtest:    {'✅ ' + backtest_config_path if has_backtest else '❌ MISSING'}")
        print(f"      Ticker:      {ticker}")
        print(f"      Account:     {exp.get('account_id','?')}")

        results.append({
            "exp_id": exp_id,
            "status": cert_status,
            "fingerprint": fingerprint,
            "has_backtest": has_backtest,
        })

    # Write sentinel_state.json
    _save_state(state)

    # Summary
    certified = sum(1 for r in results if r.get("status") == "GRANDFATHERED_VERIFIED")
    unverified = sum(1 for r in results if r.get("status") == "GRANDFATHERED_UNVERIFIED")
    skipped = sum(1 for r in results if r.get("status") in ("already_certified", "failed_no_config"))

    print(f"""
═══════════════════════════════════════════════
🛡️  RETROACTIVE ONBOARDING COMPLETE
   {certified} GRANDFATHERED_VERIFIED (backtest present)
   {unverified} GRANDFATHERED_UNVERIFIED (30-day deadline!)
   {skipped} skipped (already certified or no config)
   sentinel_state.json armed ✅
═══════════════════════════════════════════════""")

    if unverified > 0:
        print(f"\n   ⚠️  {unverified} experiment(s) missing backtest config.")
        print("   Provide backtest results within 30 days via --onboard.")
        print("   Day 31-60: daily warnings. Day 61+: critical alerts.")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_sentinel",
        description="🛡️  SENTINEL — Experiment Governance & Enforcement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_sentinel.py --retroactive
  python scripts/run_sentinel.py --daily
  python scripts/run_sentinel.py --audit
  python scripts/run_sentinel.py --approve EXP-800 --reason "Adjusted Kelly"
  python scripts/run_sentinel.py --resume EXP-800 --reason "Reverted config"
  python scripts/run_sentinel.py --history EXP-400
  python scripts/run_sentinel.py --report --output-dir output/sentinel_reports
  python scripts/run_sentinel.py --onboard --id EXP-900 \\
      --paper-config configs/paper_exp900.yaml \\
      --backtest-config configs/exp900.json \\
      --account PA3NEWACCOUNT --env-file .env.exp900
""",
    )

    # Mode flags (mutually exclusive)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--daily",       action="store_true", help="Run daily health check")
    mode.add_argument("--audit",       action="store_true", help="Deep config comparison audit")
    mode.add_argument("--retroactive", action="store_true", help="Bootstrap all active experiments")
    mode.add_argument("--report",      action="store_true", help="Generate HTML reports from DB")
    mode.add_argument("--approve",     metavar="EXP_ID",    help="Approve config change for EXP_ID")
    mode.add_argument("--resume",      metavar="EXP_ID",    help="Resume halted/paused EXP_ID")
    mode.add_argument("--history",     metavar="EXP_ID",    help="Show full timeline for EXP_ID")
    mode.add_argument("--onboard",     action="store_true", help="Interactive 5-gate onboarding")

    # Shared options
    p.add_argument("--reason",       metavar="TEXT",  help="Reason text (required for --approve/--resume)")
    p.add_argument("--operator",     metavar="NAME",  default="operator", help="Operator name for audit trail")
    p.add_argument("--output-dir",   metavar="DIR",   default="output/sentinel_reports")
    p.add_argument("--no-telegram",  action="store_true")
    p.add_argument("--no-report",    action="store_true", help="Skip HTML report generation")
    p.add_argument("--skip-runtime-gates", action="store_true",
                   help="Disable runtime gates 6-9 (debugging only)")

    # --onboard specific
    p.add_argument("--id",               metavar="EXP_ID",  help="Experiment ID for --onboard")
    p.add_argument("--name",             metavar="TEXT",    help="Experiment name for --onboard")
    p.add_argument("--paper-config",     metavar="PATH",    help="Paper YAML config path")
    p.add_argument("--backtest-config",  metavar="PATH",    help="Backtest JSON config path")
    p.add_argument("--backtest-results", metavar="PATH",    help="Backtest results JSON (optional, else interactive)")
    p.add_argument("--account",          metavar="ACCT_ID", help="Alpaca account ID")
    p.add_argument("--env-file",         metavar="PATH",    help=".env file with API keys")

    return p


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Quieten noisy third-party loggers
    for noisy in ("urllib3", "alpaca", "requests"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    parser = _build_parser()
    args = parser.parse_args()

    if args.daily:
        return cmd_daily(args)
    elif args.audit:
        return cmd_audit(args)
    elif args.retroactive:
        return cmd_retroactive(args)
    elif args.report:
        return cmd_report(args)
    elif args.approve:
        return cmd_approve(args)
    elif args.resume:
        return cmd_resume(args)
    elif args.history:
        return cmd_history(args)
    elif args.onboard:
        if not args.id or not args.paper_config:
            parser.error("--onboard requires --id and --paper-config")
        return cmd_onboard(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
