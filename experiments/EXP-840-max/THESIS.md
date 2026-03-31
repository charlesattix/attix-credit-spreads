# EXP-840-max: Portfolio Optimizer V2 — Leverage + Allocation

## Problem
Round 2 best result: EXP-750 at 29% annual, Sharpe 5.06, DD 2.8%. Excellent risk-adjusted but absolute returns are below the 40%+ target. The portfolio has massive unused risk budget (DD ceiling is 15%, actual is 2.8%).

## Hypothesis
With Sharpe 5.06 and DD 2.8%, we can safely apply 2-4x leverage while staying within the 15% DD ceiling. Combined with Kelly Criterion sizing, regime-adaptive leverage, and drawdown-controlled delevering, we can achieve 40-60% annual returns.

## Approach
1. **Kelly Criterion** — optimal fraction per strategy based on historical win rate and payoff ratio
2. **Dynamic leverage by regime** — bull: 2-3x, sideways: 1.5x, bear: 0.75x, crash: 0x
3. **Rebalance frequency analysis** — daily vs weekly vs monthly impact on returns/costs
4. **Transaction cost optimization** — tolerance bands to reduce unnecessary rebalancing
5. **Drawdown-controlled leverage** — auto-delever from 100% to 0% as DD approaches ceiling
6. **Regime-aware tilts V2** — use EXP-720 insights: momentum experiments 1.5x in bull, crash experiments 0x

## Success Criteria
- Annual return > 40% (levered)
- Max DD < 15%
- Sharpe > 3.0 (levered)
- All years profitable
- Leverage never exceeds 4x
