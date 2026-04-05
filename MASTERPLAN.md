# MASTERPLAN.md — Operation Crack The Code

## Mission
Build a validated, multi-strategy options trading system on SPY. Data-driven approach: kill losing strategies, optimize winners, follow what the data says. Paper trade the winners, then go live.

## North Star (Updated 2026-04-05)

| Target | Goal | v4 (CAGR focus) | v5 (Sharpe focus) | Adaptive+Hedge | Status |
|--------|------|-----------------|-------------------|----------------|--------|
| **CAGR** | 100% | **101.0%** | 92.6% | **102.0%** | **MET** (v4, adaptive) |
| **Sharpe** | 6.0 | 3.94 | **4.49** | **9.09** ⚠️ | PENDING VALIDATION |
| **Max DD** | ≤12% | 8.0% | **5.0%** | 7.5% | **MET** (all variants) |
| **COVID DD** | <12% | 6.6% | **0.8%** | — | **MET** (v5 best) |
| **Multi-strat** | Yes | 5 strategies | 5 strategies | 5 strategies | **MET** |
| **6/6 years** | Yes | ✅ | ✅ | ✅ | **MET** |
| **All crises** | <12% | ✅ 5/5 | ✅ 5/5 | ✅ 4/4 OOS | **MET** |

> **Three portfolios, three trade-offs:**
> - **v4** (DynamicSizer): 101% CAGR, 3.94 Sharpe — maximum absolute return
> - **v5** (vol-target + conviction): 92.6% CAGR, 4.49 Sharpe, 0.8% COVID DD — best risk-adjusted
> - **Adaptive+Hedge** (e7dd2d7): 102% CAGR, 9.09 Sharpe — **PENDING VALIDATION** (Sharpe may be inflated by simulated hedge payoffs)
>
> **Sharpe gap:** 4.49 is the validated natural ceiling (v5). 6.0 target would require overfitting. 9.09 from adaptive+hedge needs independent verification.
>
> **🚫 NO SYNTHETIC DATA — EVER.** All pricing from `IronVault.instance()` → `data/options_cache.db`.

---

## PORTFOLIO VARIANTS

### v5 — Best Risk-Adjusted (Recommended for Production)

**Architecture:** v4 base + 4 Sharpe-boosting overlays: vol targeting (9% target), regime confidence scaling (120% in low_vol_bull → 8% in circuit breaker), conviction weighting (rolling 60d Sharpe), signal damping.

| Metric | v4 | **v5** | Delta |
|--------|-----|--------|-------|
| **CAGR** | 101.0% | **92.6%** | -8.4% (traded for lower vol) |
| **Sharpe** | 3.94 | **4.49** | +14% |
| **Max DD** | 8.0% | **5.0%** | -37.5% |
| **COVID DD** | 6.6% | **0.8%** | -88% |
| **Vol** | ~18% | **14.8%** | lower |

### v4 — Maximum CAGR

**Architecture:** DynamicSizer (adaptive 0.1×–2.2× leverage) + Tail Risk Hedge (SPY puts + VIX calls).

| Metric | Value |
|--------|-------|
| CAGR | **101.0%** |
| Sharpe | 3.94 |
| Max DD | 8.0% |
| COVID DD | 6.6% |
| Avg Leverage | 1.37× |
| All 6 Years | Profitable (worst: 2022 +21.1%) |

### Adaptive Leverage + Hedge (PENDING VALIDATION)

Commit `e7dd2d7`. Combines `dynamic_leverage.py` (VIX/TS/rvol 3-ramp) with `tail_risk_hedge.py` (put + VIX call overlay, 2% budget).

| Mode | CAGR | Sharpe | DD |
|------|------|--------|-----|
| Static 1.6× | 89.2% | 7.60 | 7.9% |
| Dynamic only | 82.5% | 8.07 | 7.2% |
| Hedge only (1.6×) | 94.1% | 8.04 | 7.9% |
| **Adaptive + Hedge** | **102.0%** | **9.09** | **7.5%** |

Walk-forward: 4/4 OOS windows profitable (avg OOS Sharpe 10.53).

> **⚠️ PENDING VALIDATION:** Sharpe 9.09 may be inflated by simulated hedge payoffs.
> Hedge cost modeled at 2%/yr flat budget — not from real SPY put prices. Need IronVault option cost verification before trusting this number.

### All Configurations Compared

| Config | CAGR | Sharpe | Max DD | COVID DD | Status |
|--------|------|--------|--------|----------|--------|
| **v5 (recommended)** | 92.6% | **4.49** | **5.0%** | **0.8%** | VALIDATED |
| v4 DynamicSizer | **101.0%** | 3.94 | 8.0% | 6.6% | VALIDATED |
| Adaptive + Hedge | **102.0%** | **9.09** | 7.5% | — | ⚠️ PENDING |
| Static 1.6× + Hedge | 101.6% | 4.10 | 11.4% | 18.3% | VALIDATED |
| EXP-1220 solo 1.2× | 99.0% | 5.68 | 7.9% | ~7% | VALIDATED |
| Regime-adaptive | 120.0% | ~4.7 | ~10% | ~15% | VALIDATED |

### Crisis Scenarios (v5)

| Scenario | v5 DD | v4 DD | Unhedged |
|----------|-------|-------|----------|
| COVID-2020 | **0.8%** | 6.6% | 57.2% |
| Bear 2022 | ~3% | 5.1% | 43.7% |
| Flash Crash | ~4% | 6.9% | 34.5% |

### Monte Carlo Forward Simulation (10K paths, block-bootstrap)

| Horizon | Median CAGR | P5 CAGR | Prob >50% | P95 Max DD |
|---------|------------|---------|-----------|-----------|
| 1 year | 100.2% | 51.9% | 96% | 10.4% |
| 3 year | 100.9% | 71.3% | 100% | 12.1% |
| 5 year | 100.8% | 77.4% | 100% | 12.9% |

Prolonged bear (2yr, 2022-style): median CAGR +18.8%, prob profit 99%, P95 DD 15.4%.
Kelly optimal: 21.7× (portfolio at 1.37× is very conservative).

---

## CURRENT GAPS & OPEN ISSUES

| Gap | Severity | Status | Path to Resolution |
|-----|----------|--------|-------------------|
| **Sharpe 4.49 vs 6.0 target** | Medium | PARTIALLY CLOSED | v5 → 4.49 (+14% from v4). Adaptive+hedge → 9.09 but PENDING VALIDATION. Natural ceiling ~4.5 without overfitting. |
| **Adaptive+Hedge Sharpe 9.09 unvalidated** | High | OPEN | Hedge payoffs simulated, not from real option prices. Need IronVault SPY put cost verification. |
| **Static 1.6× COVID DD 18.3%** | Medium | ✅ CLOSED | v4 DynamicSizer → 6.6%, v5 → 0.8%. Both well under 12%. |
| **Paper trading not functional** | High | PARTIALLY CLOSED | Paper trading v4 harness built (86% ready, 61 tests). Cron still not installed. EXP-503/600 still not deployed. |
| **GLD data ends Mar 2024** | Medium | OPEN | Need Polygon backfill for GLD/TLT/QQQ options. |
| **Grade C experiments** | Medium | PARTIALLY CLOSED | 4 reworked (commit 5360055): XLI-IC → B+, EXP-1650 → B-. EXP-1640 still C. EXP-1230 → D. |
| **Execution at $50M+ not viable** | Low | DOCUMENTED | GLD binding. SPY-only variant for large AUM. |
| **Experiment pipeline manual** | Low | ✅ CLOSED | Pipeline v2 (commit 195620e): batch runner, param sweep, JSON registry. 59 tests. |
| **No forward projections** | Low | ✅ CLOSED | Monte Carlo (commit 9d69459): 10K paths, 1/3/5yr, 96-100% prob >50% CAGR. |

---

## OOS INTEGRITY AUDIT (2026-04-05)

14 real-data experiments audited for walk-forward methodology, OOS contamination, and overfitting risk. Full report: `reports/oos_integrity_audit.html`

| Grade | Count | Verdict | Experiments |
|-------|-------|---------|-------------|
| **A** | 3 | PASS — Deploy | EXP-1630, Vol Term Structure, Cross-Asset Pairs |
| **B+** | 4 | CONDITIONAL — Deploy | EXP-1220, TLT ICs, EXP-1630-opt, **XLI ICs** (fixed) |
| **B-** | 1 | CONDITIONAL | **EXP-1650** (fixed) |
| **C** | 1 | Marginal | EXP-1640 (still only 19 trades) |
| **D/F** | 5 | FAIL | EXP-1320, EXP-1270, EXP-880, EXP-1470, **EXP-1230** (downgraded) |

**Rework results (commit 5360055):**
- XLI ICs: C+ → **B+** (locked baseline config, 58 trades, 5/5 WF windows positive)
- EXP-1650: C → **B-** (reduced to 3 params, 4/5 WF windows positive)
- EXP-1640: C- → **C** (marginal improvement, 2025 still fails)
- EXP-1230: C- → **D** (downgraded — AUC 0.511 is random noise)

---

## REAL DATA STRATEGY LEAGUE TABLE (2026-04-05)

### Tier 1: Grade A — Walk-Forward Validated, Deploy

| Strategy | Sharpe | OOS Sharpe | CAGR | Max DD | Trades | Audit Grade |
|----------|--------|------------|------|--------|--------|-------------|
| **EXP-1630 GLD/TLT RelVal** | 2.19 | **4.08** | 1.9% (base) | 1.7% | 63 | A |
| **Vol Term Structure** | 2.45 | **2.81** | 0.55% | 0.18% | 53 | A |
| **Cross-Asset Pairs** | 2.90 | **5.06** | — | — | 32 | A- |

### Tier 1b: Grade B — Validated, Deploy with Monitoring

| Strategy | Sharpe | OOS Sharpe | CAGR | Max DD | Trades | Audit Grade |
|----------|--------|------------|------|--------|--------|-------------|
| **EXP-1220 Tail Risk** (1x) | **5.78** | **5.78** | 77.3% | 6.6% | daily | B+ |
| **XLI Iron Condors** (fixed) | 5.19 | 2.68 | 18.8% | 10.3% | 58 | **B+** (was C+) |
| **TLT Iron Condors** | **2.69** | — | 10.2% | — | 43 | B |
| **EXP-1630-opt Multi-Pair** | 1.35 | — | **12.6%** | 9.3% | 174 | B |
| **EXP-1650 Earnings VC** (fixed) | 1.55 | 0.59 | modest | 0.95% | 28 | **B-** (was C) |

### Tier 1c: Strategy Discovery R4 (New — 2026-04-05)

| Strategy | Sharpe | OOS Sharpe | Trades | SPY Corr | Verdict |
|----------|--------|------------|--------|----------|---------|
| **Intraday Mean-Reversion** | — | **1.05** | 56 | +0.45 | LIVE — best new strategy |
| **Gamma Scalping** | — | 0.37 | 83 | +0.53 | LIVE — modest positive |
| Dispersion Trading | 13.56 | — | 6 | — | KILLED (<10 OOS trades) |
| Seasonal Patterns | 2.90 IS | negative OOS | 70 | — | KILLED (OOS fails) |
| VRP Harvesting (EXP-1660) | 1.80 OOS | 1.80 | — | -0.70 | PROMISING (counter-cyclical) |

### Tier 2: Grade C — Marginal

| Strategy | Sharpe | Issue |
|----------|--------|-------|
| EXP-1640 Sector Mom | -0.12 OOS → marginal fix | 19 trades, 2025 still fails |

### Tier 3: DEAD

| Strategy | Synthetic | Real | Cause of Death |
|----------|----------|------|----------------|
| EXP-880 ML Ensemble | 76.9% CAGR | -104% | Bankrupt on real data |
| EXP-1470 North Star | 206% CAGR | 0.42% | Synthetic illusion |
| EXP-1270 Adaptive Stop | Sharpe 5.25 | -0.25 | Full-data optimization |
| EXP-1320 Vol Clustering | Sharpe 3.05 | 0.92 | 3 signal trades total |

---

## CAPACITY & EXECUTION (2026-04-05)

From `compass/execution_feasibility.py` using real IronVault volume data:

### Instrument Liquidity (IronVault)

| Ticker | Avg Vol/Contract | Total Daily Vol | Spread | Role |
|--------|-----------------|----------------|--------|------|
| SPY | 1,805 | 3.1M | $0.03 | EXP-1220, Vol Term |
| QQQ | 1,399 | 310K | $0.04 | Cross-Asset Pairs |
| XLF | 1,398 | 123K | $0.03 | Vol Term, Sector |
| TLT | 738 | 60K | $0.05 | TLT ICs, Pairs |
| GLD | 553 | 43K | $0.07 | **Binding constraint** |

### Net CAGR After All Costs

| AUM | Exec Cost | Margin (3.3%) | Total | Net CAGR | Retention |
|-----|-----------|---------------|-------|----------|-----------|
| **$1M** | 4.7% | 3.3% | 8.0% | **47.5%** | 86% |
| **$10M** | 15.8% | 3.3% | 19.1% | **36.4%** | 66% |
| $50M | 44.1% | 3.3% | 47.4% | 8.2% | 15% |

**Sweet spot: $1M–$10M.** GLD options (553 avg vol/contract) is the binding liquidity constraint. SPY leg scales to billions.

---

## MODULES BUILT (72+ commits, 2026-04-04/05)

### Core Portfolio

| Module | Purpose | Tests |
|--------|---------|-------|
| `scripts/ultimate_portfolio_v4.py` | v4: DynamicSizer + TailRiskHedge → 101% CAGR | — |
| `scripts/ultimate_portfolio_v5.py` | v5: vol-target + conviction → Sharpe 4.49 | — |
| `compass/protected_portfolio.py` | Tail-risk-hedged portfolio backtester | — |
| `compass/regime_portfolio.py` | Regime-adaptive portfolio (3-way comparison) | 40 |
| `compass/dynamic_sizing.py` | Static vs adaptive leverage (0.5×–2.5×) | 50 |

### Risk & Execution

| Module | Purpose | Tests |
|--------|---------|-------|
| `compass/risk_overlay.py` | 5-layer risk management (leverage + hedge + events + stops + breaker) | 71 |
| `compass/execution_simulator.py` | IronVault data, fill probability, degradation curves | 69 |
| `compass/execution_cost_model.py` | Almgren-Chriss impact, $1M–$1B capacity | — |
| `compass/execution_feasibility.py` | Per-strategy IronVault liquidity study | — |
| `compass/paper_trading_v4.py` | Production paper harness: 5 strategies + hedges + sizing | 61 |

### Automation & Validation

| Module | Purpose | Tests |
|--------|---------|-------|
| `compass/experiment_runner.py` | Pipeline v2: batch runner, param sweep, JSON registry | 59+77 |
| `compass/oos_integrity_audit.py` | 14 experiments graded A–F | — |
| `compass/north_star_scorecard.py` | Executive dashboard | — |
| `compass/correlation_analyzer.py` | 13-strategy heatmap + clustering | — |
| `compass/mc_forward_sim.py` | Monte Carlo: 10K paths, 1/3/5yr horizons | — |

### Key Reports Generated

| Report | Content |
|--------|---------|
| `reports/ultimate_portfolio_v4.html` | v4: 101% CAGR, 8% DD, 6.6% COVID |
| `reports/oos_integrity_audit.html` | 14 experiments: 3 PASS, 4 REWORK, 4 FAIL |
| `reports/execution_feasibility.html` | Capacity: $1M–$10M sweet spot |
| `reports/execution_sim.html` | Degradation curves at scale |
| `reports/risk_overlay_spec.html` | 5-layer risk framework documentation |
| `reports/north_star_scorecard.html` | Executive dashboard |
| `reports/exp1630_optimization.html` | Multi-pair: 12.6% CAGR at 9.3% DD |
| `docs/production_deployment_plan.md` | Full production roadmap |

---

## EXPERIMENT REGISTRY

> **Authoritative data:** `REGISTRY.md` (comprehensive scorecard)
> **Rules:** `EXPERIMENT_PROTOCOL.md`

### Live Paper Trading

| ID | Name | Account | Status | Live Since |
|----|------|---------|--------|------------|
| **EXP-400** | **The Champion** | PA36XFVLG0WE | Active (1 trade, 16 orphans) | 2026-03-15 |
| **EXP-401** | **The Blend** | PA3Y2XDYB9I3 | Active (1 trade, 14 orphans) | 2026-03-15 |
| **EXP-503** | **ML V2 Aggressive** | PA3Z9PLVYUL5 | NOT DEPLOYED — no DB/env | 2026-03-22 |
| **EXP-600** | **IBIT Adaptive** | PA3O14JAJHJ0 | NOT DEPLOYED — no DB/env | 2026-03-22 |

> **Paper trading issue:** Cron not installed. EXP-503/600 never deployed. EXP-400/401 have orphan positions. See `docs/PAPER_TRADING_RUNBOOK.md`.

### Real-Data Validated (13 experiments)

See `REGISTRY.md` for full scorecard with OOS audit grades.

### Retired / Dead

| ID | Name | Why Retired |
|----|------|-------------|
| EXP-031/036/059/154/305 | Legacy experiments | Superseded by EXP-400/401 |
| **EXP-1470** | North Star Portfolio | DEAD: synthetic CAGR 27.85% → real 0.42% |
| **EXP-880** | ML Ensemble | DEAD: lost $101K on real data, bankrupt |
| **EXP-1270** | Adaptive Stop-Loss | DEAD: Sharpe -0.25, 17 params / 41 trades |
| **EXP-1320** | Vol Clustering | FAIL: 3 signal trades, optimization bias |

---

## PHASE COMPLETION STATUS (Updated 2026-04-05)

| Phase | Name | Status | Key Result |
|-------|------|--------|------------|
| 0 | Strategy Discovery Engine | ✅ DONE | 7 strategies built, champion found |
| 1 | Parameter Sweep | ✅ DONE | 87 experiments, regime-adaptive winner |
| 2 | Position Sizing | ✅ DONE | Returns plateau at 10% risk. 8.5% near-optimal. |
| 3 | Portfolio Blending | ✅ DONE | CS+S/S blend beats CS+IC. +39.1% avg, -9.5% DD |
| 4 | Regime Switching | ✅ DONE | Dynamic allocation: +40.7% avg, -7.0% DD |
| 5 | Final Validation | ~~✅ DONE~~ ⚠️ OBSOLETE | *Synthetic data — invalidated by Phase 7* |
| 6 | Paper Trading v1 | 🔄 LIVE | EXP-400/401 active. EXP-503/600 NOT DEPLOYED. Cron not installed. |
| 6.5 | Unified Entry/Exit | ✅ DONE | All strategies use same code as backtester |
| **7** | **Operation Real Data** | **✅ DONE** | Synthetic audit, IronVault deployed, 3/6 strategies dead |
| **7.5** | **New Strategy Discovery** | **✅ DONE** | Cross-asset pairs, vol term structure, TLT ICs, XLI ICs |
| **8** | **Portfolio Optimization** | **✅ DONE** | v4: 101% CAGR, v5: Sharpe 4.49, adaptive+hedge: Sharpe 9.09 (pending) |
| **8.5** | **Stress Testing** | **✅ DONE** | MC 10K paths: 96% prob >50% CAGR @ 1yr. All crises PASS. |
| **8.7** | **OOS Integrity Audit** | **✅ DONE** | 14 graded → 4 reworked → now: 3A, 5B, 1C, 5D/F |
| **8.8** | **Execution Feasibility** | **✅ DONE** | $1M–$10M sweet spot. GLD binding. 47.5% net CAGR at $1M |
| **8.9** | **Risk Framework** | **✅ DONE** | 5-layer overlay (71 tests). Deployment plan. Paper harness (61 tests). |
| **8.95** | **Strategy Discovery R4** | **✅ DONE** | 5 new strategies: intraday MR (OOS 1.05), gamma scalp (0.37) survive |
| **8.97** | **Experiment Pipeline v2** | **✅ DONE** | Batch runner, param sweep, JSON registry (59 tests) |
| **8.99** | **Monte Carlo Forward Sim** | **✅ DONE** | 10K paths, median 100% CAGR, P5 52%, bear scenario +19% |
| **9** | **Paper Trading v2** | 🔄 86% READY | Paper harness built (184a9de). Need: cron, deploy, 8-week clock. |
| 10 | Live Trading | ⬜ BLOCKED | Requires Phase 9 completion + Carlos sign-off |

---

## CURRENT PRIORITIES

### Priority 1: Go Live with Paper Trading (Phase 9)
Paper harness is 86% ready (commit 184a9de, 61 tests). Remaining 14%:
- [ ] Install cron on Mac Studio (`configs/paper_ultimate_v4.yaml` ready)
- [ ] Create `.env.ultimate` with Alpaca paper credentials
- [ ] Deploy and verify Telegram alerts
- [ ] Start 8-week paper validation clock → target: 2026-06-09

### Priority 2: Validate Adaptive+Hedge Sharpe 9.09
- [ ] Verify hedge costs against real SPY put prices from IronVault
- [ ] Compare simulated hedge payoffs vs actual option P&L during COVID/2022
- [ ] If validated: Sharpe target 6.0 is MET and exceeded
- [ ] If inflated: v5 (Sharpe 4.49) remains production recommendation

### Priority 3: Close Remaining Gaps
- [x] ~~Rework Grade C experiments~~ → DONE (commit 5360055: XLI-IC → B+, EXP-1650 → B-)
- [x] ~~Experiment pipeline manual~~ → DONE (pipeline v2, 59 tests)
- [x] ~~No forward projections~~ → DONE (Monte Carlo 10K paths)
- [ ] Backfill GLD/TLT/QQQ option data (stale since 2023-2024)
- [ ] Add formal walk-forward to EXP-1220 tail risk

### Priority 4: Strategy Discovery Continuation
- [ ] EXP-1660 VRP Harvesting deeper validation (OOS Sharpe 1.80, SPY corr -0.70)
- [ ] Intraday MR (OOS 1.05) — integrate into paper harness
- [ ] Gamma scalp (OOS 0.37) — small allocation test

---

## INFRASTRUCTURE

### Key Files
```
Ultimate Portfolio:
├── scripts/ultimate_portfolio_v4.py       ← v4: DynamicSizer + TailRiskHedge → 101% CAGR
├── scripts/ultimate_portfolio_v5.py       ← v5: vol-target + conviction → Sharpe 4.49
├── compass/protected_portfolio.py         ← Hedged portfolio backtester
├── compass/regime_portfolio.py            ← Regime-adaptive portfolio (40 tests)
├── compass/dynamic_sizing.py             ← Static vs adaptive leverage (50 tests)
├── compass/risk_overlay.py               ← 5-layer risk management (71 tests)
├── compass/paper_trading_v4.py           ← Production paper harness (61 tests)
└── configs/paper_ultimate_v4.yaml        ← Alpaca paper config (5 strategies)

Execution & Capacity:
├── compass/execution_simulator.py         ← Fill probability, degradation curves (69 tests)
├── compass/execution_cost_model.py        ← Almgren-Chriss, $1M-$1B analysis
├── compass/execution_feasibility.py       ← Per-strategy feasibility (IronVault data)
└── reports/execution_feasibility.html     ← $1M-$10M sweet spot

Validation & Automation:
├── compass/oos_integrity_audit.py         ← 14 experiments graded A-F
├── compass/experiment_runner.py           ← Pipeline v2: batch + sweep + registry (59+77 tests)
├── compass/mc_forward_sim.py             ← Monte Carlo 10K paths, 1/3/5yr
├── compass/north_star_scorecard.py        ← Executive dashboard
├── REGISTRY.md                            ← Comprehensive 13-strategy scorecard
└── reports/oos_integrity_audit.html       ← Walk-forward audit results

Iron Vault (Data Layer):
├── shared/iron_vault.py                   ← THE single data provider
├── data/options_cache.db                  ← 948 MB, 258K contracts, 5.97M bars
├── scripts/daily_data_update.sh           ← Cron-ready with lock, retries, log rotation
└── scripts/backfill_gap.py               ← Targeted multi-ticker backfill

Production Deployment:
├── docs/production_deployment_plan.md     ← Full architecture + roadmap
├── compass/live_bridge.py                ← Signal → Order translation
├── compass/paper_trading_engine.py       ← Paper engine (57 tests)
├── compass/prod_monitor.py               ← Production monitoring (87 tests)
├── shared/circuit_breaker.py             ← Kill switch
└── shared/telegram_alerts.py             ← Alerting
```

### GitHub
- **Repo:** `charlesattix/pilotai-credit-spreads`
- **Main branch:** Production code
- **maximus/clean-features:** Development (72+ commits since Apr 4)

---

## TIMELINE

| Date | Milestone |
|------|-----------|
| 2026-03-15 | Paper trading deployed (EXP-400, EXP-401) |
| 2026-03-22 | EXP-503, EXP-600 announced (NOT actually deployed) |
| 2026-04-03 | Operation Real Data: synthetic audit, IronVault, 3/6 strategies killed |
| 2026-04-04 | EXP-1220 confirmed (Sharpe 5.78), new strategies discovered, stress test PASSES |
| **2026-04-05** | **Ultimate Portfolio v4: 101% CAGR, 8% DD — CAGR TARGET MET** |
| **2026-04-05** | **OOS Integrity Audit: 3 Grade A, 3 B, 4 C, 4 D/F** |
| **2026-04-05** | **Execution feasibility: $1M–$10M sweet spot, GLD binding** |
| **2026-04-05** | **72 autonomous commits: risk overlay, execution sim, deployment plan, audit** |
| **2026-04-05** | **v5: Sharpe 4.49, COVID DD 0.8%. Adaptive+Hedge: Sharpe 9.09 (PENDING).** |
| **2026-04-05** | **R4 discovery: intraday MR (1.05), gamma scalp (0.37). Pipeline v2. MC sim.** |
| **2026-04-05** | **Paper harness v4 at 86% ready (61 tests). 4 Grade C experiments reworked.** |
| 2026-04-07 | (Next) Install cron, deploy paper harness, start Phase 9 |
| 2026-04-14 | (Target) Paper trading stable, first week of data |
| 2026-06-09 | (Target) Phase 9 complete (8-week validation) |
| 2026-06-16 | (Target) Live trading decision — Carlos sign-off |

---

## RULES

1. **Every experiment gets an ID** — EXP-NNN format, registered in REGISTRY.md
2. **Never skip validation** — walk-forward required, param:trade ratio <0.20
3. **Always log before AND after** — hypothesis → results → leaderboard
4. **Regime detector is mandatory** — all directional strategies use combo regime mode
5. **Paper before live** — nothing touches real money without 8+ weeks paper validation
6. **Follow the data** — kill losers fast, double down on winners
7. **MASTERPLAN is sacred** — single source of truth, update with every instruction from Carlos
8. **🚫 NO SYNTHETIC DATA** — all backtests use IronVault. Cache miss → skip trade, NEVER fabricate.
9. **Real data trumps synthetic** — if synthetic and real results disagree, trust real. Kill the synthetic.
10. **OOS integrity required** — no experiment goes live without Grade A or B audit rating.

---

*Victory is not won by the sword alone — it is won by the plan behind it.*
