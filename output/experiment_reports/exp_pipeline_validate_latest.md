# Experiment Report: exp_pipeline_validate

**Run ID:** `exp_pipeline_validate_20260325_010655_dd193e`  
**Date:** 2026-03-25 01:07 UTC  
**Ticker:** SPY  
**Elapsed:** 11s

## Results by Year

| Year | Return | Trades | Win Rate | Sharpe | Max DD |
|:----:|:------:|:------:|:--------:|:------:|:------:|
| 2023 | +5.8% ✓ | 132 | 73.5% | 0.34 | -23.9% |
| 2024 | +26.2% ✓ | 164 | 79.3% | 0.81 | -21.3% |
| 2025 | +89.0% ✓ | 303 | 83.2% | 1.30 | -34.1% |
| **AVG** | **+40.3%** | 200 | — | — | **-34.1%** |

**Profitable years:** 3/3 (100%)

## vs. Current Champion

| Metric | This Run | Champion (`endless_20260309_054247_4ce3`) | Delta |
|:-------|:--------:|:------:|:------:|
| Avg Return | +40.3% | +29.5% | +10.8pp |
| Worst DD | -34.1% | -0.3% | -33.8pp |

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
