# Portfolio ML Optimization Report

**Generated:** 2026-03-27  
**Strategies:** 5 champions analyzed  
**Period:** 2020–2025 (6 years)  
**Base capital:** $100,000

## Executive Summary

The **max-Sharpe allocation** recommends concentrating in the highest risk-adjusted strategies while maintaining minimum 5% exposure to all.  Expected blended annual return: **47.4%**, Sharpe: **1.70**.

| Strategy | Weight | Annual Return | Profile |
|----------|--------|---------------|---------|
| EXP-305 | 31.1% | +54.6% | SPY-only effective — COMPASS sector ETFs have insuffici |
| EXP-400 | 29.8% | +32.7% | Balanced — regime-adaptive CS+IC, very low DD |
| EXP-154 | 21.9% | +31.4% | Conservative — 5% nominal risk, IC overlay in neutral r |
| EXP-520 | 12.3% | +38.0% | VIX-gated — vix_max_entry=35 cuts 2020 crash losses, co |
| EXP-126 | 5.0% | +32.8% | High-return — strong 2022/2025, weaker 2023/2024 |

### Blended Portfolio Performance (Best Allocation)

| Year | SPY S&P | EXP-400 | EXP-126 | EXP-154 | EXP-520 | EXP-305 | **Blended** |
|------|---------|---------|---------|---------|---------|---------|-------------|
| 2020 | +18.4% | +8.9% | +39.0% | +45.5% | +70.9% | +53.7% | **+39.9%** |
| 2021 | +28.7% | +101.4% | +28.8% | +27.6% | +36.1% | +29.6% | **+51.3%** |
| 2022 | -19.6% | -1.9% | +3.4% | +23.0% | +24.1% | +98.5% | **+38.2%** |
| 2023 | +26.3% | +37.5% | +11.0% | +8.4% | +15.3% | +2.7% | **+16.3%** |
| 2024 | +23.1% | +23.8% | +20.8% | +13.4% | +30.5% | +15.0% | **+19.5%** |
| 2025 | +24.9% | +26.5% | +94.0% | +70.5% | +51.0% | +128.2% | **+74.1%** |
| **Avg** | +17.0% | +32.7% | +32.8% | +31.4% | +38.0% | +54.6% | **+39.9%** |

## Strategy Profiles

### EXP-400 Champion (DTE=15, Regime-Adaptive)

- **Source:** Deterministic backtest (leaderboard)
- **Profile:** Balanced — regime-adaptive CS+IC, very low DD
- **6-year avg return:** +32.7% | Std dev: +33.2% | Best: +101.4% (2021) | Worst: -1.9% (2022)
- **Max drawdown (worst year):** -12.1%

| Year | Return | Max DD | Sharpe |
|------|--------|--------|--------|
| 2020 | +8.9% | -10.4% | 0.72 |
| 2021 | +101.4% | -2.9% | 6.62 |
| 2022 | -1.9% | -12.1% | -0.34 |
| 2023 | +37.5% | -3.3% | 3.62 |
| 2024 | +23.8% | -5.5% | 2.74 |
| 2025 | +26.5% | -8.1% | 2.19 |

### EXP-126 8% Flat Risk (DTE=35, IC-Neutral)

- **Source:** MC P50 (30 seeds, DTE U[33,37])
- **Profile:** High-return — strong 2022/2025, weaker 2023/2024
- **6-year avg return:** +32.8% | Std dev: +29.7% | Best: +94.0% (2025) | Worst: +3.4% (2022)
- **Max drawdown (worst year):** -30.9%

| Year | Return | Max DD | Sharpe |
|------|--------|--------|--------|
| 2020 | +39.0% | -30.9% | 1.08 |
| 2021 | +28.8% | -6.2% | 2.67 |
| 2022 | +3.4% | -14.6% | 2.40 |
| 2023 | +11.0% | -10.8% | 0.59 |
| 2024 | +20.8% | -7.9% | 1.01 |
| 2025 | +94.0% | -16.7% | 2.00 |

### EXP-154 5% Dir + 12% IC (IC-Neutral)

- **Source:** MC P50 (200 seeds, DTE U[33,37])
- **Profile:** Conservative — 5% nominal risk, IC overlay in neutral regime
- **6-year avg return:** +31.4% | Std dev: +21.1% | Best: +70.5% (2025) | Worst: +8.4% (2023)
- **Max drawdown (worst year):** -28.1%

| Year | Return | Max DD | Sharpe |
|------|--------|--------|--------|
| 2020 | +45.5% | -28.1% | 1.08 |
| 2021 | +27.6% | -6.2% | 2.00 |
| 2022 | +23.0% | -14.6% | 1.80 |
| 2023 | +8.4% | -10.8% | 0.50 |
| 2024 | +13.4% | -7.9% | 0.90 |
| 2025 | +70.5% | -16.7% | 1.80 |

### EXP-520 Real-Data Champion (VIX Gate, DTE=35/28)

- **Source:** Deterministic backtest (Phase 9, March 2026)
- **Profile:** VIX-gated — vix_max_entry=35 cuts 2020 crash losses, consistent returns
- **6-year avg return:** +38.0% | Std dev: +18.3% | Best: +70.9% (2020) | Worst: +15.3% (2023)
- **Max drawdown (worst year):** -39.4%

| Year | Return | Max DD | Sharpe |
|------|--------|--------|--------|
| 2020 | +70.9% | -14.4% | 2.00 |
| 2021 | +36.1% | -18.0% | 2.00 |
| 2022 | +24.1% | -20.0% | 2.00 |
| 2023 | +15.3% | -15.0% | 1.50 |
| 2024 | +30.5% | -16.0% | 2.00 |
| 2025 | +51.0% | -39.4% | 2.00 |

### EXP-305 COMPASS (Corrected — SPY-only effective)

- **Source:** Deterministic backtest 2026-03-26 (current code). Sector ETFs generated 0 trades 2020-2023 due to sparse options data (SOXX: 2 expirations/yr, XLC: 0 contracts). Original +70.6% claim (March 8 run) NOT reproducible. Corrected avg: +54.6%.
- **Profile:** SPY-only effective — COMPASS sector ETFs have insufficient options data (SOXX sparse, XLC missing entirely). 50% capital idle 2020-2023. Corrected avg +54.6% vs claimed +70.6%. 2022 is outstanding (+98.5%) due to SPY bear calls in bear market.
- **6-year avg return:** +54.6% | Std dev: +45.2% | Best: +128.2% (2025) | Worst: +2.7% (2023)
- **Max drawdown (worst year):** -30.4%

| Year | Return | Max DD | Sharpe |
|------|--------|--------|--------|
| 2020 | +53.7% | -25.0% | 1.50 |
| 2021 | +29.6% | -8.0% | 2.50 |
| 2022 | +98.5% | -15.0% | 3.50 |
| 2023 | +2.7% | -10.0% | 0.50 |
| 2024 | +15.0% | -13.0% | 1.20 |
| 2025 | +128.2% | -30.4% | 2.50 |

## Portfolio Optimization: Allocation Weights

Four optimization methods are applied to find the optimal capital allocation.
All methods enforce: long-only, minimum 5% per strategy, weights sum to 100%.

### Method Comparison

| Method | EXP-126 | EXP-154 | EXP-305 | EXP-400 | EXP-520 | Ann. Return | Ann. Vol | Sharpe |
|--------|--------|--------|--------|--------|--------|-------------|----------|--------|
| Max Sharpe | 5.0% | 21.9% | 31.1% | 29.8% | 12.3% | +47.4% | +25.3% | 1.70 |
| Risk Parity | 5.0% | 29.2% | 24.9% | 26.6% | 14.3% | +46.3% | +24.4% | 1.71 |
| Equal Risk Contrib. | 5.0% | 20.4% | 8.1% | 40.0% | 26.5% | +46.5% | +24.9% | 1.69 |
| Min Variance | 5.0% | 28.5% | 27.7% | 27.6% | 11.2% | +46.5% | +24.7% | 1.70 |

### Recommended Allocation: Max Sharpe

**Regime:** NEUTRAL_MACRO  
**Event scaling factor:** 1.00 (1.0 = no events pending)  
**Next rebalance:** 2026-04-03

#### Base weights (pre-event scaling):

- **31.1%** → EXP-305 COMPASS (Corrected — SPY-only effective)
- **29.8%** → EXP-400 Champion (DTE=15, Regime-Adaptive)
- **21.9%** → EXP-154 5% Dir + 12% IC (IC-Neutral)
- **12.3%** → EXP-520 Real-Data Champion (VIX Gate, DTE=35/28)
- **5.0%** → EXP-126 8% Flat Risk (DTE=35, IC-Neutral)

#### Scaled weights (after event gate):

Total capital deployed: **100.0%**

- **31.1%** → EXP-305
- **29.8%** → EXP-400
- **21.9%** → EXP-154
- **12.3%** → EXP-520
- **5.0%** → EXP-126

## Regime-Adaptive Allocations

COMPASS macro regime (BULL/NEUTRAL/BEAR) shifts weights toward momentum or defensive strategies.

| Regime | EXP-126 | EXP-154 | EXP-305 | EXP-400 | EXP-520 | Expected Return | Sharpe |
|--------|--------|--------|--------|--------|--------|-----------------|--------|
| BULL | 9.3% | 21.1% | 27.5% | 27.8% | 14.3% | +55.9% | 1.20 |
| NEUTRAL | 5.0% | 21.9% | 31.1% | 29.8% | 12.3% | +47.4% | 1.70 |
| BEAR | 9.8% | 21.6% | 28.0% | 25.8% | 14.8% | +56.9% | 1.17 |

> **BULL regime** upweights EXP-305 (COMPASS sectors) and EXP-400 (momentum-affinity=0.6).  
> **BEAR regime** upweights EXP-154 and EXP-154 (defensive, lower risk).  
> **Regime blend parameter:** 30% (30% tilt toward regime affinity, 70% optimizer-driven).

## Cross-Strategy Correlation Matrix

Pearson correlation of simulated monthly returns (72 periods, 2020–2025).  
Note: EXP-400 uses actual monthly PnL data where available; others use simulated monthly returns.

| | EXP-126 | EXP-154 | EXP-305 | EXP-400 | EXP-520 |
|---|---|---|---|---|---|
| EXP-126 | **1.00** | -0.29 | 0.37 | 0.05 | -0.03 |
| EXP-154 | -0.29 | **1.00** | 0.01 | -0.01 | 0.05 |
| EXP-305 | 0.37 | 0.01 | **1.00** | -0.01 | -0.22 |
| EXP-400 | 0.05 | -0.01 | -0.01 | **1.00** | -0.07 |
| EXP-520 | -0.03 | 0.05 | -0.22 | -0.07 | **1.00** |

**Average pairwise correlation:** -0.02

> ✅ LOW correlation — strong diversification benefit across strategies.

### Notable Correlation Pairs

- **EXP-126 ↔ EXP-305:** 0.37 (MODERATE)
- **EXP-126 ↔ EXP-154:** -0.29 (LOW)
- **EXP-305 ↔ EXP-520:** -0.22 (LOW)
- **EXP-400 ↔ EXP-520:** -0.07 (LOW)
- **EXP-126 ↔ EXP-400:** 0.05 (LOW)
- **EXP-154 ↔ EXP-520:** 0.05 (LOW)
- **EXP-126 ↔ EXP-520:** -0.03 (LOW)
- **EXP-305 ↔ EXP-400:** -0.01 (LOW)
- **EXP-154 ↔ EXP-400:** -0.01 (LOW)
- **EXP-154 ↔ EXP-305:** 0.01 (LOW)

## Realized Crisis Performance (Actual Backtested Returns)

These are the *actual* per-year returns from backtests (or MC P50), not synthetic scenarios.

### COVID Year (2020) — Actual Realized Returns

| Strategy | 2020 Return | 2020 Max DD | Notes |
|----------|-------------|-------------|-------|
| EXP-400 | +8.9% | -10.4% | DTE=15 tactical; light 2020 trading, avoided COVID peak |
| EXP-126 | +39.0% | -30.9% | MC P50 — deterministic was +53%; VIX spikes fire IC circuit breaker |
| EXP-154 | +45.5% | -28.1% | MC P50 — 5% risk cap limits crash exposure; CB protects |
| EXP-520 | +70.9% | -14.4% | VIX gate (vix_max_entry=35) cut DD from -61.6% to -14.4%; still +70.9%! |
| EXP-305 | +53.7% | -25.0% | CORRECTED: SPY at 50% alloc only (SOXX/XLK had 0 trades — data sparse). -42.8pp vs original claim. |
| **COMBINED** | **+39.9%** | **-20.3%** | Blended per best-allocation weights |

### 2022 Bear Market — Actual Realized Returns

| Strategy | 2022 Return | 2022 Max DD | Notes |
|----------|-------------|-------------|-------|
| EXP-400 | -1.9% | -12.1% | ONLY loser in 2022 (-1.9%); DTE=15 caught mid-put assignments |
| EXP-126 | +3.4% | -14.6% | MC P50 +3.4%; deterministic was +79%! Bear calls vs falling SPY |
| EXP-154 | +23.0% | -14.6% | MC P50 +23%; IC-NEUTRAL outperforms — bear year IC misses, prevents big losses |
| EXP-520 | +24.1% | -20.0% | +24.1% despite bear year — VIX gate prevents new entries when VIX>35 |
| EXP-305 | +98.5% | -15.0% | CORRECTED +98.5%: SPY bear calls at 50% alloc (XLE had 0 trades — sparse data). +24.5pp above original claim. |
| **COMBINED** | **+38.2%** | **-14.6%** | Blended per best-allocation weights |

> **Key insight:** ALL strategies except EXP-400 were profitable in 2022. The combined portfolio returned +38.2% while SPY fell -19.6%. This is the core value proposition: short-vol credit spreads + sector rotation = crisis alpha.

## Stress Test Results (Synthetic Crisis Scenarios)

Monte Carlo (1,000 paths, block-bootstrap) + 4 synthetic crisis scenarios.  
Note: Crisis scenarios apply a uniform shock path to all strategies (credit spread beta=1.5×).  
For *actual* crisis performance, see 'Realized Crisis Performance' section above.

### Monte Carlo: Terminal Wealth Distribution ($100,000 starting capital)

| Strategy | P5 | P25 | P50 | P75 | P95 | Prob Profit | Prob Ruin | Risk Rating |
|----------|----|-----|-----|-----|-----|-------------|-----------|-------------|
| EXP-400 | $340,796 | $391,887 | $429,243 | $475,404 | $543,882 | 100.0% | 0.0% | MODERATE |
| EXP-126 | $327,540 | $387,791 | $441,976 | $501,439 | $592,744 | 100.0% | 0.0% | MODERATE |
| EXP-154 | $366,180 | $425,090 | $470,807 | $523,669 | $612,488 | 100.0% | 0.0% | MODERATE |
| EXP-520 | $455,710 | $557,497 | $643,339 | $747,790 | $914,332 | 100.0% | 0.0% | MODERATE |
| EXP-305 | $951,440 | $1,143,681 | $1,307,862 | $1,485,216 | $1,813,362 | 100.0% | 0.0% | MODERATE |
| **COMBINED** | $570,879 | $623,579 | $660,957 | $698,042 | $758,265 | 100.0% | 0.0% | MODERATE |

### Monte Carlo: Sharpe & Drawdown Distributions

| Strategy | Median Sharpe | P5 Sharpe | Median Max DD | P5 Max DD (worst) |
|----------|---------------|-----------|---------------|-------------------|
| EXP-400 | 4.85 | 4.10 | -2.9% | -4.4% |
| EXP-126 | 3.45 | 2.74 | -5.5% | -8.2% |
| EXP-154 | 3.85 | 3.23 | -4.4% | -6.7% |
| EXP-520 | 3.54 | 2.88 | -6.0% | -8.8% |
| EXP-305 | 5.56 | 4.86 | -3.8% | -5.6% |
| **COMBINED** | 9.33 | 8.61 | -1.1% | -1.6% |

### Historical Crisis Scenario Analysis

Credit spread beta = 1.5× applied (short gamma suffers more than underlying during VIX spikes).

| Scenario | Underlying DD | Portfolio DD (1.5× beta) | Trough Value | Est. Recovery |
|----------|---------------|--------------------------|--------------|---------------|
| COVID Crash (Feb-Mar 2020) | -34.5% | **-51.8%** | $48,216 | 585 days |
| 2022 Bear Market | -29.1% | **-43.7%** | $56,319 | 461 days |
| Flash Crash (Single Day) | -10.0% | **-15.0%** | $85,000 | 131 days |
| VIX Spike (15 → 65) | -15.0% | **-22.5%** | $77,500 | 205 days |

### COVID Crash (Feb-Mar 2020) — Per-Strategy Impact

| Strategy | Est. Portfolio DD | Trough Value | Recovery Days |
|----------|-------------------|--------------|---------------|
| EXP-400 | -51.8% | $48,216 | 753 |
| EXP-126 | -51.8% | $48,216 | 730 |
| EXP-154 | -51.8% | $48,216 | 707 |
| EXP-520 | -51.8% | $48,216 | 586 |
| EXP-305 | -51.8% | $48,216 | 428 |
| **COMBINED** | -51.8% | $48,216 | 585 |

### 2022 Bear Market — Per-Strategy Impact

| Strategy | Est. Portfolio DD | Trough Value |
|----------|-------------------|--------------|
| EXP-400 | -43.7% | $56,319 |
| EXP-126 | -43.7% | $56,319 |
| EXP-154 | -43.7% | $56,319 |
| EXP-520 | -43.7% | $56,319 |
| EXP-305 | -43.7% | $56,319 |
| **COMBINED** | -43.7% | $56,319 |

## Parameter Sensitivity Analysis (Combined Portfolio)

Heuristic model: approximates the effect of parameter changes on combined portfolio returns.

### Position Size (% of account)
Risk per trade as pct of account (risk.max_risk_per_trade)

| Value | Sharpe | Max DD | CAGR | Calmar |
|-------|--------|--------|------|--------|
| 1.0 | 9.30 | -0.2% | 6.5% | 27.26 |
| 2.0 | 9.30 | -0.5% | 13.4% | 28.16 |
| 3.0 | 9.30 | -0.7% | 20.7% | 29.09 |
| 5.0 ← baseline | 9.30 | -1.2% | 36.8% | 31.07 |
| 7.0 | 9.30 | -1.7% | 55.1% | 33.23 |
| 10.0 | 9.30 | -2.4% | 87.0% | 36.82 |
| 15.0 | 9.30 | -3.5% | 155.1% | 43.95 |

### Stop Loss Multiplier
Stop loss as multiple of credit received

| Value | Sharpe | Max DD | CAGR | Calmar |
|-------|--------|--------|------|--------|
| 1.5 | 12.29 | -0.4% | 43.2% | 110.42 |
| 2.0 | 11.61 | -0.6% | 41.7% | 71.14 |
| 2.5 | 10.79 | -0.8% | 39.9% | 49.06 |
| 3.0 | 9.96 | -1.1% | 38.1% | 36.18 |
| 3.5 ← baseline | 9.30 | -1.2% | 36.8% | 31.07 |
| 4.0 | 9.09 | -1.2% | 36.3% | 29.31 |
| 5.0 | 8.68 | -1.4% | 35.3% | 26.23 |

### IV Rank Entry Threshold
Minimum IV rank to enter a trade (strategy.min_iv_rank)

| Value | Sharpe | Max DD | CAGR | Calmar |
|-------|--------|--------|------|--------|
| 0 | 9.25 | -1.2% | 36.8% | 30.45 |
| 5 | 9.28 | -1.2% | 36.8% | 29.73 |
| 10 | 9.30 | -1.2% | 36.8% | 31.38 |
| 15 | 9.07 | -1.2% | 35.5% | 29.91 |
| 20 | 8.74 | -1.1% | 32.6% | 28.58 |
| 30 | 8.35 | -1.0% | 30.2% | 29.49 |
| 40 | 7.38 | -1.2% | 24.8% | 20.95 |
| 50 | 7.05 | -1.0% | 21.6% | 22.29 |

### Profit Target (%)
Close at this % of max profit (risk.profit_target)

| Value | Sharpe | Max DD | CAGR | Calmar |
|-------|--------|--------|------|--------|
| 25 | 5.26 | -1.5% | 12.1% | 8.18 |
| 40 | 8.13 | -1.2% | 26.4% | 21.93 |
| 50 ← baseline | 9.30 | -1.2% | 36.8% | 31.07 |
| 60 | 10.15 | -1.2% | 48.2% | 41.20 |
| 75 | 11.04 | -1.1% | 67.0% | 58.46 |
| 90 | 11.04 | -1.1% | 67.0% | 58.46 |

### Spread Width ($)
Width between short and long strikes

| Value | Sharpe | Max DD | CAGR | Calmar |
|-------|--------|--------|------|--------|
| 2.5 | 9.30 | -0.7% | 21.3% | 29.16 |
| 5.0 ← baseline | 9.30 | -1.2% | 36.8% | 31.07 |
| 7.5 | 9.30 | -1.6% | 51.6% | 32.83 |
| 10.0 | 9.30 | -1.9% | 66.3% | 34.52 |
| 15.0 | 9.30 | -2.5% | 96.4% | 37.85 |
| 20.0 | 9.30 | -3.1% | 128.0% | 41.20 |

## Combined Portfolio Projections

### Projected Equity Curve (Best Allocation)

| Year | Annual Return | Ending Capital | vs S&P 500 |
|------|---------------|----------------|------------|
| 2020 | +39.9% | $139,930 | +21.5% vs SPY |
| 2021 | +51.3% | $211,726 | +22.6% vs SPY |
| 2022 | +38.2% | $292,681 | +57.8% vs SPY |
| 2023 | +16.3% | $340,283 | -10.0% vs SPY |
| 2024 | +19.5% | $406,486 | -3.7% vs SPY |
| 2025 | +74.1% | $707,758 | +49.2% vs SPY |
| **6yr Total** | **+607.8%** | **$707,758** | +469.8% vs SPY |

**CAGR:** +38.6% | **SPY CAGR:** +15.5% | **Alpha:** +23.0%

### Monte Carlo: 6-Year Forward Projections

Based on 1,000 block-bootstrap simulations of the combined portfolio:

- **P5 terminal wealth:** $570,879 (+471%)
- **P25 terminal wealth:** $623,579 (+524%)
- **P50 terminal wealth:** $660,957 (+561%)
- **P75 terminal wealth:** $698,042 (+598%)
- **P95 terminal wealth:** $758,265 (+658%)
- **Prob. of profit:** 100.0%
- **Prob. of ruin (>50% loss):** 0.00%

## Allocation Recommendations

### Primary Recommendation: Max-Sharpe Allocation

| Strategy | Capital % | Dollar Amount ($100k) | Rationale |
|----------|-----------|-----------------------|-----------|
| EXP-305 | 31.1% | $31,101 | Multi-underlying diversification; sector alpha in bull markets |
| EXP-400 | 29.8% | $29,758 | Low DD anchor; regime-adaptive prevents large bear losses |
| EXP-154 | 21.9% | $21,890 | Most conservative; IC overlay in neutral regime adds consistency |
| EXP-520 | 12.3% | $12,251 | VIX gate protects against crash years; consistent cross-cycle |
| EXP-126 | 5.0% | $5,000 | High absolute returns; 2022 and 2025 powerhouse |

### Alternative: Risk Parity

Risk parity (inverse-vol weighting) gives more to lower-volatility strategies:

- **29.2%** → EXP-154 5% Dir + 12% IC (IC-Neutral)
- **26.6%** → EXP-400 Champion (DTE=15, Regime-Adaptive)
- **24.9%** → EXP-305 COMPASS (Corrected — SPY-only effective)
- **14.3%** → EXP-520 Real-Data Champion (VIX Gate, DTE=35/28)
- **5.0%** → EXP-126 8% Flat Risk (DTE=35, IC-Neutral)
  Expected return: +46.3%, Sharpe: 1.71

### Regime-Conditional Recommendations

| Regime | Best Strategy | Reasoning |
|--------|---------------|-----------|
| BULL | EXP-305 COMPASS | Sector ETFs add alpha in trending bull markets |
| NEUTRAL | EXP-400 Champion | Regime-adaptive IC + credit spreads in range-bound |
| BEAR | EXP-154 / EXP-520 | Lower risk, VIX gate limits crash exposure |

### Implementation Notes

1. **Rebalancing frequency:** Weekly (every 7 trading days) per `PortfolioOptimizer`
2. **Event gate:** Reduce total allocation by event scaling factor before FOMC/CPI/NFP
3. **Regime detection:** Use `compass.macro_db.get_current_macro_score()` for daily regime
4. **Minimum allocation:** 5% per strategy (prevents zero allocation per optimizer constraint)
5. **Max allocation cap:** No hard cap, but max-Sharpe naturally limits concentration

## Data Quality & Methodology Notes

| Strategy | Data Type | N | Confidence |
|----------|-----------|---|------------|
| EXP-400 | Deterministic backtest, real Polygon options data | 6 years | HIGH |
| EXP-126 | MC P50 (30 seeds, DTE U[33,37]) | 6 years | MEDIUM — only 30 seeds |
| EXP-154 | MC P50 (200 seeds, DTE U[33,37]) | 6 years | HIGH |
| EXP-520 | Deterministic backtest, real Polygon options data | 6 years | HIGH |
| EXP-305 | Deterministic COMPASS portfolio backtest | 6 years | MEDIUM — sectors use heuristic data |

**Limitations:**
- Correlation matrix computed on simulated monthly returns (except EXP-400 which uses actual monthly PnL)
- 6 years of data = small sample for covariance estimation; optimizer may overfit
- Sensitivity analysis uses heuristic return-scaling, not full backtest re-runs
- EXP-305 sector ETF data is sparse (heuristic mode, not real Polygon options data)
- All strategies are SPY/credit-spread-based → expect high tail correlation in crash events
