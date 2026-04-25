"""Walk-Forward Stability Deep Dive — v8a + VIX Ladder.

Produces a comprehensive per-fold stability report. For each of the
20 walk-forward folds (EXP-2850 methodology):
  - Equity curve (SVG inline)
  - Rolling 20-day Sharpe within fold
  - Worst drawdown period (start, depth, recovery)
  - Drawdown-driver attribution (which stream hurt most)
  - VIX + ladder exposure over the fold

Plus pooled-level views:
  - Full 5-year OOS equity curve
  - Pooled 60-day rolling Sharpe
  - Pooled drawdown underwater plot

All data comes from re-running the EXP-2850 pipeline (LW risk-parity
+ 12% vol target + VIX ladder + 890 bps drag). No cached fold-level
daily returns exist — they have to be regenerated.

Rule Zero: reuses EXP-2450 sparse cube + EXP-2250 QQQ cache + real
Yahoo ^VIX. No synthetic.

Output
  compass/reports/wf_stability_deep_dive.html
  compass/reports/wf_stability_deep_dive.json
"""

from __future__ import annotations

import json
import math
import pickle
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compass.vix_ladder import VIXLadder, fetch_vix

REPORT_HTML = ROOT / "compass" / "reports" / "wf_stability_deep_dive.html"
REPORT_JSON = ROOT / "compass" / "reports" / "wf_stability_deep_dive.json"
QQQ_TRADES_PKL = ROOT / "compass" / "cache" / "exp2250_qqq_trades.pkl"

CAPITAL = 100_000
TRADING_DAYS = 252
TRAIN_DAYS = 252
TEST_DAYS = 63
TARGET_VOL = 0.12
SCALE_CAP = 20.0
NET_DRAG_PCT = 8.903
STREAMS = ["exp1220", "v5_hedge", "gld_cal", "slv_cal",
           "cross_vol", "xlf_cs", "xli_cs", "qqq_cs"]


# ═══════════════════════════════════════════════════════════════════════════
# Cube + walk-forward (reuse EXP-2850 logic)
# ═══════════════════════════════════════════════════════════════════════════

def build_v8a_cube() -> pd.DataFrame:
    from compass.exp2450_sparse_combined_honest import build_sparse_seven_stream_cube
    base = build_sparse_seven_stream_cube()
    qqq_trades = pickle.load(QQQ_TRADES_PKL.open("rb"))
    qqq = pd.Series(0.0, index=base.index, name="qqq_cs")
    for t in qqq_trades:
        d = pd.Timestamp(t["exit_date"])
        if d in qqq.index:
            qqq.loc[d] += float(t["pnl"]) / CAPITAL
    cube = base.copy()
    cube["qqq_cs"] = qqq
    return cube[STREAMS]


@dataclass
class FoldDetail:
    fold: int
    test_start: str
    test_end: str
    n_days: int
    vol_scale: float
    weights: Dict[str, float]
    daily_gross: pd.Series         # raw gross OOS returns
    daily_laddered_gross: pd.Series  # after ladder multiplier, before drag
    daily_net: pd.Series            # after ladder + drag
    daily_exposure: pd.Series       # ladder exposure per day
    daily_stream_contrib: pd.DataFrame  # per-stream contribution per day
    metrics: Dict = field(default_factory=dict)


def walk_forward(cube: pd.DataFrame, vix: pd.Series,
                  ladder: VIXLadder) -> Tuple[List[FoldDetail], pd.Series]:
    from compass.exp2360_robust_cov import cov_ledoit_wolf, risk_parity_weights

    daily_drag = NET_DRAG_PCT / 100.0 / TRADING_DAYS
    folds: List[FoldDetail] = []
    pooled_net = []

    n = len(cube)
    i = TRAIN_DAYS
    fold_ix = 0
    while i + TEST_DAYS <= n:
        train = cube.iloc[i - TRAIN_DAYS:i]
        test = cube.iloc[i:i + TEST_DAYS]
        Sigma = cov_ledoit_wolf(train.values)
        w = risk_parity_weights(Sigma)
        train_port = train.values @ w
        train_vol = float(np.std(train_port, ddof=1)) * math.sqrt(TRADING_DAYS)
        scale = TARGET_VOL / train_vol if train_vol > 1e-10 else 1.0
        scale = float(np.clip(scale, 0.1, SCALE_CAP))

        # Per-stream contribution (weighted × scaled) per day
        # contrib_i(t) = w_i × scale × test[stream_i](t)
        stream_contrib = pd.DataFrame(
            test.values * w * scale,
            index=test.index,
            columns=list(cube.columns),
        )
        gross_daily = stream_contrib.sum(axis=1)

        # Ladder exposure (causal)
        vix_slice = vix.reindex(test.index).ffill().bfill()
        exposure = ladder.apply(vix_slice)

        laddered_gross = gross_daily * exposure
        net_daily = laddered_gross - daily_drag

        # Stream contribution AFTER ladder (for DD attribution)
        stream_contrib_laddered = stream_contrib.multiply(exposure, axis=0)

        fold = FoldDetail(
            fold=fold_ix,
            test_start=str(test.index[0].date()),
            test_end=str(test.index[-1].date()),
            n_days=len(test),
            vol_scale=round(scale, 3),
            weights={c: round(float(w[j]), 4) for j, c in enumerate(cube.columns)},
            daily_gross=gross_daily,
            daily_laddered_gross=laddered_gross,
            daily_net=net_daily,
            daily_exposure=exposure,
            daily_stream_contrib=stream_contrib_laddered,
        )
        fold.metrics = fold_metrics(net_daily)
        folds.append(fold)

        pooled_net.append(net_daily)
        i += TEST_DAYS
        fold_ix += 1

    return folds, pd.concat(pooled_net).sort_index()


def fold_metrics(r: pd.Series) -> Dict:
    r = r.dropna()
    n = len(r)
    if n < 2:
        return {"n": n, "sharpe": 0.0, "cagr_pct": 0.0,
                "max_dd_pct": 0.0, "vol_pct": 0.0}
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    sh = mu / sd * math.sqrt(TRADING_DAYS) if sd > 1e-12 else 0.0
    eq = (1 + r).cumprod()
    years = n / TRADING_DAYS
    cagr = float(eq.iloc[-1] ** (1 / max(years, 1e-9)) - 1) if eq.iloc[-1] > 0 else -1.0
    hwm = eq.cummax()
    dd_series = 1 - eq / hwm
    dd = float(dd_series.max())
    return {
        "n": n,
        "sharpe": round(sh, 3),
        "cagr_pct": round(cagr * 100, 3),
        "max_dd_pct": round(dd * 100, 3),
        "vol_pct": round(sd * math.sqrt(TRADING_DAYS) * 100, 3),
        "calmar": round(cagr / dd, 3) if dd > 1e-9 else 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Drawdown analysis
# ═══════════════════════════════════════════════════════════════════════════

def worst_dd_window(r: pd.Series) -> Dict:
    """Find the peak, trough, and recovery of the worst drawdown."""
    if len(r) < 2:
        return {}
    eq = (1 + r).cumprod()
    hwm = eq.cummax()
    dd = 1 - eq / hwm
    trough_idx = int(dd.values.argmax())
    trough_val = float(dd.iloc[trough_idx])
    trough_date = r.index[trough_idx]
    # Peak = last HWM before trough
    peak_eq = hwm.iloc[trough_idx]
    peak_search = eq.iloc[:trough_idx + 1][eq.iloc[:trough_idx + 1] >= peak_eq - 1e-12]
    peak_date = peak_search.index[-1] if len(peak_search) else r.index[0]
    # Recovery = first index after trough where eq back to peak_eq
    post = eq.iloc[trough_idx:]
    rec_idx = post[post >= peak_eq].index
    rec_date = rec_idx[0] if len(rec_idx) > 0 else None
    peak_i = r.index.get_loc(peak_date)
    dd_days = trough_idx - peak_i
    recovery_days = None
    if rec_date is not None:
        rec_i = r.index.get_loc(rec_date)
        recovery_days = rec_i - trough_idx
    return {
        "peak_date": str(peak_date.date()) if hasattr(peak_date, "date") else str(peak_date),
        "trough_date": str(trough_date.date()) if hasattr(trough_date, "date") else str(trough_date),
        "recovery_date": str(rec_date.date()) if rec_date is not None else None,
        "depth_pct": round(trough_val * 100, 3),
        "dd_days": int(dd_days),
        "recovery_days": recovery_days,
    }


def dd_driver_attribution(fold: FoldDetail) -> Dict:
    """Identify which stream contributed most to the worst DD.

    Measures each stream's cumulative contribution from peak to trough
    of the worst DD window on the net daily returns.
    """
    dd_info = worst_dd_window(fold.daily_net)
    if not dd_info or not dd_info.get("peak_date"):
        return {"worst_dd": dd_info, "drivers": []}
    peak = pd.Timestamp(dd_info["peak_date"])
    trough = pd.Timestamp(dd_info["trough_date"])
    # Slice the stream contribution frame from peak+1 to trough inclusive
    idx = fold.daily_stream_contrib.index
    mask = (idx > peak) & (idx <= trough)
    sub = fold.daily_stream_contrib.loc[mask]
    if len(sub) == 0:
        return {"worst_dd": dd_info, "drivers": []}
    per_stream_pnl = sub.sum(axis=0)
    sorted_streams = per_stream_pnl.sort_values()
    # Convert to dollar-equivalent pct of capital
    drivers = [
        {"stream": s, "contribution_pct": round(float(v) * 100, 4)}
        for s, v in sorted_streams.items()
    ]
    return {"worst_dd": dd_info, "drivers": drivers}


# ═══════════════════════════════════════════════════════════════════════════
# Regime tagging (VIX buckets)
# ═══════════════════════════════════════════════════════════════════════════

def tag_regime(vix: pd.Series) -> pd.Series:
    def bucket(v):
        if pd.isna(v):
            return "unknown"
        if v < 15:
            return "low_vol"
        if v < 20:
            return "normal"
        if v < 25:
            return "elevated"
        if v < 30:
            return "high"
        if v < 40:
            return "stress"
        return "crisis"
    return vix.apply(bucket)


# ═══════════════════════════════════════════════════════════════════════════
# Rolling Sharpe
# ═══════════════════════════════════════════════════════════════════════════

def rolling_sharpe(r: pd.Series, window: int = 60) -> pd.Series:
    out = pd.Series(np.nan, index=r.index)
    if len(r) < window:
        return out
    rolling_mean = r.rolling(window).mean()
    rolling_std = r.rolling(window).std(ddof=1)
    out = (rolling_mean / rolling_std.replace(0, np.nan)) * math.sqrt(TRADING_DAYS)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Inline SVG renderers
# ═══════════════════════════════════════════════════════════════════════════

def _scale(values: List[float], lo: float, hi: float) -> List[float]:
    if not values:
        return []
    v_min = min(values)
    v_max = max(values)
    if v_max - v_min < 1e-12:
        return [(lo + hi) / 2] * len(values)
    return [lo + (hi - lo) * (v - v_min) / (v_max - v_min) for v in values]


def svg_equity_curve(r: pd.Series, width: int = 360, height: int = 90,
                      color: str = "#2563eb", title: str = "") -> str:
    if len(r) < 2:
        return f"<svg width='{width}' height='{height}'></svg>"
    eq = (1 + r).cumprod().values
    n = len(eq)
    pad = 6
    xs = [pad + i * (width - 2 * pad) / (n - 1) for i in range(n)]
    ys = _scale(list(eq), height - pad, pad)
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    hwm = np.maximum.accumulate(eq)
    dd = 1 - eq / hwm
    worst_i = int(dd.argmax())
    wx, wy = xs[worst_i], ys[worst_i]
    label_lo = f"{(eq[0]-1)*100:+.1f}%"
    label_hi = f"{(eq[-1]-1)*100:+.1f}%"
    return f"""<svg width="{width}" height="{height}" style="background:#fff;border:1px solid #e5e7eb;border-radius:4px">
<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.7"/>
<circle cx="{wx:.1f}" cy="{wy:.1f}" r="2.5" fill="#dc2626"/>
<text x="{pad}" y="{height - 2}" font-size="9" fill="#64748b">{label_lo}</text>
<text x="{width - 32}" y="{height - 2}" font-size="9" fill="#64748b">{label_hi}</text>
<text x="{width / 2}" y="11" font-size="10" fill="#334155" text-anchor="middle">{title}</text>
</svg>"""


def svg_dd_underwater(r: pd.Series, width: int = 360, height: int = 60,
                       color: str = "#dc2626") -> str:
    if len(r) < 2:
        return f"<svg width='{width}' height='{height}'></svg>"
    eq = (1 + r).cumprod().values
    hwm = np.maximum.accumulate(eq)
    dd = -(1 - eq / hwm) * 100
    n = len(dd)
    pad = 4
    xs = [pad + i * (width - 2 * pad) / (n - 1) for i in range(n)]
    dd_min = min(dd.min(), -0.01)
    ys = [pad + (height - 2 * pad) * (d - 0) / (dd_min - 0) for d in dd]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    zero_y = pad + 0
    polygon_pts = f"{pad},{zero_y} " + pts + f" {xs[-1]:.1f},{zero_y}"
    return f"""<svg width="{width}" height="{height}" style="background:#fff;border:1px solid #e5e7eb;border-radius:4px">
<polygon points="{polygon_pts}" fill="{color}" fill-opacity="0.25" stroke="{color}" stroke-width="1"/>
<text x="{pad + 4}" y="{height - 4}" font-size="9" fill="#64748b">min {dd_min:.1f}%</text>
</svg>"""


def svg_rolling_sharpe(r: pd.Series, window: int = 20,
                        width: int = 360, height: int = 60,
                        color: str = "#16a34a") -> str:
    roll = rolling_sharpe(r, window=window).dropna()
    if len(roll) < 2:
        return f"<svg width='{width}' height='{height}'></svg>"
    vals = roll.values
    n = len(vals)
    pad = 4
    xs = [pad + i * (width - 2 * pad) / (n - 1) for i in range(n)]
    v_min = min(vals.min(), 0)
    v_max = max(vals.max(), 6.0)
    span = v_max - v_min
    ys = [height - pad - (v - v_min) / span * (height - 2 * pad) for v in vals]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    # Reference line at Sharpe = 6
    y6 = height - pad - (6.0 - v_min) / span * (height - 2 * pad)
    return f"""<svg width="{width}" height="{height}" style="background:#fff;border:1px solid #e5e7eb;border-radius:4px">
<line x1="{pad}" y1="{y6:.1f}" x2="{width - pad}" y2="{y6:.1f}" stroke="#94a3b8" stroke-dasharray="2,3" stroke-width="0.8"/>
<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6"/>
<text x="{pad + 4}" y="10" font-size="9" fill="#64748b">{window}d SR {vals[-1]:.2f}</text>
<text x="{width - 42}" y="{y6 - 2:.1f}" font-size="8" fill="#94a3b8">SR=6</text>
</svg>"""


def svg_exposure(exp: pd.Series, width: int = 360, height: int = 50) -> str:
    if len(exp) < 2:
        return f"<svg width='{width}' height='{height}'></svg>"
    vals = exp.values
    n = len(vals)
    pad = 4
    xs = [pad + i * (width - 2 * pad) / (n - 1) for i in range(n)]
    ys = [height - pad - v * (height - 2 * pad) for v in vals]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    avg = float(np.mean(vals))
    return f"""<svg width="{width}" height="{height}" style="background:#fff;border:1px solid #e5e7eb;border-radius:4px">
<line x1="{pad}" y1="{pad}" x2="{width - pad}" y2="{pad}" stroke="#e5e7eb" stroke-width="0.5"/>
<polyline points="{pts}" fill="none" stroke="#a855f7" stroke-width="1.6"/>
<text x="{pad + 4}" y="{height - 4}" font-size="9" fill="#64748b">avg {avg:.2f}× · min {float(vals.min()):.2f}×</text>
</svg>"""


# ═══════════════════════════════════════════════════════════════════════════
# HTML renderer
# ═══════════════════════════════════════════════════════════════════════════

def render_html(folds: List[FoldDetail], pooled_net: pd.Series,
                 vix: pd.Series, ladder: VIXLadder) -> str:
    regimes = tag_regime(vix)

    # Pooled view
    pooled_m = fold_metrics(pooled_net)
    pooled_roll60 = rolling_sharpe(pooled_net, window=60)

    pooled_equity_svg = svg_equity_curve(
        pooled_net, width=1100, height=180,
        color="#0ea5e9", title=f"Pooled NET equity — Sharpe {pooled_m['sharpe']:.2f}, CAGR {pooled_m['cagr_pct']:+.1f}%",
    )
    pooled_roll_svg = svg_rolling_sharpe(
        pooled_net, window=60, width=1100, height=140,
        color="#16a34a",
    )
    pooled_dd_svg = svg_dd_underwater(
        pooled_net, width=1100, height=120,
    )

    # Fold rows
    fold_blocks = ""
    fold_summary_rows = ""
    for f in folds:
        m = f.metrics
        dd_attrib = dd_driver_attribution(f)
        worst = dd_attrib["worst_dd"]
        drivers = dd_attrib["drivers"][:3]
        worst_stream = drivers[0]["stream"] if drivers else "—"

        # Regime distribution inside the fold
        fold_regimes = regimes.reindex(f.daily_net.index).fillna("unknown")
        regime_counts = fold_regimes.value_counts()
        regime_dominant = regime_counts.idxmax() if len(regime_counts) else "unknown"
        regime_txt = "  ·  ".join(
            f"{r}: {c}d" for r, c in regime_counts.head(4).items()
        )

        # SVG panels
        eq_svg = svg_equity_curve(
            f.daily_net, title=f"Fold {f.fold}  {f.test_start}→{f.test_end}",
        )
        dd_svg = svg_dd_underwater(f.daily_net)
        rs_svg = svg_rolling_sharpe(f.daily_net, window=20)
        ex_svg = svg_exposure(f.daily_exposure)

        # Worst DD cause text
        if worst:
            recovery_txt = (f"{worst['recovery_days']}d"
                            if worst['recovery_days'] is not None
                            else "no recovery in fold")
            dd_line = (
                f"Worst DD: <strong>−{worst['depth_pct']:.2f}%</strong> "
                f"from {worst['peak_date']} to {worst['trough_date']} "
                f"({worst['dd_days']}d draw, {recovery_txt} recovery). "
                f"Driver: <strong>{worst_stream}</strong>"
            )
        else:
            dd_line = "No drawdown in fold."

        # Top 3 stream contributions during the DD
        if drivers:
            driver_rows = ""
            for d in drivers:
                color = "#dc2626" if d["contribution_pct"] < 0 else "#16a34a"
                driver_rows += (
                    f"<li><span style='font-family:monospace'>{d['stream']:10s}</span> "
                    f"<span style='color:{color};font-weight:700'>"
                    f"{d['contribution_pct']:+.3f}%</span></li>"
                )
            driver_list = f"<ul class='drivers'>{driver_rows}</ul>"
        else:
            driver_list = ""

        # Weight snapshot
        weight_html = "  ·  ".join(
            f"<code>{s}</code> {f.weights.get(s, 0) * 100:.1f}%"
            for s in STREAMS[:4]
        )

        # Card
        sr_color = "#16a34a" if m["sharpe"] >= 6.0 else ("#f59e0b" if m["sharpe"] >= 5.0 else "#dc2626")
        fold_blocks += f"""
<section class="fold" id="fold-{f.fold}">
  <header>
    <span class="fold-num">Fold {f.fold}</span>
    <span class="fold-dates">{f.test_start} → {f.test_end}</span>
    <span class="fold-sharpe" style="color:{sr_color}">SR {m['sharpe']:.2f}</span>
    <span class="fold-cagr">CAGR {m['cagr_pct']:+.1f}%</span>
    <span class="fold-dd">DD {m['max_dd_pct']:.2f}%</span>
    <span class="fold-scale">vol_scale {f.vol_scale:.2f}×</span>
  </header>
  <div class="panels">
    <div>{eq_svg}</div>
    <div>{dd_svg}</div>
    <div>{rs_svg}</div>
    <div>{ex_svg}</div>
  </div>
  <div class="meta">
    <div class="dd-line">{dd_line}</div>
    <div class="drivers-wrap">{driver_list}</div>
    <div class="regime">Regime mix: <code>{regime_dominant}</code> dominant &middot; {regime_txt}</div>
    <div class="weights">Top weights: {weight_html}</div>
  </div>
</section>
"""
        fold_summary_rows += (
            f"<tr><td><a href='#fold-{f.fold}'>{f.fold}</a></td>"
            f"<td>{f.test_start}</td><td>{f.test_end}</td>"
            f"<td style='color:{sr_color};font-weight:700'>{m['sharpe']:.2f}</td>"
            f"<td>{m['cagr_pct']:+.1f}%</td>"
            f"<td>{m['max_dd_pct']:.2f}%</td>"
            f"<td>{f.vol_scale:.2f}×</td>"
            f"<td>{f.daily_exposure.mean():.2f}</td>"
            f"<td>{regime_dominant}</td>"
            f"<td>{worst_stream}</td></tr>"
        )

    # Distribution stats
    sharpes = [f.metrics["sharpe"] for f in folds]
    dds = [f.metrics["max_dd_pct"] for f in folds]
    cagrs = [f.metrics["cagr_pct"] for f in folds]
    sharpe_dist = {
        "min": round(min(sharpes), 3),
        "p25": round(float(np.percentile(sharpes, 25)), 3),
        "median": round(float(np.median(sharpes)), 3),
        "p75": round(float(np.percentile(sharpes, 75)), 3),
        "max": round(max(sharpes), 3),
        "mean": round(float(np.mean(sharpes)), 3),
        "std": round(float(np.std(sharpes, ddof=1)), 3),
    }
    pct_above_6 = round(float(np.mean(np.array(sharpes) >= 6.0) * 100), 1)
    pct_above_5 = round(float(np.mean(np.array(sharpes) >= 5.0) * 100), 1)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Walk-Forward Stability Deep Dive — v8a + VIX Ladder</title>
<style>
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       max-width:1200px;margin:0 auto;padding:28px;background:#f8fafc;color:#1e293b; }}
h1 {{ font-size:1.85em;color:#0f172a;margin:0 0 4px; }}
h2 {{ margin-top:2em;padding-bottom:8px;border-bottom:2px solid #e2e8f0;color:#334155; }}
.subtitle {{ color:#64748b;font-size:0.9rem;margin-bottom:20px; }}
.sources {{ background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:14px;font-size:0.84rem;line-height:1.6; }}
.kpi-row {{ display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:18px 0; }}
.kpi {{ background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;text-align:center; }}
.kpi .v {{ font-size:1.5em;font-weight:800;color:#0f172a; }}
.kpi .l {{ font-size:0.72em;color:#64748b;margin-top:4px;text-transform:uppercase; }}
table {{ width:100%;border-collapse:collapse;margin:12px 0;font-size:0.84em;background:#fff; }}
th {{ background:#f1f5f9;padding:9px 11px;text-align:right;border-bottom:2px solid #cbd5e1;font-size:0.72em;text-transform:uppercase; }}
th:first-child, th:nth-child(2), th:nth-child(3), th:nth-child(9), th:nth-child(10) {{ text-align:left; }}
td {{ padding:7px 11px;text-align:right;border-bottom:1px solid #e2e8f0; }}
td:first-child, td:nth-child(2), td:nth-child(3), td:nth-child(9), td:nth-child(10) {{ text-align:left; }}
td a {{ color:#0ea5e9;text-decoration:none;font-weight:600; }}
.fold {{ background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin:20px 0;
       box-shadow:0 1px 2px rgba(0,0,0,0.03); }}
.fold header {{ display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;margin-bottom:10px;
                padding-bottom:8px;border-bottom:1px solid #f1f5f9;font-size:0.92em; }}
.fold-num {{ font-weight:800;color:#0f172a;font-size:1.05em; }}
.fold-dates {{ color:#64748b;font-family:monospace; }}
.fold-sharpe {{ font-weight:700; }}
.fold-cagr, .fold-dd, .fold-scale {{ color:#475569; }}
.panels {{ display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:10px 0; }}
.meta {{ font-size:0.84em;line-height:1.55;color:#475569;margin-top:10px; }}
.meta .dd-line {{ margin-bottom:6px; }}
.drivers {{ margin:4px 0 6px 0;padding:0;list-style:none;font-family:monospace; }}
.drivers li {{ display:inline-block;margin-right:16px;font-size:0.9em; }}
.regime {{ color:#64748b;font-size:0.85em; }}
.weights {{ color:#64748b;font-size:0.85em; }}
.note {{ background:#fefce8;border:1px solid #fde047;border-radius:6px;padding:12px 16px;font-size:0.86rem;margin:14px 0; }}
</style></head><body>

<h1>Walk-Forward Stability Deep Dive</h1>
<div class="subtitle">v8a 8-stream portfolio + VIX ladder · 20-fold walk-forward ·
12% vol target · 890 bps drag · {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>

<div class="sources">
<strong>Rule Zero — real data:</strong> EXP-2450 sparse 7-stream cube +
EXP-2250 cached QQQ trades (8 streams total). Yahoo ^VIX daily close
(causal shift-1d). compass.vix_ladder.VIXLadder default
(EXP-2820 winning 9-breakpoint step-linear ramp). compass.exp2360_robust_cov
Ledoit-Wolf covariance + risk-parity weights. Drag 890 bps/yr (EXP-2570).
</div>

<h2>Pooled OOS headline</h2>
<div class="kpi-row">
<div class="kpi"><div class="v">{pooled_m['sharpe']:.2f}</div><div class="l">Pooled Net Sharpe</div></div>
<div class="kpi"><div class="v">{pooled_m['cagr_pct']:+.1f}%</div><div class="l">Pooled Net CAGR</div></div>
<div class="kpi"><div class="v">{pooled_m['max_dd_pct']:.2f}%</div><div class="l">Pooled Max DD</div></div>
<div class="kpi"><div class="v">{pooled_m['vol_pct']:.1f}%</div><div class="l">Pooled Vol</div></div>
<div class="kpi"><div class="v">{len(folds)}</div><div class="l">Walk-Forward Folds</div></div>
</div>

<div style="text-align:center">{pooled_equity_svg}</div>
<div style="text-align:center">{pooled_dd_svg}</div>
<div style="text-align:center">{pooled_roll_svg}</div>

<h2>Per-fold distribution</h2>
<div class="kpi-row">
<div class="kpi"><div class="v">{sharpe_dist['min']:.2f}</div><div class="l">Min Fold SR</div></div>
<div class="kpi"><div class="v">{sharpe_dist['median']:.2f}</div><div class="l">Median Fold SR</div></div>
<div class="kpi"><div class="v">{sharpe_dist['max']:.2f}</div><div class="l">Max Fold SR</div></div>
<div class="kpi"><div class="v">{pct_above_6:.0f}%</div><div class="l">Folds ≥ 6.0</div></div>
<div class="kpi"><div class="v">{pct_above_5:.0f}%</div><div class="l">Folds ≥ 5.0</div></div>
</div>

<h2>Fold summary table</h2>
<table>
<thead><tr>
<th>#</th><th>Test start</th><th>Test end</th>
<th>Net SR</th><th>Net CAGR</th><th>Max DD</th>
<th>Scale</th><th>Exposure</th>
<th>Dom regime</th><th>DD driver</th>
</tr></thead>
<tbody>{fold_summary_rows}</tbody>
</table>

<h2>Per-fold deep dive (click fold number in table above to jump)</h2>
<div class="note">
Each fold card shows four panels: <strong>equity curve</strong> (blue, red
dot marks the worst-DD trough), <strong>drawdown underwater</strong> (red
area), <strong>rolling 20-day Sharpe</strong> (green, dashed line at SR=6),
and <strong>ladder exposure</strong> (purple, top edge = 1.0× full
exposure). The meta block below each card lists the worst DD window,
top 3 stream contributors during the DD, dominant VIX regime, and
the fold's top 4 weights.
</div>

{fold_blocks}

<p style="margin-top:3em;color:#94a3b8;font-size:0.78em;text-align:center">
compass/wf_stability_deep_dive.py · Rule Zero · real data only
</p>
</body></html>"""


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 72)
    print("Walk-Forward Stability Deep Dive — v8a + VIX ladder")
    print("=" * 72)

    print("\n[1/4] Building v8a cube...")
    cube = build_v8a_cube()
    print(f"       shape {cube.shape}  range {cube.index[0].date()} → {cube.index[-1].date()}")

    print("\n[2/4] Loading real Yahoo ^VIX (causal)...")
    vix_start = (cube.index.min() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    vix_end = (cube.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    vix = fetch_vix(vix_start, vix_end).shift(1).ffill().bfill()

    print("\n[3/4] Running walk-forward with per-fold daily capture...")
    ladder = VIXLadder()
    folds, pooled_net = walk_forward(cube, vix, ladder)
    print(f"       {len(folds)} folds, pooled {len(pooled_net)} days")

    pooled_m = fold_metrics(pooled_net)
    print(f"       pooled NET: SR {pooled_m['sharpe']:.3f}  "
          f"CAGR {pooled_m['cagr_pct']:+.1f}%  DD {pooled_m['max_dd_pct']:.2f}%")

    # Per-fold DD attribution summary
    print("\n[per-fold worst DD driver]")
    for f in folds:
        attrib = dd_driver_attribution(f)
        worst = attrib["worst_dd"]
        drivers = attrib["drivers"]
        if not worst:
            continue
        wstream = drivers[0]["stream"] if drivers else "—"
        wpct = drivers[0]["contribution_pct"] if drivers else 0.0
        print(f"  fold {f.fold:2d}  {f.test_start}  "
              f"DD -{worst['depth_pct']:5.2f}%  driver={wstream:10s} "
              f"contrib {wpct:+.3f}%  fold SR {f.metrics['sharpe']:5.2f}")

    print("\n[4/4] Rendering HTML report...")
    html = render_html(folds, pooled_net, vix, ladder)
    REPORT_HTML.parent.mkdir(parents=True, exist_ok=True)
    REPORT_HTML.write_text(html, encoding="utf-8")
    print(f"       → {REPORT_HTML}  ({len(html) / 1024:.0f} KB)")

    # JSON sidecar
    payload = {
        "experiment": "wf_stability_deep_dive",
        "title": "Walk-Forward Stability Deep Dive (v8a + VIX ladder)",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "rule_zero": True,
        "sources": {
            "cube": "EXP-2450 sparse 7-stream + EXP-2250 cached QQQ",
            "vix": "Yahoo ^VIX daily close (causal shift-1d)",
            "ladder": "compass.vix_ladder.VIXLadder (EXP-2820 winner)",
            "drag_rate": f"EXP-2570 {NET_DRAG_PCT}%/yr",
        },
        "config": {
            "target_vol": TARGET_VOL,
            "scale_cap": SCALE_CAP,
            "train_days": TRAIN_DAYS,
            "test_days": TEST_DAYS,
            "n_folds": len(folds),
        },
        "pooled_metrics": pooled_m,
        "folds": [
            {
                "fold": f.fold,
                "test_start": f.test_start,
                "test_end": f.test_end,
                "vol_scale": f.vol_scale,
                "weights": f.weights,
                "metrics": f.metrics,
                "avg_exposure": round(float(f.daily_exposure.mean()), 4),
                "min_exposure": round(float(f.daily_exposure.min()), 4),
                "dd_attribution": dd_driver_attribution(f),
            }
            for f in folds
        ],
    }
    REPORT_JSON.write_text(json.dumps(payload, indent=2, default=str))
    print(f"       → {REPORT_JSON}")


if __name__ == "__main__":
    main()
