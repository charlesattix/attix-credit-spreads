"""
sentinel/daily_report.py — Automated EOD health report.

Runs all gates for all experiments at 4:30 PM ET, compiles results into
a summary, and sends via Telegram. Stores in sentinel.db for history.

Includes:
  - Equity per experiment (from dashboard_export.json)
  - Drawdown tracking
  - Trades today
  - Regime calls + signal votes
  - Full gate audit results with health scores
  - Alert escalation for unresolved alerts >24h

Usage:
    python -m sentinel.daily_report            # Run now
    python -m sentinel.daily_report --dry-run  # Print, don't send
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_EXPORT = _PROJECT_ROOT / "data" / "dashboard_export.json"


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _load_dashboard_financial() -> Dict[str, dict]:
    """Load per-experiment financial data from dashboard_export.json."""
    if not _DASHBOARD_EXPORT.exists():
        return {}
    try:
        with open(_DASHBOARD_EXPORT) as f:
            data = json.load(f)
        result = {}
        starting = data.get("starting_equity", 100_000.0)
        for exp in data.get("experiments", []):
            eid = exp.get("id")
            if not eid:
                continue
            alp = exp.get("alpaca", {})
            result[eid] = {
                "equity": alp.get("equity", 0),
                "unrealized_pl": alp.get("unrealized_pl", 0),
                "day_pl": alp.get("day_pl", 0),
                "portfolio_value": alp.get("portfolio_value", 0),
                "cash": alp.get("cash", 0),
                "positions": len(alp.get("positions", [])),
                "starting_equity": starting,
                "pnl_pct": round((alp.get("equity", 0) / starting - 1) * 100, 1) if starting else 0,
            }
        return result
    except Exception as e:
        logger.error("Failed to load dashboard export: %s", e)
        return {}


def _count_trades_today(exp_id: str) -> int:
    """Count trades opened or closed today for an experiment."""
    try:
        from sentinel.orchestrator import _PROJECT_ROOT
        import sqlite3

        # Try common DB paths
        numeric = exp_id.removeprefix("EXP-").lower().replace("-", "")
        db_path = _PROJECT_ROOT / f"data/pilotai_exp{numeric}.db"
        if not db_path.exists():
            db_path = _PROJECT_ROOT / f"data/pilotai_{exp_id.lower().replace('-', '')}.db"
        if not db_path.exists():
            return 0

        conn = sqlite3.connect(str(db_path))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE entry_date LIKE ? OR exit_date LIKE ?",
            (f"{today}%", f"{today}%"),
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def _get_unresolved_alerts() -> List[dict]:
    """Get unresolved alerts from sentinel.db with age information."""
    try:
        from sentinel.history import SentinelDB
        db = SentinelDB()
        alerts = db.get_active_alerts()
        now = datetime.now(timezone.utc)
        for a in alerts:
            try:
                alert_time = datetime.fromisoformat(
                    a["alert_time"].replace("Z", "+00:00")
                )
                if alert_time.tzinfo is None:
                    alert_time = alert_time.replace(tzinfo=timezone.utc)
                age_hours = (now - alert_time).total_seconds() / 3600
                a["age_hours"] = round(age_hours, 1)
            except Exception:
                a["age_hours"] = None
        return alerts
    except Exception as e:
        logger.error("Failed to load unresolved alerts: %s", e)
        return []


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_daily_report(
    *,
    run_audit: bool = True,
) -> str:
    """
    Generate the full daily health report as HTML for Telegram.

    If run_audit=True, runs the full orchestrator audit first.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Financial data from main dashboard
    financials = _load_dashboard_financial()

    # Run orchestrator audit
    audits = []
    if run_audit:
        try:
            from sentinel.orchestrator import audit_all_experiments
            audits = audit_all_experiments(halt_on_critical=False)
        except Exception as e:
            logger.error("Orchestrator audit failed: %s", e)

    # Unresolved alerts
    unresolved = _get_unresolved_alerts()
    stale_alerts = [a for a in unresolved if (a.get("age_hours") or 0) > 24]

    # Build report
    total_equity = sum(f.get("equity", 0) for f in financials.values())
    total_day_pl = sum(f.get("day_pl", 0) for f in financials.values())
    starting_eq = next(iter(financials.values()), {}).get("starting_equity", 100_000)
    total_starting = starting_eq * len(financials) if financials else 600_000
    total_pnl_pct = round((total_equity / total_starting - 1) * 100, 1) if total_starting else 0

    avg_score = round(sum(a.health_score for a in audits) / len(audits)) if audits else 0
    halted_count = sum(1 for a in audits if a.halted)

    # Header
    if halted_count:
        header = f"\U0001f6a8 <b>SENTINEL EOD — {halted_count} HALTED</b>"
    elif stale_alerts:
        header = f"\u26a0\ufe0f <b>SENTINEL EOD — {len(stale_alerts)} stale alert(s)</b>"
    elif avg_score >= 80:
        header = "\u2705 <b>SENTINEL EOD — All Clear</b>"
    else:
        header = f"\u26a0\ufe0f <b>SENTINEL EOD — Avg Score {avg_score}</b>"

    lines = [header, f"<i>{now_str}</i>", ""]

    # --- Portfolio summary ---
    day_emoji = "\U0001f4c8" if total_day_pl >= 0 else "\U0001f4c9"
    lines.append("<b>Portfolio Summary</b>")
    lines.append(f"  Total Equity: <b>${total_equity:,.0f}</b> ({total_pnl_pct:+.1f}%)")
    lines.append(f"  Day P&L: {day_emoji} ${total_day_pl:+,.0f}")
    lines.append("")

    # --- Per-experiment ---
    lines.append("<b>Experiments</b>")
    for eid in sorted(financials.keys()):
        fin = financials[eid]
        equity = fin.get("equity", 0)
        day_pl = fin.get("day_pl", 0)
        pnl_pct = fin.get("pnl_pct", 0)
        positions = fin.get("positions", 0)
        trades_today = _count_trades_today(eid)

        # Health score from audit
        audit = next((a for a in audits if a.experiment_id == eid), None)
        score = audit.health_score if audit else None
        score_str = f" · Score {score}" if score is not None else ""

        day_icon = "\U0001f4c8" if day_pl >= 0 else "\U0001f4c9"
        health_icon = "\U0001f7e2" if (score is None or score >= 80) else ("\u26a0\ufe0f" if score >= 50 else "\U0001f534")

        lines.append(
            f"  {health_icon} <b>{eid}</b>: ${equity:,.0f} ({pnl_pct:+.1f}%) "
            f"· {day_icon} ${day_pl:+,.0f} · {positions}p · {trades_today}t{score_str}"
        )

        # Show failures/warnings from audit
        if audit:
            for o in audit.failures:
                lines.append(f"      \U0001f534 [{o.gate_id}] {o.message}")
            for o in audit.warnings[:3]:  # Limit to 3 warnings per exp
                lines.append(f"      \u26a0\ufe0f [{o.gate_id}] {o.message}")

    lines.append("")

    # --- Unresolved alerts ---
    if unresolved:
        lines.append(f"<b>Unresolved Alerts ({len(unresolved)})</b>")
        for a in unresolved[:10]:
            age = a.get("age_hours")
            age_str = f"{age:.0f}h" if age else "?"
            sev = a.get("severity", "?").upper()[:4]
            exp = a.get("experiment_id", "—")
            msg = a.get("message", "—")[:60]
            stale_flag = " \U0001f525" if age and age > 24 else ""
            lines.append(f"  [{sev}] {exp}: {msg} ({age_str} old){stale_flag}")
        if len(unresolved) > 10:
            lines.append(f"  <i>... and {len(unresolved) - 10} more</i>")
        lines.append("")

    # --- Stale alert escalation ---
    if stale_alerts:
        lines.append(f"\U0001f525 <b>{len(stale_alerts)} alert(s) unresolved >24h — needs attention</b>")
        lines.append("")

    # --- Footer ---
    lines.append(f"<i>Avg health score: {avg_score} · {len(audits)} experiments audited</i>")

    return "\n".join(lines)


def send_daily_report(report: str) -> bool:
    """Send the daily report via Telegram."""
    try:
        from shared.telegram_alerts import send_message
        return send_message(report, parse_mode="HTML")
    except Exception as e:
        logger.error("Failed to send daily report: %s", e)
        return False


def store_daily_report(report: str) -> None:
    """Store the daily report text in sentinel.db as a system snapshot."""
    try:
        from sentinel.history import SentinelDB
        db = SentinelDB()
        db.record_snapshot(
            "__SYSTEM__",
            notes=f"daily_report:{report[:500]}",
        )
    except Exception as e:
        logger.error("Failed to store daily report: %s", e)


def run_daily_report(*, dry_run: bool = False) -> bool:
    """
    Generate and dispatch the daily health report.

    Returns True if report was generated and sent successfully.
    """
    logger.info("Generating daily health report...")
    report = generate_daily_report(run_audit=True)

    if dry_run:
        print(report)
        return True

    store_daily_report(report)
    sent = send_daily_report(report)

    if sent:
        logger.info("Daily report sent successfully")
    else:
        logger.error("Daily report delivery failed")
        # Write to file as fallback
        fallback = _PROJECT_ROOT / "data" / "daily_report_fallback.txt"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(report)
        logger.info("Report saved to %s", fallback)

    return sent


# ---------------------------------------------------------------------------
# Weekly unresolved alert digest
# ---------------------------------------------------------------------------


def generate_weekly_digest() -> Optional[str]:
    """
    Generate a weekly digest of unresolved alerts with time-to-resolution stats.

    Returns None if no unresolved alerts exist.
    """
    unresolved = _get_unresolved_alerts()
    if not unresolved:
        return None

    try:
        from sentinel.history import SentinelDB
        db = SentinelDB()
        # Get recently resolved alerts for TTR stats
        all_alerts = db.get_all_alerts(limit=200)
        resolved = [a for a in all_alerts if a.get("resolved")]
    except Exception:
        resolved = []

    # Compute time-to-resolution for resolved alerts
    ttr_hours = []
    for a in resolved:
        try:
            opened = datetime.fromisoformat(a["alert_time"].replace("Z", "+00:00"))
            closed = datetime.fromisoformat(a["resolved_at"].replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            if closed.tzinfo is None:
                closed = closed.replace(tzinfo=timezone.utc)
            ttr_hours.append((closed - opened).total_seconds() / 3600)
        except Exception:
            pass

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "\U0001f4cb <b>SENTINEL WEEKLY DIGEST</b>",
        f"<i>{now_str}</i>",
        "",
        f"<b>Unresolved Alerts: {len(unresolved)}</b>",
    ]

    by_severity = {}
    for a in unresolved:
        sev = a.get("severity", "unknown")
        by_severity.setdefault(sev, []).append(a)

    for sev in ["critical", "warning", "info"]:
        alerts = by_severity.get(sev, [])
        if alerts:
            lines.append(f"  {sev.upper()}: {len(alerts)}")
            for a in alerts[:5]:
                age = a.get("age_hours", 0)
                exp = a.get("experiment_id", "—")
                msg = a.get("message", "—")[:50]
                lines.append(f"    · {exp}: {msg} ({age:.0f}h)")

    if ttr_hours:
        avg_ttr = sum(ttr_hours) / len(ttr_hours)
        lines.append("")
        lines.append(f"<b>Resolution Stats (last {len(resolved)} alerts)</b>")
        lines.append(f"  Avg TTR: {avg_ttr:.1f}h")
        lines.append(f"  Fastest: {min(ttr_hours):.1f}h")
        lines.append(f"  Slowest: {max(ttr_hours):.1f}h")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="SENTINEL Daily Health Report")
    parser.add_argument("--dry-run", action="store_true", help="Print report, don't send")
    parser.add_argument("--weekly", action="store_true", help="Generate weekly digest instead")
    args = parser.parse_args()

    if args.weekly:
        digest = generate_weekly_digest()
        if digest:
            print(digest)
        else:
            print("No unresolved alerts — nothing to report.")
        sys.exit(0)

    ok = run_daily_report(dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
