# EXP-1000-max: Intraday Mean Reversion on SPY Options

## Hypothesis

During low-volatility regimes, SPY implied volatility consistently overstates the magnitude of intraday moves. Credit spreads opened at 10:30 AM (after the open auction settles) and closed same-day capture this mean-reversion alpha via rapid theta decay on 0-DTE options.

## Rationale

1. **IV overstatement**: Options pricing embeds a volatility risk premium. On calm days (VIX < 20, regime = bull/sideways), actual intraday range is often 50-70% of what IV implies. Selling this gap is profitable.
2. **Theta acceleration**: 0-DTE options lose most of their time value during the trading day. Opening at 10:30 captures 60%+ of remaining theta by 3:30 PM.
3. **Mean reversion**: SPY intraday moves tend to revert after the first 30 minutes. A credit spread placed after the opening noise captures the reversion.
4. **Decorrelation**: Intraday strategies have fundamentally different return drivers than multi-day credit spreads (EXP-880), providing genuine portfolio diversification.

## Strategy

- **Entry**: 10:30 AM ET, after opening auction settles
- **Exit**: 3:30 PM same day (or profit target/stop-loss)
- **Structure**: Bull put credit spread, 0-DTE or 1-DTE
- **Strike selection**: 0.5% OTM (tighter than multi-day)
- **Regime filter**: only trade in bull/sideways/low_vol regimes
- **VIX gate**: VIX < 25 (high VIX = intraday gamma risk)
- **Signal**: ensemble model score + intraday momentum confirmation
- **Sizing**: Kelly-fractional, scaled by signal confidence

## Data

Uses training_data_combined.csv as base, filtering for short-DTE (≤2) trades and simulating intraday entry/exit timing. Actual 0-DTE data would require IronVault intraday snapshots (future enhancement).

## Success Criteria

- Sharpe > 3.0
- Win rate > 70%
- Correlation with EXP-880 < 0.3
- Positive in at least 4 of 6 years
