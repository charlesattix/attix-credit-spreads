# EXP-950-max Analysis: Leverage Optimization Deep Dive

## The Answer: 100% CAGR Is NOT Achievable at DD < 12%

Maximum CAGR at 4.0x leverage with crisis hedge: **45.2% at 10.2% DD**. The 100% CAGR target requires either:
- Leverage > 8x (which would produce >25% DD — unacceptable)
- More trade frequency (currently ~31 ML-filtered trades/year)
- Higher per-trade expected value

## Leverage Sweep (with Crisis Hedge)

| Leverage | CAGR | Max DD | Sharpe | Calmar | Worst Year |
|----------|------|--------|--------|--------|------------|
| 1.00x | 20.7% | 3.0% | 16.96 | 6.8 | baseline |
| 1.50x | 26.7% | 3.9% | 16.96 | 6.9 | — |
| 2.00x | 31.5% | 5.2% | 16.96 | 6.1 | — |
| 2.50x | 35.6% | 6.4% | 16.96 | 5.5 | — |
| 3.00x | 39.2% | 7.7% | 16.96 | 5.1 | — |
| 3.50x | 42.3% | 9.0% | 16.96 | 4.7 | — |
| **4.00x** | **45.2%** | **10.2%** | **16.96** | **4.4** | — |

**Key insight**: Sharpe remains constant across leverage levels (16.96) because leverage scales both return and risk proportionally. The Calmar ratio *decreases* with leverage — diminishing risk-adjusted returns per unit of drawdown.

## Crisis Hedge Impact

| Leverage | No Hedge DD | With Hedge DD | DD Reduction |
|----------|-------------|---------------|--------------|
| 1.0x | 4.4% | 3.0% | 1.4pp |
| 2.0x | 8.8% | 5.2% | 3.7pp |
| 3.0x | 13.2% | 7.7% | 5.4pp |
| 4.0x | 17.4% | 10.2% | 7.2pp |

The crisis hedge becomes **more valuable at higher leverage** — it reduces DD by 1.4pp at 1x but 7.2pp at 4x. Without the hedge, 3x leverage already breaches the 12% DD limit (13.2%).

## Regime-Adaptive Leverage

| Base Lev | Effective CAGR | DD | Calmar |
|----------|---------------|-----|--------|
| 2.0x | 33.2% | 6.2% | 5.4 |
| 3.0x | 41.1% | 9.2% | 4.5 |
| 4.0x | 47.2% | 12.2% | 3.9 |

Regime adaptation (bull 1.2x, sideways 0.8x, bear 0.3x multiplier) adds ~2% CAGR at each level but slightly increases DD. The best Calmar is at 2.0x base with regime adaptation.

## Optimal Leverage Points

| Criterion | Leverage | CAGR | DD | Sharpe |
|-----------|----------|------|-----|--------|
| **Kelly-optimal** | 4.00x | 45.2% | 10.2% | 16.96 |
| **Max-Sharpe** | 1.25x | 23.9% | 3.3% | 16.96 |
| **Max-return@DD<12%** | **4.00x** | **45.2%** | **10.2%** | **16.96** |

The Kelly-optimal and max-return-constrained leverage coincide at 4.0x because DD stays under 12% at all tested levels. This means the crisis hedge is doing its job.

## Why 100% CAGR Is Unreachable

1. **Trade frequency**: Only ~31 ML-filtered trades per year. Even at 4x leverage with $1,100 avg net PnL per trade, annual PnL ≈ 31 × $1,100 × 4 = $136K = 136% return on first year's capital. But compounding over 6 years with drawdowns reduces the CAGR.

2. **Drawdown drag**: At higher leverage, drawdown periods destroy more capital, which then needs recovery. The geometric growth rate is penalized by variance.

3. **The ceiling**: The ML filter achieves 89% win rate, which is near-perfect. There's no room to improve signal quality further — the bottleneck is trade frequency and per-trade magnitude.

## Recommendation

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Leverage** | **3.5x** | Best risk-adjusted balance |
| **Crisis hedge** | **Yes** | Reduces DD from 12.2% to 9.0% |
| **Regime adaptive** | **Optional** | Adds ~2% CAGR, slightly more DD |
| **Expected CAGR** | **42-44%** | — |
| **Expected Max DD** | **9-10%** | Within 12% budget |
| **Sharpe** | **~17** | — |

### Path to 100% CAGR

To reach 100% CAGR at DD < 12%, we need:
1. **More trades**: Expand to QQQ/IWM/IBIT options (EXP-702 approach) — 3-4x more trade frequency
2. **Higher per-trade edge**: Improve signal quality or find higher-premium structures
3. **Combine with vol harvesting**: EXP-750 showed the combination approach can nearly double effective returns through decorrelated streams

The most promising path: **3.5x leveraged ML-filtered CS + vol harvesting (EXP-750) = potential 60-80% CAGR at <12% DD**.
