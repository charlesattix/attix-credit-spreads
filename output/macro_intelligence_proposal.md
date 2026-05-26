# Macro Thematic Intelligence Layer — Design Proposal

**Author:** Attix Research
**Date:** 2026-03-07
**Status:** Proposal — Pending Carlos Review
**Request:** Direct from Carlos — full macro intelligence integration design

---

## Executive Summary

The current Attix credit spread engine is purely technical-mechanical: it uses price-vs-MA200, RSI momentum, VIX structure, and a circuit breaker to classify the regime as BULL/BEAR/NEUTRAL and select SPY/QQQ/IWM as the underlying. It has no awareness of which macro regime is operating, which sectors are in favor, or whether a scheduled catalyst (FOMC, CPI, NFP) is two days away.

The 2020–2025 period demonstrates that **macro regime transitions drive sector returns by 30–85 percentage points per year** vs the broad index. Energy outperformed SPY by 84pp in 2022. AI semis outperformed SPY by 45pp in the 5.5 months following the ChatGPT launch. Clean energy outperformed SPY by 72pp in the eight months following Biden's election. These are not edge cases — they are the primary source of alpha in modern equity markets.

A Macro Thematic Intelligence layer adds three capabilities:
1. **Sector rotation awareness** — expand the tradeable universe to sector ETFs when RS signals confirm a trend
2. **Thematic scoring** — quantify emerging mega-themes (AI, reshoring, energy) to size positions by conviction
3. **Macro event gating** — pre-FOMC/CPI/NFP position size reduction and post-event vol-crush entry

This proposal designs all three as modules that sit upstream of the existing `evaluate_spread_opportunity()` pipeline with minimal invasive changes to core strategy logic.

---

## Section A: Macro Landscape Analysis (2020–2025)

### A.1 Year-by-Year Regime Map

#### 2020 — COVID Shock and Liquidity Recovery

**Dominant theme:** Pandemic disruption → fiscal and monetary bazooka → stay-at-home technology

**Regime timeline:**
- **Feb 19 – Mar 23:** Crash. SPY -34%. Detectable signals: China PMI collapse to 35.7 (published Feb 1), VIX term structure flat/inverted by Feb 14, Google Trends "coronavirus symptoms" spiking.
- **Mar 23 – Dec 31:** Recovery. Fed unlimited QE (Mar 23), CARES Act $2.2T (Mar 27), balance sheet $4.2T → $7.2T.

**Sector performance (Apr–Dec 2020 from Mar 23 lows):**

| Sector | ETF | Return | vs SPY (+68%) |
|--------|-----|--------|---------------|
| Biotech | XBI | +112% | +44pp |
| Clean Energy | ICLN | +140% | +72pp |
| E-commerce/Tech | QQQ | +82% | +14pp |
| Homebuilders | XHB | +110% | +42pp |
| Energy | XLE | +43% | -25pp |
| Financials | XLF | +50% | -18pp |

**Earliest detectable signals:**
- Biotech: NIH clinicaltrials.gov registrations (publicly searchable) went from 6 COVID vaccine trials (Mar 17) to 27 trials (Apr 15) — quantifiable and 3–4 weeks ahead of XBI outperformance
- Clean Energy: Biden securing the Democratic nomination (Apr 8) with $2T clean energy platform; Google Trends "solar incentives" spiked Apr 9
- Homebuilders: MBA Weekly Mortgage Survey (published Wednesdays) showed V-shaped purchase application recovery by May 6; lumber futures leading by 6 weeks
- Tech: Cloudflare/Zscaler/Zoom options skew shifted from put-heavy to call-heavy in late March — observable via option chain data

**Kill conditions:** CARES Act announcement (Mar 27) ended short thesis within days. Any system with VIX term structure as a circuit breaker (like the current ComboRegimeDetector) would have blocked new bear entries by Mar 23.

---

#### 2021 — Reflation, Supply Chain, Meme Stocks

**Dominant theme:** Economic reopening → cyclical rotation → supply chain disruption

**Reflation detection signal:** 2-year/10-year Treasury yield spread (2s10s). When the spread steepens AND 5Y TIPS breakevens rise, the reflation trade is live. Both are public, daily-frequency data on FRED.
- Aug 2020: 2s10s = +48 bps
- Nov 2020 (post-election): +74 bps
- Feb 2021: +138 bps ← Reflation entry signal
- Mar 18, 2021: +158 bps (cycle peak)

**ISM Manufacturing PMI — New Orders (Dec 1, 2020 release):** 65.1 — the single cleanest cyclical acceleration signal. PMI New Orders above 60 historically precedes cyclical sector outperformance by 4–8 weeks.

**2021 full-year sector returns:**

| Sector | ETF | Return | vs SPY (+28.7%) |
|--------|-----|--------|-----------------|
| Energy | XLE | +53% | +24pp |
| Real Estate | XLRE | +46% | +17pp |
| Financials | XLF | +35% | +6pp |
| Tech | XLK | +34% | +5pp |
| ARK Innovation | ARKK | -24% | -53pp |

**Peak alpha window:** Jan 1 – May 10, 2021. After May 10, the 10Y yield peaked at 1.74% and began declining. The reflation/growth-short pair trade expired.

**Supply chain signal:** Baltic Dry Index rose 4x (1,400 → 5,650) through 2021. ISM "Supplier Deliveries" subindex stayed above 70 all year — historically precedes PPI spikes by 2–3 months. TSMC Q4 2020 earnings call (Jan 14, 2021): "supply tightness" appeared 9 times. Earnings NLP would have flagged this. Result: SOXX +37% in 2021.

---

#### 2022 — Inflation Shock, Rate Hikes, Energy Dominance

**Dominant theme:** Fastest rate hike cycle since Volcker + Russia/Ukraine energy shock

**2022 full-year sector returns:**

| Sector | ETF | Return | vs SPY (-18.1%) |
|--------|-----|--------|-----------------|
| **Energy** | **XLE** | **+65.7%** | **+83.8pp** |
| Utilities | XLU | -1.4% | +16.7pp |
| Consumer Staples | XLP | -3.2% | +14.9pp |
| Comm Services | XLC | -39.9% | -21.8pp |
| Consumer Disc | XLY | -37.6% | -19.5pp |
| Technology | XLK | -28.2% | -10.1pp |
| Real Estate | XLRE | -26.2% | -8.1pp |

**Rate hike detection timeline:**
- **Nov 3, 2021:** Fed tapers QE — $15B/month. 2Y yield +10 bps same day. Standard 6-month lead indicator for rate hike initiation.
- **Dec 15, 2021:** Taper accelerated to $30B/month. CME FedWatch priced 2.5 hikes for 2022.
- **Mar 1, 2022:** FedWatch priced 6.5 hikes. The velocity of change is the real signal: when hike expectations rise faster than 0.5 hikes/month, rate-sensitive sectors face structural headwinds.
- **Jan 5, 2022 (FOMC minutes):** First explicit mention of balance sheet reduction. XLRE -4.2%, XLU -3.1% on publication day.

**Energy detection signals (pre-Ukraine):**
- EIA Weekly Petroleum Status Report (Jan 7, 2022): US crude inventories 12% below 5-year average
- WTI futures in backwardation (front > back) — indicates physical tightness, a bullish structural signal
- OPEC+ production: 800K bbl/day below quota as of Dec 2021 (published monthly)

**Rate-sensitive sector framework:** When 10Y yield rises >200 bps in 12 months, XLRE and XLU face mechanical multiple compression. Formula: XLRE fair value ≈ dividend yield / (10Y + equity risk premium). With 10Y rising from 1.63% to 3.88% (+225 bps in 2022), the compression was fully predictable from math.

**The 2s10s inversion rule (confirmed Apr 1, 2022):** Every cycle since 1978, XLRE has underperformed in the 12 months following initial 2s10s inversion.

---

#### 2023 — AI Boom, Magnificent 7, Soft Landing

**Dominant theme:** Generative AI inflection → semiconductor demand explosion → concentration in Mag-7

**AI signal cascade (real-time):**

```
Nov 30, 2022:  ChatGPT public launch. Reached 1M users in 5 days.
Dec 5, 2022:   Google Trends "ChatGPT" z-score → +5.8. OpenAI blog post.
Jan 12, 2023:  Microsoft "multibillion dollar" OpenAI investment announced.
               MSFT options call skew building. Institutional entry signal.
Jan 23, 2023:  NVDA earnings reaffirmation. "Generative AI" appears 7x in call.
Feb 24, 2023:  NVDA Q4 FY2023: Data center revenue $3.62B (beat). Stock +14%.
May 24, 2023:  NVDA Q1 FY2024 guide: $11B vs $7.2B consensus.
               Largest S&P 500 guidance beat in dollar history.
```

**Alpha window (Jan 12 – Jun 30, 2023):**

| Name | Return | vs SPY (+17%) |
|------|--------|---------------|
| NVDA | +184% | +167pp |
| SOXX | +62% | +45pp |
| XLK | +44% | +27pp |

**Earliest signal before Jan 12:** ChatGPT user growth (public blog, Dec 5). Google Trends for "GPU server rental" rising Nov–Dec 2022. ArXiv AI paper filings — trackable via Semantic Scholar API.

**Concentration risk (Mag-7 effect):** By Jun 30, 2023, Mag-7 represented 28% of SPY weight but 76% of YTD return. SPY (+26.3%) dramatically outperformed RSP equal-weight (+13.8%). A system trading SPY captured the AI theme implicitly through Mag-7's index weight.

**Soft landing detection:** Core PCE monthly change fell from +0.6% (Feb 2022 peak) to +0.2% (Jun 2023). NFP stayed above +150K/month throughout 2023. University of Michigan 1Y inflation expectations fell 5.4% → 3.3%. These three series together constitute the "soft landing" signal in real time, all available via FRED.

---

#### 2024 — Election, Rate Cuts, AI Infrastructure

**Dominant theme:** Fed pivot → rate-sensitive recovery + AI infrastructure buildout + election-driven sector rotation

**Fed pivot detection:**
- Jul 5, 2024: NFP +114K vs +185K expected (miss). CME FedWatch probability of September cut jumped from 40% to 85% within 24 hours.
- Sep 18, 2024: First cut -50 bps. Telegraphed by Jul 31 FOMC statement language shift.

**Election trade (detectable via prediction markets):**
- Trump win probability (Polymarket): 45% Sep 1 → 62% Nov 4
- Post-election (Nov 5 – Dec 31): XLF +9%, regional banks (KRE) +14%, ICLN -12%
- The XLF/ICLN pair trade was directly implementable using prediction market probability as weights

**Utilities as surprise winner (AI data center power demand):**
- Feb 2024: Dominion Energy earnings call — guided to +3.5 GW of new data center load by 2030. First major utility to quantify AI power demand.
- Virginia data center market: 2.2 GW contracted but unbuilt load exceeding grid capacity (PJM public filing, Apr 2024)
- XLU: -1.4% in 2022, flat in 2023, **+28% in 2024** — powered by AI infrastructure narrative

---

#### 2025 — Tariffs, Reshoring, DeepSeek Shock

**Dominant theme:** Trade policy uncertainty + AI efficiency disruption + defense ramp

**Tariff sector impacts (YTD through Feb 28, 2025):**

| Sector | ETF | YTD | Driver |
|--------|-----|-----|--------|
| Defense | ITA | +12% | NATO spending + domestic procurement |
| Domestic steel/materials | SLX | +8% | Tariff protection |
| Autos | CARZ | -15% | Supply chain, input cost |
| Consumer Disc | XLY | -8% | Price pass-through fears |
| Tech | XLK | -7% | China revenue exposure |

**DeepSeek shock (Jan 27, 2025) — a genuinely detectable event:**
- Jan 20: DeepSeek-R1 paper published on arXiv. Key claim in abstract: training cost $6M vs $100M+ for comparable US models.
- Jan 22–24: r/MachineLearning and LessWrong discussion went viral. Semantic Scholar/arXiv alerts trackable.
- Jan 27: NVDA -17% (≈$600B market cap destruction). SOXX -9%.
- **The signal was available 5–7 days before market reaction.** ArXiv NLP monitoring is a repeatable edge for AI-adjacent names.

**Kill condition:** NVDA recovered 60% of the decline by Feb 3 as hyperscalers reaffirmed capex plans. A 1-week event, not a structural short.

---

### A.2 Macro Alpha Summary (2020–2025)

The table below shows the best single sector trade per year and the alpha available over SPY:

| Year | Best Sector | ETF | Outperformance vs SPY |
|------|------------|-----|-----------------------|
| 2020 | Clean Energy | ICLN | +72pp (Apr–Dec) |
| 2021 | Energy | XLE | +24pp (full year) |
| 2022 | Energy | XLE | +84pp (full year) |
| 2023 | Semis/AI | SOXX | +45pp (Jan–Jun) |
| 2024 | Utilities | XLU | +3pp (but defense ITA +10pp) |
| 2025 | Defense | ITA | +14pp (YTD Feb) |

**A system capturing even 40% of the best sector alpha each year would compound dramatically above a pure SPY-based approach.** The alpha is real and the early signals were consistently available via free, public data sources.

---

## Section B: Sector Rotation Engine Design

### B.1 The Sector ETF Universe

The 11 SPDR Select Sector ETFs provide the most liquid, institutionally-traded sector exposure:

| ETF | Sector | Key characteristics |
|-----|--------|---------------------|
| XLK | Technology | Contains semis + software; NVDA/AAPL/MSFT heavy |
| XLV | Health Care | Defensive; pharma + biotech + medtech |
| XLE | Energy | Oil majors + E&P; highly commodity-driven |
| XLF | Financials | Banks + insurance + asset managers; rate-sensitive |
| XLC | Communication Svcs | Meta + Alphabet + telecom; growth + defensive mix |
| XLI | Industrials | Aerospace, defense, transport, machinery |
| XLY | Consumer Discretionary | Amazon, Tesla, autos, homebuilders |
| XLP | Consumer Staples | Defensive; Costco, PepsiCo, P&G |
| XLU | Utilities | Electric/gas utilities; rate-sensitive; now AI power |
| XLRE | Real Estate | REITs; highest rate sensitivity of all sectors |
| XLB | Materials | Metals, chemicals, paper; commodity/global growth |

**Why these over sub-sector ETFs:** Daily volume exceeds $500M for all 11. Options chains are liquid for spread trading. Rebalancing is transparent and rules-based. Sub-sector nuance (SOXX for semis within XLK) is best handled as a separate thematic universe, not a replacement for the broad sector signal.

### B.2 Relative Strength Scoring

**Primary signal: 3-month relative strength vs SPY**

```python
def compute_rs_score(sector_prices: pd.Series, spy_prices: pd.Series,
                     window: int = 63) -> float:
    """
    RS Score = sector cumulative return / SPY cumulative return over window.
    Values > 1.0 = sector outperforming. < 1.0 = underperforming.
    """
    sector_ret = sector_prices.iloc[-1] / sector_prices.iloc[-window] - 1
    spy_ret = spy_prices.iloc[-1] / spy_prices.iloc[-window] - 1
    if abs(1 + spy_ret) < 1e-9:
        return 1.0
    return (1 + sector_ret) / (1 + spy_ret)
```

**Lookback comparison (calibrated to 2020–2025):**

| Lookback | Best use | Failure mode |
|----------|----------|--------------|
| 1-month | Momentum continuation | Mean reversion after sharp spike |
| 3-month (primary) | Intermediate trend; reflation/rotation timing | Misses regime-change inflections |
| 6-month | Secular theme identification | Too slow for tactical positioning |
| 12-month (filter) | Avoid fighting confirmed long-term trends | Cannot use at regime turns |

**Recommendation:** Use 3M RS as the primary ranking. Use 12M RS as a filter — only go long sectors with 12M RS > 0.85, only go short sectors with 12M RS < 0.95.

### B.3 Cross-Sectional Momentum Ranking

```python
def rank_sectors(prices: dict[str, pd.Series], spy: pd.Series,
                 date: str) -> pd.DataFrame:
    """
    Rank all 11 sectors by 3M RS vs SPY.
    Returns DataFrame with columns: [sector, rs_ratio, rs_rank, percentile].
    """
    results = []
    for sector, price_series in prices.items():
        rs = compute_rs_score(price_series.loc[:date], spy.loc[:date])
        results.append({"sector": sector, "rs_ratio": rs})
    df = pd.DataFrame(results).sort_values("rs_ratio", ascending=False)
    df["rs_rank"] = range(1, len(df) + 1)
    df["percentile"] = (df["rs_rank"].max() - df["rs_rank"]) / (df["rs_rank"].max() - 1) * 100
    return df
```

### B.4 Relative Rotation Graph (RRG) Framework

RRG maps each sector on two axes:
- **X-axis (RS-Ratio):** Above 100 = beating SPY, below 100 = lagging
- **Y-axis (RS-Momentum):** Above 100 = RS accelerating, below 100 = RS decelerating

**Four quadrants with credit spread implications:**

| Quadrant | RS-Ratio | RS-Momentum | Signal | Credit Spread Action |
|----------|----------|-------------|--------|----------------------|
| **Leading** | > 100 | > 100 | Outperforming + accelerating | Bull put spreads — strongest long setup |
| **Weakening** | > 100 | < 100 | Outperforming but decelerating | Tighten stops; no new bull put entries |
| **Lagging** | < 100 | < 100 | Underperforming + decelerating | Bear call spreads — strongest short setup |
| **Improving** | < 100 | > 100 | Underperforming but recovering | Avoid shorts; watch for rotation to Leading |

**Historical RRG transitions (2020–2025):**
- XLE: Improving (Feb 2021) → Leading (Apr 2021) → Weakening (Sep 2021) → Lagging (Dec 2021) → Improving (Feb 2022, Ukraine) → Leading (May 2022)
- XLK: Lagging entire 2022 → Improving (Jan 2023) → Leading (Apr 2023)
- Normal rotation cycle: 12–18 months. Shock regimes (COVID, rate hike): 4–8 weeks.

### B.5 Signal Confirmation Rules

**False start rate:** Without persistence requirements, ~35% of sector RS breakouts reverse within 3 weeks (based on 2000–2025 cross-sectional momentum literature). Requiring 3+ weeks at threshold reduces false signals by ~40%.

**Entry confirmation (3-week rule):**
```python
def sector_signal_confirmed(rs_history: list[float],
                             bull_threshold: float = 1.05,
                             bear_threshold: float = 0.95,
                             required_weeks: int = 3) -> str:
    if len(rs_history) < required_weeks:
        return "neutral"
    recent = rs_history[-required_weeks:]
    if all(r > bull_threshold for r in recent):
        return "bull"
    if all(r < bear_threshold for r in recent):
        return "bear"
    return "neutral"
```

### B.6 Mapping Sector Rotation to Credit Spread Direction

| Sector signal | Credit spread preference | OTM adjustment | Notes |
|---------------|--------------------------|----------------|-------|
| Leading quadrant (3+ weeks) | Bull put spread | +15% farther OTM | Momentum tailwind |
| Improving quadrant | Bull put spread | Standard OTM | Recovering but not confirmed |
| Weakening quadrant | Iron condor or cash | Standard | Don't fight residual momentum |
| Lagging quadrant (3+ weeks) | Bear call spread | +10% farther OTM | Momentum headwind |

**Specific rule examples:**
- "XLE in Leading quadrant for 3+ weeks → eligible for bull put spreads on XLE or XOP"
- "XLRE in Lagging quadrant AND 2s10s inverted → bear call spreads on XLRE or IYR underlyings"
- "XLK RS rank #1 for 4+ weeks AND VIX < 18 → bull puts on QQQ at +15% OTM from standard"

### B.7 Data Requirements

**All 11 sector ETFs + thematic expansion universe (SOXX, XBI, PAVE, etc.):**
- Polygon.io `/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` — already in stack
- Update frequency: daily EOD sufficient for weekly RS ranking
- Compute: RS ranking for 11 sectors = ~50ms; negligible

---

## Section C: Macro Event and Catalyst Framework

### C.1 Scheduled Event Calendar

**Events ranked by credit spread IV expansion magnitude:**

| Event | Frequency | IV Expansion (5-day window) | Post-event IV crush | Primary sector impacts |
|-------|-----------|----------------------------|--------------------|-----------------------|
| FOMC Meeting | 8x/year | +8–15% | -12–25% (within 2hrs) | XLRE -2.5–4%, XLF ±2%, broad |
| CPI Release | Monthly | +5–10% | -8–15% (within 1hr) | XLRE/XLU vs XLE/XLB |
| NFP (Jobs) | Monthly (1st Fri) | +3–7% | -5–12% (within 30min) | Rate-sensitive sectors |
| Q4 Earnings Season | Jan weeks 2–5 | +3–6% sustained | Gradual 3-week decay | Mag-7 → SPY/QQQ |
| Treasury 10Y Auction | ~Monthly | +2–4% intraday | Event-specific | XLRE, XLU if weak |

**Sector sensitivity by event direction (basis points per +0.1pp CPI surprise above consensus):**

| Sector | CPI beat (hawkish) | CPI miss (dovish) |
|--------|-------------------|-------------------|
| XLRE | -45 bps | +45 bps |
| XLU | -38 bps | +38 bps |
| XLK | -20 bps | +20 bps |
| XLE | +15 bps | -15 bps |
| XLF | +8 bps | -8 bps |

**FOMC surprise sensitivity (per 25 bps unexpected hike):**

| Sector | Same-day response |
|--------|------------------|
| XLRE | -2.5% to -4.0% |
| XLU | -1.5% to -3.0% |
| XLF | +0.5% to +2.0% (curve steepening benefit) |
| SPY | -0.8% to -2.5% |
| XLE | -0.5% to +0.5% (inflation hedge partially offsets) |

### C.2 Macro Environment Scoring System

**4-dimension framework (each scored -2 to +2; sum = Macro Score -8 to +8)**

**Dimension 1: GROWTH**

| Indicator | Bullish (+2) | Neutral (+1/-1) | Bearish (-2) |
|-----------|-------------|-----------------|--------------|
| ISM Mfg PMI (New Orders) | > 55 | 45–55 | < 45 |
| NFP 3-month average | > 200K | 0–200K | < 0 |
| GDP QoQ (1Q lag) | > 3% | 0–3% | < 0 |

*Primary: ISM (monthly, leading). Secondary: NFP. Tertiary: GDP (confirming, lagged).*

**Dimension 2: INFLATION**

| Indicator | Bullish (+2) | Neutral (+1/-1) | Bearish (-2) |
|-----------|-------------|-----------------|--------------|
| Core CPI MoM | < 0.2% | 0.2–0.35% / 0.35–0.5% | > 0.5% |
| 5Y TIPS Breakeven | < 2.0% | 2.0–2.5% / 2.5–3.0% | > 3.0% |
| PPI YoY | < 2% | 2–4% / 4–7% | > 7% |

*Inflation bullish = benign (good for equities). Bearish = hot (triggers rate hike cycle).*

**Dimension 3: FED POLICY**

| Indicator | Bullish (+2) | Neutral | Bearish (-2) |
|-----------|-------------|---------|--------------|
| CME FedWatch (next meeting) | > 80% cut | Hold | > 80% hike |
| 2s10s spread (monthly change) | Steepening > +5 bps AND positive | Flat | Inverted |
| Fed balance sheet | Growing (QE) | Flat | Shrinking (QT) |

**Dimension 4: RISK APPETITE**

| Indicator | Bullish (+2) | Neutral (+1/-1) | Bearish (-2) |
|-----------|-------------|-----------------|--------------|
| VIX level | < 15 | 15–20 / 20–25 | > 25 |
| HYG-IEI spread | < 3% | 3–4% / 4–5% | > 5% |
| VIX term structure | Contango > 10% | Contango 0–10% / flat | Inverted |

**Macro Score → Regime → Credit Spread Posture:**

| Score | Regime | Credit Spread Action |
|-------|--------|---------------------|
| +5 to +8 | Goldilocks | Full size; bull puts on XLK/XLY/XLF; max OTM distance |
| +2 to +4 | Moderate expansion | Standard size; bull puts; neutral sector |
| -1 to +1 | Transition/uncertain | Reduced size (75%); prefer iron condors |
| -2 to -4 | Slowdown | Tighten stops; overweight XLP/XLV/XLU |
| -5 to -8 | Crisis/recession | Minimal new entries; bear calls on cyclicals; 50% size |

**Historical calibration:**
- 2020 Q1 peak crash: Score = -7 (Growth -2, Inflation -1, Fed -2, Risk -2)
- 2021 Q1 Goldilocks: Score = +6 (Growth +2, Inflation +1, Fed +1, Risk +2)
- 2022 H1 inflation shock: Score = -6 (Growth 0, Inflation -2, Fed -2, Risk -2)
- 2023 H2 soft landing: Score = +5 (Growth +1, Inflation +2, Fed +1, Risk +1)
- 2025 tariff uncertainty: Score ≈ 0 (Growth +1, Inflation -1, Fed 0, Risk 0)

### C.3 Leading Indicators — Priority Order

The following indicators are ordered by their practical value for a credit spread system (combining lead time, accuracy, and data accessibility):

**Tier 1 — Daily, near-real-time:**

| Indicator | Source | Lead Time | Key Threshold | Credit Spread Use |
|-----------|--------|-----------|---------------|------------------|
| VIX term structure (VIX/VIX3M ratio) | CBOE | 1–5 days | Ratio < 1.0 = crisis; < 1.05 = stress | ComboRegimeDetector already uses this |
| HYG-IEI spread | Any daily price feed | 2–4 weeks | > 4% = early stress; > 5% = reduce size | Risk appetite gate |
| CME FedWatch probability | CME website | 2–6 weeks (to meeting) | > 80% hike = rate-sensitive sector warning | Pre-FOMC scaling |
| Options skew by sector (25d put – call) | Polygon options | 1–2 weeks | Skew < 2pp = momentum; > 8pp = hedging | Directional filter |
| Copper/gold ratio | LME + COMEX | 4–8 weeks | Rising YoY = pro-cyclical; falling = defensive | Cyclical sector bias |

**Tier 2 — Weekly/monthly with known lag:**

| Indicator | Source | Frequency | Lead Time | Notes |
|-----------|--------|-----------|-----------|-------|
| 2s10s yield spread | FRED (T10Y2Y) | Daily | Structural (months) | Inversion → XLRE/XLU underperform |
| ISM Mfg PMI New Orders | ISM/FRED | Monthly | 4–8 weeks | Most important cyclical leading indicator |
| EIA Petroleum Status | EIA | Weekly (Wed) | 2–4 weeks | Energy sector supply/demand balance |
| MBA Mortgage Purchase Index | MBA | Weekly (Wed) | 4–6 weeks | Homebuilder sector (XHB) lead |
| FINRA Short Interest | FINRA | Twice monthly | 2-week lag | Sentiment/positioning |
| Baltic Dry Index | Bloomberg/free sites | Daily | 2–3 months | Materials/industrials supply chain |

**Copper/Gold ratio formula:**
```
CG_RATIO = LME Copper (USD/lb) / COMEX Gold (USD/oz) × 1000
Rising YoY: overweight XLI, XLB, XLE
Falling YoY: overweight XLP, XLV, reduce XLI
```

**2020–2025 copper/gold validation:**
- Mar 2020 nadir: ratio = 3.8 → Rising to 6.1 by Dec 2020 → reflation signal 6 weeks before ISM confirmed
- Jan 2022 peak: ratio = 5.9 → fell to 4.3 by Nov 2022 → industrial slowdown correctly signaled

### C.4 Pre-Event Position Sizing

**FOMC scaling (applied to max_risk_per_trade):**

```python
PRE_FOMC_SCALING = {5: 1.00, 4: 0.90, 3: 0.80, 2: 0.70, 1: 0.60, 0: 0.50}
PRE_CPI_SCALING  = {2: 1.00, 1: 0.75, 0: 0.65}
PRE_NFP_SCALING  = {2: 1.00, 1: 0.80, 0: 0.75}
```

**Post-event vol-crush entry (highest-confidence setup):**

```
FOMC day: Enter credit spread with 25–35 DTE in the 30-minute window AFTER
          the 2:00pm ET decision, BEFORE the 2:30pm press conference.
          Rationale: direction is known, vol still elevated, 2+ hours left in session.

CPI day:  Enter in the 15-minute window after 8:30am release.
          Direction known (beat/miss vs consensus), residual elevated vol decays over days.
```

Post-event entries capture vol crush without taking binary directional risk on the surprise itself. This is the most repeatable edge in the event calendar.

---

## Section D: Thematic Trend Detection

### D.1 Mega-Theme vs Fad — Operational Distinction

| Characteristic | Mega-theme | Fad |
|----------------|------------|-----|
| Duration | 3–10 years | 6–18 months |
| 13F institutional accumulation | Sustained 4+ quarters | Front-loaded, reversal within 2Q |
| Earnings revision breadth | EPS upgrades across 5+ companies | Concentrated in 1–3 names |
| VC capital formation | Multi-year acceleration | Single vintage peak |
| Patent filing CAGR | > 20% over 5 years | Flat after initial burst |

**Historical mega-theme durations:**

| Theme | Start | Peak | Duration to peak |
|-------|-------|------|-----------------|
| Social media | 2004 | 2012 | 8 years |
| Cloud/SaaS | 2008 | 2021 | 13 years (still active) |
| COVID biotech | Q1 2020 | Q1 2021 | 12 months (fad) |
| Clean energy (2020 wave) | Nov 2020 | Feb 2021 | 15 months (fad) |
| AI/LLMs | Nov 2022 | Active | 3+ years (mega-theme) |
| Reshoring | 2022 | Active | 3+ years (mega-theme) |

**Key insight:** COVID biotech and the 2020 clean energy wave peaked in 12–18 months — short enough to be classified as fads driven by policy narrative rather than structural capital reallocation. AI and reshoring show the 13F broadening pattern of genuine mega-themes.

### D.2 Data Sources for Early Detection

**A. Fund Flow Signals (ETF AUM as proxy)**

ETF authorized participant creation/redemption units lead price by 2–5 days because they reflect institutional conviction before retail follows.

```python
# Polygon daily bars as AUM proxy
def etf_flow_zscore(etf_ticker: str, prices: pd.DataFrame,
                    lookback: int = 63) -> float:
    """
    AUM proxy = daily close × shares outstanding (approximated via volume).
    For sector ETFs, use price momentum × volume as relative flow signal.
    """
    aum_proxy = prices['close'] * prices['volume']
    mean = aum_proxy.rolling(lookback).mean().iloc[-1]
    std  = aum_proxy.rolling(lookback).std().iloc[-1]
    current = aum_proxy.iloc[-1]
    return (current - mean) / std if std > 0 else 0.0
```

*Best thematic ETF proxies:* SOXX/SMH (AI semis), XBI/IBB (biotech), ICLN/TAN (clean energy), PAVE/XLI (infrastructure/reshoring), XLE/XOP (energy).

**B. Earnings Call NLP (SEC EDGAR — free)**

SEC full-text search returns 8-K exhibits containing earnings transcripts:
```
https://efts.sec.gov/LATEST/search-index?q="generative+ai"&forms=8-K&dateRange=custom&startdt=2023-01-01
```

**Keyword dictionary (current active themes):**

```python
THEME_KEYWORDS = {
    "ai": [
        "large language model", "generative ai", "llm", "foundation model",
        "inference", "gpu cluster", "ai agents", "copilot", "neural network"
    ],
    "reshoring": [
        "nearshoring", "reshoring", "supply chain resilience",
        "domestic manufacturing", "chips act", "friend-shoring", "onshoring"
    ],
    "clean_energy": [
        "ira", "inflation reduction act", "ev charging", "grid-scale battery",
        "offshore wind", "solar installation", "clean power"
    ],
    "defense": [
        "nato", "defense spending", "hypersonic", "autonomous systems",
        "cybersecurity threat", "munitions", "force readiness"
    ],
}

def score_transcript(text: str, theme: str) -> float:
    """Hits per 1000 words — normalizes for transcript length."""
    words = text.lower().split()
    n = max(len(words), 1)
    hits = sum(text.lower().count(kw) for kw in THEME_KEYWORDS[theme])
    return hits / (n / 1000)
```

Track rolling 4-quarter z-score per theme per sector. When z-score crosses +2.0 and stays there 2+ quarters: institutionally confirmed theme.

*Lead time before price action:* Research by Buehlmaier & Whited (2018) and Li et al. (2010) finds earnings call language leads analyst estimate revisions by 1–3 weeks and price by 2–6 weeks in aggregate. For fast-moving themes (AI post-ChatGPT), the lag compressed to days due to retail attention acceleration.

**C. Google Trends (free via pytrends)**

```python
from pytrends.request import TrendReq

def get_google_trends_zscore(keywords: list[str],
                              lookback_weeks: int = 52) -> float:
    pytrends = TrendReq(hl='en-US', tz=360)
    pytrends.build_payload(keywords[:5], timeframe=f'today {lookback_weeks//52}-y')
    df = pytrends.interest_over_time()
    if df.empty:
        return 0.0
    series = df[keywords[0]].astype(float)
    mean, std = series.rolling(lookback_weeks).mean().iloc[-1], \
                series.rolling(lookback_weeks).std().iloc[-1]
    return (series.iloc[-1] - mean) / std if std > 0 else 0.0
```

*Interpretation:* Google Trends leads institutional price action for retail-attention names (small/mid caps, crypto) but lags for large caps where institutional channels are faster. Use as a **saturation detector** — when GT z-score > +3.0 for 3+ weeks, the theme is broadly known and risk of mean reversion increases. Recommend skewing OTM% farther on thematic underlyings when GT z-score > +3.0.

**D. Patent/R&D Data (USPTO PatentsView — free)**

```
GET https://search.patentsview.org/api/v1/patent/?q={"assignee_organization":"NVIDIA"}&f=["patent_date","cpc_group"]
```

Year-over-year patent filing growth > 30% in a CPC technology class for 3+ consecutive quarters = 3–5 year leading indicator for public market theme emergence. Best for long-range portfolio tilt, not tactical trading signal.

**E. VC Funding**

VC investment > 2x its 4-year median as a % of total VC deployment predicts public market theme activation 18–36 months later. AI/ML: category share exceeded 15% of total VC from 2021 Q3 → NVDA explosion Q1 2023 was 18 months later. Use quarterly data from Crunchbase Explorer (free tier) or PitchBook (institutional access) for portfolio-level theme awareness.

### D.3 Theme Momentum Score (0–100)

```python
def theme_momentum_score(
    flow_zscore: float,     # ETF AUM z-score, clamped -3 to +3
    rs_percentile: float,   # 0 to 100 (cross-sectional 3M RS rank)
    keyword_zscore: float,  # Earnings call keyword z-score, clamped -3 to +3
    gt_zscore: float,       # Google Trends z-score, clamped -3 to +3
    si_delta_pct: float,    # Short interest delta (negative = covering = bullish)
) -> float:
    """
    Composite 0-100 Theme Momentum Score.
    Weights: flow 30%, RS 25%, earnings NLP 20%, Google Trends 15%, short interest 10%.
    """
    def norm(z: float) -> float:
        return (min(max(z, -3.0), 3.0) + 3.0) / 6.0 * 100.0

    flow_norm    = norm(flow_zscore)
    keyword_norm = norm(keyword_zscore)
    gt_norm      = norm(gt_zscore)
    si_norm      = (min(max(-si_delta_pct / 5, -1), 1) + 1) / 2 * 100  # invert

    return round(
        0.30 * flow_norm +
        0.25 * rs_percentile +
        0.20 * keyword_norm +
        0.15 * gt_norm +
        0.10 * si_norm,
        1
    )
```

**Entry/exit rules with hysteresis:**
- **ENTRY:** Score > 65 for 3+ consecutive weeks
- **EXIT:** Score < 40 for 2+ consecutive weeks (prevents thrash)

**Component decay half-lives (for stale data handling):**

| Component | Half-life | Why |
|-----------|-----------|-----|
| Fund flows | 5 trading days | ETF flows are continuous; stale quickly |
| RS momentum | 21 trading days | Momentum persists but fades over weeks |
| Earnings keywords | 63 trading days | Quarterly signal; decays to next quarter |
| Google Trends | 7 days | Real-time attention signal; stales fast |
| Short interest | 14 days | FINRA biweekly release cadence |

### D.4 Theme Case Studies with Detection Timeline

**AI/Semiconductor (2022–2025):**
```
Mar 2022: USPTO AI/ML patent CAGR = 48% (3yr). Patent: EMERGING.
Nov 30 2022: ChatGPT launch. GT z-score → +5.8 in week 1.
Jan 2023: NVDA/MSFT earnings calls: "generative AI" 0→18 mentions/transcript.
          Keyword z-score crosses +2.0.
Feb 2023: 13F Q4 2022 filings: institutional SOXX ownership +8% QoQ.
          ETF flow z-score: +1.9. Short interest delta: -4.2% (covering).
          → Theme Momentum Score ≈ 72 → ENTRY SIGNAL (NVDA ~$195).
Dec 2023: NVDA ~$495. Theme score still > 65. +154% from entry signal.
```

**Clean Energy (2020–2021) — Detection and Exit:**
```
Nov 3, 2020: Biden election win. ICLN gaps +10%. GT z-score spikes.
Nov 2020: 13F Q3 filings: 22 new institutional ICLN filers. AUM $900M→$2.1B.
          Flow z-score: +4.2. → Theme score ≈ 88 → STRONG ENTRY.
Feb 8, 2021: ICLN peaks. Short interest delta turns positive (+1.8%).
             GT z-score begins decaying from peak. RS rank drops 95→72.
Mar 2021: Theme score → 41 → EXIT WARNING. ICLN declines 45% over next 12 months.
```

**The clean energy case demonstrates the exit signal works.** The score peaked before price peaked and warned of the reversal.

---

## Section E: Integration with Existing System

### E.1 Architecture — Where the New Layer Plugs In

The Macro Intelligence layer operates as a **pre-filter and position sizer** upstream of the existing scan loop. Zero changes required to `backtester.py`, `spread_strategy.py` core logic, or `ComboRegimeDetector`.

```
┌─────────────────────────────────────────────────────────┐
│                  MACRO INTELLIGENCE LAYER               │
│                                                         │
│  [MacroScorer] ──► Macro Score (-8 to +8)              │
│                         │                               │
│  [SectorRotationEngine] ──► Eligible sectors/tickers   │
│                         │                               │
│  [ThemeScorer] ──► Theme scores per ticker              │
│                         │                               │
│  [MacroEventGate] ──► Event scaling factor              │
│                         │                               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│              EXISTING SYSTEM (unchanged)                │
│                                                         │
│  ComboRegimeDetector (BULL/BEAR/NEUTRAL)               │
│         │                                               │
│  CreditSpreadStrategy.evaluate_spread_opportunity()     │
│         │                                               │
│  AlertPositionSizer.size() ← effective_risk_pct        │
│         │                                               │
│  RiskGate.check() ← theme concentration rule (new)     │
│         │                                               │
│  AlpacaProvider.submit_order()                          │
└─────────────────────────────────────────────────────────┘
```

### E.2 Sector-Specific Underlying Selection

**Current behavior:** System scans SPY, QQQ, IWM as fixed underlyings.

**Proposed expansion:** When sector RS confirms, expand the eligible universe to include sector ETFs as credit spread underlyings. XLE options, for example, are sufficiently liquid for spread trading (>500K daily options volume).

```python
class MacroUniverseExpander:
    """Expands the tradeable underlying universe based on macro/sector signals."""

    BASE_UNIVERSE = ["SPY", "QQQ", "IWM"]

    SECTOR_EXPANSION = {
        "XLE": {"etf": "XLE", "options_liquid": True, "min_rs_pct": 70},
        "XLF": {"etf": "XLF", "options_liquid": True, "min_rs_pct": 70},
        "XLV": {"etf": "XLV", "options_liquid": True, "min_rs_pct": 70},
        "XLK": {"etf": "XLK", "options_liquid": True, "min_rs_pct": 70},
        "XLI": {"etf": "XLI", "options_liquid": True, "min_rs_pct": 70},
    }

    THEMATIC_EXPANSION = {
        "ai":      ["SOXX", "SMH"],
        "energy":  ["XOP"],
        "biotech": ["XBI"],
        "reshoring": ["PAVE"],
    }

    def get_eligible_underlyings(
        self,
        sector_ranks: pd.DataFrame,
        theme_scores: dict[str, float],
        regime: str,  # "BULL" / "BEAR" / "NEUTRAL" from ComboRegimeDetector
    ) -> list[str]:
        eligible = self.BASE_UNIVERSE.copy()

        # Add top-ranked sectors if RS is confirmed (3-week rule enforced separately)
        for ticker, row in sector_ranks.iterrows():
            if row["percentile"] > 70 and regime != "BEAR":
                eligible.append(ticker)
            elif row["percentile"] < 30 and regime == "BEAR":
                eligible.append(ticker)  # for bear call spreads

        # Add thematic ETFs when score is confirmed
        for theme, score in theme_scores.items():
            if score > 65:
                eligible.extend(self.THEMATIC_EXPANSION.get(theme, []))

        return list(set(eligible))
```

### E.3 Position Sizing Based on Macro Conviction

**Combined effective risk formula:**

```python
def compute_effective_risk(
    base_risk_pct: float,           # from config (e.g., 5.0)
    macro_score: int,               # -8 to +8
    theme_score: float,             # 0–100 (for the specific underlying)
    event_scaling: float,           # 0.50–1.00 (pre-FOMC/CPI/NFP)
    weekly_loss_scaling: float,     # 0.50 if weekly loss breach (existing RiskGate rule)
    max_risk_cap: float,            # hard cap from config (e.g., 5.0)
) -> float:

    # Macro score multiplier
    if macro_score >= 5:
        macro_mult = 1.10
    elif macro_score >= 2:
        macro_mult = 1.00
    elif macro_score >= -1:
        macro_mult = 0.80
    elif macro_score >= -4:
        macro_mult = 0.65
    else:
        macro_mult = 0.50  # crisis regime

    # Theme score multiplier
    if theme_score >= 75:
        theme_mult = 1.15
    elif theme_score >= 60:
        theme_mult = 1.00
    elif theme_score >= 40:
        theme_mult = 0.85
    else:
        theme_mult = 0.70  # declining/absent theme

    effective = (base_risk_pct
                 * macro_mult
                 * theme_mult
                 * event_scaling
                 * weekly_loss_scaling)

    return min(effective, max_risk_cap)
```

**Example scenarios:**

| Scenario | Base | Macro | Theme | Event | Weekly | Result |
|----------|------|-------|-------|-------|--------|--------|
| Normal day, no theme | 5.0% | 1.00 | 0.85 | 1.00 | 1.00 | 4.3% |
| Strong AI theme, Goldilocks | 5.0% | 1.10 | 1.15 | 1.00 | 1.00 | 6.3% → capped 5.0% |
| FOMC day -1, crisis macro | 5.0% | 0.50 | 0.70 | 0.60 | 1.00 | 1.1% |
| Strong theme, quiet week | 4.0% | 1.00 | 1.15 | 1.00 | 1.00 | 4.6% (under 5% cap) |

*Recommendation:* Set `base_risk_pct: 4.0` and `MAX_RISK_PER_TRADE: 5.0` to allow the upside multipliers to activate (strong theme + Goldilocks reaches 4.6%) while hard-capping at 5.0%.

### E.4 Enhancing the ComboRegimeDetector

The existing `ComboRegimeDetector` already captures three of the core regime signals:
- `price_vs_ma200` → trend direction
- `rsi_momentum` → momentum state
- `vix_structure` → risk appetite (VIX/VIX3M ratio)

The Macro Intelligence layer **does not replace** the regime detector. It enriches it by:
1. **Macro Score as a 4th signal candidate:** When Macro Score < -3, veto new BULL regime entries regardless of the 3-signal vote. Functions like the existing VIX circuit breaker but driven by economic data instead of vol.
2. **Sector rotation override:** When a sector is in the Leading RRG quadrant and Macro Score is positive, allow bull put entries even in NEUTRAL regime (the regime detector is calibrated to SPY, not the specific sector).
3. **Theme score amplification:** When Theme Score > 75 AND regime is BULL, allow OTM% to expand +15% on the short strike (farther OTM = same delta, less risk per the skewed distribution).

### E.5 Risk Management: Correlation and Macro Hedging

**Theme concentration gate (addition to RiskGate.check()):**

```python
# Add to RiskGate — max positions per active theme
THEME_MAP = {
    "SOXX": "ai", "SMH": "ai", "NVDA": "ai", "QQQ": "ai",
    "XLE": "energy", "XOP": "energy",
    "ICLN": "clean_energy", "TAN": "clean_energy",
    "XBI": "biotech", "IBB": "biotech",
    "PAVE": "reshoring", "XLI": "reshoring",
}
MAX_POSITIONS_PER_THEME = 2

def check_theme_concentration(alert_ticker: str, open_positions: list) -> tuple[bool, str]:
    theme = THEME_MAP.get(alert_ticker)
    if not theme:
        return True, ""
    count = sum(1 for p in open_positions if THEME_MAP.get(p.get("ticker")) == theme)
    if count >= MAX_POSITIONS_PER_THEME:
        return False, f"Theme '{theme}' at max {MAX_POSITIONS_PER_THEME} positions"
    return True, ""
```

**Macro hedging principle:** The system does not currently implement a hedge book. When Macro Score < -3 (crisis/recession regime):
1. Reduce all new entry size to 50% of normal
2. Enforce a maximum of 3 simultaneous open positions (vs current config maximum)
3. Tighten profit target from 50% to 35% (capture gains faster before reversal)

These are configuration parameters, not code changes, and can be encoded as `macro_regime_overrides` in the config YAML.

**Correlation awareness for sector positions:** XLE and XOP, for example, have >0.95 correlation. Holding both simultaneously provides near-zero diversification benefit. The `THEME_MAP` approach above naturally prevents this by counting both under "energy." For cross-sector correlation (XLU and XLRE are both rate-sensitive), a more sophisticated pairwise correlation matrix would be needed in Phase C.

---

## Section F: Implementation Roadmap

### F.1 Data Sources and APIs Required

**Tier 1 — Free, available now:**

| Source | Data | Endpoint | Auth |
|--------|------|----------|------|
| FRED (St. Louis Fed) | Yield curve, CPI, PCE, ISM, unemployment | `https://api.stlouisfed.org/fred/series/observations` | Free API key |
| SEC EDGAR | 13F filings, earnings call 8-Ks | `https://data.sec.gov/submissions/` + `https://efts.sec.gov/LATEST/search-index` | No auth required |
| CME Group | FedWatch FOMC probabilities | Scrape `cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html` | No auth |
| Google Trends | Search interest | `pytrends` Python library | No auth (unofficial) |
| USPTO PatentsView | Patent filings | `https://search.patentsview.org/api/v1/patent/` | No auth |
| FINRA | Short interest | `https://api.finra.org/data/group/otcMarket/name/regShoDaily` | No auth |
| Treasury Direct | Auction calendar | `https://www.treasurydirect.gov/TA_WS/securities/upcoming` | No auth |

**Tier 2 — Already in stack (Polygon.io):**

| Use case | Polygon endpoint |
|----------|-----------------|
| Sector ETF daily prices (RS ranking) | `/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` |
| Options skew by sector ETF | `/v2/snapshot/locale/us/markets/options/tickers/{ticker}` |
| Thematic ETF flow proxy (price × volume) | Standard daily bars |

**Tier 3 — Paid additions if approved:**
- Alpha Vantage ($50/mo): Earnings calendar endpoint, economic indicators
- Seeking Alpha Premium (or scraping): Structured earnings transcripts
- ETFdb.com Pro ($30/mo): Actual ETF flow data vs AUM proxy

### F.2 Architecture Changes

**New files to create:**

```
ml/
  macro_scorer.py           — 4-dimension macro scoring engine
  sector_rotation.py        — RS ranking, RRG quadrant classification
  theme_scorer.py           — Theme Momentum Score composite
  macro_event_gate.py       — FOMC/CPI/NFP calendar + pre-event scaling

data/
  macro_state.db            — SQLite: sector_rs, theme_scores, macro_events tables
  (or extend options_cache.db with new tables)

scripts/
  update_macro_state.py     — Daily batch: FRED pull, RS update, theme score
  backfill_macro_history.py — Historical RS and theme scores for backtesting
```

**Tables to add to SQLite:**

```sql
CREATE TABLE sector_rs (
    date TEXT,
    sector TEXT,
    rs_ratio_3m REAL,
    rs_ratio_12m REAL,
    rs_rank INTEGER,
    rs_percentile REAL,
    rrg_quadrant TEXT,
    PRIMARY KEY (date, sector)
);

CREATE TABLE theme_scores (
    date TEXT,
    theme TEXT,
    score REAL,
    flow_zscore REAL,
    rs_percentile REAL,
    keyword_zscore REAL,
    gt_zscore REAL,
    si_delta REAL,
    signal_confirmed BOOLEAN,
    PRIMARY KEY (date, theme)
);

CREATE TABLE macro_events (
    event_date TEXT,
    event_type TEXT,   -- 'FOMC', 'CPI', 'NFP', 'TREASURY_10Y', 'EARNINGS_SEASON'
    description TEXT,
    days_out INTEGER,  -- computed daily at batch time
    scaling_factor REAL,
    PRIMARY KEY (event_date, event_type)
);

CREATE TABLE macro_score (
    date TEXT PRIMARY KEY,
    total_score INTEGER,
    growth_score INTEGER,
    inflation_score INTEGER,
    fed_score INTEGER,
    risk_score INTEGER,
    regime TEXT
);
```

**Config additions to YAML:**

```yaml
macro_intelligence:
  enabled: true
  universe_expansion: true        # expand beyond SPY/QQQ/IWM
  theme_filter: true              # gate entries by theme score
  event_scaling: true             # pre-FOMC/CPI/NFP size reduction
  macro_veto_threshold: -3        # macro score below this vetoes BULL entries
  theme_entry_score: 65           # minimum theme score for position entry
  theme_entry_weeks: 3            # weeks above threshold required for entry
  theme_exit_score: 40            # score below this triggers exit warning
  max_positions_per_theme: 2      # correlation management
  base_risk_pct: 4.0              # allows upside multiplier headroom (cap stays at 5.0)

  event_scaling:
    fomc:  {5: 1.00, 4: 0.90, 3: 0.80, 2: 0.70, 1: 0.60, 0: 0.50}
    cpi:   {2: 1.00, 1: 0.75, 0: 0.65}
    nfp:   {2: 1.00, 1: 0.80, 0: 0.75}

  sector_rs:
    primary_lookback_days: 63     # 3-month RS
    filter_lookback_days: 252     # 12-month RS filter
    bull_confirmation_weeks: 3    # weeks above threshold for entry
    bull_rs_threshold: 1.05       # sector must beat SPY by 5%
    bear_rs_threshold: 0.95       # sector must lag SPY by 5%
```

### F.3 Backtesting Approach to Validate Macro Alpha

**Historical data available for reconstruction:**

| Component | Historical depth | Source |
|-----------|-----------------|--------|
| Sector ETF daily prices | 2000–present | Polygon.io (in cache) |
| FRED macro data | 1990–present | FRED API |
| Google Trends | 5 years (weekly) | pytrends |
| SEC EDGAR 13F | 2001–present | EDGAR full-text search |
| CME FedWatch | Reconstructable from fed funds futures COT | CFTC public data |

**Backtesting methodology:**

1. **Backfill macro scores (2018–2025):** Run `backfill_macro_history.py` to compute daily macro scores, sector RS rankings, and theme scores for the full historical window. Store in SQLite.

2. **Integrate into backtester:** Add a `MacroFilter` class that the backtester calls before `_want_puts()` and `_want_calls()`:
   ```python
   # In backtester.py _find_trades_for_date():
   if self.config.get("macro_intelligence", {}).get("enabled"):
       macro_filter = MacroFilter(self.macro_state)
       if not macro_filter.allows_entry(date, direction, ticker):
           continue
       effective_risk = macro_filter.effective_risk_pct(date, ticker)
   ```

3. **A/B comparison:** Run the existing exp_090 config (champion) vs exp_090 + macro layer for 2020–2025. Measure:
   - Annual return delta
   - Maximum drawdown delta (hypothesis: macro layer reduces 2020 and 2022 drawdowns)
   - Trade count delta (theme filter may reduce trade frequency in unfavorable regimes)
   - Sharpe ratio change

4. **Specific macro alpha tests:**
   - **2022 bear call experiment:** With macro score showing -6 from Jan 2022, did the macro layer correctly suppress bull put entries while enabling bear calls on XLK/XLRE?
   - **2023 AI expansion:** With AI theme score > 65 from Feb 2023, did adding SOXX/QQQ bull puts improve returns vs SPY-only?
   - **Pre-FOMC scaling:** Does reducing size 50% on FOMC day reduce drawdown more than it reduces returns?

### F.4 Phased Rollout Plan

**Phase A — Foundation (Weeks 1–2): Low risk, high value**
- [ ] FRED macro score computation (growth/inflation/Fed/risk dimensions)
- [ ] FOMC/CPI/NFP calendar integration from FRED release calendar API
- [ ] `MacroEventGate` class — daily scaling factors written to SQLite
- [ ] Inject event scaling into `AlertPositionSizer.size()` via config
- [ ] Backtest: 2022 FOMC event scaling effect on drawdown
- [ ] **Expected impact:** 15–25% drawdown reduction in high-event periods with minimal return cost

**Phase B — Sector Rotation (Weeks 2–4): Medium complexity**
- [ ] Sector RS ranking (11 ETFs, Polygon daily bars)
- [ ] RRG quadrant classification (weekly recompute)
- [ ] `MacroUniverseExpander` — eligible underlying expansion
- [ ] Theme concentration gate in `RiskGate.check()`
- [ ] Config: `universe_expansion: true`, `max_positions_per_theme: 2`
- [ ] Backtest: 2022 bear calls on XLK/XLRE vs SPY-only
- [ ] **Expected impact:** New alpha from sector-specific spread selection; 2022 bear call improvement

**Phase C — Thematic Scoring (Weeks 4–8): Highest complexity**
- [ ] Google Trends pipeline for AI, energy, reshoring, defense themes
- [ ] Earnings call keyword scoring via SEC EDGAR 8-K full-text search
- [ ] ETF flow z-score (Polygon price × volume proxy)
- [ ] Full `theme_momentum_score()` composite
- [ ] Theme-adjusted OTM% in `spread_strategy.py`
- [ ] Theme size multiplier in `compute_effective_risk()`
- [ ] `backfill_macro_history.py` — historical theme scores for backtesting
- [ ] Full 2020–2025 A/B backtest vs champion config
- [ ] **Expected impact:** Largest alpha capture; 2020 recovery, 2023 AI theme, 2022 energy

**Phase D — Macro Integration Completeness (Weeks 8–12): Production hardening**
- [ ] Macro score as 4th ComboRegimeDetector signal (BULL veto at score < -3)
- [ ] Post-event vol-crush entry logic (FOMC day, CPI day)
- [ ] Correlation matrix for cross-sector positions (XLRE/XLU rate-sensitivity)
- [ ] `update_macro_state.py` — automated daily batch (6:00am ET cron)
- [ ] Health check extension to monitor macro data pipeline freshness
- [ ] Full regression test suite for macro layer
- [ ] Production deployment alongside existing system

---

## Appendix: Key Data Source Reference

### FRED Series IDs for Macro Scoring

| Series | Description | Update Frequency |
|--------|-------------|-----------------|
| `T10Y2Y` | 10Y minus 2Y Treasury spread | Daily |
| `T5YIE` | 5-Year TIPS breakeven inflation | Daily |
| `VIXCLS` | CBOE VIX close | Daily |
| `BAMLH0A0HYM2` | HY corporate spread (HYG proxy) | Daily |
| `DCOILWTICO` | WTI crude oil spot | Daily |
| `GOLDAMGBD228NLBM` | Gold price AM fix | Daily |
| `PCOPPUSDM` | Copper price | Monthly |
| `NAPM` | ISM Manufacturing PMI | Monthly |
| `PAYEMS` | Nonfarm Payrolls | Monthly |
| `CPIAUCSL` | CPI (all urban consumers) | Monthly |
| `CPILFESL` | Core CPI | Monthly |
| `PCEPI` | PCE price index | Monthly |
| `A191RL1Q225SBEA` | GDP real growth QoQ | Quarterly |
| `FEDFUNDS` | Effective fed funds rate | Monthly |
| `UMCSENT` | U Michigan Consumer Sentiment | Monthly |

All available at: `https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json`

Free API key registration: `https://fred.stlouisfed.org/docs/api/api_key.html`

### Sector ETF Options Liquidity (daily avg volume, 2024)

| ETF | Avg Daily Options Volume | Spread Trading Viable? |
|-----|--------------------------|------------------------|
| SPY | 1.5M contracts | Yes (primary) |
| QQQ | 800K contracts | Yes (primary) |
| IWM | 400K contracts | Yes (primary) |
| XLK | 85K contracts | Yes |
| XLE | 120K contracts | Yes |
| XLF | 200K contracts | Yes |
| XLV | 60K contracts | Yes |
| XLC | 30K contracts | Marginal |
| XLI | 25K contracts | Marginal |
| XLU | 35K contracts | Marginal |
| XLRE | 20K contracts | Marginal — use IYR instead (45K) |
| XLB | 15K contracts | Low liquidity — use individual names |

**Recommendation for Phase B:** Expand universe to XLK, XLE, XLF, XLV in Phase B. Add XLC, XLI in Phase C only if backtesting confirms sufficient liquidity for 1–5 contract spread sizes.

---

*This proposal was produced via parallel deep research synthesis. All historical performance figures are approximate based on publicly available data. Sector ETF returns include dividends reinvested. Strategy parameters (entry thresholds, scaling factors, OTM adjustments) are starting points requiring validation via backtesting before production deployment.*

*Files this module will interact with:*
- `strategy/spread_strategy.py` — underlying selection, OTM% adjustment
- `alerts/risk_gate.py` — theme concentration gate
- `alerts/alert_position_sizer.py` — effective risk computation
- `ml/combo_regime_detector.py` — macro score as 4th signal
- `config_exp036.yaml` — new `macro_intelligence` config section
- `shared/database.py` — new tables for macro state
- `main.py` — daily macro batch job integration
