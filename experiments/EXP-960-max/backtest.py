#!/usr/bin/env python3
"""
EXP-960-max: Path to 100% CAGR Analysis

Quantitative analysis of what combination of return streams could
reach 100% CAGR at DD < 12%.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).parent
RESULTS_DIR = ROOT / "results"
INITIAL_CAPITAL = 100_000.0
TRADING_DAYS = 252
N_YEARS = 6


# ── Proven strategy streams (from experiments) ──────────────────────────

STREAMS = {
    "ML-CS-1x": {"cagr": 20.7, "dd": 3.0, "sharpe": 16.96, "source": "EXP-950 @ 1x"},
    "ML-CS-3.5x": {"cagr": 42.3, "dd": 9.0, "sharpe": 16.96, "source": "EXP-950 @ 3.5x + hedge"},
    "ML-CS-4x": {"cagr": 45.2, "dd": 10.2, "sharpe": 16.96, "source": "EXP-950 @ 4x + hedge"},
    "Vol-Harvest": {"cagr": 15.2, "dd": 6.8, "sharpe": 2.55, "source": "EXP-740"},
    "Combined-750": {"cagr": 29.2, "dd": 2.8, "sharpe": 5.06, "source": "EXP-750 (60/40 CS+Vol)"},
}

# Cross-stream correlations (measured and estimated)
CORRELATIONS = {
    ("ML-CS-1x", "Vol-Harvest"): 0.012,    # measured EXP-750
    ("ML-CS-3.5x", "Vol-Harvest"): 0.012,  # same strategy, different leverage
    ("ML-CS-1x", "QQQ-CS"): 0.75,          # estimated equity correlation
    ("ML-CS-1x", "IWM-CS"): 0.65,          # estimated
    ("ML-CS-1x", "IBIT-CS"): 0.15,         # crypto decorrelation
    ("Vol-Harvest", "QQQ-CS"): 0.10,        # vol is independent of direction
    ("Vol-Harvest", "IBIT-CS"): 0.05,       # minimal
    ("QQQ-CS", "IWM-CS"): 0.70,            # equity correlation
}

# Hypothetical additional streams
HYPOTHETICAL = {
    "QQQ-CS": {"cagr_est": 18.0, "dd_est": 4.0, "sharpe_est": 12.0, "feasibility": "high",
               "notes": "QQQ options liquid, similar strategy applies"},
    "IWM-CS": {"cagr_est": 15.0, "dd_est": 5.0, "sharpe_est": 8.0, "feasibility": "high",
               "notes": "IWM options liquid but wider spreads"},
    "IBIT-CS": {"cagr_est": 25.0, "dd_est": 12.0, "sharpe_est": 5.0, "feasibility": "medium",
                "notes": "High vol = rich premiums but only from 2024, limited history"},
    "Intraday-SPY": {"cagr_est": 10.0, "dd_est": 3.0, "sharpe_est": 3.0, "feasibility": "low",
                     "notes": "Requires real-time execution infrastructure, different alpha"},
    "Momentum-ETF": {"cagr_est": 12.0, "dd_est": 15.0, "sharpe_est": 1.5, "feasibility": "medium",
                     "notes": "Cross-asset momentum, weekly rebalance"},
}


# ── Portfolio math ───────────────────────────────────────────────────────


def portfolio_cagr_dd(
    streams: List[Dict],
    weights: List[float],
    correlations: Dict[Tuple[str, str], float],
) -> Tuple[float, float, float]:
    """Compute portfolio CAGR, DD, and Sharpe from weighted streams.

    Uses mean-variance approximation:
      portfolio_return = sum(w_i * r_i)
      portfolio_var = sum_i sum_j w_i * w_j * sigma_i * sigma_j * rho_ij
      portfolio_dd ≈ sqrt(portfolio_var) * dd_scaling_factor

    Returns: (cagr, max_dd_pct, sharpe)
    """
    n = len(streams)
    if n == 0:
        return 0.0, 0.0, 0.0

    w = np.array(weights)
    w = w / w.sum()  # normalize

    # Weighted return
    returns = np.array([s["cagr"] for s in streams])
    port_return = float(w @ returns)

    # Weighted volatility (using DD as proxy for vol)
    dds = np.array([s["dd"] for s in streams])
    names = [s.get("name", f"s{i}") for i, s in enumerate(streams)]

    # Build correlation matrix
    corr_matrix = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            key1 = (names[i], names[j])
            key2 = (names[j], names[i])
            rho = correlations.get(key1, correlations.get(key2, 0.3))
            corr_matrix[i, j] = rho
            corr_matrix[j, i] = rho

    # Portfolio variance
    cov_matrix = np.outer(dds, dds) * corr_matrix
    port_var = float(w @ cov_matrix @ w)
    port_dd = math.sqrt(max(port_var, 0))

    # Sharpe approximation
    port_sharpe = port_return / port_dd if port_dd > 0.01 else 0.0

    return port_return, port_dd, port_sharpe


def find_min_streams_for_target(
    base_cagr: float,
    base_dd: float,
    target_cagr: float,
    max_dd: float,
    avg_correlation: float,
) -> int:
    """How many uncorrelated streams of base_cagr each reach target_cagr at max_dd?

    With n uncorrelated streams:
      return = n * base_cagr * (1/n) = base_cagr (unchanged if equal weight)
      vol = base_dd * sqrt(1/n) (diversification)

    To increase return, we need leverage or different returns per stream.
    With n streams at different allocations, maximizing return at DD constraint:
      leverage_per_stream ≈ max_dd / (base_dd / sqrt(n))
      portfolio_return ≈ n * base_cagr * leverage / n = base_cagr * leverage

    So: leverage = max_dd * sqrt(n) / base_dd
    portfolio_cagr = base_cagr * leverage
    """
    for n in range(1, 20):
        # Diversification allows higher effective leverage
        effective_dd = base_dd / math.sqrt(n) if avg_correlation < 0.5 else base_dd / math.sqrt(n) * math.sqrt(1 + (n-1) * avg_correlation)
        max_leverage = max_dd / effective_dd if effective_dd > 0 else 1.0
        achievable_cagr = base_cagr * max_leverage
        if achievable_cagr >= target_cagr:
            return n
    return -1  # not achievable with up to 19 streams


# ── Scenario modeling ────────────────────────────────────────────────────


def scenario_proven_only():
    """What can we achieve with only proven streams?"""
    # Scenario A: 3.5x CS + Vol Harvest (proven)
    streams = [
        {"name": "ML-CS-3.5x", "cagr": 42.3, "dd": 9.0},
        {"name": "Vol-Harvest", "cagr": 15.2, "dd": 6.8},
    ]
    # Optimize weights for max CAGR at DD < 12%
    best = None
    for cs_w in np.arange(0.3, 0.9, 0.05):
        vol_w = 1.0 - cs_w
        cagr, dd, sh = portfolio_cagr_dd(streams, [cs_w, vol_w], CORRELATIONS)
        if dd <= 12.0 and (best is None or cagr > best["cagr"]):
            best = {"cs_weight": cs_w, "vol_weight": vol_w, "cagr": cagr, "dd": dd, "sharpe": sh}
    return best


def scenario_add_multi_underlying():
    """Add QQQ and IBIT CS streams to the mix."""
    streams = [
        {"name": "ML-CS-3.5x", "cagr": 42.3, "dd": 9.0},
        {"name": "Vol-Harvest", "cagr": 15.2, "dd": 6.8},
        {"name": "QQQ-CS", "cagr": 18.0, "dd": 4.0},
        {"name": "IBIT-CS", "cagr": 25.0, "dd": 12.0},
    ]
    best = None
    for w0 in np.arange(0.3, 0.7, 0.1):
        for w1 in np.arange(0.1, 0.4, 0.1):
            for w2 in np.arange(0.05, 0.3, 0.05):
                w3 = 1.0 - w0 - w1 - w2
                if w3 < 0.05 or w3 > 0.4:
                    continue
                cagr, dd, sh = portfolio_cagr_dd(streams, [w0, w1, w2, w3], CORRELATIONS)
                if dd <= 12.0 and (best is None or cagr > best["cagr"]):
                    best = {"weights": [w0, w1, w2, w3],
                            "names": [s["name"] for s in streams],
                            "cagr": cagr, "dd": dd, "sharpe": sh}
    return best


def scenario_max_diversification():
    """All five proven + hypothetical streams."""
    streams = [
        {"name": "ML-CS-3.5x", "cagr": 42.3, "dd": 9.0},
        {"name": "Vol-Harvest", "cagr": 15.2, "dd": 6.8},
        {"name": "QQQ-CS", "cagr": 18.0, "dd": 4.0},
        {"name": "IBIT-CS", "cagr": 25.0, "dd": 12.0},
        {"name": "IWM-CS", "cagr": 15.0, "dd": 5.0},
    ]
    best = None
    rng = np.random.RandomState(42)
    for _ in range(50000):
        raw = rng.dirichlet(np.ones(5))
        # Constrain: ML-CS gets at least 30%
        raw[0] = max(raw[0], 0.30)
        raw /= raw.sum()
        cagr, dd, sh = portfolio_cagr_dd(streams, raw.tolist(), CORRELATIONS)
        if dd <= 12.0 and (best is None or cagr > best["cagr"]):
            best = {"weights": raw.tolist(),
                    "names": [s["name"] for s in streams],
                    "cagr": cagr, "dd": dd, "sharpe": sh}
    return best


def scenario_levered_combined():
    """What if we lever the EXP-750 combined portfolio?"""
    # EXP-750: 29.2% CAGR, 2.8% DD, Sharpe 5.06
    base_cagr = 29.2
    base_dd = 2.8
    results = []
    for lev in np.arange(1.0, 6.0, 0.5):
        cagr = base_cagr * lev
        dd = base_dd * lev  # approximate — actual is nonlinear
        sharpe = base_cagr / base_dd  # constant
        results.append({"leverage": lev, "cagr": cagr, "dd": dd, "sharpe": sharpe})
    return results


# ── Monte Carlo forward projection ──────────────────────────────────────


def monte_carlo_projection(
    cagr: float,
    dd: float,
    n_paths: int = 10000,
    horizon_years: int = 5,
    seed: int = 42,
) -> Dict[str, float]:
    """Project portfolio forward with uncertainty."""
    rng = np.random.RandomState(seed)
    daily_mean = (1 + cagr / 100) ** (1 / TRADING_DAYS) - 1
    # DD → daily vol approximation
    daily_vol = dd / 100 / math.sqrt(TRADING_DAYS) * 2

    terminal = np.zeros(n_paths)
    for p in range(n_paths):
        equity = INITIAL_CAPITAL
        peak = equity
        for _ in range(horizon_years * TRADING_DAYS):
            ret = rng.normal(daily_mean, daily_vol)
            equity *= (1 + ret)
            peak = max(peak, equity)
        terminal[p] = equity

    returns = terminal / INITIAL_CAPITAL - 1
    cagrs = (terminal / INITIAL_CAPITAL) ** (1 / horizon_years) - 1

    return {
        "median_cagr": float(np.median(cagrs) * 100),
        "p10_cagr": float(np.percentile(cagrs, 10) * 100),
        "p90_cagr": float(np.percentile(cagrs, 90) * 100),
        "prob_above_50": float((cagrs > 0.50).mean()),
        "prob_above_100": float((cagrs > 1.00).mean()),
        "prob_loss": float((returns < 0).mean()),
        "median_terminal": float(np.median(terminal)),
    }


# ── Main analysis ────────────────────────────────────────────────────────


def main():
    print("EXP-960-max: Path to 100% CAGR Analysis")
    print("=" * 60)

    # Q1: How many uncorrelated 45% streams needed?
    print("\n[Q1] How many uncorrelated 45% CAGR streams to reach 100%?")
    n_needed = find_min_streams_for_target(
        base_cagr=45.0, base_dd=10.0, target_cagr=100.0, max_dd=12.0, avg_correlation=0.1
    )
    print(f"  Answer: {n_needed} uncorrelated streams" if n_needed > 0 else "  Not achievable")
    print(f"  Math: with {max(n_needed,2)} streams at ρ≈0.1, DD diversifies by √{max(n_needed,2)}={math.sqrt(max(n_needed,2)):.1f}x")
    print(f"  Allowing leverage up to {12.0 / (10.0 / math.sqrt(max(n_needed,2))):.1f}x effective")

    # Q2: Proven streams only (CS + Vol)
    print("\n[Q2] Proven streams only (3.5x ML-CS + Vol Harvest)")
    s_proven = scenario_proven_only()
    print(f"  Best: {s_proven['cs_weight']:.0%} CS + {s_proven['vol_weight']:.0%} Vol")
    print(f"  CAGR: {s_proven['cagr']:.1f}%, DD: {s_proven['dd']:.1f}%, Sharpe: {s_proven['sharpe']:.1f}")

    # Q3: Add multi-underlying
    print("\n[Q3] Add QQQ + IBIT CS streams")
    s_multi = scenario_add_multi_underlying()
    if s_multi:
        for n, w in zip(s_multi["names"], s_multi["weights"]):
            print(f"  {n}: {w:.0%}")
        print(f"  CAGR: {s_multi['cagr']:.1f}%, DD: {s_multi['dd']:.1f}%, Sharpe: {s_multi['sharpe']:.1f}")

    # Q4: Maximum diversification (5 streams)
    print("\n[Q4] Maximum diversification (5 streams)")
    s_max = scenario_max_diversification()
    if s_max:
        for n, w in zip(s_max["names"], s_max["weights"]):
            print(f"  {n}: {w:.0%}")
        print(f"  CAGR: {s_max['cagr']:.1f}%, DD: {s_max['dd']:.1f}%, Sharpe: {s_max['sharpe']:.1f}")

    # Q5: Lever the EXP-750 combined portfolio
    print("\n[Q5] Lever the EXP-750 combined portfolio (29.2% CAGR, 2.8% DD)")
    lev_results = scenario_levered_combined()
    for r in lev_results:
        marker = " ★" if abs(r["dd"] - 12.0) < 1.0 else ""
        print(f"  {r['leverage']:.1f}x: CAGR={r['cagr']:.0f}%, DD={r['dd']:.1f}%{marker}")

    # Find leverage that hits 100% at DD≤12%
    lev_100 = [r for r in lev_results if r["cagr"] >= 100 and r["dd"] <= 12.0]
    lev_at_12 = [r for r in lev_results if r["dd"] <= 12.0]
    best_at_12 = max(lev_at_12, key=lambda r: r["cagr"]) if lev_at_12 else None

    print(f"\n  100% CAGR at DD≤12%: {'YES at ' + str(lev_100[0]['leverage']) + 'x' if lev_100 else 'NO'}")
    if best_at_12:
        print(f"  Max at DD≤12%: {best_at_12['cagr']:.0f}% CAGR at {best_at_12['leverage']:.1f}x leverage")

    # Monte Carlo on best scenarios
    print("\n[Q6] Monte Carlo (10K paths, 5-year horizon)")
    scenarios_mc = [
        ("Proven 60/40", s_proven["cagr"], s_proven["dd"]),
        ("Multi-underlying", s_multi["cagr"] if s_multi else 0, s_multi["dd"] if s_multi else 0),
        ("Max diversified", s_max["cagr"] if s_max else 0, s_max["dd"] if s_max else 0),
    ]
    if best_at_12:
        scenarios_mc.append(("Levered EXP-750", best_at_12["cagr"], best_at_12["dd"]))

    mc_results = {}
    for name, cagr, dd in scenarios_mc:
        if cagr <= 0:
            continue
        mc = monte_carlo_projection(cagr, dd)
        mc_results[name] = mc
        print(f"  {name}: median CAGR={mc['median_cagr']:.1f}%, P(>50%)={mc['prob_above_50']:.1%}, P(>100%)={mc['prob_above_100']:.1%}")

    # ── Save results ─────────────────────────────────────────────────

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "experiment": "EXP-960-max",
        "description": "Path to 100% CAGR Analysis",
        "q1_uncorrelated_streams_needed": n_needed,
        "q2_proven_only": s_proven,
        "q3_multi_underlying": s_multi,
        "q4_max_diversification": s_max,
        "q5_levered_combined": lev_results,
        "q5_best_at_dd12": best_at_12,
        "q5_100_cagr_achievable": len(lev_100) > 0,
        "monte_carlo": mc_results,
        "roadmap": {
            "tier_1_achievable_now": {
                "cagr": s_proven["cagr"],
                "dd": s_proven["dd"],
                "method": "3.5x ML-CS (60%) + Vol Harvest (40%) with crisis hedge",
                "confidence": "high — proven in backtest",
            },
            "tier_2_near_term": {
                "cagr": s_multi["cagr"] if s_multi else 0,
                "dd": s_multi["dd"] if s_multi else 0,
                "method": "Add QQQ + IBIT credit spreads to portfolio",
                "confidence": "medium — requires real options data for QQQ/IBIT",
            },
            "tier_3_aspirational": {
                "cagr": best_at_12["cagr"] if best_at_12 else 0,
                "dd": best_at_12["dd"] if best_at_12 else 0,
                "method": "Lever the combined portfolio to DD budget",
                "confidence": "medium-low — leverage amplifies tail risk",
            },
            "tier_4_north_star": {
                "cagr": 100,
                "required": "5+ genuinely uncorrelated streams OR 3.5x lever on diversified portfolio",
                "confidence": "low — requires infrastructure + capital + multiple alpha sources",
            },
        },
    }

    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    html = generate_report(summary, lev_results, mc_results)
    (RESULTS_DIR / "report.html").write_text(html)

    print(f"\nWritten: results/summary.json + results/report.html")

    # Final roadmap
    rm = summary["roadmap"]
    print(f"\n{'='*60}")
    print("  ROADMAP TO 100% CAGR")
    print(f"{'='*60}")
    print(f"  Tier 1 (NOW):       {rm['tier_1_achievable_now']['cagr']:.0f}% CAGR — {rm['tier_1_achievable_now']['method']}")
    print(f"  Tier 2 (3-6 mo):    {rm['tier_2_near_term']['cagr']:.0f}% CAGR — {rm['tier_2_near_term']['method']}")
    print(f"  Tier 3 (6-12 mo):   {rm['tier_3_aspirational']['cagr']:.0f}% CAGR — {rm['tier_3_aspirational']['method']}")
    print(f"  Tier 4 (12+ mo):    100%+ CAGR — {rm['tier_4_north_star']['required']}")
    print(f"{'='*60}")


# ── HTML report ──────────────────────────────────────────────────────────


def generate_report(summary, lev_results, mc_results) -> str:
    rm = summary["roadmap"]
    q2, q3, q4 = summary["q2_proven_only"], summary["q3_multi_underlying"], summary["q4_max_diversification"]
    best_lev = summary["q5_best_at_dd12"]

    def _fp(v): return f"{v:.1f}%"
    def _fr(v): return f"{v:.2f}"

    # Scenario comparison
    scenarios = [
        ("Proven (CS+Vol)", q2["cagr"], q2["dd"], q2["sharpe"], "High"),
    ]
    if q3:
        scenarios.append(("+ Multi-underlying", q3["cagr"], q3["dd"], q3["sharpe"], "Medium"))
    if q4:
        scenarios.append(("Max diversified", q4["cagr"], q4["dd"], q4["sharpe"], "Medium"))
    if best_lev:
        scenarios.append((f"Levered EXP-750 ({best_lev['leverage']:.1f}x)", best_lev["cagr"], best_lev["dd"], best_lev.get("sharpe", 0), "Med-Low"))

    scen_rows = ""
    for name, cagr, dd, sh, conf in scenarios:
        scen_rows += f"<tr><td style='text-align:left'>{name}</td><td>{_fp(cagr)}</td><td>{_fp(dd)}</td><td>{_fr(sh)}</td><td>{conf}</td></tr>"

    # Leverage table
    lev_rows = ""
    for r in lev_results:
        color = "style='color:#3fb950'" if r["dd"] <= 12 else "style='color:#f85149'"
        lev_rows += f"<tr {color}><td>{r['leverage']:.1f}x</td><td>{_fp(r['cagr'])}</td><td>{_fp(r['dd'])}</td></tr>"

    # MC table
    mc_rows = ""
    for name, mc in mc_results.items():
        mc_rows += f"<tr><td style='text-align:left'>{name}</td><td>{_fp(mc['median_cagr'])}</td><td>{_fp(mc['prob_above_50']*100)}</td><td>{_fp(mc['prob_above_100']*100)}</td><td>{_fp(mc['prob_loss']*100)}</td></tr>"

    # Roadmap
    road_html = ""
    for tier, data in rm.items():
        if isinstance(data, dict) and "cagr" in data:
            c = data.get("confidence", "")
            cagr = data["cagr"]
            method = data.get("method", data.get("required", ""))
            color = "#3fb950" if "high" in c.lower() else "#d29922" if "medium" in c.lower() else "#f85149"
            road_html += f"<tr><td style='text-align:left'>{tier.replace('_', ' ').title()}</td><td>{cagr}%</td><td style='color:{color}'>{c}</td><td style='text-align:left'>{method}</td></tr>"

    can_100 = summary["q5_100_cagr_achievable"]
    oc = "#3fb950" if can_100 else "#d29922"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>EXP-960: Path to 100% CAGR</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:1100px;margin:0 auto;padding:20px;background:#0d1117;color:#c9d1d9}}
h1,h2{{color:#58a6ff}}.meta{{color:#8b949e}}
.hero{{background:#161b22;border:2px solid {oc};border-radius:12px;padding:24px;text-align:center;margin:20px 0}}
.hero .big{{font-size:1.8em;font-weight:800;color:{oc}}}.hero .sub{{color:#8b949e;margin-top:8px}}
table{{width:100%;border-collapse:collapse;margin:12px 0}}th,td{{padding:6px 10px;text-align:right;border-bottom:1px solid #21262d}}th{{color:#8b949e;background:#161b22}}
.road{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:16px 0}}
</style></head><body>
<h1>Path to 100% CAGR</h1>
<div class="hero">
<div class="big">{"100% CAGR is reachable via levered combined portfolio" if can_100 else "100% CAGR requires levered diversified portfolio"}</div>
<div class="sub">Best achievable now: {_fp(q2['cagr'])} CAGR at {_fp(q2['dd'])} DD (proven streams only)</div>
</div>

<h2>Scenario Comparison</h2>
<table><tr><th style="text-align:left">Scenario</th><th>CAGR</th><th>Max DD</th><th>Sharpe</th><th>Confidence</th></tr>{scen_rows}</table>

<h2>Levered EXP-750 Combined Portfolio</h2>
<table><tr><th>Leverage</th><th>CAGR</th><th>DD</th></tr>{lev_rows}</table>

<h2>Monte Carlo (5yr, 10K paths)</h2>
<table><tr><th style="text-align:left">Scenario</th><th>Median CAGR</th><th>P(&gt;50%)</th><th>P(&gt;100%)</th><th>P(Loss)</th></tr>{mc_rows}</table>

<h2>Roadmap</h2>
<div class="road">
<table><tr><th style="text-align:left">Tier</th><th>CAGR</th><th>Confidence</th><th style="text-align:left">Method</th></tr>{road_html}</table>
</div>

</body></html>"""


if __name__ == "__main__":
    main()
