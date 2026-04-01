# EXP-1070-max: Overnight Gap Strategy

## Hypothesis
The overnight risk premium in SPY options is consistently positive. Selling straddles at 3:55 PM and buying back at 9:35 AM captures theta decay during the ~18-hour overnight window while avoiding intraday volatility whipsaws. Historical data shows overnight returns in SPY are positive on average, with gap risk manageable through position sizing and regime filtering.

## Strategy
- Sell ATM straddle at 3:55 PM (close)
- Buy back at 9:35 AM (open)
- Skip high-VIX nights (VIX > 30)
- Size positions based on historical gap distribution (99th percentile gap risk)
- Use 0-1 DTE options for maximum theta capture

## Success Criteria
- Annual return > 20%
- Max DD < 10%
- Win rate > 60%
- Sharpe > 2.0
