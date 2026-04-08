# Experiment Report: exp_pipeline_mltest

**Run ID:** `exp_pipeline_mltest_20260325_010939_0673d3`  
**Date:** 2026-03-25 01:09 UTC  
**Ticker:** SPY  
**Elapsed:** 7s

## Results by Year

| Year | Return | Trades | Win Rate | Sharpe | Max DD |
|:----:|:------:|:------:|:--------:|:------:|:------:|
| 2023 | +5.8% ✓ | 132 | 73.5% | 0.34 | -23.9% |
| 2024 | +26.2% ✓ | 164 | 79.3% | 0.81 | -21.3% |
| **AVG** | **+16.0%** | 148 | — | — | **-23.9%** |

**Profitable years:** 2/2 (100%)

## vs. Current Champion

| Metric | This Run | Champion (`endless_20260309_054247_4ce3`) | Delta |
|:-------|:--------:|:------:|:------:|
| Avg Return | +16.0% | +29.5% | -13.5pp |
| Worst DD | -23.9% | -0.3% | -23.6pp |

## ML Ensemble Filter

**Model:** `ensemble_model_20260324.joblib`  (AUC=0.739)
**Threshold:** 0.58  |  **Kept:** 210/296 trades (71%)
**Features:** trade-derived (DTE, VIX, spread, contracts) + imputed (price features → training means)

| Year | Orig Trades | Filt Trades | Orig Return | Filt Return | Orig WR | Filt WR |
|:----:|:-----------:|:-----------:|:-----------:|:-----------:|:-------:|:-------:|
| 2023 | 132 | 87 | +5.8% | +23.2% | 73.5% | 93.1% |
| 2024 | 164 | 123 | +26.2% | +59.7% | 79.3% | 94.3% |

> **Note:** Price-based features (RSI, momentum, MA distances) are imputed
> with training means. For full-accuracy ML scoring, run `backtest_ml_filter.py`
> which builds complete features from the backtester's internal data.

## Parameters

```json
{
  "target_delta": 0.12,
  "use_delta_selection": false,
  "otm_pct": 0.03,
  "target_dte": 35,
  "min_dte": 25,
  "spread_width": 5,
  "min_credit_pct": 8,
  "stop_loss_multiplier": 3.5,
  "profit_target": 50,
  "max_risk_per_trade": 8.0,
  "max_contracts": 25,
  "direction": "both",
  "compound": false,
  "sizing_mode": "flat",
  "iron_condor_enabled": true,
  "ic_neutral_regime_only": true,
  "ic_min_combined_credit_pct": 8,
  "iv_rank_min_entry": 0,
  "drawdown_cb_pct": 30,
  "trend_ma_period": 200,
  "regime_mode": "combo",
  "regime_config": {
    "signals": [
      "price_vs_ma200",
      "rsi_momentum",
      "vix_structure"
    ],
    "ma_slow_period": 200,
    "ma200_neutral_band_pct": 0.5,
    "rsi_period": 14,
    "rsi_bull_threshold": 55.0,
    "rsi_bear_threshold": 45.0,
    "vix_structure_bull": 0.95,
    "vix_structure_bear": 1.05,
    "bear_requires_unanimous": true,
    "cooldown_days": 3,
    "vix_extreme": 40.0
  },
  "max_portfolio_exposure_pct": 100
}
```
