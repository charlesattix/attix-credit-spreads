# EXP-1010-max: Intraday Signal Enhancement

## Hypothesis

The current ensemble achieves 89% WR using daily-frequency features (VIX, RSI, momentum, IV rank). Adding **intraday features** computed at signal-generation time — VWAP deviation, order flow imbalance, intraday momentum, microstructure metrics — can push WR above 90% and reduce false positives.

## Rationale

1. **Timing matters**: a trade entered when SPY is above VWAP and trending has higher success probability than one entered into selling pressure
2. **Microstructure signals**: bid-ask spread widening, volume spikes, and order imbalance carry short-term predictive power that daily features miss
3. **Regime confirmation at entry**: intraday momentum at 10:30 AM confirms or contradicts the daily regime classification
4. **The 11% failure rate** (current false positives) likely includes trades entered at intraday extremes — features measuring entry timing quality could filter these

## Baseline (EXP-860 Production Ensemble)
- Win rate: 89.6%
- Sharpe: 12.30
- False positive rate: ~10%

## Proposed Intraday Features (12)

### Price-Based (4)
1. **vwap_deviation_pct** — (price - VWAP) / VWAP × 100 at signal time
2. **intraday_return_pct** — return from open to signal time
3. **intraday_range_pct** — (high - low) / open × 100 so far today
4. **distance_from_intraday_high_pct** — how far below the day's high

### Volume-Based (3)
5. **relative_volume** — current volume / avg volume at this time of day
6. **volume_acceleration** — volume rate change over last 15 minutes
7. **buy_sell_imbalance** — (uptick vol - downtick vol) / total vol

### Microstructure (3)
8. **bid_ask_spread_bps** — current spread in basis points
9. **spread_vs_avg** — current spread / 20-day average spread
10. **quote_imbalance** — (bid_size - ask_size) / (bid_size + ask_size)

### Momentum Confirmation (2)
11. **momentum_5min** — 5-minute return at signal time
12. **momentum_alignment** — sign(intraday return) == sign(daily momentum) → 1/0

## Data Requirements

- 1-minute OHLCV bars for SPY (from Polygon via IronVault)
- Level 1 quotes (bid/ask/size) at signal time
- Historical intraday data for feature calibration

## Success Criteria

- WR improvement ≥ 1pp (89.6% → 90.6%+)
- No increase in max DD
- Feature importance: ≥ 2 intraday features in top 10
- OOS validation: improvement holds in walk-forward
