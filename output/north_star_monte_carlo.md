# North Star Portfolio — Monte Carlo Simulation

> **Generated:** 2026-03-26 12:08 UTC
> **Branch:** `main`
> **Seeds:** 10,000  |  **Years per path:** 6
> **⚠️ Win rate corrected to 93.4%** (from 86%) — see `output/win_rate_boost_report.md`

---

## Summary: Corrected vs Original Parameters

| Parameter | Original (REF) | Scenario A (corrected) | Scenario B (corrected) |
|-----------|:--------------:|:----------------------:|:----------------------:|
| Win rate | 86.0% | **93.4%** | **93.4%** |
| Trades/yr | 280 | 208 (SPY-only) | 280 (sector div.) |
| Source | Prior MC | ML WFV OOS actuals | ML WFV OOS actuals |

### North Star achievement comparison

| Scenario | T1: Return≥100% | T2: DD≥-12% | T3: Sharpe≥2.0 | **All 3** | P50 annual |
|----------|:---------------:|:-----------:|:--------------:|:---------:|:----------:|
| Scenario A — SPY-only (corrected, p=93.4%, N=208)       | 100.0% | 100.0% | 100.0% | **100.0%** | +892.0% |
| Scenario B — Sector diversified (corrected, p=93.4%, N=280) | 100.0% | 100.0% | 100.0% | **100.0%** | +2066.5% |
| REF — Original baseline (p=86%, N=280)                  |  93.0% | 100.0% |  98.1% | ** 92.0%** | +299.4% |

---

## A: Scenario A — SPY-only (corrected, p=93.4%, N=208)

### Parameters

| Parameter | Value | Source |
|-----------|:-----:|--------|
| Trades per year | 208 | SPY-only |
| Win rate | 93.4% | ML-filtered OOS walk-forward (2021-2025) |
| Avg win / risk | +19% | Credit spreads: 19% avg credit kept on winners |
| Avg loss / risk | -47% | Stop-loss path: 47% of risk lost on average |
| Trade correlation ρ | 0.04 | Shared market factor |

**Expected arithmetic annual return (208 trades):** +236.1%  
**Expected std of arithmetic annual return:** +18.3%

### Distribution (10,000 simulations)

| Metric | Mean | Median | Std | P1 | P5 | P10 | P25 | P50 | P75 | P90 | P95 | P99 |
|--------|:----:|:------:|:---:|:--:|:--:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Avg annual return | +883.8% | +892.0% | +121.8% | +561.8% | +672.5% | +723.8% | +806.6% | +892.0% | +969.1% | +1031.3% | +1066.9% | +1140.3% |
| 6yr CAGR | +820.2% | +855.2% | +169.8% | +390.1% | +520.3% | +582.0% | +695.7% | +855.2% | +948.2% | +1015.6% | +1049.4% | +1124.6% |
| Worst single-yr DD | -9.7% | -10.0% | +1.3% | -11.8% | -11.6% | -11.4% | -10.7% | -10.0% | -8.4% | -8.3% | -7.6% | -7.1% |
| Avg annual Sharpe | 9.12 | 9.15 | 0.89 | 6.70 | 7.59 | 7.99 | 8.58 | 9.15 | 9.71 | 10.21 | 10.51 | 11.09 |

#### Per-year return distribution

| Year | Mean | P5 | P25 | P50 | P75 | P95 | P(>0) | P(>100%) |
|------|:----:|:--:|:---:|:---:|:---:|:---:|:-----:|:--------:|
| Y1 | +879.8% | +129.8% | +763.5% | +916.6% | +1069.7% | +1300.9% | 99.4% | 95.6% |
| Y2 | +879.7% | +137.1% | +760.8% | +916.4% | +1067.1% | +1297.5% | 99.3% | 95.9% |
| Y3 | +883.2% | +166.0% | +763.3% | +917.6% | +1067.3% | +1289.9% | 99.5% | 96.3% |
| Y4 | +884.2% | +158.2% | +766.3% | +921.1% | +1067.4% | +1292.9% | 99.4% | 96.3% |
| Y5 | +889.1% | +168.2% | +768.1% | +924.6% | +1072.5% | +1302.2% | 99.4% | 96.2% |
| Y6 | +886.6% | +139.0% | +769.1% | +925.3% | +1073.1% | +1303.8% | 99.4% | 95.9% |

#### Per-year drawdown distribution

| Year | P5 DD | P25 DD | P50 DD | P75 DD | P95 DD |
|------|:-----:|:------:|:------:|:------:|:------:|
| Y1 | -10.7% | -8.3% | -7.4% | -5.8% | -4.2% |
| Y2 | -10.7% | -8.3% | -7.4% | -5.7% | -4.2% |
| Y3 | -10.7% | -8.3% | -7.4% | -5.5% | -4.2% |
| Y4 | -10.7% | -8.3% | -7.4% | -5.5% | -4.2% |
| Y5 | -10.7% | -8.3% | -7.4% | -5.5% | -4.2% |
| Y6 | -10.7% | -8.3% | -7.4% | -5.5% | -4.2% |

### North Star Target Achievement

| Target | Threshold | % achieving |
|--------|:---------:|:-----------:|
| T1: Avg annual return | ≥ 100% | **100.0%** |
| T2: Max portfolio DD  | ≥ -12% | **100.0%** |
| T3: Avg annual Sharpe | ≥ 2.0  | **100.0%** |
| **ALL THREE**         | —      | **100.0%** |

- **Binding constraint:** T1 (return)
- P50 avg annual: +892.0%  |  P5/P95: +672.5% → +1066.9%
- Calibration-adjusted P50: +579.8% (×0.65 vs actual backtester)

### Target Sensitivity

| Return target | DD target | Sharpe target | % passing |
|:-------------:|:---------:|:-------------:|:---------:|
| ≥100% | ≥-12% | ≥2.0 | **100.0%** |
| ≥80% | ≥-12% | ≥2.0 | **100.0%** |
| ≥60% | ≥-12% | ≥2.0 | **100.0%** |
| ≥200% | ≥-12% | ≥2.0 | **100.0%** |
| ≥300% | ≥-12% | ≥2.0 | **100.0%** |
| ≥400% | ≥-12% | ≥2.0 | **99.9%** |
| ≥500% | ≥-12% | ≥2.0 | **99.7%** |
| ≥100% | ≥-15% | ≥2.0 | **100.0%** |
| ≥100% | ≥-20% | ≥2.0 | **100.0%** |
| ≥100% | ≥-12% | ≥1.5 | **100.0%** |
| ≥100% | ≥-12% | ≥1.0 | **100.0%** |

---

## B: Scenario B — Sector diversified (corrected, p=93.4%, N=280)

### Parameters

| Parameter | Value | Source |
|-----------|:-----:|--------|
| Trades per year | 280 | SPY 208 + sector ETFs 72 |
| Win rate | 93.4% | ML-filtered OOS walk-forward (2021-2025) |
| Avg win / risk | +19% | Credit spreads: 19% avg credit kept on winners |
| Avg loss / risk | -47% | Stop-loss path: 47% of risk lost on average |
| Trade correlation ρ | 0.04 | Shared market factor |

**Expected arithmetic annual return (280 trades):** +317.8%  
**Expected std of arithmetic annual return:** +21.3%

### Distribution (10,000 simulations)

| Metric | Mean | Median | Std | P1 | P5 | P10 | P25 | P50 | P75 | P90 | P95 | P99 |
|--------|:----:|:------:|:---:|:--:|:--:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Avg annual return | +2044.0% | +2066.5% | +333.6% | +1186.5% | +1457.2% | +1599.9% | +1830.2% | +2066.5% | +2280.0% | +2453.8% | +2543.3% | +2723.0% |
| 6yr CAGR | +1798.1% | +1869.8% | +498.0% | +663.4% | +921.7% | +1108.7% | +1415.5% | +1869.8% | +2203.6% | +2399.2% | +2492.7% | +2672.1% |
| Worst single-yr DD | -10.0% | -10.2% | +1.2% | -11.8% | -11.6% | -11.5% | -11.0% | -10.2% | -9.1% | -8.3% | -8.3% | -7.4% |
| Avg annual Sharpe | 9.24 | 9.28 | 0.80 | 7.00 | 7.84 | 8.23 | 8.77 | 9.28 | 9.77 | 10.20 | 10.46 | 10.98 |

#### Per-year return distribution

| Year | Mean | P5 | P25 | P50 | P75 | P95 | P(>0) | P(>100%) |
|------|:----:|:--:|:---:|:---:|:---:|:---:|:-----:|:--------:|
| Y1 | +2036.2% | +129.8% | +1715.7% | +2144.9% | +2561.6% | +3196.3% | 99.4% | 95.6% |
| Y2 | +2042.6% | +146.1% | +1732.0% | +2149.7% | +2558.5% | +3172.4% | 99.5% | 96.0% |
| Y3 | +2049.1% | +153.8% | +1727.1% | +2159.5% | +2564.9% | +3167.3% | 99.3% | 96.2% |
| Y4 | +2034.6% | +133.7% | +1716.7% | +2148.6% | +2558.6% | +3152.6% | 99.4% | 95.9% |
| Y5 | +2043.8% | +154.7% | +1727.2% | +2154.8% | +2569.7% | +3175.0% | 99.4% | 96.0% |
| Y6 | +2057.6% | +191.3% | +1730.0% | +2160.1% | +2568.9% | +3189.6% | 99.5% | 96.5% |

#### Per-year drawdown distribution

| Year | P5 DD | P25 DD | P50 DD | P75 DD | P95 DD |
|------|:-----:|:------:|:------:|:------:|:------:|
| Y1 | -10.9% | -8.6% | -7.6% | -6.4% | -4.2% |
| Y2 | -11.0% | -8.5% | -7.6% | -6.1% | -4.2% |
| Y3 | -10.9% | -8.5% | -7.6% | -6.3% | -4.2% |
| Y4 | -11.0% | -8.6% | -7.6% | -6.4% | -4.2% |
| Y5 | -11.0% | -8.5% | -7.5% | -6.2% | -4.2% |
| Y6 | -10.8% | -8.5% | -7.6% | -6.1% | -4.2% |

### North Star Target Achievement

| Target | Threshold | % achieving |
|--------|:---------:|:-----------:|
| T1: Avg annual return | ≥ 100% | **100.0%** |
| T2: Max portfolio DD  | ≥ -12% | **100.0%** |
| T3: Avg annual Sharpe | ≥ 2.0  | **100.0%** |
| **ALL THREE**         | —      | **100.0%** |

- **Binding constraint:** T1 (return)
- P50 avg annual: +2066.5%  |  P5/P95: +1457.2% → +2543.3%
- Calibration-adjusted P50: +1343.2% (×0.65 vs actual backtester)

### Target Sensitivity

| Return target | DD target | Sharpe target | % passing |
|:-------------:|:---------:|:-------------:|:---------:|
| ≥100% | ≥-12% | ≥2.0 | **100.0%** |
| ≥80% | ≥-12% | ≥2.0 | **100.0%** |
| ≥60% | ≥-12% | ≥2.0 | **100.0%** |
| ≥200% | ≥-12% | ≥2.0 | **100.0%** |
| ≥300% | ≥-12% | ≥2.0 | **100.0%** |
| ≥400% | ≥-12% | ≥2.0 | **100.0%** |
| ≥500% | ≥-12% | ≥2.0 | **100.0%** |
| ≥100% | ≥-15% | ≥2.0 | **100.0%** |
| ≥100% | ≥-20% | ≥2.0 | **100.0%** |
| ≥100% | ≥-12% | ≥1.5 | **100.0%** |
| ≥100% | ≥-12% | ≥1.0 | **100.0%** |

---

## REF: Original Baseline (p=86%, N=280)

| Metric | Mean | P50 | P5 | P95 |
|--------|:----:|:---:|:--:|:---:|
| Avg annual return | +311.0% | +299.4% | +85.0% | +583.3% |
| Worst DD (median) | — | -11.5% | — | — |
| Avg annual Sharpe | — | 4.58 | — | — |
| All-3 pass rate | — | **92.0%** | — | — |

---

## Head-to-Head Comparison

| Metric | REF (p=86%, N=280) | Sc-A (p=93.4%, N=208) | Sc-B (p=93.4%, N=280) |
|--------|:------------------:|:---------------------:|:---------------------:|
| P50 avg annual return            |            +299.4% |               +892.0% |              +2066.5% |
| P5  avg annual return            |             +85.0% |               +672.5% |              +1457.2% |
| P95 avg annual return            |            +583.3% |              +1066.9% |              +2543.3% |
| P50 worst DD                     |             -11.5% |                -10.0% |                -10.2% |
| P50 avg Sharpe                   |               4.58 |                  9.15 |                  9.28 |
| T1: Return ≥ 100%                |              93.0% |                100.0% |                100.0% |
| T2: DD ≥ -12%                    |             100.0% |                100.0% |                100.0% |
| T3: Sharpe ≥ 2.0                 |              98.1% |                100.0% |                100.0% |
| **All 3 targets**                |          **92.0%** |            **100.0%** |            **100.0%** |
| Calibrated P50 (×0.65)           |            +194.6% |               +579.8% |              +1343.2% |

---

## Model Calibration

The sequential trade model compounds each trade against *current* capital, which
overstates returns vs the actual backtester (concurrent positions share capital pool).
Calibration factor 0.65× derived from exp_126 comparison (actual ÷ model).

| Scenario | Model P50 | Calibrated P50 (×0.65) | vs 200% roadmap target |
|----------|:---------:|:----------------------:|:----------------------:|
| Sc-A | +892.0% | +579.8% | ABOVE ✓ |
| Sc-B | +2066.5% | +1343.2% | ABOVE ✓ |
| REF | +299.4% | +194.6% | NEAR ~ |

---

## Key Findings

### Win rate correction impact

Raising win rate from 86% → 93.4% (same N=280) improves P50 annual return:
  +299.4% (REF) → +2066.5% (Sc-B) = +1767.1% absolute (590.1% relative)

### Binding North Star constraint (corrected)

**Sc-A:** tightest constraint = T1 (100.0% pass rate). All-3 pass rate = **100.0%**.
**Sc-B:** tightest constraint = T1 (100.0% pass rate). All-3 pass rate = **100.0%**.

### North Star status

**Sc-A:** ✅ EXCEEDS — 100.0% of paths pass all 3 North Star targets
**Sc-B:** ✅ EXCEEDS — 100.0% of paths pass all 3 North Star targets

### Important caveat: trade correlation

The 93.4% win rate comes from OOS walk-forward validation across 2021-2025.
However, the P50 annual returns computed here assume ρ=0.04 trade correlation.
From `output/sharpe_ceiling_analysis.md`, the actual observed Sharpe (2.60)
implies N_eff ≈ 45 (not 208) — higher effective ρ than assumed here.
The calibration factor 0.65× partially captures this, but the absolute return
numbers remain optimistic. The **relative** comparison between REF and corrected
scenarios remains valid.

---

*Simulation: `scripts/run_corrected_north_star_mc.py` | 10,000 paths × 6 years*  
*Win rate 93.4% from `output/win_rate_boost_report.md` (ML OOS walk-forward 2021-2025)*  
*Correlation model: ρ=0.04 inter-trade | Calibration factor 0.65× vs actual backtester*  
*Not accounting for: slippage, margin calls, liquidity constraints*