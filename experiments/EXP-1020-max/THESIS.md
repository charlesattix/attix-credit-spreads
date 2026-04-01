# EXP-1020-max: 0-DTE Mean Reversion After Large Intraday Moves

## Hypothesis

SPY 0-DTE options exhibit a mean-reversion pattern after large intraday moves (>1% from open). The volatility risk premium on 0-DTE overprices the continuation of the move, making credit spreads sold against the direction of the move profitable ~60-65% of the time. High frequency (multiple trades/day on volatile days) compensates for the modest per-trade edge.

## Rationale

1. **Intraday mean reversion**: after a >1% move by 10:30 AM, SPY reverts 40-60% of the move by close on ~60% of days (well-documented effect)
2. **0-DTE gamma decay**: 0-DTE options lose gamma value rapidly after noon — a credit spread sold at 10:30 benefits from accelerating decay even if SPY only partially reverts
3. **Volatility overpricing**: 0-DTE IV is systematically high on large-move days because market makers widen spreads, creating a sellable premium
4. **Frequency advantage**: on days with >1% moves (about 30-40 per year in SPY), we can enter 1-2 trades per day, accumulating 30-80 trades/year

## Strategy

- **Trigger**: SPY moves >1% from open by 10:30 AM ET
- **Entry**: sell credit spread AGAINST the move direction
  - SPY down >1%: sell bull put spread (betting on reversion up)
  - SPY up >1%: sell bear call spread (betting on reversion down)
- **Structure**: 0-DTE credit spread, $3-5 wide, ~0.3% OTM from current price
- **Exit rules**:
  - Profit target: 50% of max profit (fast close)
  - Stop loss: 100% of credit received
  - Time stop: 30 minutes — close if neither target hit
  - Hard stop: 3:30 PM — close all remaining positions
- **Sizing**: small — max 2% portfolio risk per trade
- **Regime filter**: only in bull/sideways (skip bear/crash days where moves continue)

## Data Simulation

IronVault intraday data not available for 0-DTE specifically. Simulation approach:
1. Use historical SPY daily data (open, high, low, close, VIX) from 2020-2025
2. Identify days with >1% open-to-low or open-to-high moves
3. Model intraday reversion probability using historical intraday range statistics
4. Estimate 0-DTE credit spread P&L from IV and time decay

## Success Criteria

- Win rate > 55%
- Sharpe > 2.0
- Max DD < 5%
- Average 4+ trades per month (50+/year)
- Uncorrelated with EXP-880 multi-day strategy (ρ < 0.2)
