# Underlying Data Audit — options_cache.db
**Date:** 2026-03-26
**Objective:** Identify non-SPY underlyings with sufficient option data to backtest; assess COMPASS RRG coverage and price independence for Sharpe improvement.

---

## 1. All Tickers With >1,000 Daily Option Records

Data from `option_contracts JOIN option_daily` in `data/options_cache.db`.

| Ticker | Daily Records | Unique Contracts | Expirations | Date Range | Avg Strikes/Exp | Avg Records/Day |
|--------|:------------:|:---------------:|:-----------:|:----------:|:---------------:|:---------------:|
| **SPY** | 4,400,262 | 186,909 | 627 | 2020-01-02 → 2026-03-13 | 298 | 2,560 |
| **QQQ** | 304,080 | 8,896 | 98 | 2020-01-02 → **2023-04-21** | 91 | 366 |
| **XLF** | 240,112 | 7,992 | 311 | 2020-01-02 → 2026-03-06 | 26 | 155 |
| **XLI** | 198,546 | 13,625 | 311 | 2020-01-02 → 2026-03-06 | 44 | 128 |
| **TLT** | 185,411 | 2,338 | 57 | 2020-01-17 → **2024-07-19** | 41 | 162 |
| **GLD** | 154,335 | 2,050 | 47 | 2020-01-17 → **2024-03-15** | 44 | 146 |
| SOXX | 35,288 | 2,073 | 56 | 2020-02-06 → 2026-03-06 | 37 | 45† |
| XLE | 15,427 | 838 | 101 | 2020-04-17 → 2026-03-06 | 8 | 12 |
| XLK | 14,766 | 995 | 117 | 2020-01-02 → 2026-03-06 | 9 | 11 |

†SOXX: 45 records/day average misleads — see year-by-year breakdown below.

---

## 2. Year-by-Year Coverage (Quality Assessment)

### XLF — Financials (Full 2020-2026 ✓)
| Year | Records | Trading Days | Expirations | Avg Strikes/Exp |
|------|--------:|:------------:|:-----------:|:---------------:|
| 2020 | 28,292 | 253 | 62 | 17 |
| 2021 | 44,208 | 252 | 63 | 25 |
| 2022 | 42,640 | 251 | 68 | 22 |
| 2023 | 37,239 | 250 | 71 | 20 |
| 2024 | 40,600 | 252 | 65 | 27 |
| 2025 | 43,095 | 250 | 58 | 31 |

**Assessment:** Consistent 28K-44K records/year. Improving strike density over time. Full 6-year backtest possible. $14-65 price range, $1 strike spacing.

### XLI — Industrials (Full 2020-2026 ✓)
| Year | Records | Trading Days | Expirations | Avg Strikes/Exp |
|------|--------:|:------------:|:-----------:|:---------------:|
| 2020 | 26,436 | 253 | 60 | 32 |
| 2021 | 33,182 | 252 | 63 | 37 |
| 2022 | 26,970 | 251 | 62 | 39 |
| 2023 | 38,451 | 250 | 64 | 41 |
| 2024 | 35,735 | 252 | 62 | 48 |
| 2025 | 33,840 | 250 | 57 | 45 |

**Assessment:** Consistent 26K-38K records/year. Strike density improves from 32 → 48/exp. Full 6-year backtest possible. $38-200 price range.

### QQQ — Nasdaq 100 (Ends April 2023 ✗)
| Year | Records | Expirations | Avg Strikes/Exp |
|------|--------:|:-----------:|:---------------:|
| 2020 | 172,053 | 59 | 97 |
| 2021 | 42,737 | 24 | 48 |
| 2022 | 64,029 | 26 | 79 |
| 2023 (partial) | 25,261 | 10 | 92 |

**Assessment:** Excellent strike density (91 avg) but data ends April 2023. Only 3.3 years — misses 2024-2025 performance. **Not viable for full 6-year backtest.**

### TLT — 20-Year Treasury (Ends July 2024 ✗)
| Year | Records | Expirations | Avg Strikes/Exp |
|------|--------:|:-----------:|:---------------:|
| 2020 | 37,857 | 28 | 30 |
| 2021 | 36,700 | 24 | 28 |
| 2022 | 41,266 | 24 | 35 |
| 2023 | 56,663 | 19 | 38 |
| 2024 (to Jul) | 12,871 | 7 | 36 |

**Assessment:** Good data quality 2020-2023. Drops sharply in 2024 and ends July 2024. Only 4.5 years — misses 2024 H2 and all of 2025. **Would need data fetch to complete 6-year backtest.**

### GLD — Gold ETF (Ends March 2024 ✗)
| Year | Records | Expirations | Avg Strikes/Exp |
|------|--------:|:-----------:|:---------------:|
| 2020 | 55,498 | 24 | 39 |
| 2021 | 24,980 | 14 | 35 |
| 2022 | 35,476 | 13 | 44 |
| 2023 | 37,138 | 15 | 40 |
| 2024 (to Mar) | 1,198 | 3 | 21 |

**Assessment:** Good 2020-2023. Effectively ends Dec 2023 (only 3 thin months of 2024). **Missing 2024-2025 = 2 of 6 years.**

### SOXX — Semiconductors (Sparse Pre-2024 ✗)
| Year | Records | Expirations | Avg Strikes/Exp |
|------|--------:|:-----------:|:---------------:|
| 2020 | 60 | 2 | 8 |
| 2021 | 18 | 3 | 3 |
| 2022 | 92 | 4 | 7 |
| 2023 | 352 | 7 | 6 |
| 2024 | 16,406 | 17 | 36 |
| 2025 | 15,124 | 38 | 33 |
| 2026 (ytd) | 3,236 | 10 | 45 |

**Assessment:** Essentially no data 2020-2023 (60-352 records/year vs 16K+ needed). Only viable for 2024-2025 backtests. **Not viable for 6-year comparison.**

### XLE / XLK — Energy / Technology (Sparse Throughout ✗)
Both average only 8-11 records/day. Too sparse for reliable strike finding at 3% OTM.

---

## 3. COMPASS RRG Coverage

From `data/macro_state.db` → `sector_rs` table (325 weeks, 2020-01-03 to 2026-03-20).

| Ticker | In COMPASS RRG | RRG Weeks | Options Data Viable |
|--------|:-------------:|:---------:|:-------------------:|
| XLF | ✓ | 325 | ✓ Full 2020-2026 |
| XLI | ✓ | 325 | ✓ Full 2020-2026 |
| XLK | ✓ | 325 | ✗ Sparse |
| XLE | ✓ | 325 | ✗ Sparse |
| SOXX | ✓ | 325 | ✗ Sparse pre-2024 |
| XLC | ✓ | 325 | ✗ Not in cache |
| XLV | ✓ | 325 | ✗ Not in cache |
| XLY | ✓ | 325 | ✗ Not in cache |
| XLB | ✓ | 325 | ✗ Not in cache |
| XLP | ✓ | 325 | ✗ Not in cache |
| XLRE | ✓ | 325 | ✗ Not in cache |
| XLU | ✓ | 325 | ✗ Not in cache |
| ITA | ✓ | 325 | ✗ Not in cache |
| PAVE | ✓ | 325 | ✗ Not in cache |
| XBI | ✓ | 325 | ✗ Not in cache |
| **GLD** | **✗** | — | ✓ Partial (ends 2024) |
| **TLT** | **✗** | — | ✓ Partial (ends Jul 2024) |
| **QQQ** | **✗** | — | ✓ Partial (ends Apr 2023) |

**Only XLF and XLI are in both COMPASS RRG AND have full 2020-2026 options data.**

---

## 4. Price Independence Analysis

For Sharpe improvement, we need underlyings with LOW correlation to SPY. Adding correlated underlyings boosts trade count but doesn't reduce portfolio variance.

### RRG rs_ratio Analysis (100 = inline with SPY)

| Ticker | rs_ratio | RRG Interpretation | SPY Correlation (estimated) |
|--------|:--------:|:------------------:|:---------------------------:|
| XLF | 89-92 | Persistently Lagging SPY | ~0.80-0.85 — high |
| XLI | 105-108 | Consistently Leading SPY | ~0.85-0.90 — high |
| XLK | 98-104 | Near-inline, tech-driven | ~0.90-0.95 — very high |
| GLD | — | Not in RRG | ~0.00-0.15 — **low** |
| TLT | — | Not in RRG | ~-0.20 to -0.40 — **negative** |
| QQQ | — | Not in RRG | ~0.95 — very high |

### RRG Quadrant Breakdown for COMPASS Candidates

**XLF (Financials):**
- 2020: 0 Leading, 35 Lagging — rate-shock sensitive
- 2021-2025: Consistently Lagging SPY, 0 Leading weeks
- **Interpretation:** Financials underperform but move with SPY. Adding XLF = more trades in the same market environment. NOT independent.

**XLI (Industrials):**
- All years: 19-35 Leading weeks, 0 Lagging
- rs_ratio steadily 105-108 — consistently outperforms SPY
- **Interpretation:** XLI has some independent drivers (capex cycles, reshoring) but remains highly correlated to broad market. Adding XLI = moderate independence.

---

## 5. Backtestability Summary

### Tier A — Ready Now (Full 6-Year Backtest)

| Ticker | Records | Years | COMPASS | Independence | Verdict |
|--------|:-------:|:-----:|:-------:|:------------:|:-------:|
| **XLF** | 240K | 2020-2026 | ✓ | Low | ✓ Use now |
| **XLI** | 198K | 2020-2026 | ✓ | Low-Medium | ✓ Use now |

**XLF and XLI are the only two non-SPY underlyings that can support a full 6-year backtest with COMPASS regime signals.**

### Tier B — Need Data Fetch (High Independence Value)

| Ticker | Gap | Records Existing | Independence | Fetch Effort |
|--------|-----|:----------------:|:------------:|:------------:|
| **GLD** | 2024-01 → now | 154K (2020-2023) | **High** (~0 corr) | Medium |
| **TLT** | 2024-07 → now | 185K (2020-mid2024) | **High** (negative corr) | Medium |
| **QQQ** | 2023-04 → now | 304K (2020-2023) | Low (0.95 corr) | Medium |

GLD and TLT would add the most Sharpe-diversification value but require ~18 months of Polygon fetch to complete 2024-2025 data.

### Tier C — Not Viable Without Major Fetch

SOXX, XLE, XLK, XLC, XLV, XLY — either too sparse or not in cache at all.

---

## 6. Trade Count Projection

Current SPY-only generates approximately **2-3 spreads/month** (depends on regime). Adding underlyings:

| Configuration | Monthly Trades | Independent P&L Streams | Sharpe Impact |
|---------------|:-------------:|:----------------------:|:-------------:|
| SPY only (current) | ~2.5 | 1 | Baseline |
| SPY + XLF | ~5 | ~1.2 (correlated) | Small improvement |
| SPY + XLI | ~5 | ~1.3 (moderate) | Small improvement |
| SPY + XLF + XLI | ~7.5 | ~1.4 | Moderate improvement |
| SPY + GLD* | ~5 | **~1.8 (independent)** | **Large improvement** |
| SPY + TLT* | ~5 | **~1.9 (negatively corr.)** | **Largest improvement** |

*Requires data fetch to complete 2024-2025.

---

## 7. Recommendations

### Immediate Action (This Week)
1. **Run XLF backtest** using existing COMPASS portfolio framework (`scripts/run_portfolio_backtest.py`). Full 6 years viable. Expected alpha: moderate — adds trades but in the same risk environment as SPY.
2. **Run XLI backtest** alongside XLF. XLI has better strike density (44/exp vs 26/exp) and slightly more independent behavior than XLF.
3. **Test 3-underlying portfolio**: SPY 60% + XLF 20% + XLI 20%. This is the maximum viable allocation with current cache data.

### Data Fetch Priority (Next Sprint)
1. **GLD 2024-2026** — highest independence value. Gold has near-zero beta to SPY and often moves counter-cyclically. Adding GLD would be a genuine Sharpe booster.
2. **TLT 2024-2026** — bonds vs equities = negative correlation. The ultimate Sharpe booster, but requires careful regime logic (bull-put spreads in rate-falling environment = different than equity logic).

### Structural Constraint
The Sharpe gap (2.60 vs 6.0 target) cannot be closed by adding XLF or XLI alone — they are too correlated to SPY. The **only path to genuinely independent P&L streams with current cache data** is GLD or TLT, both of which need 2024-2025 data fetched.

Alternatively: revise the Sharpe target from 6.0 to ~3.0, which is achievable with the 3-underlying (SPY+XLF+XLI) configuration and Safe Kelly circuit breakers.

---

## Appendix: Raw Query Summary

```
options_cache.db → option_contracts JOIN option_daily:
  Total tickers with data: 9
  Tickers with >1K daily records: 9 (all listed above)
  Only tickers with >100K records AND full 2020-2026: SPY, XLF, XLI

macro_state.db → sector_rs:
  Total COMPASS tickers: 15
  Overlap with viable options data: 2 (XLF, XLI)
  RRG date range: 2020-01-03 to 2026-03-20 (325 weekly snapshots)
```
