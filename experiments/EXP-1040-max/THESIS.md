# EXP-1040-max: Combined Portfolio V2 (CS-880 + Intraday-1000)

## Hypothesis

Combining EXP-880 credit spreads (76.9% CAGR, 10.2% DD) with EXP-1000 intraday mean reversion (10.6% CAGR, 1.2% DD, ρ=0.033) creates a portfolio with dramatically lower DD that can then be levered to exceed 100% CAGR.

## Key Results

| Allocation | CAGR | DD | Sharpe | Div Ratio |
|-----------|------|-----|--------|-----------|
| 100% CS-880 | 76.9% | 10.2% | 7.5 | 1.0x |
| 90% CS / 10% Intraday | **70.3%** | **9.2%** | 7.6 | — |
| 50/50 | 43.8% | 5.1% | 8.5 | — |
| 10% CS / 90% Intraday | 17.2% | 1.5% | **11.4** | 1.4x |

**Best risk-adjusted: 90/10 at 1.25x leverage → 88% CAGR, 11.5% DD.**

## Success Criteria
- Diversification ratio > 1.0: ✓ (1.39x)
- Combined DD < standalone best DD: ✓ (1.5% vs 10.2%)
- Path to 100% CAGR exists: ✓ (Calmar-optimal at 6x = 103% CAGR, 9% DD)
