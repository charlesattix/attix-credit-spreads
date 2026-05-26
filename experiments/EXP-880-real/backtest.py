#!/usr/bin/env python3
"""
EXP-880-real: Re-backtest EXP-880 ML Production Ensemble using ONLY real
IronVault data.  Zero synthetic pricing — every option price comes from
options_cache.db (Polygon historical bars).

Uses the production Backtester class with IronVault as historical_data provider.
Config is derived from configs/paper_exp880.yaml with backtest-specific overrides.

Usage:
    cd attix-credit-spreads
    python experiments/EXP-880-real/backtest.py
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yaml

from backtest.backtester import Backtester
from shared.iron_vault import IronVault, IronVaultError

logger = logging.getLogger(__name__)

EXPERIMENT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EXPERIMENT_DIR / "results"
CONFIG_PATH = ROOT / "configs" / "paper_exp880.yaml"

# ── Backtest parameters ─────────────────────────────────────────────────

TICKER = "SPY"
START_DATE = datetime(2020, 1, 2)
END_DATE = datetime(2025, 12, 31)
STARTING_CAPITAL = 100_000.0


def load_config() -> Dict[str, Any]:
    """Load EXP-880 config from paper_exp880.yaml with backtest overrides."""
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    # Backtest-specific overrides (paper_exp880 is tuned for live trading)
    config["backtest"] = {
        "starting_capital": STARTING_CAPITAL,
        "commission_per_contract": 0.65,
        "slippage": 0.05,
        "exit_slippage": 0.10,
        "compound": False,          # flat sizing — matches paper_exp880.yaml
        "sizing_mode": "flat",      # flat risk per trade
        "generate_reports": False,
        "report_dir": str(RESULTS_DIR),
        "risk_cap": 25.0,           # allow full research risk
    }

    # Ensure strategy params match EXP-880 thesis
    config["strategy"]["direction"] = "both"
    config["strategy"]["regime_mode"] = "combo"
    config["strategy"]["target_dte"] = 15
    config["strategy"]["min_dte"] = 15
    config["strategy"]["max_dte"] = 25
    config["strategy"]["spread_width"] = 12

    # Fix: min_credit_pct must be in strategy section — backtester reads from
    # strategy_params, not risk_params.  Default of 15% is too aggressive for
    # 12-wide spreads at 2% OTM, filtering out all put entries.
    config["strategy"]["min_credit_pct"] = 5   # 5% of width = $0.60 min credit

    # Risk params
    config["risk"]["account_size"] = STARTING_CAPITAL
    config["risk"]["max_risk_per_trade"] = 8.5
    config["risk"]["max_positions"] = 8
    config["risk"]["profit_target"] = 55
    config["risk"]["stop_loss_pct_of_width"] = 90
    # Disable CB: crisis hedge V2 handles position scaling dynamically;
    # hard CB would prematurely block entries when real-data results diverge
    # from heuristic backtest expectations
    config["risk"]["drawdown_cb_pct"] = 99

    return config


def compute_yearly_metrics(
    trades: List[Dict], equity_curve: List, starting_capital: float,
) -> List[Dict[str, Any]]:
    """Compute per-year metrics from trade list and equity curve."""
    if not trades:
        return []

    trades_df = pd.DataFrame(trades)
    trades_df["exit_dt"] = pd.to_datetime(
        trades_df["exit_date"].apply(lambda d: str(d)[:10])
    )
    trades_df["year"] = trades_df["exit_dt"].dt.year

    # Build equity curve DataFrame
    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"])
    eq_df["date"] = pd.to_datetime(eq_df["date"])
    eq_df["year"] = eq_df["date"].dt.year

    yearly = []
    for year in sorted(trades_df["year"].unique()):
        year_trades = trades_df[trades_df["year"] == year]
        n_trades = len(year_trades)
        winners = year_trades[year_trades["pnl"] > 0]
        win_rate = len(winners) / n_trades if n_trades > 0 else 0.0
        total_pnl = year_trades["pnl"].sum()

        # Year start/end equity from equity curve
        year_eq = eq_df[eq_df["year"] == year]
        if len(year_eq) > 0:
            year_start_eq = year_eq["equity"].iloc[0]
            year_end_eq = year_eq["equity"].iloc[-1]
            year_return_pct = (year_end_eq - year_start_eq) / year_start_eq * 100
        else:
            year_return_pct = 0.0

        # Max drawdown within year
        if len(year_eq) > 1:
            eq_vals = year_eq["equity"].values
            peak = eq_vals[0]
            max_dd = 0.0
            for v in eq_vals:
                peak = max(peak, v)
                dd = (v - peak) / peak
                max_dd = min(max_dd, dd)
            year_max_dd = max_dd * 100
        else:
            year_max_dd = 0.0

        # Sharpe within year (daily equity returns)
        if len(year_eq) > 5:
            daily_returns = year_eq["equity"].pct_change().dropna()
            if daily_returns.std() > 0:
                year_sharpe = float(
                    daily_returns.mean() / daily_returns.std() * math.sqrt(252)
                )
            else:
                year_sharpe = 0.0
        else:
            year_sharpe = 0.0

        yearly.append({
            "year": int(year),
            "trades": n_trades,
            "pnl": round(total_pnl, 2),
            "return_pct": round(year_return_pct, 2),
            "win_rate": round(win_rate, 4),
            "max_drawdown_pct": round(year_max_dd, 2),
            "sharpe": round(year_sharpe, 2),
            "profitable": total_pnl > 0,
        })

    return yearly


def compute_cagr(starting: float, ending: float, years: float) -> float:
    """Compute compound annual growth rate."""
    if starting <= 0 or ending <= 0 or years <= 0:
        return 0.0
    return ((ending / starting) ** (1.0 / years) - 1.0) * 100.0


def generate_html_report(
    summary: Dict[str, Any], yearly: List[Dict], trades: List[Dict],
) -> str:
    """Generate self-contained HTML report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Yearly table rows
    yearly_rows = ""
    for y in yearly:
        pnl_cls = "good" if y["pnl"] > 0 else "bad"
        yearly_rows += f"""<tr>
          <td>{y['year']}</td>
          <td>{y['trades']}</td>
          <td class="{pnl_cls}">${y['pnl']:,.2f}</td>
          <td class="{pnl_cls}">{y['return_pct']:+.1f}%</td>
          <td>{y['win_rate']:.1%}</td>
          <td>{y['max_drawdown_pct']:.1f}%</td>
          <td>{y['sharpe']:.2f}</td>
        </tr>"""

    # Trade type breakdown
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    bp_count = len(trades_df[trades_df["type"] == "bull_put_spread"]) if len(trades_df) > 0 else 0
    bc_count = len(trades_df[trades_df["type"] == "bear_call_spread"]) if len(trades_df) > 0 else 0
    ic_count = len(trades_df[trades_df["type"] == "iron_condor"]) if len(trades_df) > 0 else 0

    s = summary

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>EXP-880-real: IronVault Backtest Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 1200px; margin: 0 auto; padding: 20px; background: #0d1117;
         color: #c9d1d9; }}
  h1, h2, h3 {{ color: #58a6ff; }}
  .meta {{ color: #8b949e; margin-bottom: 20px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
               gap: 16px; margin: 20px 0; }}
  .kpi {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px;
          padding: 20px; text-align: center; }}
  .kpi .value {{ font-size: 2em; font-weight: 800; }}
  .kpi .label {{ color: #8b949e; font-size: 0.85em; margin-top: 4px; }}
  .good {{ color: #3fb950; }}
  .bad {{ color: #f85149; }}
  .warn {{ color: #d29922; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 0.9em; }}
  th {{ background: #161b22; padding: 8px 12px; text-align: right; color: #8b949e;
       border-bottom: 2px solid #30363d; }}
  td {{ padding: 8px 12px; text-align: right; border-bottom: 1px solid #21262d; }}
  th:first-child, td:first-child {{ text-align: left; }}
  .badge {{ display: inline-block; padding: 4px 12px; border-radius: 6px;
            font-size: 0.85em; font-weight: 600; }}
  .badge.pass {{ background: #1a3a2a; color: #3fb950; }}
  .badge.fail {{ background: #3a1a1a; color: #f85149; }}
  .data-source {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px;
                  padding: 16px; margin: 16px 0; }}
  .data-source h3 {{ margin-top: 0; }}
  .criteria-table td:last-child {{ font-weight: 600; }}
  footer {{ margin-top: 3em; padding-top: 1em; border-top: 1px solid #21262d;
            font-size: 0.8em; color: #8b949e; }}
</style>
</head>
<body>

<h1>EXP-880-real: IronVault Backtest Report</h1>
<p class="meta">ML Production Ensemble — Real Options Data Only &middot; Generated {now}</p>

<div class="data-source">
  <h3>Data Source: IronVault (options_cache.db)</h3>
  <p>Every entry credit, daily mark, and exit price comes from real Polygon historical
     option bars. <strong>Zero synthetic pricing. Zero np.random for prices/returns.</strong></p>
  <p>Period: {START_DATE.strftime('%Y-%m-%d')} &rarr; {END_DATE.strftime('%Y-%m-%d')} &middot;
     Ticker: {TICKER} &middot; Starting Capital: ${STARTING_CAPITAL:,.0f}</p>
</div>

<h2>Key Performance Indicators</h2>
<div class="kpi-grid">
  <div class="kpi">
    <div class="value {'good' if s['cagr'] > 30 else 'warn'}">{s['cagr']:.1f}%</div>
    <div class="label">CAGR</div>
  </div>
  <div class="kpi">
    <div class="value {'good' if abs(s['max_drawdown']) < 20 else 'bad'}">{s['max_drawdown']:.1f}%</div>
    <div class="label">Max Drawdown</div>
  </div>
  <div class="kpi">
    <div class="value {'good' if s['sharpe'] > 2 else 'warn'}">{s['sharpe']:.2f}</div>
    <div class="label">Sharpe Ratio</div>
  </div>
  <div class="kpi">
    <div class="value {'good' if s['win_rate'] > 70 else 'warn'}">{s['win_rate']:.1f}%</div>
    <div class="label">Win Rate</div>
  </div>
  <div class="kpi">
    <div class="value">{s['total_trades']}</div>
    <div class="label">Total Trades</div>
  </div>
  <div class="kpi">
    <div class="value">{s['return_pct']:.1f}%</div>
    <div class="label">Total Return</div>
  </div>
  <div class="kpi">
    <div class="value">${s['ending_capital']:,.0f}</div>
    <div class="label">Ending Capital</div>
  </div>
  <div class="kpi">
    <div class="value">{s['profit_factor']:.2f}</div>
    <div class="label">Profit Factor</div>
  </div>
</div>

<h2>Trade Breakdown</h2>
<div class="kpi-grid">
  <div class="kpi">
    <div class="value">{bp_count}</div>
    <div class="label">Bull Put Spreads</div>
  </div>
  <div class="kpi">
    <div class="value">{bc_count}</div>
    <div class="label">Bear Call Spreads</div>
  </div>
  <div class="kpi">
    <div class="value">{ic_count}</div>
    <div class="label">Iron Condors</div>
  </div>
  <div class="kpi">
    <div class="value">${s['avg_win']:.2f}</div>
    <div class="label">Avg Win</div>
  </div>
  <div class="kpi">
    <div class="value bad">${s['avg_loss']:.2f}</div>
    <div class="label">Avg Loss</div>
  </div>
</div>

<h2>Yearly Performance</h2>
<table>
  <tr><th>Year</th><th>Trades</th><th>PnL</th><th>Return</th>
      <th>Win Rate</th><th>Max DD</th><th>Sharpe</th></tr>
  {yearly_rows}
</table>

<h2>Success Criteria</h2>
<table class="criteria-table">
  <tr><th>Criterion</th><th>Target</th><th>Actual</th><th>Status</th></tr>
  <tr><td>CAGR</td><td>&gt; 30%</td><td>{s['cagr']:.1f}%</td>
      <td><span class="badge {'pass' if s['cagr'] > 30 else 'fail'}">{'PASS' if s['cagr'] > 30 else 'FAIL'}</span></td></tr>
  <tr><td>Max Drawdown</td><td>&lt; 20%</td><td>{abs(s['max_drawdown']):.1f}%</td>
      <td><span class="badge {'pass' if abs(s['max_drawdown']) < 20 else 'fail'}">{'PASS' if abs(s['max_drawdown']) < 20 else 'FAIL'}</span></td></tr>
  <tr><td>Sharpe Ratio</td><td>&gt; 2.0</td><td>{s['sharpe']:.2f}</td>
      <td><span class="badge {'pass' if s['sharpe'] > 2 else 'fail'}">{'PASS' if s['sharpe'] > 2 else 'FAIL'}</span></td></tr>
  <tr><td>Win Rate</td><td>&gt; 70%</td><td>{s['win_rate']:.1f}%</td>
      <td><span class="badge {'pass' if s['win_rate'] > 70 else 'fail'}">{'PASS' if s['win_rate'] > 70 else 'FAIL'}</span></td></tr>
  <tr><td>Trades/Year</td><td>&gt; 20</td><td>{s['avg_trades_per_year']:.0f}</td>
      <td><span class="badge {'pass' if s['avg_trades_per_year'] > 20 else 'fail'}">{'PASS' if s['avg_trades_per_year'] > 20 else 'FAIL'}</span></td></tr>
</table>

<footer>
  Generated by <code>experiments/EXP-880-real/backtest.py</code> &middot;
  Data: IronVault (options_cache.db) &middot; READ-ONLY — no broker connections
</footer>

</body>
</html>"""


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Validate IronVault ────────────────────────────────────────────
    logger.info("Initializing IronVault...")
    try:
        hd = IronVault.instance()
    except IronVaultError as e:
        logger.error("IronVault initialization failed: %s", e)
        sys.exit(1)

    coverage = hd.coverage_report()
    logger.info(
        "IronVault ready: %d contracts, %d daily bars, tickers: %s",
        coverage["contracts_total"],
        coverage["daily_bars_total"],
        list(coverage["by_ticker"].keys()),
    )

    spy_info = coverage["by_ticker"].get("SPY", {})
    if not spy_info:
        logger.error("No SPY data in IronVault!")
        sys.exit(1)
    logger.info("SPY coverage: %d contracts, years %s", spy_info["contracts"], spy_info.get("years", []))

    # ── 2. Load config ───────────────────────────────────────────────────
    config = load_config()
    logger.info("Config loaded from %s", CONFIG_PATH)

    # ── 3. Run backtest ──────────────────────────────────────────────────
    logger.info("Running EXP-880-real backtest: %s %s → %s", TICKER, START_DATE.date(), END_DATE.date())

    bt = Backtester(
        config=config,
        historical_data=hd,
        otm_pct=config["strategy"].get("otm_pct", 0.02),
    )
    results = bt.run_backtest(TICKER, START_DATE, END_DATE)

    if not results or results.get("total_trades", 0) == 0:
        logger.error("Backtest returned no trades!")
        # Save empty result for debugging
        (RESULTS_DIR / "summary.json").write_text(json.dumps({
            "experiment": "EXP-880-real",
            "error": "No trades generated",
            "coverage": coverage,
        }, indent=2, default=str))
        sys.exit(1)

    logger.info("Backtest complete: %d trades", results["total_trades"])

    # ── 4. Compute metrics ───────────────────────────────────────────────
    trades = results.get("trades", [])
    equity_curve = results.get("equity_curve", [])
    yearly = compute_yearly_metrics(trades, equity_curve, STARTING_CAPITAL)

    ending_capital = results.get("ending_capital", STARTING_CAPITAL)
    n_years = (END_DATE - START_DATE).days / 365.25
    cagr = compute_cagr(STARTING_CAPITAL, ending_capital, n_years)
    avg_trades_per_year = results["total_trades"] / n_years if n_years > 0 else 0

    summary = {
        "experiment": "EXP-880-real",
        "description": "ML Production Ensemble — Real IronVault Data Only",
        "data_source": "IronVault (options_cache.db)",
        "synthetic_data_used": False,
        "ticker": TICKER,
        "period": f"{START_DATE.date()} → {END_DATE.date()}",
        "starting_capital": STARTING_CAPITAL,
        "ending_capital": round(ending_capital, 2),
        "total_trades": results["total_trades"],
        "winning_trades": results.get("winning_trades", 0),
        "losing_trades": results.get("losing_trades", 0),
        "win_rate": round(results.get("win_rate", 0), 2),
        "total_pnl": round(results.get("total_pnl", 0), 2),
        "return_pct": round(results.get("return_pct", 0), 2),
        "cagr": round(cagr, 2),
        "sharpe": round(results.get("sharpe_ratio", 0), 2),
        "max_drawdown": round(results.get("max_drawdown", 0), 2),
        "profit_factor": results.get("profit_factor", 0),
        "avg_win": round(results.get("avg_win", 0), 2),
        "avg_loss": round(results.get("avg_loss", 0), 2),
        "bull_put_trades": results.get("bull_put_trades", 0),
        "bear_call_trades": results.get("bear_call_trades", 0),
        "iron_condor_trades": results.get("iron_condor_trades", 0),
        "bull_put_win_rate": round(results.get("bull_put_win_rate", 0), 2),
        "bear_call_win_rate": round(results.get("bear_call_win_rate", 0), 2),
        "avg_trades_per_year": round(avg_trades_per_year, 1),
        "friday_fallback_count": results.get("friday_fallback_count", 0),
        "volume_skipped": results.get("volume_skipped", 0),
        "per_year": yearly,
        "monthly_pnl": results.get("monthly_pnl", {}),
        "success_criteria": {
            "cagr_above_30": {
                "target": 30.0,
                "actual": round(cagr, 2),
                "met": cagr > 30.0,
            },
            "max_dd_below_20": {
                "target": -20.0,
                "actual": round(results.get("max_drawdown", 0), 2),
                "met": abs(results.get("max_drawdown", 0)) < 20.0,
            },
            "sharpe_above_2": {
                "target": 2.0,
                "actual": round(results.get("sharpe_ratio", 0), 2),
                "met": results.get("sharpe_ratio", 0) > 2.0,
            },
            "win_rate_above_70": {
                "target": 70.0,
                "actual": round(results.get("win_rate", 0), 2),
                "met": results.get("win_rate", 0) > 70.0,
            },
            "trades_per_year_above_20": {
                "target": 20.0,
                "actual": round(avg_trades_per_year, 1),
                "met": avg_trades_per_year > 20.0,
            },
        },
        "ironvault_coverage": {
            "spy_contracts": spy_info.get("contracts", 0),
            "spy_years": spy_info.get("years", []),
            "total_daily_bars": coverage["daily_bars_total"],
        },
    }

    all_met = all(c["met"] for c in summary["success_criteria"].values())
    summary["all_criteria_met"] = all_met

    # ── 5. Save results ──────────────────────────────────────────────────
    summary_path = RESULTS_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    logger.info("Summary saved to %s", summary_path)

    # HTML report
    html = generate_html_report(summary, yearly, trades)
    report_path = RESULTS_DIR / "report.html"
    report_path.write_text(html, encoding="utf-8")
    logger.info("HTML report saved to %s", report_path)

    # ── 6. Print results ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("EXP-880-real: IronVault Backtest Results")
    print("=" * 70)
    print(f"Data Source:      IronVault (options_cache.db) — ZERO synthetic data")
    print(f"Period:           {START_DATE.date()} → {END_DATE.date()}")
    print(f"Starting Capital: ${STARTING_CAPITAL:,.0f}")
    print(f"Ending Capital:   ${ending_capital:,.0f}")
    print(f"Total Return:     {results.get('return_pct', 0):.1f}%")
    print(f"CAGR:             {cagr:.1f}%")
    print(f"Max Drawdown:     {results.get('max_drawdown', 0):.1f}%")
    print(f"Sharpe Ratio:     {results.get('sharpe_ratio', 0):.2f}")
    print(f"Win Rate:         {results.get('win_rate', 0):.1f}%")
    print(f"Total Trades:     {results['total_trades']}")
    print(f"Trades/Year:      {avg_trades_per_year:.0f}")
    print(f"Profit Factor:    {results.get('profit_factor', 0):.2f}")
    print()
    print("Yearly Breakdown:")
    print(f"{'Year':>6} {'Trades':>7} {'PnL':>12} {'Return':>8} {'WinRate':>8} {'MaxDD':>7} {'Sharpe':>7}")
    for y in yearly:
        print(
            f"{y['year']:>6} {y['trades']:>7} "
            f"${y['pnl']:>10,.2f} {y['return_pct']:>7.1f}% "
            f"{y['win_rate']:>7.1%} {y['max_drawdown_pct']:>6.1f}% "
            f"{y['sharpe']:>6.2f}"
        )
    print()
    print(f"All criteria met: {all_met}")
    print(f"Report: {report_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
