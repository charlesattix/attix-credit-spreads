#!/usr/bin/env python3
"""
run_corrected_north_star_mc.py — Re-run North Star MC with ML-corrected win rate.

Win rate correction: win_rate_boost_analysis.py showed the ML filter at threshold
0.65 achieves 93.4% OOS win rate on EXP-305 (vs 86% used in original MC).

Scenarios:
  A — SPY-only (current):  p=93.4%, N=208 trades/yr
  B — Sector diversified:  p=93.4%, N=280 trades/yr
  REF — Original baseline: p=86.0%, N=280 trades/yr (for comparison)

Writes updated: output/north_star_monte_carlo.md
Usage: python3 scripts/run_corrected_north_star_mc.py [--seeds 10000]
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Dict, List

import numpy as np

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_PATH = ROOT / "output" / "north_star_monte_carlo.md"

# ── Fixed trade-outcome parameters (same as original) ──────────────────────────
WIN_FRAC   = 0.19   # avg win as % of risk
LOSS_FRAC  = 0.47   # avg loss as % of risk (positive = loss)
REGIME_PROBS = np.array([0.60, 0.25, 0.15])   # bull / neutral / bear
REGIME_RISK  = np.array([0.09, 0.07, 0.04])   # bull / neutral / bear
_avg_risk    = float(REGIME_PROBS @ REGIME_RISK)   # 0.0775

# ── North Star targets (unchanged) ─────────────────────────────────────────────
TARGET_RETURN = 100.0   # % avg annual
TARGET_DD     = -12.0   # worst single-year max DD
TARGET_SHARPE =   2.0   # avg annual trade-level Sharpe

# ── Circuit breakers (unchanged) ───────────────────────────────────────────────
CB_T1 = -0.08; CB_T2 = -0.10; CB_T3 = -0.12; CB_RECOVERY = -0.07
H_HALT = 30

# ── Simulation settings ────────────────────────────────────────────────────────
N_SEEDS  = 10_000
N_YEARS  = 6
RHO      = 0.04      # trade correlation (market factor)
RISK_FREE = 0.04     # annual, for path-level Sharpe

# ── Scenarios ──────────────────────────────────────────────────────────────────
SCENARIOS = {
    "A_SPY_only":       {"win_prob": 0.934, "n_trades": 208,
                         "label": "Scenario A — SPY-only (corrected, p=93.4%, N=208)"},
    "B_sector_div":     {"win_prob": 0.934, "n_trades": 280,
                         "label": "Scenario B — Sector diversified (corrected, p=93.4%, N=280)"},
    "REF_original":     {"win_prob": 0.860, "n_trades": 280,
                         "label": "REF — Original baseline (p=86%, N=280)"},
}


# ═════════════════════════════════════════════════════════════════════════════
# Simulation engine (self-contained, parametrized)
# ═════════════════════════════════════════════════════════════════════════════

def _sigma_market(win_prob: float) -> float:
    """Per-trade market factor sigma calibrated to RHO ≈ 0.04."""
    ev = win_prob * WIN_FRAC * _avg_risk - (1 - win_prob) * LOSS_FRAC * _avg_risk
    var = (win_prob * (WIN_FRAC * _avg_risk) ** 2
           + (1 - win_prob) * (LOSS_FRAC * _avg_risk) ** 2
           - ev ** 2)
    return math.sqrt(RHO * var)


def simulate_year(rng: np.random.Generator, cap: float,
                  win_prob: float, n_trades: int, sigma_mkt: float) -> Dict:
    peak = cap
    max_dd = 0.0
    cb_events = 0
    halt_remaining = 0
    paused = False
    pnl_list: List[float] = []
    start_cap = cap

    mkt_factor = rng.normal(0.0, sigma_mkt)

    for _ in range(n_trades):
        dd_pct = (cap - peak) / peak if peak > 0 else 0.0

        if halt_remaining > 0:
            halt_remaining -= 1
            continue
        if dd_pct <= CB_T3:
            halt_remaining = H_HALT
            cb_events += 1
            continue
        if not paused and dd_pct <= CB_T2:
            paused = True
            cb_events += 1
        if paused:
            if dd_pct > CB_RECOVERY:
                paused = False
            else:
                continue

        size_mult = 0.50 if dd_pct <= CB_T1 else 1.00

        regime_idx = rng.choice(3, p=REGIME_PROBS)
        risk = float(REGIME_RISK[regime_idx]) * size_mult

        wp_adj = max(0.01, min(0.99, win_prob + mkt_factor))
        if rng.random() < wp_adj:
            trade_ret = WIN_FRAC * risk
        else:
            trade_ret = -LOSS_FRAC * risk

        trade_pnl = cap * trade_ret
        cap += trade_pnl
        pnl_list.append(trade_pnl / start_cap * 100.0)

        if cap > peak:
            peak = cap
        dd = (cap - peak) / peak * 100.0
        if dd < max_dd:
            max_dd = dd

    sharpe = 0.0
    if len(pnl_list) >= 10:
        arr = np.array(pnl_list)
        mu = arr.mean(); std = arr.std(ddof=1)
        if std > 1e-8:
            tpd = n_trades / 252.0
            sharpe = (mu / std) * math.sqrt(252 * tpd)

    return {
        "end_cap":    cap,
        "return_pct": (cap / start_cap - 1.0) * 100.0,
        "max_dd":     max_dd,
        "sharpe":     sharpe,
        "cb_events":  cb_events,
    }


def simulate_path(seed: int, win_prob: float, n_trades: int, sigma_mkt: float) -> Dict:
    rng = np.random.default_rng(seed)
    cap = 100_000.0
    year_rets, year_dds, year_sh = [], [], []

    for _ in range(N_YEARS):
        yr = simulate_year(rng, cap, win_prob, n_trades, sigma_mkt)
        year_rets.append(yr["return_pct"])
        year_dds.append(yr["max_dd"])
        year_sh.append(yr["sharpe"])
        cap = yr["end_cap"]

    avg_ret   = float(np.mean(year_rets))
    worst_dd  = float(np.min(year_dds))
    avg_sh    = float(np.mean(year_sh))
    cagr      = (cap / 100_000.0) ** (1.0 / N_YEARS) - 1.0

    path_sh = 0.0
    if len(year_rets) >= 3:
        mu_yr = np.mean(year_rets)
        sd_yr = np.std(year_rets, ddof=1)
        if sd_yr > 1e-4:
            path_sh = float((mu_yr - RISK_FREE * 100) / sd_yr)

    return {
        "avg_annual":    avg_ret,
        "worst_dd":      worst_dd,
        "avg_sharpe":    avg_sh,
        "path_sharpe":   path_sh,
        "cagr":          cagr * 100.0,
        "cumulative":    (cap / 100_000.0 - 1.0) * 100.0,
        "cb_total":      sum(0 for _ in range(N_YEARS)),  # placeholder, recomputed
        "year_rets":     year_rets,
        "year_dds":      year_dds,
        "north_star":    avg_ret >= TARGET_RETURN
                         and worst_dd >= TARGET_DD
                         and avg_sh >= TARGET_SHARPE,
    }


def run_mc(win_prob: float, n_trades: float, n_seeds: int = N_SEEDS) -> List[Dict]:
    sig = _sigma_market(win_prob)
    t0 = time.time()
    results = [simulate_path(s, win_prob, int(n_trades), sig) for s in range(n_seeds)]
    elapsed = time.time() - t0
    print(f"    {n_seeds:,} paths in {elapsed:.1f}s")
    return results


# ═════════════════════════════════════════════════════════════════════════════
# Analytics helpers
# ═════════════════════════════════════════════════════════════════════════════

def pct(v: float, d: int = 1) -> str:
    return f"{'+'if v>=0 else ''}{v:.{d}f}%"


def p_row(arr, label, fmt=pct):
    ps = np.percentile(arr, [1, 5, 10, 25, 50, 75, 90, 95, 99])
    cells = " | ".join(fmt(x) for x in ps)
    return (f"| {label} | {fmt(float(np.mean(arr)))} | {fmt(float(np.median(arr)))} "
            f"| {fmt(float(np.std(arr, ddof=1)))} | {cells} |")


def plain(v: float) -> str:
    return f"{v:.2f}"


def expected_annual(wp: float, nt: int) -> float:
    ev = wp * WIN_FRAC * _avg_risk - (1 - wp) * LOSS_FRAC * _avg_risk
    return nt * ev * 100


def compute_stats(results: List[Dict]) -> Dict:
    n = len(results)
    avg_rets  = np.array([r["avg_annual"]  for r in results])
    worst_dds = np.array([r["worst_dd"]    for r in results])
    avg_shs   = np.array([r["avg_sharpe"]  for r in results])
    cagrs     = np.array([r["cagr"]        for r in results])
    ns_pct    = sum(1 for r in results if r["north_star"]) / n * 100
    t1_pct    = (avg_rets  >= TARGET_RETURN).mean() * 100
    t2_pct    = (worst_dds >= TARGET_DD).mean()     * 100
    t3_pct    = (avg_shs   >= TARGET_SHARPE).mean() * 100
    return {
        "avg_rets": avg_rets, "worst_dds": worst_dds,
        "avg_shs":  avg_shs,  "cagrs":     cagrs,
        "ns_pct": ns_pct, "t1_pct": t1_pct,
        "t2_pct": t2_pct, "t3_pct": t3_pct,
        "p50_ret": float(np.median(avg_rets)),
        "p50_dd":  float(np.median(worst_dds)),
        "p50_sh":  float(np.median(avg_shs)),
        "p05_ret": float(np.percentile(avg_rets, 5)),
        "p95_ret": float(np.percentile(avg_rets, 95)),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Report writer
# ═════════════════════════════════════════════════════════════════════════════

def generate_report(scenario_results: Dict[str, tuple], n_seeds: int) -> str:
    """
    scenario_results: {scenario_key: (cfg, results, stats)}
    """
    lines: List[str] = []
    A = lines.append
    now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    sc_a   = scenario_results["A_SPY_only"]
    sc_b   = scenario_results["B_sector_div"]
    sc_ref = scenario_results["REF_original"]

    A("# North Star Portfolio — Monte Carlo Simulation")
    A("")
    A(f"> **Generated:** {now}")
    A(f"> **Branch:** `main`")
    A(f"> **Seeds:** {n_seeds:,}  |  **Years per path:** {N_YEARS}")
    A(f"> **⚠️ Win rate corrected to 93.4%** (from 86%) — see `output/win_rate_boost_report.md`")
    A("")
    A("---")
    A("")
    A("## Summary: Corrected vs Original Parameters")
    A("")
    A("| Parameter | Original (REF) | Scenario A (corrected) | Scenario B (corrected) |")
    A("|-----------|:--------------:|:----------------------:|:----------------------:|")
    A(f"| Win rate | 86.0% | **93.4%** | **93.4%** |")
    A(f"| Trades/yr | 280 | 208 (SPY-only) | 280 (sector div.) |")
    A(f"| Source | Prior MC | ML WFV OOS actuals | ML WFV OOS actuals |")
    A("")
    A("### North Star achievement comparison")
    A("")
    A("| Scenario | T1: Return≥100% | T2: DD≥-12% | T3: Sharpe≥2.0 | **All 3** | P50 annual |")
    A("|----------|:---------------:|:-----------:|:--------------:|:---------:|:----------:|")
    for key, (cfg, _, st) in scenario_results.items():
        A(f"| {cfg['label']:<55} | {st['t1_pct']:>5.1f}% | {st['t2_pct']:>5.1f}% | "
          f"{st['t3_pct']:>5.1f}% | **{st['ns_pct']:>5.1f}%** | {pct(st['p50_ret'])} |")
    A("")
    A("---")
    A("")

    # ── Full results for each corrected scenario ────────────────────────────────
    for sc_key in ["A_SPY_only", "B_sector_div"]:
        cfg, results, st = scenario_results[sc_key]
        wp  = cfg["win_prob"]
        nt  = cfg["n_trades"]
        ev  = wp * WIN_FRAC * _avg_risk - (1 - wp) * LOSS_FRAC * _avg_risk
        var_t = (wp * (WIN_FRAC * _avg_risk)**2
                 + (1-wp) * (LOSS_FRAC * _avg_risk)**2 - ev**2)

        A(f"## {'A' if sc_key == 'A_SPY_only' else 'B'}: {cfg['label']}")
        A("")
        A("### Parameters")
        A("")
        A(f"| Parameter | Value | Source |")
        A(f"|-----------|:-----:|--------|")
        A(f"| Trades per year | {nt} | {'SPY-only' if nt == 208 else 'SPY 208 + sector ETFs 72'} |")
        A(f"| Win rate | {wp*100:.1f}% | ML-filtered OOS walk-forward (2021-2025) |")
        A(f"| Avg win / risk | +{WIN_FRAC*100:.0f}% | Credit spreads: 19% avg credit kept on winners |")
        A(f"| Avg loss / risk | -{LOSS_FRAC*100:.0f}% | Stop-loss path: 47% of risk lost on average |")
        A(f"| Trade correlation ρ | {RHO:.2f} | Shared market factor |")
        A("")
        A(f"**Expected arithmetic annual return ({nt} trades):** {pct(nt * ev * 100, 1)}  ")
        A(f"**Expected std of arithmetic annual return:** {pct(math.sqrt(nt * var_t) * 100, 1)}")
        A("")
        A("### Distribution (10,000 simulations)")
        A("")
        A(f"| Metric | Mean | Median | Std | P1 | P5 | P10 | P25 | P50 | P75 | P90 | P95 | P99 |")
        A(f"|--------|:----:|:------:|:---:|:--:|:--:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
        A(p_row(st["avg_rets"],  "Avg annual return"))
        A(p_row(st["cagrs"],     "6yr CAGR"))
        A(p_row(st["worst_dds"], "Worst single-yr DD"))
        A(p_row(st["avg_shs"],   "Avg annual Sharpe", fmt=plain))
        A("")

        # Per-year table
        per_yr_rets = [np.array([r["year_rets"][y] for r in results]) for y in range(N_YEARS)]
        per_yr_dds  = [np.array([r["year_dds"][y]  for r in results]) for y in range(N_YEARS)]
        A("#### Per-year return distribution")
        A("")
        A(f"| Year | Mean | P5 | P25 | P50 | P75 | P95 | P(>0) | P(>100%) |")
        A(f"|------|:----:|:--:|:---:|:---:|:---:|:---:|:-----:|:--------:|")
        for yi, lbl in enumerate(["Y1","Y2","Y3","Y4","Y5","Y6"]):
            arr = per_yr_rets[yi]
            p5,p25,p50,p75,p95 = np.percentile(arr, [5,25,50,75,95])
            A(f"| {lbl} | {pct(arr.mean())} | {pct(p5)} | {pct(p25)} | {pct(p50)} | "
              f"{pct(p75)} | {pct(p95)} | {(arr>0).mean()*100:.1f}% | {(arr>100).mean()*100:.1f}% |")
        A("")
        A("#### Per-year drawdown distribution")
        A("")
        A(f"| Year | P5 DD | P25 DD | P50 DD | P75 DD | P95 DD |")
        A(f"|------|:-----:|:------:|:------:|:------:|:------:|")
        for yi, lbl in enumerate(["Y1","Y2","Y3","Y4","Y5","Y6"]):
            arr = per_yr_dds[yi]
            p5,p25,p50,p75,p95 = np.percentile(arr, [5,25,50,75,95])
            A(f"| {lbl} | {p5:.1f}% | {p25:.1f}% | {p50:.1f}% | {p75:.1f}% | {p95:.1f}% |")
        A("")

        # North Star targets
        A("### North Star Target Achievement")
        A("")
        A(f"| Target | Threshold | % achieving |")
        A(f"|--------|:---------:|:-----------:|")
        A(f"| T1: Avg annual return | ≥ 100% | **{st['t1_pct']:.1f}%** |")
        A(f"| T2: Max portfolio DD  | ≥ -12% | **{st['t2_pct']:.1f}%** |")
        A(f"| T3: Avg annual Sharpe | ≥ 2.0  | **{st['t3_pct']:.1f}%** |")
        A(f"| **ALL THREE**         | —      | **{st['ns_pct']:.1f}%** |")
        A("")
        A(f"- **Binding constraint:** "
          + ("T1 (return)" if min(st['t1_pct'],st['t2_pct'],st['t3_pct']) == st['t1_pct']
             else "T3 (Sharpe)" if min(st['t1_pct'],st['t2_pct'],st['t3_pct']) == st['t3_pct']
             else "T2 (drawdown)"))
        A(f"- P50 avg annual: {pct(st['p50_ret'])}  |  "
          f"P5/P95: {pct(st['p05_ret'])} → {pct(st['p95_ret'])}")
        A(f"- Calibration-adjusted P50: {pct(st['p50_ret']*0.65, 1)} (×0.65 vs actual backtester)")
        A("")

        # Sensitivity
        A("### Target Sensitivity")
        A("")
        A(f"| Return target | DD target | Sharpe target | % passing |")
        A(f"|:-------------:|:---------:|:-------------:|:---------:|")
        for ret_t, dd_t, sh_t in [
            (100,-12,2.0),(80,-12,2.0),(60,-12,2.0),(200,-12,2.0),
            (300,-12,2.0),(400,-12,2.0),(500,-12,2.0),
            (100,-15,2.0),(100,-20,2.0),(100,-12,1.5),(100,-12,1.0),
        ]:
            n_pass = sum(
                1 for r in results
                if r["avg_annual"] >= ret_t and r["worst_dd"] >= dd_t
                and r["avg_sharpe"] >= sh_t
            ) / n_seeds * 100
            A(f"| ≥{ret_t:.0f}% | ≥{dd_t:.0f}% | ≥{sh_t:.1f} | **{n_pass:.1f}%** |")
        A("")
        A("---")
        A("")

    # ── Reference comparison ───────────────────────────────────────────────────
    A("## REF: Original Baseline (p=86%, N=280)")
    A("")
    _, ref_results, ref_st = sc_ref
    A(f"| Metric | Mean | P50 | P5 | P95 |")
    A(f"|--------|:----:|:---:|:--:|:---:|")
    A(f"| Avg annual return | {pct(float(np.mean(ref_st['avg_rets'])))} | {pct(ref_st['p50_ret'])} | {pct(ref_st['p05_ret'])} | {pct(ref_st['p95_ret'])} |")
    A(f"| Worst DD (median) | — | {ref_st['p50_dd']:.1f}% | — | — |")
    A(f"| Avg annual Sharpe | — | {ref_st['p50_sh']:.2f} | — | — |")
    A(f"| All-3 pass rate | — | **{ref_st['ns_pct']:.1f}%** | — | — |")
    A("")
    A("---")
    A("")

    # ── Head-to-head comparison ────────────────────────────────────────────────
    A("## Head-to-Head Comparison")
    A("")
    A("| Metric | REF (p=86%, N=280) | Sc-A (p=93.4%, N=208) | Sc-B (p=93.4%, N=280) |")
    A("|--------|:------------------:|:---------------------:|:---------------------:|")

    _, _, st_a   = sc_a
    _, _, st_b   = sc_b
    _, _, st_ref = sc_ref

    rows = [
        ("P50 avg annual return", pct(st_ref['p50_ret']),   pct(st_a['p50_ret']),   pct(st_b['p50_ret'])),
        ("P5  avg annual return", pct(st_ref['p05_ret']),   pct(st_a['p05_ret']),   pct(st_b['p05_ret'])),
        ("P95 avg annual return", pct(st_ref['p95_ret']),   pct(st_a['p95_ret']),   pct(st_b['p95_ret'])),
        ("P50 worst DD",          f"{st_ref['p50_dd']:.1f}%", f"{st_a['p50_dd']:.1f}%", f"{st_b['p50_dd']:.1f}%"),
        ("P50 avg Sharpe",        f"{st_ref['p50_sh']:.2f}", f"{st_a['p50_sh']:.2f}", f"{st_b['p50_sh']:.2f}"),
        ("T1: Return ≥ 100%",     f"{st_ref['t1_pct']:.1f}%", f"{st_a['t1_pct']:.1f}%", f"{st_b['t1_pct']:.1f}%"),
        ("T2: DD ≥ -12%",         f"{st_ref['t2_pct']:.1f}%", f"{st_a['t2_pct']:.1f}%", f"{st_b['t2_pct']:.1f}%"),
        ("T3: Sharpe ≥ 2.0",      f"{st_ref['t3_pct']:.1f}%", f"{st_a['t3_pct']:.1f}%", f"{st_b['t3_pct']:.1f}%"),
        ("**All 3 targets**",     f"**{st_ref['ns_pct']:.1f}%**", f"**{st_a['ns_pct']:.1f}%**", f"**{st_b['ns_pct']:.1f}%**"),
        ("Calibrated P50 (×0.65)", pct(st_ref['p50_ret']*0.65,1), pct(st_a['p50_ret']*0.65,1), pct(st_b['p50_ret']*0.65,1)),
    ]
    for label, v_ref, v_a, v_b in rows:
        A(f"| {label:<32} | {v_ref:>18} | {v_a:>21} | {v_b:>21} |")
    A("")
    A("---")
    A("")

    # ── Model calibration note ─────────────────────────────────────────────────
    A("## Model Calibration")
    A("")
    A("The sequential trade model compounds each trade against *current* capital, which")
    A("overstates returns vs the actual backtester (concurrent positions share capital pool).")
    A("Calibration factor 0.65× derived from exp_126 comparison (actual ÷ model).")
    A("")
    A("| Scenario | Model P50 | Calibrated P50 (×0.65) | vs 200% roadmap target |")
    A("|----------|:---------:|:----------------------:|:----------------------:|")
    for key, lbl in [("A_SPY_only","Sc-A"),("B_sector_div","Sc-B"),("REF_original","REF")]:
        _, _, st = scenario_results[key]
        p50 = st['p50_ret']
        adj = p50 * 0.65
        vs = "ABOVE ✓" if adj >= 200 else "BELOW ✗" if adj < 100 else "NEAR ~"
        A(f"| {lbl} | {pct(p50)} | {pct(adj,1)} | {vs} |")
    A("")
    A("---")
    A("")
    A("## Key Findings")
    A("")
    A(f"### Win rate correction impact")
    A("")
    p50_ref = st_ref['p50_ret']
    p50_a   = st_a['p50_ret']
    p50_b   = st_b['p50_ret']
    A(f"Raising win rate from 86% → 93.4% (same N=280) improves P50 annual return:")
    A(f"  {pct(p50_ref)} (REF) → {pct(p50_b)} (Sc-B) = {pct(p50_b-p50_ref, 1)} absolute ({(p50_b/p50_ref-1)*100:.1f}% relative)")
    A("")
    A(f"### Binding North Star constraint (corrected)")
    A("")
    for key, lbl in [("A_SPY_only","Sc-A"),("B_sector_div","Sc-B")]:
        _, _, st = scenario_results[key]
        bind = min([("T1",st['t1_pct']),("T2",st['t2_pct']),("T3",st['t3_pct'])], key=lambda x: x[1])
        A(f"**{lbl}:** tightest constraint = {bind[0]} ({bind[1]:.1f}% pass rate). "
          f"All-3 pass rate = **{st['ns_pct']:.1f}%**.")
    A("")
    A("### North Star status")
    A("")
    for key, lbl in [("A_SPY_only","Sc-A"),("B_sector_div","Sc-B")]:
        _, _, st = scenario_results[key]
        status = "✅ EXCEEDS" if st['ns_pct'] >= 99.0 else "✅ ACHIEVES" if st['ns_pct'] >= 90.0 else "⚠️ PARTIAL"
        A(f"**{lbl}:** {status} — {st['ns_pct']:.1f}% of paths pass all 3 North Star targets")
    A("")
    A("### Important caveat: trade correlation")
    A("")
    A("The 93.4% win rate comes from OOS walk-forward validation across 2021-2025.")
    A("However, the P50 annual returns computed here assume ρ=0.04 trade correlation.")
    A("From `output/sharpe_ceiling_analysis.md`, the actual observed Sharpe (2.60)")
    A("implies N_eff ≈ 45 (not 208) — higher effective ρ than assumed here.")
    A("The calibration factor 0.65× partially captures this, but the absolute return")
    A("numbers remain optimistic. The **relative** comparison between REF and corrected")
    A("scenarios remains valid.")
    A("")
    A("---")
    A("")
    A(f"*Simulation: `scripts/run_corrected_north_star_mc.py` | {n_seeds:,} paths × {N_YEARS} years*  ")
    A(f"*Win rate 93.4% from `output/win_rate_boost_report.md` (ML OOS walk-forward 2021-2025)*  ")
    A(f"*Correlation model: ρ={RHO} inter-trade | Calibration factor 0.65× vs actual backtester*  ")
    A(f"*Not accounting for: slippage, margin calls, liquidity constraints*")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=N_SEEDS)
    args = ap.parse_args()

    print(f"North Star MC — corrected win rate 93.4% — {args.seeds:,} seeds × {N_YEARS} years")
    print(f"Safe Kelly 4/7/9 | CB -8/-10/-12% | ρ={RHO}")
    print()

    scenario_results: Dict[str, tuple] = {}
    for key, cfg in SCENARIOS.items():
        wp = cfg["win_prob"]; nt = cfg["n_trades"]
        ev = wp * WIN_FRAC * _avg_risk - (1 - wp) * LOSS_FRAC * _avg_risk
        print(f"  Running {cfg['label']}")
        print(f"    E[annual arith] = {nt * ev * 100:+.1f}%")
        results = run_mc(wp, nt, args.seeds)
        st = compute_stats(results)
        scenario_results[key] = (cfg, results, st)
        print(f"    P50={pct(st['p50_ret'])} | P50_DD={st['p50_dd']:.1f}% | "
              f"P50_Sharpe={st['p50_sh']:.2f} | All-3={st['ns_pct']:.1f}%")
        print()

    print("Generating report...")
    report = generate_report(scenario_results, args.seeds)
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(report)
    print(f"✓ Report written to {OUTPUT_PATH}")

    # Print summary table
    print("\n" + "="*65)
    print("NORTH STAR MC — CORRECTED RESULTS SUMMARY")
    print("="*65)
    for key, (cfg, _, st) in scenario_results.items():
        print(f"\n  {cfg['label']}")
        print(f"    P50 annual: {pct(st['p50_ret']):<12}  Calibrated: {pct(st['p50_ret']*0.65,1)}")
        print(f"    All-3 pass: {st['ns_pct']:.1f}%  "
              f"(T1={st['t1_pct']:.1f}% T2={st['t2_pct']:.1f}% T3={st['t3_pct']:.1f}%)")


if __name__ == "__main__":
    main()
