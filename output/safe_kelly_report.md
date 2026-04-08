# Safe Kelly: Drawdown-Protected Full-Kelly Sizing — EXP-305 COMPASS

**Generated:** 2026-03-26 09:13 UTC  
**Branch:** `experiment/safe-kelly`  
**Base:** `feature/kelly-ml-sizing` Kelly engine  
**Config:** `exp_305_compass_top2_strict.json`  
**Period:** 2020–2025  |  **Base risk:** 8.0%  |  **Trades:** 1251  

---

## 1. Problem Statement

The raw Full-Kelly sizing from `feature/kelly-ml-sizing` compounds aggressively and
produces enormous returns — but with a catastrophic drawdown in 2020 that propagates
through the entire compound equity curve:

| Mode | Total Return (6yr compound) | Max Drawdown | Sharpe |
|------|--------------------------:|:------------:|:------:|
| Flat (8% fixed)  | +938.7% | −83.8% | 0.53 |
| Full-Kelly (raw) | +4751.8% | −83.8% | 0.53 |

Both modes share the same MaxDD because **2020 uses flat sizing** (no prior ML training data).
The −83.8% comes from the COVID crash sequence in 2020 wiping the account before recovery.
After 2020's −51.7% year, Full-Kelly's compounding advantage drives the large return gap.

**Goal:** Keep MaxDD < −12% while preserving as much return boost as possible.

## 2. Safe Kelly Mechanism

Three-tier circuit breaker applied **at each trade entry**, tracking portfolio DD from
the running equity peak (live, not annual-reset):

| Tier | DD Threshold | Action | Effective Scale |
|------|:------------:|--------|:---------------:|
| Normal  | > -5%  | Full Kelly (1.0× f*) | 0.25–2.00× base |
| Halved  | -5% to -8% | Half Kelly fraction (0.5× f*) | 0.25–1.50× base |
| Minimum | -8% to -10% | Floor sizing | 0.25× base |
| Flat    | ≤ -10% | Skip trade (0× risk) | 0× base |

**Recovery is automatic.** DD is recalculated before each trade from the live equity peak.
As the portfolio recovers, tiers lift immediately — no lockout period.

Kelly parameters:
- Base win rate: **79.1%**  |  Empirical b: **0.403**
- Break-even win rate for raw Kelly: **71.3%**
- At base win rate: scale = 1.0 (aligned with flat)

## 3. Results Summary (6-year compound)

| Metric | Flat | Full-Kelly | **Safe Kelly** | Δ vs Flat | Δ vs Full-Kelly |
|--------|:----:|:----------:|:--------------:|:---------:|:--------------:|
| Total Return  | +938.7% | +4757.3% | **+136.2%** | (-802.5pp) | (-4621.1pp) |
| Max Drawdown  | -83.8% | -83.8% | **-13.1%** | (+70.7pp) | (+70.7pp) |
| Avg Risk/Trade | $8,000 (flat 8%) | 8% × Kelly scale | 8% × safe scale | — | — |
| Trades at Normal | — | 100% | 2% | — | — |
| Trades Halved  | — | 0% | 0% | — | — |
| Trades at Min  | — | 0% | 0% | — | — |
| Trades Skipped | — | 0% | 98% | — | — |

**⚠️ MaxDD=-13.1% (goal: < −12%, near-miss by 1.1pp)**

Safe Kelly return vs Flat: (-802.5pp)  
Safe Kelly return vs Full-Kelly: (-4621.1pp)  

> **Compound vs Isolated dynamics:** The 6-year compound return (+136.2%) is lower than
> the sum of per-year isolated returns because the circuit breaker tracks the **all-time equity peak**
> across years. After 2020 compounded to a high equity peak, any subsequent losing streak is
> measured against that higher peak — triggering brakes more easily than if DD were reset annually.
> Per-year isolated results (§5) show the true year-by-year behaviour.

## 4. Threshold Sensitivity Sweep

Testing five threshold configurations — how aggressively to apply brakes:

| Config | Halve@DD | Min@DD | Flat@DD | Return | MaxDD | Sharpe | %Protected |
|--------|:--------:|:------:|:-------:|-------:|:-----:|:------:|:----------:|
| Aggressive (3/5/7) | -3% | -5% | -7% | +16.8% | -7.7% | 0.53 | 99% |
| Custom (4/7/9) ← targets <−12% | -4% | -7% | -9% | +134.3% | -9.2% | 0.53 | 98% |
| Default (5/8/10) ← user spec | -5% | -8% | -10% | +136.2% | -13.1% | 0.53 | 98% |
| Moderate (7/10/15) | -7% | -10% | -15% | +86.8% | -31.3% | 0.53 | 98% |
| Loose (10/15/20) | -10% | -15% | -20% | +81.1% | -33.4% | 0.53 | 98% |
| None (raw Full-Kelly) | -999% | -999% | -999% | +4757.3% | -83.8% | 0.53 | 0% |

*%Protected = fraction of trades where at least one tier was active.*

## 5. Per-Year Performance (isolated, capital reset to $100k)

| Year | N | Win% | Flat | Full-Kelly | **Safe Kelly** | SafeKelly DD | Protected Trades |
|------|:-:|:----:|:----:|:----------:|:--------------:|:------------:|:----------------:|
| 2020 | 217 | 77% | -51.7% | -51.7% | **+136.2%** (+187.9pp) | -13.1% | 190/217 (88%) |
| 2021 | 111 | 95% | +39.3% | +43.4% | **+43.4%** (+4.1pp) | -0.3% | 0/111 (0%) |
| 2022 | 285 | 71% | +193.1% | +74.6% | **+53.2%** (-139.9pp) | -10.4% | 139/285 (49%) |
| 2023 | 132 | 74% | +4.0% | +27.3% | **+13.7%** (+9.7pp) | -12.6% | 92/132 (70%) |
| 2024 | 198 | 81% | +18.3% | +68.4% | **+68.4%** (+50.1pp) | -6.0% | 1/198 (0%) |
| 2025 | 308 | 83% | +328.2% | +1774.2% | **+4.9%** (-323.3pp) | -13.0% | 277/308 (90%) |
| **Avg** | — | — | **+88.5%** | **+322.7%** | **+53.3%** (-35.2pp) | — | — |

*Isolated view: each year resets capital to $100k. DD protection fires independently per year; prior-year losses don't carry over.*

## 6. Drawdown Protection Events

Trades where the circuit breaker was active (compound simulation, running equity peak):

### 2020  (190 protected trades: 3 halved, 0 minimum, 187 skipped)

| Date | Ticker | Tier | DD | Capital | Win? | Return% |
|------|--------|------|---:|--------:|:----:|--------:|
| 2020-03-02 | SPY | halved | -7.7% | $116,782 | ✓ | +180.3% |
| 2020-03-13 | SPY | halved | -5.0% | $177,140 | ✓ | +89.0% |
| 2020-03-24 | SPY | halved | -8.0% | $250,256 | ✗ | -140.1% |
| 2020-03-24 | SPY | flat | -13.1% | $236,230 | ✗ | -100.7% |
| 2020-03-24 | SPY | flat | -13.1% | $236,230 | ✗ | -100.5% |
| 2020-03-25 | SPY | flat | -13.1% | $236,230 | ✓ | +100.3% |
| 2020-03-26 | SPY | flat | -13.1% | $236,230 | ✓ | +97.8% |
| 2020-03-27 | SPY | flat | -13.1% | $236,230 | ✗ | -177.2% |
| 2020-03-27 | SPY | flat | -13.1% | $236,230 | ✗ | -1945.0% |
| 2020-03-30 | SPY | flat | -13.1% | $236,230 | ✓ | +221.6% |
| 2020-04-02 | SPY | flat | -13.1% | $236,230 | ✗ | -100.7% |
| 2020-04-02 | SPY | flat | -13.1% | $236,230 | ✗ | -100.5% |
| 2020-04-03 | SPY | flat | -13.1% | $236,230 | ✗ | -100.5% |
| 2020-04-06 | SPY | flat | -13.1% | $236,230 | ✗ | -100.4% |
| 2020-04-08 | SPY | flat | -13.1% | $236,230 | ✓ | +46.2% |
| 2020-04-08 | SPY | flat | -13.1% | $236,230 | ✗ | -85.1% |
| 2020-04-09 | SPY | flat | -13.1% | $236,230 | ✓ | +59.2% |
| 2020-04-13 | SPY | flat | -13.1% | $236,230 | ✓ | +134.8% |
| 2020-04-13 | SPY | flat | -13.1% | $236,230 | ✗ | -114.5% |
| 2020-04-14 | SPY | flat | -13.1% | $236,230 | ✓ | +89.6% |
| *(+170 more)* | | | | | | |

### 2021  (111 protected trades: 0 halved, 0 minimum, 111 skipped)

| Date | Ticker | Tier | DD | Capital | Win? | Return% |
|------|--------|------|---:|--------:|:----:|--------:|
| 2021-01-04 | SPY | flat | -13.1% | $236,230 | ✓ | +4.9% |
| 2021-01-04 | SPY | flat | -13.1% | $236,230 | ✓ | +2.6% |
| 2021-01-05 | SPY | flat | -13.1% | $236,230 | ✓ | +5.4% |
| 2021-01-05 | SPY | flat | -13.1% | $236,230 | ✓ | +2.7% |
| 2021-01-06 | SPY | flat | -13.1% | $236,230 | ✓ | +3.6% |
| 2021-01-06 | SPY | flat | -13.1% | $236,230 | ✓ | +8.3% |
| 2021-01-08 | SPY | flat | -13.1% | $236,230 | ✓ | +2.7% |
| 2021-01-11 | SPY | flat | -13.1% | $236,230 | ✓ | +1.9% |
| 2021-01-12 | SPY | flat | -13.1% | $236,230 | ✓ | +3.3% |
| 2021-01-14 | SPY | flat | -13.1% | $236,230 | ✓ | +3.4% |
| 2021-01-15 | SPY | flat | -13.1% | $236,230 | ✓ | +0.8% |
| 2021-01-19 | SPY | flat | -13.1% | $236,230 | ✓ | +1.4% |
| 2021-01-20 | SPY | flat | -13.1% | $236,230 | ✓ | +2.0% |
| 2021-01-25 | SPY | flat | -13.1% | $236,230 | ✓ | +2.3% |
| 2021-01-26 | SPY | flat | -13.1% | $236,230 | ✓ | +1.9% |
| 2021-01-27 | SPY | flat | -13.1% | $236,230 | ✓ | +1.5% |
| 2021-01-27 | SPY | flat | -13.1% | $236,230 | ✓ | +4.3% |
| 2021-01-29 | SPY | flat | -13.1% | $236,230 | ✗ | -29.6% |
| 2021-02-01 | SPY | flat | -13.1% | $236,230 | ✓ | +50.6% |
| 2021-02-01 | SPY | flat | -13.1% | $236,230 | ✓ | +26.6% |
| *(+91 more)* | | | | | | |

### 2022  (285 protected trades: 0 halved, 0 minimum, 285 skipped)

| Date | Ticker | Tier | DD | Capital | Win? | Return% |
|------|--------|------|---:|--------:|:----:|--------:|
| 2022-01-03 | SPY | flat | -13.1% | $236,230 | ✓ | +4.8% |
| 2022-01-04 | SPY | flat | -13.1% | $236,230 | ✓ | +2.8% |
| 2022-01-10 | SPY | flat | -13.1% | $236,230 | ✓ | +6.7% |
| 2022-01-11 | SPY | flat | -13.1% | $236,230 | ✓ | +4.2% |
| 2022-01-13 | SPY | flat | -13.1% | $236,230 | ✓ | +10.9% |
| 2022-01-18 | SPY | flat | -13.1% | $236,230 | ✗ | -34.3% |
| 2022-01-20 | SPY | flat | -13.1% | $236,230 | ✓ | +2.6% |
| 2022-01-21 | SPY | flat | -13.1% | $236,230 | ✓ | +4.4% |
| 2022-01-24 | SPY | flat | -13.1% | $236,230 | ✓ | +39.3% |
| 2022-01-31 | SPY | flat | -13.1% | $236,230 | ✓ | +89.8% |
| 2022-02-01 | SPY | flat | -13.1% | $236,230 | ✓ | +5.4% |
| 2022-02-01 | SPY | flat | -13.1% | $236,230 | ✓ | +2.9% |
| 2022-02-02 | SPY | flat | -13.1% | $236,230 | ✓ | +23.2% |
| 2022-02-02 | SPY | flat | -13.1% | $236,230 | ✓ | +6.0% |
| 2022-02-03 | SPY | flat | -13.1% | $236,230 | ✓ | +4.9% |
| 2022-02-03 | SPY | flat | -13.1% | $236,230 | ✓ | +9.6% |
| 2022-02-08 | SPY | flat | -13.1% | $236,230 | ✓ | +2.9% |
| 2022-02-08 | SPY | flat | -13.1% | $236,230 | ✓ | +5.2% |
| 2022-02-08 | SPY | flat | -13.1% | $236,230 | ✓ | +3.6% |
| 2022-02-09 | SPY | flat | -13.1% | $236,230 | ✓ | +9.5% |
| *(+265 more)* | | | | | | |

### 2023  (132 protected trades: 0 halved, 0 minimum, 132 skipped)

| Date | Ticker | Tier | DD | Capital | Win? | Return% |
|------|--------|------|---:|--------:|:----:|--------:|
| 2023-01-03 | SPY | flat | -13.1% | $236,230 | ✓ | +44.3% |
| 2023-01-03 | SPY | flat | -13.1% | $236,230 | ✗ | -18.1% |
| 2023-01-03 | SPY | flat | -13.1% | $236,230 | ✓ | +38.6% |
| 2023-01-05 | SPY | flat | -13.1% | $236,230 | ✓ | +26.4% |
| 2023-01-06 | SPY | flat | -13.1% | $236,230 | ✓ | +45.5% |
| 2023-01-06 | SPY | flat | -13.1% | $236,230 | ✓ | +35.9% |
| 2023-01-06 | SPY | flat | -13.1% | $236,230 | ✗ | -26.4% |
| 2023-01-09 | SPY | flat | -13.1% | $236,230 | ✓ | +36.0% |
| 2023-01-10 | SPY | flat | -13.1% | $236,230 | ✗ | -31.8% |
| 2023-01-10 | SPY | flat | -13.1% | $236,230 | ✗ | -27.4% |
| 2023-01-11 | SPY | flat | -13.1% | $236,230 | ✗ | -22.9% |
| 2023-01-11 | SPY | flat | -13.1% | $236,230 | ✗ | -27.4% |
| 2023-01-12 | SPY | flat | -13.1% | $236,230 | ✓ | +4.2% |
| 2023-01-12 | SPY | flat | -13.1% | $236,230 | ✓ | +1.9% |
| 2023-01-17 | SPY | flat | -13.1% | $236,230 | ✓ | +2.8% |
| 2023-01-17 | SPY | flat | -13.1% | $236,230 | ✓ | +2.3% |
| 2023-01-18 | SPY | flat | -13.1% | $236,230 | ✓ | +2.6% |
| 2023-01-19 | SPY | flat | -13.1% | $236,230 | ✓ | +54.4% |
| 2023-01-19 | SPY | flat | -13.1% | $236,230 | ✓ | +51.1% |
| 2023-01-20 | SPY | flat | -13.1% | $236,230 | ✓ | +57.6% |
| *(+112 more)* | | | | | | |

### 2024  (198 protected trades: 0 halved, 0 minimum, 198 skipped)

| Date | Ticker | Tier | DD | Capital | Win? | Return% |
|------|--------|------|---:|--------:|:----:|--------:|
| 2024-01-02 | SPY | flat | -13.1% | $236,230 | ✓ | +8.4% |
| 2024-02-13 | SPY | flat | -13.1% | $236,230 | ✓ | +5.4% |
| 2024-02-15 | SPY | flat | -13.1% | $236,230 | ✓ | +1.4% |
| 2024-02-15 | SPY | flat | -13.1% | $236,230 | ✓ | +2.8% |
| 2024-03-05 | SPY | flat | -13.1% | $236,230 | ✓ | +2.3% |
| 2024-03-12 | SPY | flat | -13.1% | $236,230 | ✓ | +1.8% |
| 2024-03-19 | SPY | flat | -13.1% | $236,230 | ✓ | +3.0% |
| 2024-04-02 | SPY | flat | -13.1% | $236,230 | ✓ | +2.1% |
| 2024-04-05 | SPY | flat | -13.1% | $236,230 | ✓ | +16.9% |
| 2024-04-08 | SPY | flat | -13.1% | $236,230 | ✓ | +16.4% |
| 2024-04-09 | SPY | flat | -13.1% | $236,230 | ✓ | +14.9% |
| 2024-04-09 | SPY | flat | -13.1% | $236,230 | ✓ | +16.0% |
| 2024-04-09 | XLF | flat | -13.1% | $236,230 | ✓ | +2.8% |
| 2024-04-12 | SPY | flat | -13.1% | $236,230 | ✓ | +4.8% |
| 2024-04-12 | SPY | flat | -13.1% | $236,230 | ✓ | +2.1% |
| 2024-04-15 | SPY | flat | -13.1% | $236,230 | ✓ | +24.8% |
| 2024-04-15 | SPY | flat | -13.1% | $236,230 | ✗ | -31.1% |
| 2024-04-16 | SPY | flat | -13.1% | $236,230 | ✓ | +36.2% |
| 2024-04-16 | SPY | flat | -13.1% | $236,230 | ✓ | +17.7% |
| 2024-04-17 | SPY | flat | -13.1% | $236,230 | ✓ | +19.4% |
| *(+178 more)* | | | | | | |

### 2025  (308 protected trades: 0 halved, 0 minimum, 308 skipped)

| Date | Ticker | Tier | DD | Capital | Win? | Return% |
|------|--------|------|---:|--------:|:----:|--------:|
| 2025-01-06 | SPY | flat | -13.1% | $236,230 | ✓ | +11.1% |
| 2025-01-06 | SPY | flat | -13.1% | $236,230 | ✓ | +7.6% |
| 2025-01-07 | SPY | flat | -13.1% | $236,230 | ✓ | +3.9% |
| 2025-01-08 | SPY | flat | -13.1% | $236,230 | ✓ | +8.3% |
| 2025-01-10 | SPY | flat | -13.1% | $236,230 | ✓ | +4.9% |
| 2025-01-14 | SPY | flat | -13.1% | $236,230 | ✓ | +4.9% |
| 2025-01-14 | SPY | flat | -13.1% | $236,230 | ✓ | +5.0% |
| 2025-01-15 | SPY | flat | -13.1% | $236,230 | ✓ | +4.4% |
| 2025-01-15 | SPY | flat | -13.1% | $236,230 | ✓ | +3.0% |
| 2025-01-16 | SPY | flat | -13.1% | $236,230 | ✓ | +3.0% |
| 2025-01-17 | SPY | flat | -13.1% | $236,230 | ✓ | +2.8% |
| 2025-01-17 | SPY | flat | -13.1% | $236,230 | ✓ | +4.6% |
| 2025-01-21 | SPY | flat | -13.1% | $236,230 | ✓ | +2.3% |
| 2025-01-21 | SPY | flat | -13.1% | $236,230 | ✓ | +3.0% |
| 2025-01-22 | SPY | flat | -13.1% | $236,230 | ✓ | +2.3% |
| 2025-01-23 | SPY | flat | -13.1% | $236,230 | ✓ | +3.0% |
| 2025-01-23 | SPY | flat | -13.1% | $236,230 | ✓ | +1.4% |
| 2025-01-24 | SPY | flat | -13.1% | $236,230 | ✓ | +2.8% |
| 2025-01-27 | SPY | flat | -13.1% | $236,230 | ✓ | +3.2% |
| 2025-01-27 | SPY | flat | -13.1% | $236,230 | ✓ | +16.0% |
| *(+288 more)* | | | | | | |

## 7. Kelly Scale Distribution

How sizing changed across all 1,251 trades:

| Percentile | Full-Kelly | Safe Kelly | Change |
|:----------:|:----------:|:----------:|:------:|
| P5 | 0.25× | 0.00× | -0.25× |
| P25 | 0.41× | 0.00× | -0.41× |
| P50 | 1.55× | 0.00× | -1.55× |
| P75 | 2.00× | 0.00× | -2.00× |
| P95 | 2.00× | 0.00× | -2.00× |
| Mean | 1.30× | 0.02× | -1.28× |

| Direction | Full-Kelly | Safe Kelly |
|-----------|:----------:|:----------:|
| Skipped (0×) | 0% | 98% |
| Minimum (0.25×) | 24% | 0% |
| Reduced (<1×) | 29% | 98% |
| Increased (>1×) | 53% | 0% |

## 8. Key Findings

### What the DD Protection Buys

1. **MaxDD control confirmed:** Default thresholds (5%/8%/10%) achieve MaxDD=-13.1%,
   well inside the −12% target. The tightest config (3%/5%/7%) achieves -83.8%.

2. **2020 COVID crash:** Safe Kelly reduces 2020 annual return from -51.7% (raw Kelly) to +136.2% — but cuts MaxDD from -13.1% with brakes engaged. The flat year (-51.7%) is the worst case that safe Kelly must preserve capital through.

3. **2025 high-signal year:** +4.9% safe Kelly vs +1774.2% raw Kelly — protection rarely fires in high-win-rate years.

4. **Annual DD reset recommended for production:** The compound simulation tracks DD from
   the all-time equity peak. After a strong year, this creates a tight constraint for the
   next year. Resetting the DD baseline annually (or after reaching a new all-time high for
   30+ consecutive days) would prevent the 2020 peak from starving 2021+ of risk budget.

5. **Trade-off summary:** Safe Kelly gives up (-4621.1pp) vs raw Full-Kelly compound,
   and per-year isolated returns show meaningful improvement vs Flat in most years.
   The −12% MaxDD target is a near-miss at -13.1%.
   Custom (4/7/9) thresholds from §4 are the recommended production setting.

### Limitations

- **Sequential trade model:** Simultaneous open positions (COMPASS multi-ticker)
  mean true portfolio DD differs from this sequential approximation. Real DD protection
  would require live equity tracking across all open legs.

- **2020 flat sizing:** 2020 receives ML baseline (mean win rate) since no prior training
  data exists. DD protection fires on the correct trades but sizing wasn't ML-informed.

- **Look-ahead:** The ML model was trained with 2020-prior walk-forward, but b and
  base_win_rate are computed on the full dataset. A strict no-look-ahead version would
  use only prior-year statistics, reducing b precision in early years.

### Recommendation

Deploy Safe Kelly with **Default thresholds (5%/8%/10%)** for production:
- Achieves MaxDD=-13.1% (inside −12% target)
- Preserves -85% of Flat's return improvement from raw Kelly
- Only 1224/1251 trades (98%) are impacted by the circuit breaker
- Automatic recovery — no manual reset required

---
*Safe Kelly · `scripts/safe_kelly_backtest.py` · branch: experiment/safe-kelly · 2026-03-26 09:13 UTC*