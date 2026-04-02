# EXP-1100-max: Correlation Regime Switching + Dispersion Trading

## Hypothesis
1. **Correlation regime switching:** When cross-asset correlations spike (risk-off), credit spread losses compound. Detecting the regime and delevering can reduce DD while maintaining exposure in dispersed markets.
2. **Dispersion trading:** Implied correlation (from index IV vs component IVs) is systematically higher than realised correlation. Selling SPY strangles while buying component strangles captures this premium.

## Modules Built
- `compass/dispersion_trader.py` — implied correlation calculator, dispersion entry signals, vega-balanced sizing, P&L attribution, full backtest engine

## Success Criteria
- Dispersion trades profitable >55% of the time
- Correlation regime detected before major selloffs
- Vega-neutral positioning limits vol exposure
