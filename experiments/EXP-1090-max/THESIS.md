# EXP-1090-max: Cross-Asset Correlation Trading

## Hypothesis
Short-term breakdowns in SPY-QQQ/IWM/TLT correlations predict mean-reversion opportunities. When z-score of rolling correlation drops below -2σ, bet on convergence.

## Module
`compass/correlation_alpha.py` — 34/34 tests passing

## Features
- Rolling correlation tracker (SPY/QQQ, SPY/IWM, SPY/TLT)
- Correlation regime detector (high/normal/breakdown/divergence)
- Pair trade signals on breakdown → convergence bet
- Walk-forward backtest with max hold and stop loss
