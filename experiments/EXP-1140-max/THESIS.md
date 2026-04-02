# EXP-1140-max: Multi-Timeframe Signal Fusion

## Hypothesis
Combining signals from 3 timeframes (5min/1D/1W) with attention-weighted fusion produces more robust predictions than any single timeframe. The attention mechanism learns which timeframe is most informative in the current volatility regime.

## Module
`compass/multi_timeframe_fusion.py` — 42/42 tests passing

## Architecture
```
5min bars → FeatureExtractor → Normalise ──┐
Daily bars → FeatureExtractor → Normalise ──┼→ AttentionFusion → UnifiedSignal
Weekly bars → FeatureExtractor → Normalise ─┘
```

## Features
- Per-TF features: momentum, mean-reversion z-score, realised vol, trend slope, RSI
- Normalisation to common -1..+1 scale with confidence
- Attention weights: confidence × accuracy × vol-regime adjustment
- Regime-adaptive: high-vol → trust intraday; low-vol → trust weekly
- Backtest compares fused vs each individual TF on Sharpe/DD/hit-rate
