# EXP-2910: TLT Put Credit Spread Integration — Results

**Date:** 2026-04-23 13:12
**Status:** KILLED
**Rule Zero:** CLEAN

---

## 1. Data Source

| Field | Value |
|---|---|
| Database | IronVault `options_cache.db` |
| Ticker | TLT (iShares 20+ Year Treasury Bond ETF) |
| Contracts | 10,749 |
| Daily bars | 293,500 |
| Date range | 2020-01 to 2025-12 |
| VIX source | Yahoo ^VIX (real) |
| TLT spot | Yahoo TLT (real) |

**Rule Zero compliance:** All prices from IronVault real option data. No synthetic data. No Black-Scholes as primary pricing.

---

## 2. Individual Stream Metrics (TLT Put Credit Spreads)

**Parameters:** 28 DTE, 5% OTM, $5 target width, 50% profit target, 2× stop loss

| Metric | Value | Kill Gate | Status |
|---|---|---|---|
| **Trade Sharpe** | **0.76** | ≥ 1.0 | FAIL |
| **Trades/year** | **9.2** | ≥ 20 | FAIL |
| CAGR | 0.8% | — | — |
| Max DD | 0.4% | — | — |
| Win rate | 88% | — | — |
| Total PnL | $4,638.00 | — | — |
| Avg PnL/trade | $89.19 | — | — |
| Total trades | 52 | — | — |

### Per-Year Breakdown

| Year | Trades | PnL | Win Rate |
|---|---|---|---|
| 2020 | 5 | $174.00 | 100% |
| 2021 | 8 | $250.00 | 88% |
| 2022 | 7 | $2,258.00 | 86% |
| 2023 | 11 | $1,676.00 | 82% |
| 2024 | 11 | $22.00 | 82% |
| 2025 | 10 | $258.00 | 100% |

### Exit Reasons

- **profit:** 45 trades
- **stop:** 6 trades
- **expiration:** 1 trades

---

## 3. Correlation with Existing 8 Streams

**Mean correlation:** ρ = -0.0172

| Stream | Correlation (ρ) |
|---|---|
| cross_vol | -0.0020 |
| exp1220 | +0.0338 |
| gld_cal | +0.0341 |
| qqq_cs | +0.0050 |
| slv_cal | +0.0404 |
| v5_hedge | -0.2306 |
| xlf_cs | -0.0109 |
| xli_cs | -0.0072 |

**Interpretation:** Near-zero correlation confirms TLT adds genuine diversification.

---

## 4. 9-Stream Portfolio (with TLT)

**Configuration:** Ledoit-Wolf risk-parity + 12% vol target + VIX ladder + 890 bps drag (Alpaca)

### Pooled Walk-Forward Metrics (NET)

| Metric | 9-Stream (with TLT) | 8-Stream Baseline | Delta |
|---|---|---|---|
| **Sharpe** | **4.94** | 4.99 | -0.05 |
| CAGR | 102.7% | 105.1% | -2.4pp |
| Max DD | 5.3% | 5.4% | -0.1pp |
| Vol | 12.7% | 12.7% | — |

### Walk-Forward Fold Distribution

| Metric | 9-Stream | 8-Stream |
|---|---|---|
| Median fold Sharpe | 6.26 | 6.22 |
| Worst fold Sharpe | 3.52 | — |
| % folds ≥ 6.0 | 70% | — |
| Number of folds | 20 | 20 |

---

## 5. Kill Criteria Summary

| Criterion | Threshold | Result | Status |
|---|---|---|---|
| Trade Sharpe | ≥ 1.0 | 0.76 | ❌ FAIL |
| Trades/year | ≥ 20 | 9.2 | ❌ FAIL |
| 9-stream net Sharpe | ≥ 6.0 | 4.94 | ❌ FAIL |
| vs v8a baseline (6.39) | improvement | -0.05 | ⚠️ DEGRADED |

---

## 6. Verdict

**KILLED — Trade Sharpe 0.76 < 1.0; Trades/year 9.2 < 20; 9-stream net Sharpe 4.94 < 6.0. Individual Sharpe 0.76, 9 trades/yr. 9-stream net Sharpe 4.94.**

---

## 7. Root Cause Analysis — Why TLT Failed

### Primary Kill: Trade Frequency (9.2/yr vs 20/yr threshold)

TLT has only **73 put expirations** in IronVault (mostly monthly cycles), yielding ~12 entry opportunities per year. After the 10-day minimum entry gap filter, only 52 trades survive across 5.6 years = **9.2 trades/year**.

This is the same failure mode as EXP-2800 (XLE: 4.4 trades/year). The IronVault TLT data has sufficient *contract depth* (10,749 contracts) but insufficient *expiration frequency* for the 28-DTE strategy cadence to generate 20+ entries/year.

**What would fix this:** TLT has weekly expirations in the live market (26 available per Yahoo Finance). If IronVault were backfilled with weekly TLT expirations via Polygon, the cadence would jump to ~26 entries/year, clearing the 20/yr threshold. The Polygon subscription ($199/mo) would unlock this.

### Secondary Kill: Trade Sharpe (0.76 vs 1.0 threshold)

TLT put credit spreads at 5% OTM collect a **median credit of only $0.045** (vs SPY's ~$2-5). TLT's lower absolute price (~$87 vs SPY ~$500+) and lower implied volatility drive this. The premium is not enough to build a reliable risk-adjusted return after the stop-loss events.

The 88% win rate is strong, but the average PnL/trade ($89.19) is dominated by a few large winners in 2022-2023 (rate-vol regime). In 2024, total PnL was only $22 across 11 trades — essentially flat despite high win rate.

### Portfolio Impact: Slight Degradation (-0.05 Sharpe)

Adding TLT as a 9th stream actually *decreased* the net Sharpe by 0.05 (4.99 → 4.94). The near-zero correlation (ρ = -0.017) should help, but the TLT stream has such low return magnitude that Ledoit-Wolf assigns it near-zero weight, and the additional dimensionality slightly dilutes the covariance estimate.

**Note on baseline vs v8a reference:** Our 8-stream baseline here (net Sharpe 4.99) differs from the v8a reference (6.39) because this walk-forward uses a simpler pipeline than EXP-2850's full configuration (which includes the DD circuit breaker and slightly different fold alignment).

### Positive Signal: Correlation is Excellent

The mean correlation of ρ = -0.017 with existing streams confirms the AUM_CAPACITY_RESEARCH thesis that TLT is the best diversifier. The v5_hedge anti-correlation (ρ = -0.23) is particularly valuable. **This signal should be pursued via other TLT strategies** (IV-RV arb via MOVE index, duration-neutral yield curve trades) rather than put credit spreads.

### Recommendation

1. **Kill TLT put credit spreads** — insufficient trade frequency on current data
2. **Prioritize EXP-2920 (TLT IV-RV arb via MOVE index)** — uses equity ADV not options, no IronVault needed, genuinely uncorrelated
3. **Revisit TLT spreads after Polygon** — weekly expirations would triple frequency to ~26/yr
4. **Do NOT reduce SLV weight** to accommodate TLT — the stream doesn't clear the quality bar

---

## 8. Methodology

- **Walk-forward:** 252-day train / 63-day test expanding window
- **Covariance:** Ledoit-Wolf shrinkage (sklearn)
- **Allocation:** Equal risk contribution (Chaves-Hsu-Li-Shakernia 2011)
- **Vol target:** 12% annualized, capped at 20×
- **VIX ladder:** EXP-2820 default (9 breakpoints, causal shift-1d)
- **Transaction costs:** 890 bps/yr (Alpaca commission-free + execution)
- **Sharpe formula:** `mean(daily_returns) / std(daily_returns) × √252` (canonical, ddof=0 for std)
- **Convention:** Sparse exit-date attribution (no P&L smearing)

### Data Sources Cited

- IronVault TLT options: `data/options_cache.db` (10,749 contracts, 293,500 daily bars, 2020-01 to 2025-12)
- Yahoo TLT close: `yfinance.download("TLT")`
- Yahoo ^VIX close: `yfinance.download("^VIX")`
- v8a cube: `compass/cache/exp2280_v6_sparse.pkl` + `exp2250_qqq_trades.pkl`

### Rule Zero Verification

Synthetic data patterns grepped before reporting:
- `np.random`: 0 occurrences ✅
- `random.normal`: 0 occurrences ✅
- `generate_prices`: 0 occurrences ✅

---

*Generated by compass/exp2910_tlt_credit_spreads.py*
*All data sources are real. No synthetic data used.*
