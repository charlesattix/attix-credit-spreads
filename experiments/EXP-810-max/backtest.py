#!/usr/bin/env python3
"""
EXP-810-max: Signal Ensemble Testing

Compares single XGBoost vs multi-model ensembles on real trade data
using strict walk-forward validation (expanding window by year).

Variants:
  A) Single XGBoost (baseline — reproduces EXP-710)
  B) 3-model ensemble: XGBoost + RandomForest + ExtraTrees
  C) Stacked ensemble: meta-learner (Ridge) on base model predictions

All variants use the same walk-forward protocol: train on years 1..N,
predict on year N+1. Never any future data leakage.
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression, Ridge
from xgboost import XGBClassifier

ROOT = Path(__file__).parent
# Try multiple data paths
DATA_CANDIDATES = [
    ROOT.parent.parent / "compass" / "training_data_combined.csv",
    Path("/home/node/.openclaw/workspace/pilotai-compass/experiments/training_data_combined.csv"),
    Path("/home/node/.openclaw/workspace/pilotai-credit-spreads/compass/training_data_combined.csv"),
]
RESULTS_DIR = ROOT / "results"
INITIAL_CAPITAL = 100_000.0
SLIPPAGE_BPS = 5.0
COMMISSION_PER_CONTRACT = 1.30

FEATURE_COLS = [
    "dte_at_entry", "hold_days", "day_of_week", "days_since_last_trade",
    "rsi_14", "momentum_5d_pct", "momentum_10d_pct",
    "vix", "vix_percentile_20d", "vix_percentile_50d", "vix_percentile_100d",
    "iv_rank", "spy_price",
    "dist_from_ma20_pct", "dist_from_ma50_pct", "dist_from_ma80_pct", "dist_from_ma200_pct",
    "ma20_slope_ann_pct", "ma50_slope_ann_pct",
    "realized_vol_atr20", "realized_vol_5d", "realized_vol_10d", "realized_vol_20d",
]

THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]


# ── Data loading ─────────────────────────────────────────────────────────


def load_data() -> pd.DataFrame:
    for p in DATA_CANDIDATES:
        if p.exists():
            df = pd.read_csv(p, parse_dates=["entry_date", "exit_date"])
            df["year"] = pd.to_datetime(df["entry_date"]).dt.year
            return df
    raise FileNotFoundError("training_data_combined.csv not found")


def prepare_features(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    available = [c for c in FEATURE_COLS if c in df.columns]
    X = df[available].fillna(0).values.astype(np.float32)
    y = df["win"].values.astype(int)
    return X, y


# ── Base models ──────────────────────────────────────────────────────────


def make_xgb():
    return XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        eval_metric="logloss", random_state=42,
        verbosity=0,
    )


def make_rf():
    return RandomForestClassifier(
        n_estimators=200, max_depth=6, min_samples_leaf=5,
        random_state=42, n_jobs=-1,
    )


def make_et():
    return ExtraTreesClassifier(
        n_estimators=200, max_depth=6, min_samples_leaf=5,
        random_state=42, n_jobs=-1,
    )


def make_lr():
    return LogisticRegression(
        C=1.0, max_iter=500, random_state=42,
    )


# ── Walk-forward engine ──────────────────────────────────────────────────


@dataclass
class FoldResult:
    fold: int
    train_years: List[int]
    test_year: int
    n_train: int
    n_test: int
    predictions: np.ndarray  # probabilities for test set
    actuals: np.ndarray
    test_indices: np.ndarray


def walk_forward_predict(
    df: pd.DataFrame,
    model_factory,
    model_name: str = "model",
) -> List[FoldResult]:
    """Expanding-window walk-forward: train on years 1..N, test year N+1."""
    X, y = prepare_features(df)
    years = sorted(df["year"].unique())
    folds: List[FoldResult] = []

    for i in range(1, len(years)):
        train_years = years[:i]
        test_year = years[i]

        train_mask = df["year"].isin(train_years).values
        test_mask = (df["year"] == test_year).values

        X_train, y_train = X[train_mask], y[train_mask]
        X_test, y_test = X[test_mask], y[test_mask]

        if len(X_train) < 20 or len(X_test) < 5:
            continue

        model = model_factory()
        model.fit(X_train, y_train)

        if hasattr(model, "predict_proba"):
            proba = model.predict_proba(X_test)[:, 1]
        else:
            proba = model.predict(X_test).astype(float)

        folds.append(FoldResult(
            fold=i, train_years=list(train_years), test_year=test_year,
            n_train=len(X_train), n_test=len(X_test),
            predictions=proba, actuals=y_test,
            test_indices=np.where(test_mask)[0],
        ))

    return folds


def walk_forward_ensemble(
    df: pd.DataFrame,
    model_factories: Dict[str, Any],
) -> Tuple[List[FoldResult], Dict[str, List[FoldResult]]]:
    """Walk-forward for ensemble: run all models, average predictions."""
    X, y = prepare_features(df)
    years = sorted(df["year"].unique())

    per_model_folds: Dict[str, List[FoldResult]] = {name: [] for name in model_factories}
    ensemble_folds: List[FoldResult] = []

    for i in range(1, len(years)):
        train_years = years[:i]
        test_year = years[i]

        train_mask = df["year"].isin(train_years).values
        test_mask = (df["year"] == test_year).values

        X_train, y_train = X[train_mask], y[train_mask]
        X_test, y_test = X[test_mask], y[test_mask]

        if len(X_train) < 20 or len(X_test) < 5:
            continue

        model_preds = {}
        for name, factory in model_factories.items():
            model = factory()
            model.fit(X_train, y_train)
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X_test)[:, 1]
            else:
                proba = model.predict(X_test).astype(float)
            model_preds[name] = proba

            per_model_folds[name].append(FoldResult(
                fold=i, train_years=list(train_years), test_year=test_year,
                n_train=len(X_train), n_test=len(X_test),
                predictions=proba, actuals=y_test,
                test_indices=np.where(test_mask)[0],
            ))

        # Simple average ensemble
        avg_pred = np.mean(list(model_preds.values()), axis=0)

        ensemble_folds.append(FoldResult(
            fold=i, train_years=list(train_years), test_year=test_year,
            n_train=len(X_train), n_test=len(X_test),
            predictions=avg_pred, actuals=y_test,
            test_indices=np.where(test_mask)[0],
        ))

    return ensemble_folds, per_model_folds


def walk_forward_stacked(
    df: pd.DataFrame,
    base_factories: Dict[str, Any],
) -> List[FoldResult]:
    """Stacked ensemble: train base models, then Ridge meta-learner on their OOS predictions."""
    X, y = prepare_features(df)
    years = sorted(df["year"].unique())
    folds: List[FoldResult] = []

    if len(years) < 3:
        return folds

    for i in range(2, len(years)):
        # Meta-learner needs at least 2 train years
        # Split: years[:i-1] for base training, year i-1 for meta training, year i for testing
        base_train_years = years[:i - 1]
        meta_train_year = years[i - 1]
        test_year = years[i]

        base_mask = df["year"].isin(base_train_years).values
        meta_mask = (df["year"] == meta_train_year).values
        test_mask = (df["year"] == test_year).values

        X_base, y_base = X[base_mask], y[base_mask]
        X_meta, y_meta = X[meta_mask], y[meta_mask]
        X_test, y_test = X[test_mask], y[test_mask]

        if len(X_base) < 20 or len(X_meta) < 10 or len(X_test) < 5:
            continue

        # Train base models on base_train data
        base_models = {}
        for name, factory in base_factories.items():
            model = factory()
            model.fit(X_base, y_base)
            base_models[name] = model

        # Generate meta-features on meta_train data
        meta_features_train = np.column_stack([
            m.predict_proba(X_meta)[:, 1] if hasattr(m, "predict_proba") else m.predict(X_meta)
            for m in base_models.values()
        ])

        # Train meta-learner (Ridge)
        meta = Ridge(alpha=1.0)
        meta.fit(meta_features_train, y_meta.astype(float))

        # Generate meta-features on test data
        meta_features_test = np.column_stack([
            m.predict_proba(X_test)[:, 1] if hasattr(m, "predict_proba") else m.predict(X_test)
            for m in base_models.values()
        ])

        stacked_pred = np.clip(meta.predict(meta_features_test), 0, 1)

        folds.append(FoldResult(
            fold=i, train_years=list(base_train_years) + [meta_train_year],
            test_year=test_year,
            n_train=len(X_base) + len(X_meta), n_test=len(X_test),
            predictions=stacked_pred, actuals=y_test,
            test_indices=np.where(test_mask)[0],
        ))

    return folds


# ── Metrics from folds at a given threshold ──────────────────────────────


@dataclass
class ThresholdResult:
    threshold: float
    n_trades: int
    win_rate: float
    total_pnl: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    profit_factor: float
    years_covered: int
    per_year: Dict[int, Dict[str, float]]


def evaluate_at_threshold(
    folds: List[FoldResult],
    df: pd.DataFrame,
    threshold: float,
) -> ThresholdResult:
    """Evaluate fold predictions at a given probability threshold."""
    all_pnl: List[float] = []
    per_year: Dict[int, Dict[str, float]] = {}

    for fold in folds:
        mask = fold.predictions >= threshold
        selected_indices = fold.test_indices[mask]

        for idx in selected_indices:
            row = df.iloc[idx]
            pnl = float(row["pnl"])
            contracts = max(int(row.get("contracts", 5)), 1)
            entry_p = abs(float(row.get("net_credit", 1.0)))

            slip = entry_p * 2 * SLIPPAGE_BPS / 10_000 * contracts * 100
            comm = COMMISSION_PER_CONTRACT * contracts * 2
            net = pnl - slip - comm

            all_pnl.append(net)

            year = fold.test_year
            if year not in per_year:
                per_year[year] = {"pnl": 0, "trades": 0, "wins": 0}
            per_year[year]["pnl"] += net
            per_year[year]["trades"] += 1
            if net > 0:
                per_year[year]["wins"] += 1

    if not all_pnl:
        return ThresholdResult(threshold, 0, 0, 0, 0, 0, 0, 0, 0, {})

    pnls = np.array(all_pnl)
    n = len(pnls)
    wins = (pnls > 0).sum()
    equity = INITIAL_CAPITAL + np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1)

    mu = pnls.mean()
    std = pnls.std(ddof=1) if n > 1 else 1.0
    sh = mu / std * math.sqrt(252) if std > 1e-12 else 0.0

    g = pnls[pnls > 0].sum()
    l = abs(pnls[pnls < 0].sum())
    pf = g / l if l > 1e-12 else (10.0 if g > 0 else 0.0)

    n_years = max(len(per_year), 1)
    ann_ret = float(pnls.sum()) / INITIAL_CAPITAL / n_years

    return ThresholdResult(
        threshold=threshold, n_trades=n,
        win_rate=wins / n, total_pnl=float(pnls.sum()),
        annual_return=ann_ret, sharpe=sh,
        max_drawdown=float(dd.min()), profit_factor=min(pf, 50.0),
        years_covered=len(per_year), per_year=per_year,
    )


# ── Main backtest ────────────────────────────────────────────────────────


def run_backtest() -> Dict[str, Any]:
    print("  Loading data...")
    df = load_data()
    n_total = len(df)
    print(f"  {n_total} trades, {df['year'].nunique()} years ({df['year'].min()}-{df['year'].max()})")

    # Variant A: Single XGBoost
    print("  [A] Single XGBoost walk-forward...")
    xgb_folds = walk_forward_predict(df, make_xgb, "XGBoost")

    # Variant B: 3-model ensemble
    print("  [B] 3-Model Ensemble (XGB+RF+ET) walk-forward...")
    ens3_factories = {"XGBoost": make_xgb, "RF": make_rf, "ExtraTrees": make_et}
    ens3_folds, per_model_3 = walk_forward_ensemble(df, ens3_factories)

    # Variant C: Stacked ensemble
    print("  [C] Stacked Ensemble (meta-learner) walk-forward...")
    stacked_folds = walk_forward_stacked(df, ens3_factories)

    # Evaluate all variants at multiple thresholds
    print("  Evaluating at thresholds...")
    variants = {
        "A_XGBoost": xgb_folds,
        "B_Ensemble3": ens3_folds,
        "C_Stacked": stacked_folds,
    }
    # Also individual models from ensemble
    for model_name, model_folds in per_model_3.items():
        variants[f"B_{model_name}"] = model_folds

    results: Dict[str, List[Dict]] = {}
    for variant_name, folds in variants.items():
        variant_results = []
        for thresh in THRESHOLDS:
            tr = evaluate_at_threshold(folds, df, thresh)
            variant_results.append({
                "threshold": thresh,
                "n_trades": tr.n_trades,
                "win_rate": tr.win_rate,
                "total_pnl": tr.total_pnl,
                "annual_return": tr.annual_return,
                "sharpe": tr.sharpe,
                "max_drawdown": tr.max_drawdown,
                "profit_factor": tr.profit_factor,
                "years_covered": tr.years_covered,
                "per_year": {str(k): v for k, v in tr.per_year.items()},
            })
        results[variant_name] = variant_results

    # Find best at P>=0.75 for comparison
    comparison_thresh = 0.75
    comparison = {}
    for vname, vresults in results.items():
        for r in vresults:
            if r["threshold"] == comparison_thresh:
                comparison[vname] = r
                break

    # Find best variant at 0.75
    best_at_75 = max(comparison.items(), key=lambda x: x[1]["sharpe"])

    # OOS degradation: compare first-half vs second-half folds
    oos_degradation = {}
    for vname, folds in variants.items():
        if len(folds) < 2:
            oos_degradation[vname] = 0.0
            continue
        mid = len(folds) // 2
        early = evaluate_at_threshold(folds[:mid], df, comparison_thresh)
        late = evaluate_at_threshold(folds[mid:], df, comparison_thresh)
        if early.sharpe > 0:
            oos_degradation[vname] = 1.0 - late.sharpe / early.sharpe
        else:
            oos_degradation[vname] = 0.0

    summary = {
        "experiment": "EXP-810-max",
        "description": "Signal Ensemble Testing — XGB vs Ensemble vs Stacked",
        "data": {"total_trades": n_total, "oos_years": df["year"].nunique() - 1},
        "baseline_threshold": comparison_thresh,
        "comparison_at_075": comparison,
        "best_variant_at_075": {
            "name": best_at_75[0],
            "sharpe": best_at_75[1]["sharpe"],
            "win_rate": best_at_75[1]["win_rate"],
            "max_dd": best_at_75[1]["max_drawdown"],
            "n_trades": best_at_75[1]["n_trades"],
        },
        "oos_degradation": oos_degradation,
        "all_results": results,
    }

    return summary


# ── HTML report ──────────────────────────────────────────────────────────


def generate_report(summary: Dict) -> str:
    comp = summary["comparison_at_075"]
    best = summary["best_variant_at_075"]
    oos = summary["oos_degradation"]

    def _fr(v): return f"{v:.2f}"
    def _fp(v): return f"{v:.1%}"
    def _fd(v): return f"${v:,.0f}"
    def _ti(m): return '<span style="color:#3fb950">&#10003;</span>' if m else '<span style="color:#f85149">&#10007;</span>'

    # Comparison table at P>=0.75
    comp_rows = ""
    variant_order = ["A_XGBoost", "B_Ensemble3", "C_Stacked", "B_RF", "B_ExtraTrees"]
    for vname in variant_order:
        if vname not in comp:
            continue
        r = comp[vname]
        is_best = vname == best["name"]
        cls = "style='color:#3fb950;font-weight:700'" if is_best else ""
        deg = oos.get(vname, 0)
        comp_rows += f"<tr {cls}><td style='text-align:left'>{vname}{'  ★' if is_best else ''}</td><td>{r['n_trades']}</td><td>{_fp(r['win_rate'])}</td><td>{_fr(r['sharpe'])}</td><td>{_fp(abs(r['max_drawdown']))}</td><td>{_fd(r['total_pnl'])}</td><td>{_fp(r['annual_return'])}</td><td>{_fp(abs(deg))}</td></tr>"

    # Full threshold sweep for top 3 variants
    sweep_rows = ""
    for vname in ["A_XGBoost", "B_Ensemble3", "C_Stacked"]:
        for r in summary["all_results"].get(vname, []):
            sweep_rows += f"<tr><td style='text-align:left'>{vname}</td><td>{r['threshold']:.2f}</td><td>{r['n_trades']}</td><td>{_fp(r['win_rate'])}</td><td>{_fr(r['sharpe'])}</td><td>{_fp(abs(r['max_drawdown']))}</td><td>{_fd(r['total_pnl'])}</td></tr>"

    best_color = "#3fb950"
    xgb_sharpe = comp.get("A_XGBoost", {}).get("sharpe", 0)
    ens_sharpe = comp.get("B_Ensemble3", {}).get("sharpe", 0)
    ensemble_wins = ens_sharpe > xgb_sharpe

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>EXP-810-max: Signal Ensemble</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:1100px;margin:0 auto;padding:20px;background:#0d1117;color:#c9d1d9}}
h1,h2{{color:#58a6ff}}.meta{{color:#8b949e}}
.hero{{background:#161b22;border:2px solid {best_color};border-radius:12px;padding:24px;text-align:center;margin:20px 0}}
.hero .big{{font-size:2em;font-weight:800;color:{best_color}}}
.hero .sub{{color:#8b949e}}
table{{width:100%;border-collapse:collapse;margin:12px 0}}
th,td{{padding:6px 10px;text-align:right;border-bottom:1px solid #21262d}}
th{{color:#8b949e;background:#161b22}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:20px 0}}
.c{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px;text-align:center}}
.c .l{{color:#8b949e;font-size:.8em}}.c .v{{color:#f0f6fc;font-weight:600;font-size:1.1em}}
</style></head><body>
<h1>EXP-810-max: Signal Ensemble Testing</h1>
<div class="hero">
<div class="big">Best: {best['name']} (Sharpe {_fr(best['sharpe'])})</div>
<div class="sub">{"Ensemble beats XGBoost" if ensemble_wins else "XGBoost remains champion"} at P&ge;0.75</div>
</div>

<div class="cards">
<div class="c"><div class="l">XGBoost Sharpe</div><div class="v">{_fr(xgb_sharpe)}</div></div>
<div class="c"><div class="l">Ensemble3 Sharpe</div><div class="v">{_fr(ens_sharpe)}</div></div>
<div class="c"><div class="l">Stacked Sharpe</div><div class="v">{_fr(comp.get('C_Stacked', {}).get('sharpe', 0))}</div></div>
<div class="c"><div class="l">Best WR</div><div class="v">{_fp(best['win_rate'])}</div></div>
<div class="c"><div class="l">Best Max DD</div><div class="v">{_fp(abs(best['max_dd']))}</div></div>
<div class="c"><div class="l">Best Trades</div><div class="v">{best['n_trades']}</div></div>
</div>

<h2>Head-to-Head at P&ge;0.75</h2>
<table><tr><th style="text-align:left">Variant</th><th>Trades</th><th>Win Rate</th><th>Sharpe</th><th>Max DD</th><th>Total PnL</th><th>Ann. Return</th><th>OOS Degrad.</th></tr>{comp_rows}</table>

<h2>Full Threshold Sweep</h2>
<table><tr><th style="text-align:left">Variant</th><th>Threshold</th><th>Trades</th><th>Win Rate</th><th>Sharpe</th><th>Max DD</th><th>Total PnL</th></tr>{sweep_rows}</table>

</body></html>"""


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    print("Running EXP-810-max: Signal Ensemble Testing...")
    summary = run_backtest()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Save JSON
    (RESULTS_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print("  Written: results/summary.json")

    # Save HTML
    html = generate_report(summary)
    (RESULTS_DIR / "report.html").write_text(html, encoding="utf-8")
    print("  Written: results/report.html")

    # Print comparison
    comp = summary["comparison_at_075"]
    oos = summary["oos_degradation"]
    best = summary["best_variant_at_075"]

    print(f"\n{'='*70}")
    print(f"  EXP-810-max: Signal Ensemble Results (at P>=0.75)")
    print(f"{'='*70}")
    for vname in ["A_XGBoost", "B_Ensemble3", "C_Stacked"]:
        r = comp.get(vname, {})
        deg = oos.get(vname, 0)
        print(f"  {vname:20s}  Sharpe={r.get('sharpe',0):7.2f}  WR={r.get('win_rate',0):.1%}  DD={abs(r.get('max_drawdown',0)):.1%}  PnL=${r.get('total_pnl',0):>10,.0f}  OOS_deg={abs(deg):.1%}")
    print(f"\n  ★ Best: {best['name']} (Sharpe {best['sharpe']:.2f})")

    xgb_sh = comp.get("A_XGBoost", {}).get("sharpe", 0)
    ens_sh = comp.get("B_Ensemble3", {}).get("sharpe", 0)
    if ens_sh > xgb_sh:
        print(f"  → Ensemble BEATS XGBoost by {ens_sh - xgb_sh:.2f} Sharpe points")
    else:
        print(f"  → XGBoost BEATS Ensemble by {xgb_sh - ens_sh:.2f} Sharpe points")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
