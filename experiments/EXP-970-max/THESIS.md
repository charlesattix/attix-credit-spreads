# EXP-970-max: Combined Portfolio Walk-Forward Validation

## Purpose

EXP-960 showed 100% CAGR is achievable at 3.5x leverage on the combined CS+Vol portfolio. This experiment **validates that claim** with:

1. Year-by-year expanding-window walk-forward (no lookahead)
2. Correlation stability — does ρ ≈ 0 hold in every year including crises?
3. Drawdown decomposition — which leg causes worst DD?
4. Leverage stress testing — what if correlations spike to 0.5 during crisis?
5. Margin feasibility at 3.5x with portfolio margin

## Test Levels
- 1.0x (unlevered baseline)
- 2.5x (conservative production target)
- 3.5x (100% CAGR target)
