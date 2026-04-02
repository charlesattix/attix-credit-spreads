# Production Readiness Audit Report

**Modules scanned:** 233
**Production ready:** 215 (92%)
**With tests:** 215
**Import OK:** 233
**Average quality:** 9.4/10

## Top 10 Production-Ready Strategies

| Rank | Module | Quality | Lines | Tests | Latency | Ext Deps | Category |
|------|--------|---------|-------|-------|---------|----------|----------|
| 1 | `advanced_sizing` | **10.0** | 330 | YES | fast | none | regime |
| 2 | `auto_docs` | **10.0** | 520 | YES | medium | none | monitoring |
| 3 | `crisis_hedge` | **10.0** | 343 | YES | fast | none | regime |
| 4 | `earnings_alpha` | **10.0** | 487 | YES | fast | none | signal |
| 5 | `feature_analysis` | **10.0** | 533 | YES | medium | none | signal |
| 6 | `generate_docs` | **10.0** | 511 | YES | medium | none | data |
| 7 | `meta_learner_v2` | **10.0** | 379 | YES | fast | none | signal |
| 8 | `module_health` | **10.0** | 348 | YES | fast | none | data |
| 9 | `position_reconciler` | **10.0** | 349 | YES | fast | none | execution |
| 10 | `regime_ensemble_v2` | **10.0** | 534 | YES | medium | none | regime |

## Category Breakdown

| Category | Modules |
|----------|---------|
| signal | 118 |
| regime | 41 |
| risk | 31 |
| execution | 18 |
| data | 7 |
| ml | 6 |
| portfolio | 4 |
| backtest | 4 |
| monitoring | 2 |
| analysis | 2 |

## Full Module Ranking (Top 50)

| # | Module | Score | Lines | Tests | Import | Latency | Deps | Ready | Blockers |
|---|--------|-------|-------|-------|--------|---------|------|-------|----------|
| 1 | `advanced_sizing` | 10.0 | 330 | YES | OK | fast | 0 | YES | — |
| 2 | `auto_docs` | 10.0 | 520 | YES | OK | medium | 0 | YES | — |
| 3 | `crisis_hedge` | 10.0 | 343 | YES | OK | fast | 0 | YES | — |
| 4 | `earnings_alpha` | 10.0 | 487 | YES | OK | fast | 0 | YES | — |
| 5 | `feature_analysis` | 10.0 | 533 | YES | OK | medium | 0 | YES | — |
| 6 | `generate_docs` | 10.0 | 511 | YES | OK | medium | 0 | YES | — |
| 7 | `meta_learner_v2` | 10.0 | 379 | YES | OK | fast | 0 | YES | — |
| 8 | `module_health` | 10.0 | 348 | YES | OK | fast | 0 | YES | — |
| 9 | `position_reconciler` | 10.0 | 349 | YES | OK | fast | 0 | YES | — |
| 10 | `regime_ensemble_v2` | 10.0 | 534 | YES | OK | medium | 0 | YES | — |
| 11 | `regime_gate` | 10.0 | 357 | YES | OK | fast | 0 | YES | — |
| 12 | `regime_transition` | 10.0 | 488 | YES | OK | fast | 0 | YES | — |
| 13 | `retrain_scheduler` | 10.0 | 118 | YES | OK | fast | 0 | YES | — |
| 14 | `risk_gate` | 10.0 | 376 | YES | OK | fast | 0 | YES | — |
| 15 | `rl_position_sizer` | 10.0 | 418 | YES | OK | fast | 0 | YES | — |
| 16 | `sentiment_regime` | 10.0 | 449 | YES | OK | fast | 0 | YES | — |
| 17 | `signal_decay_analyzer` | 10.0 | 433 | YES | OK | fast | 0 | YES | — |
| 18 | `sizing` | 10.0 | 561 | YES | OK | medium | 0 | YES | — |
| 19 | `telegram_alerter` | 10.0 | 369 | YES | OK | fast | 0 | YES | — |
| 20 | `vix_term_structure` | 10.0 | 434 | YES | OK | fast | 0 | YES | — |
| 21 | `master_dashboard` | 9.8 | 518 | YES | OK | medium | 0 | YES | — |
| 22 | `adaptive_stoploss` | 9.5 | 548 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 23 | `adaptive_stops` | 9.5 | 594 | YES | OK | medium | 3 | YES | External deps: matplotlib, numpy, pandas |
| 24 | `alpha_combiner` | 9.5 | 844 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 25 | `alpha_research` | 9.5 | 974 | YES | OK | medium | 1 | YES | External deps: numpy |
| 26 | `anomaly_detector` | 9.5 | 458 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 27 | `backtest_compare` | 9.5 | 509 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 28 | `backtest_reality` | 9.5 | 722 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 29 | `backtest_reconciler` | 9.5 | 709 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 30 | `backtest_validator` | 9.5 | 901 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 31 | `backtest_vs_live_tracker` | 9.5 | 603 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 32 | `bayesian_selector` | 9.5 | 415 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 33 | `benchmark_pruned_features` | 9.5 | 414 | YES | OK | slow | 4 | YES | External deps: numpy, pandas, xgboost, sklearn |
| 34 | `calendar_effects` | 9.5 | 451 | YES | OK | medium | 3 | YES | External deps: numpy, pandas, scipy |
| 35 | `combined_portfolio_v2` | 9.5 | 407 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 36 | `config_optimizer` | 9.5 | 632 | YES | OK | slow | 3 | YES | External deps: numpy, pandas, sklearn |
| 37 | `corr_regime_detector` | 9.5 | 381 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 38 | `correlation_alpha` | 9.5 | 447 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 39 | `correlation_breakdown` | 9.5 | 915 | YES | OK | medium | 3 | YES | External deps: matplotlib, numpy, pandas |
| 40 | `correlation_monitor` | 9.5 | 491 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 41 | `crisis_hedge_monitor` | 9.5 | 512 | YES | OK | medium | 1 | YES | External deps: numpy |
| 42 | `crisis_hedge_v2` | 9.5 | 658 | YES | OK | medium | 1 | YES | External deps: numpy |
| 43 | `cross_asset_momentum` | 9.5 | 530 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 44 | `cross_asset_signal` | 9.5 | 825 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 45 | `cross_asset_signals` | 9.5 | 849 | YES | OK | medium | 4 | YES | External deps: matplotlib, numpy, pandas, scipy |
| 46 | `data_pipeline` | 9.5 | 609 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
| 47 | `data_quality` | 9.5 | 478 | YES | OK | medium | 3 | YES | External deps: matplotlib, numpy, pandas |
| 48 | `dependency_analyzer` | 9.5 | 370 | YES | OK | medium | 1 | YES | External deps: numpy |
| 49 | `deployment_validator` | 9.5 | 432 | YES | OK | medium | 1 | YES | External deps: requests |
| 50 | `dispersion_trader` | 9.5 | 478 | YES | OK | medium | 2 | YES | External deps: numpy, pandas |
