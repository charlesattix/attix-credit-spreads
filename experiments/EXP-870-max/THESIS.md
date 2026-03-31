# EXP-870-max: Multi-Underlying Expansion

## Problem

EXP-850 showed SPY-only AUM caps at $50-150M before market impact destroys
alpha.  To reach $500M+ (and eventually billions), we MUST diversify across
uncorrelated underlyings.

## Hypothesis

Credit spread strategies that work on SPY can be adapted to QQQ, IWM, GLD,
TLT, and IBIT with underlying-specific calibration.  A multi-underlying
portfolio will:
- Increase capacity 3-5x via independent liquidity pools
- Improve portfolio Sharpe via imperfect correlation
- Reduce concentration risk

## Underlyings

| Ticker | Asset Class | Why |
|--------|-------------|-----|
| SPY | US Large Cap | Baseline — deepest liquidity |
| QQQ | Nasdaq/Tech | Different vol surface, growth factor |
| IWM | Small Cap | Wider spreads, different cycle |
| GLD | Gold | Crisis diversifier, negative equity corr |
| TLT | Treasuries | Counter-cyclical, rate-driven |
| IBIT | Bitcoin ETF | Uncorrelated crypto exposure |

## Key Questions

1. What are realistic bid-ask spreads, volumes, and OI for each?
2. Which underlyings have uncorrelated return streams to SPY strategies?
3. What is the capacity per underlying?
4. What is the optimal portfolio allocation across underlyings?

## Success Criteria

- Portfolio capacity > $500M
- Portfolio Sharpe > individual underlying Sharpe
- At least 3 underlyings with correlation < 0.5 to SPY
- Max drawdown < 15% at portfolio level
