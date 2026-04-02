# EXP-1350-max: Dynamic Kelly Criterion

## Hypothesis
Adaptive Kelly sizing using rolling win rate/payoff ratio with regime modulation reduces drawdown while preserving most return vs fixed Kelly.

## Module
`compass/dynamic_kelly.py` — 43/43 tests passing

## Method
- Rolling Kelly at 20/60/120 day windows, blended (40%/35%/25%)
- Regime modulation: bull 0.50×, bear 0.30×, crash 0.15×
- Compared vs fixed Kelly, fixed 5%, risk parity, equal weight
