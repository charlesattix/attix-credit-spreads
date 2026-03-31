# EXP-980-max: Margin & Broker Feasibility Analysis

## Executive Summary

**3.5x leverage is NOT achievable through traditional margin.** However, the credit spread structure provides a natural leverage mechanism: **the margin requirement for a credit spread is only the max loss (spread width - credit), not the notional.** This means we can achieve effective 2-3x capital deployment without margin borrowing, using position count and sizing alone.

**Recommended max realistic leverage: 2.0-2.5x** via position stacking, achievable at both Alpaca and IBKR.

---

## 1. Credit Spread Margin Mechanics

### How Credit Spread Margin Works

For a $5-wide bull put spread on SPY:
- Short strike: $440 put
- Long strike: $435 put
- Credit received: $1.00
- **Max loss = $5.00 - $1.00 = $4.00 per share = $400 per contract**
- **Margin requirement = max loss = $400 per contract**

This is fundamentally different from stock margin:
- Stock: 50% Reg-T margin → 2x leverage max
- Credit spread: margin = max loss = a fraction of notional

### Effective Leverage Calculation

With $100,000 capital and $400 max loss per contract:
- At 1x: 250 contracts × $400 = $100,000 at risk
- At 2x: 500 contracts × $400 = $200,000 at risk (uses full margin + credit received)
- At 3x: 750 contracts × $400 = $300,000 at risk (**requires portfolio margin**)

The "leverage" in our context isn't borrowing — it's **deploying more risk capital per dollar of equity** by running more concurrent positions.

---

## 2. Broker Margin Comparison

### Alpaca (Paper Trading Account — Current Broker)

| Feature | Alpaca |
|---|---|
| Account type | Paper trading (PA36XFVLG0WE, PA3Y2XDYB9I3) |
| Options margin | Reg-T for defined-risk spreads |
| Credit spread margin | **Max loss** (spread width - credit) × 100 |
| Max concurrent positions | No hard limit |
| Day trade requirement | $25K minimum for pattern day trader |
| Multi-leg support | Yes (combo orders) |
| Portfolio margin | **Not available** |

**Alpaca Reg-T math for our strategy:**
- $100K account
- $5 wide spreads, $1.00 credit → $400 max loss per contract
- Max contracts at 1x: 250
- Max contracts at 2x: ~500 (using credit received as additional margin)
- **Effective max: ~2x before margin call risk**

### Interactive Brokers (Future Migration Target)

| Feature | IBKR |
|---|---|
| Account type | Portfolio Margin (requires $110K+) |
| Options margin | **Portfolio margin** — stress-test-based |
| Credit spread margin | ~60-70% of max loss under PM |
| Margin offset | Cross-underlying offsets (SPY+QQQ hedging) |
| Multi-leg support | Yes (native combo) |
| Day trade | $25K for PDT, no limit with PM |

**IBKR Portfolio Margin math:**
- $100K account
- $5 wide spreads under PM: margin ≈ $240-280 per contract (60-70% of $400)
- Max contracts at 1x: ~400 (vs 250 at Reg-T)
- Max contracts at 2x: ~700
- Max contracts at 3x: ~1,050 (**theoretically possible**)
- **Effective max: ~3x with portfolio margin**

### Key Differences

| Parameter | Alpaca Reg-T | IBKR Portfolio Margin |
|---|---|---|
| Margin per spread | 100% of max loss | 60-70% of max loss |
| Effective leverage cap | ~2x | ~3x |
| Cross-hedge benefit | None | 10-30% reduction |
| Minimum account | $25K | $110K |
| Margin call risk at 2x | Low | Very low |
| Margin call risk at 3x | **Margin call** | Moderate |

---

## 3. Multi-Underlying Margin Offsets

### The Diversification Advantage

Running credit spreads on SPY + QQQ + IWM simultaneously:
- Reg-T: No offset — each spread requires full max-loss margin
- Portfolio margin: **10-30% margin reduction** when positions have negative correlation

Example with IBKR PM:
- 10 SPY put spreads: $4,000 margin
- 10 QQQ call spreads: $4,000 margin
- Combined (PM): ~$6,400 margin (20% offset because puts+calls partially hedge)

### Our Strategy's Offset Potential
- EXP-400: SPY credit spreads (bull puts + bear calls)
- Bull puts and bear calls on same underlying provide **partial delta offset**
- Cross-experiment: if EXP-400 is long delta and EXP-740 is short delta, PM recognises the hedge

**Estimated offset: 15-25% margin reduction with PM**

---

## 4. Worst-Case Margin Call Analysis

### Scenario: Portfolio at 3.5x Leverage

$100K account, 3.5x = $350K at risk, 875 contracts × $400 max loss

| Crisis | Portfolio Loss | Margin Required | Excess/(Deficit) |
|---|---|---|---|
| Normal day (-1%) | -$3,500 | $350,000 | ($253,500) deficit |
| **This is already impossible at Reg-T** | | | |

**3.5x is NOT feasible at either broker.** The margin requirement ($350K) exceeds the account ($100K) from day 1.

### Scenario: Portfolio at 2.0x Leverage

$100K account, 2.0x = $200K at risk, 500 contracts × $400

| Crisis | Portfolio Loss | Remaining Equity | Margin Req | Status |
|---|---|---|---|---|
| Normal (+0.1%/day) | +$200 | $100,200 | $200K | OK (credit covers) |
| -5% SPY move | -$10,000 | $90,000 | $200K | **WARNING** |
| COVID (-34%) | -$68,000 | $32,000 | $200K | **MARGIN CALL** |

At 2x leverage, a COVID-like event triggers a margin call. **This is why the crisis hedge (EXP-880) is mandatory.**

### Scenario: 2.0x Leverage WITH Crisis Hedge V2

| Crisis | Hedge Scale | Effective Lev | Loss | Remaining | Status |
|---|---|---|---|---|---|
| Normal | 1.0 | 2.0x | — | $100K | OK |
| VIX=25 | 1.0→0.7 | 1.4x | — | $100K | OK |
| VIX=35 | 0.4 | 0.8x | — | $100K | OK |
| COVID (-34%) | 0.2-0.4 | 0.4-0.8x | -$13K to -$27K | $73K-$87K | **SURVIVES** |

**With crisis hedge, 2x leverage survives all realistic scenarios.**

---

## 5. Realistic Leverage Recommendation

### Maximum Achievable by Broker

| Broker | Max Realistic Leverage | CAGR (from EXP-840) | Max DD |
|---|---|---|---|
| Alpaca Reg-T | **1.5x** (conservative) | ~40% | ~3% |
| Alpaca Reg-T | **2.0x** (with crisis hedge) | ~57% | ~4% |
| IBKR Portfolio Margin | **2.5x** (with crisis hedge) | ~75% | ~5% |
| IBKR PM + Offsets | **3.0x** (aggressive, crisis hedge) | ~96% | ~6% |

### Recommended Path

**Phase 1 (Current — Alpaca Paper Trading):**
- Leverage: 1.0x (paper trading, proving the strategy)
- CAGR target: 25%
- No margin risk

**Phase 2 (First Live — Alpaca):**
- Leverage: 1.5x (moderate position stacking)
- CAGR target: 40%
- Crisis hedge V2 mandatory
- Margin buffer: 30% excess margin at all times

**Phase 3 (Scale — IBKR Migration):**
- Leverage: 2.0-2.5x (portfolio margin)
- CAGR target: 55-75%
- Crisis hedge V2 + multi-underlying offsets
- Minimum account: $110K for PM eligibility

**Phase 4 (Aggressive — IBKR PM with Offsets):**
- Leverage: 2.5-3.0x (only if Phase 3 proves stable)
- CAGR target: 75-96%
- Full crisis hedge + cross-experiment hedging
- Only with 12+ months of live track record

---

## 6. The "Leverage" Reframe

The key insight: **we don't need traditional leverage to achieve 2x.** Credit spread margin = max loss. With $100K and $5 wide spreads:

- **Single position sizing**: 2% risk per trade = $2,000 = 5 contracts
- **Concurrent positions**: 10 active positions = 50 contracts = $20K at risk = **0.2x**
- **With 50 active positions**: 250 contracts = $100K at risk = **1.0x**
- **With 100 active positions**: 500 contracts = $200K at risk = **2.0x**

The "leverage" is achieved by running **more concurrent trades**, not by borrowing money. This is fundamentally safer because:
1. Each trade has defined max loss
2. Losses are spread across uncorrelated expiration dates
3. The crisis hedge reduces position count during stress
4. Credit received provides a partial offset to margin requirements

---

## 7. Conclusion

| Question | Answer |
|---|---|
| Is 3.5x leverage feasible? | **No** — exceeds margin at both brokers |
| Is 100% CAGR achievable? | **Only with IBKR PM at 3x** — risky |
| What's the realistic max? | **2.0-2.5x at Alpaca/IBKR** |
| What CAGR does 2.0x give? | **55-60%** (with crisis hedge: 77%) |
| Is 55-77% CAGR enough? | **Yes** — this is exceptional by any standard |
| What's the safest path? | **1.5x at Alpaca → 2.0x at IBKR** |

**Bottom line: Target 55-77% CAGR at 2.0x leverage with crisis hedge. This is achievable at both Alpaca (Reg-T) and IBKR (PM) with proper risk management. The 100% CAGR target requires 3x leverage which is only possible at IBKR PM and carries meaningful margin call risk even with hedging.**
