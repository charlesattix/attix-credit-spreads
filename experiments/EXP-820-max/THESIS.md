# EXP-820-max: Paper Trading Engine

## Hypothesis

A proper forward-testing engine with realistic execution modeling (slippage,
partial fills, margin), per-strategy P&L attribution, and risk limit
enforcement will reveal strategy performance characteristics that backtests
miss — particularly around execution quality, position sizing limits, and
regime transition behavior.

## Rationale

- Current paper trading uses simple dry-run fills with no slippage model
- No margin tracking — can't detect leverage constraint violations
- P&L attribution is trade-level only, not decomposed by strategy/day/regime
- Risk limits are checked at order time but not monitored continuously
- Need a simulation engine that can replay historical data *as if it were live*

## Architecture

```
SignalSource → PaperTradingEngine → FillSimulator → PositionTracker
                    ↓                                     ↓
              RiskMonitor ← ← ← ← ← ← ← ← ← ← ← PnLAttributor
                    ↓
              DashboardExporter (JSON + HTML)
```

## Components

1. **SignalSource**: Ingests signals from any compass strategy module
2. **FillSimulator**: Slippage ($0.03-0.05/contract), partial fills, delay
3. **PositionTracker**: Open/closed positions, margin requirements, Greeks
4. **RiskMonitor**: Max DD circuit breaker, position limits, correlation limits
5. **PnLAttributor**: Per-strategy, per-day, per-regime attribution
6. **DashboardExporter**: JSON summary + HTML report

## Success Criteria

- Engine runs full historical replay of 428 trades in < 5 seconds
- Slippage model reduces naive P&L by 2-5% (realistic cost)
- Risk monitor correctly triggers circuit breaker on drawdown > threshold
- Per-strategy attribution sums exactly to portfolio total
- 40+ tests covering all components
