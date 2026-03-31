#!/usr/bin/env python3
"""
EXP-950-max: Leverage Optimization Deep Dive

Sweeps leverage from 1.0x–4.0x with crisis hedge overlay.
Finds Kelly-optimal, max-Sharpe, and max-return-at-DD<12% leverage.
Runs Monte Carlo stress at each level.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA_CANDIDATES = [
    ROOT.parent.parent / "compass" / "training_data_combined.csv",
    Path("/home/node/.openclaw/workspace/pilotai-compass/experiments/training_data_combined.csv"),
    Path("/home/node/.openclaw/workspace/pilotai-credit-spreads/compass/training_data_combined.csv"),
]
RESULTS_DIR = ROOT / "results"
INITIAL_CAPITAL = 100_000.0
TRADING_DAYS = 252

# ML filter: simulate EXP-710 P>=0.75 characteristics
ML_WIN_RATE = 0.893
ML_SELECTIVITY = 0.432

# Crisis hedge parameters (from EXP-880 V2)
CRISIS_HEDGE_COST_BPS_ANNUAL = 33  # 0.33% annual drag
CRISIS_DD_REDUCTION = 0.40  # hedge reduces DD by 40% during crises
CRISIS_VIX_THRESHOLD = 25  # activate hedge above this VIX

# Leverage sweep
LEV_MIN, LEV_MAX, LEV_STEP = 1.0, 4.0, 0.25
SLIPPAGE_BPS = 5.0
COMMISSION_PER_CONTRACT = 1.30

# Regime leverage multipliers
REGIME_MULT = {"bull": 1.2, "sideways": 0.8, "bear": 0.3, "high_vol": 0.2, "crisis": 0.1}


# ── Data loading ─────────────────────────────────────────────────────────


def load_data() -> pd.DataFrame:
    for p in DATA_CANDIDATES:
        if p.exists():
            df = pd.read_csv(p, parse_dates=["entry_date", "exit_date"])
            df["year"] = pd.to_datetime(df["entry_date"]).dt.year
            return df
    raise FileNotFoundError("training_data_combined.csv not found")


def simulate_ml_filter(df: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """Simulate ML filter keeping ~43% of trades with ~89% win rate."""
    rng = np.random.RandomState(seed)
    scores = np.zeros(len(df))
    wins = df["win"].values.astype(float)
    for i in range(len(df)):
        scores[i] = rng.beta(4, 2) if wins[i] == 1 else rng.beta(2, 4)
    # Calibrate threshold
    n_pass = int(len(df) * ML_SELECTIVITY)
    threshold = np.sort(scores)[::-1][min(n_pass, len(scores) - 1)]
    return df[scores >= threshold].copy()


# ── Metrics ──────────────────────────────────────────────────────────────


def compute_metrics(pnls: np.ndarray, capital: float, n_years: int) -> Dict[str, float]:
    if len(pnls) == 0:
        return {"cagr": 0, "sharpe": 0, "sortino": 0, "max_dd": 0, "calmar": 0,
                "win_rate": 0, "pf": 0, "total_pnl": 0, "final_capital": capital}
    equity = capital + np.cumsum(pnls)
    final = float(equity[-1])
    total_ret = final / capital
    cagr = (total_ret ** (1.0 / max(n_years, 1)) - 1) * 100 if total_ret > 0 else -100

    mu = pnls.mean()
    std = pnls.std(ddof=1) if len(pnls) > 1 else 1.0
    sharpe = mu / std * math.sqrt(TRADING_DAYS) if std > 1e-12 else 0.0

    down = pnls[pnls < 0]
    ds = np.sqrt(np.mean(down ** 2)) if len(down) > 0 else 1.0
    sortino = mu / ds * math.sqrt(TRADING_DAYS) if ds > 1e-12 else 0.0

    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1)
    max_dd = abs(float(dd.min())) * 100

    calmar = cagr / max_dd if max_dd > 0.01 else 0.0

    wins = (pnls > 0).sum()
    g = pnls[pnls > 0].sum()
    l = abs(pnls[pnls < 0].sum())
    pf = g / l if l > 1e-12 else 10.0

    # Per-year
    return {
        "cagr": cagr, "sharpe": sharpe, "sortino": sortino,
        "max_dd": max_dd, "calmar": calmar,
        "win_rate": wins / len(pnls), "pf": min(pf, 50),
        "total_pnl": float(pnls.sum()), "final_capital": final,
    }


def worst_year(pnls: np.ndarray, years: np.ndarray) -> float:
    """Worst single-year return %."""
    by_year = {}
    for pnl, y in zip(pnls, years):
        by_year[y] = by_year.get(y, 0.0) + pnl
    if not by_year:
        return 0.0
    return min(by_year.values()) / INITIAL_CAPITAL * 100


def monthly_returns(pnls: np.ndarray, dates: np.ndarray) -> np.ndarray:
    """Monthly P&L."""
    df = pd.DataFrame({"date": pd.to_datetime(dates), "pnl": pnls})
    try:
        monthly = df.set_index("date").resample("ME")["pnl"].sum()
    except ValueError:
        monthly = df.set_index("date").resample("M")["pnl"].sum()
    return monthly.values


# ── Leverage + crisis hedge backtest ─────────────────────────────────────


def run_at_leverage(
    df: pd.DataFrame,
    leverage: float,
    crisis_hedge: bool = True,
    regime_adaptive: bool = False,
) -> Dict[str, Any]:
    """Run backtest at a given leverage with optional crisis hedge."""
    pnls = []
    years_arr = []
    dates_arr = []

    annual_hedge_cost = INITIAL_CAPITAL * CRISIS_HEDGE_COST_BPS_ANNUAL / 10_000 if crisis_hedge else 0
    # Spread hedge cost across trades proportionally
    per_trade_hedge = annual_hedge_cost / max(len(df) / 6, 1)  # ~6 years

    for _, row in df.iterrows():
        regime = str(row.get("regime", "bull")).lower()
        vix = float(row.get("vix", 20))
        year = int(row.get("year", 2020))

        # Effective leverage
        if regime_adaptive:
            regime_mult = REGIME_MULT.get(regime, 0.5)
            eff_lev = leverage * regime_mult
        else:
            eff_lev = leverage

        # Crisis hedge: reduce losses during high-VIX periods
        raw_pnl = float(row.get("pnl", 0))

        if crisis_hedge and vix > CRISIS_VIX_THRESHOLD and raw_pnl < 0:
            # Hedge reduces loss magnitude
            raw_pnl *= (1.0 - CRISIS_DD_REDUCTION)

        # Apply leverage
        gross = raw_pnl * eff_lev

        # Costs scale with leverage
        contracts = max(int(row.get("contracts", 5)), 1)
        entry_p = abs(float(row.get("net_credit", 1.0)))
        slip = entry_p * 2 * SLIPPAGE_BPS / 10_000 * contracts * 100 * eff_lev
        comm = COMMISSION_PER_CONTRACT * contracts * 2 * eff_lev
        hedge_cost = per_trade_hedge * eff_lev if crisis_hedge else 0

        net = gross - slip - comm - hedge_cost
        pnls.append(net)
        years_arr.append(year)
        dates_arr.append(str(row.get("exit_date", "")))

    pnls_arr = np.array(pnls)
    years_np = np.array(years_arr)
    n_years = len(set(years_arr))

    metrics = compute_metrics(pnls_arr, INITIAL_CAPITAL, n_years)
    metrics["leverage"] = leverage
    metrics["crisis_hedge"] = crisis_hedge
    metrics["regime_adaptive"] = regime_adaptive
    metrics["n_trades"] = len(pnls)
    metrics["worst_year_pct"] = worst_year(pnls_arr, years_np)

    # Worst month
    mr = monthly_returns(pnls_arr, np.array(dates_arr))
    metrics["worst_month_pct"] = float(mr.min() / INITIAL_CAPITAL * 100) if len(mr) > 0 else 0.0
    metrics["best_month_pct"] = float(mr.max() / INITIAL_CAPITAL * 100) if len(mr) > 0 else 0.0

    # Per-year breakdown
    per_year = {}
    for pnl, y in zip(pnls, years_arr):
        per_year[y] = per_year.get(y, 0.0) + pnl
    metrics["per_year"] = {str(y): v / INITIAL_CAPITAL * 100 for y, v in sorted(per_year.items())}
    metrics["all_years_profitable"] = all(v > 0 for v in per_year.values())

    return metrics


# ── Kelly-optimal leverage ───────────────────────────────────────────────


def kelly_optimal(sweep_results: List[Dict]) -> Dict:
    """Find leverage maximising geometric growth rate (≈ log-return)."""
    best = max(sweep_results, key=lambda r: math.log(max(r["final_capital"], 1)) / max(r.get("n_years", 1), 1))
    return best


def max_sharpe_leverage(sweep_results: List[Dict]) -> Dict:
    """Find leverage maximising Sharpe ratio."""
    return max(sweep_results, key=lambda r: r["sharpe"])


def max_return_dd_constrained(sweep_results: List[Dict], max_dd: float = 12.0) -> Dict:
    """Find leverage maximising CAGR subject to DD < max_dd."""
    constrained = [r for r in sweep_results if r["max_dd"] <= max_dd]
    if not constrained:
        return min(sweep_results, key=lambda r: r["max_dd"])
    return max(constrained, key=lambda r: r["cagr"])


# ── Monte Carlo ──────────────────────────────────────────────────────────


def monte_carlo_at_leverage(
    df: pd.DataFrame, leverage: float, n_paths: int = 5000,
    horizon: int = 252, seed: int = 42,
) -> Dict[str, float]:
    """Bootstrap MC at a given leverage level."""
    rng = np.random.RandomState(seed)
    base_metrics = run_at_leverage(df, leverage, crisis_hedge=True)

    # Use per-trade PnL distribution
    pnls = []
    for _, row in df.iterrows():
        raw = float(row.get("pnl", 0)) * leverage
        pnls.append(raw)
    pnls = np.array(pnls)

    terminal_returns = np.zeros(n_paths)
    for p in range(n_paths):
        sample = rng.choice(pnls, size=min(horizon, len(pnls)), replace=True)
        terminal = (INITIAL_CAPITAL + sample.sum()) / INITIAL_CAPITAL - 1
        terminal_returns[p] = terminal

    return {
        "leverage": leverage,
        "prob_50_cagr": float((terminal_returns > 0.50).mean()),
        "prob_100_cagr": float((terminal_returns > 1.00).mean()),
        "prob_negative": float((terminal_returns < 0).mean()),
        "median_return": float(np.median(terminal_returns) * 100),
        "p5_return": float(np.percentile(terminal_returns, 5) * 100),
        "p95_return": float(np.percentile(terminal_returns, 95) * 100),
    }


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    print("EXP-950-max: Leverage Optimization Deep Dive")
    print("=" * 60)

    df_raw = load_data()
    df = simulate_ml_filter(df_raw)
    print(f"Data: {len(df_raw)} raw → {len(df)} ML-filtered trades")

    # ── Sweep leverage with crisis hedge ──────────────────────────────
    print("\n[1] Leverage sweep (with crisis hedge)...")
    leverages = np.arange(LEV_MIN, LEV_MAX + LEV_STEP / 2, LEV_STEP)
    sweep: List[Dict] = []
    for lev in leverages:
        r = run_at_leverage(df, lev, crisis_hedge=True)
        sweep.append(r)
        print(f"  {lev:.2f}x: CAGR={r['cagr']:.1f}%, DD={r['max_dd']:.1f}%, Sharpe={r['sharpe']:.2f}, Calmar={r['calmar']:.1f}")

    # ── Optimal leverage points ──────────────────────────────────────
    print("\n[2] Optimal leverage points...")
    kelly = kelly_optimal(sweep)
    max_sh = max_sharpe_leverage(sweep)
    max_ret = max_return_dd_constrained(sweep, 12.0)

    print(f"  Kelly-optimal:    {kelly['leverage']:.2f}x → CAGR={kelly['cagr']:.1f}%, DD={kelly['max_dd']:.1f}%")
    print(f"  Max-Sharpe:       {max_sh['leverage']:.2f}x → Sharpe={max_sh['sharpe']:.2f}, CAGR={max_sh['cagr']:.1f}%")
    print(f"  Max-return@DD<12: {max_ret['leverage']:.2f}x → CAGR={max_ret['cagr']:.1f}%, DD={max_ret['max_dd']:.1f}%")

    # ── Without crisis hedge (comparison) ────────────────────────────
    print("\n[3] Without crisis hedge (at key leverage points)...")
    for lev in [1.0, 2.0, 3.0, kelly["leverage"]]:
        r_no = run_at_leverage(df, lev, crisis_hedge=False)
        r_yes = next(s for s in sweep if abs(s["leverage"] - lev) < 0.01)
        print(f"  {lev:.2f}x: No hedge DD={r_no['max_dd']:.1f}% vs Hedged DD={r_yes['max_dd']:.1f}% (reduction={r_no['max_dd'] - r_yes['max_dd']:.1f}pp)")

    # ── Regime-conditional leverage ──────────────────────────────────
    print("\n[4] Regime-conditional leverage sweep...")
    regime_sweep: List[Dict] = []
    for lev in [2.0, 2.5, 3.0, 3.5, 4.0]:
        r = run_at_leverage(df, lev, crisis_hedge=True, regime_adaptive=True)
        regime_sweep.append(r)
        print(f"  Base {lev:.1f}x regime-adaptive: CAGR={r['cagr']:.1f}%, DD={r['max_dd']:.1f}%, Sharpe={r['sharpe']:.2f}")

    best_regime = max(regime_sweep, key=lambda r: r["calmar"])

    # ── Monte Carlo ──────────────────────────────────────────────────
    print("\n[5] Monte Carlo (5K paths per leverage)...")
    mc_results: List[Dict] = []
    for lev in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
        mc = monte_carlo_at_leverage(df, lev, n_paths=5000)
        mc_results.append(mc)
        print(f"  {lev:.1f}x: P(>50% CAGR)={mc['prob_50_cagr']:.1%}, P(>100%)={mc['prob_100_cagr']:.1%}, P(loss)={mc['prob_negative']:.1%}")

    # ── Can we hit 100% CAGR at DD<12%? ──────────────────────────────
    target_100 = [s for s in sweep if s["cagr"] >= 100]
    target_100_dd12 = [s for s in target_100 if s["max_dd"] <= 12.0]

    print(f"\n[6] 100% CAGR achievable at: {[s['leverage'] for s in target_100] if target_100 else 'NONE'}")
    print(f"    100% CAGR + DD<12%:   {[s['leverage'] for s in target_100_dd12] if target_100_dd12 else 'NONE'}")

    # ── Save results ─────────────────────────────────────────────────
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "experiment": "EXP-950-max",
        "description": "Leverage Optimization with Crisis Hedge",
        "data": {"raw_trades": len(df_raw), "ml_filtered": len(df)},
        "leverage_sweep": [{k: v for k, v in s.items() if k != "per_year"} for s in sweep],
        "optimal": {
            "kelly": {"leverage": kelly["leverage"], "cagr": kelly["cagr"],
                      "max_dd": kelly["max_dd"], "sharpe": kelly["sharpe"], "calmar": kelly["calmar"]},
            "max_sharpe": {"leverage": max_sh["leverage"], "cagr": max_sh["cagr"],
                           "sharpe": max_sh["sharpe"], "max_dd": max_sh["max_dd"]},
            "max_return_dd12": {"leverage": max_ret["leverage"], "cagr": max_ret["cagr"],
                                "max_dd": max_ret["max_dd"], "sharpe": max_ret["sharpe"]},
        },
        "regime_adaptive": [{k: v for k, v in s.items() if k != "per_year"} for s in regime_sweep],
        "best_regime": {"leverage": best_regime["leverage"], "cagr": best_regime["cagr"],
                        "max_dd": best_regime["max_dd"], "calmar": best_regime["calmar"]},
        "monte_carlo": mc_results,
        "can_hit_100_cagr": len(target_100) > 0,
        "can_hit_100_cagr_dd12": len(target_100_dd12) > 0,
        "target_100_leverages": [s["leverage"] for s in target_100],
        "recommendation": {
            "leverage": max_ret["leverage"],
            "rationale": f"Max CAGR ({max_ret['cagr']:.1f}%) subject to DD < 12% ({max_ret['max_dd']:.1f}%)",
            "cagr": max_ret["cagr"],
            "max_dd": max_ret["max_dd"],
            "sharpe": max_ret["sharpe"],
        },
        "per_year_at_recommended": next(
            (s["per_year"] for s in sweep if abs(s["leverage"] - max_ret["leverage"]) < 0.01), {}
        ),
    }

    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nWritten: results/summary.json")

    # ── HTML report ──────────────────────────────────────────────────
    html = generate_report(summary, sweep, mc_results, regime_sweep)
    (RESULTS_DIR / "report.html").write_text(html)
    print("Written: results/report.html")

    # ── Final summary ────────────────────────────────────────────────
    rec = summary["recommendation"]
    print(f"\n{'='*60}")
    print(f"  RECOMMENDATION: {rec['leverage']:.2f}x leverage with crisis hedge")
    print(f"  CAGR: {rec['cagr']:.1f}%  |  Max DD: {rec['max_dd']:.1f}%  |  Sharpe: {rec['sharpe']:.2f}")
    print(f"  100% CAGR possible: {'YES' if summary['can_hit_100_cagr'] else 'NO'}")
    print(f"  100% CAGR + DD<12%: {'YES' if summary['can_hit_100_cagr_dd12'] else 'NO'}")
    print(f"{'='*60}")


# ── HTML report ──────────────────────────────────────────────────────────


def generate_report(summary, sweep, mc_results, regime_sweep) -> str:
    opt = summary["optimal"]
    rec = summary["recommendation"]

    def _fr(v): return f"{v:.2f}"
    def _fp(v): return f"{v:.1f}%"
    def _fd(v): return f"${v:,.0f}"

    # Sweep table
    sweep_rows = ""
    for s in sweep:
        highlight = " style='color:#3fb950;font-weight:700'" if abs(s["leverage"] - rec["leverage"]) < 0.01 else ""
        sweep_rows += f"<tr{highlight}><td>{s['leverage']:.2f}x</td><td>{_fp(s['cagr'])}</td><td>{_fp(s['max_dd'])}</td><td>{_fr(s['sharpe'])}</td><td>{_fr(s['calmar'])}</td><td>{_fp(s['win_rate']*100)}</td><td>{_fp(s['worst_year_pct'])}</td><td>{_fp(s['worst_month_pct'])}</td></tr>"

    # MC table
    mc_rows = ""
    for m in mc_results:
        mc_rows += f"<tr><td>{m['leverage']:.1f}x</td><td>{_fp(m['prob_50_cagr']*100)}</td><td>{_fp(m['prob_100_cagr']*100)}</td><td>{_fp(m['prob_negative']*100)}</td><td>{_fp(m['median_return'])}</td></tr>"

    # Regime table
    reg_rows = ""
    for r in regime_sweep:
        reg_rows += f"<tr><td>{r['leverage']:.1f}x</td><td>{_fp(r['cagr'])}</td><td>{_fp(r['max_dd'])}</td><td>{_fr(r['sharpe'])}</td><td>{_fr(r['calmar'])}</td></tr>"

    # SVG: CAGR vs DD at each leverage
    levs = [s["leverage"] for s in sweep]
    cagrs = [s["cagr"] for s in sweep]
    dds = [s["max_dd"] for s in sweep]

    w, h = 700, 250
    pad = 55
    if cagrs and dds:
        pw, ph = w - 2*pad, h - 65
        cx_min, cx_max = min(dds), max(max(dds), 12.1)
        cy_min, cy_max = min(min(cagrs), 0), max(cagrs)
        if cx_max <= cx_min: cx_max = cx_min + 1
        if cy_max <= cy_min: cy_max = cy_min + 1
        tx = lambda v: pad + (v - cx_min) / (cx_max - cx_min) * pw
        ty = lambda v: 35 + (1 - (v - cy_min) / (cy_max - cy_min)) * ph

        dots = []
        for i in range(len(sweep)):
            x = tx(dds[i])
            y = ty(cagrs[i])
            color = "#3fb950" if abs(sweep[i]["leverage"] - rec["leverage"]) < 0.01 else "#58a6ff"
            r = 6 if abs(sweep[i]["leverage"] - rec["leverage"]) < 0.01 else 4
            dots.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{color}"/>')
            dots.append(f'<text x="{x + 8:.0f}" y="{y + 4:.0f}" font-size="8" fill="#8b949e">{levs[i]:.1f}x</text>')

        # 12% DD vertical line
        dd12_x = tx(12)
        dd_line = f'<line x1="{dd12_x:.0f}" y1="35" x2="{dd12_x:.0f}" y2="{h - 30}" stroke="#f85149" stroke-dasharray="4,3"/>'
        dd_label = f'<text x="{dd12_x + 3:.0f}" y="48" font-size="9" fill="#f85149">12% DD</text>'

        scatter = f"""<svg viewBox="0 0 {w} {h}" class="chart">
        <text x="{w//2}" y="20" text-anchor="middle" class="st">CAGR vs Max Drawdown by Leverage</text>
        <text x="{w//2}" y="{h-5}" text-anchor="middle" font-size="10" fill="#8b949e">Max Drawdown (%)</text>
        <text x="15" y="{h//2}" text-anchor="middle" font-size="10" fill="#8b949e" transform="rotate(-90,15,{h//2})">CAGR (%)</text>
        {dd_line}{dd_label}
        {"".join(dots)}
        </svg>"""
    else:
        scatter = ""

    can_100 = summary["can_hit_100_cagr"]
    can_100_dd12 = summary["can_hit_100_cagr_dd12"]
    oc = "#3fb950" if can_100_dd12 else "#d29922"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>EXP-950-max: Leverage Optimization</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:1100px;margin:0 auto;padding:20px;background:#0d1117;color:#c9d1d9}}
h1,h2{{color:#58a6ff}}.meta{{color:#8b949e}}
.hero{{background:#161b22;border:2px solid {oc};border-radius:12px;padding:24px;text-align:center;margin:20px 0}}
.hero .big{{font-size:2em;font-weight:800;color:{oc}}}.hero .sub{{color:#8b949e}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:20px 0}}
.c{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;text-align:center}}
.c .l{{color:#8b949e;font-size:.8em}}.c .v{{color:#f0f6fc;font-weight:600;font-size:1.1em}}
table{{width:100%;border-collapse:collapse;margin:12px 0}}th,td{{padding:6px 10px;text-align:right;border-bottom:1px solid #21262d}}th{{color:#8b949e;background:#161b22}}
.chart{{width:100%;max-width:750px;margin:16px auto;display:block}}.st{{fill:#58a6ff;font-size:13px}}
.rec{{background:#161b22;border:2px solid #3fb950;border-radius:12px;padding:20px;margin:20px 0}}
.rec h3{{color:#3fb950;margin-bottom:12px}}
</style></head><body>
<h1>EXP-950-max: Leverage Optimization</h1>
<div class="hero">
<div class="big">Recommended: {rec['leverage']:.2f}x leverage</div>
<div class="sub">CAGR {_fp(rec['cagr'])} | DD {_fp(rec['max_dd'])} | Sharpe {_fr(rec['sharpe'])} | With Crisis Hedge</div>
</div>

<div class="cards">
<div class="c"><div class="l">Kelly Optimal</div><div class="v">{opt['kelly']['leverage']:.2f}x → {_fp(opt['kelly']['cagr'])}</div></div>
<div class="c"><div class="l">Max Sharpe</div><div class="v">{opt['max_sharpe']['leverage']:.2f}x → {_fr(opt['max_sharpe']['sharpe'])}</div></div>
<div class="c"><div class="l">Max Return@DD&lt;12%</div><div class="v">{opt['max_return_dd12']['leverage']:.2f}x → {_fp(opt['max_return_dd12']['cagr'])}</div></div>
<div class="c"><div class="l">100% CAGR + DD&lt;12%?</div><div class="v" style="color:{'#3fb950' if can_100_dd12 else '#f85149'}">{'YES' if can_100_dd12 else 'NO'}</div></div>
</div>

{scatter}

<h2>Leverage Sweep (with Crisis Hedge)</h2>
<table><tr><th>Leverage</th><th>CAGR</th><th>Max DD</th><th>Sharpe</th><th>Calmar</th><th>Win Rate</th><th>Worst Year</th><th>Worst Month</th></tr>{sweep_rows}</table>

<h2>Monte Carlo (5K paths)</h2>
<table><tr><th>Leverage</th><th>P(&gt;50% CAGR)</th><th>P(&gt;100% CAGR)</th><th>P(Loss)</th><th>Median Return</th></tr>{mc_rows}</table>

<h2>Regime-Adaptive Leverage</h2>
<table><tr><th>Base Leverage</th><th>CAGR</th><th>Max DD</th><th>Sharpe</th><th>Calmar</th></tr>{reg_rows}</table>

<div class="rec">
<h3>Recommendation</h3>
<p><strong>{rec['leverage']:.2f}x leverage</strong> with crisis hedge overlay.</p>
<p>{rec['rationale']}</p>
<p>100% CAGR achievable: <strong>{'Yes at ' + ', '.join(str(l) + 'x' for l in summary['target_100_leverages']) if can_100 else 'No — maximum CAGR at 4x is below 100%'}</strong></p>
{'<p>100% CAGR + DD&lt;12%: <strong>Yes</strong></p>' if can_100_dd12 else '<p>100% CAGR + DD&lt;12%: <strong>Not achievable</strong> — DD constraint binds before 100% CAGR is reached.</p>'}
</div>

</body></html>"""


if __name__ == "__main__":
    main()
