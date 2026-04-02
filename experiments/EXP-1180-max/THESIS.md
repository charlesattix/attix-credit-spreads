# EXP-1180-max: Feature Importance & Signal Decay Analysis

## Hypothesis

Not all features contribute equally to alpha. Understanding which features
drive predictions, how quickly their signals decay, and which are redundant
enables: (1) model simplification, (2) faster retraining, (3) better
feature engineering priorities.

## Methods Implemented

1. **SHAP-style importance** — marginal contribution via random replacement
2. **Permutation importance** — accuracy drop from feature shuffling
3. **Feature interaction** — H-statistic proxy for pairwise interactions
4. **Signal half-life** — autocorrelation decay at lags 1-60 periods
5. **Correlation clustering** — union-find grouping of redundant features
6. **Regime-conditional importance** — features that only matter in certain regimes
7. **Sequential forward selection** — greedy optimal subset

## Key Findings

- Top 3 features (feat_00, feat_01, feat_02) account for majority of importance
- feat_03 is redundant with feat_00 (r > 0.70) — can be dropped
- feat_00 × feat_04 interaction detected by H-statistic
- Signal half-lives range from fast (1-2 periods) to persistent (60+ periods)
- Regime-conditional: different features dominate in bull vs bear

## Status: COMPLETE
- compass/feature_analysis.py: 420+ lines, 7 analysis methods
- tests/test_feature_analysis.py: 37 tests, all passing
