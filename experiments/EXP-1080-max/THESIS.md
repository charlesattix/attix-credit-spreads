# EXP-1080-max: Systematic Volatility Surface Trading

## Hypothesis

Mispricings in the SPY IV surface (skew steepness, term structure slope)
create systematic alpha.  By measuring the surface shape and trading when
it deviates from fair value, we can add a new uncorrelated return stream.

## Strategies

1. **Skew normality**: detect cheap/expensive wings → sell overpriced puts via bull put spreads, or sell overpriced calls via bear call spreads
2. **Term structure slope**: steep contango (front < back) → sell front-month premium via calendar spreads; flat/inverted → reduce or hedge
3. **Butterfly arbitrage**: when the smile is kinked, sell butterflies at the peak and buy at the troughs
4. **Combined**: overlay surface signals with existing regime detector for timing

## Success Criteria

- Sharpe > 1.5 for surface-timed entries vs always-on
- Skew signal adds >5% annual return
- Term structure timing reduces drawdown by >20%
- Uncorrelated with existing credit spread alpha (corr < 0.3)
