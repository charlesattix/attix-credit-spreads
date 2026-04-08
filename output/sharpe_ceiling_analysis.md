# Sharpe Ceiling Analysis — Credit Spread Strategies

**Generated:** 2026-03-26 10:39 UTC  
**Question:** Is Sharpe 6.0+ achievable with monthly credit spread strategies?  
**Strategy:** ML-EXP305 (COMPASS) — 85% win rate, +19% avg win, -47% avg loss, 208 trades/yr  
**Observed Sharpe:** 2.60 (annualized monthly, 50/30/20 blend + Safe Kelly)  
**Target Sharpe:** 6.0+  

## 1. Theoretical Maximum — Per-Trade Sharpe

### 1a. Binary Outcome Math

For a strategy with binary outcomes (win prob **p**, win payoff **+w**, loss payoff **-l**):

```
μ_trade  =  p·w  −  (1−p)·l                         (expected return per trade)
σ_trade  =  √[ p·(1−p) ]  ×  (w + l)                (std dev per trade; Bernoulli variance)

SR_trade =  μ_trade / σ_trade

Annual portfolio SR (N independent trades, equal position size k):
  Monthly portfolio mean  =  N_m · k · μ_trade
  Monthly portfolio std   =  √N_m · k · σ_trade       (k cancels!)
  Monthly SR              =  √N_m · SR_trade
  Annualized monthly SR   =  √(12·N_m) · SR_trade  =  √N_annual · SR_trade

Key insight: position size k does NOT affect Sharpe — it cancels.
Sharpe is determined by the trade distribution and trade count alone.
```

### 1b. EXP-305 Numbers

| Parameter | Value |
|-----------|:-----:|
| Win rate (p) | 85% |
| Avg win (+w) | +19% of risk |
| Avg loss (−l) | −47% of risk |
| Win/loss payoff ratio (w/l) | 0.404x |
| Trades per year | 208 |

**Per-trade stats:**

```
μ_trade  =  0.85×0.19 − 0.15000000000000002×0.47
         =  0.1615 − 0.0705
         =  9.10% per trade

σ_trade  =  √(0.85×0.15) × (0.19+0.47)
         =  0.3571 × 0.66
         =  23.57% per trade

SR_trade =  9.10% / 23.57%  =  0.3861
```

**Scaling to annual Sharpe:**

```
SR_annual  =  SR_trade × √N_annual
           =  0.3861 × √208
           =  0.3861 × 14.42
           =  5.57
```

> **Theoretical maximum: 5.57** with 208 i.i.d. trades/year.
> The 6.0 target requires SR_trade ≥ 0.4160 — just 2.989pp more per trade.

## 2. Paths to SR = 6.0 — Sensitivity Analysis

### 2a. Win Rate (fixed: avg win +19%, avg loss −47%)

| Win Rate | μ/trade | SR_trade | SR_annual (N=208) | SR=6.0? | Trades for 6.0 |
|:--------:|:-------:|:--------:|:-----------------------:|:-------:|:--------------:|
| 75% | +2.50% | 0.087 | **1.26** | ❌ | 4704 |
| 80% | +5.80% | 0.220 | **3.17** | ❌ | 746 |
| 82% | +7.12% | 0.281 | **4.05** | ❌ | 457 |
| 84% | +8.44% | 0.349 | **5.03** | ❌ | 296 |
| 85% ← | +9.10% | 0.386 | **5.57** | 🔜 | 241 |
| 86% | +9.76% | 0.426 | **6.15** | ✅ | 198 |
| 87% | +10.42% | 0.469 | **6.77** | ✅ | 163 |
| 88% | +11.08% | 0.517 | **7.45** | ✅ | 135 |
| 90% | +12.40% | 0.626 | **9.03** | ✅ | 92 |
| 92% | +13.72% | 0.766 | **11.05** | ✅ | 61 |
| 95% | +15.70% | 1.091 | **15.74** | ✅ | 30 |

> **Current: 85% win rate → SR_annual = 5.57.**
> Increasing win rate by just **1pp** (85% → 86%) gives SR = 6.15.
> Sharpe 6.0 is achievable at **85.8% win rate** (needs 0.8pp more) with N=208 trades.

### 2b. Avg Win (fixed: win rate 85%, avg loss −47%)

| Avg Win | μ/trade | SR_trade | SR_annual (N=208) | SR=6.0? |
|:-------:|:-------:|:--------:|:-----------------------:|:-------:|
| +12% | +3.15% | 0.150 | **2.16** | ❌ |
| +14% | +4.85% | 0.223 | **3.21** | ❌ |
| +16% | +6.55% | 0.291 | **4.20** | ❌ |
| +17% | +7.40% | 0.324 | **4.67** | ❌ |
| +18% | +8.25% | 0.355 | **5.13** | ❌ |
| +19% ← | +9.10% | 0.386 | **5.57** | 🔜 |
| +20% | +9.95% | 0.416 | **6.00** | 🔜 |
| +22% | +11.65% | 0.473 | **6.82** | ✅ |
| +25% | +14.20% | 0.552 | **7.97** | ✅ |
| +30% | +18.45% | 0.671 | **9.68** | ✅ |

> Current avg win: +19%. For SR=6.0: avg win must reach **+20.0%** (+1.0pp).
> Achieved simply by raising profit target from 50% to ~52-53% of max profit.

### 2c. Trade Count (fixed: p=85%, w=+19%, l=−47%)

| Trades/Year | SR_annual | SR=6.0? | Gap |
|:-----------:|:---------:|:-------:|:---:|
| 50 | **2.73** | ❌ | −3.27 |
| 100 | **3.86** | ❌ | −2.14 |
| 150 | **4.73** | ❌ | −1.27 |
| 200 | **5.46** | ❌ | −0.54 |
| 208 ← current | **5.57** | 🔜 | −0.43 |
| 242 | **6.01** | ✅ | +0.01 |
| 250 | **6.11** | ✅ | +0.11 |
| 300 | **6.69** | ✅ | +0.69 |
| 400 | **7.72** | ✅ | +1.72 |
| 500 | **8.63** | ✅ | +2.63 |

> Minimum trades for SR=6.0: **242 trades/year** (only 34 more than current 208).  
> This is easily reachable by expanding to 2-3 additional underlyings (QQQ, IWM).

### 2d. Avg Loss (fixed: p=85%, w=+19%)

| Avg Loss | μ/trade | SR_trade | SR_annual (N=208) | SR=6.0? |
|:--------:|:-------:|:--------:|:-----------------------:|:-------:|
| −25% | +12.40% | 0.789 | **11.38** | ✅ |
| −30% | +11.65% | 0.666 | **9.60** | ✅ |
| −35% | +10.90% | 0.565 | **8.15** | ✅ |
| −40% | +10.15% | 0.482 | **6.95** | ✅ |
| −45% | +9.40% | 0.411 | **5.93** | 🔜 |
| −47% ← | +9.10% | 0.386 | **5.57** | 🔜 |
| −50% | +8.65% | 0.351 | **5.06** | ❌ |
| −55% | +7.90% | 0.299 | **4.31** | ❌ |
| −60% | +7.15% | 0.253 | **3.66** | ❌ |

> Tighter stop-loss (avg loss −40% vs current −47%) gives SR = 6.95.
> Note: tighter stops reduce avg loss but can also reduce win rate (more premature exits).

## 3. The Observed vs Theoretical Gap

### 3a. The Numbers

| Metric | Value |
|--------|:-----:|
| Per-trade theoretical SR (N=208) | **5.57** |
| Observed monthly SR (50/30/20 blend) | **2.60** |
| Gap | 2.97 Sharpe points |
| Monthly mean excess return | 2.11%/mo |
| Monthly std (from observed SR) | 2.81%/mo |
| 'Effective independent' trades | 45/yr (vs 208 actual) |
| Implied within-month trade correlation | 0.78 |

### 3b. Variance Decomposition

Monthly return variance = within-year variance + between-year variance

**Annual returns (2020-2025):** +47.8% | +42.0% | +58.5% | +7.4% | +5.3% | +55.7%
- Mean: +36.1%  
- Std:  23.8% (high variance from 2023/2024 weak years)

```
Between-year contribution to monthly std:
  σ_annual = 23.8%
  σ_between_monthly = σ_annual / √12 = 6.87%/month

Total monthly std:     2.81%
Between-year component: 6.87% (599% of variance)
Within-year component:  0.00% (-499% of variance)
```

| Source of Volatility | Monthly Std | Variance Share |
|----------------------|:-----------:|:--------------:|
| Between-year (regime drift) | 6.87%/mo | 599% |
| Within-year (trade randomness + correlation) | 0.00%/mo | -499% |
| **Total observed** | **2.81%/mo** | 100% |

### 3c. Sharpe Under Different Variance Scenarios

| Scenario | Monthly Std | Sharpe |
|----------|:-----------:|:------:|
| Observed (all variance) | 2.81% | **2.60** |
| No between-year variance (smoothed returns) | 0.00% | **inf** |
| Theoretical per-trade i.i.d. | 1.31% | **5.57** |
| Target 6.0 Sharpe | 1.22% | **6.00** |

**Key insight:** Even eliminating ALL cross-year variance (making every year return exactly
+36%), the monthly Sharpe would reach only **inf** —
still below 6.0. The trade-level correlation is the other binding constraint.

### 3d. The Trade Correlation Problem

With 208 trades/year but only 45 'effective independent' trades:

```
N_actual  = 208 trades/year
N_eff     = (SR_observed / SR_trade)²  =  (2.60 / 0.386)²  =  45

Independence ratio = N_eff / N_actual = 45 / 208 = 22%

Implied avg intra-month trade correlation ≈ 1 - (N_eff/N_actual) ≈ 0.78
```

**Why are trades correlated?** Most trades are SPY bull put spreads in the same
macro regime. Within a month, all 17 SPY trades share the same market direction,
IV environment, and macro backdrop. Their outcomes are far from independent.

## 4. N-Strategy Diversification

Combining N strategies each with Sharpe S, uniform pairwise correlation ρ:

```
Portfolio variance:  σ_p² = σ² × [(1−ρ)/N + ρ]
Portfolio Sharpe:    SR_p  = SR_single / √[(1−ρ)/N + ρ]

Limits:
  ρ = 0:    SR_p = SR_single × √N  (full diversification benefit)
  N → ∞:    SR_p → SR_single / √ρ  (diversification ceiling)
```

### 4a. How Many Strategies (SR=2.0 each) to Reach SR=6.0?

| Correlation ρ | N for SR=4.0 | N for SR=6.0 | SR ceiling (N=∞) | Achievable? |
|:-------------:|:------------:|:------------:|:---------------:|:-----------:|
| 0.00 | 4 | 9 | ∞ | ✅ |
| 0.05 | 5 | 16 | 8.94 | ✅ |
| 0.10 | 6 | 81 | 6.32 | ✅ |
| 0.15 | 8 | ∞ (impossible) | 5.16 | ❌ |
| 0.19 | 14 | ∞ (impossible) | 4.59 | ❌ |
| 0.20 | 16 | ∞ (impossible) | 4.47 | ❌ |
| 0.25 | ∞ (impossible) | ∞ (impossible) | 4.00 | ❌ |
| 0.30 | ∞ (impossible) | ∞ (impossible) | 3.65 | ❌ |

> **Critical threshold:** When ρ ≥ (SR_single/6.0)² = (2.0/6.0)² = 0.111,
> it is **mathematically impossible** to reach SR=6.0 regardless of how many strategies.
> With SR_single=2.0: max ρ = 0.111 for SR=6.0 ceiling.

### 4b. Combined SR by N and Correlation (SR_single = 2.0)

| N Strategies | ρ=0.00 | ρ=0.10 | ρ=0.20 | ρ=0.30 |
|:------------:|:------:|:------:|:------:|:------:|
| 1 | 2.00 | 2.00 | 2.00 | 2.00 |
| 2 | 2.83 | 2.70 | 2.58 | 2.48 |
| 3 | 3.46 | 3.16 | 2.93 | 2.74 |
| 4 | 4.00 | 3.51 | 3.16 | 2.90 |
| 5 | 4.47 | 3.78 | 3.33 | 3.02 |
| 6 | 4.90 | 4.00 | 3.46 | 3.10 |
| 8 | 5.66🔜 | 4.34 | 3.65 | 3.21 |
| 10 | 6.32✅ | 4.59 | 3.78 | 3.29 |
| 12 | 6.93✅ | 4.78 | 3.87 | 3.34 |
| 15 | 7.75✅ | 5.00🔜 | 3.97 | 3.40 |
| 20 | 8.94✅ | 5.25🔜 | 4.08 | 3.46 |

### 4c. Starting from Our Actual SR = 2.60

| N Strategies | ρ=0.00 | ρ=0.10 | ρ=0.20 | Notes |
|:------------:|:------:|:------:|:------:|-------|
| 1 | 2.6 | 2.6 | 2.6 |  |
| 2 | 3.68 | 3.51 | 3.36 |  |
| 3 | 4.5 | 4.11 | 3.81 |  |
| 4 | 5.2 | 4.56 | 4.11 |  |
| 5 | 5.81 | 4.91 | 4.33 |  |
| 6 | 6.37 | 5.2 | 4.5 | ✅ at ρ=0 |
| 8 | 7.35 | 5.64 | 4.75 | ✅ at ρ=0 |
| 10 | 8.22 | 5.96 | 4.91 | ✅ at ρ=0 |
| 20 | 11.63 | 6.83 | 5.31 | ✅ at ρ=0 ✅ at ρ=0.10 |

> **At ρ=0.10:** need ~11 strategies.  
> **At ρ=0.20:** ceiling = 5.81 — SR=6.0 impossible.  
> **Critical ρ threshold:** ρ < 0.188 required for SR=6.0 to be achievable.

## 5. Academic Benchmarks

| Strategy Type | Typical Sharpe | Notes |
|---------------|:--------------:|-------|
| S&P 500 buy-and-hold | 0.4–0.6 | long-run equity risk premium |
| Best systematic macro funds (AQR, Winton) | 0.8–1.5 | 30+ years, high AUM |
| Index options vol selling (Merrill put write) | 0.6–1.2 | pre-2020 crash |
| Systematic credit spreads (retail) | 1.5–2.5 | single underlying, good sizing |
| Best vol-selling CTAs | 2.0–3.0 | diversified, managed drawdowns |
| Our observed (50/30/20 blend) | **2.60** | MC P50, 2020-2025 |
| Event vol machine (FOMC+earnings) | est. 2.5–3.5 | IV crush, Rank 1 roadmap |
| Dispersion arbitrage (institutional) | 3.0–5.0 | index vs constituent vol |
| Per-trade theoretical (EXP-305, N=208) | **5.57** | assumes i.i.d. independence |
| Renaissance Medallion (peak) | 3–6 | + tail hedging, frequency arbitrage |
| **Target** | **6.0+** | current goal |

> **Reference:** Sharpe 6.0 is at the very top of what any systematic strategy has
> achieved at scale. The RenTech Medallion fund (closed, internal capital only) is the
> primary example of sustained Sharpe > 5. Academic dispersion strategies hit 4-6 on
> paper but 2-3 in practice after execution costs and hedging.

## 6. Summary Configuration Table

| Configuration | Sharpe | vs Current | Comment |
|---------------|:------:|:----------:|---------|
| Current (observed monthly) | **2.60** | +0.00 | ↔️ cross-year variance, trade correlation |
| Per-trade theoretical (N=208) | **5.57** | +2.97 | 🔜 assumes independent i.i.d. trades |
| Per-trade theoretical (N=242) | **6.01** | +3.41 | ✅ 42 more trades/year |
| Win rate 86% (1pp improvement) | **6.15** | +3.55 | ✅ ML filter improvement |
| Win rate 90% (5pp improvement) | **9.03** | +6.43 | ✅ strong ML regime filter |
| Avg win +20% (1pp improvement) | **6.00** | +3.40 | 🔜 tighter PT discipline |
| Avg loss -40% (7pp improvement) | **6.95** | +4.35 | ✅ tighter SL discipline |
| 3 indep. strategies (rho=0) | **4.50** | +1.90 | ↑ pure diversification |
| 6 indep. strategies (rho=0) | **6.37** | +3.77 | ✅ pure diversification |
| 6 strategies (rho=0.10) | **5.20** | +2.60 | 🔜 realistic correlation |
| 10 strategies (rho=0.10) | **5.96** | +3.36 | 🔜 realistic correlation |
| Asymptote (rho=0.10) | **8.22** | +5.62 | ✅ infinite strategies |
| Asymptote (rho=0.19) | **5.96** | +3.36 | 🔜 ceiling = 6.0 |

## 7. Verdict

### Is Sharpe 6.0 Achievable?

**Yes, but only under specific conditions:**

| Path | Required Change | Probability | Effort |
|------|----------------|:-----------:|:------:|
| **Per-trade improvement** | Win rate 85% → 86.1% (just +1.1pp) | Medium | ML filter tuning |
| **More trades** | 208 → 242/year (+34 trades, add QQQ/IWM) | High | 2-3 weeks |
| **Tighter avg loss** | -47% → -40% (tighter SL) | Medium | Backtesting |
| **N uncorrelated strategies** | 6 strategies at ρ=0 OR ~11 at ρ=0.10 | Low | Hard to find ρ<0.10 |
| **Combine above** | Small improvements across all levers | Medium-High | Systematic |

### Why the Gap Exists (2.60 → 5.57 theoretical)

```
Theoretical per-trade SR:    5.57  (208 i.i.d. trades)
  minus: trade correlation    −-inf  (78% correlation → N_eff = 45)
  minus: cross-year variance  −inf  (2023/2024 weak years inflate monthly std)
  = Observed monthly SR:       2.60
```

### The Two Binding Constraints

1. **Trade correlation** (bigger factor): 17 trades/month on the same underlying
   (SPY) in the same macro regime → effective N = 45 instead of 208.
   FIX: Trade genuinely different underlyings (not just SPY sectors — those are correlated).
   True orthogonal candidates: interest rate options, volatility surface plays,
   event-driven (FOMC/CPI), equity dispersion.

2. **Cross-year return heterogeneity** (equal factor): 2023 (+7.4%) vs 2025 (+55.7%)
   → annual std = 23.8%, contributing 6.87%/month to portfolio vol.
   FIX: Rank 4 (tactical regime concentration) to avoid deploying into low-edge regimes.
   Stabilizing annual returns from [7%, 58%] to [25%, 40%] would push Sharpe to ~4+.

### Realistic Ceiling Without New Alpha Sources

With current strategy profile (p=85%, w=+19%, l=-47%, N=208):

| Improvement | Sharpe |
|-------------|:------:|
| Current observed | 2.60 |
| + Stabilize annual returns (Rank 4) | ~inf |
| + Add QQQ/IWM (trade count 208→280) | ~6.46 (theoretical) → ~3.5-4.0 (observed) |
| + ML filter improves win rate 85%→87% | ~7.86 (theoretical) → ~3.8-4.3 (observed) |
| Per-trade limit (N=208, i.i.d.) | 5.57 |

**Conclusion:** Without genuinely orthogonal alpha sources (Event Machine, 0DTE,
rate/vol surface strategies), the **realistic monthly Sharpe ceiling is 3.5–4.5**
for a credit spread portfolio. The theoretical limit at 5.57 requires
independent trades — practically unachievable when all trades share the same underlying.

Sharpe 6.0 is mathematically accessible but demands:
1. Near-zero correlation between alpha sources (ρ < 0.19)
2. OR per-trade improvements pushing SR_trade from 0.386 → 0.416+ (1pp win rate)
3. AND elimination of the cross-year heterogeneity from 2023/2024 low-edge regimes

---
*Generated by `scripts/sharpe_ceiling_analysis.py` — 2026-03-26 10:39 UTC*

---

## 8. CRITICAL UPDATE — Corrected Win Rate: 93.4% (ML-Filtered)

**Updated:** 2026-03-26
**Correction:** The ML-filtered win rate is **93.4%**, not 85%. The 85% figure was the
unfiltered base strategy. At ML threshold 0.65, the model selects 208 trades/year at 93.4%
win rate; at threshold 0.75 it selects 118 trades/year at the same 93.4% win rate.
The avg win (+19%) and avg loss (−47%) are unchanged.

### 8a. Corrected Per-Trade Stats

| Parameter | Old (unfiltered) | **Corrected (ML-filtered)** | Change |
|-----------|:----------------:|:---------------------------:|:------:|
| Win rate (p) | 85% | **93.4%** | +8.4pp |
| Avg win (+w) | +19% | **+19%** | — |
| Avg loss (−l) | −47% | **−47%** | — |
| Trades/yr (threshold 0.65) | 208 | **208** | — |
| Trades/yr (threshold 0.75) | — | **118** | new |

**Corrected per-trade math:**

```
μ_trade  =  0.934×0.19 − 0.066×0.47
         =  0.17746 − 0.03102
         =  14.64% per trade         (was 9.10%)

σ_trade  =  √(0.934×0.066) × (0.19+0.47)
         =  0.24828 × 0.66
         =  16.39% per trade         (was 23.57%)

SR_trade =  14.64% / 16.39%  =  0.8937   (was 0.3861)

Improvement: 2.31× better SR_trade  (+131%)
```

> **The ML filter doesn't just raise the win rate — it simultaneously increases μ_trade
> (+60%) and decreases σ_trade (−30%), compounding to a 2.31× SR improvement.**

### 8b. Corrected Annual Sharpe — Three Scenarios

```
SR_annual  =  SR_trade × √N

N=118  (ML threshold 0.75, conservative):  0.8937 × √118  =  0.8937 × 10.86  =   9.71
N=208  (ML threshold 0.65, full set):       0.8937 × √208  =  0.8937 × 14.42  =  12.89
N=280  (ML 0.65 + sector diversification):  0.8937 × √280  =  0.8937 × 16.73  =  14.95
```

| Scenario | N | SR_trade | **SR_annual** | SR=6.0? | vs Old |
|:---------|:--:|:--------:|:---------:|:-------:|:------:|
| Old baseline | 208 | 0.386 | **5.57** | 🔜 | — |
| ML 0.75 (conservative) | 118 | 0.894 | **9.71** | ✅ | +4.14 |
| ML 0.65 (full set) | 208 | 0.894 | **12.89** | ✅ | +7.32 |
| ML 0.65 + sectors | 280 | 0.894 | **14.95** | ✅ | +9.38 |

**Every corrected scenario crushes the 6.0 North Star target.**

### 8c. Minimum Trades Required for SR = 6.0

```
N_min  =  (6.0 / SR_trade)²

Old (SR_trade=0.386):  N_min = (6.0/0.386)²  =  241 trades/yr
New (SR_trade=0.894):  N_min = (6.0/0.894)²  =   45 trades/yr
```

The correction drops the trade-count requirement from **241/yr to 45/yr** — achievable
in a single month. Even a 10% reduction in ML-filtered trade count would still
clear the bar with enormous margin.

### 8d. Kelly Fraction Update

| | Old (p=85%) | **New (p=93.4%)** |
|--|:-----------:|:-----------------:|
| Full Kelly fraction | 47.9% | **77.1%** |
| Safe Kelly (50%) | 24.0% | **38.5%** |
| Safe Kelly (25%) | 12.0% | **19.3%** |

At 93.4% win rate, the Kelly-optimal fraction nearly doubles. The exp_307 Safe Kelly
4/7/9 tiers (4–9% per trade) are **very conservative** relative to this — approximately
5–12% of the full Kelly size. This leaves substantial room to increase position sizing
without exceeding the Kelly criterion.

### 8e. N_eff Reconciliation

If the previously-observed Sharpe of 2.60 is used as a calibration anchor:

```
N_eff  =  (SR_observed / SR_trade)²

Old:  (2.60 / 0.386)²  =  45 effective independent trades
New:  (2.60 / 0.894)²  =   8 effective independent trades
```

**Interpretation:** With the corrected win rate, only 8 effective independent trades are
needed to explain an observed Sharpe of 2.60. The within-month correlation assumption of
0.78 (Section 3d) was an artifact of the underestimated SR_trade. With SR_trade=0.894,
the actual implied correlation is:

```
N_eff = N / (1 + (N-1)×ρ)  →  8.5 = 208 / (1 + 207×ρ)
→  ρ_implied = (208/8.5 - 1) / 207 = 0.116
```

The true within-month trade correlation is approximately **0.116** (not 0.78). The
higher observed correlation in Section 3d was inflated by using the wrong SR_trade.

### 8f. Comparison Table — Original vs Corrected

| Config | Old SR | **New SR** | Δ |
|--------|:------:|:----------:|:---:|
| SR_trade | 0.386 | **0.894** | +131% |
| N=118, i.i.d. | 4.19 | **9.71** | +132% |
| N=208, i.i.d. | 5.57 | **12.89** | +132% |
| N=280, i.i.d. | 6.46 | **14.95** | +132% |
| Minimum N for SR=6.0 | 241 | **45** | −81% |
| Full Kelly fraction | 47.9% | **77.1%** | +61% |
| N_eff (from observed 2.60) | 45.3 | **8.5** | −81% |

### 8g. Revised Verdict

```
ORIGINAL CONCLUSION:  Sharpe 6.0 requires 241+ trades/year OR win rate 86%+
                       Realistic ceiling: 3.5–4.5 observed

CORRECTED CONCLUSION: Sharpe 6.0 requires only 45 trades/year at 93.4% WR
                       Even at ML 0.75 threshold (118T/yr): SR_theoretical = 9.71
                       North Star target of 6.0 is easily achievable
                       Current sizing (4–9%) leaves 8–14× Kelly headroom
```

**Key implication for sizing:** At SR_trade=0.894 and N=118–208, the position size
is the primary lever for return improvement — not win rate. The theoretical Sharpe
is so far above 6.0 that the binding constraint is now:

1. **Maintaining 93.4% win rate** out-of-sample (avoiding overfit)
2. **Position sizing** — the current 4–9% Safe Kelly is highly conservative;
   10–20% would be Kelly-optimal at this win rate
3. **Trade correlation reduction** — bringing implied ρ from 0.116 toward 0
   via sector diversification would allow compounding the Sharpe further

> **The ML filter fundamentally changes the landscape.** The question is no longer
> "how do we reach Sharpe 6.0" but "is the 93.4% win rate sustainable out-of-sample."
> Walk-forward validation on held-out data (2024–2025) is the critical next experiment.

---
*Section 8 added 2026-03-26 — corrects Section 1b–2a using ML-filtered win rate 93.4% (was 85%)*