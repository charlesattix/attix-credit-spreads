# Strategy Research: How to Hit 100%+ Returns Every Year
## Charles's Analysis — Feb 27, 2026

### The Core Question
What options strategy can CONSISTENTLY deliver 100%+ annual returns with manageable drawdowns?

---

## What The Data Already Tells Us

From our 62 experiments:
- **exp_036 (credit spreads, no IC)**: +103% avg, but DD -49% in 2020
- **exp_059 IC-fixed**: +172% avg, but SUSPECT (2022 +511% skews everything)
- **Key pattern**: Credit spreads CAN hit 100%+ but drawdowns are unacceptable
- **The DD problem**: Compound growth + tail events = catastrophic drawdowns

### Why Credit Spreads Alone Won't Get Us There Consistently

Credit spreads harvest the **variance risk premium** (VRP) — implied vol > realized vol ~85% of the time. But:
1. **Negative skew**: You collect small premiums, occasionally take large losses
2. **Correlation spikes**: During crashes, ALL spreads lose simultaneously
3. **Compound amplification**: A 50% DD on compounded equity is devastating
4. **VRP compression**: Post-2018, the VRP has gotten smaller as more people sell premium

---

## Strategies Ranked by 100%+ Return Potential

### TIER 1: Highest Probability of 100%+ (with leverage/compounding)

#### 1. **VIX Regime-Switched Premium Selling** ⭐⭐⭐⭐⭐
- **Concept**: Sell aggressive premium when VIX is low/normal (< 20), go to CASH or buy protection when VIX > 25-30
- **Why it works**: Avoids the tail events that kill premium sellers. You're not in the market during COVID, Aug 2024 spike, etc.
- **Expected return**: 60-150%/yr with proper sizing + compound
- **Key insight**: Our COVID DD problem would be SOLVED by this overlay
- **CAN WE TEST THIS?** YES — add VIX regime filter to existing backtester
- **Priority**: 🔴 HIGHEST — this directly addresses our #1 problem

#### 2. **0DTE / Ultra-Short DTE Credit Spreads** ⭐⭐⭐⭐⭐
- **Concept**: Sell SPX 0DTE or 1-3 DTE spreads, collect rapid theta decay
- **Why it works**: Theta decay accelerates exponentially near expiry. You can trade DAILY, compounding faster
- **Expected return**: 100-300%/yr in backtests (many caveats)
- **Risk**: Gamma risk is extreme; single bad day can wipe weeks of gains
- **Key research**: CBOE studies show 0DTE sellers have positive expected value but with fat tails
- **CAN WE TEST THIS?** PARTIALLY — need intraday data, but can simulate with daily OHLC
- **Priority**: 🔴 HIGH — massive return potential, needs careful position sizing

#### 3. **Leveraged Put Writing (Cash-Secured → Margin-Enhanced)** ⭐⭐⭐⭐
- **Concept**: Sell OTM puts at 1.5-2x notional leverage (using portfolio margin)
- **Why it works**: CBOE PUT index (cash-secured put writing) has outperformed S&P with lower vol since 1986. Add leverage → 100%+
- **Historical data**: PUT index ~10%/yr unleveraged. At 2x leverage → 20%. With OTM + compounding → much higher
- **Key research**: Israelov & Nielsen (2015) "Covered Calls Uncovered" — put writing is the purest VRP harvest
- **CAN WE TEST THIS?** YES — modify risk sizing to allow leveraged notional
- **Priority**: 🟡 MEDIUM-HIGH

#### 4. **Dispersion Trading (Index vs Components)** ⭐⭐⭐⭐
- **Concept**: Sell index vol (SPX straddles), buy component vol (individual stocks). Profit from correlation premium.
- **Why it works**: Index implied vol > sum of component vols (correlation premium). Systematic edge.
- **Expected return**: 30-80% on allocated capital, but very consistent
- **Key research**: Driessen, Maenhout & Vilkov — "The Price of Correlation Risk"
- **CAN WE TEST THIS?** NO — needs individual stock options data we don't have
- **Priority**: 🟡 FUTURE — needs new data sources

### TIER 2: Solid 50-100%+ Potential

#### 5. **Strangles with Mechanical Management** ⭐⭐⭐⭐
- **Concept**: Sell 16-delta strangles on SPY, manage at 21 DTE or 50% profit
- **Why it works**: TastyTrade research shows 45 DTE strangles managed at 50% profit win ~83% of the time
- **Expected return**: 40-80% unleveraged, 100%+ with proper sizing
- **Key insight**: Strangles capture BOTH sides of the VRP (put AND call)
- **CAN WE TEST THIS?** YES — minor backtester modification (already have IC logic)
- **Priority**: 🟡 MEDIUM

#### 6. **Calendar/Diagonal Spreads in Low-IV** ⭐⭐⭐
- **Concept**: Buy back-month, sell front-month. Profit from theta differential + vega expansion
- **Why it works**: Different theta decay rates create structural edge
- **Expected return**: 30-60% (lower ceiling but much smoother equity curve)
- **CAN WE TEST THIS?** HARD — needs multi-expiry pricing data
- **Priority**: 🟢 LOW

#### 7. **Momentum + Options Overlay** ⭐⭐⭐⭐
- **Concept**: Use momentum signals (MA crossovers, RSI) to TIME when to sell premium
- **Why it works**: Selling puts in uptrends has much higher win rate than selling in downtrends
- **Our data already shows this**: MA200 filter improved every backtest config
- **Expected return**: 80-150% with proper timing
- **CAN WE TEST THIS?** YES — we already have MA200, just need more signals
- **Priority**: 🔴 HIGH — enhances existing strategy

### TIER 3: Alternative Approaches

#### 8. **Volatility Mean-Reversion (VIX Products)**
- Buy VIX puts or short VIX futures when VIX > 30 (mean reversion)
- Historically very profitable but requires futures account
- NOT TESTABLE with current setup

#### 9. **Earnings Premium Crush**
- Sell straddles before earnings, profit from IV crush
- Very high win rate (~70%) but needs individual stock data
- NOT TESTABLE with current setup

#### 10. **Wheel Strategy (Put Selling + Covered Calls)**
- Cash-secured puts → if assigned → covered calls → repeat
- Only works with enough capital for assignment
- NOT SUITABLE for our approach (we're spread-based)

---

## My Recommendation: The Hybrid Approach

**To consistently hit 100%+, we need MULTIPLE edges stacked together:**

### The "Attix Alpha Stack":

1. **VIX Regime Filter** (kill switch when VIX > 25)
   - Eliminates COVID, Aug 2024, etc.
   - Expected DD reduction: 50-70%

2. **0DTE + Short DTE Layer** (1-5 DTE spreads for rapid compounding)
   - Trade daily, not weekly
   - Smaller position sizes but higher frequency

3. **Momentum Timing** (enhanced beyond just MA200)
   - RSI divergence, MACD crossover, price action
   - Only sell premium in confirmed uptrend/neutral

4. **Dynamic Sizing by VIX Level**
   - VIX 12-18: Full size (10% risk)
   - VIX 18-22: Half size (5% risk)
   - VIX 22-25: Quarter size (2.5% risk)
   - VIX > 25: FLAT — no new trades

5. **Strategy Rotation**
   - Low IV: Iron condors / strangles (collect both sides)
   - Medium IV: Put credit spreads (bullish bias)
   - High IV: Cash or long calendar spreads (profit from vol crush)

---

## Immediate Experiments to Run (Priority Order)

### Phase A: VIX Regime Filter (HIGHEST PRIORITY)
- **A1**: exp_036 + VIX < 20 entry filter (close all if VIX > 25)
- **A2**: exp_036 + VIX < 25 entry filter (close all if VIX > 30)
- **A3**: exp_059 + VIX regime filter (same thresholds)
- **A4**: Dynamic sizing by VIX level (not just on/off)
- **Expected impact**: This ALONE might solve the DD problem

### Phase B: Short DTE Experiments
- **B1**: 5 DTE credit spreads (daily rebalance)
- **B2**: 7 DTE credit spreads (twice-weekly)
- **B3**: 0DTE simulation (end-of-day entry, next-day exit)

### Phase C: Enhanced Timing
- **C1**: MACD crossover + MA200 combined filter
- **C2**: RSI < 30 = pause selling puts (oversold = risk of continuation)
- **C3**: Put/Call ratio as sentiment overlay

### Phase D: Strangles & Naked Premium
- **D1**: 16-delta strangles, 45 DTE, manage at 50% profit
- **D2**: 20-delta strangles, 30 DTE, manage at 25% profit
- **D3**: Strangles + VIX regime filter

---

## Bottom Line

**The single biggest lever for 100%+ consistent returns is NOT the strategy — it's the REGIME FILTER.**

Our backtests already show 100%+ returns in most years. The problem is COVID-type events creating catastrophic DD. A VIX-based kill switch would:
- Keep returns high in normal years (no change)
- Dramatically reduce DD in crash years
- Allow us to compound more aggressively with confidence

**If I had to bet on ONE change that gets us to consistent 100%+ with <40% DD: it's the VIX regime filter.**
