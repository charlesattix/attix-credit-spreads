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
# Shared navigation
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    ("/", "Overview"),
    ("/positions", "Positions"),
    ("/trades", "Trades"),
    ("/registry", "Registry"),
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
    if (data.valid) {{
      panel.innerHTML = '<span class="result-pass">PASS</span> — ' + data.message;
    }} else {{
      panel.innerHTML = '<span class="result-fail">FAIL</span> — ' +
        data.error_count + ' error(s):\\n' + (data.errors || []).join('\\n');
    }}
    btn.disabled = false;
    btn.textContent = 'Validate All';
  }})
  .catch(function(e) {{
    panel.className = 'result-panel open';
    panel.innerHTML = '<span class="result-fail">ERROR</span>: ' + e.message;
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
    if (data.orphan_envs && data.orphan_envs.length > 0) {{
      lines.push('Orphan .env files: ' + data.orphan_envs.join(', '));
    }}
    if (data.orphan_dbs && data.orphan_dbs.length > 0) {{
      lines.push('Orphan databases: ' + data.orphan_dbs.join(', '));
    }}
    if (data.active_not_running && data.active_not_running.length > 0) {{
      lines.push('Active but not running: ' + data.active_not_running.join(', '));
    }}
    if (lines.length === 0) {{
      panel.innerHTML = '<span class="result-pass">ALL CLEAN</span> — no orphans or issues detected';
    }} else {{
      panel.innerHTML = '<span class="result-fail">' + data.issue_count + ' issue(s)</span>:\\n' + lines.join('\\n');
    }}
    btn.disabled = false;
    btn.textContent = 'Sync';
  }})
  .catch(function(e) {{
    panel.className = 'result-panel open';
    panel.innerHTML = '<span class="result-fail">ERROR</span>: ' + e.message;
    btn.disabled = false;
    btn.textContent = 'Sync';
  }});
}}
</script>
</body>
</html>"""
