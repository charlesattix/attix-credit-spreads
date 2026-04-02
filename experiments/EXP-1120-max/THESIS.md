# EXP-1120-max: Order Flow Imbalance Alpha

## Hypothesis

Volume-weighted order flow imbalance (buy vs sell aggressor) at the daily
level predicts next-day SPY direction with >55% accuracy.  OHLCV proxy:
close position within the day's range measures net buying pressure.

## Method

1. **OFI proxy from OHLCV**: close-location value (CLV) = (close - low) / (high - low)
   - CLV > 0.5 → buyers dominated
   - CLV < 0.5 → sellers dominated
2. **Accumulation/Distribution**: CLV × volume → cumulative delta proxy
3. **Tick imbalance bars**: detect volume-clock regime shifts
4. **Signal generation**:
   - Extreme OFI (|z| > 2): contrarian (mean-reversion)
   - Moderate OFI (0.5 < |z| < 2): trend-following
5. **Backtest**: standalone alpha + as overlay filter for EXP-880

## Success Criteria

- Standalone next-day direction accuracy > 55%
- Sharpe improvement when combined with EXP-880 > 10%
- Signal is uncorrelated with existing regime/VIX signals (corr < 0.3)
