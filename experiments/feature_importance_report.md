# Feature Importance Analysis — Walk-Forward

**Generated:** 2026-03-29 09:00
**Dataset:** `compass/training_data_combined.csv` (428 trades)
**Folds:** 5 (year-based expanding window)
**Baseline WF AUC:** 0.8199 +/- 0.0517

## 1. Walk-Forward Fold Summary

| Fold | Train Years | Test Year | Train N | Test N | AUC |
|------|-------------|-----------|---------|--------|-----|
| 0 | 2020 | 2021 | 60 | 101 | 0.8350 |
| 1 | 2020, 2021 | 2022 | 161 | 59 | 0.7319 |
| 2 | 2020, 2021, 2022 | 2023 | 220 | 69 | 0.8685 |
| 3 | 2020, 2021, 2022, 2023 | 2024 | 289 | 69 | 0.8274 |
| 4 | 2020, 2021, 2022, 2023, 2024 | 2025 | 358 | 70 | 0.8367 |

## 2. Feature Importance Rankings

Ranked by **composite** (average of XGBoost gain rank and permutation importance rank).
**Stability** measures consistency across folds (1.0 = identical in every fold).

| Rank | Feature | Gain Mean | Gain Stability | Perm Mean | Perm Stability | Composite |
|------|---------|-----------|----------------|-----------|----------------|-----------|
| 2 | net_credit | 0.0682 | 0.92 | 0.0952 | 0.55 | 2.5 |
| 3 | contracts | 0.1163 | 0.58 | 0.0135 | 0.54 | 3.0 |
| 3 | strategy_type_CS | 0.1838 | 0.65 | 0.0084 | 0.67 | 3.0 |
| 5 | max_loss_per_unit | 0.0270 | 0.76 | 0.0206 | 0.46 | 5.0 |
| 7 | spread_type_bull_put | 0.1265 | 0.81 | 0.0025 | 0.23 | 7.0 |
| 8 | otm_pct | 0.0569 | 0.74 | 0.0038 | 0.44 | 8.0 |
| 8 | iv_rank | 0.0276 | 0.64 | 0.0044 | 0.22 | 8.0 |
| 8 | realized_vol_5d | 0.0242 | 0.85 | 0.0057 | 0.56 | 8.5 |
| 10 | dte_at_entry | 0.0332 | 0.49 | 0.0017 | 0.29 | 10.0 |
| 10 | hold_days | 0.0236 | 0.60 | 0.0047 | 0.41 | 10.0 |
| 11 | ma20_slope_ann_pct | 0.0176 | 0.62 | 0.0070 | 0.27 | 11.0 |
| 12 | vix_percentile_50d | 0.0193 | 0.63 | 0.0042 | 0.39 | 12.5 |
| 12 | realized_vol_20d | 0.0270 | 0.69 | 0.0013 | 0.30 | 12.5 |
| 13 | momentum_10d_pct | 0.0146 | 0.63 | 0.0111 | 0.53 | 13.0 |
| 18 | rsi_14 | 0.0156 | 0.62 | 0.0013 | 0.23 | 17.5 |
| 18 | momentum_5d_pct | 0.0195 | 0.78 | 0.0000 | 0.01 | 18.0 |
| 18 | dist_from_ma200_pct | 0.0146 | 0.63 | 0.0020 | 0.30 | 18.0 |
| 19 | dist_from_ma50_pct | 0.0150 | 0.64 | 0.0011 | 0.30 | 19.0 |
| 20 | realized_vol_10d | 0.0157 | 0.62 | 0.0002 | 0.05 | 19.5 |
| 22 | dist_from_ma80_pct | 0.0129 | 0.63 | 0.0005 | 0.16 | 22.5 |
| 23 | spy_price | 0.0116 | 0.63 | 0.0008 | 0.42 | 23.0 |
| 24 | days_since_last_trade | 0.0120 | 0.64 | 0.0001 | 0.03 | 24.0 |
| 24 | ma50_slope_ann_pct | 0.0234 | 0.66 | -0.0006 | 0.00 | 24.5 |
| 24 | vix_percentile_20d | 0.0238 | 0.73 | -0.0012 | 0.00 | 24.5 |
| 26 | regime_high_vol | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | strategy_type_IC | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | regime_low_vol | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | strategy_type_SS | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | spread_type_bear_call | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | regime_crash | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | spread_type_unknown | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | regime_bear | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | spread_width | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 26 | regime_bull | 0.0000 | 0.00 | 0.0000 | 0.00 | 26.5 |
| 28 | vix_percentile_100d | 0.0161 | 0.63 | -0.0009 | 0.00 | 27.5 |
| 28 | vix | 0.0168 | 0.63 | -0.0023 | 0.00 | 28.0 |
| 29 | dist_from_ma20_pct | 0.0141 | 0.64 | -0.0001 | 0.00 | 29.0 |
| 29 | realized_vol_atr20 | 0.0134 | 0.63 | -0.0000 | 0.00 | 29.0 |
| 32 | day_of_week | 0.0097 | 0.52 | -0.0005 | 0.00 | 32.0 |

## 3. Signal vs Noise Classification

**Signal features** (15): consistently contribute to AUC
  - `net_credit` (gain=0.0682, perm=0.0952)
  - `contracts` (gain=0.1163, perm=0.0135)
  - `strategy_type_CS` (gain=0.1838, perm=0.0084)
  - `max_loss_per_unit` (gain=0.0270, perm=0.0206)
  - `spread_type_bull_put` (gain=0.1265, perm=0.0025)
  - `otm_pct` (gain=0.0569, perm=0.0038)
  - `iv_rank` (gain=0.0276, perm=0.0044)
  - `realized_vol_5d` (gain=0.0242, perm=0.0057)
  - `dte_at_entry` (gain=0.0332, perm=0.0017)
  - `hold_days` (gain=0.0236, perm=0.0047)
  - `ma20_slope_ann_pct` (gain=0.0176, perm=0.0070)
  - `realized_vol_20d` (gain=0.0270, perm=0.0013)
  - `momentum_10d_pct` (gain=0.0146, perm=0.0111)
  - `ma50_slope_ann_pct` (gain=0.0234, perm=-0.0006)
  - `vix_percentile_20d` (gain=0.0238, perm=-0.0012)

**Ambiguous features** (13): mixed signal, keep for now
  - `vix_percentile_50d` (gain=0.0193, perm=0.0042)
  - `rsi_14` (gain=0.0156, perm=0.0013)
  - `momentum_5d_pct` (gain=0.0195, perm=0.0000)
  - `dist_from_ma200_pct` (gain=0.0146, perm=0.0020)
  - `dist_from_ma50_pct` (gain=0.0150, perm=0.0011)
  - `realized_vol_10d` (gain=0.0157, perm=0.0002)
  - `dist_from_ma80_pct` (gain=0.0129, perm=0.0005)
  - `spy_price` (gain=0.0116, perm=0.0008)
  - `days_since_last_trade` (gain=0.0120, perm=0.0001)
  - `vix_percentile_100d` (gain=0.0161, perm=-0.0009)
  - `vix` (gain=0.0168, perm=-0.0023)
  - `dist_from_ma20_pct` (gain=0.0141, perm=-0.0001)
  - `realized_vol_atr20` (gain=0.0134, perm=-0.0000)

**Noise features** (11): candidates for pruning
  - `regime_high_vol` (gain=0.0000, perm=0.0000)
  - `strategy_type_IC` (gain=0.0000, perm=0.0000)
  - `regime_low_vol` (gain=0.0000, perm=0.0000)
  - `strategy_type_SS` (gain=0.0000, perm=0.0000)
  - `spread_type_bear_call` (gain=0.0000, perm=0.0000)
  - `regime_crash` (gain=0.0000, perm=0.0000)
  - `spread_type_unknown` (gain=0.0000, perm=0.0000)
  - `regime_bear` (gain=0.0000, perm=0.0000)
  - `spread_width` (gain=0.0000, perm=0.0000)
  - `regime_bull` (gain=0.0000, perm=0.0000)
  - `day_of_week` (gain=0.0097, perm=-0.0005)

## 4. Ablation Analysis

AUC drop when each feature is **removed** from the model.
Positive = feature helps; negative = feature hurts (removing it improves AUC).

| Feature | AUC Drop | Verdict |
|---------|----------|---------|
| net_credit | +0.0084 | KEEP |
| max_loss_per_unit | +0.0050 | NEUTRAL |
| iv_rank | +0.0038 | NEUTRAL |
| dist_from_ma200_pct | +0.0030 | NEUTRAL |
| vix_percentile_50d | +0.0027 | NEUTRAL |
| dist_from_ma20_pct | +0.0024 | NEUTRAL |
| strategy_type_CS | +0.0013 | NEUTRAL |
| momentum_10d_pct | +0.0010 | NEUTRAL |
| rsi_14 | +0.0008 | NEUTRAL |
| dist_from_ma50_pct | +0.0001 | NEUTRAL |
| spy_price | -0.0003 | NEUTRAL |
| day_of_week | -0.0004 | NEUTRAL |
| regime_bear | -0.0004 | NEUTRAL |
| regime_crash | -0.0004 | NEUTRAL |
| regime_high_vol | -0.0004 | NEUTRAL |
| regime_low_vol | -0.0004 | NEUTRAL |
| vix_percentile_100d | -0.0006 | NEUTRAL |
| regime_bull | -0.0007 | NEUTRAL |
| spread_type_bull_put | -0.0008 | NEUTRAL |
| spread_type_unknown | -0.0010 | NEUTRAL |
| strategy_type_IC | -0.0011 | NEUTRAL |
| strategy_type_SS | -0.0011 | NEUTRAL |
| spread_type_bear_call | -0.0011 | NEUTRAL |
| days_since_last_trade | -0.0015 | NEUTRAL |
| dte_at_entry | -0.0020 | NEUTRAL |
| ma50_slope_ann_pct | -0.0021 | NEUTRAL |
| realized_vol_10d | -0.0021 | NEUTRAL |
| hold_days | -0.0023 | NEUTRAL |
| momentum_5d_pct | -0.0024 | NEUTRAL |
| realized_vol_20d | -0.0025 | NEUTRAL |
| dist_from_ma80_pct | -0.0028 | NEUTRAL |
| spread_width | -0.0036 | NEUTRAL |
| vix | -0.0037 | NEUTRAL |
| realized_vol_5d | -0.0037 | NEUTRAL |
| realized_vol_atr20 | -0.0040 | NEUTRAL |
| ma20_slope_ann_pct | -0.0052 | PRUNE |
| contracts | -0.0062 | PRUNE |
| otm_pct | -0.0065 | PRUNE |
| vix_percentile_20d | -0.0093 | PRUNE |

## 5. Pruning Recommendations

**Recommended to remove** (8 features):
These features actively **hurt** AUC when present (negative ablation drop):
  - `vix` (AUC improves by 0.0037 when removed)
  - `vix_percentile_20d` (AUC improves by 0.0093 when removed)
  - `ma20_slope_ann_pct` (AUC improves by 0.0052 when removed)
  - `realized_vol_atr20` (AUC improves by 0.0040 when removed)
  - `realized_vol_5d` (AUC improves by 0.0037 when removed)
  - `spread_width` (AUC improves by 0.0036 when removed)
  - `otm_pct` (AUC improves by 0.0065 when removed)
  - `contracts` (AUC improves by 0.0062 when removed)

**Consider removing** (10 features):
Near-zero importance and permutation impact — likely noise:
  - `regime_high_vol`
  - `strategy_type_IC`
  - `regime_low_vol`
  - `strategy_type_SS`
  - `spread_type_bear_call`
  - `regime_crash`
  - `spread_type_unknown`
  - `regime_bear`
  - `regime_bull`
  - `day_of_week`

## 6. Methodology Notes

- **Walk-forward validation**: Year-based expanding window (no data leakage)
- **Gain importance**: XGBoost's built-in metric measuring total gain from splits on each feature
- **Permutation importance**: Measures AUC drop when feature values are shuffled in the test set
- **Stability score**: 1 / (1 + coefficient_of_variation) across folds
- **Ablation**: Full walk-forward re-run with each feature excluded

---
*Generated by compass/feature_importance.py*