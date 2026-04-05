# MASTERPLAN.md — Operation Crack The Code

## Mission
Build a validated, multi-strategy options trading system on SPY. Data-driven approach: kill losing strategies, optimize winners, follow what the data says. Paper trade the winners, then go live.

## North Star — CORRECTED Numbers (2026-04-05)

> All numbers below are corrected per validation audit (1f0888a) and hedge cost reality check (51e11e6). Previous inflated claims have been struck through.

| Target | Goal | Corrected Current | Confidence | Notes |
|--------|------|-------------------|------------|-------|
| **CAGR** | 100% | **77.3% at 1x** / ~73% after real hedge costs | VALIDATED | 100% requires 1.2x leverage (unproven on live account) |
| **Sharpe** | 6.0 | **3.76** (portfolio) / **5.78** (EXP-1220 standalone) | VALIDATED | ~~9.09~~, ~~4.49~~ were inflated by formula bug |
| **Max DD** | ≤12% | **6.6%** at 1x | VALIDATED | ~9% at 1.2x with real costs |
| **COVID DD** | <12% | **6.6%** (validated) | VALIDATED | ~~0.8%~~ relied on simulated hedge payoff |
| **All 6 years** | Yes | **6/6 profitable** | VALIDATED | Worst: 2022 at +14.8% (protected) |
| **Multi-strategy** | Yes | **1 validated** (EXP-1220), 3 Grade A pending data refresh | PARTIAL | GLD/QQQ/TLT data gaps block multi-asset |

### What Was Wrong

| Claim | Reported | Corrected | Root Cause |
|-------|----------|-----------|------------|
| Adaptive+Hedge Sharpe | 9.09 | **3.76** | Geometric CAGR in Sharpe formula (inflates 2.4× at high CAGR) + synthetic data |
| v4 Sharpe | 3.94 | **3.69** | Same formula bug (1.07× inflation at 101% CAGR) |
| v5 Sharpe | 4.49 | **~3.5** (estimated) | Same formula bug applies |
| Hedge cost | 2%/yr | **4.36%/yr** | Real IronVault SPY put prices: 2020=5.3%, 2022=5.4%, 2025=7.3% |
| Hedge "net negative" | Payoffs exceed cost | **Net cost ~2.4%/yr** | Payoff magnitudes were simulated (12× convex multiplier), not from real options |
| VIX call hedge | 40% of budget | **UNVALIDATED** | VIX/UVXY/VXX not in IronVault database |
| COVID DD (v5) | 0.8% | **~6.6%** (validated) | 0.8% required simulated hedge payoff during COVID |

> **🚫 NO SYNTHETIC DATA.** All pricing from `IronVault.instance()` → `data/options_cache.db`.

---

## WHAT WE ACTUALLY HAVE (Corrected Performance)

### Validated — HIGH Confidence

| Scenario | CAGR | Sharpe | Max DD | Data Source | Confidence |
|----------|------|--------|--------|-------------|------------|
| **EXP-1220 at 1x, no hedge** | **77.3%** | **5.78** | 6.6% | Yahoo SPY/VIX 2019-2025 | HIGH |
| **EXP-1220 at 1x, real hedge (4.4%/yr)** | **~73%** | **~5.4** | ~5-6% | IronVault SPY puts | HIGH |
| **EXP-1220 at 1x, collar hedge (~1.3%/yr net)** | **~76%** | **~5.6** | ~6% | IronVault + `compass/smart_hedge.py` | MEDIUM-HIGH |
| **Vol Term Structure (SPY)** | 0.55% | 2.81 OOS | 0.18% | IronVault SPY options | HIGH (Grade A) |

### Plausible — MEDIUM Confidence (requires leverage or stale data)

| Scenario | CAGR | Sharpe | Max DD | Issue |
|----------|------|--------|--------|-------|
| EXP-1220 at 1.2x + collar | ~90% | ~3.7 | ~9% | Leverage not tested on real account |
| EXP-1630 GLD/TLT relval | 1.9% base | 4.08 OOS | 1.7% | GLD data ends Mar 2024 (14mo gap) |
| Cross-Asset Pairs | — | 5.06 OOS | — | QQQ data ends Apr 2023 (35mo gap) |
| TLT Iron Condors | 10.2% | 2.69 | — | TLT data ends Jul 2024 (8mo gap) |

### Invalidated — DO NOT USE

| Claim | Why Wrong |
|-------|-----------|
| ~~Sharpe 9.09~~ | Synthetic data + wrong formula. Correct: 3.76 |
| ~~COVID DD 0.8%~~ | Simulated hedge payoff. Real: 6.6% |
| ~~Hedge cost 0.7%/yr net negative~~ | Real: 4.36%/yr. Net positive cost. |
| ~~102% CAGR (adaptive+hedge)~~ | Built on synthetic np.random data |
| ~~101% CAGR (v4)~~ | Based on 2% hedge cost; real cost is 4.36% → ~97% CAGR |

### Smart Hedge Cost Reduction (`compass/smart_hedge.py`)

Real SPY puts cost 4.36%/yr. Five cheaper alternatives:

| Variant | Annual Cost | How |
|---------|------------|-----|
| A: VIX<15 puts only | ~2.0-2.5% | Buy only when VIX is cheap |
| B: 5% put spreads | ~2.4% | Cap downside cost with long wing |
| C: Dynamic budget | ~1.5-4% | Scale 0.5-3% with VIX |
| **D: Collar** | **~1.3% net** | **Sell 3% OTM calls to fund puts (70% offset)** |
| E: Quarterly selective | ~1.0% | Hedge only before FOMC/CPI/NFP |

**Collar is the recommended production hedge**: ~1.3%/yr net cost, caps upside at ~3% per month but funds puts. Net CAGR impact: -1.3% vs -4.4% for naked puts.

---

## DATA INVENTORY — Critical Gaps

| Ticker | Options End | Gap | Impact | Fix |
|--------|-----------|-----|--------|-----|
| **SPY** | 2026-06-30 | None | — | Production-ready |
| **XLF/XLI** | 2026-06-30 | None | — | Production-ready |
| **GLD** | 2024-03-15 | **14 months** | Blocks EXP-1630 recent validation | Polygon Options tier ($200/mo) |
| **QQQ** | 2023-04-21 | **35 months** | Blocks cross-asset pairs | Polygon Options tier |
| **TLT** | 2024-07-19 | **8 months** | Blocks TLT ICs | Polygon Options tier |
| **VIX/UVXY** | Not in DB | **Complete** | VIX call hedge unvalidated | Polygon Options tier |

**Implication:** Multi-asset strategies cannot be validated on recent data. SPY-only production path has no data gaps.

---

## OOS INTEGRITY AUDIT

| Grade | Count | Experiments |
|-------|-------|-------------|
| **A** | 3 | EXP-1630 GLD/TLT, Vol Term Structure, Cross-Asset Pairs |
| **B+** | 4 | EXP-1220, TLT ICs, EXP-1630-opt, XLI ICs |
| **B-** | 1 | EXP-1650 Earnings |
| **C** | 1 | EXP-1640 Sector Momentum |
| **D/F** | 5 | EXP-1320, EXP-1270, EXP-880, EXP-1470, EXP-1230 |

**SPY-only strategies that are production-ready:** EXP-1220 (B+), Vol Term Structure (A).

Full report: `reports/oos_integrity_audit.html`

---

## PHASE PLAN (Corrected)

### Completed Phases (0-6)

| Phase | Name | Status | Result |
|-------|------|--------|--------|
| 0-4 | Strategy Discovery → Regime Switching | ✅ DONE | Built foundation, found champion |
| 5 | Final Validation | ⚠️ OBSOLETE | Synthetic data — invalidated |
| 6 | Paper Trading v1 | 🔴 STALLED | EXP-400/401 have orphans. EXP-503/600 never deployed. Cron never installed. |
| 6.5-8.99 | Real Data + Portfolio Optimization + Audit | ✅ DONE | IronVault deployed, 14 experiments audited, v4/v5 portfolios built, risk framework, execution sim |

### Phase 7: SPY-Only Production Deployment (NEXT)

**Goal:** Paper trade EXP-1220 + collar hedge + Vol Term Structure on SPY only.
**Why SPY-only:** No data gaps. Most liquid options in the world. No Polygon upgrade needed.

| Step | Task | Duration | Blocker |
|------|------|----------|---------|
| 7.1 | Deploy paper harness (SPY-only mode) | 2-3 days | Mac Studio access, Alpaca keys |
| 7.2 | Configure: EXP-1220 overlay + collar hedge (`smart_hedge.py` variant D) + Vol Term on SPY | 1 day | None |
| 7.3 | Install cron, verify Telegram alerts | 1 day | None |
| 7.4 | Paper trade 8 weeks (target: 30+ trades) | 8 weeks | Time |
| 7.5 | Review: fill rate >90%, DD <15%, P&L matches backtest ±20% | 1 week | Carlos sign-off |
| 7.6 | Seed $25K at 1.0x leverage | 1 day | Funding |
| 7.7 | Monitor live for 4 weeks | 4 weeks | Time |
| 7.8 | Scale: increase capital, optionally add 1.2x leverage | Ongoing | Confidence from real P&L |

**Expected paper results (SPY-only):**
- EXP-1220 at 1x + collar: ~76% CAGR, Sharpe ~5.6, DD ~6%
- Vol Term adds ~0.5% CAGR with negative SPY correlation (-0.32)
- Total paper period: ~14 weeks to first real trade

### Phase 8: Multi-Asset Expansion (BLOCKED on data)

**Goal:** Add GLD/TLT/QQQ strategies once data is current.
**Blocker:** Polygon Options tier upgrade (~$200/mo) + 3hr backfill.

| Step | Task | Prerequisite |
|------|------|--------------|
| 8.1 | Upgrade Polygon to Options tier | Carlos approval ($200/mo) |
| 8.2 | Backfill GLD (14mo), QQQ (35mo), TLT (8mo), VIX options | Polygon upgrade |
| 8.3 | Re-validate EXP-1630 GLD/TLT on 2024-2026 data | Backfill complete |
| 8.4 | Re-validate cross-asset pairs on 2023-2026 data | Backfill complete |
| 8.5 | Re-validate TLT ICs on 2024-2026 data | Backfill complete |
| 8.6 | Validate VIX call hedge with real option prices | VIX options backfilled |
| 8.7 | Paper trade multi-asset portfolio (8 weeks) | All re-validations pass |
| 8.8 | Go live with multi-asset | Paper results confirm |

**Expected if data validates:** 5-strategy portfolio with ~12% additional diversification benefit. Capacity limited by GLD liquidity ($1-10M sweet spot).

---

## CURRENT PRIORITIES

### 1. Deploy SPY-Only Paper Trading (Phase 7.1-7.3)
- [ ] Install cron on Mac Studio
- [ ] Create `.env.ultimate` with Alpaca paper credentials
- [ ] Configure: EXP-1220 + collar hedge + Vol Term (SPY only)
- [ ] Deploy and verify Telegram alerts
- [ ] Start 8-week clock

### 2. Correct All Inflated Numbers
- [x] ~~Sharpe 9.09 → 3.76~~ DONE (1f0888a)
- [x] ~~Hedge cost 2% → 4.36%~~ DONE (51e11e6)
- [x] ~~Data gaps documented~~ DONE (678b764)
- [x] ~~Honest assessment for Carlos~~ DONE (`reports/honest_assessment.html`)
- [x] ~~MASTERPLAN corrected~~ DONE (this commit)

### 3. Evaluate Polygon Upgrade ($200/mo)
- [ ] Carlos decision: is multi-asset expansion worth $200/mo?
- [ ] If yes: backfill GLD/QQQ/TLT/VIX in ~3 hours
- [ ] If no: stay SPY-only (perfectly viable at 73-77% CAGR)

---

## CAPACITY

SPY-only path has no liquidity constraints (3.1M contracts/day). Scales to billions.

| AUM | Exec Cost | Hedge Cost | Total Drag | Net CAGR (est.) |
|-----|-----------|-----------|------------|-----------------|
| $100K | ~0.5% | ~1.3% (collar) | ~1.8% | ~75% |
| $1M | ~1% | ~1.3% | ~2.3% | ~74% |
| $10M | ~2% | ~1.3% | ~3.3% | ~73% |
| $100M | ~5% | ~1.3% | ~6.3% | ~70% |

SPY options are the most liquid in the world. Execution costs are minimal even at $100M.

---

## INFRASTRUCTURE

### Production-Ready (SPY-Only)
```
Strategy:
├── compass/tail_risk_hedge.py        ← EXP-1220 core (Sharpe 5.78)
├── compass/smart_hedge.py            ← 5 cost-efficient hedge variants (collar = 1.3%/yr)
├── compass/vol_term_structure_deep_dive.py ← Vol Term on SPY (Sharpe 2.81 OOS)
├── compass/dynamic_leverage.py       ← VIX/TS/rvol-based leverage (0.3-1.8×)
└── compass/risk_overlay.py           ← 5-layer risk management (71 tests)

Execution:
├── compass/paper_trading_v4.py       ← Paper harness (61 tests)
├── compass/paper_trading_engine.py   ← Core engine (57 tests)
├── compass/prod_monitor.py           ← Production monitoring (87 tests)
├── compass/live_bridge.py            ← Signal → Order translation
├── shared/circuit_breaker.py         ← Kill switch
└── shared/telegram_alerts.py         ← Alerting

Data:
├── shared/iron_vault.py              ← Single data provider
├── data/options_cache.db             ← 948 MB (SPY current to 2026-06)
└── scripts/daily_data_update.sh      ← Cron-ready
```

### Pending Multi-Asset (Phase 8)
```
├── compass/gld_tlt_relval.py         ← EXP-1630 (Grade A, needs GLD data refresh)
├── compass/exp1630_optimizer.py      ← Multi-pair optimizer
├── compass/iron_condor_optimizer.py  ← TLT/XLI ICs (needs data refresh)
└── reports/execution_feasibility.html ← $1-10M capacity analysis
```

---

## TIMELINE

| Date | Milestone |
|------|-----------|
| 2026-04-03 | Operation Real Data: IronVault deployed, 3/6 strategies killed |
| 2026-04-04 | EXP-1220 validated (Sharpe 5.78), new strategies discovered |
| 2026-04-05 | Validation audit: Sharpe 9.09 → 3.76, hedge cost 4.36%/yr |
| 2026-04-05 | Honest assessment for Carlos. Smart hedge (collar @ 1.3%/yr). |
| **2026-04-07** | **(Next) Deploy SPY-only paper trading** |
| 2026-04-14 | Paper trading stable, first week of data |
| 2026-06-09 | Phase 7 complete (8-week paper validation) |
| 2026-06-16 | Go/no-go: seed $25K at 1.0x |
| 2026-07-14 | 4 weeks live monitoring → scale decision |
| TBD | Phase 8: multi-asset expansion (pending Polygon upgrade) |

---

## RULES

1. **Every experiment gets an ID** — EXP-NNN format
2. **Walk-forward required** — param:trade ratio <0.20, no look-ahead
3. **Paper before live** — 8+ weeks paper validation minimum
4. **Follow the data** — kill losers fast, double down on winners
5. **🚫 NO SYNTHETIC DATA** — IronVault only. Cache miss → skip trade.
6. **Real data trumps synthetic** — trust real, kill synthetic
7. **No inflated claims** — use corrected Sharpe formula, real hedge costs
8. **OOS Grade A or B required** for production deployment
9. **MASTERPLAN is sacred** — single source of truth

---

*Victory is not won by the sword alone — it is won by the plan behind it.*
