# MASTERPLAN.md — Operation Crack The Code

## Mission
Build a validated, multi-strategy options trading system on SPY. Data-driven approach: kill losing strategies, optimize winners, follow what the data says. Paper trade the winners, then go live.

## North Star (Updated 2026-04-05 — Ultimate Portfolio v4)

| Target | Original | Real Data Actual | Status |
|--------|----------|-----------------|--------|
| **Avg annual return** | 55% | **101.0% CAGR** (v4, adaptive leverage) | **EXCEEDED** (1.84x target) |
| **Sharpe ratio** | 6.0 | **3.94** (v4) / **5.78** (EXP-1220 1x) | GAP — 3.94 at portfolio level |
| **Max drawdown** | ≤30% | **8.0%** (v4) / **6.6% COVID** | **EXCEEDED** (3.8x better) |
| **Multi-strategy** | Yes | **5 validated** (3 Grade A + 2 Grade B) | **MET** |
| **All 6 years profitable** | Yes | **6/6 years** (2020-2025) | **MET** |
| **100% CAGR path** | 3.5x leverage | **Adaptive 1.37x avg → 101% CAGR** | **MET** (lower leverage!) |
| **Survive all crises** | <12% DD | **COVID 6.6%, Bear 5.1%, Flash 6.9%** | **MET** (all 5 scenarios) |

> **CAGR target: MET.** DD target: MET. Crisis survival: MET. 6/6 years: MET.
> Sharpe gap: 3.94 vs 6.0 target (34% short). Attribution: adaptive leverage trades off Sharpe for higher absolute return.
>
> **🚫 NO SYNTHETIC DATA — EVER.** All pricing from `IronVault.instance()` → `data/options_cache.db`. See `docs/DATA_ARCHITECTURE.md`.

---

## ULTIMATE PORTFOLIO v4 — The Winner

**Architecture:** DynamicSizer (adaptive 0.1×–2.2× leverage based on VIX, term structure, realized vol, trend, drawdown) + Tail Risk Hedge (SPY puts + VIX calls, amplified in crisis).

| Metric | Value |
|--------|-------|
| **CAGR** | **101.0%** |
| **Sharpe** | **3.94** |
| **Max DD** | **8.0%** |
| **Calmar** | 12.6 |
| **COVID DD** | **6.6%** (from 57.2% unhedged) |
| **Avg Leverage** | 1.37× (range: 0.1×–2.2×) |
| **Hedge Cost** | 0.7%/yr (net negative — payoffs exceed cost) |
| **All 6 Years** | Profitable (worst: 2022 +21.1%) |

**Year-by-Year:**

| Year | Return | Notes |
|------|--------|-------|
| 2020 | +57.3% | COVID crash protected (6.6% DD) |
| 2021 | +141.3% | Bull market, full leverage |
| 2022 | +21.1% | Bear market — still profitable |
| 2023 | +148.2% | Recovery + low vol |
| 2024 | +144.6% | Continued strength |
| 2025 | +136.8% | YTD through available data |

**Crisis Scenarios (ALL PASS <12% threshold):**

| Scenario | v4 Max DD | Unhedged DD | Protection |
|----------|----------|-------------|------------|
| COVID-2020 | **6.6%** | 57.2% | 50.6pp saved |
| Bear 2022 | **5.1%** | 43.7% | 38.6pp saved |
| Flash Crash | **6.9%** | 34.5% | 27.6pp saved |
| China 2015 | **5.8%** | 28.4% | 22.6pp saved |
| Volmageddon | **4.5%** | 25.1% | 20.6pp saved |

---

## OOS INTEGRITY AUDIT (2026-04-05)

14 real-data experiments audited for walk-forward methodology, OOS contamination, and overfitting risk. Full report: `reports/oos_integrity_audit.html`

| Grade | Count | Verdict | Experiments |
|-------|-------|---------|-------------|
| **A** | 3 | PASS — Deploy | EXP-1630, Vol Term Structure, Cross-Asset Pairs |
| **B** | 3 | CONDITIONAL — Deploy with caveats | EXP-1220, TLT ICs, EXP-1630-opt |
| **C** | 4 | REWORK — Re-validate | XLI ICs, EXP-1650, EXP-1640, EXP-1230 |
| **D/F** | 4 | FAIL — Dead | EXP-1320, EXP-1270, EXP-880, EXP-1470 |

**Key findings:**
- Only **3/14** have proper documented walk-forward validation
- **6/14** have NO IS/OOS separation — all params tuned on full data
- XLI IC "OOS Sharpe 8.58" is **selection bias** (34 configs tested)
- Required standard: ≥30 OOS trades, param:trade ratio <0.20, IronVault data

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
| **TLT Iron Condors** | **2.69** | — | 10.2% | — | 43 | B |
| **EXP-1630-opt Multi-Pair** | 1.35 | — | **12.6%** | 9.3% | 174 | B |

### Tier 2: Grade C — Needs Rework

| Strategy | Sharpe | Issue |
|----------|--------|-------|
| XLI Iron Condors | 8.58 OOS | Selection bias (34 configs) |
| EXP-1650 Earnings VC | 0.59 OOS | No WF docs, degrading |
| EXP-1640 Sector Mom | -0.12 OOS | 6 OOS trades, sign flip |
| EXP-1230 Microstructure | 0.89 | AUC 0.511 (random) |

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

## MODULES BUILT (Overnight Autonomous Work: 72 commits)

### New Modules (2026-04-05)

| Module | Purpose | Tests |
|--------|---------|-------|
| `compass/risk_overlay.py` | Unified 5-layer risk management (leverage + hedge + events + stops + DD breaker) | 71 |
| `compass/protected_portfolio.py` | Tail-risk-hedged portfolio backtester | — |
| `compass/regime_portfolio.py` | Regime-adaptive portfolio with 3-way comparison | 40 |
| `compass/dynamic_sizing.py` | Static vs adaptive leverage (0.5×–2.5×) | 50 |
| `compass/execution_simulator.py` | Execution sim: IronVault data, fill probability, degradation curves | 69 |
| `compass/execution_cost_model.py` | Almgren-Chriss impact, $1M–$1B capacity analysis | — |
| `compass/execution_feasibility.py` | Per-strategy feasibility study with IronVault liquidity | — |
| `compass/experiment_runner.py` | Automated pipeline: spec → run → score → register → report | 77 |
| `compass/exp1630_optimizer.py` | EXP-1630 deep optimization: 6 pairs, regime filters, walk-forward | — |
| `compass/oos_integrity_audit.py` | OOS integrity audit: 14 experiments graded A–F | — |
| `scripts/ultimate_portfolio_v4.py` | Ultimate Portfolio v4: DynamicSizer + TailRiskHedge | — |
| `compass/north_star_scorecard.py` | Executive dashboard for Carlos | — |
| `compass/correlation_analyzer.py` | 13-strategy correlation heatmap and clustering | — |
| `compass/rebalancing_sim.py` | Monthly optimal rebalancing simulator | — |

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
| **8** | **Portfolio Optimization** | **✅ DONE** | Ultimate Portfolio v4: 101% CAGR, 8% DD, 6.6% COVID |
| **8.5** | **Stress Testing** | **✅ DONE** | All 5 crisis scenarios PASS. 100% MC survival. P5 DD 9.6% |
| **8.7** | **OOS Integrity Audit** | **✅ DONE** | 14 experiments graded: 3 A, 3 B, 4 C, 4 D/F |
| **8.8** | **Execution Feasibility** | **✅ DONE** | $1M–$10M sweet spot. GLD binding. 47.5% net CAGR at $1M |
| **8.9** | **Risk Framework** | **✅ DONE** | 5-layer risk overlay (71 tests). Production deployment plan. |
| **9** | **Paper Trading v2** | ⬜ NEXT | Wire v4 portfolio into paper trader. 8-week validation. |
| 10 | Live Trading | ⬜ BLOCKED | Requires Phase 9 completion + Carlos sign-off |

---

## CURRENT PRIORITIES

### Priority 1: Fix Paper Trading Infrastructure
- [ ] Install cron on Mac Studio for daily scanner + data updates
- [ ] Deploy EXP-503 and EXP-600 (create DBs and env files)
- [ ] Investigate and fix orphan positions in EXP-400/401
- [ ] Verify Telegram alerts working

### Priority 2: Paper Trade the Real-Data Portfolio (Phase 9)
- [ ] Wire Ultimate Portfolio v4 signals into `compass/live_bridge.py`
- [ ] Configure 5-layer risk overlay for paper mode
- [ ] Start 8-week paper validation clock
- [ ] Daily P&L reconciliation against broker state

### Priority 3: Close Sharpe Gap (3.94 → 6.0)
- [ ] Sharpe attribution: 3.94 base → 5.07 via regime filtering (compass/sharpe_optimizer.py)
- [ ] Investigate: is 6.0 achievable without sacrificing CAGR?
- [ ] SPY-only high-capacity variant shows Sharpe 6.55 but lower CAGR

### Priority 4: Address Audit Findings
- [ ] Re-validate XLI ICs with single config (not 34-way selection)
- [ ] Add formal walk-forward to EXP-1220 tail risk
- [ ] Extend EXP-1640 OOS to 3+ years
- [ ] Document EXP-1650 walk-forward methodology

---

## INFRASTRUCTURE

### Key Files
```
Ultimate Portfolio v4:
├── scripts/ultimate_portfolio_v4.py       ← DynamicSizer + TailRiskHedge
├── compass/protected_portfolio.py         ← Hedged portfolio backtester
├── compass/regime_portfolio.py            ← Regime-adaptive portfolio
├── compass/dynamic_sizing.py             ← Static vs adaptive leverage
├── compass/risk_overlay.py               ← 5-layer risk management (71 tests)
└── reports/ultimate_portfolio_v4.html     ← 101% CAGR, 8% DD

Execution & Capacity:
├── compass/execution_simulator.py         ← Fill probability, degradation curves (69 tests)
├── compass/execution_cost_model.py        ← Almgren-Chriss, $1M-$1B analysis
├── compass/execution_feasibility.py       ← Per-strategy feasibility (IronVault data)
└── reports/execution_feasibility.html     ← $1M-$10M sweet spot

Validation & Audit:
├── compass/oos_integrity_audit.py         ← 14 experiments graded A-F
├── compass/experiment_runner.py           ← Automated spec→run→score pipeline (77 tests)
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
| 2026-04-07 | (Next) Fix paper trading infra, install cron |
| 2026-04-14 | (Target) Start Phase 9: paper trade v4 portfolio |
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
