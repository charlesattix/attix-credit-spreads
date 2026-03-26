"""
circuit_breaker_backtest.py — Backtest the impact of three-tier portfolio
drawdown circuit breakers on the exp_126 historical results.

Simulates -8% (flatten) / -10% (pause) / -12% (halt) CB rules applied to
the equity curve produced by the backtester.  Compares:
  - Return% with and without CB
  - Max DD with and without CB
  - Number of Tier 1/2/3 triggers per year

Usage:
    python3 execution/circuit_breaker_backtest.py

Output:
    Prints a comparison table to stdout and writes a JSON summary to
    output/cb_backtest_exp126.json
"""

import json
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as a script
_here = Path(__file__).resolve().parent
_project_root = _here.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

# ── CB parameters to test ─────────────────────────────────────────────────────
CB_FLATTEN_PCT = 8    # -8%: flatten all positions
CB_PAUSE_PCT   = 10   # -10%: pause new entries
CB_HALT_PCT    = 12   # -12%: hard stop


def _run_year_with_cb(ticker: str, year: int, params: dict, starting_capital: float = 100_000) -> dict:
    """Run a single year backtest with portfolio CB params injected."""
    # Import here to avoid circular import issues
    sys.path.insert(0, str(_project_root))
    from scripts.run_optimization import run_year

    cb_params = dict(params)
    cb_params["portfolio_cb_flatten_pct"] = CB_FLATTEN_PCT
    cb_params["portfolio_cb_pause_pct"]   = CB_PAUSE_PCT
    cb_params["portfolio_cb_halt_pct"]    = CB_HALT_PCT

    return run_year(ticker, year, cb_params, starting_capital=starting_capital)


def _run_year_without_cb(ticker: str, year: int, params: dict, starting_capital: float = 100_000) -> dict:
    """Run a single year backtest without CB (baseline)."""
    from scripts.run_optimization import run_year
    return run_year(ticker, year, params, starting_capital=starting_capital)


def _simulate_cb_on_equity_curve(equity_curve: list, starting_capital: float) -> dict:
    """
    Post-hoc simulation of CB rules on a recorded equity curve.

    This is a faster alternative to re-running the full backtest.
    It applies the three-tier CB rules to the equity curve to estimate impact.

    Args:
        equity_curve: List of dicts with 'date' and 'equity' keys (from backtester results).
        starting_capital: Account size at the start of the period.

    Returns:
        Dict with max_drawdown, tier1/2/3 triggers, and simulated return pct.
    """
    hwm = starting_capital
    max_dd = 0.0
    tier1 = tier2 = tier3 = 0
    paused = halted = False

    # Track capped equity (what equity would have been with CB applied)
    capped_equity = []
    current_capped = starting_capital

    for point in equity_curve:
        eq = point.get("equity", point[1] if isinstance(point, (list, tuple)) else starting_capital)

        # Update HWM
        if eq > hwm:
            hwm = eq

        dd_frac = (eq - hwm) / hwm if hwm > 0 else 0.0
        max_dd = min(max_dd, dd_frac)  # track worst DD (most negative)

        # Tier 3: hard stop
        if not halted and dd_frac <= -CB_HALT_PCT / 100:
            tier3 += 1
            halted = True
            paused = True
            # After halt: equity stays flat (no new trades)
            current_capped = eq  # capture equity at halt point
        elif not paused and dd_frac <= -CB_FLATTEN_PCT / 100:
            # Tier 1: flatten — assume equity stays at current value (positions closed)
            tier1 += 1
            paused = True
            current_capped = eq
        elif not paused and dd_frac <= -CB_PAUSE_PCT / 100:
            # Tier 2: pause
            tier2 += 1
            paused = True
            current_capped = eq
        elif paused and not halted and dd_frac > -CB_FLATTEN_PCT / 100:
            # Recovery: lift pause when DD recovers above flatten threshold
            paused = False

        if not paused:
            current_capped = eq

        capped_equity.append(current_capped)

    final_capped = capped_equity[-1] if capped_equity else starting_capital
    sim_return_pct = (final_capped - starting_capital) / starting_capital * 100

    # Max DD on capped equity curve
    if capped_equity:
        cap_hwm = starting_capital
        cap_max_dd = 0.0
        for ce in capped_equity:
            if ce > cap_hwm:
                cap_hwm = ce
            dd = (ce - cap_hwm) / cap_hwm if cap_hwm > 0 else 0.0
            cap_max_dd = min(cap_max_dd, dd)
    else:
        cap_max_dd = 0.0

    return {
        "max_dd_baseline": round(max_dd * 100, 2),
        "max_dd_with_cb": round(cap_max_dd * 100, 2),
        "sim_return_pct": round(sim_return_pct, 2),
        "tier1_triggers": tier1,
        "tier2_triggers": tier2,
        "tier3_triggers": tier3,
        "paused_at_end": paused,
        "halted_at_end": halted,
    }


def run_analysis():
    """Run the full CB backtest analysis for exp_126 across 2020-2025."""
    from scripts.run_optimization import run_year

    # EXP-126 params (from MEMORY.md and config file)
    params = {
        "target_delta": 0.12,
        "use_delta_selection": False,
        "otm_pct": 0.03,
        "target_dte": 35,
        "min_dte": 25,
        "spread_width": 5,
        "min_credit_pct": 8,
        "stop_loss_multiplier": 3.5,
        "profit_target": 50,
        "max_risk_per_trade": 8.0,
        "max_contracts": 25,
        "direction": "both",
        "compound": False,
        "sizing_mode": "flat",
        "iron_condor_enabled": True,
        "ic_neutral_regime_only": True,
        "ic_min_combined_credit_pct": 8,
        "iv_rank_min_entry": 0,
        "drawdown_cb_pct": 30,
        "trend_ma_period": 200,
        "regime_mode": "combo",
        "regime_config": {
            "signals": ["price_vs_ma200", "rsi_momentum", "vix_structure"],
            "ma_slow_period": 200,
            "ma200_neutral_band_pct": 0.5,
            "rsi_period": 14,
            "rsi_bull_threshold": 55.0,
            "rsi_bear_threshold": 45.0,
            "vix_structure_bull": 0.95,
            "vix_structure_bear": 1.05,
            "bear_requires_unanimous": True,
            "cooldown_days": 3,
            "vix_extreme": 40.0,
        },
        "max_portfolio_exposure_pct": 100,
        # Portfolio CB enabled
        "portfolio_cb_flatten_pct": 0,  # will be set per run
        "portfolio_cb_pause_pct":   0,
        "portfolio_cb_halt_pct":    0,
    }

    years = [2020, 2021, 2022, 2023, 2024, 2025]
    starting_capital = 100_000

    print("\n" + "=" * 110)
    print("EXP-126: Three-Tier Portfolio CB Impact Analysis")
    print(f"CB thresholds: Tier1(flatten)=-{CB_FLATTEN_PCT}%  Tier2(pause)=-{CB_PAUSE_PCT}%  Tier3(halt)=-{CB_HALT_PCT}%")
    print("=" * 110)

    rows = []
    yearly_results = {}

    for year in years:
        print(f"\nRunning {year}...", end=" ", flush=True)

        # Run WITHOUT CB (baseline)
        baseline = run_year("SPY", year, params, starting_capital=starting_capital)
        ret_baseline = baseline.get("return_pct", 0)
        dd_baseline  = baseline.get("max_drawdown", 0)
        equity_curve = baseline.get("equity_curve", [])

        # Post-hoc CB simulation on the baseline equity curve
        cb_sim = _simulate_cb_on_equity_curve(equity_curve, starting_capital)

        # Also run WITH CB natively in the backtester to get authoritative trigger counts
        cb_params = dict(params)
        cb_params["portfolio_cb_flatten_pct"] = CB_FLATTEN_PCT
        cb_params["portfolio_cb_pause_pct"]   = CB_PAUSE_PCT
        cb_params["portfolio_cb_halt_pct"]    = CB_HALT_PCT
        native_cb = run_year("SPY", year, cb_params, starting_capital=starting_capital)
        ret_cb = native_cb.get("return_pct", 0)
        dd_cb  = native_cb.get("max_drawdown", 0)
        t1 = native_cb.get("portfolio_cb_tier1_triggers", 0)
        t2 = native_cb.get("portfolio_cb_tier2_triggers", 0)
        t3 = native_cb.get("portfolio_cb_tier3_triggers", 0)

        print(f"done. Baseline={ret_baseline:+.1f}% CB={ret_cb:+.1f}%")

        row = {
            "year": year,
            "return_baseline": round(ret_baseline, 1),
            "return_with_cb":  round(ret_cb, 1),
            "dd_baseline":     round(dd_baseline, 1),
            "dd_with_cb":      round(dd_cb, 1),
            "tier1_triggers":  t1,
            "tier2_triggers":  t2,
            "tier3_triggers":  t3,
            "sim_return":      cb_sim["sim_return_pct"],
            "sim_dd_with_cb":  cb_sim["max_dd_with_cb"],
        }
        rows.append(row)
        yearly_results[year] = {
            "baseline": baseline,
            "with_cb": native_cb,
            "cb_sim": cb_sim,
        }

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n" + "=" * 110)
    hdr = (
        f"{'Year':>6} | "
        f"{'Ret w/o CB':>12} | "
        f"{'Ret w/ CB':>11} | "
        f"{'DD w/o CB':>11} | "
        f"{'DD w/ CB':>10} | "
        f"{'Tier1':>7} | "
        f"{'Tier2':>7} | "
        f"{'Tier3':>7}"
    )
    print(hdr)
    print("-" * 110)

    returns_baseline = []
    returns_cb = []
    for r in rows:
        line = (
            f"{r['year']:>6} | "
            f"{r['return_baseline']:>+11.1f}% | "
            f"{r['return_with_cb']:>+10.1f}% | "
            f"{r['dd_baseline']:>+10.1f}% | "
            f"{r['dd_with_cb']:>+9.1f}% | "
            f"{r['tier1_triggers']:>7} | "
            f"{r['tier2_triggers']:>7} | "
            f"{r['tier3_triggers']:>7}"
        )
        print(line)
        returns_baseline.append(r["return_baseline"])
        returns_cb.append(r["return_with_cb"])

    avg_baseline = sum(returns_baseline) / len(returns_baseline)
    avg_cb       = sum(returns_cb)       / len(returns_cb)
    dd_all_baseline = [r["dd_baseline"] for r in rows]
    dd_all_cb       = [r["dd_with_cb"]  for r in rows]

    print("-" * 110)
    print(
        f"{'AVG':>6} | "
        f"{avg_baseline:>+11.1f}% | "
        f"{avg_cb:>+10.1f}% | "
        f"{min(dd_all_baseline):>+10.1f}% | "
        f"{min(dd_all_cb):>+9.1f}% | "
        f"{'':>7} | {'':>7} | {'':>7}"
    )
    print("=" * 110)

    return_sacrifice = avg_cb - avg_baseline
    dd_improvement = min(dd_all_baseline) - min(dd_all_cb)  # positive = CB improved DD
    print(f"\nReturn sacrifice from CB: {return_sacrifice:+.1f}% avg/year")
    print(f"Max DD improvement from CB: {dd_improvement:+.1f}% (worst single year)")

    # ── Write JSON output ──────────────────────────────────────────────────────
    output_dir = _project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "cb_backtest_exp126.json"

    summary = {
        "config": {
            "experiment": "exp_126",
            "cb_flatten_pct": CB_FLATTEN_PCT,
            "cb_pause_pct":   CB_PAUSE_PCT,
            "cb_halt_pct":    CB_HALT_PCT,
        },
        "summary": {
            "avg_return_baseline": round(avg_baseline, 1),
            "avg_return_with_cb":  round(avg_cb, 1),
            "return_sacrifice":    round(return_sacrifice, 1),
            "worst_dd_baseline":   round(min(dd_all_baseline), 1),
            "worst_dd_with_cb":    round(min(dd_all_cb), 1),
            "dd_improvement":      round(dd_improvement, 1),
        },
        "yearly": rows,
    }

    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nJSON output written to: {output_path}")
    return summary


if __name__ == "__main__":
    summary = run_analysis()
    # Print the key numbers for the report
    s = summary["summary"]
    print(f"\nKey findings:")
    print(f"  Avg return w/o CB: {s['avg_return_baseline']:+.1f}%")
    print(f"  Avg return w/ CB:  {s['avg_return_with_cb']:+.1f}%")
    print(f"  Return sacrifice:  {s['return_sacrifice']:+.1f}% / year")
    print(f"  Worst DD w/o CB:   {s['worst_dd_baseline']:+.1f}%")
    print(f"  Worst DD w/ CB:    {s['worst_dd_with_cb']:+.1f}%")
    print(f"  DD improvement:    {s['dd_improvement']:+.1f}%")
