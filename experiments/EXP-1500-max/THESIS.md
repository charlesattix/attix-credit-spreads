# EXP-1500-max: Live Trading Simulation Engine

## Hypothesis
A realistic simulation modeling slippage, partial fills, queue priority, market impact, and latency provides much more accurate performance estimates than standard mid-price backtests.

## Module
`compass/live_sim_engine.py` — 42/42 tests passing

## Components
1. SpreadDynamics: VIX-scaled, time-of-day-varying bid-ask spreads
2. QueueModel: depth-based fill probability for limit orders
3. LatencyModel: log-normal 10-500ms random latency
4. MarketImpactModel: Kyle lambda √(Q/ADV) impact
5. PartialFillModel: per-contract fill probability
6. Multi-strategy comparison: ideal vs realistic returns
