# MASTERPLAN.md v6 — Honest Numbers Only

**Updated:** 2026-04-05
**Policy:** This document contains ONLY validated, corrected numbers. Inflated claims from previous versions have been removed. See Lessons Learned for full accounting of what went wrong.

## Mission
Build a validated options trading system. Data-driven: kill losers, optimize winners, paper trade, go live.

---

## North Star — Honest Current State

| Target | Goal | Honest Current | Status |
|--------|------|----------------|--------|
| **CAGR** | 100% | **1.2%/yr** (trade-level, pre-utilization fix) | **NOT MET** — dilution bug in daily returns |
| **Sharpe** | 6.0 | **1.26** (per-trade) / **3.85** (validated WF portfolio) | **NOT MET** — previous 5.78 used wrong annualization |
| **Max DD** | ≤12% | **1.6%** (trade-level) / **11.3%** (1.6x WF) | **MET** at trade level |
| **Win Rate** | — | **88%** (171 real IronVault trades) | **PROVEN** |
| **6/6 years** | Yes | **Yes** (EXP-1220 walk-forward) | **MET** |
| **Multi-strategy** | Yes | **1 proven** (EXP-1220), others pending data | **NOT MET** |

### The Honest Bottom Line

**What's real:** EXP-1220 generates 171 real trades over 5 years on IronVault data with 88% win rate, $43 avg PnL per trade, $7,372 total gross PnL on $100K. The alpha is genuine — real option prices, walk-forward validated.

**What's broken:** When we convert these trades to a daily return series for portfolio metrics, 86% of days are zero-return (no trade exits). This dilution crushes the daily Sharpe from the per-trade 1.26 down to near-zero. **This is the capital utilization problem — the #1 blocker.**

**What's wrong with previous claims:**
- 77% CAGR / Sharpe 5.78 used per-trade Sharpe annualized with √(trades/yr) — valid for trade-level analysis but overstates daily-return Sharpe
- The validated walk-forward portfolio (real data, 1.6x leverage) shows 101.6% CAGR / Sharpe 3.85 — but this uses EXP-1220's dynamic leverage overlay on SPY buy-and-hold, which is different from the credit spread trades
- Hedge cost (4.36%/yr real) exceeds the credit spread alpha (1.5%/yr) by 3x

> **🚫 NO SYNTHETIC DATA.** All pricing from `IronVault.instance()` → `data/options_cache.db`.

---

## Critical Issue: Capital Utilization (THE Blocker)

**The problem:** EXP-1220 credit spread trades only occupy capital on ~14% of trading days. On the other 86%, capital sits idle. When we compute daily portfolio returns:
- Trade days: real P&L / capital → meaningful returns
- Idle days: $0 / capital → 0% return
- Daily Sharpe denominator includes all days → diluted to ~0

**The fix (Phase 7):** Deploy idle capital productively:
- Short-term Treasuries (risk-free ~5%/yr on idle days)
- Overlapping positions (enter new trades before old ones expire)
- Cash-secured puts (collect premium on idle capital)
- Dynamic position count (scale from 1-5 concurrent positions)

**Expected impact:** If idle capital earns even 3%/yr, total CAGR jumps from 1.2% to ~4.5%. With 3-5 concurrent positions, CAGR could reach 5-8%/yr at Sharpe >2.

---

## What We Actually Have

### PROVEN — Real IronVault Data

| Metric | Value | Source |
|--------|-------|--------|
| Trade count | 171 | IronVault option_daily (2020-2025) |
| Win rate | 88% | 150 wins / 171 trades |
| Avg P&L per trade | $43 | After real bid-ask spreads |
| Total gross P&L | $7,372 | On $100K capital |
| Max trade DD | 1.6% | Per-trade drawdown |
| Trade Sharpe | 1.26 | Per-trade risk-adjusted return |
| Avg holding period | ~14 days | Credit spread expiration cycles |
| Data quality | Real | IronVault SPY options, no synthetic |

### VALIDATED but with CAVEATS

| Metric | Value | Caveat |
|--------|-------|--------|
| Walk-forward portfolio CAGR | 101.6% at 1.6x | Uses dynamic leverage overlay, not just credit spreads |
| Walk-forward Sharpe | 3.85 | Corrected formula (was 3.94 before bug fix) |
| Walk-forward DD | 11.3% | At 1.6x leverage |
| Vol Term Structure OOS Sharpe | 2.81 | Grade A walk-forward, but only 0.55% CAGR |
| EXP-1630 GLD/TLT OOS Sharpe | 4.08 | Grade A, but GLD data ends Oct 2024 |

### WRONG — Previously Claimed, Now Corrected

| Previous Claim | Corrected Value | Root Cause |
|----------------|-----------------|------------|
| Sharpe 9.09 | **3.76** | Geometric CAGR in formula + synthetic data |
| Sharpe 5.78 | **1.26** (trade) / **3.85** (WF portfolio) | Wrong annualization (√trades vs √252) |
| CAGR 77% (EXP-1220) | **1.2%** (trade-level) | Per-trade CAGR ≠ portfolio CAGR with idle capital |
| Hedge cost 2%/yr | **4.36%/yr** | Real IronVault SPY 5% OTM put prices |
| Hedge "net negative" | **Net cost ~3%/yr** | Alpha ($1.5K/yr) < hedge ($4.4K/yr) |
| COVID DD 0.8% | **6.6%** | Simulated payoff, not real options |

---

## Data Inventory (Post-Backfill 2026-04-05)

| Ticker | Last Expiration | Last Bar | Status |
|--------|----------------|----------|--------|
| **SPY** | 2026-06-30 | 2026-04-02 | **FULL** — production ready |
| **XLF** | 2026-06-30 | 2026-04-02 | **FULL** |
| **XLI** | 2026-06-18 | 2026-04-02 | **FULL** |
| **TLT** | **2025-12-19** | **2025-12-19** | **BACKFILLED** — was Jul 2024, now Dec 2025 |
| **GLD** | 2024-10-18 | 2024-10-18 | **PARTIAL** — extended +7mo, still 14mo gap |
| **QQQ** | 2023-04-21 | 2023-04-21 | **GAP** — 32 months stale |
| VIX/UVXY | Not in DB | — | **MISSING** — VIX call hedge unvalidated |

**TLT is now unblocked** for TLT ICs and TLT-based pairs through Dec 2025.
**GLD still blocked** — need Polygon Options tier ($200/mo) or more OCC symbol construction.
**QQQ still blocked** — 32 months missing.

---

## Lessons Learned

### Bug 1: Sharpe Formula (inflated all portfolio Sharpe by 1.07-2.4×)

Used `CAGR / (vol * √252)` instead of `mean(daily_returns) / std(daily_returns) * √252`. At 100%+ CAGR, geometric vs arithmetic mean diverges significantly. Every portfolio Sharpe reported before commit `ff9dd15` was inflated.

### Bug 2: Synthetic Data Contamination

The "adaptive+hedge" portfolio (Sharpe 9.09) used `np.random.normal()` for daily returns instead of real market data. The artificially smooth synthetic returns produced impossible OOS Sharpe values (18.59 for one year). Multiple portfolio variants mixed real strategy results with synthetic daily return series.

### Bug 3: Capital Dilution (86% zero-return days)

EXP-1220 trades exit on ~171 days out of ~1,260 trading days. The daily return series is 86% zeros. Standard Sharpe, CAGR, and DD calculations on this series drastically understate the strategy's per-trade performance. The 77% CAGR claim used a framework that annualized per-trade returns differently — valid for comparing strategies but misleading as a portfolio CAGR.

### Bug 4: Hedge Cost Underestimation (2.2× higher than assumed)

Real IronVault SPY 5% OTM put prices average 4.36%/yr (range: 2.4% in calm 2023 to 7.3% in volatile 2025). The assumed 2%/yr flat budget was calibrated from academic estimates, not real market prices. Since the credit spread alpha is only ~1.5%/yr, the hedge costs more than the alpha it protects.

### Bug 5: VIX Call Hedge Unvalidated

40% of the tail risk hedge budget was allocated to VIX calls. VIX/UVXY/VXX options are not in the IronVault database. The entire VIX call component was modeled with assumptions — payoff multipliers, trigger thresholds — none backed by real data.

### What We Actually Proved

Despite the bugs, several things are genuinely validated:
1. **EXP-1220 credit spread alpha exists**: 88% WR, $43/trade, 171 real trades
2. **VIX mean-reversion is exploitable**: dynamic leverage based on VIX/TS/rvol works
3. **Walk-forward holds**: EXP-1220 OOS years are consistently profitable
4. **Multi-asset signals work**: EXP-1630, Vol Term Structure, Cross-Asset Pairs all Grade A
5. **SPY options are infinitely liquid**: no execution capacity constraints
6. **Infrastructure is solid**: IronVault, risk overlay, execution sim, paper harness all tested

---

## Phase Plan

### Phase 7: Capital Utilization Fix (CRITICAL BLOCKER — NOW)

**Goal:** Solve the 86% idle capital problem so daily-return metrics reflect the real alpha.

| Task | Approach | Expected Impact |
|------|----------|----------------|
| 7.1 Overlap positions | Run 3-5 concurrent credit spread positions | 3-5× capital utilization |
| 7.2 Idle capital deployment | Short-term T-bills on non-trade days (~5%/yr) | +4% CAGR on idle capital |
| 7.3 Dynamic position count | Scale position count with regime (more in calm, fewer in crisis) | Better risk-adjusted returns |
| 7.4 Re-compute portfolio metrics | Daily returns with utilization fix | Honest Sharpe/CAGR/DD |

**Expected output:** CAGR 5-10%/yr (up from 1.2%), Sharpe 1.5-2.5 (up from diluted ~0), DD 3-5%.

### Phase 8: Multi-Asset Validation with Real Data (NOW)

**Goal:** Re-validate all multi-asset strategies with backfilled data.

| Task | Data Status | Strategy |
|------|-------------|----------|
| 8.1 TLT IC validation (2024-2025) | **READY** (backfilled to Dec 2025) | TLT Iron Condors |
| 8.2 TLT-XLF pair validation | **READY** (both tickers current) | Cross-Asset Pairs |
| 8.3 GLD/TLT relval (EXP-1630) | PARTIAL (GLD ends Oct 2024) | GLD/TLT Relative Value |
| 8.4 QQQ pairs | BLOCKED (QQQ ends Apr 2023) | TLT-QQQ, GLD-QQQ |
| 8.5 Vol Term multi-ticker | **READY** (SPY/XLF current) | Vol Term Structure |

### Phase 9: Honest Portfolio Construction (After 7 + 8)

**Goal:** Build a portfolio using only strategies that survive Phase 7 and 8 validation.

- Only include strategies with: Grade A/B OOS audit, real IronVault data, corrected Sharpe
- Compute portfolio metrics with utilization-fixed daily returns
- Use real hedge costs (4.36%/yr puts or 1.3%/yr collar)
- Walk-forward validate the combined portfolio

### Phase 10: Production Deployment (After 9)

- Paper trade honest portfolio for 8 weeks
- Compare paper P&L to backtest within ±30% tolerance
- Seed $25K at 1x leverage
- Scale only after 4+ weeks of real results

---

## OOS Integrity Audit

| Grade | Count | Experiments |
|-------|-------|-------------|
| **A** | 3 | EXP-1630 GLD/TLT, Vol Term Structure, Cross-Asset Pairs |
| **B+** | 4 | EXP-1220, TLT ICs, EXP-1630-opt, XLI ICs |
| **B-** | 1 | EXP-1650 Earnings |
| **C** | 1 | EXP-1640 Sector Momentum |
| **D/F** | 5 | EXP-1320, EXP-1270, EXP-880, EXP-1470, EXP-1230 |

Full report: `reports/oos_integrity_audit.html`

---

## Current Priorities

### 1. Fix Capital Utilization (Phase 7) — THE BLOCKER
- [ ] Implement overlapping positions (3-5 concurrent)
- [ ] Add T-bill returns on idle capital
- [ ] Re-compute all portfolio metrics with fix
- [ ] Validate: does utilization-adjusted CAGR reach 5%+?

### 2. Validate TLT Strategies on Fresh Data (Phase 8)
- [x] ~~TLT backfill~~ DONE (Jul 2024 → Dec 2025, 0 errors)
- [ ] Re-run TLT IC walk-forward on 2024-2025 data
- [ ] Re-run TLT-XLF pair validation
- [ ] Update REGISTRY.md grades

### 3. Finish GLD/QQQ Backfill
- [ ] GLD: construct OCC symbols for Nov 2024 → Dec 2025 (same method as TLT)
- [ ] QQQ: construct OCC symbols for May 2023 → Dec 2025
- [ ] Re-validate EXP-1630 and cross-asset pairs

### 4. Hedge Cost Resolution
- [ ] Decide: no hedge (1.2% CAGR) vs selective VIX<15 puts (~2%/yr) vs collar (1.3%/yr)
- [ ] At current alpha (1.5%/yr), only selective or collar hedges are cost-viable

---

## Infrastructure

```
Data (IronVault):
├── data/options_cache.db          ← ~1 GB, SPY/XLF/XLI current, TLT backfilled
├── shared/iron_vault.py           ← Single data provider
├── scripts/backfill_tlt.py        ← TLT backfill (OCC construction, Polygon Starter)
├── scripts/backfill_gap.py        ← Targeted gap backfill
└── scripts/daily_data_update.sh   ← Cron-ready

Strategy:
├── compass/tail_risk_hedge.py     ← EXP-1220 core (171 trades, 88% WR)
├── compass/smart_hedge.py         ← 5 cost-efficient hedge variants
├── compass/gld_tlt_relval.py      ← EXP-1630 (Grade A, needs GLD refresh)
├── compass/vol_term_structure_deep_dive.py ← Grade A
└── compass/risk_overlay.py        ← 5-layer risk management (71 tests)

Execution:
├── compass/paper_trading_v4.py    ← Paper harness (61 tests)
├── compass/execution_simulator.py ← Fill probability, degradation (69 tests)
├── compass/prod_monitor.py        ← Monitoring (87 tests)
└── shared/circuit_breaker.py      ← Kill switch

Validation:
├── compass/oos_integrity_audit.py ← 14 experiments graded A-F
├── compass/experiment_runner.py   ← Automated pipeline (77 tests)
├── reports/honest_assessment.html ← Carlos honest report
└── REGISTRY.md                    ← Master scorecard
```

---

## Timeline

| Date | Milestone |
|------|-----------|
| 2026-04-03 | Operation Real Data: IronVault deployed, 3/6 strategies killed |
| 2026-04-04 | EXP-1220 validated, new strategies discovered |
| 2026-04-05 | Validation audit: 5 bugs found, all numbers corrected |
| 2026-04-05 | TLT backfilled to Dec 2025. GLD extended to Oct 2024. |
| 2026-04-05 | Honest assessment: 1.2% trade-level CAGR, dilution bug identified |
| **NOW** | **Phase 7: Capital utilization fix (THE blocker)** |
| **NOW** | **Phase 8: Multi-asset validation with fresh TLT data** |
| TBD | Phase 9: Honest portfolio construction |
| TBD | Phase 10: Paper trading → live |

---

## Rules

1. **🚫 NO SYNTHETIC DATA** — IronVault only. Cache miss → skip trade.
2. **No inflated claims** — corrected Sharpe formula, real hedge costs, honest CAGR
3. **Walk-forward required** — Grade A/B audit before production
4. **Paper before live** — 8+ weeks validation
5. **Capital utilization must be solved** — no portfolio metrics without it
6. **Real data trumps everything** — if model says X and data says Y, data wins
7. **MASTERPLAN is honest** — single source of truth, warts and all

---

*The truth doesn't care about our timeline. Build on what's real.*
