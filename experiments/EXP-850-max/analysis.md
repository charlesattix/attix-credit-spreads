# EXP-850-max: Execution Analytics & Market Impact — Analysis

## Executive Summary

**Slippage is the #1 constraint on strategy profitability at any scale.** Our models quantify the relationship between spread width, VIX, order size, and execution cost, revealing clear actionable recommendations.

## Key Findings

### 1. Slippage Destroys Narrow Spreads

| Width | Slippage % of Premium | Net Return | Max AUM |
|-------|----------------------|------------|---------|
| $1 | **28.6%** | 17.8% | $1M |
| $2 | 12.7% | 21.8% | $2M |
| $3 | 7.6% | 23.1% | $4M |
| **$5** | **3.6%** | **24.1%** | **$6M** |
| $10 | 1.3% | 24.7% | $12M |

**$1 spreads lose 28.6% of premium to slippage.** This confirms and quantifies the EXP-731 finding that slippage on narrow spreads is catastrophic. The bid-ask spread on both legs eats nearly a third of the collected premium.

**$5 spreads are the sweet spot**: only 3.6% slippage with 6x the capacity of $1 spreads.

### 2. Bid-Ask Spread Model

The bid-ask spread for SPY options depends on:

| Factor | Effect | Magnitude |
|--------|--------|-----------|
| VIX | Higher VIX → wider spreads | 2-3x at VIX 40 vs 15 |
| Moneyness | OTM → much wider | 5-10x at 10-delta |
| Time of Day | U-shaped: wide open/close | 2x at open vs mid-day |
| DTE | Shorter = tighter | 20% tighter at 14 vs 45 DTE |
| Width | Wider spreads → slightly tighter per $ | 15% tighter at $10 vs $1 |

**Actionable**: Trade mid-day (10:30-14:00), avoid first/last 30 minutes, prefer ATM/near-ATM strikes, prefer shorter DTE for tighter execution.

### 3. Market Impact at Scale

| Notional | Impact (bps) | Annual Cost ($) |
|----------|-------------|-----------------|
| $1M | 2.2 | ~$22K |
| $10M | 7.1 | ~$71K |
| $100M | 22.4 | ~$2.2M |
| $1B | 70.7 | ~$70M |

The square-root model shows impact scales as √(size/ADV). At $100M notional, we're consuming 22bps — nearly half of a 50bps edge. At $1B, impact alone (71bps) exceeds any realistic alpha.

### 4. Capacity Estimation

**Maximum AUM where >50% of alpha survives:**

| Strategy | Max AUM | Break-Even AUM | Notes |
|----------|---------|-----------------|-------|
| Credit Spread ($5w) | $50M+ | ~$200M | Best capacity |
| Iron Condor | $50M+ | ~$200M | 4 legs but wider |
| Vol Harvest | $25M+ | ~$100M | Less frequent trading |
| Short DTE | $5M | ~$15M | Lowest capacity — gap risk + liquidity |
| Credit Spread ($1w) | $5M | ~$20M | Slippage-constrained |

**Portfolio-level**: with three uncorrelated streams (EXP-800-max), total capacity is ~$100-150M before meaningful alpha decay. This is realistic for a systematic options fund.

### 5. Smart Order Routing

| Condition | Recommendation | Expected Savings |
|-----------|---------------|-----------------|
| VIX < 25, small size | Limit order, $0.01 from mid | 1-2 bps |
| VIX > 30 | Limit order, $0.02 from mid | 3-5 bps |
| High urgency or >50 contracts | Market order | Guaranteed fill |
| Optimal time | 10:30 - 14:00 | 2-3 bps vs open/close |

### 6. Partial Fill Model

Fill probability for limit orders:
- At mid: ~99% fill rate
- 1¢ from mid: ~85%
- 3¢ from mid: ~55%
- High VIX helps: wider spreads = more room to fill at limit

## Recommendations

### For Current Operations ($100K-$1M)
1. **Use $5 spreads**, not $1-2 (saves 25% of premium from slippage)
2. **Trade mid-day** (10:30-14:00 for tightest execution)
3. **Use limit orders** with $0.01 offset from mid
4. **Avoid opening/closing** 30-minute windows

### For Scaling to $10M+
1. **Shift to $5-10 spreads** exclusively
2. **Spread trades across 2-3 days** to reduce market impact
3. **Use TWAP-style execution**: split into 3-5 child orders
4. **Monitor participation rate**: stay below 2% of strike OI

### For North Star AUM ($100M+)
1. **Multi-underlying diversification**: SPY + QQQ + IWM + sector ETFs
2. **$10+ spreads** become necessary to maintain capacity
3. **Market making approach**: provide liquidity rather than take it
4. **Algorithmic execution**: proprietary smart router with venue optimization
5. **Realistic alpha target at scale**: 15-20% net (not 50%)

## Conclusion

The data is clear: **narrow spreads are a trap**. They look profitable in backtests that ignore execution costs, but in reality 28.6% of $1-spread premium goes to slippage. Moving to $5+ spreads immediately improves net returns from 17.8% to 24.1% while increasing capacity 6x.

Maximum realistic AUM for our current SPY-focused portfolio: **$50-150M** with proper spread width and execution optimization. Beyond that, multi-underlying diversification is required.

The "manage billions" North Star requires fundamental changes to strategy construction: wider spreads, multiple underlyings, market-making posture, and algorithmic execution. But $50-150M is an excellent and achievable intermediate target.
