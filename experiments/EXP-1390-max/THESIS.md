# EXP-1390-max: Signal Decay Half-Life Analyzer

## Hypothesis

Every alpha signal has a measurable decay half-life. Knowing this allows:
- Optimal holding period per strategy
- Correct rebalance frequency (don't rebalance a slow signal daily)
- Signal combination weighting by freshness

## Methods

1. **Autocorrelation function** — ACF at lags 1-60 days
2. **Predictive R² decay** — R² of signal vs cumulative forward returns
3. **Information coefficient decay** — IC at horizons 1d-60d
4. **Exponential decay fit** — ln(|IC|) = a - λt → half-life = ln(2)/λ
5. **Rebalance recommendation** — optimal frequency from half-life

## Synthetic Test Signals

| Signal | AR(1) Coeff | Expected Decay | Category |
|--------|-------------|----------------|----------|
| ml_ensemble | 0.30 | Fast | fast (<5d) |
| regime_score | 0.85 | Medium | medium (5-20d) |
| momentum_20d | 0.97 | Slow | slow (>20d) |
| microstructure | ~0 | Very fast | fast |
| sentiment | combo | Medium | medium |
| random_noise | 0 | No signal | slow (∞) |

Tests verify ordering: fast < medium < slow half-lives.

## Status: COMPLETE
- compass/signal_decay_analyzer.py: 380+ lines
- tests/test_signal_decay_analyzer.py: 35 tests, all passing
