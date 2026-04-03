# EXP-1580-max: Year-by-Year Walk-Forward Performance Report

## Purpose

Compute ACTUAL year-by-year returns (2020-2025) for the North Star portfolio
(EXP-1470 4-strategy blend with HRP weights). Validate that the aggregate
metrics hold up when decomposed into individual calendar years across
different market regimes.

## Method

1. Use per-strategy per-year backtest data for the 4 North Star strategies
2. Combine with HRP weights from EXP-1470 (ML-CS-860: 40.5%, Regime-Lev: 20.9%,
   Intraday-MR: 20.5%, Combined-750: 18.1%)
3. Compute correlation-adjusted drawdowns per year
4. Show three portfolio variants:
   - **Base (unlevered)**: Raw weighted portfolio
   - **3.6x levered**: Fixed leverage matching EXP-1470 target
   - **DD<12% capped**: Dynamic leverage scaled so worst DD stays within 12%
5. Compare against SPY buy-and-hold, EXP-400, and EXP-401 baselines

## Market Regimes Covered

| Year | Regime | VIX Avg | Key Events |
|------|--------|---------|------------|
| 2020 | Crisis→Recovery | ~29 | COVID crash, 34% SPY drawdown, massive recovery |
| 2021 | Bull | ~19 | Steady uptrend, low vol, meme stocks |
| 2022 | Bear | ~25 | Rate hikes, inflation, -18% SPY |
| 2023 | Recovery | ~17 | AI rally, banking crisis, strong H2 |
| 2024 | Bull | ~15 | Election year, momentum, new ATHs |
| 2025 | Mixed | ~22 | Tariff uncertainty, rotation (partial year) |

## North Star Targets

- 100% CAGR achievable at DD<12%
- All years profitable
- Sharpe > 6 on base portfolio
- Outperform SPY, EXP-400, EXP-401 in every year
