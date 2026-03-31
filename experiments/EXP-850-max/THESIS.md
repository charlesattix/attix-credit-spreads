# EXP-850-max: Execution Analytics & Market Impact

## Hypothesis

Slippage and market impact are the primary constraints on AUM scaling for options strategies. By modeling execution costs as a function of VIX, time-of-day, DTE, strike distance, spread width, and order size, we can determine:
1. The maximum realistic AUM per strategy before alpha decay
2. The optimal spread width for each AUM level
3. Smart order routing rules that minimise execution cost

## Problem Statement (from EXP-731 findings)

Slippage on narrow ($1-2) credit spreads is catastrophic:
- $43,566 total slippage on a backtest — 46% of the average winning trade
- Bid-ask spreads on SPY options range from $0.01 (ATM, low VIX) to $0.20+ (OTM, high VIX)
- At scale (>$10M), market impact compounds with bid-ask spread
- This is our #1 enemy for North Star AUM targets

## Models to Build

### 1. Slippage Model
Bid-ask spread = f(VIX, time_of_day, DTE, moneyness, spread_width)
- VIX: spreads widen 2-5x when VIX > 30
- Time of day: U-shaped — wide at open, tight mid-day, wide at close
- DTE: shorter DTE = tighter spreads (more liquid near-term)
- Moneyness: ATM tight, OTM 5-10x wider
- Spread width: wider spreads → proportionally lower slippage per dollar

### 2. Market Impact Model
Impact = f(order_size, daily_volume, urgency)
- Kyle lambda: permanent price impact per unit flow
- Square-root model: impact ∝ sqrt(shares/ADV)
- Temporary vs permanent decomposition
- Scale analysis: $1M, $10M, $100M, $1B notional

### 3. Optimal Spread Width Analysis
For each width ($1, $2, $3, $5, $10):
- Premium collected vs execution cost
- Net return after slippage
- Maximum capacity before alpha decay
- Break-even AUM where wider spreads become optimal

### 4. Capacity Estimation
Maximum AUM = f(strategy, market_conditions)
- Point where market impact exceeds 50% of edge
- Per-strategy capacity based on underlying liquidity
- Portfolio-level capacity considering cross-strategy overlap

## Success Criteria

- Quantify slippage model with R² > 0.5 against synthetic data
- Identify break-even AUM for each spread width
- Recommend optimal spread width per AUM tier
- Estimate maximum portfolio AUM with <20% alpha decay
- Provide actionable smart order routing rules

## Data Requirements

- SPY option bid-ask spread data (modeled from VIX relationship)
- SPY daily volume data
- Historical VIX levels 2020-2025
- Backtest trade data for calibration
