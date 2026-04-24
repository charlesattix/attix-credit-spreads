# Dead Code Inventory — compass/

**Generated:** 2026-04-24
**Total modules in compass/:** 408
**Production footprint:** 51 modules
**Archival candidates:** 357 modules

---

## Production Footprint (51 modules — DO NOT ARCHIVE)

These modules are reachable from the daily signal generation + execution pipeline
via verified import tracing. They are the production runtime.

### Tier 1 — Signal Generators (8 alpha streams)
| Module | Role |
|---|---|
| `exp1220_standalone` | SPY put credit spreads, 28 DTE, 5% OTM |
| `exp2160_high_capacity_alts` | XLF/XLI delta-targeted put credit spreads |
| `exp2240_qqq_iwm_credit_spreads` | QQQ put credit spreads |
| `exp1770_commodity_calendars` | GLD/SLV calendar spreads |
| `exp2020_cross_vol_arb` | SPY/QQQ/IWM/EEM IV-RV arbitrage |
| `crisis_alpha_v5` | 13-ETF CTA with stress gate |

### Tier 2 — Signal Registry & Orchestration
| Module | Role |
|---|---|
| `exp2690_signal_generators` | Central registry for 8 signal functions |
| `exp2830_paper_signal_generator` | Daily signal driver for paper trading |
| `exp2300_portfolio_runner` | Paper trading portfolio harness |
| `exp2600_north_star_v8` | 8-stream v8a portfolio cube builder |
| `exp2850_v8a_with_vix_ladder` | v8a walk-forward with VIX ladder integration |

### Tier 3 — Portfolio Overlays & Risk
| Module | Role |
|---|---|
| `portfolio_risk_manager` | EXP-1890: 5-component risk engine |
| `vix_ladder` | EXP-2820: 9-breakpoint VIX exposure ramp |
| `exp2370_dd_circuit_breaker` | 3%/12% drawdown circuit breaker |
| `regime` | Market regime classifier (BULL/BEAR/HIGH_VOL/LOW_VOL/CRASH) |
| `metrics` | Canonical Sharpe/CAGR/DD formulas |

### Tier 4 — Execution
| Module | Role |
|---|---|
| `alpaca_connector` | EXP-2890: Alpaca API integration scaffold |
| `dollar_notional_sizer` | Dollar-notional position sizing for T3+ scale |

### Tier 5 — Transitive Dependencies (imported by signal generators)
| Module | Role |
|---|---|
| `crisis_alpha` | Base crisis hedge framework (trend-following) |
| `crisis_alpha_v3` | v3 hedge: universe loading, lookback grid |
| `crisis_alpha_v4` | v4 hedge: signal confirmation logic |
| `exp1780_exp1220_integration` | EXP-1220 daily return builder for portfolio cube |
| `exp1960_skew_alpha` | SPY put-skew mean reversion (v5 component) |
| `exp1970_vol_of_vol` | VoV overlay gate for signal confidence |
| `exp1850_regime_portfolio` | Regime-aware stream loader for risk parity |
| `exp2080_corr_regime` | Correlation regime detector for stream loading |
| `greeks_sensitivity` | Black-Scholes helpers for options pricing |
| `exp1660_vrp_deepening` | VRP analysis (imported by signal generators) |
| `exp1740_sentiment_filter` | FOMC minutes parser (imported by overlays) |
| `exp1750_putcall_overlay` | Put/call ratio overlay (imported by overlays) |

### Tier 6 — Covariance & Walk-Forward Pipeline
| Module | Role |
|---|---|
| `exp2360_robust_cov` | Ledoit-Wolf covariance + risk-parity weights |
| `exp2390_robust_cov_audit` | Sparse XLF/XLI cube builder for honest audit |
| `exp2400_combined_best_of` | Ledoit-Wolf + DD circuit combo |
| `exp2420_transaction_costs` | Real bid-ask + commission + slippage model |
| `exp2450_sparse_combined_honest` | Sparse-cube walk-forward (honest edition) |
| `exp2200_north_star_v6` | 7-stream builder (imported by v8) |

### Tier 7 — Package Exports (compass/__init__.py)
| Module | Role |
|---|---|
| `__init__` | Package init — re-exports all below |
| `macro` | Macro snapshot engine |
| `macro_db` | Macro state database |
| `events` | FOMC calendar + event scaling |
| `risk_gate` | Risk gate logic |
| `sizing` | Kelly + IV-scaled position sizing |
| `signal_model` | Base signal model |
| `ensemble_signal_model` | Ensemble signal model (GAP-8) |
| `features` | Feature engineering pipeline |
| `iv_surface` | IV analyzer |
| `ml_strategy` | ML-enhanced strategy + RegimeModelRouter |
| `stress_test` | Stress tester + crisis scenarios |
| `crisis_hedge` | Crisis hedge controller (imported by ml_strategy) |
| `shadow_ensemble` | Shadow ensemble wrapper |
| `online_retrain` | Online retrain scheduler |

---

## Archival Candidates (357 modules)

**Safety key:**
- **SAFE** — No production imports found. Standalone script or prototype. Archive freely.
- **CAUTION** — Not in the import chain, but used for operational tooling (go/no-go checks, monitoring, paper trading validation). Archive only after confirming replacement exists.


### Sprint Experiments (EXP-NNNN) (56 modules)

| Module | Description | Safe? |
|---|---|---|
| `exp1220_slippage_analysis` | EXP-1220 Realistic Execution Slippage Analysis | SAFE |
| `exp1630_optimizer` | EXP-1630 Deep Optimization: Position sizing, leverage, multi-pair, regime filters, walk-forward | SAFE |
| `exp1770_commodity_spreads` | EXP-1770 — Commodity Spreads (ETF-ratio mean reversion) | SAFE |
| `exp1880_integrated_overlays` | EXP-1880 — Integrated FOMC + Put/Call Entry Overlays for EXP-1220 ===============================... | SAFE |
| `exp1980_dynamic_hedge` | EXP-1980 — Correlation Regime Switching / Dynamic Hedge Ratio  Hypothesis ---------- Crisis Alpha... | SAFE |
| `exp2000_triple_overlay` | EXP-2000 — Triple Overlay Stack on EXP-1220 | SAFE |
| `exp2010_tail_convexity` | EXP-2010 — Tail Risk Convexity (Long OTM Puts as Alpha) | SAFE |
| `exp2040_leveraged_calendars` | (no docstring) | SAFE |
| `exp2060_cross_vol_arb_v2` | EXP-2060 — Cross-Vol Arb v2 (VoV overlay + leverage + capacity + corr matrix) | SAFE |
| `exp2070_term_structure` | EXP-2070 — VIX Term Structure Alpha as an EXP-1220 Entry Overlay ================================... | SAFE |
| `exp2110_leveraged_diversified` | EXP-2110 — Leveraged Diversified Portfolio (CAGR sweet spot sweep) | SAFE |
| `exp2120_triple_overlay` | EXP-2120 — T+V+F Triple Overlay Integration for EXP-1220 ========================================... | SAFE |
| `exp2140_portfolio_capacity` | (no docstring) | SAFE |
| `exp2180_vol_targeting` | EXP-2180 — Volatility Targeting for Sharpe Boost  Hypothesis ---------- For a diversified portfol... | SAFE |
| `exp2210_xlf_xli_validation` | EXP-2210 — XLF/XLI Credit Spread Deep Validation | SAFE |
| `exp2220_seven_stream_corr` | EXP-2220 — Full Pairwise Correlation Matrix on the 7-Stream Portfolio ===========================... | SAFE |
| `exp2230_capacity_xlf_xli` | EXP-2230 — Updated Capacity Analysis with XLF + XLI (7-stream portfolio)  Hypothesis under test -... | SAFE |
| `exp2270_xlf_xli_slippage` | EXP-2270 — XLF / XLI Slippage Impact Analysis ==============================================  Que... | SAFE |
| `exp2280_wf_robustness` | EXP-2280 — Walk-Forward Robustness Audit of North Star v6 (equal_risk_15%)  Why this experiment e... | CAUTION |
| `exp2330_mc_stress_test` | (no docstring) | SAFE |
| `exp2340_dd_deep_dive` | EXP-2340 — Walk-Forward DD Deep Dive and Fix | SAFE |
| `exp2440_cost_aware_optimization` | EXP-2440 — Cost-Aware Portfolio Optimization | SAFE |
| `exp2470_execution_optimization` | EXP-2470 — Execution Optimization: Reducing Bid-Ask Spread & Slippage  Baseline (from EXP-2420, 3... | CAUTION |
| `exp2500_true_net_backtest` | EXP-2500 — TRUE Net Backtest with Cost-Aware Parameters | SAFE |
| `exp2510_broker_analysis` | EXP-2510 — Commission-Free Broker Analysis | SAFE |
| `exp2540_regime_tc_model` | (no docstring) | SAFE |
| `exp2550_net_sharpe_recovery` | EXP-2550 — Net Sharpe Recovery via Regime TC Filter + Circuit Breaker | SAFE |
| `exp2560_trade_frequency_compression` | EXP-2560 — Trade Frequency Compression for Cost Reduction | SAFE |
| `exp2570_commfree_net_sharpe` | EXP-2570 — Commission-Free Broker Net Sharpe =============================================  EXP-2... | CAUTION |
| `exp2580_spy_weekly_cs` | EXP-2580 — SPY Weekly Credit Spreads (separate stream from EXP-1220)  Hypothesis ---------- EXP-1... | SAFE |
| `exp2590_qqq_capacity_deep_dive` | (no docstring) | SAFE |
| `exp2610_spy_weekly_integration` | EXP-2610 — SPY Weekly Credit Spreads Integration | SAFE |
| `exp2620_alpaca_connector` | EXP-2620 — Alpaca Paper-Trading Connector for the 7-Stream Portfolio ============================... | SAFE |
| `exp2630_regime_stress_oos` | (no docstring) | SAFE |
| `exp2640_vix_stress_hardening` | EXP-2640 — VIX High-Vol Stress Hardening (gentler than EXP-2630 CB) | SAFE |
| `exp2650_multi_expiry_capacity` | EXP-2650 — AUM Capacity via Multi-Expiry Staggering | SAFE |
| `exp2660_aum_capacity_scaling` | EXP-2660 — AUM Capacity: Multi-Underlying Scaling Audit =========================================... | SAFE |
| `exp2670_paper_gonogo` | EXP-2670 — Paper Trading Go/No-Go Checklist  Pre-flight verification before Phase 9 paper trading... | CAUTION |
| `exp2700_reproducibility_audit` | EXP-2700 — Backtest Reproducibility Audit | SAFE |
| `exp2710_xle_integration` | EXP-2710 — XLE Credit-Spread Integration =========================================  EXP-2660 foun... | SAFE |
| `exp2720_dd_recovery` | EXP-2720 — Drawdown Recovery Analysis for North Star v8a  Question -------- For the v8a portfolio... | SAFE |
| `exp2730_wf_robustness_v8a_net` | EXP-2730 — Walk-Forward Robustness Deep Dive on v8a NET | CAUTION |
| `exp2740_sensitivity` | EXP-2740 — Sensitivity Analysis on the Production Net-Sharpe 6 | SAFE |
| `exp2750_oos_regime_stress` | EXP-2750 — Out-of-Distribution Regime Stress Test ===============================================... | SAFE |
| `exp2800_sharpe_buffer_expansion` | EXP-2800 — Sharpe Buffer Expansion via XLE 9th Stream | SAFE |
| `exp2810_9stream_portfolio` | EXP-2810 — Revised 9-Stream Portfolio (SLV/XLI cut + SPY-Weekly added) | SAFE |
| `exp2820_flash_crash_protection` | EXP-2820 — Flash Crash Protection ===================================  EXP-2750 found that the ba... | CAUTION |
| `exp2840_backtest_to_live_degradation` | (no docstring) | SAFE |
| `exp2860_paper_dry_run` | EXP-2860 — Paper Trading Dry Run (end-to-end signal → mock Alpaca) | CAUTION |
| `exp2870_investor_overview` | EXP-2870 — Investor Overview Document Generator =================================================... | SAFE |
| `exp2900_v8a_consistency_audit` | EXP-2900 — v8a Portfolio Consistency Audit | CAUTION |
| `exp2910_industry_comparison` | EXP-2910 — Backtest-vs-Reality Industry Comparison | SAFE |
| `exp2910_tlt_credit_spreads` | EXP-2910 — TLT Put Credit Spread Integration  Hypothesis: TLT put credit spreads (28 DTE, 5% OTM)... | SAFE |
| `exp2920_monitor_core` | EXP-2920 — Paper-Trading Monitoring Core ==========================================  Reference im... | CAUTION |
| `exp2920_tlt_ivrv_arb` | EXP-2920 — TLT IV-RV Arbitrage via MOVE Index  Hypothesis: When MOVE (bond implied vol) is elevat... | SAFE |
| `exp2950_sector_momentum` | EXP-2950 — Sector Momentum Rotation Strategy  Hypothesis: A long-short sector rotation using 11 S... | SAFE |

### Alpha Research & Discovery (49 modules)

| Module | Description | Safe? |
|---|---|---|
| `alpha_combiner` | Alpha signal combiner — merges multiple alpha signals into a composite | SAFE |
| `alpha_research` | (no docstring) | SAFE |
| `calendar_effects` | Calendar effects alpha engine | SAFE |
| `correlation_alpha` | Cross-asset correlation alpha — detects short-term correlation breakdowns between major asset pai... | SAFE |
| `crisis_alpha_v2` | EXP-1780 v2 — Crisis Alpha with boosted CAGR  Improvements over v1:   1 | SAFE |
| `cross_asset_momentum` | Cross-asset momentum signal engine | SAFE |
| `cross_asset_signal` | Cross-asset signal generator | SAFE |
| `cross_asset_signals` | Cross-asset signal generator for multi-asset alpha | SAFE |
| `discovery_round3` | Strategy Discovery Round 3 — find uncorrelated alpha to EXP-1220 | SAFE |
| `dispersion` | EXP-1820: Dispersion-Inspired Relative Vol Premium ==============================================... | SAFE |
| `dispersion_trader` | Dispersion trading engine — captures the correlation risk premium by selling index volatility and... | SAFE |
| `earnings_alpha` | Earnings event alpha: IV crush credit spread strategy | SAFE |
| `earnings_crush` | (no docstring) | SAFE |
| `earnings_iv_crush` | EXP-1800 — Earnings / Event IV Crush Strategy  ══════════════════════════════════════════════════... | SAFE |
| `earnings_vol_crush` | (no docstring) | SAFE |
| `gamma_scalp` | (no docstring) | SAFE |
| `gld_tlt_relval` | EXP-1630: GLD/TLT Relative Value Spread Strategy | SAFE |
| `iv_spike_entry` | EXP-1840: IV Spike Entry — Volatility-Timed Credit Spread Overlay ===============================... | SAFE |
| `mean_reversion_zscore` | Mean reversion z-score strategy — Bollinger-style z-score with RSI divergence confirmation and vo... | SAFE |
| `momentum_rotation` | EXP-1830 — Momentum Factor Rotation (Sector ETFs + TLT + GLD)  Academic basis: Jegadeesh & Titman... | SAFE |
| `options_flow_sentiment` | Options flow sentiment engine — aggregates flow metrics to predict short-term SPY direction | SAFE |
| `options_strategy` | Options strategy construction engine | SAFE |
| `overnight_drift` | EXP-1790 — Overnight Drift Strategy  Academic edge (Cooper 2008, Kelly-Clark 2011, Lou-Polk-Skour... | SAFE |
| `overnight_gap` | Overnight gap strategy — exploits the overnight risk premium in SPY options by selling straddles ... | SAFE |
| `overnight_risk` | (no docstring) | SAFE |
| `pair_trading` | Statistical pair trading engine | SAFE |
| `pairs_deep_validation` | Deep Validation: Cross-Asset Pairs Mean-Reversion (TLT-SPY Correlation Breakdown) | SAFE |
| `pairs_options` | Pairs trading for options — cointegrated pair signals monetised via credit spreads on the divergi... | SAFE |
| `research_pipeline` | Automated research pipeline for signal discovery and validation | SAFE |
| `sector_pairs` | EXP-1720 Sector ETF Pairs Trading | SAFE |
| `sentiment_alpha` | Sentiment-driven alpha engine | SAFE |
| `sentiment_engine` | NLP sentiment signal engine for trading | SAFE |
| `sentiment_regime` | Sentiment regime detector: composite fear/greed index with changepoint detection | SAFE |
| `sentiment_signal` | Multi-source sentiment signal aggregator | SAFE |
| `signal_researcher` | Automated signal research and discovery engine | SAFE |
| `strategy_discovery_r2` | Strategy Discovery Round 2 — test novel strategy types on real IronVault data | SAFE |
| `strategy_discovery_r3` | Strategy Discovery Round 3 — find NEW uncorrelated alpha sources | SAFE |
| `strategy_discovery_r4` | Strategy Discovery Round 4 — genuinely NEW uncorrelated alpha sources | SAFE |
| `strategy_generator` | Automated strategy generation and screening pipeline | SAFE |
| `strategy_screener` | Automated strategy screener — test 50+ strategies/day instead of 14/week | SAFE |
| `trade_clustering` | Trade clustering analyzer — unsupervised discovery of trade archetypes | SAFE |
| `treasury_curve` | EXP-1730 Wave 2: Treasury Curve Mean Reversion ============================================== Wav... | SAFE |
| `vix_roll_yield` | (no docstring) | SAFE |
| `vix_roll_yield_v2` | (no docstring) | SAFE |
| `vix_term_structure` | VIX term structure trading engine | SAFE |
| `vol_forecaster` | Volatility forecaster — EWMA, GARCH(1,1), IV/RV spread analysis, regime classification | SAFE |
| `vol_surface` | Implied volatility surface modeler | SAFE |
| `vol_surface_trader` | Systematic volatility surface trading | SAFE |
| `vrp_production` | (no docstring) | SAFE |

### Portfolio Construction Prototypes (34 modules)

| Module | Description | Safe? |
|---|---|---|
| `adaptive_stoploss` | Adaptive stop-loss optimizer — 5 stop types with regime-conditional multipliers and walk-forward ... | SAFE |
| `adaptive_stops` | Regime-aware adaptive stop loss optimizer | SAFE |
| `capacity_analyzer` | (no docstring) | SAFE |
| `capital_utilization` | (no docstring) | SAFE |
| `combined_portfolio_v2` | Combined Portfolio V2 — multi-strategy portfolio combining uncorrelated alpha streams with optimi... | SAFE |
| `correlation_analysis` | Cross-experiment correlation analysis for Phase 5 portfolio optimization | SAFE |
| `correlation_breakdown` | Correlation breakdown analyzer for credit spread portfolios | SAFE |
| `dynamic_hedging` | Dynamic hedging engine for options portfolios | SAFE |
| `factor_model` | Multi-factor risk model for portfolio construction — fundamental and statistical factors, cross-s... | SAFE |
| `greeks_calculator` | Options Greeks calculator with portfolio aggregation and risk limits | SAFE |
| `mc_portfolio_optimizer` | Monte Carlo portfolio optimizer with efficient frontier, regime-conditional allocation, and compr... | SAFE |
| `multi_asset_portfolio_v2` | Multi-Asset Portfolio v2 — Honest Rebuild with Backfilled Data ==================================... | SAFE |
| `multi_strategy_portfolio` | Multi-Strategy Portfolio — combines 4 real-data validated strategies | SAFE |
| `multi_timeframe` | Multi-timeframe signal aggregator for credit spread portfolios | SAFE |
| `north_star_gap` | North Star gap analyzer — measures where we stand vs targets | SAFE |
| `north_star_portfolio` | (no docstring) | SAFE |
| `north_star_portfolio_v1_invvol` | (no docstring) | SAFE |
| `north_star_portfolio_v3` | EXP-1860 — North Star Portfolio v3 (Wave 1+2 winners combined) | SAFE |
| `optimal_portfolio_v3` | Optimal Portfolio Construction V3 — North Star synthesis | SAFE |
| `performance_attribution` | Performance attribution engine — decomposes portfolio returns into actionable sources of alpha an... | SAFE |
| `phase5_optimization` | Phase 5 — Portfolio Optimization Across EXP-400/401/503/600  Runs the full PortfolioOptimizer pip... | SAFE |
| `portfolio_analytics` | Comprehensive portfolio analytics module | SAFE |
| `portfolio_attribution` | Portfolio performance attribution engine | SAFE |
| `portfolio_constructor` | Advanced portfolio construction with multiple optimisation methods | SAFE |
| `portfolio_optimizer` | Cross-Experiment Portfolio Optimizer ===================================== Allocates capital acro... | SAFE |
| `portfolio_simulator` | (no docstring) | SAFE |
| `regime_performance` | Comprehensive regime performance analysis for the multi-strategy portfolio | SAFE |
| `regime_portfolio` | Regime-adaptive portfolio allocator for the Ultimate Portfolio | SAFE |
| `sharpe_optimizer` | Sharpe ratio attribution, decomposition, and optimization | SAFE |
| `spy_only_portfolio` | SPY-ONLY Production Portfolio — requires ZERO multi-asset data | SAFE |
| `target_optimizer` | North Star target optimizer — identifies gaps and improvement opportunities | SAFE |
| `trade_cost_analyzer` | Comprehensive trade cost analyzer for credit spread portfolios | SAFE |
| `turnover_optimizer` | Trade turnover and rebalancing cost optimizer | SAFE |
| `universal_portfolio` | Universal Portfolio — Cover's log-optimal meta-allocation via the Exponential Gradient (EG) algor... | SAFE |

### Risk & Hedging Prototypes (40 modules)

| Module | Description | Safe? |
|---|---|---|
| `crisis_hedge_monitor` | Real-time crisis hedge monitoring dashboard — tracks VIX tiers, scale adjustments, hedge cost vs ... | SAFE |
| `crisis_hedge_v2` | Crisis Hedge Controller V2 — extends V1 with gradual delevering, put spread overlay, recovery det... | SAFE |
| `drawdown_analyzer` | Drawdown analyzer – comprehensive drawdown analysis with regime attribution, conditional drawdown... | SAFE |
| `drawdown_predictor` | Drawdown recovery prediction engine | SAFE |
| `drawdown_protection` | Dynamic drawdown protection system | SAFE |
| `drawdown_recovery` | Drawdown recovery prediction engine – survival analysis, regime-conditional recovery expectations... | SAFE |
| `dynamic_hedge` | Dynamic hedging engine — computes optimal hedge ratios in real-time for VIX call overlays, SPY de... | SAFE |
| `dynamic_leverage_hedged` | (no docstring) | SAFE |
| `full_stress_report` | Phase 6 comprehensive stress testing pipeline | SAFE |
| `greeks_risk_engine` | Real-time Greeks risk engine for the combined portfolio | SAFE |
| `hedge_cost_reality` | Hedge cost reality check — validate the 2%/yr flat assumption against REAL SPY put prices from Ir... | SAFE |
| `hedge_param_sweep` | (no docstring) | SAFE |
| `hedge_v5_sweep` | 2x + Crisis Alpha hedge sweep — v4 vs v5 side-by-side | SAFE |
| `momentum_crash_protector` | Momentum crash protector — detects crowding and reversal risk | SAFE |
| `monte_carlo_north_star` | Monte Carlo stress testing for the North Star 4-strategy portfolio | SAFE |
| `north_star_stress_test` | EXP-1870 — North-Star Combined Portfolio Stress Test | SAFE |
| `north_star_validator` | North Star validation suite — rigorous stress-testing of the optimal 4-strategy blend from EXP-1470 | SAFE |
| `portfolio_3x_hedged` | EXP-1220 @ 3x + Crisis Alpha v4 Hedge ======================================= Goal: Combine EXP-1... | SAFE |
| `portfolio_stress` | Advanced portfolio stress testing — historical and synthetic scenarios, reverse stress testing, P... | SAFE |
| `portfolio_stress_test` | Monte Carlo stress test of EXP-1220 + EXP-1780 + EXP-1820 + EXP-1660 portfolio | SAFE |
| `protected_portfolio` | Ultimate Portfolio Hedged v3 — COVID DD < 12% target | SAFE |
| `regime_hedge` | Regime-adaptive hedging engine | SAFE |
| `risk_aggregator` | (no docstring) | SAFE |
| `risk_budget` | Risk budget allocator — distributes portfolio risk across experiments | SAFE |
| `risk_budget_allocator` | Risk budget allocator — distributes portfolio risk across experiments | SAFE |
| `risk_dashboard` | Real-time portfolio risk dashboard — VaR, CVaR, stress tests, Greeks, concentration, margin, and ... | SAFE |
| `risk_decomposition` | (no docstring) | SAFE |
| `risk_limits` | Dynamic risk limit engine — adaptive limits based on regime, volatility, and drawdown state with ... | SAFE |
| `risk_orchestrator` | Unified risk management orchestrator | SAFE |
| `risk_overlay` | Unified Risk Management Overlay | SAFE |
| `risk_parity` | Risk parity portfolio optimizer — dedicated module | SAFE |
| `risk_stress` | Risk stress testing engine for credit spread portfolios | SAFE |
| `run_stress_test` | (no docstring) | SAFE |
| `scenario_analyzer` | What-if scenario analysis engine – historical and custom scenario replay with portfolio impact es... | SAFE |
| `smart_hedge` | Smart Hedge — Cost-Efficient Tail Risk Protection ===============================================... | SAFE |
| `stress_scenario` | Advanced stress scenario engine — predefined and custom scenarios with correlated multi-asset str... | SAFE |
| `tail_risk` | Tail risk analyzer – CVaR, EVT/GPD fitting, stress VaR, and per-experiment tail contribution for ... | SAFE |
| `tail_risk_hedge` | Dynamic tail risk hedging for the Ultimate Portfolio | SAFE |
| `tail_risk_protector` | Tail risk protection system — multi-signal crash detector with graduated hedging that activates B... | SAFE |
| `ultimate_portfolio_v6` | Ultimate Portfolio v6 — Dynamic Leverage + Collar Hedge + Regime Filter =========================... | SAFE |

### ML/Feature Engineering (Phase 1-4) (27 modules)

| Module | Description | Safe? |
|---|---|---|
| `bayesian_selector` | Bayesian strategy selection via Thompson Sampling — models each strategy as a bandit arm with Nor... | SAFE |
| `config_optimizer` | Bayesian configuration optimizer – Gaussian process surrogate model for experiment parameter tuni... | SAFE |
| `data_pipeline` | Automated data pipeline manager — scheduled collection, validation, feature computation, incremen... | SAFE |
| `ensemble_model_health` | Ensemble model health monitor for live paper trading | SAFE |
| `feature_analysis` | Feature importance, signal decay, and redundancy analysis | SAFE |
| `feature_importance` | Walk-forward feature importance analysis for COMPASS signal models | SAFE |
| `feature_pipeline` | Feature Pipeline — stationary, normalized features for ML models | SAFE |
| `feature_store` | Feature store manager — centralized feature versioning and lineage tracking | SAFE |
| `genetic_evolver` | Genetic algorithm strategy evolver | SAFE |
| `meta_learner` | Ensemble meta-learner — learns optimal model combination weights | SAFE |
| `meta_learner_v2` | Ensemble meta-learner V2: gradient-boosted stacking of 10+ signal generators | SAFE |
| `model_diagnostics` | Model diagnostics dashboard — self-contained HTML report with embedded charts | SAFE |
| `model_monitor` | Model monitoring — drift detection, accuracy tracking, and alerting | SAFE |
| `pnl_predictor` | Pre-trade P&L prediction engine — gradient boosted model predicting trade P&L distribution from m... | SAFE |
| `production_ensemble` | Production ensemble pipeline — walk-forward retraining with confidence grading, disagreement sizi... | SAFE |
| `realtime_pipeline` | Real-time signal generation pipeline — streaming data ingestion, feature computation, ensemble in... | SAFE |
| `regime_ensemble` | Ensemble regime detection — combines HMM, change-point detection, volatility clustering, trend/me... | SAFE |
| `regime_ensemble_v2` | Adaptive Regime Ensemble V2: meta-ensemble of 4 regime detectors | SAFE |
| `regime_forecast` | Regime forecaster — predicts next market regime using transition probabilities, macro leading ind... | SAFE |
| `regime_hmm` | Market regime transition probabilities via Hidden Markov Model | SAFE |
| `regime_predictor` | Market regime predictor – forecasts regime transitions using macro features with multi-horizon pr... | SAFE |
| `retrain_scheduler` | Retrain Scheduler — wires ModelRetrainer into the daily scan scheduler | SAFE |
| `rl_portfolio_manager` | Reinforcement learning portfolio manager — lightweight PPO agent | SAFE |
| `rl_position_sizer` | Reinforcement learning position sizer | SAFE |
| `signal_ensemble` | Signal ensemble — combine multiple alpha signals into a composite | SAFE |
| `strategy_ensemble` | Strategy ensemble combiner for credit spread experiments | SAFE |
| `transformer_predictor` | Lightweight transformer for next-day SPY direction prediction | SAFE |

### Backtesting & Validation Tools (34 modules)

| Module | Description | Safe? |
|---|---|---|
| `backtest_auditor` | (no docstring) | SAFE |
| `backtest_compare` | (no docstring) | SAFE |
| `backtest_reality` | Backtest reality checker -- detect biases, unrealistic assumptions, and over-fitting indicators i... | SAFE |
| `backtest_reconciler` | Backtest reconciliation tool — compares backtest vs paper trading results | SAFE |
| `backtest_validator` | Backtest validation suite – detects common backtesting pitfalls, overfitting, and statistical ano... | SAFE |
| `backtest_vs_live_tracker` | Backtest vs Live Tracker — compares paper trading results against backtest expectations in real-time | SAFE |
| `benchmark_cs_only` | CS-Only Benchmark: XGBoost vs Ensemble vs Baseline (No Model)  Walk-forward validation on credit-... | SAFE |
| `benchmark_ensemble_vs_xgboost` | Benchmark: EnsembleSignalModel vs standalone XGBoost on combined training data | SAFE |
| `benchmark_per_regime` | Per-Regime Benchmark: Ensemble vs XGBoost ==========================================  Runs full w... | SAFE |
| `benchmark_pruned_features` | Benchmark: Pruned features vs full feature set (walk-forward, 5-fold) | SAFE |
| `benchmark_tier1_features` | Tier 1 Feature Benchmark: Original vs Original+Tier1 features ===================================... | SAFE |
| `collect_training_data` | COMPASS ML Training Data Collection  Runs backtests (EXP-400 or EXP-401 config) and captures EVER... | SAFE |
| `module_auditor` | Meta-audit of all compass modules for quality and completeness | SAFE |
| `new_strategy_explorer` | New strategy exploration — backtest 4 uncorrelated strategies using ONLY real IronVault data | SAFE |
| `north_star_backtest` | North Star backtest — end-to-end validation pipeline | SAFE |
| `north_star_integrator` | North Star integrator — master integration combining best modules into a backtestable system targ... | SAFE |
| `north_star_real_backtest` | North Star Portfolio — Real IronVault Backtest (EXP-1470-real) | SAFE |
| `north_star_v4_audit` | North Star Portfolio v4 — Sharpe discrepancy audit (EXP-1860 vs EXP-1870) | SAFE |
| `oos_integrity_audit` | OOS Integrity Audit — All Real-Data Experiments | SAFE |
| `paper_reconciler` | Paper Trading Reconciler V2 — compares live paper trading results against backtest predictions wi... | CAUTION |
| `perf_benchmark` | Performance benchmarking for critical-path compass modules | SAFE |
| `production_audit` | Production readiness auditor for all compass modules | SAFE |
| `production_portfolio_wf` | Production-grade combined portfolio walk-forward backtest | SAFE |
| `regime_backtest` | (no docstring) | SAFE |
| `rl_executor` | Reinforcement learning execution agent — Q-learning with experience replay for optimal order exec... | SAFE |
| `signal_backtester` | Rapid signal backtesting framework — vectorised evaluation of trading signals | SAFE |
| `systematic_backtest` | (no docstring) | SAFE |
| `ultimate_portfolio_hedged` | Ultimate Portfolio + Tail Risk Hedge — Integrated Walk-Forward Backtest | SAFE |
| `unified_backtest` | Unified backtesting engine — ties ALL compass modules into a single pipeline | SAFE |
| `walk_forward` | Walk-Forward Validation Framework for COMPASS ML Models  Chronological expanding-window validatio... | CAUTION |
| `walk_forward_portfolio` | Walk-forward out-of-sample portfolio validation | SAFE |
| `walkforward_yearly` | Walk-Forward Year-by-Year Performance — EXP-1580 | SAFE |
| `wf_ensemble_optimizer` | Walk-forward ensemble optimizer — expanding-window optimization of strategy weights that maximize... | SAFE |
| `wf_stability_deep_dive` | Walk-Forward Stability Deep Dive — v8a + VIX Ladder | SAFE |

### Execution & Sizing Prototypes (27 modules)

| Module | Description | Safe? |
|---|---|---|
| `advanced_sizing` | (no docstring) | SAFE |
| `dynamic_kelly` | Dynamic Kelly Criterion — adaptive position sizing using rolling win rate and payoff ratio with r... | SAFE |
| `dynamic_sizing` | Dynamic position sizing framework for the Ultimate Portfolio | SAFE |
| `execution_algo` | Smart execution algorithm engine — TWAP, VWAP, Implementation Shortfall, Iceberg orders, adaptive... | SAFE |
| `execution_analytics` | Comprehensive execution cost modeling and market impact analysis | SAFE |
| `execution_analyzer` | Execution quality analyzer — READ-ONLY analysis of paper trading fills | SAFE |
| `execution_cost_model` | Realistic Execution Cost Model for the Ultimate Portfolio | CAUTION |
| `execution_feasibility` | Execution Feasibility Study — Ultimate Portfolio at Scale | SAFE |
| `execution_quality` | (no docstring) | SAFE |
| `execution_simulator` | Trade execution simulator with realistic option order fill modeling | SAFE |
| `exp_execution_timing_analysis` | (no docstring) | SAFE |
| `fill_analytics` | Fill quality analytics engine — measures how well trades were executed | SAFE |
| `greeks_trade_sizer` | Adaptive Greeks-based trade sizing | SAFE |
| `iron_condor_optimizer` | Iron Condor Strategy Optimizer — Scale & Optimize across tickers, sizing, spread widths, DTE rang... | SAFE |
| `liquidity_analyzer` | Liquidity analyzer – estimates strategy capacity, fill quality, market impact, and volume partici... | SAFE |
| `liquidity_sizer` | Liquidity-aware position sizing engine | SAFE |
| `live_bridge` | Live trading bridge — connects compass signals to broker execution | SAFE |
| `live_sim_engine` | Live trading simulation engine — realistic fill simulation with slippage, partial fills, queue pr... | SAFE |
| `live_trading_blueprint` | Live trading integration blueprint — bridge from strategy signals to broker execution with full r... | SAFE |
| `margin_analyzer` | Margin efficiency analyzer for credit spread portfolio | SAFE |
| `order_flow_alpha` | Order flow imbalance alpha — daily OFI from OHLCV proxy | SAFE |
| `order_flow_analyzer` | Order flow analysis engine | SAFE |
| `order_manager` | Order management system – lifecycle tracking, smart routing, batching, execution cost tracking, o... | SAFE |
| `slippage_model` | Advanced slippage modeling engine | CAUTION |
| `smart_execution` | Smart execution engine — TWAP, VWAP, and adaptive execution algorithms | SAFE |
| `smart_router` | (no docstring) | SAFE |
| `vrp_harvester` | Volatility risk premium harvester — multi-tenor VRP with regime sizing | SAFE |

### Monitoring & Dashboards (31 modules)

| Module | Description | Safe? |
|---|---|---|
| `adaptive_1dte` | EXP-1710 Adaptive — Rolling Sharpe Monitor + Regime Filter + Portfolio Combo ====================... | SAFE |
| `correlation_monitor` | Portfolio correlation monitor — detects diversification breakdown and auto-suggests delevering du... | CAUTION |
| `crisis_alpha_production` | EXP-1780 Crisis Alpha — PRODUCTION VERSION ============================================ The deplo... | SAFE |
| `data_quality` | Data quality monitoring system | SAFE |
| `deploy_checklist` | (no docstring) | CAUTION |
| `deployment_validator` | Pre-flight deployment validator for paper trading | CAUTION |
| `dispersion_strategy` | EXP-1820 Dispersion Strategy — Production Version ===============================================... | SAFE |
| `execution_optimizer` | Execution Optimizer – pre-trade cost estimation, algorithm selection, smart venue routing, real-t... | SAFE |
| `experiment_dashboard` | Unified experiment dashboard aggregator – pulls data from all other dashboards and reports into a... | SAFE |
| `honest_dashboard` | Honest North Star Dashboard — for Carlos | SAFE |
| `liquidity_risk` | Liquidity risk monitoring system | SAFE |
| `live_correlation_monitor` | Live strategy correlation monitor — real-time diversification tracking | CAUTION |
| `master_dashboard` | (no docstring) | SAFE |
| `module_health` | Module health checker for the compass package | SAFE |
| `north_star_dashboard` | (no docstring) | SAFE |
| `north_star_deployer` | North Star portfolio deployment engine — orchestrates the 4-strategy blend for paper trading with... | SAFE |
| `north_star_tracker` | North Star progress tracker — monitors all MASTERPLAN targets | SAFE |
| `paper_monitor_dashboard` | Paper trading monitoring dashboard — unified HTML view for EXP-880 standalone and EXP-1470 combin... | CAUTION |
| `paper_tracker` | Paper trading performance tracker — READ-ONLY reporting dashboard | CAUTION |
| `paper_trading_engine` | Paper trading engine — forward-testing framework with realistic execution | CAUTION |
| `paper_trading_monitor` | (no docstring) | CAUTION |
| `paper_trading_v4` | Paper trading harness for Ultimate Portfolio v4 | SAFE |
| `portfolio_dashboard` | (no docstring) | SAFE |
| `portfolio_rebalancer` | Portfolio rebalancer — computes optimal rebalance trades and monitors drift | SAFE |
| `position_reconciler` | Position reconciler — compares internal paper-trade position tracker against broker-reported stat... | CAUTION |
| `prod_monitor` | Real-time production monitoring system | CAUTION |
| `production_monitor` | Real-time production monitoring dashboard for credit spread strategies | CAUTION |
| `strategy_decay_monitor` | Strategy decay monitor — detects alpha decay and manages lifecycle transitions | SAFE |
| `telegram_alerter` | Unified Telegram alert system for paper trading | SAFE |
| `test_health` | Test suite health analyzer | SAFE |
| `trade_cadence_analyzer` | EXP-1220 Trade Cadence Analyzer — optimal deployment frequency | SAFE |

### Infrastructure & Tooling (13 modules)

| Module | Description | Safe? |
|---|---|---|
| `auto_docs` | Auto-documentation generator for COMPASS modules | SAFE |
| `dependency_analyzer` | Import dependency analyzer for compass modules | SAFE |
| `experiment_auto` | Automated experiment pipeline — unified spec, run, score, register, report | SAFE |
| `experiment_compare` | Experiment comparison module for comparing multiple trading experiments | SAFE |
| `experiment_launcher` | (no docstring) | SAFE |
| `experiment_manager` | Experiment lifecycle manager — register, track, compare, and promote experiments through a struct... | SAFE |
| `experiment_pipeline` | (no docstring) | SAFE |
| `experiment_ranker` | Multi-criteria experiment ranking system | SAFE |
| `experiment_runner` | (no docstring) | SAFE |
| `generate_docs` | COMPASS auto-documentation generator | SAFE |
| `master_runner` | Master orchestration engine — runs the full COMPASS pipeline | SAFE |
| `pipeline_validator` | Production pipeline validator — end-to-end validation of the COMPASS pipeline | SAFE |
| `system_integration` | Full system integration engine — wires compass modules into an end-to-end pipeline and verifies e... | SAFE |

### Crypto/IBIT (abandoned vertical) (5 modules)

| Module | Description | Safe? |
|---|---|---|
| `collect_ibit_training_data` | IBIT ML Training Data Collection for EXP-601 | SAFE |
| `crypto_vol_strategy` | EXP-1810: Crypto Volatility Deep Dive — IBIT Credit Spread Feasibility  Question: Does crypto vol... | SAFE |
| `ibit_credit_spread` | (no docstring) | SAFE |
| `ibit_features` | IBIT-specific feature engineering for EXP-601 ML Signal Filter | SAFE |
| `ibit_signal_model` | IBIT-specific ML signal model for EXP-601 | SAFE |

### Intraday Strategies (not deployed) (12 modules)

| Module | Description | Safe? |
|---|---|---|
| `intraday_features` | Intraday feature engineering for signal enhancement | SAFE |
| `intraday_momentum` | Intraday momentum feature engineering and scalping signal generation | SAFE |
| `intraday_mr` | Intraday Mean Reversion on SPY — Overnight Gap Fade  Edge hypothesis: When SPY opens materially a... | SAFE |
| `intraday_patterns` | Intraday pattern analyzer – identifies time-of-day edges, day-of-week patterns, opening vs closin... | SAFE |
| `intraday_vol_clustering` | Intraday volatility clustering — detect vol expansion/contraction transitions within the trading ... | SAFE |
| `market_maker` | Market-making strategy simulator | SAFE |
| `market_making_sim` | Market-making simulator with Avellaneda-Stoikov optimal quoting | SAFE |
| `microstructure` | Market microstructure analysis engine | SAFE |
| `microstructure_alpha` | Microstructure alpha scanner — liquidity regime detection and signals | SAFE |
| `microstructure_analyzer` | Market microstructure analyzer — bid-ask spreads, price impact, order flow toxicity, intraday pat... | SAFE |
| `multi_timeframe_fusion` | Multi-timeframe signal fusion — combines intraday (5min), daily (1D), and weekly (1W) signals via... | SAFE |
| `zero_dte_ic` | EXP-1710: 1-3 DTE SPY Iron Condors (pivoted from 0DTE SPX) ======================================... | SAFE |

### Other (29 modules)

| Module | Description | Safe? |
|---|---|---|
| `anomaly_detector` | Anomaly detection system – detects anomalous market conditions and trade outcomes using z-score a... | SAFE |
| `cadence_optimization` | Trade cadence optimization for EXP-1220 — validated on real IronVault data | SAFE |
| `corr_regime_detector` | Correlation regime detector — early warning via absorption ratio | SAFE |
| `correlation_analyzer` | Correlation Matrix Analyzer — All 13 Real-Data Validated Strategies =============================... | SAFE |
| `dynamic_leverage` | Dynamic leverage manager for EXP-1220 Tail Risk Protection | SAFE |
| `dynamic_leverage_v2` | (no docstring) | SAFE |
| `dynamic_leverage_v3` | (no docstring) | SAFE |
| `event_calendar` | Event calendar engine for systematic event trading | SAFE |
| `event_impact` | Event impact analyzer – measures how macro events affect credit spread outcomes | SAFE |
| `factor_exposure` | Factor exposure analyzer — decomposes strategy returns into standard risk factor exposures and ge... | SAFE |
| `harvest_trades_v2` | Mass Trade Harvester V2 — Diverse ML Training Data Collection  Runs ~63 different parameter confi... | SAFE |
| `position_risk` | (no docstring) | SAFE |
| `realtime_pnl` | Real-time P&L estimator with Black-Scholes Greek attribution | SAFE |
| `regime_gate` | Regime Gate — CS entry filter based on market regime | SAFE |
| `regime_transition` | Hidden Semi-Markov regime transition predictor | SAFE |
| `regime_transitions` | (no docstring) | SAFE |
| `signal_decay` | Signal decay analyzer – measures ML signal quality degradation over time | SAFE |
| `signal_decay_analyzer` | Signal decay half-life analyzer | SAFE |
| `signal_pipeline` | Real-time signal generation pipeline for live paper trading | SAFE |
| `signal_quality_scorer` | Real-time signal quality assessment across experiments | SAFE |
| `strategy_correlation` | Cross-strategy correlation and diversification analysis | SAFE |
| `strategy_factory` | Automated strategy generation factory | SAFE |
| `strategy_report` | (no docstring) | SAFE |
| `strategy_switcher` | Strategy switching engine — regime-based rotation across strategies | SAFE |
| `trade_clusters` | Trade clustering analyzer — alias module | SAFE |
| `trade_flow` | Institutional trade flow analyzer | SAFE |
| `trade_journal` | Trade journal and analytics — post-trade analysis and reporting | SAFE |
| `trade_outcome_predictor` | Trade outcome predictor — pre-entry P&L prediction with similar-trade matching | SAFE |
| `vol_term_structure_deep_dive` | Vol Term Structure Strategy — Deep Dive | SAFE |

---

## Summary Statistics

| Category | Count | % of Dead |
|---|---|---|
| Sprint Experiments (EXP-NNNN) | 56 | 16% |
| Alpha Research & Discovery | 49 | 14% |
| Portfolio Construction Prototypes | 34 | 10% |
| Risk & Hedging Prototypes | 40 | 11% |
| ML/Feature Engineering (Phase 1-4) | 27 | 8% |
| Backtesting & Validation Tools | 34 | 10% |
| Execution & Sizing Prototypes | 27 | 8% |
| Monitoring & Dashboards | 31 | 9% |
| Infrastructure & Tooling | 13 | 4% |
| Crypto/IBIT (abandoned vertical) | 5 | 1% |
| Intraday Strategies (not deployed) | 12 | 3% |
| Other | 29 | 8% |
| **Total** | **357** | **100%** |

## Archive Recommendations

1. **Immediate archive (SAFE, no dependencies):** 340+ modules. Move to `compass/_archive/` or a separate branch.
2. **Review before archive (CAUTION):** ~17 modules used for operational tooling. Confirm replacements exist before archiving.
3. **Keep __init__.py exports:** The `__init__.py` imports 15 modules. If any archived module is in `__init__.py`, update the init to remove the import or it will break `import compass`.
4. **Suggested approach:** `git mv compass/<module>.py compass/_archive/<module>.py` preserves history.

## Methodology

Production footprint identified via:
1. MASTERPLAN.md §Architecture — 8 alpha streams, portfolio overlays, execution stack
2. Forward import tracing from 5 entry points: `exp2690_signal_generators`, `exp2830_paper_signal_generator`, `exp2300_portfolio_runner`, `alpaca_connector`, `compass/__init__.py`
3. Transitive closure: 2-level BFS on `from compass.X import` statements
4. Verified no reverse imports (dead modules importing production modules does not make them production)

---

*Generated 2026-04-24 by Maximus*

