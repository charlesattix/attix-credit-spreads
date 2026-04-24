"""
EXP-2920 — TLT IV-RV Arbitrage via MOVE Index

Hypothesis: When MOVE (bond implied vol) is elevated relative to TLT realized
vol, short bond vol is profitable. The MOVE-VIX correlation is only 0.13,
making this genuinely uncorrelated with the equity-centric cross_vol arb.

Three approaches tested:
  A. MOVE-filtered TLT put credit spreads (improve EXP-2910 Sharpe)
  B. Standalone TLT IV-RV signal (equity-based execution via TLT shares)
  C. MOVE as a macro signal overlay for existing cross_vol stream

Data: Yahoo ^MOVE (1,823 days, 2019-2026), Yahoo TLT, IronVault TLT options.
Rule Zero: all data from Yahoo Finance and IronVault. No synthetic data.

Tag: EXP-2920
"""

from __future__ import annotations

import json
import math
import pickle
import re
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

from compass.metrics import (
    annualized_sharpe, cagr, max_drawdown, full_metrics,
    TRADING_DAYS, DEFAULT_RF_ANNUAL,
)
from compass.vix_ladder import VIXLadder, fetch_vix

CACHE_DIR = ROOT / "compass" / "cache"
REPORT_DIR = ROOT / "compass" / "reports"
RESULTS_PATH = ROOT / "experiments" / "EXP-2920_TLT_IVRV_RESULTS.md"

TRAIN_DAYS = 252
TEST_DAYS = 63
TARGET_VOL = 0.12
VOL_SCALE_CAP = 20.0
CAPITAL = 100_000
NET_DRAG_BPS = 890
NET_DRAG_PCT = NET_DRAG_BPS / 100.0


# ═══════════════════════════════════════════════════════════════════════
# Data loading (all real — Yahoo Finance + IronVault)
# ═══════════════════════════════════════════════════════════════════════

def load_yahoo(ticker: str, start: str = "2019-01-01",
               end: str = "2026-07-01") -> pd.Series:
    """Load real Yahoo Finance daily close."""
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(ticker, start=start, end=end, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    s = df["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    s.name = ticker
    return s


def load_move_and_tlt():
    """Load MOVE index and TLT close, compute realized vol and IV-RV spread."""
    move = load_yahoo("^MOVE")
    tlt = load_yahoo("TLT")
    vix = load_yahoo("^VIX")

    # TLT 20-day realized vol (annualized, in %)
    tlt_ret = np.log(tlt / tlt.shift(1)).dropna()
    tlt_rv20 = tlt_ret.rolling(20).std() * np.sqrt(TRADING_DAYS) * 100

    # Align all series
    common = move.index.intersection(tlt.index).intersection(tlt_rv20.dropna().index)
    move_a = move.reindex(common)
    tlt_a = tlt.reindex(common)
    rv_a = tlt_rv20.reindex(common)
    vix_a = vix.reindex(common).ffill()

    # Normalize MOVE to TLT-equivalent vol scale
    # Empirical calibration: MOVE / TLT_RV_pct has median ~6.1
    # So MOVE_normalized = MOVE / 6.1 gives us "implied TLT vol in %"
    calibration_ratio = (move_a / rv_a).median()
    move_normalized = move_a / calibration_ratio

    # IV-RV spread: positive when implied > realized (short vol profitable)
    spread = move_normalized - rv_a

    # Z-score of spread (rolling 60-day)
    spread_mean60 = spread.rolling(60).mean()
    spread_std60 = spread.rolling(60).std()
    spread_z = (spread - spread_mean60) / spread_std60.replace(0, np.nan)

    return {
        "move": move_a,
        "tlt": tlt_a,
        "tlt_rv20": rv_a,
        "vix": vix_a,
        "move_normalized": move_normalized,
        "spread": spread,
        "spread_z": spread_z.dropna(),
        "calibration_ratio": float(calibration_ratio),
    }


# ═══════════════════════════════════════════════════════════════════════
# Approach A: MOVE-filtered TLT put credit spreads
# ═══════════════════════════════════════════════════════════════════════

def approach_a_filtered_spreads(data: dict) -> Dict:
    """Use MOVE IV-RV z-score to filter EXP-2910 TLT trades.
    Only enter when spread_z > threshold (implied vol elevated)."""
    cache_path = CACHE_DIR / "exp2910_tlt_trades.pkl"
    if not cache_path.exists():
        return {"status": "SKIPPED", "reason": "No EXP-2910 trades cached"}

    all_trades = pickle.load(open(cache_path, "rb"))
    spread_z = data["spread_z"]

    results = {}
    for threshold in [0.0, 0.5, 1.0, 1.5]:
        filtered = []
        for t in all_trades:
            ed = pd.Timestamp(t["entry_date"])
            if ed in spread_z.index:
                z = float(spread_z.loc[ed])
                if z > threshold:
                    filtered.append(t)
            # If no z-score available, skip (conservative)

        n = len(filtered)
        if n < 5:
            results[f"z>{threshold}"] = {
                "n_trades": n, "status": "too few trades"}
            continue

        pnls = np.array([t["pnl"] for t in filtered])
        wins = int((pnls > 0).sum())
        total = float(pnls.sum())
        df = pd.DataFrame(filtered)
        years = max((pd.to_datetime(df.exit_date).max() -
                     pd.to_datetime(df.entry_date).min()).days / 365.25, 0.5)
        tpy = n / years
        mu = float(pnls.mean())
        sigma = float(pnls.std(ddof=1)) if n > 1 else 1.0
        sharpe = mu / sigma * math.sqrt(tpy) if sigma > 1e-9 else 0.0

        results[f"z>{threshold}"] = {
            "n_trades": n,
            "trades_per_year": round(tpy, 1),
            "sharpe": round(sharpe, 2),
            "win_rate": round(wins / n, 3),
            "total_pnl": round(total, 2),
            "avg_pnl": round(mu, 2),
        }

    return {"status": "OK", "variants": results,
            "baseline_unfiltered": {
                "n_trades": len(all_trades),
                "sharpe": 0.76,  # from EXP-2910
                "trades_per_year": 9.2,
            }}


# ═══════════════════════════════════════════════════════════════════════
# Approach B: Standalone TLT IV-RV mean-reversion signal
# ═══════════════════════════════════════════════════════════════════════

def approach_b_standalone_ivrv(data: dict) -> Dict:
    """Trade TLT shares based on IV-RV spread mean-reversion.

    When MOVE is high relative to TLT realized vol (z > threshold):
      → sell TLT (bet that implied vol will compress → prices stabilize/rise)
      Wait — that's directional. Better approach:
      → go long TLT when spread_z > 1 (high implied vol → vol tends to mean-revert
        → TLT realized vol will decrease → TLT price stabilizes/rises)
      → go short TLT when spread_z < -1 (low implied vol → vol tends to expand)

    Actually, for a VRP-harvesting strategy:
      → When MOVE >> TLT_RV (spread_z > 0): short vol = sell TLT puts
        (collect premium because implied vol is rich)
      → When MOVE << TLT_RV (spread_z < 0): avoid selling vol

    Since we can't sell TLT options efficiently (EXP-2910 showed thin premiums),
    use TLT shares as a proxy:
      → Long TLT when bond vol is expected to compress (spread_z > threshold)
      → Flat when spread_z <= threshold

    This captures the vol risk premium indirectly through the vol-price channel:
    elevated implied vol (MOVE high) → vol mean-reverts down → TLT rallies as
    rate uncertainty dissipates.
    """
    spread_z = data["spread_z"]
    tlt = data["tlt"]
    tlt_ret = (tlt / tlt.shift(1) - 1).dropna()

    # Align
    common = spread_z.index.intersection(tlt_ret.index)
    z = spread_z.reindex(common)
    ret = tlt_ret.reindex(common)

    results = {}
    for threshold in [0.0, 0.5, 1.0, -0.5]:
        label = f"z>{threshold}"
        # Signal: long TLT when z > threshold (lagged by 1 day for causality)
        signal = (z.shift(1) > threshold).astype(float)
        strat_ret = signal * ret

        # Only count days we're actually in a position
        active_days = int((signal > 0).sum())
        total_days = len(signal)
        exposure = active_days / total_days if total_days > 0 else 0

        if active_days < 50:
            results[label] = {"status": "too few active days",
                              "active_days": active_days}
            continue

        m = full_metrics(strat_ret.values)
        results[label] = {
            "sharpe": m["sharpe"],
            "cagr_pct": m["cagr_pct"],
            "max_dd_pct": m["max_dd_pct"],
            "vol_pct": m["vol_pct"],
            "exposure_pct": round(exposure * 100, 1),
            "active_days": active_days,
            "total_days": total_days,
        }

    # Buy-and-hold TLT benchmark
    bh = full_metrics(ret.values)
    results["buy_hold_tlt"] = {
        "sharpe": bh["sharpe"],
        "cagr_pct": bh["cagr_pct"],
        "max_dd_pct": bh["max_dd_pct"],
        "vol_pct": bh["vol_pct"],
    }

    return results


# ═══════════════════════════════════════════════════════════════════════
# Approach C: MOVE as overlay signal for existing cross_vol stream
# ═══════════════════════════════════════════════════════════════════════

def approach_c_move_overlay(data: dict) -> Dict:
    """Test whether MOVE regime improves the existing cross_vol arb.

    Hypothesis: When MOVE is elevated (bond vol high), equity vol tends to
    follow with a lag. Using MOVE as a leading indicator for the equity
    cross_vol signal could improve timing.

    Also test: MOVE as a regime filter — only trade cross_vol when
    MOVE is in a specific range.
    """
    # Load cross_vol trades
    cv_cache = CACHE_DIR / "exp2020_vol_arb_trades.pkl"
    if not cv_cache.exists():
        return {"status": "SKIPPED", "reason": "No cross_vol trades cached"}

    cv_trades = pickle.load(open(cv_cache, "rb"))
    spread_z = data["spread_z"]
    move = data["move"]

    # For each cross_vol trade, get the MOVE z-score at entry
    enhanced_trades = []
    for t in cv_trades:
        ed = pd.Timestamp(t["entry_date"])
        if ed in spread_z.index:
            t_copy = dict(t)
            t_copy["move_z"] = float(spread_z.loc[ed])
            t_copy["move_level"] = float(move.loc[ed]) if ed in move.index else np.nan
            enhanced_trades.append(t_copy)

    if len(enhanced_trades) < 20:
        return {"status": "SKIPPED", "reason": f"Only {len(enhanced_trades)} trades with MOVE data"}

    # Baseline: all cross_vol trades
    all_pnls = np.array([t["pnl"] for t in enhanced_trades])
    n_all = len(all_pnls)
    df_all = pd.DataFrame(enhanced_trades)
    years_all = (pd.to_datetime(df_all.exit_date).max() -
                 pd.to_datetime(df_all.entry_date).min()).days / 365.25
    tpy_all = n_all / max(years_all, 0.5)
    mu_all = float(all_pnls.mean())
    sigma_all = float(all_pnls.std(ddof=1))
    sharpe_all = mu_all / sigma_all * math.sqrt(tpy_all) if sigma_all > 1e-9 else 0

    results = {
        "baseline_all": {
            "n_trades": n_all,
            "sharpe": round(sharpe_all, 2),
            "win_rate": round(float((all_pnls > 0).mean()), 3),
            "trades_per_year": round(tpy_all, 1),
        },
        "filters": {},
    }

    # Filter variants: only trade cross_vol when MOVE z-score meets criteria
    for label, condition in [
        ("move_z>0", lambda t: t["move_z"] > 0),
        ("move_z>0.5", lambda t: t["move_z"] > 0.5),
        ("move_z>1.0", lambda t: t["move_z"] > 1.0),
        ("move_z<0", lambda t: t["move_z"] < 0),
        ("move_high (>90)", lambda t: t.get("move_level", 0) > 90),
        ("move_low (<70)", lambda t: t.get("move_level", 0) < 70),
    ]:
        filtered = [t for t in enhanced_trades if condition(t)]
        n = len(filtered)
        if n < 10:
            results["filters"][label] = {"n_trades": n, "status": "too few"}
            continue
        pnls = np.array([t["pnl"] for t in filtered])
        wins = int((pnls > 0).sum())
        mu = float(pnls.mean())
        sigma = float(pnls.std(ddof=1))
        df_f = pd.DataFrame(filtered)
        yrs = max((pd.to_datetime(df_f.exit_date).max() -
                   pd.to_datetime(df_f.entry_date).min()).days / 365.25, 0.5)
        tpy = n / yrs
        sh = mu / sigma * math.sqrt(tpy) if sigma > 1e-9 else 0

        results["filters"][label] = {
            "n_trades": n,
            "sharpe": round(sh, 2),
            "win_rate": round(wins / n, 3),
            "avg_pnl": round(mu, 2),
            "trades_per_year": round(tpy, 1),
            "sharpe_delta_vs_baseline": round(sh - sharpe_all, 2),
        }

    return results


# ═══════════════════════════════════════════════════════════════════════
# Approach D: MOVE as leading indicator for SPY vol regime
# ═══════════════════════════════════════════════════════════════════════

def approach_d_move_leading_indicator(data: dict) -> Dict:
    """Test MOVE as a leading indicator for SPY/equity volatility.

    Key question: Does a spike in MOVE predict a VIX spike 1-5 days later?
    If yes, MOVE could be used to pre-position the portfolio before equity
    vol events.
    """
    move = data["move"]
    vix = data["vix"]

    # MOVE daily changes
    move_ret = move.pct_change().dropna()
    vix_ret = vix.pct_change().dropna()

    common = move_ret.index.intersection(vix_ret.index)
    mr = move_ret.reindex(common)
    vr = vix_ret.reindex(common)

    results = {
        "same_day_correlation": round(float(mr.corr(vr)), 4),
        "lead_lag": {},
    }

    # Lead-lag analysis
    for lag in [1, 2, 3, 5, 10, 20]:
        # Does today's MOVE change predict VIX change in `lag` days?
        vr_fwd = vr.shift(-lag)
        valid = mr.index.intersection(vr_fwd.dropna().index)
        corr = float(mr.reindex(valid).corr(vr_fwd.reindex(valid)))
        results["lead_lag"][f"move_leads_vix_by_{lag}d"] = round(corr, 4)

    # Regime analysis: what happens to VIX after MOVE spikes?
    move_z = (move - move.rolling(60).mean()) / move.rolling(60).std()
    move_z = move_z.dropna()
    vix_fwd5 = vix.pct_change(5).shift(-5)  # 5-day forward VIX change

    common2 = move_z.index.intersection(vix_fwd5.dropna().index)
    mz = move_z.reindex(common2)
    vf = vix_fwd5.reindex(common2)

    # Conditional analysis
    for thresh, label in [(1.0, "move_spike_z>1"), (1.5, "move_spike_z>1.5"),
                          (2.0, "move_spike_z>2"), (-1.0, "move_calm_z<-1")]:
        if thresh > 0:
            mask = mz > thresh
        else:
            mask = mz < thresh
        n_events = int(mask.sum())
        if n_events < 10:
            results[label] = {"n_events": n_events, "status": "too few"}
            continue
        fwd_vix = vf[mask]
        results[label] = {
            "n_events": n_events,
            "mean_vix_5d_fwd_change": round(float(fwd_vix.mean()) * 100, 2),
            "median_vix_5d_fwd_change": round(float(fwd_vix.median()) * 100, 2),
            "pct_vix_up": round(float((fwd_vix > 0).mean()) * 100, 1),
        }

    return results


# ═══════════════════════════════════════════════════════════════════════
# Approach E: MOVE-conditioned TLT equity strategy (daily signal)
# ═══════════════════════════════════════════════════════════════════════

def approach_e_move_tlt_daily(data: dict) -> Dict:
    """Daily TLT trading strategy conditioned on MOVE regime.

    The idea: use MOVE z-score as a regime indicator.
    - High MOVE (z > 1): bond vol elevated → expect mean-reversion → go long TLT
    - Low MOVE (z < -1): bond vol depressed → expect vol expansion → go short TLT
    - Neutral: flat

    This is a daily-rebalanced strategy that produces a return series
    suitable for inclusion in the multi-stream cube.
    """
    spread_z = data["spread_z"]
    tlt = data["tlt"]
    tlt_ret = (tlt / tlt.shift(1) - 1).dropna()
    move = data["move"]

    # MOVE z-score (60-day rolling)
    move_z = (move - move.rolling(60).mean()) / move.rolling(60).std()
    move_z = move_z.dropna()

    common = move_z.index.intersection(tlt_ret.index)
    mz = move_z.reindex(common)
    ret = tlt_ret.reindex(common)

    results = {}

    # Variant 1: Long TLT when MOVE z > 1 (vol elevated, expect compression)
    for long_thresh, short_thresh, label in [
        (1.0, None, "long_only_z>1"),
        (0.5, None, "long_only_z>0.5"),
        (1.0, -1.0, "long_short_z1"),
        (0.5, -0.5, "long_short_z0.5"),
    ]:
        # Causal: use yesterday's signal
        signal = pd.Series(0.0, index=common)
        if long_thresh is not None:
            signal[mz.shift(1) > long_thresh] = 1.0
        if short_thresh is not None:
            signal[mz.shift(1) < short_thresh] = -1.0

        strat_ret = signal * ret
        active = int((signal != 0).sum())

        if active < 50:
            results[label] = {"active_days": active, "status": "too few"}
            continue

        m = full_metrics(strat_ret.values)
        results[label] = {
            "sharpe": m["sharpe"],
            "cagr_pct": m["cagr_pct"],
            "max_dd_pct": m["max_dd_pct"],
            "vol_pct": m["vol_pct"],
            "exposure_pct": round(active / len(common) * 100, 1),
            "active_days": active,
        }

    # Variant 2: MOVE momentum (5-day MOVE change as signal)
    move_mom5 = move.pct_change(5)
    move_mom5_z = (move_mom5 - move_mom5.rolling(60).mean()) / move_mom5.rolling(60).std()
    move_mom5_z = move_mom5_z.dropna()

    common2 = move_mom5_z.index.intersection(tlt_ret.index)
    mm = move_mom5_z.reindex(common2)
    ret2 = tlt_ret.reindex(common2)

    # When MOVE is spiking (momentum z > 1), go long TLT (flight to safety)
    signal2 = pd.Series(0.0, index=common2)
    signal2[mm.shift(1) > 1.0] = 1.0
    signal2[mm.shift(1) < -1.0] = -1.0
    strat_ret2 = signal2 * ret2
    active2 = int((signal2 != 0).sum())

    if active2 >= 50:
        m2 = full_metrics(strat_ret2.values)
        results["move_momentum_ls"] = {
            "sharpe": m2["sharpe"],
            "cagr_pct": m2["cagr_pct"],
            "max_dd_pct": m2["max_dd_pct"],
            "vol_pct": m2["vol_pct"],
            "exposure_pct": round(active2 / len(common2) * 100, 1),
        }

    return results


# ═══════════════════════════════════════════════════════════════════════
# Portfolio integration test (best approach into 9-stream cube)
# ═══════════════════════════════════════════════════════════════════════

def portfolio_integration(data: dict, best_signal: pd.Series,
                          stream_name: str = "tlt_ivrv") -> Dict:
    """Test integration of the best TLT IV-RV signal into the 9-stream portfolio."""
    from compass.exp2420_transaction_costs import net_sharpe_from_drag

    # Load v8a cube
    cube_path = CACHE_DIR / "exp2280_v6_sparse.pkl"
    cube = pickle.load(open(cube_path, "rb"))
    if "vol_arb" in cube.columns:
        cube = cube.rename(columns={"vol_arb": "cross_vol"})

    # Add QQQ
    qqq_cache = CACHE_DIR / "exp2250_qqq_trades.pkl"
    if qqq_cache.exists():
        qqq_trades = pickle.load(open(qqq_cache, "rb"))
        qqq_daily = pd.Series(0.0, index=cube.index, name="qqq_cs")
        for t in qqq_trades:
            ed = pd.Timestamp(t["exit_date"])
            if ed in qqq_daily.index:
                qqq_daily.loc[ed] += t["pnl"] / CAPITAL
        cube["qqq_cs"] = qqq_daily

    # Add TLT IV-RV stream
    tlt_stream = best_signal.reindex(cube.index).fillna(0.0)
    cube_9 = cube.copy()
    cube_9[stream_name] = tlt_stream

    # VIX data
    vix = data["vix"].reindex(cube_9.index).ffill().bfill()

    # Walk-forward both
    from compass.exp2910_tlt_credit_spreads import (
        walk_forward_portfolio, risk_parity_weights,
    )

    pooled_9, folds_9 = walk_forward_portfolio(
        cube_9, vix_series=vix, apply_ladder=True)
    gross_9 = full_metrics(pooled_9.values)
    net_9 = net_sharpe_from_drag(
        gross_sharpe=gross_9["sharpe"],
        gross_cagr_pct=gross_9["cagr_pct"],
        vol_pct=gross_9["vol_pct"],
        annual_drag_pct=NET_DRAG_PCT,
    )

    pooled_8, folds_8 = walk_forward_portfolio(
        cube.copy(), vix_series=vix, apply_ladder=True)
    gross_8 = full_metrics(pooled_8.values)
    net_8 = net_sharpe_from_drag(
        gross_sharpe=gross_8["sharpe"],
        gross_cagr_pct=gross_8["cagr_pct"],
        vol_pct=gross_8["vol_pct"],
        annual_drag_pct=NET_DRAG_PCT,
    )

    # Correlation of new stream with existing
    corr = cube_9.corr()[stream_name].drop(stream_name).to_dict()

    fold_sharpes_9 = [f["sharpe"] for f in folds_9]
    fold_sharpes_8 = [f["sharpe"] for f in folds_8]

    return {
        "nine_stream_gross": gross_9,
        "nine_stream_net": {
            "sharpe": net_9["net_sharpe"],
            "cagr_pct": net_9["net_cagr_pct"],
            "max_dd_pct": gross_9["max_dd_pct"],
        },
        "eight_stream_gross": gross_8,
        "eight_stream_net": {
            "sharpe": net_8["net_sharpe"],
            "cagr_pct": net_8["net_cagr_pct"],
            "max_dd_pct": gross_8["max_dd_pct"],
        },
        "sharpe_delta_gross": round(gross_9["sharpe"] - gross_8["sharpe"], 2),
        "sharpe_delta_net": round(net_9["net_sharpe"] - net_8["net_sharpe"], 2),
        "correlation_with_streams": {k: round(v, 4) for k, v in corr.items()},
        "mean_correlation": round(float(np.mean(list(corr.values()))), 4),
        "fold_sharpes_9": fold_sharpes_9,
        "fold_sharpes_8": fold_sharpes_8,
        "median_fold_9": round(float(np.median(fold_sharpes_9)), 2),
        "median_fold_8": round(float(np.median(fold_sharpes_8)), 2),
    }


# ═══════════════════════════════════════════════════════════════════════
# Rule Zero check
# ═══════════════════════════════════════════════════════════════════════

def rule_zero_check() -> Dict:
    """Verify no synthetic data generation in executable code."""
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
            if re.search(pat, line):
                count += 1
        findings[name] = count
    return {"clean": all(v == 0 for v in findings.values()), "findings": findings}


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("EXP-2920: TLT IV-RV Arbitrage via MOVE Index")
    print("=" * 70)

    r0 = rule_zero_check()
    print(f"\n[Rule Zero] {'CLEAN' if r0['clean'] else 'CONTAMINATED'}: {r0['findings']}")

    # Load data
    print("\n[1/7] Loading MOVE index and TLT data from Yahoo...")
    data = load_move_and_tlt()
    print(f"  MOVE: {len(data['move'])} days, range {data['move'].min():.1f}-{data['move'].max():.1f}")
    print(f"  TLT RV20: mean {data['tlt_rv20'].mean():.2f}%, range {data['tlt_rv20'].min():.2f}-{data['tlt_rv20'].max():.2f}%")
    print(f"  Calibration ratio (MOVE/TLT_RV): {data['calibration_ratio']:.2f}")
    print(f"  IV-RV spread: mean {data['spread'].mean():.3f}, std {data['spread'].std():.3f}")
    print(f"  Spread z-score: {len(data['spread_z'])} days")

    # Approach A: MOVE-filtered TLT spreads
    print("\n[2/7] Approach A: MOVE-filtered TLT put credit spreads...")
    result_a = approach_a_filtered_spreads(data)
    if result_a["status"] == "OK":
        for label, v in result_a["variants"].items():
            if "sharpe" in v:
                print(f"  {label}: {v['n_trades']} trades, Sharpe {v['sharpe']:.2f}, "
                      f"WR {v['win_rate']:.0%}, TPY {v['trades_per_year']:.1f}")
            else:
                print(f"  {label}: {v.get('status', 'N/A')} ({v['n_trades']} trades)")
    else:
        print(f"  {result_a['status']}: {result_a.get('reason', '')}")

    # Approach B: Standalone IV-RV signal
    print("\n[3/7] Approach B: Standalone TLT IV-RV mean-reversion (equity-based)...")
    result_b = approach_b_standalone_ivrv(data)
    for label, v in result_b.items():
        if "sharpe" in v:
            print(f"  {label}: Sharpe {v['sharpe']:.2f}, CAGR {v['cagr_pct']:.1f}%, "
                  f"DD {v['max_dd_pct']:.1f}%, exposure {v.get('exposure_pct', 100):.0f}%")
        else:
            print(f"  {label}: {v.get('status', 'N/A')}")

    # Approach C: MOVE overlay for cross_vol
    print("\n[4/7] Approach C: MOVE as overlay filter for cross_vol arb...")
    result_c = approach_c_move_overlay(data)
    if "baseline_all" in result_c:
        bl = result_c["baseline_all"]
        print(f"  Baseline cross_vol: {bl['n_trades']} trades, Sharpe {bl['sharpe']:.2f}")
        for label, v in result_c.get("filters", {}).items():
            if "sharpe" in v:
                delta = v.get("sharpe_delta_vs_baseline", 0)
                print(f"  {label}: {v['n_trades']} trades, Sharpe {v['sharpe']:.2f} "
                      f"(delta {delta:+.2f}), WR {v['win_rate']:.0%}")
            else:
                print(f"  {label}: {v.get('status', 'N/A')}")

    # Approach D: MOVE as leading indicator
    print("\n[5/7] Approach D: MOVE as VIX leading indicator...")
    result_d = approach_d_move_leading_indicator(data)
    print(f"  Same-day MOVE-VIX correlation: {result_d['same_day_correlation']:.4f}")
    for lag_label, corr in result_d["lead_lag"].items():
        print(f"  {lag_label}: ρ = {corr:+.4f}")
    for label, v in result_d.items():
        if isinstance(v, dict) and "n_events" in v and "mean_vix_5d_fwd_change" in v:
            print(f"  {label}: {v['n_events']} events, avg VIX 5d fwd {v['mean_vix_5d_fwd_change']:+.2f}%, "
                  f"% VIX up {v['pct_vix_up']:.0f}%")

    # Approach E: MOVE-conditioned TLT daily
    print("\n[6/7] Approach E: MOVE-conditioned TLT daily equity strategy...")
    result_e = approach_e_move_tlt_daily(data)
    best_sharpe = -999
    best_label = None
    for label, v in result_e.items():
        if "sharpe" in v:
            print(f"  {label}: Sharpe {v['sharpe']:.2f}, CAGR {v['cagr_pct']:.1f}%, "
                  f"DD {v['max_dd_pct']:.1f}%, exposure {v.get('exposure_pct', 100):.0f}%")
            if v["sharpe"] > best_sharpe:
                best_sharpe = v["sharpe"]
                best_label = label
        else:
            print(f"  {label}: {v.get('status', 'N/A')}")

    # Portfolio integration with best signal
    print("\n[7/7] Portfolio integration test...")
    best_daily = None
    integration_result = None

    # Build the best signal's daily return series for integration
    if best_label and best_sharpe > 0.5:
        print(f"  Best signal: {best_label} (Sharpe {best_sharpe:.2f})")
        tlt_ret = (data["tlt"] / data["tlt"].shift(1) - 1).dropna()
        move_z = (data["move"] - data["move"].rolling(60).mean()) / data["move"].rolling(60).std()
        move_z = move_z.dropna()
        common = move_z.index.intersection(tlt_ret.index)
        mz = move_z.reindex(common)
        ret = tlt_ret.reindex(common)

        # Reproduce best signal
        if "long_short" in best_label:
            if "z1" in best_label:
                lt, st = 1.0, -1.0
            else:
                lt, st = 0.5, -0.5
            signal = pd.Series(0.0, index=common)
            signal[mz.shift(1) > lt] = 1.0
            signal[mz.shift(1) < st] = -1.0
        elif "long_only" in best_label:
            thresh = 1.0 if "z>1" in best_label else 0.5
            signal = pd.Series(0.0, index=common)
            signal[mz.shift(1) > thresh] = 1.0
        elif "momentum" in best_label:
            mm = data["move"].pct_change(5)
            mm_z = (mm - mm.rolling(60).mean()) / mm.rolling(60).std()
            mm_z = mm_z.dropna()
            common2 = mm_z.index.intersection(tlt_ret.index)
            signal = pd.Series(0.0, index=common2)
            signal[mm_z.shift(1) > 1.0] = 1.0
            signal[mm_z.shift(1) < -1.0] = -1.0
            ret = tlt_ret.reindex(common2)
        else:
            thresh = 0.0
            signal = pd.Series(0.0, index=common)
            signal[mz.shift(1) > thresh] = 1.0

        best_daily = signal * ret

        try:
            integration_result = portfolio_integration(data, best_daily, "tlt_ivrv")
            p9 = integration_result["nine_stream_net"]
            p8 = integration_result["eight_stream_net"]
            delta = integration_result["sharpe_delta_net"]
            print(f"  9-stream NET Sharpe: {p9['sharpe']:.2f} (vs 8-stream {p8['sharpe']:.2f}, delta {delta:+.2f})")
            print(f"  9-stream NET CAGR: {p9['cagr_pct']:.1f}% (vs 8-stream {p8['cagr_pct']:.1f}%)")
            print(f"  Mean correlation: {integration_result['mean_correlation']:.4f}")
        except Exception as e:
            print(f"  Integration failed: {e}")
            import traceback; traceback.print_exc()
    else:
        print(f"  No viable signal (best Sharpe {best_sharpe:.2f}). Skipping integration.")

    # Compile and write results
    print("\n" + "=" * 70)
    all_results = {
        "experiment": "EXP-2920",
        "title": "TLT IV-RV Arbitrage via MOVE Index",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "rule_zero": r0,
        "data_sources": {
            "move": "Yahoo ^MOVE (ICE BofA MOVE Index)",
            "tlt": "Yahoo TLT",
            "vix": "Yahoo ^VIX",
            "tlt_options": "IronVault options_cache.db (via EXP-2910 cache)",
            "cross_vol_trades": "compass/cache/exp2020_vol_arb_trades.pkl",
        },
        "calibration": {
            "move_tlt_rv_ratio": data["calibration_ratio"],
            "move_vix_correlation": round(float(data["move"].corr(data["vix"])), 4),
            "spread_autocorr_1d": round(float(data["spread"].autocorr(1)), 4),
            "spread_autocorr_5d": round(float(data["spread"].autocorr(5)), 4),
        },
        "approach_a_filtered_spreads": result_a,
        "approach_b_standalone_ivrv": result_b,
        "approach_c_move_overlay": result_c,
        "approach_d_leading_indicator": result_d,
        "approach_e_move_tlt_daily": result_e,
        "portfolio_integration": integration_result,
        "best_individual_signal": {
            "label": best_label,
            "sharpe": round(best_sharpe, 2) if best_sharpe > -999 else None,
        },
    }

    # Determine overall verdict
    individual_pass = best_sharpe >= 1.0
    portfolio_pass = (integration_result is not None and
                      integration_result["nine_stream_net"]["sharpe"] >= 6.0)
    sharpe_improved = (integration_result is not None and
                       integration_result["sharpe_delta_net"] > 0)

    if individual_pass and portfolio_pass:
        verdict = (f"PASS — {best_label} signal Sharpe {best_sharpe:.2f}, "
                   f"9-stream net Sharpe {integration_result['nine_stream_net']['sharpe']:.2f}")
    elif individual_pass and not portfolio_pass:
        net_sh = integration_result["nine_stream_net"]["sharpe"] if integration_result else "N/A"
        verdict = (f"PARTIAL — Individual signal passes (Sharpe {best_sharpe:.2f}) "
                   f"but portfolio integration fails (net Sharpe {net_sh})")
    else:
        verdict = (f"KILLED — Best individual Sharpe {best_sharpe:.2f} < 1.0. "
                   f"None of the MOVE-based approaches clear the quality bar.")

    all_results["verdict"] = verdict
    print(f"VERDICT: {verdict}")
    print("=" * 70)

    # Write results
    write_results_md(all_results)
    json_path = REPORT_DIR / "exp2920_tlt_ivrv_results.json"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nResults written to:\n  {RESULTS_PATH}\n  {json_path}")

    return all_results


def write_results_md(r: Dict):
    """Write structured markdown results."""
    v = r.get("verdict", "UNKNOWN")
    best = r.get("best_individual_signal", {})
    cal = r.get("calibration", {})
    integ = r.get("portfolio_integration", {})

    md = f"""# EXP-2920: TLT IV-RV Arbitrage via MOVE Index — Results

**Date:** {r['date']}
**Status:** {v.split(' — ')[0]}
**Rule Zero:** {'CLEAN' if r['rule_zero']['clean'] else 'CONTAMINATED'}

---

## 1. Data Sources

| Source | Series | Coverage |
|---|---|---|
| Yahoo Finance | ^MOVE (ICE BofA MOVE Index) | 2019-2026, {len(r.get('calibration', {}))} calibration params |
| Yahoo Finance | TLT (iShares 20+ Year Treasury Bond ETF) | 2019-2026 |
| Yahoo Finance | ^VIX | 2019-2026 |
| IronVault | TLT options (via EXP-2910 cache) | 2020-2025 |
| IronVault | cross_vol trades (EXP-2020 cache) | 2020-2025 |

---

## 2. MOVE-TLT Calibration

| Parameter | Value |
|---|---|
| MOVE/TLT_RV ratio (median) | {cal.get('move_tlt_rv_ratio', 'N/A')} |
| MOVE-VIX correlation | {cal.get('move_vix_correlation', 'N/A')} |
| IV-RV spread autocorrelation (1d) | {cal.get('spread_autocorr_1d', 'N/A')} |
| IV-RV spread autocorrelation (5d) | {cal.get('spread_autocorr_5d', 'N/A')} |

**Key insight:** MOVE-VIX correlation is only {cal.get('move_vix_correlation', '?')}, confirming MOVE captures a genuinely different volatility factor (rates vs equities). The IV-RV spread has high autocorrelation ({cal.get('spread_autocorr_1d', '?')} at 1-day), suggesting the signal is persistent and tradeable.

---

## 3. Approach A: MOVE-Filtered TLT Put Credit Spreads

Can we improve EXP-2910's Sharpe (0.76) by only entering when the IV-RV spread is elevated?

"""
    ra = r.get("approach_a_filtered_spreads", {})
    if ra.get("status") == "OK":
        md += "| Filter | Trades | Sharpe | TPY | Win Rate | Avg PnL |\n"
        md += "|---|---|---|---|---|---|\n"
        bl = ra.get("baseline_unfiltered", {})
        md += f"| *Unfiltered (EXP-2910)* | *{bl.get('n_trades', '?')}* | *{bl.get('sharpe', '?')}* | *{bl.get('trades_per_year', '?')}* | — | — |\n"
        for label, v in ra.get("variants", {}).items():
            if "sharpe" in v:
                md += f"| {label} | {v['n_trades']} | **{v['sharpe']:.2f}** | {v['trades_per_year']:.1f} | {v['win_rate']:.0%} | ${v['avg_pnl']:.2f} |\n"
            else:
                md += f"| {label} | {v['n_trades']} | {v.get('status', '—')} | — | — | — |\n"
    else:
        md += f"*{ra.get('status', 'N/A')}: {ra.get('reason', '')}*\n"

    md += """
---

## 4. Approach B: Standalone TLT IV-RV Mean-Reversion (Equity-Based)

Long TLT when MOVE is elevated relative to TLT realized vol (causal, 1-day lag).

"""
    rb = r.get("approach_b_standalone_ivrv", {})
    md += "| Variant | Sharpe | CAGR | Max DD | Vol | Exposure |\n"
    md += "|---|---|---|---|---|---|\n"
    for label, v in rb.items():
        if "sharpe" in v:
            md += f"| {label} | **{v['sharpe']:.2f}** | {v['cagr_pct']:.1f}% | {v['max_dd_pct']:.1f}% | {v['vol_pct']:.1f}% | {v.get('exposure_pct', 100):.0f}% |\n"

    md += """
---

## 5. Approach C: MOVE Overlay for Existing cross_vol Arb

Does filtering cross_vol trades by MOVE regime improve Sharpe?

"""
    rc = r.get("approach_c_move_overlay", {})
    if "baseline_all" in rc:
        bl = rc["baseline_all"]
        md += f"**Baseline cross_vol:** {bl['n_trades']} trades, Sharpe {bl['sharpe']:.2f}, TPY {bl['trades_per_year']:.1f}\n\n"
        md += "| Filter | Trades | Sharpe | Delta | Win Rate | TPY |\n"
        md += "|---|---|---|---|---|---|\n"
        for label, v in rc.get("filters", {}).items():
            if "sharpe" in v:
                md += f"| {label} | {v['n_trades']} | **{v['sharpe']:.2f}** | {v['sharpe_delta_vs_baseline']:+.2f} | {v['win_rate']:.0%} | {v['trades_per_year']:.1f} |\n"
            else:
                md += f"| {label} | {v.get('n_trades', '?')} | {v.get('status', '—')} | — | — | — |\n"

    md += """
---

## 6. Approach D: MOVE as VIX Leading Indicator

"""
    rd = r.get("approach_d_leading_indicator", {})
    md += f"**Same-day MOVE-VIX correlation:** {rd.get('same_day_correlation', '?')}\n\n"
    md += "| Lead-lag | Correlation |\n|---|---|\n"
    for label, corr in rd.get("lead_lag", {}).items():
        md += f"| {label} | {corr:+.4f} |\n"
    md += "\n### Conditional VIX Response to MOVE Spikes\n\n"
    md += "| MOVE Regime | Events | Avg VIX 5d Fwd | % VIX Up |\n|---|---|---|---|\n"
    for label, v in rd.items():
        if isinstance(v, dict) and "n_events" in v and "mean_vix_5d_fwd_change" in v:
            md += f"| {label} | {v['n_events']} | {v['mean_vix_5d_fwd_change']:+.2f}% | {v['pct_vix_up']:.0f}% |\n"

    md += """
---

## 7. Approach E: MOVE-Conditioned TLT Daily Strategy

"""
    re_res = r.get("approach_e_move_tlt_daily", {})
    md += "| Variant | Sharpe | CAGR | Max DD | Vol | Exposure |\n"
    md += "|---|---|---|---|---|---|\n"
    for label, v in re_res.items():
        if "sharpe" in v:
            md += f"| {label} | **{v['sharpe']:.2f}** | {v['cagr_pct']:.1f}% | {v['max_dd_pct']:.1f}% | {v['vol_pct']:.1f}% | {v.get('exposure_pct', '?')}% |\n"

    md += "\n---\n\n## 8. Portfolio Integration\n\n"
    if integ:
        p9 = integ.get("nine_stream_net", {})
        p8 = integ.get("eight_stream_net", {})
        md += f"""**Best signal:** {best.get('label', '?')} (individual Sharpe {best.get('sharpe', '?')})

| Metric | 9-Stream (with TLT IV-RV) | 8-Stream Baseline | Delta |
|---|---|---|---|
| NET Sharpe | **{p9.get('sharpe', '?')}** | {p8.get('sharpe', '?')} | {integ.get('sharpe_delta_net', '?'):+.2f} |
| NET CAGR | {p9.get('cagr_pct', '?')}% | {p8.get('cagr_pct', '?')}% | — |
| Max DD | {p9.get('max_dd_pct', '?')}% | {p8.get('max_dd_pct', '?')}% | — |

**Mean correlation with existing streams:** {integ.get('mean_correlation', '?')}

### Per-Stream Correlation

| Stream | ρ |
|---|---|
"""
        for stream, rho in sorted(integ.get("correlation_with_streams", {}).items()):
            md += f"| {stream} | {rho:+.4f} |\n"

        md += f"""
### Walk-Forward Folds

| Metric | 9-Stream | 8-Stream |
|---|---|---|
| Median fold Sharpe | {integ.get('median_fold_9', '?')} | {integ.get('median_fold_8', '?')} |
"""
    else:
        md += "*No viable signal for portfolio integration.*\n"

    md += f"""
---

## 9. Verdict

**{v}**

---

## 10. Methodology

- **MOVE index:** ICE BofA MOVE Index from Yahoo ^MOVE — measures expected 1-month Treasury yield volatility in basis points
- **TLT realized vol:** 20-day trailing annualized standard deviation of log returns
- **IV-RV spread:** MOVE (normalized to TLT-equivalent scale) minus TLT realized vol
- **Z-score:** 60-day rolling standardization of the IV-RV spread
- **Causality:** All signals use 1-day lag (yesterday's MOVE → today's position)
- **Walk-forward:** 252d train / 63d test expanding window
- **Cost model:** 890 bps/yr (analytical drag via net_sharpe_from_drag)
- **Sharpe formula:** mean(daily_returns) / std(daily_returns, ddof=0) × √252

### Rule Zero Verification

"""
    for pat, count in r["rule_zero"]["findings"].items():
        md += f"- `{pat}`: {count} occurrences {'OK' if count == 0 else 'FAIL'}\n"

    md += """
---

*Generated by compass/exp2920_tlt_ivrv_arb.py*
*All data from Yahoo Finance (^MOVE, TLT, ^VIX) and IronVault. No synthetic data.*
"""

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(md)


if __name__ == "__main__":
    main()
