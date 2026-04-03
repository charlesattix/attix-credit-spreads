# EXP-880-real Status

**Status**: COMPLETE — CRITICAL FINDINGS
**Date**: 2026-04-03
**Result**: Real IronVault data reveals strategy fails with actual options pricing

## Key Findings

The EXP-880 base strategy (ML-filtered credit spreads, 2x leverage, crisis hedge V2)
**fails catastrophically** when backtested with real options data from IronVault,
compared to the original heuristic backtest (76.9% CAGR, 10.2% DD).

### Real Data Results (262 trades, 2020-2024)

| Year | Trades | PnL | Return | Win Rate | Max DD |
|------|--------|-----|--------|----------|--------|
| 2020 | 58 | -$47,839 | -49.2% | 50.0% | -82.0% |
| 2021 | 18 | +$3,432 | +6.4% | 94.4% | -1.9% |
| 2022 | 134 | -$1,043 | -3.7% | 77.6% | -22.1% |
| 2023 | 36 | +$4,800 | +8.2% | 88.9% | -5.4% |
| 2024 | 16 | -$60,549 | -107.9% | 31.2% | -122.2% |

**RUIN EVENT**: Capital went negative on 2024-08-30

### Root Causes

1. **Heuristic vs real pricing gap**: The original EXP-880 used estimated credit fractions;
   real options data shows much tighter spreads with lower credits
2. **Crisis Hedge V2 not integrated into Backtester**: The backtester class doesn't use
   the CrisisHedgeControllerV2 — it only has a hard circuit breaker. Without dynamic
   delevering, full-size positions are taken in high-vol regimes
3. **8.5% flat risk sizing is too aggressive**: With real options data and no dynamic
   scaling, losses are disproportionately large
4. **COVID 2020 and Aug 2024 VIX events**: Both caused catastrophic losses that overwhelm
   the high win rate in calm periods

### Bugs Found & Fixed

1. **backtester.py daily fallback bug**: `_find_real_spread` skipped the original strike
   when falling back from intraday to daily pricing — only tried offset strikes. Fixed.
2. **min_credit_pct config location**: Strategy reads from `strategy_params` but config
   had it under `risk` — caused 15% default instead of intended 5%

## Deliverables
- [x] `experiments/EXP-880-real/backtest.py` — IronVault-only backtest
- [x] `experiments/EXP-880-real/results/summary.json` — full results
- [x] `experiments/EXP-880-real/results/report.html` — HTML report
- [x] `backtest/backtester.py` — daily fallback bug fix
