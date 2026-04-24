# EXP-2940: Overnight Return Premium Strategy

**Date:** 2026-04-21
**Type:** Full backtest + walk-forward validation
**Rule Zero:** Yahoo Finance real data only (SPY, QQQ, ^VIX daily OHLCV, 2010-01 to 2026-04). 4,099 trading days. Zero synthetic data.

---

## Verdict: CONDITIONAL KILL

| Kill Criterion | Threshold | Result | Status |
|---|---|---|---|
| Sharpe (pooled) | ≥ 1.0 | **0.959** (VIX-inverse, gross) | BREACH (marginal) |
| Max DD | ≤ 15% | **-23.2%** (VIX-inverse, gross) | **BREACH** |
| Correlation with exp1220 | < 0.5 | **0.577** (SPY close-to-close proxy) | **BREACH** |
| Slippage-adjusted Sharpe | ≥ 1.0 | **0.469** at 1 bps/leg; **-0.02** at 2 bps/leg | **FATAL** |

**The strategy is KILLED as a standalone alpha stream.** It breaches all three kill criteria and is destroyed by realistic slippage. However, the day-of-week substructure reveals a viable residual signal (see Section 8).

---

## 1. Hypothesis

Stock returns accrue primarily overnight (close-to-open) rather than intraday (open-to-close). A systematic strategy buying SPY+QQQ at market close and selling at next open captures this premium. Per Lou, Polk & Skouras (2019, JFE), the overnight premium is persistent across decades and robust to multiple anomaly types.

## 2. Data

| Source | Ticker | Period | Days | Rule Zero |
|---|---|---|---|---|
| Yahoo Finance | SPY | 2010-01-04 to 2026-04-21 | 4,099 | Real |
| Yahoo Finance | QQQ | 2010-01-04 to 2026-04-21 | 4,099 | Real |
| Yahoo Finance | ^VIX | 2010-01-04 to 2026-04-21 | 4,099 | Real |

## 3. Overnight vs. Intraday Decomposition

### Full period (16.3 years):

| Metric | SPY Overnight | SPY Intraday | QQQ Overnight | QQQ Intraday |
|---|---|---|---|---|
| Cumulative return | +179.6% | +122.2% | +484.5% | +137.5% |
| Annualized return | 6.5% | 5.0% | 11.4% | 5.5% |
| Sharpe ratio | 0.632 | 0.443 | **0.926** | 0.408 |
| Avg daily (bps) | 2.75 | 2.28 | 4.63 | 2.64 |
| Std daily (bps) | 69.1 | 81.7 | 79.3 | 102.7 |

**The overnight premium is real.** QQQ overnight Sharpe (0.926) is 2.3× intraday (0.408). SPY overnight Sharpe (0.632) is 1.4× intraday (0.443). QQQ overnight is the stronger signal.

### By era:

| Era | SPY ON Sharpe | SPY ID Sharpe | QQQ ON Sharpe | QQQ ID Sharpe |
|---|---|---|---|---|
| 2010-2014 | 0.687 | 0.542 | **1.215** | 0.385 |
| 2015-2019 | 0.792 | 0.314 | **1.068** | 0.336 |
| 2020-2022 | 0.295 | 0.214 | 0.327 | 0.255 |
| 2023-2026 | **1.027** | 0.774 | **1.343** | 0.746 |

**The premium collapsed in 2020-2022** (COVID + rate hikes) but recovered strongly in 2023-2026. This non-stationarity is a major risk factor.

## 4. Strategy Variants Tested

All use 50/50 SPY+QQQ overnight returns. VIX signals are causal (shift-1d: use previous day's VIX close).

| Variant | Sharpe | CAGR | Max DD | Win Rate |
|---|---|---|---|---|
| Basic (no filter) | 0.801 | 9.0% | **-28.4%** | 56.0% |
| VIX step (full<20, half 20-30, skip>30) | **0.921** | 6.6% | -20.5% | 52.6% |
| VIX binary (skip if VIX≥25) | 0.855 | 6.8% | -21.0% | 48.6% |
| **VIX inverse (size=min(1.5, 20/VIX))** | **0.959** | **9.8%** | -23.2% | 56.0% |
| VIX ladder (EXP-2820 production) | 0.890 | 8.3% | -23.9% | 56.0% |

**Best variant: VIX-inverse sizing** (Sharpe 0.959, CAGR 9.8%). Still below the 1.0 Sharpe threshold.

## 5. Walk-Forward Validation (252d train / 63d test)

**61 folds, VIX-inverse variant:**

| Metric | Value |
|---|---|
| Pooled Sharpe | 0.959 |
| Median fold Sharpe | 1.364 |
| Mean fold Sharpe | 1.254 |
| Worst fold Sharpe | **-2.656** (Apr–Jul 2022) |
| Best fold Sharpe | 5.109 (Oct 2017–Jan 2018) |
| % folds Sharpe ≥ 1.0 | 61% |
| % folds Sharpe > 0 | 72% |
| % folds negative | **28%** |

**28% of folds are negative Sharpe.** This is poor robustness. For comparison, v8a has 0% negative folds (20/20 positive in EXP-2280).

### Worst drawdown periods:
- **2022 H1:** Three consecutive negative folds (Jan-Oct 2022), Sharpe -2.08 to -2.66. Max fold DD -8.4%.
- **2020 Q1:** Fold 37 (Jan-Apr 2020): Sharpe -1.85, DD -16.6% (COVID crash).
- **2025 Q1:** Fold 57 (Jan-Apr 2025): Sharpe -2.49, DD -15.5%.

## 6. Slippage Sensitivity — THE FATAL FLAW

The overnight strategy requires two executions per day: buy at MOC (market-on-close) and sell at MOO (market-on-open). Each execution incurs slippage.

| Slippage/leg | Round-trip | Sharpe | CAGR | Max DD |
|---|---|---|---|---|
| **0 bps** (ideal) | 0 bps | 0.959 | 9.8% | -23.2% |
| **1 bps** | 2 bps | **0.469** | 4.4% | -27.9% |
| **2 bps** | 4 bps | **-0.022** | -0.8% | -32.9% |
| 3 bps | 6 bps | -0.513 | -5.6% | -65.4% |
| 5 bps | 10 bps | -1.494 | -14.7% | -92.6% |

**At just 1 bps/leg slippage (extremely optimistic for MOO orders), Sharpe drops from 0.959 to 0.469.** At 2 bps/leg (realistic for MOO execution at market open, where spreads are widest), the strategy has **negative expected returns.**

**Why this is fatal:**
- The average daily return is only 3.91 bps (VIX-inverse variant)
- A 2-bps slippage per leg (4 bps round trip) consumes 100% of the edge
- MOO (market-on-open) orders execute in the most volatile, widest-spread period of the trading day
- Even MOC orders on SPY (the most liquid ETF in the world) show 0.5-2 bps of implementation shortfall in practice
- At $100M+ AUM, market impact at the open would add another 1-3 bps

**The overnight premium is real but not tradeable at any meaningful scale.** This is consistent with the academic literature: Lou, Polk & Skouras (2019) document the premium but explicitly note it is "not necessarily profitable after transaction costs."

## 7. Correlation Analysis

| Proxy | ρ with overnight strategy | Assessment |
|---|---|---|
| SPY close-to-close (exp1220 proxy) | **0.577** | **EXCEEDS 0.5 threshold** |
| QQQ close-to-close | 0.564 | High |
| XLF | 0.498 | Moderate |
| XLI | 0.520 | Moderate-high |
| GLD | 0.004 | Uncorrelated |
| TLT | -0.203 | Negatively correlated |
| VIX change | -0.493 | Short vol exposure |

**The overnight strategy is correlated with SPY at ρ = 0.577.** This exceeds the 0.5 kill threshold. It's mechanically obvious: buying SPY at close and selling at open profits when SPY goes up overnight — the same directional exposure as put credit spreads. The strategy does NOT provide independent alpha; it provides leveraged directional equity exposure concentrated in the overnight window.

## 8. Day-of-Week Substructure

| Day | N | Avg bps | Sharpe | Win % |
|---|---|---|---|---|
| Mon | 766 | 0.83 | 0.148 | 54.7% |
| **Tue** | 844 | **7.09** | **1.595** | **55.8%** |
| **Wed** | 840 | **7.26** | **1.761** | **59.4%** |
| Thu | 825 | 2.32 | 0.513 | 56.0% |
| Fri | 823 | 0.59 | 0.137 | 54.1% |

**Tuesday and Wednesday overnight returns are 3-9× stronger than other days.** Monday and Friday overnights are essentially noise (Sharpe < 0.2). This is consistent with the academic literature on weekly seasonality (Ariel 1990, Birru 2018).

**Potential residual:** A "Tue+Wed only" overnight strategy would have ~100 execution days/year (vs. 252 for daily), reducing slippage impact by 60% while concentrating on the strongest signal days. However, this narrows the trade count and likely still fails the slippage test.

## 9. Year-by-Year Performance (VIX-inverse)

| Year | N | CAGR | Sharpe | Max DD |
|---|---|---|---|---|
| 2010 | 250 | 7.5% | 0.871 | -6.1% |
| 2011 | 252 | -0.8% | -0.029 | -11.5% |
| 2012 | 250 | 6.7% | 0.751 | -6.8% |
| 2013 | 252 | 19.3% | 1.966 | -3.9% |
| 2014 | 252 | 17.1% | 1.904 | -4.9% |
| 2015 | 252 | 2.4% | 0.270 | -10.2% |
| 2016 | 252 | -4.6% | -0.428 | -12.6% |
| 2017 | 251 | 24.6% | **3.302** | -3.2% |
| 2018 | 251 | 13.1% | 1.221 | -4.2% |
| 2019 | 252 | 18.4% | 1.603 | -12.3% |
| 2020 | 253 | 16.1% | 1.208 | -16.6% |
| 2021 | 252 | 12.7% | 1.483 | -4.1% |
| **2022** | **251** | **-17.1%** | **-1.526** | **-19.6%** |
| 2023 | 250 | 8.9% | 0.889 | -8.2% |
| 2024 | 252 | 32.6% | **2.491** | -5.7% |
| 2025 | 250 | 13.3% | 1.086 | -15.7% |
| 2026 | 75 | -1.9% | -0.124 | -7.3% |

**3 of 17 years are negative** (2011, 2016, 2022). 2022 was catastrophic: -17.1% CAGR, -19.6% DD. The strategy has genuine drawdown risk in bear markets and is NOT a hedge — it AMPLIFIES equity losses.

## 10. Decision: KILL (with residual notes)

### Kill reasons (3 of 3 criteria breached):

1. **Sharpe 0.959 < 1.0 threshold** — marginal breach, but honest. Even the best variant (VIX-inverse) doesn't clear the bar.

2. **Max DD -23.2% >> 15% threshold** — severe breach. The strategy drew down 23% in 2020 and 20% in 2022. With VIX ladder, this improves but stays above 15%.

3. **Correlation 0.577 > 0.5 threshold** — the overnight return IS equity beta. It provides the same directional exposure as our put credit spreads, just in a different time window. Zero diversification.

4. **Slippage is fatal** — the edge is 3.9 bps/day; execution costs are 2-4 bps round-trip. At 2 bps/leg the strategy has NEGATIVE expected returns. This alone kills it.

### What survives (for future reference):

- **Tue/Wed overnight Sharpe >1.5** — a concentrated 2-day/week strategy that trades only the strongest signal days. Could be explored as a micro-overlay if execution costs can be driven below 1 bps/leg (e.g., using SPY futures instead of ETFs, or pre-close limit orders).
- **QQQ overnight is stronger than SPY** (Sharpe 0.93 vs 0.63). If a single-name version is ever tested, use QQQ.
- **Negative correlation with TLT (-0.20)** — this means the overnight premium strategy and TLT credit spreads would diversify well. If both survived individually, combining them would help.

### Lesson for the portfolio:

The overnight premium is a well-documented academic anomaly that **does not survive transaction costs at institutional scale.** This is exactly the type of strategy that publishes well in journals (Sharpe 0.9 gross) but destroys capital in practice. The 0.5-0.7× backtest-to-live decay factor (EXP-2760) would be even harsher here — more like 0.0-0.3× after slippage — because the edge is so thin relative to execution costs.

**This validates our core approach of options-based strategies:** put credit spreads collect 20-40% of spread width in premium — a MUCH larger edge per trade (200-400 bps) compared to the overnight premium's 3.9 bps/day. Options strategies are inherently more execution-friendly because the edge is larger relative to bid-ask costs.

---

## Data Sources

- Yahoo Finance SPY daily OHLCV: 4,099 days (2010-01-04 to 2026-04-21)
- Yahoo Finance QQQ daily OHLCV: 4,099 days (2010-01-04 to 2026-04-21)
- Yahoo Finance ^VIX daily close: 4,099 days (2010-01-04 to 2026-04-21)
- Yahoo Finance GLD, XLF, XLI, TLT daily close: correlation proxies
- **Rule Zero: HELD. Zero synthetic data. All returns computed from real Yahoo Finance OHLCV.**

---

*EXP-2940 completed 2026-04-21 by Maximus*
*Verdict: KILL — slippage-fatal, correlated, excess drawdown*
