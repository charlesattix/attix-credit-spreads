#!/usr/bin/env python3
"""
exp700_yearly_walkforward.py — Full 37-feature walk-forward yearly breakdown.

For each test year (2021–2025), trains an ensemble on all prior years using
the SAME feature extraction as the production EXP-700 model, then evaluates.
2020 = base only (no prior training data).

Outputs JSON to output/exp700_full_walkforward.json
"""
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

TRADES_CACHE = ROOT / "output" / "ml_filter_exp400_trades_cache.json"
THRESHOLD = 0.65
STARTING_CAPITAL = 100_000.0


def main():
    import os
    from datetime import datetime
    from backtest.backtester import Backtester
    from backtest.historical_data import HistoricalOptionsData
    from compass.ensemble_signal_model import EnsembleSignalModel
    from compass.walk_forward import (
        NUMERIC_FEATURES,
        CATEGORICAL_FEATURES,
        prepare_features,
    )
    from scripts.backtest_ml_filter import (
        extract_trade_features,
        build_price_features,
        build_vix_features,
        subset_metrics,
        simulate_equity,
        compute_sharpe,
        compute_max_drawdown,
    )

    # ── Run full backtest to get vix_by_date, iv_rank_by_date, regime_by_date
    logger.info("Running EXP-400 backtest to extract full feature data...")
    
    config_path = ROOT / "configs" / "exp_400_champion_realdata.json"
    with open(config_path) as f:
        flat_params = json.load(f)
    
    # Build nested config
    config = {
        "backtest": {
            "ticker": "SPY",
            "start_date": "2020-01-02",
            "end_date": "2025-12-31",
            "starting_capital": STARTING_CAPITAL,
            "commission_per_contract": 0.65,
            "slippage": 0.05,
        },
        "strategy": flat_params,
        "risk": {},
    }
    
    _polygon_key = os.environ.get("POLYGON_API_KEY", "dummy")
    hist_data = HistoricalOptionsData(_polygon_key, offline_mode=True)
    _otm_pct = flat_params.get("otm_pct", 0.02)
    bt = Backtester(config=config, historical_data=hist_data, otm_pct=_otm_pct)
    full_results = bt.run_backtest(
        ticker="SPY",
        start_date=datetime(2020, 1, 2),
        end_date=datetime(2025, 12, 31),
    )
    trades_list = full_results.get("trades", [])
    
    price_data      = getattr(bt, "_price_data",      pd.DataFrame())
    vix_by_date     = getattr(bt, "_vix_by_date",     {})
    iv_rank_by_date = getattr(bt, "_iv_rank_by_date", {})
    regime_by_date  = getattr(bt, "_regime_by_date",  {})
    
    logger.info("Backtest: %d trades", len(trades_list))

    # ── Build price/VIX features ──────────────────────────────────────────
    if not price_data.empty:
        price_features = build_price_features(price_data)
        if vix_by_date:
            raw_vix = pd.Series({pd.Timestamp(k): v for k, v in vix_by_date.items()}).sort_index()
            vix_features = build_vix_features(raw_vix)
        else:
            import yfinance as yf
            vix_raw = yf.download("^VIX", start="2019-06-01", end="2026-01-01", progress=False)
            if isinstance(vix_raw.columns, pd.MultiIndex):
                vix_raw.columns = vix_raw.columns.get_level_values(0)
            vix_features = build_vix_features(vix_raw["Close"])
    else:
        import yfinance as yf
        spy_raw = yf.download("SPY", start="2019-06-01", end="2026-01-01", progress=False)
        vix_raw = yf.download("^VIX", start="2019-06-01", end="2026-01-01", progress=False)
        if isinstance(spy_raw.columns, pd.MultiIndex):
            spy_raw.columns = spy_raw.columns.get_level_values(0)
        if isinstance(vix_raw.columns, pd.MultiIndex):
            vix_raw.columns = vix_raw.columns.get_level_values(0)
        price_features = build_price_features(spy_raw)
        vix_features = build_vix_features(vix_raw["Close"])

    # ── Extract 37 features per trade ─────────────────────────────────────
    logger.info("Extracting 37 features per trade...")
    df = extract_trade_features(trades_list, price_features, vix_features, 
                                 vix_by_date, iv_rank_by_date, regime_by_date)
    df["year"] = df["entry_date"].dt.year
    logger.info("Feature matrix: %d trades × %d columns", len(df), len(df.columns))
    logger.info("Years: %s", sorted(df["year"].unique()))

    # ── Walk-forward per year ─────────────────────────────────────────────
    years = sorted(df["year"].unique())
    results = []

    for test_year in years:
        train_df = df[df["year"] < test_year].copy()
        test_df = df[df["year"] == test_year].copy()

        # Base metrics
        base_equity = simulate_equity(test_df)
        base_pnl = test_df["return_pct"].sum()  # approx
        base_n = len(test_df)
        base_wr = float(test_df["win"].mean() * 100) if len(test_df) > 0 else 0
        base_sharpe = compute_sharpe(test_df["return_pct"]) if len(test_df) > 5 else 0
        base_dd = compute_max_drawdown(base_equity) if len(base_equity) > 1 else 0

        # Use actual PnL from trades
        base_dollar_pnl = sum(t["pnl"] for t in trades_list 
                              if pd.Timestamp(t["entry_date"]).year == test_year)

        if len(train_df) < 50:
            results.append({
                "year": int(test_year),
                "base_trades": base_n,
                "base_wr": round(base_wr, 1),
                "base_pnl": round(base_dollar_pnl, 0),
                "base_sharpe": round(base_sharpe, 2),
                "base_dd": round(base_dd, 1),
                "ml_trades": None,
                "ml_wr": None,
                "ml_pnl": None,
                "ml_sharpe": None,
                "ml_dd": None,
                "auc": None,
                "note": "No prior training data"
            })
            logger.info("Year %d: BASE ONLY (no prior data) — %d trades, WR=%.1f%%, PnL=$%.0f",
                        test_year, base_n, base_wr, base_dollar_pnl)
            continue

        # Train ensemble
        logger.info("Year %d: Training on %d trades (years %s)...",
                    test_year, len(train_df), f"{years[0]}-{test_year-1}")

        model_dir = str(ROOT / "ml" / "models" / "walkforward_temp")
        Path(model_dir).mkdir(parents=True, exist_ok=True)
        model = EnsembleSignalModel(model_dir=model_dir)

        X_train = prepare_features(train_df, NUMERIC_FEATURES, CATEGORICAL_FEATURES).astype(float)
        y_train = train_df["win"].values.astype(int)

        try:
            train_stats = model.train(X_train, y_train, calibrate=True, save_model=False, n_wf_folds=3)
            logger.info("  Trained: AUC=%.3f", train_stats.get("ensemble_test_auc", 0))
        except Exception as e:
            logger.error("  Training failed for year %d: %s", test_year, e)
            results.append({
                "year": int(test_year),
                "base_trades": base_n, "base_wr": round(base_wr, 1),
                "base_pnl": round(base_dollar_pnl, 0), "base_sharpe": round(base_sharpe, 2),
                "base_dd": round(base_dd, 1),
                "ml_trades": None, "ml_wr": None, "ml_pnl": None,
                "ml_sharpe": None, "ml_dd": None, "auc": None,
                "note": f"Training failed: {e}"
            })
            continue

        # Predict on test year
        X_test = prepare_features(test_df, NUMERIC_FEATURES, CATEGORICAL_FEATURES)
        for col in X_train.columns:
            if col not in X_test.columns:
                X_test[col] = 0.0
        X_test = X_test.reindex(columns=X_train.columns, fill_value=0.0).astype(float)

        probs = model.predict_batch(X_test)

        # AUC
        from sklearn.metrics import roc_auc_score
        try:
            auc = roc_auc_score(test_df["win"].values, probs)
        except:
            auc = None

        # Filter at threshold
        mask = probs >= THRESHOLD
        filtered_df = test_df[mask].copy()
        filtered_indices = test_df.index[mask]

        ml_n = int(mask.sum())
        ml_wr = float(filtered_df["win"].mean() * 100) if len(filtered_df) > 0 else 0

        # Get dollar PnL for filtered trades
        ml_dollar_pnl = 0
        trade_idx = 0
        for i, (_, row) in enumerate(test_df.iterrows()):
            entry_str = str(row["entry_date"])
            # Match by index position
            pass

        # Simpler: use test_df index alignment
        test_df_copy = test_df.copy()
        test_df_copy["ml_pass"] = mask
        ml_trades_data = []
        for i, (idx, row) in enumerate(test_df_copy.iterrows()):
            if row["ml_pass"]:
                # Find matching trade in trades_list by entry_date
                entry_str = str(row["entry_date"])[:10]
                for t in trades_list:
                    if t["entry_date"][:10] == entry_str:
                        ml_trades_data.append(t)
                        break

        # Deduplicate by using position matching instead
        # The df is sorted by entry_date, same as trades_list within each year
        year_trades = [t for t in trades_list if pd.Timestamp(t["entry_date"]).year == test_year]
        ml_dollar_pnl = 0
        ml_trade_count = 0
        for i, passed in enumerate(mask):
            if passed and i < len(year_trades):
                ml_dollar_pnl += year_trades[i]["pnl"]
                ml_trade_count += 1

        ml_sharpe = compute_sharpe(filtered_df["return_pct"]) if len(filtered_df) > 5 else 0
        ml_equity = simulate_equity(filtered_df) if len(filtered_df) > 0 else pd.Series([STARTING_CAPITAL])
        ml_dd = compute_max_drawdown(ml_equity) if len(ml_equity) > 1 else 0

        results.append({
            "year": int(test_year),
            "base_trades": base_n,
            "base_wr": round(base_wr, 1),
            "base_pnl": round(base_dollar_pnl, 0),
            "base_sharpe": round(base_sharpe, 2),
            "base_dd": round(base_dd, 1),
            "ml_trades": ml_n,
            "ml_wr": round(ml_wr, 1),
            "ml_pnl": round(ml_dollar_pnl, 0),
            "ml_sharpe": round(ml_sharpe, 2),
            "ml_dd": round(ml_dd, 1),
            "auc": round(auc, 3) if auc else None,
            "pass_rate": round(ml_n / base_n * 100, 1) if base_n > 0 else 0,
            "note": f"Train {years[0]}-{test_year-1} ({len(train_df)} trades)"
        })

        logger.info("  Year %d: Base WR=%.1f%% PnL=$%.0f | ML WR=%.1f%% PnL=$%.0f | AUC=%.3f | Kept %d/%d",
                    test_year, base_wr, base_dollar_pnl, ml_wr, ml_dollar_pnl,
                    auc or 0, ml_n, base_n)

    # ── Compute compounding returns ───────────────────────────────────────
    base_cap = STARTING_CAPITAL
    ml_cap = STARTING_CAPITAL
    for r in results:
        r["base_return_pct"] = round((r["base_pnl"] / base_cap) * 100, 1)
        base_cap += r["base_pnl"]
        if r["ml_pnl"] is not None:
            r["ml_return_pct"] = round((r["ml_pnl"] / ml_cap) * 100, 1)
            ml_cap += r["ml_pnl"]
        else:
            r["ml_return_pct"] = None
            # For 2020 (no ML), use base pnl for ML capital too
            ml_cap += r["base_pnl"]

    output = {
        "threshold": THRESHOLD,
        "features": "37 (production feature set)",
        "starting_capital": STARTING_CAPITAL,
        "base_final_capital": round(base_cap, 0),
        "ml_final_capital": round(ml_cap, 0),
        "base_total_return": round((base_cap / STARTING_CAPITAL - 1) * 100, 1),
        "ml_total_return": round((ml_cap / STARTING_CAPITAL - 1) * 100, 1),
        "years": results
    }

    out_path = ROOT / "output" / "exp700_full_walkforward.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info("Results saved to %s", out_path)

    # Print summary
    print("\n" + "=" * 100)
    print(f"{'Year':<6} {'Base Trades':>11} {'Base WR':>8} {'Base Ret%':>10} {'Base PnL':>12}  |  {'ML Trades':>10} {'ML WR':>7} {'ML Ret%':>9} {'ML PnL':>12} {'AUC':>6}")
    print("=" * 100)
    for r in results:
        if r["ml_trades"] is not None:
            print(f"{r['year']:<6} {r['base_trades']:>11} {r['base_wr']:>7.1f}% {r['base_return_pct']:>+9.1f}% ${r['base_pnl']:>11,.0f}  |  {r['ml_trades']:>10} {r['ml_wr']:>6.1f}% {r['ml_return_pct']:>+8.1f}% ${r['ml_pnl']:>11,.0f} {r['auc'] or 0:>6.3f}")
        else:
            print(f"{r['year']:<6} {r['base_trades']:>11} {r['base_wr']:>7.1f}% {r['base_return_pct']:>+9.1f}% ${r['base_pnl']:>11,.0f}  |  {'— no prior training data —':>55}")
    print("=" * 100)
    print(f"Base: ${base_cap:,.0f} ({output['base_total_return']}%)  |  ML: ${ml_cap:,.0f} ({output['ml_total_return']}%)")


if __name__ == "__main__":
    main()
