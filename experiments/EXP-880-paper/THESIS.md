# EXP-880-paper: Crisis Hedge V2 Paper Trading Deployment

## Purpose

Paper-trade the validated EXP-880 Crisis Hedge V2 Ultra-Safe configuration to verify live execution matches backtest expectations before committing real capital.

## Configuration Summary

| Component | Setting | Source |
|-----------|---------|--------|
| **Signal** | 3-model ensemble (XGB+RF+ET), P≥0.75 | EXP-860 |
| **Confidence sizing** | 100%/75%/50% at P≥0.90/0.80/0.75 | EXP-860 |
| **Disagreement scaling** | Halve size when model std > 0.20 | EXP-860 |
| **Base leverage** | 2.0x | EXP-840/980 |
| **Regime multipliers** | bull 1.2×, sideways 0.8×, bear 0.3× | EXP-840 |
| **Crisis hedge** | V2 Ultra-Safe | EXP-880 |
| **VIX tiers** | Reduce at 25, minimum at 35, full hedge at 50 | EXP-880 |
| **DD delevering** | Start 2%, full at 7%, floor 0.20 | EXP-880 |
| **Put overlay** | Activate VIX > 30, 2% base cost | EXP-880 |
| **Recovery** | 10d momentum + VIX < 22, 20d ramp | EXP-880 |
| **Hard DD stop** | 12% (circuit breaker) | EXP-890 |
| **Max positions** | 8 (reduced for leverage) | Risk management |

## Backtest Expectations (from EXP-880/970)

| Metric | Backtest (2x) | Target Range (paper) |
|--------|--------------|---------------------|
| CAGR | 31-36% | 20-40% (±30% tolerance) |
| Max DD | 5.6% | < 12% |
| Sharpe | 4.4 | > 2.0 |
| Win Rate | 87-90% | > 75% |
| Trades/Year | ~30 | 20-40 |

## Validation Period

**8 weeks minimum** (matching EXP-400/401 protocol).

## Victory Conditions for Live Trading

1. Results within 30% of backtest expectations
2. No system errors or unintended trades
3. Crisis hedge activates correctly during VIX spikes
4. Win rate > 75%
5. Max drawdown < 12%
6. Ensemble model predictions match shadow log accuracy

## Files

| File | Description |
|------|-------------|
| `configs/paper_exp880.yaml` | Full paper trading configuration |
| `.env.exp880.example` | Environment variable template |
| `scripts/start_exp880_paper.sh` | Launcher script |
| `data/exp880/pilotai_exp880.db` | Trade database (created at runtime) |
| `logs/paper_exp880.log` | Execution log |
