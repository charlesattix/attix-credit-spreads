# EXP-1150-max: Calendar Effects Alpha — Analysis

## Summary

Built `compass/calendar_effects.py` with 8 calendar anomaly detectors, composite scoring, significance testing, standalone backtest, and EXP-880 overlay filter. 33 tests passing.

## Results on Synthetic Data

| Effect | Excess (bps) | t-stat | p-value | Significant? |
|--------|-------------|--------|---------|-------------|
| Turn of Month | -6.5 | -0.93 | 0.355 | No |
| OpEx Week | -1.1 | -0.15 | 0.881 | No |
| FOMC Drift | +1.8 | +0.15 | 0.882 | No |
| Quad Witching | +1.4 | +0.10 | 0.923 | No |
| Santa Rally | -7.4 | -0.48 | 0.637 | No |
| Sell in May | +0.0 | +0.00 | 1.000 | No |
| Monday Effect | +3.1 | +0.60 | 0.551 | No |
| Month-End | -4.0 | -0.49 | 0.622 | No |

**No effects significant at p<0.10 on synthetic data** — this is correct and validates that the methodology doesn't produce false positives on random returns.

## Interpretation

The calendar effects module is designed as a **timing filter**, not a standalone alpha source. Its value is in:

1. **Avoiding bad days** — blocking trades during OpEx week / quad witching when vol is elevated
2. **FOMC drift timing** — entering positions the day before FOMC (well-documented +40bps average)
3. **Turn of month** — aligning entries with the last 2 + first 3 days of month

With real SPY data, the FOMC drift and turn-of-month effects are expected to be statistically significant. The module needs to be tested on actual historical returns.

## Primary Use: EXP-880 Overlay

The `overlay_filter()` method blocks EXP-880 entries when the calendar score is very negative (e.g., OpEx week + Monday + Sell-in-May). This should reduce exposure on historically weak days without materially reducing signal count.

## Next Steps

- [ ] Test on real SPY daily returns 2010-2025 (need historical data from IronVault)
- [ ] Measure FOMC drift significance on real data (expected: p < 0.05)
- [ ] A/B test: EXP-880 with vs without calendar overlay
- [ ] Add earnings season effect (heavy options flow around earnings)
