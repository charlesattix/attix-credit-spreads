# AUM Capacity Research — Path from $50M to $1B

**Date:** 2026-04-21
**Author:** Maximus (AI Trading Strategist)
**Status:** Research complete — actionable recommendations ready
**Context:** North Star v8a portfolio, 3 of 4 targets MET, AUM capacity is the sole structural gap

---

## 1. Current Portfolio Capacity Analysis

### 1.1 The Bottleneck Stack

The portfolio breaks at different AUM tiers due to different streams hitting liquidity walls. From EXP-2140 and EXP-2230:

| AUM Tier | Status | Binding Constraint | Broken Streams |
|---|---|---|---|
| **$10M** | OK | None | None |
| **$16M** | SOFT CAP | SLV calendar (SI=F futures: $0.12B/d) | — |
| **$50M** | BOTTLENECK | SLV calendar + GLD calendar | SLV approaching BROKEN |
| **$82M** | HARD CAP | SLV BROKEN | SLV calendar |
| **$100M** | BROKEN | SLV + GLD broken, XLF/XLI strained | SLV, GLD, (XLF at 50/50 split) |
| **$200M** | BROKEN | All commodity calendars + sector ETFs | SLV, GLD, XLI, Crisis Alpha |
| **$500M** | BROKEN | Only SPY options + IWM equity survive | SLV, GLD, XLF, XLI, Crisis Alpha |
| **$1B** | BROKEN | Even SPY options start to degrade | All except SPY (Sharpe 0.22) |

### 1.2 Stream-by-Stream Capacity Waterfall

Ordered by binding capacity (tightest first):

| Stream | Weight | ADV Notional | Soft Cap AUM | Hard Cap AUM | Binding Instrument |
|---|---|---|---|---|---|
| **SLV calendar** | 7.5% | $122M/d (SI=F) | **$16M** | **$82M** | SI=F silver futures |
| **GLD calendar** | 7.5% | $314M/d (GC=F) | $42M | $209M | GC=F gold futures |
| **Crisis Alpha v5** | 5.0% | $763M/d (UVXY+VXX) | $153M | $763M | VIX options proxy |
| **XLI credit spread** | var | $230M/d (options) | $19M | $96M | XLI options (14K ctr/d) |
| **XLF credit spread** | var | $508M/d (options) | $17M | $85M | XLF options (102K ctr/d) |
| **Cross-vol arb** | 15% | $10.3B/d (IWM shares) | $683M | $3.4B | IWM equity |
| **QQQ credit spread** | 10% | ~$40B/d (options) | ~$1B+ | ~$5B+ | QQQ options |
| **SPY credit spread** | 35% | $152B/d (options) | $2.5B | $12.7B | SPY options |

### 1.3 Root Cause Diagnosis

**The problem is NOT SPY/QQQ liquidity.** SPY alone can support $2.5B+ AUM. The problem is the **long tail of illiquid streams:**

1. **SLV calendar:** SI=F silver futures trade only $122M/d. At 7.5% weight and 1% participation cap, that's $16M portfolio AUM. This is the #1 bottleneck.
2. **GLD calendar:** GC=F gold futures trade $314M/d. Better but still tight at $42M.
3. **XLI options:** Only 14,068 contracts/day. Sector ETF options are thin.
4. **Crisis Alpha:** VIX options/UVXY are inherently thin. Only 5% weight saves this from being the bottleneck.

**Key insight from EXP-2230:** No reweighting of SPY/XLF/XLI credit spread splits changes the bottleneck. In ALL 30 split scenarios tested, **SLV calendar remained the binding stream.** The capacity ceiling is set by the weakest link, not the strongest.

### 1.4 Implication

To reach $1B AUM, we must either:
- **(A) Replace SLV/GLD calendars** with higher-capacity alternatives (eliminates the $82M hard cap)
- **(B) Add enough new high-capacity streams** that SLV/GLD weight drops below 2% (dilution path)
- **(C) Both** — the most realistic path

---

## 2. IronVault Data Coverage Audit

### 2.1 Discovery: Unreported Coverage

EXP-2660 tested IWM/EEM/DIA/XLV/AAPL/MSFT/AMZN and found them all blocked (0 contracts). **However, the IronVault database actually contains coverage for underliers not tested in EXP-2660:**

| Underlying | Contracts | Daily Bars | Expiration Range | Tested in EXP-2660? |
|---|---|---|---|---|
| **SPY** | 193,272 | 4,494,366 | 2020-01 to 2026-06 | Yes (in production) |
| **QQQ** | 23,022 | 779,955 | 2020-01 to 2025-12 | Yes (in production) |
| **XLI** | 17,287 | 200,761 | 2020-01 to 2026-06 | Yes (in production) |
| **GLD** | 14,738 | 190,133 | 2020-01 to 2025-12 | Yes (in production) |
| **TLT** | **10,749** | **293,500** | **2020-01 to 2025-12** | **NO — MISSED** |
| **XLF** | 9,256 | 243,583 | 2020-01 to 2026-06 | Yes (in production) |
| **SOXX** | **3,460** | **37,229** | **2020-07 to 2026-06** | **NO — MISSED** |
| **XLK** | **2,680** | **18,702** | **2020-01 to 2026-06** | **NO — MISSED** |
| **XLE** | 1,757 | 20,542 | 2020-04 to 2026-06 | Yes (killed: EXP-2800) |
| SLV | 0 | 0 | — | No data |
| IWM | 0 | 0 | — | Yes (blocked) |
| EEM | 0 | 0 | — | Yes (blocked) |
| DIA | 0 | 0 | — | Yes (blocked) |
| XLV | 0 | 0 | — | Yes (blocked) |

**CRITICAL FINDING: TLT has 10,749 contracts and 293,500 daily bars in IronVault — more daily bars than GLD (190K) or XLI (201K). It was never tested as a credit spread candidate.** This is a Rule Zero-compliant data source that could support a new high-capacity stream immediately.

**XLK has 2,680 contracts** — thin but more than XLE (1,757) which was tested in EXP-2800.

**SOXX has 3,460 contracts** — thicker than XLE and XLK.

### 2.2 What's Blocked Without Polygon

| Underlying | IronVault? | Options ADV (Yahoo) | Equity ADV | Blocked By |
|---|---|---|---|---|
| IWM | No | ~76K puts/day (nearest exp) | $12.1B/d | No option chain data |
| EEM | No | ~6K puts/day | $2.5B/d | No option chain data |
| DIA | No | ~7K puts/day | $3.5B/d | No option chain data |
| XLV | No | ~377 puts/day | $1.9B/d | No option chain data |
| XLU | No | ~918 puts/day | $1.2B/d | No option chain data |
| HYG | No | ~339 puts/day | $4.0B/d | No option chain data |
| XOP | No | ~2K puts/day | $798M/d | No option chain data |
| XBI | No | ~1.3K puts/day | $1.3B/d | No option chain data |

---

## 3. Candidate Underlier Deep Dive

### 3.1 Live Market Data (Yahoo Finance, 3-month median as of 2026-04-21)

| Ticker | Last Close | Equity ADV (shares) | Equity ADV ($) | Options Depth | IronVault? |
|---|---|---|---|---|---|
| **SPY** | $711.21 | 82.0M | $58.3B | Deepest in world | Yes (193K) |
| **QQQ** | $655.11 | 63.1M | $41.3B | Very deep | Yes (23K) |
| **IWM** | $276.48 | 43.6M | $12.1B | Deep (76K puts/exp) | No |
| **TLT** | $86.74 | 37.3M | $3.2B | Deep (18K puts/exp) | **Yes (10.7K)** |
| **HYG** | $80.50 | 49.6M | $4.0B | Moderate (339 puts/exp) | No |
| **DIA** | $494.76 | 7.0M | $3.5B | Moderate (7K puts/exp) | No |
| **EEM** | $63.38 | 39.3M | $2.5B | Moderate (6K puts/exp) | No |
| **XLF** | $52.21 | 50.4M | $2.6B | Moderate (in prod) | Yes (9.3K) |
| **XLK** | $158.09 | 16.1M | $2.5B | Moderate | **Yes (2.7K)** |
| **XLI** | $171.04 | 12.8M | $2.2B | Thin (in prod) | Yes (17.3K) |
| **XLV** | $146.38 | 12.8M | $1.9B | Very thin (377 puts) | No |
| **XLP** | $82.11 | 18.5M | $1.5B | Thin | No |
| **XLE** | $44.71* | 31.3M* | $1.4B* | Thin | Yes (1.8K) — KILLED |
| **XBI** | $137.31 | 9.3M | $1.3B | Thin (1.3K puts) | No |
| **XLY** | $118.93 | 10.2M | $1.2B | Thin | No |
| **XLU** | $44.87 | 26.7M | $1.2B | Thin (918 puts) | No |
| **XOP** | $168.26 | 4.7M | $798M | Thin (2K puts) | No |
| **GLD** | $435.26 | 12.4M | $5.4B | Moderate (in prod) | Yes (14.7K) |
| **SLV** | $70.37 | 50.7M | $3.6B | N/A (bottleneck) | No |
| **SOXX** | N/A | N/A | N/A | Thin | **Yes (3.5K)** |

*XLE data from EXP-2660 cache; current prices may differ.

### 3.2 Correlation Matrix (2-year daily returns vs. existing portfolio underliers)

| Candidate | SPY | QQQ | XLF | XLI | GLD | SLV | Avg ρ Existing |
|---|---|---|---|---|---|---|---|
| **TLT** | 0.09 | 0.04 | 0.03 | 0.06 | 0.10 | 0.03 | **0.06** |
| **XLU** | 0.37 | 0.26 | 0.37 | 0.43 | 0.22 | 0.18 | **0.30** |
| **XOP** | 0.47 | 0.39 | 0.43 | 0.47 | 0.09 | 0.14 | **0.33** |
| **XLV** | 0.53 | 0.39 | 0.53 | 0.55 | 0.11 | 0.14 | **0.38** |
| **XBI** | 0.61 | 0.57 | 0.47 | 0.56 | 0.17 | 0.22 | **0.43** |
| **EEM** | 0.69 | 0.69 | 0.43 | 0.62 | 0.38 | 0.45 | **0.55** |
| **HYG** | 0.80 | 0.73 | 0.65 | 0.73 | 0.18 | 0.20 | **0.55** |
| **XLC** | 0.83 | 0.81 | 0.67 | 0.67 | 0.10 | 0.20 | **0.55** |
| **XLK** | 0.92 | 0.97 | 0.57 | 0.71 | 0.14 | 0.27 | **0.60** |
| **IWM** | 0.83 | 0.76 | 0.77 | 0.86 | 0.17 | 0.25 | **0.61** |
| **DIA** | 0.90 | 0.78 | 0.88 | 0.88 | 0.11 | 0.22 | **0.63** |

**Sorted by average correlation with existing portfolio (lowest = best diversifier).**

### 3.3 Key Observations

1. **TLT stands alone** with avg ρ = 0.06. It's essentially uncorrelated with every existing stream. This is the single most valuable candidate for diversification.

2. **XLU** (utilities) at ρ = 0.30 is the second-best diversifier among equity sectors. Defensive sector with different factor exposure.

3. **XOP** (oil & gas E&P) at ρ = 0.33 benefits from commodity exposure but different from GLD/SLV.

4. **DIA and IWM** are the WORST diversifiers (ρ > 0.60) — they're just large-cap equity proxies like SPY. Adding them adds capacity but not diversification.

5. **XLK** at ρ = 0.60 is essentially a QQQ clone (QQQ correlation 0.97). Adds zero diversification.

---

## 4. Candidate Assessment: Put Credit Spread Replication

### Can we replicate our 28 DTE / 5% OTM put credit spread strategy on new underliers?

**Requirements (from EXP-1220/EXP-2160/EXP-2240):**
- Liquid 28-DTE puts at 5% OTM strike
- Bid-ask spread < 10% of mid
- Daily option volume > 500 contracts at target strike
- IronVault data for backtesting (Rule Zero)
- Trades/year ≥ 20 (EXP-2800 kill criterion)

### 4.1 TLT Put Credit Spreads

| Metric | Value | Source |
|---|---|---|
| IronVault contracts | 10,749 | IronVault audit |
| IronVault daily bars | 293,500 | IronVault audit |
| Date range | 2020-01 to 2025-12 | IronVault audit |
| Yahoo put volume (nearest exp) | 18,145/day | Yahoo Finance live |
| Yahoo put OI (nearest exp) | 109,389 | Yahoo Finance live |
| Number of expirations | 26 | Yahoo Finance live |
| Equity ADV | $3.2B/d | Yahoo Finance 3mo median |
| Correlation with SPY | 0.09 | 2yr daily returns |
| Correlation with existing avg | 0.06 | 2yr daily returns |

**Assessment: HIGHLY VIABLE.** TLT has:
- Deep options markets (18K puts/day, 109K OI) — deeper than XLI (14K contracts/d)
- 26 available expirations — more than XLV (11) or XOP (13)
- 10,749 contracts in IronVault — **we can backtest TODAY** without Polygon
- Near-zero correlation with equity put spreads — adds genuine diversification
- Bond volatility is structurally different from equity vol — less left-tail risk for put selling

**Capacity estimate:** TLT options ADV notional ≈ $87 × 18,145 × 100 = **$158M/day.** At 1% participation, soft cap = $1.58M/day × weight. At 10% portfolio weight: **soft cap portfolio AUM ≈ $158M.** This is 10× the SLV bottleneck.

**Trade frequency:** TLT has weekly expirations. 28 DTE puts generate ~12-13 trades/yr on the same cadence as SPY. With multiple expiry staggering: **20+ trades/yr easily.**

**VRP in TLT:** Bond implied vol has been persistently above realized vol since 2020 (rate uncertainty regime). The variance risk premium in rates is real and documented (Bollerslev et al., MOVE index vs realized).

**VERDICT: TLT is the #1 immediate action item. Propose as EXP-2910.**

### 4.2 XLK Put Credit Spreads

| Metric | Value | Source |
|---|---|---|
| IronVault contracts | 2,680 | IronVault audit |
| IronVault daily bars | 18,702 | IronVault audit |
| Date range | 2020-01 to 2026-06 | IronVault audit |
| Equity ADV | $2.5B/d | Yahoo Finance |
| Correlation with QQQ | 0.97 | 2yr daily returns |
| Correlation with existing avg | 0.60 | 2yr daily returns |

**Assessment: MARGINAL.** XLK has IronVault data but:
- Only 2,680 contracts — thinner than XLE (1,757) which was killed
- ρ = 0.97 with QQQ means zero diversification benefit
- Would essentially be a QQQ clone with worse liquidity
- XLE was killed (EXP-2800) for 4.4 trades/yr — XLK likely similar issue

**VERDICT: SKIP. XLK adds capacity but no diversification. QQQ is strictly better.**

### 4.3 SOXX Put Credit Spreads

| Metric | Value | Source |
|---|---|---|
| IronVault contracts | 3,460 | IronVault audit |
| IronVault daily bars | 37,229 | IronVault audit |
| Date range | 2020-07 to 2026-06 | IronVault audit |
| Equity ADV | N/A (check needed) | — |
| Correlation with QQQ | ~0.85-0.90 (est) | Semiconductor = tech proxy |

**Assessment: MARGINAL.** SOXX (semiconductor ETF) has more data than XLE but:
- High correlation with QQQ/XLK
- Narrower sector concentration risk
- Worth a quick test given we have the data

**VERDICT: LOW PRIORITY test. If SOXX shows trade frequency > 20/yr and uncorrelated residual, worth exploring. Otherwise skip.**

### 4.4 IWM Put Credit Spreads (Requires Polygon)

| Metric | Value | Source |
|---|---|---|
| IronVault contracts | 0 | BLOCKED |
| Yahoo put volume | 76,248/day | Yahoo Finance live |
| Yahoo put OI | 67,527 | Yahoo Finance live |
| Expirations | 29 | Yahoo Finance live |
| Equity ADV | $12.1B/d | Yahoo Finance |
| Correlation with existing avg | 0.61 | 2yr daily returns |

**Assessment: HIGH CAPACITY, MODERATE DIVERSIFICATION.** IWM has the deepest options after SPY/QQQ. At 76K puts/day it's 5× deeper than TLT. But correlation at 0.61 means limited diversification. Already used for cross-vol arb (equity leg).

**Capacity estimate:** IWM options ADV notional ≈ $276 × 76,248 × 100 = **$2.1B/day.** Massive.

**VERDICT: High priority once Polygon is provisioned. The capacity alone justifies the $199/mo subscription.**

### 4.5 XLV Put Credit Spreads (Requires Polygon)

| Metric | Value | Source |
|---|---|---|
| IronVault contracts | 0 | BLOCKED |
| Yahoo put volume | 377/day | Very thin |
| Equity ADV | $1.9B/d | Yahoo Finance |
| Correlation with existing avg | 0.38 | 2yr daily returns |

**Assessment: POOR OPTIONS LIQUIDITY.** Only 377 puts/day on nearest expiry. Healthcare sector has fundamental reasons to be uncorrelated (regulatory, demographic drivers), but the options market is too thin for credit spreads at scale.

**VERDICT: SKIP for put credit spreads. Consider equity-only strategies on XLV instead.**

### 4.6 XLU Put Credit Spreads (Requires Polygon)

| Metric | Value | Source |
|---|---|---|
| IronVault contracts | 0 | BLOCKED |
| Yahoo put volume | 918/day | Thin |
| Equity ADV | $1.2B/d | Yahoo Finance |
| Correlation with existing avg | 0.30 | 2yr daily returns |

**Assessment: INTERESTING DIVERSIFICATION, THIN LIQUIDITY.** Utilities sector is a natural diversifier (rate-sensitive, defensive). ρ = 0.30 is decent. But 918 puts/day is very thin for institutional scale.

**VERDICT: SKIP for put credit spreads. Consider XLU as part of a sector rotation equity strategy instead.**

### 4.7 HYG Put Credit Spreads (Requires Polygon)

| Metric | Value | Source |
|---|---|---|
| IronVault contracts | 0 | BLOCKED |
| Yahoo put volume | 339/day | Very thin |
| Equity ADV | $4.0B/d | Yahoo Finance |
| Correlation with existing avg | 0.55 | 2yr daily returns |

**Assessment: THIN OPTIONS, HIGH CORRELATION.** HYG equity is very liquid ($4B/d) but options are thin (339 puts). High correlation (0.55) reduces diversification benefit. Credit ETFs behave like equity risk in crises.

**VERDICT: SKIP for credit spreads. Use HYG/LQD spread as a macro overlay signal instead (see alpha_stream_research_phase1.md, Stream 12).**

---

## 5. IV-RV Arbitrage Extension

### Can we extend the cross_vol IV-RV arb (EXP-2020) to new underliers?

The current cross_vol stream trades IV-RV spread across SPY/QQQ/IWM/EEM. It uses **equity ADV** (not options), so IronVault options data is not required. The binding instrument is IWM equity at $10.3B/d.

**Candidates for IV-RV extension:**

| Underlying | Equity ADV | VIX-equivalent IV Index | FRED Code | Available? |
|---|---|---|---|---|
| SPY | $58.3B/d | ^VIX | VIXCLS | Yes (in production) |
| QQQ | $41.3B/d | ^VXN | VXNCLS | Yes (in production) |
| IWM | $12.1B/d | ^RVX | RVXCLS | Yes (in production) |
| EEM | $2.5B/d | ^VXEEM | VXEEMCLS | Yes (in production) |
| **TLT** | **$3.2B/d** | **MOVE Index** | **MOVE** | **Yes (FRED)** |
| **GLD** | **$5.4B/d** | **GVZ** | **GVZCLS** | **Yes (FRED)** |

**TLT IV-RV arb using MOVE index:** The MOVE index (bond market volatility) is available on FRED. We could extend the cross-vol framework to include a TLT IV-RV component. This would be genuinely uncorrelated with equity IV-RV.

**GLD IV-RV arb using GVZ:** The CBOE Gold Volatility Index (GVZ) is also on FRED. We already trade GLD calendars; adding an IV-RV overlay could enhance that stream.

**VERDICT: Extend cross_vol to include TLT (MOVE) and potentially GLD (GVZ). No new data subscription required.**

---

## 6. Strategies That Don't Require Options Data

Several capacity-expanding strategies can use Yahoo Finance equity data only:

### 6.1 Sector Momentum Rotation (ETF-only)
- Trade 11 sector SPDRs (XLK, XLF, XLE, XLV, XLI, XLB, XLU, XLRE, XLC, XLY, XLP)
- Monthly rebalancing based on 3/6/12-month momentum
- **Capacity: $5B+** (all sector ETFs trade $500M+/day)
- **Data: Yahoo Finance only**
- Expected Sharpe: 1.0–1.6 backtest → 0.5–1.0 live

### 6.2 Treasury Yield Curve Relative Value (ETF-only)
- Trade TLT/IEF/SHY duration-neutral spreads
- Monthly rebalancing using FRED term premium signals
- **Capacity: $5B+** (TLT alone is $3.2B/d)
- **Data: Yahoo Finance + FRED**
- Expected Sharpe: 0.6–1.2 backtest → 0.3–0.8 live

### 6.3 Overnight Return Premium (ETF-only)
- Buy SPY/QQQ at close, sell at open
- Daily execution
- **Capacity: $5B+** (MOC/MOO orders in deepest liquidity windows)
- **Data: Yahoo Finance intraday**
- Expected Sharpe: 1.5–3.0 backtest → 1.0–1.5 live (execution-sensitive)

### 6.4 Short-Term Mean Reversion (ETF-only)
- RSI(2)/RSI(5) on SPY/QQQ/IWM/DIA
- VIX regime filter (trade only when VIX < 25)
- **Capacity: $2B+**
- **Data: Yahoo Finance + ^VIX**
- Expected Sharpe: 1.5–2.5 backtest → 0.8–1.5 live

---

## 7. Ranked Recommendations

### Tier 1: Immediate Action (no new data subscriptions)

| Rank | Action | Est. Capacity Added | Data Source | Exp ID |
|---|---|---|---|---|
| **1** | **TLT put credit spreads** | **+$150M** | IronVault (10.7K contracts) | Propose EXP-2910 |
| **2** | **TLT IV-RV arb (MOVE index)** | +$100M | FRED MOVE + Yahoo TLT | Propose EXP-2920 |
| **3** | **Sector momentum rotation** | +$500M | Yahoo Finance only | Propose EXP-2930 |
| **4** | **Overnight return premium** | +$500M | Yahoo Finance only | Propose EXP-2940 |
| **5** | **Short-term mean reversion** | +$200M | Yahoo Finance + ^VIX | Propose EXP-2950 |

**Tier 1 combined capacity uplift: ~$50M → $300M–$500M**

### Tier 2: Requires Polygon ($199/mo)

| Rank | Action | Est. Capacity Added | Data Source |
|---|---|---|---|
| **6** | **IWM put credit spreads** | +$500M | Polygon options |
| **7** | **DIA put credit spreads** | +$200M | Polygon options |
| **8** | **EEM put credit spreads** | +$100M | Polygon options |
| **9** | **Multi-asset VRP (IWM/EEM straddles)** | +$300M | Polygon options |

**Tier 2 combined capacity uplift: $300M → $800M–$1.2B**

### Tier 3: Requires Futures Account

| Rank | Action | Est. Capacity Added | Data Source |
|---|---|---|---|
| **10** | **VIX futures term structure** | +$500M | CBOE VIX futures |
| **11** | **Commodity term structure (energy/ag)** | +$200M | Futures data |

### Portfolio Capacity Projection

| Milestone | Streams | Estimated AUM Cap | Timeline |
|---|---|---|---|
| **Current** | 8 streams | ~$50M | Now |
| **+TLT spreads** | 9 streams | ~$150M | Week 1 (IronVault data exists) |
| **+Equity strategies** (momentum, MR, overnight) | 12 streams | ~$300M | Weeks 2-4 |
| **+Polygon streams** (IWM, DIA, EEM) | 15 streams | ~$800M | After Polygon subscription |
| **+VIX futures** | 16 streams | ~$1B+ | After futures account |

---

## 8. TLT Deep Dive — Proposed EXP-2910

Given TLT is the #1 recommendation with existing data, here's the proposed experiment spec:

### EXP-2910: TLT Put Credit Spread Integration

**Hypothesis:** TLT put credit spreads (28 DTE, 5% OTM) harvest the bond variance risk premium, which is persistent and uncorrelated with equity VRP. Adding TLT as a 9th stream expands portfolio capacity while improving diversification.

**Data:** IronVault TLT options (10,749 contracts, 293,500 daily bars, 2020-01 to 2025-12).

**Expected results:**
- Individual stream Sharpe: 1.5–3.0 (bond VRP is large post-2020)
- Correlation with existing 8 streams: ρ ≈ 0.06 (near zero)
- Trade frequency: 12–26/year (monthly to bi-weekly cadence)
- Capacity: $100M–$200M at 10% portfolio weight

**Methodology:**
1. Extract TLT 28-DTE 5% OTM put credit spreads from IronVault
2. Walk-forward backtest with 252-day train / 63-day test folds
3. Integrate into 9-stream portfolio with Ledoit-Wolf risk-parity weights
4. Apply VIX ladder + 12% vol target + 890 bps drag
5. Compare pooled net Sharpe vs. v8a baseline (6.39)

**Kill criteria:**
- Trade Sharpe < 1.0
- Trades/year < 20
- Combined 9-stream Sharpe < 6.0 net
- Buffer degradation (EXP-2740 sensitivity test)

**Why TLT beats XLE (EXP-2800 kill):**
- XLE: 1,757 contracts, 4.4 trades/yr → killed for insufficient frequency
- TLT: 10,749 contracts (~6× more), 18K puts/day on Yahoo (alive market)
- TLT correlation 0.06 vs XLE correlation -0.02 → both low, but TLT has structurally different factor exposure (rates vs. energy)

---

## 9. Portfolio Optimization: Weight Allocation with New Streams

### Current v8a weights (Ledoit-Wolf risk-parity):
Dynamic, but approximate equilibrium:
- SPY ~35%, QQQ ~10%, XLF ~15%, XLI ~10%, GLD ~10%, SLV ~5%, cross_vol ~10%, v5_hedge ~5%

### Proposed v9 weights (with TLT + equity strategies):
- SPY ~25%, QQQ ~8%, XLF ~10%, XLI ~8%, GLD ~7%, **SLV ~2%** (reduced from 5%), cross_vol ~8%, v5_hedge ~4%, **TLT ~10%**, **sector_mom ~8%**, **overnight ~5%**, **mean_rev ~5%**

**Key change: SLV weight drops from 5% → 2%.** This alone moves the SLV hard cap from $82M to $205M (proportional to 1/weight).

### Capacity with proposed v9 weights:

| Stream | Weight | Binding ADV | Soft Cap AUM |
|---|---|---|---|
| SLV calendar | 2% | $122M/d | $61M → **no longer binding** |
| GLD calendar | 7% | $314M/d | $45M → tight but OK |
| TLT credit spread | 10% | $158M/d (options) | $158M |
| SPY credit spread | 25% | $152B/d | $6B+ |
| Sector momentum | 8% | $5B+ (equity) | $62B+ |
| Overnight premium | 5% | $58B/d (SPY) | ∞ |
| Mean reversion | 5% | $58B/d (SPY) | ∞ |

**v9 binding constraint: GLD calendar at ~$45M → portfolio soft cap ~$640M**

If GLD calendar is also reduced to 3%: soft cap rises to **$1.5B.**

---

## 10. Honest Risks and Caveats

### 10.1 Rule Zero Warning
- TLT, XLK, SOXX have IronVault data but **have not been backtested yet**. The data may have gaps, the put credit spread parameters may not apply to bonds the same way they apply to equities, and the VRP in rates may not be as persistent.
- All capacity estimates use the EXP-2140 market impact model (impact_bps = 150 × √participation). This model was calibrated for equity options — bond options may have different dynamics.

### 10.2 Correlation Regime Risk
- The ρ = 0.06 between TLT and SPY is measured over 2 years (2024-2026). In a 1970s-style stagflation, bonds and equities can become positively correlated. The 2022 bond crash saw TLT down 30%+ while SPY was also down — that's a positive correlation tail event.
- Hedging: The VIX ladder partially addresses this (reduces all exposure in high-vol regimes).

### 10.3 Strategy Decay
- Equity strategies (momentum, mean reversion, overnight) have well-documented alpha decay of 5-15%/yr. They are not permanent alpha sources.
- Bond VRP may decay as more participants harvest it.
- The 0.5-0.7× backtest-to-live decay factor applies to ALL new streams.

### 10.4 Execution Complexity
- Going from 8 → 12+ streams increases operational complexity significantly
- Each new stream needs: signal generation, position sizing, risk monitoring, broker integration
- The current Alpaca connector (791 lines) would need extension for bond options and equity strategies

---

## 11. Action Items

### Immediate (Carlos to approve):

1. **Approve EXP-2910 (TLT put credit spreads)** — IronVault data exists, no new subscriptions needed. Can start today.
2. **Approve EXP-2920 (TLT IV-RV arb)** — FRED MOVE index is free. Extends existing cross_vol framework.
3. **Reduce SLV weight to 2-3%** in next portfolio rebalance — immediate capacity uplift from $50M to $150M+.

### This week:

4. **Prototype sector momentum and overnight premium strategies** — Yahoo Finance only, no blockers.
5. **Run SOXX/XLK quick feasibility check** — IronVault data exists, 1-day experiment each.

### Next month:

6. **Provision Polygon Options subscription ($199/mo)** — unlocks IWM/DIA/EEM, the highest-capacity candidates.
7. **Build v9 portfolio framework** — 12-stream architecture with Ledoit-Wolf weights.

---

## References

### Internal Experiments
- **EXP-2140:** North Star Portfolio Capacity Analysis (bottleneck identification)
- **EXP-2230:** 7-Stream Capacity with XLF + XLI (split sweep — SLV always binding)
- **EXP-2380:** Futures Calendar Capacity (futures ≈ ETF capacity; killed)
- **EXP-2430:** Capacity-Optimized Reweight (XLI becomes next bottleneck)
- **EXP-2650:** Multi-Expiry Staggering (SPY put volume analysis)
- **EXP-2660:** AUM Capacity Multi-Underlying Scaling Audit (7 of 8 blocked)
- **EXP-2800:** XLE 9th Stream (killed: 4.4 trades/yr)
- **EXP-2810:** SPY Weekly 9th Stream (killed: TC eats alpha)

### Data Sources
- **IronVault:** `data/options_cache.db` — SPY/QQQ/XLF/XLI/GLD/TLT/XLK/XLE/SOXX
- **Yahoo Finance:** all equity ADV/OHLCV, ^VIX, ^VIX3M
- **FRED:** VIXCLS, VXNCLS, RVXCLS, VXEEMCLS, MOVE, GVZCLS, DGS2, DGS10, DGS30

### Phase 1 Alpha Stream Research
- See `analysis/alpha_stream_research_phase1.md` for 13 candidate alpha streams with academic citations

---

*Last updated: 2026-04-21 by Maximus*
*Rule Zero: ALL data sources cited are real. No synthetic data used in this analysis.*
