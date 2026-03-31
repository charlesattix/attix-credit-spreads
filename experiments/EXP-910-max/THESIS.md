# EXP-910-max: North Star Portfolio Construction

## Goal

Assemble the ULTIMATE portfolio combining every proven component from
Rounds 1-4 into a single walk-forward backtest targeting the North Star:
100% CAGR, <12% max DD, 6.0+ Sharpe.

## Components Integrated

| Component | Source | Contribution |
|-----------|--------|-------------|
| ML ensemble filter | EXP-860 (Sharpe 12.30, 89.6% WR) | Trade selection |
| Multi-underlying | EXP-870 (6 assets, $2B capacity) | Diversification + capacity |
| Crisis hedge V2 | EXP-880 (76.9% CAGR, 10.2% DD) | Tail protection |
| Regime detector V2 | EXP-900 (41% whipsaw reduction) | Timing + leverage |
| Kelly + regime leverage | EXP-840 (56-96% CAGR range) | Position sizing |

## Architecture

```
Market Data → Regime Detector V2 (HMM + rules)
                    ↓
              Regime Label (bull/bear/high_vol/crisis)
                    ↓
    ┌───────────────┼───────────────┐
    ↓               ↓               ↓
  SPY/QQQ/IWM    GLD/TLT         IBIT
  (equity leg)   (hedge leg)     (alt leg)
    ↓               ↓               ↓
  ML Ensemble    ML Ensemble     ML Ensemble
  Filter P≥0.60  Filter P≥0.60   Filter P≥0.60
    ↓               ↓               ↓
  Kelly Sizing × Regime Leverage
    ↓               ↓               ↓
  Crisis Hedge Overlay (VIX-triggered protection)
    ↓
  Combined Portfolio → Daily P&L → Performance Metrics
```

## Success Criteria

- CAGR ≥ 100% (North Star target)
- Max DD ≤ 12%
- Sharpe ≥ 6.0
- Calmar ≥ 8.0
- Capacity ≥ $500M
- All 6 years profitable (2020-2025)
