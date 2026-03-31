# EXP-950-max: Leverage Optimization Deep Dive

## Hypothesis

EXP-840 showed 2x regime leverage → 56% CAGR at 4.5% DD. EXP-880 crisis hedge gives 76.9% at 10.2% DD. By combining leverage + crisis hedge and sweeping systematically, we can find the optimal leverage that maximises geometric growth while keeping DD < 12%.

## Key Question

**Can we hit 100% CAGR with <12% DD? What leverage does it require?**

## Method

1. Sweep leverage 1.0x–4.0x in 0.25x increments with crisis hedge V2
2. For each: CAGR, DD, Sharpe, Calmar, worst year, worst month
3. Find: Kelly-optimal leverage, max-Sharpe leverage, max-return at DD<12%
4. Regime-conditional leverage with crisis hedge overlay
5. Monte Carlo: probability of >50%/100% CAGR at each level

## Success Criteria

- Identify leverage that achieves 100% CAGR with DD < 12%
- Kelly-optimal leverage clearly identified
- Full risk characterisation at recommended leverage
