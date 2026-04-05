# REGISTRY — Master Experiment Scorecard

**Last Updated:** 2026-04-05
**Experiments:** 100 directories | 78 documented | 13 real-data validated | 4 paper trading
**Critical Warning:** Synthetic backtests overstate Sharpe by 10-100x. Only trust rows marked `Real` or `Paper`.

---

## Summary — CORRECTED Portfolio-Level Stats (2026-04-05)

> **Warning:** Previous summary stats used inflated numbers. Corrections below per validation audit (1f0888a), hedge cost reality (51e11e6), and EXP-1220 truth report (1ef262a).

| Metric | Previous Claim | Corrected | Source |
|--------|---------------|-----------|--------|
| EXP-1220 trade-level Sharpe | ~~5.78~~ | **1.26** (per-trade) | 171 real IronVault trades |
| EXP-1220 trade-level CAGR | ~~77%~~ | **1.2%/yr** (pre-utilization fix) | $7,372 over 5yr on $100K |
| EXP-1220 walk-forward portfolio | ~~Sharpe 3.94~~ | **Sharpe 3.85** (corrected formula) | Dynamic leverage on SPY |
| Best portfolio Sharpe | ~~9.09~~ | **3.85** | Walk-forward validated |
| Hedge cost | ~~2%/yr~~ | **4.36%/yr** | Real IronVault SPY put prices |
| Real-data validated | 13 | **13** (unchanged) | OOS audit report |
| EXP-880 | BANKRUPT | BANKRUPT (unchanged) | −104% return on real data |

### Real-Data Validated Strategies — CORRECTED Grades

| Strategy | Trade Sharpe | OOS Sharpe | CAGR | DD | Trades | OOS Audit | Backtest Audit | Verdict | Data Status |
|----------|-------------|------------|------|----|--------|-----------|----------------|---------|-------------|
| **EXP-1220 Tail Risk** | **1.26** | — | 1.2% (trade) | 1.6% | 171 | B+ | D (cost+data) | **ALPHA PROVEN** (needs util fix) | SPY: current |
| **EXP-1630 GLD/TLT RelVal** | 2.19 | **4.08** | 1.9% | 1.7% | 63 | A | **B+** | **PROVEN** | GLD: ends Oct 2024 |
| **Vol Term Structure** | 2.45 | **2.81** | 0.55% | 0.18% | 53 | A | **B+** | **PROVEN** | SPY/XLF: current |
| **Cross-Asset Pairs** | 2.90 | **5.06** | — | — | 32 | A- | **B+** | **PROVEN** | QQQ: ends Apr 2023 |
| **TLT Iron Condors** | 2.69 | — | 10.2% | — | 43 | B | **B+** | **PROMISING** | TLT: to Dec 2025 |
| **XLI Iron Condors** | 5.19 | 2.68 | 18.8% | 10.3% | 58 | B+ | **B+** | **PROMISING** | XLI: current |
| EXP-1630-opt Multi-Pair | 1.35 | — | 12.6% | 9.3% | 174 | B | B+ | PROMISING | Mixed data |
| EXP-1650 Earnings VC | 1.55 | 0.59 | modest | 0.95% | 28 | B- | C | MARGINAL | XLF: current |
| EXP-1640 Sector Mom | 0.64 | −0.12 | 0.3% | 0.8% | 19 | C | D | MARGINAL | XLF: current |
| EXP-880-real | 0.41 | — | −104% | 106% | 262 | F | C | **DEAD** | SPY: current |
| EXP-1220-real | — | — | — | — | — | B+ | D | See EXP-1220 above | SPY: current |
| EXP-1270-real | −0.25 | — | −0.05% | 1.2% | 41 | D | C | **DEAD** | SPY: current |
| EXP-1320-real | 0.92 | — | 0.09% | 0.37% | 41 | D | D | **DEAD** | SPY: current |
| EXP-1470-real | ~0 | — | 0.42% | — | 19 | F | D | **DEAD** | — |
| EXP-1230-real | 0.89 | — | — | — | — | D | C | **DEAD** | — |
| *39 -max experiments* | — | — | — | — | — | — | F (all) | SYNTHETIC | — |

> **Backtest audit key:** 52 experiments scanned. 39 `-max` experiments auto-fail (synthetic data). 4 Grade B+ (IronVault real data, minor cost warning). 5 Grade C/D (real data but missing cost fields or data source issues). See `reports/full_audit_report.html` for full matrix.

---

## Complete Experiment Table

Sorted by real-data Sharpe (descending), then synthetic Sharpe. Columns: ID, Name, Type, Ticker, Status, Data Source, Sharpe (real or best available), CAGR, Max DD, SPY Corr, Capacity, Verdict.

### Strategies with Real-Data Validation

| # | ID | Name | Type | Ticker | Status | Data | Sharpe | CAGR | Max DD | SPY Corr | Capacity | Verdict |
|---|-----|------|------|--------|--------|------|--------|------|--------|----------|----------|---------|
| 1 | EXP-1220-real | Tail Risk Protection | Hedge overlay | SPY/VIX | **LIVE-READY** | Real | **5.78** | ~99% @1.2x | 6.6% | low | — | Best proven strategy; 9 crashes detected avg 53d warning |
| 2 | EXP-1630 | GLD/TLT Relative Value | Pairs / mean-rev | GLD,TLT | **LIVE-READY** | Real (IV) | **4.08** OOS | 1.9% (base) | 1.7% | 0.03 | ~$700K | Market-neutral; 86% WR; regime-agnostic |
| 2b | EXP-1630-opt | Multi-Pair Optimized | Pairs / mean-rev | GLD,TLT,XLF,QQQ | **LIVE-READY** | Real (IV) | 1.35 | **12.6%** | 9.3% | ~0.01 | ~$2M | 4 pairs at 3%/1.25x; deep-optimized |
| 3 | Cross-Asset | Cross-Asset Pairs | Pairs / z-score | TLT,SPY,QQQ | **LIVE-READY** | Real (IV) | **5.06** OOS | — | — | ~0.02 | — | TLT-QQQ best (97.5% WR, Sharpe 3.67) |
| 4 | Vol-Term | Vol Term Structure | Contango signal | SPY,XLF,XLI,TLT | **LIVE-READY** | Real (IV) | **2.81** OOS | 0.55% | 0.18% | −0.32 | — | Counter-cyclical; all 4 WF windows + |
| 5 | TLT-IC | TLT Iron Condors | Monthly ICs | TLT | PROMISING | Real (IV) | **2.69** | **10.2%** | — | — | — | 6yr profitable; 2020 best (18.8%); needs WF test |
| 6 | XLI-IC | XLF/XLI Iron Condors | Sector ETF ICs | XLI | PROMISING | Real (IV) | **8.58** OOS | **18.77%** | 10.3% | — | — | Best single CAGR; WF 2.2x; 92.5% WR |
| 7 | TLT-XLF | TLT-XLF Pair | Pairs / mean-rev | TLT,XLF | PROMISING | Real (IV) | 0.96 OOS | 5.5% | — | 0.37 | — | Rates/financials inverse; 57 trades |
| 8 | EXP-1650 | Earnings Vol Crush | IV crush | XLF | PROMISING | Real (IV) | **1.55** (0.59 OOS) | modest | 0.95% | — | — | Real edge Q1/Q2/Q4; Q3 toxic (−104% crush) |
| 9 | EXP-1230-real | Microstructure Alpha | Liquidity filter | SPY | MARGINAL | Real | **0.89** | — | — | — | — | Overlay value; AUC 0.511 (barely > random) |
| 10 | EXP-1640 | Sector Momentum | Momentum + CS | XLF,XLI | MARGINAL | Real (IV) | **0.64** | 0.3% | 0.8% | 0.04 | — | OOS Sharpe −0.12; only 19 trades |
| 11 | EXP-1270-real | Adaptive Stop-Loss | Stop optimizer | SPY | MARGINAL | Real (IV) | −0.25 | −0.05% | 1.2% | — | — | trailing_regime stop → 4.34 OOS; base negative |
| 12 | EXP-1320-real | Vol Clustering Overlay | Vol timing | SPY | MARGINAL | Real (IV) | 0.92 | 0.09% | 0.37% | — | — | 41 trades; improves 2022 DD; limited signal |
| 13 | EXP-880-real | Crisis Hedge V2 | ML credit spread | SPY | **DEAD** | Real (IV) | 0.41 | **−104%** | 106% | — | — | BANKRUPT on real data; profit factor 0.68 |
| 14 | EXP-1470-real | North Star Portfolio | Multi-strategy | SPY | **DEAD** | Real (IV) | ~0 | 0.42% | — | — | — | 19 trades; −27.43% gap from synthetic |

### Paper Trading (Live Forward Test)

| # | ID | Name | Type | Ticker | Status | Data | Sharpe | CAGR | Max DD | SPY Corr | Capacity | Verdict |
|---|-----|------|------|--------|--------|------|--------|------|--------|----------|----------|---------|
| 10 | EXP-400 | The Champion | Regime-adaptive CS+IC | SPY | **PAPER** | Live | — | — | — | — | — | Live since 2026-03-15; account PA36XFVLG0WE |
| 11 | EXP-401 | The Blend | CS + straddle/strangle | SPY | **PAPER** | Live | — | — | — | — | — | Live since 2026-03-15; WF 3/3 passed |
| 12 | EXP-503 | ML V2 Aggressive | ML credit spread | SPY | **PAPER** | Live | — | — | — | — | — | Live since 2026-03-22; XGBoost regime + Kelly |
| 13 | EXP-600 | IBIT Adaptive | Direction-adaptive CS | IBIT | **PAPER** | Live | — | — | — | — | — | Live since 2026-03-22; 139% avg annual backtest |

### Strategies — Synthetic Data Only (sorted by synthetic Sharpe)

| # | ID | Name | Type | Ticker | Status | Data | Sharpe | CAGR | Max DD | SPY Corr | Capacity | Verdict |
|---|-----|------|------|--------|--------|------|--------|------|--------|----------|----------|---------|
| 14 | EXP-1470-max | North Star Synthesis | Portfolio optimizer | Multi | PROMISING | Synth | 17.21 | 207% @3.6x | 2.1% | — | — | **Invalidated by EXP-1470-real**; synthetic assumptions false |
| 15 | EXP-860-max | Adaptive Retraining | ML ensemble | SPY | PROMISING | Synth | 12.30 | ~25% | 1.9% | — | — | Best standalone Sharpe; needs real-data validation urgently |
| 16 | EXP-1040-max | Combined Portfolio V2 | CS+intraday blend | SPY | PROMISING | Synth | 11.41 | 17.2% | 1.5% | — | — | 6x lev → 103% CAGR; depends on EXP-1000 validation |
| 17 | EXP-810-max | Model Ensemble | 3-model ensemble | SPY | PROMISING | Synth | 10.49 | ~20% | 3.6% | — | — | XGB+LGBM+Ridge beats single model |
| 18 | EXP-1000-max | Intraday Mean Reversion | 0-DTE credit spread | SPY | PROMISING | Synth | 9.92 | 10.6% | 1.2% | 0.03 | — | 404 trades, 86% WR; needs real intraday data |
| 19 | EXP-1270-max | Adaptive Stop-Loss | Stop optimizer | SPY | PROMISING | Synth | 5.25 | — | 3.2% | — | — | **Invalidated by EXP-1270-real** |
| 20 | EXP-880-max | Crisis Hedge V2 | ML credit spread | SPY | PROMISING | Synth | 4.97 | 76.9% | 10.2% | — | $50-150M | **Invalidated by EXP-880-real** |
| 21 | EXP-840-max | Regime Leverage 2x | Position sizing | SPY | PROMISING | Synth | 4.84 | 56.1% | 4.6% | — | — | 14/16 leverage variants pass; needs real validation |
| 22 | EXP-881-max | Combined CPCV Validation | Robustness test | SPY | COMPLETE | Synth | 4.32 OOS | 78.2% | 2.5% | — | — | 15/15 CPCV folds positive; but base strategy (880) fails on real |
| 23 | EXP-880-valid. | Crisis Hedge Validated | Robustness test | SPY | COMPLETE | Synth+CPCV | 3.99 | 78.2% | 2.5% | — | — | Statistical validation of synthetic results |
| 24 | EXP-1320-max | Intraday Vol Clustering | Vol timing | SPY | MARGINAL | Synth | 3.05 | — | — | — | — | **Invalidated by EXP-1320-real** |
| 25 | EXP-1020-max | 0-DTE Mean Reversion | Contrarian 0-DTE | SPY | MARGINAL | Synth | 2.95 | 0.9% | 2.5% | −0.35 | — | 68% WR but only 59 trades; needs real 0-DTE data |
| 26 | EXP-1220-max | Tail Risk Protection | Hedge overlay | SPY | COMPLETE | Synth | 2.12 | — | 13.0% | — | — | Synthetic version; real version (1220-real) is far superior |
| 27 | EXP-1420-max | Transformer Predictor | Deep learning | SPY | MARGINAL | Synth | 0.43 | — | — | — | — | XGBoost wins (Sharpe 1.38); transformers need PyTorch+more data |
| 28 | EXP-1110-max | Cross-Asset Momentum | Multi-asset signal | Multi | MARGINAL | Synth | 0.38 | — | — | — | — | Contemporaneous not leading; HURTS EXP-880 overlay (−18.6pp) |
| 29 | EXP-1310-max | Options Flow Sentiment | Flow analysis | SPY | MARGINAL | Synth | 0.37 | — | 35.2% | — | — | Weak standalone; block trades when flow < −0.4 |
| 30 | EXP-1360-max | Regime Transition Probs | HMM transitions | SPY | MARGINAL | Synth | 0.12 | — | 3.1% | — | — | 99% accuracy, 28d lead; but 97% persistence = few trades |
| 31 | EXP-1230-max | Microstructure Alpha | Liquidity filter | SPY | MARGINAL | Synth | −0.03 | — | — | — | — | No standalone alpha; +21pp as overlay (but real version is better) |
| 32 | EXP-1150-max | Calendar Effects | Seasonal timing | SPY | MARGINAL | Synth | −0.54 | — | 33.6% | — | — | No significant calendar effects found |
| 33 | EXP-1370-max | Momentum Crash Protection | Crash detector | SPY | MARGINAL | Synth | — | — | 39.0% | — | — | 20% DD reduction; no sharp episodes detected in sim |

### Infrastructure & Tooling (no standalone alpha — support modules)

| # | ID | Name | Type | Status | Tests | Verdict |
|---|-----|------|------|--------|-------|---------|
| 34 | EXP-820-max | Paper Trading Engine | Execution infra | COMPLETE | 57 | FillSimulator, RiskMonitor, PnLAttributor |
| 35 | EXP-850-max | Execution Analytics | Cost analysis | COMPLETE | — | **CRITICAL:** $1 spreads lose 28.6% — $5+ mandatory |
| 36 | EXP-870-max | Multi-Underlying Diversification | Portfolio research | COMPLETE | — | GLD+TLT key diversifiers; $3.1B capacity (synthetic) |
| 37 | EXP-890-max | Live Trading Blueprint | Deployment infra | COMPLETE | 35 | 6 risk gates, kill switch, reconciliation |
| 38 | EXP-900-max | HMM Regime Detection | Regime model | COMPLETE | — | 41% whipsaw reduction; ensemble+HMM best |
| 39 | EXP-910-max | North Star Integration | System integration | COMPLETE | — | 80% CAGR, Sharpe 8.46 (synthetic) |
| 40 | EXP-920-max | Robustness Validation | Statistical test | COMPLETE | — | Bootstrap Sharpe CI [2.4, 4.3], CPCV 21/21 |
| 41 | EXP-930-max | Real-Time Signal Pipeline | Data pipeline | COMPLETE | 49 | No look-ahead bias verified |
| 42 | EXP-940-max | Master Performance Report | Reporting | COMPLETE | — | Investor-grade HTML |
| 43 | EXP-950-max | Leverage Frontier | Leverage research | COMPLETE | — | 3.5x optimal; 100% CAGR needs portfolio approach |
| 44 | EXP-960-max | Path to 100% CAGR | Portfolio research | COMPLETE | — | 102% at 3.5x combined (synthetic) |
| 45 | EXP-970-max | Walk-Forward Leverage | Leverage validation | COMPLETE | — | 2.5x→36.4% CAGR/5.6% DD; 3.5x→45.8%/7.8% |
| 46 | EXP-980-max | Margin & Broker Feasibility | Broker research | COMPLETE | — | Alpaca 2.0x, IBKR PM 2.5-3.0x |
| 47 | EXP-990-max | Test Suite Consolidation | Test infra | COMPLETE | 180 | Coverage analysis |
| 48 | EXP-1010-max | Intraday Signal Enhancement | Feature eng | IN PROGRESS | — | Awaiting intraday data |
| 49 | EXP-1030-max | Intraday Momentum Scalping | Strategy design | THESIS ONLY | — | Designed but never backtested |
| 50 | EXP-1060-max | Earnings Event Alpha | Strategy design | THESIS ONLY | — | IV crush concept; never backtested |
| 51 | EXP-1070-max | Overnight Gap Strategy | Strategy design | THESIS ONLY | — | Straddle overnight premium; never backtested |
| 52 | EXP-1080-max | VIX Term Structure Trading | Vol surface model | COMPLETE | 39 | Term structure signals + butterfly generator |
| 53 | EXP-1090-max | Cross-Asset Correlation | Correlation model | COMPLETE | 34 | Breakdown detection + convergence signals |
| 54 | EXP-1100-max | Dispersion Trading | Vol model | COMPLETE | 41 | Implied vs realized correlation; vega-balanced |
| 55 | EXP-1120-max | Order Flow Imbalance | Signal model | COMPLETE | 40 | CLV-based OFI proxy |
| 56 | EXP-1130-max | Adaptive Regime Ensemble V2 | Regime model | COMPLETE | — | 86% whipsaw reduction, 93% accuracy, 4 detectors |
| 57 | EXP-1140-max | Multi-Timeframe Fusion | Signal fusion | COMPLETE | 42 | Attention-weighted 5min/1D/1W |
| 58 | EXP-1160-max | Smart Execution Engine | Execution algo | COMPLETE | — | VWAP 10.3 bps vs 83 bps naive (saves 72.7 bps) |
| 59 | EXP-1170-max | Dynamic Hedging Engine | Hedge model | COMPLETE | 41 | Delta/tail/VIX overlay + cost optimizer |
| 60 | EXP-1180-max | Feature Importance Analysis | ML analysis | COMPLETE | — | SHAP, permutation, signal half-life, clustering |
| 61 | EXP-1190-max | Portfolio Risk Dashboard | Risk monitoring | COMPLETE | 36 | VaR/CVaR/stress/Greeks/concentration |
| 62 | EXP-1200-max | Liquidity-Aware Sizing | Position sizing | COMPLETE | 26 | ATM SPY liquid; value at OTM/high-VIX/scale |
| 63 | EXP-1210-max | Bayesian Strategy Selection | Allocation model | COMPLETE | 43 | Thompson Sampling, NIG posteriors |
| 64 | EXP-1240-max | VRP Harvester | Vol premium | COMPLETE | 39 | Multi-tenor VRP + gamma scalp overlay |
| 65 | EXP-1250-max | Sentiment Regime Detector | Regime model | COMPLETE | — | Put/call + VIX slope + SKEW + credit composite |
| 66 | EXP-1260-max | Factor Exposure Analyzer | Attribution | COMPLETE | 21 | Alpha +11.8%/yr (t=3.60), R²=0.12, β=−0.19 |
| 67 | EXP-1280-max | Correlation Breakdown Detector | Risk model | COMPLETE | 35 | Absorption ratio early warning |
| 68 | EXP-1290-max | RL Position Sizer | ML sizing | COMPLETE | — | Tabular Q-learning, 180 states, 11 actions |
| 69 | EXP-1300-max | Mean Reversion Z-Score | Signal model | COMPLETE | 42 | Bollinger z<−2 + RSI divergence + volume spike |
| 70 | EXP-1330-max | Pairs Trading Options | Pairs model | COMPLETE | 33 | Cointegration-based, 6-pair universe |
| 71 | EXP-1340-max | Ensemble Meta-Learner V2 | ML stacker | COMPLETE | — | 12-signal gradient-boosted meta-learner |
| 72 | EXP-1350-max | Dynamic Kelly Criterion | Sizing model | COMPLETE | 43 | Rolling Kelly 20/60/120d, regime-modulated |
| 73 | EXP-1380-max | Greeks-Based Trade Sizing | Sizing model | COMPLETE | 36 | Theta-targeted, gamma/vega/delta caps |
| 74 | EXP-1390-max | Signal Decay Half-Life | Signal analysis | COMPLETE | — | ACF + IC decay + optimal rebalance |
| 75 | EXP-1400-max | Walk-Forward Ensemble Opt. | Allocation model | COMPLETE | 35 | Expanding-window projected gradient ascent |
| 76 | EXP-1410-max | Portfolio Correlation Monitor | Risk monitor | COMPLETE | 25 | DCC-GARCH, auto-delever at ρ>0.5 |
| 77 | EXP-1430-max | Genetic Algorithm Evolver | Optimization | COMPLETE | 35 | 20-gene genome, tournament selection, OOS fitness |
| 78 | EXP-1440-max | Regime Transition Predictor | Regime model | COMPLETE | — | HSMM with duration-dependent transitions |
| 79 | EXP-1450-max | Universal Portfolio | Allocation model | COMPLETE | 35 | Cover's EG algorithm, regret tracking |
| 80 | EXP-1480-max | RL Portfolio Manager | ML allocation | COMPLETE | 28 | Numpy PPO, portfolio env |
| 81 | EXP-1490-max | Production Readiness Audit | Audit | COMPLETE | — | 233 modules, 92% prod-ready, avg quality 9.4/10 |
| 82 | EXP-1500-max | Live Trading Simulation | Execution sim | COMPLETE | 42 | 5 friction models (spread/queue/latency/impact/fills) |
| 83 | EXP-1510-max | Performance Attribution | Attribution | COMPLETE | 22 | CS=61% of returns, hedge=7% DD for 3bps, β=0.012 |
| 84 | EXP-1520-max | North Star Validation | Statistical test | COMPLETE | — | 7/7 tests passed (but on synthetic data) |
| 85 | EXP-1530-max | Walk-Forward OOS Validation | Validation | IN PROGRESS | — | Expanding-window WF on EXP-1470 |
| 86 | EXP-1540-max | Monte Carlo Stress Test | Risk analysis | COMPLETE | — | 50K paths, 100% survival base, worst DD 24.6% |
| 87 | EXP-1550-max | North Star Deployment Plan | Deployment | COMPLETE | 39 | Config + circuit breakers + rebalancer |
| 88 | EXP-1570-max | Paper Trading Deployment | Deployment | COMPLETE | — | 11 pre-flight checks, launcher script |
| 89 | EXP-1580-max | Year-by-Year Walk-Forward | Validation | COMPLETE | — | NS base 27.8%, @3.6x→99%, @DD<12%→195.5% |
| 90 | EXP-1590-max | Production Monitor Dashboard | Monitoring | COMPLETE | 87 | HTML dashboard + Telegram alerts + health score |
| 91 | EXP-1600-max | Comprehensive Summary Report | Reporting | COMPLETE | 30 | 78-experiment investor-grade HTML |
| 92 | EXP-1610-max | Paper Trading Reconciler | Reconciliation | COMPLETE | — | 6-dimension backtest/paper comparison |
| 93 | EXP-601 | IBIT ML Signal Filter | ML filter | IN DEV | — | XGBoost binary classifier for EXP-600 |
| 94 | EXP-500 | ML Champion | ML overlay | IN DEV | — | XGBoost confidence on EXP-400; shipped via 503 |
| 95 | EXP-501 | ML Blend | ML overlay | BLOCKED | — | Blocked on EXP-500 validation |

### Retired / Legacy

| # | ID | Name | Type | Ticker | Status | Verdict |
|---|-----|------|------|--------|--------|---------|
| 96 | EXP-031 | Compound Bull Put | Credit spread | SPY | RETIRED | Overfit score 0.590; DTE cliff; compound sizing artifacts |
| 97 | EXP-036 | Compound 10% Both MA200 | Credit spread | SPY | RETIRED | Baseline; superseded by EXP-400 |
| 98 | EXP-059 | Various | Credit spread | SPY | RETIRED | Superseded by EXP-400/401 |
| 99 | EXP-154 | Various | Credit spread | SPY | RETIRED | Superseded by EXP-400/401 |
| 100 | EXP-305 | COMPASS Portfolio | Multi-ticker | SPY | RETIRED | Superseded by focused EXP-400/401 |

---

## Synthetic vs Real — The Credibility Gap

| Experiment | Synth Sharpe | Real Sharpe | Synth CAGR | Real CAGR | Overstatement |
|------------|-------------|-------------|------------|-----------|---------------|
| EXP-880 | 4.97 | 0.41 | 76.9% | −104% | **12x Sharpe, bankrupt** |
| EXP-1470 | 17.21 | ~0 | 207% @3.6x | 0.42% | **∞ — completely fictional** |
| EXP-1270 | 5.25 | −0.25 | — | −0.05% | **21x Sharpe, sign flip** |
| EXP-1320 | 3.05 | −14.10 | — | — | **∞ — catastrophic** |
| EXP-1230 | −0.03 | 0.89 | — | — | **Rare exception: real > synthetic** |

**Rule:** Assume any synthetic Sharpe >3 is actually <1 until proven on real IronVault data.

---

## Correlation Matrix — Real-Data Validated Strategies

|  | 1220 | 1630 | X-Pairs | Vol-Term | TLT-IC | XLI-IC | 1650 |
|--|------|------|---------|----------|--------|--------|------|
| **EXP-1220** Tail Risk | 1.00 | **0.00** | — | — | — | — | — |
| **EXP-1630** GLD/TLT RV | **0.00** | 1.00 | low | — | — | — | — |
| **Cross-Asset Pairs** | — | low | 1.00 | — | — | — | — |
| **Vol Term Structure** | — | — | — | 1.00 | — | — | — |
| **TLT Iron Condors** | — | — | — | — | 1.00 | — | — |
| **XLI Iron Condors** | — | — | — | — | — | 1.00 | — |
| **EXP-1650** Earnings VC | — | — | — | — | — | — | 1.00 |

All validated strategies exploit **orthogonal market dimensions**:
- **EXP-1220**: volatility regime (SPY/VIX relationship)
- **EXP-1630**: safe-haven relative value (GLD vs TLT); regime-agnostic (works in bull/bear/crash)
- **Cross-Asset Pairs**: ETF pair z-score mean-reversion (TLT-QQQ best)
- **Vol Term Structure**: contango/backwardation signal; SPY corr **−0.32** (counter-cyclical)
- **TLT Iron Condors**: rates vol premium harvest
- **XLI Iron Condors**: sector ETF IV overstatement
- **EXP-1650**: idiosyncratic earnings IV crush (Q1/Q2/Q4 only)

### Ultimate Portfolio (from `reports/ultimate_portfolio.json`)

| Component | Weight | Contribution |
|-----------|--------|-------------|
| EXP-1220 Dynamic | 95.0% | Tail risk protection overlay |
| Cross-Asset Pairs | 1.67% | Z-score mean-reversion |
| TLT Iron Condors | 1.67% | Rates vol premium |
| Vol Term Structure | 1.67% | Contango signal |

| Metric | Value |
|--------|-------|
| **CAGR** | **55.56%** |
| Sharpe | 4.10 |
| Max DD | 7.21% |
| Calmar | 7.71 |
| Sortino | 7.29 |
| Vol | 10.92% |

**OOS Walk-Forward (2022-2025)**: Avg CAGR 89.6%, Avg Sharpe 3.57, Max DD 11.4%

> **Caveat**: 95% weight in EXP-1220 means the 55% CAGR is primarily the tail-risk hedge
> overlay on equity returns, not pure credit spread alpha. The credit spread strategies
> contribute ~1-5% CAGR as diversifiers.

### EXP-1630 Optimized Portfolio (from `reports/exp1630_optimization.json`)

Best credit-spread-only portfolio (no EXP-1220 overlay):

| Config | Pairs | Risk | Leverage | CAGR | Max DD | CAGR/DD | Sharpe |
|--------|-------|------|----------|------|--------|---------|--------|
| Conservative | GLD-TLT only | 3% | 1x | 3.9% | 5.1% | 0.78 | 1.52 |
| **Moderate** | **4 pairs** | **3%** | **1.25x** | **12.6%** | **9.3%** | **1.35** | **—** |
| Aggressive | 4 pairs | 5% | 1.5x | 17.2% | 14.7% | 1.17 | — |

Regime analysis: Strategy works in **all regimes** — bear (100% WR), crash (100% WR), high_vol (100% WR), bull (82% WR). No regime filter needed.

---

## Gap Analysis

### Covered (real-data proof)
- Tail risk / crash (EXP-1220-real)
- Safe-haven relative value (EXP-1630)
- Earnings vol crush (EXP-1650)
- Sector rotation (EXP-1640)
- Liquidity regime detection (EXP-1230-real)

### NOT Covered (no real-data strategy exists)
| Gap | Priority | Why It Matters |
|-----|----------|----------------|
| Sustained high rates (>5%, 2+ years) | **HIGH** | Risk-free competes with option premium; no test data |
| SPY credit spreads on real data | **HIGH** | Flagship (EXP-880) bankrupt; need rebuilt from scratch |
| Extended low-vol (VIX 10-12, 6+ months) | MEDIUM | Premium collapses; 2017-style not in 2020-2025 data |
| Commodity supercycle / inflation | MEDIUM | Only GLD/TLT; no energy/agriculture/copper options |
| Liquidity crisis / flash crash | MEDIUM | No circuit-breaker or illiquid-market stress test |
| Crypto contagion (IBIT↔SPY) | LOW | Small allocation; EXP-600 paper trading |
| Overnight gap risk | LOW | EXP-1070 designed but never backtested |
| Mega-cap earnings contagion | LOW | AAPL/NVDA moving SPY 2%+ not modeled |

### Data Gaps
- **Real 0-DTE intraday bars** — EXP-1000, 1020, 1030, 1320 all need 1-min data
- **Real QQQ/IWM/IBIT option chains** — only SPY, GLD, TLT, sector ETFs validated
- **Forward performance** — backtests end Dec 2025; paper trading is only forward test

---

## Immediate Priorities

1. **Deploy EXP-1220 + EXP-1630 optimized multi-pair as live portfolio** — both proven on real data, zero correlation, 12.6% CAGR achievable
2. **Add TLT-XLF pair and XLI ICs to live allocation** — TLT-XLF at 5.5% CAGR, XLI ICs at 18.8% CAGR; both walk-forward validated
3. **Monitor EXP-400/401/503/600 paper trading** — first real forward performance data
4. **Re-backtest EXP-860 (Adaptive Retraining) on real IronVault data** — Sharpe 12.30 synthetic is suspicious but methodology is sound
5. **Rebuild SPY credit spread strategy from scratch** — EXP-880 approach is fundamentally broken on real pricing
6. **Expand IronVault data** — GLD ends Mar 2024, QQQ ends Apr 2023; need fresh option chains for forward testing
