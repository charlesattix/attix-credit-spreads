# compass/ Production Manifest

**Generated:** 2026-04-23
**Purpose:** Map which modules are in the production signal/trading pipeline vs. standalone experiments vs. unlinked code.

---

## Production Entry Points (7)

These are the orchestrators that run in the paper-trading loop or deployment gates.

| Module | Role |
|---|---|
| `scripts/generate_daily_signals.py` | Daily signal driver (cron ~09:25 ET) |
| `exp2690_signal_generators.py` | GENERATOR_REGISTRY — single source of truth for 8-stream signals |
| `exp2830_paper_signal_generator.py` | Phase 9 paper signal generator (market snapshot + regime + sizing) |
| `exp2860_paper_dry_run.py` | End-to-end dry run: signals → VIX ladder → mock Alpaca |
| `alpaca_connector.py` | Alpaca API integration (order submission, position reconciliation) |
| `exp2670_paper_gonogo.py` | Pre-flight go/no-go checklist |
| `exp2900_v8a_consistency_audit.py` | Portfolio consistency audit (36/39 checks) |

## Production Dependencies (18)

Directly imported by one or more entry points.

| Module | Imported By | Role |
|---|---|---|
| `exp1220_standalone.py` | exp2670, exp2900 | SPY put-credit-spread backtester |
| `exp1770_commodity_calendars.py` | exp2670, exp2900 | GLD/SLV calendar spread backtester |
| `exp2020_cross_vol_arb.py` | exp2670, exp2900 | IV-RV cross-sectional arb; delegates signals to exp2690 |
| `exp2160_high_capacity_alts.py` | exp2900 | XLF/XLI put-credit-spread backtester |
| `exp2200_north_star_v6.py` | exp2670 | North Star v6 7-stream framework |
| `exp2240_qqq_iwm_credit_spreads.py` | exp2900 | QQQ put-credit-spread backtester |
| `exp2360_robust_cov.py` | exp2900 | Robust covariance estimation |
| `exp2450_sparse_combined_honest.py` | exp2900 | Sparse combined portfolio (honest Sharpe) |
| `exp2580_spy_weekly_cs.py` | exp2670 | SPY weekly credit spreads |
| `exp2820_flash_crash_protection.py` | exp2860 | VIX leverage ladder |
| `exp2830_paper_signal_generator.py` | exp2860 | (also an entry point) |
| `exp2850_v8a_with_vix_ladder.py` | exp2900 | VIX ladder integration |
| `crisis_alpha_v5.py` | exp2670, exp2690 | Crisis Alpha v5 hedge sleeve |
| `vix_ladder.py` | exp2850, exp2860 | VIX ladder helper |
| `paper_monitor_dashboard.py` | exp2670 | Rolling PnL/DD/Sharpe dashboard |
| `paper_trading_monitor.py` | exp2670 | Paper trading monitor |
| `prod_monitor.py` | exp2670 | Production monitoring |

## Transitive Dependencies (17)

Imported by production deps, not directly by entry points.

| Module | Imported By | Role |
|---|---|---|
| `crisis_alpha.py` | crisis_alpha_v3 | Original crisis alpha (v1) |
| `crisis_alpha_v3.py` | exp2690, crisis_alpha_v5 | Crisis Alpha v3 universe loader |
| `crisis_alpha_v4.py` | exp2690, crisis_alpha_v5 | Signal confirmation logic |
| `exp1780_exp1220_integration.py` | crisis_alpha_v4 | EXP-1220 integration helper |
| `exp1850_regime_portfolio.py` | exp2080_corr_regime | Regime-aware portfolio construction |
| `exp1960_skew_alpha.py` | exp2160 | Skew-based alpha |
| `exp1970_vol_of_vol.py` | exp2690 | VoV overlay gate |
| `exp2080_corr_regime.py` | exp2360, exp2710, exp2750, exp2820 | Correlation regime detection + 5-stream cube |
| `exp2370_dd_circuit_breaker.py` | exp2820 | 3%/12% DD circuit breaker |
| `exp2390_robust_cov_audit.py` | exp2450 | Covariance validation |
| `exp2400_combined_best_of.py` | exp2450 | Ensemble portfolio |
| `exp2420_transaction_costs.py` | exp2450 | TC model |
| `exp2710_xle_integration.py` | exp2820 | XLE sector integration |
| `exp2750_oos_regime_stress.py` | exp2820 | Out-of-sample regime stress |
| `greeks_sensitivity.py` | exp2160 | Options Greeks |
| `metrics.py` | exp2200 | Core performance metrics |
| `regime.py` | exp1850 | Regime classifier |

## Standalone Experiments (105)

Scripts with `if __name__ == "__main__"` that are NOT imported by production. These are completed research experiments. A few notable ones:

| Module | Status | Notes |
|---|---|---|
| `exp2060_cross_vol_arb_v2.py` | Completed | Cross-vol v2 with vvol scaling |
| `exp2280_wf_robustness.py` | Completed | Walk-forward robustness (v6) |
| `exp2600_north_star_v8.py` | Completed | v8a baseline — results cached |
| `exp2730_wf_robustness_v8a_net.py` | Completed | 20-fold WF net robustness |
| `exp2810_9stream_portfolio.py` | Killed | 9-stream net Sharpe 2.34 |
| `exp2910_tlt_credit_spreads.py` | Killed | TLT Sharpe 0.76 |
| `exp2920_tlt_ivrv_arb.py` | Killed | MOVE IV-RV Sharpe 0.26 |
| `exp2950_sector_momentum.py` | Killed | WF Sharpe 0.57 |

## Unlinked Modules (~264)

Modules with no `__main__` block and not in the production import graph. These include:
- Utility libraries (sizing, risk budgeting, execution algos)
- Feature engineering pipelines
- Monitoring/dashboard infrastructure
- Crypto-related modules (unused)
- Earlier portfolio construction attempts

**Archival candidates (not imported anywhere, no __main__):**
These can be moved to `compass/archive/` without breaking any production code. A full list would require tracing imports across ALL 411 modules (not just production), which is a larger task.

---

## Signal Pipeline Architecture

```
                  ┌─────────────────────────────┐
                  │  generate_daily_signals.py   │  ← cron driver
                  └────────────┬────────────────┘
                               │ importlib
                               ▼
                  ┌─────────────────────────────┐
                  │  exp2690_signal_generators   │  ← GENERATOR_REGISTRY
                  │  (8 stream functions)        │
                  └──┬──┬──┬──┬──┬──┬──┬──┬─────┘
                     │  │  │  │  │  │  │  │
   exp1220 ──────────┘  │  │  │  │  │  │  └── v5_hedge
   xlf_cs ──────────────┘  │  │  │  │  └───── qqq_cs
   xli_cs ─────────────────┘  │  │  └──────── slv_cal
   gld_cal ────────────────────┘  └─────────── cross_vol
                                                  │
                                    delegates to exp2690
                                    (NOT duplicated in exp2020)

  ┌─────────────────────────────┐
  │  exp2830_paper_signal_gen   │  ← PARALLEL implementation
  │  (Phase 9, dollar sizing)   │     for paper trading
  └─────────────────────────────┘     Does NOT call exp2690
```

**Key architectural note:** `exp2830` reimplements cross_vol logic independently (SPY-only VRP threshold) vs. `exp2690` (multi-ticker IV-RV ranking). This is by design — exp2830 adds regime gates, position sizing, and VoV overlays that exp2690 doesn't have. They are parallel implementations for different use cases, not duplicates.
