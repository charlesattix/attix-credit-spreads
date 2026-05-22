"""
html.py — Live dashboard HTML generation.
"""

from __future__ import annotations
import html as _html
import logging
from datetime import datetime, timezone
from .data import STARTING_EQUITY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Watchdog status — populated by app.py when the external watchdog POSTs data
# ---------------------------------------------------------------------------

# app.py replaces this reference with the live dict after each POST.
_watchdog_status: dict = {}


def _render_watchdog_banner() -> str:
    """Render a system-status banner using the latest watchdog status.

    Returns an HTML string (may be empty string if dismissed via JS).
    The banner is injected immediately after <body> on every page.
    """
    status = _watchdog_status

    if not status:
        # No data yet — show yellow "not checked in" banner
        return _watchdog_banner_html(
            color="yellow",
            icon="⚠️",
            message="Watchdog has not checked in yet",
            last_check_iso=None,
        )

    last_check_iso: str | None = status.get("last_check")
    overall = status.get("overall", "unknown")

    # Compute minutes since last check
    minutes_ago: int | None = None
    if last_check_iso:
        try:
            last_dt = datetime.fromisoformat(last_check_iso.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - last_dt
            minutes_ago = int(delta.total_seconds() / 60)
        except Exception:
            pass

    stale = minutes_ago is not None and minutes_ago > 45

    if stale:
        return _watchdog_banner_html(
            color="yellow",
            icon="⚠️",
            message=f"Watchdog hasn't checked in",
            last_check_iso=last_check_iso,
            minutes_ago=minutes_ago,
        )

    if overall != "ok":
        # Find the first failing item to name in the banner
        down_items: list[str] = []
        for name, s in (status.get("services") or {}).items():
            if s.get("status") != "ok":
                down_items.append(name)
        for exp_id, s in (status.get("alpaca_accounts") or {}).items():
            if s.get("status") != "ok":
                down_items.append(exp_id)
        desc = ", ".join(down_items) if down_items else "unknown"
        return _watchdog_banner_html(
            color="red",
            icon="🔴",
            message=f"ALERT: {_html.escape(desc)} is DOWN",
            last_check_iso=last_check_iso,
            minutes_ago=minutes_ago,
        )

    return _watchdog_banner_html(
        color="green",
        icon="🟢",
        message="All systems operational",
        last_check_iso=last_check_iso,
        minutes_ago=minutes_ago,
    )


def _watchdog_banner_html(
    color: str,
    icon: str,
    message: str,
    last_check_iso: str | None,
    minutes_ago: int | None = None,
) -> str:
    """Build the inline-styled banner HTML."""
    if color == "green":
        bg, border, text_color = "#dcfce7", "#bbf7d0", "#166534"
    elif color == "red":
        bg, border, text_color = "#fee2e2", "#fecaca", "#991b1b"
    else:  # yellow
        bg, border, text_color = "#fef9c3", "#fde68a", "#854d0e"

    if minutes_ago is not None:
        age = f"{minutes_ago}m ago" if minutes_ago < 60 else f"{minutes_ago // 60}h {minutes_ago % 60}m ago"
        last_check_str = f" &mdash; last check: {_html.escape(age)}"
    elif last_check_iso:
        last_check_str = f" &mdash; last check: {_html.escape(last_check_iso)}"
    else:
        last_check_str = ""

    return f"""<div id="watchdog-banner" style="
        background:{bg};border-bottom:1px solid {border};color:{text_color};
        font-size:13px;font-weight:600;padding:8px 24px;
        display:flex;justify-content:space-between;align-items:center;gap:8px;
        font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <span>{icon} {message}{last_check_str}</span>
  <button onclick="document.getElementById('watchdog-banner').style.display='none'"
          style="background:transparent;border:none;cursor:pointer;font-size:16px;
                 color:{text_color};padding:0 4px;line-height:1;" title="Dismiss">&#x2715;</button>
</div>"""


# ---------------------------------------------------------------------------
# Shared navigation
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    ("/", "Overview"),
    ("/positions", "Positions"),
    ("/trades", "Trades"),
    ("/registry", "Registry"),
    ("/sentinel", "Sentinel"),
]


def _render_nav(active_path: str, right_html: str = "") -> str:
    """Render the shared top bar with navigation links."""
    links = []
    for href, label in _NAV_ITEMS:
        cls = ' class="active"' if href == active_path else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')
    nav_html = " ".join(links)
    return f"""<div class="top-bar">
  <div style="display:flex;align-items:center;gap:4px">
    <span class="brand">Attix</span>
    <span class="nav-links">{nav_html}</span>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    {right_html}
    <a href="/logout" class="logout-btn">Sign out</a>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Registry page CSS (appended to base CSS on the /registry page)
# ---------------------------------------------------------------------------
_REGISTRY_CSS = """
/* Registry page additions */
.filter-bar {
    display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
    margin-bottom: 20px;
}
.filter-btn {
    padding: 5px 14px; font-size: 12px; font-weight: 600;
    border: 1px solid #e2e8f0; border-radius: 8px;
    background: #fff; color: #64748b; cursor: pointer;
    transition: all 0.15s; font-family: inherit;
}
.filter-btn:hover { border-color: #94a3b8; color: #334155; }
.filter-btn.active { background: #0f172a; color: #f8fafc; border-color: #0f172a; }
.search-input {
    padding: 6px 14px; font-size: 13px; border: 1px solid #e2e8f0;
    border-radius: 8px; background: #fff; color: #1e293b; outline: none;
    min-width: 200px; font-family: inherit; transition: border-color 0.15s;
}
.search-input:focus { border-color: #6366f1; }

/* Status badges */
.status-badge {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.3px;
    text-transform: uppercase;
}
.status-active       { background: #dcfce7; color: #166534; }
.status-paused       { background: #fef9c3; color: #854d0e; }
.status-stopped      { background: #fee2e2; color: #991b1b; }
.status-failed       { background: #fee2e2; color: #991b1b; }
.status-retired      { background: #f1f5f9; color: #64748b; }
.status-registered   { background: #dbeafe; color: #1e40af; }
.status-configuring  { background: #e0e7ff; color: #3730a3; }
.status-completed    { background: #f1f5f9; color: #64748b; }

/* Registry table */
.reg-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.reg-table th {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.5px; color: #94a3b8; text-align: left;
    padding: 8px 12px; border-bottom: 2px solid #e2e8f0;
    position: sticky; top: 0; background: #f8fafc; z-index: 1;
}
.reg-table td {
    padding: 10px 12px; border-bottom: 1px solid #f1f5f9;
    vertical-align: top;
}
.reg-table tr { cursor: pointer; transition: background 0.1s; }
.reg-table tr:hover { background: #f8fafc; }
.reg-table tr.expanded { background: #f8fafc; }
.reg-exp-id { font-weight: 700; color: #0f172a; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
.reg-name { font-weight: 600; color: #334155; }
.reg-desc-cell { max-width: 200px; color: #64748b; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.reg-meta { font-size: 11px; color: #94a3b8; }

/* Detail panel (expandable) */
.detail-panel {
    display: none; background: #fff; border: 1px solid #e2e8f0;
    border-radius: 10px; margin: 4px 0 12px; padding: 20px 24px;
    animation: fadeIn 0.15s ease-in-out;
}
.detail-panel.open { display: block; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
.detail-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px 32px;
    font-size: 13px;
}
.detail-field { display: flex; flex-direction: column; }
.detail-label { font-size: 10px; font-weight: 700; text-transform: uppercase;
                letter-spacing: 0.5px; color: #94a3b8; margin-bottom: 2px; }
.detail-value { color: #1e293b; word-break: break-all; }
.detail-value.mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
.detail-desc { grid-column: 1 / -1; }
.detail-notes { grid-column: 1 / -1; background: #f8fafc; border-radius: 8px; padding: 12px 16px; font-size: 12px; color: #475569; line-height: 1.5; }

/* Action buttons */
.action-bar { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
.action-btn {
    padding: 6px 16px; font-size: 12px; font-weight: 600;
    border: 1px solid #e2e8f0; border-radius: 8px; cursor: pointer;
    transition: all 0.15s; font-family: inherit; background: #fff; color: #334155;
}
.action-btn:hover { border-color: #94a3b8; }
.action-btn.activate { background: #059669; color: #fff; border-color: #059669; }
.action-btn.activate:hover { background: #047857; }
.action-btn.pause { background: #d97706; color: #fff; border-color: #d97706; }
.action-btn.pause:hover { background: #b45309; }
.action-btn.stop { background: #dc2626; color: #fff; border-color: #dc2626; }
.action-btn.stop:hover { background: #b91c1c; }
.action-btn.retire { background: #64748b; color: #fff; border-color: #64748b; }
.action-btn.retire:hover { background: #475569; }
.action-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* Validate/Sync result panel */
.result-panel {
    background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
    padding: 16px 20px; margin-top: 12px; font-size: 13px;
    white-space: pre-wrap; font-family: 'SF Mono', 'Fira Code', monospace;
    max-height: 300px; overflow-y: auto; display: none;
}
.result-panel.open { display: block; }
.result-pass { color: #059669; }
.result-fail { color: #dc2626; }

@media (max-width: 800px) {
    .reg-table { font-size: 12px; }
    .detail-grid { grid-template-columns: 1fr; }
    .reg-desc-cell { display: none; }
}
"""

# ---------------------------------------------------------------------------
_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f8fafc; color: #1e293b; font-size: 14px; line-height: 1.5;
}
.top-bar {
    background: #0f172a; color: #94a3b8; font-size: 12px;
    padding: 10px 24px; display: flex; justify-content: space-between;
    align-items: center; flex-wrap: wrap; gap: 12px;
}
.logout-btn {
    color: #64748b; font-size: 12px; text-decoration: none;
    padding: 3px 10px; border: 1px solid #1e293b; border-radius: 5px;
    transition: color 0.15s, border-color 0.15s;
}
.logout-btn:hover { color: #94a3b8; border-color: #334155; }
.top-bar .brand { color: #f8fafc; font-weight: 700; font-size: 14px; margin-right: 4px; }
.nav-links { display: inline-flex; gap: 2px; margin-left: 12px; }
.nav-links a {
    color: #64748b; font-size: 12px; text-decoration: none;
    padding: 4px 10px; border-radius: 5px; transition: color 0.15s, background 0.15s;
}
.nav-links a:hover { color: #cbd5e1; background: rgba(255,255,255,0.06); }
.nav-links a.active { color: #f8fafc; font-weight: 600; background: rgba(255,255,255,0.1); }
.live-dot {
    display: inline-block; width: 7px; height: 7px;
    background: #22c55e; border-radius: 50%; margin-right: 4px;
    animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
.page { max-width: 1000px; margin: 0 auto; padding: 32px 24px 64px; }
h1 { font-size: 22px; font-weight: 700; margin-bottom: 3px; }
.subtitle { color: #64748b; font-size: 13px; margin-bottom: 28px; }

/* Summary cards */
.summary { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 32px; }
.s-card {
    background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
    padding: 14px 20px; flex: 1; min-width: 150px;
}
.s-card.highlight {
    background: #0f172a; border-color: #0f172a;
}
.s-card.highlight .s-label { color: #64748b; }
.s-card.highlight .s-val { color: #f8fafc; }
.s-card.highlight .s-sub { color: #475569; }
.s-label { font-size: 10px; font-weight: 700; text-transform: uppercase;
           letter-spacing: 0.6px; color: #94a3b8; }
.s-val { font-size: 22px; font-weight: 700; margin-top: 2px; }
.s-sub { font-size: 11px; color: #94a3b8; margin-top: 1px; }
.up { color: #059669; } .down { color: #dc2626; } .neutral { color: #334155; }

/* Experiment cards */
.exp-list { display: flex; flex-direction: column; gap: 14px; }
.exp-card {
    background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
    overflow: hidden; transition: box-shadow 0.15s;
}
.exp-card:hover { box-shadow: 0 2px 16px rgba(0,0,0,0.07); }

/* Card header row */
.exp-header {
    display: flex; justify-content: space-between; align-items: flex-start;
    padding: 18px 22px 14px; border-bottom: 1px solid #f1f5f9;
    flex-wrap: wrap; gap: 12px;
}
.exp-id-line { font-size: 11px; font-weight: 700; color: #64748b;
               text-transform: uppercase; letter-spacing: 0.5px; }
.exp-name { font-size: 17px; font-weight: 700; color: #0f172a; margin-top: 2px; }
.exp-meta { font-size: 12px; color: #94a3b8; margin-top: 4px; }
.ticker {
    background: #0f172a; color: #f8fafc; font-size: 10px; font-weight: 700;
    padding: 1px 6px; border-radius: 3px; letter-spacing: 0.5px;
}
.ticker.ibit { background: #d97706; }

/* Live equity block (top-right of header) */
.equity-block { text-align: right; }
.equity-val { font-size: 28px; font-weight: 800; color: #0f172a; letter-spacing: -0.5px; }
.equity-label { font-size: 10px; font-weight: 700; text-transform: uppercase;
                letter-spacing: 0.5px; color: #94a3b8; margin-bottom: 2px; }
.equity-return { font-size: 13px; font-weight: 700; margin-top: 2px; }

/* Stats row */
.exp-stats-row {
    display: flex; gap: 0; border-top: 1px solid #f1f5f9;
}
.stat-cell {
    flex: 1; padding: 12px 16px; text-align: center;
    border-right: 1px solid #f1f5f9;
}
.stat-cell:last-child { border-right: none; }
.stat-val { font-size: 17px; font-weight: 700; }
.stat-lbl { font-size: 10px; color: #94a3b8; text-transform: uppercase;
            letter-spacing: 0.4px; margin-top: 1px; }

/* Alpaca mini row */
.alpaca-row {
    display: flex; gap: 0; background: #f8fafc;
    border-top: 1px solid #f1f5f9; padding: 10px 22px;
    font-size: 12px; flex-wrap: wrap; gap: 20px;
}
.alp-item { display: flex; flex-direction: column; }
.alp-lbl { font-size: 10px; color: #94a3b8; text-transform: uppercase;
           letter-spacing: 0.4px; }
.alp-val { font-weight: 700; margin-top: 1px; }

/* Alpaca positions mini table */
.positions-section { padding: 0 22px 14px; }
.positions-title { font-size: 10px; font-weight: 700; text-transform: uppercase;
                   letter-spacing: 0.5px; color: #94a3b8; margin: 10px 0 6px; }
.pos-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.pos-table th { font-size: 10px; font-weight: 600; text-transform: uppercase;
                letter-spacing: 0.4px; color: #94a3b8; text-align: left;
                padding: 4px 8px; border-bottom: 1px solid #f1f5f9; }
.pos-table td { padding: 5px 8px; border-bottom: 1px solid #f8fafc;
                font-family: 'SF Mono', 'Fira Code', monospace; }
.pos-table tr:last-child td { border-bottom: none; }
.pos-sym { font-weight: 600; color: #334155; letter-spacing: 0.2px; }
.pos-side-short { color: #dc2626; font-weight: 700; }
.pos-side-long  { color: #059669; font-weight: 700; }

.badge {
    display: inline-block; padding: 2px 8px; border-radius: 12px;
    font-size: 11px; font-weight: 700;
}
.badge-green  { background: #dcfce7; color: #166534; }
.badge-yellow { background: #fef9c3; color: #854d0e; }
.badge-red    { background: #fee2e2; color: #991b1b; }
.badge-gray   { background: #f1f5f9; color: #64748b; }

.no-alpaca {
    padding: 8px 22px 14px; font-size: 12px; color: #94a3b8; font-style: italic;
}
.exp-desc {
    font-size: 12px; color: #64748b; margin-top: 5px; line-height: 1.45;
    max-width: 480px;
}

.footer {
    margin-top: 48px; padding-top: 14px; border-top: 1px solid #e2e8f0;
    font-size: 11px; color: #94a3b8; display: flex;
    justify-content: space-between; flex-wrap: wrap; gap: 8px;
}
@media (max-width: 640px) {
    .page { padding: 20px 14px 48px; }
    .exp-header { flex-direction: column; }
    .equity-block { text-align: left; }
    .exp-stats-row { flex-wrap: wrap; }
    .stat-cell { min-width: 80px; }
}
"""

_JS = """
<script>
(function(){
  var I=300,r=I,el=document.getElementById('cd');
  function t(){r--;if(r<=0){location.reload();return;}
  if(el)el.textContent=r+'s';setTimeout(t,1000);}
  setTimeout(t,1000);
  document.addEventListener('keydown',function(e){if(e.key==='r'||e.key==='R')location.reload();});
})();
</script>
"""

# ---------------------------------------------------------------------------

def _fmt_pnl(v):
    s = "+" if v >= 0 else ""
    return f"{s}${abs(v):,.0f}"

def _fmt_money(v):
    return f"${v:,.0f}"

def _pnl_cls(v):
    return "up" if v > 0 else ("down" if v < 0 else "neutral")

def _wr_badge(wr, count):
    if count == 0:
        return '<span class="badge badge-gray">—</span>'
    if wr >= 70:
        return f'<span class="badge badge-green">{wr:.0f}%</span>'
    if wr >= 50:
        return f'<span class="badge badge-yellow">{wr:.0f}%</span>'
    return f'<span class="badge badge-red">{wr:.0f}%</span>'

def _ticker_cls(t):
    return "ibit" if t.upper() == "IBIT" else ""

def _pct_return(equity):
    """Return % vs STARTING_EQUITY."""
    if equity is None:
        return None
    return (equity - STARTING_EQUITY) / STARTING_EQUITY * 100

# ---------------------------------------------------------------------------
# Strategy descriptions shown below experiment names on the dashboard.
# Keyed by experiment ID (matches registry.json "id" field).
_EXP_DESCRIPTIONS: dict[str, str] = {
    "EXP-400": (
        "Regime-adaptive credit spreads &amp; iron condors on SPY. "
        "Switches between bull puts and bear calls based on trend detection (MA crossovers). "
        "No ML. Robustness score: 0.870."
    ),
    "EXP-401": (
        "Blended strategy combining credit spreads with straddles/strangles on SPY. "
        "Uses volatility regime detection to select optimal structure. "
        "No ML. Walk-forward validated (3/3 passed)."
    ),
    "EXP-503": (
        "Machine learning-driven credit spreads on SPY. "
        "XGBoost regime classifier routes trades through ML-optimized position sizing. "
        "Aggressive Kelly sizing with model confidence weighting."
    ),
    "EXP-600": (
        "Direction-adaptive credit spreads on IBIT (Bitcoin ETF). "
        "MA50-based trend detection, 14 DTE, 10% OTM. No ML. "
        "Optimized via mega parameter sweep (139% avg annual backtest)."
    ),
}

# ---------------------------------------------------------------------------

_chart_counter = 0


def _render_equity_chart(history: list[dict], today_equity: float | None = None) -> str:
    """Render an inline SVG sparkline equity chart from alpaca_equity_history.

    Args:
        history: list of {"date": "YYYY-MM-DD", "equity": float} from Alpaca portfolio history.
        today_equity: optional live intraday equity to append as a final "today" point.

    Returns "" when fewer than 2 plottable points are available.
    """
    global _chart_counter
    _chart_counter += 1
    chart_id = f"eqc{_chart_counter}"

    if len(history) < 2 and today_equity is None:
        return ""

    # Build full point list, appending today if provided.
    all_points_data = list(history)
    has_today = today_equity is not None
    if has_today:
        from datetime import date as _date
        today_str = _date.today().isoformat()
        if not all_points_data or all_points_data[-1].get("date") != today_str:
            all_points_data.append({"date": today_str, "equity": today_equity})

    if len(all_points_data) < 2:
        return ""

    w, h = 560, 120
    pad_l, pad_r, pad_t, pad_b = 40, 8, 8, 14
    cw = w - pad_l - pad_r
    ch = h - pad_t - pad_b

    equities = [d["equity"] for d in all_points_data]
    min_eq = min(equities) * 0.999
    max_eq = max(equities) * 1.001
    rng = max_eq - min_eq or 1

    n = len(all_points_data)
    overall = equities[-1] - STARTING_EQUITY
    color = "#22c55e" if overall >= 0 else "#ef4444"
    fill_color = "rgba(34,197,94,0.08)" if overall >= 0 else "rgba(239,68,68,0.08)"

    points = []
    for i, d in enumerate(all_points_data):
        x = pad_l + (i / (n - 1)) * cw
        y = pad_t + ch - ((d["equity"] - min_eq) / rng) * ch
        points.append((x, y, d["date"], d["equity"]))

    hist_pts = points[:-1] if has_today else points
    today_pt = points[-1] if has_today else None

    # <polyline> renders the visible line. A separate <path> below the polyline
    # fills the area under the curve.
    polyline_pts = " ".join(f"{x:.1f},{y:.1f}" for (x, y, _, _) in points)
    line_path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _, _) in enumerate(points))
    area = line_path + f" L{points[-1][0]:.1f},{pad_t + ch} L{points[0][0]:.1f},{pad_t + ch} Z"

    # Y-axis: 3 reference labels
    y_ticks = ""
    for frac, label_eq in [(0.0, max_eq), (0.5, (min_eq + max_eq) / 2), (1.0, min_eq)]:
        ty = pad_t + frac * ch
        y_ticks += f'<text x="{pad_l - 4}" y="{ty + 3:.1f}" text-anchor="end" fill="#94a3b8" font-size="9" font-family="system-ui">${label_eq/1000:.1f}k</text>'

    # Reference line at STARTING_EQUITY when in range
    ref_line = ""
    if min_eq <= STARTING_EQUITY <= max_eq:
        start_y = pad_t + ch - ((STARTING_EQUITY - min_eq) / rng) * ch
        ref_line = f'<line x1="{pad_l}" y1="{start_y:.1f}" x2="{w - pad_r}" y2="{start_y:.1f}" stroke="#cbd5e1" stroke-width="0.8" stroke-dasharray="4,3"/>'

    # X-axis: ~5 date labels
    step = max(1, len(points) // 5)
    x_labels = ""
    for i, (x, _, dt, _) in enumerate(points):
        if i % step == 0 or i == len(points) - 1:
            x_labels += f'<text x="{x:.1f}" y="{h - 3}" text-anchor="middle" fill="#94a3b8" font-size="8" font-family="system-ui">{dt[5:]}</text>'

    # Last historical point dot
    lx, ly = hist_pts[-1][0], hist_pts[-1][1]

    # Today point: pulsing hollow circle
    today_svg = ""
    if has_today and today_pt:
        tx, ty = today_pt[0], today_pt[1]
        today_svg = f"""
    <circle cx="{tx:.1f}" cy="{ty:.1f}" r="5" fill="none" stroke="{color}" stroke-width="1.5" opacity="0.4">
      <animate attributeName="r" values="4;7;4" dur="2s" repeatCount="indefinite"/>
      <animate attributeName="opacity" values="0.5;0.1;0.5" dur="2s" repeatCount="indefinite"/>
    </circle>
    <circle cx="{tx:.1f}" cy="{ty:.1f}" r="3" fill="{color}" stroke="white" stroke-width="1.5"/>"""

    # Invisible hover overlay rects + tooltip via JS
    hover_rects = ""
    for i, (x, y, dt, eq) in enumerate(points):
        x_left = (points[i - 1][0] + x) / 2 if i > 0 else pad_l
        x_right = (x + points[i + 1][0]) / 2 if i < len(points) - 1 else w - pad_r
        rect_w = x_right - x_left
        is_today = has_today and i == len(points) - 1
        label = "today (live)" if is_today else dt[5:]
        eq_fmt = f"${eq:,.0f}"
        hover_rects += (
            f'<rect data-cid="{chart_id}" data-date="{label}" data-eq="{eq_fmt}" '
            f'x="{x_left:.1f}" y="{pad_t}" width="{rect_w:.1f}" height="{ch}" '
            f'fill="transparent" style="cursor:crosshair"/>'
        )

    js = f"""
<script>
(function(){{
  var tip = document.getElementById('eq-tip');
  if (!tip) {{
    tip = document.createElement('div');
    tip.id = 'eq-tip';
    tip.style.cssText = 'position:fixed;display:none;background:#0f172a;color:#f1f5f9;font-size:11px;font-family:system-ui;padding:5px 9px;border-radius:6px;pointer-events:none;box-shadow:0 2px 8px rgba(0,0,0,0.4);white-space:nowrap;z-index:9999;line-height:1.6';
    document.body.appendChild(tip);
  }}
  document.querySelectorAll('rect[data-cid="{chart_id}"]').forEach(function(r){{
    r.addEventListener('mouseenter', function(e){{
      tip.innerHTML = '<span style="color:#94a3b8">' + r.dataset.date + '</span><br><b>' + r.dataset.eq + '</b>';
      tip.style.display = 'block';
    }});
    r.addEventListener('mousemove', function(e){{
      var tx = e.clientX + 14, ty = e.clientY - 36;
      if (tx + 120 > window.innerWidth) tx = e.clientX - 130;
      tip.style.left = tx + 'px';
      tip.style.top = ty + 'px';
    }});
    r.addEventListener('mouseleave', function(){{ tip.style.display = 'none'; }});
  }});
}})();
</script>"""

    return f"""
<div style="margin:8px 0 4px;overflow:hidden">
  <svg id="{chart_id}" viewBox="0 0 {w} {h}" style="width:100%;height:{h}px">
    {y_ticks}
    {ref_line}
    <path d="{area}" fill="{fill_color}"/>
    <polyline points="{polyline_pts}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{color}"/>
    {today_svg}
    {x_labels}
    {hover_rects}
  </svg>
</div>{js}"""


def _render_chart_placeholder() -> str:
    """Tiny placeholder shown when an experiment has no equity history yet."""
    return (
        '<div style="margin:8px 0 4px;padding:14px;border:1px dashed #cbd5e1;'
        'border-radius:6px;color:#94a3b8;font-size:11px;text-align:center;'
        'font-family:system-ui">no equity history yet</div>'
    )


# ---------------------------------------------------------------------------

def _render_exp_card(s: dict) -> str:
    alp = s.get("alpaca") or {}
    equity = alp.get("equity")
    unrealized_pl = alp.get("unrealized_pl")
    day_pl = alp.get("day_pl")
    cash = alp.get("cash")
    positions = alp.get("positions") or []
    alp_error = alp.get("error")

    tc = _ticker_cls(s["ticker"])

    # Equity / return block
    if equity is not None:
        ret_pct = _pct_return(equity)
        ret_cls = _pnl_cls(ret_pct)
        equity_html = f"""
  <div class="equity-block">
    <div class="equity-label">Live Equity</div>
    <div class="equity-val">{_fmt_money(equity)}</div>
    <div class="equity-return {ret_cls}">{ret_pct:+.1f}% since inception</div>
  </div>"""
    else:
        equity_html = '<div class="equity-block" style="color:#94a3b8;font-size:12px;">No Alpaca data</div>'

    # Realized P&L
    pnl_display = _fmt_pnl(s["total_pnl"]) if s["total_closed"] > 0 else "—"
    pnl_c = _pnl_cls(s["total_pnl"]) if s["total_closed"] > 0 else "neutral"

    # Stats row
    stats_row = f"""
<div class="exp-stats-row">
  <div class="stat-cell">
    <div class="stat-val {pnl_c}">{pnl_display}</div>
    <div class="stat-lbl">Realized P&amp;L</div>
  </div>
  <div class="stat-cell">
    <div class="stat-val {'up' if (unrealized_pl or 0) >= 0 else 'down'}">{_fmt_pnl(unrealized_pl) if unrealized_pl is not None else '—'}</div>
    <div class="stat-lbl">Unrealized P&amp;L</div>
  </div>
  <div class="stat-cell">
    <div class="stat-val {'up' if (day_pl or 0) >= 0 else 'down'}">{_fmt_pnl(day_pl) if day_pl is not None else '—'}</div>
    <div class="stat-lbl">Day P&amp;L</div>
  </div>
  <div class="stat-cell">
    <div class="stat-val neutral">{s['total_closed']}</div>
    <div class="stat-lbl">Closed</div>
  </div>
  <div class="stat-cell">
    <div class="stat-val neutral">{s['open_count']}</div>
    <div class="stat-lbl">Open</div>
  </div>
  <div class="stat-cell">
    <div>{_wr_badge(s['win_rate'], s['total_closed'])}</div>
    <div class="stat-lbl">Win Rate</div>
  </div>
</div>"""

    # Alpaca cash + positions
    if equity is not None and not alp_error:
        alpaca_detail = f"""
<div class="alpaca-row">
  <div class="alp-item">
    <span class="alp-lbl">Cash</span>
    <span class="alp-val neutral">{_fmt_money(cash) if cash is not None else '—'}</span>
  </div>
  <div class="alp-item">
    <span class="alp-lbl">Alpaca Positions</span>
    <span class="alp-val neutral">{len(positions)}</span>
  </div>
  <div class="alp-item">
    <span class="alp-lbl">Account ID</span>
    <span class="alp-val neutral" style="font-family:monospace;font-size:11px">{s.get('account_id','—')}</span>
  </div>
</div>"""
        if positions:
            rows = []
            for p in positions:
                side_cls = "pos-side-short" if p.get("side") == "short" else "pos-side-long"
                side_label = "SHORT" if p.get("side") == "short" else "LONG"
                unreal = p.get("unrealized_pl", 0)
                unreal_pct = p.get("unrealized_plpc", 0)
                rows.append(f"""<tr>
  <td class="pos-sym">{_html.escape(str(p.get('symbol', '')))}</td>
  <td class="{side_cls}">{side_label}</td>
  <td style="text-align:right">{abs(p.get('qty',0)):.0f}</td>
  <td style="text-align:right">${p.get('current_price',0):.2f}</td>
  <td style="text-align:right">{_fmt_money(p.get('market_value',0))}</td>
  <td style="text-align:right" class="{'up' if unreal >= 0 else 'down'}">{_fmt_pnl(unreal)} ({unreal_pct:+.1f}%)</td>
</tr>""")
            pos_section = f"""
<div class="positions-section">
  <div class="positions-title">Alpaca Option Legs ({len(positions)})</div>
  <table class="pos-table">
    <thead><tr>
      <th>Symbol</th><th>Side</th><th>Qty</th>
      <th style="text-align:right">Price</th>
      <th style="text-align:right">Mkt Value</th>
      <th style="text-align:right">Unreal P&amp;L</th>
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</div>"""
        else:
            pos_section = ""
    else:
        # SECURITY AUDIT #11: log raw error server-side; show only a generic
        # message in the HTML so API error strings are never rendered to users.
        if alp_error:
            logger.warning("[dashboard] Alpaca error for %s: %s", s.get("id"), alp_error)
            err_msg = "Alpaca account unavailable"
        else:
            err_msg = "No Alpaca credentials configured"
        alpaca_detail = f'<div class="no-alpaca">{err_msg}</div>'
        pos_section = ""

    # SECURITY AUDIT #7: escape all registry-sourced values before inserting into HTML.
    eid      = _html.escape(str(s['id']))
    ename    = _html.escape(str(s['name']))
    eticker  = _html.escape(str(s['ticker']))
    ecreator = _html.escape(str(s.get('creator', '—')))
    elive    = _html.escape(str(s.get('live_since', '—')))

    # Description: prefer registry field, fall back to built-in dict.
    # Registry value is escaped; built-in strings use &amp; literals so are safe as-is.
    _reg_desc = s.get('description', '')
    if _reg_desc:
        edesc = _html.escape(str(_reg_desc))
    else:
        edesc = _EXP_DESCRIPTIONS.get(s['id'], '')
    desc_html = f'<div class="exp-desc">{edesc}</div>' if edesc else ''

    # Equity sparkline chart — pass live equity as today's intraday point.
    # Falls back to a small placeholder when no history has been synced yet.
    history = s.get("alpaca_equity_history") or []
    chart_html = _render_equity_chart(history, today_equity=equity)
    if not chart_html:
        chart_html = _render_chart_placeholder()

    return f"""
<div class="exp-card">
  <div class="exp-header">
    <div class="exp-left">
      <div class="exp-id-line">{eid}</div>
      <div class="exp-name">{ename}</div>
      {desc_html}
      <div class="exp-meta">
        <span class="ticker {tc}">{eticker}</span>
        &nbsp; by {ecreator} &nbsp;&bull;&nbsp; live since {elive}
      </div>
    </div>
    {equity_html}
  </div>
  {chart_html}
  {stats_row}
  {alpaca_detail}
  {pos_section}
</div>"""


# ---------------------------------------------------------------------------

def render_dashboard(all_stats: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    total_pnl    = sum(s["total_pnl"] for s in all_stats)
    total_closed = sum(s["total_closed"] for s in all_stats)
    total_open   = sum(s["open_count"] for s in all_stats)
    total_wins   = sum(s["wins"] for s in all_stats)
    wr = (total_wins / total_closed * 100) if total_closed else 0

    # Combined live equity from Alpaca
    equities = [
        s["alpaca"]["equity"]
        for s in all_stats
        if s.get("alpaca") and s["alpaca"].get("equity") is not None
    ]
    combined_equity = sum(equities) if equities else None
    combined_unrealized = sum(
        s["alpaca"].get("unrealized_pl") or 0
        for s in all_stats
        if s.get("alpaca") and s["alpaca"].get("equity") is not None
    ) if equities else None
    combined_return_pct = (
        (combined_equity - STARTING_EQUITY * len(all_stats)) / (STARTING_EQUITY * len(all_stats)) * 100
        if combined_equity is not None else None
    )

    # Summary cards
    if combined_equity is not None:
        equity_card = f"""
    <div class="s-card highlight">
      <div class="s-label">Combined Equity</div>
      <div class="s-val">{_fmt_money(combined_equity)}</div>
      <div class="s-sub">{combined_return_pct:+.1f}% across {len(all_stats)} accounts</div>
    </div>"""
        unrealized_card = f"""
    <div class="s-card">
      <div class="s-label">Unrealized P&L</div>
      <div class="s-val {_pnl_cls(combined_unrealized)}">{_fmt_pnl(combined_unrealized)}</div>
      <div class="s-sub">live open positions</div>
    </div>"""
    else:
        equity_card = ""
        unrealized_card = ""

    exp_rows = "".join(_render_exp_card(s) for s in all_stats)

    nav = _render_nav("/", f'<span class="live-dot"></span> <span>Updated {now_str} &bull; Refresh in <span id="cd">300s</span></span>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Attix Dashboard</title>
  <style>{_CSS}</style>
</head>
<body>
{_render_watchdog_banner()}
{nav}
<div class="page">
  <h1>Attix Dashboard</h1>
  <p class="subtitle">Credit Spreads &bull; 8-week gate: Mar 16 → May 11, 2026</p>

  <div class="summary">
    {equity_card}
    {unrealized_card}
    <div class="s-card">
      <div class="s-label">Realized P&L</div>
      <div class="s-val {_pnl_cls(total_pnl)}">{_fmt_pnl(total_pnl)}</div>
      <div class="s-sub">{total_pnl/STARTING_EQUITY*100:+.1f}% of $100K starting</div>
    </div>
    <div class="s-card">
      <div class="s-label">Trades</div>
      <div class="s-val neutral">{total_closed}</div>
      <div class="s-sub">{total_wins}W / {total_closed - total_wins}L</div>
    </div>
    <div class="s-card">
      <div class="s-label">Win Rate</div>
      <div class="s-val {'up' if wr >= 70 else 'neutral'}">{wr:.0f}%</div>
      <div class="s-sub">{total_open} open positions</div>
    </div>
  </div>

  <div class="exp-list">
    {exp_rows}
  </div>

  <div class="footer">
    <span>Attix Credit Spreads &bull; {len(all_stats)} experiments</span>
    <span>{now_str}</span>
  </div>
</div>
{_JS}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------

def render_login_page(error: str = "") -> str:
    error_html = (
        f'<div class="login-error">{error}</div>' if error else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Attix Dashboard — Sign In</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0f172a; color: #e2e8f0; font-size: 14px;
        min-height: 100vh; display: flex; flex-direction: column;
        align-items: center; justify-content: center;
    }}
    .login-box {{
        background: #1e293b; border: 1px solid #334155; border-radius: 14px;
        padding: 40px 36px; width: 100%; max-width: 380px;
        box-shadow: 0 20px 60px rgba(0,0,0,0.4);
    }}
    .login-brand {{
        font-size: 22px; font-weight: 800; letter-spacing: -0.5px;
        color: #f8fafc; margin-bottom: 4px;
    }}
    .login-sub {{
        font-size: 13px; color: #64748b; margin-bottom: 28px;
    }}
    label {{
        display: block; font-size: 12px; font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.5px;
        color: #94a3b8; margin-bottom: 6px;
    }}
    input[type=password] {{
        width: 100%; padding: 10px 14px; font-size: 15px;
        background: #0f172a; border: 1px solid #334155; border-radius: 8px;
        color: #f8fafc; outline: none; transition: border-color 0.15s;
        font-family: inherit;
    }}
    input[type=password]:focus {{ border-color: #6366f1; }}
    .login-error {{
        background: #450a0a; border: 1px solid #7f1d1d; border-radius: 8px;
        color: #fca5a5; font-size: 13px; padding: 10px 14px; margin-bottom: 18px;
    }}
    .login-btn {{
        margin-top: 20px; width: 100%; padding: 11px;
        background: #6366f1; border: none; border-radius: 8px;
        color: #fff; font-size: 15px; font-weight: 600;
        cursor: pointer; transition: background 0.15s; font-family: inherit;
    }}
    .login-btn:hover {{ background: #4f46e5; }}
    .login-footer {{
        margin-top: 24px; font-size: 11px; color: #475569; text-align: center;
    }}
  </style>
</head>
<body>
  <div class="login-box">
    <div class="login-brand">Attix</div>
    <div class="login-sub">Paper Trading Dashboard</div>
    {error_html}
    <form method="post" action="/login">
      <label for="password">Password</label>
      <input type="password" id="password" name="password"
             placeholder="Enter dashboard password"
             autofocus autocomplete="current-password">
      <button type="submit" class="login-btn">Sign In</button>
    </form>
    <div class="login-footer">Attix Credit Spreads &bull; Authorized access only</div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Positions page (aggregated across all live experiments)
# ---------------------------------------------------------------------------

def render_positions_page(all_stats: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    nav = _render_nav("/positions", f'<span>{now_str}</span>')

    total_positions = sum(len(s.get("open_trades", [])) for s in all_stats)

    # Build position rows from all experiments
    pos_rows = ""
    for s in all_stats:
        exp_id = _html.escape(s.get("id", ""))
        exp_name = _html.escape(s.get("name", ""))
        alp = s.get("alpaca") or {}
        alp_positions = alp.get("positions") or []
        open_trades = s.get("open_trades", [])

        # Prefer Alpaca live positions if available
        if alp_positions:
            for p in alp_positions:
                sym = _html.escape(str(p.get("symbol", "—")))
                side = p.get("side", "—")
                side_cls = "pos-side-short" if side == "short" else "pos-side-long"
                qty = p.get("qty", "—")
                price = p.get("current_price") or p.get("avg_entry_price") or "—"
                mkt_val = p.get("market_value", "—")
                unreal = p.get("unrealized_pl")
                unreal_str = _fmt_pnl(float(unreal)) if unreal is not None else "—"
                unreal_cls = _pnl_cls(float(unreal)) if unreal is not None else "neutral"
                pos_rows += f"""<tr>
  <td class="reg-exp-id">{exp_id}</td>
  <td>{exp_name}</td>
  <td class="pos-sym">{sym}</td>
  <td class="{side_cls}">{side}</td>
  <td>{qty}</td>
  <td>{f"${float(price):,.2f}" if isinstance(price, (int, float)) else price}</td>
  <td>{f"${float(mkt_val):,.0f}" if isinstance(mkt_val, (int, float)) else mkt_val}</td>
  <td class="{unreal_cls}">{unreal_str}</td>
</tr>"""
        elif open_trades:
            for t in open_trades:
                sym = _html.escape(str(t.get("ticker", "—")))
                strategy = _html.escape(str(t.get("strategy_type", "—")).replace("_", " ").title())
                short_s = t.get("short_strike", "—")
                long_s = t.get("long_strike", "—")
                contracts = t.get("contracts", "—")
                credit = t.get("credit")
                credit_str = f"${float(credit):,.0f}" if credit is not None else "—"
                exp_date = _html.escape(str(t.get("expiration", "—"))[:10])
                pos_rows += f"""<tr>
  <td class="reg-exp-id">{exp_id}</td>
  <td>{exp_name}</td>
  <td class="pos-sym">{sym}</td>
  <td>{strategy}</td>
  <td>{contracts}</td>
  <td>{short_s}/{long_s}</td>
  <td>{credit_str}</td>
  <td class="reg-meta">{exp_date}</td>
</tr>"""

    if not pos_rows:
        pos_rows = '<tr><td colspan="8" style="text-align:center;color:#94a3b8;padding:40px">No open positions</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Positions — Attix Dashboard</title>
  <style>{_CSS}{_REGISTRY_CSS}</style>
</head>
<body>
{_render_watchdog_banner()}
{nav}
<div class="page" style="max-width:1200px">
  <h1>Open Positions</h1>
  <p class="subtitle">All open positions across {len(all_stats)} live experiments &bull; {total_positions} total</p>

  <div style="overflow-x:auto">
    <table class="reg-table">
      <thead>
        <tr>
          <th>Experiment</th>
          <th>Name</th>
          <th>Symbol</th>
          <th>Side / Strategy</th>
          <th>Qty / Contracts</th>
          <th>Price / Strikes</th>
          <th>Market Val / Credit</th>
          <th>Unrealized / Expiry</th>
        </tr>
      </thead>
      <tbody>
        {pos_rows}
      </tbody>
    </table>
  </div>

  <div class="footer">
    <span>Attix Credit Spreads &bull; {total_positions} positions</span>
    <span>{now_str}</span>
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Trades page (recent trades across all live experiments)
# ---------------------------------------------------------------------------

def render_trades_page(all_stats: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    nav = _render_nav("/trades", f'<span>{now_str}</span>')

    total_closed = sum(s.get("total_closed", 0) for s in all_stats)

    # Collect recent trades from all experiments, sorted by exit date
    all_trades = []
    for s in all_stats:
        exp_id = s.get("id", "")
        exp_name = s.get("name", "")
        for t in s.get("recent_trades", []):
            t["_exp_id"] = exp_id
            t["_exp_name"] = exp_name
            all_trades.append(t)
    all_trades.sort(key=lambda t: str(t.get("exit_date", "")), reverse=True)

    trade_rows = ""
    for t in all_trades[:50]:  # show last 50
        eid = _html.escape(str(t.get("_exp_id", "")))
        pnl = float(t.get("pnl", 0))
        strategy = _html.escape(str(t.get("strategy_type", "—")).replace("_", " ").title())
        ticker = _html.escape(str(t.get("ticker", "—")))
        entry = _html.escape(str(t.get("entry_date", "—"))[:10])
        exit_d = _html.escape(str(t.get("exit_date", "—"))[:10])
        short_s = t.get("short_strike", "—")
        long_s = t.get("long_strike", "—")
        contracts = t.get("contracts", "—")
        credit = t.get("credit")
        credit_str = f"${float(credit):,.0f}" if credit is not None else "—"
        reason = _html.escape(str(t.get("exit_reason", "—")))
        trade_rows += f"""<tr>
  <td class="reg-exp-id">{eid}</td>
  <td class="pos-sym">{ticker}</td>
  <td>{strategy}</td>
  <td>{short_s}/{long_s}</td>
  <td>{contracts}</td>
  <td>{credit_str}</td>
  <td class="reg-meta">{entry}</td>
  <td class="reg-meta">{exit_d}</td>
  <td class="{_pnl_cls(pnl)}" style="font-weight:700">{_fmt_pnl(pnl)}</td>
  <td class="reg-meta">{reason}</td>
</tr>"""

    if not trade_rows:
        trade_rows = '<tr><td colspan="10" style="text-align:center;color:#94a3b8;padding:40px">No closed trades yet</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trades — Attix Dashboard</title>
  <style>{_CSS}{_REGISTRY_CSS}</style>
</head>
<body>
{_render_watchdog_banner()}
{nav}
<div class="page" style="max-width:1200px">
  <h1>Recent Trades</h1>
  <p class="subtitle">{total_closed} closed trades across {len(all_stats)} live experiments &bull; showing last 50</p>

  <div style="overflow-x:auto">
    <table class="reg-table">
      <thead>
        <tr>
          <th>Experiment</th>
          <th>Ticker</th>
          <th>Strategy</th>
          <th>Strikes</th>
          <th>Contracts</th>
          <th>Credit</th>
          <th>Entry</th>
          <th>Exit</th>
          <th>P&amp;L</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {trade_rows}
      </tbody>
    </table>
  </div>

  <div class="footer">
    <span>Attix Credit Spreads &bull; {total_closed} total trades</span>
    <span>{now_str}</span>
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Registry page
# ---------------------------------------------------------------------------

_STATUS_BADGE_CSS = {
    "active": "status-active",
    "paused": "status-paused",
    "stopped": "status-stopped",
    "failed": "status-failed",
    "retired": "status-retired",
    "registered": "status-registered",
    "configuring": "status-configuring",
    "completed": "status-completed",
    # Legacy
    "paper_trading": "status-active",
    "in_development": "status-registered",
}


def _status_badge(status: str) -> str:
    cls = _STATUS_BADGE_CSS.get(status, "status-registered")
    label = _html.escape(status.upper())
    return f'<span class="status-badge {cls}">{label}</span>'


def _sort_experiments(experiments: dict) -> list[dict]:
    """Sort experiments: active first, then by ID."""
    status_order = {
        "active": 0, "paused": 1, "stopped": 2, "failed": 3,
        "registered": 4, "configuring": 5, "retired": 6, "completed": 7,
        # Legacy
        "paper_trading": 0, "in_development": 4,
    }
    exps = list(experiments.values())
    # Exclude research entries (EXP-*-max, etc.)
    exps = [e for e in exps if not any(e.get("id", "").endswith(s) for s in ("-max", "-real", "-paper", "-validation"))]
    exps.sort(key=lambda e: (status_order.get(e.get("status", ""), 99), e.get("id", "")))
    return exps


def _render_registry_row(exp: dict, idx: int) -> str:
    eid = _html.escape(str(exp.get("id", "")))
    name = _html.escape(str(exp.get("name", "—")))
    ticker = _html.escape(str(exp.get("ticker") or "—"))
    status = exp.get("status", "registered")
    strategy = _html.escape(str(exp.get("strategy_type") or "—"))
    account = _html.escape(str(exp.get("alpaca_account_id") or exp.get("account_id") or "—"))
    creator = _html.escape(str(exp.get("created_by", "—")))
    created = _html.escape(str(exp.get("created_at") or exp.get("created_date") or "—"))[:10]
    started = _html.escape(str(exp.get("last_started_at") or exp.get("live_since") or "—"))[:10]
    desc_raw = exp.get("description") or ""
    desc_trunc = _html.escape(desc_raw[:80] + ("..." if len(desc_raw) > 80 else ""))

    tc = "ibit" if ticker.upper() == "IBIT" else ""

    # Detail panel fields
    config_path = _html.escape(str(exp.get("config_path") or exp.get("paper_config") or "—"))
    env_file = _html.escape(str(exp.get("env_file") or "—"))
    db_path = _html.escape(str(exp.get("db_path") or "—"))
    git_branch = _html.escape(str(exp.get("git_branch") or "—"))
    updated = _html.escape(str(exp.get("updated_at") or "—"))[:19]
    stopped_at = _html.escape(str(exp.get("last_stopped_at") or "—"))[:19]
    notes = _html.escape(str(exp.get("notes") or ""))
    full_desc = _html.escape(str(desc_raw))
    retired_date = _html.escape(str(exp.get("retired_date") or "—"))
    superseded = _html.escape(str(exp.get("superseded_by") or "—"))
    lessons = _html.escape(str(exp.get("lessons_learned") or ""))

    # Action buttons based on current status
    # Valid transitions from experiments/registry.py
    transitions = {
        "registered": [("configuring", "Configure", ""), ("retired", "Retire", "retire")],
        "configuring": [("active", "Activate", "activate"), ("retired", "Retire", "retire")],
        "active": [("paused", "Pause", "pause"), ("stopped", "Stop", "stop"), ("retired", "Retire", "retire")],
        "paused": [("active", "Resume", "activate"), ("stopped", "Stop", "stop"), ("retired", "Retire", "retire")],
        "stopped": [("active", "Restart", "activate"), ("retired", "Retire", "retire")],
        "failed": [("configuring", "Reconfigure", ""), ("retired", "Retire", "retire")],
    }
    btns = transitions.get(status, [])
    buttons_html = ""
    for target, label, css_cls in btns:
        btn_cls = f"action-btn {css_cls}" if css_cls else "action-btn"
        buttons_html += (
            f'<button class="{btn_cls}" '
            f'onclick="doTransition(\'{eid}\', \'{target}\', this)">{label}</button> '
        )

    # Retired extras
    retired_html = ""
    if status == "retired":
        retired_html = f"""
      <div class="detail-field"><div class="detail-label">Retired Date</div><div class="detail-value">{retired_date}</div></div>
      <div class="detail-field"><div class="detail-label">Superseded By</div><div class="detail-value">{superseded}</div></div>"""
        if lessons:
            retired_html += f"""
      <div class="detail-desc"><div class="detail-label">Lessons Learned</div><div class="detail-notes">{lessons}</div></div>"""

    return f"""
<tr class="reg-row" data-status="{status}" data-search="{eid.lower()} {name.lower()}" onclick="toggleDetail({idx})">
  <td>{_status_badge(status)}</td>
  <td class="reg-exp-id">{eid}</td>
  <td class="reg-name">{name}</td>
  <td><span class="ticker {tc}">{ticker}</span></td>
  <td class="reg-meta">{strategy}</td>
  <td class="reg-meta" style="font-family:monospace;font-size:11px">{account}</td>
  <td class="reg-meta">{created}</td>
  <td class="reg-meta">{started}</td>
  <td class="reg-meta">{creator}</td>
  <td class="reg-desc-cell" title="{_html.escape(desc_raw)}">{desc_trunc}</td>
</tr>
<tr class="detail-row" id="detail-{idx}" style="display:none">
  <td colspan="10" style="padding:0 12px 8px">
    <div class="detail-panel open">
      <div class="detail-grid">
        <div class="detail-field"><div class="detail-label">Config Path</div><div class="detail-value mono">{config_path}</div></div>
        <div class="detail-field"><div class="detail-label">Env File</div><div class="detail-value mono">{env_file}</div></div>
        <div class="detail-field"><div class="detail-label">DB Path</div><div class="detail-value mono">{db_path}</div></div>
        <div class="detail-field"><div class="detail-label">Git Branch</div><div class="detail-value mono">{git_branch}</div></div>
        <div class="detail-field"><div class="detail-label">Updated At</div><div class="detail-value">{updated}</div></div>
        <div class="detail-field"><div class="detail-label">Last Stopped</div><div class="detail-value">{stopped_at}</div></div>
        {retired_html}
        {"" if not full_desc else f'<div class="detail-desc"><div class="detail-label">Full Description</div><div class="detail-notes">{full_desc}</div></div>'}
        {"" if not notes else f'<div class="detail-desc"><div class="detail-label">Notes</div><div class="detail-notes">{notes}</div></div>'}
      </div>
      {"" if not buttons_html else f'<div class="action-bar">{buttons_html}</div>'}
    </div>
  </td>
</tr>"""


def render_registry_page(registry: dict, validation: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    experiments = registry.get("experiments", {})
    sorted_exps = _sort_experiments(experiments)

    # Summary counts
    all_real = [e for e in experiments.values()
                if not any(e.get("id", "").endswith(s) for s in ("-max", "-real", "-paper", "-validation"))]
    active_count = sum(1 for e in all_real if e.get("status") in ("active", "paper_trading"))
    paused_count = sum(1 for e in all_real if e.get("status") == "paused")
    stopped_count = sum(1 for e in all_real if e.get("status") in ("stopped", "failed"))
    retired_count = sum(1 for e in all_real if e.get("status") in ("retired", "completed"))
    total_count = len(all_real)

    val_status = "—"
    val_cls = "neutral"
    if validation:
        if validation.get("valid"):
            val_status = "PASS"
            val_cls = "up"
        else:
            val_status = f"FAIL ({validation.get('error_count', 0)})"
            val_cls = "down"

    # Rows
    rows = "".join(_render_registry_row(exp, i) for i, exp in enumerate(sorted_exps))

    schema_ver = _html.escape(str(registry.get('schema_version', '?')))
    nav = _render_nav("/registry", f'<span>Schema v{schema_ver} &bull; {now_str}</span>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Registry — Attix Dashboard</title>
  <style>{_CSS}{_REGISTRY_CSS}</style>
</head>
<body>
{_render_watchdog_banner()}
{nav}
<div class="page" style="max-width:1200px">
  <h1>Experiment Registry</h1>
  <p class="subtitle">Single source of truth for all experiments &bull; {total_count} registered</p>

  <!-- Summary cards -->
  <div class="summary">
    <div class="s-card highlight">
      <div class="s-label">Total</div>
      <div class="s-val">{total_count}</div>
      <div class="s-sub">experiments registered</div>
    </div>
    <div class="s-card">
      <div class="s-label">Active</div>
      <div class="s-val up">{active_count}</div>
      <div class="s-sub">running scanners</div>
    </div>
    <div class="s-card">
      <div class="s-label">Paused</div>
      <div class="s-val" style="color:#d97706">{paused_count}</div>
      <div class="s-sub">dry-run mode</div>
    </div>
    <div class="s-card">
      <div class="s-label">Stopped / Failed</div>
      <div class="s-val down">{stopped_count}</div>
      <div class="s-sub">not running</div>
    </div>
    <div class="s-card">
      <div class="s-label">Retired</div>
      <div class="s-val neutral">{retired_count}</div>
      <div class="s-sub">historical</div>
    </div>
    <div class="s-card">
      <div class="s-label">Validation</div>
      <div class="s-val {val_cls}">{val_status}</div>
      <div class="s-sub">registry integrity</div>
    </div>
  </div>

  <!-- Filter bar -->
  <div class="filter-bar">
    <button class="filter-btn active" onclick="filterStatus('all', this)">All</button>
    <button class="filter-btn" onclick="filterStatus('active', this)">Active</button>
    <button class="filter-btn" onclick="filterStatus('paused', this)">Paused</button>
    <button class="filter-btn" onclick="filterStatus('stopped,failed', this)">Stopped</button>
    <button class="filter-btn" onclick="filterStatus('registered,configuring', this)">Registered</button>
    <button class="filter-btn" onclick="filterStatus('retired', this)">Retired</button>
    <input type="text" class="search-input" placeholder="Search by ID or name..." oninput="searchFilter(this.value)">
    <span style="flex:1"></span>
    <button class="action-btn" onclick="runValidate()" id="validate-btn">Validate All</button>
    <button class="action-btn" onclick="runSync()" id="sync-btn">Sync</button>
  </div>

  <!-- Results panel (for validate/sync) -->
  <div class="result-panel" id="result-panel"></div>

  <!-- Experiment table -->
  <div style="overflow-x:auto">
    <table class="reg-table">
      <thead>
        <tr>
          <th>Status</th>
          <th>ID</th>
          <th>Name</th>
          <th>Ticker</th>
          <th>Strategy</th>
          <th>Account</th>
          <th>Created</th>
          <th>Started</th>
          <th>Creator</th>
          <th>Description</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>

  <div class="footer">
    <span>Attix Credit Spreads &bull; {total_count} experiments</span>
    <span>{now_str}</span>
  </div>
</div>

<script>
// Toggle detail panel
function toggleDetail(idx) {{
  var row = document.getElementById('detail-' + idx);
  if (!row) return;
  var isOpen = row.style.display !== 'none';
  // Close all open detail rows
  document.querySelectorAll('.detail-row').forEach(function(r) {{ r.style.display = 'none'; }});
  if (!isOpen) row.style.display = 'table-row';
}}

// Filter by status
function filterStatus(statuses, btn) {{
  document.querySelectorAll('.filter-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  btn.classList.add('active');
  var list = statuses.split(',');
  document.querySelectorAll('.reg-row').forEach(function(row) {{
    var s = row.getAttribute('data-status');
    var show = (statuses === 'all') || list.indexOf(s) >= 0;
    row.style.display = show ? '' : 'none';
    // Also hide detail row
    var idx = Array.from(row.parentNode.children).indexOf(row);
    var detail = row.nextElementSibling;
    if (detail && detail.classList.contains('detail-row')) {{
      detail.style.display = 'none';
    }}
  }});
}}

// Search filter
function searchFilter(q) {{
  q = q.toLowerCase().trim();
  document.querySelectorAll('.reg-row').forEach(function(row) {{
    var searchText = row.getAttribute('data-search') || '';
    row.style.display = (!q || searchText.indexOf(q) >= 0) ? '' : 'none';
    var detail = row.nextElementSibling;
    if (detail && detail.classList.contains('detail-row')) {{
      detail.style.display = 'none';
    }}
  }});
}}

// Transition action
function doTransition(expId, target, btn) {{
  event.stopPropagation();
  var reason = '';
  if (target === 'paused' || target === 'retired') {{
    reason = prompt('Reason for ' + target + ':');
    if (reason === null) return;
  }}
  btn.disabled = true;
  btn.textContent = '...';
  fetch('/api/v1/registry/' + expId + '/transition', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ status: target, reason: reason || '' }})
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(data) {{
    if (data.status === 'ok') {{
      location.reload();
    }} else {{
      alert('Error: ' + (data.detail || data.message || 'unknown'));
      btn.disabled = false;
      btn.textContent = target;
    }}
  }})
  .catch(function(e) {{
    alert('Error: ' + e.message);
    btn.disabled = false;
  }});
}}

// HTML-escape dynamic data to prevent XSS
function esc(s) {{
  var d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}}

// Validate
function runValidate() {{
  var btn = document.getElementById('validate-btn');
  var panel = document.getElementById('result-panel');
  btn.disabled = true;
  btn.textContent = 'Validating...';
  fetch('/api/v1/registry/validate')
  .then(function(r) {{ return r.json(); }})
  .then(function(data) {{
    panel.className = 'result-panel open';
    if (data.status === 'ok') {{
      panel.innerHTML = '<span class="result-pass">PASS</span> — registry is valid';
    }} else {{
      panel.innerHTML = '<span class="result-fail">FAIL</span> — ' +
        esc(data.error_count) + ' error(s):\\n' + (data.errors || []).map(esc).join('\\n');
    }}
    btn.disabled = false;
    btn.textContent = 'Validate All';
  }})
  .catch(function(e) {{
    panel.className = 'result-panel open';
    panel.innerHTML = '<span class="result-fail">ERROR</span>: ' + esc(e.message);
    btn.disabled = false;
    btn.textContent = 'Validate All';
  }});
}}

// Sync
function runSync() {{
  var btn = document.getElementById('sync-btn');
  var panel = document.getElementById('result-panel');
  btn.disabled = true;
  btn.textContent = 'Scanning...';
  fetch('/api/v1/registry/sync')
  .then(function(r) {{ return r.json(); }})
  .then(function(data) {{
    panel.className = 'result-panel open';
    var lines = [];
    if (data.orphan_env_files && data.orphan_env_files.length > 0) {{
      lines.push('Orphan .env files: ' + data.orphan_env_files.map(esc).join(', '));
    }}
    if (data.orphan_dbs && data.orphan_dbs.length > 0) {{
      lines.push('Orphan databases: ' + data.orphan_dbs.map(esc).join(', '));
    }}
    if (data.active_not_running && data.active_not_running.length > 0) {{
      lines.push('Active but not running: ' + data.active_not_running.map(esc).join(', '));
    }}
    if (lines.length === 0) {{
      panel.innerHTML = '<span class="result-pass">ALL CLEAN</span> — no orphans or issues detected';
    }} else {{
      var count = (data.orphan_env_files||[]).length + (data.orphan_dbs||[]).length + (data.active_not_running||[]).length;
      panel.innerHTML = '<span class="result-fail">' + esc(count) + ' issue(s)</span>:\\n' + lines.join('\\n');
    }}
    btn.disabled = false;
    btn.textContent = 'Sync';
  }})
  .catch(function(e) {{
    panel.className = 'result-panel open';
    panel.innerHTML = '<span class="result-fail">ERROR</span>: ' + esc(e.message);
    btn.disabled = false;
    btn.textContent = 'Sync';
  }});
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Sentinel dashboard page
# ---------------------------------------------------------------------------

_SENTINEL_CSS = """
/* Sentinel page */
.sentinel-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }
.summary-card { padding: 16px; border-radius: 8px; border: 1px solid #eee; }
.summary-card .label { font-size: 0.65rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin-bottom: 4px; }
.summary-card .val { font-size: 1.5rem; font-weight: 800; }
.summary-card.ok { border-left: 3px solid #10b981; }
.summary-card.warn { border-left: 3px solid #f59e0b; }
.summary-card.crit { border-left: 3px solid #e94560; }
.summary-card.info { border-left: 3px solid #3b82f6; }
.health-card { padding: 16px; border-radius: 8px; border: 1px solid #eee; margin-bottom: 12px; }
.health-card .exp-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.health-card .exp-id { font-weight: 700; font-size: 1rem; }
.health-score { display: inline-block; padding: 2px 10px; border-radius: 4px; font-weight: 700; font-size: 0.85rem; }
.health-score.good { background: #d1fae5; color: #065f46; }
.health-score.mid { background: #fef3c7; color: #92400e; }
.health-score.bad { background: #fee2e2; color: #991b1b; }
.gate-pills { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.gate-pill { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 0.68rem; font-weight: 600; }
.gate-pill.pass { background: #d1fae5; color: #065f46; }
.gate-pill.warn { background: #fef3c7; color: #92400e; }
.gate-pill.fail { background: #fee2e2; color: #991b1b; }
.gate-pill.skip { background: #f3f4f6; color: #6b7280; }
.fresh-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
.fresh-green { background: #10b981; }
.fresh-yellow { background: #f59e0b; }
.fresh-red { background: #e94560; }
.halt-reason { font-size: 0.78rem; color: #991b1b; margin-top: 4px; }
.alert-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 12px; }
.alert-table th { text-align: left; padding: 6px 8px; border-bottom: 2px solid #ddd; font-size: 0.68rem; font-weight: 700; text-transform: uppercase; color: #999; }
.alert-table td { padding: 6px 8px; border-bottom: 1px solid #f3f3f3; }
@media (max-width: 600px) { .sentinel-summary { grid-template-columns: repeat(2, 1fr); } }
"""


def _classify_experiment_severity(status: str, gates: dict) -> str:
    """
    Decide which summary bucket this experiment belongs to.

    Returns one of: "halted" | "critical" | "warning" | "ok".
    Aggregates per-gate severity (NOT score band) so the counters cannot lie.
    A halted experiment counts as halted only — never double-counted as
    critical even if gate severities also include "critical" or "halt".
    """
    severities = {g.get("severity", "ok") for g in gates.values()}
    if status == "halted" or "halt" in severities:
        return "halted"
    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "warning"
    return "ok"


def _compute_health_score(exp_state: dict, gates: dict) -> int:
    """
    Compute health score 0-100 for an experiment based on state and gate results.

    Staleness is applied EXACTLY ONCE — via sentinel.cadence.staleness_score_penalty
    on `last_health_check` age. The G3 gate is shown to the user as a pill but
    is intentionally excluded from the gate-severity loop here, so we don't
    double-deduct the same staleness signal (which used to produce a -25 cliff
    at the 24h boundary).
    """
    from sentinel.cadence import staleness_score_penalty

    if exp_state.get("status") == "halted":
        return 0

    score = 100

    # Gate severities (G3 handled separately via the smooth staleness penalty)
    for gate_id, gate_info in gates.items():
        if gate_id == "G3":
            continue
        sev = gate_info.get("severity", "ok")
        if sev == "halt":
            return 0
        elif sev == "critical":
            score -= 30
        elif sev == "warning":
            score -= 10

    # Single staleness penalty (smooth gradient — no cliffs)
    last_hc = exp_state.get("last_health_check")
    if last_hc:
        try:
            hc_dt = datetime.fromisoformat(last_hc)
            if hc_dt.tzinfo is None:
                hc_dt = hc_dt.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - hc_dt).total_seconds() / 3600
            score -= staleness_score_penalty(age_h)
        except (ValueError, TypeError):
            score -= 10
    else:
        score -= 5

    return max(0, min(100, score))


def _freshness_dot(ts_str: str | None) -> str:
    """Return a colored dot indicating data freshness."""
    if not ts_str:
        return '<span class="fresh-dot fresh-red"></span><span class="muted">never</span>'
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if age_h < 2:
            cls = "fresh-green"
        elif age_h < 24:
            cls = "fresh-yellow"
        else:
            cls = "fresh-red"
        return f'<span class="fresh-dot {cls}"></span>{ts_str[:19]}'
    except (ValueError, TypeError):
        return '<span class="fresh-dot fresh-red"></span><span class="muted">invalid</span>'


def render_sentinel_page(
    sentinel_state: dict,
    alerts: list,
    snapshots: dict,
    registry: dict,
) -> str:
    """Render the Sentinel dashboard page with health scores, gates, and alerts."""
    experiments = sentinel_state.get("experiments", {})

    if not experiments:
        nav = _render_nav("/sentinel")
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Sentinel</title>
<style>{_CSS}\n{_SENTINEL_CSS}</style></head>
<body>
{_render_watchdog_banner()}
{nav}
<div class="page">
<h2 style="margin-top:24px">No experiments enrolled</h2>
<p class="muted">Enroll experiments in sentinel_state.json to see health data.</p>
</div></body></html>"""

    # Compute per-experiment health
    exp_cards = []
    total_score = 0
    halted_count = 0
    critical_count = 0
    warning_count = 0

    for eid in sorted(experiments.keys()):
        exp = experiments[eid]
        status = exp.get("status", "unknown")

        # Build gate status from available data
        gates = {}
        reg_exp = registry.get("experiments", {}).get(eid)
        if reg_exp:
            reg_status = reg_exp.get("status", "unknown")
            if reg_status in ("active", "paper_trading"):
                gates["G0"] = {"severity": "ok", "detail": f"status={reg_status}"}
            else:
                gates["G0"] = {"severity": "warning", "detail": f"status={reg_status}"}
        else:
            gates["G0"] = {"severity": "warning", "detail": "not in registry"}

        if status == "active":
            gates["G1"] = {"severity": "ok", "detail": "active"}
        elif status == "halted":
            gates["G1"] = {"severity": "halt", "detail": exp.get("halt_reason", "halted")}
        else:
            gates["G1"] = {"severity": "warning", "detail": f"status={status}"}

        if exp.get("config_fingerprint"):
            gates["G2"] = {"severity": "ok", "detail": exp["config_fingerprint"][:12] + "..."}
        else:
            gates["G2"] = {"severity": "warning", "detail": "no fingerprint"}

        last_hc = exp.get("last_health_check")
        if last_hc:
            try:
                hc_dt = datetime.fromisoformat(last_hc)
                if hc_dt.tzinfo is None:
                    hc_dt = hc_dt.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - hc_dt).total_seconds() / 3600
                # Cadence-aware severity: scales automatically with the cron
                # interval so G3 doesn't cliff at hard-coded 24h literals.
                from sentinel.cadence import StalenessThresholds
                sev = StalenessThresholds.from_cadence().severity_for_age(age_h)
                detail = f"{age_h:.1f}h ago" if sev in ("ok", "warning") else f"{age_h:.0f}h stale"
                gates["G3"] = {"severity": sev, "detail": detail}
            except (ValueError, TypeError):
                gates["G3"] = {"severity": "warning", "detail": "invalid timestamp"}

        if exp.get("backtest_baseline"):
            bl = exp["backtest_baseline"]
            gates["G8"] = {"severity": "ok", "detail": f"WR={bl.get('win_rate', '?')}%"}
        else:
            gates["G8"] = {"severity": "warning", "detail": "no baseline"}

        score = _compute_health_score(exp, gates)
        total_score += score

        # Counters aggregate per-gate severity (NOT score band) so the summary
        # never lies even if the score formula evolves.
        bucket = _classify_experiment_severity(status, gates)
        if bucket == "halted":
            halted_count += 1
        elif bucket == "critical":
            critical_count += 1
        elif bucket == "warning":
            warning_count += 1

        # Score badge class
        if score >= 80:
            score_cls = "good"
        elif score >= 50:
            score_cls = "mid"
        else:
            score_cls = "bad"

        # Gate pills HTML
        gate_pills = ""
        gate_labels = {"G0": "Registry", "G1": "State", "G2": "Config", "G3": "API", "G8": "Drift"}
        for gid in ("G0", "G1", "G2", "G3", "G8"):
            if gid in gates:
                g = gates[gid]
                sev = g.get("severity", "ok")
                pill_cls = {"ok": "pass", "warning": "warn", "critical": "fail", "halt": "fail"}.get(sev, "skip")
                gate_pills += f'<span class="gate-pill {pill_cls}">{gid}:{gate_labels.get(gid, gid)}</span>'

        # Halt reason
        halt_html = ""
        halt_reason = exp.get("halt_reason")
        if halt_reason:
            halt_html = f'<div class="halt-reason">{_html.escape(str(halt_reason))}</div>'

        freshness = _freshness_dot(last_hc)

        exp_cards.append(f"""<div class="health-card">
  <div class="exp-header">
    <span class="exp-id">{_html.escape(eid)}</span>
    <span class="health-score {score_cls}">{score}/100</span>
  </div>
  <div class="gate-pills">{gate_pills}</div>
  <div style="margin-top:6px;font-size:0.78rem;color:#666">Last check: {freshness}</div>
  {halt_html}
</div>""")

    # Summary stats
    n = len(experiments)
    avg_score = total_score // n if n else 0

    # Alert rows
    alert_rows = ""
    for a in (alerts or []):
        sev = _html.escape(str(a.get("severity", "info")).upper())
        eid_a = _html.escape(str(a.get("experiment_id") or "system"))
        msg = _html.escape(str(a.get("message", "")))
        ts = _html.escape(str(a.get("alert_time", ""))[:19])
        resolved = "RESOLVED" if a.get("resolved") else "OPEN"
        alert_rows += f"<tr><td>{ts}</td><td>{sev}</td><td>{eid_a}</td><td>{msg}</td><td>{resolved}</td></tr>"

    if not alert_rows:
        alert_rows = '<tr><td colspan="5" class="muted" style="text-align:center">No alerts</td></tr>'

    nav = _render_nav("/sentinel")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sentinel Dashboard</title>
<style>{_CSS}\n{_SENTINEL_CSS}</style></head>
<body>
{_render_watchdog_banner()}
{nav}
<div class="page">

<h2 style="margin:20px 0 16px">Sentinel Health</h2>

<div class="sentinel-summary">
  <div class="summary-card {'ok' if avg_score >= 80 else ('warn' if avg_score >= 50 else 'crit')}">
    <div class="label">Avg Health</div><div class="val">{avg_score}</div>
  </div>
  <div class="summary-card {'crit' if critical_count else 'ok'}">
    <div class="label">Critical</div><div class="val">{critical_count}</div>
  </div>
  <div class="summary-card {'warn' if halted_count else 'ok'}">
    <div class="label">Halted</div><div class="val">{halted_count}</div>
  </div>
  <div class="summary-card {'warn' if warning_count else 'ok'}">
    <div class="label">Warnings</div><div class="val">{warning_count}</div>
  </div>
</div>

{''.join(exp_cards)}

<h3 style="margin:24px 0 8px">Alert History</h3>
<table class="alert-table">
<thead><tr><th>Time</th><th>Severity</th><th>Experiment</th><th>Message</th><th>Status</th></tr></thead>
<tbody>{alert_rows}</tbody>
</table>

<div style="margin-top:24px;font-size:0.7rem;color:#bbb">
  Sentinel v2 &bull; <a href="/api/v1/sentinel" style="color:#3b82f6">Raw JSON</a>
</div>
</div></body></html>"""
