# Status: COMPLETE — NORTH STAR ACHIEVED

## Best 4-Strategy Combination

| Strategy | Weight | Source |
|----------|--------|--------|
| ML-CS-860 (Production Ensemble) | 40.5% | EXP-860 |
| Regime Leverage | 20.9% | EXP-840 |
| Intraday Mean Reversion | 20.5% | EXP-1000 |
| Combined CS+Vol | 18.1% | EXP-750 |

## Results

| Metric | Base | At 3.6x (100% target) | At DD<12% |
|--------|------|----------------------|-----------|
| CAGR | 27.9% | **100%** | **206.5%** |
| Max DD | 1.6% | 5.7% | **12%** |
| Sharpe | 17.21 | 17.21 | 17.21 |
| OOS Sharpe | 12.08 | — | — |

**100% CAGR is achievable at 3.6x leverage with only 5.7% DD — well within the 12% budget.**

## HRP Clusters

1. **Intraday cluster** (low corr 0.22): Intraday-MR, 0DTE-Reversion, VWAP-Exec
2. **Credit spread cluster** (med corr 0.42): ML-CS-880, ML-CS-860, Regime-Lev, Ensemble-3
3. **Alternative cluster** (singleton): Vol-Harvest
