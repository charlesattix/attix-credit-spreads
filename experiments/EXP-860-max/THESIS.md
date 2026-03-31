# EXP-860-max: Production Ensemble Pipeline

## Hypothesis

EXP-810 proved the 3-model ensemble (XGB+RF+ET) achieves Sharpe 10.49 with 3.6% DD — superior risk-adjusted returns vs single XGBoost. A **production-grade** pipeline with quarterly retraining, confidence-graded sizing, ensemble disagreement detection, and feature drift monitoring should further improve OOS robustness and reduce drawdowns.

## Baseline (EXP-810 3-Model Ensemble at P≥0.75)
- Sharpe: 10.49
- Win rate: 87.2%
- Max DD: -3.6%
- OOS degradation: 4.1%

## Production Enhancements

### 1. Quarterly Retraining
Models retrained every quarter on expanding window. Compare vs static (train-once) model.

### 2. Confidence-Graded Sizing
Instead of binary P≥0.75 threshold, grade position size by confidence:
- P≥0.90: full size (100%)
- P≥0.80: 75% size
- P≥0.70: 50% size
- P<0.70: skip

### 3. Ensemble Disagreement Sizing
When base models disagree (high prediction variance), reduce position size. Low disagreement = high confidence = full size.

### 4. Feature Importance Tracking
Monitor feature importance stability across retraining windows. Alert when important features decay.

### 5. Model Health Monitoring
Track AUC per retraining window. Detect drift and trigger alerts.

## Success Criteria
- Quarterly retrained > static on OOS Sharpe
- Disagreement sizing reduces max DD further
- All years profitable
- OOS degradation < 3%
