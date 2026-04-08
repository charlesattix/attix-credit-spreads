# EXP-1470-real: North Star Portfolio — Real IronVault Backtest

## Purpose

Re-backtest the EXP-1470 North Star 4-strategy blend using ONLY real option
prices from IronVault.  The original EXP-1470 used `np.random` Monte Carlo
over hard-coded `STRATEGY_CATALOG` metrics — that is synthetic data.

This experiment answers: **What are the ACTUAL returns when each strategy
runs trade-by-trade through the production Backtester with real Polygon
option prices?**

## What Was Wrong With EXP-1470

The original `compass/optimal_portfolio_v3.py`:
1. Hard-codes strategy metrics in `STRATEGY_CATALOG` (lines 110-123)
2. Uses `np.random.RandomState` to Monte Carlo random weight combinations
3. Computes portfolio metrics analytically from these hard-coded numbers
4. Never touches IronVault or any real option data
5. Claims: 27.9% base CAGR, 1.6% DD, Sharpe 17.21, 206% CAGR at DD<12%

## Method

1. Each of the 4 strategies runs independently through `backtest.backtester.Backtester`
2. All option prices come from `IronVault.instance()` → `options_cache.db`
3. Real slippage, commissions, data gaps, regime detection
4. Extract per-year equity curves, compute yearly CAGR/DD/Sharpe/WR
5. Combine with HRP weights (ML-CS-860: 40.5%, Regime-Lev: 20.9%,
   Intraday-MR: 20.5%, Combined-750: 18.1%)
6. Compare honestly against original synthetic claims

## Strategies

| Strategy | Config | Delta | DTE | Risk/Trade | Spread |
|----------|--------|-------|-----|------------|--------|
| ML-CS-860 | Conservative ensemble | 0.12 | 35 | 4% | $5 |
| Regime-Lev | Aggressive regime-adaptive | 0.12 | 35 | 8.5% | $12 |
| Intraday-MR | Short-DTE mean reversion | 0.08 | 7 | 2% | $5 |
| Combined-750 | Moderate blend | 0.10 | 30 | 6% | $12 |

## Expected Outcome

Real returns will likely be **significantly lower** than synthetic claims because:
- Real spreads have wider bid/ask than analytical models assume
- Data gaps cause missed trades (IronVault returns None on cache miss)
- Regime detection isn't perfect (wrong direction = losing trades)
- Slippage and commissions compound over 1000s of trades
- The original Sharpe of 17.21 is almost certainly overfit
