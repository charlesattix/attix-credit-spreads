# EXP-880-max: Crisis Hedge Integration

## Problem
EXP-840 Regime Leverage 2x achieves 56% CAGR at 4.55% DD on calibrated synthetic data. But synthetic data smooths crises. Under actual GFC (-57%), COVID (-34%), and 2022 (-25%) shocks with 1.5x credit-spread beta, unhedged 2x leverage would produce 15-40% portfolio drawdowns — breaching or hitting our ceiling.

## Hypothesis
Layering CrisisHedgeController V2 on top of the Regime Leverage 2x variant can survive all historical crises with <15% DD while preserving 40%+ CAGR. The key is: gradual delevering (not binary), VIX-triggered put overlays, and smart recovery detection to re-lever quickly after crises resolve.

## Approach
1. **CrisisHedgeController V2** extending existing crisis_hedge.py with:
   - Configurable min_scale (default 0.40 vs V1's 0.0)
   - Smoother VIX scaling with more granular breakpoints
   - Put spread tail hedge overlay with cost-benefit analysis
   - Drawdown-controlled delevering (gradual, not binary)
   - Recovery detection: momentum + vol regime to re-lever
   - Hedge cost tracking (drag in normal markets)
2. **Backtest** the EXP-840 Regime Leverage 2x variant with crisis overlay
3. **Stress test** against GFC, COVID, 2022, Flash Crash scenarios
4. **Compare** hedged vs unhedged performance

## Success Criteria
- CAGR > 40% (hedged)
- Max DD < 15% under ALL historical crisis scenarios
- Hedge cost < 3% annual drag in normal markets
- Recovery within 60 trading days of all crises
