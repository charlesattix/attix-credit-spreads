# EXP-1030-max: Intraday Momentum Scalping on SPY 0-DTE Options

## Hypothesis

Short-duration momentum bursts (5-15 minute windows) in SPY create exploitable patterns in 0-DTE options pricing. After strong directional volume (order flow imbalance > 60%), SPY continues moving in the same direction for 5-10 more minutes ~58-62% of the time. Buying directional 0-DTE options at the start of these bursts and closing 5-15 minutes later captures gamma-amplified momentum.

This is fundamentally different from our credit spread strategies (which sell premium and bet on mean reversion) — it **buys** options and bets on momentum continuation, making it a genuinely uncorrelated alpha stream.

## Rationale

1. **Momentum persistence at micro timescales**: institutional order flow creates 5-15 minute momentum windows as large orders are worked through the book
2. **0-DTE gamma leverage**: a $0.50 SPY move generates a disproportionate P&L on 0-DTE options due to high gamma — small edge × high gamma = meaningful P&L
3. **Decorrelation**: momentum scalping (long gamma, directional) is the mirror image of credit spreads (short gamma, mean-reversion) — correlation should be strongly negative
4. **Capacity**: SPY 0-DTE volume exceeds $1B/day notional — no capacity constraint

## Strategy Design

### Entry Signal
- **Trigger**: 5-bar (5-minute) momentum exceeds 0.15% AND order flow imbalance > 60%
- **Confirmation**: VWAP slope aligns with momentum direction
- **Time window**: 9:45 AM - 3:00 PM ET only (avoid open/close volatility)

### Trade Structure
- Buy ATM 0-DTE call (for upward momentum) or put (for downward)
- **Not** a spread — directional option purchase for gamma capture
- Risk per trade: max $200 (premium paid)

### Exit Rules
- **Profit target**: 30% of premium (quick scalp)
- **Stop loss**: 50% of premium
- **Time stop**: 15 minutes max hold
- **Hard exit**: 3:15 PM regardless

### Signal Features (from compass/intraday_momentum.py)
1. **tick_momentum_5m**: 5-minute price change in bps
2. **tick_momentum_15m**: 15-minute momentum
3. **vwap_slope**: VWAP rate of change (trend direction)
4. **volume_surge**: current 5-min volume / 20-period avg
5. **order_flow_imbalance**: (uptick vol - downtick vol) / total
6. **price_acceleration**: second derivative of price (momentum increasing?)
7. **bid_ask_pressure**: bid size / ask size ratio (directional pressure)
8. **spread_tightening**: spread narrowing = conviction
9. **momentum_consistency**: % of last 5 bars in same direction
10. **vwap_distance_bps**: how far from VWAP (overextension risk)
11. **rsi_5min**: 5-bar RSI (overbought/oversold filter)
12. **tick_velocity**: rate of price change acceleration

## Data Requirements

- 1-minute OHLCV bars for SPY (Polygon)
- 0-DTE option chain snapshots (IronVault intraday)
- Level 1 quotes for spread/imbalance features

For initial simulation: use daily data to estimate frequency and win rate,
then validate with intraday data when available.

## Expected Outcome

- Win rate: 55-62% (momentum persistence)
- Avg win: +30% of premium ($60 on $200 risk)
- Avg loss: -50% of premium ($100 on $200 risk)
- Risk/reward: 0.6:1 per trade, but frequency compensates
- Trades: 3-8 per day on active days (~60-150/month)
- Sharpe: 1.5-3.0 (high frequency smooths returns)
- Correlation with EXP-880: strongly negative (-0.3 to -0.5)

## Success Criteria

- Win rate > 55%
- Sharpe > 1.5
- Max DD < 8%
- Correlation with credit spreads < 0.1 (ideally negative)
- ≥ 30 trades per month
