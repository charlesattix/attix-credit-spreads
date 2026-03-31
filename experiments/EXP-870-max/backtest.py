"""
EXP-870-max: Multi-Underlying Expansion.

Simulate credit spread strategies on 6 underlyings (SPY, QQQ, IWM, GLD,
TLT, IBIT) using realistic market microstructure per underlying.
Compute per-underlying performance, cross-correlations, capacity, and
optimal multi-underlying portfolio weights.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
DATA_PATH = REPO_ROOT / "compass" / "training_data_combined.csv"
RESULTS_DIR = ROOT / "results"

STARTING_CAPITAL = 100_000.0

# ── Underlying profiles (from public market data as of 2025) ────────────
# bid_ask: typical spread for ATM weekly puts ($)
# daily_volume: average daily options volume (contracts)
# open_interest: typical ATM OI
# iv_premium: implied vol premium over SPY (multiplier)
# spy_corr: typical daily return correlation with SPY
# capacity_factor: relative to SPY (1.0 = SPY level)
# slippage_per_contract: estimated realistic slippage ($)

UNDERLYINGS = {
    "SPY": {
        "name": "S&P 500 ETF",
        "asset_class": "US Large Cap Equity",
        "price": 430.0,
        "bid_ask": 0.03,
        "daily_volume": 3_500_000,
        "open_interest": 500_000,
        "iv_premium": 1.0,
        "spy_corr": 1.00,
        "capacity_factor": 1.00,
        "slippage_per_contract": 0.03,
        "spread_width": 5.0,
        "credit_multiplier": 1.0,
        "win_rate_adj": 0.0,     # adjustment to base win rate
        "vol_multiplier": 1.0,
    },
    "QQQ": {
        "name": "Nasdaq 100 ETF",
        "asset_class": "US Tech Equity",
        "price": 370.0,
        "bid_ask": 0.04,
        "daily_volume": 2_200_000,
        "open_interest": 350_000,
        "iv_premium": 1.15,       # QQQ typically 15% higher IV than SPY
        "spy_corr": 0.92,
        "capacity_factor": 0.70,
        "slippage_per_contract": 0.04,
        "spread_width": 5.0,
        "credit_multiplier": 1.12, # higher IV → more credit
        "win_rate_adj": -0.02,    # slightly lower WR (more volatile)
        "vol_multiplier": 1.20,
    },
    "IWM": {
        "name": "Russell 2000 ETF",
        "asset_class": "US Small Cap Equity",
        "price": 200.0,
        "bid_ask": 0.06,
        "daily_volume": 1_100_000,
        "open_interest": 180_000,
        "iv_premium": 1.25,
        "spy_corr": 0.85,
        "capacity_factor": 0.35,
        "slippage_per_contract": 0.06,
        "spread_width": 3.0,      # tighter spreads at lower price
        "credit_multiplier": 1.08,
        "win_rate_adj": -0.04,    # small cap more volatile
        "vol_multiplier": 1.35,
    },
    "GLD": {
        "name": "Gold ETF",
        "asset_class": "Commodities",
        "price": 190.0,
        "bid_ask": 0.08,
        "daily_volume": 350_000,
        "open_interest": 80_000,
        "iv_premium": 0.80,       # gold IV typically lower
        "spy_corr": 0.05,         # near-zero correlation!
        "capacity_factor": 0.15,
        "slippage_per_contract": 0.08,
        "spread_width": 3.0,
        "credit_multiplier": 0.75, # lower IV → less credit
        "win_rate_adj": +0.03,    # gold mean-reverts well
        "vol_multiplier": 0.70,
    },
    "TLT": {
        "name": "20+ Year Treasury ETF",
        "asset_class": "Fixed Income",
        "price": 95.0,
        "bid_ask": 0.07,
        "daily_volume": 500_000,
        "open_interest": 120_000,
        "iv_premium": 0.90,
        "spy_corr": -0.30,        # negative correlation!
        "capacity_factor": 0.20,
        "slippage_per_contract": 0.07,
        "spread_width": 2.0,
        "credit_multiplier": 0.80,
        "win_rate_adj": +0.02,    # bonds mean-revert
        "vol_multiplier": 0.85,
    },
    "IBIT": {
        "name": "iShares Bitcoin ETF",
        "asset_class": "Crypto",
        "price": 45.0,
        "bid_ask": 0.10,
        "daily_volume": 800_000,
        "open_interest": 100_000,
        "iv_premium": 2.50,       # crypto has extreme IV
        "spy_corr": 0.35,
        "capacity_factor": 0.25,
        "slippage_per_contract": 0.10,
        "spread_width": 2.0,
        "credit_multiplier": 2.00, # massive premium
        "win_rate_adj": -0.08,    # much lower WR from tail risk
        "vol_multiplier": 2.50,
    },
}


# ── Helpers ─────────────────────────────────────────────────────────────

def sharpe(returns: np.ndarray) -> float:
    if len(returns) < 2 or np.std(returns) == 0:
        return 0.0
    return float(np.mean(returns) / np.std(returns) * np.sqrt(252))


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) < 2:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1.0)
    return float(np.min(dd))


def profit_factor(pnls: np.ndarray) -> float:
    w = pnls[pnls > 0].sum()
    l = abs(pnls[pnls < 0].sum())
    return float(w / l) if l > 0 else (99.9 if w > 0 else 0)


def annual_return(equity: np.ndarray, n_days: int) -> float:
    if len(equity) < 2 or equity[0] <= 0 or n_days <= 0:
        return 0.0
    total = equity[-1] / equity[0]
    years = n_days / 252
    if years <= 0 or total <= 0:
        return 0.0
    return float(total ** (1 / years) - 1)


# ── Per-underlying backtest ─────────────────────────────────────────────

def backtest_underlying(
    base_trades: pd.DataFrame,
    ticker: str,
    profile: Dict[str, Any],
    seed: int = 42,
) -> Dict[str, Any]:
    """Simulate credit spread strategy on a single underlying.

    Adapts SPY base trades to the underlying using its profile:
    - Adjust credits by credit_multiplier and IV premium
    - Adjust win rate by win_rate_adj
    - Apply realistic slippage per underlying
    - Scale PnL by vol_multiplier
    """
    rng = np.random.RandomState(seed + hash(ticker) % 10000)
    n = len(base_trades)

    # Adapt base trades
    base_credits = base_trades["net_credit"].abs().values
    base_wins = base_trades["win"].values.astype(int)
    base_pnls = base_trades["pnl"].values
    base_regimes = base_trades["regime"].values

    # Adjust credits
    adj_credits = base_credits * profile["credit_multiplier"]

    # Adjust win rate: re-roll outcomes with adjusted probability
    base_wr = float(base_wins.mean())
    adj_wr = max(0.30, min(0.95, base_wr + profile["win_rate_adj"]))
    adj_wins = (rng.random(n) < adj_wr).astype(int)

    # Generate PnL per trade
    spread_width = profile["spread_width"]
    slippage = profile["slippage_per_contract"]
    contracts = base_trades["contracts"].values.astype(int)

    pnls = np.zeros(n)
    for i in range(n):
        c = adj_credits[i]
        w = spread_width
        ct = contracts[i]
        s = slippage * ct * 2  # entry + exit slippage

        if adj_wins[i]:
            # Win: collect fraction of credit (50-100%)
            frac = rng.uniform(0.50, 1.0)
            pnls[i] = c * frac * ct * 100 - s
        else:
            # Loss: lose fraction of max loss
            loss_frac = rng.uniform(0.3, 1.0)
            pnls[i] = -(w - c) * loss_frac * ct * 100 - s

    # Scale by vol multiplier (higher vol = bigger swings)
    pnls *= profile["vol_multiplier"]

    # Equity curve
    equity = STARTING_CAPITAL + np.cumsum(pnls)
    equity_full = np.concatenate([[STARTING_CAPITAL], equity])
    n_days = max(1, len(base_trades) * 7)  # approximate calendar days

    # Capacity estimation
    # Max AUM = ADV × avg_price × 2% participation / avg_notional_per_trade
    avg_notional = float(np.mean(contracts) * spread_width * 100)
    max_daily_notional = profile["daily_volume"] * profile["price"] * 0.02
    trades_per_day = max(n / (n_days / 7 * 5), 0.1)  # trading days
    capacity = max_daily_notional / max(trades_per_day * avg_notional, 1) * STARTING_CAPITAL
    capacity = min(capacity, 5e9)

    # Per-regime breakdown
    regime_stats = {}
    for regime in sorted(set(base_regimes)):
        mask = base_regimes == regime
        if mask.sum() == 0:
            continue
        r_pnls = pnls[mask]
        regime_stats[regime] = {
            "n": int(mask.sum()),
            "pnl": float(r_pnls.sum()),
            "wr": float((r_pnls > 0).mean()),
            "avg_pnl": float(r_pnls.mean()),
        }

    return {
        "ticker": ticker,
        "name": profile["name"],
        "asset_class": profile["asset_class"],
        "n_trades": n,
        "win_rate": float((pnls > 0).mean()),
        "total_pnl": float(pnls.sum()),
        "annual_return": annual_return(equity_full, n_days),
        "max_drawdown": max_drawdown(equity_full),
        "sharpe": sharpe(pnls / STARTING_CAPITAL),
        "profit_factor": min(profit_factor(pnls), 99.9),
        "avg_pnl": float(pnls.mean()),
        "total_slippage": float(slippage * np.sum(contracts) * 2),
        "slippage_pct_of_pnl": float(slippage * np.sum(contracts) * 2 / max(abs(pnls.sum()), 1) * 100),
        "spy_correlation": profile["spy_corr"],
        "capacity_est": capacity,
        "bid_ask": profile["bid_ask"],
        "daily_volume": profile["daily_volume"],
        "open_interest": profile["open_interest"],
        "regime_breakdown": regime_stats,
        "daily_returns": (pnls / STARTING_CAPITAL).tolist(),
    }


# ── Portfolio optimiser ─────────────────────────────────────────────────

def build_correlation_matrix(results: List[Dict]) -> Tuple[np.ndarray, List[str]]:
    """Build correlation matrix from daily return streams."""
    tickers = [r["ticker"] for r in results]
    n = len(tickers)
    # Pad shorter series
    max_len = max(len(r["daily_returns"]) for r in results)
    returns_matrix = np.zeros((max_len, n))
    for j, r in enumerate(results):
        rets = np.array(r["daily_returns"])
        returns_matrix[:len(rets), j] = rets

    corr = np.corrcoef(returns_matrix.T)
    return corr, tickers


def optimize_portfolio(
    results: List[Dict],
    corr: np.ndarray,
    method: str = "equal",
) -> Dict[str, Any]:
    """Compute portfolio weights and metrics."""
    tickers = [r["ticker"] for r in results]
    n = len(tickers)
    vols = np.array([np.std(r["daily_returns"]) * np.sqrt(252) for r in results])
    rets = np.array([r["annual_return"] for r in results])
    capacities = np.array([r["capacity_est"] for r in results])

    if method == "equal":
        w = np.ones(n) / n
    elif method == "risk_parity":
        inv_vol = 1.0 / np.maximum(vols, 0.01)
        w = inv_vol / inv_vol.sum()
    elif method == "max_sharpe":
        # Analytical: w ∝ Σ^{-1} μ
        cov = np.outer(vols, vols) * corr
        try:
            reg = np.eye(n) * 1e-6
            inv_cov = np.linalg.inv(cov + reg)
            w = inv_cov @ rets
            w = np.maximum(w, 0)
            w = w / w.sum() if w.sum() > 0 else np.ones(n) / n
        except np.linalg.LinAlgError:
            w = np.ones(n) / n
    elif method == "capacity_weighted":
        w = capacities / capacities.sum()
    else:
        w = np.ones(n) / n

    # Portfolio metrics
    port_ret = float(w @ rets)
    cov = np.outer(vols, vols) * corr
    port_var = float(w @ cov @ w)
    port_vol = math.sqrt(max(port_var, 0))
    port_sharpe = port_ret / port_vol if port_vol > 0 else 0

    # Diversification ratio
    weighted_avg_vol = float(w @ vols)
    div_ratio = weighted_avg_vol / port_vol if port_vol > 0 else 1

    # Combined capacity
    port_capacity = float(w @ capacities)

    # Max DD estimate (weighted average of individual DDs, reduced by diversification)
    dds = np.array([abs(r["max_drawdown"]) for r in results])
    port_dd = float(w @ dds) / max(div_ratio, 1.0)

    # Combined daily returns
    max_len = max(len(r["daily_returns"]) for r in results)
    combined_rets = np.zeros(max_len)
    for j, r in enumerate(results):
        dr = np.array(r["daily_returns"])
        combined_rets[:len(dr)] += w[j] * dr

    return {
        "method": method,
        "weights": {t: float(w[i]) for i, t in enumerate(tickers)},
        "portfolio_return": port_ret,
        "portfolio_vol": port_vol,
        "portfolio_sharpe": port_sharpe,
        "portfolio_dd": -port_dd,
        "diversification_ratio": div_ratio,
        "portfolio_capacity": port_capacity,
        "combined_pnl": float(combined_rets.sum() * STARTING_CAPITAL),
    }


# ── HTML report ─────────────────────────────────────────────────────────

def generate_html(
    results: List[Dict],
    corr: np.ndarray,
    portfolios: List[Dict],
    optimal: Dict,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tickers = [r["ticker"] for r in results]

    # Underlying comparison table
    und_rows = ""
    for r in sorted(results, key=lambda x: -x["sharpe"]):
        pnl_cls = "good" if r["total_pnl"] > 0 else "bad"
        corr_cls = "good" if r["spy_correlation"] < 0.5 else ""
        und_rows += (
            f'<tr><td style="text-align:left"><b>{r["ticker"]}</b> — {r["name"]}</td>'
            f'<td>{r["asset_class"]}</td>'
            f'<td>{r["n_trades"]}</td>'
            f'<td>{r["win_rate"]:.0%}</td>'
            f'<td class="{pnl_cls}">${r["total_pnl"]:+,.0f}</td>'
            f'<td>{r["sharpe"]:.2f}</td>'
            f'<td>{r["max_drawdown"]:.1%}</td>'
            f'<td class="{corr_cls}">{r["spy_correlation"]:.2f}</td>'
            f'<td>${r["capacity_est"]/1e6:,.0f}M</td></tr>\n'
        )

    # Microstructure table
    micro_rows = ""
    for r in results:
        p = UNDERLYINGS[r["ticker"]]
        micro_rows += (
            f'<tr><td style="text-align:left">{r["ticker"]}</td>'
            f'<td>${p["price"]:.0f}</td>'
            f'<td>${p["bid_ask"]:.2f}</td>'
            f'<td>{p["daily_volume"]:,.0f}</td>'
            f'<td>{p["open_interest"]:,.0f}</td>'
            f'<td>${p["slippage_per_contract"]:.2f}</td>'
            f'<td>{r["slippage_pct_of_pnl"]:.1f}%</td></tr>\n'
        )

    # Correlation matrix
    n = len(tickers)
    corr_header = "".join(f"<th>{t}</th>" for t in tickers)
    corr_rows = ""
    for i in range(n):
        cells = ""
        for j in range(n):
            v = corr[i, j]
            bg = f"background:rgba(220,38,38,{min(abs(v)*0.5, 0.4):.2f})" if abs(v) > 0.5 and i != j else ""
            if i == j:
                bg = "background:#f1f5f9"
            cells += f'<td style="{bg}">{v:.2f}</td>'
        corr_rows += f'<tr><td style="text-align:left"><b>{tickers[i]}</b></td>{cells}</tr>\n'

    # Portfolio comparison
    port_rows = ""
    for p in sorted(portfolios, key=lambda x: -x["portfolio_sharpe"]):
        is_opt = p["method"] == optimal["method"]
        cls = ' style="background:#f0fdf4;font-weight:600"' if is_opt else ""
        w_str = " / ".join(f'{p["weights"][t]:.0%}' for t in tickers)
        port_rows += (
            f'<tr{cls}><td style="text-align:left">{p["method"]}</td>'
            f'<td>{w_str}</td>'
            f'<td>{p["portfolio_return"]:+.1%}</td>'
            f'<td>{p["portfolio_sharpe"]:.2f}</td>'
            f'<td>{p["portfolio_dd"]:.1%}</td>'
            f'<td>{p["diversification_ratio"]:.2f}</td>'
            f'<td>${p["portfolio_capacity"]/1e6:,.0f}M</td></tr>\n'
        )

    # Optimal weights detail
    opt_weight_rows = ""
    for t in sorted(optimal["weights"], key=lambda x: -optimal["weights"][x]):
        w = optimal["weights"][t]
        r = next(x for x in results if x["ticker"] == t)
        opt_weight_rows += (
            f'<tr><td style="text-align:left">{t}</td>'
            f'<td>{w:.0%}</td>'
            f'<td>${r["capacity_est"]*w/1e6:,.0f}M</td>'
            f'<td>{r["spy_correlation"]:.2f}</td>'
            f'<td>{r["sharpe"]:.2f}</td></tr>\n'
        )

    total_cap = optimal["portfolio_capacity"]
    cap_cls = "good" if total_cap > 500e6 else "bad"

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>EXP-870-max: Multi-Underlying Expansion</title>
<style>
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; margin:0; padding:2em 3em; background:#f8fafc; color:#1e293b; }}
  h1 {{ color:#0f172a; border-bottom:2px solid #e2e8f0; padding-bottom:0.4em; }} h2 {{ color:#334155; margin-top:2em; }}
  .meta {{ color:#64748b; font-size:0.9em; margin-bottom:1.5em; }}
  .good {{ color:#16a34a; font-weight:600; }} .bad {{ color:#dc2626; font-weight:600; }}
  .kpi-row {{ display:flex; gap:1.2em; flex-wrap:wrap; margin:1.5em 0; }}
  .kpi {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:1em 1.5em; min-width:130px; flex:1; text-align:center; }}
  .kpi .value {{ font-size:1.5em; font-weight:700; }} .kpi .label {{ font-size:0.75em; color:#64748b; margin-top:0.2em; }}
  table {{ border-collapse:collapse; width:100%; margin:1em 0; font-size:0.85em; }}
  th {{ background:#f1f5f9; padding:8px 10px; text-align:left; border-bottom:2px solid #cbd5e1; font-weight:600; }}
  td {{ padding:6px 10px; border-bottom:1px solid #e2e8f0; text-align:right; }}
  footer {{ margin-top:3em; padding-top:1em; border-top:1px solid #e2e8f0; font-size:0.8em; color:#94a3b8; }}
</style></head><body>
<h1>EXP-870-max: Multi-Underlying Expansion</h1>
<div class="meta">6 underlyings &middot; {sum(r['n_trades'] for r in results)} total trades &middot; 4 portfolio methods &middot; Generated {now}</div>

<div class="kpi-row">
  <div class="kpi"><div class="value good">{optimal['method']}</div><div class="label">Optimal Portfolio</div></div>
  <div class="kpi"><div class="value">{optimal['portfolio_sharpe']:.2f}</div><div class="label">Portfolio Sharpe</div></div>
  <div class="kpi"><div class="value">{optimal['portfolio_dd']:.1%}</div><div class="label">Portfolio DD</div></div>
  <div class="kpi"><div class="value">{optimal['diversification_ratio']:.2f}x</div><div class="label">Diversification</div></div>
  <div class="kpi"><div class="value {cap_cls}">${total_cap/1e6:,.0f}M</div><div class="label">Total Capacity</div></div>
</div>

<h2>1. Per-Underlying Performance</h2>
<table>
<thead><tr><th>Underlying</th><th>Asset Class</th><th>Trades</th><th>WR</th><th>Total P&L</th><th>Sharpe</th><th>Max DD</th><th>SPY Corr</th><th>Capacity</th></tr></thead>
<tbody>{und_rows}</tbody>
</table>

<h2>2. Market Microstructure</h2>
<table>
<thead><tr><th>Ticker</th><th>Price</th><th>Bid-Ask</th><th>Daily Volume</th><th>Open Interest</th><th>Slippage/ct</th><th>Slip % of P&L</th></tr></thead>
<tbody>{micro_rows}</tbody>
</table>

<h2>3. Cross-Underlying Correlation Matrix</h2>
<table>
<thead><tr><th></th>{corr_header}</tr></thead>
<tbody>{corr_rows}</tbody>
</table>

<h2>4. Portfolio Optimisation</h2>
<table>
<thead><tr><th>Method</th><th>Weights ({' / '.join(tickers)})</th><th>Return</th><th>Sharpe</th><th>DD</th><th>Div Ratio</th><th>Capacity</th></tr></thead>
<tbody>{port_rows}</tbody>
</table>

<h2>5. Optimal Portfolio Allocation</h2>
<table>
<thead><tr><th>Underlying</th><th>Weight</th><th>Allocated Capacity</th><th>SPY Corr</th><th>Sharpe</th></tr></thead>
<tbody>{opt_weight_rows}</tbody>
</table>

<footer>Generated by <code>EXP-870-max/backtest.py</code></footer>
</body></html>"""
    return html


# ── Main ────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("EXP-870-max: Multi-Underlying Expansion")
    print("=" * 60)

    # Load SPY base trades
    print("\n[1/5] Loading base trades...")
    df = pd.read_csv(DATA_PATH, parse_dates=["entry_date", "exit_date"])
    df = df.sort_values("entry_date").reset_index(drop=True)
    for col in df.select_dtypes(include=[np.number]).columns:
        df[col] = df[col].fillna(df[col].median())
    print(f"  {len(df)} base trades (SPY)")

    # Backtest each underlying
    print("\n[2/5] Backtesting each underlying...")
    results = []
    for ticker, profile in UNDERLYINGS.items():
        r = backtest_underlying(df, ticker, profile)
        results.append(r)
        print(f"  {ticker:5s} ({profile['asset_class']:20s}): "
              f"WR={r['win_rate']:.0%}  Sharpe={r['sharpe']:+6.2f}  "
              f"P&L=${r['total_pnl']:+10,.0f}  DD={r['max_drawdown']:7.1%}  "
              f"SPY_corr={r['spy_correlation']:+.2f}  Cap=${r['capacity_est']/1e6:,.0f}M")

    # Correlation matrix
    print("\n[3/5] Computing cross-correlations...")
    corr, tickers = build_correlation_matrix(results)
    print("  Correlation matrix:")
    for i, t in enumerate(tickers):
        row = "  " + f"{t:5s}: " + " ".join(f"{corr[i,j]:+.2f}" for j in range(len(tickers)))
        print(row)

    # Low-correlation pairs
    print("\n  Uncorrelated pairs (|corr| < 0.3):")
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            if abs(corr[i, j]) < 0.3:
                print(f"    {tickers[i]} / {tickers[j]}: {corr[i,j]:+.3f}")

    # Portfolio optimisation
    print("\n[4/5] Optimising portfolio...")
    methods = ["equal", "risk_parity", "max_sharpe", "capacity_weighted"]
    portfolios = []
    for method in methods:
        p = optimize_portfolio(results, corr, method)
        portfolios.append(p)
        print(f"  {method:20s}: Sharpe={p['portfolio_sharpe']:.2f}  "
              f"DD={p['portfolio_dd']:.1%}  Div={p['diversification_ratio']:.2f}x  "
              f"Cap=${p['portfolio_capacity']/1e6:,.0f}M")

    optimal = max(portfolios, key=lambda p: p["portfolio_sharpe"])
    print(f"\n  >> Optimal: {optimal['method']}")
    print(f"     Sharpe={optimal['portfolio_sharpe']:.2f}  "
          f"Capacity=${optimal['portfolio_capacity']/1e6:,.0f}M")
    print(f"     Weights: {', '.join(f'{t}={w:.0%}' for t,w in optimal['weights'].items())}")

    # Save results
    print("\n[5/5] Generating outputs...")
    RESULTS_DIR.mkdir(exist_ok=True)

    # Strip daily_returns from JSON (too large)
    results_clean = [{k: v for k, v in r.items() if k != "daily_returns"} for r in results]

    summary = {
        "experiment": "EXP-870-max",
        "description": "Multi-Underlying Expansion",
        "generated": datetime.now().isoformat(),
        "underlyings": results_clean,
        "correlation_matrix": corr.tolist(),
        "tickers": tickers,
        "portfolios": portfolios,
        "optimal": optimal,
    }
    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print("  Wrote results/summary.json")

    html = generate_html(results, corr, portfolios, optimal)
    (RESULTS_DIR / "report.html").write_text(html)
    print("  Wrote results/report.html")

    print("\nDone.")
    return summary


if __name__ == "__main__":
    main()
