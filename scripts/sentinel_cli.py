#!/usr/bin/env python3
"""
sentinel_cli.py — Sentinel command-line interface for manual checks.

Usage:
  python scripts/sentinel_cli.py status            — all experiments + health scores
  python scripts/sentinel_cli.py check EXP-503     — run all gates for one experiment
  python scripts/sentinel_cli.py alerts            — list open alerts
  python scripts/sentinel_cli.py resolve 42        — mark alert #42 as resolved
  python scripts/sentinel_cli.py report            — generate daily health report
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger("sentinel_cli")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_sentinel_state() -> dict:
    path = _PROJECT_ROOT / "sentinel_state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _load_registry() -> dict:
    path = _PROJECT_ROOT / "experiments" / "registry.json"
    if not path.exists():
        return {"experiments": {}}
    return json.loads(path.read_text())


def _get_db():
    """Get SentinelDB instance."""
    from sentinel.history import SentinelDB
    return SentinelDB()


def _compute_health_score(exp_state: dict) -> int:
    """Simple health score 0-100."""
    score = 100
    status = exp_state.get("status", "active")
    if status == "halted":
        return 0

    # Missing fingerprint
    if not exp_state.get("config_fingerprint"):
        score -= 10

    # Stale health check
    last_hc = exp_state.get("last_health_check")
    if last_hc:
        try:
            hc_dt = datetime.fromisoformat(last_hc)
            if hc_dt.tzinfo is None:
                hc_dt = hc_dt.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - hc_dt).total_seconds() / 3600
            if age_h > 48:
                score -= 20
            elif age_h > 24:
                score -= 5
        except (ValueError, TypeError):
            score -= 10
    else:
        score -= 5

    # Halt reason set
    if exp_state.get("halt_reason"):
        score -= 30

    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(args):
    """Show all experiments with health scores and status."""
    state = _load_sentinel_state()
    experiments = state.get("experiments", {})

    if not experiments:
        print("No experiments enrolled in Sentinel.")
        return 0

    print(f"{'Experiment':<12} {'Status':<10} {'Health':>6}  {'Fingerprint':<14} {'Last Check':<20} {'Halt Reason'}")
    print("-" * 95)

    for eid in sorted(experiments.keys()):
        exp = experiments[eid]
        status = exp.get("status", "unknown")
        score = _compute_health_score(exp)
        fp = exp.get("config_fingerprint", "")[:12] + "..." if exp.get("config_fingerprint") else "none"
        last_hc = (exp.get("last_health_check") or "never")[:19]
        halt = exp.get("halt_reason") or ""

        # Color indicator
        if score >= 80:
            indicator = "OK"
        elif score >= 50:
            indicator = "WARN"
        else:
            indicator = "CRIT"

        print(f"{eid:<12} {status:<10} {score:>3} {indicator:<3} {fp:<14} {last_hc:<20} {halt[:30]}")

    # Summary
    active = sum(1 for e in experiments.values() if e.get("status") == "active")
    halted = sum(1 for e in experiments.values() if e.get("status") == "halted")
    print(f"\n{len(experiments)} experiments: {active} active, {halted} halted")

    # Open alerts
    try:
        db = _get_db()
        open_alerts = db.get_active_alerts()
        if open_alerts:
            print(f"{len(open_alerts)} open alert(s)")
    except Exception:
        pass

    return 0


def cmd_check(args):
    """Run all gates for a single experiment."""
    exp_id = args.experiment_id
    state = _load_sentinel_state()
    registry = _load_registry()

    exp_state = state.get("experiments", {}).get(exp_id)
    if not exp_state:
        print(f"ERROR: {exp_id} not enrolled in Sentinel")
        return 1

    print(f"Checking {exp_id}...")
    print()

    results = []

    # Gate 0: Registry status
    reg_exp = registry.get("experiments", {}).get(exp_id)
    if reg_exp:
        reg_status = reg_exp.get("status", "unknown")
        ok = reg_status in ("active", "paper_trading")
        results.append(("G0: Registry Status", "PASS" if ok else "FAIL", f"status={reg_status}"))
    else:
        results.append(("G0: Registry Status", "SKIP", "not in registry"))

    # Gate 1: Sentinel state
    sentinel_status = exp_state.get("status", "unknown")
    if sentinel_status == "active":
        results.append(("G1: Sentinel State", "PASS", "active"))
    elif sentinel_status == "halted":
        reason = exp_state.get("halt_reason", "unknown")
        results.append(("G1: Sentinel State", "HALT", f"reason: {reason}"))
    else:
        results.append(("G1: Sentinel State", "WARN", f"status={sentinel_status}"))

    # Gate 2: Config fingerprint
    fp = exp_state.get("config_fingerprint")
    config_path = exp_state.get("paper_config")
    if fp and config_path:
        full_path = _PROJECT_ROOT / config_path
        if full_path.exists():
            try:
                from sentinel.state import compute_fingerprint
                current_fp = compute_fingerprint(str(full_path))
                if current_fp == fp:
                    results.append(("G2: Config Fingerprint", "PASS", f"matches ({fp[:12]}...)"))
                else:
                    results.append(("G2: Config Fingerprint", "FAIL", f"DRIFT! stored={fp[:12]}... current={current_fp[:12]}..."))
            except Exception as e:
                results.append(("G2: Config Fingerprint", "WARN", f"check failed: {e}"))
        else:
            results.append(("G2: Config Fingerprint", "WARN", f"config not found: {config_path}"))
    elif fp:
        results.append(("G2: Config Fingerprint", "WARN", "no config path to verify"))
    else:
        results.append(("G2: Config Fingerprint", "SKIP", "no fingerprint stored"))

    # Gate 3: API health (check freshness of last_health_check)
    last_hc = exp_state.get("last_health_check")
    if last_hc:
        try:
            hc_dt = datetime.fromisoformat(last_hc)
            if hc_dt.tzinfo is None:
                hc_dt = hc_dt.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - hc_dt).total_seconds() / 3600
            if age_h < 2:
                results.append(("G3: API Health", "PASS", f"checked {age_h:.1f}h ago"))
            elif age_h < 24:
                results.append(("G3: API Health", "WARN", f"checked {age_h:.1f}h ago"))
            else:
                results.append(("G3: API Health", "CRIT", f"stale — {age_h:.0f}h since last check"))
        except (ValueError, TypeError):
            results.append(("G3: API Health", "WARN", "invalid timestamp"))
    else:
        results.append(("G3: API Health", "SKIP", "never checked"))

    # Gate 8: Backtest baseline
    baseline = exp_state.get("backtest_baseline", {})
    if baseline:
        wr = baseline.get("win_rate", "?")
        dd = baseline.get("mc_worst_dd_pct", "?")
        results.append(("G8: Backtest Baseline", "PASS", f"WR={wr}% DD={dd}%"))
    else:
        results.append(("G8: Backtest Baseline", "SKIP", "no baseline configured"))

    # Print results
    has_fail = False
    for gate, status, detail in results:
        if status == "PASS":
            icon = "  PASS"
        elif status == "WARN":
            icon = "  WARN"
        elif status == "SKIP":
            icon = "  SKIP"
        elif status in ("FAIL", "HALT", "CRIT"):
            icon = "  FAIL"
            has_fail = True
        else:
            icon = "  ????"
        print(f"  {icon}  {gate:<28} {detail}")

    # Health score
    score = _compute_health_score(exp_state)
    print(f"\n  Health Score: {score}/100")

    return 1 if has_fail else 0


def cmd_alerts(args):
    """List open alerts."""
    try:
        db = _get_db()
    except Exception as e:
        print(f"Cannot access sentinel DB: {e}")
        return 1

    if args.all:
        alerts = db.get_all_alerts(limit=50)
    else:
        alerts = db.get_active_alerts()

    if not alerts:
        print("No open alerts." if not args.all else "No alerts found.")
        return 0

    print(f"{'ID':>4}  {'Severity':<10} {'Experiment':<12} {'Time':<20} {'Status':<9} Message")
    print("-" * 100)

    for a in alerts:
        aid = a.get("id", "?")
        sev = a.get("severity", "info").upper()
        eid = a.get("experiment_id") or "system"
        ts = (a.get("alert_time") or "")[:19]
        resolved = "RESOLVED" if a.get("resolved") else "OPEN"
        msg = (a.get("message") or "")[:60]
        print(f"{aid:>4}  {sev:<10} {eid:<12} {ts:<20} {resolved:<9} {msg}")

    print(f"\n{len(alerts)} alert(s)")
    return 0


def cmd_resolve(args):
    """Mark an alert as resolved."""
    try:
        db = _get_db()
    except Exception as e:
        print(f"Cannot access sentinel DB: {e}")
        return 1

    alert_id = int(args.alert_id)
    operator = args.operator or os.environ.get("USER", "operator")
    note = args.note or "resolved via CLI"

    ok = db.resolve_alert(alert_id, resolved_by=operator, resolution_note=note)
    if ok:
        print(f"Alert #{alert_id} resolved by {operator}: {note}")
        return 0
    else:
        print(f"Alert #{alert_id} not found or already resolved.")
        return 1


def cmd_resume(args):
    """Atomically resume a halted experiment (FU#5).

    All-or-nothing:
      1. Load sentinel_state.json
      2. Look up experiment; bail if missing or already active+!halted
      3. Compute fresh config_fingerprint from paper_config
      4. Pull current account_id from experiments/registry.json
      5. Apply: status='active', halted=False, halt_reason=None,
         halted_at=None, halted_by=None, resumed_at/by/reason set
      6. save_state() — atomic tmp+rename; if it raises, on-disk file is
         untouched and we return rc=3
      7. (--restart) launchctl unload+load the experiment plist
      8. Print before/after diff
    """
    from sentinel.state import compute_fingerprint, load_state, save_state

    exp_id = args.experiment_id
    reason = args.reason
    by = args.by
    restart = bool(getattr(args, "restart", False))

    try:
        state = load_state()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    experiments = state.get("experiments", {})
    if exp_id not in experiments:
        print(
            f"ERROR: {exp_id} not enrolled in sentinel_state.json",
            file=sys.stderr,
        )
        return 2

    exp = experiments[exp_id]

    is_halted = exp.get("status") == "halted" or exp.get("halted") is True
    if not is_halted:
        print(
            f"WARNING: {exp_id} is not halted "
            f"(status={exp.get('status')!r}, halted={exp.get('halted')!r}). No-op."
        )
        return 0

    # Snapshot before-state for diff + in-memory rollback.
    before = {
        "status": exp.get("status"),
        "halted": exp.get("halted"),
        "halt_reason": exp.get("halt_reason"),
        "halted_at": exp.get("halted_at"),
        "halted_by": exp.get("halted_by"),
        "halt_acknowledged_stale": exp.get("halt_acknowledged_stale"),
        "halt_acknowledged_by": exp.get("halt_acknowledged_by"),
        "halt_acknowledged_at": exp.get("halt_acknowledged_at"),
        "config_fingerprint": exp.get("config_fingerprint"),
        "account_id": exp.get("account_id"),
        "resumed_at": exp.get("resumed_at"),
        "resumed_by": exp.get("resumed_by"),
        "resume_reason": exp.get("resume_reason"),
    }

    # Pull current account from registry.
    registry = _load_registry()
    reg_exp = registry.get("experiments", {}).get(exp_id, {})
    new_account_id = (
        reg_exp.get("account_id")
        or reg_exp.get("alpaca_account_id")
        or before["account_id"]
    )

    # Compute fresh fingerprint from paper_config.
    paper_config = exp.get("paper_config")
    new_fingerprint = before["config_fingerprint"]
    if paper_config:
        cfg_path = _PROJECT_ROOT / paper_config
        if not cfg_path.exists():
            print(
                f"ERROR: paper_config not found: {paper_config}",
                file=sys.stderr,
            )
            return 3
        try:
            new_fingerprint = compute_fingerprint(str(cfg_path))
        except OSError as e:
            print(f"ERROR: cannot read paper_config: {e}", file=sys.stderr)
            return 3

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Apply changes in memory.
    exp["status"] = "active"
    exp["halted"] = False
    exp["halt_reason"] = None
    exp["halted_at"] = None
    exp["halted_by"] = None
    # Clear any G24 stale-halt acknowledgement so a future re-halt starts fresh.
    exp["halt_acknowledged_stale"] = None
    exp["halt_acknowledged_by"] = None
    exp["halt_acknowledged_at"] = None
    exp["config_fingerprint"] = new_fingerprint
    exp["account_id"] = new_account_id
    exp["resumed_at"] = now
    exp["resumed_by"] = by
    exp["resume_reason"] = reason

    # Atomic save — if it raises, restore in-memory state so callers/tests
    # see no partial mutation. (save_state itself writes via tmp+rename, so
    # the on-disk file is unchanged when an exception escapes.)
    try:
        save_state(state)
    except Exception as e:  # noqa: BLE001
        for k, v in before.items():
            exp[k] = v
        print(f"ERROR: save_state failed: {e}", file=sys.stderr)
        return 3

    # Print before/after diff.
    def _short(v):
        if isinstance(v, str) and len(v) > 16:
            return v[:12] + "..."
        return repr(v)

    print(f"Resumed {exp_id}:")
    print(f"  status:             {before['status']!r} -> {exp['status']!r}")
    print(f"  halted:             {before['halted']!r} -> {exp['halted']!r}")
    print(f"  halt_reason:        {before['halt_reason']!r} -> {exp['halt_reason']!r}")
    print(f"  config_fingerprint: {_short(before['config_fingerprint'])} -> {_short(exp['config_fingerprint'])}")
    print(f"  account_id:         {before['account_id']!r} -> {exp['account_id']!r}")
    print(f"  resumed_at:         {now}")
    print(f"  resumed_by:         {by}")
    print(f"  resume_reason:      {reason}")

    # Optional --restart via launchctl. Plist convention:
    # com.pilotai.exp{NNN}.plist in ~/Library/LaunchAgents/.
    if restart:
        suffix = exp_id.lower().replace("exp-", "exp").replace("-", "")
        plist_name = f"com.pilotai.{suffix}.plist"
        plist_path = str(Path.home() / "Library" / "LaunchAgents" / plist_name)
        print(f"\nRestarting via launchctl: {plist_path}")
        subprocess.run(
            ["launchctl", "unload", plist_path],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["launchctl", "load", plist_path],
            capture_output=True,
            check=False,
        )
        print("launchctl unload+load complete.")

    return 0


def cmd_ack_stale(args):
    """Acknowledge a halt as 'known stale' so Gate 24 stops nagging (Branch 8 / G24).

    Sets three fields on the experiment in sentinel_state.json:
      - halt_acknowledged_stale = True
      - halt_acknowledged_by    = <args.by>
      - halt_acknowledged_at    = <UTC ISO 8601>

    Optionally records ``halt_acknowledged_reason`` for forensic context.
    Does NOT change status / halted / halt_reason — the experiment stays
    halted; only the G24 nag is suppressed. ``sentinel_cli resume`` clears
    these fields atomically as part of resume.
    """
    from sentinel.state import load_state, save_state

    exp_id = args.experiment_id
    by = args.by
    reason = args.reason

    try:
        state = load_state()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    experiments = state.get("experiments", {})
    if exp_id not in experiments:
        print(
            f"ERROR: {exp_id} not enrolled in sentinel_state.json",
            file=sys.stderr,
        )
        return 2

    exp = experiments[exp_id]
    if exp.get("status") != "halted":
        print(
            f"ERROR: {exp_id} is not halted (status={exp.get('status')!r}); "
            "ack-stale only applies to halted experiments.",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Snapshot for in-memory rollback on save failure.
    before = {
        "halt_acknowledged_stale": exp.get("halt_acknowledged_stale"),
        "halt_acknowledged_by": exp.get("halt_acknowledged_by"),
        "halt_acknowledged_at": exp.get("halt_acknowledged_at"),
        "halt_acknowledged_reason": exp.get("halt_acknowledged_reason"),
    }

    exp["halt_acknowledged_stale"] = True
    exp["halt_acknowledged_by"] = by
    exp["halt_acknowledged_at"] = now
    exp["halt_acknowledged_reason"] = reason

    try:
        save_state(state)
    except Exception as e:  # noqa: BLE001
        for k, v in before.items():
            exp[k] = v
        print(f"ERROR: save_state failed: {e}", file=sys.stderr)
        return 3

    print(f"Acknowledged stale halt for {exp_id}:")
    print(f"  halt_acknowledged_stale: True")
    print(f"  halt_acknowledged_by   : {by}")
    print(f"  halt_acknowledged_at   : {now}")
    print(f"  halt_acknowledged_reason: {reason}")
    print(f"  (status remains 'halted' — use `resume` to recover)")
    return 0


def cmd_why_halted(args):
    """Explain WHY an experiment is halted, with evidence vs current state (FU#6).

    Reads halted_at, halted_by, halt_reason, halt_evidence from
    sentinel_state.json and prints:
      - Halt reason
      - Halted at (with age in days)
      - Halted by
      - Reason class (drawdown / config_drift / non_functional / api_failed / manual)
      - Markdown table of contributing gate(s) — for G2 fingerprint we
        recompute current_value from disk so an operator can see whether
        the drift still applies.

    Legacy halts without halted_at print
    ``halted_at: unknown (pre-2026-04-28)`` (display-only synthetic
    backfill — no file mutation).
    """
    exp_id = args.experiment_id
    state = _load_sentinel_state()
    experiments = state.get("experiments", {})

    exp = experiments.get(exp_id)
    if exp is None:
        print(f"ERROR: {exp_id} not enrolled in sentinel_state.json", file=sys.stderr)
        return 2

    if exp.get("status") != "halted":
        print(
            f"WARNING: {exp_id} is not halted (status={exp.get('status')!r}). "
            "Nothing to explain."
        )
        return 1

    halt_reason = exp.get("halt_reason") or "(no reason recorded)"
    halted_at = exp.get("halted_at")
    halted_by = exp.get("halted_by") or "(unknown)"
    halt_evidence = exp.get("halt_evidence") or {}

    # Header block
    print(f"Why halted: {exp_id}")
    print(f"  Halt reason   : {halt_reason}")

    if halted_at:
        age_str = _format_halt_age(halted_at)
        print(f"  Halted at     : {halted_at}{age_str}")
    else:
        # Display-only synthetic backfill (no on-disk mutation).
        print("  Halted at     : unknown (pre-2026-04-28)")

    print(f"  Halted by     : {halted_by}")
    print(f"  Reason class  : {_classify_halt(halt_reason, halt_evidence)}")

    # Evidence table
    if halt_evidence:
        print()
        print("  | Gate | Metric             | Stored value     | Threshold     | Current value    | Stale?            |")
        print("  |------|--------------------|------------------|---------------|------------------|-------------------|")

        gate_id = halt_evidence.get("gate_id", "?")
        metric = halt_evidence.get("metric_name", "?")
        stored = halt_evidence.get("stored_value", "?")
        threshold = halt_evidence.get("threshold", "?")
        current = halt_evidence.get("current_value", "?")
        stale_label = "n/a"

        # G2 fingerprint: recompute current from disk.
        if gate_id == "G2" and metric == "config_fingerprint":
            paper_config = exp.get("paper_config")
            if paper_config:
                cfg_path = _PROJECT_ROOT / paper_config
                if cfg_path.exists():
                    try:
                        from sentinel.state import compute_fingerprint
                        current = compute_fingerprint(str(cfg_path))
                    except Exception:  # noqa: BLE001
                        current = "(read failed)"
                else:
                    current = "(config missing)"
            # Stale flag: if recomputed current still differs from stored,
            # drift is still present. If they now match, the halt is stale.
            if isinstance(stored, str) and isinstance(current, str):
                stale_label = (
                    "stale (drift cleared)"
                    if current == stored
                    else "drift confirmed"
                )

        print(
            f"  | {gate_id:<4} | {_clip(metric, 18):<18} | "
            f"{_clip(stored, 16):<16} | {_clip(threshold, 13):<13} | "
            f"{_clip(current, 16):<16} | {stale_label:<17} |"
        )
    else:
        print()
        print("  (no halt_evidence recorded — likely a legacy or manual halt)")

    return 0


def _clip(value: object, n: int) -> str:
    s = str(value) if value is not None else ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _format_halt_age(halted_at: str) -> str:
    try:
        dt = datetime.fromisoformat(halted_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_d = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        if age_d < 1:
            return f" ({age_d * 24:.1f} hours ago)"
        return f" ({age_d:.0f} days ago)"
    except (ValueError, TypeError):
        return ""


def _classify_halt(reason: str, evidence: dict) -> str:
    """Heuristic mapping of halt_reason / evidence → reason class."""
    gate_id = (evidence or {}).get("gate_id", "")
    metric = ((evidence or {}).get("metric_name") or "").lower()
    text = (reason or "").lower()

    if gate_id == "G2" or "fingerprint" in metric or "drift" in text and "config" in text:
        return "config_drift"
    if "drawdown" in text or "drawdown" in metric:
        return "drawdown"
    if "non-functional" in text or "non_functional" in text or "no completed trades" in text:
        return "non_functional"
    if "api" in text or "401" in text or "403" in text or "auth" in text:
        return "api_failed"
    return "manual"


def cmd_report(args):
    """Generate daily health report."""
    state = _load_sentinel_state()
    experiments = state.get("experiments", {})
    exp_ids = [eid for eid, e in experiments.items() if e.get("status") != "retired"]

    if not exp_ids:
        print("No experiments to report on.")
        return 0

    try:
        db = _get_db()
        summary = db.get_daily_summary(exp_ids)
    except Exception as e:
        print(f"Cannot access sentinel DB: {e}")
        summary = {}

    # Text report
    print(f"SENTINEL Daily Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    for eid in sorted(exp_ids):
        exp = experiments.get(eid, {})
        score = _compute_health_score(exp)
        status = exp.get("status", "unknown")
        print(f"\n  {eid}: {status} (health={score}/100)")

    crits = summary.get("critical_alerts", [])
    warns = summary.get("warning_alerts", [])
    if crits:
        print(f"\n  {len(crits)} CRITICAL alert(s)")
    if warns:
        print(f"  {len(warns)} warning(s)")
    if not crits and not warns:
        print("\n  All clear — no open alerts")

    # Optionally generate HTML
    if args.html:
        try:
            from sentinel.report import generate_daily_html
            registry = _load_registry()
            html = generate_daily_html(summary, sentinel_state=state, registry=registry)
            out_dir = Path(args.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            out_path = out_dir / f"sentinel_daily_{today}.html"
            out_path.write_text(html)
            print(f"\n  HTML report: {out_path}")
        except Exception as e:
            print(f"\n  HTML generation failed: {e}")

    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sentinel_cli",
        description="Sentinel CLI — manual health checks and alert management",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="Show all experiments and health scores")

    # check
    check_p = sub.add_parser("check", help="Run all gates for one experiment")
    check_p.add_argument("experiment_id", help="Experiment ID (e.g., EXP-503)")

    # alerts
    alerts_p = sub.add_parser("alerts", help="List open alerts")
    alerts_p.add_argument("--all", action="store_true", help="Show all alerts (not just open)")

    # resolve
    resolve_p = sub.add_parser("resolve", help="Mark an alert as resolved")
    resolve_p.add_argument("alert_id", help="Alert ID number")
    resolve_p.add_argument("--operator", help="Operator name")
    resolve_p.add_argument("--note", help="Resolution note")

    # resume
    resume_p = sub.add_parser("resume", help="Atomically resume a halted experiment")
    resume_p.add_argument("experiment_id", help="Experiment ID (e.g., EXP-503)")
    resume_p.add_argument("--reason", required=True, help="Reason for resuming")
    resume_p.add_argument("--by", required=True, help="Operator name")
    resume_p.add_argument(
        "--restart",
        action="store_true",
        help="Also unload+load the experiment's launchd plist",
    )

    # ack-stale (Branch 8 / G24)
    ack_p = sub.add_parser(
        "ack-stale",
        help="Acknowledge a halt as known-stale (suppresses Gate 24 nag)",
    )
    ack_p.add_argument("experiment_id", help="Experiment ID (e.g., EXP-503)")
    ack_p.add_argument("--by", required=True, help="Operator name")
    ack_p.add_argument("--reason", required=True, help="Why the halt is being acknowledged as stale")

    # why-halted
    why_p = sub.add_parser("why-halted", help="Explain why an experiment is halted")
    why_p.add_argument("experiment_id", help="Experiment ID (e.g., EXP-503)")

    # report
    report_p = sub.add_parser("report", help="Generate daily health report")
    report_p.add_argument("--html", action="store_true", help="Also generate HTML report")
    report_p.add_argument("--output-dir", default="output/sentinel_reports", help="HTML output directory")

    return p


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args()

    dispatch = {
        "status": cmd_status,
        "check": cmd_check,
        "alerts": cmd_alerts,
        "resolve": cmd_resolve,
        "resume": cmd_resume,
        "ack-stale": cmd_ack_stale,
        "why-halted": cmd_why_halted,
        "report": cmd_report,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
