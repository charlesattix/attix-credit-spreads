# EXP-1160-max: Smart Execution Engine

## Hypothesis

Naive market orders lose 5-15 bps per trade in execution costs. A smart execution engine using TWAP/VWAP slicing, adaptive limit pricing, and market impact modeling can reduce this to 2-5 bps — saving $2K-8K/year on a $100K portfolio at 30 trades/year.

## Algorithms

1. **TWAP**: split order evenly over N time slices
2. **VWAP**: weight slices by expected volume profile
3. **Adaptive**: start at mid, walk toward aggressive side based on urgency

## Key Metrics

- Implementation shortfall vs arrival price
- Market impact (temporary + permanent)
- Fill rate by algorithm and order size
- Cost savings vs naive market orders
