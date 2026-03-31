# EXP-810-max: Signal Ensemble Testing

## Hypothesis

A single XGBoost classifier at P≥0.75 achieves Sharpe 12.37 and 89.3% WR (EXP-710). Ensembling multiple diverse classifiers should improve **out-of-sample** prediction quality by:
- Reducing model-specific overfitting through averaging
- Capturing different signal patterns (tree-based vs linear)
- Providing more robust probability calibration

## Baseline (EXP-710 at P≥0.75)
- Sharpe: 12.37
- Win rate: 89.3%
- Max DD: -4.92%
- Trades: 159 (of 368 OOS)
- Annual return: 15.9%

## Variants Tested

### A) Single XGBoost (baseline)
Reproduce EXP-710 with walk-forward expanding window.

### B) 3-Model Ensemble (XGBoost + RandomForest + ExtraTrees)
Walk-forward weighted average. Weights determined by held-out AUC per fold.

### C) Stacked Ensemble (Meta-Learner)
Ridge regression meta-learner trained on base model predictions.
Walk-forward: meta-learner never sees future data.

## Key Question
**Does ensembling beat single XGBoost on OUT-OF-SAMPLE data?**

## Success Criteria
- OOS Sharpe improvement over single XGBoost
- OOS win rate ≥ 89%
- Max DD ≤ 5%
- No OOS degradation increase vs baseline
