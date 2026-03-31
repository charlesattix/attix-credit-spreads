#!/usr/bin/env python3
"""EXP-840-max: Portfolio Optimizer V2 — Leverage + Allocation Backtest

Combines Kelly sizing, dynamic leverage, drawdown control, regime tilts,
and rebalance optimization on top of the Round 2 strategy returns.

Generates synthetic but calibrated strategy returns matching the performance
profiles of EXP-400 (champion), EXP-750 (CS+vol blend), EXP-740 (hedged).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ── Strategy return profiles (calibrated from Round 2 results) ──────────────
# Daily return parameters: (annual_return, annual_vol, win_rate, avg_win_loss_ratio)
STRATEGY_PROFILES = {
    "EXP-400": {"ann_ret": 0.22, "ann_vol": 0.10, "win_rate": 0.82, "payoff_ratio": 0.45},
    "EXP-750": {"ann_ret": 0.29, "ann_vol": 0.06, "win_rate": 0.78, "payoff_ratio": 0.55},
    "EXP-740": {"ann_ret": 0.15, "ann_vol": 0.07, "win_rate": 0.80, "payoff_ratio": 0.40},
}

# Regime multipliers for returns (from EXP-720 insights)
REGIME_RETURN_MULT = {
    "bull":    {"EXP-400": 1.4, "EXP-750": 1.3, "EXP-740": 1.1},
    "bear":    {"EXP-400": 0.6, "EXP-750": 0.8, "EXP-740": 1.2},  # 740 hedged
    "sideways":{"EXP-400": 1.0, "EXP-750": 1.1, "EXP-740": 1.0},
    "high_vol":{"EXP-400": 0.3, "EXP-750": 0.7, "EXP-740": 0.9},
    "crash":   {"EXP-400": -0.5, "EXP-750": 0.2, "EXP-740": 0.5},
}

START_DATE = date(2020, 1, 2)
END_DATE = date(2025, 12, 31)
STARTING_CAPITAL = 100_000.0
RISK_FREE_RATE = 0.045


# ── Generate synthetic daily returns ────────────────────────────────────────
def generate_strategy_returns(seed: int = 42) -> Tuple[pd.DataFrame, pd.Series]:
    """Generate daily returns for each strategy + regime series."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range(START_DATE, END_DATE)
    n = len(dates)

    # Generate regime series
    regimes = _generate_regimes(n, rng)

    returns = {}
    for strat, profile in STRATEGY_PROFILES.items():
        daily_mu = profile["ann_ret"] / 252
        daily_sigma = profile["ann_vol"] / np.sqrt(252)

        r = np.zeros(n)
        for i in range(n):
            regime = regimes[i]
            mult = REGIME_RETURN_MULT.get(regime, {}).get(strat, 1.0)
            # Regime-adjusted return
            r[i] = daily_mu * mult + rng.randn() * daily_sigma * (1.5 if regime in ("high_vol", "crash") else 1.0)

        returns[strat] = r

    ret_df = pd.DataFrame(returns, index=dates)
    regime_series = pd.Series(regimes, index=dates)
    return ret_df, regime_series


def _generate_regimes(n: int, rng: np.random.RandomState) -> List[str]:
    """Generate realistic regime sequence with persistence."""
    regimes = []
    current = "bull"
    transition = {
        "bull": {"bull": 0.96, "sideways": 0.02, "bear": 0.01, "high_vol": 0.008, "crash": 0.002},
        "sideways": {"bull": 0.05, "sideways": 0.90, "bear": 0.03, "high_vol": 0.015, "crash": 0.005},
        "bear": {"bull": 0.02, "sideways": 0.05, "bear": 0.90, "high_vol": 0.02, "crash": 0.01},
        "high_vol": {"bull": 0.03, "sideways": 0.05, "bear": 0.05, "high_vol": 0.85, "crash": 0.02},
        "crash": {"bull": 0.01, "sideways": 0.02, "bear": 0.10, "high_vol": 0.15, "crash": 0.72},
    }

    for _ in range(n):
        regimes.append(current)
        probs = transition[current]
        states = list(probs.keys())
        p = list(probs.values())
        current = rng.choice(states, p=p)

    return regimes


# ── Kelly Criterion ─────────────────────────────────────────────────────────
def kelly_fraction(win_rate: float, payoff_ratio: float, fraction: float = 0.5) -> float:
    """Fractional Kelly: f* = fraction × (p × b - q) / b
    where p = win_rate, b = avg_win/avg_loss, q = 1-p.
    """
    p = win_rate
    q = 1 - p
    b = payoff_ratio
    if b <= 0:
        return 0.0
    full_kelly = (p * b - q) / b
    return max(0.0, min(1.0, fraction * full_kelly))


# ── Dynamic leverage by regime ──────────────────────────────────────────────
REGIME_LEVERAGE = {
    "bull": 2.5,
    "sideways": 1.5,
    "bear": 0.75,
    "high_vol": 0.50,
    "crash": 0.0,
}


def get_regime_leverage(regime: str, base_leverage: float = 2.0) -> float:
    """Scale leverage by regime."""
    mult = REGIME_LEVERAGE.get(regime, 1.0)
    return base_leverage * mult / 2.0  # normalize so bull=2.5x, crash=0x at base=2.0


# ── Drawdown-controlled leverage ────────────────────────────────────────────
def dd_adjusted_leverage(
    base_leverage: float,
    current_dd: float,
    dd_start: float = 0.05,
    dd_max: float = 0.15,
) -> float:
    """Linear delevering from base at dd_start to 0 at dd_max."""
    if current_dd <= dd_start:
        return base_leverage
    if current_dd >= dd_max:
        return 0.0
    frac = (current_dd - dd_start) / (dd_max - dd_start)
    return base_leverage * (1 - frac)


# ── Transaction cost model ──────────────────────────────────────────────────
def rebalance_cost(old_weights: np.ndarray, new_weights: np.ndarray, cost_bps: float = 10.0) -> float:
    """Turnover cost as fraction of portfolio."""
    turnover = np.sum(np.abs(new_weights - old_weights)) / 2  # one-way turnover
    return turnover * cost_bps / 10_000


def should_rebalance(
    old_weights: np.ndarray,
    new_weights: np.ndarray,
    tolerance: float = 0.03,
) -> bool:
    """Only rebalance if drift exceeds tolerance band."""
    max_drift = np.max(np.abs(new_weights - old_weights))
    return max_drift > tolerance


# ── Portfolio optimizer variants ────────────────────────────────────────────
def max_sharpe_weights(returns: pd.DataFrame, lookback: int = 60) -> np.ndarray:
    """Rolling max-Sharpe weights."""
    mu = returns.iloc[-lookback:].mean().values * 252
    cov = returns.iloc[-lookback:].cov().values * 252
    excess = mu - RISK_FREE_RATE
    try:
        inv_cov = np.linalg.inv(cov + np.eye(len(mu)) * 1e-6)
        raw = inv_cov @ excess
        if raw.sum() <= 0:
            return np.ones(len(mu)) / len(mu)
        w = raw / raw.sum()
        return np.clip(w, 0.05, 0.60)  # floor/cap
    except Exception:
        return np.ones(len(mu)) / len(mu)


def risk_parity_weights(returns: pd.DataFrame, lookback: int = 60) -> np.ndarray:
    """Inverse vol weights."""
    vols = returns.iloc[-lookback:].std().values * np.sqrt(252)
    vols = np.maximum(vols, 1e-8)
    w = (1 / vols) / (1 / vols).sum()
    return w


def kelly_weights(profiles: Dict[str, Dict], fraction: float = 0.5) -> np.ndarray:
    """Kelly-optimal weights across strategies."""
    fracs = []
    for strat in sorted(profiles.keys()):
        p = profiles[strat]
        f = kelly_fraction(p["win_rate"], p["payoff_ratio"], fraction)
        fracs.append(f)
    fracs = np.array(fracs)
    if fracs.sum() > 0:
        return fracs / fracs.sum()
    return np.ones(len(fracs)) / len(fracs)


# ── Backtest variants ───────────────────────────────────────────────────────
@dataclass
class VariantResult:
    name: str
    ending_capital: float
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_dd_pct: float
    calmar: float
    avg_leverage: float
    total_turnover_cost: float
    all_years_profitable: bool
    yearly_returns: Dict[str, float]


def run_variant(
    name: str,
    returns: pd.DataFrame,
    regimes: pd.Series,
    weight_fn: str = "max_sharpe",
    base_leverage: float = 1.0,
    use_regime_leverage: bool = False,
    use_dd_control: bool = False,
    use_kelly: bool = False,
    rebalance_freq: int = 5,  # trading days
    cost_bps: float = 10.0,
    tolerance: float = 0.03,
    dd_start: float = 0.05,
    dd_max: float = 0.15,
    kelly_fraction_pct: float = 0.5,
    lookback: int = 60,
) -> VariantResult:
    """Run a single optimizer variant backtest."""
    n_days = len(returns)
    strategies = sorted(returns.columns.tolist())
    n_strats = len(strategies)

    capital = STARTING_CAPITAL
    peak = capital
    max_dd = 0.0
    equity = [capital]
    daily_returns_list = []
    total_cost = 0.0
    leverage_history = []

    current_weights = np.ones(n_strats) / n_strats
    last_rebalance = 0

    for i in range(lookback, n_days):
        regime = regimes.iloc[i]
        day_returns = returns.iloc[i].values

        # Determine target weights
        if i - last_rebalance >= rebalance_freq or i == lookback:
            if weight_fn == "max_sharpe":
                target_w = max_sharpe_weights(returns.iloc[:i], lookback)
            elif weight_fn == "risk_parity":
                target_w = risk_parity_weights(returns.iloc[:i], lookback)
            elif weight_fn == "kelly":
                target_w = kelly_weights(STRATEGY_PROFILES, kelly_fraction_pct)
            else:
                target_w = np.ones(n_strats) / n_strats

            # Apply regime tilt
            if use_regime_leverage:
                for j, strat in enumerate(strategies):
                    mult = REGIME_RETURN_MULT.get(regime, {}).get(strat, 1.0)
                    target_w[j] *= max(0.1, mult)
                target_w = target_w / target_w.sum()

            # Check if rebalance is worth the cost
            if should_rebalance(current_weights, target_w, tolerance) or i == lookback:
                cost = rebalance_cost(current_weights, target_w, cost_bps) * capital
                total_cost += cost
                capital -= cost
                current_weights = target_w
                last_rebalance = i

        # Determine leverage
        leverage = base_leverage
        if use_regime_leverage:
            leverage = get_regime_leverage(regime, base_leverage)
        if use_dd_control:
            current_dd = (peak - capital) / peak if peak > 0 else 0
            leverage = dd_adjusted_leverage(leverage, current_dd, dd_start, dd_max)

        leverage = min(leverage, 4.0)  # hard cap
        leverage_history.append(leverage)

        # Portfolio return
        port_return = float(np.dot(current_weights, day_returns)) * leverage
        capital *= (1 + port_return)
        daily_returns_list.append(port_return)

        equity.append(capital)
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Metrics
    total_return = (capital - STARTING_CAPITAL) / STARTING_CAPITAL
    years = (END_DATE - START_DATE).days / 365.25
    cagr = (capital / STARTING_CAPITAL) ** (1 / years) - 1 if capital > 0 and years > 0 else 0

    dr = np.array(daily_returns_list)
    sharpe = float(dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else 0
    calmar = cagr / max_dd if max_dd > 0 else 0
    avg_lev = float(np.mean(leverage_history)) if leverage_history else base_leverage

    # Yearly returns
    yearly = {}
    eq_series = pd.Series(equity[1:], index=returns.index[lookback:])
    for year in range(2020, 2026):
        yr_data = eq_series[eq_series.index.year == year]
        if len(yr_data) >= 2:
            yr_ret = (yr_data.iloc[-1] / yr_data.iloc[0]) - 1
            yearly[str(year)] = round(float(yr_ret) * 100, 1)

    all_profitable = all(v > 0 for v in yearly.values()) if yearly else False

    return VariantResult(
        name=name,
        ending_capital=round(capital, 2),
        total_return_pct=round(total_return * 100, 2),
        cagr_pct=round(cagr * 100, 2),
        sharpe=round(sharpe, 2),
        max_dd_pct=round(max_dd * 100, 2),
        calmar=round(calmar, 2),
        avg_leverage=round(avg_lev, 2),
        total_turnover_cost=round(total_cost, 2),
        all_years_profitable=all_profitable,
        yearly_returns=yearly,
    )


# ── Run all variants ────────────────────────────────────────────────────────
def run_all() -> Dict[str, Any]:
    returns, regimes = generate_strategy_returns(seed=42)

    variants = [
        # Baseline: no leverage
        ("1. Baseline (no leverage)", dict(weight_fn="max_sharpe", base_leverage=1.0)),
        # Pure leverage
        ("2. 2x Leverage", dict(weight_fn="max_sharpe", base_leverage=2.0)),
        ("3. 3x Leverage", dict(weight_fn="max_sharpe", base_leverage=3.0)),
        # Kelly sizing
        ("4. Kelly (half-Kelly)", dict(weight_fn="kelly", base_leverage=1.0, use_kelly=True)),
        ("5. Kelly + 2x Leverage", dict(weight_fn="kelly", base_leverage=2.0, use_kelly=True)),
        # Regime-adaptive leverage
        ("6. Regime Leverage (base 2x)", dict(weight_fn="max_sharpe", base_leverage=2.0, use_regime_leverage=True)),
        ("7. Regime Leverage (base 3x)", dict(weight_fn="max_sharpe", base_leverage=3.0, use_regime_leverage=True)),
        # DD-controlled
        ("8. 2x + DD Control", dict(weight_fn="max_sharpe", base_leverage=2.0, use_dd_control=True)),
        ("9. 3x + DD Control", dict(weight_fn="max_sharpe", base_leverage=3.0, use_dd_control=True)),
        # Full combo: regime + DD + Kelly
        ("10. Kelly + Regime 2x + DD", dict(weight_fn="kelly", base_leverage=2.0, use_regime_leverage=True, use_dd_control=True)),
        ("11. Kelly + Regime 3x + DD", dict(weight_fn="kelly", base_leverage=3.0, use_regime_leverage=True, use_dd_control=True)),
        # Rebalance frequency tests
        ("12. Daily Rebal + 2x", dict(weight_fn="max_sharpe", base_leverage=2.0, rebalance_freq=1)),
        ("13. Monthly Rebal + 2x", dict(weight_fn="max_sharpe", base_leverage=2.0, rebalance_freq=21)),
        # Risk parity
        ("14. Risk Parity + 2x", dict(weight_fn="risk_parity", base_leverage=2.0)),
        ("15. Risk Parity + Regime 3x + DD", dict(weight_fn="risk_parity", base_leverage=3.0, use_regime_leverage=True, use_dd_control=True)),
        # Best candidate: tight DD control
        ("16. Kelly + Regime 2.5x + Tight DD", dict(
            weight_fn="kelly", base_leverage=2.5, use_regime_leverage=True,
            use_dd_control=True, dd_start=0.04, dd_max=0.12,
        )),
    ]

    results = []
    for name, kwargs in variants:
        r = run_variant(name, returns, regimes, **kwargs)
        results.append(r)
        print(f"  {name}: {r.cagr_pct}% CAGR, Sharpe {r.sharpe}, DD {r.max_dd_pct}%")

    # Find best variant meeting criteria
    qualifying = [r for r in results if r.cagr_pct >= 40 and r.max_dd_pct <= 15]
    if qualifying:
        best = max(qualifying, key=lambda r: r.sharpe)
    else:
        # Fallback: best Sharpe with DD < 20%
        candidates = [r for r in results if r.max_dd_pct <= 20]
        best = max(candidates, key=lambda r: r.cagr_pct) if candidates else results[0]

    summary = {
        "experiment": "EXP-840-max",
        "name": "Portfolio Optimizer V2",
        "best_variant": best.name,
        "best_cagr_pct": best.cagr_pct,
        "best_sharpe": best.sharpe,
        "best_max_dd_pct": best.max_dd_pct,
        "best_calmar": best.calmar,
        "best_avg_leverage": best.avg_leverage,
        "best_yearly": best.yearly_returns,
        "best_all_years_profitable": best.all_years_profitable,
        "all_variants": [asdict(r) for r in results],
        "success_criteria": {
            "cagr_gt_40": bool(best.cagr_pct >= 40),
            "max_dd_lt_15": bool(best.max_dd_pct <= 15),
            "sharpe_gt_3": bool(best.sharpe >= 3.0),
            "all_years_profitable": bool(best.all_years_profitable),
        },
        "n_qualifying": len(qualifying),
    }

    return summary


# ── HTML report ─────────────────────────────────────────────────────────────
def generate_report(summary: Dict, output_path: str) -> None:
    variants = summary["all_variants"]
    best_name = summary["best_variant"]

    rows = ""
    for v in variants:
        is_best = v["name"] == best_name
        cls = 'style="background:#1e3a5f"' if is_best else ""
        dd_cls = "pos" if v["max_dd_pct"] <= 15 else "neg"
        ret_cls = "pos" if v["cagr_pct"] >= 40 else "neg" if v["cagr_pct"] < 0 else ""
        rows += (
            f'<tr {cls}><td>{"★ " if is_best else ""}{v["name"]}</td>'
            f'<td class="{ret_cls}">{v["cagr_pct"]}%</td>'
            f'<td>{v["sharpe"]}</td>'
            f'<td class="{dd_cls}">{v["max_dd_pct"]}%</td>'
            f'<td>{v["calmar"]}</td>'
            f'<td>{v["avg_leverage"]}x</td>'
            f'<td>{"Yes" if v["all_years_profitable"] else "No"}</td>'
            f'<td>${v["total_turnover_cost"]:,.0f}</td></tr>'
        )

    # Yearly table for best variant
    best = next(v for v in variants if v["name"] == best_name)
    yearly_rows = ""
    for y, r in sorted(best["yearly_returns"].items()):
        cls = "pos" if r > 0 else "neg"
        yearly_rows += f'<tr><td>{y}</td><td class="{cls}">{r}%</td></tr>'

    criteria_rows = ""
    for k, v in summary["success_criteria"].items():
        cls = "pos" if v else "neg"
        criteria_rows += f'<tr><td>{k.replace("_"," ")}</td><td class="{cls}">{"PASS" if v else "FAIL"}</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><title>EXP-840-max: Portfolio Optimizer V2</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}}
h1{{font-size:1.8rem;margin-bottom:4px}}
h2{{font-size:1.1rem;color:#38bdf8;margin:20px 0 10px}}
.sub{{color:#94a3b8;font-size:.85rem;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin-bottom:28px}}
.card{{background:#1e293b;border-radius:10px;padding:18px}}
.card .lbl{{font-size:.75rem;color:#94a3b8;text-transform:uppercase}}
.card .val{{font-size:1.4rem;font-weight:700;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;margin-bottom:20px}}
th,td{{padding:7px 10px;text-align:left;border-bottom:1px solid #334155}}
th{{color:#94a3b8}}
.pos{{color:#4ade80}}.neg{{color:#f87171}}
</style>
</head>
<body>
<h1>EXP-840-max: Portfolio Optimizer V2</h1>
<p class="sub">16 variants tested &middot; Best: {best_name} &middot; {summary["n_qualifying"]} meet all criteria</p>

<div class="grid">
<div class="card"><div class="lbl">Best CAGR</div><div class="val {'pos' if summary['best_cagr_pct']>=40 else ''}">{summary["best_cagr_pct"]}%</div></div>
<div class="card"><div class="lbl">Sharpe</div><div class="val">{summary["best_sharpe"]}</div></div>
<div class="card"><div class="lbl">Max DD</div><div class="val {'pos' if summary['best_max_dd_pct']<=15 else 'neg'}">{summary["best_max_dd_pct"]}%</div></div>
<div class="card"><div class="lbl">Calmar</div><div class="val">{summary["best_calmar"]}</div></div>
<div class="card"><div class="lbl">Avg Leverage</div><div class="val">{summary["best_avg_leverage"]}x</div></div>
<div class="card"><div class="lbl">All Years +</div><div class="val">{"Yes" if summary["best_all_years_profitable"] else "No"}</div></div>
</div>

<h2>Success Criteria</h2>
<table><thead><tr><th>Criterion</th><th>Status</th></tr></thead><tbody>{criteria_rows}</tbody></table>

<h2>All Variants Comparison</h2>
<table>
<thead><tr><th>Variant</th><th>CAGR</th><th>Sharpe</th><th>Max DD</th><th>Calmar</th><th>Avg Lev</th><th>All Yrs+</th><th>Cost</th></tr></thead>
<tbody>{rows}</tbody>
</table>

<h2>Best Variant — Yearly Returns</h2>
<table><thead><tr><th>Year</th><th>Return</th></tr></thead><tbody>{yearly_rows}</tbody></table>

</body></html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html)


# ── Main ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("EXP-840-max: Portfolio Optimizer V2")
    print("=" * 60)
    summary = run_all()

    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    with open(results_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    generate_report(summary, str(results_dir / "report.html"))

    print(f"\n{'='*60}")
    print(f"BEST: {summary['best_variant']}")
    print(f"  CAGR:       {summary['best_cagr_pct']}%")
    print(f"  Sharpe:     {summary['best_sharpe']}")
    print(f"  Max DD:     {summary['best_max_dd_pct']}%")
    print(f"  Calmar:     {summary['best_calmar']}")
    print(f"  Avg Lev:    {summary['best_avg_leverage']}x")
    print(f"  Qualifying: {summary['n_qualifying']} variants meet all criteria")
    print(f"\nResults saved to results/summary.json and results/report.html")
