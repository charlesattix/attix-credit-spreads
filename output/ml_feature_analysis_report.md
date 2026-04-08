# ML Feature Analysis Report: Credit Spreads Strategy

_Generated: 2026-03-24 15:39_
_Dataset: 187,300 trades | 6 years | sources: 1_

---

## 1. Dataset Overview

**Trades by Source:**

- `mc`: 187,300

**Trades by Year:**

- 2020: 35,457
- 2021: 17,293
- 2022: 23,616
- 2023: 20,931
- 2024: 39,970
- 2025: 50,033

**Overall Win Rate:** 86.5% across 187,300 labeled trades

**Win Rate by Strategy Type:**

| Type | Win Rate | Count |
|------|---------|-------|
| bear_call_spread | 75.1% | 12,855 |
| bull_put_spread | 93.3% | 145,035 |
| iron_condor | 58.1% | 29,410 |

**Feature Coverage:**

| Feature | Coverage | Group |
|---------|---------|-------|
| `is_bull_put` | 100% ██████████ | Strategy |
| `is_bear_call` | 100% ██████████ | Strategy |
| `is_iron_condor` | 100% ██████████ | Strategy |
| `spread_width` | 100% ██████████ | Strategy |
| `credit_pct_of_width` | 100% ██████████ | Strategy |
| `dte_at_entry` | 100% ██████████ | Strategy |
| `contracts` | 100% ██████████ | Strategy |
| `otm_pct` | 100% █████████░ | Strategy |
| `config_risk_pct` | 100% ██████████ | Strategy |
| `config_sl_mult` | 100% ██████████ | Strategy |
| `config_pt_pct` | 0% ░░░░░░░░░░ | Strategy |
| `vix_level` | 100% █████████░ | Volatility Regime |
| `vix_regime_cat` | 100% █████████░ | Volatility Regime |
| `rsi_14` | 100% █████████░ | SPY Technical |
| `macd_hist` | 100% █████████░ | SPY Technical |
| `macd_bullish` | 100% █████████░ | SPY Technical |
| `bb_width` | 94% █████████░ | SPY Technical |
| `bb_position` | 94% █████████░ | SPY Technical |
| `spy_ret_5d` | 100% █████████░ | SPY Technical |
| `spy_ret_20d` | 94% █████████░ | SPY Technical |
| `spy_rvol_20d` | 94% █████████░ | SPY Technical |
| `spy_52w_pct` | 33% ███░░░░░░░ | SPY Technical |
| `pc_vol_ratio` | 100% ██████████ | Options Market |
| `pc_oi_ratio` | 0% ░░░░░░░░░░ | Options Market |
| `day_of_week` | 100% ██████████ | Calendar |
| `month` | 100% ██████████ | Calendar |
| `quarter` | 100% ██████████ | Calendar |
| `days_to_opex` | 100% ██████████ | Calendar |
| `is_jan_effect` | 100% ██████████ | Calendar |
| `is_q4_rally` | 100% ██████████ | Calendar |
| `is_summer` | 100% ██████████ | Calendar |

## 2. Model Performance

**XGBoost Win/Loss Classifier — 5-Fold Stratified CV:**

| Metric | Value |
|--------|-------|
| Mean AUC | **0.9995** |
| Std Dev  | 0.0001 |
| Min / Max | 0.9994 / 0.9995 |
| Per-fold  | 0.9994 / 0.9995 / 0.9994 / 0.9995 / 0.9995 |

> **Strong signal** (AUC ≥ 0.65) — features meaningfully predict win/loss.

## 3. Feature Importances (Full Dataset)

### 3a. XGBoost Gain (primary — measures information gain per split)

| Rank | Feature | Gain% | Weight% | Cover% | Group |
|------|---------|-------|---------|--------|-------|
| 1 | `is_bull_put` | 36.9% | 1.1% | 13.6% | Strategy |
| 2 | `bb_position` | 4.9% | 3.7% | 4.6% | SPY Technical |
| 3 | `spy_ret_20d` | 4.9% | 4.6% | 5.7% | SPY Technical |
| 4 | `spy_52w_pct` | 4.1% | 1.2% | 5.6% | SPY Technical |
| 5 | `bb_width` | 3.6% | 4.4% | 4.5% | SPY Technical |
| 6 | `month` | 3.4% | 3.7% | 3.4% | Calendar |
| 7 | `rsi_14` | 3.3% | 3.8% | 2.7% | SPY Technical |
| 8 | `is_iron_condor` | 3.1% | 1.7% | 10.5% | Strategy |
| 9 | `spy_ret_5d` | 3.1% | 5.8% | 4.6% | SPY Technical |
| 10 | `macd_hist` | 3.0% | 5.1% | 2.8% | SPY Technical |
| 11 | `credit_pct_of_width` | 2.9% | 14.7% | 4.0% | Strategy |
| 12 | `vix_level` | 2.9% | 6.5% | 4.4% | Volatility Regime |
| 13 | `macd_bullish` | 2.9% | 0.1% | 1.6% | SPY Technical |
| 14 | `spy_rvol_20d` | 2.9% | 5.8% | 3.4% | SPY Technical |
| 15 | `config_sl_mult` | 1.9% | 2.1% | 3.9% | Strategy |
| 16 | `is_summer` | 1.9% | 0.2% | 2.5% | Calendar |
| 17 | `pc_vol_ratio` | 1.9% | 10.6% | 3.6% | Options Market |
| 18 | `otm_pct` | 1.8% | 9.6% | 3.1% | Strategy |
| 19 | `days_to_opex` | 1.7% | 5.5% | 1.9% | Calendar |
| 20 | `is_jan_effect` | 1.7% | 0.0% | 3.0% | Calendar |
| 21 | `quarter` | 1.7% | 0.3% | 1.0% | Calendar |
| 22 | `day_of_week` | 1.6% | 2.4% | 1.1% | Calendar |
| 23 | `dte_at_entry` | 1.5% | 5.5% | 4.2% | Strategy |
| 24 | `vix_regime_cat` | 1.3% | 0.2% | 1.3% | Volatility Regime |
| 25 | `contracts` | 0.7% | 1.5% | 2.7% | Strategy |

### 3b. SHAP
_Not available — install `shap` package for directional importance._

### 3c. Feature Group Summary

| Group | Total Gain% | Top Feature |
|-------|------------|-------------|
| Strategy | **49.4%** | `is_bull_put` |
| SPY Technical | **32.6%** | `bb_position` |
| Calendar | **12.0%** | `month` |
| Volatility Regime | **4.2%** | `vix_level` |
| Options Market | **1.9%** | `pc_vol_ratio` |

## 4. Regime-Conditional Analysis

### Low Vol (VIX < 20)

_n=99,766 | win_rate=91.7%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `macd_bullish` | 20.3% |
| 2 | `macd_hist` | 12.8% |
| 3 | `quarter` | 7.3% |
| 4 | `spy_ret_5d` | 6.5% |
| 5 | `spy_rvol_20d` | 4.7% |
| 6 | `spy_ret_20d` | 4.7% |
| 7 | `vix_regime_cat` | 4.2% |
| 8 | `is_summer` | 4.1% |

### Normal Vol (VIX 20–30)

_n=68,623 | win_rate=82.9%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `is_iron_condor` | 20.4% |
| 2 | `is_bull_put` | 18.3% |
| 3 | `is_summer` | 9.8% |
| 4 | `is_bear_call` | 7.8% |
| 5 | `spy_ret_20d` | 6.2% |
| 6 | `spy_ret_5d` | 4.5% |
| 7 | `macd_hist` | 3.2% |
| 8 | `vix_level` | 3.1% |

### High Vol (VIX > 30)

_n=18,903 | win_rate=72.7%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `is_bull_put` | 24.2% |
| 2 | `bb_position` | 14.8% |
| 3 | `rsi_14` | 11.6% |
| 4 | `days_to_opex` | 4.4% |
| 5 | `month` | 4.2% |
| 6 | `vix_level` | 3.9% |
| 7 | `contracts` | 3.8% |
| 8 | `spy_ret_20d` | 3.6% |

### Bull Put Spreads

_n=145,035 | win_rate=93.3%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `spy_ret_20d` | 9.9% |
| 2 | `bb_position` | 9.8% |
| 3 | `bb_width` | 8.1% |
| 4 | `vix_level` | 6.9% |
| 5 | `credit_pct_of_width` | 6.6% |
| 6 | `spy_rvol_20d` | 6.3% |
| 7 | `quarter` | 6.1% |
| 8 | `is_summer` | 5.1% |

### Bear Call Spreads

_n=12,855 | win_rate=75.1%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `vix_regime_cat` | 15.6% |
| 2 | `rsi_14` | 10.5% |
| 3 | `macd_bullish` | 7.5% |
| 4 | `vix_level` | 6.6% |
| 5 | `dte_at_entry` | 6.4% |
| 6 | `otm_pct` | 5.7% |
| 7 | `bb_width` | 5.6% |
| 8 | `config_sl_mult` | 5.5% |

### Iron Condors

_n=29,410 | win_rate=58.1%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `bb_position` | 13.0% |
| 2 | `macd_hist` | 8.9% |
| 3 | `spy_ret_5d` | 8.7% |
| 4 | `spy_rvol_20d` | 7.8% |
| 5 | `quarter` | 6.8% |
| 6 | `month` | 6.0% |
| 7 | `spy_ret_20d` | 5.9% |
| 8 | `rsi_14` | 5.1% |

### Macro: Neutral_Macro

_n=118,507 | win_rate=82.5%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `is_bull_put` | 21.8% |
| 2 | `is_bear_call` | 10.3% |
| 3 | `vix_regime_cat` | 9.7% |
| 4 | `is_iron_condor` | 8.9% |
| 5 | `bb_position` | 4.1% |
| 6 | `vix_level` | 3.5% |
| 7 | `month` | 3.4% |
| 8 | `macd_hist` | 3.4% |

### Macro: Bull_Macro

_n=53,771 | win_rate=94.9%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `is_bear_call` | 35.9% |
| 2 | `macd_hist` | 14.6% |
| 3 | `month` | 9.0% |
| 4 | `days_to_opex` | 5.6% |
| 5 | `dte_at_entry` | 4.9% |
| 6 | `spy_rvol_20d` | 3.9% |
| 7 | `credit_pct_of_width` | 2.7% |
| 8 | `bb_width` | 2.7% |

### Macro: Bear_Macro

_n=15,014 | win_rate=88.9%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `month` | 31.9% |
| 2 | `quarter` | 24.2% |
| 3 | `vix_level` | 14.6% |
| 4 | `rsi_14` | 11.4% |
| 5 | `dte_at_entry` | 5.6% |
| 6 | `is_bull_put` | 4.0% |
| 7 | `bb_width` | 1.3% |
| 8 | `is_iron_condor` | 1.3% |

### Year 2020

_n=35,457 | win_rate=84.6%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `is_bull_put` | 47.0% |
| 2 | `vix_level` | 8.1% |
| 3 | `macd_hist` | 6.2% |
| 4 | `rsi_14` | 4.7% |
| 5 | `dte_at_entry` | 3.5% |
| 6 | `quarter` | 3.2% |
| 7 | `credit_pct_of_width` | 3.0% |
| 8 | `spy_rvol_20d` | 3.0% |

### Year 2021

_n=17,293 | win_rate=97.8%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `bb_width` | 17.5% |
| 2 | `vix_regime_cat` | 17.4% |
| 3 | `otm_pct` | 17.3% |
| 4 | `days_to_opex` | 12.4% |
| 5 | `spy_rvol_20d` | 8.4% |
| 6 | `is_bull_put` | 8.0% |
| 7 | `vix_level` | 5.4% |
| 8 | `credit_pct_of_width` | 2.7% |

### Year 2022

_n=23,616 | win_rate=70.7%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `is_iron_condor` | 16.7% |
| 2 | `vix_level` | 9.1% |
| 3 | `spy_ret_20d` | 8.1% |
| 4 | `macd_hist` | 7.3% |
| 5 | `month` | 7.2% |
| 6 | `spy_rvol_20d` | 6.3% |
| 7 | `quarter` | 6.1% |
| 8 | `rsi_14` | 5.0% |

### Year 2023

_n=20,931 | win_rate=86.0%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `vix_level` | 31.2% |
| 2 | `spy_ret_5d` | 11.2% |
| 3 | `days_to_opex` | 8.0% |
| 4 | `spy_ret_20d` | 7.3% |
| 5 | `rsi_14` | 5.0% |
| 6 | `spy_rvol_20d` | 4.3% |
| 7 | `is_bull_put` | 4.2% |
| 8 | `credit_pct_of_width` | 3.9% |

### Year 2024

_n=39,970 | win_rate=86.1%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `bb_position` | 24.3% |
| 2 | `macd_hist` | 9.0% |
| 3 | `days_to_opex` | 8.2% |
| 4 | `rsi_14` | 8.1% |
| 5 | `is_summer` | 6.4% |
| 6 | `spy_ret_5d` | 6.2% |
| 7 | `credit_pct_of_width` | 5.3% |
| 8 | `month` | 4.8% |

### Year 2025

_n=50,033 | win_rate=92.1%_

**Top 8 features by gain:**

| Rank | Feature | Gain% |
|------|---------|-------|
| 1 | `macd_hist` | 25.0% |
| 2 | `spy_rvol_20d` | 9.1% |
| 3 | `spy_ret_20d` | 6.1% |
| 4 | `bb_width` | 6.1% |
| 5 | `spy_ret_5d` | 5.8% |
| 6 | `is_iron_condor` | 5.6% |
| 7 | `is_jan_effect` | 5.4% |
| 8 | `credit_pct_of_width` | 4.8% |

## 5. Key Patterns

### 5a. VIX Level vs Win Rate

| VIX Range | Win Rate | N Trades |
|-----------|---------|---------|
| VIX <15 | 91.5% | 21,630 |
| VIX 15–20 | 91.8% | 78,771 |
| VIX 20–25 | 81.3% | 45,513 |
| VIX 25–30 | 85.6% | 22,475 |
| VIX 30–35 | 79.1% | 8,991 |
| VIX >35 | 66.8% | 9,912 |

### 5b. DTE at Entry vs Win Rate

| DTE Range | Win Rate | N Trades |
|-----------|---------|---------|
| DTE 21–30 | 82.9% | 4,952 |
| DTE 30–45 | 86.6% | 182,348 |

### 5c. Credit as % of Spread Width vs Win Rate

| Credit Range | Win Rate | N Trades |
|-------------|---------|---------|
| <10% | 91.5% | 78,254 |
| 10–15% | 94.0% | 53,433 |
| 15–20% | 99.5% | 10,024 |
| 20–25% | 99.4% | 2,295 |
| 25–33% | 89.6% | 1,766 |
| 33–50% | 71.1% | 7,811 |
| >50% | 61.4% | 33,136 |

### 5d. OTM% vs Win Rate

| OTM Range | Win Rate | N Trades |
|-----------|---------|---------|
| <1% | 96.8% | 1,531 |
| 1–2% | 70.4% | 5,356 |
| 2–3% | 92.4% | 11,551 |
| 3–5% | 90.6% | 58,183 |
| 5–7% | 84.4% | 37,002 |
| 7–10% | 86.0% | 50,525 |
| >10% | 87.8% | 13,841 |

### 5e. Monthly Seasonality

| Month | Win Rate | N Trades |
|-------|---------|---------|
| Jan | 89.7% | 10,679 |
| Feb | 86.1% | 12,584 |
| Mar | 82.0% | 12,840 |
| Apr | 74.1% | 16,047 |
| May | 85.3% | 13,703 |
| Jun | 87.4% | 13,486 |
| Jul | 93.9% | 20,414 |
| Aug | 85.9% | 17,641 |
| Sep | 88.9% | 17,898 |
| Oct | 89.8% | 19,180 |
| Nov | 90.1% | 15,631 |
| Dec | 82.8% | 17,197 |

### 5f. Day-of-Week vs Win Rate

| Day | Win Rate | N Trades |
|-----|---------|---------|
| Mon | 85.0% | 49,760 |
| Tue | 86.8% | 36,076 |
| Wed | 88.5% | 33,937 |
| Thu | 87.2% | 34,972 |
| Fri | 85.8% | 32,555 |

### 5g. SPY 20-Day Return at Entry vs Win Rate

| SPY Trend | Win Rate | N Trades |
|-----------|---------|---------|
| SPY<-5% | 71.8% | 20,091 |
| SPY -5–-2% | 77.2% | 8,657 |
| SPY ±2% | 91.1% | 18,250 |
| SPY +2–+5% | 88.0% | 17,383 |
| SPY>+5% | 90.7% | 112,303 |

### 5h. SPY RSI(14) at Entry vs Win Rate

| RSI Range | Win Rate | N Trades |
|-----------|---------|---------|
| RSI <30 | 65.5% | 2,430 |
| RSI 30–40 | 74.2% | 7,973 |
| RSI 40–50 | 70.5% | 23,053 |
| RSI 50–60 | 85.0% | 54,448 |
| RSI 60–70 | 92.2% | 86,358 |
| RSI >70 | 95.4% | 13,018 |

## 6. Recommendations

### 6a. Prioritized Signals

Ranked by XGBoost gain (information contribution to win/loss prediction):

| Rank | Feature | Gain% | Group | Action |
|------|---------|-------|-------|--------|
| 1 | `is_bull_put` | 36.9% | Strategy | Direction matters; confirm with regime model |
| 2 | `bb_position` | 4.9% | SPY Technical | Avoid entries when SPY near BB extremes |
| 3 | `spy_ret_20d` | 4.9% | SPY Technical | Avoid entries after SPY >+5% or <-5% 20d run |
| 4 | `spy_52w_pct` | 4.1% | SPY Technical | Monitor and validate |
| 5 | `bb_width` | 3.6% | SPY Technical | Monitor and validate |
| 6 | `month` | 3.4% | Calendar | Apply seasonal overlay; reduce Sep/Oct exposure |
| 7 | `rsi_14` | 3.3% | SPY Technical | Filter entries at RSI extremes (<30 or >70) |
| 8 | `is_iron_condor` | 3.1% | Strategy | Monitor and validate |
| 9 | `spy_ret_5d` | 3.1% | SPY Technical | Monitor and validate |
| 10 | `macd_hist` | 3.0% | SPY Technical | Use MACD histogram sign as trend confirmation |
| 11 | `credit_pct_of_width` | 2.9% | Strategy | Set minimum credit floor (≥15–20% of width) |
| 12 | `vix_level` | 2.9% | Volatility Regime | Use VIX gate; tune thresholds by regime |
| 13 | `macd_bullish` | 2.9% | SPY Technical | Monitor and validate |
| 14 | `spy_rvol_20d` | 2.9% | SPY Technical | Reduce size when realized vol spikes |
| 15 | `config_sl_mult` | 1.9% | Strategy | Stop-loss tightness significantly affects P50 |

### 6b. Signal Strength Assessment

Model AUC = **0.999** — features carry strong predictive signal. Use predicted win probability as a position-sizing multiplier.

### 6c. Entry Filter Recommendations

- **VIX Gate**: Win rate drops to 66.8% when VIX > 35 (vs 88.0% in VIX 15–25). Confirm vix_max_entry=35 or lower.
- **Credit Floor**: Credit ≥ 20% of width → 66.1% win rate vs 92.0% for < 12% (lower credit = lower win rate). Enforce min_credit_pct in entry filter.
- **OTM Sweet Spot**: Highest win rate (92.0%) in 2–4% OTM range. Prefer 3% OTM as a balance between credit and safety margin.

### 6d. Regime-Specific Signal Priorities

- **Low Vol (VIX < 20)** (win_rate=91.7%): prioritise `macd_bullish`, `macd_hist`, `quarter`
- **Normal Vol (VIX 20–30)** (win_rate=82.9%): prioritise `is_iron_condor`, `is_bull_put`, `is_summer`
- **High Vol (VIX > 30)** (win_rate=72.7%): prioritise `is_bull_put`, `bb_position`, `rsi_14`
- **Bull Put Spreads** (win_rate=93.3%): prioritise `spy_ret_20d`, `bb_position`, `bb_width`
- **Bear Call Spreads** (win_rate=75.1%): prioritise `vix_regime_cat`, `rsi_14`, `macd_bullish`
- **Iron Condors** (win_rate=58.1%): prioritise `bb_position`, `macd_hist`, `spy_ret_5d`
- **Macro: Neutral_Macro** (win_rate=82.5%): prioritise `is_bull_put`, `is_bear_call`, `vix_regime_cat`
- **Macro: Bull_Macro** (win_rate=94.9%): prioritise `is_bear_call`, `macd_hist`, `month`
- **Macro: Bear_Macro** (win_rate=88.9%): prioritise `month`, `quarter`, `vix_level`
- **Year 2020** (win_rate=84.6%): prioritise `is_bull_put`, `vix_level`, `macd_hist`
- **Year 2021** (win_rate=97.8%): prioritise `bb_width`, `vix_regime_cat`, `otm_pct`
- **Year 2022** (win_rate=70.7%): prioritise `is_iron_condor`, `vix_level`, `spy_ret_20d`
- **Year 2023** (win_rate=86.0%): prioritise `vix_level`, `spy_ret_5d`, `days_to_opex`
- **Year 2024** (win_rate=86.1%): prioritise `bb_position`, `macd_hist`, `days_to_opex`
- **Year 2025** (win_rate=92.1%): prioritise `macd_hist`, `spy_rvol_20d`, `spy_ret_20d`

### 6e. Data Quality Notes

- MC trade records share market conditions across seeds (only DTE varies). Win/loss labels are therefore correlated within same calendar date. AUC is likely *optimistically* biased — treat as upper bound.

- Missing market features (VIX, SPY technicals) reduce usable sample. Ensure `data/macro_state.db` is up to date for best coverage.

- For IV rank / IV percentile features: not available in current data. Proxy used: `credit_pct_of_width` (higher credit ≈ higher implied vol).

---

_Pipeline: XGBoost 5-fold stratified CV | Features: 31 | Trades used for ML: 187,300_