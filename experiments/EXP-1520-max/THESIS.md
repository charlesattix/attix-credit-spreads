# EXP-1520-max: North Star Validation Suite

## Purpose

EXP-1470 claims 206% CAGR at DD<12% (Sharpe 17.21) from a 4-strategy blend.
Before this becomes a production target, we MUST stress-test every assumption.

## 7 Validation Tests

1. **CPCV**: Combinatorial purged cross-validation (10 folds)
2. **Bootstrap CI**: 10K-sample confidence intervals for all metrics
3. **Weight sensitivity**: ±5% perturbation — is performance fragile?
4. **Leverage frontier**: 1x→8x efficient frontier mapping
5. **Regime analysis**: does it work in bull/bear/sideways/crisis?
6. **Cost sensitivity**: what if execution costs are 2x, 3x assumed?
7. **Correlation stress**: what if crisis correlations spike to 0.5?

## Pass/Fail Criteria

- CPCV: OOS Sharpe > 5.0 across all folds
- Bootstrap: 95% CI for Sharpe excludes zero
- Weights: <20% performance change at ±5% perturbation
- Leverage: 100% CAGR achievable at DD<15% (not just 12%)
- Regimes: positive return in ≥3 of 4 regimes
- Costs: profitable at 3x costs
- Correlation: DD<20% at crisis correlations of 0.5
