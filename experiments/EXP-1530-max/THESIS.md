# EXP-1530-max: Walk-Forward Out-of-Sample Validation

## Goal

Validate the EXP-1470 4-strategy portfolio using strict walk-forward:
NO look-ahead.  Expanding window trains on past years, tests on next.

## Method

- Fold 1: train 2020, test 2021
- Fold 2: train 2020-2021, test 2022
- Fold 3: train 2020-2022, test 2023
- Fold 4: train 2020-2023, test 2024
- Fold 5: train 2020-2024, test 2025

Per fold: compute IS Sharpe/CAGR/DD vs OOS. Check degradation.

## Key Questions

1. Does Sharpe degrade > 50% OOS?
2. Does CAGR hold > 50% of IS?
3. Does DD stay < 12% OOS?
4. Which years are hardest? (expect 2022)
5. Do the 4-strategy weights remain stable across folds?

## Success Criteria

- OOS/IS Sharpe ratio > 0.50 (< 50% degradation)
- OOS CAGR > 50% of IS CAGR
- OOS max DD < 12% in every fold
- Combined OOS Sharpe > 2.0
