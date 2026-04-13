"""
SENTINEL — HTML Report Generator

Produces two report types:
  1. Daily summary  — all experiments, portfolio risk, open alerts
  2. Experiment history — full timeline for one experiment

Styling matches Carlos's existing daily_report.py aesthetic:
  white background, system font stack, blue h1 underline, green/red P&L.

Usage
-----
  from sentinel.report import generate_daily_html, generate_history_html
  html = generate_daily_html(summary, portfolio_risk, health_results)
  html = generate_history_html(timeline)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Shared CSS (matches Carlos's daily_report.py style)
# ─────────────────────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #fff; color: #1a1a1a; margin: 0; padding: 20px; font-size: 14px;
}
h1 {
    color: #1a1a1a; border-bottom: 3px solid #2563eb; padding-bottom: 8px;
    margin-top: 0; font-size: 22px;
}
h2 {
    color: #374151; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px;
    margin-top: 28px; font-size: 16px;
}
h3 { color: #4b5563; margin-top: 20px; font-size: 14px; }
.meta { color: #6b7280; font-size: 13px; margin-bottom: 16px; }
table {
    border-collapse: collapse; width: 100%; margin: 12px 0 20px 0;
    font-size: 13px;
}
th {
    background: #f3f4f6; color: #374151; text-align: left;
    padding: 8px 10px; border-bottom: 2px solid #d1d5db; font-weight: 600;
}
td { padding: 6px 10px; border-bottom: 1px solid #e5e7eb; }
tr:hover { background: #f9fafb; }
.profit  { color: #059669; font-weight: 600; }
.loss    { color: #dc2626; font-weight: 600; }
.neutral { color: #6b7280; }
.warn    { color: #d97706; font-weight: 600; }
.ok      { color: #059669; }
.grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; margin: 16px 0;
}
.card {
    background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px;
    padding: 12px 16px;
}
.card .label {
    font-size: 11px; color: #6b7280; text-transform: uppercase;
    letter-spacing: 0.5px;
}
.card .value { font-size: 20px; font-weight: 700; margin-top: 4px; }
.badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700; text-transform: uppercase;
}
.badge-ok         { background: #d1fae5; color: #065f46; }
.badge-warn       { background: #fef3c7; color: #92400e; }
.badge-critical   { background: #fee2e2; color: #991b1b; }
.badge-grandf     { background: #e0e7ff; color: #3730a3; }
.badge-halted     { background: #fee2e2; color: #991b1b; }
.badge-active     { background: #d1fae5; color: #065f46; }
.alert-critical   { background: #fef2f2; border-left: 4px solid #dc2626; padding: 8px 12px; margin: 6px 0; border-radius: 4px; }
.alert-warning    { background: #fffbeb; border-left: 4px solid #f59e0b; padding: 8px 12px; margin: 6px 0; border-radius: 4px; }
.alert-info       { background: #eff6ff; border-left: 4px solid #3b82f6; padding: 8px 12px; margin: 6px 0; border-radius: 4px; }
.section-divider  { border: none; border-top: 1px solid #e5e7eb; margin: 24px 0; }
.footer {
    margin-top: 30px; padding-top: 12px; border-top: 1px solid #e5e7eb;
    font-size: 11px; color: #9ca3af;
}
pre { background: #f3f4f6; padding: 10px; border-radius: 4px; font-size: 12px; overflow-x: auto; }
"""


# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────


def _pnl_class(val: Optional[float]) -> str:
    if val is None:
        return "neutral"
    return "profit" if val > 0 else "loss" if val < 0 else "neutral"


def _fmt_money(val: Optional[float], prefix: str = "$") -> str:
    if val is None:
        return "—"
    sign = "+" if val > 0 else ""
    return f"{sign}{prefix}{abs(val):,.2f}" if val < 0 else f"{sign}{prefix}{val:,.2f}"


def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "—"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.1f}%"


def _fmt_dt(dt_str: Optional[str]) -> str:
    if not dt_str:
        return "—"
    return str(dt_str)[:19].replace("T", " ")


def _severity_badge(severity: str) -> str:
    cls = {"critical": "badge-critical", "warning": "badge-warn", "info": "badge-ok"}.get(
        severity, "badge-warn"
    )
    return f'<span class="badge {cls}">{severity}</span>'


def _status_badge(status: str, grandfathered: bool = False) -> str:
    if grandfathered:
        return '<span class="badge badge-grandf">GRANDFATHERED</span>'
    m = {
        "active": "badge-active",
        "paper_trading": "badge-active",
        "halted": "badge-halted",
        "paused": "badge-warn",
        "retired": "neutral",
    }
    cls = m.get(status.lower(), "badge-warn")
    return f'<span class="badge {cls}">{status.upper()}</span>'


def _cert_badge(cert: Optional[Dict]) -> str:
    if not cert:
        return '<span class="badge badge-critical">NOT CERTIFIED</span>'
    if cert.get("grandfathered"):
        return '<span class="badge badge-grandf">GRANDFATHERED</span>'
    gates = cert.get("gates_passed", 0)
    eq = cert.get("equivalence_days", 0)
    return f'<span class="badge badge-active">CERTIFIED ({gates}/10 gates, {eq}/5 days)</span>'


# ─────────────────────────────────────────────────────────────────────────────
# Daily summary report
# ─────────────────────────────────────────────────────────────────────────────


def generate_daily_html(
    summary: Dict[str, Any],
    portfolio_risk: Optional[Any] = None,   # PortfolioRisk from sentinel.portfolio
    health_results: Optional[List[Any]] = None,  # List[ExperimentHealth] from sentinel.monitor
    sentinel_state: Optional[Dict] = None,  # dict from sentinel_state.json
    registry: Optional[Dict] = None,        # experiments/registry.json content
) -> str:
    """
    Generate the daily SENTINEL summary HTML report.

    Parameters that come from other sentinel modules (portfolio_risk,
    health_results, sentinel_state) are all optional — report degrades
    gracefully if they are not available.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    experiments: Dict[str, Any] = summary.get("experiments", {})
    crit_alerts: List[Dict] = summary.get("critical_alerts", [])
    warn_alerts: List[Dict] = summary.get("warning_alerts", [])
    total_alerts: int = summary.get("total_active_alerts", 0)

    # ── Build health lookup (exp_id → ExperimentHealth) ────────────────────
    health_map: Dict[str, Any] = {}
    if health_results:
        for h in health_results:
            health_map[h.exp_id] = h

    # ── Aggregate totals ───────────────────────────────────────────────────
    total_equity = 0.0
    active_count = 0
    for exp_id, edata in experiments.items():
        snap = edata.get("latest_snapshot")
        if snap and snap.get("equity"):
            total_equity += snap["equity"]
        if edata.get("certificate"):
            active_count += 1

    # ── State map (exp_id → sentinel state entry) ──────────────────────────
    state_map: Dict[str, Dict] = {}
    if sentinel_state:
        for k, v in sentinel_state.items():
            if isinstance(v, dict):
                state_map[k] = v

    parts: List[str] = []

    # ── HEAD ───────────────────────────────────────────────────────────────
    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>🛡️ SENTINEL Daily — {report_date}</title>
  <style>{_CSS}</style>
</head>
<body>
<h1>🛡️ SENTINEL Daily Report</h1>
<p class="meta">
  {report_date}
  &bull; <b>{active_count}</b> certified experiments
  &bull; <b>{total_alerts}</b> open alerts
</p>
""")

    # ── Summary cards ──────────────────────────────────────────────────────
    alert_cls = "loss" if crit_alerts else ("warn" if warn_alerts else "profit")
    alert_label = (
        f"{len(crit_alerts)} CRITICAL" if crit_alerts
        else (f"{len(warn_alerts)} warning" if warn_alerts else "0 — all clear")
    )
    parts.append('<div class="grid">')
    for label, value, cls in [
        ("Total Paper Equity", f"${total_equity:,.0f}", "neutral"),
        ("Active Experiments", str(len(experiments)), "neutral"),
        ("Open Alerts", alert_label, alert_cls),
    ]:
        parts.append(
            f'<div class="card"><div class="label">{label}</div>'
            f'<div class="value {cls}">{value}</div></div>'
        )

    # API health summary
    api_ok = sum(1 for h in health_map.values() if getattr(h, "api_ok", False))
    api_total = len(health_map)
    if api_total:
        api_cls = "profit" if api_ok == api_total else "loss"
        parts.append(
            f'<div class="card"><div class="label">API Health</div>'
            f'<div class="value {api_cls}">{api_ok}/{api_total} OK</div></div>'
        )

    parts.append("</div>")

    # ── Critical alerts banner ─────────────────────────────────────────────
    if crit_alerts:
        parts.append("<h2>🚨 Critical Alerts</h2>")
        for a in crit_alerts:
            exp_tag = f"[{a['experiment_id']}] " if a.get("experiment_id") else ""
            parts.append(
                f'<div class="alert-critical">'
                f'<b>{exp_tag}{a["message"]}</b>'
                f' <span class="meta">— {_fmt_dt(a["alert_time"])}</span>'
                f"</div>"
            )

    if warn_alerts:
        parts.append("<h2>⚠️ Warnings</h2>")
        for a in warn_alerts:
            exp_tag = f"[{a['experiment_id']}] " if a.get("experiment_id") else ""
            parts.append(
                f'<div class="alert-warning">'
                f'{exp_tag}{a["message"]}'
                f' <span class="meta">— {_fmt_dt(a["alert_time"])}</span>'
                f"</div>"
            )

    # ── Experiment status table ────────────────────────────────────────────
    parts.append("<h2>📊 Experiment Status</h2>")
    parts.append(
        "<table>"
        "<tr><th>Experiment</th><th>Status</th><th>Certification</th>"
        "<th>Equity</th><th>Day P&L</th><th>Positions</th>"
        "<th>API</th><th>Issues</th></tr>"
    )

    for exp_id in sorted(experiments.keys()):
        edata = experiments[exp_id]
        snap = edata.get("latest_snapshot") or {}
        cert = edata.get("certificate")
        exp_alerts = edata.get("active_alerts", [])
        health = health_map.get(exp_id)

        state_entry = state_map.get(exp_id, {})
        exp_status = state_entry.get("status", "active")

        equity = snap.get("equity")
        day_pnl = snap.get("day_pnl")
        positions = snap.get("open_positions", "—")
        api_ok_str = "✅" if getattr(health, "api_ok", None) is True else (
            "❌" if health is not None else "—"
        )

        issue_count = len(getattr(health, "issues", []))
        issues_str = (
            f'<span class="loss">{issue_count} issue(s)</span>' if issue_count
            else '<span class="ok">—</span>'
        )
        name = (registry or {}).get("experiments", {}).get(exp_id, {}).get("name", exp_id)

        parts.append(
            f"<tr>"
            f"<td><b>{exp_id}</b><br><span class='neutral' style='font-size:11px'>{name}</span></td>"
            f"<td>{_status_badge(exp_status)}</td>"
            f"<td>{_cert_badge(cert)}</td>"
            f"<td>{'${:,.0f}'.format(equity) if equity else '—'}</td>"
            f"<td class='{_pnl_class(day_pnl)}'>{_fmt_money(day_pnl)}</td>"
            f"<td>{positions}</td>"
            f"<td>{api_ok_str}</td>"
            f"<td>{issues_str}</td>"
            f"</tr>"
        )

    parts.append("</table>")

    # ── Portfolio risk ─────────────────────────────────────────────────────
    if portfolio_risk is not None:
        parts.append("<h2>📈 Portfolio Risk</h2>")
        pr = portfolio_risk

        parts.append('<div class="grid">')
        parts.append(
            f'<div class="card"><div class="label">Total Open Positions</div>'
            f'<div class="value neutral">{pr.total_open_positions}</div></div>'
        )
        parts.append(
            f'<div class="card"><div class="label">Tickers</div>'
            f'<div class="value neutral">{len(pr.tickers)}</div></div>'
        )
        if pr.expiration_clusters:
            cluster_warn = pr.expiration_clusters[0]
            parts.append(
                f'<div class="card"><div class="label">Top Exp. Cluster</div>'
                f'<div class="value warn">{cluster_warn[0]} ({cluster_warn[1]} pos)</div></div>'
            )
        parts.append("</div>")

        if pr.tickers:
            parts.append(
                "<table><tr><th>Ticker</th><th>Experiments</th>"
                "<th>Contracts</th><th>Bull</th><th>Bear</th>"
                "<th>IC</th><th>Expirations</th></tr>"
            )
            for ticker, te in sorted(pr.tickers.items()):
                exps = ", ".join(te.experiments)
                exps_str = ", ".join(str(e)[:10] for e in te.expirations[:3])
                if len(te.expirations) > 3:
                    exps_str += f" +{len(te.expirations)-3}"
                conc_cls = " class='warn'" if ticker in pr.concentrated_tickers else ""
                parts.append(
                    f"<tr{conc_cls}>"
                    f"<td><b>{ticker}</b></td><td>{exps}</td>"
                    f"<td>{te.total_contracts}</td>"
                    f"<td class='profit'>{te.bull_count}</td>"
                    f"<td class='loss'>{te.bear_count}</td>"
                    f"<td class='neutral'>{te.ic_count}</td>"
                    f"<td style='font-size:11px'>{exps_str}</td>"
                    f"</tr>"
                )
            parts.append("</table>")

        if pr.directional_conflicts:
            parts.append("<p class='warn'>⚠️ Directional conflicts: " +
                         "; ".join(pr.directional_conflicts) + "</p>")

        if pr.concentrated_tickers:
            parts.append("<p class='warn'>⚠️ Concentrated tickers (3+ experiments): " +
                         ", ".join(pr.concentrated_tickers) + "</p>")

    # ── Orphan/Ghost/Stale detections ──────────────────────────────────────
    if health_results:
        orphans = [h for h in health_results if h.is_orphan]
        ghosts = [h for h in health_results if h.is_ghost]
        stale = [h for h in health_results if h.is_stale]
        dupes = [h for h in health_results if h.is_duplicate]

        if orphans or ghosts or stale or dupes:
            parts.append("<h2>⚠️ Account Issues</h2>")
            for h in orphans:
                eq_str = f"${h.equity:,.0f}" if h.equity else "?"
                parts.append(
                    f'<div class="alert-warning">🏚️ ORPHAN: '
                    f'{h.exp_id} ({h.account_id}) — retired but holds {eq_str}</div>'
                )
            for h in ghosts:
                parts.append(
                    f'<div class="alert-critical">👻 GHOST: '
                    f'{h.exp_id} ({h.account_id}) — active but unreachable: '
                    + "; ".join(h.issues) + "</div>"
                )
            for h in stale:
                parts.append(
                    f'<div class="alert-warning">🕰️ STALE: '
                    f'{h.exp_id} ({h.account_id}): '
                    + "; ".join(h.issues) + "</div>"
                )
            for h in dupes:
                parts.append(
                    f'<div class="alert-critical">🔁 DUPLICATE ACCOUNT: '
                    f'{h.exp_id}: '
                    + "; ".join(h.issues) + "</div>"
                )

    # ── Footer ─────────────────────────────────────────────────────────────
    parts.append(
        f'<hr class="section-divider">'
        f'<div class="footer">Generated {now_str} &bull; 🛡️ SENTINEL v1.0 &bull; PilotAI</div>'
        f"</body></html>"
    )

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Per-experiment history report
# ─────────────────────────────────────────────────────────────────────────────


def generate_history_html(
    timeline: Dict[str, Any],
    registry_entry: Optional[Dict] = None,
) -> str:
    """
    Generate a per-experiment history HTML report.

    *timeline* is the dict returned by SentinelDB.get_experiment_timeline().
    *registry_entry* is the experiment's entry from experiments/registry.json.
    """
    exp_id: str = timeline["experiment_id"]
    cert: Optional[Dict] = timeline.get("certificate")
    snapshots: List[Dict] = timeline.get("snapshots", [])
    changes: List[Dict] = timeline.get("config_changes", [])
    alerts: List[Dict] = timeline.get("alerts", [])

    reg = registry_entry or {}
    exp_name = reg.get("name", exp_id)
    ticker = reg.get("ticker", "?")
    live_since = reg.get("live_since", "?")
    creator = reg.get("created_by", "?")
    account_id = reg.get("account_id", "?")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts: List[str] = []

    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>🛡️ SENTINEL — {exp_id} History</title>
  <style>{_CSS}</style>
</head>
<body>
<h1>🛡️ {exp_id} — {exp_name}</h1>
<p class="meta">
  Ticker: <b>{ticker}</b>
  &bull; Account: <code>{account_id}</code>
  &bull; Live since: {live_since}
  &bull; Creator: {creator}
  &bull; Certification: {_cert_badge(cert)}
</p>
""")

    # ── Certification block ─────────────────────────────────────────────────
    if cert:
        parts.append("<h2>📜 Deployment Certificate</h2>")
        grandf = bool(cert.get("grandfathered"))
        parts.append('<table style="max-width:600px">')
        for label, val in [
            ("Status", "GRANDFATHERED (pre-SENTINEL)" if grandf else "CERTIFIED"),
            ("Certified at", _fmt_dt(cert.get("certified_at"))),
            ("Fingerprint", f'<code>{cert.get("fingerprint","—")}</code>'),
            ("Gates passed", f'{cert.get("gates_passed",0)}/10'),
            ("Equivalence days", f'{cert.get("equivalence_days",0)}/5'),
            ("Certified by", cert.get("certified_by", "—")),
            ("Notes", cert.get("notes", "—") or "—"),
        ]:
            parts.append(f"<tr><td><b>{label}</b></td><td>{val}</td></tr>")
        parts.append("</table>")
    else:
        parts.append(
            '<p class="alert-critical">⛔ No deployment certificate found — experiment not onboarded.</p>'
        )

    # ── Equity snapshots ───────────────────────────────────────────────────
    parts.append("<h2>💰 Equity Snapshots</h2>")
    if snapshots:
        parts.append(
            "<table><tr><th>Date / Time</th><th>Equity</th><th>Day P&L</th>"
            "<th>Total P&L</th><th>Positions</th><th>Trades</th>"
            "<th>Win Rate</th><th>API</th></tr>"
        )
        for s in snapshots[:60]:   # show last 60 rows
            eq = s.get("equity")
            dpnl = s.get("day_pnl")
            tpnl = s.get("total_pnl")
            wr = s.get("win_rate")
            api_s = s.get("api_status", "ok")
            api_cls = "ok" if api_s == "ok" else "loss"
            parts.append(
                f"<tr>"
                f"<td>{_fmt_dt(s.get('snapshot_time'))}</td>"
                f"<td>{'${:,.0f}'.format(eq) if eq else '—'}</td>"
                f"<td class='{_pnl_class(dpnl)}'>{_fmt_money(dpnl)}</td>"
                f"<td class='{_pnl_class(tpnl)}'>{_fmt_money(tpnl)}</td>"
                f"<td>{s.get('open_positions','—')}</td>"
                f"<td>{s.get('total_trades','—')}</td>"
                f"<td>{'%.1f%%' % wr if wr is not None else '—'}</td>"
                f"<td class='{api_cls}'>{api_s}</td>"
                f"</tr>"
            )
        parts.append("</table>")
        if len(snapshots) > 60:
            parts.append(
                f'<p class="meta">{len(snapshots) - 60} earlier snapshots not shown.</p>'
            )
    else:
        parts.append("<p class='neutral'>No snapshots recorded yet.</p>")

    # ── Config change log ──────────────────────────────────────────────────
    parts.append("<h2>🔧 Config Change Log</h2>")
    if changes:
        parts.append(
            "<table><tr><th>Date</th><th>Field</th><th>Old Value</th>"
            "<th>New Value</th><th>Approved By</th><th>Reason</th></tr>"
        )
        for c in changes:
            appr = c.get("approved_by") or ""
            reason = c.get("approval_reason") or ""
            appr_cls = "ok" if appr else "loss"
            appr_str = f'<span class="{appr_cls}">{appr or "UNAUTHORIZED"}</span>'
            parts.append(
                f"<tr>"
                f"<td>{_fmt_dt(c.get('changed_at'))}</td>"
                f"<td><code>{c.get('field_name','?')}</code></td>"
                f"<td><code>{(c.get('old_value') or '—')[:60]}</code></td>"
                f"<td><code>{(c.get('new_value') or '—')[:60]}</code></td>"
                f"<td>{appr_str}</td>"
                f"<td>{reason[:80] or '—'}</td>"
                f"</tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='neutral'>No config changes recorded.</p>")

    # ── Alert history ──────────────────────────────────────────────────────
    parts.append("<h2>🔔 Alert History</h2>")
    if alerts:
        parts.append(
            "<table><tr><th>Date</th><th>Severity</th><th>Message</th>"
            "<th>Resolved</th><th>Resolved By</th><th>Note</th></tr>"
        )
        for a in alerts:
            res = bool(a.get("resolved"))
            res_str = (
                f'<span class="ok">✅ {_fmt_dt(a.get("resolved_at"))}</span>'
                if res
                else '<span class="loss">OPEN</span>'
            )
            parts.append(
                f"<tr>"
                f"<td>{_fmt_dt(a.get('alert_time'))}</td>"
                f"<td>{_severity_badge(a.get('severity','info'))}</td>"
                f"<td>{a.get('message','')}</td>"
                f"<td>{res_str}</td>"
                f"<td>{a.get('resolved_by','—') or '—'}</td>"
                f"<td>{(a.get('resolution_note') or '—')[:80]}</td>"
                f"</tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='neutral'>No alerts recorded.</p>")

    # ── Footer ─────────────────────────────────────────────────────────────
    parts.append(
        f'<hr class="section-divider">'
        f'<div class="footer">Generated {now_str} &bull; 🛡️ SENTINEL v1.0 &bull; PilotAI</div>'
        f"</body></html>"
    )

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Telegram-friendly text summary (subset of daily report)
# ─────────────────────────────────────────────────────────────────────────────


def generate_telegram_daily(
    summary: Dict[str, Any],
    portfolio_risk: Optional[Any] = None,
    health_results: Optional[List[Any]] = None,
    registry: Optional[Dict] = None,
) -> str:
    """
    Generate a concise Telegram text message for the daily health run.

    Matches the style shown in the SENTINEL proposal:
      🛡️ SENTINEL DAILY — Apr 13, 2026
      Active: 6 experiments | Equity: $568,466
      Guards: ✅ All configs match | ✅ All APIs healthy
      ...
    """
    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
    experiments = summary.get("experiments", {})
    crit = summary.get("critical_alerts", [])
    warn = summary.get("warning_alerts", [])

    total_equity = sum(
        (edata.get("latest_snapshot") or {}).get("equity", 0) or 0
        for edata in experiments.values()
    )
    n_active = len(experiments)

    # API health
    api_ok_all = all(getattr(h, "api_ok", False) for h in (health_results or []))
    api_str = "✅ All APIs healthy" if api_ok_all else "❌ API issues detected"

    # Config drift (no critical alerts from drift = all clear)
    drift_crit = [a for a in crit if "drift" in a.get("message", "").lower()]
    drift_str = "✅ All configs match" if not drift_crit else f"❌ {len(drift_crit)} drift alert(s)"

    lines = [
        f"🛡️ <b>SENTINEL DAILY — {now_str}</b>",
        "",
        f"Active: {n_active} experiments | Equity: ${total_equity:,.0f}",
        f"Guards: {drift_str} | {api_str}",
    ]

    if crit:
        lines += ["", "🚨 <b>Critical:</b>"]
        for a in crit[:3]:
            exp_tag = f"[{a['experiment_id']}] " if a.get("experiment_id") else ""
            lines.append(f"  • {exp_tag}{a['message'][:80]}")
        if len(crit) > 3:
            lines.append(f"  …+{len(crit)-3} more")
    elif warn:
        lines += ["", "⚠️ <b>Warnings:</b>"]
        for a in warn[:3]:
            exp_tag = f"[{a['experiment_id']}] " if a.get("experiment_id") else ""
            lines.append(f"  • {exp_tag}{a['message'][:80]}")
    else:
        lines.append("Drift: 0 | Alerts: 0 critical, 0 warning")

    # Portfolio risk section
    if portfolio_risk and portfolio_risk.concentrated_tickers:
        lines += [
            "",
            "📊 <b>Portfolio Risk:</b>",
        ]
        for ticker, te in portfolio_risk.tickers.items():
            n_exps = len(te.experiments)
            dir_str = f"{'BULL' if te.bull_count > te.bear_count else 'BEAR' if te.bear_count > te.bull_count else 'MIXED'}"
            lines.append(
                f"  {ticker}: {n_exps} exp, {dir_str}, {te.total_contracts} contracts"
            )

        if portfolio_risk.expiration_clusters:
            top = portfolio_risk.expiration_clusters[0]
            lines.append(f"  ⚠️ Expiration cluster: {top[0]} ({top[1]} positions)")

    # Per-experiment summary
    reg_exps = (registry or {}).get("experiments", {})
    lines += ["", "📈 <b>Experiments:</b>"]
    for exp_id in sorted(experiments.keys()):
        edata = experiments[exp_id]
        snap = (edata.get("latest_snapshot") or {})
        eq = snap.get("equity")
        dpnl = snap.get("day_pnl")
        exp_alerts = edata.get("active_alerts", [])

        eq_str = f"${eq:,.0f}" if eq else "—"
        pnl_str = (
            (f"+${dpnl:.0f}" if dpnl >= 0 else f"-${abs(dpnl):.0f}")
            if dpnl is not None else ""
        )
        alert_str = f" ⚠️{len(exp_alerts)}" if exp_alerts else ""
        name = reg_exps.get(exp_id, {}).get("name", "")
        lines.append(f"  {exp_id} ({name[:12]}): {eq_str} {pnl_str}{alert_str}")

    return "\n".join(lines)
