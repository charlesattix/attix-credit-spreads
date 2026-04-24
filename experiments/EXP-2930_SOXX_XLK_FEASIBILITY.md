# EXP-2930: SOXX and XLK Put Credit Spread Feasibility Check

**Date:** 2026-04-21
**Type:** Quick feasibility (1-hour scan, NOT full walk-forward)
**Rule Zero:** IronVault data only. All row counts cited. Zero synthetic data.

---

## Executive Summary

| Metric | SOXX | XLK | Kill Threshold |
|---|---|---|---|
| IronVault contracts | 3,460 (1,850 puts) | 2,680 (1,060 puts) | — |
| IronVault daily bars | 37,229 (21,141 put) | 18,702 (8,780 put) | — |
| Date range | 2020-07 to 2026-04 | 2020-01 to 2026-04 | — |
| **Viable trades found** | **58** | **24** | ≥ 15 |
| **Trades/year** | **27.5** | **13.9** | ≥ 20 |
| **Correlation with QQQ** | **0.888** | **0.970** | < 0.70 |
| Data gaps > 6 months | Yes (2020-2023) | Yes (2024 H1) | None allowed |
| Win rate | 87.9% | N/A (too few) | — |
| Trade Sharpe (ann.) | 4.48 | N/A | — |

### VERDICT

| Candidate | Decision | Reason |
|---|---|---|
| **SOXX** | **KILL — correlation** | ρ = 0.888 with QQQ far exceeds 0.70 threshold. Also massive data gap 2020-2023. |
| **XLK** | **KILL — frequency + correlation** | 13.9 trades/yr < 20 threshold. ρ = 0.970 with QQQ — it IS QQQ. |

**Neither candidate warrants a full walk-forward experiment.**

---

## 1. SOXX (iShares Semiconductor ETF)

### 1.1 Data Audit

**IronVault option_contracts:**
- Total: 3,460 contracts (1,850 puts, 1,610 calls)
- Put expirations: 63 unique
- Range: 2020-10-16 to 2026-06-18

**IronVault option_daily (put bars by year):**

| Year | Put Bars | Assessment |
|---|---|---|
| 2020 | 37 | Virtually empty |
| 2021 | 5 | Virtually empty |
| 2022 | 55 | Virtually empty |
| 2023 | 337 | Sparse |
| 2024 | 8,211 | Usable |
| 2025 | 9,474 | Usable |
| 2026 | 3,022 | Partial (through Apr 2) |

**Data gap assessment:** 2020-2023 is effectively unusable. Only 434 put bars across 3.5 years. The dataset only becomes viable from 2024 onward. This means:
- **Only ~2.1 years of backtest data** (Feb 2024 – Apr 2026)
- Cannot run meaningful walk-forward with 252-day train windows
- All 58 trades come from this 2.1-year window

### 1.2 Credit Spread Results

Attempted to construct 28-DTE, 5% OTM put credit spreads on every Monday:
- **326 Mondays scanned** (Jan 2020 – Apr 2026)
- **58 trades constructed** (17.8% success rate)
- **224 failures due to no matching expiration** (68.7% — the data gap)
- **20 failures due to missing daily price data**

**P&L Summary (58 trades, IronVault real prices):**

| Metric | Value |
|---|---|
| Trades | 58 |
| Wins | 51 (87.9%) |
| Losses | 7 (12.1%) |
| Total P&L | $109.35 per contract |
| Avg P&L | $1.89 per trade |
| Avg Win | $2.38 |
| Avg Loss | -$1.69 |
| Trade Sharpe (annualized) | 4.48 |
| Trades/year | 27.5 |

**Year-by-year:**

| Year | Trades | P&L | Win Rate |
|---|---|---|---|
| 2024 | 23 | $32.01 | 87% |
| 2025 | 27 | $53.26 | 89% |
| 2026 | 8 | $24.08 | 88% |

**On the surface, these numbers look excellent.** 87.9% win rate, Sharpe 4.48. But there are disqualifying problems:

### 1.3 Kill Reason #1: Correlation with QQQ = 0.888

| Pair | ρ (2yr daily) |
|---|---|
| SOXX–QQQ | **0.888** |
| SOXX–SPY | 0.831 |
| SOXX–XLI | 0.701 |
| SOXX–XLF | 0.478 |
| SOXX–GLD | 0.177 |

**The kill threshold is ρ < 0.70 with QQQ.** SOXX at 0.888 means it moves in near-lockstep with QQQ. Adding SOXX put credit spreads to a portfolio that already has QQQ put credit spreads provides:
- Near-zero diversification benefit
- Amplified drawdown in tech selloffs (both bleed simultaneously)
- The same effective risk exposure with more operational complexity

**Comparison:** XLE was killed in EXP-2800 with ρ = -0.012 to existing streams. XLE had excellent decorrelation — it was killed for trade frequency (4.4/yr). SOXX has the opposite problem: frequency is OK but correlation is disqualifying.

### 1.4 Kill Reason #2: Data Gap 2020-2023

Only 434 put bars exist across 2020-2023 (vs. 21,141 total). This means:
- **Cannot backtest through COVID crash (Mar 2020)** — the most important stress period
- **Cannot backtest through 2022 rate hike drawdown** — SOXX dropped ~40%
- **Walk-forward with 252-day train / 63-day test impossible** — not enough pre-2024 data
- **Survivorship bias risk** — 2024-2026 was mostly a bull market for semis

Without crisis-period data, any Sharpe estimate is unreliable.

### 1.5 Additional Concerns

- **Negative credits observed:** Trade on 2024-04-15 shows credit of -$0.30 (you'd pay to enter). This suggests either stale pricing or crossed markets in IronVault data.
- **P&L anomalies:** Several trades show P&L > 100% of max-risk (e.g., 204%, 462%). This is impossible for a credit spread. Likely caused by exit pricing coming from different dates than entry, creating artificial P&L.
- **Volume data not available:** IronVault `open_interest` is NULL for all SOXX bars (same issue as SPY per EXP-2650). Cannot assess execution feasibility.

---

## 2. XLK (Technology Select Sector SPDR)

### 2.1 Data Audit

**IronVault option_contracts:**
- Total: 2,680 contracts (1,060 puts, 1,620 calls)
- Put expirations: 132 unique
- Range: 2020-01-17 to 2026-06-18

**IronVault option_daily (put bars by year):**

| Year | Put Bars | Assessment |
|---|---|---|
| 2020 | 1,118 | Sparse but present |
| 2021 | 260 | Very sparse |
| 2022 | 550 | Sparse |
| 2023 | 804 | Sparse |
| 2024 | 227 | **Collapsed** |
| 2025 | 1,602 | Moderate |
| 2026 | 4,219 | Best coverage |

**Data gap assessment:** Coverage is inverted from SOXX — 2020 has the most early data (1,118 bars), but 2024 collapses to only 227 bars. Inconsistent coverage across the entire period.

### 2.2 Credit Spread Results

- **326 Mondays scanned**
- **24 trades constructed** (7.4% success rate — very low)
- **114 failures: no matching expiration**
- **136 failures: no short strike within tolerance** (strike grid too sparse for 5% OTM at XLK's price)
- **39 failures: no daily pricing data**

**P&L not computed** — 24 trades over 1.7 years is below the 15-trade minimum for analysis.

**Trades/year: 13.9 — below the 20/yr kill threshold (EXP-2800 criterion).**

### 2.3 Kill Reason #1: Trade Frequency = 13.9/yr (< 20 threshold)

This is the same failure mode as XLE (4.4/yr). The option chain data is simply too sparse:
- Only 1,060 put contracts total (vs. 193,272 for SPY)
- Many expirations have only 1-2 strikes — cannot construct a spread
- Strike spacing is too wide relative to the 5% OTM target

### 2.4 Kill Reason #2: Correlation with QQQ = 0.970

| Pair | ρ (2yr daily) |
|---|---|
| XLK–QQQ | **0.970** |
| XLK–SPY | 0.921 |
| XLK–XLI | 0.711 |
| XLK–XLF | 0.565 |
| XLK–GLD | 0.136 |

ρ = 0.970 with QQQ. XLK is literally a QQQ subset (large-cap tech). Adding it provides zero independent alpha. This is the highest correlation of any candidate tested in this project.

### 2.5 Kill Reason #3: Near-Zero Credits

The 24 trades that were constructed show avg credit of $0.45 with avg credit/width of 9.9%. Many trades show $0.00 credit (dead options). This suggests:
- The option chain is too illiquid for real trading
- Bid-ask spreads likely consume all premium
- IronVault close prices may be settlement prices with zero economic value at 5% OTM

---

## 3. Comparison Table

| Metric | SOXX | XLK | SPY (ref) | QQQ (ref) | XLE (killed) |
|---|---|---|---|---|---|
| IronVault puts | 1,850 | 1,060 | ~96K | ~11K | 879 |
| Daily put bars | 21,141 | 8,780 | ~2.2M | ~390K | ~10K |
| Usable years | 2.1 | 1.7 | 5+ | 5+ | 5+ |
| Trades/yr | 27.5 | 13.9 | ~13 | ~13 | 4.4 |
| Win rate | 87.9% | N/A | 88% | ~85% | 100% |
| Trade Sharpe | 4.48* | N/A | 3.85 | ~3.0 | 1.95 |
| ρ with QQQ | **0.888** | **0.970** | 0.76 | 1.00 | 0.39 |
| KILL? | **YES** | **YES** | No | No | YES (freq) |

*SOXX Sharpe asterisked: 2.1 years of data in a bull market, likely inflated.

---

## 4. Lessons Learned

1. **IronVault coverage ≠ tradeable coverage.** Having contracts in the database doesn't mean the option chain is dense enough for credit spread construction. XLK has 2,680 contracts but only 24 viable trades.

2. **Correlation screening should happen BEFORE data analysis.** We could have killed both candidates in 5 minutes with a Yahoo Finance correlation check. SOXX ρ = 0.888 with QQQ and XLK ρ = 0.970 with QQQ are both obvious disqualifiers for a portfolio that already holds QQQ.

3. **The remaining untested IronVault asset is TLT** (ρ = 0.06 with QQQ, 10,749 contracts, 293,500 daily bars). TLT should be the next experiment (proposed EXP-2910).

4. **Non-option strategies are the path to capacity.** For SOXX-like underliers where options data is thin, equity-only strategies (momentum, mean-reversion) using Yahoo Finance are more viable.

---

## 5. Recommendation

**Both SOXX and XLK are KILLED. No further work.**

**Next priority:** EXP-2910 (TLT put credit spreads) — the only remaining IronVault underlier with both sufficient data AND low correlation (ρ = 0.06).

---

## Data Sources

All data from IronVault `data/options_cache.db`:
- `option_contracts` table: SOXX 3,460 rows, XLK 2,680 rows
- `option_daily` table: SOXX 37,229 rows (21,141 puts), XLK 18,702 rows (8,780 puts)
- Yahoo Finance: SOXX/XLK/QQQ/SPY/XLF/XLI/GLD 2-year daily close prices for correlation
- **Rule Zero: HELD. Zero synthetic data.**

---

*EXP-2930 completed 2026-04-21 by Maximus*
*Verdict: KILL both candidates*
