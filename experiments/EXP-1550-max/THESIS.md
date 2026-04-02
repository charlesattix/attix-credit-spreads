# EXP-1550-max: North Star Portfolio Deployment Plan

## Purpose
Complete production deployment spec for the 4-strategy North Star blend targeting 80%+ CAGR with <12% DD.

## Module
`compass/north_star_deployer.py` — 39/39 tests passing

## Strategies
| Strategy | Weight | DD Budget | Style |
|---|---|---|---|
| ML-CS-860 | 30% | 4% | ML credit spreads |
| Regime-Lev | 25% | 3% | Regime leverage |
| Intraday-MR | 20% | 3% | Mean reversion |
| Combined-750 | 25% | 2% | CS + vol blend |

## Components
1. ConfigGenerator: paper trading config with weights, leverage, risk gates
2. SignalOrchestrator: weight-average 4 strategy signals
3. RiskBudgetAllocator: 12% total DD → per-strategy budgets
4. RebalancingEngine: daily rebalance with tolerance bands + cost opt
5. CircuitBreakers: per-strategy DD halt, correlation spike, kill switch
