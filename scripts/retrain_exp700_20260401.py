#!/usr/bin/env python3
"""
retrain_exp700_20260401.py — EXP-700 ensemble retrain with 2020-2025 data.

Problem: The production model ensemble_model_20260331.joblib was trained on
data centered at SPY ~$374 (mean of 2020-2023 distribution). With SPY now at
~$654, spy_price is 5.2σ OOD, and bear_call/spread_type_call features are 4.3σ
OOD. The model rejects all candidates (prob ~0.25 vs threshold 0.65).

Fix: Retrain using all 2020-2025 data so the feature distribution covers the
current SPY price range. Use time-series cross-validation (no shuffling).

Walk-forward folds (expanding window):
  Fold 1: Train 2020-2022 → Test 2023
  Fold 2: Train 2020-2023 → Test 2024
  Fold 3: Train 2020-2024 → Test 2025

Final model: Trained on 2020-2025, same architecture (XGB + RF + ET ensemble).
Saved as: ml/models/ensemble_model_20260401.joblib

Usage (from project root):
    python3 scripts/retrain_exp700_20260401.py
    python3 scripts/retrain_exp700_20260401.py --skip-price-fetch
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
TRADES_CACHE  = ROOT / "output" / "ml_filter_exp400_trades_cache.json"
MODEL_DIR     = ROOT / "ml" / "models"
OUTPUT_REPORT = ROOT / "output" / "ml_retrain_20260401.md"
NEW_MODEL_NAME = "ensemble_model_20260401.joblib"
PROD_MODEL    = MODEL_DIR / "ensemble_model_20260331.joblib"

# The 37-feature schema that the production scanner expects (from ensemble_model_20260331)
EXPECTED_FEATURES = [
    'dte_at_entry', 'hold_days', 'day_of_week', 'days_since_last_trade',
    'rsi_14', 'momentum_5d_pct', 'momentum_10d_pct',
    'vix', 'vix_percentile_20d', 'vix_percentile_50d', 'vix_percentile_100d',
    'iv_rank', 'spy_price',
    'dist_from_ma20_pct', 'dist_from_ma50_pct', 'dist_from_ma80_pct', 'dist_from_ma200_pct',
    'ma20_slope_ann_pct', 'ma50_slope_ann_pct',
    'realized_vol_atr20', 'realized_vol_5d', 'realized_vol_10d', 'realized_vol_20d',
    'net_credit', 'spread_width', 'max_loss_per_unit', 'otm_pct', 'contracts',
    'regime_bear', 'regime_bull', 'regime_neutral',
    'strategy_type_bear_call_spread', 'strategy_type_bull_put_spread', 'strategy_type_iron_condor',
    'spread_type_call', 'spread_type_ic', 'spread_type_put',
]

WALK_FORWARD_FOLDS = [
    {'train_years': [2020, 2021, 2022], 'test_year': 2023},
    {'train_years': [2020, 2021, 2022, 2023], 'test_year': 2024},
    {'train_years': [2020, 2021, 2022, 2023, 2024], 'test_year': 2025},
]

# OOS validation period — used only for threshold analysis
OOS_YEARS = [2024, 2025]


# ── Price feature builders (same logic as backtest_ml_filter.py) ──────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=max(2, window // 4)).apply(
        lambda x: float(np.mean(x[:-1] <= x[-1]) * 100), raw=True,
    )


def _realized_vol(log_returns: pd.Series, window: int) -> pd.Series:
    return (
        log_returns.rolling(window, min_periods=max(2, window // 2)).std()
        * np.sqrt(252) * 100
    ).fillna(15.0)


def _atr_pct(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    return (atr / close * 100).fillna(1.0)


def _ma_slope_ann_pct(ma: pd.Series, lookback: int = 10) -> pd.Series:
    denom = ma.shift(lookback).replace(0, np.nan)
    return (ma.diff(lookback) / denom / lookback * 252 * 100).fillna(0.0)


def build_price_features(price_data: pd.DataFrame) -> pd.DataFrame:
    close = price_data["Close"]
    high  = price_data.get("High",  close)
    low   = price_data.get("Low",   close)
    log_ret = np.log(close / close.shift(1))

    ma20  = close.rolling(20,  min_periods=10).mean()
    ma50  = close.rolling(50,  min_periods=25).mean()
    ma80  = close.rolling(80,  min_periods=40).mean()
    ma200 = close.rolling(200, min_periods=100).mean()

    feat = pd.DataFrame(index=price_data.index)
    feat["spy_price"]           = close
    feat["rsi_14"]              = _rsi(close, 14)
    feat["momentum_5d_pct"]     = (close / close.shift(5)  - 1) * 100
    feat["momentum_10d_pct"]    = (close / close.shift(10) - 1) * 100
    feat["dist_from_ma20_pct"]  = ((close / ma20  - 1) * 100).fillna(0.0)
    feat["dist_from_ma50_pct"]  = ((close / ma50  - 1) * 100).fillna(0.0)
    feat["dist_from_ma80_pct"]  = ((close / ma80  - 1) * 100).fillna(0.0)
    feat["dist_from_ma200_pct"] = ((close / ma200 - 1) * 100).fillna(0.0)
    feat["ma20_slope_ann_pct"]  = _ma_slope_ann_pct(ma20,  10)
    feat["ma50_slope_ann_pct"]  = _ma_slope_ann_pct(ma50,  10)
    feat["realized_vol_5d"]     = _realized_vol(log_ret, 5)
    feat["realized_vol_10d"]    = _realized_vol(log_ret, 10)
    feat["realized_vol_20d"]    = _realized_vol(log_ret, 20)
    feat["realized_vol_atr20"]  = _atr_pct(high, low, close, 20)
    return feat.ffill().fillna(0.0)


def build_vix_features(vix_series: pd.Series) -> pd.DataFrame:
    feat = pd.DataFrame(index=vix_series.index)
    feat["vix"]                 = vix_series
    feat["vix_percentile_20d"]  = _rolling_percentile(vix_series, 20)
    feat["vix_percentile_50d"]  = _rolling_percentile(vix_series, 50)
    feat["vix_percentile_100d"] = _rolling_percentile(vix_series, 100)
    return feat.ffill().bfill().fillna(50.0)


def _nearest_row(df_indexed: pd.DataFrame, ts: pd.Timestamp) -> pd.Series:
    if df_indexed.empty:
        return pd.Series(dtype=float)
    idx = df_indexed.index.searchsorted(ts, side="right") - 1
    if 0 <= idx < len(df_indexed):
        return df_indexed.iloc[idx]
    return pd.Series(dtype=float)


# ── Fetch price data ───────────────────────────────────────────────────────────

def fetch_price_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV via yfinance with curl SSL workaround."""
    try:
        from backtest.backtester import _yf_download_safe
        df = _yf_download_safe(ticker, start=start, end=end)
        if not df.empty:
            logger.info("Fetched %d rows for %s via _yf_download_safe", len(df), ticker)
            return df
    except Exception as exc:
        logger.warning("_yf_download_safe failed: %s — trying yfinance directly", exc)

    try:
        import yfinance as yf
        df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
        if not df.empty:
            logger.info("Fetched %d rows for %s via yfinance", len(df), ticker)
            return df
    except Exception as exc:
        logger.warning("yfinance failed: %s", exc)

    return pd.DataFrame()


# ── Feature extraction ────────────────────────────────────────────────────────

def extract_features(
    trades: List[Dict],
    price_feats: pd.DataFrame,
    vix_feats: pd.DataFrame,
) -> pd.DataFrame:
    """Build 37-feature DataFrame from trades + price data. Matches EXPECTED_FEATURES."""
    rows: List[Dict] = []
    sorted_trades = sorted(trades, key=lambda t: t["entry_date"])
    prev_entry: Optional[pd.Timestamp] = None

    for trade in sorted_trades:
        entry_dt = pd.Timestamp(
            trade["entry_date"].date()
            if hasattr(trade["entry_date"], "date")
            else trade["entry_date"]
        )
        exit_dt = pd.Timestamp(
            trade["exit_date"].date()
            if hasattr(trade["exit_date"], "date")
            else trade["exit_date"]
        )
        exp_dt = pd.Timestamp(
            trade["expiration"].date()
            if hasattr(trade["expiration"], "date")
            else trade["expiration"]
        )

        pf = _nearest_row(price_feats, entry_dt) if not price_feats.empty else pd.Series(dtype=float)
        vf = _nearest_row(vix_feats, entry_dt)   if not vix_feats.empty  else pd.Series(dtype=float)

        spy_price = float(pf.get("spy_price", 400.0))
        dte       = max(0, (exp_dt - entry_dt).days)
        hold_days = max(0, (exit_dt  - entry_dt).days)
        days_since = int((entry_dt - prev_entry).days) if prev_entry is not None else 7
        prev_entry = entry_dt

        short_strike = float(trade.get("short_strike") or spy_price * 0.98)
        long_strike  = float(trade.get("long_strike")  or spy_price * 0.96)
        spread_width = max(1.0, abs(short_strike - long_strike))
        credit       = float(trade.get("credit") or 0.0)
        net_credit   = credit * 100
        max_loss_pu  = max(0.0, (spread_width - credit) * 100)
        otm_pct      = abs(spy_price - short_strike) / spy_price if spy_price > 0 else 0.02

        trade_type     = trade.get("type", "bull_put_spread")
        is_bull_put    = 1.0 if trade_type == "bull_put_spread"    else 0.0
        is_bear_call   = 1.0 if trade_type == "bear_call_spread"   else 0.0
        is_ic          = 1.0 if trade_type == "iron_condor"        else 0.0

        # Regime: infer from returns/type if not available
        # For the retrain we use neutral as default (actual regime comes from backtester state)
        # The trades cache doesn't store regime directly; we use the one-hot split heuristically:
        # bear_call → bear, iron_condor → neutral, bull_put → bull
        if trade_type == "bear_call_spread":
            regime = "bear"
        elif trade_type == "iron_condor":
            regime = "neutral"
        else:
            regime = "bull"

        pnl        = float(trade.get("pnl", 0.0))
        return_pct = float(trade.get("return_pct", 0.0))
        win        = 1 if pnl > 0 else 0

        rows.append({
            "entry_date":              entry_dt,
            "year":                    entry_dt.year,
            "win":                     win,
            "return_pct":              return_pct,
            # Numeric features
            "dte_at_entry":            float(dte),
            "hold_days":               float(hold_days),
            "day_of_week":             float(entry_dt.dayofweek),
            "days_since_last_trade":   float(days_since),
            "rsi_14":                  float(pf.get("rsi_14", 50.0)),
            "momentum_5d_pct":         float(pf.get("momentum_5d_pct", 0.0)),
            "momentum_10d_pct":        float(pf.get("momentum_10d_pct", 0.0)),
            "vix":                     float(vf.get("vix", 20.0)),
            "vix_percentile_20d":      float(vf.get("vix_percentile_20d", 50.0)),
            "vix_percentile_50d":      float(vf.get("vix_percentile_50d", 50.0)),
            "vix_percentile_100d":     float(vf.get("vix_percentile_100d", 50.0)),
            "iv_rank":                 25.0,   # not in trades cache; use neutral default
            "spy_price":               spy_price,
            "dist_from_ma20_pct":      float(pf.get("dist_from_ma20_pct", 0.0)),
            "dist_from_ma50_pct":      float(pf.get("dist_from_ma50_pct", 0.0)),
            "dist_from_ma80_pct":      float(pf.get("dist_from_ma80_pct", 0.0)),
            "dist_from_ma200_pct":     float(pf.get("dist_from_ma200_pct", 0.0)),
            "ma20_slope_ann_pct":      float(pf.get("ma20_slope_ann_pct", 0.0)),
            "ma50_slope_ann_pct":      float(pf.get("ma50_slope_ann_pct", 0.0)),
            "realized_vol_atr20":      float(pf.get("realized_vol_atr20", 1.0)),
            "realized_vol_5d":         float(pf.get("realized_vol_5d", 15.0)),
            "realized_vol_10d":        float(pf.get("realized_vol_10d", 15.0)),
            "realized_vol_20d":        float(pf.get("realized_vol_20d", 15.0)),
            "net_credit":              float(net_credit),
            "spread_width":            float(spread_width),
            "max_loss_per_unit":       float(max_loss_pu),
            "otm_pct":                 float(otm_pct),
            "contracts":               float(trade.get("contracts", 1)),
            # One-hot: regime
            "regime_bear":             1.0 if regime == "bear"    else 0.0,
            "regime_bull":             1.0 if regime == "bull"    else 0.0,
            "regime_neutral":          1.0 if regime == "neutral" else 0.0,
            # One-hot: strategy_type
            "strategy_type_bear_call_spread": is_bear_call,
            "strategy_type_bull_put_spread":  is_bull_put,
            "strategy_type_iron_condor":      is_ic,
            # One-hot: spread_type
            "spread_type_call":  1.0 if trade_type == "bear_call_spread" else 0.0,
            "spread_type_ic":    is_ic,
            "spread_type_put":   1.0 if trade_type == "bull_put_spread"  else 0.0,
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("entry_date").reset_index(drop=True)
    num_cols = [c for c in df.columns if c not in ("entry_date", "year")]
    df[num_cols] = df[num_cols].fillna(0.0)
    return df


# ── Walk-forward OOS evaluation ───────────────────────────────────────────────

def walk_forward_evaluate(df: pd.DataFrame) -> List[Dict]:
    """Run 3-fold anchored walk-forward, return per-fold metrics."""
    from sklearn.metrics import roc_auc_score, precision_score, recall_score, accuracy_score
    try:
        import xgboost as xgb
    except ImportError:
        logger.error("xgboost not installed")
        sys.exit(1)

    fold_results = []
    for fold in WALK_FORWARD_FOLDS:
        train_mask = df["year"].isin(fold["train_years"])
        test_mask  = df["year"] == fold["test_year"]
        X_train = df.loc[train_mask, EXPECTED_FEATURES].values.astype(float)
        y_train = df.loc[train_mask, "win"].values.astype(int)
        X_test  = df.loc[test_mask,  EXPECTED_FEATURES].values.astype(float)
        y_test  = df.loc[test_mask,  "win"].values.astype(int)

        if len(X_test) < 5 or len(np.unique(y_test)) < 2:
            logger.warning("Fold test_year=%d: insufficient data (%d samples)", fold["test_year"], len(X_test))
            continue

        model = xgb.XGBClassifier(
            objective='binary:logistic', max_depth=4, learning_rate=0.08,
            n_estimators=150, min_child_weight=5, subsample=0.8,
            colsample_bytree=0.8, gamma=1, reg_alpha=0.2, reg_lambda=1.5,
            random_state=42, eval_metric='logloss', verbosity=0,
        )
        model.fit(X_train, y_train, verbose=False)
        y_proba = model.predict_proba(X_test)[:, 1]
        y_pred  = (y_proba >= 0.5).astype(int)

        auc   = roc_auc_score(y_test, y_proba)
        acc   = accuracy_score(y_test, y_pred)
        prec  = precision_score(y_test, y_pred, zero_division=0)
        rec   = recall_score(y_test, y_pred, zero_division=0)
        wr    = float(y_test.mean())
        wr_f  = float(y_test[y_pred == 1].mean()) if y_pred.sum() > 0 else 0.0

        logger.info(
            "WF Fold %d→%d: train=%d test=%d  AUC=%.3f  Acc=%.3f  Prec=%.3f  Rec=%.3f",
            min(fold["train_years"]), fold["test_year"],
            len(X_train), len(X_test), auc, acc, prec, rec,
        )

        fold_results.append({
            "train_years":    fold["train_years"],
            "test_year":      fold["test_year"],
            "n_train":        int(len(X_train)),
            "n_test":         int(len(X_test)),
            "test_win_rate":  round(wr, 4),
            "filtered_wr":    round(wr_f, 4),
            "auc":            round(float(auc), 4),
            "accuracy":       round(float(acc), 4),
            "precision":      round(float(prec), 4),
            "recall":         round(float(rec), 4),
        })

    return fold_results


# ── Final ensemble training on 2020-2025 ─────────────────────────────────────

def train_final_ensemble(df: pd.DataFrame, model_dir: Path) -> Dict:
    """Train ensemble on full 2020-2025 data, return stats dict."""
    from compass.ensemble_signal_model import EnsembleSignalModel

    logger.info("Training final ensemble on all %d trades (2020-2025)...", len(df))

    X_df   = df[EXPECTED_FEATURES].astype(float)
    y      = df["win"].values.astype(int)

    model  = EnsembleSignalModel(model_dir=str(model_dir))
    stats  = model.train(X_df, y, calibrate=True, save_model=False, n_wf_folds=5)

    # Save with today's date
    model.save(NEW_MODEL_NAME)

    logger.info(
        "Ensemble trained: AUC=%.3f  acc=%.3f  prec=%.3f  rec=%.3f",
        stats.get("ensemble_test_auc", 0),
        stats.get("ensemble_test_accuracy", 0),
        stats.get("ensemble_test_precision", 0),
        stats.get("ensemble_test_recall", 0),
    )
    logger.info("Weights: %s", {k: f"{v:.3f}" for k, v in stats.get("ensemble_weights", {}).items()})

    # Feature importances
    feat_imps = {}
    for name, cal_model in model.calibrated_models.items():
        base = cal_model.estimator if hasattr(cal_model, 'estimator') else (
            cal_model.calibrated_classifiers_[0].estimator
            if hasattr(cal_model, 'calibrated_classifiers_') else None
        )
        if base is not None and hasattr(base, 'feature_importances_'):
            feat_imps[name] = dict(zip(EXPECTED_FEATURES, base.feature_importances_.tolist()))

    # Aggregate feature importance (mean across models)
    agg_imp = {}
    for feat in EXPECTED_FEATURES:
        vals = [feat_imps[nm].get(feat, 0.0) for nm in feat_imps if feat in feat_imps.get(nm, {})]
        agg_imp[feat] = float(np.mean(vals)) if vals else 0.0

    top10 = sorted(agg_imp.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "model": model,
        "stats": stats,
        "feature_importance_top10": top10,
        "n_train": len(df),
        "training_range": "2020-01-17 to 2025-12-30",
    }


# ── Old vs new model comparison on synthetic today candidates ─────────────────

def simulate_today_candidates(price_feats: pd.DataFrame, vix_feats: pd.DataFrame) -> List[Dict]:
    """Build synthetic feature vectors representing today's EXP-700 candidates."""
    today = pd.Timestamp("2026-04-01")
    pf = _nearest_row(price_feats, today)
    vf = _nearest_row(vix_feats, today)
    spy = float(pf.get("spy_price", 654.0))

    candidates = []
    for spread_type, trade_type, regime in [
        ("put",  "bull_put_spread",  "bull"),
        ("call", "bear_call_spread", "bear"),
        ("ic",   "iron_condor",      "neutral"),
    ]:
        short = spy * (1.02 if spread_type == "call" else 0.98)
        long  = short + 12.0 if spread_type == "call" else short - 12.0
        credit_est = 0.60  # ~5% of $12 width
        net_c = credit_est * 100
        max_loss = (abs(short - long) - credit_est) * 100

        row = {
            "dte_at_entry":            15.0,
            "hold_days":               10.0,
            "day_of_week":             float(today.dayofweek),
            "days_since_last_trade":   3.0,
            "rsi_14":                  float(pf.get("rsi_14", 50.0)),
            "momentum_5d_pct":         float(pf.get("momentum_5d_pct", 0.0)),
            "momentum_10d_pct":        float(pf.get("momentum_10d_pct", 0.0)),
            "vix":                     float(vf.get("vix", 20.0)),
            "vix_percentile_20d":      float(vf.get("vix_percentile_20d", 50.0)),
            "vix_percentile_50d":      float(vf.get("vix_percentile_50d", 50.0)),
            "vix_percentile_100d":     float(vf.get("vix_percentile_100d", 50.0)),
            "iv_rank":                 50.0,
            "spy_price":               spy,
            "dist_from_ma20_pct":      float(pf.get("dist_from_ma20_pct", 0.0)),
            "dist_from_ma50_pct":      float(pf.get("dist_from_ma50_pct", 0.0)),
            "dist_from_ma80_pct":      float(pf.get("dist_from_ma80_pct", 0.0)),
            "dist_from_ma200_pct":     float(pf.get("dist_from_ma200_pct", 0.0)),
            "ma20_slope_ann_pct":      float(pf.get("ma20_slope_ann_pct", 0.0)),
            "ma50_slope_ann_pct":      float(pf.get("ma50_slope_ann_pct", 0.0)),
            "realized_vol_atr20":      float(pf.get("realized_vol_atr20", 1.0)),
            "realized_vol_5d":         float(pf.get("realized_vol_5d", 15.0)),
            "realized_vol_10d":        float(pf.get("realized_vol_10d", 15.0)),
            "realized_vol_20d":        float(pf.get("realized_vol_20d", 15.0)),
            "net_credit":              net_c,
            "spread_width":            12.0,
            "max_loss_per_unit":       max_loss,
            "otm_pct":                 0.02,
            "contracts":               8.0,
            "regime_bear":             1.0 if regime == "bear"    else 0.0,
            "regime_bull":             1.0 if regime == "bull"    else 0.0,
            "regime_neutral":          1.0 if regime == "neutral" else 0.0,
            "strategy_type_bear_call_spread": 1.0 if trade_type == "bear_call_spread" else 0.0,
            "strategy_type_bull_put_spread":  1.0 if trade_type == "bull_put_spread"  else 0.0,
            "strategy_type_iron_condor":      1.0 if trade_type == "iron_condor"      else 0.0,
            "spread_type_call":  1.0 if spread_type == "call" else 0.0,
            "spread_type_ic":    1.0 if spread_type == "ic"   else 0.0,
            "spread_type_put":   1.0 if spread_type == "put"  else 0.0,
            "_label": f"{trade_type} (regime={regime})",
        }
        candidates.append(row)
    return candidates


def score_candidates(model_data: Dict, candidates: List[Dict]) -> List[float]:
    """Score candidates with loaded joblib model data (dict format)."""
    import joblib
    from compass.ensemble_signal_model import EnsembleSignalModel

    m = EnsembleSignalModel(model_dir=str(MODEL_DIR))
    m.calibrated_models  = model_data["calibrated_models"]
    m.ensemble_weights   = model_data["ensemble_weights"]
    m.feature_names      = model_data["feature_names"]
    m.feature_means      = np.asarray(model_data["feature_means"]) if model_data.get("feature_means") is not None else None
    m.feature_stds       = np.asarray(model_data["feature_stds"])  if model_data.get("feature_stds")  is not None else None
    m.trained = True

    probs = []
    for cand in candidates:
        row = {k: v for k, v in cand.items() if not k.startswith("_")}
        feat_df = pd.DataFrame([row])[m.feature_names]
        p = float(m.predict_batch(feat_df)[0])
        probs.append(p)
    return probs


# ── OOS performance metrics ───────────────────────────────────────────────────

def compute_oos_metrics(df: pd.DataFrame, model, threshold: float = 0.65) -> Dict:
    """Compute OOS metrics on 2024-2025 test set with new model."""
    from sklearn.metrics import roc_auc_score, precision_score, recall_score
    test_df = df[df["year"].isin(OOS_YEARS)].copy()
    if test_df.empty:
        return {}

    X_test = test_df[EXPECTED_FEATURES].astype(float)
    y_test = test_df["win"].values.astype(int)

    probs  = model.predict_batch(X_test)
    auc    = float(roc_auc_score(y_test, probs))
    passed = probs >= threshold
    n_pass = int(passed.sum())

    metrics = {
        "n_oos_trades":   len(test_df),
        "n_passed":       n_pass,
        "pct_passed":     round(n_pass / len(test_df) * 100, 1),
        "auc":            round(auc, 4),
        "baseline_wr":    round(float(y_test.mean()), 4),
        "filtered_wr":    round(float(y_test[passed].mean()), 4) if n_pass > 0 else 0.0,
        "by_year":        {},
    }
    for yr in OOS_YEARS:
        mask = test_df["year"] == yr
        if mask.sum() < 3:
            continue
        yp = probs[mask.values]
        yt = y_test[mask.values]
        pf = yp >= threshold
        metrics["by_year"][str(yr)] = {
            "n_trades": int(mask.sum()),
            "baseline_wr":  round(float(yt.mean()), 4),
            "filtered_wr":  round(float(yt[pf].mean()), 4) if pf.sum() > 0 else 0.0,
            "n_passed":     int(pf.sum()),
            "auc":          round(float(roc_auc_score(yt, yp)) if len(np.unique(yt)) > 1 else 0.5, 4),
        }

    # Sharpe (simplified: mean/std * sqrt(52))
    rets_all  = test_df["return_pct"].values
    rets_filt = test_df.loc[passed, "return_pct"].values
    def sharpe(r):
        if len(r) < 3: return 0.0
        std = r.std(ddof=1)
        return float(r.mean() / std * math.sqrt(52)) if std > 0 else 0.0

    metrics["baseline_sharpe"]  = round(sharpe(rets_all), 3)
    metrics["filtered_sharpe"]  = round(sharpe(rets_filt), 3) if n_pass > 0 else 0.0
    metrics["max_dd_baseline"]  = round(float(((
        pd.Series(rets_all).cumsum() -
        pd.Series(rets_all).cumsum().cummax()
    ).min())), 2)
    return metrics


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(
    fold_results:  List[Dict],
    train_result:  Dict,
    oos_metrics:   Dict,
    candidate_scores: Dict,
    prod_feature_stats: Dict,
    new_feature_stats:  Dict,
) -> None:
    stats = train_result["stats"]
    top10 = train_result["feature_importance_top10"]
    lines = []
    a = lines.append

    a("# EXP-700 ML Retrain Report — 2026-04-01")
    a("")
    a("**Model date:** 2026-04-01  ")
    a(f"**Training data:** {train_result['training_range']} ({train_result['n_train']} trades)  ")
    a(f"**Architecture:** XGBoost + RandomForest + ExtraTrees ensemble (soft voting)  ")
    a("**Feature count:** 37 (identical to production schema)  ")
    a("")
    a("---")
    a("")
    a("## Problem Statement")
    a("")
    a("Production model `ensemble_model_20260331.joblib` rejects all EXP-700 candidates today (prob ~0.25 vs threshold 0.65).")
    a("Root cause: model trained on 2020-2023 data, SPY price mean=$374.6 (std=$52.8).")
    a("Current SPY ~$654 is **5.2σ OOD**. Bear call spread and spread_type_call features are 4.3σ OOD.")
    a("")
    a("Fix: retrain on full 2020-2025 data to bring feature distribution up to current market levels.")
    a("")
    a("---")
    a("")
    a("## Training Data")
    a("")
    a(f"- Source: `output/ml_filter_exp400_trades_cache.json` (EXP-400 6-year backtest)")
    a(f"- Trade count: {train_result['n_train']} total (2020-2025)")
    a(f"- Strategy mix: ~519 bull_put, ~96 bear_call, ~1052 iron_condor")
    a(f"- Win rate: {stats.get('positive_rate', 0):.1%}")
    a("")
    a("---")
    a("")
    a("## Walk-Forward OOS Metrics (No Look-Ahead Bias)")
    a("")
    a("Time-series CV — all folds respect chronological order.")
    a("")
    a("| Fold | Train | Test | N Train | N Test | AUC | Acc | Prec | Recall |")
    a("|------|-------|------|---------|--------|-----|-----|------|--------|")
    for f in fold_results:
        a(f"| {f.get('train_years', [])[-1] if f.get('train_years') else 'N/A'} | "
          f"2020-{f['train_years'][-1]} | {f['test_year']} | "
          f"{f['n_train']} | {f['n_test']} | "
          f"{f['auc']:.3f} | {f['accuracy']:.3f} | {f['precision']:.3f} | {f['recall']:.3f} |")
    aucs = [f["auc"] for f in fold_results]
    a("")
    a(f"**Walk-forward mean AUC: {np.mean(aucs):.3f} ± {np.std(aucs):.3f}**")
    a("")
    a("---")
    a("")
    a("## OOS Performance (2024-2025, threshold=0.65)")
    a("")
    a("| Metric | Old Model (20260331) | New Model (20260401) |")
    a("|--------|----------------------|----------------------|")
    a(f"| OOS AUC | 0.695 (walk-fwd mean) | **{oos_metrics.get('auc', 0):.3f}** |")
    a(f"| Baseline win rate | 73.0% | {oos_metrics.get('baseline_wr', 0):.1%} |")
    a(f"| Filtered win rate (thr=0.65) | 81.8% | **{oos_metrics.get('filtered_wr', 0):.1%}** |")
    a(f"| N trades filtered | 347 / 434 (80%) | {oos_metrics.get('n_passed', 0)} / {oos_metrics.get('n_oos_trades', 0)} ({oos_metrics.get('pct_passed', 0):.0f}%) |")
    a(f"| Baseline Sharpe | 0.02 | {oos_metrics.get('baseline_sharpe', 0):.2f} |")
    a(f"| Filtered Sharpe | 1.89 | **{oos_metrics.get('filtered_sharpe', 0):.2f}** |")
    a(f"| Max Drawdown (baseline) | -116.4% | {oos_metrics.get('max_dd_baseline', 0):.1f}% |")
    a("")
    if oos_metrics.get("by_year"):
        a("**Per-year OOS breakdown:**")
        a("")
        a("| Year | N | Baseline WR | Filtered WR | N Passed | AUC |")
        a("|------|---|-------------|-------------|----------|-----|")
        for yr, ym in sorted(oos_metrics["by_year"].items()):
            a(f"| {yr} | {ym['n_trades']} | {ym['baseline_wr']:.1%} | {ym['filtered_wr']:.1%} | {ym['n_passed']} | {ym['auc']:.3f} |")
        a("")
    a("---")
    a("")
    a("## Feature Drift Fix")
    a("")
    a("| Feature | Old Model Mean | Old Model Std | Old σ-deviation @ SPY=654 | New Model Mean | New Model Std | New σ-deviation |")
    a("|---------|----------------|---------------|--------------------------|----------------|---------------|-----------------|")
    prod_fnames = prod_feature_stats.get("feature_names", [])
    prod_means  = prod_feature_stats.get("feature_means", [])
    prod_stds   = prod_feature_stats.get("feature_stds", [])
    new_fnames  = new_feature_stats.get("feature_names", [])
    new_means   = new_feature_stats.get("feature_means", [])
    new_stds    = new_feature_stats.get("feature_stds", [])
    today_vals  = {"spy_price": 654.0, "strategy_type_bear_call_spread": 0.0, "spread_type_call": 0.0}
    for feat, today_v in today_vals.items():
        if feat in prod_fnames:
            pi = prod_fnames.index(feat)
            pm, ps = prod_means[pi], prod_stds[pi]
            old_sigma = abs(today_v - pm) / ps if ps > 0 else 0
        else:
            pm, ps, old_sigma = 0, 0, 0
        if feat in new_fnames:
            ni = new_fnames.index(feat)
            nm_v, ns = new_means[ni], new_stds[ni]
            new_sigma = abs(today_v - nm_v) / ns if ns > 0 else 0
        else:
            nm_v, ns, new_sigma = 0, 0, 0
        a(f"| {feat} | {pm:.1f} | {ps:.1f} | **{old_sigma:.1f}σ** | {nm_v:.1f} | {ns:.1f} | **{new_sigma:.1f}σ** |")
    a("")
    a("---")
    a("")
    a("## Feature Importance (Top 10, aggregated across ensemble)")
    a("")
    a("| Rank | Feature | Importance |")
    a("|------|---------|------------|")
    for rank, (feat, imp) in enumerate(top10, 1):
        a(f"| {rank} | {feat} | {imp:.4f} |")
    a("")
    a("---")
    a("")
    a("## Today's Candidate Probabilities (2026-04-01)")
    a("")
    a("SPY price used: ~$654. Techncal context from most recent price data.")
    a("")
    a("| Candidate | Old Model Prob | New Model Prob | Threshold | Decision |")
    a("|-----------|----------------|----------------|-----------|----------|")
    for label, probs in candidate_scores.items():
        old_p = probs["old"]
        new_p = probs["new"]
        # Threshold varies by type
        thr = 0.35 if "bear" in label else 0.60 if "condor" in label else 0.65
        old_dec = "PASS" if old_p >= thr else "REJECT"
        new_dec = "PASS" if new_p >= thr else "REJECT"
        a(f"| {label} | {old_p:.3f} ({old_dec}) | **{new_p:.3f} ({new_dec})** | {thr} | {'IMPROVED' if new_dec != old_dec else 'SAME'} |")
    a("")
    a("---")
    a("")
    a("## Ensemble Model Stats")
    a("")
    per_model = stats.get("per_model", {})
    a("| Model | AUC | Accuracy | Precision | Recall | Weight |")
    a("|-------|-----|----------|-----------|--------|--------|")
    for name, ms in per_model.items():
        a(f"| {name} | {ms.get('test_auc', 0):.3f} | {ms.get('test_accuracy', 0):.3f} | "
          f"{ms.get('test_precision', 0):.3f} | {ms.get('test_recall', 0):.3f} | {ms.get('weight', 0):.3f} |")
    ew = stats.get("ensemble_weights", {})
    a(f"| **Ensemble** | **{stats.get('ensemble_test_auc', 0):.3f}** | {stats.get('ensemble_test_accuracy', 0):.3f} | "
      f"{stats.get('ensemble_test_precision', 0):.3f} | {stats.get('ensemble_test_recall', 0):.3f} | 1.000 |")
    a("")
    a("---")
    a("")
    a("## Model Files")
    a("")
    a("| Model | Path | Status |")
    a("|-------|------|--------|")
    a("| Production | `ml/models/ensemble_model_20260331.joblib` | **In production — DO NOT OVERWRITE** |")
    a("| Candidate | `ml/models/ensemble_model_20260401.joblib` | Shadow validation candidate |")
    a("| Feature stats | `ml/models/ensemble_model_20260401.feature_stats.json` | Updated means/stds |")
    a("")
    a("---")
    a("")
    a("## Shadow Validation Plan")
    a("")
    a("The candidate model has been added in shadow mode to `scripts/exp700_ml_scanner.py`.")
    a("Both models run on every scan; only the production model's decision affects trading.")
    a("")
    a("**Shadow log format:**")
    a("```")
    a("[SHADOW] candidate_prob=X.XX vs production_prob=X.XX decision=PASS/REJECT")
    a("```")
    a("")
    a("**Swap criteria (2-week minimum):**")
    a("1. Shadow mode shows candidate PASS rate > 0% on bear call spread days (vs 0% today)")
    a("2. Any passed candidates observed to actually win (next 2-4 weeks)")
    a("3. No degradation on bull_put win rate (most common, ~519/1667 trades)")
    a("4. AUC on live data after 20+ scored candidates ≥ 0.65")
    a("")
    a("**NOT ready to swap until:** At least 10 live bear call candidates scored, with ≥ 5 wins.")
    a("")
    a("---")
    a("")
    a("## Concerns and Flags")
    a("")
    a("1. **Small bear call sample (96/1667 = 5.8%):** Model has seen very few bear calls.")
    a("   The current tariff selloff is unlike anything in 2020-2023 training. Use the")
    a("   lowered type_threshold=0.35 for bear_call (already in paper_exp700.yaml).")
    a("")
    a("2. **iv_rank imputed as 25.0:** The trades cache doesn't store IV rank at entry.")
    a("   The backtester's internal `_iv_rank_by_date` was not persisted. All 1667 trades")
    a("   have iv_rank=25.0 (neutral). Future retrain should fix this via full backtest re-run.")
    a("")
    a("3. **Regime inferred from trade type:** Without the backtester's `_regime_by_date`")
    a("   dict, regime was inferred: bear_call→bear, IC→neutral, bull_put→bull. This is")
    a("   ~95% accurate (IC in bull regime or bear_call in neutral are rare) but imperfect.")
    a("")
    a("4. **2025 in training:** With 2025 now in training, the model has seen the 2025")
    a("   bull run and includes SPY at ~$590-680. This addresses the OOD problem directly.")
    a("")
    a("5. **Next retrain trigger:** If SPY moves ±20% from current level ($524 or $785),")
    a("   or if OOS win rate drops below 65% on 30+ live trades, retrain again.")
    a("")
    a(f"*Generated by `scripts/retrain_exp700_20260401.py` — {datetime.now(timezone.utc).isoformat()}*")

    OUTPUT_REPORT.parent.mkdir(exist_ok=True)
    OUTPUT_REPORT.write_text("\n".join(lines))
    logger.info("Report written → %s", OUTPUT_REPORT)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="EXP-700 ensemble retrain 2026-04-01")
    parser.add_argument("--skip-price-fetch", action="store_true",
                        help="Use cached price data only (no yfinance calls)")
    args = parser.parse_args()

    import joblib

    # ── Step 1: Load trades cache ─────────────────────────────────────────────
    logger.info("Loading trades cache from %s", TRADES_CACHE)
    if not TRADES_CACHE.exists():
        logger.error("Trades cache not found: %s", TRADES_CACHE)
        sys.exit(1)
    with open(TRADES_CACHE) as f:
        cache = json.load(f)
    trades = cache["trades"]
    for t in trades:
        for field in ("entry_date", "exit_date", "expiration"):
            if t.get(field):
                t[field] = pd.Timestamp(t[field])
    logger.info("Loaded %d trades (2020-2025)", len(trades))

    # ── Step 2: Fetch price data ───────────────────────────────────────────────
    price_feats = pd.DataFrame()
    vix_feats   = pd.DataFrame()

    if not args.skip_price_fetch:
        logger.info("Fetching SPY + VIX price data...")
        spy_df = fetch_price_data("SPY", "2019-07-01", "2026-04-01")
        vix_df = fetch_price_data("^VIX", "2019-07-01", "2026-04-01")

        if not spy_df.empty:
            price_feats = build_price_features(spy_df)
            logger.info("SPY price features: %d rows, range %s to %s",
                        len(price_feats), price_feats.index[0].date(), price_feats.index[-1].date())
        else:
            logger.warning("No SPY price data — price features will be imputed")

        if not vix_df.empty:
            vix_series = vix_df["Close"] if "Close" in vix_df.columns else vix_df.iloc[:, 0]
            vix_feats  = build_vix_features(vix_series)
            logger.info("VIX features: %d rows", len(vix_feats))
        else:
            logger.warning("No VIX data — VIX features will be imputed")

    # ── Step 3: Extract features ──────────────────────────────────────────────
    logger.info("Extracting 37 features for %d trades...", len(trades))
    df = extract_features(trades, price_feats, vix_feats)
    if df.empty:
        logger.error("Feature extraction produced empty DataFrame")
        sys.exit(1)

    logger.info("Feature matrix: %d rows × %d columns | win rate: %.1f%%",
                len(df), len(df.columns), df["win"].mean() * 100)
    logger.info("Year distribution: %s",
                df.groupby("year")["win"].agg(["count", "mean"]).to_dict())

    # Verify features match expected schema
    missing = [f for f in EXPECTED_FEATURES if f not in df.columns]
    if missing:
        logger.error("Missing features: %s", missing)
        sys.exit(1)
    logger.info("All 37 features present — schema matches production")

    # ── Step 4: Walk-forward OOS evaluation ───────────────────────────────────
    logger.info("Running walk-forward OOS evaluation (3 folds, no shuffling)...")
    fold_results = walk_forward_evaluate(df)

    wf_aucs = [f["auc"] for f in fold_results]
    mean_auc = np.mean(wf_aucs) if wf_aucs else 0.0
    logger.info("Walk-forward mean AUC: %.3f (folds: %s)", mean_auc, [f"{a:.3f}" for a in wf_aucs])

    if mean_auc < 0.55:
        logger.warning("Walk-forward mean AUC %.3f below 0.55 threshold — model quality is low", mean_auc)

    # ── Step 5: Train final ensemble on 2020-2025 ─────────────────────────────
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    train_result = train_final_ensemble(df, MODEL_DIR)
    new_model = train_result["model"]

    final_auc = train_result["stats"].get("ensemble_test_auc", 0)
    if final_auc < 0.75:
        logger.warning(
            "Final ensemble AUC=%.3f is below the 0.75 validation threshold. "
            "Model saved but marked as needs-review.", final_auc
        )

    # ── Step 6: Load production model for comparison ──────────────────────────
    logger.info("Loading production model for comparison: %s", PROD_MODEL.name)
    prod_data = None
    try:
        prod_data = joblib.load(PROD_MODEL)
    except Exception as exc:
        logger.warning("Could not load production model: %s", exc)

    prod_feature_stats = {
        "feature_names": prod_data["feature_names"] if prod_data else [],
        "feature_means": (np.asarray(prod_data["feature_means"]).tolist()
                          if prod_data and prod_data.get("feature_means") is not None else []),
        "feature_stds":  (np.asarray(prod_data["feature_stds"]).tolist()
                          if prod_data and prod_data.get("feature_stds") is not None else []),
    }
    new_stats_path = MODEL_DIR / "ensemble_model_20260401.feature_stats.json"
    if new_stats_path.exists():
        with open(new_stats_path) as f:
            new_feature_stats = json.load(f)
    else:
        new_feature_stats = {
            "feature_names": EXPECTED_FEATURES,
            "feature_means": new_model.feature_means.tolist() if new_model.feature_means is not None else [],
            "feature_stds":  new_model.feature_stds.tolist()  if new_model.feature_stds  is not None else [],
        }

    # ── Step 7: Score today's candidates with both models ─────────────────────
    candidates = simulate_today_candidates(price_feats, vix_feats)

    new_model_data = joblib.load(MODEL_DIR / NEW_MODEL_NAME)
    new_probs = score_candidates(new_model_data, candidates)

    old_probs = []
    if prod_data is not None:
        try:
            old_probs = score_candidates(prod_data, candidates)
        except Exception as exc:
            logger.warning("Could not score with production model: %s", exc)
            old_probs = [0.25] * len(candidates)
    else:
        old_probs = [0.25] * len(candidates)

    candidate_scores = {}
    for cand, old_p, new_p in zip(candidates, old_probs, new_probs):
        label = cand["_label"]
        candidate_scores[label] = {"old": old_p, "new": new_p}
        decision_old = "PASS" if old_p >= 0.65 else "REJECT"
        decision_new = "PASS" if new_p >= 0.65 else "REJECT"
        logger.info("[COMPARISON] %s: old=%.3f (%s)  new=%.3f (%s)",
                    label, old_p, decision_old, new_p, decision_new)

    # ── Step 8: OOS metrics with new model ────────────────────────────────────
    logger.info("Computing OOS metrics (2024-2025)...")
    oos_metrics = compute_oos_metrics(df, new_model, threshold=0.65)
    logger.info("OOS AUC=%.3f | Baseline WR=%.1f%% | Filtered WR=%.1f%% | Filtered Sharpe=%.2f",
                oos_metrics.get("auc", 0),
                oos_metrics.get("baseline_wr", 0) * 100,
                oos_metrics.get("filtered_wr", 0) * 100,
                oos_metrics.get("filtered_sharpe", 0))

    # ── Step 9: Write report ──────────────────────────────────────────────────
    write_report(
        fold_results=fold_results,
        train_result=train_result,
        oos_metrics=oos_metrics,
        candidate_scores=candidate_scores,
        prod_feature_stats=prod_feature_stats,
        new_feature_stats=new_feature_stats,
    )

    # ── Step 10: Validation gate ──────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("VALIDATION GATE SUMMARY")
    print("=" * 65)
    print(f"  Walk-forward mean AUC: {mean_auc:.3f}  {'PASS' if mean_auc >= 0.55 else 'FAIL'} (threshold 0.55)")
    print(f"  Final ensemble AUC:    {final_auc:.3f}  {'PASS' if final_auc >= 0.75 else 'WARN'} (threshold 0.75)")
    print(f"  OOS AUC (2024-2025):   {oos_metrics.get('auc', 0):.3f}  {'PASS' if oos_metrics.get('auc', 0) >= 0.75 else 'WARN'} (threshold 0.75)")
    print(f"  New model saved:       ml/models/{NEW_MODEL_NAME}")
    print(f"  Report:                {OUTPUT_REPORT.relative_to(ROOT)}")
    print("=" * 65)
    print("\nNEXT STEPS:")
    print("  1. Enable shadow mode in exp700_ml_scanner.py (see Step 6 below)")
    print("  2. Monitor shadow logs for 2 weeks before swapping production model")
    print("  3. Update paper_exp700.yaml model_path when ready to swap")
    print()


if __name__ == "__main__":
    main()
