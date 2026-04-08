#!/usr/bin/env python3
"""
ML Feature Analysis Pipeline for Credit Spreads Strategy
=========================================================
Loads all historical trade data, engineers features, trains XGBoost models,
and extracts feature importances with regime-conditional analysis.

Output: output/ml_feature_analysis_report.md

Usage:
    python3 scripts/ml_feature_analysis.py [--max-mc-files N] [--sample-seeds N]
"""

import os
import sys
import json
import sqlite3
import warnings
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.metrics import roc_auc_score
import xgboost as xgb

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

# ─────────────────────────────────────────────
# FEATURE SCHEMA
# ─────────────────────────────────────────────
FEATURE_COLS = [
    # Strategy params
    "is_bull_put", "is_bear_call", "is_iron_condor",
    "spread_width", "credit_pct_of_width", "dte_at_entry", "contracts",
    "otm_pct", "config_risk_pct", "config_sl_mult", "config_pt_pct",
    # Volatility regime
    "vix_level", "vix_regime_cat",
    # SPY technical
    "rsi_14", "macd_hist", "macd_bullish",
    "bb_width", "bb_position",
    "spy_ret_5d", "spy_ret_20d", "spy_rvol_20d", "spy_52w_pct",
    # Options market
    "pc_vol_ratio", "pc_oi_ratio",
    # Calendar
    "day_of_week", "month", "quarter", "days_to_opex",
    "is_jan_effect", "is_q4_rally", "is_summer",
]

FEATURE_GROUPS = {
    "Strategy": [
        "is_bull_put", "is_bear_call", "is_iron_condor",
        "spread_width", "credit_pct_of_width", "dte_at_entry", "contracts",
        "otm_pct", "config_risk_pct", "config_sl_mult", "config_pt_pct",
    ],
    "Volatility Regime": ["vix_level", "vix_regime_cat"],
    "SPY Technical": [
        "rsi_14", "macd_hist", "macd_bullish",
        "bb_width", "bb_position",
        "spy_ret_5d", "spy_ret_20d", "spy_rvol_20d", "spy_52w_pct",
    ],
    "Options Market": ["pc_vol_ratio", "pc_oi_ratio"],
    "Calendar": [
        "day_of_week", "month", "quarter", "days_to_opex",
        "is_jan_effect", "is_q4_rally", "is_summer",
    ],
}


# ─────────────────────────────────────────────
# SECTION 1: DATA LOADING
# ─────────────────────────────────────────────

def _extract_config_scalars(config: dict) -> dict:
    """Pull relevant numeric params from a backtest config dict."""
    out = {}
    # Flat config (exp_NNN style)
    out["config_risk_pct"] = (
        config.get("max_risk_per_trade")
        or config.get("risk_pct")
        or config.get("risk_per_trade")
    )
    out["config_sl_mult"] = config.get("stop_loss_multiplier")
    out["config_pt_pct"] = config.get("profit_target_pct")
    # champion/nested style
    sp = config.get("strategy_params", {}).get("credit_spread", {})
    if sp:
        out["config_risk_pct"] = out["config_risk_pct"] or sp.get("max_risk_pct")
        out["config_sl_mult"] = out["config_sl_mult"] or sp.get("stop_loss_multiplier")
        out["config_pt_pct"] = out["config_pt_pct"] or sp.get("profit_target_pct")
    return {k: float(v) if v is not None else np.nan for k, v in out.items()}


def load_mc_trades(max_files: int = 20, sample_seeds: int = 50) -> pd.DataFrame:
    """Load individual trades from Monte Carlo JSON files."""
    mc_files = sorted(OUTPUT_DIR.glob("mc_*.json"), key=lambda p: p.stat().st_size, reverse=True)
    if max_files == 0:
        print("  max_files=0, skipping MC data")
        return pd.DataFrame()
    if max_files > 0:
        mc_files = mc_files[:max_files]

    print(f"  Found {len(mc_files)} MC files (loading up to {max_files})")
    all_trades: list[dict] = []

    for mc_file in mc_files:
        mb = mc_file.stat().st_size / 1_048_576
        print(f"    {mc_file.name} ({mb:.0f} MB)...", end=" ", flush=True)
        try:
            with open(mc_file) as fh:
                data = json.load(fh)

            # config may be a path string or an inline dict
            config_raw = data.get("config", {})
            if isinstance(config_raw, str):
                cfg_path = ROOT / config_raw
                try:
                    with open(cfg_path) as cf:
                        config = json.load(cf)
                except Exception:
                    config = {}
            else:
                config = config_raw if isinstance(config_raw, dict) else {}
            cfg_scalars = _extract_config_scalars(config)
            run_id = data.get("run_id", mc_file.stem)
            seeds = data.get("all_seeds", [])

            if sample_seeds and len(seeds) > sample_seeds:
                rng = np.random.default_rng(42)
                idx = rng.choice(len(seeds), size=sample_seeds, replace=False)
                seeds = [seeds[i] for i in sorted(idx)]

            n_added = 0
            for seed_data in seeds:
                seed = seed_data.get("seed", 0)
                for yr_str, yr_data in seed_data.get("per_year", {}).items():
                    for t in yr_data.get("trades", []):
                        rec = dict(t)
                        rec.update(cfg_scalars)
                        rec["run_id"] = run_id
                        rec["seed"] = seed
                        rec["year"] = int(yr_str)
                        rec["source"] = "mc"
                        all_trades.append(rec)
                        n_added += 1

            print(f"{n_added:,} trades")
        except Exception as e:
            print(f"SKIP ({e})")

    if not all_trades:
        return pd.DataFrame()

    df = pd.DataFrame(all_trades)
    print(f"  MC total: {len(df):,} trades")
    return df


def load_db_trades() -> pd.DataFrame:
    """Load closed trades from all experiment SQLite DBs."""
    db_files = (
        list(DATA_DIR.glob("pilotai_*.db"))
        + list(DATA_DIR.glob("paper_trading.db"))
    )
    all_rows: list[dict] = []

    for db_file in db_files:
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "trades" not in tables:
                conn.close()
                continue

            rows = conn.execute(
                "SELECT * FROM trades WHERE status IN ('closed','completed') LIMIT 5000"
            ).fetchall()
            conn.close()

            for row in rows:
                d = dict(row)
                # parse metadata JSON blob
                if d.get("metadata"):
                    try:
                        d.update(json.loads(d["metadata"]))
                    except Exception:
                        pass
                d["source"] = f"db:{db_file.stem}"
                all_rows.append(d)

            if rows:
                print(f"    {db_file.name}: {len(rows)} trades")
        except Exception as e:
            print(f"    {db_file.name}: SKIP ({e})")

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


def load_paper_trades() -> pd.DataFrame:
    """Load paper trade outcomes from JSONL."""
    path = DATA_DIR / "ml_training" / "trade_outcomes.jsonl"
    if not path.exists():
        return pd.DataFrame()

    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    t = json.loads(line)
                    t["source"] = "paper"
                    rows.append(t)
                except Exception:
                    pass

    print(f"    paper_trades: {len(rows)} trades")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ─────────────────────────────────────────────
# SECTION 2: MARKET DATA
# ─────────────────────────────────────────────

def load_market_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load SPY closes and VIX/regime from macro_state.db."""
    macro_db = DATA_DIR / "macro_state.db"
    spy_df = pd.DataFrame()
    vix_df = pd.DataFrame()

    if not macro_db.exists():
        print("  macro_state.db not found — market features will be unavailable")
        return spy_df, vix_df

    try:
        conn = sqlite3.connect(f"file:{macro_db}?mode=ro", uri=True)

        spy_df = pd.read_sql(
            "SELECT date, spy_close FROM snapshots WHERE spy_close IS NOT NULL ORDER BY date",
            conn,
            parse_dates=["date"],
            index_col="date",
        )

        vix_df = pd.read_sql(
            "SELECT date, vix, regime FROM macro_score WHERE vix IS NOT NULL ORDER BY date",
            conn,
            parse_dates=["date"],
            index_col="date",
        )
        conn.close()
        print(f"  SPY: {len(spy_df)} days | VIX: {len(vix_df)} days")
    except Exception as e:
        print(f"  macro_state.db error: {e}")

    return spy_df, vix_df


def compute_spy_technicals(spy_df: pd.DataFrame) -> pd.DataFrame:
    """RSI, MACD, Bollinger, realized vol, trend from daily SPY closes."""
    df = spy_df.copy()
    p = df["spy_close"]

    # RSI(14)
    delta = p.diff()
    avg_gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    df["rsi_14"] = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, np.nan))

    # MACD
    ema12 = p.ewm(span=12, adjust=False).mean()
    ema26 = p.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = macd - signal
    df["macd_bullish"] = (df["macd_hist"] > 0).astype(int)

    # Bollinger (20-day)
    rm = p.rolling(20).mean()
    rs = p.rolling(20).std()
    df["bb_width"] = (4 * rs) / rm
    df["bb_position"] = (p - (rm - 2 * rs)) / (4 * rs).replace(0, np.nan)

    # Returns / vol
    for n in [5, 20]:
        df[f"spy_ret_{n}d"] = p.pct_change(n)
    df["spy_rvol_20d"] = p.pct_change().rolling(20).std() * np.sqrt(252) * 100
    df["spy_52w_pct"] = p / p.rolling(252).max()

    return df


def load_put_call_data() -> pd.DataFrame:
    """Aggregate SPY put/call volume + OI ratios from options_cache.db."""
    db = DATA_DIR / "options_cache.db"
    if not db.exists():
        return pd.DataFrame()

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        df = pd.read_sql("""
            SELECT
                d.date,
                SUM(CASE WHEN c.option_type='P' THEN COALESCE(d.volume,0) ELSE 0 END)        AS put_vol,
                SUM(CASE WHEN c.option_type='C' THEN COALESCE(d.volume,0) ELSE 0 END)        AS call_vol,
                SUM(CASE WHEN c.option_type='P' THEN COALESCE(d.open_interest,0) ELSE 0 END) AS put_oi,
                SUM(CASE WHEN c.option_type='C' THEN COALESCE(d.open_interest,0) ELSE 0 END) AS call_oi
            FROM option_daily d
            JOIN option_contracts c ON d.contract_symbol = c.contract_symbol
            WHERE c.ticker = 'SPY'
              AND d.date > '2000-01-01'
            GROUP BY d.date
            ORDER BY d.date
        """, conn, parse_dates=["date"], index_col="date")
        conn.close()

        df = df[df.index.notna()].sort_index()
        df["pc_vol_ratio"] = df["put_vol"] / df["call_vol"].replace(0, np.nan)
        df["pc_oi_ratio"] = df["put_oi"] / df["call_oi"].replace(0, np.nan)
        print(f"  Put/call ratios: {len(df)} dates")
        return df[["pc_vol_ratio", "pc_oi_ratio"]]
    except Exception as e:
        print(f"  options_cache.db error: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────
# SECTION 3: FEATURE ENGINEERING
# ─────────────────────────────────────────────

def _third_friday(year: int, month: int) -> datetime:
    """Return the 3rd Friday of a given month."""
    first = datetime(year, month, 1)
    # days until first Friday (weekday=4)
    first_fri = first + timedelta(days=(4 - first.weekday()) % 7)
    return first_fri + timedelta(weeks=2)


def engineer_features(
    raw_df: pd.DataFrame,
    spy_tech: pd.DataFrame,
    vix_df: pd.DataFrame,
    pc_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build ML feature matrix from raw trade data joined with market data."""

    # Pre-index market data for fast asof lookups
    spy_idx = spy_tech.index if not spy_tech.empty else pd.DatetimeIndex([])
    vix_idx = vix_df.index if not vix_df.empty else pd.DatetimeIndex([])
    pc_idx = pc_df.index if not pc_df.empty else pd.DatetimeIndex([])

    records = []
    skipped = 0

    for _, row in raw_df.iterrows():
        try:
            rec: dict = {}

            # ── Identify trade type ──────────────────────────────────────
            ttype = str(row.get("type") or row.get("strategy_type") or "").lower()
            rec["strategy_type"] = ttype
            rec["is_bull_put"] = int("bull_put" in ttype)
            rec["is_bear_call"] = int("bear_call" in ttype)
            rec["is_iron_condor"] = int("iron_condor" in ttype)

            # ── Dates ────────────────────────────────────────────────────
            entry_date = pd.to_datetime(row.get("entry_date"), errors="coerce", utc=False)
            exit_date  = pd.to_datetime(row.get("exit_date"),  errors="coerce", utc=False)
            expiry     = pd.to_datetime(row.get("expiration"), errors="coerce", utc=False)

            # Normalise timezone-aware dates to naive
            if entry_date is not pd.NaT and entry_date.tzinfo is not None:
                entry_date = entry_date.tz_localize(None)
            if exit_date is not pd.NaT and exit_date.tzinfo is not None:
                exit_date = exit_date.tz_localize(None)
            if expiry is not pd.NaT and expiry.tzinfo is not None:
                expiry = expiry.tz_localize(None)

            if pd.isna(entry_date):
                skipped += 1
                continue

            rec["entry_date"] = entry_date
            rec["year"] = int(row.get("year") or entry_date.year)

            # ── Position features ────────────────────────────────────────
            short_k = float(row.get("short_strike") or 0) or np.nan
            long_k  = float(row.get("long_strike")  or 0) or np.nan
            credit  = float(row.get("credit")        or 0) or np.nan
            contracts = float(row.get("contracts")   or 1)

            width = abs(long_k - short_k) if not (np.isnan(short_k) or np.isnan(long_k)) else np.nan
            rec["spread_width"] = width
            rec["credit_pct_of_width"] = (credit / width) if (width and width > 0) else np.nan
            rec["contracts"] = contracts

            # DTE at entry
            if not pd.isna(expiry):
                rec["dte_at_entry"] = max(0, (expiry - entry_date).days)
            elif "dte_at_entry" in row.index and pd.notna(row["dte_at_entry"]):
                rec["dte_at_entry"] = float(row["dte_at_entry"])
            else:
                rec["dte_at_entry"] = np.nan

            # Hold days
            rec["hold_days"] = (
                max(0, (exit_date - entry_date).days) if not pd.isna(exit_date) else np.nan
            )

            # ── Config params ────────────────────────────────────────────
            rec["config_risk_pct"] = float(row.get("config_risk_pct") or np.nan)
            rec["config_sl_mult"]  = float(row.get("config_sl_mult")  or np.nan)
            rec["config_pt_pct"]   = float(row.get("config_pt_pct")   or np.nan)

            # Paper-only enriched fields
            for field in ("entry_delta", "entry_pop"):
                if field in row.index and pd.notna(row[field]):
                    rec[field] = float(row[field])

            # Exit reason encoding
            er = str(row.get("exit_reason") or "").lower()
            rec["exited_profit_target"]  = int("profit" in er)
            rec["exited_stop_loss"]      = int("stop" in er)
            rec["exited_management_dte"] = int("management" in er or ("dte" in er and "profit" not in er))
            rec["exited_expiration"]     = int("expir" in er)

            # ── Calendar features ────────────────────────────────────────
            ed = entry_date
            rec["day_of_week"] = ed.dayofweek
            rec["month"]       = ed.month
            rec["quarter"]     = (ed.month - 1) // 3 + 1

            # Days to next monthly OpEx (3rd Friday)
            opex = _third_friday(ed.year, ed.month)
            if opex.date() < ed.date():
                nxt_m = ed.month % 12 + 1
                nxt_y = ed.year + (1 if nxt_m == 1 else 0)
                opex = _third_friday(nxt_y, nxt_m)
            rec["days_to_opex"] = (opex - ed).days

            rec["is_jan_effect"] = int(ed.month == 1)
            rec["is_q4_rally"]   = int(ed.month in (10, 11, 12))
            rec["is_summer"]     = int(ed.month in (6, 7, 8))

            # ── Join market data (asof — forward-fill weekends/holidays) ─
            ts = pd.Timestamp(entry_date).normalize()

            if len(spy_idx):
                loc = spy_idx.asof(ts)
                if loc is not pd.NaT:
                    sr = spy_tech.loc[loc]
                    spy_close = sr.get("spy_close", np.nan)
                    rec["spy_close"]   = spy_close
                    rec["rsi_14"]      = sr.get("rsi_14",      np.nan)
                    rec["macd_hist"]   = sr.get("macd_hist",   np.nan)
                    rec["macd_bullish"]= sr.get("macd_bullish",np.nan)
                    rec["bb_width"]    = sr.get("bb_width",    np.nan)
                    rec["bb_position"] = sr.get("bb_position", np.nan)
                    rec["spy_ret_5d"]  = sr.get("spy_ret_5d",  np.nan)
                    rec["spy_ret_20d"] = sr.get("spy_ret_20d", np.nan)
                    rec["spy_rvol_20d"]= sr.get("spy_rvol_20d",np.nan)
                    rec["spy_52w_pct"] = sr.get("spy_52w_pct", np.nan)

                    # OTM%: distance from short strike to current price
                    if not np.isnan(spy_close) and spy_close > 0 and not np.isnan(short_k):
                        if rec["is_bear_call"]:
                            rec["otm_pct"] = (short_k - spy_close) / spy_close
                        elif rec["is_bull_put"]:
                            rec["otm_pct"] = (spy_close - short_k) / spy_close
                        else:
                            rec["otm_pct"] = abs(short_k - spy_close) / spy_close
                    else:
                        rec["otm_pct"] = np.nan

            if len(vix_idx):
                loc = vix_idx.asof(ts)
                if loc is not pd.NaT:
                    vr = vix_df.loc[loc]
                    vix = float(vr.get("vix", np.nan))
                    rec["vix_level"] = vix
                    rec["macro_regime"] = str(vr.get("regime", "")).lower()
                    if not np.isnan(vix):
                        if   vix < 15: rec["vix_regime_cat"] = 0
                        elif vix < 20: rec["vix_regime_cat"] = 1
                        elif vix < 25: rec["vix_regime_cat"] = 2
                        elif vix < 35: rec["vix_regime_cat"] = 3
                        else:          rec["vix_regime_cat"] = 4
                    else:
                        rec["vix_regime_cat"] = np.nan

            if len(pc_idx):
                loc = pc_idx.asof(ts)
                if loc is not pd.NaT:
                    pr = pc_df.loc[loc]
                    rec["pc_vol_ratio"] = float(pr.get("pc_vol_ratio", np.nan))
                    rec["pc_oi_ratio"]  = float(pr.get("pc_oi_ratio",  np.nan))

            # ── Outcome labels ───────────────────────────────────────────
            pnl = float(row.get("pnl") or np.nan)
            ret = float(row.get("return_pct") or np.nan)
            rec["pnl"]        = pnl
            rec["return_pct"] = ret
            rec["win"]        = int(pnl > 0) if not np.isnan(pnl) else np.nan
            rec["big_win"]    = int(ret >= 40)  if not np.isnan(ret) else np.nan
            rec["big_loss"]   = int(ret <= -150) if not np.isnan(ret) else np.nan

            rec["source"]  = str(row.get("source", "unknown"))
            rec["run_id"]  = str(row.get("run_id",  "unknown"))

            records.append(rec)

        except Exception:
            skipped += 1
            continue

    print(f"  Engineered {len(records):,} records ({skipped} skipped)")
    return pd.DataFrame(records)


# ─────────────────────────────────────────────
# SECTION 4: ML TRAINING
# ─────────────────────────────────────────────

def train_xgb(X: pd.DataFrame, y: pd.Series) -> tuple[xgb.XGBClassifier, np.ndarray]:
    pos_rate = y.mean()
    spw = (1 - pos_rate) / pos_rate if 0 < pos_rate < 1 else 1.0

    model = xgb.XGBClassifier(
        n_estimators=500,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=20,
        scale_pos_weight=spw,
        eval_metric="auc",
        random_state=42,
        n_jobs=-1,
        verbosity=0,
    )

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(model, X, y, cv=cv, scoring="roc_auc", n_jobs=-1)
    model.fit(X, y)
    return model, cv_scores


def get_importances(model: xgb.XGBClassifier, feature_names: list[str]) -> pd.DataFrame:
    booster = model.get_booster()
    rows = []
    for feat in feature_names:
        rows.append({
            "feature": feat,
            "gain":   booster.get_score(importance_type="gain"  ).get(feat, 0),
            "weight": booster.get_score(importance_type="weight").get(feat, 0),
            "cover":  booster.get_score(importance_type="cover" ).get(feat, 0),
        })
    df = pd.DataFrame(rows)
    for col in ("gain", "weight", "cover"):
        total = df[col].sum()
        df[f"{col}_pct"] = df[col] / total * 100 if total > 0 else 0.0
    return df.sort_values("gain", ascending=False).reset_index(drop=True)


def try_shap(model: xgb.XGBClassifier, X: pd.DataFrame) -> Optional[pd.DataFrame]:
    try:
        import shap  # type: ignore
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X)
        if isinstance(sv, list):
            sv = sv[1]
        mean_abs = np.abs(sv).mean(axis=0)
        return (
            pd.DataFrame({"feature": X.columns.tolist(), "shap_importance": mean_abs})
            .sort_values("shap_importance", ascending=False)
            .reset_index(drop=True)
        )
    except ImportError:
        return None
    except Exception as e:
        print(f"  SHAP error: {e}")
        return None


# ─────────────────────────────────────────────
# SECTION 5: REGIME ANALYSIS
# ─────────────────────────────────────────────

def regime_analysis(
    ml_df: pd.DataFrame,
    avail_cols: list[str],
    regime_masks: dict[str, pd.Series],
) -> dict:
    results = {}

    for label, mask in regime_masks.items():
        sub = ml_df[mask].dropna(subset=["win"])
        feat_cols = [c for c in avail_cols if sub[c].notna().sum() >= 20]

        n = len(sub)
        if n < 50 or sub["win"].nunique() < 2:
            results[label] = {"n": n, "skipped": "insufficient_data"}
            continue

        X = sub[feat_cols].fillna(sub[feat_cols].median())
        y = sub["win"].astype(int)

        pos_rate = y.mean()
        spw = (1 - pos_rate) / pos_rate if 0 < pos_rate < 1 else 1.0

        m = xgb.XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.1,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=10,
            scale_pos_weight=spw, eval_metric="auc",
            random_state=42, n_jobs=-1, verbosity=0,
        )
        try:
            m.fit(X, y)
            imp = get_importances(m, feat_cols)
            results[label] = {"n": n, "win_rate": float(y.mean()), "importances": imp}
        except Exception as e:
            results[label] = {"n": n, "error": str(e)}

    return results


# ─────────────────────────────────────────────
# SECTION 6: REPORT
# ─────────────────────────────────────────────

def _bar(val: float, width: int = 20) -> str:
    filled = max(0, min(width, int(val * width)))
    return "█" * filled + "░" * (width - filled)


def generate_report(
    df: pd.DataFrame,
    cv_scores: np.ndarray,
    importances: pd.DataFrame,
    shap_df: Optional[pd.DataFrame],
    regime_results: dict,
    avail_cols: list[str],
) -> str:
    L: list[str] = []

    def h(text: str):
        L.append(text)

    h("# ML Feature Analysis Report: Credit Spreads Strategy")
    h(f"\n_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    h(f"_Dataset: {len(df):,} trades | {df.get('year', pd.Series()).dropna().nunique()} years | sources: {df['source'].nunique()}_")
    h("\n---\n")

    # ── 1. Dataset Overview ──────────────────────────────────────────────
    h("## 1. Dataset Overview\n")

    if "source" in df.columns:
        h("**Trades by Source:**\n")
        for src, cnt in df["source"].value_counts().items():
            h(f"- `{src}`: {cnt:,}")
        h("")

    if "year" in df.columns:
        yr_counts = df[df["year"] > 0]["year"].value_counts().sort_index()
        h("**Trades by Year:**\n")
        for yr, cnt in yr_counts.items():
            h(f"- {int(yr)}: {cnt:,}")
        h("")

    if "win" in df.columns:
        wr = df["win"].mean()
        h(f"**Overall Win Rate:** {wr:.1%} across {df['win'].notna().sum():,} labeled trades\n")

        h("**Win Rate by Strategy Type:**\n")
        h("| Type | Win Rate | Count |")
        h("|------|---------|-------|")
        for stype, grp in df.groupby("strategy_type"):
            if pd.notna(stype) and stype:
                h(f"| {stype} | {grp['win'].mean():.1%} | {len(grp):,} |")
        h("")

    h("**Feature Coverage:**\n")
    h("| Feature | Coverage | Group |")
    h("|---------|---------|-------|")
    for feat in avail_cols:
        cov = df[feat].notna().mean() if feat in df.columns else 0.0
        grp = next((g for g, fs in FEATURE_GROUPS.items() if feat in fs), "Other")
        h(f"| `{feat}` | {cov:.0%} {_bar(cov, 10)} | {grp} |")
    h("")

    # ── 2. Model Performance ─────────────────────────────────────────────
    h("## 2. Model Performance\n")
    h("**XGBoost Win/Loss Classifier — 5-Fold Stratified CV:**\n")
    h(f"| Metric | Value |")
    h(f"|--------|-------|")
    h(f"| Mean AUC | **{cv_scores.mean():.4f}** |")
    h(f"| Std Dev  | {cv_scores.std():.4f} |")
    h(f"| Min / Max | {cv_scores.min():.4f} / {cv_scores.max():.4f} |")
    h(f"| Per-fold  | {' / '.join(f'{s:.4f}' for s in cv_scores)} |")
    h("")
    auc = cv_scores.mean()
    if auc >= 0.65:
        h("> **Strong signal** (AUC ≥ 0.65) — features meaningfully predict win/loss.\n")
    elif auc >= 0.58:
        h("> **Moderate signal** (AUC 0.58–0.65) — features have useful but limited predictive power.\n")
    elif auc >= 0.53:
        h("> **Weak signal** (AUC 0.53–0.58) — marginal improvement over chance; consider richer features.\n")
    else:
        h("> **No meaningful signal** (AUC ≈ 0.50) — win/loss largely random given entry features alone.\n")

    # ── 3. Feature Importances ───────────────────────────────────────────
    h("## 3. Feature Importances (Full Dataset)\n")
    h("### 3a. XGBoost Gain (primary — measures information gain per split)\n")
    h("| Rank | Feature | Gain% | Weight% | Cover% | Group |")
    h("|------|---------|-------|---------|--------|-------|")
    for rank, row in importances.head(25).iterrows():
        feat = row["feature"]
        grp = next((g for g, fs in FEATURE_GROUPS.items() if feat in fs), "Other")
        h(f"| {rank+1} | `{feat}` | {row['gain_pct']:.1f}% | {row['weight_pct']:.1f}% | {row['cover_pct']:.1f}% | {grp} |")
    h("")

    if shap_df is not None:
        h("### 3b. SHAP Mean |Value| (directional impact on prediction)\n")
        h("| Rank | Feature | SHAP Importance |")
        h("|------|---------|----------------|")
        for i, row in shap_df.head(20).iterrows():
            h(f"| {i+1} | `{row['feature']}` | {row['shap_importance']:.5f} |")
        h("")
    else:
        h("### 3b. SHAP\n_Not available — install `shap` package for directional importance._\n")

    h("### 3c. Feature Group Summary\n")
    h("| Group | Total Gain% | Top Feature |")
    h("|-------|------------|-------------|")
    group_summary = []
    for grp, feats in FEATURE_GROUPS.items():
        sub = importances[importances["feature"].isin(feats)]
        total = sub["gain_pct"].sum() if "gain_pct" in sub.columns else 0.0
        top = sub.iloc[0]["feature"] if len(sub) > 0 else "—"
        group_summary.append((grp, total, top))
    for grp, total, top in sorted(group_summary, key=lambda x: -x[1]):
        h(f"| {grp} | **{total:.1f}%** | `{top}` |")
    h("")

    # ── 4. Regime Analysis ───────────────────────────────────────────────
    h("## 4. Regime-Conditional Analysis\n")
    for label, res in regime_results.items():
        n = res.get("n", 0)
        if "skipped" in res:
            h(f"### {label}\n_Skipped ({res['skipped']}, n={n})_\n")
            continue
        if "error" in res:
            h(f"### {label}\n_Error: {res['error']}_\n")
            continue
        wr = res.get("win_rate", np.nan)
        imp = res.get("importances", pd.DataFrame())
        h(f"### {label}\n")
        h(f"_n={n:,} | win_rate={wr:.1%}_\n")
        if len(imp) >= 3:
            h("**Top 8 features by gain:**\n")
            h("| Rank | Feature | Gain% |")
            h("|------|---------|-------|")
            for i, row in imp.head(8).iterrows():
                h(f"| {i+1} | `{row['feature']}` | {row['gain_pct']:.1f}% |")
        h("")

    # ── 5. Patterns ──────────────────────────────────────────────────────
    h("## 5. Key Patterns\n")

    # VIX buckets
    if "vix_level" in df.columns and "win" in df.columns:
        v = df[df["vix_level"].notna() & df["win"].notna()].copy()
        if len(v) >= 100:
            bins = [0, 15, 20, 25, 30, 35, 100]
            labels = ["<15", "15–20", "20–25", "25–30", "30–35", ">35"]
            v["vix_bucket"] = pd.cut(v["vix_level"], bins=bins, labels=labels)
            agg = v.groupby("vix_bucket")["win"].agg(["mean", "count"])
            h("### 5a. VIX Level vs Win Rate\n")
            h("| VIX Range | Win Rate | N Trades |")
            h("|-----------|---------|---------|")
            for bkt, row in agg.iterrows():
                if row["count"] >= 10:
                    h(f"| VIX {bkt} | {row['mean']:.1%} | {int(row['count']):,} |")
            h("")

    # DTE buckets
    if "dte_at_entry" in df.columns and "win" in df.columns:
        d = df[df["dte_at_entry"].notna() & df["win"].notna() & (df["dte_at_entry"] > 0)].copy()
        if len(d) >= 100:
            bins = [0, 7, 14, 21, 30, 45, 60, 999]
            labels = ["0–7", "7–14", "14–21", "21–30", "30–45", "45–60", "60+"]
            d["dte_bucket"] = pd.cut(d["dte_at_entry"], bins=bins, labels=labels)
            agg = d.groupby("dte_bucket")["win"].agg(["mean", "count"])
            h("### 5b. DTE at Entry vs Win Rate\n")
            h("| DTE Range | Win Rate | N Trades |")
            h("|-----------|---------|---------|")
            for bkt, row in agg.iterrows():
                if row["count"] >= 10:
                    h(f"| DTE {bkt} | {row['mean']:.1%} | {int(row['count']):,} |")
            h("")

    # Credit % of width
    if "credit_pct_of_width" in df.columns and "win" in df.columns:
        c = df[
            df["credit_pct_of_width"].notna() & df["win"].notna()
            & (df["credit_pct_of_width"] > 0) & (df["credit_pct_of_width"] < 1)
        ].copy()
        if len(c) >= 100:
            bins = [0, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50, 1.0]
            labels = ["<10%", "10–15%", "15–20%", "20–25%", "25–33%", "33–50%", ">50%"]
            c["cred_bucket"] = pd.cut(c["credit_pct_of_width"], bins=bins, labels=labels)
            agg = c.groupby("cred_bucket")["win"].agg(["mean", "count"])
            h("### 5c. Credit as % of Spread Width vs Win Rate\n")
            h("| Credit Range | Win Rate | N Trades |")
            h("|-------------|---------|---------|")
            for bkt, row in agg.iterrows():
                if row["count"] >= 10:
                    h(f"| {bkt} | {row['mean']:.1%} | {int(row['count']):,} |")
            h("")

    # OTM %
    if "otm_pct" in df.columns and "win" in df.columns:
        o = df[df["otm_pct"].notna() & df["win"].notna() & (df["otm_pct"] >= 0)].copy()
        if len(o) >= 100:
            bins = [-0.001, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10, 1.0]
            labels = ["<1%", "1–2%", "2–3%", "3–5%", "5–7%", "7–10%", ">10%"]
            o["otm_bucket"] = pd.cut(o["otm_pct"], bins=bins, labels=labels)
            agg = o.groupby("otm_bucket")["win"].agg(["mean", "count"])
            h("### 5d. OTM% vs Win Rate\n")
            h("| OTM Range | Win Rate | N Trades |")
            h("|-----------|---------|---------|")
            for bkt, row in agg.iterrows():
                if row["count"] >= 10:
                    h(f"| {bkt} | {row['mean']:.1%} | {int(row['count']):,} |")
            h("")

    # Monthly seasonality
    if "month" in df.columns and "win" in df.columns:
        MONTHS = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                  7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
        agg = df[df["win"].notna()].groupby("month")["win"].agg(["mean", "count"])
        if len(agg) >= 6:
            h("### 5e. Monthly Seasonality\n")
            h("| Month | Win Rate | N Trades |")
            h("|-------|---------|---------|")
            for m, row in agg.iterrows():
                if row["count"] >= 5:
                    h(f"| {MONTHS.get(int(m), m)} | {row['mean']:.1%} | {int(row['count']):,} |")
            h("")

    # Day-of-week
    if "day_of_week" in df.columns and "win" in df.columns:
        DAYS = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri"}
        agg = df[df["win"].notna()].groupby("day_of_week")["win"].agg(["mean", "count"])
        if len(agg) >= 3:
            h("### 5f. Day-of-Week vs Win Rate\n")
            h("| Day | Win Rate | N Trades |")
            h("|-----|---------|---------|")
            for d, row in agg.iterrows():
                if row["count"] >= 5:
                    h(f"| {DAYS.get(int(d), d)} | {row['mean']:.1%} | {int(row['count']):,} |")
            h("")

    # SPY trend
    if "spy_ret_20d" in df.columns and "win" in df.columns:
        s = df[df["spy_ret_20d"].notna() & df["win"].notna()].copy()
        if len(s) >= 100:
            bins = [-1, -0.05, -0.02, 0.02, 0.05, 1]
            labels = ["SPY<-5%", "SPY -5–-2%", "SPY ±2%", "SPY +2–+5%", "SPY>+5%"]
            s["spy_bucket"] = pd.cut(s["spy_ret_20d"], bins=bins, labels=labels)
            agg = s.groupby("spy_bucket")["win"].agg(["mean", "count"])
            h("### 5g. SPY 20-Day Return at Entry vs Win Rate\n")
            h("| SPY Trend | Win Rate | N Trades |")
            h("|-----------|---------|---------|")
            for bkt, row in agg.iterrows():
                if row["count"] >= 10:
                    h(f"| {bkt} | {row['mean']:.1%} | {int(row['count']):,} |")
            h("")

    # RSI
    if "rsi_14" in df.columns and "win" in df.columns:
        r = df[df["rsi_14"].notna() & df["win"].notna()].copy()
        if len(r) >= 100:
            bins = [0, 30, 40, 50, 60, 70, 100]
            labels = ["<30", "30–40", "40–50", "50–60", "60–70", ">70"]
            r["rsi_bucket"] = pd.cut(r["rsi_14"], bins=bins, labels=labels)
            agg = r.groupby("rsi_bucket")["win"].agg(["mean", "count"])
            h("### 5h. SPY RSI(14) at Entry vs Win Rate\n")
            h("| RSI Range | Win Rate | N Trades |")
            h("|-----------|---------|---------|")
            for bkt, row in agg.iterrows():
                if row["count"] >= 10:
                    h(f"| RSI {bkt} | {row['mean']:.1%} | {int(row['count']):,} |")
            h("")

    # ── 6. Recommendations ──────────────────────────────────────────────
    h("## 6. Recommendations\n")

    h("### 6a. Prioritized Signals\n")
    h("Ranked by XGBoost gain (information contribution to win/loss prediction):\n")
    h("| Rank | Feature | Gain% | Group | Action |")
    h("|------|---------|-------|-------|--------|")
    ACTION = {
        "vix_level":          "Use VIX gate; tune thresholds by regime",
        "vix_regime_cat":     "Regime-gate entries; reduce size in VIX 25–35",
        "dte_at_entry":       "Optimise DTE range; avoid extremes",
        "credit_pct_of_width":"Set minimum credit floor (≥15–20% of width)",
        "otm_pct":            "Calibrate OTM% per vol regime",
        "rsi_14":             "Filter entries at RSI extremes (<30 or >70)",
        "spy_ret_20d":        "Avoid entries after SPY >+5% or <-5% 20d run",
        "spy_rvol_20d":       "Reduce size when realized vol spikes",
        "macd_hist":          "Use MACD histogram sign as trend confirmation",
        "bb_position":        "Avoid entries when SPY near BB extremes",
        "spread_width":       "Standardise width to capture consistent risk/reward",
        "month":              "Apply seasonal overlay; reduce Sep/Oct exposure",
        "days_to_opex":       "Favour entries 10–25d before OpEx",
        "day_of_week":        "Prefer Tue/Wed entries; avoid Mon open",
        "pc_vol_ratio":       "Use as contrarian sentiment signal",
        "config_risk_pct":    "Risk sizing is a top lever — respect Kelly limits",
        "config_sl_mult":     "Stop-loss tightness significantly affects P50",
        "is_bull_put":        "Direction matters; confirm with regime model",
    }
    for rank, row in importances.head(15).iterrows():
        feat = row["feature"]
        grp = next((g for g, fs in FEATURE_GROUPS.items() if feat in fs), "Other")
        action = ACTION.get(feat, "Monitor and validate")
        h(f"| {rank+1} | `{feat}` | {row['gain_pct']:.1f}% | {grp} | {action} |")
    h("")

    h("### 6b. Signal Strength Assessment\n")
    if auc >= 0.65:
        h(f"Model AUC = **{auc:.3f}** — features carry strong predictive signal. "
          "Use predicted win probability as a position-sizing multiplier.")
    elif auc >= 0.58:
        h(f"Model AUC = **{auc:.3f}** — moderate signal. Features useful for filtering "
          "marginal setups; not strong enough for full Kelly sizing.")
    else:
        h(f"Model AUC = **{auc:.3f}** — weak/no signal. This is expected for high-win-rate "
          "strategies: most trades win, so entry features have limited discriminating power. "
          "The real signal lives in *loss magnitude* (return_pct), not win/loss binary.")
    h("")

    h("### 6c. Entry Filter Recommendations\n")
    recs: list[str] = []

    if "vix_level" in df.columns and "win" in df.columns:
        vix_sub = df[df["vix_level"].notna() & df["win"].notna()]
        wr_high = vix_sub[vix_sub["vix_level"] > 35]["win"].mean()
        wr_norm = vix_sub[(vix_sub["vix_level"] >= 15) & (vix_sub["vix_level"] < 25)]["win"].mean()
        if not np.isnan(wr_high) and not np.isnan(wr_norm) and wr_high < wr_norm - 0.05:
            recs.append(
                f"- **VIX Gate**: Win rate drops to {wr_high:.1%} when VIX > 35 "
                f"(vs {wr_norm:.1%} in VIX 15–25). Confirm vix_max_entry=35 or lower."
            )

    if "credit_pct_of_width" in df.columns and "win" in df.columns:
        cred = df[
            df["credit_pct_of_width"].notna() & df["win"].notna()
            & (df["credit_pct_of_width"] > 0) & (df["credit_pct_of_width"] < 1)
        ]
        wr_hi_cred = cred[cred["credit_pct_of_width"] >= 0.20]["win"].mean()
        wr_lo_cred = cred[cred["credit_pct_of_width"] < 0.12]["win"].mean()
        if not np.isnan(wr_hi_cred) and not np.isnan(wr_lo_cred):
            delta = wr_hi_cred - wr_lo_cred
            if abs(delta) > 0.03:
                direction = "higher" if delta > 0 else "lower"
                recs.append(
                    f"- **Credit Floor**: Credit ≥ 20% of width → {wr_hi_cred:.1%} win rate "
                    f"vs {wr_lo_cred:.1%} for < 12% ({direction} credit = {direction} win rate). "
                    "Enforce min_credit_pct in entry filter."
                )

    if "otm_pct" in df.columns and "win" in df.columns:
        otm = df[df["otm_pct"].notna() & df["win"].notna() & (df["otm_pct"] >= 0)]
        wr_2_3 = otm[(otm["otm_pct"] >= 0.02) & (otm["otm_pct"] < 0.04)]["win"].mean()
        wr_4_6 = otm[(otm["otm_pct"] >= 0.04) & (otm["otm_pct"] < 0.06)]["win"].mean()
        if not np.isnan(wr_2_3) and not np.isnan(wr_4_6):
            best = max(wr_2_3, wr_4_6)
            best_range = "2–4%" if wr_2_3 >= wr_4_6 else "4–6%"
            recs.append(
                f"- **OTM Sweet Spot**: Highest win rate ({best:.1%}) in {best_range} OTM range. "
                "Prefer 3% OTM as a balance between credit and safety margin."
            )

    if not recs:
        recs = [
            "- Current entry parameters appear well-tuned relative to available signal.",
            "- Focus on *risk management* (stop-loss, position sizing) rather than entry filters — "
            "this is where most P50 improvement opportunity lies (see IC risk sweep findings).",
        ]
    for r in recs:
        h(r)
    h("")

    h("### 6d. Regime-Specific Signal Priorities\n")
    for label, res in regime_results.items():
        imp = res.get("importances")
        if imp is None or len(imp) == 0:
            continue
        top3 = imp.head(3)["feature"].tolist()
        wr = res.get("win_rate")
        wr_str = f"{wr:.1%}" if isinstance(wr, float) else "N/A"
        h(f"- **{label}** (win_rate={wr_str}): prioritise `{'`, `'.join(top3)}`")
    h("")

    h("### 6e. Data Quality Notes\n")
    h("- MC trade records share market conditions across seeds (only DTE varies). "
      "Win/loss labels are therefore correlated within same calendar date. "
      "AUC is likely *optimistically* biased — treat as upper bound.\n")
    h("- Missing market features (VIX, SPY technicals) reduce usable sample. "
      "Ensure `data/macro_state.db` is up to date for best coverage.\n")
    h("- For IV rank / IV percentile features: not available in current data. "
      "Proxy used: `credit_pct_of_width` (higher credit ≈ higher implied vol).\n")

    h("---")
    h(f"\n_Pipeline: XGBoost 5-fold stratified CV | Features: {len(avail_cols)} | "
      f"Trades used for ML: {len(df):,}_")

    return "\n".join(L)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-mc-files", type=int, default=20)
    parser.add_argument("--sample-seeds",  type=int, default=50,
                        help="Max seeds per MC file to sample (0 = all)")
    args = parser.parse_args()

    print("=" * 60)
    print("ML Feature Analysis Pipeline")
    print("=" * 60)

    # ── 1. Load raw trades ───────────────────────────────────────────
    print("\n[1/6] Loading trade data...")
    mc_df    = load_mc_trades(max_files=args.max_mc_files, sample_seeds=args.sample_seeds)
    db_df    = load_db_trades()
    paper_df = load_paper_trades()

    dfs = [df for df in [mc_df, db_df, paper_df] if not df.empty]
    if not dfs:
        print("ERROR: No trade data found.")
        sys.exit(1)

    raw_df = pd.concat(dfs, ignore_index=True, sort=False)
    print(f"  Raw trades combined: {len(raw_df):,}")

    # ── 2. Market data ───────────────────────────────────────────────
    print("\n[2/6] Loading market data...")
    spy_df, vix_df = load_market_data()
    spy_tech = compute_spy_technicals(spy_df) if not spy_df.empty else pd.DataFrame()
    pc_df    = load_put_call_data()

    # ── 3. Feature engineering ───────────────────────────────────────
    print("\n[3/6] Engineering features...")
    feat_df = engineer_features(raw_df, spy_tech, vix_df, pc_df)

    if feat_df.empty:
        print("ERROR: Feature engineering yielded no records.")
        sys.exit(1)

    # ── Build ML matrix ──────────────────────────────────────────────
    avail_cols = [c for c in FEATURE_COLS if c in feat_df.columns]
    ml_df = feat_df[feat_df["win"].notna()].copy()

    # Require at least 1/3 of features populated
    min_feats = max(4, len(avail_cols) // 3)
    ml_df = ml_df[ml_df[avail_cols].notna().sum(axis=1) >= min_feats].copy()

    print(f"\n  ML-ready: {len(ml_df):,} trades × {len(avail_cols)} features")
    print(f"  Win rate: {ml_df['win'].mean():.1%}")

    if len(ml_df) < 100:
        print("WARNING: fewer than 100 labeled trades — results may not be reliable.")

    X = ml_df[avail_cols].fillna(ml_df[avail_cols].median())
    y = ml_df["win"].astype(int)

    # ── 4. Train model ───────────────────────────────────────────────
    print("\n[4/6] Training XGBoost model...")
    model, cv_scores = train_xgb(X, y)
    importances = get_importances(model, avail_cols)

    print(f"  CV AUC: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")
    print("  Top 5 features by gain:")
    for _, row in importances.head(5).iterrows():
        print(f"    {row['feature']}: {row['gain_pct']:.1f}%")

    print("  Attempting SHAP analysis...")
    shap_df = try_shap(model, X)
    if shap_df is not None:
        print(f"  SHAP: computed for {len(shap_df)} features")
    else:
        print("  SHAP: not available (pip install shap)")

    # ── 5. Regime analysis ───────────────────────────────────────────
    print("\n[5/6] Regime-conditional analysis...")
    regime_masks: dict[str, pd.Series] = {}

    if "vix_level" in ml_df.columns and ml_df["vix_level"].notna().sum() > 50:
        regime_masks["Low Vol (VIX < 20)"]    = ml_df["vix_level"] < 20
        regime_masks["Normal Vol (VIX 20–30)"] = (ml_df["vix_level"] >= 20) & (ml_df["vix_level"] < 30)
        regime_masks["High Vol (VIX > 30)"]   = ml_df["vix_level"] >= 30

    if "is_bull_put" in ml_df.columns:
        regime_masks["Bull Put Spreads"] = ml_df["is_bull_put"] == 1
        regime_masks["Bear Call Spreads"] = ml_df["is_bear_call"] == 1
        if "is_iron_condor" in ml_df.columns and ml_df["is_iron_condor"].sum() >= 50:
            regime_masks["Iron Condors"] = ml_df["is_iron_condor"] == 1

    if "macro_regime" in ml_df.columns:
        for rv in ml_df["macro_regime"].dropna().unique():
            mask = ml_df["macro_regime"] == rv
            if mask.sum() >= 50:
                regime_masks[f"Macro: {str(rv).title()}"] = mask

    if "year" in ml_df.columns:
        for yr in [2020, 2021, 2022, 2023, 2024, 2025]:
            mask = ml_df["year"] == yr
            if mask.sum() >= 50:
                regime_masks[f"Year {yr}"] = mask

    regime_results = regime_analysis(ml_df, avail_cols, regime_masks)

    for label, res in regime_results.items():
        n  = res.get("n", 0)
        wr = res.get("win_rate", "—")
        wr_str = f"{wr:.1%}" if isinstance(wr, float) else str(wr)
        status = "✓" if "importances" in res else "✗"
        print(f"  {status} {label}: n={n}, win_rate={wr_str}")

    # ── 6. Generate report ───────────────────────────────────────────
    print("\n[6/6] Generating report...")
    report = generate_report(
        ml_df, cv_scores, importances, shap_df, regime_results, avail_cols
    )

    report_path = OUTPUT_DIR / "ml_feature_analysis_report.md"
    report_path.write_text(report)

    print(f"\n✓  Report → {report_path}")
    print(f"   {len(report.splitlines())} lines")
    print("\nTop 10 features (gain):")
    for _, row in importances.head(10).iterrows():
        bar = "█" * int(row["gain_pct"] / 2)
        print(f"  {row['feature']:30s} {row['gain_pct']:5.1f}% {bar}")


if __name__ == "__main__":
    main()
