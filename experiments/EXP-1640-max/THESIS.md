# EXP-1640-max: Sector Momentum with Options Overlay

## Hypothesis

Sectors with strong recent momentum (top 20-day returns) offer
favorable put-selling conditions: momentum winners are less likely
to breach OTM put strikes within 30 DTE.

## Strategy

1. Rank XLF, XLI, XLK, XLE by trailing 20-day return every 2 weeks
2. Sell OTM put credit spreads on the top-ranked sector ETF
3. Skip the worst-ranked sector entirely (avoid momentum losers)
4. Target: 5% OTM, $1-$2 wide, 30-35 DTE, VIX < 30 filter

## Data

- Sector ETF prices: Yahoo Finance (real)
- Options pricing: IronVault options_cache.db (real Polygon data)
- NO synthetic data

## Validation

- Walk-forward: train 2020-2023, test 2024-2025
- Year-by-year breakdown
- SPY correlation analysis
