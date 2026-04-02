# EXP-1280-max: Correlation Breakdown Detector

## Hypothesis

Sudden breakdown in cross-asset correlations (SPY-TLT, SPY-GLD,
equity-credit) signals regime transitions 2-5 days before traditional
VIX-based indicators.  The absorption ratio (Kritzman 2011) — fraction
of total variance explained by top eigenvalues — spikes before crises.

## Method

1. Rolling correlation matrices at 20d, 60d, 120d windows
2. Absorption ratio: top-N eigenvalues / total variance
3. Correlation regime: normal, breakdown, crisis (from AR z-score)
4. Early warning: AR spike + correlation dispersion increase
5. Backtest as EXP-880 timing overlay: reduce exposure 2-5 days early

## Success Criteria

- Early warning fires 2-5 days before VIX spike >25 in >60% of events
- Absorption ratio predicts drawdown >5% with >55% accuracy
- Combined with EXP-880: reduces crisis DD by >20% vs VIX-only trigger
