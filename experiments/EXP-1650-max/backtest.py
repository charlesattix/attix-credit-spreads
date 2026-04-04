"""
EXP-1650: Earnings Vol Crush on Sector ETFs.

Tests whether XLF, XLK, XLE show systematic IV overstatement ahead of
sector earnings seasons. Sells strangles ~2 weeks before, buys back
after IV crush.

All option prices from IronVault. Zero synthetic data.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from shared.iron_vault import IronVault

logger = logging.getLogger(__name__)
OUTPUT_DIR = Path(__file__).resolve().parent / "results"
REPORT_PATH = ROOT / "reports" / "exp1650_earnings_vol_crush.html"
CAPITAL = 100_000
TRADING_DAYS = 252
OOS_START = 2023


# ── Earnings calendar ────────────────────────────────────────────────────
# Major sector earnings windows (approximate mid-month reporting dates)
# Banks: JPM/GS/BAC/C report mid-Jan, mid-Apr, mid-Jul, mid-Oct
# Tech: AAPL/MSFT/GOOG report late-Jan, late-Apr, late-Jul, late-Oct
# Energy: XOM/CVX report late-Jan, late-Apr, late-Jul, late-Oct

EARNINGS_CALENDAR = {
    "XLF": {
        "name": "Financials",
        "months": [1, 4, 7, 10],
        "report_day": 15,  # ~mid-month
        "entry_offset_days": 14,  # enter 2 weeks before
    },
    "XLK": {
        "name": "Technology",
        "months": [1, 4, 7, 10],
        "report_day": 25,  # ~late month
        "entry_offset_days": 14,
    },
    "XLE": {
        "name": "Energy",
        "months": [1, 4, 7, 10],
        "report_day": 28,  # ~late month
        "entry_offset_days": 14,
    },
}


# ── Market data ──────────────────────────────────────────────────────────

def _dl(ticker: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(ticker, start="2019-06-01", end="2026-01-01", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    return df


def _exp_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _next_td(dt: datetime, td_set) -> Optional[datetime]:
    for off in range(7):
        c = dt + timedelta(days=off)
        if c.strftime("%Y-%m-%d") in td_set:
            return c
    return None


# ── Strangle construction ────────────────────────────────────────────────

def _find_strangle(
    hd: IronVault,
    ticker: str,
    exp: str,
    trade_date: str,
    price: float,
    put_otm_pct: float = 0.95,
    call_otm_pct: float = 1.05,
) -> Optional[Dict]:
    """Find an OTM strangle with real prices from IronVault.

    Returns dict with put and call strike/price or None if data missing.
    """
    exp_as_dt = _exp_dt(exp)
    put_strikes = hd.get_available_strikes(ticker, exp, trade_date, "P")
    call_strikes = hd.get_available_strikes(ticker, exp, trade_date, "C")

    if not put_strikes or not call_strikes:
        return None

    # Find OTM put (~5% below)
    put_target = price * put_otm_pct
    put_k = min(put_strikes, key=lambda k: abs(k - put_target))
    put_sym = IronVault.build_occ_symbol(ticker, exp_as_dt, put_k, "P")
    put_price = hd.get_contract_price(put_sym, trade_date)

    # Find OTM call (~5% above)
    call_target = price * call_otm_pct
    call_k = min(call_strikes, key=lambda k: abs(k - call_target))
    call_sym = IronVault.build_occ_symbol(ticker, exp_as_dt, call_k, "C")
    call_price = hd.get_contract_price(call_sym, trade_date)

    if put_price is None or call_price is None:
        return None
    if put_price < 0.01 or call_price < 0.01:
        return None

    total_premium = put_price + call_price

    return {
        "put_strike": put_k,
        "call_strike": call_k,
        "put_price": put_price,
        "call_price": call_price,
        "total_premium": round(total_premium, 4),
        "put_sym": put_sym,
        "call_sym": call_sym,
    }


def _reprice_strangle(
    hd: IronVault,
    put_sym: str,
    call_sym: str,
    date: str,
) -> Optional[float]:
    """Get current strangle value from real prices."""
    pp = hd.get_contract_price(put_sym, date)
    cp = hd.get_contract_price(call_sym, date)
    if pp is None or cp is None:
        return None
    return pp + cp


# ── Core backtest ────────────────────────────────────────────────────────

@dataclass
class StrangleTrade:
    ticker: str
    entry_date: str
    exit_date: str
    expiration: str
    put_strike: float
    call_strike: float
    entry_premium: float
    exit_premium: float
    pnl: float
    pnl_pct: float
    contracts: int
    hold_days: int
    exit_reason: str
    earnings_month: int
    year: int
    underlying_entry: float
    underlying_exit: float
    realized_move_pct: float
    iv_crush_pct: float  # premium decay as % of entry


def run_earnings_vol_crush(
    hd: IronVault,
    ticker: str,
    price_df: pd.DataFrame,
    config: Dict,
    start_year: int = 2020,
    end_year: int = 2025,
    # Strangle params
    put_otm: float = 0.95,
    call_otm: float = 1.05,
    # Exit params
    profit_target_pct: float = 0.40,  # close at 40% profit (premium decay)
    stop_loss_pct: float = 1.00,      # stop at 100% loss (premium doubled)
    max_hold_days: int = 15,
    # Sizing
    max_contracts: int = 5,
    risk_pct: float = 0.015,
) -> List[StrangleTrade]:
    """Run earnings vol crush strategy for one ticker."""
    close = price_df["Close"]
    td_set = set(price_df.index.strftime("%Y-%m-%d"))
    months = config["months"]
    report_day = config["report_day"]
    entry_offset = config["entry_offset_days"]

    trades: List[StrangleTrade] = []

    for year in range(start_year, end_year + 1):
        for month in months:
            # Target entry: ~2 weeks before sector earnings
            try:
                earnings_date = datetime(year, month, report_day)
            except ValueError:
                earnings_date = datetime(year, month, 28)
            entry_target = earnings_date - timedelta(days=entry_offset)
            entry_dt = _next_td(entry_target, td_set)
            if entry_dt is None:
                continue
            entry_str = entry_dt.strftime("%Y-%m-%d")

            try:
                entry_price = float(close.loc[entry_str])
            except (KeyError, TypeError):
                continue

            # Find expiration that covers the earnings window
            # Want expiration ~10-30 days after earnings date
            exp_target = earnings_date + timedelta(days=14)
            conn = sqlite3.connect(hd._db_path)
            cur = conn.cursor()
            # Wide search window to maximize matches
            cur.execute("""SELECT DISTINCT expiration FROM option_contracts
                WHERE ticker=? AND option_type='P'
                AND expiration BETWEEN ? AND ?
                ORDER BY expiration""",
                (ticker,
                 (exp_target - timedelta(days=14)).strftime("%Y-%m-%d"),
                 (exp_target + timedelta(days=30)).strftime("%Y-%m-%d")))
            exps = [r[0] for r in cur.fetchall()]
            conn.close()

            if not exps:
                continue
            exp = min(exps, key=lambda e: abs((_exp_dt(e) - exp_target).days))
            exp_obj = _exp_dt(exp)

            if (exp_obj - entry_dt).days < 10 or (exp_obj - entry_dt).days > 60:
                continue

            # Build strangle
            strangle = _find_strangle(hd, ticker, exp, entry_str, entry_price,
                                       put_otm, call_otm)
            if strangle is None:
                continue

            entry_prem = strangle["total_premium"]
            if entry_prem < 0.05:
                continue

            # Size: risk = potential loss on strangle (uncapped for naked, but
            # we use ETFs which have bounded moves; cap risk at 3× premium)
            risk_per_contract = entry_prem * 3 * 100
            contracts = max(1, min(max_contracts,
                                   int(CAPITAL * risk_pct / risk_per_contract)))

            # Walk forward for exit
            exit_date = None
            exit_reason = ""
            exit_prem = entry_prem
            hold_days = 0

            current = entry_dt + timedelta(days=1)
            while current <= exp_obj and hold_days < max_hold_days:
                curr_str = current.strftime("%Y-%m-%d")
                if curr_str not in td_set:
                    current += timedelta(days=1)
                    continue

                hold_days += 1

                cur_prem = _reprice_strangle(hd, strangle["put_sym"],
                                              strangle["call_sym"], curr_str)
                if cur_prem is None:
                    current += timedelta(days=1)
                    continue

                # Profit target: premium dropped by profit_target_pct
                if cur_prem <= entry_prem * (1 - profit_target_pct):
                    exit_date = curr_str
                    exit_reason = "profit_target"
                    exit_prem = cur_prem
                    break

                # Stop loss: premium increased by stop_loss_pct
                if cur_prem >= entry_prem * (1 + stop_loss_pct):
                    exit_date = curr_str
                    exit_reason = "stop_loss"
                    exit_prem = cur_prem
                    break

                current += timedelta(days=1)

            if exit_date is None:
                # Time exit at max_hold_days or expiration
                exit_date = current.strftime("%Y-%m-%d") if hold_days >= max_hold_days else exp
                exit_reason = "time_exit" if hold_days >= max_hold_days else "expiration"
                # Try to get final price
                final = _reprice_strangle(hd, strangle["put_sym"],
                                           strangle["call_sym"], exit_date)
                if final is not None:
                    exit_prem = final
                else:
                    exit_prem = entry_prem * 0.5  # conservative estimate

            # P&L: sold premium at entry, bought back at exit
            pnl_per_contract = (entry_prem - exit_prem) * 100
            total_pnl = pnl_per_contract * contracts
            pnl_pct = (entry_prem - exit_prem) / entry_prem * 100

            # Realized move
            try:
                exit_price = float(close.loc[exit_date])
            except (KeyError, TypeError):
                exit_price = entry_price
            realized_move = abs(exit_price - entry_price) / entry_price

            # IV crush metric
            iv_crush = (entry_prem - exit_prem) / entry_prem if entry_prem > 0 else 0

            trades.append(StrangleTrade(
                ticker=ticker,
                entry_date=entry_str,
                exit_date=exit_date,
                expiration=exp,
                put_strike=strangle["put_strike"],
                call_strike=strangle["call_strike"],
                entry_premium=round(entry_prem, 4),
                exit_premium=round(exit_prem, 4),
                pnl=round(total_pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                contracts=contracts,
                hold_days=hold_days,
                exit_reason=exit_reason,
                earnings_month=month,
                year=year,
                underlying_entry=round(entry_price, 2),
                underlying_exit=round(exit_price, 2),
                realized_move_pct=round(realized_move * 100, 2),
                iv_crush_pct=round(iv_crush * 100, 2),
            ))

    return trades


# ── Analysis ─────────────────────────────────────────────────────────────

@dataclass
class TickerAnalysis:
    ticker: str
    name: str
    trades: List[StrangleTrade]
    n_trades: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    max_dd: float = 0.0
    sharpe: float = 0.0
    cagr: float = 0.0
    avg_iv_crush_pct: float = 0.0
    avg_realized_move_pct: float = 0.0
    avg_entry_premium: float = 0.0
    iv_overstatement_ratio: float = 0.0  # entry premium / realized move
    # Walk-forward
    is_n: int = 0
    is_sharpe: float = 0.0
    is_wr: float = 0.0
    oos_n: int = 0
    oos_sharpe: float = 0.0
    oos_wr: float = 0.0
    oos_pnl: float = 0.0
    # By quarter
    by_quarter: Dict = field(default_factory=dict)
    yearly: Dict = field(default_factory=dict)


def analyze_ticker(trades: List[StrangleTrade], ticker: str, name: str) -> TickerAnalysis:
    if not trades:
        return TickerAnalysis(ticker=ticker, name=name, trades=[])

    pnls = np.array([t.pnl for t in trades])
    n = len(pnls)
    total = float(pnls.sum())
    wins = int((pnls > 0).sum())

    eq = np.cumsum(pnls) + CAPITAL
    pk = np.maximum.accumulate(eq)
    dd = (pk - eq) / pk

    mu = pnls.mean()
    sd = pnls.std(ddof=1) if n > 1 else 1.0
    sharpe = float(mu / sd * math.sqrt(min(n, 52))) if sd > 1e-9 else 0

    years_span = (max(t.year for t in trades) - min(t.year for t in trades) + 1)
    cagr = ((1 + total / CAPITAL) ** (1 / max(years_span, 1)) - 1) if total > -CAPITAL else -1

    avg_crush = np.mean([t.iv_crush_pct for t in trades])
    avg_move = np.mean([t.realized_move_pct for t in trades])
    avg_prem = np.mean([t.entry_premium for t in trades])

    # IV overstatement: implied vol (proxied by premium) vs realized move
    # If premium consistently > realized move, IV is overstated
    implied_moves = [t.entry_premium / max(t.underlying_entry, 1) * 100 for t in trades]
    realized_moves = [t.realized_move_pct for t in trades]
    overstatement = np.mean(implied_moves) / max(np.mean(realized_moves), 0.01)

    # Walk-forward split
    is_trades = [t for t in trades if t.year < OOS_START]
    oos_trades = [t for t in trades if t.year >= OOS_START]

    is_pnls = np.array([t.pnl for t in is_trades]) if is_trades else np.array([0])
    oos_pnls = np.array([t.pnl for t in oos_trades]) if oos_trades else np.array([0])

    is_sd = is_pnls.std(ddof=1) if len(is_pnls) > 1 else 1.0
    oos_sd = oos_pnls.std(ddof=1) if len(oos_pnls) > 1 else 1.0

    # By quarter (Q1=Jan, Q2=Apr, Q3=Jul, Q4=Oct)
    by_q = {}
    for q, m in [(1, 1), (2, 4), (3, 7), (4, 10)]:
        q_trades = [t for t in trades if t.earnings_month == m]
        if q_trades:
            qp = np.array([t.pnl for t in q_trades])
            by_q[f"Q{q}"] = {
                "n": len(q_trades),
                "pnl": round(float(qp.sum()), 2),
                "wr": round(float((qp > 0).sum()) / len(qp), 4),
                "avg_crush": round(float(np.mean([t.iv_crush_pct for t in q_trades])), 2),
            }

    # Yearly
    yearly = {}
    for yr in sorted(set(t.year for t in trades)):
        yt = [t for t in trades if t.year == yr]
        yp = np.array([t.pnl for t in yt])
        yn = len(yp)
        ysd = yp.std(ddof=1) if yn > 1 else 1.0
        ye = np.cumsum(yp) + CAPITAL
        ypk = np.maximum.accumulate(ye)
        ydd = (ypk - ye) / ypk
        yearly[yr] = {
            "n": yn, "pnl": round(float(yp.sum()), 2),
            "wr": round(float((yp > 0).sum()) / yn, 4),
            "dd": round(float(ydd.max()), 4),
            "sharpe": round(float(yp.mean() / ysd * math.sqrt(min(yn, 52))) if ysd > 1e-9 else 0, 3),
            "avg_crush": round(float(np.mean([t.iv_crush_pct for t in yt])), 2),
        }

    return TickerAnalysis(
        ticker=ticker, name=name, trades=trades,
        n_trades=n, total_pnl=round(total, 2),
        win_rate=round(wins / n, 4), avg_pnl=round(float(mu), 2),
        max_dd=round(float(dd.max()), 4),
        sharpe=round(sharpe, 3), cagr=round(cagr, 4),
        avg_iv_crush_pct=round(float(avg_crush), 2),
        avg_realized_move_pct=round(float(avg_move), 2),
        avg_entry_premium=round(float(avg_prem), 4),
        iv_overstatement_ratio=round(float(overstatement), 3),
        is_n=len(is_trades),
        is_sharpe=round(float(is_pnls.mean() / is_sd * math.sqrt(min(len(is_pnls), 52))) if is_sd > 1e-9 else 0, 3),
        is_wr=round(float((is_pnls > 0).sum()) / max(len(is_pnls), 1), 4),
        oos_n=len(oos_trades),
        oos_sharpe=round(float(oos_pnls.mean() / oos_sd * math.sqrt(min(len(oos_pnls), 52))) if oos_sd > 1e-9 else 0, 3),
        oos_wr=round(float((oos_pnls > 0).sum()) / max(len(oos_pnls), 1), 4),
        oos_pnl=round(float(oos_pnls.sum()), 2),
        by_quarter=by_q, yearly=yearly,
    )


# ── Combined portfolio analysis ──────────────────────────────────────────

def analyze_combined(all_analyses: List[TickerAnalysis]) -> Dict:
    """Combine all tickers into portfolio-level stats."""
    all_trades = []
    for a in all_analyses:
        all_trades.extend(a.trades)

    if not all_trades:
        return {}

    # Sort by entry date for proper equity curve
    all_trades.sort(key=lambda t: t.entry_date)
    pnls = np.array([t.pnl for t in all_trades])
    n = len(pnls)
    total = float(pnls.sum())
    wins = int((pnls > 0).sum())

    eq = np.cumsum(pnls) + CAPITAL
    pk = np.maximum.accumulate(eq)
    dd = (pk - eq) / pk

    mu = pnls.mean()
    sd = pnls.std(ddof=1) if n > 1 else 1.0
    sharpe = float(mu / sd * math.sqrt(min(n, 52))) if sd > 1e-9 else 0

    # OOS
    oos_pnls = np.array([t.pnl for t in all_trades if t.year >= OOS_START])
    oos_n = len(oos_pnls)
    oos_sd = oos_pnls.std(ddof=1) if oos_n > 1 else 1.0
    oos_sharpe = float(oos_pnls.mean() / oos_sd * math.sqrt(min(oos_n, 52))) if oos_sd > 1e-9 and oos_n > 0 else 0

    # IV overstatement across all trades
    implied = [t.entry_premium / max(t.underlying_entry, 1) * 100 for t in all_trades]
    realized = [t.realized_move_pct for t in all_trades]
    overstatement = np.mean(implied) / max(np.mean(realized), 0.01)

    return {
        "n_trades": n, "total_pnl": round(total, 2),
        "win_rate": round(wins / n, 4), "max_dd": round(float(dd.max()), 4),
        "sharpe": round(sharpe, 3),
        "oos_n": oos_n, "oos_sharpe": round(oos_sharpe, 3),
        "oos_pnl": round(float(oos_pnls.sum()), 2) if oos_n > 0 else 0,
        "avg_iv_crush_pct": round(float(np.mean([t.iv_crush_pct for t in all_trades])), 2),
        "avg_realized_move_pct": round(float(np.mean([t.realized_move_pct for t in all_trades])), 2),
        "iv_overstatement_ratio": round(float(overstatement), 3),
    }


# ── HTML Report ──────────────────────────────────────────────────────────

def _c(v): return "#3fb950" if v >= 0 else "#f85149"
def _fd(v): return f"${v:,.0f}"
def _fp(v): return f"{v:.1%}"
def _fr(v): return f"{v:.2f}"


def build_report(
    analyses: List[TickerAnalysis],
    combined: Dict,
    output: Path,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Key question answer
    overstatement = combined.get("iv_overstatement_ratio", 0)
    iv_answer = "YES" if overstatement > 1.2 and combined.get("win_rate", 0) > 0.55 else "NO" if overstatement < 0.8 else "MIXED"
    iv_color = "#3fb950" if iv_answer == "YES" else "#f85149" if iv_answer == "NO" else "#d29922"
    iv_detail = (
        f"IV overstatement ratio: {_fr(overstatement)}× "
        f"(implied premium is {_fr(overstatement)}× the realized move). "
        f"Average IV crush: {combined.get('avg_iv_crush_pct', 0):.1f}% of entry premium. "
        f"Average realized move: {combined.get('avg_realized_move_pct', 0):.1f}%."
    )

    # Per-ticker overview
    ticker_rows = ""
    for a in analyses:
        oos_status = f"✓ {a.oos_n} trades" if a.oos_n >= 5 else f"⚠ {a.oos_n} trades"
        ticker_rows += f"""<tr>
          <td style="text-align:left"><strong>{a.ticker}</strong> ({a.name})</td>
          <td>{a.n_trades}</td>
          <td style="color:{_c(a.total_pnl)}">{_fd(a.total_pnl)}</td>
          <td>{_fp(a.win_rate)}</td><td>{_fp(a.max_dd)}</td>
          <td style="color:{_c(a.sharpe)}">{_fr(a.sharpe)}</td>
          <td>{a.avg_iv_crush_pct:.1f}%</td><td>{a.avg_realized_move_pct:.1f}%</td>
          <td>{_fr(a.iv_overstatement_ratio)}×</td>
          <td style="color:{_c(a.oos_sharpe)}">{_fr(a.oos_sharpe)}</td>
          <td>{oos_status}</td></tr>"""

    # Walk-forward detail per ticker
    wf_sections = ""
    for a in analyses:
        if a.n_trades == 0:
            continue
        wf_sections += f"""<h3>{a.ticker} ({a.name})</h3>
        <div class="g2">
          <div class="card"><h4>In-Sample (2020-2022)</h4>
            <p>{a.is_n} trades, Sharpe {_fr(a.is_sharpe)}, WR {_fp(a.is_wr)}</p></div>
          <div class="card"><h4>Out-of-Sample (2023-2025)</h4>
            <p>{a.oos_n} trades, Sharpe {_fr(a.oos_sharpe)}, WR {_fp(a.oos_wr)}, P&L {_fd(a.oos_pnl)}</p></div>
        </div>"""

        # Quarterly breakdown
        if a.by_quarter:
            q_rows = ""
            for q, data in sorted(a.by_quarter.items()):
                q_rows += f"""<tr><td>{q}</td><td>{data['n']}</td>
                  <td style="color:{_c(data['pnl'])}">{_fd(data['pnl'])}</td>
                  <td>{_fp(data['wr'])}</td><td>{data['avg_crush']:.1f}%</td></tr>"""
            wf_sections += f"""<table class="dt"><tr><th>Quarter</th><th>Trades</th>
              <th>P&L</th><th>Win Rate</th><th>Avg IV Crush</th></tr>{q_rows}</table>"""

        # Yearly breakdown
        if a.yearly:
            yr_rows = ""
            for yr in sorted(a.yearly):
                y = a.yearly[yr]
                oos_mark = " (OOS)" if yr >= OOS_START else ""
                yr_rows += f"""<tr><td>{yr}{oos_mark}</td><td>{y['n']}</td>
                  <td style="color:{_c(y['pnl'])}">{_fd(y['pnl'])}</td>
                  <td>{_fp(y['wr'])}</td><td>{_fp(y['dd'])}</td>
                  <td style="color:{_c(y['sharpe'])}">{_fr(y['sharpe'])}</td>
                  <td>{y['avg_crush']:.1f}%</td></tr>"""
            wf_sections += f"""<table class="dt"><tr><th>Year</th><th>N</th><th>P&L</th>
              <th>WR</th><th>DD</th><th>Sharpe</th><th>Avg Crush</th></tr>{yr_rows}</table>"""

    # Trade-level sample (top 10 by P&L)
    all_trades = []
    for a in analyses:
        all_trades.extend(a.trades)
    all_trades.sort(key=lambda t: -t.pnl)
    sample_rows = ""
    for t in all_trades[:10]:
        sample_rows += f"""<tr><td>{t.ticker}</td><td>{t.entry_date}</td><td>{t.exit_date}</td>
          <td>{t.put_strike}/{t.call_strike}</td>
          <td>{t.entry_premium:.2f}</td><td>{t.exit_premium:.2f}</td>
          <td style="color:{_c(t.pnl)}">{_fd(t.pnl)}</td>
          <td>{t.iv_crush_pct:.1f}%</td><td>{t.realized_move_pct:.1f}%</td>
          <td>{t.exit_reason}</td></tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>EXP-1650: Earnings Vol Crush</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 1400px; margin: 0 auto; padding: 24px; background: #0d1117; color: #c9d1d9; }}
  h1 {{ color: #58a6ff; margin-bottom: 4px; }}
  h2 {{ color: #58a6ff; border-bottom: 1px solid #21262d; padding-bottom: 6px; margin-top: 36px; }}
  h3 {{ color: #79c0ff; }} h4 {{ color: #79c0ff; margin: 8px 0; }}
  .meta {{ color: #8b949e; font-size: 0.88em; }}
  .verdict {{ background: #161b22; border: 2px solid {iv_color}; border-radius: 12px;
              padding: 24px; margin: 20px 0; text-align: center; }}
  .verdict .big {{ font-size: 2.5em; font-weight: 800; color: {iv_color}; }}
  .verdict .detail {{ color: #8b949e; margin-top: 8px; }}
  .kpi {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin: 16px 0; }}
  .kpi > div {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                padding: 10px; text-align: center; }}
  .kpi .l {{ display: block; color: #8b949e; font-size: 0.72em; }}
  .kpi .v {{ display: block; font-weight: 600; font-size: 1.1em; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 16px; }}
  .g2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 12px 0; }}
  table.dt {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.84em; }}
  table.dt th, table.dt td {{ padding: 5px 8px; text-align: right; border-bottom: 1px solid #21262d; }}
  table.dt th {{ color: #8b949e; background: #161b22; }}
  table.dt td:first-child {{ text-align: left; }}
  footer {{ margin-top: 40px; padding-top: 12px; border-top: 1px solid #21262d;
            color: #484f58; font-size: 0.78em; }}
</style></head><body>

<h1>EXP-1650: Earnings Vol Crush on Sector ETFs</h1>
<p class="meta">Generated {ts} &middot; Real IronVault data only &middot;
   XLF/XLK/XLE strangles &middot; IS 2020-2022 / OOS 2023-2025</p>

<div class="verdict">
  <div class="big">IV Overstatement at ETF Level: {iv_answer}</div>
  <div class="detail">{iv_detail}</div>
</div>

<h2>Combined Portfolio</h2>
<div class="kpi">
  <div><span class="l">Total Trades</span><span class="v">{combined.get('n_trades', 0)}</span></div>
  <div><span class="l">Total P&L</span><span class="v" style="color:{_c(combined.get('total_pnl', 0))}">{_fd(combined.get('total_pnl', 0))}</span></div>
  <div><span class="l">Win Rate</span><span class="v">{_fp(combined.get('win_rate', 0))}</span></div>
  <div><span class="l">Max DD</span><span class="v">{_fp(combined.get('max_dd', 0))}</span></div>
  <div><span class="l">Full Sharpe</span><span class="v" style="color:{_c(combined.get('sharpe', 0))}">{_fr(combined.get('sharpe', 0))}</span></div>
  <div><span class="l">OOS Sharpe</span><span class="v" style="color:{_c(combined.get('oos_sharpe', 0))}">{_fr(combined.get('oos_sharpe', 0))}</span></div>
  <div><span class="l">OOS Trades</span><span class="v">{combined.get('oos_n', 0)}</span></div>
  <div><span class="l">OOS P&L</span><span class="v" style="color:{_c(combined.get('oos_pnl', 0))}">{_fd(combined.get('oos_pnl', 0))}</span></div>
  <div><span class="l">IV Overstatement</span><span class="v">{_fr(combined.get('iv_overstatement_ratio', 0))}×</span></div>
  <div><span class="l">Avg IV Crush</span><span class="v">{combined.get('avg_iv_crush_pct', 0):.1f}%</span></div>
</div>

<h2>Per-Ticker Analysis</h2>
<table class="dt">
  <tr><th style="text-align:left">Ticker</th><th>Trades</th><th>P&L</th><th>WR</th><th>DD</th>
      <th>Sharpe</th><th>IV Crush</th><th>Real Move</th><th>IV/Real</th>
      <th>OOS Sharpe</th><th>OOS Status</th></tr>
{ticker_rows}
</table>

<h2>Walk-Forward Validation</h2>
<p class="meta">IS: 2020-2022 / OOS: 2023-2025. Per-ticker quarterly and yearly breakdown.</p>
{wf_sections}

<h2>Top Trades by P&L</h2>
<table class="dt">
  <tr><th>Ticker</th><th>Entry</th><th>Exit</th><th>Strikes</th>
      <th>Entry $</th><th>Exit $</th><th>P&L</th><th>IV Crush</th>
      <th>Real Move</th><th>Reason</th></tr>
{sample_rows}
</table>

<footer>
  Data: IronVault options_cache.db &middot; XLF (243K bars), XLK (18.7K), XLE (20.5K) &middot;
  No synthetic pricing &middot; Strangles priced from real put + call closes
</footer>
</body></html>"""

    output.write_text(html, encoding="utf-8")
    return output


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING)

    print("=" * 65)
    print("EXP-1650: Earnings Vol Crush on Sector ETFs")
    print("=" * 65)

    hd = IronVault.instance()
    cov = hd.coverage_report()
    print(f"IronVault: {cov['contracts_total']:,} contracts\n")

    # Fetch underlying prices
    print("Fetching market data...")
    price_data = {}
    for ticker in ["XLF", "XLK", "XLE"]:
        price_data[ticker] = _dl(ticker)

    # Run backtests per ticker — EARNINGS-ONLY
    analyses = []
    for ticker, config in EARNINGS_CALENDAR.items():
        print(f"\n[{ticker}] {config['name']} — earnings months {config['months']}")
        trades = run_earnings_vol_crush(hd, ticker, price_data[ticker], config)
        analysis = analyze_ticker(trades, ticker, config["name"])
        analyses.append(analysis)

        if analysis.n_trades > 0:
            print(f"  {analysis.n_trades} trades, P&L {_fd(analysis.total_pnl)}, "
                  f"WR {_fp(analysis.win_rate)}, Sharpe {_fr(analysis.sharpe)}")
            print(f"  IV crush: {analysis.avg_iv_crush_pct:.1f}%, "
                  f"Realized move: {analysis.avg_realized_move_pct:.1f}%, "
                  f"Overstatement: {_fr(analysis.iv_overstatement_ratio)}×")
            print(f"  IS: {analysis.is_n} trades, Sharpe {_fr(analysis.is_sharpe)} | "
                  f"OOS: {analysis.oos_n} trades, Sharpe {_fr(analysis.oos_sharpe)}")
        else:
            print(f"  No trades (insufficient data)")

    # Run ALL-MONTHS comparison for XLF (control group)
    print(f"\n[XLF] ALL MONTHS (control — not earnings-specific)")
    all_month_config = {
        "name": "Financials All-Month",
        "months": list(range(1, 13)),
        "report_day": 15,
        "entry_offset_days": 14,
    }
    all_month_trades = run_earnings_vol_crush(
        hd, "XLF", price_data["XLF"], all_month_config)
    all_month_analysis = analyze_ticker(all_month_trades, "XLF-AllMonth", "Financials All-Month")
    analyses.append(all_month_analysis)
    if all_month_analysis.n_trades > 0:
        print(f"  {all_month_analysis.n_trades} trades, P&L {_fd(all_month_analysis.total_pnl)}, "
              f"WR {_fp(all_month_analysis.win_rate)}, Sharpe {_fr(all_month_analysis.sharpe)}")
        print(f"  IV crush: {all_month_analysis.avg_iv_crush_pct:.1f}%, "
              f"Realized move: {all_month_analysis.avg_realized_move_pct:.1f}%, "
              f"Overstatement: {_fr(all_month_analysis.iv_overstatement_ratio)}×")
        print(f"  IS: {all_month_analysis.is_n} trades, Sharpe {_fr(all_month_analysis.is_sharpe)} | "
              f"OOS: {all_month_analysis.oos_n} trades, Sharpe {_fr(all_month_analysis.oos_sharpe)}")

    # Compare earnings-only vs all-months for XLF
    xlf_earnings = next((a for a in analyses if a.ticker == "XLF"), None)
    if xlf_earnings and all_month_analysis.n_trades > 0:
        print(f"\n  COMPARISON — XLF Earnings-Only vs All-Month:")
        print(f"    Earnings: WR {_fp(xlf_earnings.win_rate)}, crush {xlf_earnings.avg_iv_crush_pct:.1f}%, "
              f"overstatement {_fr(xlf_earnings.iv_overstatement_ratio)}×")
        print(f"    All-Month: WR {_fp(all_month_analysis.win_rate)}, crush {all_month_analysis.avg_iv_crush_pct:.1f}%, "
              f"overstatement {_fr(all_month_analysis.iv_overstatement_ratio)}×")

    # Combined
    combined = analyze_combined(analyses)

    print(f"\n{'=' * 65}")
    print(f"COMBINED PORTFOLIO")
    print(f"  {combined.get('n_trades', 0)} trades, P&L {_fd(combined.get('total_pnl', 0))}")
    print(f"  Win Rate: {_fp(combined.get('win_rate', 0))}, Sharpe: {_fr(combined.get('sharpe', 0))}")
    print(f"  OOS: {combined.get('oos_n', 0)} trades, Sharpe: {_fr(combined.get('oos_sharpe', 0))}")
    overstatement = combined.get('iv_overstatement_ratio', 0)
    answer = "YES" if overstatement > 1.2 and combined.get('win_rate', 0) > 0.55 else "NO" if overstatement < 0.8 else "MIXED"
    print(f"\n  KEY QUESTION — IV overstatement at ETF level: {answer}")
    print(f"  Overstatement ratio: {_fr(overstatement)}×")

    # Generate reports
    rp = build_report(analyses, combined, REPORT_PATH)
    print(f"\nReport: {rp}")

    # Save JSON
    summary = {
        "experiment": "EXP-1650",
        "data_source": "IronVault (options_cache.db)",
        "synthetic_data": False,
        "key_finding": f"IV overstatement at ETF level: {answer} (ratio {_fr(overstatement)}×)",
        "combined": combined,
        "per_ticker": {
            a.ticker: {
                "name": a.name, "n_trades": a.n_trades, "total_pnl": a.total_pnl,
                "win_rate": a.win_rate, "sharpe": a.sharpe, "max_dd": a.max_dd,
                "avg_iv_crush_pct": a.avg_iv_crush_pct,
                "avg_realized_move_pct": a.avg_realized_move_pct,
                "iv_overstatement_ratio": a.iv_overstatement_ratio,
                "is_n": a.is_n, "is_sharpe": a.is_sharpe,
                "oos_n": a.oos_n, "oos_sharpe": a.oos_sharpe, "oos_pnl": a.oos_pnl,
                "by_quarter": a.by_quarter, "yearly": a.yearly,
            }
            for a in analyses
        },
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"JSON: {OUTPUT_DIR / 'summary.json'}")


if __name__ == "__main__":
    main()
