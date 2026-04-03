# EXP-880-real: ML Production Ensemble — Real IronVault Data Validation

## Hypothesis

Re-backtest the EXP-880 base strategy (ML-filtered credit spreads with Crisis
Hedge V2 and 2x regime leverage) using **exclusively real options data from
IronVault** (options_cache.db).  No synthetic pricing, no np.random for
prices/returns — every entry credit and exit price comes from actual Polygon
historical option bars.

This is the definitive validation of the strategy that all overlays are built on.

## Strategy

Identical to EXP-880-max:
- **Underlying:** SPY
- **Position Type:** Credit spreads (bull put + bear call, regime-adaptive)
- **DTE:** 15-25 days (target 15)
- **Spread Width:** $12
- **Strike Selection:** 2% OTM nominal
- **Direction:** Combo regime (bull→puts, bear→calls, neutral→both)
- **ML Filter:** P>=0.75 ensemble threshold (XGB+RF+ET)
- **Leverage:** 2x base with regime multipliers
- **Crisis Hedge:** V2 Ultra-Safe (min_scale=0.20, DD 2%→7%)

## Data Source

`IronVault.instance()` → `data/options_cache.db` (944 MB)
- 168K SPY option contracts
- 5.67M daily option bars
- Coverage: 2020-01-02 → 2025-12-31

## Success Criteria

| Metric | Target | Notes |
|--------|--------|-------|
| CAGR | > 30% | Conservative vs original 76.9% — real data has gaps |
| Max Drawdown | < 20% | With crisis hedge protection |
| Sharpe Ratio | > 2.0 | Risk-adjusted performance |
| Win Rate | > 70% | ML-filtered entry quality |
| Trades/Year | > 20 | Sufficient signal frequency |

## Date

2026-04-03
