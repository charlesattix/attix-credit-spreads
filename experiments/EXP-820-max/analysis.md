# EXP-820-max: Paper Trading Engine — Architecture & Usage

## Overview

`compass/paper_trading_engine.py` is a forward-testing framework that
simulates realistic trade execution with slippage, partial fills, margin
tracking, risk limit enforcement, and per-strategy P&L attribution.  It
replaces the simple dry-run mode in `live_bridge.py` with production-grade
simulation.

## Architecture

```
SignalSource ──→ PaperTradingEngine.submit_signal()
                      │
                      ├─→ RiskMonitor.check_new_position()
                      │     ├─ Circuit breaker (max DD)
                      │     ├─ Position count limit
                      │     ├─ Per-strategy limit
                      │     ├─ Margin utilisation cap (80%)
                      │     └─ Confidence gate (min 0.30)
                      │
                      ├─→ FillSimulator.simulate_fill()
                      │     ├─ Fill rate (95% default, 0-100%)
                      │     ├─ Slippage ($0.03-0.05/contract)
                      │     ├─ Commission ($0.65/contract × 2 legs)
                      │     └─ Partial fills (5% chance)
                      │
                      └─→ Position created → SQLite persisted
                            │
     PaperTradingEngine.step(date)
                      │
                      ├─→ Revalue all positions (sqrt theta decay)
                      ├─→ Check exits: expiration, profit target, stop loss
                      ├─→ Close positions → ClosedTrade recorded
                      ├─→ Daily P&L snapshot (per-strategy attribution)
                      └─→ Daily loss limit check
                            │
     PaperTradingEngine.get_performance()
                      │
                      └─→ PerformanceSummary: Sharpe, Sortino, DD,
                          profit factor, by_strategy, by_regime
```

## Components

### FillSimulator
- **Slippage**: configurable per-contract ($0.04 default), randomised ±50%
- **Fill rate**: configurable (95% default); unfilled orders rejected
- **Partial fills**: 5% probability, fills 60-90% of order
- **Commission**: $0.65/contract × 2 legs (industry standard)
- Open fills: credit reduced by slippage; close fills: debit increased

### RiskMonitor
Six-layer pre-trade risk check:
1. **Circuit breaker**: drawdown > max_drawdown_pct → halt all trading
2. **Position count**: total open positions ≤ max_positions (20)
3. **Per-strategy**: positions per strategy ≤ max_position_per_strategy (10)
4. **Margin**: total margin used ≤ 80% of capital
5. **Confidence**: signal confidence ≥ 0.30
6. **Daily loss**: running daily P&L > -max_daily_loss ($5K)

### PositionTracker
- Theta decay model: sqrt(time_elapsed / total_dte) — accelerating decay
- Exit triggers: expiration, profit target (default 50% of credit),
  stop loss (default 3.5× credit)
- Margin tracked per position ($500/spread default)

### P&L Attribution
- **Per-strategy**: each strategy's contribution to total P&L
- **Per-regime**: P&L broken down by market regime at entry
- **Attribution sum validation**: strategy P&Ls sum exactly to portfolio total
- **Daily snapshots**: date, total/realised/unrealised, positions, margin, drawdown

## Usage

### Single trade
```python
from compass.paper_trading_engine import PaperTradingEngine, Signal, EngineConfig

engine = PaperTradingEngine(EngineConfig(starting_capital=100_000))
sig = Signal(strategy="EXP-400", ticker="SPY", contracts=2,
             net_credit=1.5, max_loss=3.5, dte=30, confidence=0.7)
accepted, position_id = engine.submit_signal(sig)
snap = engine.step("2024-06-10")  # advance time
perf = engine.get_performance()
```

### Bulk replay of historical trades
```python
import pandas as pd
df = pd.read_csv("compass/training_data_combined.csv")
engine = PaperTradingEngine(EngineConfig(slippage_per_contract=0.04))
perf = engine.replay(df)
print(f"P&L: ${perf.total_pnl:+,.0f}, WR: {perf.win_rate:.0%}")
engine.generate_report("reports/paper_engine.html")
```

### JSON export for dashboards
```python
data = engine.export_json("results/paper_engine.json")
```

## Test Coverage

57 tests across 12 test classes:

| Class | Tests | Coverage |
|-------|-------|----------|
| TestDataclasses | 9 | All dataclass constructors |
| TestFillSimulator | 6 | Slippage, fill rate, commission, partial |
| TestRiskMonitor | 11 | All 6 risk checks + breaches |
| TestSubmitSignal | 6 | Accept, reject, unfill, commission |
| TestStep | 5 | Expiry, profit target, daily PnL |
| TestClosePosition | 3 | Trade recording, capital update |
| TestReplay | 3 | Bulk replay with slippage |
| TestPerformance | 5 | Win rate, attribution sums |
| TestExportJSON | 2 | Dict + file output |
| TestReport | 4 | HTML sections, charts |
| TestPersistence | 3 | SQLite position/trade/status |

## Integration Points

- **live_bridge.py**: Engine can replace the dry-run path
- **portfolio_simulator.py**: Feed engine's daily_pnl to portfolio optimiser
- **strategy_ensemble.py**: Use engine to evaluate ensemble vs individual signals
- **backtest_reality.py**: Feed engine results to reality checker for bias detection
