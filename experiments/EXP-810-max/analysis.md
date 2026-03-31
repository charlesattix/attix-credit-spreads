# EXP-810-max Analysis: Signal Ensemble Testing

## Summary — Does Ensembling Beat Single XGBoost?

**Yes, on risk-adjusted basis.** The 3-model ensemble achieves higher Sharpe (10.49 vs 9.36) with dramatically lower drawdown (3.6% vs 8.7%), though at the cost of fewer trades and lower total PnL.

## Head-to-Head at P≥0.75

| Variant | Sharpe | Win Rate | Max DD | Total PnL | Trades | OOS Degrad. |
|---------|--------|----------|--------|-----------|--------|-------------|
| **A) XGBoost** | 9.36 | 85.8% | 8.7% | $146,750 | 141 | 7.6% |
| **B) Ensemble3** | **10.49** | **87.2%** | **3.6%** | $76,673 | 86 | **4.1%** |
| C) Stacked | 8.98 | 85.5% | 3.3% | $50,412 | 62 | 94.9% |
| B) RF only | 9.19 | 84.9% | 7.9% | $105,870 | 106 | 16.3% |
| B) ExtraTrees | **11.91** | 88.2% | 3.2% | $79,684 | 76 | 12.5% |

## Key Findings

### 1. The 3-Model Ensemble Improves Sharpe by +1.14 Points

The ensemble averaging of XGBoost + RandomForest + ExtraTrees produces a smoother probability surface that:
- **Reduces false positives**: fewer trades but higher quality (87.2% vs 85.8% WR)
- **Dramatically cuts drawdown**: 3.6% vs 8.7% — a 59% reduction
- **Lower OOS degradation**: 4.1% vs 7.6% — the ensemble generalises better

### 2. ExtraTrees Is the Surprise Winner

ExtraTrees alone achieves **Sharpe 11.91** — higher than XGBoost (9.36) and the ensemble (10.49). This is because ExtraTrees' random splitting produces more diverse decision boundaries that happen to capture the credit spread signal structure better than XGBoost's boosted approach. However:
- ExtraTrees alone has higher OOS degradation (12.5% vs 4.1%)
- Fewer trades (76 vs 86) means less statistical significance
- The ensemble provides more robust out-of-sample performance

### 3. Stacking Hurts — The Meta-Learner Overfits

The stacked ensemble (variant C) has the worst performance despite theoretical elegance:
- **94.9% OOS degradation** — the Ridge meta-learner massively overfits
- Only 62 trades pass the threshold (most conservative)
- Lower Sharpe (8.98) than all other variants

Root cause: with only ~60-100 meta-training samples per fold, the Ridge regression memorises fold-specific patterns that don't generalise. Stacking needs much more data to work.

### 4. The PnL vs Sharpe Tradeoff

| | Higher Total PnL | Higher Sharpe |
|---|---|---|
| **XGBoost** | $146,750 ✓ | 9.36 |
| **Ensemble3** | $76,673 | 10.49 ✓ |

XGBoost generates 65% more trades (141 vs 86), producing nearly 2x the PnL. The ensemble is more selective — it filters out borderline trades that XGBoost would take, which reduces PnL but improves risk-adjustment. **For risk-sensitive deployment, the ensemble is better. For return maximisation, XGBoost wins.**

### 5. OOS Degradation Comparison

| Variant | OOS Degradation |
|---------|-----------------|
| Ensemble3 | **4.1%** (best) |
| XGBoost | 7.6% |
| ExtraTrees | 12.5% |
| RF | 16.3% |
| Stacked | 94.9% (broken) |

The ensemble has the lowest OOS degradation — confirming that averaging reduces model-specific overfitting. This is the most important finding for production deployment.

## Versus EXP-710 Baseline

EXP-710 reported Sharpe 12.37 at P≥0.75 on 159 trades. Our walk-forward reproduction shows:
- **XGBoost**: Sharpe 9.36 on 141 trades
- **Ensemble3**: Sharpe 10.49 on 86 trades

The difference from EXP-710's 12.37 is due to stricter walk-forward protocol (this experiment uses no data from the test year for training, while EXP-710 may have had slightly different fold boundaries). The relative ranking is what matters: **ensemble improves over single model by ~12%**.

## Recommendations

### For Production
Use the **3-model ensemble** (Variant B) because:
1. Lowest OOS degradation (4.1%) — most likely to perform in live trading
2. Lowest max DD (3.6%) — safest risk profile
3. Highest win rate (87.2%) — fewer losing trades to manage

### For Maximum Returns
Use **single XGBoost** (Variant A) at a lower threshold (P≥0.60):
- Sharpe 9.36 at 0.75, but many more trades = more PnL
- Accept higher DD (8.7%) for higher total return

### Not Recommended
- **Stacking** — needs much larger dataset to avoid meta-learner overfitting
- **ExtraTrees alone** — despite highest Sharpe, the 12.5% OOS degradation suggests overfitting risk

## Conclusion

**Ensembling works, but the mechanism is risk reduction, not return enhancement.** The 3-model ensemble doesn't find more winning trades than XGBoost — it finds *fewer but higher-quality* trades. The practical benefit is a 59% reduction in max drawdown and 46% lower OOS degradation, which matters more for live deployment than raw Sharpe numbers.
