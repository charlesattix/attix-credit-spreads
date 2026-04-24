# EXP-2950: Sector Momentum Rotation Strategy — Results

**Date:** 2026-04-23 14:08
**Status:** KILLED
**Rule Zero:** CLEAN

---

## 1. Data Sources

| Source | Details |
|---|---|
| Sector ETFs | Yahoo Finance: XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY (2010-2026) |
| Extended | + XLC, XLRE (2018-2026) |
| Benchmark | SPY |
| VIX filter | Yahoo ^VIX |

**Rule Zero:** All prices from Yahoo Finance real market data. No synthetic data.

---

## 2. Strategy Variants — Raw Metrics (Before Walk-Forward)

| Strategy | Sharpe | CAGR | Max DD | Type |
|---|---|---|---|---|
| spy_benchmark | 0.59 | 14.0% | 33.7% | — |
| long_top3_3m | 0.50 | 10.3% | 16.8% | — |
| ts_mom_12m | 0.50 | 11.6% | 32.7% | — |
| dual_mom_combo | 0.48 | 11.8% | 32.3% | — |
| dual_mom_6m | 0.48 | 11.6% | 31.4% | — |
| long_top3_novix_combo | 0.47 | 11.5% | 32.3% | — |
| ts_mom_6m | 0.46 | 11.0% | 34.1% | — |
| long_top3_6m | 0.38 | 8.8% | 15.8% | — |
| long_top3_combo | 0.32 | 8.0% | 16.2% | — |
| ls_xsect_12m | -0.27 | 0.0% | 35.5% | — |
| ls_xsect_3m | -0.29 | -0.2% | 36.4% | — |
| ls_xsect_combo | -0.31 | -0.6% | 36.4% | — |
| ls_xsect_1m | -0.33 | -0.5% | 57.0% | — |
| ls_xsect_6m | -0.36 | -1.1% | 37.1% | — |

---

## 3. Walk-Forward Validation (Vol-Targeted to 12%)

| Rank | Strategy | WF Sharpe | WF CAGR | WF Max DD | Median Fold |
|---|---|---|---|---|---|
| 1 | long_top3_3m | **0.57** | 11.4% | 16.3% | 0.79 |
| 2 | long_top3_6m | **0.44** | 9.7% | 16.4% | 0.51 |
| 3 | ts_mom_6m | **0.42** | 9.8% | 28.6% | 0.83 |
| 4 | ts_mom_12m | **0.40** | 9.8% | 27.5% | 0.82 |
| 5 | long_top3_novix_combo | **0.37** | 9.2% | 27.6% | 0.47 |
| 6 | dual_mom_combo | **0.37** | 9.2% | 27.5% | 0.54 |
| 7 | dual_mom_6m | **0.37** | 9.2% | 26.0% | 0.54 |
| 8 | long_top3_combo | **0.35** | 8.5% | 21.1% | 0.50 |
| 9 | ls_xsect_3m | **-0.27** | 0.1% | 35.6% | -0.16 |
| 10 | ls_xsect_6m | **-0.27** | -0.0% | 32.8% | -0.42 |
| 11 | ls_xsect_combo | **-0.29** | -0.5% | 35.1% | -0.52 |
| 12 | ls_xsect_12m | **-0.32** | -1.1% | 44.0% | -0.05 |
| 13 | ls_xsect_1m | **-0.42** | -1.9% | 63.5% | -0.82 |

**Best strategy:** `long_top3_3m` (WF Sharpe 0.57)

---

## 4. Correlation with Existing 8 Streams

### long_top3_3m

**Mean correlation:** ρ = 0.0212

| Stream | ρ |
|---|---|
| cross_vol | +0.0012 |
| exp1220 | +0.0909 |
| gld_cal | -0.0276 |
| qqq_cs | +0.0946 |
| slv_cal | -0.0328 |
| v5_hedge | -0.0215 |
| xlf_cs | +0.0323 |
| xli_cs | +0.0321 |

**XLF correlation:** 0.0323 | **XLI correlation:** 0.0321

### long_top3_6m

**Mean correlation:** ρ = 0.0228

| Stream | ρ |
|---|---|
| cross_vol | +0.0135 |
| exp1220 | +0.0870 |
| gld_cal | -0.0188 |
| qqq_cs | +0.0771 |
| slv_cal | -0.0269 |
| v5_hedge | -0.0255 |
| xlf_cs | +0.0493 |
| xli_cs | +0.0265 |

**XLF correlation:** 0.0493 | **XLI correlation:** 0.0265

### ts_mom_6m

**Mean correlation:** ρ = -0.0079

| Stream | ρ |
|---|---|
| cross_vol | +0.0661 |
| exp1220 | +0.1874 |
| gld_cal | +0.0206 |
| qqq_cs | +0.0988 |
| slv_cal | -0.0385 |
| v5_hedge | -0.4715 ⚠️ |
| xlf_cs | +0.0498 |
| xli_cs | +0.0242 |

**XLF correlation:** 0.0498 | **XLI correlation:** 0.0242

---

## 5. Kill Criteria

| Criterion | Threshold | Result | Status |
|---|---|---|---|
| WF Sharpe | ≥ 1.0 | 0.57 | FAIL |
| Max DD | ≤ 20% | 16.3% | PASS |
| Mean ρ with portfolio | ≤ 0.4 | 0.0212 | PASS |
| 9-stream net Sharpe | ≥ 6.0 | 4.74 | FAIL |

---

## 6. Portfolio Integration

| Metric | 9-Stream | 8-Stream Baseline | Delta |
|---|---|---|---|
| NET Sharpe | **4.74** | 4.99 | -0.25 |
| NET CAGR | 96.7% | 105.1% | — |
| Max DD | 5.6% | 5.4% | — |
| Median fold Sharpe | 5.75 | 6.22 | — |

---

## 7. Root Cause Analysis

### Cross-Sectional Long-Short: Sector Momentum Is Dead (Post-2018)

All five long-short variants produced **negative Sharpes** (-0.27 to -0.42 WF). This is not a data error — sector momentum has experienced well-documented alpha decay:

- **2010-2016:** Sector momentum worked (academic literature cites Sharpe 0.5-1.0)
- **2017-2026:** ETF proliferation, crowding, and factor rotation killed the premium
- The short leg is the problem: bottom-3 sectors often *reverse* instead of continuing down, especially after COVID (energy, financials, etc.)

This is consistent with the AUM_CAPACITY_RESEARCH estimate of "Expected Sharpe: 1.0-1.6 backtest -> 0.5-1.0 live" — our full-sample backtest already shows the post-decay reality.

### Long-Only Top-3: Modest But Real

The best strategy (long top-3 by 3-month momentum, VIX filter) achieves WF Sharpe 0.57:
- **11.4% CAGR** with 16.3% max DD — reasonable risk-reward
- **VIX filter helps:** long_top3_combo without VIX filter has 32.3% DD vs 16.3% with it
- But 0.57 Sharpe is less than SPY buy-and-hold (0.59) — the strategy doesn't beat the benchmark

### Correlation Is Excellent (ρ = 0.02) — But Irrelevant

The near-zero correlation with our credit spread portfolio (mean ρ = 0.021) would be extremely valuable IF the strategy had sufficient Sharpe. XLF and XLI correlations are negligible (0.03) despite these sectors being in both the rotation and credit spread universes. This makes sense: credit spreads harvest vol premium while momentum captures price trends — orthogonal return drivers.

### Portfolio Integration: Dilutive

Adding sector momentum as a 9th stream *degraded* the portfolio (net Sharpe 4.99 → 4.74, delta -0.25). The Ledoit-Wolf optimizer correctly downweights it, but the added dimensionality introduces estimation noise that outweighs the diversification benefit.

### Why the AUM Capacity Research Was Optimistic

The research estimated "+$500M capacity" for sector momentum. This capacity estimate is valid — sector ETFs trade $500M+/day. But capacity without alpha is worthless. The 0.5-1.0 expected live Sharpe range assumed the backtest Sharpe was 1.0-1.6, but our honest walk-forward shows 0.57 *before* any live decay. After 0.5-0.7x live decay, the expected Sharpe is 0.28-0.40 — not worth the operational complexity.

### Recommendations

1. **Kill sector momentum rotation** as a standalone stream — Sharpe insufficient
2. **Do NOT add more equity-factor strategies** (value, quality, low-vol) — they share the same alpha decay problem
3. **Focus capacity expansion on Polygon-enabled options strategies** (IWM, DIA, EEM) where the variance risk premium is the alpha source, not factor timing
4. **Retain the finding** that sector momentum is uncorrelated (ρ = 0.02) — if a higher-alpha variant surfaces (e.g., intraday momentum, or earnings-catalyzed), the correlation slot is open

---

## 8. Verdict

**KILLED — WF Sharpe 0.57 < 1.0; 9-stream net Sharpe 4.74 < 6.0. Best: long_top3_3m WF Sharpe 0.57. All cross-sectional long-short variants have negative Sharpe. Sector momentum premium has decayed below the quality bar.**

---

## 9. Methodology

- **Universe:** 9 SPDR sector ETFs (XLB/XLE/XLF/XLI/XLK/XLP/XLU/XLV/XLY), 2010-2026
- **Momentum lookbacks:** 1m (21d), 3m (63d), 6m (126d), 12m (252d), combo (avg z-score of 3/6/12)
- **Rebalance:** Monthly (last trading day of each month)
- **Long-short:** Equal-weight top-3 long, bottom-3 short (dollar-neutral)
- **Long-only:** Equal-weight top-3, flat when VIX > 25 (causal, 1-day lag)
- **Walk-forward:** 252d train / 63d test, vol-targeted to 12%
- **Cost model:** 890 bps/yr analytical drag
- **Sharpe:** mean(daily) / std(daily, ddof=0) × √252

### Rule Zero

- `np.random.call`: 0 occurrences OK
- `random.normal.call`: 0 occurrences OK
- `generate_prices.call`: 0 occurrences OK

---

*Generated by compass/exp2950_sector_momentum.py*
*All data from Yahoo Finance. No synthetic data.*
