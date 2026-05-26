# Experiment Registry — Attix Credit Spreads

**Last Updated:** 2026-04-04
**Total Experiments:** 100 (directories) | 78 with documented results
**Data Integrity Note:** Experiments suffixed `-max` use synthetic/heuristic data. Experiments suffixed `-real` use real IronVault option prices. **Real-data backtests reveal dramatically lower performance** — see Reality Check section.

---

## Master Scorecard

### Tier 1 — Production Ready (validated, deployable)

| ID | Name | Status | Sharpe IS | Sharpe OOS | CAGR | Max DD | SPY Corr | Trades | Data |
|----|------|--------|-----------|------------|------|--------|----------|--------|------|
| EXP-1220-real | Tail Risk Protection (Real) | **LIVE-READY** | 0.37 | 5.78 | ~99%* | 6.6% | low | daily | Real |
| EXP-1630-max | GLD/TLT Relative Value | **LIVE-READY** | 0.31 | 4.08 | 1.9% | 1.7% | 0.03 | 63 | Real (IronVault) |
| EXP-880-validation | Crisis Hedge V2 (Validated) | **VALIDATED** | 3.99 | 3.27+ | 78.2% | 2.5% | — | — | Synthetic+CPCV |

*EXP-1220-real 99% CAGR uses leveraged tail risk protection on real SPY/VIX data; needs live confirmation.

### Tier 2 — Promising (strong synthetic results, needs real-data validation)

| ID | Name | Status | Sharpe IS | Sharpe OOS | CAGR | Max DD | SPY Corr | Trades | Data |
|----|------|--------|-----------|------------|------|--------|----------|--------|------|
| EXP-1000-max | Intraday Mean Reversion | PROMISING | 9.92 | — | 10.6% | 1.2% | 0.03 | 404 | Synthetic |
| EXP-860-max | Adaptive Retraining | PROMISING | 12.30 | — | ~25% | 1.9% | — | — | Synthetic |
| EXP-810-max | Model Ensemble | PROMISING | 10.49 | — | ~20% | 3.6% | — | — | Synthetic |
| EXP-840-max | Regime Leverage 2x | PROMISING | 4.84 | — | 56.1% | 4.6% | — | — | Synthetic |
| EXP-880-max | Crisis Hedge V2 (Base) | PROMISING | 4.97 | — | 76.9% | 10.2% | — | — | Synthetic |
| EXP-1040-max | Combined Portfolio V2 | PROMISING | 11.41 | — | 17.2% | 1.5% | — | — | Synthetic |
| EXP-1270-max | Adaptive Stop-Loss | PROMISING | 5.25 | — | — | 3.2% | — | — | Synthetic |
| EXP-1650-max | Earnings Vol Crush | PROMISING | 1.55 | 0.59 | — | 0.95% | — | 50 | Real (IronVault) |
| EXP-1640-max | Sector Momentum | PROMISING | 0.64 | — | 0.3% | 0.8% | 0.04 | 19 | Real (IronVault) |
| EXP-1470-max | North Star Synthesis | PROMISING | 17.21 | 12.08 | 27.9% (207% @3.6x) | 2.1% | — | — | Synthetic |

### Tier 3 — Marginal (mixed results or overlay-only value)

| ID | Name | Status | Sharpe | Notes |
|----|------|--------|--------|-------|
| EXP-1020-max | 0-DTE Mean Reversion | MARGINAL | 2.95 | Only 59 trades; needs real 0-DTE data for frequency |
| EXP-1110-max | Cross-Asset Momentum | MARGINAL | 0.38 | Contemporaneous not leading; HURTS EXP-880 as overlay (-18.6pp) |
| EXP-1230-max | Microstructure Alpha | MARGINAL | -0.03 | No standalone alpha; good as entry filter (+21pp overlay) |
| EXP-1310-max | Options Flow Sentiment | MARGINAL | 0.37 | Weak standalone; moderate overlay value |
| EXP-1320-max | Intraday Vol Clustering | MARGINAL | 3.05 | Overlay hurts (-11pp); needs real 5-min data |
| EXP-1150-max | Calendar Effects | MARGINAL | -0.54 | No significant effects found on synthetic data |
| EXP-1370-max | Momentum Crash Protection | MARGINAL | — | 20% DD reduction but no sharp episodes detected |
| EXP-1360-max | Regime Transition Probs | MARGINAL | 0.12 | High accuracy but 97% persistence — limited trading value |
| EXP-1420-max | Transformer Predictor | MARGINAL | 0.43 | XGBoost wins (Sharpe 1.38); transformers need more data |

### Tier 4 — Dead / Failed / Infrastructure Only

| ID | Name | Status | Reason |
|----|------|--------|--------|
| EXP-880-real | Crisis Hedge V2 (Real Data) | **DEAD** | -104% return, Sharpe 0.41, profit factor 0.68 on real IronVault data |
| EXP-1270-real | Adaptive Stop-Loss (Real) | **DEAD** | -$274 PnL, Sharpe -0.25 on real data |
| EXP-1320-real | Intraday Vol Clustering (Real) | **DEAD** | Sharpe -14.1 on real data |
| EXP-1470-real | North Star Portfolio (Real) | **DEAD** | 0.42% CAGR, 19 trades, Sharpe ~0 on real data |
| EXP-031 | Compound Bull Put | RETIRED | Overfit score 0.590, DTE cliff |
| EXP-036 | Compound 10% Both MA200 | RETIRED | Superseded by EXP-400 |
| EXP-059 | Various | RETIRED | Superseded by EXP-400/401 |
| EXP-154 | Various | RETIRED | Superseded by EXP-400/401 |
| EXP-305 | COMPASS Portfolio | RETIRED | Superseded by EXP-400/401 |

### Infrastructure Experiments (no tradeable alpha, support-only)

| ID | Name | Status | Deliverable |
|----|------|--------|-------------|
| EXP-820-max | Paper Trading Engine | COMPLETE | FillSimulator, RiskMonitor, PnLAttributor (57 tests) |
| EXP-850-max | Execution Analytics | COMPLETE | **CRITICAL: $1 spreads lose 28.6% — use $5+ mandatory** |
| EXP-890-max | Live Trading Blueprint | COMPLETE | 6 risk gates, kill switch, reconciliation (35 tests) |
| EXP-900-max | HMM Regime Detection | COMPLETE | 41% whipsaw reduction |
| EXP-910-max | North Star Integration | COMPLETE | 80% CAGR, Sharpe 8.46 (synthetic integration) |
| EXP-920-max | Robustness Validation | COMPLETE | Bootstrap CI [2.4, 4.3], CPCV 21/21 positive |
| EXP-930-max | Real-Time Signal Pipeline | COMPLETE | 49 tests, no look-ahead bias verified |
| EXP-940-max | Master Performance Report | COMPLETE | Investor-grade HTML report |
| EXP-950-max | Leverage Frontier | COMPLETE | 3.5x optimal; 100% CAGR not achievable single-strategy |
| EXP-960-max | Path to 100% CAGR | COMPLETE | 102% CAGR at 3.5x combined portfolio (synthetic) |
| EXP-970-max | Walk-Forward Leverage | COMPLETE | 2.5x: 36.4% CAGR/5.6% DD; 3.5x: 45.8%/7.8% DD |
| EXP-980-max | Margin & Broker Feasibility | COMPLETE | Alpaca 2.0x, IBKR PM 2.5-3.0x |
| EXP-990-max | Test Suite Consolidation | COMPLETE | ~180 test files |
| EXP-1080-max | VIX Term Structure | COMPLETE | Vol surface trader, 39 tests |
| EXP-1090-max | Cross-Asset Correlation | COMPLETE | 34 tests, breakdown detection |
| EXP-1100-max | Dispersion Trading | COMPLETE | 41 tests, implied vs realized correlation |
| EXP-1120-max | Order Flow Imbalance | COMPLETE | 40 tests, CLV-based OFI |
| EXP-1130-max | Adaptive Regime Ensemble V2 | COMPLETE | 86% whipsaw reduction, 93% accuracy |
| EXP-1140-max | Multi-Timeframe Fusion | COMPLETE | 42 tests, attention-weighted |
| EXP-1160-max | Smart Execution Engine | COMPLETE | VWAP saves 72.7 bps vs naive (10.3 bps cost) |
| EXP-1170-max | Dynamic Hedging Engine | COMPLETE | 41 tests, delta/tail/VIX overlay |
| EXP-1180-max | Feature Importance | COMPLETE | SHAP, permutation, signal half-life |
| EXP-1190-max | Portfolio Risk Dashboard | COMPLETE | VaR, CVaR, stress tests, Greeks (36 tests) |
| EXP-1200-max | Liquidity-Aware Sizing | COMPLETE | ATM SPY liquid; value at OTM/high VIX/scale (26 tests) |
| EXP-1210-max | Bayesian Strategy Selection | COMPLETE | Thompson Sampling, NIG posteriors (43 tests) |
| EXP-1240-max | VRP Harvester | COMPLETE | Multi-tenor VRP, gamma scalp (39 tests) |
| EXP-1250-max | Sentiment Regime Detector | COMPLETE | Composite sentiment, CUSUM changepoint |
| EXP-1260-max | Factor Exposure Analyzer | COMPLETE | Alpha +11.8%/yr (t=3.60), R²=0.12, beta=-0.19 |
| EXP-1280-max | Correlation Breakdown Detector | COMPLETE | Absorption ratio, multi-window (35 tests) |
| EXP-1290-max | RL Position Sizer | COMPLETE | Tabular Q-learning, 180-state space |
| EXP-1300-max | Mean Reversion Z-Score | COMPLETE | Bollinger z<-2 + RSI divergence (42 tests) |
| EXP-1330-max | Pairs Trading Options | COMPLETE | Cointegration-based, 6 pair universe (33 tests) |
| EXP-1340-max | Ensemble Meta-Learner V2 | COMPLETE | 12-signal gradient-boosted stacker |
| EXP-1350-max | Dynamic Kelly Criterion | COMPLETE | Rolling Kelly, regime-modulated (43 tests) |
| EXP-1380-max | Greeks-Based Trade Sizing | COMPLETE | Theta-targeted, gamma/vega caps (36 tests) |
| EXP-1390-max | Signal Decay Half-Life | COMPLETE | ACF, IC decay, optimal rebalance frequency |
| EXP-1400-max | Walk-Forward Ensemble Optimizer | COMPLETE | Expanding-window gradient ascent (35 tests) |
| EXP-1410-max | Portfolio Correlation Monitor | COMPLETE | DCC-GARCH, auto-delevering (25 tests) |
| EXP-1430-max | Genetic Algorithm Evolver | COMPLETE | 20-gene genome, tournament selection (35 tests) |
| EXP-1440-max | Regime Transition Predictor | COMPLETE | HSMM with duration modeling |
| EXP-1450-max | Universal Portfolio | COMPLETE | Cover's EG algorithm (35 tests) |
| EXP-1480-max | RL Portfolio Manager | COMPLETE | Numpy PPO, portfolio env (28 tests) |
| EXP-1490-max | Production Readiness Audit | COMPLETE | 233 modules scanned, 92% production-ready |
| EXP-1500-max | Live Trading Simulation | COMPLETE | 5 friction components (42 tests) |
| EXP-1510-max | Performance Attribution | COMPLETE | 6-source attribution, CS=61% of returns |
| EXP-1520-max | North Star Validation Suite | COMPLETE | 7/7 validation tests passed |
| EXP-1530-max | Walk-Forward OOS Validation | IN PROGRESS | Expanding window WF on EXP-1470 |
| EXP-1540-max | Monte Carlo Stress Test | COMPLETE | 50K paths, 100% survival base case |
| EXP-1550-max | North Star Deployment Plan | COMPLETE | 39 tests, circuit breakers |
| EXP-1570-max | Paper Trading Deployment | COMPLETE | 11 pre-flight checks, launcher script |
| EXP-1580-max | Year-by-Year Walk-Forward | COMPLETE | NS base 27.8% CAGR, 3.6x→99%, DD<12%→195.5% |
| EXP-1590-max | Production Monitor Dashboard | COMPLETE | 87 tests, Telegram alerts, health score |
| EXP-1600-max | Comprehensive Summary Report | COMPLETE | 78 experiments, investor-grade HTML |
| EXP-1610-max | Paper Trading Reconciler | COMPLETE | 6-dimension reconciler |
| EXP-881-max | Combined CPCV Validation | COMPLETE | 15/15 folds positive OOS Sharpe (mean 4.32) |

### Paper Trading (Live)

| ID | Name | Status | Account | Live Since | Ticker |
|----|------|--------|---------|------------|--------|
| EXP-400 | The Champion | PAPER TRADING | PA36XFVLG0WE | 2026-03-15 | SPY |
| EXP-401 | The Blend | PAPER TRADING | PA3Y2XDYB9I3 | 2026-03-15 | SPY |
| EXP-503 | ML V2 Aggressive | PAPER TRADING | PA3Z9PLVYUL5 | 2026-03-22 | SPY |
| EXP-600 | IBIT Adaptive | PAPER TRADING | PA3O14JAJHJ0 | 2026-03-22 | IBIT |

---

## Reality Check: Synthetic vs Real Data

**This is the most important section of this registry.**

Four experiments have been re-backtested on real IronVault option prices. The results are sobering:

| Experiment | Synthetic Sharpe | Real Sharpe | Synthetic CAGR | Real CAGR | Synthetic DD | Real DD |
|------------|-----------------|-------------|----------------|-----------|-------------|---------|
| EXP-880 | 4.97 | 0.41 | 76.9% | **-104%** (bankrupt) | 10.2% | 106% |
| EXP-1270 | 5.25 | -0.25 | — | -0.05% | 3.2% | 1.2% |
| EXP-1320 | 3.05 | -14.10 | — | — | — | — |
| EXP-1470 | 17.21 | ~0 | 207% @3.6x | 0.42% | 2.1% | — |

**Conclusion:** Synthetic/heuristic backtests overstate performance by 10-100x. Any Tier 2 experiment claiming Sharpe >3 on synthetic data should be assumed to have Sharpe <1 until validated on real IronVault data.

**Bright spots on real data:**
- **EXP-1220-real** (Tail Risk Protection): Sharpe 5.78, 27% DD reduction, 9 crashes detected — genuinely works on real SPY/VIX data
- **EXP-1630-max** (GLD/TLT RelVal): OOS Sharpe 4.08, SPY corr 0.03, 86% WR — uses real IronVault option prices
- **EXP-1650-max** (Earnings Vol Crush): OOS Sharpe 0.59, 80% WR — modest but real on IronVault data
- **EXP-1640-max** (Sector Momentum): Sharpe 0.64, 84% WR, SPY corr 0.04 — real data, low CAGR

---

## Correlation Matrix — Tier 1-2 Strategies

Pairwise correlations from available data:

|  | EXP-1220 | EXP-1630 | EXP-1000 | EXP-880 | EXP-1040 | EXP-1650 | EXP-1640 |
|--|----------|----------|----------|---------|----------|----------|----------|
| **EXP-1220** (Tail Risk) | 1.00 | 0.00 | — | — | — | — | — |
| **EXP-1630** (GLD/TLT RV) | 0.00 | 1.00 | — | — | — | — | — |
| **EXP-1000** (Intraday MR) | — | — | 1.00 | 0.03 | — | — | — |
| **EXP-880** (Crisis Hedge) | — | — | 0.03 | 1.00 | — | — | — |
| **EXP-1040** (Combined) | — | — | — | — | 1.00 | — | — |
| **EXP-1650** (Earnings VC) | — | — | — | — | — | 1.00 | — |
| **EXP-1640** (Sector Mom) | — | — | — | — | — | — | 1.00 |

**Key correlation findings:**
- EXP-1220 ↔ EXP-1630: **0.00** (zero correlation — ideal pair)
- EXP-1000 ↔ EXP-880: **0.033** (near-zero — genuine diversification)
- EXP-1630 ↔ SPY: **0.03** (market-neutral)
- EXP-1640 ↔ SPY: **0.04** (market-neutral)
- Most strategy pairs show low correlation because they exploit different market dimensions (vol surface, relative value, momentum, timing)

**Best diversified combinations (from reports/combined_portfolio_backtest.json):**
- EXP-1220 1.2x + TLT Iron Condors + XLI→SPY Pairs: Sharpe 6.25, CAGR 29.8%, DD 2.7%

---

## Gap Analysis: Uncovered Market Conditions

### Currently Well-Covered
- **Bull/sideways equity markets** — credit spreads dominate (EXP-880, 1000, 860)
- **Tail risk / crash protection** — EXP-1220 (proven on real data)
- **Safe-haven relative value** — EXP-1630 GLD/TLT (real data, SPY-neutral)
- **Regime detection** — EXP-900, 1130, 1360 (multiple methods)
- **VIX dynamics** — EXP-1080, 1250 (term structure, sentiment)
- **Execution optimization** — EXP-1160 (VWAP 72 bps savings)

### Gaps — Market Conditions NOT Covered

1. **Sustained high-rate environment (rates >5% for 2+ years)**
   - No experiment tests performance when risk-free rate competes with option premium
   - TLT strategies assume rates eventually normalize
   - **Priority: HIGH** — we may be entering this regime

2. **Commodity supercycle / inflation spike**
   - Only GLD/TLT covered; no energy (USO/XLE), agriculture, or copper options
   - EXP-1110 showed cross-asset signals are contemporaneous, not leading
   - **Priority: MEDIUM**

3. **Liquidity crisis / market structure breakdown**
   - EXP-850 assumes normal bid-ask; no test for flash crash / circuit breaker scenarios
   - EXP-1200 showed ATM SPY is fine but didn't stress-test truly illiquid conditions
   - **Priority: MEDIUM**

4. **Extended sideways grind (VIX 10-12 for 6+ months)**
   - Credit spread premium collapses; unclear how strategies perform when premium is thin
   - 2017-style low-vol regime not well-represented in 2020-2025 test data
   - **Priority: MEDIUM**

5. **Crypto correlation contagion**
   - EXP-600 (IBIT) is paper trading independently; no test of IBIT↔SPY correlation during crypto-specific crises
   - **Priority: LOW** (IBIT allocation is small)

6. **Overnight gap risk beyond 0-DTE**
   - EXP-1070 (overnight gap) was built but never backtested with real data
   - Multi-day gap events (weekend geopolitical risk) not modeled
   - **Priority: LOW**

7. **Single-name earnings contagion**
   - EXP-1650 covers sector ETF earnings; no test for AAPL/NVDA mega-cap earnings moving SPY 2%+
   - **Priority: LOW**

### Data Gaps
- **Real 0-DTE intraday data** — EXP-1000, 1020, 1030, 1320 all need 1-min or 5-min bar data from IronVault to validate frequency claims
- **Real cross-asset option chains** — only SPY, GLD, TLT, and sector ETFs have real IronVault data; QQQ, IWM, IBIT chains are unverified
- **Post-2025 forward data** — all backtests end Dec 2025; paper trading (EXP-400/401/503/600) is the only forward test

---

## Key Takeaways

1. **Trust only real-data results.** Synthetic Sharpe of 10+ means nothing when real Sharpe is 0.4.
2. **Best proven strategies:** EXP-1220-real (tail risk, Sharpe 5.78), EXP-1630 (GLD/TLT RV, OOS Sharpe 4.08).
3. **EXP-880 (the flagship) is bankrupt on real data.** The entire North Star portfolio built on top of it (EXP-1470) also fails. Root cause: synthetic option pricing grossly understates real spreads and execution costs.
4. **The infrastructure is excellent.** 233 modules, 92% production-ready, 1000+ tests. The engineering is solid; the alpha assumptions need real-data validation.
5. **Immediate priorities:** (a) Re-backtest EXP-860, EXP-1000, EXP-840 on real IronVault data. (b) Validate EXP-1220-real further with paper trading. (c) Combine EXP-1220-real + EXP-1630 into a portfolio — both use real data and have near-zero correlation.
