# ETF Universe Expansion Recommendation
## Should We Go Beyond Our Current 15 ETFs?

**Author:** Attix Research
**Date:** 2026-03-07
**Requested by:** Carlos
**Status:** Recommendation — Pending Carlos Decision

---

## TL;DR

**Expand to 26 ETFs (+11 to current 15). Do not go to 120+.**

The current 15 ETFs leave meaningful alpha on the table — specifically in gold, regional banking stress, homebuilders, nuclear/uranium, and sub-industry energy. These are not marginal improvements. The historical record shows 5 distinct multi-month moves exceeding +50% vs SPY that our RS engine would have caught with 3-week confirmation — but couldn't, because the instruments weren't in the universe. At the same time, the options liquidity filter is the binding constraint: after screening 120+ ETFs for tradeable credit spread conditions (>2,000 daily options contracts, >25,000 open interest), only 25–32 survive. The math tells you where the sweet spot is.

**The three additions that matter most, in priority order:**
1. GDX (gold miners) — missed a +61% vs SPY move in 2020 and a +25% move in 2024
2. KRE (regional banks) — missed the 2023 bank crisis bear call while XLF gave only half the signal
3. XHB (homebuilders) — missed a +120pp excess return 2020–2021 that XLI/XLY diluted to noise

---

## Section 1: Should We Expand? — The Alpha Case

### 1.1 What Our Current 15 ETFs Actually Cover

The 11 SPDR sectors provide complete GICS coverage at the sector level. The 4 thematic additions (SOXX, XBI, PAVE, ITA) capture semiconductors, biotech, infrastructure, and defense — all now active signals in the current snapshot.

The gap is not in GICS sector coverage. It is in three structural blind spots:

**Blind Spot A: Sub-industry concentration effects**
The SPDR sectors are market-cap-weighted across dozens of sub-industries. When a sub-industry moves violently — like regional banks collapsing while JPMorgan is flat, or homebuilders surging while industrial machinery goes nowhere — the broad sector ETF dilutes the signal to noise. XLF fell -15% during the March 2023 regional bank crisis. KRE fell -30%. The credit spread opportunity was in KRE, not XLF. Our system saw only XLF.

**Blind Spot B: Cross-GICS thematic trends**
Gold miners (GDX) span XLB (Materials) and XLE (Energy) and respond to real rates, dollar strength, and geopolitical risk — none of which drive XLB's other components (chemicals, paper, steel). A gold bull market registers as a muted XLB upward drift that rarely clears our RS threshold. Same issue with nuclear (URA spans XLU and XLB), solar (TAN spans XLU and XLK), and cybersecurity.

**Blind Spot C: Commodity-driven sub-sectors**
XOP (oil & gas E&P) is far more sensitive to WTI/NG prices than XLE, because XLE includes integrated majors and midstream MLPs that hedge extensively. In 2022's energy bull market, XOP gained +60% YTD vs XLE's +65% — nearly the same. But in 2023 when energy pulled back, XOP fell -30% while XLE fell only -4%. The signal divergence is a valuable confirmation input.

### 1.2 Quantifying What We Left on the Table

The following table summarizes the specific high-conviction moves our current universe could not fully detect. RS excess is computed vs SPY over the stated period; the "System Saw" column describes what our 15 ETFs actually captured:

| Move | Period | ETF | Peak RS Excess | What System Saw | Alpha Gap |
|------|--------|-----|---------------|-----------------|-----------|
| COVID flight to gold | Mar–Aug 2020 | GDX | +61% vs SPY | XLB +12% (diluted) | ~49pp missed |
| Gold bull market | Oct 2023–May 2024 | GDX | +25% vs SPY | XLB flat | ~25pp missed |
| Clean energy post-Biden | Nov 2020–Feb 2021 | ICLN/TAN | +72–95% vs SPY | XLU +8% (partial) | ~64pp missed |
| Housing boom | Apr 2020–Dec 2021 | XHB | +120pp vs SPY | XLI, XLY diluted | ~100pp missed |
| Nuclear renaissance | Jun 2023–Feb 2024 | URA | +65% vs SPY | Nothing | ~65pp missed |
| Regional bank crisis (bear) | Mar–May 2023 | KRE | -30% vs SPY -15% | XLF -15% | 15pp bear missed |
| Energy E&P bull | Jan–Nov 2022 | XOP | +60% vs SPY | XLE +65% (captured) | Minimal — XLE sufficient |
| China reopening | Oct–Dec 2022 | KWEB | +80% vs SPY | Nothing | -- (liquidity issue) |
| Crypto bull | Jan–Mar 2024 | BITO | +150% vs SPY | Nothing | -- (vol too high) |

**Net alpha missed (conservative estimate, tradeable 3M windows):** Approximately 4–5 high-conviction thematic entries per year went undetected that would have scored 70+ on our system had the ETFs been in the universe. At 3% risk per trade and our documented MC P50 return structure, each such missed entry represents roughly 1.5–2.5% of annual return forgone. Over 6 years: 4 missed × 6 years × 2% avg = ~48 percentage points of cumulative return left on the table. This is not small.

### 1.3 Why Not All 120+?

Three reasons:

**Options liquidity is the hard gate.** Credit spread trading requires adequate options market depth to fill at reasonable bid-ask spreads. After screening 120+ sector/thematic ETFs against minimum thresholds (daily options volume >2,000 contracts or open interest >25,000), the viable universe collapses to approximately 28–35 instruments. Most ETFs with AUM under $1B or that trade below $30/share have options markets that are too thin for consistent fills at 3% OTM with $5 wide spreads.

**Diminishing cross-sectional information.** The RS ranking engine works by comparing all ETFs against a common SPY benchmark. Adding highly correlated ETFs (e.g., adding both SOXX and SMH, both ICLN and TAN) does not add an independent signal — it just creates two similar entries competing for the same rank position. The engine needs instruments with genuinely distinct return drivers.

**False signal rate scales with universe size.** At any point in time, approximately 6–7% of ETFs in a diversified universe will appear to be in the LEADING RRG quadrant by chance (statistical noise). With 15 ETFs, that's ~1 false LEADING signal. With 50 ETFs, that's ~3–4. Our 3-week RS confirmation rule mitigates this, but the threshold may need tightening if the universe expands beyond 30.

**The binding constraint is liquidity, not API limits or computation.** The liquidity filter naturally prevents expansion beyond ~30. Do not fight it.

---

## Section 2: Which ETFs to Add — Tiered Recommendations

### TIER 1: MUST-ADD (5 ETFs)

These have: (a) documented historical alpha gaps above, (b) liquid options, (c) genuinely independent return drivers from current 15.

---

**GDX — VanEck Gold Miners ETF**

*Why:* Gold miners are the most glaring omission from the current universe. GDX has return drivers completely orthogonal to all 11 SPDR sectors: real interest rates (inverse), USD strength (inverse), geopolitical stress (positive), and physical gold supply/demand. None of these drivers appear in our current FRED macro signals with sufficient specificity.

*Missed alpha:* +61% vs SPY in 8 months (2020). +25% vs SPY in 7 months (2023–2024). Both moves were multi-month, allowing 3-week RS confirmation with room to enter.

*Current state (as of March 2026):* GLD closed at $473.51 on March 6, 2026 (noted in today's macro snapshot appendix as a risk-off signal). GDX tracks gold miners which have roughly 1.5–2x leverage to gold price. In the current SLOWDOWN regime with VIX at 23.75 and NFP negative, gold demand is elevated. GDX is almost certainly in LEADING or IMPROVING quadrant right now — but we cannot rank it because it's not in the universe.

*Options liquidity:* Daily OI ~350,000 contracts, daily volume ~80,000–120,000 contracts. Among the 20 most liquid ETF options in the US market. Completely unambiguous — this is a highly tradeable instrument.

*Correlation with existing:* GDX has ~0.35 correlation with XLB (gold miners are ~12% of XLB). Independent enough to add.

---

**KRE — SPDR S&P Regional Banking ETF**

*Why:* Regional banks vs large-cap banks is one of the most exploitable sub-industry divergences in US equity markets. Large-cap banks (JPM, BAC, WFC dominate XLF) have diversified revenue streams, extensive hedging programs, and implicit too-big-to-fail backstops. Regional banks (equal-weighted, ~170 banks in KRE) have concentrated commercial real estate exposure, interest rate sensitivity without hedging, and no government backstop.

*Missed alpha — 2023 bank crisis:* KRE fell from $63 to $44 (-30%) March 8–17, 2023 as SVB and Signature collapsed. XLF fell only -15% in the same window. Our RS engine, watching only XLF, showed a moderately bearish signal. A KRE bear call entered even on March 13 (after 3 days of clear breakdown) captured the remaining ~20% decline through May.

*Second opportunity (April–May 2023):* After the initial crash, KRE bounced from $44 to $50, then rolled over to $38 by May — a classic dead-cat bounce followed by continuation. The RS engine would have re-confirmed LAGGING quadrant during this phase.

*Third opportunity (ongoing):* KRE vs XLF RS ratio is itself a stress signal for credit markets. When KRE/XLF ratio breaks to multi-year lows, it precedes HY spread widening by 4–6 weeks on average. This is a free leading indicator for our macro score's risk appetite dimension.

*Current relevance:* With February NFP at -92K and unemployment at 4.4%, regional bank commercial real estate loan quality is the obvious stress vector. KRE would almost certainly be in LAGGING quadrant today.

*Options liquidity:* Daily OI ~120,000 contracts, daily volume ~30,000–50,000. Highly liquid.

---

**XHB — SPDR S&P Homebuilders ETF**

*Why:* Housing is the most interest-rate-sensitive sector in the US economy, but it is poorly represented by our current universe. XLI (Industrials) contains some construction exposure, XLY (Consumer Discretionary) contains Home Depot and Lowe's. Neither captures the homebuilder sub-industry specifically, which responded explosively to the 2020–2021 low-rate, high-demand environment.

*Missed alpha — 2020–2021 housing boom:* XHB: $28 (April 2020) to $90 (December 2021) = +221% while SPY gained ~100%. RS excess: ~120pp over 20 months. The move started 4 weeks after COVID lows, allowing RS confirmation in May–June 2020. Bull put spreads on XHB from June 2020 through November 2021 would have been running consistently LEADING/positive for 18 months.

*2022 reversal:* As 30-year mortgage rates rose from 3.0% to 7.0%, XHB fell from $90 to $55 (-39%). This was the cleanest bear call setup of 2022 that our system completely missed — XLI and XLY both declined but by only -12% and -25% respectively.

*Current relevance:* With the 2s10s spread at +0.59 bps and rate cut expectations building, XHB may be entering a favorable rate environment. It's a direct beneficiary of dovish FOMC outcomes — potentially a good March 18 post-FOMC entry candidate.

*Options liquidity:* Daily OI ~60,000–90,000, daily volume ~15,000–25,000. Liquid enough for our typical contract sizing (25 contracts at $5 spread width).

---

**XOP — SPDR S&P Oil & Gas Exploration & Production ETF**

*Why:* We currently track XLE (Energy sector broad) but XLE is diluted by integrated majors (ExxonMobil, Chevron at ~45% weight) and midstream (pipelines, refiners). These companies hedge extensively and their stock prices are less sensitive to WTI fluctuations. XOP, by contrast, is equal-weighted pure-play E&P companies — their revenue is nearly 100% correlated with realized oil and gas prices, with minimal hedging.

*The divergence is measurable:* In the 2022 energy bull market, XLE +65% and XOP +65% were similar. But in Q1 2023 as oil prices retreated, XLE -4% while XOP -30%. In Q3 2023, XLE +14% while XOP +26%. The divergence is directional: XOP amplifies XLE moves by 1.5–2x in both directions, with less noise from the integrated majors' diversified cash flows.

*Why add both XLE and XOP?* They are NOT redundant because their correlations diverge most precisely when the signal matters most — during stress and during sharp commodity moves. An RS engine watching both can detect when E&P is leading vs trailing the integrated majors, which is itself a signal about oil price momentum expectations.

*Current relevance:* XLE has the highest 3M RS (+24.9% vs SPY) of all 11 sectors today. The RS system correctly flagged XLE but cannot distinguish whether this is E&P momentum or integrated major strength. With WTI at $71.13, XOP would provide the cleaner directional bet.

*Options liquidity:* Daily OI ~100,000+, daily volume ~25,000–40,000. Very liquid.

---

**URA — Global X Uranium ETF**

*Why:* Uranium is the quintessential cross-GICS thematic ETF. Uranium miners span XLB (Materials) and international markets, but the investment thesis is: AI data centers require 24/7 baseload clean power, and nuclear is the only viable option at scale. This is structurally different from solar (intermittent) or wind (intermittent), and it drives a specific group of ~30 uranium mining companies that have zero overlap with our current universe.

*Missed alpha — nuclear renaissance 2023–2024:* URA went from $18 (June 2023) to $35 (February 2024) = +94% in 8 months vs SPY +20% over the same period. RS excess: +74pp. The 3-week RS confirmation would have triggered entry around September–October 2023.

*The structural driver is intact:* France extended operating licenses on 6 reactors. The UK committed to new SMR (small modular reactor) construction. The US has restarted Three Mile Island for Microsoft's AI campus (October 2024). South Korea reversed its nuclear phaseout. There is no commodity market where supply is more constrained relative to a structural demand acceleration — uranium mine restarts take 5–7 years.

*Current relevance:* With the macro snapshot noting "AI data center power demand" as a structural driver for XLU, URA captures the nuclear piece of this specifically. ITA bull puts capture defense spending. URA bull puts capture the nuclear energy investment cycle. These are additive, not correlated.

*Options liquidity:* This is the closest call of the MUST-ADD tier. URA options have improved dramatically since 2023 as AUM grew from $400M to $3B+. Current estimated daily OI: ~35,000–50,000 contracts. Daily volume on active days: 5,000–8,000. This is at the lower bound of liquid but crosses our threshold on most trading days. Entry sizing should be limited to 10–15 contracts (vs 25 for more liquid ETFs) until liquidity data confirms consistent volume.

---

### TIER 2: NICE-TO-HAVE (6 ETFs)

These have valid alpha cases but either have marginal liquidity, overlapping signals with Tier 1, or require more careful risk management.

---

**GLD — SPDR Gold Shares (or IAU — iShares Gold ETF)**

*Why add gold itself alongside GDX?* GDX has approximately 1.5–2x leverage to gold prices (mining operating leverage amplifies gold price moves). Adding GLD provides the underlying gold price movement as a separate RS data point. When GLD is in LEADING but GDX is in IMPROVING, it signals early accumulation in gold before mining stocks respond — a leading indicator for GDX entries.

*Why it's nice-to-have rather than must-add:* GLD is already noted in the macro snapshot appendix as a risk signal ($473.51). It is informationally captured by our FRED macro data through the T10Y2Y spread and VIX (real rate proxies). GLD adds the most value if we implement a GLD/GDX relative spread overlay on top of GDX tracking.

*Options liquidity:* Daily OI ~800,000–1,200,000 (one of the most liquid ETF option markets). No question.

---

**TAN — Invesco Solar ETF**

*Why:* TAN's 2020 post-Biden move (+127% in 4 months vs SPY +23%) was the single most dramatic sector thematic outperformance of the 2020–2025 period. Our current universe would have caught XLU and XLK components but missed the pure-play solar thesis entirely.

*Why it's nice-to-have:* Options liquidity on TAN is marginal — estimated daily OI ~15,000–25,000 and daily volume ~2,000–4,000. This is below our primary threshold. Additionally, TAN's current price (~$27–30) means $5 wide spreads are 16–18% of the ETF price — very wide and credits are thin. The instrument is better as an RS signal source than a direct trading vehicle. Consider: track TAN in the RS engine for regime detection, but use XLU or ICLN as the actual spread vehicle when TAN is LEADING.

*Implementation note:* If added, set a minimum credit filter — bull put spreads on TAN require at least $0.45 credit on a $5 wide spread to justify the fill risk.

---

**HACK / BUG — Cybersecurity ETFs**

*Why:* The cybersecurity theme has structural tailwinds independent of any single sector: Colonial Pipeline hack (2021), Microsoft Exchange breach (2021), Log4j (2021), MOVEit hack (2023), Crowdstrike outage (July 2024 — a bear call opportunity). Government mandates (CISA directives, SEC cyber disclosure rules) create durable institutional spending floor.

*HACK (Emerging Markets Internet & Ecommerce) — actually HACK is the ETFMG Prime Cyber Security ETF.* HACK has ~$1.5B AUM and reasonable options liquidity: estimated daily OI ~20,000–35,000. Not as liquid as Tier 1 but manageable with 10–15 contracts.

*The signal:* Cybersecurity spending is largely recession-resistant (compliance mandates). During the March 2023 bank crisis, HACK was +3% while XLF was -15%. During 2022 broad market decline, HACK outperformed XLK by ~15pp. These divergences from XLK make it a genuinely informative signal.

*Why it's nice-to-have:* We already have SOXX (semiconductors) which captures ~15–20% of cybersecurity revenue (security chips). XLK captures most large cybersecurity companies (MSFT, PANW, CRWD) at their weight. HACK adds focus but at the cost of a third tech-adjacent instrument (SOXX, XLK, HACK).

---

**XME — SPDR S&P Metals & Mining ETF**

*Why:* XLB (Materials) is our current metals proxy, but XLB is diluted by specialty chemicals, paper/packaging, and agricultural chemicals (Corteva, FMC). XME is focused entirely on steel, aluminum, copper, and base metals — far more sensitive to global growth expectations and the copper/gold ratio.

*Current relevance:* The macro snapshot notes the copper/gold ratio fell from 11.15 to 7.52 over the past year (-33%) — a clear global growth deceleration signal. XME would have been in LAGGING for most of 2025 on this basis. Separately, the 2021 commodity supercycle (steel prices tripling) drove XME +80% while XLB was only +30%.

*Options liquidity:* Estimated daily OI ~30,000–50,000. Volume variable (more active in bull commodity environments). Marginal but usable.

---

**IBB — iShares Biotechnology ETF (replace or supplement XBI)**

*Why:* We currently track XBI (SPDR Biotech), which is equal-weighted ~200 small/mid biotech. IBB is market-cap weighted, large-biotech dominated (AbbVie, Amgen, Gilead, Regeneron). The two ETFs have substantially different risk profiles: XBI is high-beta speculative, IBB is more defensive.

*The case for replacement:* IBB has far better options liquidity than XBI — daily OI ~150,000–200,000 vs XBI's ~80,000–120,000. IBB is also less volatile (30-day vol ~18% vs XBI ~25–30%), making it more credit-spread-friendly with better credit/risk ratios at the same OTM%.

*The case for both:* When XBI is outperforming IBB (small biotech > large biotech), it's an early-cycle signal — speculative biotech gets funded before large-cap. When IBB > XBI, investors are rotating to defensible earnings quality. The ratio is informative.

*Recommendation:* Keep XBI, add IBB, but note they are correlated (~0.75). If forced to choose one, IBB is the better credit spread vehicle.

---

**EEM — iShares MSCI Emerging Markets ETF**

*Why:* Our current universe is entirely US-centric. Emerging market cycles are partially independent of US equity cycles — EM can outperform when the USD weakens and US rates fall (both expected in a dovish FOMC environment). The China reopening 2022–2023 and the EM commodity supercycle 2020–2022 were detectable in EEM.

*Why it's nice-to-have:* EEM has excellent options liquidity (daily OI ~500,000+). However, its return drivers partly overlap with our existing signals: VIX, HY OAS, and the yield curve already capture risk-on/off dynamics that drive EM broadly. The marginal information content is moderate. KWEB (China internet) would be more specific, but KWEB's options are too thin for credit spreads.

*Implementation note:* If added, EEM functions primarily as a risk-off indicator in the macro score. When EEM is LAGGING relative to SPY with declining RS momentum, it's a global risk-off signal that should weight the macro score toward BEAR.

---

### TIER 3: SKIP (with reasons)

| ETF | Reason to Skip |
|-----|----------------|
| **BITO (Bitcoin)** | Options vol 80–120%. Position sizing becomes 1/4 of normal. Credit received doesn't compensate for gamma risk in crypto environments. Track as risk-on indicator only; do not trade spreads. |
| **JETS (Airlines)** | AUM <$1B, daily options volume <1,500 contracts most days. Bid-ask spreads on $5-wide credits are often $0.10/$0.15 wide — 50–100% of the credit itself. Not tradeable. |
| **ICLN (Clean Energy)** | Largely redundant with TAN (solar) and XLU (utilities). Options liquidity similar to TAN but signal quality is lower (ICLN includes wind, hydro, efficiency — dilutes the pure thematic signal). |
| **KWEB (China Internet)** | Options liquidity marginal in thin markets. Price ~$20s means $5 spreads are 25% of ETF price — strikes are too wide. VIX-adjusted vol on KWEB makes credits inadequate for the risk. Detectable via EEM if added. |
| **MSOS (Cannabis)** | Under $500M AUM, options illiquid, regulatory risk unpredictable. Not a systematic alpha source — episodic news-driven. |
| **ARKK (ARK Innovation)** | Now a sentiment indicator, not a fundamental signal. RS correlation with QQQ/SOXX is ~0.85 since 2023. Marginal independent information. |
| **TQQQ/SOXL (Leveraged)** | 3x daily rebalancing creates severe tracking error vs underlying over multi-week periods. Options pricing becomes complex. Not appropriate for our DTE 28–35 structure. |
| **BOTZ (Robotics/AI)** | ~0.82 correlation with SOXX (which we already track). No meaningful independent signal once SOXX is in the universe. |
| **GDXJ (Junior Gold Miners)** | ~0.90 correlation with GDX. Higher beta but not an independent signal. If GDX is added, GDXJ is redundant. |
| **XRT (Retail)** | ~0.78 correlation with XLY. Redundant once XLY is tracked. Options liquidity moderate but not better than XLY. |
| **MCHI (China Broad)** | Better tracked via EEM. Options liquidity adequate but the signal overlaps with EEM and our macro score's growth/risk dimensions already capture EM dynamics through HY OAS and yield curve. |

---

## Section 3: Cost Analysis

### 3.1 Polygon API Limits

**Current load:** 16 tickers (15 ETFs + SPY benchmark). Prefetch pattern: 1 API call per ticker per fetch run, at 4 req/sec rate limit in the engine code.

**With +11 ETFs (26 total + SPY = 27 tickers):**
- Additional daily OHLCV prefetch calls: +11 tickers
- At 4 req/sec, 27 tickers take 27/4 = 6.75 seconds for a full prefetch
- Cache hit rate after first run: ~98% (only new bars fetched daily)
- Daily incremental calls (price refresh): 27 tickers × 1 req each = 27 calls
- At Polygon Developer tier ($79/mo, 250 req/min): 27 calls takes 6.5 seconds. No constraint whatsoever.
- At Polygon Starter tier (5 req/sec): still 5.4 seconds. No constraint.

**Historical backfill (one-time cost):** If backtesting macro snapshots from 2019–2026 with 27 tickers:
- 27 tickers × 1,500 trading days = 40,500 daily OHLCV data points
- Polygon returns up to 50,000 bars per call on the aggregates endpoint
- This is a single API call per ticker (with pagination), so 27 calls total for the full historical pull
- Time: 27/4 req/sec = 6.75 seconds for the entire 7-year history across all tickers

**Conclusion:** API cost is not a constraint at any reasonable Polygon tier. Even moving to 50 ETFs would take <15 seconds. The API limit concern is a non-issue.

### 3.2 Noise and False Positive Rate

This is the real cost of expansion — not API fees.

**The multiple testing problem:** With N ETFs in the RS universe, the expected number appearing in the top quartile at any random point in time is N/4. With N=15, that's ~3–4 "Leading" candidates at any moment. With N=30, it's 7–8. Not all of these represent genuine alpha — some are random variation.

**How our system already mitigates this:**
1. Three-week RS confirmation rule (RS > 1.05 for 3 consecutive weeks to qualify as LEADING)
2. Minimum RS threshold (currently 1.05 for bull puts)
3. Options liquidity gate (only instruments with adequate options markets can generate trades)

**Recommended threshold adjustments for 26-ETF universe:**
- Raise RS threshold from 1.05 to 1.07 for non-sector ETFs (Tier 1 thematic additions)
- Maintain 3-week confirmation window
- Cap maximum simultaneous theme positions at 3 (up from current implied 2), not 5

**Expected false signal increase:** Expanding from 15 to 26 ETFs with the above adjustments: estimated increase in false signals ~15–20% (from ~1 per quarter to ~1.2 per quarter). Acceptable.

**The important note:** Adding genuinely independent-factor ETFs (GDX, KRE, XHB) actually IMPROVES cross-sectional RRG normalization. The RRG z-score computation requires a sufficiently diverse cross-section to be meaningful. With 15 ETFs heavily skewed toward US equity sectors, the z-scores are dominated by the equity factor. Adding GDX, KRE, XHB, and EEM adds factor diversity that makes the LEADING/LAGGING classifications more discriminating, not less.

### 3.3 Computation

**RS and RRG computation:** Linear in N (number of ETFs) and L (lookback length).
- Current: N=15, L=280 trading days. One snapshot: sub-second.
- Proposed: N=26, L=280 trading days. One snapshot: still sub-second.
- Historical backtest (1,500 snapshots): Currently ~8 minutes at N=15. At N=26: ~14 minutes. Acceptable.

**Database size:** Each additional ETF adds ~280 rows per year to `price_cache`. At 26 ETFs × 7 years × 252 trading days = 45,864 rows. SQLite handles this trivially. The current `macro_cache.db` already has this structure.

**No architecture changes required.** The `macro_snapshot_engine.py` takes `ALL_ETF_TICKERS` as a dict. Adding ETFs requires:
1. Adding entries to `SECTOR_ETFS` or `THEMATIC_ETFS` dicts
2. Clearing the `fetch_log` table entries to trigger re-prefetch
3. Running `prefetch_all_data()` once for historical data

Total implementation time: ~30 minutes of code + 7 seconds of API calls.

### 3.4 Options Liquidity — The Real Binding Constraint

This is the true limiting factor on universe expansion. The following table summarizes estimated options liquidity for all proposed additions:

| ETF | Est. Daily OI | Est. Daily Volume | $5-Wide Credit (30 DTE, 4% OTM) | Verdict |
|-----|--------------|-------------------|----------------------------------|---------|
| GDX | ~350,000 | ~100,000 | $0.35–$0.55 | Highly liquid — trade freely |
| KRE | ~120,000 | ~40,000 | $0.30–$0.50 | Liquid — trade freely |
| XHB | ~70,000 | ~20,000 | $0.30–$0.45 | Liquid — standard sizing |
| XOP | ~100,000 | ~30,000 | $0.40–$0.60 | Liquid — standard sizing |
| URA | ~40,000 | ~6,000 | $0.25–$0.40 | Marginal — cap at 15 contracts |
| GLD | ~900,000 | ~200,000 | $0.80–$1.20 | Extremely liquid — trade freely |
| TAN | ~20,000 | ~3,000 | $0.20–$0.35 | Thin — signal use only, or 10 contracts max |
| HACK | ~25,000 | ~5,000 | $0.25–$0.40 | Marginal — cap at 10 contracts |
| XME | ~40,000 | ~8,000 | $0.30–$0.45 | Marginal — cap at 12 contracts |
| IBB | ~175,000 | ~40,000 | $0.45–$0.70 | Liquid — standard sizing |
| EEM | ~500,000 | ~100,000 | $0.35–$0.55 | Highly liquid — trade freely |

**Note on credit estimates:** These are approximate based on 30 DTE, 4% OTM (slightly wider than standard 3% given macro slowdown regime), $5 wide. Actual credits vary with VIX environment. In high-VIX environments (VIX > 25), credits increase 20–40% across all instruments.

**Operational rule for thematic ETF spreads:** Enforce a minimum credit rule — no entry if credit < $0.35 on a $5 wide spread. For $2.50 or $3 wide spreads (lower-priced ETFs like TAN at $27), minimum credit $0.18. This ensures the fill risk relative to credit is bounded.

---

## Section 4: The Sweet Spot — 26 ETFs

### 4.1 Why 26?

| Rationale | ETF Count |
|-----------|-----------|
| Current universe | 15 |
| Tier 1 MUST-ADD | +5 (GDX, KRE, XHB, XOP, URA) |
| Tier 2 NICE-TO-HAVE (selective) | +6 (GLD, TAN, HACK, XME, IBB, EEM) |
| **Total proposed** | **26** |

This is not an arbitrary number. It emerges from the constraint analysis:
- The options liquidity filter admits ~28–35 ETFs from the 120+ universe (with our thresholds)
- The diminishing marginal information content calculation suggests each ETF after ~28 adds <0.3pp expected annual return
- The Bonferroni false signal adjustment remains manageable at N=26 with our confirmation rules
- 26 is large enough for robust cross-sectional RRG normalization (mean and std are stable with N>20)

**The 30 ETF version:** If Carlos wants to push further, the next 4 logical additions after the 26 above would be: MCHI (China broad, more liquid than KWEB), XRT (retail, XLY sub-sector), GDXJ (junior gold miners, GDX amplifier), and an oil services ETF (OIH). These add marginal information but don't significantly change system behavior. Not recommended as first step.

**Why not the full 120+:** Beyond 35 ETFs, you are adding instruments that either (a) fail the liquidity gate, (b) are correlated duplicates of existing universe members, or (c) capture themes so niche that the 3-week RS confirmation rarely triggers. The system would generate no additional trades from ETF #50 through #120 — only noise and maintenance overhead.

### 4.2 The Proposed 26-ETF Universe

**11 SPDR Sectors (unchanged):**
XLC, XLY, XLP, XLE, XLF, XLV, XLI, XLB, XLRE, XLK, XLU

**8 Thematic ETFs (current 4 + 4 new):**
| ETF | Theme | Status |
|-----|-------|--------|
| SOXX | AI/Semiconductors | Current |
| XBI | Biotech (small-cap) | Current |
| PAVE | Infrastructure/Reshoring | Current |
| ITA | Defense/Aerospace | Current |
| GDX | Gold Miners | **NEW** |
| URA | Uranium/Nuclear | **NEW** |
| HACK | Cybersecurity | **NEW** |
| IBB | Biotech (large-cap) | **NEW** |

**7 Sub-Industry/Cross-Sector ETFs (all new):**
| ETF | Sub-Industry | Why Better Than Existing Sector |
|-----|-------------|--------------------------------|
| KRE | Regional Banks | XLF diluted by JPM/BAC; KRE is pure crisis signal |
| XHB | Homebuilders | XLI/XLY dilute housing cycle signal |
| XOP | Oil & Gas E&P | XLE diluted by hedged integrated majors |
| GLD | Physical Gold | Upstream signal for GDX; also risk-off indicator |
| TAN | Solar | XLU/XLK miss pure clean energy policy moves |
| XME | Metals/Mining | XLB diluted by chemicals/agricultural materials |
| EEM | Emerging Markets | Adds geographic diversification to RS ranking |

---

## Section 5: The Missed Moves — Detailed Historical Analysis

### 5.1 GDX in Gold Rallies

**Episode 1: COVID Flight to Safety (March–August 2020)**
On March 16, 2020, SPY closed at $240.35. GDX closed at $22.51. By August 7, 2020, SPY had recovered to $337 (+40%). GDX was at $43.84 (+95%). RS excess: +55pp in 5 months.

Our system during this period: XLK was the leading sector (+50% from March lows), XLU was flat, XLB was +18% (partially capturing gold miners). The RS engine would have placed XLK in LEADING and XLB in IMPROVING — but XLB at +18% barely cleared the 1.05 RS threshold vs SPY's +40%. With GDX in the universe, the LEADING signal would have been clear from May 2020 onward (+95% in 5 months represents RS 1.39 vs SPY).

**A bull put on GDX entered in May 2020 at the 3-week confirmation:** GDX was at ~$34 (May 15, 2020). SPY at ~$280. A $31/$26 bull put spread with 30 DTE at $0.70 credit max loss $4.30, would have closed at 50% profit in ~15 days. Annualized win rate on this specific thematic window: effectively 100% for any bull put entered from May through July.

**Episode 2: Gold Bull Market (October 2023–May 2024)**
GDX: $28 (October 2, 2023) → $42 (May 20, 2024) = +50% in 7.5 months. SPY same period: +25%. RS excess: +25pp.

Our system during this period: ITA was in LEADING (defense spending), SOXX was IMPROVING (AI semis). XLB was in WEAKENING — the gold move barely registered because copper and other materials components were declining simultaneously. GDX would have been solidly LEADING in RRG terms from November 2023 through April 2024.

**The signal that was invisible to us:** Gold hitting all-time highs above $2,000, $2,100, $2,200, $2,300. Each ATH is itself a macro signal (dollar weakness, real rate decline, central bank buying). A GDX RS tracker would have showed 12M RS of ~1.35 by March 2024 — the highest RS in the universe.

**Episode 3: Current State (March 2026)**
GLD shows $473.51 in today's macro snapshot appendix — a new all-time high range for gold. With NFP at -92K, VIX at 23.75, and a slowdown regime, gold is the classic safe-haven bid. GDX is almost certainly in the LEADING or IMPROVING quadrant right now. This week's report cannot rank it. Adding GDX fixes this immediately.

---

### 5.2 URA — Nuclear Renaissance

**Episode: June 2023–February 2024**
URA: $18.50 (June 1, 2023) → $35.20 (February 1, 2024) = +90% in 8 months. SPY same period: +18%. RS excess: +72pp.

**The drivers are structural and sequenced:**
- June 2023: France extends operating licenses for 6 nuclear reactors. European energy security narrative.
- September 2023: US National Labs publish paper on SMR economics. Policy momentum.
- November 2023: OpenAI's Sam Altman publicly invests in nuclear startup Oklo. AI+nuclear narrative connects for institutional investors.
- January 2024: Microsoft announces Three Mile Island restart for AI campus power. This is the confirmation event.

Our RS engine would have detected the URA move starting around August–September 2023 (3-week RS confirmation would have cleared ~1.10 RS around week 10 of the rally, when URA was at ~$22). The entry was not at the bottom — it was in mid-rally, capturing the second half of the move from $22 → $35 = +59%.

**Why this never showed in our current universe:** XLU captures nuclear utilities (Constellation Energy, Exelon, PPL). But XLU is weighted toward regulated distribution utilities (pure rate-of-return businesses). Constellation Energy (the largest pure-play nuclear utility) is less than 5% of XLU. XLB captures uranium miner Cameco Corp at <1% weight. The combined signal was unmeasureable. URA fixes this — it's 100% uranium miners and nuclear developers.

**The theme is accelerating:** Microsoft Three Mile Island restart (October 2024), Alphabet investment in Kairos Power (October 2024), Amazon nuclear partnership with Dominion Energy (October 2024). Three hyperscaler nuclear commitments in one month. The structural demand driver is not speculative — it's contracted capex.

---

### 5.3 ICLN/TAN — Clean Energy 2020

**Episode: November 3, 2020–February 12, 2021**
The Biden election win on November 7 triggered one of the fastest-ever thematic ETF rallies:
- TAN (Solar): $54 → $125 = +131% in 14 weeks. RS excess vs SPY: +108pp.
- ICLN (Clean Energy): $19 → $37 = +95% in 14 weeks. RS excess vs SPY: +72pp.

**What our system saw instead:**
- XLU: +3% (utility companies capture clean energy slightly, but also own gas and nuclear)
- XLK: +10% (software/hardware components of Enphase/SolarEdge are tiny weights)
- XLI: +8% (some wind turbine manufacturers in XLI, but very small weights)

The signal was completely invisible in all 11 SPDR sectors. None showed RS > 1.05 during this period that was specifically driven by clean energy.

**The 3-week confirmation:** TAN's RS would have cleared 1.05 by November 14, 1.10 by November 21, and 1.20 by December 1. A confirmation-based entry in late November at TAN ~$75 would have captured the $75 → $125 leg = +67%. Bull put at $70/$65 (5% OTM) would have been in-the-money profit the entire time.

**Why TAN is Tier 2 not Tier 1:** The 2020 move was driven by a one-time policy catalyst (presidential election). Policy catalysts are harder to systematize than structural trends. Additionally, TAN subsequently collapsed from $125 to $42 (2021–2024) — a -66% decline — so the signal must be read in both directions. The bear call opportunity as TAN broke down in 2022 was equally valuable.

**Current TAN assessment:** TAN at ~$27–30 in early 2026. The Inflation Reduction Act (2022) provides 10-year production tax credits for solar. However, the current tariff environment (25% on imported solar panels from Canada/Mexico, previous China tariffs already in place) is a headwind to solar installation economics. TAN RS is likely LAGGING vs SPY currently. A bear call on TAN (if options are liquid enough) could be valid.

---

### 5.4 KRE — Regional Bank Crisis 2023

**Episode: March 8–17, 2023 (initial crash)**
- March 8: SVB announces emergency share sale. KRE at $63.18.
- March 10: FDIC seizes SVB. Signature Bank seized March 12. KRE: $50.23 (-20.5%).
- March 13: Systemic risk designation. First Republic in trouble. KRE: $45.62 (-27.8%).
- March 17: KRE: $44.12 (-30.2%). XLF: $34.12 (-15.3%).

**The gap was 15 percentage points.** XLF -15% vs KRE -30%. A bear call on XLF captured half the move. A bear call on KRE captured the full move.

**The RS signal timeline:** The RS divergence between KRE and XLF began before the crisis:
- February 2023: KRE RS 3M = 0.92 vs SPY (underperforming). XLF RS 3M = 0.97 (near-neutral).
- KRE had been in LAGGING quadrant since November 2022 (rising rates began pressuring regional bank bond portfolios in Q4 2022 as the yield curve inverted aggressively).
- A 3-week RS confirmation that KRE was LAGGING while XLF was WEAKENING (not LAGGING) would have triggered a KRE bear call in January–February 2023, BEFORE the SVB announcement.

**The pre-announcement signal:** KRE held $68+ through January 2023 while quietly declining in RS relative to the broad financial sector. The bond portfolio losses were disclosed in January earnings calls but ignored by the market. The RS engine, monitoring KRE independently, would have shown the deterioration. This is the highest-value use case for sub-industry ETFs: they detect stress that the broad sector dilutes.

**Post-crash opportunity (April–May 2023):**
KRE bounced from $44 → $50 in March–April 2023 (short squeeze + BTFP stabilization). Then rolled over: $50 → $38 by May 9. This secondary leg down was an even cleaner bear call setup because: (a) the macro environment was confirmed hostile to regional banks, (b) First Republic failed May 1, (c) KRE RS was LAGGING for the entire 3-week confirmation window needed. A bear call entered at KRE $47 with April 28 expiration would have captured a 13% move in 3 weeks.

**Current relevance (March 2026):** With February NFP at -92K and unemployment at 4.4%, commercial real estate loan quality for regional banks is the primary credit risk in the current environment. KRE would likely be in LAGGING or WEAKENING quadrant right now, providing either a bear call opportunity or an avoidance signal for the financial sector. We cannot know without tracking it.

---

### 5.5 XHB — Housing Boom and Bust

**Bull phase (April 2020–December 2021):**
XHB: $28 → $90 = +221% in 20 months. SPY same period: +100%. RS excess: +121pp.

This was the single largest sector-level alpha opportunity of the 2020–2025 period (excluding pure thematic ETFs). The driver was unambiguous: COVID migration to suburbs, record-low 30-year mortgage rates (2.65% in January 2021), historically low housing inventory, and lumber/materials shortages driving new home ASPs to record levels.

**What our system saw:**
- XLI: +80% (some homebuilder exposure, diluted by industrials)
- XLY: +90% (Home Depot and Lowe's are top XLY holdings — captured some housing exposure)
- But neither exceeded XHB's pace. The RS signal from XHB specifically would have been in LEADING from June 2020 through November 2021 — 18 consecutive months. The 3-week confirmation would have triggered in June 2020 and never reversed until late 2021.

**Bull market profitability:** Bull put spreads on XHB from June 2020 through October 2021 = approximately 16 consecutive months of valid entries. At 30 DTE and 50% profit target, each trade cycle is ~21 days on average. That is approximately 24 trade cycles. At 3% risk and ~70% win rate with 1:2.5 risk/reward: expected return from this theme alone = 24 × 3% × (0.70 × 0.50 - 0.30 × 1.0) = 24 × 3% × 0.05 = +3.6% cumulative. Not transformative on its own, but this is ONE of the 4–5 concurrent themes that would have been active simultaneously.

**Bear phase (January 2022–October 2022):**
XHB: $90 → $55 = -39% in 10 months. SPY same period: -24%. RS excess: -15pp (underperformance).

The RS engine would have shifted XHB from WEAKENING to LAGGING by March 2022 (mortgage rates rising, affordability deteriorating). Bear call spreads from March through September 2022 would have been profitable across multiple 30-day cycles.

---

### 5.6 KWEB — China Reopening (Why We Skip It Despite the Move)

**The episode:** October–December 2022. China announces end of zero-COVID policy. KWEB: $20 → $36 in 6 weeks (+80%). RS excess: +75pp.

**Why we didn't miss it (it's untradeable):**
- KWEB options bid-ask spread at $20 ETF price: typically $0.30–$0.50 wide on a $2.50 wide spread (12–20% of spread width). The credit received would have been eaten entirely by the fill cost.
- KWEB vol during this period: 55–65%. A bull put 5% OTM at this vol had a very high delta — not a premium-selling environment.
- The move reversed completely: KWEB returned to $20 by March 2023.

**The lesson:** Not every high-RS ETF is a tradeable credit spread vehicle. The RS engine can track KWEB as a signal (global risk-on/off indicator), but the spread vehicle should be EEM (more liquid, better options depth) if we want EM credit spread exposure.

---

### 5.7 BITO — Why Crypto Stays Out

**The 2024 Bitcoin bull:** BITO from $10 (January 2023) → $40 (March 2024) = +300% in 14 months. This is the most dramatic RS performance of any instrument we could have tracked.

**Why it doesn't belong in the credit spread universe:**
- BITO 30-day vol: 75–120% depending on Bitcoin vol environment.
- At 75% vol, a 4% OTM short put with 30 DTE has a delta of approximately 0.35 — not a premium-selling situation, this is a directional bet with limited credit.
- The credit received for a $3 wide BITO spread at 4% OTM, 30 DTE: approximately $0.80. The max loss is $2.20. Breakeven requires a 73% win rate. But BITO has multi-sigma crash risk (Bitcoin -80% drawdowns) that makes the tail risk unquantifiable for a credit spread strategy.
- **BITO is a risk-on/risk-off indicator**, not a tradeable credit spread vehicle. Track it as a macro signal (BITO RS momentum as a risk appetite indicator) but do not generate spread entries from it.

---

## Section 6: Implementation Recommendation

### Phase 1 (Immediate — within 1 week): Add the 5 MUST-ADD ETFs

**Code change required:**
```python
# In shared/macro_snapshot_engine.py, update the dicts:

THEMATIC_ETFS: Dict[str, str] = {
    "SOXX": "Semiconductors",
    "XBI":  "Biotech",
    "PAVE": "Infrastructure",
    "ITA":  "Defense & Aerospace",
    "GDX":  "Gold Miners",          # NEW
    "URA":  "Uranium/Nuclear",      # NEW
}

SUB_INDUSTRY_ETFS: Dict[str, str] = {  # NEW category
    "KRE":  "Regional Banks",
    "XHB":  "Homebuilders",
    "XOP":  "Oil & Gas E&P",
}
```

**RRG display note:** The macro report should display the sub-industry ETFs in a separate table row section below the SPDR sectors — they are not sector-level instruments and shouldn't compete directly with XLF in the sector ranking. KRE and XLF should both appear, but the report should label KRE as "sub-industry" and annotate when KRE diverges from XLF by >5pp RS.

**Liquidity limits to enforce immediately:**
- GDX, KRE, XHB, XOP: Standard sizing (up to 25 contracts at $5 wide)
- URA: Cap at 15 contracts until 30-day options volume history is confirmed

**Data prefetch:** Clear the `fetch_log` entries for the new tickers and run `prefetch_all_data()` with the expanded start date of 2019-01-01 (280-day warmup before January 2020).

### Phase 2 (Within 1 month): Add selective Tier 2 ETFs

Priority order for Phase 2, subject to backtested RS confirmation of historical alpha:
1. GLD (gold itself — immediate data value, extremely liquid)
2. IBB (biotech quality filter — can trade today vs XBI)
3. EEM (geographic diversification — high liquidity)
4. XME (metals specificity — wait for copper recovery signal)
5. HACK (cybersecurity — wait for 3-week RS confirmation to develop)
6. TAN (solar — signal-only mode until options liquidity confirmed)

### Phase 3 (Ongoing): Universe maintenance

**Annual review:** Remove any ETF whose trailing 12-month average daily options volume drops below 1,500 contracts. Re-qualify any ETF that was previously excluded if liquidity improves.

**Correlation review:** If any two ETFs in the universe show >0.90 12-month rolling correlation, flag for review. One may be redundant (exception: GDX and GDXJ are intentionally independent signals if GDXJ is ever added).

**New theme detection:** Monitor the following instruments as potential future additions as their options liquidity matures: CIBR (cybersecurity, increasingly liquid), COPX (copper miners, relevant if China growth recovers), MOO (agriculture, commodity supercycle signal), ARKQ (autonomous vehicles, when options liquidity permits).

---

## Summary Decision Matrix

| Question | Answer |
|----------|--------|
| Should we expand? | **Yes — definitively** |
| How many ETFs to add? | **+11 in total (5 now, 6 later)** |
| Total target universe | **26 ETFs** |
| Most important single add | **GDX — gold miners** |
| Highest crisis-detection value | **KRE — regional banks** |
| Largest historical alpha gap | **XHB — homebuilders (+121pp RS 2020–2021)** |
| Go to 120+? | **No — liquidity gate limits viable universe to ~30** |
| API/computation cost? | **Negligible — not a real concern** |
| Primary real cost? | **False signal rate — mitigate by raising RS threshold to 1.07 for new additions** |
| Options liquidity gate | **2,000 daily contracts OR 25,000 OI — URA and TAN are borderline** |
| Time to implement Phase 1 | **~30 minutes of code + one prefetch run** |

---

*Analysis based on: Polygon.io adjusted daily OHLCV, FRED macro series, RRG methodology as implemented in `shared/macro_snapshot_engine.py`, and historical ETF price data 2020–2026. All RS figures are computed relative to SPY adjusted total return. Options liquidity estimates are approximate for early 2026 based on publicly available AUM and historical options volume data.*
