# EXP-1270-max: Adaptive Stop-Loss Optimizer

## Hypothesis

Fixed percentage stops ignore market conditions — they're too tight in volatile regimes (whipsawed out) and too loose in calm regimes (let losses run). ATR-based trailing stops with VIX-regime multipliers should reduce max DD by 20%+ while preserving >90% of returns.

## Stop Types Tested

1. **Fixed %**: constant stop distance (baseline)
2. **ATR trailing**: N × ATR(14) trailing from peak equity
3. **Chandelier**: highest high - N × ATR (trend-following stop)
4. **Keltner**: EMA ± N × ATR channel breach
5. **Volatility breakout**: Bollinger-style N × rolling std from peak

## Regime Multipliers (VIX-based)

| Regime | VIX Range | Stop Multiplier | Rationale |
|--------|-----------|-----------------|-----------|
| Low vol | VIX < 15 | 0.7× (tight) | Small moves matter in calm markets |
| Normal | 15-25 | 1.0× (base) | Standard conditions |
| High vol | 25-35 | 1.5× (wide) | Avoid whipsaw in volatile markets |
| Crisis | VIX > 35 | 2.0× (very wide) | Only exit on sustained moves |

## Success Criteria

- Best adaptive stop reduces DD by ≥20% vs fixed stop
- Return preservation ≥90% of no-stop returns
- Sharpe improvement ≥0.5 over fixed stop
