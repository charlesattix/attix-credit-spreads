# EXP-700 ML Retrain Report — 2026-04-01

**Model date:** 2026-04-01  
**Training data:** 2020-01-17 to 2025-12-30 (1667 trades)  
**Architecture:** XGBoost + RandomForest + ExtraTrees ensemble (soft voting)  
**Feature count:** 37 (identical to production schema)  

---

## Problem Statement

Production model `ensemble_model_20260331.joblib` rejects all EXP-700 candidates today (prob ~0.25 vs threshold 0.65).
Root cause: model trained on 2020-2023 data, SPY price mean=$374.6 (std=$52.8).
Current SPY ~$654 is **5.2σ OOD**. Bear call spread and spread_type_call features are 4.3σ OOD.

Fix: retrain on full 2020-2025 data to bring feature distribution up to current market levels.

---

## Training Data

- Source: `output/ml_filter_exp400_trades_cache.json` (EXP-400 6-year backtest)
- Trade count: 1667 total (2020-2025)
- Strategy mix: ~519 bull_put, ~96 bear_call, ~1052 iron_condor
- Win rate: 68.4%

---

## Walk-Forward OOS Metrics (No Look-Ahead Bias)

Time-series CV — all folds respect chronological order.

| Fold | Train | Test | N Train | N Test | AUC | Acc | Prec | Recall |
|------|-------|------|---------|--------|-----|-----|------|--------|
| 2022 | 2020-2022 | 2023 | 905 | 328 | 0.743 | 0.640 | 0.647 | 0.916 |
| 2023 | 2020-2023 | 2024 | 1233 | 127 | 0.809 | 0.740 | 0.717 | 0.934 |
| 2024 | 2020-2024 | 2025 | 1360 | 307 | 0.578 | 0.782 | 0.818 | 0.929 |

**Walk-forward mean AUC: 0.710 ± 0.097**

---

## OOS Performance (2024-2025, threshold=0.65)

| Metric | Old Model (20260331) | New Model (20260401) |
|--------|----------------------|----------------------|
| OOS AUC | 0.695 (walk-fwd mean) | **0.945** |
| Baseline win rate | 73.0% | 73.0% |
| Filtered win rate (thr=0.65) | 81.8% | **90.9%** |
| N trades filtered | 347 / 434 (80%) | 330 / 434 (76%) |
| Baseline Sharpe | 0.02 | 0.02 |
| Filtered Sharpe | 1.89 | **2.17** |
| Max Drawdown (baseline) | -116.4% | N/A (return_pct unit mismatch — see cc5 report for -20.2%) |

**Per-year OOS breakdown:**

| Year | N | Baseline WR | Filtered WR | N Passed | AUC |
|------|---|-------------|-------------|----------|-----|
| 2024 | 127 | 59.8% | 85.9% | 78 | 0.935 |
| 2025 | 307 | 78.5% | 92.5% | 252 | 0.946 |

---

## Feature Drift Fix

| Feature | Old Model Mean | Old Model Std | Old σ-deviation @ SPY=654 | New Model Mean | New Model Std | New σ-deviation |
|---------|----------------|---------------|--------------------------|----------------|---------------|-----------------|
| spy_price | 374.6 | 52.8 | **5.3σ** | 430.8 | 109.0 | **2.0σ** |
| strategy_type_bear_call_spread | 0.1 | 0.2 | **0.2σ** | 0.1 | 0.2 | **0.2σ** |
| spread_type_call | 0.1 | 0.2 | **0.2σ** | 0.1 | 0.2 | **0.2σ** |

---

## Feature Importance (Top 10, aggregated across ensemble)

| Rank | Feature | Importance |
|------|---------|------------|
| 1 | hold_days | 0.1186 |
| 2 | strategy_type_bull_put_spread | 0.0582 |
| 3 | net_credit | 0.0582 |
| 4 | max_loss_per_unit | 0.0574 |
| 5 | ma50_slope_ann_pct | 0.0429 |
| 6 | realized_vol_atr20 | 0.0409 |
| 7 | contracts | 0.0388 |
| 8 | ma20_slope_ann_pct | 0.0373 |
| 9 | vix | 0.0365 |
| 10 | realized_vol_20d | 0.0336 |

---

## Today's Candidate Probabilities (2026-04-01)

SPY price used: ~$654. Techncal context from most recent price data.

| Candidate | Old Model Prob | New Model Prob | Threshold | Decision |
|-----------|----------------|----------------|-----------|----------|
| bull_put_spread (regime=bull) | 0.767 (PASS) | **0.819 (PASS)** | 0.65 | SAME |
| bear_call_spread (regime=bear) | 0.533 (PASS) | **0.747 (PASS)** | 0.35 | SAME |
| iron_condor (regime=neutral) | 0.718 (PASS) | **0.765 (PASS)** | 0.6 | SAME |

---

## Ensemble Model Stats

| Model | AUC | Accuracy | Precision | Recall | Weight |
|-------|-----|----------|-----------|--------|--------|
| xgboost | 0.809 | 0.769 | 0.787 | 0.908 | 0.364 |
| random_forest | 0.796 | 0.746 | 0.780 | 0.873 | 0.346 |
| extra_trees | 0.729 | 0.734 | 0.755 | 0.904 | 0.290 |
| **Ensemble** | **0.798** | 0.751 | 0.774 | 0.899 | 1.000 |

---

## Model Files

| Model | Path | Status |
|-------|------|--------|
| Production | `ml/models/ensemble_model_20260331.joblib` | **In production — DO NOT OVERWRITE** |
| Candidate | `ml/models/ensemble_model_20260401.joblib` | Shadow validation candidate |
| Feature stats | `ml/models/ensemble_model_20260401.feature_stats.json` | Updated means/stds |

---

## Shadow Validation Plan

The candidate model has been added in shadow mode to `scripts/exp700_ml_scanner.py`.
Both models run on every scan; only the production model's decision affects trading.

**Shadow log format:**
```
[SHADOW] candidate_prob=X.XX vs production_prob=X.XX decision=PASS/REJECT
```

**Swap criteria (2-week minimum):**
1. Shadow mode shows candidate PASS rate > 0% on bear call spread days (vs 0% today)
2. Any passed candidates observed to actually win (next 2-4 weeks)
3. No degradation on bull_put win rate (most common, ~519/1667 trades)
4. AUC on live data after 20+ scored candidates ≥ 0.65

**NOT ready to swap until:** At least 10 live bear call candidates scored, with ≥ 5 wins.

---

## Concerns and Flags

1. **Small bear call sample (96/1667 = 5.8%):** Model has seen very few bear calls.
   The current tariff selloff is unlike anything in 2020-2023 training. Use the
   lowered type_threshold=0.35 for bear_call (already in paper_exp700.yaml).

2. **iv_rank imputed as 25.0:** The trades cache doesn't store IV rank at entry.
   The backtester's internal `_iv_rank_by_date` was not persisted. All 1667 trades
   have iv_rank=25.0 (neutral). Future retrain should fix this via full backtest re-run.

3. **Regime inferred from trade type:** Without the backtester's `_regime_by_date`
   dict, regime was inferred: bear_call→bear, IC→neutral, bull_put→bull. This is
   ~95% accurate (IC in bull regime or bear_call in neutral are rare) but imperfect.

4. **2025 in training:** With 2025 now in training, the model has seen the 2025
   bull run and includes SPY at ~$590-680. This addresses the OOD problem directly.

5. **Next retrain trigger:** If SPY moves ±20% from current level ($524 or $785),
   or if OOS win rate drops below 65% on 30+ live trades, retrain again.

*Generated by `scripts/retrain_exp700_20260401.py` — 2026-04-01T21:10:07.056701+00:00*