# ML Filter Regime Analysis — EXP-700 Bear Call Crisis
**Generated:** 2026-03-30
**Analyst:** Claude (automated analysis of ensemble_model_20260324.joblib + 1667-trade cache)
**Status:** ⚠️ CRITICAL FINDING — Multiple compounding failures identified

---

## Executive Summary

EXP-700 has placed zero trades since 2026-03-26 because the ML ensemble rejects every bear call spread with probability ~0.22, well below the flat 0.65 threshold. This is not a temporary problem — **no bear call spread in the entire 1667-trade training dataset ever received a probability ≥ 0.65**. The threshold was calibrated exclusively on bull puts (97.9% pass rate) and iron condors (53.1% pass rate). Bear calls are a structural blind spot.

The situation is more complex than the original framing suggested. There are **four compounding problems**, each independently serious, that together make the current deployment scientifically unsound.

---

## Problem 1: Class Imbalance (as stated)

Training set composition:
| Type | Count | % of total | Base win rate |
|------|-------|-----------|--------------|
| iron_condor | 1,052 | 63.1% | 64.4% |
| bull_put_spread | 519 | 31.1% | 81.7% |
| **bear_call_spread** | **96** | **5.7%** | **39.6%** |

With only 96 bear call examples, the model's decision boundary is dominated by bull put and IC patterns. The bear call features (strategy_type_bear_call_spread=1.0, spread_type_call=1.0) are **4.3 standard deviations from training mean** — far OOD. The model has never learned "what makes a bear call good vs bad" at meaningful scale.

### Probability distributions by type (scored against live model)

| Type | p5 | p25 | median | p75 | p95 | mean | % ≥ 0.65 |
|------|----|-----|--------|-----|-----|------|----------|
| bull_put_spread | 0.759 | 0.815 | 0.835 | 0.853 | 0.864 | 0.826 | **97.9%** |
| iron_condor | 0.417 | 0.536 | 0.664 | 0.763 | 0.839 | 0.648 | 53.1% |
| **bear_call_spread** | **0.184** | **0.201** | **0.238** | **0.531** | **0.616** | **0.339** | **0.0%** |

The median bear call probability is 0.238. The current live rejection (prob ≈ 0.22) is slightly below the median. The current flat threshold of 0.65 is **impossible to clear for any bear call** — not just unlikely, structurally impossible given the model's learned boundaries.

---

## Problem 2: Market Context Features Are ALL Zero-Variance in Training (Critical)

**This is the most serious structural defect.** The backtest was run with `--skip-backtest` (cached trades), which means no live price data was available. The backtest script imputes **static defaults** for all market context features when price data is absent:

```
rsi_14             → 50.0 (constant for ALL 1667 trades)
vix                → 20.0 (constant)
vix_percentile_20d → 50.0 (constant)
vix_percentile_50d → 50.0 (constant)
momentum_5d_pct    → 0.0  (constant)
momentum_10d_pct   → 0.0  (constant)
dist_from_ma20_pct → 0.0  (constant)
dist_from_ma50_pct → 0.0  (constant)
dist_from_ma80_pct → 0.0  (constant)
dist_from_ma200_pct→ 0.0  (constant)
ma20_slope_ann_pct → 0.0  (constant)
ma50_slope_ann_pct → 0.0  (constant)
iv_rank            → 25.0 (constant)
regime             → "neutral" for ALL trades
```

The confirmed evidence: training `feature_stds` for these features are **exactly 0.000**. A feature with zero variance carries zero information to the classifier. The XGBoost/RF/ET ensemble simply cannot use them.

**The 15 most important market context features provided ZERO signal during training.**

When the live scanner now provides real values (RSI=28.5, VIX=31, momentum=-4%, MA50 distance=-6.5%), those values are compared against a training distribution where std=0. The scanner's drift warning system logs "0.0σ" because `(live - mean) / std = (31 - 20) / 0 = undefined → 0`. The OOD warnings for contracts (6.9σ) and strategy_type (4.3σ) ARE legitimate because those features had non-zero variance. But the critical market context warnings are **silently suppressed** by the zero-division guard.

**Bottom line: The model scored every single live bear call with identical probability (0.2226) regardless of market conditions because it cannot distinguish RSI=28 from RSI=70. The model is effectively blind to the market environment.**

---

## Problem 3: Zero Bear Calls in 2022 — The Most Relevant Analog Is Missing

Year-by-year breakdown of bear calls in the training data:

| Year | Count | Win Rate | Total PnL | Mean Prob |
|------|-------|----------|-----------|-----------|
| 2020 | 62 | 32.3% | -$24,221 | 0.326 |
| 2021 | 0 | — | $0 | — |
| 2022 | **0** | **—** | **$0** | **—** |
| 2023 | 0 | — | $0 | — |
| 2024 | 12 | 0.0% | -$40,673 | 0.266 |
| 2025 | 22 | 81.8% | +$28,370 | 0.413 |

**The 2022 bear market generated zero bear call trades in EXP-400's backtest.** This is because the combo regime detector requires all three signals unanimous for BEAR (price < MA200 AND RSI < 45 AND VIX structure bearish), and in 2022 SPY remained above MA200 for much of the drawdown. The current 2026 tariff selloff is the closest analog to 2022 — and there is **no historical training data for this regime type**.

The 96 bear call trades are concentrated in:
- **2020 COVID crash** (62 trades): 32.3% WR — poor, entered too early/late in volatility spike
- **2024** (12 trades): 0.0% WR — all losses, false bear signals in a bull year
- **2025** (22 trades): 81.8% WR — genuine bear regime reversions in late 2025

The model has never seen a prolonged bear market with bear calls. The current market environment (sustained SPY decline, RSI=28, VIX=31, tariff uncertainty) has no parallel in the training corpus.

---

## Problem 4: Bear Call Break-Even Analysis

For the EXP-400 params (credit ≈ 8.5% risk, spread_width=$12, SL=1.25x):
- Net credit per trade ≈ $500
- Stop-loss triggers at ≈ 1.25 × $500 = $625 max loss
- **Break-even win rate = $625 / ($625 + $500) = 55.6%**

The raw 39.6% bear call win rate is **below break-even**. The strategy loses money on bear calls in aggregate (total PnL = -$36,523 across all 96 trades). The 0.65 threshold blocking bear calls is accidentally doing the right thing — though for the wrong reason.

---

## Threshold Analysis

### Bear Call Threshold Curve

| Threshold | N kept | % kept | WR kept | WR rejected | PnL captured | Lift vs base |
|-----------|--------|--------|---------|-------------|-------------|--------------|
| 0.10 | 96 | 100% | 39.6% | — | -$36,523 | +0pp |
| 0.20 | 75 | 78% | 48.0% | 9.5% | (negative) | +8pp |
| 0.22 (live) | 56 | 58% | 55.4% | 17.5% | +$15,407 | +16pp |
| 0.25 | 46 | 48% | 67.4% | 14.0% | +$37,453 | +28pp |
| **0.30** | **35** | **37%** | **80.0%** | 16.4% | **+$44,173** | **+40pp** |
| **0.35** | **34** | **35%** | **82.4%** | 16.1% | **+$45,175** | **+43pp** |
| 0.40 | 33 | 34% | 81.8% | 17.5% | +$44,532 | +42pp |
| 0.50 | 30 | 31% | 83.3% | 19.7% | +$46,282 | +44pp |
| 0.55 | 21 | 22% | 90.5% | 25.3% | +$41,628 | +51pp |
| 0.60 | 9 | 9% | 100.0% | 33.3% | +$26,432 | +60pp |
| 0.65 | **0** | **0%** | **—** | 39.6% | **$0** | **—** |

**The natural break point is at 0.30–0.35.** Below 0.30, the accepted trades have <80% WR which is above break-even (56%) but leaves thin margin. Above 0.35, win rates are 80–100% with limited trade volume (9–34 trades over 6 years).

### Bear Call Probability Distribution (bimodal structure)

```
      <0.15:   0 trades  (   0% WR)
  0.15-0.20:  21 trades  (  10% WR) ████████████████████  ← LOW: losers cluster here
  0.20-0.22:  19 trades  (  26% WR) ███████████████████
  0.22-0.25:  10 trades  (   0% WR) ██████████           ← current live prob is here
  0.25-0.30:  11 trades  (  27% WR) ███████████
  0.30-0.35:   1 trades  (   0% WR) █
  0.35-0.40:   1 trades  (100% WR) █
  0.40-0.50:   3 trades  (  67% WR) ███
  0.50-0.60:  21 trades  (  76% WR) █████████████████████  ← HIGH: winners cluster here
  0.60-0.70:   9 trades  (100% WR) █████████
      ≥0.70:   0 trades  (   0% WR)
```

**The distribution is bimodal with a gap at 0.30–0.40.** The model does separate losers (prob ≤ 0.25, WR ≈ 17%) from winners (prob ≥ 0.50, WR ≈ 82%). The AUC for bear calls alone is **0.859** — the model has genuine discriminative power for bear calls, it just uses a threshold that renders it useless.

### Model Discrimination by Type (AUC)

| Type | N | AUC | Base WR |
|------|---|-----|---------|
| **bear_call_spread** | 96 | **0.859** | 39.6% |
| iron_condor | 1,052 | 0.819 | 64.4% |
| bull_put_spread | 519 | 0.607 | 81.7% |

Paradoxically, the model discriminates bear calls BETTER than bull puts (AUC 0.859 vs 0.607). The problem is not the model's discrimination ability — it's the threshold calibration and training data size.

---

## What the 0.65 Threshold Actually Did in Backtest

The reported "+13.8pp return improvement on 2025 holdout" used these threshold effects:

| Type | 2024-2025 Trades | Pass 0.65 | Total PnL | Captured PnL | Missed PnL |
|------|-----------------|-----------|-----------|-------------|-----------|
| bull_put_spread | 335 | 325 (97%) | +$34,329 | +$36,212 | -$1,883 |
| bear_call_spread | 34 | 0 (0%) | -$12,303 | $0 | **-$12,303** |
| iron_condor | 65 | 14 (22%) | -$13,504 | +$6,633 | -$20,136 |

The filter's improvement came from: **blocking losing ICs** (+$20K captured vs baseline). It simultaneously:
- Blocked ALL bear calls (net -$12,303 was a loser, so blocking was accidentally correct)
- Nearly rubber-stamped all bull puts (no discrimination)

**The ML filter primarily works as an Iron Condor filter.** The bear call and bull put cases are not where it adds value.

---

## Proposed Regime-Aware Thresholds

Based on the data analysis, here are scientifically defensible type-specific thresholds:

| Strategy Type | Current Threshold | Proposed Threshold | Rationale |
|--------------|------------------|--------------------|-----------|
| bull_put_spread | 0.65 | **0.65** | Keep unchanged. 97.9% pass. AUC=0.607 means filter adds little value but doesn't block good trades. |
| iron_condor | 0.65 | **0.60** | Slightly relaxed. 53% currently pass. IC AUC=0.819 is genuinely useful. |
| **bear_call_spread** | **0.65** | **0.35** | Hard lower threshold. AUC=0.859 but bimodal. Prob≥0.35 → 82% WR (vs 56% break-even). |

### Why 0.35 for bear calls, not 0.30?

At 0.30: 35 trades, 80% WR. At 0.35: 34 trades, 82% WR. One additional trade is filtered (a losing one). The difference is marginal, but 0.35 avoids the flat segment in the 0.30–0.35 region where the distribution is sparse (only 2 trades) and WR is uncertain.

### Data confidence caveat

The entire bear call analysis rests on **96 historical trades** (62 from 2020, 12 from 2024, 22 from 2025). This is too small a sample to have high confidence in the 0.35 threshold. The 0.60–0.70 cluster showing 100% WR (9 trades) is particularly fragile. Treat these thresholds as directionally correct but statistically uncertain.

---

## Opportunity Cost Assessment

| Scenario | Total Bear Call PnL | Notes |
|----------|-------------------|-------|
| Current (0.65 flat): all blocked | $0 captured | -$36,523 blocked (net loser — accidentally correct) |
| Proposed (0.35 bear-specific) | +$45,175 captured | 34 trades, 82% WR |
| Raw bear calls (no filter) | -$36,523 total | 96 trades, 39.6% WR |
| Threshold 0.30 | +$44,173 captured | 35 trades, 80% WR |

The net bear call PnL is negative (-$36,523 with no filter). The 0.35 threshold converts a -$36,523 loser into a +$45,175 profit center — a **+$81,698 swing** — by eliminating the 62 low-probability bear calls (mostly 2020 COVID crash trades that had 10-26% WR).

**The current 0.65 block IS accidentally protecting EXP-700 from bad bear call trades.** The problem is it also blocks ALL bear calls including the 9 high-quality ones (0.60-0.70 probability, 100% WR). In the current live environment, the 2026 bear call (~0.22 probability) would be rejected even at 0.35.

---

## Critical: Does the Current Live Bear Call (prob≈0.22) Deserve a Trade?

**Based on the data: NO.**

The current live bear call has probability 0.22. In the historical data, trades at 0.20-0.25 had:
- 0.20-0.22 bucket: 19 trades, 26% WR → below break-even (56%)
- 0.22-0.25 bucket: 10 trades, 0% WR → all losses

**The current live rejection is the right outcome for the right reasons**, even though the threshold calibration is wrong. The bear call being rejected today (prob 0.22, SPY at DTE=18 with RSI=28, VIX=31) would have been a loser in the historical data.

However, the structural problem remains: if RSI recovers to 40 and VIX falls to 22 tomorrow, the model will still output 0.22 (because market context features are invisible to it). The model cannot update its bear call assessments based on market conditions.

---

## Root Causes: Priority List

### 1. [CRITICAL — Immediate fix] Regime-aware thresholds in `paper_exp700.yaml`
Implement type-specific probability thresholds. This is the fastest fix that doesn't require retraining.

### 2. [HIGH — Required for scientific validity] Retrain with actual price data
The backtest must be rerun with `--no-skip-backtest` to fetch real SPY price history. The 1667 trades need to be re-featurized with actual RSI, VIX, MA distances, momentum at each entry date. The current model is blind to market conditions.

### 3. [HIGH] The "integrated backtest" problem
The backtest as designed is post-hoc: run strategy → collect all trades → score with ML. A truly integrated test would run the strategy with the ML filter making real-time go/no-go decisions. This changes which trades get entered (because skipped trades change position sizing, which affects contracts count, which changes subsequent trade features).

### 4. [MEDIUM] Bear call data sparsity
Only 96 historical bear calls, concentrated in 3 non-continuous years (2020, 2024, 2025). Zero bear calls from 2021-2023. The model cannot generalize to a prolonged bear market regime. Training data for bear calls needs to be expanded (EXP-126/EXP-154 bear call trades if available, or synthetic augmentation).

### 5. [LOW] Contracts feature OOD
Training: mean=2.575, std=1.222. Live: 11-12 contracts (6.9-7.7σ). This is a calibration issue from different account sizes. The contracts count should be normalized (e.g., as % of max_contracts or $-risk-equivalent).

---

## Recommended Immediate Actions

### Option A: Deploy regime-aware thresholds now (fastest)
Update `paper_exp700.yaml`:
```yaml
ml_filter:
  enabled: true
  probability_threshold: 0.65           # default (bull puts, ICs)
  type_thresholds:                       # type-specific overrides
    bull_put_spread: 0.65
    iron_condor: 0.60
    bear_call_spread: 0.35
```

**Impact**: The current live bear call (prob≈0.22) would STILL be rejected at 0.35. No trades would be generated until market conditions improve or prob rises. This is the correct behavior given the data.

### Option B: Disable ML filter for bear calls only (aggressive)
```yaml
ml_filter:
  enabled: true
  probability_threshold: 0.65
  bypass_types:
    - bear_call_spread
```

**Risk**: Raw bear calls are a net loser (-$36,523 over 6 years). Bypassing means accepting all 39.6% WR bear calls including the 2024 zero-WR cluster. NOT recommended without a retrained model.

### Option C: Retrain the model with real price data (correct long-term fix)
Rerun `python3 scripts/backtest_ml_filter.py` (without `--skip-backtest`) to get actual technical features. This will produce a model that can actually use RSI, VIX, and momentum to score bear calls in market context. Estimated runtime: 30-60 min (full backtest required).

**Recommended path**: A now, C within 1 week. Then re-evaluate regime-aware thresholds with the retrained model.

---

## Summary: Three Reliable Numbers

| Claim | Status | Reality |
|-------|--------|---------|
| "ML filter improved 2025 holdout by +13.8pp" | Partially valid | Effect came from blocking losing ICs. Bear calls were blocked (net loser — accidentally correct). Price context features had zero variance. |
| "78% of trades pass filter at 0.65 threshold" | Misleading | 97.9% of bull puts pass (trivially), 53.1% of ICs, 0% of bear calls. Threshold is type-specific in effect. |
| "Model AUC=0.793 on walk-forward folds" | Valid for IC/bull put mix | AUC for bear calls specifically is 0.859, but trained on imputed-constant market context. Would score differently with real price data. |

---

*Analysis completed 2026-03-30. Data sources: `output/ml_filter_exp400_trades_cache.json` (1667 trades), `ml/models/ensemble_model_20260324.joblib`, `logs/exp700_scanner.log`. All probabilities scored by re-running `EnsembleSignalModel._weighted_predict_proba()` on reconstructed feature vectors matching the backtest's imputed defaults.*
