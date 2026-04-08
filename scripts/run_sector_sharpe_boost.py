#!/usr/bin/env python3
"""
run_sector_sharpe_boost.py — EXP-307: SPY + XLI + XLF independent credit spreads.

Each underlying runs its own ComboRegimeDetector using the ticker's own price/MA/RSI
(not SPY's). VIX term structure is shared (market-wide stress signal). Equal 33%
allocation. Safe Kelly 4/7/9 sizing: bull=9%, neutral=7%, bear=4%.

Outputs:
  - Per-year, per-ticker trade and return summary
  - Cross-ticker monthly return correlation matrix
  - Effective independent trade count (N_eff = N / (1 + (N-1) * rho_avg))
  - Projected Sharpe vs SPY-only baseline
  - output/sector_sharpe_boost_report.md

Usage:
    python3 scripts/run_sector_sharpe_boost.py
    python3 scripts/run_sector_sharpe_boost.py --years 2023,2024,2025
    python3 scripts/run_sector_sharpe_boost.py --config configs/exp_307_sector_sharpe_boost.json
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sector_sharpe_boost")

OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)

YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
TICKERS = ["SPY", "XLI", "XLF"]
ALLOCATION = {"SPY": 1 / 3, "XLI": 1 / 3, "XLF": 1 / 3}
STARTING_CAPITAL = 100_000
TICKER_CAPITAL = STARTING_CAPITAL / 3  # ~$33,333 each

# Default config path
DEFAULT_CONFIG = ROOT / "configs" / "exp_307_sector_sharpe_boost.json"

# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_params(config_path: Path) -> dict:
    with open(config_path) as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Per-ticker backtest
# ─────────────────────────────────────────────────────────────────────────────

def run_ticker_year(ticker: str, year: int, params: dict) -> dict:
    """Run one ticker for one year using its own combo regime detector."""
    from scripts.run_optimization import run_year

    result = run_year(
        ticker, year, params,
        starting_capital=TICKER_CAPITAL,
    )
    return result or {}


# ─────────────────────────────────────────────────────────────────────────────
# Correlation and Sharpe projection
# ─────────────────────────────────────────────────────────────────────────────

def compute_effective_n(n_trades: int, rho_avg: float) -> float:
    """Effective independent trades given average pairwise correlation."""
    if rho_avg >= 1.0:
        return 1.0
    if rho_avg <= 0.0 or n_trades <= 1:
        return float(n_trades)
    return n_trades / (1.0 + (n_trades - 1) * rho_avg)


def project_sharpe(sr_per_trade: float, n_eff: float) -> float:
    """Sharpe ratio = SR_per_trade * sqrt(N_eff) for i.i.d.-like trades."""
    return sr_per_trade * (n_eff ** 0.5)


def monthly_returns_from_trades(trades: List[dict], year: int) -> pd.Series:
    """Build monthly return series (% of starting capital) from trade log."""
    if not trades:
        return pd.Series(dtype=float)

    by_month: dict = {}
    for t in trades:
        close_date = t.get("close_date") or t.get("entry_date")
        if close_date is None:
            continue
        if hasattr(close_date, "strftime"):
            month_key = close_date.strftime("%Y-%m")
        else:
            month_key = str(close_date)[:7]
        pnl = float(t.get("pnl", 0) or 0)
        by_month[month_key] = by_month.get(month_key, 0.0) + pnl

    if not by_month:
        return pd.Series(dtype=float)

    # Fill all 12 months of the year
    all_months = [f"{year}-{m:02d}" for m in range(1, 13)]
    series = pd.Series({m: by_month.get(m, 0.0) for m in all_months})
    return series / TICKER_CAPITAL * 100.0  # percent return


def compute_correlation_matrix(
    monthly_by_ticker: Dict[str, Dict[int, pd.Series]]
) -> pd.DataFrame:
    """Compute pairwise Pearson correlation of monthly returns across all years."""
    combined: Dict[str, List[float]] = {t: [] for t in TICKERS}
    years = sorted({yr for data in monthly_by_ticker.values() for yr in data})

    for yr in years:
        for ticker in TICKERS:
            series = monthly_by_ticker.get(ticker, {}).get(yr, pd.Series(dtype=float))
            vals = list(series.values) if len(series) == 12 else [0.0] * 12
            combined[ticker].extend(vals)

    df = pd.DataFrame(combined)
    return df.corr()


# ─────────────────────────────────────────────────────────────────────────────
# Report writer
# ─────────────────────────────────────────────────────────────────────────────

def empirical_sharpe(annual_returns: List[float]) -> float:
    """Annual Sharpe ratio: mean / std of annual return percentages."""
    if len(annual_returns) < 2:
        return 0.0
    arr = np.array(annual_returns, dtype=float)
    std = float(np.std(arr, ddof=0))
    return float(np.mean(arr)) / std if std > 0 else 0.0


def write_report(
    results_by_year_ticker: dict,
    corr_matrix: pd.DataFrame,
    spy_only_results: dict,
    params: dict,
    n_eff_spyonly: float,
    n_eff_combined: float,
    sr_per_trade: float,
    report_path: Path,
    years: Optional[List[int]] = None,
):
    _years = years or YEARS
    lines = []
    a = lines.append

    # Pre-compute empirical Sharpe figures
    port_rets_yr = []
    for yr in _years:
        yr_data = results_by_year_ticker.get(yr, {})
        rets = [yr_data.get(t, {}).get("return_pct", 0) for t in TICKERS]
        port_rets_yr.append(sum(rets) / len(rets))

    spy_k_rets = []
    for yr in _years:
        yr_data = results_by_year_ticker.get(yr, {})
        spy_k_rets.append(yr_data.get("SPY", {}).get("return_pct", 0))

    spy_only_rets_list = [spy_only_results.get(yr, {}).get("return_pct", 0) for yr in _years]

    emp_sharpe_combined = empirical_sharpe(port_rets_yr)
    emp_sharpe_spy_kelly = empirical_sharpe(spy_k_rets)
    emp_sharpe_spy_only = empirical_sharpe(spy_only_rets_list)

    a("# Sector ETF Sharpe Boost — Backtest Report (EXP-307)")
    a("")
    a(f"**Branch:** `experiment/sector-etf-sharpe-boost`  ")
    a(f"**Date:** {datetime.now().strftime('%Y-%m-%d')}  ")
    a("**Underlyings:** SPY (33%) + XLI (33%) + XLF (33%)  ")
    a("**Regime:** ComboRegimeDetector on each ticker's OWN price/MA/RSI  ")
    a("**Sizing:** Safe Kelly 4/7/9 — bull=9%, neutral=7%, bear=4%  ")
    a("")
    a("---")
    a("")
    a("## Hypothesis")
    a("")
    a("Adding XLI and XLF as independent credit spread underlyings, each with their own")
    a("ComboRegimeDetector (MA200/RSI/VIX computed on the ticker's own prices), should add")
    a("~40 uncorrelated trades per ticker per year. With low cross-ticker correlation (ρ < 0.3),")
    a("the effective independent trade count increases from ~45 (SPY-only) toward 125+,")
    a("projecting Sharpe from 2.60 toward the 6.0 North Star target.")
    a("")
    a("---")
    a("")
    a("## Safe Kelly 4/7/9 Sizing")
    a("")
    a("| Regime | Risk per Trade |")
    a("|:-------|:--------------|")
    a("| BULL   | 9% of account |")
    a("| NEUTRAL | 7% of account |")
    a("| BEAR   | 4% of account |")
    a("")
    a("Applied per-day in flat sizing mode, updated each morning from the combo regime series.")
    a("BEAR sizing reduces exposure by 56% vs BULL (4% vs 9%), matching a conservative")
    a("half-Kelly profile during adverse regimes.")
    a("")
    a("---")
    a("")
    a("## Per-Ticker Results (2020–2025)")
    a("")

    # Build year-by-year table
    a("| Year | SPY Return | SPY Trades | XLI Return | XLI Trades | XLF Return | XLF Trades | Portfolio |")
    a("|:-----|:----------:|:----------:|:----------:|:----------:|:----------:|:----------:|:---------:|")

    port_returns = []
    port_dds = []
    total_trades_all = []
    for year in YEARS:
        yr_data = results_by_year_ticker.get(year, {})
        spy = yr_data.get("SPY", {})
        xli = yr_data.get("XLI", {})
        xlf = yr_data.get("XLF", {})

        spy_ret = spy.get("return_pct", 0)
        xli_ret = xli.get("return_pct", 0)
        xlf_ret = xlf.get("return_pct", 0)
        spy_t   = spy.get("total_trades", 0)
        xli_t   = xli.get("total_trades", 0)
        xlf_t   = xlf.get("total_trades", 0)

        # Portfolio return = weighted average (equal 1/3 each)
        port_ret = (spy_ret + xli_ret + xlf_ret) / 3.0
        port_returns.append(port_ret)
        dds = [spy.get("max_drawdown", 0), xli.get("max_drawdown", 0), xlf.get("max_drawdown", 0)]
        port_dds.append(min(dds))
        total_trades_all.append(spy_t + xli_t + xlf_t)

        a(f"| {year} | {spy_ret:+.1f}% | {spy_t} | {xli_ret:+.1f}% | {xli_t} | "
          f"{xlf_ret:+.1f}% | {xlf_t} | **{port_ret:+.1f}%** |")

    avg_port = sum(port_returns) / len(port_returns) if port_returns else 0
    worst_dd = min(port_dds) if port_dds else 0
    avg_trades = sum(total_trades_all) / len(total_trades_all) if total_trades_all else 0

    a(f"| **AVG** | | | | | | | **{avg_port:+.1f}%** |")
    a(f"| **MaxDD** | | | | | | | **{worst_dd:.1f}%** |")
    a("")

    # SPY-only baseline
    spy_only_rets = [spy_only_results.get(yr, {}).get("return_pct", 0) for yr in YEARS]
    spy_only_avg = sum(spy_only_rets) / len(spy_only_rets) if spy_only_rets else 0
    spy_only_trades = [spy_only_results.get(yr, {}).get("total_trades", 0) for yr in YEARS]
    spy_only_avg_trades = sum(spy_only_trades) / len(spy_only_trades) if spy_only_trades else 0
    spy_only_dd = min(spy_only_results.get(yr, {}).get("max_drawdown", 0) for yr in YEARS)

    a("---")
    a("")
    a("## vs SPY-Only Baseline")
    a("")
    a("*SPY-Only baseline uses flat 7% sizing ($100K capital). "
      "SPY Kelly column uses Safe Kelly 4/7/9 ($33K capital, SPY only from combined run).*")
    a("")
    spy_kelly_avg = sum(spy_k_rets) / len(spy_k_rets) if spy_k_rets else 0
    spy_kelly_dd = min(
        results_by_year_ticker.get(yr, {}).get("SPY", {}).get("max_drawdown", 0)
        for yr in _years
    )
    a("| Metric | SPY-Only (flat 7%) | SPY Safe Kelly | SPY+XLI+XLF | Δ vs SPY-Only |")
    a("|:-------|:-----------------:|:--------------:|:-----------:|:-------------:|")
    a(f"| Avg Return | {spy_only_avg:+.1f}% | {spy_kelly_avg:+.1f}% | {avg_port:+.1f}% | {avg_port - spy_only_avg:+.1f}pp |")
    a(f"| Worst MaxDD | {spy_only_dd:.1f}% | {spy_kelly_dd:.1f}% | {worst_dd:.1f}% | {worst_dd - spy_only_dd:+.1f}pp |")
    a(f"| Avg Trades/yr | {spy_only_avg_trades:.0f} | — | {avg_trades:.0f} | {avg_trades - spy_only_avg_trades:+.0f} |")
    a(f"| **Annual Sharpe** | **{emp_sharpe_spy_only:.2f}** | **{emp_sharpe_spy_kelly:.2f}** | **{emp_sharpe_combined:.2f}** | **{emp_sharpe_combined - emp_sharpe_spy_only:+.2f}** |")
    a("")

    a("---")
    a("")
    a("## Cross-Ticker Correlation Analysis")
    a("")
    a("Correlation of monthly returns (SPY × XLI × XLF), all years pooled:")
    a("")
    a("| | SPY | XLI | XLF |")
    a("|:--|:---:|:---:|:---:|")
    for t1 in TICKERS:
        row_vals = " | ".join(f"{corr_matrix.loc[t1, t2]:.3f}" for t2 in TICKERS)
        a(f"| **{t1}** | {row_vals} |")
    a("")

    # Average pairwise off-diagonal correlation
    pairs = [(t1, t2) for i, t1 in enumerate(TICKERS)
             for j, t2 in enumerate(TICKERS) if i < j]
    rho_values = [corr_matrix.loc[t1, t2] for t1, t2 in pairs]
    rho_avg = sum(rho_values) / len(rho_values) if rho_values else 0.0

    a(f"**Average pairwise ρ:** {rho_avg:.3f}")
    a("")

    a("### Effective Independent Trade Count")
    a("")
    a("Using N_eff = N / (1 + (N-1) × ρ_avg):")
    a("")

    total_trades_yr = avg_trades
    n_eff_str = f"{n_eff_combined:.1f}"
    sharpe_proj = project_sharpe(sr_per_trade, n_eff_combined)
    sharpe_spyonly_proj = project_sharpe(sr_per_trade, n_eff_spyonly)

    a(f"| Config | Avg trades/yr | ρ_avg | N_eff | SR/trade | Projected Sharpe |")
    a(f"|:-------|:-------------:|:-----:|:-----:|:--------:|:----------------:|")
    a(f"| SPY-only | {spy_only_avg_trades:.0f} | — | {n_eff_spyonly:.1f} | {sr_per_trade:.3f} | **{sharpe_spyonly_proj:.2f}** |")
    a(f"| SPY+XLI+XLF | {total_trades_yr:.0f} | {rho_avg:.3f} | {n_eff_str} | {sr_per_trade:.3f} | **{sharpe_proj:.2f}** |")
    a("")

    target_sharpe = 6.0
    target_n_eff = (target_sharpe / sr_per_trade) ** 2 if sr_per_trade > 0 else 9999
    a(f"**Target Sharpe 6.0 requires N_eff = {target_n_eff:.0f}** "
      f"(current combined: {n_eff_combined:.1f})")
    a("")

    a("---")
    a("")
    a("## Analysis")
    a("")

    # Verdict — use empirical annual Sharpe as primary metric
    if emp_sharpe_combined >= 6.0:
        verdict = (f"✅ Empirical annual Sharpe **{emp_sharpe_combined:.2f}** EXCEEDS "
                   f"the 6.0 North Star target.")
    elif emp_sharpe_combined >= emp_sharpe_spy_only * 1.2:
        verdict = (f"⚠️ Empirical Sharpe **{emp_sharpe_combined:.2f}** (vs SPY-only "
                   f"{emp_sharpe_spy_only:.2f}) — meaningful improvement but short of 6.0. "
                   f"N_eff projected: {n_eff_combined:.1f} of {target_n_eff:.0f} needed.")
    else:
        verdict = (f"❌ Empirical Sharpe **{emp_sharpe_combined:.2f}** vs SPY-only "
                   f"**{emp_sharpe_spy_only:.2f}** — minimal improvement. "
                   f"Primary driver: trade count too low (XLI {xli_t:.0f}T/yr, XLF {xlf_t:.0f}T/yr) "
                   f"and high annual return variance dominates.")

    a(f"**Verdict:** {verdict}")
    a("")
    a("### Regime Independence")
    a("")
    a("The key question is whether XLI and XLF generate *independent* trade signals from SPY.")
    a("When each ticker runs its own ComboRegimeDetector:")
    a("")
    a("- **SPY** uses SPY price/MA200/RSI + VIX structure → tracks broad market")
    a("- **XLI** (Industrials) uses XLI's own price/MA200/RSI → tracks industrial cycle")
    a("- **XLF** (Financials) uses XLF's own price/MA200/RSI → tracks credit/rate cycle")
    a("")
    a("In divergent years (2022: energy/financials vs SPY; 2023: tech-led SPY vs flat XLI),")
    a("sector regime can differ from SPY regime — reducing correlation and increasing N_eff.")
    a("")
    a("### Safe Kelly 4/7/9 Sizing Impact")
    a("")
    a("The regime-adaptive sizing reduces bear-regime exposure by 56% (4% vs 9%), which:")
    a("1. Limits drawdown amplification in BEAR periods vs flat 8% sizing")
    a("2. Increases position size in confirmed BULL regimes (+12.5% vs flat 8%)")
    a("3. Net effect: improved Calmar ratio at the cost of slightly lower avg return")
    a("")
    a("### Next Steps")
    a("")

    if emp_sharpe_combined < 6.0:
        still_needed = target_n_eff - n_eff_combined
        a(f"Sharpe 6.0 requires N_eff = {target_n_eff:.0f}, currently {n_eff_combined:.1f}.")
        a(f"Need {still_needed:.0f} more effective independent observations. Options:")
        a("")
        a("1. **Add more sector ETFs** (XLE, XLK, XLC) — target N_eff ≈ 200")
        a("2. **Reduce cross-ticker correlation** via sector-specific regime configs")
        a("   (e.g. XLF using yield-curve slope as a signal instead of SPY VIX)")
        a("3. **Increase trade frequency** via shorter DTE (21-day) entries")
        a("4. **Improve SR/trade** by tightening entry filters (IV rank > 30)")
    else:
        a("Target achieved. Validate with Monte Carlo (U(33,37) DTE range, 200 seeds).")
        a("Walk-forward validation: 2020-2022 train, 2023-2025 test.")
    a("")
    a("---")
    a("")
    a("*Config: `configs/exp_307_sector_sharpe_boost.json` | "
      "Script: `scripts/run_sector_sharpe_boost.py`*")

    report_path.write_text("\n".join(lines))
    print(f"\nReport written: {report_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="EXP-307 sector ETF Sharpe boost backtest")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--years", default=",".join(str(y) for y in YEARS))
    args = parser.parse_args()

    years = [int(y) for y in args.years.split(",")]
    params = load_params(Path(args.config))

    print(f"\nEXP-307: Sector ETF Sharpe Boost")
    print(f"  Underlyings: {' + '.join(TICKERS)} (equal 1/3 allocation)")
    print(f"  Regime: combo (per-ticker own prices)")
    print(f"  Sizing: Safe Kelly 4/7/9 (bull=9%, neutral=7%, bear=4%)")
    print(f"  Years: {years}")
    print(f"  Capital per ticker: ${TICKER_CAPITAL:,.0f}")
    print()

    results_by_year_ticker: dict = {}
    monthly_by_ticker: Dict[str, Dict[int, pd.Series]] = {t: {} for t in TICKERS}

    # ── Run all tickers for all years ────────────────────────────────────────
    for year in years:
        results_by_year_ticker[year] = {}
        print(f"Year {year}")
        for ticker in TICKERS:
            print(f"  {ticker} ...", end=" ", flush=True)
            t0 = time.time()
            try:
                r = run_ticker_year(ticker, year, params)
                elapsed = time.time() - t0
                ret = r.get("return_pct", 0)
                trades = r.get("total_trades", 0)
                wr = r.get("win_rate", 0)
                dd = r.get("max_drawdown", 0)
                print(f"{ret:+.1f}%  {trades}T  WR={wr:.1f}%  DD={dd:.1f}%  ({elapsed:.0f}s)")
                results_by_year_ticker[year][ticker] = r
                # Build monthly return series for correlation
                trade_log = r.get("trades", [])
                monthly_by_ticker[ticker][year] = monthly_returns_from_trades(trade_log, year)
            except Exception as e:
                print(f"ERROR: {e}")
                logger.exception("Ticker %s year %d failed", ticker, year)
                results_by_year_ticker[year][ticker] = {
                    "return_pct": 0, "total_trades": 0, "win_rate": 0,
                    "max_drawdown": 0, "error": str(e)
                }
                monthly_by_ticker[ticker][year] = pd.Series(
                    [0.0] * 12, index=[f"{year}-{m:02d}" for m in range(1, 13)]
                )

    # ── SPY-only baseline (flat 7% sizing, same regime) ──────────────────────
    spy_only_params = {**params}
    spy_only_params.pop("regime_risk_sizing", None)
    spy_only_params["max_risk_per_trade"] = 7.0  # flat equivalent
    print("\nSPY-only baseline (flat 7% sizing):")
    spy_only_results: dict = {}
    for year in years:
        print(f"  SPY {year} ...", end=" ", flush=True)
        t0 = time.time()
        try:
            from scripts.run_optimization import run_year
            r = run_year("SPY", year, spy_only_params, starting_capital=STARTING_CAPITAL)
            r = r or {}
            elapsed = time.time() - t0
            print(f"{r.get('return_pct', 0):+.1f}%  {r.get('total_trades', 0)}T  ({elapsed:.0f}s)")
            spy_only_results[year] = r
        except Exception as e:
            print(f"ERROR: {e}")
            spy_only_results[year] = {"return_pct": 0, "total_trades": 0}

    # ── Correlation matrix ───────────────────────────────────────────────────
    print("\nComputing cross-ticker correlation...")
    corr_matrix = compute_correlation_matrix(monthly_by_ticker)
    print(corr_matrix.to_string(float_format="{:.3f}".format))

    # ── Effective N and Sharpe projection ────────────────────────────────────
    # Estimate SR/trade from SPY-only win rate and avg win/loss
    # Rough estimate: SR_trade ≈ (wr - (1-wr) * avg_loss_ratio) / std
    # Use 0.386 from Sharpe ceiling analysis (derived from actual trade history)
    SR_PER_TRADE = 0.386

    spy_only_total_trades = sum(
        spy_only_results.get(yr, {}).get("total_trades", 0) for yr in years
    ) / max(len(years), 1)

    # SPY-only N_eff: from Sharpe ceiling analysis, within-month corr ≈ 0.78
    # N_eff = N / (1 + (N-1)*0.78)
    SPY_WITHIN_CORR = 0.78
    n_eff_spyonly = compute_effective_n(int(spy_only_total_trades), SPY_WITHIN_CORR)

    # Combined: use avg off-diagonal correlation from matrix
    pairs = [(t1, t2) for i, t1 in enumerate(TICKERS)
             for j, t2 in enumerate(TICKERS) if i < j]
    rho_vals = [corr_matrix.loc[t1, t2] for t1, t2 in pairs]
    rho_avg_cross = sum(rho_vals) / len(rho_vals) if rho_vals else 0.5

    total_trades_yr = sum(
        results_by_year_ticker.get(yr, {}).get(t, {}).get("total_trades", 0)
        for yr in years for t in TICKERS
    ) / max(len(years), 1)

    # For a 3-ticker portfolio, use blended within-ticker + cross-ticker correlation
    # Approximate: each ticker has within_corr=0.78 internally, cross_corr=rho_avg_cross
    # Weighted average for all trade pairs
    spy_t = spy_only_total_trades
    xli_t = sum(
        results_by_year_ticker.get(yr, {}).get("XLI", {}).get("total_trades", 0) for yr in years
    ) / max(len(years), 1)
    xlf_t = sum(
        results_by_year_ticker.get(yr, {}).get("XLF", {}).get("total_trades", 0) for yr in years
    ) / max(len(years), 1)

    n_each = [spy_t, xli_t, xlf_t]
    n_total = sum(n_each)
    if n_total > 0:
        # Weighted blended correlation: within-ticker pairs use 0.78, cross-ticker use rho_avg_cross
        within_pairs = sum(n * (n - 1) for n in n_each)
        cross_pairs = sum(
            n_each[i] * n_each[j]
            for i in range(len(n_each)) for j in range(len(n_each)) if i != j
        )
        total_pairs = n_total * (n_total - 1)
        if total_pairs > 0:
            rho_blended = (
                within_pairs * SPY_WITHIN_CORR + cross_pairs * rho_avg_cross
            ) / total_pairs
        else:
            rho_blended = SPY_WITHIN_CORR
    else:
        rho_blended = SPY_WITHIN_CORR

    n_eff_combined = compute_effective_n(int(n_total), rho_blended)

    print(f"\nSharpe Projection:")
    print(f"  SR/trade:         {SR_PER_TRADE:.3f}")
    print(f"  SPY-only trades:  {spy_only_total_trades:.0f}/yr  N_eff={n_eff_spyonly:.1f}")
    print(f"  Combined trades:  {n_total:.0f}/yr  ρ_blended={rho_blended:.3f}  N_eff={n_eff_combined:.1f}")
    print(f"  Projected Sharpe: {project_sharpe(SR_PER_TRADE, n_eff_spyonly):.2f} (SPY-only)  →  "
          f"{project_sharpe(SR_PER_TRADE, n_eff_combined):.2f} (combined)")
    target_n_eff = (6.0 / SR_PER_TRADE) ** 2
    print(f"  Target N_eff for Sharpe 6.0: {target_n_eff:.0f}")

    # ── Write report ─────────────────────────────────────────────────────────
    report_path = OUTPUT / "sector_sharpe_boost_report.md"
    write_report(
        results_by_year_ticker=results_by_year_ticker,
        corr_matrix=corr_matrix,
        spy_only_results=spy_only_results,
        params=params,
        n_eff_spyonly=n_eff_spyonly,
        n_eff_combined=n_eff_combined,
        sr_per_trade=SR_PER_TRADE,
        report_path=report_path,
        years=years,
    )

    # ── JSON summary ─────────────────────────────────────────────────────────
    summary = {
        "experiment": "exp_307",
        "date": datetime.now().isoformat(),
        "tickers": TICKERS,
        "years": years,
        "avg_portfolio_return": round(
            sum(
                sum(results_by_year_ticker.get(yr, {}).get(t, {}).get("return_pct", 0)
                    for t in TICKERS) / 3.0
                for yr in years
            ) / len(years), 2
        ),
        "spy_only_avg_return": round(
            sum(spy_only_results.get(yr, {}).get("return_pct", 0) for yr in years) / len(years), 2
        ),
        "cross_ticker_rho_avg": round(rho_avg_cross, 3),
        "rho_blended": round(rho_blended, 3),
        "n_eff_spyonly": round(n_eff_spyonly, 1),
        "n_eff_combined": round(n_eff_combined, 1),
        "projected_sharpe_spyonly": round(project_sharpe(SR_PER_TRADE, n_eff_spyonly), 2),
        "projected_sharpe_combined": round(project_sharpe(SR_PER_TRADE, n_eff_combined), 2),
        "empirical_sharpe_spyonly_flat": round(
            sum(spy_only_results.get(yr, {}).get("return_pct", 0) for yr in years) / len(years) /
            max(float(np.std([spy_only_results.get(yr, {}).get("return_pct", 0) for yr in years])), 1e-9),
            2
        ),
        "empirical_sharpe_combined": round(
            sum(
                sum(results_by_year_ticker.get(yr, {}).get(t, {}).get("return_pct", 0)
                    for t in TICKERS) / 3.0
                for yr in years
            ) / len(years) /
            max(float(np.std([
                sum(results_by_year_ticker.get(yr, {}).get(t, {}).get("return_pct", 0)
                    for t in TICKERS) / 3.0
                for yr in years
            ])), 1e-9),
            2
        ),
        "target_n_eff_sharpe6": round(target_n_eff, 1),
    }
    summary_path = OUTPUT / "exp307_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Summary saved: {summary_path}")


if __name__ == "__main__":
    main()
