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
.top-bar .brand { color: #f8fafc; font-weight: 700; font-size: 14px; }
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
    """
    global _chart_counter
    _chart_counter += 1
    chart_id = f"eqc{_chart_counter}"

    if len(history) < 2 and today_equity is None:
        return ""

    # Build full point list, appending today if provided
    all_points_data = list(history)
    has_today = today_equity is not None
    if has_today:
        from datetime import date as _date
        today_str = _date.today().isoformat()
        # Only append if today isn't already the last entry
        if not all_points_data or all_points_data[-1].get("date") != today_str:
            all_points_data.append({"date": today_str, "equity": today_equity})

    if len(all_points_data) < 2:
        return ""

    w, h = 560, 120
    pad_l, pad_r, pad_t, pad_b = 50, 10, 8, 20
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

    # Build line using only historical points (exclude today from the filled area look if desired)
    hist_pts = points[:-1] if has_today else points
    today_pt = points[-1] if has_today else None

    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _, _) in enumerate(points))
    area = line + f" L{points[-1][0]:.1f},{pad_t + ch} L{points[0][0]:.1f},{pad_t + ch} Z"

    # Y-axis: 3 ticks
    y_ticks = ""
    for v in [min_eq, (min_eq + max_eq) / 2, max_eq]:
        yp = pad_t + ch - ((v - min_eq) / rng) * ch
        label = f"${v / 1000:.1f}k"
        y_ticks += f'<line x1="{pad_l}" y1="{yp:.1f}" x2="{w - pad_r}" y2="{yp:.1f}" stroke="#e2e8f0" stroke-width="0.5"/>'
        y_ticks += f'<text x="{pad_l - 5}" y="{yp + 3:.1f}" text-anchor="end" fill="#94a3b8" font-size="9" font-family="system-ui">{label}</text>'

    # Starting equity reference
    start_y = pad_t + ch - ((STARTING_EQUITY - min_eq) / rng) * ch
    ref_line = ""
    if min_eq <= STARTING_EQUITY <= max_eq:
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
    # Each rect covers the Voronoi region for that point (half-intervals)
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
    <path d="{line}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{color}"/>
    {today_svg}
    {x_labels}
    {hover_rects}
  </svg>
</div>{js}"""


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

    # Equity sparkline chart — pass live equity as today's intraday point
    chart_html = _render_equity_chart(s.get("alpaca_equity_history") or [], today_equity=equity)

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
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    now_str = now.strftime("%Y-%m-%d %H:%M ET")

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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Attix Dashboard</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="top-bar">
  <div><span class="brand">Attix</span> &nbsp; <span class="live-dot"></span> Live Paper Trading</div>
  <div style="display:flex;align-items:center;gap:16px">
    <span>Updated {now_str} &nbsp;&bull;&nbsp; Refresh in <span id="cd">300s</span></span>
    <a href="/logout" class="logout-btn">Sign out</a>
  </div>
</div>
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
