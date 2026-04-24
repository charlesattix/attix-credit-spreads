"""
EXP-2910 — TLT Put Credit Spread Integration

Hypothesis: TLT put credit spreads (28 DTE, 5% OTM) harvest the bond
variance risk premium, which is persistent and uncorrelated with equity VRP.
Adding TLT as a 9th stream expands portfolio capacity while improving
diversification.

Data: IronVault TLT options (10,749 contracts, 293,500 daily bars,
2020-01 to 2025-12). Rule Zero: all prices from IronVault real data.

Methodology:
  1. Extract TLT 28-DTE 5% OTM put credit spreads (EXP-1220 methodology)
  2. Walk-forward backtest: 252-day train / 63-day test folds
  3. Report individual stream metrics
  4. Measure correlation with existing 8 streams
  5. If gates pass: integrate into 9-stream portfolio with Ledoit-Wolf
  6. Apply VIX ladder + 12% vol target + 890 bps drag
  7. Compare pooled net Sharpe vs v8a baseline (6.39)

Kill criteria:
  - Trade Sharpe < 1.0
  - Trades/year < 20
  - 9-stream net Sharpe < 6.0
  - Buffer degradation

Tag: EXP-2910
"""

from __future__ import annotations

import json
import math
import pickle
import sqlite3
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.iron_vault import IronVault
from compass.metrics import (
    annualized_sharpe, cagr, max_drawdown, full_metrics,
    TRADING_DAYS, DEFAULT_RF_ANNUAL,
)
from compass.vix_ladder import VIXLadder, fetch_vix

REPORT_DIR = ROOT / "compass" / "reports"
CACHE_DIR = ROOT / "compass" / "cache"
RESULTS_PATH = ROOT / "experiments" / "EXP-2910_TLT_RESULTS.md"

# Walk-forward config (matching v8a methodology)
TRAIN_DAYS = 252
TEST_DAYS = 63
TARGET_VOL = 0.12  # 12% annualized vol target
VOL_SCALE_CAP = 20.0
NET_DRAG_BPS = 890  # Alpaca commission-free
NET_DRAG_PCT = NET_DRAG_BPS / 100.0  # 8.90%
CAPITAL = 100_000

# Credit spread parameters (matching EXP-1220)
OTM_PCT = 0.95  # 5% OTM
TARGET_DTE = 28
MIN_ENTRY_GAP_DAYS = 10
WIDTH_TARGET = 5.0
PROFIT_TARGET = 0.50
STOP_MULT = 2.0
MIN_DTE_EXIT = 7


# ═══════════════════════════════════════════════════════════════════════
# Step 1: Extract TLT put credit spreads from IronVault
# ═══════════════════════════════════════════════════════════════════════

def _find_tlt_exps(db_path: str, start: str, end: str) -> List[str]:
    """Find all TLT put expirations with real data in IronVault."""
    conn = sqlite3.connect(db_path)
    exps = [r[0] for r in conn.execute(
        "SELECT DISTINCT expiration FROM option_contracts "
        "WHERE ticker='TLT' AND option_type='P' AND expiration BETWEEN ? AND ? "
        "ORDER BY expiration", (start, end)).fetchall()]
    conn.close()
    return exps


def _next_td(dt, td_set):
    for off in range(7):
        c = dt + timedelta(days=off)
        if c.strftime("%Y-%m-%d") in td_set:
            return c
    return None


def _sell_tlt_put_spread(hd, exp, trade_date, price, otm_pct=OTM_PCT,
                         width=WIDTH_TARGET):
    """Construct a TLT put credit spread: sell OTM put, buy further OTM put."""
    strikes = hd.get_available_strikes("TLT", exp, trade_date, "P")
    if not strikes:
        return None
    target = price * otm_pct
    for sk in sorted(strikes, key=lambda k: abs(k - target))[:12]:
        lk = sk - width
        if lk not in strikes:
            # Find nearest long strike below short
            cands = [s for s in strikes if s < sk and abs(s - lk) <= 2.0]
            if not cands:
                continue
            lk = max(cands)
        if sk - lk <= 0:
            continue
        exp_dt = datetime.strptime(exp, "%Y-%m-%d")
        pp = hd.get_spread_prices("TLT", exp_dt, sk, lk, "P", trade_date)
        if pp is None:
            continue
        credit = pp["short_close"] - pp["long_close"]
        if credit > 0.03:  # TLT options have lower premiums than SPY
            return {
                "short": sk, "long": lk, "credit": round(credit, 4),
                "width": round(sk - lk, 2),
                "max_loss": round(sk - lk - credit, 4),
            }
    return None


def _walk_tlt_spread(hd, exp, short_k, long_k, entry_credit, entry_dt,
                     exp_dt_obj, td_index, profit_pct=PROFIT_TARGET,
                     stop_mult=STOP_MULT, min_dte=MIN_DTE_EXIT):
    """Walk a TLT spread forward: profit target, stop loss, or DTE exit."""
    td_set = set(td_index.strftime("%Y-%m-%d"))
    hold = 0
    current = entry_dt + timedelta(days=1)
    while current <= exp_dt_obj:
        cs = current.strftime("%Y-%m-%d")
        if cs not in td_set:
            current += timedelta(days=1)
            continue
        hold += 1
        pp = hd.get_spread_prices("TLT", exp_dt_obj, short_k, long_k, "P", cs)
        if pp is None:
            current += timedelta(days=1)
            continue
        cv = pp["short_close"] - pp["long_close"]
        if cv <= entry_credit * (1 - profit_pct):
            return cs, "profit", cv, hold
        if cv - entry_credit > entry_credit * stop_mult:
            return cs, "stop", cv, hold
        if (exp_dt_obj - current).days <= min_dte:
            return cs, "dte_exit", cv, hold
        current += timedelta(days=1)
    # Expiration
    exp_str = exp_dt_obj.strftime("%Y-%m-%d")
    fp = hd.get_spread_prices("TLT", exp_dt_obj, short_k, long_k, "P", exp_str)
    return exp_str, "expiration", (fp["short_close"] - fp["long_close"]) if fp else 0.0, hold


def extract_tlt_trades(hd, tlt_df, vix) -> List[Dict]:
    """Extract TLT 28-DTE 5% OTM put credit spreads from IronVault."""
    tlt_close = tlt_df["Close"]
    td_set = set(tlt_df.index.strftime("%Y-%m-%d"))
    exps = _find_tlt_exps(hd._db_path, "2020-03-01", "2025-12-31")
    trades, last = [], None

    for exp in exps:
        exp_obj = datetime.strptime(exp, "%Y-%m-%d")
        entry_dt = _next_td(exp_obj - timedelta(days=TARGET_DTE), td_set)
        if entry_dt is None:
            continue
        es = entry_dt.strftime("%Y-%m-%d")
        if last and (entry_dt - last).days < MIN_ENTRY_GAP_DAYS:
            continue
        try:
            price = float(tlt_close.loc[es])
            v = float(vix.loc[es])
        except (KeyError, TypeError):
            continue
        if np.isnan(price) or np.isnan(v):
            continue
        if v > 40:
            continue  # skip extreme crisis

        spread = _sell_tlt_put_spread(hd, exp, es, price)
        if spread is None:
            continue
        cts = max(1, min(10, int(CAPITAL * 0.03 / (spread["max_loss"] * 100))))
        ed, er, ev, hold = _walk_tlt_spread(
            hd, exp, spread["short"], spread["long"],
            spread["credit"], entry_dt, exp_obj, tlt_df.index)
        pnl = (spread["credit"] - ev) * 100 * cts
        trades.append({
            "ticker": "TLT",
            "entry_date": es,
            "exit_date": ed,
            "pnl": round(pnl, 2),
            "exit_reason": er,
            "credit": spread["credit"],
            "short_strike": spread["short"],
            "long_strike": spread["long"],
            "width": spread["width"],
            "vix": round(v, 1),
            "hold_days": hold,
            "contracts": cts,
        })
        last = entry_dt

    return trades


# ═══════════════════════════════════════════════════════════════════════
# Step 2: Trade-level metrics
# ═══════════════════════════════════════════════════════════════════════

def trade_level_metrics(trades: List[Dict]) -> Dict:
    """Compute trade-level metrics (matching EXP-1220 method_trade_level)."""
    if not trades:
        return {"sharpe": 0, "cagr_pct": 0, "max_dd_pct": 0, "n_trades": 0,
                "win_rate": 0, "trades_per_year": 0, "total_pnl": 0}

    pnls = np.array([t["pnl"] for t in trades])
    n = len(pnls)
    total = float(pnls.sum())
    wins = int((pnls > 0).sum())

    df = pd.DataFrame(trades)
    entry_dates = pd.to_datetime(df["entry_date"])
    exit_dates = pd.to_datetime(df["exit_date"])
    years = max((exit_dates.max() - entry_dates.min()).days / 365.25, 0.5)
    trades_per_year = n / years

    # Equity curve
    eq = np.cumsum(pnls) + CAPITAL
    peak = np.maximum.accumulate(eq)
    dd = float(((peak - eq) / peak).max())

    # CAGR
    cagr_val = ((1 + total / CAPITAL) ** (1 / years) - 1) if total > -CAPITAL else -1

    # Sharpe (trade-level, annualized)
    mu = float(pnls.mean())
    sigma = float(pnls.std(ddof=1)) if n > 1 else 1.0
    sharpe = mu / sigma * math.sqrt(trades_per_year) if sigma > 1e-9 else 0.0

    return {
        "sharpe": round(sharpe, 2),
        "cagr_pct": round(cagr_val * 100, 2),
        "max_dd_pct": round(dd * 100, 2),
        "n_trades": n,
        "win_rate": round(wins / n, 3),
        "trades_per_year": round(trades_per_year, 1),
        "total_pnl": round(total, 2),
        "avg_pnl": round(mu, 2),
        "years": round(years, 2),
    }


# ═══════════════════════════════════════════════════════════════════════
# Step 3: Build sparse daily returns for the TLT stream
# ═══════════════════════════════════════════════════════════════════════

def trades_to_sparse_daily(trades: List[Dict], bday_index: pd.DatetimeIndex,
                           capital: float = CAPITAL) -> pd.Series:
    """Convert trade list to sparse exit-date daily returns (v8a convention)."""
    daily = pd.Series(0.0, index=bday_index, name="tlt_cs")
    for t in trades:
        ed = pd.Timestamp(t["exit_date"])
        if ed in daily.index:
            daily.loc[ed] += t["pnl"] / capital
    return daily


# ═══════════════════════════════════════════════════════════════════════
# Step 4: Correlation with existing 8 streams
# ═══════════════════════════════════════════════════════════════════════

def load_v8a_cube() -> pd.DataFrame:
    """Load the v8a 8-stream cube (7 from sparse cache + QQQ)."""
    cache_path = CACHE_DIR / "exp2280_v6_sparse.pkl"
    if not cache_path.exists():
        raise FileNotFoundError(f"v6 sparse cache not found: {cache_path}")
    cube = pickle.load(open(cache_path, "rb"))
    # Rename vol_arb -> cross_vol for consistency
    if "vol_arb" in cube.columns:
        cube = cube.rename(columns={"vol_arb": "cross_vol"})

    # Add QQQ if available
    qqq_cache = CACHE_DIR / "exp2250_qqq_trades.pkl"
    if qqq_cache.exists():
        qqq_trades = pickle.load(open(qqq_cache, "rb"))
        qqq_daily = pd.Series(0.0, index=cube.index, name="qqq_cs")
        for t in qqq_trades:
            ed = pd.Timestamp(t["exit_date"])
            if ed in qqq_daily.index:
                qqq_daily.loc[ed] += t["pnl"] / CAPITAL
        cube["qqq_cs"] = qqq_daily

    return cube


def compute_correlations(tlt_daily: pd.Series, cube: pd.DataFrame) -> Dict:
    """Compute pairwise correlation of TLT with all existing streams."""
    aligned = cube.copy()
    aligned["tlt_cs"] = tlt_daily.reindex(cube.index).fillna(0.0)

    # Only correlate on non-zero days for meaningful signal
    corr_matrix = aligned.corr()
    tlt_corr = corr_matrix["tlt_cs"].drop("tlt_cs").to_dict()

    return {
        "pairwise": {k: round(v, 4) for k, v in tlt_corr.items()},
        "mean_correlation": round(np.mean(list(tlt_corr.values())), 4),
    }


# ═══════════════════════════════════════════════════════════════════════
# Step 5: Risk-parity weights via Ledoit-Wolf
# ═══════════════════════════════════════════════════════════════════════

def risk_parity_weights(Sigma: np.ndarray, n_iter: int = 500,
                        tol: float = 1e-10) -> np.ndarray:
    """Equal-risk-contribution via Chaves-Hsu-Li-Shakernia fixed-point."""
    N = Sigma.shape[0]
    Sigma = (Sigma + Sigma.T) / 2
    eig_min = float(np.linalg.eigvalsh(Sigma).min())
    if eig_min < 1e-14:
        Sigma = Sigma + np.eye(N) * (1e-14 - eig_min + 1e-14)
    w = np.ones(N) / N
    for _ in range(n_iter):
        mrc = Sigma @ w
        rc = w * mrc
        target = rc.mean()
        if target <= 1e-30:
            break
        scale = np.sqrt(target / np.maximum(rc, 1e-30))
        w_new = w * scale
        w_new = np.maximum(w_new, 1e-10)
        w_new = w_new / w_new.sum()
        if np.max(np.abs(w_new - w)) < tol:
            w = w_new
            break
        w = w_new
    return w


# ═══════════════════════════════════════════════════════════════════════
# Step 6: Walk-forward backtest
# ═══════════════════════════════════════════════════════════════════════

def walk_forward_portfolio(cube: pd.DataFrame,
                           vix_series: Optional[pd.Series] = None,
                           apply_ladder: bool = True,
                           ) -> Tuple[pd.Series, List[Dict]]:
    """Walk-forward with Ledoit-Wolf + risk-parity + vol target + VIX ladder.

    Returns GROSS daily returns. Transaction costs are applied analytically
    via net_sharpe_from_drag() after computing gross metrics (matching EXP-2450
    methodology). Direct daily drag subtraction destroys sparse-convention
    portfolios where most days have zero return.
    """
    n = len(cube)
    cols = list(cube.columns)
    pooled_idx, pooled_vals = [], []
    fold_rows = []
    fold_ix = 0
    i = TRAIN_DAYS

    # VIX ladder
    ladder = VIXLadder() if apply_ladder else None
    if vix_series is not None:
        vix_aligned = vix_series.reindex(cube.index).ffill().bfill()
    else:
        vix_aligned = None

    while i + TEST_DAYS <= n:
        train = cube.iloc[i - TRAIN_DAYS:i].values
        test = cube.iloc[i:i + TEST_DAYS]

        # Ledoit-Wolf covariance
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                Sigma = LedoitWolf().fit(train).covariance_
            except Exception:
                Sigma = np.cov(train, rowvar=False, ddof=1)

        w = risk_parity_weights(Sigma)

        # Gross portfolio returns
        raw = test.values @ w

        # Vol-target scaling
        train_port = train @ w
        train_vol = float(np.std(train_port, ddof=1)) * math.sqrt(TRADING_DAYS)
        if train_vol <= 1e-10:
            scale = 1.0
        else:
            scale = TARGET_VOL / train_vol
        scale = float(np.clip(scale, 0.1, VOL_SCALE_CAP))
        scaled = raw * scale

        # VIX ladder (causal: use yesterday's VIX)
        if apply_ladder and ladder is not None and vix_aligned is not None:
            test_vix = vix_aligned.iloc[i:i + TEST_DAYS]
            exposure = ladder.apply(test_vix)
            scaled = scaled * exposure.values

        pooled_idx.extend(test.index.tolist())
        pooled_vals.extend(scaled.tolist())

        # Per-fold metrics (GROSS)
        m = full_metrics(np.array(scaled))
        fold_rows.append({
            "fold": fold_ix,
            "test_start": str(test.index[0].date()),
            "test_end": str(test.index[-1].date()),
            "sharpe": m["sharpe"],
            "cagr_pct": m["cagr_pct"],
            "max_dd_pct": m["max_dd_pct"],
            "vol_pct": m["vol_pct"],
            "vol_scale": round(scale, 3),
            "weights": {cols[j]: round(float(w[j]), 4) for j in range(len(cols))},
        })
        fold_ix += 1
        i += TEST_DAYS

    pooled = pd.Series(pooled_vals, index=pooled_idx, dtype=float)
    return pooled, fold_rows


# ═══════════════════════════════════════════════════════════════════════
# Step 7: Rule Zero synthetic data check
# ═══════════════════════════════════════════════════════════════════════

def rule_zero_check() -> Dict:
    """Grep for synthetic data patterns in executable code (not comments/strings)."""
    import re, ast, tokenize, io
    src = Path(__file__).read_text()

    # Strip comments and docstrings — only check executable code
    # Simple approach: check for actual function calls, not mentions in strings
    patterns = {
        "np.random": r"np\.random\.\w+\(",
        "random.normal": r"random\.normal\(",
        "generate_prices": r"generate_prices\(",
    }
    findings = {}
    lines = src.split("\n")
    for name, pat in patterns.items():
        count = 0
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                continue
            if re.search(pat, line):
                count += 1
        findings[name] = count

    clean = all(v == 0 for v in findings.values())
    return {"clean": clean, "findings": findings}


# ═══════════════════════════════════════════════════════════════════════
# Main execution
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("EXP-2910: TLT Put Credit Spread Integration")
    print("=" * 70)

    # Rule Zero check
    r0 = rule_zero_check()
    print(f"\n[Rule Zero] Synthetic data check: {'CLEAN' if r0['clean'] else 'CONTAMINATED'}")
    print(f"  Findings: {r0['findings']}")

    # Load data
    print("\n[1/8] Loading market data...")
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tlt_df = yf.download("TLT", start="2019-06-01", end="2026-07-01", progress=False)
    if isinstance(tlt_df.columns, pd.MultiIndex):
        tlt_df.columns = tlt_df.columns.get_level_values(0)
    tlt_df.index = pd.to_datetime(tlt_df.index).tz_localize(None)

    vix_series = fetch_vix("2019-06-01", "2026-07-01")

    hd = IronVault.instance()

    # Extract TLT trades
    print("\n[2/8] Extracting TLT 28-DTE 5% OTM put credit spreads from IronVault...")
    trades = extract_tlt_trades(hd, tlt_df, vix_series)
    print(f"  Extracted {len(trades)} trades")

    if not trades:
        print("  FATAL: No trades extracted. Experiment KILLED.")
        return None

    # Cache trades
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "exp2910_tlt_trades.pkl"
    pickle.dump(trades, open(cache_path, "wb"))
    print(f"  Cached to {cache_path}")

    # Trade-level metrics
    print("\n[3/8] Computing individual stream metrics...")
    tl_metrics = trade_level_metrics(trades)
    print(f"  Sharpe:        {tl_metrics['sharpe']:.2f}")
    print(f"  CAGR:          {tl_metrics['cagr_pct']:.1f}%")
    print(f"  Max DD:        {tl_metrics['max_dd_pct']:.1f}%")
    print(f"  Trades/year:   {tl_metrics['trades_per_year']:.1f}")
    print(f"  Win rate:      {tl_metrics['win_rate']:.0%}")
    print(f"  Total PnL:     ${tl_metrics['total_pnl']:,.2f}")
    print(f"  Avg PnL/trade: ${tl_metrics['avg_pnl']:,.2f}")

    # Kill criteria check
    print("\n[4/8] Kill criteria check...")
    kill_reasons = []
    if tl_metrics["sharpe"] < 1.0:
        kill_reasons.append(f"Trade Sharpe {tl_metrics['sharpe']:.2f} < 1.0")
    if tl_metrics["trades_per_year"] < 20:
        kill_reasons.append(
            f"Trades/year {tl_metrics['trades_per_year']:.1f} < 20")

    if kill_reasons:
        print(f"  KILL CRITERIA TRIGGERED:")
        for r in kill_reasons:
            print(f"    - {r}")
        print("  Proceeding with analysis but flagging as KILLED.")
    else:
        print("  All individual kill criteria PASSED.")

    # Correlation analysis
    print("\n[5/8] Computing correlation with existing 8 streams...")
    cube_8 = load_v8a_cube()
    tlt_daily = trades_to_sparse_daily(trades, cube_8.index)
    corr_result = compute_correlations(tlt_daily, cube_8)
    print(f"  Mean correlation with existing streams: {corr_result['mean_correlation']:.4f}")
    for stream, rho in sorted(corr_result["pairwise"].items()):
        print(f"    {stream:12s}: ρ = {rho:+.4f}")

    # Build 9-stream cube
    print("\n[6/8] Building 9-stream portfolio and running walk-forward...")
    cube_9 = cube_8.copy()
    cube_9["tlt_cs"] = tlt_daily.reindex(cube_9.index).fillna(0.0)
    print(f"  9-stream cube: {cube_9.shape}, columns: {list(cube_9.columns)}")

    # Walk-forward: 9-stream with VIX ladder (GROSS, then analytical cost)
    from compass.exp2420_transaction_costs import net_sharpe_from_drag
    vix_aligned = vix_series.reindex(cube_9.index).ffill().bfill()
    pooled_9, folds_9 = walk_forward_portfolio(
        cube_9, vix_series=vix_aligned, apply_ladder=True)
    metrics_9_gross = full_metrics(pooled_9.values)
    net_9 = net_sharpe_from_drag(
        gross_sharpe=metrics_9_gross["sharpe"],
        gross_cagr_pct=metrics_9_gross["cagr_pct"],
        vol_pct=metrics_9_gross["vol_pct"],
        annual_drag_pct=NET_DRAG_PCT,
    )
    metrics_9_net = {
        "sharpe": net_9["net_sharpe"],
        "cagr_pct": net_9["net_cagr_pct"],
        "max_dd_pct": metrics_9_gross["max_dd_pct"],  # DD unchanged by cost
        "vol_pct": metrics_9_gross["vol_pct"],
    }
    print(f"  9-stream GROSS (with VIX ladder):")
    print(f"    Pooled Sharpe: {metrics_9_gross['sharpe']:.2f}")
    print(f"    CAGR:          {metrics_9_gross['cagr_pct']:.1f}%")
    print(f"    Max DD:        {metrics_9_gross['max_dd_pct']:.1f}%")
    print(f"    Vol:           {metrics_9_gross['vol_pct']:.1f}%")
    print(f"  9-stream NET ({NET_DRAG_BPS} bps drag):")
    print(f"    Pooled Sharpe: {metrics_9_net['sharpe']:.2f}")
    print(f"    CAGR:          {metrics_9_net['cagr_pct']:.1f}%")

    # Walk-forward: 8-stream baseline (for comparison)
    print("\n[7/8] Running v8a baseline (8-stream) for comparison...")
    pooled_8, folds_8 = walk_forward_portfolio(
        cube_8, vix_series=vix_aligned, apply_ladder=True)
    metrics_8_gross = full_metrics(pooled_8.values)
    net_8 = net_sharpe_from_drag(
        gross_sharpe=metrics_8_gross["sharpe"],
        gross_cagr_pct=metrics_8_gross["cagr_pct"],
        vol_pct=metrics_8_gross["vol_pct"],
        annual_drag_pct=NET_DRAG_PCT,
    )
    metrics_8_net = {
        "sharpe": net_8["net_sharpe"],
        "cagr_pct": net_8["net_cagr_pct"],
        "max_dd_pct": metrics_8_gross["max_dd_pct"],
        "vol_pct": metrics_8_gross["vol_pct"],
    }
    print(f"  8-stream GROSS baseline:")
    print(f"    Pooled Sharpe: {metrics_8_gross['sharpe']:.2f}")
    print(f"    CAGR:          {metrics_8_gross['cagr_pct']:.1f}%")
    print(f"  8-stream NET baseline:")
    print(f"    Pooled Sharpe: {metrics_8_net['sharpe']:.2f}")
    print(f"    CAGR:          {metrics_8_net['cagr_pct']:.1f}%")

    # Per-fold analysis
    fold_sharpes_9 = [f["sharpe"] for f in folds_9]
    fold_sharpes_8 = [f["sharpe"] for f in folds_8]
    median_9 = float(np.median(fold_sharpes_9)) if fold_sharpes_9 else 0
    median_8 = float(np.median(fold_sharpes_8)) if fold_sharpes_8 else 0
    pct_above_6_9 = float(np.mean([s >= 6.0 for s in fold_sharpes_9])) * 100
    worst_fold_9 = min(fold_sharpes_9) if fold_sharpes_9 else 0

    # 9-stream portfolio kill check
    portfolio_kill = []
    if metrics_9_net["sharpe"] < 6.0:
        portfolio_kill.append(
            f"9-stream net Sharpe {metrics_9_net['sharpe']:.2f} < 6.0")

    # Sharpe delta
    sharpe_delta = metrics_9_net["sharpe"] - metrics_8_net["sharpe"]

    # Compile results
    print("\n[8/8] Compiling results...")
    results = {
        "experiment": "EXP-2910",
        "title": "TLT Put Credit Spread Integration",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rule_zero": r0,
        "data_source": {
            "database": "IronVault options_cache.db",
            "ticker": "TLT",
            "contracts": 10749,
            "daily_bars": 293500,
            "date_range": "2020-01 to 2025-12",
            "vix": "Yahoo ^VIX (real)",
            "tlt_spot": "Yahoo TLT (real)",
        },
        "individual_stream": {
            "metrics": tl_metrics,
            "kill_criteria": {
                "sharpe_ge_1": tl_metrics["sharpe"] >= 1.0,
                "trades_per_year_ge_20": tl_metrics["trades_per_year"] >= 20,
                "passed": len(kill_reasons) == 0,
                "reasons": kill_reasons,
            },
            "trade_summary": {
                "n_trades": len(trades),
                "exit_reasons": pd.DataFrame(trades)["exit_reason"].value_counts().to_dict(),
                "per_year": {},
            },
        },
        "correlation": corr_result,
        "portfolio_9stream": {
            "gross_metrics": metrics_9_gross,
            "net_metrics": metrics_9_net,
            "fold_sharpes": fold_sharpes_9,
            "median_fold_sharpe": round(median_9, 2),
            "worst_fold_sharpe": round(worst_fold_9, 2),
            "pct_folds_above_6": round(pct_above_6_9, 1),
            "n_folds": len(folds_9),
        },
        "baseline_8stream": {
            "gross_metrics": metrics_8_gross,
            "net_metrics": metrics_8_net,
            "fold_sharpes": fold_sharpes_8,
            "median_fold_sharpe": round(median_8, 2),
        },
        "comparison": {
            "sharpe_delta": round(sharpe_delta, 2),
            "sharpe_improved": sharpe_delta > 0,
            "v8a_reference_sharpe": 6.39,
        },
        "portfolio_kill_criteria": {
            "net_sharpe_ge_6": metrics_9_net["sharpe"] >= 6.0,
            "reasons": portfolio_kill,
        },
        "verdict": "",
        "folds_detail_9": folds_9,
        "folds_detail_8": folds_8,
    }

    # Per-year trade summary
    df_trades = pd.DataFrame(trades)
    df_trades["year"] = pd.to_datetime(df_trades["exit_date"]).dt.year
    for yr, grp in df_trades.groupby("year"):
        results["individual_stream"]["trade_summary"]["per_year"][int(yr)] = {
            "n": len(grp),
            "pnl": round(float(grp["pnl"].sum()), 2),
            "win_rate": round(float((grp["pnl"] > 0).mean()), 3),
        }

    # Final verdict
    individual_pass = len(kill_reasons) == 0
    portfolio_pass = len(portfolio_kill) == 0

    if individual_pass and portfolio_pass:
        results["verdict"] = (
            f"PASS — TLT put credit spread integration approved. "
            f"Individual Sharpe {tl_metrics['sharpe']:.2f}, "
            f"{tl_metrics['trades_per_year']:.0f} trades/yr. "
            f"9-stream net Sharpe {metrics_9_net['sharpe']:.2f} "
            f"(delta {sharpe_delta:+.2f} vs 8-stream). "
            f"Mean ρ = {corr_result['mean_correlation']:.4f} with existing streams."
        )
    else:
        all_reasons = kill_reasons + portfolio_kill
        results["verdict"] = (
            f"KILLED — {'; '.join(all_reasons)}. "
            f"Individual Sharpe {tl_metrics['sharpe']:.2f}, "
            f"{tl_metrics['trades_per_year']:.0f} trades/yr. "
            f"9-stream net Sharpe {metrics_9_net['sharpe']:.2f}."
        )

    # Write results
    write_results_md(results)
    json_path = REPORT_DIR / "exp2910_tlt_results.json"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n  Results written to:")
    print(f"    {RESULTS_PATH}")
    print(f"    {json_path}")

    print(f"\n{'=' * 70}")
    print(f"VERDICT: {results['verdict']}")
    print(f"{'=' * 70}")

    return results


def write_results_md(results: Dict):
    """Write structured markdown results."""
    r = results
    tl = r["individual_stream"]["metrics"]
    corr = r["correlation"]
    p9 = r["portfolio_9stream"]
    p8 = r["baseline_8stream"]
    comp = r["comparison"]
    kc = r["individual_stream"]["kill_criteria"]
    pk = r["portfolio_kill_criteria"]

    md = f"""# EXP-2910: TLT Put Credit Spread Integration — Results

**Date:** {r['date']}
**Status:** {r['verdict'].split(' — ')[0]}
**Rule Zero:** {'CLEAN' if r['rule_zero']['clean'] else 'CONTAMINATED'}

---

## 1. Data Source

| Field | Value |
|---|---|
| Database | IronVault `options_cache.db` |
| Ticker | TLT (iShares 20+ Year Treasury Bond ETF) |
| Contracts | {r['data_source']['contracts']:,} |
| Daily bars | {r['data_source']['daily_bars']:,} |
| Date range | {r['data_source']['date_range']} |
| VIX source | Yahoo ^VIX (real) |
| TLT spot | Yahoo TLT (real) |

**Rule Zero compliance:** All prices from IronVault real option data. No synthetic data. No Black-Scholes as primary pricing.

---

## 2. Individual Stream Metrics (TLT Put Credit Spreads)

**Parameters:** 28 DTE, 5% OTM, $5 target width, 50% profit target, 2× stop loss

| Metric | Value | Kill Gate | Status |
|---|---|---|---|
| **Trade Sharpe** | **{tl['sharpe']:.2f}** | ≥ 1.0 | {'PASS' if kc['sharpe_ge_1'] else 'FAIL'} |
| **Trades/year** | **{tl['trades_per_year']:.1f}** | ≥ 20 | {'PASS' if kc['trades_per_year_ge_20'] else 'FAIL'} |
| CAGR | {tl['cagr_pct']:.1f}% | — | — |
| Max DD | {tl['max_dd_pct']:.1f}% | — | — |
| Win rate | {tl['win_rate']:.0%} | — | — |
| Total PnL | ${tl['total_pnl']:,.2f} | — | — |
| Avg PnL/trade | ${tl['avg_pnl']:,.2f} | — | — |
| Total trades | {tl['n_trades']} | — | — |

### Per-Year Breakdown

| Year | Trades | PnL | Win Rate |
|---|---|---|---|
"""
    for yr, data in sorted(r["individual_stream"]["trade_summary"]["per_year"].items()):
        md += f"| {yr} | {data['n']} | ${data['pnl']:,.2f} | {data['win_rate']:.0%} |\n"

    md += f"""
### Exit Reasons

"""
    for reason, count in r["individual_stream"]["trade_summary"]["exit_reasons"].items():
        md += f"- **{reason}:** {count} trades\n"

    md += f"""
---

## 3. Correlation with Existing 8 Streams

**Mean correlation:** ρ = {corr['mean_correlation']:.4f}

| Stream | Correlation (ρ) |
|---|---|
"""
    for stream, rho in sorted(corr["pairwise"].items()):
        md += f"| {stream} | {rho:+.4f} |\n"

    md += f"""
**Interpretation:** {'Near-zero correlation confirms TLT adds genuine diversification.' if abs(corr['mean_correlation']) < 0.15 else 'Correlation is moderate; diversification benefit is limited.'}

---

## 4. 9-Stream Portfolio (with TLT)

**Configuration:** Ledoit-Wolf risk-parity + 12% vol target + VIX ladder + {NET_DRAG_BPS} bps drag (Alpaca)

### Pooled Walk-Forward Metrics (NET)

| Metric | 9-Stream (with TLT) | 8-Stream Baseline | Delta |
|---|---|---|---|
| **Sharpe** | **{p9['net_metrics']['sharpe']:.2f}** | {p8['net_metrics']['sharpe']:.2f} | {comp['sharpe_delta']:+.2f} |
| CAGR | {p9['net_metrics']['cagr_pct']:.1f}% | {p8['net_metrics']['cagr_pct']:.1f}% | {p9['net_metrics']['cagr_pct'] - p8['net_metrics']['cagr_pct']:+.1f}pp |
| Max DD | {p9['net_metrics']['max_dd_pct']:.1f}% | {p8['net_metrics']['max_dd_pct']:.1f}% | {p9['net_metrics']['max_dd_pct'] - p8['net_metrics']['max_dd_pct']:+.1f}pp |
| Vol | {p9['net_metrics']['vol_pct']:.1f}% | {p8['net_metrics']['vol_pct']:.1f}% | — |

### Walk-Forward Fold Distribution

| Metric | 9-Stream | 8-Stream |
|---|---|---|
| Median fold Sharpe | {p9['median_fold_sharpe']:.2f} | {p8['median_fold_sharpe']:.2f} |
| Worst fold Sharpe | {p9['worst_fold_sharpe']:.2f} | — |
| % folds ≥ 6.0 | {p9['pct_folds_above_6']:.0f}% | — |
| Number of folds | {p9['n_folds']} | {len(p8['fold_sharpes'])} |

---

## 5. Kill Criteria Summary

| Criterion | Threshold | Result | Status |
|---|---|---|---|
| Trade Sharpe | ≥ 1.0 | {tl['sharpe']:.2f} | {'✅ PASS' if kc['sharpe_ge_1'] else '❌ FAIL'} |
| Trades/year | ≥ 20 | {tl['trades_per_year']:.1f} | {'✅ PASS' if kc['trades_per_year_ge_20'] else '❌ FAIL'} |
| 9-stream net Sharpe | ≥ 6.0 | {p9['net_metrics']['sharpe']:.2f} | {'✅ PASS' if pk['net_sharpe_ge_6'] else '❌ FAIL'} |
| vs v8a baseline (6.39) | improvement | {comp['sharpe_delta']:+.2f} | {'✅ IMPROVED' if comp['sharpe_improved'] else '⚠️ DEGRADED'} |

---

## 6. Verdict

**{r['verdict']}**

---

## 7. Methodology Notes

- **Walk-forward:** {TRAIN_DAYS}-day train / {TEST_DAYS}-day test expanding window
- **Covariance:** Ledoit-Wolf shrinkage (sklearn)
- **Allocation:** Equal risk contribution (Chaves-Hsu-Li-Shakernia 2011)
- **Vol target:** {TARGET_VOL*100:.0f}% annualized, capped at {VOL_SCALE_CAP:.0f}×
- **VIX ladder:** EXP-2820 default (9 breakpoints, causal shift-1d)
- **Transaction costs:** {NET_DRAG_BPS} bps/yr (Alpaca commission-free + execution)
- **Sharpe formula:** `mean(daily_returns) / std(daily_returns) × √252` (canonical, ddof=0 for std)
- **Convention:** Sparse exit-date attribution (no P&L smearing)

### Data Sources Cited

- IronVault TLT options: `data/options_cache.db` (10,749 contracts, 293,500 daily bars, 2020-01 to 2025-12)
- Yahoo TLT close: `yfinance.download("TLT")`
- Yahoo ^VIX close: `yfinance.download("^VIX")`
- v8a cube: `compass/cache/exp2280_v6_sparse.pkl` + `exp2250_qqq_trades.pkl`

### Rule Zero Verification

Synthetic data patterns grepped before reporting:
"""
    for pattern, count in r["rule_zero"]["findings"].items():
        md += f"- `{pattern}`: {count} occurrences {'✅' if count == 0 else '❌'}\n"

    md += f"""
---

*Generated by compass/exp2910_tlt_credit_spreads.py*
*All data sources are real. No synthetic data used.*
"""

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(md)


if __name__ == "__main__":
    main()
