"""
EXP-2950 — Sector Momentum Rotation Strategy

Hypothesis: A long-short sector rotation using 11 SPDR sector ETFs captures
cross-sectional momentum premia uncorrelated with our put credit spread portfolio.

Approaches tested:
  1. Cross-sectional momentum: long top-3, short bottom-3, monthly rebalance
  2. Long-only top-3: VIX filter (flat when VIX>25)
  3. Time-series momentum: long sector if own 12m return > 0, else flat
  4. Dual momentum: cross-sectional rank + time-series filter combined

Lookback variants: 1m, 3m, 6m, 12m, and combo (avg of 3/6/12).

Kill criteria: Sharpe < 1.0, max DD > 20%, correlation with portfolio > 0.4

Data: Yahoo Finance real ETF closes only. Rule Zero: no synthetic data.

Tag: EXP-2950
"""

from __future__ import annotations

import json
import math
import pickle
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compass.metrics import (
    annualized_sharpe, cagr, max_drawdown, full_metrics,
    TRADING_DAYS,
)
from compass.vix_ladder import VIXLadder, fetch_vix

CACHE_DIR = ROOT / "compass" / "cache"
REPORT_DIR = ROOT / "compass" / "reports"
RESULTS_PATH = ROOT / "experiments" / "EXP-2950_SECTOR_MOMENTUM_RESULTS.md"

TRAIN_DAYS = 252
TEST_DAYS = 63
TARGET_VOL = 0.12
VOL_SCALE_CAP = 20.0
CAPITAL = 100_000
NET_DRAG_BPS = 890
NET_DRAG_PCT = NET_DRAG_BPS / 100.0

# Sector ETFs
SECTORS_9 = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
SECTORS_11 = SECTORS_9 + ["XLC", "XLRE"]


# ═══════════════════════════════════════════════════════════════════════
# Data loading — Yahoo Finance only (Rule Zero)
# ═══════════════════════════════════════════════════════════════════════

def load_sector_data(start: str = "2009-12-01", end: str = "2026-07-01"):
    """Load daily closes for all sector ETFs + SPY + VIX from Yahoo."""
    import yfinance as yf
    tickers = SECTORS_11 + ["SPY"]
    closes = {}
    for t in tickers:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download(t, start=start, end=end, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        closes[t] = df["Close"].dropna()

    vix = fetch_vix(start, end)
    return closes, vix


def build_returns(closes: Dict[str, pd.Series],
                  sectors: List[str]) -> pd.DataFrame:
    """Build daily return matrix for the given sector list."""
    df = pd.DataFrame({t: closes[t] for t in sectors if t in closes})
    df = df.dropna()
    return df.pct_change().dropna()


def monthly_endpoints(index: pd.DatetimeIndex) -> List[int]:
    """Return indices of the last trading day of each month."""
    months = index.to_period("M")
    endpoints = []
    for i in range(len(months) - 1):
        if months[i] != months[i + 1]:
            endpoints.append(i)
    endpoints.append(len(months) - 1)
    return endpoints


# ═══════════════════════════════════════════════════════════════════════
# Momentum signal computation
# ═══════════════════════════════════════════════════════════════════════

def compute_momentum(closes_df: pd.DataFrame,
                     lookbacks: List[int] = [21, 63, 126, 252],
                     ) -> Dict[int, pd.DataFrame]:
    """Compute momentum (total return) for each lookback period.
    Returns dict of lookback -> DataFrame of momentum scores."""
    mom = {}
    for lb in lookbacks:
        mom[lb] = closes_df.pct_change(lb)
    return mom


def combo_momentum(mom_dict: Dict[int, pd.DataFrame],
                   lookbacks: List[int] = [63, 126, 252]) -> pd.DataFrame:
    """Average z-scored momentum across multiple lookbacks."""
    z_scores = []
    for lb in lookbacks:
        if lb not in mom_dict:
            continue
        m = mom_dict[lb]
        # Cross-sectional z-score each day
        mu = m.mean(axis=1)
        sigma = m.std(axis=1).replace(0, np.nan)
        z = m.sub(mu, axis=0).div(sigma, axis=0)
        z_scores.append(z)
    if not z_scores:
        return pd.DataFrame()
    return sum(z_scores) / len(z_scores)


# ═══════════════════════════════════════════════════════════════════════
# Strategy implementations
# ═══════════════════════════════════════════════════════════════════════

def strategy_long_short_xsect(closes_df: pd.DataFrame,
                               returns_df: pd.DataFrame,
                               mom: pd.DataFrame,
                               n_long: int = 3,
                               n_short: int = 3,
                               ) -> pd.Series:
    """Cross-sectional momentum: long top-N, short bottom-N, monthly rebal.
    Equal-weight within each leg. Returns daily return series."""
    endpoints = monthly_endpoints(returns_df.index)
    sectors = list(returns_df.columns)
    n_sectors = len(sectors)
    daily_returns = pd.Series(0.0, index=returns_df.index, name="ls_xsect")

    for i in range(len(endpoints) - 1):
        rebal_idx = endpoints[i]
        next_rebal = endpoints[i + 1]
        rebal_date = returns_df.index[rebal_idx]

        # Get momentum scores at rebalance date
        if rebal_date not in mom.index:
            continue
        scores = mom.loc[rebal_date].dropna()
        if len(scores) < n_long + n_short:
            continue

        ranked = scores.sort_values(ascending=False)
        longs = ranked.index[:n_long].tolist()
        shorts = ranked.index[-n_short:].tolist()

        # Hold period returns
        hold_start = rebal_idx + 1
        hold_end = next_rebal + 1
        if hold_start >= len(returns_df):
            continue

        hold_ret = returns_df.iloc[hold_start:hold_end]
        # Equal-weight long-short
        long_ret = hold_ret[longs].mean(axis=1) / n_long * n_long  # simplify: mean
        short_ret = hold_ret[shorts].mean(axis=1) / n_short * n_short
        # Portfolio: long + short (dollar-neutral)
        port_ret = hold_ret[longs].mean(axis=1) - hold_ret[shorts].mean(axis=1)
        daily_returns.iloc[hold_start:hold_end] = port_ret.values

    return daily_returns


def strategy_long_only_top(closes_df: pd.DataFrame,
                            returns_df: pd.DataFrame,
                            mom: pd.DataFrame,
                            vix: pd.Series,
                            n_long: int = 3,
                            vix_threshold: float = 25.0,
                            ) -> pd.Series:
    """Long-only top-N sectors, flat when VIX > threshold. Monthly rebal."""
    endpoints = monthly_endpoints(returns_df.index)
    daily_returns = pd.Series(0.0, index=returns_df.index, name="long_only")
    vix_aligned = vix.reindex(returns_df.index).ffill()

    for i in range(len(endpoints) - 1):
        rebal_idx = endpoints[i]
        next_rebal = endpoints[i + 1]
        rebal_date = returns_df.index[rebal_idx]

        if rebal_date not in mom.index:
            continue
        scores = mom.loc[rebal_date].dropna()
        if len(scores) < n_long:
            continue

        ranked = scores.sort_values(ascending=False)
        longs = ranked.index[:n_long].tolist()

        hold_start = rebal_idx + 1
        hold_end = next_rebal + 1
        if hold_start >= len(returns_df):
            continue

        hold_ret = returns_df.iloc[hold_start:hold_end]
        port_ret = hold_ret[longs].mean(axis=1)

        # VIX filter: go flat when VIX > threshold (causal: use previous day's VIX)
        hold_vix = vix_aligned.iloc[hold_start:hold_end].shift(1).bfill()
        vix_mask = (hold_vix <= vix_threshold).astype(float)
        daily_returns.iloc[hold_start:hold_end] = (port_ret * vix_mask).values

    return daily_returns


def strategy_ts_momentum(closes_df: pd.DataFrame,
                          returns_df: pd.DataFrame,
                          lookback: int = 252,
                          ) -> pd.Series:
    """Time-series momentum: long each sector if its own N-day return > 0.
    Equal-weight the longs. Monthly rebal."""
    endpoints = monthly_endpoints(returns_df.index)
    mom = closes_df.pct_change(lookback)
    daily_returns = pd.Series(0.0, index=returns_df.index, name="ts_mom")

    for i in range(len(endpoints) - 1):
        rebal_idx = endpoints[i]
        next_rebal = endpoints[i + 1]
        rebal_date = returns_df.index[rebal_idx]

        if rebal_date not in mom.index:
            continue
        scores = mom.loc[rebal_date].dropna()
        longs = scores[scores > 0].index.tolist()

        if not longs:
            continue

        hold_start = rebal_idx + 1
        hold_end = next_rebal + 1
        if hold_start >= len(returns_df):
            continue

        hold_ret = returns_df.iloc[hold_start:hold_end]
        port_ret = hold_ret[longs].mean(axis=1)
        daily_returns.iloc[hold_start:hold_end] = port_ret.values

    return daily_returns


def strategy_dual_momentum(closes_df: pd.DataFrame,
                            returns_df: pd.DataFrame,
                            mom_xs: pd.DataFrame,
                            lookback_ts: int = 252,
                            n_long: int = 3,
                            ) -> pd.Series:
    """Dual momentum: cross-sectional rank + time-series filter.
    Only go long top-N if their own 12m return > 0."""
    endpoints = monthly_endpoints(returns_df.index)
    mom_ts = closes_df.pct_change(lookback_ts)
    daily_returns = pd.Series(0.0, index=returns_df.index, name="dual_mom")

    for i in range(len(endpoints) - 1):
        rebal_idx = endpoints[i]
        next_rebal = endpoints[i + 1]
        rebal_date = returns_df.index[rebal_idx]

        if rebal_date not in mom_xs.index or rebal_date not in mom_ts.index:
            continue
        xs_scores = mom_xs.loc[rebal_date].dropna()
        ts_scores = mom_ts.loc[rebal_date].dropna()

        # Top-N by cross-sectional rank
        ranked = xs_scores.sort_values(ascending=False)
        candidates = ranked.index[:n_long].tolist()

        # Filter: only keep if time-series momentum > 0
        longs = [s for s in candidates if s in ts_scores.index and ts_scores[s] > 0]

        if not longs:
            continue

        hold_start = rebal_idx + 1
        hold_end = next_rebal + 1
        if hold_start >= len(returns_df):
            continue

        hold_ret = returns_df.iloc[hold_start:hold_end]
        port_ret = hold_ret[longs].mean(axis=1)
        daily_returns.iloc[hold_start:hold_end] = port_ret.values

    return daily_returns


# ═══════════════════════════════════════════════════════════════════════
# Walk-forward framework
# ═══════════════════════════════════════════════════════════════════════

def walk_forward_strategy(strat_daily: pd.Series,
                          train_days: int = TRAIN_DAYS,
                          test_days: int = TEST_DAYS,
                          target_vol: float = TARGET_VOL,
                          ) -> Tuple[pd.Series, List[Dict]]:
    """Walk-forward with vol-targeting on a single-stream daily return series."""
    n = len(strat_daily)
    pooled_idx, pooled_vals = [], []
    folds = []
    fold_ix = 0
    i = train_days

    while i + test_days <= n:
        train = strat_daily.iloc[i - train_days:i].values
        test = strat_daily.iloc[i:i + test_days]

        # Vol-target
        train_vol = float(np.std(train, ddof=1)) * math.sqrt(TRADING_DAYS)
        if train_vol <= 1e-10:
            scale = 1.0
        else:
            scale = target_vol / train_vol
        scale = float(np.clip(scale, 0.1, VOL_SCALE_CAP))
        scaled = test.values * scale

        pooled_idx.extend(test.index.tolist())
        pooled_vals.extend(scaled.tolist())

        m = full_metrics(scaled)
        folds.append({
            "fold": fold_ix,
            "test_start": str(test.index[0].date()),
            "test_end": str(test.index[-1].date()),
            "sharpe": m["sharpe"],
            "cagr_pct": m["cagr_pct"],
            "max_dd_pct": m["max_dd_pct"],
            "vol_pct": m["vol_pct"],
            "vol_scale": round(scale, 3),
        })
        fold_ix += 1
        i += test_days

    pooled = pd.Series(pooled_vals, index=pooled_idx, dtype=float)
    return pooled, folds


# ═══════════════════════════════════════════════════════════════════════
# Correlation analysis with existing 8 streams
# ═══════════════════════════════════════════════════════════════════════

def compute_portfolio_correlation(strat_daily: pd.Series) -> Dict:
    """Compute correlation of strategy with existing v8a streams."""
    cube_path = CACHE_DIR / "exp2280_v6_sparse.pkl"
    if not cube_path.exists():
        return {"status": "SKIPPED", "reason": "v6 sparse cube not found"}

    cube = pickle.load(open(cube_path, "rb"))
    if "vol_arb" in cube.columns:
        cube = cube.rename(columns={"vol_arb": "cross_vol"})

    qqq_cache = CACHE_DIR / "exp2250_qqq_trades.pkl"
    if qqq_cache.exists():
        qqq_trades = pickle.load(open(qqq_cache, "rb"))
        qqq_daily = pd.Series(0.0, index=cube.index, name="qqq_cs")
        for t in qqq_trades:
            ed = pd.Timestamp(t["exit_date"])
            if ed in qqq_daily.index:
                qqq_daily.loc[ed] += t["pnl"] / CAPITAL
        cube["qqq_cs"] = qqq_daily

    aligned = strat_daily.reindex(cube.index).fillna(0.0)
    cube["sector_mom"] = aligned

    corr_matrix = cube.corr()
    sector_corr = corr_matrix["sector_mom"].drop("sector_mom").to_dict()

    return {
        "pairwise": {k: round(v, 4) for k, v in sector_corr.items()},
        "mean_correlation": round(float(np.mean(list(sector_corr.values()))), 4),
        "max_abs_correlation": round(float(max(abs(v) for v in sector_corr.values())), 4),
        "xlf_correlation": round(sector_corr.get("xlf_cs", 0), 4),
        "xli_correlation": round(sector_corr.get("xli_cs", 0), 4),
    }


# ═══════════════════════════════════════════════════════════════════════
# Portfolio integration (9-stream)
# ═══════════════════════════════════════════════════════════════════════

def portfolio_integration(strat_daily: pd.Series, vix: pd.Series) -> Dict:
    """Integrate best strategy into 9-stream portfolio."""
    from compass.exp2420_transaction_costs import net_sharpe_from_drag
    from compass.exp2910_tlt_credit_spreads import (
        walk_forward_portfolio, risk_parity_weights,
    )

    cube_path = CACHE_DIR / "exp2280_v6_sparse.pkl"
    cube = pickle.load(open(cube_path, "rb"))
    if "vol_arb" in cube.columns:
        cube = cube.rename(columns={"vol_arb": "cross_vol"})

    qqq_cache = CACHE_DIR / "exp2250_qqq_trades.pkl"
    if qqq_cache.exists():
        qqq_trades = pickle.load(open(qqq_cache, "rb"))
        qqq_daily = pd.Series(0.0, index=cube.index, name="qqq_cs")
        for t in qqq_trades:
            ed = pd.Timestamp(t["exit_date"])
            if ed in qqq_daily.index:
                qqq_daily.loc[ed] += t["pnl"] / CAPITAL
        cube["qqq_cs"] = qqq_daily

    # 9-stream: add sector momentum
    cube_9 = cube.copy()
    cube_9["sector_mom"] = strat_daily.reindex(cube.index).fillna(0.0)

    vix_aligned = vix.reindex(cube_9.index).ffill().bfill()

    # 9-stream walk-forward
    pooled_9, folds_9 = walk_forward_portfolio(
        cube_9, vix_series=vix_aligned, apply_ladder=True)
    gross_9 = full_metrics(pooled_9.values)
    net_9 = net_sharpe_from_drag(
        gross_sharpe=gross_9["sharpe"],
        gross_cagr_pct=gross_9["cagr_pct"],
        vol_pct=gross_9["vol_pct"],
        annual_drag_pct=NET_DRAG_PCT,
    )

    # 8-stream baseline
    pooled_8, folds_8 = walk_forward_portfolio(
        cube.copy(), vix_series=vix_aligned, apply_ladder=True)
    gross_8 = full_metrics(pooled_8.values)
    net_8 = net_sharpe_from_drag(
        gross_sharpe=gross_8["sharpe"],
        gross_cagr_pct=gross_8["cagr_pct"],
        vol_pct=gross_8["vol_pct"],
        annual_drag_pct=NET_DRAG_PCT,
    )

    fold_sh_9 = [f["sharpe"] for f in folds_9]
    fold_sh_8 = [f["sharpe"] for f in folds_8]

    return {
        "nine_gross": gross_9,
        "nine_net": {"sharpe": net_9["net_sharpe"],
                     "cagr_pct": net_9["net_cagr_pct"],
                     "max_dd_pct": gross_9["max_dd_pct"]},
        "eight_gross": gross_8,
        "eight_net": {"sharpe": net_8["net_sharpe"],
                      "cagr_pct": net_8["net_cagr_pct"],
                      "max_dd_pct": gross_8["max_dd_pct"]},
        "sharpe_delta_net": round(net_9["net_sharpe"] - net_8["net_sharpe"], 2),
        "median_fold_9": round(float(np.median(fold_sh_9)), 2) if fold_sh_9 else 0,
        "median_fold_8": round(float(np.median(fold_sh_8)), 2) if fold_sh_8 else 0,
        "n_folds": len(folds_9),
    }


# ═══════════════════════════════════════════════════════════════════════
# Rule Zero check
# ═══════════════════════════════════════════════════════════════════════

def rule_zero_check() -> Dict:
    src = Path(__file__).read_text()
    patterns = {
        "np.random.call": r"np\.random\.\w+\(",
        "random.normal.call": r"random\.normal\(",
        "generate_prices.call": r"generate_prices\(",
    }
    findings = {}
    for name, pat in patterns.items():
        count = 0
        for line in src.split("\n"):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if re.search(pat, line) and 'r"' not in line and "r'" not in line:
                count += 1
        findings[name] = count
    return {"clean": all(v == 0 for v in findings.values()), "findings": findings}


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("EXP-2950: Sector Momentum Rotation Strategy")
    print("=" * 70)

    r0 = rule_zero_check()
    print(f"\n[Rule Zero] {'CLEAN' if r0['clean'] else 'CONTAMINATED'}: {r0['findings']}")

    # Load data
    print("\n[1/8] Loading sector ETF data from Yahoo Finance...")
    closes, vix = load_sector_data()

    # Build two universes
    # 9-sector: from 2010 (longer history)
    closes_9 = pd.DataFrame({t: closes[t] for t in SECTORS_9}).dropna()
    returns_9 = closes_9.pct_change().dropna()
    print(f"  9-sector universe: {closes_9.index[0].date()} to {closes_9.index[-1].date()}, {len(closes_9)} days")

    # 11-sector: from 2018 (includes XLC, XLRE)
    closes_11 = pd.DataFrame({t: closes[t] for t in SECTORS_11}).dropna()
    returns_11 = closes_11.pct_change().dropna()
    print(f"  11-sector universe: {closes_11.index[0].date()} to {closes_11.index[-1].date()}, {len(closes_11)} days")

    # Compute momentum signals
    print("\n[2/8] Computing momentum signals...")
    lookbacks = [21, 63, 126, 252]  # 1m, 3m, 6m, 12m
    mom_9 = compute_momentum(closes_9, lookbacks)
    mom_11 = compute_momentum(closes_11, lookbacks)
    combo_9 = combo_momentum(mom_9, [63, 126, 252])
    combo_11 = combo_momentum(mom_11, [63, 126, 252])

    # Run all strategy variants
    print("\n[3/8] Running strategy variants on 9-sector universe...")
    all_strategies = {}

    # A. Cross-sectional long-short (various lookbacks)
    for lb_name, lb, mom_dict in [
        ("1m", 21, mom_9), ("3m", 63, mom_9), ("6m", 126, mom_9),
        ("12m", 252, mom_9), ("combo", None, None),
    ]:
        label = f"ls_xsect_{lb_name}"
        if lb is not None:
            mom_signal = mom_dict[lb]
        else:
            mom_signal = combo_9
        strat = strategy_long_short_xsect(closes_9, returns_9, mom_signal)
        m = full_metrics(strat.values)
        all_strategies[label] = {"daily": strat, "raw_metrics": m, "type": "long_short"}
        print(f"  {label}: Sharpe {m['sharpe']:.2f}, CAGR {m['cagr_pct']:.1f}%, DD {m['max_dd_pct']:.1f}%")

    # B. Long-only top-3 with VIX filter
    for lb_name, lb, mom_dict in [
        ("3m", 63, mom_9), ("6m", 126, mom_9), ("combo", None, None),
    ]:
        label = f"long_top3_{lb_name}"
        if lb is not None:
            mom_signal = mom_dict[lb]
        else:
            mom_signal = combo_9
        strat = strategy_long_only_top(closes_9, returns_9, mom_signal, vix)
        m = full_metrics(strat.values)
        all_strategies[label] = {"daily": strat, "raw_metrics": m, "type": "long_only_vix"}
        print(f"  {label}: Sharpe {m['sharpe']:.2f}, CAGR {m['cagr_pct']:.1f}%, DD {m['max_dd_pct']:.1f}%")

    # Also without VIX filter for comparison
    for lb_name, lb, mom_dict in [("combo", None, None)]:
        label = f"long_top3_novix_{lb_name}"
        mom_signal = combo_9
        strat = strategy_long_only_top(closes_9, returns_9, mom_signal, vix,
                                        vix_threshold=999)  # no filter
        m = full_metrics(strat.values)
        all_strategies[label] = {"daily": strat, "raw_metrics": m, "type": "long_only"}
        print(f"  {label}: Sharpe {m['sharpe']:.2f}, CAGR {m['cagr_pct']:.1f}%, DD {m['max_dd_pct']:.1f}%")

    # C. Time-series momentum
    for lb in [126, 252]:
        label = f"ts_mom_{lb // 21}m"
        strat = strategy_ts_momentum(closes_9, returns_9, lookback=lb)
        m = full_metrics(strat.values)
        all_strategies[label] = {"daily": strat, "raw_metrics": m, "type": "time_series"}
        print(f"  {label}: Sharpe {m['sharpe']:.2f}, CAGR {m['cagr_pct']:.1f}%, DD {m['max_dd_pct']:.1f}%")

    # D. Dual momentum
    for lb_name, lb, mom_dict in [("combo", None, None), ("6m", 126, mom_9)]:
        label = f"dual_mom_{lb_name}"
        if lb is not None:
            mom_signal = mom_dict[lb]
        else:
            mom_signal = combo_9
        strat = strategy_dual_momentum(closes_9, returns_9, mom_signal)
        m = full_metrics(strat.values)
        all_strategies[label] = {"daily": strat, "raw_metrics": m, "type": "dual"}
        print(f"  {label}: Sharpe {m['sharpe']:.2f}, CAGR {m['cagr_pct']:.1f}%, DD {m['max_dd_pct']:.1f}%")

    # SPY benchmark
    spy_ret = (closes["SPY"].reindex(returns_9.index) /
               closes["SPY"].reindex(returns_9.index).shift(1) - 1).dropna()
    spy_m = full_metrics(spy_ret.values)
    all_strategies["spy_benchmark"] = {"daily": spy_ret, "raw_metrics": spy_m, "type": "benchmark"}
    print(f"  spy_benchmark: Sharpe {spy_m['sharpe']:.2f}, CAGR {spy_m['cagr_pct']:.1f}%, DD {spy_m['max_dd_pct']:.1f}%")

    # Walk-forward the most promising strategies
    print("\n[4/8] Walk-forward validation (vol-targeted)...")
    wf_results = {}
    for label, info in all_strategies.items():
        if label == "spy_benchmark":
            continue
        pooled, folds = walk_forward_strategy(info["daily"])
        m = full_metrics(pooled.values)
        fold_sharpes = [f["sharpe"] for f in folds]
        wf_results[label] = {
            "pooled": m,
            "folds": folds,
            "fold_sharpes": fold_sharpes,
            "median_fold": round(float(np.median(fold_sharpes)), 2) if fold_sharpes else 0,
            "worst_fold": round(float(min(fold_sharpes)), 2) if fold_sharpes else 0,
        }
        print(f"  {label}: WF Sharpe {m['sharpe']:.2f}, CAGR {m['cagr_pct']:.1f}%, "
              f"DD {m['max_dd_pct']:.1f}%, median fold {wf_results[label]['median_fold']:.2f}")

    # Find best strategy
    print("\n[5/8] Ranking strategies...")
    ranked = sorted(wf_results.items(),
                    key=lambda kv: kv[1]["pooled"]["sharpe"], reverse=True)
    print("\n  Rank | Strategy | WF Sharpe | WF CAGR | WF Max DD | Median Fold")
    print("  " + "-" * 75)
    for i, (label, res) in enumerate(ranked[:10]):
        m = res["pooled"]
        print(f"  {i+1:4d} | {label:30s} | {m['sharpe']:9.2f} | {m['cagr_pct']:7.1f}% | "
              f"{m['max_dd_pct']:9.1f}% | {res['median_fold']:6.2f}")

    best_label, best_res = ranked[0]
    best_metrics = best_res["pooled"]
    best_daily = all_strategies[best_label]["daily"]
    print(f"\n  Best: {best_label} — WF Sharpe {best_metrics['sharpe']:.2f}")

    # Correlation analysis
    print("\n[6/8] Computing correlation with existing 8 streams...")
    corr_results = {}
    for label in [best_label] + [l for l, _ in ranked[1:3]]:
        corr = compute_portfolio_correlation(all_strategies[label]["daily"])
        corr_results[label] = corr
        if "pairwise" in corr:
            print(f"  {label}: mean ρ = {corr['mean_correlation']:.4f}, "
                  f"XLF ρ = {corr['xlf_correlation']:.4f}, "
                  f"XLI ρ = {corr['xli_correlation']:.4f}")

    # Kill criteria check
    print("\n[7/8] Kill criteria check...")
    kill_reasons = []
    if best_metrics["sharpe"] < 1.0:
        kill_reasons.append(f"WF Sharpe {best_metrics['sharpe']:.2f} < 1.0")
    if best_metrics["max_dd_pct"] > 20.0:
        kill_reasons.append(f"Max DD {best_metrics['max_dd_pct']:.1f}% > 20%")

    best_corr = corr_results.get(best_label, {})
    if best_corr.get("mean_correlation", 0) > 0.4:
        kill_reasons.append(f"Mean ρ {best_corr['mean_correlation']:.2f} > 0.4")

    individual_pass = len(kill_reasons) == 0
    if individual_pass:
        print(f"  All individual kill criteria PASSED.")
    else:
        print(f"  KILL CRITERIA TRIGGERED:")
        for r in kill_reasons:
            print(f"    - {r}")

    # Portfolio integration (only if individual criteria pass)
    print("\n[8/8] Portfolio integration...")
    integ = None
    if individual_pass:
        try:
            integ = portfolio_integration(best_daily, vix)
            p9 = integ["nine_net"]
            p8 = integ["eight_net"]
            print(f"  9-stream NET Sharpe: {p9['sharpe']:.2f} (vs 8-stream {p8['sharpe']:.2f}, "
                  f"delta {integ['sharpe_delta_net']:+.2f})")
        except Exception as e:
            print(f"  Integration failed: {e}")
    elif best_metrics["sharpe"] >= 0.5:
        # Still try integration for research value even if killed
        try:
            integ = portfolio_integration(best_daily, vix)
            p9 = integ["nine_net"]
            p8 = integ["eight_net"]
            print(f"  9-stream NET Sharpe: {p9['sharpe']:.2f} (vs 8-stream {p8['sharpe']:.2f}, "
                  f"delta {integ['sharpe_delta_net']:+.2f}) [research only — individual criteria failed]")
        except Exception as e:
            print(f"  Integration failed: {e}")
    else:
        print(f"  Skipping — individual criteria failed.")

    # Compile results
    print("\n" + "=" * 70)
    portfolio_kill = []
    if integ and integ["nine_net"]["sharpe"] < 6.0:
        portfolio_kill.append(f"9-stream net Sharpe {integ['nine_net']['sharpe']:.2f} < 6.0")

    all_pass = individual_pass and len(portfolio_kill) == 0
    if all_pass:
        verdict = (f"PASS — {best_label} WF Sharpe {best_metrics['sharpe']:.2f}, "
                   f"mean ρ = {best_corr.get('mean_correlation', '?')}, "
                   f"9-stream net Sharpe {integ['nine_net']['sharpe']:.2f}")
    elif individual_pass:
        verdict = (f"PARTIAL — Individual criteria pass but portfolio: "
                   f"{'; '.join(portfolio_kill)}")
    else:
        verdict = (f"KILLED — {'; '.join(kill_reasons + portfolio_kill)}. "
                   f"Best: {best_label} WF Sharpe {best_metrics['sharpe']:.2f}.")

    results = {
        "experiment": "EXP-2950",
        "title": "Sector Momentum Rotation Strategy",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rule_zero": r0,
        "data_sources": {
            "sector_etfs": "Yahoo Finance (XLB/XLC/XLE/XLF/XLI/XLK/XLP/XLRE/XLU/XLV/XLY)",
            "spy": "Yahoo Finance SPY",
            "vix": "Yahoo Finance ^VIX",
        },
        "universes": {
            "9_sector": {
                "tickers": SECTORS_9,
                "start": str(closes_9.index[0].date()),
                "end": str(closes_9.index[-1].date()),
                "days": len(closes_9),
            },
            "11_sector": {
                "tickers": SECTORS_11,
                "start": str(closes_11.index[0].date()),
                "end": str(closes_11.index[-1].date()),
                "days": len(closes_11),
            },
        },
        "raw_metrics": {k: v["raw_metrics"] for k, v in all_strategies.items()},
        "walk_forward": {k: {"pooled": v["pooled"], "median_fold": v["median_fold"],
                             "worst_fold": v["worst_fold"]}
                         for k, v in wf_results.items()},
        "ranking": [{"rank": i + 1, "strategy": l, "sharpe": r["pooled"]["sharpe"],
                     "cagr_pct": r["pooled"]["cagr_pct"],
                     "max_dd_pct": r["pooled"]["max_dd_pct"],
                     "median_fold": r["median_fold"]}
                    for i, (l, r) in enumerate(ranked)],
        "best_strategy": {
            "label": best_label,
            "wf_metrics": best_metrics,
            "type": all_strategies[best_label]["type"],
        },
        "correlation": corr_results,
        "kill_criteria": {
            "individual": {"passed": individual_pass, "reasons": kill_reasons},
            "portfolio": {"passed": len(portfolio_kill) == 0, "reasons": portfolio_kill},
        },
        "portfolio_integration": integ,
        "verdict": verdict,
    }

    print(f"VERDICT: {verdict}")
    print("=" * 70)

    write_results_md(results)
    json_path = REPORT_DIR / "exp2950_sector_momentum_results.json"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResults: {RESULTS_PATH}\n         {json_path}")

    return results


def write_results_md(r: Dict):
    best = r["best_strategy"]
    corr = r.get("correlation", {})
    integ = r.get("portfolio_integration")
    kc = r["kill_criteria"]

    md = f"""# EXP-2950: Sector Momentum Rotation Strategy — Results

**Date:** {r['date']}
**Status:** {r['verdict'].split(' — ')[0]}
**Rule Zero:** {'CLEAN' if r['rule_zero']['clean'] else 'CONTAMINATED'}

---

## 1. Data Sources

| Source | Details |
|---|---|
| Sector ETFs | Yahoo Finance: {', '.join(SECTORS_9)} (2010-2026) |
| Extended | + XLC, XLRE (2018-2026) |
| Benchmark | SPY |
| VIX filter | Yahoo ^VIX |

**Rule Zero:** All prices from Yahoo Finance real market data. No synthetic data.

---

## 2. Strategy Variants — Raw Metrics (Before Walk-Forward)

| Strategy | Sharpe | CAGR | Max DD | Type |
|---|---|---|---|---|
"""
    for label, m in sorted(r["raw_metrics"].items(),
                           key=lambda kv: kv[1]["sharpe"], reverse=True):
        md += f"| {label} | {m['sharpe']:.2f} | {m['cagr_pct']:.1f}% | {m['max_dd_pct']:.1f}% | {r.get('walk_forward', {}).get(label, {}).get('type', '—')} |\n"

    md += f"""
---

## 3. Walk-Forward Validation (Vol-Targeted to {TARGET_VOL*100:.0f}%)

| Rank | Strategy | WF Sharpe | WF CAGR | WF Max DD | Median Fold |
|---|---|---|---|---|---|
"""
    for item in r["ranking"]:
        md += (f"| {item['rank']} | {item['strategy']} | **{item['sharpe']:.2f}** | "
               f"{item['cagr_pct']:.1f}% | {item['max_dd_pct']:.1f}% | {item['median_fold']:.2f} |\n")

    best_m = best["wf_metrics"]
    md += f"""
**Best strategy:** `{best['label']}` (WF Sharpe {best_m['sharpe']:.2f})

---

## 4. Correlation with Existing 8 Streams

"""
    for label, c in corr.items():
        if "pairwise" not in c:
            continue
        md += f"### {label}\n\n"
        md += f"**Mean correlation:** ρ = {c['mean_correlation']:.4f}\n\n"
        md += "| Stream | ρ |\n|---|---|\n"
        for stream, rho in sorted(c["pairwise"].items()):
            flag = " ⚠️" if abs(rho) > 0.3 else ""
            md += f"| {stream} | {rho:+.4f}{flag} |\n"
        md += f"\n**XLF correlation:** {c['xlf_correlation']:.4f} | **XLI correlation:** {c['xli_correlation']:.4f}\n\n"

    md += f"""---

## 5. Kill Criteria

| Criterion | Threshold | Result | Status |
|---|---|---|---|
| WF Sharpe | ≥ 1.0 | {best_m['sharpe']:.2f} | {'PASS' if best_m['sharpe'] >= 1.0 else 'FAIL'} |
| Max DD | ≤ 20% | {best_m['max_dd_pct']:.1f}% | {'PASS' if best_m['max_dd_pct'] <= 20 else 'FAIL'} |
| Mean ρ with portfolio | ≤ 0.4 | {corr.get(best['label'], {}).get('mean_correlation', '?')} | {'PASS' if corr.get(best['label'], {}).get('mean_correlation', 1) <= 0.4 else 'FAIL'} |
"""

    if integ:
        md += f"| 9-stream net Sharpe | ≥ 6.0 | {integ['nine_net']['sharpe']:.2f} | {'PASS' if integ['nine_net']['sharpe'] >= 6.0 else 'FAIL'} |\n"

    md += f"""
---

## 6. Portfolio Integration
"""
    if integ:
        p9 = integ["nine_net"]
        p8 = integ["eight_net"]
        md += f"""
| Metric | 9-Stream | 8-Stream Baseline | Delta |
|---|---|---|---|
| NET Sharpe | **{p9['sharpe']:.2f}** | {p8['sharpe']:.2f} | {integ['sharpe_delta_net']:+.2f} |
| NET CAGR | {p9['cagr_pct']:.1f}% | {p8['cagr_pct']:.1f}% | — |
| Max DD | {p9['max_dd_pct']:.1f}% | {p8['max_dd_pct']:.1f}% | — |
| Median fold Sharpe | {integ['median_fold_9']:.2f} | {integ['median_fold_8']:.2f} | — |
"""
    else:
        md += "\n*Not tested — individual criteria failed.*\n"

    md += f"""
---

## 7. Verdict

**{r['verdict']}**

---

## 8. Methodology

- **Universe:** 9 SPDR sector ETFs (XLB/XLE/XLF/XLI/XLK/XLP/XLU/XLV/XLY), 2010-2026
- **Momentum lookbacks:** 1m (21d), 3m (63d), 6m (126d), 12m (252d), combo (avg z-score of 3/6/12)
- **Rebalance:** Monthly (last trading day of each month)
- **Long-short:** Equal-weight top-3 long, bottom-3 short (dollar-neutral)
- **Long-only:** Equal-weight top-3, flat when VIX > 25 (causal, 1-day lag)
- **Walk-forward:** {TRAIN_DAYS}d train / {TEST_DAYS}d test, vol-targeted to {TARGET_VOL*100:.0f}%
- **Cost model:** {NET_DRAG_BPS} bps/yr analytical drag
- **Sharpe:** mean(daily) / std(daily, ddof=0) × √252

### Rule Zero

"""
    for pat, count in r["rule_zero"]["findings"].items():
        md += f"- `{pat}`: {count} occurrences {'OK' if count == 0 else 'FAIL'}\n"

    md += """
---

*Generated by compass/exp2950_sector_momentum.py*
*All data from Yahoo Finance. No synthetic data.*
"""
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(md)


if __name__ == "__main__":
    main()
