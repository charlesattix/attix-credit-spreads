# PilotAI Credit Spread Trading System

**Autonomous options trading platform** — 185 compass modules, 8,500+ tests, 23 experiments, targeting 55-77% annual returns with <15% drawdown via credit spreads on SPY.

> **Production config: EXP-880-max** — 76.9% CAGR, Sharpe 4.97, Max DD 10.2%, crisis-hedged through COVID/2022.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MARKET DATA                                  │
│  Alpaca WebSocket ─→ DataFeed ─→ IronVault (options_cache.db)       │
└──────────────┬──────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    SIGNAL PIPELINE (realtime_pipeline.py)            │
│                                                                      │
│  FeatureEngine ──→ ProductionEnsemble ──→ RegimeDetector             │
│  (23 features)     (XGB+RF+ET voting)    (bull/bear/high_vol/crash)  │
│       │                   │                      │                   │
│       ▼                   ▼                      ▼                   │
│  ┌──────────┐     ┌─────────────┐      ┌─────────────────┐          │
│  │ IV Rank  │     │ Confidence  │      │ CrisisHedge V2  │          │
│  │ Momentum │     │ Grading     │      │ VIX tiers 25/35 │          │
│  │ Vol/RSI  │     │ P ≥ 0.70    │      │ DD control 2-7% │          │
│  └──────────┘     └──────┬──────┘      │ Put overlay     │          │
│                          │             │ Recovery detect  │          │
│                          ▼             └────────┬────────┘          │
│                   SignalQueue (dedup)            │                   │
│                          │                      │                   │
│                          ▼                      ▼                   │
│                    ┌────────────────────────────────┐                │
│                    │  FINAL SIGNAL + SCALE FACTOR   │                │
│                    └──────────────┬─────────────────┘                │
└───────────────────────────────────┼──────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│                     EXECUTION (live_trading_blueprint.py)             │
│                                                                       │
│  Pre-Trade Risk ──→ OrderManager ──→ SmartRouter ──→ Alpaca API       │
│  (5 gates)          (lifecycle)      (venue split)   (paper/live)     │
│       │                                                    │          │
│       ▼                                                    ▼          │
│  PositionReconciler ◄──────────────────────────── Broker Positions    │
│  (auto-correct minor drifts, flag major)                              │
└───────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│                      MONITORING & REPORTING                           │
│                                                                       │
│  CrisisHedgeMonitor ─── EXP880Monitor ─── PortfolioDashboard         │
│  (VIX tiers, cost)      (deviation from    (Sharpe, DD, trades,       │
│                          backtest)          regime, alerts)            │
│                              │                                        │
│                              ▼                                        │
│                    Telegram Alerts (trade/hedge/DD breach)             │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Key Results

| Metric | EXP-880 (Production) | EXP-840 (Unhedged) | EXP-400 (Champion) |
|---|---|---|---|
| CAGR | **76.9%** | 56.1% | 22% |
| Sharpe | **4.97** | 4.84 | 2.98 |
| Max DD | **10.2%** | 4.6% | ~15% |
| Leverage | 2.0x | 1.71x | 1.0x |
| Crisis hedge | V2 Ultra-Safe | None | V1 |
| COVID survival | 10.0% DD | N/A | ~20% DD |

---

## Experiment Leaderboard (Top 5)

| Rank | Experiment | CAGR | Sharpe | Max DD | Status |
|---|---|---|---|---|---|
| 1 | **EXP-910-max** | 80.0% | 8.46 | 2.8% | North Star Integration |
| 2 | **EXP-860-max** | ~25% | 12.30 | 1.9% | Adaptive Retraining |
| 3 | **EXP-960-max** | 102% | 4.97 | 9.8% | 100% CAGR Path |
| 4 | **EXP-880-max** | 76.9% | 4.97 | 10.2% | **PRODUCTION CONFIG** |
| 5 | **EXP-840-max** | 56.1% | 4.84 | 4.6% | Regime Leverage 2x |

Full leaderboard: [`experiments/LEADERBOARD.md`](experiments/LEADERBOARD.md)

---

## Compass Modules (185)

The `compass/` directory contains the complete analytical and trading engine:

### Core Strategy
| Module | Description |
|---|---|
| `signal_model.py` | XGBoost classifier with calibration |
| `features.py` | 23 pruned features (post-ablation) |
| `regime.py` | Regime classifier (bull/bear/high_vol/low_vol/crash) |
| `sizing.py` | Kelly criterion position sizing |
| `advanced_sizing.py` | Regime-adaptive fractional Kelly |
| `production_ensemble.py` | Walk-forward 3-model ensemble |

### Risk Management
| Module | Description |
|---|---|
| `crisis_hedge.py` | V1 VIX-adaptive position scaling |
| `crisis_hedge_v2.py` | V2 gradual delevering + put overlay + recovery detection |
| `risk_limits.py` | Dynamic limits with breach severity |
| `drawdown_analyzer.py` | Regime-attributed DD analysis |
| `drawdown_recovery.py` | Kaplan-Meier survival analysis for DD recovery |
| `tail_risk.py` | CVaR, EVT/GPD fitting, stress VaR |
| `anomaly_detector.py` | Z-score + IQR anomaly detection |
| `risk_aggregator.py` | Portfolio VaR/CVaR, marginal contribution |

### Execution
| Module | Description |
|---|---|
| `order_manager.py` | Order lifecycle, kill switch, batching |
| `execution_algo.py` | TWAP/VWAP/IS/Iceberg algorithms |
| `smart_router.py` | Venue selection, dark pool routing |
| `position_reconciler.py` | Paper vs broker position reconciliation |
| `live_trading_blueprint.py` | Signal → order translation, 5 risk gates |

### Analytics & Reporting
| Module | Description |
|---|---|
| `portfolio_analytics.py` | Sharpe/Sortino/Calmar/Omega, rolling analytics |
| `portfolio_dashboard.py` | Master dashboard with all metrics |
| `north_star_dashboard.py` | Target tracking with gap analysis |
| `experiment_dashboard.py` | Per-experiment traffic-light status |
| `experiment_compare.py` | Side-by-side statistical comparison |

### ML & Signals
| Module | Description |
|---|---|
| `signal_decay.py` | IC curve, SNR, half-life estimation |
| `regime_predictor.py` | GP-based regime forecasting |
| `regime_ensemble.py` | HMM + CUSUM + vol clustering + trend + macro |
| `pnl_predictor.py` | Pre-trade P&L prediction with go/no-go |
| `config_optimizer.py` | Bayesian optimisation with GP surrogate |
| `rl_executor.py` | Q-learning execution agent |

### Infrastructure
| Module | Description |
|---|---|
| `realtime_pipeline.py` | Streaming data → features → inference |
| `data_pipeline.py` | Scheduled collection, validation, versioning |
| `deploy_checklist.py` | Production readiness verification |
| `module_health.py` | Import validation, test coverage check |
| `crisis_hedge_monitor.py` | Real-time hedge tracking dashboard |

---

## Test Suite

```
Total test files:  248
Total tests:       8,573+
Failures:          0
Coverage:          ~59%
```

Run the full suite:
```bash
python3 -m pytest tests/ --ignore=tests/test_property_based.py -q --no-cov
```

---

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Tests
```bash
python3 -m pytest tests/ -q --no-cov
```

### 3. Paper Trading Setup

**Environment variables:**
```bash
export ALPACA_API_KEY="your_paper_key"
export ALPACA_SECRET_KEY="your_paper_secret"
export TELEGRAM_BOT_TOKEN="your_bot_token"    # optional
export TELEGRAM_CHAT_ID="your_chat_id"        # optional
```

**Run the EXP-880 monitor:**
```bash
python3 scripts/monitor_exp880.py
```

This will:
- Connect to Alpaca paper trading API
- Read trade history from SQLite database
- Compare actual performance vs EXP-880 backtest expectations
- Generate HTML dashboard at `reports/exp880_monitor.html`
- Send Telegram alerts for trades, hedge activations, DD breaches

### 4. Run a Backtest
```bash
python3 main.py backtest
```

---

## Project Structure

```
pilotai-credit-spreads/
├── compass/                    # 183 analytical + trading modules
│   ├── signal_model.py         # ML classifier
│   ├── features.py             # Feature engineering
│   ├── regime.py               # Regime detection
│   ├── crisis_hedge_v2.py      # Crisis protection
│   ├── production_ensemble.py  # Walk-forward ensemble
│   ├── realtime_pipeline.py    # Streaming signal generation
│   ├── live_trading_blueprint.py # Execution framework
│   └── ... (178 more modules)
├── tests/                      # 248 test files, 8573+ tests
├── experiments/                # 23 completed experiments
│   ├── EXP-880-max/            # Production config
│   ├── LEADERBOARD.md          # Ranked results
│   └── registry.json           # Experiment registry
├── shared/                     # Constants, types, utilities
│   ├── iron_vault.py           # Centralised data access
│   ├── telegram_alerts.py      # Telegram notification
│   └── constants.py            # Risk limits (hard-coded)
├── scripts/                    # Operational scripts
│   ├── monitor_exp880.py       # Paper trading monitor
│   └── ...
├── reports/                    # Generated HTML dashboards
├── configs/                    # Strategy configurations
├── data/                       # Market data, model artifacts
└── main.py                     # Entry point
```

---

## Key Files

| File | Purpose |
|---|---|
| [`experiments/LEADERBOARD.md`](experiments/LEADERBOARD.md) | Ranked experiment results |
| [`experiments/EXP-880-max/analysis.md`](experiments/EXP-880-max/analysis.md) | Production config analysis |
| [`experiments/EXP-980-max/analysis.md`](experiments/EXP-980-max/analysis.md) | Margin & broker feasibility |
| [`compass/crisis_hedge_v2.py`](compass/crisis_hedge_v2.py) | Crisis hedge controller |
| [`compass/realtime_pipeline.py`](compass/realtime_pipeline.py) | Real-time signal pipeline |
| [`shared/constants.py`](shared/constants.py) | Hard-coded risk limits |
| [`docs/DATA_ARCHITECTURE.md`](docs/DATA_ARCHITECTURE.md) | Iron Vault data access |

---

## Risk Disclaimer

This software is for **educational and research purposes only**. Trading options involves substantial risk of loss. Past backtest performance does not guarantee future results. The system is currently in paper trading validation. Never risk more than you can afford to lose.

---

**185 modules | 8,573+ tests | 23 experiments | Sharpe 4.97 | Built with Python 3.11**
