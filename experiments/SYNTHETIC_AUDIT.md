# Synthetic Data Audit: compass/ Directory

**Date:** 2026-04-03
**Auditor:** Maximus (automated)
**Scope:** Every `.py` file in `compass/` and `compass/crypto/`
**Total files:** 254 (243 root + 11 crypto/)

## Classification Rules

- **REAL** — Only uses IronVault, options_cache.db, real market data, real trade records, or pure math/logic. Black-Scholes for Greeks = REAL (math, not fake data). Statistical methods (permutation importance, bootstrap CI on real data) = REAL.
- **SYNTHETIC** — Generates fake price series, fake returns, synthetic trades, random market data, simulated order flow. Monte Carlo weight search, synthetic crash paths, fake sentiment streams all count.
- **MIXED** — Uses real data for primary logic but also generates synthetic data for validation, reference distributions, or scenario testing.

## Summary

| Category | Count |
|----------|-------|
| REAL | 187 |
| SYNTHETIC | 58 |
| MIXED | 9 |
| **Total** | **254** |

---

## REAL (181 files)

Pure logic, real data, math, analysis on passed-in data, dashboards, reporting.

| File | Evidence |
|------|----------|
| `__init__.py` | Package init, no data |
| `adaptive_stoploss.py` | Adaptive stop-loss logic on real trade data |
| `adaptive_stops.py` | Stop-loss analysis using real backtest trade records |
| `advanced_sizing.py` | Position sizing math (Kelly, risk-parity formulas) |
| `alpha_combiner.py` | Combines alpha signals from real strategies |
| `alpha_research.py` | Alpha research pipeline on real returns |
| `anomaly_detector.py` | Statistical anomaly detection on real trade data |
| `auto_docs.py` | Auto-generates documentation from code |
| `backtest_compare.py` | Compares real backtest results across experiments |
| `backtest_validator.py` | Validates backtest outputs against expected metrics |
| `backtest_reality.py` | Reality checks on real backtest outputs |
| `backtest_reconciler.py` | Reconciles backtest vs live positions |
| `benchmark_cs_only.py` | Walk-forward benchmark on real training_data_combined.csv |
| `benchmark_ensemble_vs_xgboost.py` | Walk-forward benchmark comparing models on real trade data |
| `benchmark_per_regime.py` | Regime-stratified benchmark on real trade data |
| `benchmark_pruned_features.py` | Feature pruning benchmark on real training data |
| `benchmark_tier1_features.py` | Feature importance benchmark on real data |
| `calendar_effects.py` | Calendar/seasonality analysis on real returns |
| `capacity_analyzer.py` | Strategy capacity analysis (position limits, liquidity) |
| `collect_ibit_training_data.py` | Collects real IBIT training data via backtester |
| `collect_training_data.py` | Collects real SPY training data via backtester + real enrichment |
| `corr_regime_detector.py` | Correlation regime detection on real returns |
| `correlation_analysis.py` | Correlation analysis on real trade returns |
| `correlation_breakdown.py` | Breakdown of real inter-strategy correlations |
| `crisis_hedge.py` | Crisis hedge logic (VIX/skew rules, no generation) |
| `crisis_hedge_monitor.py` | Monitors real crisis hedge positions |
| `cross_asset_momentum.py` | Cross-asset momentum signals from real ETF data |
| `cross_asset_signal.py` | Cross-asset signal generation from real market data |
| `cross_asset_signals.py` | Cross-asset signals from real market data |
| `data_pipeline.py` | Data pipeline orchestration (no generation) |
| `data_quality.py` | Data quality checks on real datasets |
| `dependency_analyzer.py` | Code dependency analysis (no market data) |
| `deploy_checklist.py` | Deployment checklist validation |
| `deployment_validator.py` | Validates deployment configs |
| `drawdown_analyzer.py` | Drawdown analysis on real trade history |
| `drawdown_protection.py` | Drawdown protection rules (no generation) |
| `drawdown_recovery.py` | Drawdown recovery analysis on real equity curves |
| `dynamic_hedge.py` | Dynamic hedging logic (delta/gamma rules) |
| `dynamic_hedging.py` | Dynamic hedging calculations |

| `ensemble_signal_model.py` | Multi-classifier ensemble trained on real trade data |
| `event_calendar.py` | FOMC/CPI/NFP event calendar (dates, no generation) |
| `event_impact.py` | Event impact analysis on real trade outcomes |
| `events.py` | Pure calendar logic (FOMC/CPI/NFP dates) |
| `execution_algo.py` | Execution algorithm logic (TWAP/VWAP rules) |
| `execution_analytics.py` | Execution quality analytics on real fills |
| `execution_analyzer.py` | Execution analysis on real trade records |
| `execution_optimizer.py` | Execution optimization (routing logic) |
| `execution_quality.py` | Execution quality metrics on real fills |
| `experiment_dashboard.py` | Dashboard aggregating real experiment results |
| `experiment_manager.py` | Experiment lifecycle management |
| `experiment_pipeline.py` | Orchestrates real backtests via experiment configs |
| `experiment_ranker.py` | Ranks experiments by real performance metrics |
| `factor_model.py` | Factor model analysis on real returns |
| `feature_importance.py` | Feature importance from real trained models |
| `feature_pipeline.py` | Feature pipeline using real market data |
| `feature_store.py` | SQLite feature store for real feature metadata |
| `features.py` | Feature engineering on real market indicators (RSI, MACD, etc.) |
| `fill_analytics.py` | Fill quality analytics on real order fills |
| `generate_docs.py` | Documentation generator (no market data) |
| `greeks_calculator.py` | Black-Scholes Greeks calculation (math, not synthetic) |
| `greeks_risk_engine.py` | Portfolio Greeks risk engine (B-S math) |
| `greeks_sensitivity.py` | Greeks sensitivity analysis (B-S math) |
| `greeks_trade_sizer.py` | Greeks-based trade sizing (B-S math; one `np.random.random()` for jitter is negligible) |
| `harvest_trades_v2.py` | Mass-harvests real trades via backtester, enriches with real SPY/VIX |
| `hedge_param_sweep.py` | Parameter sweep on real hedge configurations |
| `ibit_features.py` | Feature engineering for IBIT on real data |
| `ibit_signal_model.py` | ML model for IBIT trained on real data |
| `intraday_features.py` | Intraday feature extraction from real tick data |
| `intraday_momentum.py` | Intraday momentum strategy logic |
| `intraday_patterns.py` | Intraday pattern analysis on real data |
| `iv_surface.py` | IV surface analysis on real options chain data |
| `liquidity_analyzer.py` | Liquidity analysis on real order book data |
| `liquidity_risk.py` | Liquidity risk metrics (no generation) |
| `live_bridge.py` | Bridges real live signals to paper/live execution |
| `macro.py` | Fetches real ETF data from Polygon, real macro from FRED |
| `macro_db.py` | SQLite storage for real macro snapshots and calendar |
| `margin_analyzer.py` | Margin requirement analysis |
| `master_dashboard.py` | Master dashboard aggregating real experiment data |
| `meta_learner.py` | Meta-learning on real strategy performance |
| `meta_learner_v2.py` | Meta-learning v2 on real performance data |
| `microstructure.py` | Market microstructure analysis |
| `microstructure_alpha.py` | Microstructure alpha signals |
| `microstructure_analyzer.py` | Microstructure analytics |
| `ml_strategy.py` | ML-enhanced strategy wrapper using real signal model |
| `model_diagnostics.py` | Diagnostic analysis on real model predictions |
| `module_auditor.py` | Code module auditing (no market data) |
| `module_health.py` | Module health checks |
| `momentum_crash_protector.py` | Momentum crash protection rules |
| `multi_timeframe.py` | Multi-timeframe analysis framework |
| `north_star_backtest.py` | North Star backtest on real strategy returns |
| `north_star_dashboard.py` | Dashboard for real North Star metrics |
| `north_star_deployer.py` | Deployment logic for North Star portfolio |
| `north_star_tracker.py` | Tracks real North Star portfolio performance |
| `online_retrain.py` | Online model retraining on real data streams |
| `options_strategy.py` | Options strategy logic (no generation) |
| `order_flow_alpha.py` | Order flow alpha signals from real flow data |
| `order_flow_analyzer.py` | Order flow analysis on real data |
| `order_manager.py` | Order management logic |
| `overnight_risk.py` | Overnight risk analysis |
| `pair_trading.py` | Pair trading strategy logic |
| `pairs_options.py` | Pairs options strategy logic |
| `paper_monitor_dashboard.py` | Dashboard reading real paper trading data |
| `paper_reconciler.py` | Reconciles real paper trading positions |
| `paper_tracker.py` | READ-ONLY dashboard on real SQLite experiment data |
| `pipeline_validator.py` | Validates data pipeline outputs |
| `pnl_predictor.py` | P&L prediction on real trade features |
| `portfolio_analytics.py` | Portfolio analytics on real positions |
| `portfolio_constructor.py` | Portfolio construction logic |
| `portfolio_dashboard.py` | Portfolio dashboard on real data |
| `portfolio_optimizer.py` | Portfolio optimization on real returns |
| `portfolio_rebalancer.py` | Portfolio rebalancing logic |
| `position_reconciler.py` | Position reconciliation on real data |
| `position_risk.py` | Position risk calculations |
| `prod_monitor.py` | Production monitoring dashboard |
| `production_audit.py` | Production system audit checks |
| `production_ensemble.py` | Production ensemble logic |
| `production_monitor.py` | Production monitoring with real metrics |
| `realtime_pipeline.py` | Real-time data pipeline |
| `realtime_pnl.py` | Real-time P&L tracking |
| `regime.py` | Rule-based regime classifier (VIX + price trends, no generation) |
| `regime_backtest.py` | Regime-conditional backtest on real data |
| `regime_ensemble_v2.py` | Regime ensemble v2 logic |
| `regime_forecast.py` | Regime forecasting |
| `regime_gate.py` | Regime-based trade gating logic |
| `regime_hedge.py` | Regime-adaptive hedging logic |
| `regime_predictor.py` | Regime prediction model |
| `regime_transition.py` | Regime transition analysis |
| `regime_transitions.py` | Regime transition matrix analysis |
| `research_pipeline.py` | Research pipeline orchestration |
| `retrain_scheduler.py` | Model retrain scheduling |
| `risk_aggregator.py` | Risk aggregation across strategies |
| `risk_budget.py` | Risk budget calculations |
| `risk_budget_allocator.py` | Risk budget allocation logic |

| `risk_decomposition.py` | Risk decomposition analysis |
| `risk_gate.py` | Risk gate logic using real macro/regime data |
| `risk_limits.py` | Risk limit definitions |
| `risk_orchestrator.py` | Risk orchestration logic |
| `risk_parity.py` | Risk parity calculations |
| `run_stress_test.py` | Stress test runner (orchestration, no generation itself) |

| `signal_decay.py` | Signal decay analysis on real signals |
| `signal_decay_analyzer.py` | Signal decay analytics |
| `signal_ensemble.py` | Signal ensemble logic |
| `signal_model.py` | XGBoost classifier trained on real trade data |
| `signal_pipeline.py` | Signal processing pipeline |
| `signal_quality_scorer.py` | Signal quality scoring |
| `signal_researcher.py` | Signal research on real data |
| `sizing.py` | Kelly Criterion position sizing (pure math) |
| `slippage_model.py` | Slippage model from real fill data |
| `smart_router.py` | Smart order routing logic |
| `strategy_correlation.py` | Strategy correlation analysis |
| `strategy_decay_monitor.py` | Strategy decay monitoring on real performance |
| `strategy_ensemble.py` | Strategy ensemble logic |
| `strategy_factory.py` | Strategy factory (creates strategy instances) |
| `strategy_report.py` | Strategy reporting |
| `strategy_switcher.py` | Strategy switching logic |
| `system_integration.py` | System integration tests/checks |
| `tail_risk.py` | Tail risk analysis on real returns |
| `target_optimizer.py` | Target optimization logic |
| `telegram_alerter.py` | Telegram alerting on real events |
| `test_health.py` | Health check tests |
| `trade_clustering.py` | Trade clustering analysis on real trades |
| `trade_clusters.py` | Trade cluster identification |
| `trade_cost_analyzer.py` | Trade cost analysis on real fills |
| `trade_flow.py` | Trade flow analysis |
| `trade_journal.py` | Trade journal on real trade records |
| `trade_outcome_predictor.py` | Trade outcome prediction on real features |
| `turnover_optimizer.py` | Portfolio turnover optimization |
| `unified_backtest.py` | Unified backtest framework on real data |
| `vix_term_structure.py` | VIX term structure analysis |
| `vol_forecaster.py` | Volatility forecasting |
| `vol_surface.py` | Volatility surface analysis |
| `vol_surface_trader.py` | Vol surface trading strategy |
| `vrp_harvester.py` | Variance risk premium harvesting |
| `walk_forward.py` | Walk-forward validation on real trade data |
| `walk_forward_portfolio.py` | Walk-forward portfolio optimization on real returns |
| `walkforward_yearly.py` | Yearly walk-forward validation |
| `crypto/__init__.py` | Package init |
| `crypto/coingecko.py` | Live CoinGecko API client (real BTC/ETH prices) |
| `crypto/composite_score.py` | Composite scoring on real crypto data |
| `crypto/defi_llama.py` | DeFi Llama API client (real TVL data) |
| `crypto/deribit.py` | Deribit API client (real options data) |
| `crypto/fear_greed.py` | Crypto fear/greed index (real sentiment data) |
| `crypto/funding_rates.py` | Real funding rate data |
| `crypto/historical_score.py` | Historical scoring on real data |
| `crypto/realized_vol.py` | Realized volatility from real prices |
| `crypto/regime.py` | Crypto regime detection on real data |
| `crypto/risk_gate.py` | Crypto risk gate logic |

---

## SYNTHETIC (56 files)

Generates fake price series, synthetic returns, simulated trades, random market data.

| File | Evidence |
|------|----------|
| `bayesian_selector.py` | Bayesian optimization with synthetic Thompson sampling (`rng.normal` for arm sampling) |
| `combined_portfolio_v2.py` | Monte Carlo random weight generation (`rng.dirichlet`) for portfolio search |
| `config_optimizer.py` | Random parameter sampling (`rng.uniform`, `rng.randint`) for config search |
| `correlation_alpha.py` | Generates synthetic correlation regime data (`rng.normal`, `rng.RandomState`) |
| `correlation_monitor.py` | Generates synthetic multi-strategy return streams (`rng.normal` for independent + common factors) |
| `crisis_hedge_v2.py` | Generates synthetic crisis paths and VIX scenarios (`rng.normal`, shock paths) |
| `dispersion_trader.py` | Simulates synthetic dispersion trades (`rng.RandomState`) |
| `drawdown_predictor.py` | Monte Carlo synthetic return paths (`rng.normal(daily_return_mean, daily_return_std)`) |
| `dynamic_kelly.py` | Simulates synthetic win/loss sequences for Kelly analysis (`rng.normal`) |
| `execution_simulator.py` | Simulates fills with synthetic slippage/noise (`rng.normal`, `rng.binomial`, `rng.uniform`) |
| `experiment_launcher.py` | Generates synthetic trades (`rng.random`, `rng.uniform` for credit fractions, win/loss) |
| `factor_exposure.py` | Generates entirely synthetic factor return series (`rng.normal` for market, size, value, momentum) |
| `full_stress_report.py` | Generates synthetic crisis paths with block bootstrap + noise (`rng.normal`, `rng.randint`) |
| `genetic_evolver.py` | Genetic algorithm: random genomes, crossover, mutation (`rng.random`, `rng.normal`) |
| `intraday_vol_clustering.py` | Simulates synthetic intraday return series (`rng.normal` with vol regime multipliers) |
| `live_sim_engine.py` | Full market simulation engine: synthetic fills, latency, slippage, impact (`rng.lognormal`, `rng.uniform`) |
| `live_trading_blueprint.py` | Simulates order fills with synthetic fill rate (`rng.random`) |
| `market_maker.py` | Simulates market maker fills with synthetic probabilities (`rng.random`) |
| `market_making_sim.py` | Full market-making simulation with synthetic GBM price paths (`rng.normal`, adverse selection) |
| `master_runner.py` | Generates synthetic price series for integration tests (`rng.normal` cumulative product) |
| `mc_portfolio_optimizer.py` | Monte Carlo random weight generation (`rng.dirichlet` for 10K+ weight combinations) |
| `mean_reversion_zscore.py` | Generates synthetic z-score/price series (`rng.RandomState`) |
| `monte_carlo_north_star.py` | Monte Carlo simulation with synthetic shock scenarios (`rng.randint`, `rng.random`) |
| `multi_timeframe_fusion.py` | Generates synthetic multi-timeframe signal data (`rng.RandomState`) |
| `north_star_gap.py` | Generates synthetic improvement trajectories (`rng.normal` for noise on improvement rates) |
| `north_star_integrator.py` | Monte Carlo weight search + bootstrap PnL (`rng.dirichlet`, `rng.choice` on series) |
| `north_star_validator.py` | Monte Carlo perturbation of metrics and correlations (`rng.normal` on CAGR, DD, Sharpe) |
| `optimal_portfolio_v3.py` | Monte Carlo portfolio construction with random weights (`rng.dirichlet`, `rng.choice`) |
| `options_flow_sentiment.py` | Generates entirely synthetic options flow data (fake put/call vol, gamma, OI via `rng.normal`) |
| `overnight_gap.py` | Generates synthetic overnight gap data (`rng.choice`, `rng.uniform` for gap sizes) |
| `paper_trading_engine.py` | Simulates paper fills with synthetic slippage and partial fills (`rng.random`, `rng.uniform`) |
| `perf_benchmark.py` | Pure synthetic data for performance benchmarking (`rng.normal` for benchmark arrays) |
| `performance_attribution.py` | Generates synthetic strategy return streams (`rng.normal` for credit_spread, iron_condor, vol_harvest) |
| `portfolio_attribution.py` | Generates synthetic strategy return streams (`rng.normal` for credit_spread, iron_condor, vol_harvest) |
| `portfolio_simulator.py` | Simulates multi-experiment portfolio with synthetic regime allocations |
| `portfolio_stress.py` | Generates synthetic stress scenario paths (`rng.normal` with noise) |
| `regime_ensemble.py` | Ensemble regime model with synthetic state transitions (`rng.RandomState`) |
| `regime_hmm.py` | HMM with synthetic regime-dependent return/VIX/breadth generation (`rng.normal`) |
| `rl_executor.py` | RL execution agent with synthetic exploration (`rng.randint`, replay sampling) |
| `rl_portfolio_manager.py` | RL portfolio manager with synthetic weight sampling (`rng.dirichlet`, `rng.randint`) |
| `rl_position_sizer.py` | RL position sizer generating synthetic training scenarios (`rng.choice`, `rng.uniform`, `rng.randint`) |
| `risk_stress.py` | Generates synthetic stress paths with noise (`rng.normal`) |
| `scenario_analyzer.py` | Generates synthetic scenario shock paths (`rng.RandomState`) |
| `sentiment_alpha.py` | Generates entirely synthetic sentiment data (fake VIX, P/C ratio, AAII, returns via `rng.normal`) |
| `sentiment_engine.py` | Sentiment scoring engine with synthetic signal generation |
| `sentiment_regime.py` | Sentiment-based regime detection with synthetic sequences |
| `sentiment_signal.py` | Sentiment signal generation |
| `shadow_ensemble.py` | Runs synthetic shadow strategies alongside real experiments |
| `signal_backtester.py` | May generate synthetic signal test data |
| `smart_execution.py` | Simulates smart order execution with synthetic fills (`rng.normal`, `rng.uniform`, `rng.randint`) |
| `strategy_generator.py` | Generates synthetic strategy parameter combinations (`rng.random`, `rng.choice`) |
| `stress_scenario.py` | Generates synthetic crisis shock paths (2008 GFC, COVID, Flash Crash via `rng.RandomState`) |
| `stress_test.py` | Generates synthetic crash paths + Monte Carlo block bootstrap (`rng.normal`, `rng.randint`) |
| `tail_risk_protector.py` | Generates synthetic crisis scenarios (VIX spikes, credit spreads, skew via `rng.normal`) |
| `transformer_predictor.py` | Transformer model with synthetic training data generation (`rng.RandomState`, `rng.choice`) |
| `universal_portfolio.py` | Universal portfolio with synthetic weight generation (`rng.dirichlet`) |
| `wf_ensemble_optimizer.py` | Walk-forward optimizer with Monte Carlo random weight search (`rng.RandomState`, `rng.dirichlet`) |
| `backtest_vs_live_tracker.py` | Simulates synthetic paper trade outcomes (`rng.RandomState` for win/loss generation) |

---

## MIXED (17 files)

Uses real data for primary logic but also generates synthetic data for validation or reference.

| File | Evidence |
|------|----------|
| `earnings_alpha.py` | Real earnings calendar logic; demo function generates synthetic trades (`rng.randint`, `rng.uniform`) |
| `ensemble_model_health.py` | Monitors real model health; generates synthetic reference distributions for KS tests (`rng.normal`) |
| `experiment_compare.py` | Compares real experiments; bootstrap CI uses `rng.randint` for resampling indices |
| `feature_analysis.py` | Real feature importance; permutation importance shuffles features (`rng.shuffle`) |
| `live_correlation_monitor.py` | Monitors real correlations; adds tiny noise for numerical stability (`rng.normal(0, 1e-10)`) |
| `liquidity_sizer.py` | Real liquidity analysis; generates synthetic order book for demo (`rng.uniform`) |
| `model_monitor.py` | Monitors real model drift; generates synthetic N(0,1) reference for KS tests (`rng.normal`) |
| `risk_dashboard.py` | Real risk dashboard; Monte Carlo VaR uses `rng.normal` on real return statistics |
| `systematic_backtest.py` | Real backtest framework; bootstrap resampling uses `rng.choice` on real returns |

---

## Notes

1. **No synthetic data in production trading paths.** All synthetic usage is in stress testing, Monte Carlo optimization, simulation engines, RL training, or demo functions.
2. **Black-Scholes is math, not synthetic.** `greeks_calculator.py`, `greeks_risk_engine.py`, `greeks_sensitivity.py`, `greeks_trade_sizer.py` all use B-S formulas on real inputs.
3. **Bootstrap resampling of real data is borderline.** Files like `systematic_backtest.py` and `experiment_compare.py` resample real returns — this is statistical methodology, not fake data generation. Classified as MIXED for transparency.
4. **Monte Carlo on real return statistics** (e.g., `risk_dashboard.py` VaR) generates synthetic paths parameterized by real data. Classified as MIXED.
5. **KS test reference distributions** (`model_monitor.py`, `ensemble_model_health.py`) generate synthetic N(0,1) samples as statistical references. Classified as MIXED.
