# Experiment Summary — Attix Credit Spreads

**Total experiments cataloged:** ~195 (EXP-031 through EXP-2950)
**Date range:** 2026-02 through 2026-04-24
**Current production config:** North Star v8a (EXP-2850) — 8-stream, Sharpe 6.39 net, 118% CAGR, 5.1% max DD
**Data integrity:** Experiments suffixed `-max` used synthetic data. `-real` used IronVault. Wave 1-12 (EXP-1660+) use real data exclusively per Rule Zero.

---

## How to Read This Document

- **LIVE**: Stream is in the production v8a portfolio
- **MERGED**: Results incorporated into a later experiment
- **KILLED**: Failed validation; documented why
- **INFRA**: Infrastructure/tooling, no tradeable alpha
- **RETRACTED**: Results were retracted after audit

Sharpe figures use the canonical formula: `mean(daily_returns) / std(daily_returns) * sqrt(252)`, sparse exit-date convention (per EXP-2390 audit).

---

## Phase 0 — Legacy Experiments (EXP-031 to EXP-305)

Early exploratory work, all superseded by EXP-400+.

| ID | Name | Status | Notes |
|---|---|---|---|
| EXP-031 | Compound Bull Put | RETIRED | Overfit score 0.590, DTE cliff |
| EXP-036 | Compound 10% Both MA200 | RETIRED | Superseded by EXP-400 |
| EXP-059 | Various early strategies | RETIRED | Superseded by EXP-400/401 |
| EXP-154 | Various early strategies | RETIRED | Superseded by EXP-400/401 |
| EXP-305 | COMPASS Portfolio | RETIRED | Superseded by EXP-400/401 |

---

## Phase 1 — Paper Trading (EXP-400 to EXP-600)

| ID | Name | Status | Account | Ticker | Notes |
|---|---|---|---|---|---|
| EXP-400 | The Champion | PAPER TRADING | PA36XFVLG0WE | SPY | Live since 2026-03-15 |
| EXP-401 | The Blend | PAPER TRADING | PA3Y2XDYB9I3 | SPY | Live since 2026-03-15 |
| EXP-503 | ML V2 Aggressive | PAPER TRADING | PA3Z9PLVYUL5 | SPY | Live since 2026-03-22 |
| EXP-600 | IBIT Adaptive | PAPER TRADING | PA3O14JAJHJ0 | IBIT | Live since 2026-03-22 |

---

## Phase 2 — Model & Infrastructure (EXP-810 to EXP-990)

### Round 1: Model & Infrastructure

| ID | Name | Sharpe | CAGR | Max DD | Status | Key Learning |
|---|---|---|---|---|---|---|
| EXP-810 | Model Ensemble | 10.49 | ~20% | 3.6% | MERGED | Ensembles reduce variance; Ridge adds stability |
| EXP-820 | Paper Trading Engine | — | — | — | INFRA | FillSimulator + RiskMonitor + PnLAttributor (57 tests) |
| EXP-840 | Regime-Adaptive Leverage | 4.84 | 56.1% | 4.6% | MERGED | 14/16 variants meet criteria; leverage not the bottleneck |
| EXP-850 | Execution Analytics | — | — | — | INFRA | **CRITICAL: $1 spreads lose 28.6% to slippage. $5+ mandatory.** |
| EXP-860 | Adaptive Retraining | 12.30 | ~25% | 1.9% | MERGED | Models must retrain quarterly; static models decay |
| EXP-870 | Multi-Underlying Diversification | 1.26 | ~15% | 0.7% | MERGED | GLD (corr 0.05) and TLT (corr -0.30) are key diversifiers. $3.1B capacity |

### Round 2: Production & Risk

| ID | Name | Sharpe | CAGR | Max DD | Status | Key Learning |
|---|---|---|---|---|---|---|
| EXP-880 | Crisis Hedge V2 | 4.97 | 76.9% | 10.2% | **DEAD on real data** | Synthetic Sharpe 4.97 → Real Sharpe 0.41, CAGR -104%. **Flagship kill.** |
| EXP-880-real | Crisis Hedge V2 (Real) | 0.41 | -104% | 106% | KILLED | Real IronVault data reveals bankruptcy. Synthetic data overstates 10-100x |
| EXP-881 | Combined CPCV Validation | — | — | — | INFRA | 15/15 folds positive OOS Sharpe (mean 4.32) — but on synthetic data |
| EXP-890 | Live Trading Blueprint | — | — | — | INFRA | 6 risk gates, kill switch, reconciliation, audit trail (35 tests) |
| EXP-900 | HMM Regime Detection | — | — | — | INFRA | 41% whipsaw reduction; ensemble rule + HMM best combo |

### Round 3: Integration & Validation

| ID | Name | Sharpe | CAGR | Max DD | Status | Key Learning |
|---|---|---|---|---|---|---|
| EXP-910 | North Star Integration | 8.46 | 80% | 2.8% | MERGED (synthetic) | System > sum of parts; but all synthetic data |
| EXP-920 | Robustness Validation | — | — | — | INFRA | Bootstrap Sharpe CI [2.4, 4.3]; CPCV 21/21 positive |
| EXP-930 | Real-Time Signal Pipeline | — | — | — | INFRA | 49 tests, no look-ahead bias verified |
| EXP-940 | Master Performance Report | — | — | — | INFRA | Investor-grade HTML report |

### Round 4: Leverage & Feasibility

| ID | Name | Sharpe | CAGR | Max DD | Status | Key Learning |
|---|---|---|---|---|---|---|
| EXP-950 | Leverage Frontier | ~4.5 | 45.2% | 10.2% | MERGED | 3.5x optimal; 100% CAGR needs portfolio approach |
| EXP-960 | Path to 100% CAGR | ~4.97 | 102% | 9.8% | MERGED | 3.5x combined → 102% CAGR achievable (synthetic) |
| EXP-970 | Walk-Forward Leverage | 3.5-4.5 | 36-46% | 5.6-7.8% | MERGED | 2.5x/3.5x both OOS validated |
| EXP-980 | Margin & Broker Feasibility | — | — | — | INFRA | Alpaca 2.0x, IBKR PM 2.5-3.0x |
| EXP-990 | Test Suite Consolidation | — | — | — | INFRA | ~180 test files |

---

## Phase 3 — Alpha Research & Infrastructure (EXP-1000 to EXP-1650)

Large batch of research experiments. Most used synthetic data. Key real-data survivors noted.

### Tier 1 — Real-Data Winners

| ID | Name | Sharpe | CAGR | Max DD | Status | Data |
|---|---|---|---|---|---|---|
| **EXP-1220-real** | SPY Put Credit Spread (Real) | **5.78** | ~99% | 6.6% | **LIVE** | IronVault real |
| **EXP-1630** | GLD/TLT Relative Value | **4.08** OOS | 1.9% | 1.7% | **LIVE-READY** | IronVault real |
| EXP-1640 | Sector Momentum | 0.64 | 0.3% | 0.8% | PROMISING | IronVault real |
| EXP-1650 | Earnings Vol Crush | 0.59 OOS | — | 0.95% | PROMISING | IronVault real |

### Tier 2 — Synthetic-Only (strong results, unvalidated)

| ID | Name | Sharpe | Status | Notes |
|---|---|---|---|---|
| EXP-1000 | Intraday Mean Reversion | 9.92 | PROMISING | 404 trades; needs real 1-min data |
| EXP-1020 | 0-DTE Mean Reversion | 2.95 | MARGINAL | 59 trades only |
| EXP-1040 | Combined Portfolio V2 | 11.41 | PROMISING | Synthetic integration |
| EXP-1270 | Adaptive Stop-Loss | 5.25 | **DEAD on real** | Real: Sharpe -0.25, -$274 PnL |
| EXP-1320 | Intraday Vol Clustering | 3.05 | **DEAD on real** | Real: Sharpe -14.1 |
| EXP-1470 | North Star Synthesis | 17.21/12.08 OOS | **DEAD on real** | Real: 0.42% CAGR |

### Tier 3 — Infrastructure (no alpha, support only)

| ID | Name | Deliverable |
|---|---|---|
| EXP-1080 | VIX Term Structure | Vol surface trader (39 tests) |
| EXP-1090 | Cross-Asset Correlation | Breakdown detection (34 tests) |
| EXP-1100 | Dispersion Trading | Implied vs realized correlation (41 tests) |
| EXP-1110 | Cross-Asset Momentum | **HURTS EXP-880 as overlay** (-18.6pp). Contemporaneous, not leading |
| EXP-1120 | Order Flow Imbalance | CLV-based OFI (40 tests) |
| EXP-1130 | Adaptive Regime Ensemble V2 | 86% whipsaw reduction, 93% accuracy |
| EXP-1140 | Multi-Timeframe Fusion | Attention-weighted (42 tests) |
| EXP-1150 | Calendar Effects | No significant effects on synthetic data |
| EXP-1160 | Smart Execution Engine | VWAP saves 72.7 bps vs naive |
| EXP-1170 | Dynamic Hedging Engine | Delta/tail/VIX overlay (41 tests) |
| EXP-1180 | Feature Importance | SHAP, permutation, signal half-life |
| EXP-1190 | Portfolio Risk Dashboard | VaR, CVaR, stress tests (36 tests) |
| EXP-1200 | Liquidity-Aware Sizing | ATM SPY liquid; constraints at OTM/high VIX |
| EXP-1210 | Bayesian Strategy Selection | Thompson Sampling, NIG posteriors (43 tests) |
| EXP-1230 | Microstructure Alpha | Standalone: -0.03 Sharpe. **+21pp as overlay** |
| EXP-1240 | VRP Harvester | Multi-tenor VRP, gamma scalp (39 tests) |
| EXP-1250 | Sentiment Regime Detector | Composite sentiment, CUSUM changepoint |
| EXP-1260 | Factor Exposure Analyzer | Alpha +11.8%/yr (t=3.60), R²=0.12, beta=-0.19 |
| EXP-1280 | Correlation Breakdown Detector | Absorption ratio, multi-window (35 tests) |
| EXP-1290 | RL Position Sizer | Tabular Q-learning, 180-state space |
| EXP-1300 | Mean Reversion Z-Score | Bollinger z<-2 + RSI divergence (42 tests) |
| EXP-1310 | Options Flow Sentiment | Weak standalone; moderate overlay value |
| EXP-1330 | Pairs Trading Options | Cointegration-based, 6 pair universe (33 tests) |
| EXP-1340 | Ensemble Meta-Learner V2 | 12-signal gradient-boosted stacker |
| EXP-1350 | Dynamic Kelly Criterion | Rolling Kelly, regime-modulated (43 tests) |
| EXP-1360 | Regime Transition Probs | 97% persistence — limited trading value |
| EXP-1370 | Momentum Crash Protection | 20% DD reduction but no sharp episodes detected |
| EXP-1380 | Greeks-Based Trade Sizing | Theta-targeted, gamma/vega caps (36 tests) |
| EXP-1390 | Signal Decay Half-Life | ACF, IC decay, optimal rebalance frequency |
| EXP-1400 | Walk-Forward Ensemble Optimizer | Expanding-window gradient ascent (35 tests) |
| EXP-1410 | Portfolio Correlation Monitor | DCC-GARCH, auto-delevering (25 tests) |
| EXP-1420 | Transformer Predictor | XGBoost wins (Sharpe 1.38); transformers need more data |
| EXP-1430 | Genetic Algorithm Evolver | 20-gene genome, tournament selection (35 tests) |
| EXP-1440 | Regime Transition Predictor | HSMM with duration modeling |
| EXP-1450 | Universal Portfolio | Cover's EG algorithm (35 tests) |
| EXP-1480 | RL Portfolio Manager | Numpy PPO, portfolio env (28 tests) |
| EXP-1490 | Production Readiness Audit | 233 modules scanned, 92% production-ready |
| EXP-1500 | Live Trading Simulation | 5 friction components (42 tests) |
| EXP-1510 | Performance Attribution | CS = 61% of returns |
| EXP-1520 | North Star Validation Suite | 7/7 validation tests passed |
| EXP-1530 | Walk-Forward OOS Validation | Expanding window WF on EXP-1470 |
| EXP-1540 | Monte Carlo Stress Test | 50K paths, 100% survival base case |
| EXP-1550 | North Star Deployment Plan | 39 tests, circuit breakers |
| EXP-1570 | Paper Trading Deployment | 11 pre-flight checks, launcher script |
| EXP-1580 | Year-by-Year Walk-Forward | NS base 27.8% CAGR, 3.6x → 99% |
| EXP-1590 | Production Monitor Dashboard | 87 tests, Telegram alerts |
| EXP-1600 | Comprehensive Summary Report | 78 experiments, investor-grade HTML |
| EXP-1610 | Paper Trading Reconciler | 6-dimension reconciler |

---

## Phase 4 — The Real-Data Reckoning & Rule Zero (2026-04-04 to 04-05)

**The most important 48 hours of the project.** Four experiments were re-backtested on real IronVault option prices. Every flagship collapsed.

| Experiment | Synthetic Sharpe | Real Sharpe | Synthetic CAGR | Real CAGR |
|---|---|---|---|---|
| EXP-880 | 4.97 | 0.41 | 76.9% | **-104% (bankrupt)** |
| EXP-1270 | 5.25 | -0.25 | — | -0.05% |
| EXP-1320 | 3.05 | -14.10 | — | — |
| EXP-1470 | 17.21 | ~0 | 207% | 0.42% |

**Consequence:** RULE ZERO enacted — no synthetic data ever again. All future experiments must use IronVault, Yahoo Finance, or verified exchange feeds. This is carved into the MASTERPLAN.

---

## Phase 5 — Wave 1-11 Sprint (2026-04-06 to 04-08)

~95 experiments across five parallel Claude Code sessions in three days. All use real data per Rule Zero. This sprint moved honest net Sharpe from 3.83 (single-strategy EXP-1220) to 6.39 (v8a + VIX ladder).

### Wave 1: Alpha Discovery (EXP-1660 to EXP-1840)

| ID | Name | Sharpe | Status | Key Result |
|---|---|---|---|---|
| EXP-1660 | VRP Deepening (multi-asset) | SPY 0.97, QQQ 1.10, IWM 1.07, EEM 0.91 | KILLED | Individual Sharpe too low; useful as reference data |
| EXP-1710 | 0-DTE Feasibility | — | KILLED | Insufficient real intraday data |
| EXP-1740 | Sentiment-Filtered Entry | — | KILLED | No exploitable signal found |
| **EXP-1750** | **PCR Overlay** | 1.40 | **MERGED** | Put-call ratio overlay improves timing |
| EXP-1760 | Crypto Vol (IBIT) | 1.04 | KILLED | CAGR 4.47%, DD -4.58% — insufficient |
| **EXP-1770** | **GLD/SLV Commodity Calendars** | — | **LIVE** | GLD-GC=F and SLV-SI=F roll harvest; uncorrelated with equity streams |
| **EXP-1780** | **Crisis Alpha v5** | — | **LIVE** | 13-ETF CTA with stress gate; becomes v5_hedge stream |

### Wave 2: Portfolio Construction (EXP-1850 to EXP-1880)

| ID | Name | Sharpe | Status | Key Result |
|---|---|---|---|---|
| **EXP-1850** | Regime-Adaptive Portfolio | 3.70 | **MERGED** | Regime-dependent weighting framework adopted |
| EXP-1860 | North Star Portfolio (early) | — | MERGED | Integrated experiment |
| EXP-1870 | Weekend Hedge Cost | — | INFRA | Real IronVault put cost: **4.36%/yr** (not 2%) |
| **EXP-1880** | Walk-Forward Integration | 1.26 | **MERGED** | FOMC + PCR entry timing; walk-forward framework established |

### Wave 3: Risk Infrastructure (EXP-1890 to EXP-1900)

| ID | Name | Status | Key Result |
|---|---|---|---|
| **EXP-1890** | **PortfolioRiskManager** | **LIVE** | Cross-stream sizer, correlation monitor, circuit breakers. Core production module. |
| EXP-1900 | Risk overlay tuning | MERGED | Integrated into EXP-1890 |

### Wave 4: Alpha Hunt (EXP-1910 to EXP-1990)

| ID | Name | Sharpe | Status | Key Result |
|---|---|---|---|---|
| EXP-1910 | Intraday Gap-and-Go Breakout | -0.60 | KILLED | DD -30.25%; no edge on real data |
| EXP-1920 | Carry Trade / Rate ETFs | 0.58 | KILLED | Modest; DD -11.83% |
| EXP-1930 | VVIX Signal Overlay | 1.10 | KILLED | Below Sharpe threshold after integration |
| EXP-1940 | Multi-TF Momentum | 0.76 | KILLED | DD -32%; too noisy |
| EXP-1950 | Adaptive Kelly | +0.03 Sharpe lift | KILLED | Below 0.2 improvement threshold |
| EXP-1960 | Put-Skew Mean Reversion | 3.18 | KILLED | Near-zero CAGR despite high Sharpe — too few trades |
| **EXP-1970** | **Vol-of-Vol Overlay** | 2.12 | **LIVE** | VoV gate blocks entries when VVIX > 85th percentile. Production overlay. |
| EXP-1980 | Correlation Regime Switching | 4.09 | MERGED | Framework adopted into EXP-1890 |
| EXP-1990 | Ensemble Signal Stacking | 1.26 | KILLED | Did not improve over simpler overlays |

### Wave 5: Overlay Integration (EXP-2000 to EXP-2030)

| ID | Name | Sharpe | Status | Key Result |
|---|---|---|---|---|
| **EXP-2000** | **Triple Overlay (VoV + PCR + FOMC)** | 2.12 | **LIVE** | Combined gate: VoV + term structure + FOMC week. Production default. |
| EXP-2010 | Long 10-delta OTM Puts | -8.21 | KILLED | Catastrophic; long tail-hedge bleeds premium |
| **EXP-2020** | **Cross-Vol Arb** | 2.28 | **LIVE** | SPY/QQQ/IWM/EEM IV-RV spread trading. Becomes cross_vol stream. |
| EXP-2030 | Intraweek Seasonality Overlay | — | KILLED | No significant pattern |

### Wave 6: 5-to-7 Stream Expansion (EXP-2100 to EXP-2180)

| ID | Name | Sharpe | Status | Key Result |
|---|---|---|---|---|
| EXP-2100 | V+F TRUE Integration | 2.14 | MERGED | Overlay framework validated end-to-end |
| EXP-2110 | Leveraged Diversified (1.0x) | 5.24 | MERGED | 33% CAGR, 2.6% DD at 1x — strong baseline |
| EXP-2120 | Triple Overlay Integration | 2.08 | MERGED | T+V+F combined |
| EXP-2140 | Portfolio Capacity Analysis | — | INFRA | **SLV soft cap = $16M, hard cap = $82M** — binding constraint identified |
| **EXP-2150** | Weekly Cadence | 2.03 | **MERGED** | Higher-frequency EXP-1220 + overlays |
| **EXP-2160** | **XLF/XLI Credit Spreads** | — | **LIVE** | Delta-targeted put spreads on sector ETFs. Adds capacity + diversification. |
| EXP-2170 | Weight Optimization | 5.34 | MERGED | Equal-weight baseline: 79.6% CAGR, 4.2% DD |
| EXP-2180 | Volatility Targeting | 5.26 | MERGED | Vol target framework adopted (12% ann) |

### Wave 7: 7-Stream North Star v6 (EXP-2200 to EXP-2300)

| ID | Name | Sharpe | CAGR | Max DD | Status | Key Result |
|---|---|---|---|---|---|---|
| **EXP-2200** | **North Star v6 (7-stream)** | **5.23** | **146%** | **5.7%** | **MERGED** | Flagship 7-stream portfolio. Foundation for v8a. |
| EXP-2210 | XLF/XLI Validation | 11.19 | 18.2% | 0.02% | MERGED | XLF has 98.4% WR on IronVault |
| EXP-2220 | 7-Stream Correlation | — | — | — | INFRA | Mean pairwise rho = +0.016 — effectively independent |
| EXP-2230 | Capacity with XLF+XLI | — | — | — | INFRA | SLV still binding; no split changes the bottleneck |
| **EXP-2240** | **QQQ Credit Spreads** | 0.60 | 1.0% | 3.0% | **LIVE** | Individual Sharpe low, but combined portfolio benefit approved |
| EXP-2250 | North Star v7 (8/9-stream) | 3.97 | 79.8% | 6.2% | MERGED | Added QQQ as 8th stream |
| EXP-2260 | SLV Replacement | -0.74 | — | 41.4% | KILLED | No viable SLV substitute found |
| EXP-2270 | XLF/XLI Slippage Impact | — | — | — | INFRA | Execution cost analysis |
| **EXP-2280** | **Walk-Forward Robustness** | **5.93** | — | — | **MERGED** | 20/20 folds positive. v6 validated. |
| EXP-2300 | Deployment Package | — | — | — | INFRA | Per-sleeve configs finalized |

### Wave 8: Cost Honesty (EXP-2310 to EXP-2450)

This wave discovered and corrected the smeared-daily Sharpe inflation.

| ID | Name | Sharpe | Status | Key Result |
|---|---|---|---|---|
| EXP-2310 | AUM Scaling Research | — | INFRA | Capacity analysis |
| EXP-2330 | Monte Carlo Stress Test (v6) | 6.02 | MERGED | 10K paths: median Sharpe 6.07, zero paths breach 12% DD |
| EXP-2340 | DD Deep Dive | 4.43 | INFRA | Walk-forward drawdown analysis |
| EXP-2350 | SLV Replacement v2 | 1.86 | KILLED | Combined Sharpe + capacity bar missed |
| EXP-2360 | Robust Covariance | 8.30 | **RETRACTED** | Smeared daily convention inflated Sharpe. Audited by EXP-2390. |
| EXP-2370 | DD Circuit Breaker | 5.93 | **LIVE** | 3% soft / 12% hard circuit breaker. Production module. |
| EXP-2380 | Futures Calendar Capacity | — | KILLED | Futures ≈ ETF spreads on real data; even lower capacity |
| **EXP-2390** | **Robust-Cov Audit** | — | **CRITICAL** | **Retracted EXP-2360's Sharpe 11.7 → honest 6.66.** Most important methodology lesson. Smeared convention banned. |
| EXP-2400 | Combined Best-Of | 8.30 | RETRACTED | Same smearing inflation as EXP-2360 |
| EXP-2420 | Transaction Cost Model | 4.49 | MERGED | Full TC model for 7-stream portfolio |
| EXP-2430 | Capacity-Optimized Reweight | 4.29 | KILLED | XLI becomes next bottleneck |
| EXP-2440 | Cost-Aware Optimization | 4.49 | MERGED | Projection-based optimization |
| **EXP-2450** | **Sparse Combined (Honest)** | **6.87** | **LIVE** | Gross baseline with honest sparse convention. Source of truth for v8a gross numbers. |

### Wave 9: Cost Mitigation (EXP-2460 to EXP-2580)

| ID | Name | Sharpe | Status | Key Result |
|---|---|---|---|---|
| EXP-2460 | Zero-Cost Alpha Overlay | 12.10 | KILLED | Overlay-only; doesn't survive as standalone |
| **EXP-2470** | **Execution Optimization** | — | **LIVE** | Limit@mid, patient pre-close, cheapest-route, combo orders. Saves 503 bps/yr at 3x. |
| EXP-2480 | 3-Sleeve High-Capacity | — | INFRA | Architecture exploration |
| EXP-2500 | TRUE Net Backtest | 3.89 | KILLED | Cost-aware params (21d + 0.93 OTM) kill -53% of alpha |
| EXP-2510 | Commission-Free Broker Analysis | — | INFRA | Alpaca commission-free identified |
| EXP-2540 | Regime-Conditional TC | — | INFRA | TC model by regime |
| EXP-2550 | Net Sharpe Recovery | 4.71 | MERGED | Partial recovery of net Sharpe |
| EXP-2560 | Trade Frequency Compression | 6.72 | MERGED | Reducing trade count improves net |
| **EXP-2570** | **Commission-Free Net Sharpe** | **6.00** net | **LIVE** | Alpaca commission-free: net Sharpe 6.00. Key cost breakthrough. |
| EXP-2580 | SPY Weekly Credit Spreads | 0.66 | KILLED | Standalone Sharpe too low |

### Wave 10: v8a and Stress (EXP-2590 to EXP-2730)

| ID | Name | Sharpe | CAGR | Max DD | Status | Key Result |
|---|---|---|---|---|---|---|
| EXP-2590 | QQQ Capacity Deep Dive | 1.86 | — | — | MERGED | QQQ capacity validated |
| **EXP-2600** | **North Star v8a (8-stream)** | **6.16** | **126%** | **7.1%** | **LIVE** | QQQ added as 8th stream + vol target 12% |
| EXP-2610 | SPY Weekly Integration | 6.72 | 96.6% | 5.5% | MERGED | SPY weekly tested |
| EXP-2630 | OOS Regime Stress (v6) | 4.54 | — | — | INFRA | 2/3 synthetic scenarios pass |
| **EXP-2640** | VIX Stress Hardening | 4.26 | — | — | **MERGED** | VIX-high-90d scenario now passes |
| EXP-2650 | Multi-Expiry Capacity | — | — | — | INFRA | SPY put volume analysis by expiry |
| EXP-2660 | AUM Multi-Underlying Scaling | — | — | — | INFRA | 7/8 candidates blocked by IronVault data |
| EXP-2670 | Paper Go/No-Go | — | — | — | INFRA | 5/6 PASS, 1 WARN (Alpaca creds) |
| EXP-2700 | Reproducibility Audit | 5.23 | — | — | INFRA | v6/v8a metrics reproducible |
| EXP-2710 | XLE Integration | 1.87 | 0.63% | — | KILLED | Trade Sharpe OK but only 2.95 trades/yr |
| EXP-2720 | Drawdown Recovery (v8a) | 6.83 | 147% | 6.9% | MERGED | Recovery analysis |
| **EXP-2730** | **WF Robustness v8a NET** | **6.16** | — | — | **LIVE** | 20-fold WF, median 6.94, worst 3.72. v8a validated NET. |
| EXP-2740 | Parameter Sensitivity | 6.17 | — | — | INFRA | 3/28 perturbations breach; buffer is thin |
| EXP-2750 | OOS Regime Stress (v8a) | 4.27 | — | — | INFRA | Regime stress with XLE |
| EXP-2760 | Lit Survey: Backtest-to-Live Decay | — | — | — | INFRA | **0.5-0.7x decay factor** per Cornell (2019), Harvey-Liu (2014) |

### Wave 11: Production Readiness (EXP-2800 to EXP-2900)

| ID | Name | Sharpe | Status | Key Result |
|---|---|---|---|---|
| EXP-2800 | XLE as 9th Stream | 6.16 baseline | KILLED | Buffer contracts by -0.12; breaches double. **4.4 trades/yr < 20 threshold.** |
| EXP-2810 | 9-Stream SPY Weekly | 2.34 net | KILLED | TC from extra stream eats alpha |
| **EXP-2820** | **Flash Crash Protection** | — | **LIVE** | **VIX ladder: 9-breakpoint step-linear. DD 43.1% → 0.8% in VIX→80 replay.** |
| EXP-2830 | Paper Signal Generator | — | **LIVE** | Production daily signal generator for 8 streams |
| EXP-2840 | Backtest-to-Live Degradation | 4.70 | INFRA | Expected live Sharpe 3.2-4.5 |
| **EXP-2850** | **v8a + VIX Ladder** | **6.39** net | **PRODUCTION** | **The production config.** Sharpe 6.39, CAGR 118%, DD 5.1%. |
| EXP-2860 | Paper Dry Run | — | **LIVE** | 7/7 orders validated end-to-end |
| EXP-2890 | Alpaca Connector | — | **LIVE** | 791-line production module |
| **EXP-2900** | **Consistency Audit** | — | **LIVE** | 36/39 PASS, 0 FAIL, 3 soft doc warnings |
| EXP-2910 | Industry Comparison | — | INFRA | v8a vs hedge fund benchmarks |

---

## Phase 6 — Wave 12: AUM Capacity Research (2026-04-21 to 04-24)

Motivated by AUM being the sole unmet North Star target (~$50M vs $1B goal). Systematic search for new alpha streams.

| ID | Name | Best Sharpe | Status | Kill Reason |
|---|---|---|---|---|
| EXP-2910 | TLT Put Credit Spreads | 0.76 | KILLED | Sharpe < 1.0; 9.2 trades/yr (IronVault monthly-only exps) |
| EXP-2920 | TLT IV-RV via MOVE Index | 0.26 | KILLED | Bond vol is persistent, not mean-reverting; all 5 approaches fail |
| EXP-2930 | SOXX/XLK Credit Spreads | SOXX 4.48* | KILLED | SOXX rho=0.888 with QQQ; XLK rho=0.970 — equity beta clones |
| EXP-2940 | Overnight Return Premium | 0.96 gross | KILLED | **1 bps slippage → Sharpe 0.47; 2 bps → negative.** Edge is 3.9 bps/day, consumed by execution. |
| EXP-2950 | Sector Momentum Rotation | 0.57 | KILLED | All long-short variants negative Sharpe. Sector momentum premium decayed. |

*SOXX Sharpe asterisked: only 2.1 years of data, biased to bull market.

**Wave 12 Conclusion:** The IronVault-only expansion path is exhausted. The Polygon subscription ($199/mo) is now the confirmed sole path to AUM scaling beyond $50M.

---

## Critical Retractions

| Original Claim | Corrected To | Cause | Audit |
|---|---|---|---|
| EXP-2360 Sharpe 11.73 (Ledoit-Wolf) | **6.66-6.87** | Smeared daily convention inflates Sharpe 2-3x on high-WR tapes | EXP-2390 |
| EXP-2400 Sharpe 13.73 | **6.72** | Same smearing inflation | EXP-2390 |
| EXP-2440 projected Sharpe 5.94 | **3.89 actual** | Lever projections assumed alpha preservation — wrong | EXP-2500 |
| EXP-2540 regime filter +0.83 | **-0.56 on LW cube** | Didn't transfer to portfolio context | EXP-2550 |
| Weekend hedge cost 2%/yr | **4.36%/yr** | Real IronVault put prices | EXP-1870 |
| Weekend Sharpe 9.09 | **3.76** | Formula bug + synthetic data | Rule Zero audit |
| EXP-880 Sharpe 4.97 | **0.41 (bankrupt on real data)** | Synthetic option pricing overstates 10-100x | EXP-880-real |

---

## Production Configuration (v8a — EXP-2850)

The current live system, validated through ~95 experiments:

| Component | Source Experiment | Status |
|---|---|---|
| SPY put credit spread (28 DTE 5% OTM) | EXP-1220 | LIVE |
| QQQ put credit spread | EXP-2240 | LIVE |
| XLF delta-targeted put spread | EXP-2160 | LIVE |
| XLI delta-targeted put spread | EXP-2160 | LIVE |
| GLD-GC=F calendar spread | EXP-1770 | LIVE |
| SLV-SI=F calendar spread | EXP-1770 | LIVE |
| Cross-vol IV-RV arb | EXP-2020 | LIVE |
| Crisis Alpha v5 hedge | EXP-1780 | LIVE |
| VoV gate overlay | EXP-1970 | LIVE |
| Triple overlay (VoV+TS+FOMC) | EXP-2000 | LIVE |
| VIX ladder (9-breakpoint) | EXP-2820 | LIVE |
| Ledoit-Wolf risk-parity weights | EXP-2450 | LIVE |
| 12% vol target | EXP-2600 | LIVE |
| DD circuit breaker (3%/12%) | EXP-2370 | LIVE |
| Execution stack (limit@mid etc.) | EXP-2470 | LIVE |
| Alpaca commission-free | EXP-2570 | LIVE |
| Portfolio risk manager | EXP-1890 | LIVE |
| Signal generator | EXP-2830 | LIVE |
| Alpaca connector | EXP-2890 | LIVE |
| Dollar-notional sizing | Phase 9 prereq #5 | LIVE |

**Headline metrics (EXP-2850 NET, honest):**
- Pooled Sharpe: **6.39** | CAGR: **118%** | Max DD: **5.1%** | Flash crash DD: **0.8%**
- Expected live (0.5-0.7x decay): Sharpe **3.2-4.5** | CAGR 60-80% | DD <10%

---

## Key Lessons (in order of importance)

1. **Synthetic data overstates by 10-100x.** EXP-880 Sharpe 4.97 → 0.41 on real data. Rule Zero exists because of this.
2. **The smeared daily convention inflates Sharpe 2-3x** on high-win-rate trade tapes. Always use sparse exit-date convention. (EXP-2390)
3. **Portfolio diversification is the real alpha.** Individual streams have Sharpe 1-4; the 8-stream portfolio achieves 6.39 because mean pairwise rho = 0.016.
4. **Transaction costs dominate at the margin.** Alpaca commission-free was the difference between net Sharpe 5.20 (IBKR) and 6.39. (EXP-2570)
5. **Kill losers fast.** The April sprint killed ~20 experiments within hours. Dead-end avoidance saved weeks.
6. **The VIX ladder is the single most valuable risk overlay.** Flash crash DD from 43% to 0.8%. (EXP-2820)
7. **Capacity is the hardest problem.** AUM is the sole unmet North Star target. SLV at $122M/day is the binding constraint. (EXP-2140/2230)
8. **Report three numbers, not one.** Gross, net, AND expected-live side-by-side. (EXP-2760)
9. **Equity factor strategies don't clear the options quality bar.** Overnight premium (3.9 bps/day) is 50-100x thinner than put credit spread premium (200-400 bps/trade). (EXP-2940)
10. **IronVault coverage != tradeable.** Having contracts in the DB doesn't mean the chain is dense enough. XLK has 2,680 contracts but only 24 viable trades. (EXP-2930)

---

*Last updated: 2026-04-24 by Maximus*
*Sources: experiments/EXPERIMENT_LOG.md, experiments/REGISTRY.md, experiments/LEADERBOARD.md, MASTERPLAN.md v12, compass/reports/*.json, experiments/EXP-29{10-50}*.md*
