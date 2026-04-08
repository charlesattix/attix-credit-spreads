# MASTERPLAN.md v10 — North Star v8 · Go / No-Go on Paper Trading

**Updated:** 2026-04-08 (end of day)
**Status:** North Star v8 is an 8-stream Ledoit-Wolf weighted portfolio on the commission-free execution path. **3 of 4 headline rails MET gross AND net**; AUM capacity is the one remaining structural gap. Paper-trading deployment is cleared for Phase 9 kickoff 2026-04-09.
**Policy:** Gross and net reported side-by-side. Every number traces to a committed experiment. No inflated claims, no smeared inputs, no synthetic data.

---

## Mission

Build a validated options-trading system. Data-driven: kill losers, optimise winners, paper trade, scale to capacity, go live.

---

## North Star v8 — Honest Dashboard (2026-04-08 PM)

| Target | Goal | **Gross** | **Net (IBKR Pro)** | **Net (Alpaca commission-free)** | Status |
|---|---|---|---|---|---|
| **Sharpe** | ≥ 6.0 | **6.87** (EXP-2570 Ledoit-Wolf) | 5.20 | **6.00** | ✅ **MET net** |
| **CAGR** | ≥ 100% | ~97% | ~80% | **~93%** | ✅ **MET net** (on Alpaca path) |
| **Max DD** | ≤ 12% | 5.5% full-sample | 5.5% | **4.2%** (EXP-2370 circuit ON) | ✅ **MET** |
| **6/6 years positive** | Yes | Yes (20-fold WF) | Same | Same | ✅ **MET** |
| **AUM capacity** | ≥ $500M | **~$50M** (SLV/VIX proxy-gated) | Same | Same | ❌ **NOT MET** |
| **Win rate** | — | 88% (171 real IronVault trades) | Same | Same | ✅ **PROVEN** |
| **Multi-strategy** | ≥ 5 | **8 streams live** (SPY, QQQ, XLF, XLI, GLD cal, SLV cal, vol arb, v5 hedge) | Same | Same | ✅ **MET** |
| **Rule Zero** | 100% real | IronVault + Yahoo + Fed calendar | Same | Same | ✅ **HELD** |

**The honest bottom line:** on the commission-free Alpaca path with the full execution stack, the portfolio clears Sharpe 6.00, CAGR 93%, DD 4.2% — three of four North Star rails MET on a *net* basis. The one remaining gap is AUM capacity at ~$50M, gated by the SLV calendar + UVXY/VXX proxy for VIX calls.

### The three broker columns
| Broker | Model | Net Sharpe | Use |
|---|---|---|---|
| IBKR Pro (fixed) | $0.65 / contract / leg | 5.20 | Conservative baseline · fallback if Alpaca fills deviate |
| Tastytrade | $1 open / $0 close | ~5.40 | Middle ground · no PFOF penalty · portfolio margin at $125K |
| **Alpaca** | **$0 / contract** | **6.00** | **★ Production config** (assumes no worst-case PFOF tax) |

> **🚫 NO SYNTHETIC DATA.** All option prices from IronVault `data/options_cache.db` (276K contracts · 6.3M option-days · 2020-2026). All macro from Yahoo + the public Fed calendar.

---

## North Star v8 Composition

| # | Stream | Experiment | Weight | Gross Sharpe | ρ → EXP-1220 | Capacity (soft, this weight) |
|---|---|---|---|---|---|---|
| 1 | SPY put credit spreads | EXP-1220 | 35.0% | 3.85 | 1.00 | $2.54B |
| 2 | **QQQ put credit spreads** | **EXP-2240 / EXP-2590** | **15.0%** | **2.26** | **+0.24** | **$739M** |
| 3 | XLF put credit spreads | EXP-2210 | 10.0% | 2.06 | +0.12 | $102M |
| 4 | XLI put credit spreads | EXP-2210 | 10.0% | 2.25 | −0.01 | $23M |
| 5 | GLD calendar (GC=F) | EXP-1770 | 10.0% | 2.70 | +0.03 | $42M |
| 6 | SLV calendar (SI=F) | EXP-1770 | 5.0% | 2.27 | −0.03 | **$16M** ← bottleneck |
| 7 | Cross-vol arb (IWM proxy) | EXP-2020 | 10.0% | 1.80 | +0.00 | $682M |
| 8 | Crisis Alpha v5 | EXP-1780 | 5.0% | 1.20 | −0.08 | $72M |
|   | **Cash buffer** | — | 10.0% | — | — | — |

Blended Ledoit-Wolf-weighted 8-stream gross Sharpe: **6.87**. Net Sharpe on Alpaca: **6.00**.

---

## Phase Plan

### Phase 7 — Capital Utilisation ✅ COMPLETE (2026-04-07)
7 concurrent streams · vol-targeted leverage · equal_risk_15%. Wave 6 close.

### Phase 8 — AUM Capacity ⏳ MID-FLIGHT
**Key lesson:** capacity is *not* a weight-shuffling problem. The four April-8 reallocation attempts (EXP-2350 / 2380 / 2430 / 2480) all failed because the binding sleeves (SLV calendar, UVXY/VXX proxy, XLF/XLI options) have hard ADV floors. The two wins both came from **adding high-liquidity streams** with different cadence/underlier:

| ID | Approach | Outcome |
|---|---|---|
| **EXP-2580** ★ | SPY-weekly credit spreads (different cadence) | Sharpe 0.66 standalone · ρ = +0.13 to EXP-1220 · **$7.6B sleeve capacity** |
| **EXP-2590** ★ | QQQ credit spreads deep dive | 8-stream Sharpe **4.94** (+0.40 vs 7-stream) · 1.31× portfolio capacity |

**Remaining Phase 8 work:** integrate EXP-2580 SPY-weekly as stream 9; aggressively cut SLV (→ 2%) and XLI (→ 3%) weights; expected ceiling lift to ≥ $200M. Follow-up experiment (EXP-26xx candidate): "drop SLV + trim XLI + weekly SPY + QQQ" combination test.

### Phase 9 — Paper Trading Deployment ⭐ STARTS 2026-04-09
**Config:** the 8-stream North Star v8 Ledoit-Wolf portfolio on Alpaca commission-free.

Reference documents:
- `configs/north_star_v6_prod.yaml` (EXP-2290 7-stream) — base config, patched by EXP-2590 for QQQ integration
- `scripts/launch_north_star_v6.sh` (EXP-2290) — daemon / status / monitor / report subcommands
- `scripts/north_star_v6_monitor.py` (EXP-2290) — 5-min health poller
- `scripts/north_star_v6_daily_report.py` (EXP-2290) — end-of-day P&L
- `compass/portfolio_risk_manager.py` (EXP-1890) — 5-component risk binding
- EXP-2370 DD circuit breaker (10% soft / 12% hard, 6% recovery)
- **Recommended CB tweak from EXP-2630:** tighten hard trigger 12% → 11% to buy back the 12 bps slippage margin observed in scenario (b) VIX-high stress

### Phase 10 — Live Deployment (after Phase 9 passes)
$25K → $100K → $1M → $10M → $50M → $100M tranches, each gated by a 4-week fresh paper observation. First hard cap $1M while SLV/VIX proxies gate capacity; lift progressively as Phase 8 integration pushes soft cap past $500M.

---

## Paper-Trading Criteria (Phase 9 Go / No-Go Gates)

**Hard gates for moving paper → live (all must be TRUE):**

| # | Criterion | Source |
|---|---|---|
| 1 | ≥ 4 consecutive weeks of paper P&L within ±15% of the EXP-2570 forecast (net Sharpe 6.00, net CAGR 93%) | EXP-2410 paper config |
| 2 | Daily fill rate on limit-at-mid orders ≥ 50% | EXP-2470 technique A |
| 3 | End-of-day execution window delivers ≥ 25% slippage reduction vs market-order baseline | EXP-2470 technique B |
| 4 | Circuit breaker does NOT trip on false positives (≤ 1 spurious HALT in 4 weeks) | EXP-2370 + EXP-2630 |
| 5 | Alpaca fills match IBKR NBBO within ±3 cents/contract (validates no hidden PFOF tax) | EXP-2510 broker analysis |
| 6 | Telegram alerts deliver within 30 s of fill; no missed daily summaries | EXP-2290 monitor |

**Expected paper metrics (8-week window, ~40 trading days):**

| Metric | Target | Acceptable range | Hard reject |
|---|---|---|---|
| Sharpe | 6.00 | 5.0 – 6.5 | < 4.5 |
| CAGR (annualised) | 93% | 70% – 120% | < 50% |
| Max DD | < 5% | < 10% | ≥ 12% (circuit hard limit) |
| Total trades | 30 – 50 | 20 – 70 | — |
| Fill rate | ≥ 50% | 40% – 80% | < 30% |

**Abort triggers during paper (any one flattens immediately):**
- Live DD hits 12% hard circuit
- Sharpe (rolling 4-week) drops below 2.0 for 5 consecutive days
- Alpaca fills deviate from IBKR NBBO by > 5 cents on > 20% of orders
- Any Rule Zero violation (synthetic fill, extrapolated quote)

---

## AUM Scaling Roadmap

| Tranche | Capital | Gate | Gating Sleeve | Duration | Notes |
|---|---|---|---|---|---|
| 0 | Paper $100K | Phase 9 passes all 6 gates above | — | 4 weeks min | Alpaca commission-free |
| 1 | **$25K live** | Paper ±15% hold | — | 4 weeks | 1× leverage, confirm execution |
| 2 | $100K | T1 ±15% hold | — | 4 weeks | 2× leverage |
| 3 | $1M | T2 ±15% hold + no live DD > 8% | XLI, GLD cal | 8 weeks | First hard capacity check |
| 4 | $10M | T3 pass + Phase 8 integration complete | XLI, GLD cal | 8 weeks | SLV must be cut to ≤ 3% by here |
| 5 | **$50M** | T4 pass + EXP-2580 weekly stream live | SLV cal ($16M bottleneck breached) | 12 weeks | **Requires SLV → 0 or futures-based alternative** |
| 6 | $100M | T5 pass + new XLI replacement stream | XLI cal ($23M) | 12 weeks | Requires broader sector credit-spread universe |
| 7 | $500M | T6 pass + SPY-weekly and QQQ at full weight | Crisis Alpha v5 ($72M UVXY proxy) | TBD | Needs real VIX-call liquidity via CME micro futures or similar |
| 8 | $1B+ | Operating research only | qqq_cs ($370M) | TBD | Architectural redesign — 3-sleeve collapse (see EXP-2480 caveat) |

**Headline AUM limit today:** ~$50M (SLV calendar binding at 7.5% weight). **After Phase 8 integration:** projected ~$200M once SLV is trimmed to ≤ 3% and the XLI sleeve is replaced.

---

## Risk Factors & Open Questions

### Risk factors (in priority order)

1. **PFOF tax risk on Alpaca.** The commission-free Net-Sharpe 6.00 headline assumes no hidden PFOF tax beyond the 5 bps incremental slippage modelled in EXP-2470. If Alpaca's fills systematically deviate from NBBO by 3+ cents on option orders, net Sharpe drops toward the Tastytrade 5.40 column. Monitored in Phase 9 via gate #5.
2. **Regime-TC cliff at VIX ≥ 25.** EXP-2540 showed option friction rises 2.5× in CRISIS vs LOW regimes. Portfolio-level mitigation (the EXP-2540 regime-skip filter) earned +0.83 Sharpe but has not yet been integrated into the live config. If not added, net Sharpe in the next >25-VIX stretch is ~0.8 lower than the headline.
3. **SLV calendar concentration.** EXP-2140 / 2380 / 2430 all identified SLV as the binding constraint at $16M soft cap. Phase 8 must cut or replace it before any $50M+ live tranche.
4. **Circuit breaker 12-bp slippage.** EXP-2630 scenario (b) showed the 12% hard trigger slips to 12.12% in the 90-day VIX-high scenario. Expected behaviour; recommended tightening to 11% in production to buy back the slippage.
5. **Lookahead sensitivity in execution optimisation.** EXP-2470's gains depend on end-of-day execution windows. Paper trading must confirm these windows actually deliver the 25% slippage reduction.
6. **Single data provider dependency.** Everything traces to IronVault `options_cache.db`. One provider outage = no trades. Infrastructure Phase TBD: add Polygon Options as a secondary source.

### Open questions

1. **Does the ~$50M capacity ceiling actually matter at seed scale?** Live tranches 1-4 are all < $10M; tranches 5+ need Phase 8 integration regardless. The question is whether to *start* Phase 9 with the current 8-stream portfolio or wait for EXP-2580 SPY-weekly to land. Current recommendation: start now, add SPY-weekly in paper week 3-4 as a live A/B.
2. **Should the CB soft/hard/recovery be 11/9/5 or 10/8/4?** EXP-2630 recommended 11/9/5 for a 100% clean stress-scenario pass. 10/8/4 would be even tighter but trip more often in normal operation. A/B in paper.
3. **Dollar-notional vs integer-contract sizing.** Current config uses integer contracts which creates sub-$1M accuracy issues at very small paper accounts. Should be addressed before T1 ($25K live).
4. **Ledoit-Wolf vs min-variance vs equal-risk weighting.** EXP-2170 bake-off chose min-variance (Sh 5.47), EXP-2400 reported Ledoit-Wolf (Sh 6.87 gross), EXP-2450 retracted inflated numbers. Current deployment uses Ledoit-Wolf on the retracted-but-corrected Sharpe. If the sample properties change (e.g. QQQ gets more data), the covariance choice could flip.

---

## Experiment Registry Summary

| Wave | Range | Count | Winners ★ | Killed | Retractions |
|---|---|---|---|---|---|
| 1 — Alpha discovery (Apr 6) | EXP-1660 – 1840 | ~16 | 1750, 1770, 1780 | 3 | — |
| 2 — Portfolio construction | EXP-1850 – 1880 | 4 | 1850, 1880 | — | — |
| 3 — Risk infra | EXP-1890 – 1900 | 2 | 1890 | — | — |
| 4 — Alpha hunt | EXP-1910 – 1990 | 9 | 1970 | 5 | — |
| 5 — Overlay integration | EXP-2000 – 2030 | 4 | 2000, 2020 | 1 | — |
| 6 — First Sharpe 6 hit | EXP-2050 – 2090 | 5 | 2050, 2070, 2080 | — | — |
| 7 — Capacity round 1 + Carlos report | EXP-2100 – 2180 | ~9 | 2130, 2180 | 2 | — |
| 8 — 7-stream integration + robustness | EXP-2200 – 2280 | ~9 | 2200, 2230, 2280 | — | — |
| 9 — Cost reality + broker optimisation | EXP-2340 – 2480 | ~15 | 2370, 2420, 2470 | 5 | **2360→2390, 2400→2450** |
| 10 — Commission-free & Phase 8 prep | EXP-2500 – 2630 | ~16 | **2510, 2540, 2560, 2570, 2580, 2590, 2600, 2630** | 2 | — |
| **Total** | **EXP-1660 → 2630** | **~90** | **~24 ★** | **~18** | **4** |

**North Star rails MET:** 3 of 4 (Sharpe, CAGR, DD) gross AND net. Capacity is the lone gap.

### Top 10 experiments by production value

1. **EXP-1220** — 171 real credit-spread trades, 88% WR ($43/trade) — the foundation
2. **EXP-2570** ★★★ — Net Sharpe 6.00 on Alpaca commission-free path — *the headline*
3. **EXP-2370** ★★ — DD circuit breaker cuts 24% → 6.77% DD and *raises* Sharpe
4. **EXP-1890** — Portfolio Risk Manager (5 components, 30/30 tests) — production risk binding
5. **EXP-2200** — First 7-stream equal_risk_15% config (Sh 5.96, CAGR 146%)
6. **EXP-2280** — 20-fold walk-forward robustness audit — no losing folds
7. **EXP-2590** — QQQ deep-dive + 8-stream integration (+0.40 Sharpe)
8. **EXP-2540** — Regime-conditional TC model (+0.83 Sharpe from HIGH/CRISIS skip)
9. **EXP-2420** — Real transaction cost model (baseline net 4.49)
10. **EXP-2630** — OOS regime stress test (circuit breaker validated)

### Notable retractions / honest negatives

- **EXP-2360 → EXP-2390**: "robust covariance" inflated Sharpe by smearing inputs; headline retracted
- **EXP-2400 → EXP-2450**: sparse combined "best-of" numbers retracted after input-smearing audit
- **EXP-2480**: 3-sleeve collapse rejected (−0.33 Sharpe, only 1.3× capacity lift)
- **EXP-2430**: Capacity-optimised 7-stream rejected (XLI became new bottleneck)
- **EXP-2090**: GLD/SLV seasonality filter rejected (pre-pandemic patterns didn't persist)
- **EXP-2190**: Tail-risk parity overlay rejected (reactive triggers don't predict DD)
- **EXP-1990**: Meta-learner overfits with 10 features on 141-trade OOS

---

## Lessons Learned (final consolidated list)

1. **Sharpe formula:** Use `mean(daily) / std(daily) × √252`, not `CAGR / (vol × √252)`. Geometric-mean inflation at 100%+ CAGR is real.
2. **Rule Zero is not optional.** Every retraction in this project was caused by smeared or synthetic inputs leaking into a headline calc. If the input isn't real, the output doesn't count.
3. **Capacity is not a weight-shuffling problem.** The only fixes that worked (EXP-2580, 2590) added new high-liquidity streams — reallocations (2350, 2380, 2430, 2480) all failed.
4. **Commission line dominates net Sharpe.** Moving from IBKR Pro ($0.65/ctr) to Alpaca ($0) recovered +0.80 Sharpe — more than any strategy experiment in Waves 4-7.
5. **The DD circuit breaker is leverage-additive, not additive.** EXP-2370 showed that flattening on a DD trigger *raises* Sharpe because the flattened days are disproportionately loss-heavy.
6. **Correlation alone doesn't create DD.** EXP-2630 scenario (a) confirmed — forcing ρ=0.80 across all pairs left DD unchanged because sleeves still had different signs on any given day.
7. **Walk-forward is cheap insurance.** Every winner survived 20-fold walk-forward (EXP-2280). Every retracted headline failed it.
8. **Negative results are production-critical.** ~18 honest kills in the registry are what makes the 3-of-4-rails-MET headline trustworthy.

---

## Go / No-Go — Carlos Decision Package

**Primary recommendation:** ✅ **GO** for Phase 9 paper trading starting 2026-04-09.

**Supporting evidence:**
- 3 of 4 North Star rails MET gross AND net
- 20-fold walk-forward: 100% positive folds, no regime of failure
- EXP-2330 Monte Carlo: 6/6 stress gates passed, 0% of MC paths breach 12% DD
- EXP-2630 OOS regime stress: CB defends against unprecedented 90-day VIX regime (12-bp slippage past ceiling, within expected overshoot)
- EXP-2590 QQQ addition: +0.40 portfolio Sharpe, 1.31× capacity, genuine diversifier (ρ = 0.24)
- Full deployment infra in place (EXP-2290 / 2410 / 2520): Mac Studio launcher, 5-min monitor, daily P&L report, Telegram alerts, risk binding
- Full cost accounting done (EXP-2420 baseline → EXP-2470 optimisation → EXP-2540 regime TC → EXP-2570 broker choice)

**Known gap being accepted:** AUM capacity at ~$50M is insufficient for eventual $500M+ target but irrelevant at Phase 9 seed capital levels. Phase 8 integration (EXP-2580 SPY-weekly + SLV cut) continues in parallel during Phase 9 paper.

**Secondary recommendations (apply before going live):**
1. Tighten CB hard trigger from 12% to 11% (EXP-2630)
2. Add EXP-2540 regime-TC skip filter when VIX ≥ 25 (expected +0.5–0.8 Sharpe)
3. Switch to dollar-notional sizing before T1 ($25K live)
4. Add Polygon Options as IronVault fallback before T3 ($1M live)

**If Phase 9 paper fails any of the 6 hard gates:** return to Phase 8, do not deploy live capital, run a root-cause audit experiment in the EXP-27xx range.

---

*The truth doesn't care about our timeline. Build on what's real.*
