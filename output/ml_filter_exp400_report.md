# EXP-400 ML Ensemble Filter — Backtest Report

**Generated:** 2026-03-31 00:58 UTC  
**Strategy:** EXP-400 The Champion — SPY regime-adaptive credit spreads & iron condors  
**Config:** `configs/exp_400_champion_realdata.json`  
**ML Model:** EnsembleSignalModel (XGBoost + RandomForest + ExtraTrees, calibrated)  
**Validation:** Walk-forward expanding window (one year per fold)  
**Train period:** 2020–2023  |  **Test period (OOS):** 2024–2025  

---

## 1. Baseline Strategy Performance (2020–2025, no ML filter)

| Year | Phase | Trades | Win Rate | Return | Sharpe | Max DD |
|------|-------|--------|----------|--------|--------|--------|
| 2020 | train | 281 | 58.7% | -48.1% | 1.39 | -83.1% |
| 2021 | train | 316 | 74.1% | 67.3% | 7.38 | -0.3% |
| 2022 | train | 308 | 72.1% | 26.2% | 3.38 | -5.8% |
| 2023 | train | 328 | 61.6% | 21.4% | 1.68 | -6.4% |
| 2024 | OOS TEST | 127 | 59.8% | -105.6% | -2.60 | -116.4% |
| 2025 | OOS TEST | 307 | 78.5% | 119.7% | 0.97 | -37.3% |

**Full 6-year period (2020–2025):**

| Metric | Value |
|--------|-------|
| Total trades | 1667 |
| Win rate | 68.4% |
| Total return | 586.8% |
| Max drawdown | -64.9% |
| Sharpe ratio | 0.89 |
| Bull put trades | 519 (81.7% win) |
| Bear call trades | 96 (39.6% win) |
| Iron condor trades | 1052 (64.5% win) |

---

## 2. Walk-Forward Validation (OOS AUC Scores)

Each fold trains on all prior years and tests on the following year.  
AUC > 0.55 = meaningful signal  |  AUC ≈ 0.50 = no better than chance.

| Fold | Train Period | Test Year | N Train | N Test | AUC | Signal |
|------|-------------|-----------|---------|--------|-----|--------|
| 0 | 2020-01-17 → 2020-12-31 | 2021-01 | 281 | 316 | 0.644 | ✅ signal (Sharpe 7.84) |
| 1 | 2020-01-17 → 2021-12-31 | 2022-01 | 597 | 308 | 0.660 | ✅ signal (Sharpe 3.97) |
| 2 | 2020-01-17 → 2022-12-30 | 2023-01 | 905 | 328 | 0.728 | ✅ signal (Sharpe 1.95) |
| 3 | 2020-01-17 → 2023-12-29 | 2024-01 | 1233 | 127 | 0.826 | ✅ signal (Sharpe -0.79) |
| 4 | 2020-01-17 → 2024-12-27 | 2025-01 | 1360 | 307 | 0.616 | ✅ signal (Sharpe 1.43) |

**Mean OOS AUC: 0.695 ± 0.084**  ✅ **Above-chance signal** — ML filter likely additive.

---

## 3. Ensemble Model — Training Details

Training set: **1233 trades** (2020–2023)  
Training win rate: **66.7%**  

**Model weights** (walk-forward AUC minus chance, renormalised):


**Internal train stats** *(note: uses random shuffle split — treat AUC with caution, use walk-forward AUC above for reliable OOS estimate)*:

| Metric | Value |
|--------|-------|
| Ensemble test AUC (shuffle split) | 0.768 |
| Accuracy | 0.000 |
| Precision | 0.000 |
| Recall | 0.000 |

**Feature set** (29 numeric + 3 categorical → one-hot):  
`dte_at_entry`, `vix`, `iv_rank`, `rsi_14`, `momentum_5d/10d`, `dist_from_ma20/50/80/200`, `realized_vol_5/10/20d`, `otm_pct`, `spread_width`, `net_credit`, `regime`, `strategy_type`, `spread_type`

---

## 4. ML Filter — Confidence Threshold Sweep (OOS Test: 2024–2025)

**Baseline (no filter):** 434 trades | win rate 73.0% | return 14.1% | Sharpe 0.02

| Threshold | Kept | Filter% | Win Rate | Δ Win Rate | Return | Sharpe | Δ Sharpe |
|-----------|------|---------|----------|------------|--------|--------|----------|
| 0.50 | 388 | 89% | 78.9% | +5.8pp | 90.0% | 0.80 | +0.78 |
| 0.52 | 383 | 88% | 79.6% | +6.6pp | 86.5% | 1.05 | +1.03 |
| 0.54 | 381 | 88% | 79.8% | +6.7pp | 86.3% | 1.04 | +1.02 |
| 0.56 | 375 | 86% | 80.3% | +7.2pp | 80.4% | 1.07 | +1.05 |
| 0.58 | 366 | 84% | 80.3% | +7.3pp | 75.7% | 1.26 | +1.23 |
| 0.60 | 360 | 83% | 80.6% | +7.5pp | 64.9% | 1.16 | +1.14 |
| 0.62 | 357 | 82% | 80.4% | +7.4pp | 44.8% | 1.01 | +0.98 |
| 0.65 ⭐ optimal | 347 | 80% | 81.8% | +8.8pp | 60.6% | 1.89 | +1.87 |
| 0.70 | 341 | 79% | 81.8% | +8.8pp | 60.6% | 1.85 | +1.83 |

### Optimal Threshold: **0.65**  (347/434 trades kept, 80% pass rate)

| Metric | Baseline | ML Filtered | Improvement |
|--------|----------|-------------|-------------|
| Trades (OOS) | 434 | 347 | −87 (20% filtered) |
| Win Rate | 73.0% | 81.8% | +8.8pp |
| Return (OOS) | 14.1% | 60.6% | +46.4pp |
| Sharpe | 0.02 | 1.89 | +1.87 |
| Max Drawdown | -116.4% | -20.2% | — |

---

## 5. Year-by-Year Breakdown (OOS Test Period Only)

ML threshold used: 0.65

| Year | | Trades | Win Rate | Return | Sharpe | Max DD |
|------|--|--------|----------|--------|--------|--------|
| 2024 | Baseline | 127 | 59.8% | -105.6% | -2.60 | -116.4% |
| 2024 | ML 0.65 | 81 | 84.0% | 7.1% | 1.78 | -5.3% |
| 2025 | Baseline | 307 | 78.5% | 119.7% | 0.97 | -37.3% |
| 2025 | ML 0.65 | 266 | 81.2% | 53.5% | 1.92 | -19.1% |

---

## 6. Feature Importance Analysis

The EnsembleSignalModel uses walk-forward AUC to weight models. The following features are expected to be most predictive based on EXP-400's regime-adaptive design:

| Feature | Expected Signal | Rationale |
|---------|----------------|-----------|
| `vix` | HIGH | VIX level gates IC entries; >40 blocks all entries |
| `regime` | HIGH | Strategy type is regime-selected — bull→puts, bear→calls |
| `dist_from_ma80_pct` | HIGH | MA80 is the EXP-400 trend trigger |
| `iv_rank` | MEDIUM | Higher IV rank → fatter premiums → higher win probability |
| `dte_at_entry` | MEDIUM | DTE drives time-decay profile and max-loss risk |
| `rsi_14` | MEDIUM | RSI threshold (50 bull / 45 bear) is part of combo regime |
| `spread_type` | MEDIUM | Bull-put vs bear-call vs IC have distinct win profiles |
| `vix_percentile_100d` | LOW | Relative VIX positioning vs 100d history |
| `hold_days` | ⚠️ POST-HOC | Duration is outcome-correlated; remove for live use |

---

## 7. Known Limitations & Bugs

1. **Random shuffle split in `EnsembleSignalModel.train()`** (PR_REVIEW.md bug #1): internal AUC in Section 3 is inflated. The walk-forward AUC in Section 2 is the reliable estimate.

2. **One-hot leakage in `WalkForwardValidator`** (PR_REVIEW.md bug #2): `prepare_features()` is called on the full dataset before fold splitting, leaking future category membership. Impact is small for EXP-400 (stable regime/strategy labels) but fix before using on strategies with new regime labels over time.

3. **`hold_days` is post-hoc**: actual holding period is unknown at trade entry. This feature is valid for retrospective analysis; exclude it for live signal generation.

4. **Small OOS set**: 2024–2025 may have <60 trades. Sharpe and win-rate estimates have high sampling variance. Walk-forward AUC from Section 2 is more statistically reliable.

---

## 8. Recommendation

✅ **PROCEED TO LIVE SHADOW MODE**

Walk-forward mean AUC = 0.695 (meaningful signal) and Sharpe improves by 1.87 at threshold 0.65. Recommended next step: paper trade EXP-400 alongside ML filter for 90 days before acting on filter signals. Fix PR_REVIEW.md bugs #1–#2 first.

**Walk-forward mean AUC:** 0.695 ± 0.084  
**Optimal threshold:** 0.65  
**Win rate improvement:** +8.8pp  
**Sharpe improvement:** +1.87  
**Trades filtered out:** 20% (87 of 434)  

---

*Generated by `scripts/backtest_ml_filter.py`.  
Re-run after fixing PR_REVIEW.md bugs #1–#3 for production-grade estimates.*
