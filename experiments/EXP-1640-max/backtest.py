#!/usr/bin/env python3
"""
EXP-1640: Sector Momentum with Options Overlay — REAL IronVault data only.

Ranks XLF, XLI, XLK, XLE by 20-day trailing return.
Sells OTM put credit spreads on the momentum winner, skips the worst.
Rebalances bi-weekly.

Output: reports/exp1640_sector_momentum.html + results/summary.json
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backtest.backtester import _yf_download_safe
from shared.constants import DATA_DIR

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
ACCOUNT = 100_000
TICKERS = ["XLF", "XLI", "XLK", "XLE"]
REPORT_PATH = ROOT / "reports" / "exp1640_sector_momentum.html"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ═══════════════════════════════════════════════════════════════════════════
# Data Loading — REAL only
# ═══════════════════════════════════════════════════════════════════════════


def _fetch(ticker: str, start: str, end: str) -> pd.DataFrame:
    df = _yf_download_safe(ticker, start, end)
    if df.empty:
        raise RuntimeError(f"No data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def load_prices(tickers: List[str], start="2019-06-01", end="2025-12-31") -> pd.DataFrame:
    """Load daily close prices for all sector ETFs + SPY."""
    frames = {}
    for t in tickers + ["SPY"]:
        df = _fetch(t, start, end)
        frames[t] = df["Close"].dropna()
    prices = pd.DataFrame(frames).dropna()
    return prices


def get_real_expirations(ticker: str, start_year: int, end_year: int) -> List[str]:
    """Query actual expirations from options_cache.db for a sector ETF."""
    db_path = os.path.join(DATA_DIR, "options_cache.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT expiration, COUNT(*) as n_strikes
        FROM option_contracts
        WHERE ticker=? AND option_type='P'
          AND expiration >= ? AND expiration <= ?
        GROUP BY expiration
        HAVING n_strikes >= 10
        ORDER BY expiration
    """, (ticker, f"{start_year}-01-01", f"{end_year}-12-31"))
    exps = [r[0] for r in cur.fetchall()]
    conn.close()
    return exps


def get_available_strikes(ticker: str, expiration: str, as_of_date: str) -> List[float]:
    """Get available put strikes from IronVault DB."""
    db_path = os.path.join(DATA_DIR, "options_cache.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT strike FROM option_contracts
        WHERE ticker=? AND expiration=? AND option_type='P'
        ORDER BY strike
    """, (ticker, expiration))
    strikes = [r[0] for r in cur.fetchall()]
    conn.close()
    return strikes


def get_spread_price(ticker: str, expiration: str, short_strike: float,
                     long_strike: float, date: str) -> Optional[float]:
    """Get real spread credit from IronVault option_daily table.

    Returns net credit (short_price - long_price) or None on cache miss.
    NO synthetic fallback.
    """
    db_path = os.path.join(DATA_DIR, "options_cache.db")
    conn = sqlite3.connect(db_path)

    exp_dt = datetime.strptime(expiration, "%Y-%m-%d")

    def _occ(strike):
        exp_str = exp_dt.strftime("%y%m%d")
        strike_int = int(round(strike * 1000))
        return f"O:{ticker}{exp_str}P{strike_int:08d}"

    short_sym = _occ(short_strike)
    long_sym = _occ(long_strike)

    cur = conn.cursor()
    cur.execute("SELECT close FROM option_daily WHERE contract_symbol=? AND date=?",
                (short_sym, date))
    short_row = cur.fetchone()

    cur.execute("SELECT close FROM option_daily WHERE contract_symbol=? AND date=?",
                (long_sym, date))
    long_row = cur.fetchone()

    conn.close()

    if short_row is None or long_row is None:
        return None
    if short_row[0] is None or long_row[0] is None:
        return None

    credit = short_row[0] - long_row[0]
    return credit if credit > 0 else None


# ═══════════════════════════════════════════════════════════════════════════
# Momentum Ranking
# ═══════════════════════════════════════════════════════════════════════════


def compute_momentum(prices: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Compute trailing returns for ranking."""
    return prices.pct_change(window)


def rank_sectors(momentum: pd.DataFrame, date, tickers: List[str]) -> List[str]:
    """Rank sectors by momentum. Returns tickers sorted best → worst."""
    if date not in momentum.index:
        return tickers
    row = momentum.loc[date, tickers].dropna()
    if row.empty:
        return tickers
    return row.sort_values(ascending=False).index.tolist()


# ═══════════════════════════════════════════════════════════════════════════
# Trade Execution
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    ticker: str
    short_strike: float
    long_strike: float
    credit: float
    pnl: float  # per-contract P&L in option price terms
    win: bool
    expiration: str
    momentum_rank: int  # 1 = best
    contracts: int = 1  # number of contracts


def find_trade(ticker: str, entry_date: str, prices: pd.DataFrame,
               target_dte: int = 35, otm_pct: float = 0.05,
               spread_width: float = 2.0,
               risk_pct: float = 0.03) -> Optional[Trade]:
    """Find and price a put credit spread on a sector ETF using real IronVault data.

    Returns None if no valid trade found (cache miss, no strikes, etc.).
    """
    entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")

    # Get current price for OTM calculation
    if pd.Timestamp(entry_date) not in prices.index:
        return None
    current_price = float(prices.loc[pd.Timestamp(entry_date), ticker])
    if current_price <= 0 or np.isnan(current_price):
        return None

    # Find nearest expiration ~target_dte out
    target_exp = entry_dt + timedelta(days=target_dte)
    exps = get_real_expirations(ticker, entry_dt.year, entry_dt.year + 1)
    if not exps:
        return None

    # Find closest expiration to target
    best_exp = None
    best_diff = 999
    for exp_str in exps:
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d")
        diff = (exp_dt - entry_dt).days
        if 20 <= diff <= 50:  # DTE range
            if abs(diff - target_dte) < best_diff:
                best_diff = abs(diff - target_dte)
                best_exp = exp_str

    if best_exp is None:
        return None

    # Get available strikes
    strikes = get_available_strikes(ticker, best_exp, entry_date)
    if len(strikes) < 5:
        return None

    # Find OTM put strike (~5% below current price)
    target_strike = current_price * (1 - otm_pct)
    short_strike = min(strikes, key=lambda s: abs(s - target_strike))

    # Long strike = short_strike - spread_width
    long_candidates = [s for s in strikes if s < short_strike and
                       abs((short_strike - s) - spread_width) < 1.5]
    if not long_candidates:
        # Try wider search
        long_candidates = [s for s in strikes if s < short_strike - 0.5]
        if not long_candidates:
            return None

    long_strike = max(long_candidates, key=lambda s: s)  # Closest below
    if long_strike >= short_strike:
        return None

    # Price the spread at entry
    entry_credit = get_spread_price(ticker, best_exp, short_strike, long_strike, entry_date)
    if entry_credit is None or entry_credit <= 0:
        return None

    # Price at expiration (or closest date)
    exp_dt = datetime.strptime(best_exp, "%Y-%m-%d")

    # Try expiration date, then 1-2 days before
    exit_debit = None
    exit_date = best_exp
    for offset in range(0, 4):
        check_dt = exp_dt - timedelta(days=offset)
        check_str = check_dt.strftime("%Y-%m-%d")
        exit_debit = get_spread_price(ticker, best_exp, short_strike, long_strike, check_str)
        if exit_debit is not None:
            exit_date = check_str
            break

    if exit_debit is not None:
        pnl = entry_credit - exit_debit
    else:
        # Check if short strike expired worthless (price > short strike)
        if pd.Timestamp(best_exp) in prices.index:
            final_price = float(prices.loc[pd.Timestamp(best_exp), ticker])
        else:
            # Use last available price before expiration
            prior = prices.loc[:pd.Timestamp(best_exp), ticker].dropna()
            if prior.empty:
                return None
            final_price = float(prior.iloc[-1])

        if final_price > short_strike:
            pnl = entry_credit  # Full credit kept
        else:
            pnl = entry_credit - (short_strike - long_strike)  # Max loss
        exit_date = best_exp

    # Position sizing: risk_pct of account per trade
    # Max loss per contract = (spread_width - credit) * 100
    width = short_strike - long_strike
    max_loss_per = (width - entry_credit) * 100
    if max_loss_per <= 0:
        max_loss_per = width * 100  # safety
    risk_budget = ACCOUNT * risk_pct
    contracts = max(1, int(risk_budget / max_loss_per))
    contracts = min(contracts, 50)  # cap

    return Trade(
        entry_date=entry_date,
        exit_date=exit_date,
        ticker=ticker,
        short_strike=short_strike,
        long_strike=long_strike,
        credit=round(entry_credit, 4),
        pnl=round(pnl, 4),
        win=pnl > 0,
        expiration=best_exp,
        momentum_rank=0,  # Set by caller
        contracts=contracts,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Backtest Engine
# ═══════════════════════════════════════════════════════════════════════════


def run_backtest(prices: pd.DataFrame, start_year: int = 2020,
                 end_year: int = 2025, vix_filter: float = 30.0) -> List[Trade]:
    """Run sector momentum backtest with bi-weekly rebalancing."""
    vix_df = _fetch("^VIX", f"{start_year-1}-01-01", f"{end_year}-12-31")
    vix = vix_df["Close"].dropna()

    momentum = compute_momentum(prices, window=20)

    # Generate bi-weekly rebalance dates (every 10 trading days)
    all_dates = prices.loc[f"{start_year}-01-01":f"{end_year}-12-31"].index
    rebalance_dates = all_dates[::10]  # Every 10 trading days ≈ bi-weekly

    trades = []
    active_expiration = None  # Track to avoid overlapping trades

    for date in rebalance_dates:
        date_str = date.strftime("%Y-%m-%d")

        # VIX filter: skip if VIX > threshold
        if date in vix.index and float(vix.loc[date]) > vix_filter:
            continue

        # Skip if we have an active trade not yet expired
        if active_expiration is not None:
            if date < pd.Timestamp(active_expiration):
                continue

        # Rank sectors
        ranked = rank_sectors(momentum, date, TICKERS)
        if len(ranked) < 2:
            continue

        winner = ranked[0]   # Best momentum
        # Skip the worst-ranked (ranked[-1])

        # Try to trade the winner
        trade = find_trade(winner, date_str, prices)
        if trade is not None:
            trade.momentum_rank = 1
            trades.append(trade)
            active_expiration = trade.expiration
            continue

        # If winner has no data, try second-ranked
        if len(ranked) >= 3:
            runner_up = ranked[1]
            trade = find_trade(runner_up, date_str, prices)
            if trade is not None:
                trade.momentum_rank = 2
                trades.append(trade)
                active_expiration = trade.expiration

    return trades


# ═══════════════════════════════════════════════════════════════════════════
# Analytics
# ═══════════════════════════════════════════════════════════════════════════


def trades_to_daily_returns(trades: List[Trade], start_year: int = 2020,
                            end_year: int = 2025) -> pd.Series:
    """Convert trade list to daily return series."""
    idx = pd.bdate_range(f"{start_year}-01-02", f"{end_year}-12-31")
    daily_pnl = pd.Series(0.0, index=idx)

    for t in trades:
        exit_dt = pd.Timestamp(t.exit_date)
        if exit_dt in daily_pnl.index:
            # PnL in dollars: per-contract × 100 multiplier × contracts
            daily_pnl.loc[exit_dt] += t.pnl * 100 * t.contracts

    # Convert to returns
    equity = ACCOUNT
    returns = pd.Series(0.0, index=idx)
    for i, (dt, pnl) in enumerate(daily_pnl.items()):
        if equity > 0:
            returns.iloc[i] = pnl / equity
        equity += pnl

    return returns


def compute_metrics(rets: np.ndarray) -> dict:
    if len(rets) < 2:
        return {"cagr_pct": 0, "sharpe": 0, "max_dd_pct": 0, "calmar": 0,
                "sortino": 0, "vol_pct": 0, "total_ret_pct": 0, "n_days": 0}
    eq = np.cumprod(1 + rets)
    total = float(eq[-1] - 1)
    n_yr = len(rets) / TRADING_DAYS
    cagr = (eq[-1]) ** (1 / max(n_yr, 0.01)) - 1 if eq[-1] > 0 else 0
    mu, std = float(rets.mean()), float(rets.std())
    sharpe = mu / std * math.sqrt(TRADING_DAYS) if std > 1e-12 else 0
    hwm = np.maximum.accumulate(eq)
    dd = float((1 - eq / hwm).max())
    calmar = cagr / dd if dd > 1e-6 else 0
    down = rets[rets < 0]
    down_std = float(down.std()) if len(down) > 1 else std
    sortino = mu / down_std * math.sqrt(TRADING_DAYS) if down_std > 1e-12 else 0
    return {
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(dd * 100, 2),
        "calmar": round(calmar, 2),
        "sortino": round(sortino, 2),
        "vol_pct": round(std * math.sqrt(TRADING_DAYS) * 100, 2),
        "total_ret_pct": round(total * 100, 2),
        "n_days": len(rets),
    }


def yearly_breakdown(trades: List[Trade]) -> Dict[int, dict]:
    """Year-by-year trade statistics."""
    by_year: Dict[int, List[Trade]] = {}
    for t in trades:
        yr = int(t.entry_date[:4])
        by_year.setdefault(yr, []).append(t)

    results = {}
    for yr, yr_trades in sorted(by_year.items()):
        wins = sum(1 for t in yr_trades if t.win)
        total_pnl = sum(t.pnl * 100 * t.contracts for t in yr_trades)
        tickers_used = set(t.ticker for t in yr_trades)
        results[yr] = {
            "n_trades": len(yr_trades),
            "wins": wins,
            "win_rate": round(wins / len(yr_trades) * 100, 1) if yr_trades else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / len(yr_trades), 2) if yr_trades else 0,
            "tickers": sorted(tickers_used),
            "return_pct": round(total_pnl / ACCOUNT * 100, 2),
        }
    return results


def spy_correlation(daily_rets: pd.Series, prices: pd.DataFrame) -> float:
    """Compute correlation with SPY buy-and-hold returns."""
    spy_rets = prices["SPY"].pct_change().dropna()
    common = daily_rets.index.intersection(spy_rets.index)
    if len(common) < 30:
        return 0.0
    a = daily_rets.reindex(common).fillna(0).values
    b = spy_rets.reindex(common).fillna(0).values
    if a.std() < 1e-12 or b.std() < 1e-12:
        return 0.0
    return round(float(np.corrcoef(a, b)[0, 1]), 3)


def walk_forward(trades: List[Trade], daily_rets: pd.Series) -> dict:
    """Train 2020-2023, test 2024-2025."""
    train_trades = [t for t in trades if int(t.entry_date[:4]) <= 2023]
    test_trades = [t for t in trades if int(t.entry_date[:4]) >= 2024]

    train_mask = daily_rets.index < "2024-01-01"
    test_mask = daily_rets.index >= "2024-01-01"

    return {
        "train": {
            "n_trades": len(train_trades),
            "win_rate": round(sum(t.win for t in train_trades) / max(len(train_trades), 1) * 100, 1),
            "metrics": compute_metrics(daily_rets[train_mask].values),
        },
        "test": {
            "n_trades": len(test_trades),
            "win_rate": round(sum(t.win for t in test_trades) / max(len(test_trades), 1) * 100, 1),
            "metrics": compute_metrics(daily_rets[test_mask].values),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# HTML Report
# ═══════════════════════════════════════════════════════════════════════════


def generate_report(
    trades: List[Trade],
    overall: dict,
    yearly: dict,
    wf: dict,
    spy_corr: float,
    ticker_stats: dict,
) -> str:
    n_trades = len(trades)
    wins = sum(1 for t in trades if t.win)
    wr = round(wins / max(n_trades, 1) * 100, 1)
    total_pnl = sum(t.pnl * 100 * t.contracts for t in trades)

    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EXP-1640: Sector Momentum with Options Overlay</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Inter',-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;padding:2rem;line-height:1.6;max-width:1100px;margin:0 auto}}
  h1{{font-size:1.8rem;margin-bottom:.5rem;color:#f8fafc}}
  h2{{font-size:1.3rem;margin:2rem 0 1rem;color:#93c5fd;border-bottom:1px solid #334155;padding-bottom:.5rem}}
  h3{{font-size:1.05rem;margin:1.2rem 0 .6rem;color:#cbd5e1}}
  .sub{{color:#94a3b8;font-size:.95rem;margin-bottom:2rem}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:1rem;margin:1rem 0}}
  .card{{background:#1e293b;border-radius:8px;padding:1rem;border:1px solid #334155}}
  .card .label{{font-size:.72rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}}
  .card .value{{font-size:1.4rem;font-weight:700;margin-top:.25rem}}
  .green{{color:#4ade80}}.red{{color:#f87171}}.yellow{{color:#fbbf24}}.blue{{color:#60a5fa}}.orange{{color:#fb923c}}.white{{color:#f8fafc}}
  table{{width:100%;border-collapse:collapse;margin:1rem 0;font-size:.85rem}}
  th{{background:#1e293b;padding:.5rem .75rem;text-align:left;color:#94a3b8;font-weight:600;border-bottom:2px solid #334155}}
  td{{padding:.5rem .75rem;border-bottom:1px solid #1e293b}}
  tr:hover td{{background:#1e293b}}
  .verdict{{padding:.75rem 1rem;border-radius:6px;margin:1rem 0;font-size:.9rem}}
  .verdict-pass{{background:#052e16;border:1px solid #16a34a;color:#4ade80}}
  .verdict-warn{{background:#422006;border:1px solid #d97706;color:#fbbf24}}
  .verdict-fail{{background:#450a0a;border:1px solid #dc2626;color:#f87171}}
  .note{{font-size:.8rem;color:#64748b;margin-top:.5rem}}
  .footer{{margin-top:3rem;font-size:.75rem;color:#475569;text-align:center;border-top:1px solid #1e293b;padding-top:1rem}}
</style></head><body>

<h1>EXP-1640: Sector Momentum with Options Overlay</h1>
<div class="sub">
  Rank XLF/XLI/XLK/XLE by 20-day return, sell OTM put spreads on winner — real IronVault data<br>
  Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} &nbsp;|&nbsp; 2020–2025
</div>

<h2>Hypothesis</h2>
<p>Sectors with strong trailing momentum are less likely to breach OTM put strikes.
By selling put credit spreads only on the top-ranked sector ETF and avoiding the
worst-ranked, we exploit momentum persistence while limiting exposure to
mean-reversion risk in weak sectors. Bi-weekly rebalancing captures regime shifts.</p>
""")

    # ── Summary Cards ──
    parts.append(f"""<h2>Summary</h2>
<div class="cards">
  <div class="card"><div class="label">Total Trades</div><div class="value white">{n_trades}</div></div>
  <div class="card"><div class="label">Win Rate</div><div class="value {'green' if wr>60 else 'yellow'}">{wr}%</div></div>
  <div class="card"><div class="label">Total P&L</div><div class="value {'green' if total_pnl>0 else 'red'}">${total_pnl:,.0f}</div></div>
  <div class="card"><div class="label">CAGR</div><div class="value {'green' if overall['cagr_pct']>0 else 'red'}">{overall['cagr_pct']:+.1f}%</div></div>
  <div class="card"><div class="label">Sharpe</div><div class="value {'green' if overall['sharpe']>1 else 'yellow'}">{overall['sharpe']:.2f}</div></div>
  <div class="card"><div class="label">Max DD</div><div class="value yellow">{overall['max_dd_pct']:.1f}%</div></div>
  <div class="card"><div class="label">SPY Correlation</div><div class="value {'green' if abs(spy_corr)<0.3 else 'yellow'}">{spy_corr:.3f}</div></div>
  <div class="card"><div class="label">Calmar</div><div class="value blue">{overall['calmar']:.2f}</div></div>
</div>""")

    # ── Year-by-Year ──
    parts.append("""<h2>Year-by-Year Breakdown</h2>
<table><thead><tr>
<th>Year</th><th>Trades</th><th>Wins</th><th>Win Rate</th><th>P&L</th>
<th>Return</th><th>Sectors Used</th></tr></thead><tbody>""")
    for yr, d in sorted(yearly.items()):
        pnl_cls = "green" if d["total_pnl"] > 0 else "red"
        parts.append(f"""<tr><td><strong>{yr}</strong></td>
        <td>{d['n_trades']}</td><td>{d['wins']}</td><td>{d['win_rate']}%</td>
        <td class="{pnl_cls}">${d['total_pnl']:,.0f}</td>
        <td class="{pnl_cls}">{d['return_pct']:+.1f}%</td>
        <td>{', '.join(d['tickers'])}</td></tr>""")
    parts.append("</tbody></table>")

    # ── Ticker Distribution ──
    parts.append("""<h2>Ticker Distribution</h2>
<table><thead><tr>
<th>Ticker</th><th>Trades</th><th>Wins</th><th>Win Rate</th><th>Avg P&L</th>
<th>Total P&L</th></tr></thead><tbody>""")
    for ticker, d in sorted(ticker_stats.items()):
        pnl_cls = "green" if d["total_pnl"] > 0 else "red"
        parts.append(f"""<tr><td><strong>{ticker}</strong></td>
        <td>{d['n_trades']}</td><td>{d['wins']}</td><td>{d['win_rate']}%</td>
        <td class="{pnl_cls}">${d['avg_pnl']:.0f}</td>
        <td class="{pnl_cls}">${d['total_pnl']:,.0f}</td></tr>""")
    parts.append("</tbody></table>")

    # ── Walk-Forward ──
    parts.append("<h2>Walk-Forward Validation (Train: 2020–2023, Test: 2024–2025)</h2>")
    t_m = wf["train"]["metrics"]
    s_m = wf["test"]["metrics"]
    wf_ratio = s_m["sharpe"] / t_m["sharpe"] if abs(t_m["sharpe"]) > 0.01 else 0

    parts.append(f"""<table><thead><tr>
    <th>Period</th><th>Trades</th><th>Win Rate</th><th>Sharpe</th>
    <th>CAGR</th><th>Max DD</th></tr></thead><tbody>
    <tr><td><strong>Train 2020–2023</strong></td>
    <td>{wf['train']['n_trades']}</td><td>{wf['train']['win_rate']}%</td>
    <td>{t_m['sharpe']:.2f}</td><td>{t_m['cagr_pct']:+.1f}%</td>
    <td>{t_m['max_dd_pct']:.1f}%</td></tr>
    <tr><td><strong>Test 2024–2025</strong></td>
    <td>{wf['test']['n_trades']}</td><td>{wf['test']['win_rate']}%</td>
    <td>{s_m['sharpe']:.2f}</td><td>{s_m['cagr_pct']:+.1f}%</td>
    <td>{s_m['max_dd_pct']:.1f}%</td></tr>
    </tbody></table>""")

    parts.append(f'<p class="note">Walk-forward Sharpe ratio (test/train): <strong>{wf_ratio:.2f}</strong></p>')
    if wf_ratio > 0.5:
        parts.append(f'<div class="verdict verdict-pass">Walk-forward PASS: OOS Sharpe '
                     f'{s_m["sharpe"]:.2f} maintains {wf_ratio:.0%} of in-sample</div>')
    elif wf_ratio > 0:
        parts.append(f'<div class="verdict verdict-warn">Walk-forward CAUTION: OOS Sharpe '
                     f'{s_m["sharpe"]:.2f} is {wf_ratio:.0%} of in-sample</div>')
    else:
        parts.append(f'<div class="verdict verdict-fail">Walk-forward FAIL: negative OOS Sharpe</div>')

    # ── Trade Log (sample) ──
    parts.append("<h2>Recent Trades (Last 15)</h2>")
    parts.append("""<table><thead><tr>
    <th>Entry</th><th>Exit</th><th>Ticker</th><th>Rank</th>
    <th>Short/Long</th><th>Credit</th><th>P&L</th><th>Win</th></tr></thead><tbody>""")
    for t in trades[-15:]:
        pnl_cls = "green" if t.win else "red"
        pnl_dollars = t.pnl * 100 * t.contracts
        parts.append(f"""<tr><td>{t.entry_date}</td><td>{t.exit_date}</td>
        <td>{t.ticker}</td><td>#{t.momentum_rank}</td>
        <td>${t.short_strike:.0f}/${t.long_strike:.0f}</td>
        <td>${t.credit*100:.0f}</td>
        <td class="{pnl_cls}">${pnl_dollars:+.0f}</td>
        <td class="{pnl_cls}">{'W' if t.win else 'L'}</td></tr>""")
    parts.append("</tbody></table>")

    # ── Verdict ──
    parts.append("<h2>Overall Assessment</h2>")
    checks = [
        ("Trades ≥ 30 (statistical significance)", n_trades >= 30),
        ("Win rate > 50%", wr > 50),
        ("Positive Sharpe", overall["sharpe"] > 0),
        ("Max DD < 10%", overall["max_dd_pct"] < 10),
        ("SPY correlation < 0.3", abs(spy_corr) < 0.3),
        ("OOS trades ≥ 10", wf["test"]["n_trades"] >= 10),
    ]
    for label, ok in checks:
        cls = "verdict-pass" if ok else "verdict-warn"
        parts.append(f'<div class="verdict {cls}">{"PASS" if ok else "MISS"}: {label}</div>')

    parts.append(f"""
<div class="footer">
  EXP-1640: Sector Momentum with Options Overlay — PilotAI Credit Spreads<br>
  All prices from IronVault options_cache.db + Yahoo Finance. Zero synthetic data.<br>
  Bi-weekly rebalance, VIX&lt;30 filter, 5% OTM, 30-35 DTE put credit spreads.
</div></body></html>""")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=" * 70)
    print("EXP-1640: SECTOR MOMENTUM WITH OPTIONS OVERLAY")
    print("=" * 70)

    print("\n[1/6] Loading sector ETF prices...")
    prices = load_prices(TICKERS)
    print(f"  {len(prices)} days ({prices.index[0].date()} to {prices.index[-1].date()})")
    for t in TICKERS:
        exps = get_real_expirations(t, 2020, 2025)
        print(f"  {t}: {len(exps)} expirations in IronVault")

    print("\n[2/6] Running backtest (2020-2025)...")
    trades = run_backtest(prices, start_year=2020, end_year=2025)
    print(f"  {len(trades)} trades executed")
    if trades:
        wins = sum(1 for t in trades if t.win)
        total_pnl = sum(t.pnl * 100 * t.contracts for t in trades)
        print(f"  Win rate: {wins}/{len(trades)} ({wins/len(trades)*100:.1f}%)")
        print(f"  Total P&L: ${total_pnl:,.0f}")

    print("\n[3/6] Computing metrics...")
    daily_rets = trades_to_daily_returns(trades)
    overall = compute_metrics(daily_rets.values)
    print(f"  CAGR: {overall['cagr_pct']:+.1f}%, Sharpe: {overall['sharpe']:.2f}, "
          f"DD: {overall['max_dd_pct']:.1f}%")

    print("\n[4/6] Year-by-year breakdown...")
    yearly = yearly_breakdown(trades)
    for yr, d in sorted(yearly.items()):
        print(f"  {yr}: {d['n_trades']} trades, WR {d['win_rate']}%, "
              f"P&L ${d['total_pnl']:+,.0f}, sectors: {d['tickers']}")

    print("\n[5/6] Walk-forward & correlation...")
    wf = walk_forward(trades, daily_rets)
    spy_corr = spy_correlation(daily_rets, prices)
    print(f"  Train Sharpe: {wf['train']['metrics']['sharpe']:.2f}, "
          f"Test Sharpe: {wf['test']['metrics']['sharpe']:.2f}")
    print(f"  SPY correlation: {spy_corr:.3f}")

    # Ticker-level stats
    ticker_stats = {}
    for ticker in TICKERS:
        t_trades = [t for t in trades if t.ticker == ticker]
        if t_trades:
            t_wins = sum(1 for t in t_trades if t.win)
            t_pnl = sum(t.pnl * 100 * t.contracts for t in t_trades)
            ticker_stats[ticker] = {
                "n_trades": len(t_trades),
                "wins": t_wins,
                "win_rate": round(t_wins / len(t_trades) * 100, 1),
                "total_pnl": round(t_pnl, 2),
                "avg_pnl": round(t_pnl / len(t_trades), 2),
            }

    print("\n[6/6] Generating report...")
    html = generate_report(trades, overall, yearly, wf, spy_corr, ticker_stats)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html)
    print(f"  Report: {REPORT_PATH}")

    # Save results JSON
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "experiment": "EXP-1640",
        "strategy": "Sector Momentum with Options Overlay",
        "data_source": "IronVault options_cache.db + Yahoo Finance",
        "n_trades": len(trades),
        "overall": overall,
        "yearly": {str(k): v for k, v in yearly.items()},
        "walk_forward": wf,
        "spy_correlation": spy_corr,
        "ticker_stats": ticker_stats,
        "trades": [
            {"entry": t.entry_date, "exit": t.exit_date, "ticker": t.ticker,
             "rank": t.momentum_rank, "short": t.short_strike, "long": t.long_strike,
             "credit": t.credit, "pnl": t.pnl, "win": t.win}
            for t in trades
        ],
    }
    json_path = RESULTS_DIR / "summary.json"
    json_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"  JSON: {json_path}")

    return summary


if __name__ == "__main__":
    main()
