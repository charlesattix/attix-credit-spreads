# EXP-890-max: Live Trading Integration Blueprint

## Hypothesis

A production-grade integration layer between COMPASS strategies and Alpaca broker, with comprehensive pre-trade risk checks, kill switch, reconciliation, and full audit trail, is the critical bridge from backtests to real money. Without it, even profitable strategies will fail operationally.

## Architecture

```
Strategy Signal → LiveTradingBlueprint
                      │
                      ├── Pre-Trade Risk Checks
                      │     ├── Position limits (per-strategy, portfolio)
                      │     ├── Drawdown limit (halt if DD > threshold)
                      │     ├── Correlation check (reject correlated positions)
                      │     ├── Margin check (reject if margin > 80%)
                      │     └── Daily loss limit
                      │
                      ├── Order Management
                      │     ├── Signal → Order translation
                      │     ├── Entry orders (limit, with timeout)
                      │     ├── Scale-in / scale-out logic
                      │     ├── Exit orders (profit target, stop, time)
                      │     └── Emergency liquidation
                      │
                      ├── Kill Switch
                      │     ├── Max drawdown trigger
                      │     ├── Anomaly detection (unusual fills, pricing)
                      │     ├── Manual override
                      │     └── Automatic recovery/restart logic
                      │
                      ├── Real-Time P&L
                      │     ├── Per-position mark-to-market
                      │     ├── Per-strategy attribution
                      │     └── Alert thresholds
                      │
                      ├── Reconciliation
                      │     ├── Paper vs live comparison
                      │     ├── Expected vs actual fills
                      │     └── Drift detection
                      │
                      └── Audit Trail
                            ├── Every signal received
                            ├── Every risk check result
                            ├── Every order submitted/filled/cancelled
                            └── Every P&L update
```

## Success Criteria

- All risk checks enforced before every order
- Kill switch triggers within 1 second of threshold breach
- Full audit trail of every decision
- Reconciliation detects >5bps drift
- Zero unintended orders in testing
