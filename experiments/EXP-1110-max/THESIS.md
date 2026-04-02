# EXP-1110-max: Cross-Asset Momentum Signal

## Hypothesis

Commodities (gold, oil, copper) and rates (TLT, HYG spread) momentum leads SPY options positioning by 1-3 days. Copper rallying + TLT falling + credit spreads tightening = risk-on regime forming — bullish for selling SPY put spreads. Reverse = risk-off — reduce or skip.

## Assets Tracked

| Asset | Ticker | Role | SPY Lead-Lag |
|-------|--------|------|-------------|
| Gold | GLD | Safe haven demand | 1-2 day lead (inverse) |
| Oil | USO | Growth proxy | 1 day lead (positive) |
| Copper | CPER | Manufacturing demand | 2-3 day lead (positive) |
| Treasuries | TLT | Risk appetite (inverse) | 1 day lead (inverse) |
| High Yield | HYG | Credit risk appetite | 1-2 day lead (positive) |
| Dollar | UUP | Risk sentiment (inverse) | 1 day lead (inverse) |

## Success Criteria

- At least 2 assets show statistically significant lead-lag with SPY
- Cross-asset signal improves EXP-880 timing (higher WR on signal-confirmed days)
- Composite signal Sharpe > 1.0 as standalone
