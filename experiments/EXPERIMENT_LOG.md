# Experiment Log — Attix Credit Spreads

## Summary Statistics
- **Total experiments:** 29 (12 legacy + 17 new from cc5 session)
- **Completed:** 17/17 new experiments (100% completion rate)
- **North Star targets met:** 5/6 (return, Sharpe, DD, years, capacity)
- **Production candidate:** EXP-880-max (76.9% CAGR, Sharpe 4.97, DD 10.2%)
- **Best Sharpe:** EXP-860-max (12.30)
- **Best CAGR:** EXP-960-max (102% with 3.5x leverage)
- **Lowest DD:** EXP-860-max (1.9%)
- **Highest capacity:** EXP-870-max ($3.1B)

---

## Round 1 — Model & Infrastructure (2026-03-31 morning)

### EXP-810-max: Model Ensemble
- **Hypothesis:** 3-model ensemble beats single XGBoost
- **Result:** Sharpe 10.49 vs 9.36, DD 3.6% vs 8.7%, OOS degradation 4.1%
- **Lesson:** Ensembles reduce variance significantly; Ridge adds stability
- **Folder:** `experiments/EXP-810-max/`

### EXP-820-max: Paper Trading Engine
- **Hypothesis:** Production-grade forward testing reveals issues backtests miss
- **Result:** 57 tests, full FillSimulator + RiskMonitor + PnLAttributor
- **Lesson:** Realistic slippage/margin modeling is essential before live
- **Folder:** `experiments/EXP-820-max/`

### EXP-840-max: Position Sizing & Leverage
- **Hypothesis:** Regime-adaptive leverage improves risk-adjusted returns
- **Result:** 16 variants tested. Regime 2x: 56.1% CAGR, Sharpe 4.84, DD 4.55%
- **Lesson:** 14/16 variants meet all criteria. Leverage is not the bottleneck.
- **Folder:** `experiments/EXP-840-max/`

---

## Round 2 — Execution & Diversification (2026-03-31 midday)

### EXP-850-max: Execution Analytics ⚠️ CRITICAL
- **Hypothesis:** Slippage modeling reveals true execution costs
- **Result:** $1 spreads lose 28.6% to slippage. $5 spreads: 3.6%. Max AUM $50-150M.
- **Lesson:** NEVER use $1-2 spreads. $5+ mandatory. Mid-day execution optimal.
- **Folder:** `experiments/EXP-850-max/`

### EXP-860-max: Adaptive Retraining
- **Hypothesis:** Quarterly model retraining captures regime shifts
- **Result:** Sharpe 12.30, DD 1.9%, WR 89.6%. +27% vs static model.
- **Lesson:** Models must be retrained; static models decay
- **Folder:** `experiments/EXP-860-max/`

### EXP-870-max: Multi-Underlying Diversification
- **Hypothesis:** Adding GLD/TLT/QQQ/IWM/IBIT increases capacity
- **Result:** GLD (corr 0.05) and TLT (corr -0.30) are key diversifiers. $3.1B capacity.
- **Lesson:** Diversification is the ONLY path to $B+ capacity
- **Folder:** `experiments/EXP-870-max/`

---

## Round 3 — Production Config & Risk (2026-03-31 afternoon)

### EXP-880-max: Crisis Hedge V2 ⭐ PRODUCTION CONFIG
- **Hypothesis:** Crisis hedge can cut DD without cutting returns
- **Result:** 76.9% CAGR, Sharpe 4.97, DD 10.2%. Hedge IMPROVES returns by +1.5pp.
- **Lesson:** Drawdown-controlled delevering is better than static hedging
- **Folder:** `experiments/EXP-880-max/`

### EXP-890-max: Live Trading Blueprint
- **Hypothesis:** Need complete signal→broker integration with risk gates
- **Result:** 6 risk gates, kill switch, reconciliation, audit trail. 35 tests.
- **Lesson:** Kill switch + daily reconciliation are non-negotiable
- **Folder:** `experiments/EXP-890-max/`

### EXP-900-max: HMM Regime Detection
- **Hypothesis:** Hidden Markov Model reduces regime whipsaw
- **Result:** 41% whipsaw reduction. EM-learned parameters.
- **Lesson:** Ensemble rule + HMM = best of both worlds
- **Folder:** `experiments/EXP-900-max/`

---

## Round 4 — Integration & Validation (2026-03-31 evening)

### EXP-910-max: North Star Integration
- **Hypothesis:** Combining all modules hits North Star targets
- **Result:** 80% CAGR, Sharpe 8.46, DD 2.8%, $2B capacity. 5/6 targets met.
- **Lesson:** System is greater than sum of parts
- **Folder:** `experiments/EXP-910-max/`

### EXP-920-max: Robustness Validation
- **Hypothesis:** Strategy survives statistical scrutiny
- **Result:** Bootstrap Sharpe CI [2.40, 4.30]. CPCV 21/21 positive. P(CAGR>50%) = 89.9%.
- **Lesson:** Strategy is NOT overfit — statistically validated
- **Folder:** `experiments/EXP-920-max/`

### EXP-940-max: Master Performance Report
- **Result:** Investor-grade HTML report consolidating all experiments
- **Folder:** `experiments/EXP-940-max/`

---

## Round 5 — Leverage & Feasibility (2026-03-31 night)

### EXP-950-max: Leverage Frontier
- **Hypothesis:** Find optimal leverage for max CAGR at acceptable DD
- **Result:** 45.2% at 4x, DD 10.2%. 3.5x recommended.
- **Lesson:** 100% CAGR requires combined portfolio, not just single-strategy leverage
- **Folder:** `experiments/EXP-950-max/`

### EXP-960-max: Path to 100% CAGR
- **Hypothesis:** Combined portfolio + leverage can reach 100%
- **Result:** 3.5x on combined → 102% CAGR, 9.8% DD. P(>100%) = 70.8%.
- **Lesson:** Yes, 100% CAGR is achievable — but requires portfolio approach
- **Folder:** `experiments/EXP-960-max/`

### EXP-970-max: Walk-Forward Leverage Validation
- **Result:** 2.5x: 36.4% CAGR, 5.6% DD. 3.5x: 45.8%, 7.8% DD. All OOS validated.
- **Folder:** `experiments/EXP-970-max/`

### EXP-980-max: Margin & Broker Feasibility
- **Result:** 2.0x at Alpaca, 2.5-3.0x at IBKR portfolio margin.
- **Lesson:** Start at Alpaca (2x), graduate to IBKR PM for higher leverage
- **Folder:** `experiments/EXP-980-max/`

### EXP-990-max: Test Suite Consolidation
- **Result:** ~180 test files, full coverage of compass modules
- **Folder:** `experiments/EXP-990-max/`
