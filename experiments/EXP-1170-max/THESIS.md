# EXP-1170-max: Dynamic Hedging Engine

## Hypothesis

Continuous portfolio hedging — delta-neutral rebalancing, OTM put
protection, VIX call overlay — can reduce credit spread portfolio
drawdown by 40-60% while costing less than 3% of annual returns
through regime-adaptive sizing and cost optimization.

## Components

1. Delta-neutral hedging with SPY shares (rebalance when |delta| > threshold)
2. Tail risk puts: OTM puts sized by portfolio VaR (5th percentile)
3. VIX call overlay: buy VIX calls when term structure inverts
4. Regime-adaptive hedge ratio: aggressive in bear, minimal in bull
5. Cost optimizer: minimize hedge cost while meeting protection target
6. Effectiveness tracker: alpha vs hedge cost P&L attribution

## Success Criteria

- Hedged max DD < 60% of unhedged DD
- Annual hedge cost < 3% of portfolio value
- Sharpe improvement > 15% over unhedged
- Crisis survival: COVID DD < 15%, 2022 DD < 12%
