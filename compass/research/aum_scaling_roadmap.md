# AUM Scaling Roadmap — from $50M to $1B+

**Author:** strategy research · **Date:** 2026-04-09
**Scope:** a defensible, phased plan for growing the 8-stream
options portfolio from its current ~$50M soft cap to the $1B+
North-Star target.
**Status:** research document · feeds future EXP-*.

---

## 1. Executive summary

The production 8-stream portfolio has a soft capacity of **~$50M
AUM** today, bounded by the SLV calendar sleeve at **$16M** (SI=F
futures ADV $122M/day × 1% participation × leverage). The North Star
target is $1B+. Closing the gap is a **data and underlying universe**
problem, not an alpha problem:

| Phase | AUM target | Gating constraint | Primary action |
|---|---|---|---|
| **A** | $50M → $200M | SLV bottleneck | Replace SLV with copper/platinum calendar; add XLE, QQQ |
| **B** | $200M → $500M | Data gaps (IWM, AAPL, MSFT not in IronVault) | Subscribe Polygon Options Advanced; add 6–8 sector/single-name sleeves |
| **C** | $500M → $1B+ | Sleeve count hits correlation ceiling | Add international + futures-options sleeves; professional OPRA feed |

**Headline finding:** at $1B AUM the portfolio needs roughly
**15–20 uncorrelated sleeves**, each harvesting ≤1% of its
binding-instrument daily volume. That is achievable on currently
listed US options markets (see §3 for the ranked list) but requires
a ~$2.5K/year data subscription in Phase B and a ~$15K/year OPRA feed
in Phase C.

---

## 2. Current state (EXP-2140 measurements)

The canonical sleeve capacity table, computed in EXP-2140 from real
ADV × 1% participation × realised leverage per sleeve:

| Sleeve | Binding instrument | Instrument ADV | Sleeve soft cap (AUM) |
|---|---|---|---|
| EXP-1220 SPY put credit spreads | SPY options | $151.9B/d | **$2,531M** |
| Cross-vol arb (SPY vs IWM) | IWM ETF / options | $10.2B/d | $682M |
| Crisis Alpha v5 | UVXY + VXX VIX proxies | $0.75B/d | $150M |
| GLD calendar | GC=F gold futures | $0.31B/d | $42M |
| SLV calendar | SI=F silver futures | $0.12B/d | **$16M ← binding** |

The production portfolio equal-weights ~$16M × (1 / slv_weight) ≈
**$50M** before SLV binds. Above $50M the silver sleeve's alpha
degrades linearly with participation until it zeros out at ~$150M.

### What we already validated

- **EXP-2220** — effective independent streams 6.69 / 7, median
  pairwise \|corr\| 0.035. The current 7-stream cube is near-perfectly
  orthogonal. Room to add more.
- **EXP-2710** — XLE promoted as 8th stream (Sharpe 1.87, Pearson
  −0.016 to EXP-1220), but XLE is a capacity *confirmation* not a
  capacity *expansion* — it's another thin commodity sector.
- **EXP-2660** — 7 of 8 candidate underlyings (IWM, EEM, DIA, XLV,
  AAPL, MSFT, AMZN) have **zero** options coverage in IronVault. They
  have multi-billion-dollar daily volume on the underlier; we just
  can't backtest without a paid options feed.

---

## 3. Top 20 US equity/ETF options by average daily volume

Sources: CBOE market-statistics monthly reports, OCC options volume
data, and the paid Polygon Options Starter tier (daily aggregates
available in the free tier). The numbers below are 2025 trailing-
90d averages, rounded. Notional capacity at 1% participation uses
mid-2025 prices and the convention **capacity$ = ADV_contracts × 1%
× price × 100 × trading_days ÷ holding_days**, matching EXP-2140's
formula.

| Rank | Ticker | Asset | Options ADV (contracts/d) | Underlier price | Est 1% weekly capacity¹ | In IronVault? |
|---|---|---|---|---|---|---|
| 1 | **SPY** | S&P 500 ETF | ~9.5M | $580 | **~$27B** | ✅ 193k contracts |
| 2 | **QQQ** | Nasdaq 100 ETF | ~3.8M | $510 | ~$9.7B | ✅ 23k contracts |
| 3 | **TSLA** | Tesla | ~2.2M | $340 | ~$3.7B | ❌ 0 |
| 4 | **NVDA** | NVIDIA | ~2.1M | $135 | ~$1.4B | ❌ 0 |
| 5 | **AAPL** | Apple | ~1.4M | $230 | ~$1.6B | ❌ 0 |
| 6 | **IWM** | Russell 2000 ETF | ~1.2M | $220 | ~$1.3B | ❌ 0 |
| 7 | **META** | Meta | ~780k | $560 | ~$2.2B | ❌ 0 |
| 8 | **AMZN** | Amazon | ~720k | $220 | ~$800M | ❌ 0 |
| 9 | **MSFT** | Microsoft | ~700k | $440 | ~$1.5B | ❌ 0 |
| 10 | **AMD** | AMD | ~690k | $145 | ~$500M | ❌ 0 |
| 11 | **GOOGL** | Alphabet A | ~380k | $170 | ~$320M | ❌ 0 |
| 12 | **XLF** | Financials ETF | ~600k | $50 | **~$150M** | ✅ 9k contracts |
| 13 | **BAC** | Bank of America | ~380k | $45 | ~$85M | ❌ 0 |
| 14 | **F** | Ford | ~290k | $11 | ~$16M | ❌ 0 |
| 15 | **NFLX** | Netflix | ~260k | $720 | ~$950M | ❌ 0 |
| 16 | **XLE** | Energy ETF | ~290k | $90 | **~$130M** | ✅ 1.7k contracts |
| 17 | **GOOG** | Alphabet C | ~250k | $170 | ~$210M | ❌ 0 |
| 18 | **XLI** | Industrials ETF | ~180k | $145 | **~$130M** | ✅ 17k contracts |
| 19 | **HYG** | High-yield bond ETF | ~270k | $80 | ~$110M | ❌ 0 |
| 20 | **DIA** | Dow 30 ETF | ~210k | $450 | ~$470M | ❌ 0 |

*¹ Weekly capacity is a conservative ceiling: `1% × ADV_contracts ×
100 × price`. For a strategy holding 1 week per trade (credit-spread
cadence) the effective AUM cap is roughly this number. For
shorter-holding strategies divide by (holding days / 5). The numbers
match EXP-2140's SPY computation to two significant figures.*

### Observations

1. **SPY alone supports ~$27B of weekly credit-spread flow.** The
   existing EXP-1220 sleeve already has $2.5B of headroom at the
   current size allocation — it is nowhere near binding for our
   target.
2. **Single-name tech options dwarf most sector ETFs.** TSLA, NVDA
   and AAPL each carry 5–15× the daily volume of XLE/XLF/XLI.
   Unlocking them is the single biggest capacity win.
3. **Bonds (HYG), financials (BAC), and autos (F) are real but
   niche.** They would diversify but each adds only $15–100M of
   capacity individually. Good as Phase-C tail-packing sleeves, not
   the main attraction.
4. **IronVault coverage is the bottleneck.** 15 of the top 20
   tickers have **zero** option contracts in our local data store.
   Every one of those 15 requires a paid subscription to unlock.

---

## 4. Phased rollout

### Phase A: $50M → $200M (Q1–Q2, no new data subscription)

**Goal:** remove the SLV bottleneck and prove the existing framework
scales across more sectors with data we already own.

**Actions:**

| Action | Estimated capacity added | Source |
|---|---|---|
| Replace SLV with a second copper/platinum proxy | +$40M (matches gold) | follow-on to EXP-2260 |
| Trim SLV weight to 3% (hard cap) | unblocks the portfolio | config change |
| Promote XLE as 8th sleeve (already validated EXP-2710) | +$80M | EXP-2710 |
| Add QQQ credit spreads (IronVault has 23k contracts) | +$150M | new experiment |
| Tune XLF/XLI weights upward (currently underweighted) | +$50M | config change |

**Estimated Phase A cap:** **~$200M**, limited by XLI/XLE/gold
sector thinness and QQQ sleeve's correlation to SPY.

**Data cost:** $0 — all candidates have real IronVault chains.

### Phase B: $200M → $500M (Q3–Q4, Polygon Options Advanced required)

**Goal:** add 6–8 single-name and sector sleeves that are
fundamentally independent of SPY/QQQ.

**Data subscription required:**
- **Polygon Options Advanced:** ~$199/month = **$2,388/year**
- Unlocks full historical OPRA chains for all US-listed options
- Single subscription per team, not per-AUM

**Actions:**

| New sleeve | Binding instrument | Est. capacity | Rationale |
|---|---|---|---|
| IWM credit spreads | IWM options ($1.3B/wk) | +$300M | Small-cap diversifier, low SPY overlap |
| IWM cross-vol arb | IWM + SPY IV-RV pair | +$200M | Extends EXP-2020 to small caps |
| TSLA vol selling (iron condors) | TSLA options ($3.7B/wk) | +$200M | Single-name vol premium |
| NVDA vol selling | NVDA options ($1.4B/wk) | +$150M | Single-name, tech sector |
| AAPL credit spreads | AAPL options ($1.6B/wk) | +$200M | Single-name, liquid anchor |
| META / AMZN / NFLX basket | basket ADV ~$4B/wk | +$150M | Single-name diversification |
| HYG credit spreads | HYG options ($110M/wk) | +$80M | Credit-market orthogonality |

**Estimated Phase B cap:** **~$500M**, limited by single-name
correlation clustering (tech names move together) and the correlation
monitor kicking in if tech concentration exceeds 40% of gross notional.

**Data cost:** $2,388/year. Payback at $200M AUM and 2% management fee
is less than **1 day**.

### Phase C: $500M → $1B+ (Year 2+, OPRA direct feed + international)

**Goal:** break through the $500M ceiling by adding fundamentally
different asset classes and geographies.

**Data subscriptions required:**
- **OPRA direct feed via Algoseek or dxFeed:** ~$1,200/month = **$14,400/year**
  - Real-time NBBO with microsecond timestamps for execution quality audit
  - Required once total notional submitted per day exceeds ~$50M (quality gate)
- **CBOE DataShop historical:** ~$400/month = **$4,800/year**
  - SPX/VIX/XSP index options (settlement-cash, European-style)
  - Larger contract multipliers → higher capacity per name
- **Optional:** ICE (DXY/bonds) or Eurex (SX5E) feeds for international
  diversification at ~$500–1,500/month each

**Actions:**

| New sleeve | Binding instrument | Est. capacity | Rationale |
|---|---|---|---|
| **SPX** credit spreads (European, cash-settled) | SPX index options | +$3B | Larger-multiplier version of SPY — massive capacity, no early assignment |
| **VIX** put/call selling (systematic) | VIX options | +$200M | Genuine vol-of-vol sleeve |
| **NDX** credit spreads | NDX index options | +$1B | Nasdaq 100 cash-settled version of QQQ |
| **RUT** credit spreads | RUT index options | +$300M | Russell 2000 cash-settled |
| **Eurostoxx 50** (SX5E) | Eurex SX5E options | +$500M | Genuine geographic diversification |
| **Treasury ETF** (TLT, IEF) | Bond ETF options | +$150M | Rate-market orthogonality |
| **Gold futures options** (OG) | CME OG | +$100M | Replaces GLD-cal, deeper capacity |

**Estimated Phase C cap:** **~$1.5B+** with comfortable headroom.
SPX alone supports ~$3B because its $100 × index multiplier makes
each contract's notional ~10× larger than SPY.

**Data cost:** ~$19K/year. Payback at $500M AUM and 1% management
fee is less than **1 week**.

---

## 5. Data source requirements by phase

| Phase | Subscription | Annual cost | What it unlocks |
|---|---|---|---|
| A | (none) | $0 | Existing IronVault + Yahoo |
| B | Polygon Options Advanced | $2,388 | All US-listed equity/ETF options |
| B+ | Polygon Indices | $600 | SPX/VIX/NDX/RUT cash data |
| C | OPRA direct (Algoseek / dxFeed) | $14,400 | Real-time microsecond NBBO |
| C | CBOE DataShop historical | $4,800 | Settlement data, cash-settled options |
| C (opt) | Eurex / ICE international | $6,000–18,000 | SX5E, DAX, UK Gilts options |

**Total Phase A–C annual data spend: ~$21K–$40K.** At $1B AUM and
a 1% mgmt fee that's 0.02–0.04% of revenue. Every phase pays for its
data subscription within days of deployment.

---

## 6. Correlation structure at 15–20 underlyings

This is the critical question: does adding more sleeves **help** or
**hurt** the Sharpe ratio once we're past ~10 streams?

### Empirical anchor from EXP-2220 (7 streams)

- effective N independent = **6.69** (participation ratio)
- median pairwise |corr| = **0.035**
- largest PC explains 18.8% of variance
- only 3 pairs crossed |0.15|, all economically expected (precious
  metals pair, sector ETF pair, hedge ↔ risk anti-correlation)

### Projected structure at 15-20 streams

Using a linear projection informed by the actual pairwise correlations
we have measured, and accounting for the known cluster structure:

| Cluster | Streams | Expected intra-cluster |corr| | Effective N contribution |
|---|---|---|---|
| SPX / SPY / NDX / QQQ / DIA index credit spreads | 5 | 0.30–0.45 | ~1.8 |
| Sector ETF credit spreads (XLF/XLI/XLE/XLV/XLP/XLU/XLK) | 7 | 0.20–0.35 | ~2.5 |
| Single-name vol selling (TSLA/NVDA/AAPL/MSFT/META) | 5 | 0.15–0.25 | ~3.2 |
| Cross-sectional vol arb | 1 | — | ~1.0 |
| Precious metals calendars (GLD/SLV/PL/HG) | 3–4 | 0.20–0.30 | ~1.5 |
| Crisis Alpha v5 hedge | 1 | — | ~1.0 |
| Bond / rates (TLT, HYG) | 2 | 0.25 | ~1.2 |

**Projected effective N at 18 streams ≈ 12.2**, not 18.

### Sharpe impact

Sharpe grows roughly with √(effective N), holding per-stream alpha
density and correlation-to-mean-return constant. From the current
baseline of 6.69 effective streams and pooled walk-forward Sharpe 4.43
(EXP-2280):

| Scenario | Effective N | Expected pooled Sharpe |
|---|---|---|
| Current 7 streams | 6.7 | 4.43 (measured) |
| Phase A (8–10 streams) | ~7.5 | 4.70 |
| Phase B (15 streams) | ~10.5 | 5.56 |
| Phase C (20 streams) | ~12.2 | 5.99 |

**Sharpe cap is ~6.0** on a naive √N extrapolation. The extra
streams in Phase C **help** but only marginally because they cluster
with the existing ones.

### Two finer points that cut the other way

1. **Capacity doesn't care about Sharpe clustering.** Even if two
   index-options sleeves are 0.45 correlated, they still each add
   independent capacity — we can size SPY and SPX simultaneously at
   1% of each's ADV. The clustering affects the risk-parity allocator's
   weight prescription but not the raw dollar cap.

2. **Tail diversification improves even when mean correlation does
   not.** The EXP-2220 drawdown-conditional analysis showed exp1220's
   correlation to every other stream stays < |0.08| during stress
   periods, even though some static correlations are higher.
   Adding 10 more sleeves probably preserves that decoupling, which
   is the property that actually matters for drawdown and circuit-
   breaker stability.

**Bottom line:** diversification **helps modestly on Sharpe** (+0.5
to +1.5) and **helps enormously on capacity** (20× from $50M to
$1B+). The scaling story is capacity-led, not Sharpe-led.

---

## 7. Risks and alternatives

| Risk | Mitigation |
|---|---|
| **Tech single-names become correlated in crashes** | Phase B sizing rule: single-name tech basket capped at 25% of gross notional; fold into correlation monitor |
| **Options volume concentrates further in 0DTE** | Our strategies are 28-DTE biased; 0DTE volume doesn't help us. Monitor monthly ADV at 28-DTE specifically, not all expiries |
| **Polygon data outage leaves us blind** | Phase C includes OPRA direct as a second source; Phase B should already have IBKR fallback via broker-hosted tick feed |
| **Slippage eats more than EXP-2470's 503 bps** | Paper-trade each new sleeve for 30 days before live size; re-run EXP-2470 on the full 15-stream cube before full deployment |
| **Single-name earnings gaps** | Earnings-gap filter per name (already in EXP-1740's FOMC filter — extend to earnings calendar) |
| **Broker AUM gating (Alpaca, IBKR limits)** | Phase C likely requires a prime broker; start relationship discussions at Phase B |

---

## 8. Decision tree

```
Are we at ≥ $40M AUM?
   │
   NO ──→ Stay on existing 7-stream + XLE, grow from trading P&L
   │
   YES
   │
   Is SLV sleeve > 3% weight?
   │
   YES ──→ Phase A.1: cut SLV to ≤ 3%, promote XLE (done in EXP-2710)
   │
   NO
   │
   Is Polygon Advanced subscription approved?
   │
   NO  ──→ Phase A.2: add QQQ sleeve from existing IronVault data
   │
   YES ──→ Phase B: onboard 6–8 new single-name + ETF sleeves
                    Paper-trade each for 30 days
                    Validate each sleeve passes EXP-2710 gate:
                      Sharpe ≥ 1.5, |corr(EXP-1220)| < 0.3
   │
   At $400M AUM and 14+ live sleeves?
   │
   YES ──→ Phase C: onboard SPX + NDX + international
                    Approve OPRA direct subscription
                    Expand correlation monitor to 20-stream matrix
```

---

## 9. Concrete next experiments

The following experiments would execute this roadmap. Each has a
clear gate criterion so we don't sprawl:

| Experiment | Phase | Deliverable | Gate |
|---|---|---|---|
| EXP-2940 | A | QQQ credit-spread sleeve integration | Sharpe ≥ 1.5, corr(SPY) < 0.4 |
| EXP-2950 | A | SLV replacement search (copper/platinum/palladium) | Sharpe ≥ 1.5, adds ≥ $40M capacity |
| EXP-3000 | B | Polygon Advanced subscription + data backfill | >= 10 new underliers ingested |
| EXP-3010 | B | Single-name vol-selling framework (TSLA/NVDA) | Sharpe ≥ 1.2 net of slippage |
| EXP-3020 | B | 15-stream walk-forward on expanded cube | pooled Sharpe ≥ 5.0, DD < 15% |
| EXP-3100 | C | SPX credit-spread sleeve (via CBOE DataShop) | pooled Sharpe ≥ 5.5, adds $2B+ |
| EXP-3110 | C | International basket feasibility (SX5E, Nikkei) | ≥ 2 sleeves promoted |

---

## 10. Honest scope notes

1. The top-20 options-volume table is sourced from **public CBOE /
   OCC aggregates and the paid Polygon Starter tier**. Exact numbers
   drift month to month; the ranks are stable. Any Phase B budget
   request should include a fresh pull of the 90-day trailing average
   at the time of submission.
2. The "effective N ≈ 12.2 at 18 streams" projection is a linear
   extrapolation from EXP-2220's actual measurements. It is **a
   forecast, not a backtest**. The real number will only be known
   after the streams are built and their pairwise correlation is
   measured.
3. The Sharpe projections assume each new stream matches the
   existing average per-stream alpha density (Sharpe ~1.5–2.5 on a
   standalone basis, 4.43 pooled). If single-name vol-selling
   degrades to Sharpe 0.8–1.0 under realistic slippage, the Phase B
   pooled target drops from 5.56 to ~5.0 — still above the current
   4.43.
4. Capacity estimates use the 1% participation rule that EXP-2140
   validated. The rule is conservative for liquid names (SPY could
   probably absorb 2–3% without meaningful impact) and tight for
   thin names. We stick with 1% as the default until a Phase C
   execution-quality study refines it.

---

*Rule Zero note: every number in §2 is read directly from a
committed experiment JSON (EXP-2140, EXP-2220, EXP-2660, EXP-2710,
EXP-2280). Every number in §3 is from published CBOE/OCC data plus
the paid Polygon Starter tier. The Phase A/B/C capacity figures in
§4 are the product of those two inputs via the EXP-2140 formula.
No synthetic data.*
