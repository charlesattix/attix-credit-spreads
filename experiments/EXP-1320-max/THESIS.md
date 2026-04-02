# EXP-1320-max: Intraday Volatility Clustering

## Hypothesis

Intraday realized vol clusters: a high-vol 30-minute block predicts continued high vol for the next 30-60 minutes (autocorrelation ~0.6-0.8). Detecting vol expansion early in the session → avoid selling premium. Detecting contraction → ideal for credit spreads.

## Method

1. Compute 5-min realized vol blocks across the trading day
2. EWMA vol smoother (λ=0.94, similar to RiskMetrics)
3. Detect expansion (vol rising > 1.5σ above session mean) and contraction
4. Generate same-day signals: sell premium in contraction, avoid in expansion
5. Overlay on EXP-880 entry timing

## Success Criteria

- Vol clustering autocorrelation > 0.5 at 1-block lag
- Expansion signal predicts higher EOD vol (AUC > 0.55)
- Overlay improves EXP-880 timing by ≥1pp WR
