# Attix Credit Spread Trading System

**Multi-strategy options portfolio** — 8 alpha streams, 12,607 tests, ~100 experiments across 12 development waves. Targeting 60–80% annual returns with <10% max drawdown via variance risk premium harvesting.

> **Production config: North Star v8a** — NET Sharpe 6.39, CAGR 118%, Max DD 5.1% (Alpaca commission-free). Expected live: Sharpe 3.2–4.5 after 0.5–0.7× industry-standard backtest decay.

⛔ **Rule Zero: NO SYNTHETIC DATA. EVER.** All backtests use real market data from IronVault (276K contracts, 6.3M option-days), Yahoo Finance, and FOMC calendars.

---

## Architecture

```
┌─────────────────────────── INPUTS (all REAL) ───────────────────────────┐
│                                                                          │
│   IronVault options_cache.db          Yahoo Finance               Fed    │
│   • SPY/QQQ/XLF/XLI/GLD/SLV           • ETF closes + ^VIX/^VIX3M  •FOMC│
│     option_daily + contracts          • 90d ADV, futures roll       cal. │
│   • 276K contracts, 6.3M option-days                                     │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────── 8 ALPHA STREAMS ──────────────────────────────┐
│                                                                         │
│  1. exp1220      SPY put credit spread 28 DTE 5% OTM                    │
│  2. xlf_cs       XLF delta-targeted put credit spread                   │
│  3. xli_cs       XLI delta-targeted put credit spread                   │
│  4. qqq_cs       QQQ put credit spread 28 DTE 5% OTM                   │
│  5. gld_cal      GLD–GC=F futures roll calendar spread                  │
│  6. slv_cal      SLV–SI=F futures roll calendar spread                  │
│  7. cross_vol    SPY/QQQ/IWM/EEM IV–RV cross-sectional arb             │
│  8. v5_hedge     13-ETF CTA with stress gate (Crisis Alpha v5)          │
│                                                                         │
│  Mean pairwise ρ: +0.016   (effectively independent diversification)    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────── PORTFOLIO OVERLAYS ───────────────────────────────┐
│                                                                          │
│  Ledoit-Wolf covariance       → risk-parity weights                     │
│  12% annual vol target        → scale factor (capped 20×)               │
│  VIX ladder (9 breakpoints)   → causal exposure scaling                 │
│  DD circuit breaker            → 3% soft / 12% hard, flatten            │
│  Portfolio Risk Manager        → cross-stream sizing, correlation monitor│
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────── EXECUTION STACK ──────────────────────────────────┐
│                                                                          │
│  Limit orders at mid · Patient pre-close window · Cheapest-route first   │
│  Multi-leg combo orders · Stacked savings: 503 bps/yr at 3× leverage     │
│  Primary: Alpaca (commission-free) · Fallback: IBKR Pro ($0.65/ctr)      │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Key Results

### North Star v8a — Three-Column Reality Check

| Context | Sharpe | CAGR | Max DD | When to quote |
|---|---:|---:|---:|---|
| **Gross** (ideal execution) | 6.87 | 102% | 4.2% | Theoretical ceiling — never the headline |
| **Net IBKR Pro** ($0.65/ctr) | 5.20 | 80% | 4.2% | Conservative / fallback broker |
| **Net Alpaca** (commission-free + VIX ladder) | **6.39** | **118%** | **5.1%** | **Production config headline** |
| **Expected live** (0.5–0.7× decay) | **3.2–4.5** | 60–80% | <10% | **What to actually underwrite** |

The 0.5–0.7× decay factor is the industry-standard haircut for options-selling strategies per Cornell (2019) and Harvey-Liu (2014). A 6.39 backtest that delivers 3.2–4.5 live is still elite — Medallion's long-run net is ~2.5.

### Walk-Forward Validation (20 folds)

| Metric | Value |
|---|---|
| Median fold Sharpe | 7.18 |
| Worst fold Sharpe | 4.32 |
| % folds ≥ 6.0 | 70% |
| All folds positive | Yes (20/20) |

---

## Test Suite

```
Tests passing:   12,607
Tests failing:   0
Skipped:         14
Coverage:        58.6% (threshold: 50%)
Runtime:         ~21 minutes
```

```bash
# Run the full suite
python3 -m pytest tests/ -q

# Run with coverage
python3 -m pytest tests/ --cov=strategy --cov=ml --cov=alerts --cov=shared --cov=backtest --cov=tracker
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

```bash
# Required
export ALPACA_API_KEY_PAPER="your_paper_key"
export ALPACA_API_SECRET_PAPER="your_paper_secret"

# Optional
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

```bash
# Generate today's signals
python3 compass/scripts/generate_daily_signals.py --date 2026-04-24

# Launch paper trading
scripts/launch_exp2300.sh start
```

### 4. Run a Backtest
```bash
python3 main.py backtest
```

---

## Project Structure

```
attix-credit-spreads/
├── compass/                       # ~440 modules: strategy, research, production
│   ├── exp1220_standalone.py      #   SPY put credit spread stream
│   ├── exp2160_high_capacity_alts.py  #   XLF/XLI credit spreads
│   ├── exp2240_qqq_iwm_credit_spreads.py  #   QQQ credit spreads
│   ├── exp1770_commodity_calendars.py     #   GLD/SLV calendar spreads
│   ├── exp2020_cross_vol_arb.py   #   Cross-sectional IV-RV arb
│   ├── crisis_alpha_v5.py         #   13-ETF CTA hedge stream
│   ├── exp2690_signal_generators.py  #   Central 8-stream registry
│   ├── exp2890_alpaca_connector.py   #   Alpaca paper API (791 lines)
│   ├── vix_ladder.py              #   9-breakpoint VIX exposure scaling
│   ├── portfolio_risk_manager.py  #   Cross-stream sizing + circuit breaker
│   ├── metrics.py                 #   Canonical Sharpe/CAGR/DD (use this)
│   └── scripts/generate_daily_signals.py  #   Daily cron driver
├── execution/                     # Live execution engine
│   ├── execution_engine.py        #   Order submission pipeline
│   └── position_monitor.py        #   Background position management daemon
├── strategy/                      # Core strategy modules
│   ├── spread_strategy.py         #   CreditSpreadStrategy
│   ├── options_analyzer.py        #   Options chain analysis
│   └── alpaca_provider.py         #   Alpaca API integration
├── shared/                        # Common utilities
│   ├── database.py                #   SQLite trade/alert storage (WAL mode)
│   ├── iron_vault.py              #   IronVault options data access
│   ├── reconciler.py              #   Position reconciliation (1,464 lines)
│   └── constants.py               #   FOMC dates, risk limits
├── alerts/                        # Alert generation + Telegram
├── backtest/                      # Backtesting engine
├── tests/                         # 364 test files, 12,607 tests
├── experiments/                   # ~100 experiments (EXP-1220 through EXP-2950)
├── configs/                       # Per-experiment YAML configs
│   └── exp2300/                   #   v8a production config (8 sleeve YAMLs)
├── scripts/                       # Operational scripts
│   └── launch_exp2300.sh          #   smoke | dry | start | stop | status
├── data/                          # IronVault DB, model artifacts
├── main.py                        # Entry point (1,165 lines)
├── MASTERPLAN.md                  # Single source of truth (v12)
└── requirements.txt               # Python dependencies
```

---

## Key Files

| File | Purpose |
|---|---|
| `MASTERPLAN.md` | Single source of truth — architecture, targets, phase plan, wave registry |
| `configs/exp2300_north_star_v6_paper.yaml` | Master portfolio configuration |
| `compass/exp2690_signal_generators.py` | Central registry for all 8 signal functions |
| `compass/exp2890_alpaca_connector.py` | Alpaca paper API integration |
| `compass/vix_ladder.py` | VIX-based exposure scaling (production default) |
| `compass/metrics.py` | Canonical Sharpe/CAGR/DD — use this, never re-implement |
| `shared/iron_vault.py` | IronVault data access (Rule Zero compliant) |

---

## Phase Status

| Phase | Status | Notes |
|---|---|---|
| 1–4: COMPASS ML integration | ✅ Complete | Regime classifier, ensemble, walk-forward, portfolio optimizer |
| 5: Portfolio optimization | ✅ Complete | Rolled into Phase 7 multi-strategy sprint |
| 6: Stress testing | ✅ Complete | 10K Monte Carlo paths, regime stress, VIX ladder |
| 7: Multi-strategy expansion | ✅ Complete | ~95 experiments across 12 waves, Apr 6–23 |
| **8: Paper trading** | **⏳ Blocked** | **Config ready, signal harness ready, dry run passed. Blocked on Alpaca API keys.** |
| 9: Live deployment | Future | Conditional on 4-week paper trading window |

---

## Constraints

1. **⛔ NO SYNTHETIC DATA** — `np.random`, `random.normal`, `generate_prices` = banned. Any violation = experiment kill.
2. Time-series CV only — no random-shuffle splits on trade data
3. ML is a filter — can reduce/block signals, never create new ones
4. Paper before live — 4+ weeks minimum per config before real capital
5. Canonical Sharpe only — `mean(daily) / std(daily) × √252`
6. Sparse exit-date convention — no P&L smearing
7. Gross, net, AND expected-live reported side-by-side

---

## Risk Disclaimer

This software is for **educational and research purposes only**. Trading options involves substantial risk of loss. Past backtest performance does not guarantee future results. The 0.5–0.7× backtest-to-live decay factor means the expected live Sharpe is 3.2–4.5, not 6.39. Never risk more than you can afford to lose.

---

**~440 modules | 12,607 tests | ~100 experiments | 1,079 commits | NET Sharpe 6.39 | Built with Python 3.11**
