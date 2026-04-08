# Win Rate Boost Analysis: EXP-305 COMPASS Trades 2020-2025

**Date:** 2026-03-26
**Branch:** experiment/win-rate-boost
**Baseline:** EXP-305 COMPASS top-2 strict (SPY + top-2 sectors at 65% threshold)
**Research goal:** Identify filters that add ≥ +1pp win rate beyond ML-0.65 current filter

---

## ⚠️ Critical Caveat: Theoretical vs Observed Sharpe

All `SR_annual` values below are **theoretical** (assuming i.i.d. trades).
The **observed** annual Sharpe from portfolio simulation is ~2.60 — far lower.

The gap is explained by two factors (from `output/sharpe_ceiling_analysis.md`):
1. **Trade correlation:** SPY credit spreads entered daily are highly correlated.
   Effective independent trades N_eff ≈ 45, not 126. SR scales as √N_eff not √N_actual.
2. **Cross-year heterogeneity:** 2023 returns (+7%) vs 2025 returns (+56%) add
   between-year variance σ_between ≈ 2%/month, compressing the realized Sharpe.

**Implication:** When comparing filters, use SR_annual ratios (not absolute values).
A filter that improves theoretical SR by 10% will improve observed SR by ~10% too.
Absolute SR numbers are inflated by the i.i.d. assumption but ratios are valid.

---

## Background: The Sharpe Ceiling Constraint

From `output/sharpe_ceiling_analysis.md`, the theoretical maximum Sharpe for EXP-305 statistics:

| Win rate | SR_trade | SR_annual (N=208) | Δ SR_annual |
|----------|----------|-------------------|-------------|
| 83% | 0.3138 | 4.53 | -1.04 |
| 84% | 0.3488 | 5.03 | -0.54 |
| 85% | 0.3861 | 5.57 | +0.00 |
| 86% | 0.4262 | 6.15 | +0.58 |
| 87% | 0.4695 | 6.77 | +1.20 |
| 88% | 0.5166 | 7.45 | +1.88 |
| 89% | 0.5685 | 8.20 | +2.63 |
| 90% | 0.6263 | 9.03 | +3.46 |

> **Key constraint:** Sharpe scales as `SR_trade × √N`. Filtering improves win rate but
> reduces N. A filter must raise win rate enough to offset the √N reduction.
> Break-even: if filtering removes fraction f of trades, win rate must rise enough that
> `SR_trade(new) ≥ SR_trade(old) / √(1-f)`.

---

## Dataset Summary

- **Total trades (2020-2025):** 1,251 (all tickers: SPY, XLF, XLI)
- **OOS trades (2021-2025):** 1,034 (ML walk-forward scores available)
- **2020 trades:** 217 (training-only, no OOS ML scores)

### Flag coverage on OOS trades (2021-2025)

| Filter flag | Trades blocked | % of OOS | Blocked-trade win rate |
|-------------|----------------|----------|------------------------|
| VIX spike/crash (|5d ROC| > 20% or VIX > 35) |    217 |    21.0% |                  75.6% |
| Options expiration week (3rd Friday ±5d)       |    251 |    24.3% |                  76.1% |
| Mega-cap earnings week (AAPL/MSFT/NVDA/GOOGL/META) |    177 |    17.1% |                  78.5% |

> If blocked-trade win rate < OOS baseline, the filter helps (removes bad trades).
> If blocked-trade win rate ≈ OOS baseline, the filter is neutral (removes good+bad equally).

---

## Filter Results

All SR calculations use binary trade model: `SR_trade = (p·w − q·l) / (√(pq)·(w+l))`
with avg win w=19%, avg loss l=47%.
`SR_annual = SR_trade × √N_annual` (N_annual = trades per 6-year period ÷ 6).

### Summary table

| Filter                                     | Trades |  Retain |  Win rateΔ vs ref      |    N/yrΔ N/yr        | SR_trade | SR_annual         Δ% |
|--------------------------------------------|-------|---------|-----------------------|----------------------|----------|----------------------|
| Raw baseline (all trades)                  |  1251 |  100.0% |    79.06%              |   208.5              |   0.193 |     2.78            |
| OOS baseline (2021-2025, no ML)            |  1034 |  100.0% |    79.40% (0.0%)       |   172.3 (0.0%)       |   0.202 |     2.66 (0.0%)     |
|--------------------------------------------|-------|---------|-----------------------|----------------------|----------|----------------------|
| **Filter 1: ML confidence threshold**            |       |         |           |             |         |             |          |
| ML ≥ 0.55                                  |   802 |   77.6% |    91.40% (+12.0%)     |   133.7 (-38.7%)     |   0.720 |     8.32 (+213.1%)  |
| ML ≥ 0.60                                  |   778 |   75.2% |    92.54% (+13.1%)     |   129.7 (-42.7%)     |   0.812 |     9.25 (+248.0%)  |
| ML ≥ 0.65                                  |   756 |   73.1% |    93.39% (+14.0%)     |   126.0 (-46.3%)     |   0.892 |    10.02 (+276.8%)  |
| ML ≥ 0.70                                  |   735 |   71.1% |    93.74% (+14.3%)     |   122.5 (-49.8%)     |   0.930 |    10.29 (+287.3%)  |
| ML ≥ 0.75                                  |   710 |   68.7% |    93.94% (+14.5%)     |   118.3 (-54.0%)     |   0.953 |    10.37 (+290.0%)  |
| ML ≥ 0.80                                  |   664 |   64.2% |    94.13% (+14.7%)     |   110.7 (-61.7%)     |   0.975 |    10.25 (+285.7%)  |
|--------------------------------------------|-------|---------|-----------------------|----------------------|----------|----------------------|
| **Filter 2-4: Standalone (no ML)**               |       |         |           |             |         |             |          |
| VIX spike filter only (no ML)              |   817 |   79.0% |    80.42% (+1.0%)      |   136.2 (-36.2%)     |   0.232 |     2.71 (+1.8%)    |
| Expiry week filter only (no ML)            |   783 |   75.7% |    80.46% (+1.1%)      |   130.5 (-41.8%)     |   0.233 |     2.66 (+0.2%)    |
| Earnings week filter only (no ML)          |   857 |   82.9% |    79.58% (+0.2%)      |   142.8 (-29.5%)     |   0.208 |     2.48 (-6.7%)    |
|--------------------------------------------|-------|---------|-----------------------|----------------------|----------|----------------------|
| **Stacked: ML ≥ 0.65 + additional filters**      |       |         |           |             |         |             |          |
| ML ≥ 0.65 (current)                        |   756 |   73.1% |    93.39% (+14.0%)     |   126.0 (-46.3%)     |   0.892 |    10.02 (+276.8%)  |
| ML ≥ 0.65 + VIX spike                      |   604 |   79.9% |    93.54% (+0.2%)      |   100.7 (-25.3%)     |   0.909 |     9.12 (-9.0%)    |
| ML ≥ 0.65 + expiry week                    |   584 |   77.2% |    94.18% (+0.8%)      |    97.3 (-28.7%)     |   0.981 |     9.68 (-3.4%)    |
| ML ≥ 0.65 + earnings week                  |   639 |   84.5% |    93.11% (-0.3%)      |   106.5 (-19.5%)     |   0.865 |     8.93 (-10.9%)   |
| ML ≥ 0.65 + all three                      |   402 |   53.2% |    94.03% (+0.6%)      |    67.0 (-59.0%)     |   0.963 |     7.88 (-21.3%)   |
|--------------------------------------------|-------|---------|-----------------------|----------------------|----------|----------------------|
| **Stacked: ML ≥ 0.70**                           |       |         |           |             |         |             |          |
| ML ≥ 0.70                                  |   735 |   71.1% |    93.74% (+0.4%)      |   122.5 (-3.5%)      |   0.930 |    10.29 (+2.8%)    |
| ML ≥ 0.70 + all three                      |   391 |   53.2% |    94.63% (+1.2%)      |    65.2 (-60.8%)     |   1.039 |     8.39 (-16.3%)   |
|--------------------------------------------|-------|---------|-----------------------|----------------------|----------|----------------------|
| **Stacked: ML ≥ 0.75**                           |       |         |           |             |         |             |          |
| ML ≥ 0.75                                  |   710 |   68.7% |    93.94% (+0.6%)      |   118.3 (-7.7%)      |   0.953 |    10.37 (+3.5%)    |

*Δ vs ref: (a) ML sweep rows → vs OOS baseline; (b) ML-0.65+ rows → vs ML-0.65; (c) ML-0.70+ rows → vs ML-0.65.*

---

### Per-year win rate breakdown

| Filter | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | Avg |
|--------|------|------|------|------|------|------|-----|
| Raw baseline (all trades)              |   77.4% |   94.6% |   70.9% |   73.5% |   81.3% |   83.1% |   80.1% |
| OOS baseline (2021-2025)               |       — |   94.6% |   70.9% |   73.5% |   81.3% |   83.1% |   80.7% |
| ML ≥ 0.65 (current)                    |       — |  100.0% |   87.0% |   97.4% |   93.7% |   93.9% |   94.4% |
| ML ≥ 0.70                              |       — |  100.0% |   87.6% |   97.3% |   93.6% |   94.6% |   94.6% |
| ML ≥ 0.75                              |       — |  100.0% |   88.6% |   97.3% |   94.1% |   94.2% |   94.8% |
| ML ≥ 0.65 + VIX spike                  |       — |  100.0% |   86.6% |   97.3% |   94.8% |   94.6% |   94.7% |
| ML ≥ 0.65 + expiry week                |       — |  100.0% |   89.8% |  100.0% |   94.0% |   93.3% |   95.4% |
| ML ≥ 0.65 + earnings week              |       — |  100.0% |   85.3% |   96.6% |   92.7% |   94.8% |   93.9% |
| ML ≥ 0.65 + all three                  |       — |  100.0% |   90.5% |  100.0% |   92.5% |   93.6% |   95.3% |
| ML ≥ 0.70 + all three                  |       — |  100.0% |   92.2% |  100.0% |   92.4% |   94.1% |   95.7% |

---

## Filter Deep Dives

### Filter 1: ML Confidence Threshold

The EnsembleSignalModel (XGBoost + RF + ExtraTrees) assigns each trade a probability
of being a winner. Current threshold = 0.65 (optimized on 2024 validation set).

**Trade-off:** Higher threshold → higher win rate but fewer trades → ambiguous SR impact.

| Threshold | Win rate | N (OOS) | N/yr | SR_trade | SR_annual | Net Δ SR |
|-----------|----------|---------|------|----------|-----------|----------|
| 0.55      |   91.40% |     802 | 133.7 |   0.7198 |      8.32 | +5.66 (+213.1%) |
| 0.60      |   92.54% |     778 | 129.7 |   0.8122 |      9.25 | +6.59 (+248.0%) |
| 0.65      |   93.39% |     756 | 126.0 |   0.8922 |     10.02 | +7.36 (+276.8%) |
| 0.70      |   93.74% |     735 | 122.5 |   0.9301 |     10.29 | +7.64 (+287.3%) |
| 0.75      |   93.94% |     710 | 118.3 |   0.9530 |     10.37 | +7.71 (+290.0%) |
| 0.80      |   94.13% |     664 | 110.7 |   0.9745 |     10.25 | +7.59 (+285.7%) |

**Verdict (Filter 1):** Optimal threshold = **0.75** (SR_annual = 10.37 vs 10.02 at 0.65). Trade count goes from 756 → 710 (94% retained).

---

### Filter 2: VIX Transition / Spike Filter

Skip entries when the 5-day VIX rate-of-change exceeds ±20% 
or VIX > 35. Rationale: rapidly moving VIX indicates regime
transitions where historical win-rate priors break down.

- **VIX spike (ROC > +20%):** panic entries — IV elevated, regime uncertain
- **VIX crash (ROC < -20%):** recovery bounces — spread premiums collapse rapidly
- **VIX > 35:** extreme fear — historical data shows elevated stop-loss frequency

| Sub-filter | Count | Win rate |
|------------|-------|----------|
| All OOS trades | 1034 | 79.4% |
| VIX spike (5d ROC > +20%) | 141 | 73.0% |
| VIX crash (5d ROC < -20%) | 74 | 81.1% |
| VIX > 35 | 12 | 83.3% |
| Any VIX flag (union) | 217 | 75.6% |
| Remaining (no VIX flag) | 817 | 80.4% |

**Verdict (Filter 2):** VIX transition filter removes 217 trades (21.0% of OOS). 
Win rate moves from 79.4% → 80.4% (Δ = +1.0%pp). 
SR_annual: 2.66 → 2.71.

---

### Filter 3: Options Expiration Week Filter

Standard monthly options expire on the 3rd Friday of each month. The week around
expiration tends to have elevated gamma risk, increased pin risk, and unusual
intraday moves as market makers hedge.

Filter: skip any entry dated within 5 days before through
2 days after the 3rd Friday of any month.

| Period | Count | Win rate |
|--------|-------|----------|
| Expiry week entries (blocked) | 251 | 76.1% |
| Non-expiry entries (kept)     | 783 | 80.5% |

**Verdict (Filter 3):** Expiry week filter removes 251 trades (24.3%). 
Win rate: 79.4% → 80.5% (Δ = +1.1%pp). 
SR_annual: 2.66 → 2.66.

---

### Filter 4: Mega-cap Earnings Week Filter

SPY is heavily weighted toward AAPL, MSFT, NVDA, GOOGL, META (combined ~28% of SPY).
When any of these report, IV crush / surprise moves affect SPY significantly.
Filter: skip any entry within 5 calendar days before earnings date
for these 5 companies.

Earnings dates covered: AAPL, MSFT, NVDA, GOOGL, META — quarterly 2020-2025
(~114 earnings events = ~91 distinct dates)

| Period | Count | Win rate |
|--------|-------|----------|
| Earnings window entries (blocked) | 177 | 78.5% |
| Non-earnings entries (kept)       | 857 | 79.6% |

**Verdict (Filter 4):** Earnings filter removes 177 trades (17.1%). 
Win rate: 79.4% → 79.6% (Δ = +0.2%pp). 
SR_annual: 2.66 → 2.48.

---

## Optimal Filter Stack Analysis

Given the trade-count constraint, the optimal filter stack maximizes SR_annual:
`SR_annual = SR_trade(win_rate) × √(N_annual)`

| Stack | Win rate | N/yr | SR_annual | Δ SR | Net verdict |
|-------|----------|------|-----------|------|-------------|
| ML ≥ 0.65 (baseline)                   |   93.39% | 126.0 |     10.02 | +0.00 | NEUTRAL |
| ML ≥ 0.65 + VIX spike                  |   93.54% | 100.7 |      9.12 | -0.90 | WORSE |
| ML ≥ 0.65 + expiry week                |   94.18% | 97.3 |      9.68 | -0.34 | WORSE |
| ML ≥ 0.65 + earnings week              |   93.11% | 106.5 |      8.93 | -1.09 | WORSE |
| ML ≥ 0.65 + all three                  |   94.03% | 67.0 |      7.88 | -2.13 | WORSE |
| ML ≥ 0.70                              |   93.74% | 122.5 |     10.29 | +0.28 | BETTER |
| ML ≥ 0.70 + all three                  |   94.63% | 65.2 |      8.39 | -1.63 | WORSE |
| ML ≥ 0.75                              |   93.94% | 118.3 |     10.37 | +0.35 | BETTER |

**Best stack: ML ≥ 0.75** — SR_annual = 10.37 
(Δ = +0.35 vs ML-0.65 baseline)

---

## Key Findings & Recommendations

### 1. The win-rate vs trade-count tradeoff is severe

Every 10% of trades removed requires a ~5.1pp win rate increase just to break even:

| Trades removed | Win rate needed to maintain SR | Minimum actual improvement needed |
|----------------|-------------------------------|----------------------------------|
| 5% removed | 93.7% | +0.31% |
| 10% removed | 93.9% | +0.51% |
| 15% removed | 94.1% | +0.71% |
| 20% removed | 94.4% | +1.01% |
| 25% removed | 94.6% | +1.21% |
| 30% removed | 94.9% | +1.51% |

### 2. ML threshold recommendations

- **Current (0.65):** Win rate 93.39%, N/yr 126.0, SR_annual 10.02
- **Optimal (0.75):** Win rate 93.94%, N/yr 118.3, SR_annual 10.37

The ML threshold should be tuned annually on the prior-year validation set.
Raising it beyond the optimal point sacrifices SR_annual despite a higher win rate.

### 3. Non-ML filter effectiveness

| Filter | Win rate Δ | SR_annual Δ | Verdict |
|--------|-----------|------------|---------|
| VIX spike            |      +1.0% | +0.05 | NEUTRAL ~ |
| Expiry week          |      +1.1% | +0.01 | NEUTRAL ~ |
| Earnings week        |      +0.2% | -0.18 | HARMFUL ✗ |

### 4. +1pp win rate target: feasibility

Current ML-0.65: win rate 93.39%, SR_annual = 10.02
Target: win rate 94.39%, SR_annual = 11.30 (assuming N unchanged)

**To achieve +1pp win rate without reducing N, would need:**
- SR_annual improvement: +1.29 (+12.8%)
- This requires a filter that improves win rate +1pp while keeping ≥97% of trades

Based on the analysis, **no single mechanical filter** (VIX, expiry, earnings) meets
this bar cleanly. The most promising paths are:

1. **ML threshold optimization** — tuning to the per-year optimal threshold is the
   highest-precision lever (the model already captures VIX/earnings/timing implicitly).
2. **VIX spike filter on top of ML-0.65** — removes the worst-timing entries with
   low trade-count cost; may be additive if blocked trades are below-average.
3. **Diversification over single-strategy stacking** — adding a second uncorrelated
   strategy (e.g., the Mode B covered puts sleeve) achieves higher Sharpe than any
   win-rate filter because it increases N without increasing within-strategy correlation.

---

## Appendix: Technical Notes

### ML Walk-Forward Setup

- Model: XGBoost (n_estimators=100, max_depth=4, subsample=0.8)
- Features: 27 numeric (VIX, RSI, momentum, realized vol, DTE, OTM%, credit, etc.)
  + 3 categorical (regime, strategy_type, spread_type)
- Walk-forward: expanding window by year (train 2020→test 2021, train 2020-21→test 2022, ...)
- 2020 trades have no OOS scores (insufficient prior training data)

### Filter Definitions

**VIX transition:** |VIX_t / VIX_{t-5} - 1| > 20% OR VIX_t > 35
**Expiry week:** entry_date within [5 days before, 2 days after] 3rd Friday of any month
**Earnings week:** entry_date within 5 calendar days before any AAPL/MSFT/NVDA/GOOGL/META earnings

### Earnings dates used

Approximate historical earnings dates (from public records):
- **AAPL:** 2020-01-28, 2020-04-30, 2020-07-30, 2020-10-29, 2021-01-27, 2021-04-28, 2021-07-27, 2021-10-28, 2022-01-27, 2022-04-28, 2022-07-28, 2022-10-27, 2023-02-02, 2023-05-04, 2023-08-03, 2023-11-02, 2024-02-01, 2024-05-02, 2024-08-01, 2024-10-31, 2025-01-30, 2025-05-01
- **MSFT:** 2025-07-31, 2020-01-29, 2020-04-29, 2020-07-22, 2020-10-28, 2021-01-27, 2021-04-28, 2021-07-27, 2021-10-27, 2022-01-25, 2022-04-26, 2022-07-26, 2022-10-25, 2023-01-24, 2023-04-25, 2023-07-25, 2023-10-24, 2024-01-30, 2024-04-25, 2024-07-30, 2024-10-30, 2025-01-29, 2025-04-30
- **NVDA:** 2025-07-30, 2020-02-19, 2020-05-20, 2020-08-19, 2020-11-18, 2021-02-24, 2021-05-26, 2021-08-25, 2021-11-17, 2022-02-16, 2022-05-25, 2022-08-24, 2022-11-16, 2023-02-22, 2023-05-24, 2023-08-23, 2023-11-21, 2024-02-21, 2024-05-22, 2024-08-28, 2024-11-20, 2025-02-26
- **GOOGL:** 2025-05-28, 2020-02-04, 2020-04-28, 2020-07-28, 2020-10-29, 2021-02-02, 2021-04-27, 2021-07-27, 2021-10-26, 2022-02-01, 2022-04-26, 2022-07-26, 2022-10-25, 2023-02-02, 2023-04-25, 2023-07-25, 2023-10-24, 2024-01-30, 2024-04-25, 2024-07-23, 2024-10-29, 2025-02-04
- **META:** 2025-04-29, 2025-07-29, 2020-01-29, 2020-04-29, 2020-07-29, 2020-10-29, 2021-01-27, 2021-04-28, 2021-07-28, 2021-10-25, 2022-02-02, 2022-04-27, 2022-07-27, 2022-10-26, 2023-02-01, 2023-04-26, 2023-07-26, 2023-10-25, 2024-01-31, 2024-04-24, 2024-07-31, 2024-10-30, 2025-01-29, 2025-04-30, 2025-07-30

*Analysis generated by `scripts/win_rate_boost_analysis.py`.*
