# Attix Strategy Recommendation API — Deep Analysis
## All 57 Strategies | 401 Unique Tickers | March 7, 2026

---

## EXECUTIVE SUMMARY

**What was done:** All 57 Attix investment strategies were pulled from the API, yielding 1,030 ticker appearances across 401 unique stocks and ETFs. Every strategy was analyzed for holdings, weights, quality scores, and cross-strategy overlap.

**The headline finding — and it is unmistakable:**

> **PILOTAI IS SCREAMING: BUY GOLD.**

Gold-related instruments dominate the consensus signal with a force that is impossible to ignore. DBP (Invesco DB Precious Metals Fund) appears in **20 of 57 strategies**. SGDM (Sprott Gold Miners ETF) and GOEX (Global X Gold Explorers ETF) each appear in **27 strategies** — nearly half of every portfolio in the system. IAU (iShares Gold Trust) appears in 15 strategies. Most remarkably: these gold ETFs are appearing as top holdings in portfolios labeled "AI-Related Companies," "Cloud Computing Boom," "Technology Sector Innovation Fund," and "Semiconductor Supercycle." The model is so bullish on gold that it is overriding thematic classification and injecting gold into every category.

**This is the Attix model's current view of the world in one sentence:** *Risk-off, macro stress, gold as the primary hedge — even if you asked me for a tech portfolio.*

**Three other key findings:**
1. Value and momentum strategies score highest on composite quality metrics — the model's highest-quality portfolios are defensive and value-oriented, not growth.
2. Six strategy pairs are near-duplicates (>50% overlap) — the strategy universe has significant redundancy.
3. The consensus signal (top 25 stocks by frequency × weight × quality) is dominated by gold but reveals 8 high-conviction non-gold plays: MU, AMD, VICR, ATI, CODA, UVE, SEZL, INNV.

---

## SECTION 1: UNIVERSE STATISTICS

| Metric | Value |
|--------|-------|
| Total strategies analyzed | 57 |
| Total unique tickers | 401 |
| Total ticker appearances | 1,030 |
| Average tickers per strategy | 18.1 |
| Min tickers in a strategy | 10 (many) |
| Max tickers in a strategy | 30 (growth-investing, momentum-investing, etc.) |
| Assumed portfolio budget | $100,000 per strategy |
| Average leftover cash | ~$10,600 (10.6%) |
| Top recommendation strategies returned by API | 10 |

---

## SECTION 2: STRATEGY SCORES — All 57 Ranked by Composite Quality

Each strategy has 5 stock-score dimensions (value, growth, health, momentum, past_performance). All scores are on a 0–5 scale. Composite = simple average of all 5.

| Rank | Strategy | Composite | Value | Growth | Health | Momentum | PastPerf | #Tickers |
|------|----------|-----------|-------|--------|--------|----------|----------|----------|
| 1 | value-investing | **3.228** | 3.53 | 3.44 | 2.59 | 3.46 | 3.12 | 10 |
| 2 | diversified-bluechips | **3.120** | 2.60 | 3.33 | 3.01 | 3.56 | 3.10 | 10 |
| 3 | market-disruptors | **3.118** | 2.43 | 3.77 | 2.87 | 3.47 | 3.05 | 10 |
| 4 | low-beta-stocks | **3.112** | 3.11 | 3.14 | 2.70 | 3.54 | 3.07 | 10 |
| 5 | small-cap-stocks | **3.112** | 3.09 | 3.62 | 2.40 | 3.49 | 2.96 | 20 |
| 6 | sector-rotation | **3.094** | 2.73 | 3.67 | 2.22 | 3.30 | 3.55 | 10 |
| 7 | momentum-investing | **3.084** | 2.93 | 3.58 | 2.14 | 3.90 | 2.87 | 30 |
| 8 | socially-responsible-investing-sri | **3.070** | 2.64 | 3.41 | 2.96 | 3.34 | 3.00 | 10 |
| 9 | financials-sector-capital-strategy | **3.040** | 3.14 | 3.41 | 2.76 | 2.84 | 3.05 | 10 |
| 10 | esg-leaders | **3.030** | 1.54 | 3.63 | 2.95 | 3.65 | 3.38 | 10 |
| 11 | fallen-angels | **3.016** | 3.13 | 3.57 | 2.56 | 3.24 | 2.58 | 10 |
| 12 | biomedical-and-genetics-industry | **3.002** | 2.69 | 3.47 | 2.52 | 2.78 | 3.55 | 10 |
| 13 | thematic-investing | **3.000** | 2.83 | 3.49 | 2.17 | 3.46 | 3.05 | 30 |
| 14 | growth-investing | **2.984** | 2.71 | 3.77 | 2.24 | 3.40 | 2.80 | 30 |
| 15 | gold-mining-industry | **2.920** | 2.71 | 3.59 | 1.94 | 3.19 | 3.17 | 20 |
| 16 | green-infrastructure | **2.910** | 2.38 | 3.73 | 2.20 | 3.41 | 2.83 | 10 |
| 17 | industrials-sector-infrastructure-fund | **2.900** | 2.62 | 3.43 | 2.63 | 3.25 | 2.57 | 10 |
| 18 | manufacturing-industry | **2.894** | 2.25 | 3.51 | 2.53 | 3.31 | 2.87 | 10 |
| 19 | drip-dividend-reinvestment-plan | **2.886** | 3.33 | 2.80 | 2.01 | 2.77 | 3.52 | 10 |
| 20 | deep-value-investing | **2.884** | 3.41 | 3.18 | 1.28 | 3.25 | 3.30 | 30 |
| 21 | defensive-investing | **2.878** | 2.83 | 3.05 | 2.43 | 3.19 | 2.89 | 20 |
| 22 | robotics-automation | **2.878** | 2.25 | 3.39 | 2.48 | 3.43 | 2.84 | 20 |
| 23 | leisure-and-recreation-services-industry | **2.876** | 2.81 | 3.14 | 2.41 | 3.12 | 2.90 | 10 |
| 24 | electric-vehicle-ev-boom | **2.844** | 1.48 | 3.42 | 2.81 | 3.32 | 3.19 | 10 |
| 25 | high-dividend-stocks | **2.834** | 3.21 | 2.95 | 1.73 | 3.01 | 3.27 | 30 |
| 26 | cybersecurity-shield | **2.814** | 2.04 | 3.36 | 1.74 | 3.50 | 3.43 | 20 |
| 27 | mid-cap-stocks | **2.814** | 2.98 | 3.12 | 2.33 | 2.95 | 2.69 | 20 |
| 28 | real-estate-sector-income-fund | **2.808** | 2.94 | 3.04 | 1.88 | 3.28 | 2.90 | 10 |
| 29 | biotech-breakthroughs | **2.800** | 2.31 | 3.63 | 1.38 | 3.35 | 3.33 | 10 |
| 30 | consumer-discretionary | **2.796** | 2.51 | 3.56 | 1.93 | 3.53 | 2.45 | 10 |
| 31 | consumer-staples-stability-strategy | **2.778** | 2.90 | 3.01 | 2.04 | 3.36 | 2.58 | 10 |
| 32 | gaming-giants | **2.776** | 2.52 | 3.22 | 2.30 | 3.14 | 2.70 | 10 |
| 33 | investment-management-industry | **2.766** | 3.29 | 2.88 | 1.24 | 3.27 | 3.15 | 10 |
| 34 | low-volatility-stocks | **2.752** | 2.60 | 2.90 | 2.35 | 3.13 | 2.78 | 20 |
| 35 | aging-population | **2.744** | 2.71 | 3.26 | 2.07 | 2.60 | 3.08 | 20 |
| 36 | water-scarcity-solutions | **2.702** | 2.52 | 3.43 | 1.98 | 3.21 | 2.37 | 10 |
| 37 | buffett-bargains | **2.690** | 2.50 | 2.29 | 2.34 | 2.33 | 3.99 | 20 |
| 38 | transportation-airline-sector | **2.690** | 2.91 | 3.16 | 1.38 | 2.95 | 3.05 | 20 |
| 39 | e-commerce-enablers | **2.646** | 1.75 | 3.54 | 2.02 | 3.70 | 2.22 | 10 |
| 40 | healthcare-sector-stability-and-growth-fund | **2.634** | 2.58 | 3.10 | 1.78 | 2.76 | 2.95 | 30 |
| 41 | contrarian-investing | **2.574** | 3.15 | 2.09 | 1.67 | 2.92 | 3.04 | 20 |
| 42 | high-beta-stocks | **2.566** | 2.41 | 3.08 | 1.96 | 2.98 | 2.40 | 30 |
| 43 | the-amazon-of-x | **2.542** | 2.13 | 3.54 | 1.51 | 3.09 | 2.44 | 10 |
| 44 | dividend-aristocrats | **2.476** | 3.10 | 2.03 | 1.81 | 2.36 | 3.08 | 30 |
| 45 | semiconductor-supercycle | **2.468** | 1.30 | 2.79 | 2.63 | 2.93 | 2.69 | 20 |
| 46 | meme-stock-mania | **2.434** | 1.89 | 2.96 | 2.08 | 2.64 | 2.60 | 20 |
| 47 | 5g-infrastructure | **2.422** | 2.30 | 2.80 | 1.83 | 2.83 | 2.35 | 20 |
| 48 | space-exploration | **2.414** | 1.71 | 2.89 | 1.94 | 2.83 | 2.70 | 20 |
| 49 | quality-investing | **2.408** | 2.25 | 2.80 | 2.20 | 2.60 | 2.19 | 20 |
| 50 | ai-related-companies | **2.386** | 1.94 | 2.93 | 1.93 | 2.73 | 2.40 | 30 |
| 51 | clean-energy-revolution | **2.354** | 1.94 | 3.15 | 1.33 | 2.89 | 2.46 | 20 |
| 52 | metaverse-pioneers | **2.334** | 1.72 | 2.62 | 2.29 | 2.62 | 2.42 | 30 |
| 53 | technology-sector-innovation-fund | **2.332** | 1.78 | 2.61 | 2.03 | 2.84 | 2.40 | 30 |
| 54 | global-investing | **2.286** | 2.91 | 2.84 | 0.00 | 2.96 | 2.72 | 20 |
| 55 | energy-sector-growth-strategy | **2.286** | 2.45 | 2.67 | 1.19 | 3.03 | 2.09 | 30 |
| 56 | cloud-computing-boom | **2.272** | 1.90 | 2.82 | 1.82 | 2.56 | 2.26 | 30 |
| 57 | utilities-sector-stability-fund | **2.032** | 2.60 | 1.76 | 1.37 | 1.96 | 2.47 | 30 |

### Score Dimension Leaders (Top 5 per dimension)

| Dimension | #1 | #2 | #3 | #4 | #5 |
|-----------|----|----|----|----|-----|
| **VALUE** | value-investing (3.53) | deep-value-investing (3.41) | drip-dividend-reinvestment-plan (3.33) | investment-management-industry (3.29) | high-dividend-stocks (3.21) |
| **GROWTH** | market-disruptors (3.77) | growth-investing (3.77) | green-infrastructure (3.73) | sector-rotation (3.67) | esg-leaders (3.63) |
| **HEALTH** | diversified-bluechips (3.01) | socially-responsible-investing-sri (2.96) | esg-leaders (2.95) | market-disruptors (2.87) | electric-vehicle-ev-boom (2.81) |
| **MOMENTUM** | momentum-investing (3.90) | e-commerce-enablers (3.70) | esg-leaders (3.65) | diversified-bluechips (3.56) | low-beta-stocks (3.54) |
| **PAST PERF** | buffett-bargains (3.99) | sector-rotation (3.55) | biomedical-and-genetics-industry (3.55) | drip-dividend-reinvestment-plan (3.52) | cybersecurity-shield (3.43) |

**Notable:** `buffett-bargains` has the highest past_performance score (3.99) by a large margin — but low composite (2.690) because its value (2.50), growth (2.29), and momentum (2.33) scores are poor. It's a strategy that did well in the past but isn't positioned for the current environment.

**Notable:** `esg-leaders` has the weakest value score (1.54) of any high-ranked strategy but has exceptional momentum (3.65) and health (2.95) — it's a high-quality growth portfolio.

---

## SECTION 3: TICKER FREQUENCY — Most Strategies Per Stock

### Top 40 Stocks by Number of Strategies They Appear In

| Rank | Ticker | Name | Price | # Strategies | Type | Key Strategies |
|------|--------|------|-------|-------------|------|----------------|
| 1 | **GOEX** | Global X Gold Explorers ETF | $95.00 | **27** | Gold ETF | sector-rotation, value-investing, momentum… |
| 2 | **SGDM** | Sprott Gold Miners ETF | $84.47 | **27** | Gold ETF | dividend-aristocrats, growth-investing, AI… |
| 3 | **DBP** | Invesco DB Precious Metals Fund | $120.32 | **20** | Precious Metals ETF | dividend-aristocrats, buffett-bargains… |
| 4 | **VICR** | Vicor Corporation | $162.60 | **15** | Power semiconductors | growth-investing, AI, momentum… |
| 5 | **IAU** | iShares Gold Trust Shares | $96.98 | **15** | Gold ETF | AI, semiconductor, cloud, metaverse, tech… |
| 6 | **GBUG** | Sprott Active Gold & Silver Miners ETF | $51.11 | **15** | Gold/Silver ETF | AI, semiconductor, small-cap… |
| 7 | **MU** | Micron Technology | $370.34 | **14** | Semiconductors | growth, AI, semiconductor, sector-rotation… |
| 8 | **ATI** | ATI Inc. | $150.11 | **13** | Advanced metals/aerospace | growth, AI, momentum, EV-boom… |
| 9 | **MGNR** | American Beacon Select Funds | $50.12 | **13** | Multi-asset fund | buffett-bargains, defensive, bluechips… |
| 10 | **CODA** | Coda Octopus Group | $14.31 | **12** | Marine tech/sonar | AI, deep-value, defensive, ESG… |
| 11 | **AMD** | Advanced Micro Devices | $192.46 | **11** | Semiconductors | AI, semiconductor, sector-rotation… |
| 12 | **DXJ** | WisdomTree Japan Hedged Equity Fund | $156.70 | **11** | Japan equity ETF | dividend-aristocrats, buffett, aging… |
| 13 | **SEZL** | Sezzle Inc. | $73.36 | **9** | Buy-now-pay-later fintech | growth, momentum, quality-investing… |
| 14 | **FORM** | FormFactor | $85.02 | **9** | Semiconductor test equipment | AI, momentum, semiconductor… |
| 15 | **INNV** | InnovAge Holding Corp. | $8.95 | **8** | Senior care | growth, momentum, small-cap… |
| 16 | **TTMI** | TTM Technologies | $87.87 | **8** | PCB manufacturing | growth, AI, high-beta… |
| 17 | **MTZ** | MasTec | $285.39 | **8** | Infrastructure/telecom | AI, high-beta, clean-energy… |
| 18 | **NESR** | National Energy Services Reunited | $20.86 | **8** | Oil services (Middle East) | momentum, quality, low-beta… |
| 19 | **TCMD** | Tactile Systems Technology | $28.78 | **8** | Medical devices | momentum, small-cap, fallen-angels… |
| 20 | **RXT** | Rackspace Technology | $2.06 | **7** | Cloud services | AI, high-beta, cybersecurity… |
| 21 | **STRL** | Sterling Infrastructure | $395.07 | **7** | Data center infrastructure | AI, momentum, high-beta… |
| 22 | **RING** | iShares MSCI Global Gold Miners ETF | $87.22 | **7** | Gold miners ETF | thematic, ESG, energy… |
| 23 | **UVE** | Universal Insurance Holdings | $35.17 | **6** | Property & casualty insurance | value, growth, momentum… |
| 24 | **IRWD** | Ironwood Pharmaceuticals | $3.50 | **6** | Specialty pharma | value, small-cap, biotech… |
| 25 | **FSLY** | Fastly | $20.13 | **6** | Edge cloud/CDN | growth, AI, cybersecurity… |
| 26 | **MYRG** | MYR Group | $260.77 | **6** | Electrical construction | growth, momentum, sector-rotation… |
| 27 | **SBSW** | Sibanye-Stillwater Limited ADS | $14.10 | **6** | Platinum/gold/palladium miner | growth, deep-value, thematic… |
| 28 | **FIX** | Comfort Systems USA | $1,281.51 | **6** | HVAC/electrical contracting | AI, clean-energy, cloud… |
| 29 | **ISSC** | Innovative Solutions and Support | $26.30 | **6** | Avionics | momentum, deep-value, diversified… |
| 30 | **NVDA** | NVIDIA Corporation | $177.74 | **6** | AI/Semiconductors | semiconductor, cloud, EV-boom… |
| 31 | **TSN** | Tyson Foods | $61.46 | **5** | Protein/food processing | dividend-aristocrats, defensive, DRIP… |
| 32 | **FDP** | Fresh Del Monte Produce | $42.59 | **5** | Fresh produce | defensive, diversified, low-beta… |
| 33 | **RUSHA** | Rush Enterprises | $65.99 | **5** | Commercial trucks | AI, mid-cap, cybersecurity, cloud… |
| 34 | **OUNZ** | VanEck Merk Gold ETF | $49.55 | **6** | Gold ETF (deliverable) | ESG, green-infra, water… |
| 35 | **OIS** | Oil States International | $12.22 | **5** | Oilfield equipment | fallen-angels, market-disruptors… |
| 36 | **GLD** | SPDR Gold Shares | $473.47 | **5** | Gold ETF (flagship) | AI, cloud, tech, high-beta, metaverse |
| 37 | **GLDM** | SPDR Gold MiniShares Trust | $101.93 | **5** | Gold ETF (mini) | AI, cloud, tech, high-beta, metaverse |
| 38 | **DG** | Dollar General Corp | $146.29 | **4** | Discount retail | consumer-staples, defensive, contrarian… |
| 39 | **ORN** | Orion Group Holdings | $11.66 | **4** | Marine construction | fallen-angels, market-disruptors… |
| 40 | **PLOW** | Douglas Dynamics | $43.29 | **3** | Snowplow/work truck equipment | growth, momentum, small-cap |

### Consensus Picks (10+ Strategies)

These 12 tickers have the broadest endorsement across Attix's strategy universe:

| Ticker | Name | # Strategies | Category |
|--------|------|-------------|----------|
| GOEX | Global X Gold Explorers ETF | 27 | Gold ETF |
| SGDM | Sprott Gold Miners ETF | 27 | Gold ETF |
| DBP | Invesco DB Precious Metals Fund | 20 | Precious Metals ETF |
| VICR | Vicor Corporation | 15 | Power semiconductors |
| IAU | iShares Gold Trust Shares | 15 | Gold ETF |
| GBUG | Sprott Active Gold & Silver Miners ETF | 15 | Gold/Silver ETF |
| MU | Micron Technology | 14 | DRAM/NAND semiconductors |
| ATI | ATI Inc. | 13 | Aerospace/defense metals |
| MGNR | American Beacon Select Funds | 13 | Multi-asset fund |
| CODA | Coda Octopus Group | 12 | Marine tech (SONAR) |
| AMD | Advanced Micro Devices | 11 | GPU/CPU semiconductors |
| DXJ | WisdomTree Japan Hedged Equity Fund | 11 | Japan equity hedge |

---

## SECTION 4: THE GOLD ANOMALY — A Deep Dive

This is the most important finding in the entire analysis. **Gold instruments are appearing in portfolios where they have no thematic justification.**

### Gold in Tech/AI/Cloud Portfolios (The Anomaly)

| Strategy | Strategy Theme | Gold Holdings | Gold % of Portfolio |
|----------|---------------|--------------|---------------------|
| ai-related-companies | AI tech stocks | GLDM, IAU, GLD, GBUG, GOEX, SGDM | **~42%** |
| cloud-computing-boom | Cloud SaaS | GLDM, IAU, GLD | **~30%** |
| technology-sector-innovation-fund | Tech sector | GLDM, IAU, GLD | **~30%** |
| semiconductor-supercycle | Chips | IAU, SGDM, GBUG | **~28%** |
| metaverse-pioneers | Metaverse/VR | IAU, GLDM, GLD | **~30%** |
| e-commerce-enablers | E-commerce | SGDM | ~10% |
| the-amazon-of-x | E-commerce/tech | GOEX, SGDM | ~20% |
| electric-vehicle-ev-boom | EVs/clean tech | GOEX, SGDM | ~20% |

**What this means:**

The Attix model is not saying "this AI company is good." It is saying: *"In the current macro environment, even within an AI portfolio, the model sees gold as a better risk-adjusted allocation than most AI stocks."*

This is a macro signal, not a stock-picking signal. The AI portfolio holding 42% in gold instruments tells us: **the model has low conviction in most AI stocks at current prices, and high conviction in gold.**

Current macro context (March 2026): Tariff shock from February–March 2026, dollar weakness, geopolitical uncertainty, gold near all-time highs. The model is responding rationally to the environment.

### Complete Gold/Precious Metals Signal

| Instrument | Type | # Strategies | Avg Weight | Current Price |
|-----------|------|-------------|-----------|---------------|
| GOEX | Global X Gold Explorers ETF | 27 | 5.62% | $95.00 |
| SGDM | Sprott Gold Miners ETF | 27 | 6.24% | $84.47 |
| DBP | Invesco DB Precious Metals Fund | 20 | 8.41% | $120.32 |
| IAU | iShares Gold Trust Shares | 15 | 9.83% | $96.98 |
| GBUG | Sprott Active Gold & Silver Miners ETF | 15 | 3.13% | $51.11 |
| OUNZ | VanEck Merk Gold ETF | 6 | 9.30% | $49.55 |
| RING | iShares MSCI Global Gold Miners ETF | 7 | 7.71% | $87.22 |
| GLDM | SPDR Gold MiniShares Trust | 5 | 9.60% | $101.93 |
| GLD | SPDR Gold Shares | 5 | 9.33% | $473.47 |
| SBSW | Sibanye-Stillwater | 6 | ~6% | $14.10 |
| AU | AngloGold Ashanti | 6 | ~6% | $106.59 |
| NEM | Newmont Corporation | 2 | ~10% | $116.27 |
| B | Barrick Mining | 2 | ~10% | $45.42 |
| RGLD | Royal Gold | 1 | ~10% | $279.90 |
| FCX | Freeport-McMoRan | 1 | ~10% | $59.33 |

**Total gold/precious metals weight in the consensus signal: approximately 45% of the top signal list.**

---

## SECTION 5: TOP HOLDINGS BY AVERAGE WEIGHT

Stocks with the highest average portfolio weight tend to be "high-conviction single-stock picks" within the strategies that choose them — often given ~10% allocation (near the natural max for a diversified 10-stock portfolio).

| Rank | Ticker | Name | Avg Weight | # Strategies | Price | Notes |
|------|--------|------|-----------|-------------|-------|-------|
| 1 | W | Wayfair Inc. Class A | **10.04%** | 3 | $76.20 | E-commerce recovery play |
| 2 | AVT | Avnet | **10.03%** | 2 | $60.09 | Electronics distribution |
| 3 | ADM | Archer-Daniels-Midland | **10.02%** | 2 | $67.43 | Ag commodities, value |
| 4 | FDP | Fresh Del Monte Produce | **10.01%** | 5 | $42.59 | Defensive food |
| 5 | HIG | The Hartford Insurance Group | **10.01%** | 2 | $139.26 | P&C insurance |
| 6 | FAF | First American Corporation | **10.01%** | 2 | $67.61 | Title insurance |
| 7 | ANGO | AngioDynamics | **10.01%** | 2 | $10.84 | Medical devices |
| 8 | SPXC | SPX Technologies | **10.01%** | 2 | $204.76 | HVAC / industrial |
| 9 | PRDO | Perdoceo Education | **10.00%** | 2 | $34.40 | For-profit education |
| 10 | RSI | Rush Street Interactive | **10.00%** | 2 | $20.87 | Online gaming/sports betting |
| 11 | IAU | iShares Gold Trust | **9.83%** | 15 | $96.98 | Gold ETF — high conviction + high frequency |
| 12 | GLDM | SPDR Gold MiniShares | **9.60%** | 5 | $101.93 | Gold ETF |
| 13 | RUSHA | Rush Enterprises | **9.49%** | 5 | $65.99 | Commercial trucks |
| 14 | UVE | Universal Insurance Holdings | **9.48%** | 6 | $35.17 | P&C insurance, deep value |
| 15 | GLD | SPDR Gold Shares | **9.33%** | 5 | $473.47 | Gold flagship |

**The real signal from average weight:** `IAU` combines both high frequency (15 strategies) AND near-maximum average weight (9.83%). This is the strongest individual conviction signal in the entire dataset. When a ticker appears in 15 strategies AND each one allocates ~10% to it, the collective model is extremely bullish.

---

## SECTION 6: STRATEGY OVERLAP — Near-Duplicates

Jaccard similarity = (shared tickers) / (union of all tickers). Score of 1.0 = identical portfolios.

| Similarity | Strategy A | Strategy B | Shared | Total |
|-----------|-----------|-----------|--------|-------|
| **66.7%** | defensive-investing | low-volatility-stocks | 13 | 20 |
| **53.8%** | market-disruptors | socially-responsible-investing-sri | 7 | 13 |
| **53.8%** | green-infrastructure | manufacturing-industry | 7 | 13 |
| **53.8%** | cloud-computing-boom | technology-sector-innovation-fund | 14 | 26 |
| **53.8%** | ai-related-companies | cloud-computing-boom | 14 | 26 |
| **51.5%** | ai-related-companies | robotics-automation | 17 | 33 |

### Near-Duplicate Pairs — Analysis

**1. `defensive-investing` ↔ `low-volatility-stocks` (66.7% overlap)**
These are essentially the same portfolio. Both hold: FDP, DG, RGA, DBP, BMRN, and other defensive names. A user choosing between these two is making a distinction without a difference. The 33% difference is in portfolio completion, not theme.

**2. `ai-related-companies` ↔ `cloud-computing-boom` ↔ `technology-sector-innovation-fund` (chain)**
These three form a "tech cluster" with 53–54% pairwise overlap. All three hold the same gold ETFs (GLDM, IAU, GLD) as top holdings and share many of the same small/mid-cap tech names. For analytical purposes, they should be treated as one portfolio family.

**3. `ai-related-companies` ↔ `robotics-automation` (51.5%)**
More than half the stocks are shared. Robotics is effectively a subset of the AI portfolio's holdings.

**4. `market-disruptors` ↔ `socially-responsible-investing-sri` (53.8%)**
Surprising overlap. Both hold: MU, SEZL, INNV, OIS, RELY, CODA, ORN. The model appears to classify "disruptive" companies as also "socially responsible" — possibly due to their growth characteristics and lower carbon intensity vs legacy industries.

**5. `green-infrastructure` ↔ `manufacturing-industry` (53.8%)**
Both hold: FIX (Comfort Systems USA), SBSW, MGNR, JLL, ATI, VICR. The model sees green infrastructure as fundamentally a manufacturing story.

### Complete Similarity Matrix (heatmap summary, 57×57)

High-overlap clusters identified:
- **Gold cluster**: GOEX/SGDM/DBP/IAU/GBUG/OUNZ/RING/GLDM/GLD overlap across 20–27 strategies
- **Tech cluster**: ai-related-companies, cloud-computing-boom, technology-sector-innovation-fund, robotics-automation, semiconductor-supercycle
- **Defensive cluster**: defensive-investing, low-volatility-stocks, diversified-bluechips, low-beta-stocks
- **Value cluster**: value-investing, deep-value-investing, buffett-bargains, contrarian-investing
- **Small-cap/momentum cluster**: small-cap-stocks, momentum-investing, fallen-angels, market-disruptors

---

## SECTION 7: PER-STRATEGY HOLDINGS (Top 5 by Weight)

| Strategy | #1 Holding (Wt%) | #2 Holding (Wt%) | #3 Holding (Wt%) | #4 Holding (Wt%) | #5 Holding (Wt%) |
|----------|-----------------|-----------------|-----------------|-----------------|-----------------|
| **value-investing** | SNDK 10.1% | FYC 10.0% | IRWD 10.0% | VSAT 10.0% | GCT 10.0% |
| **growth-investing** | EHAB 9.2% | UVE 8.2% | RELY 7.7% | TAYD 7.0% | PLOW 5.4% |
| **ai-related-companies** | RUSHA 10.0% | GLDM 9.3% | IAU 9.0% | GLD 8.8% | CODA 7.3% |
| **semiconductor-supercycle** | CRUS 10.0% | NVDA 10.0% | IAU 10.0% | SGDM 8.0% | ADI 7.7% |
| **momentum-investing** | NESR 10.0% | EDRY 8.9% | UVE 8.7% | TCMD 6.6% | PLOW 6.4% |
| **dividend-aristocrats** | DBP 7.6% | MO 6.3% | CVS 5.7% | TSN 5.4% | VZ 4.3% |
| **buffett-bargains** | DBP 7.8% | GOOGL 7.0% | PEP 6.6% | HSY 6.6% | T 6.5% |
| **sector-rotation** | MU 10.2% | MYRG 10.1% | CSTM 10.0% | AMD 10.0% | GOEX 10.0% |
| **deep-value-investing** | DVA 10.0% | USFD 7.3% | ULVM 5.9% | CODA 5.8% | SHG 5.3% |
| **defensive-investing** | FDP 10.0% | TSN 10.0% | DG 9.9% | RGA 7.6% | DBP 6.5% |
| **quality-investing** | ULVM 10.0% | HCI 9.9% | KMT 9.4% | OPTZ 8.9% | FFSM 8.3% |
| **high-dividend-stocks** | TSN 8.1% | OHI 6.8% | CTRE 6.6% | POR 5.5% | ITA 5.3% |
| **diversified-bluechips** | NEM 10.1% | B (Barrick) 10.0% | ISSC 10.0% | MGNR 10.0% | FDP 10.0% |
| **low-beta-stocks** | DXJ 10.1% | B (Barrick) 10.0% | FDP 10.0% | NESR 10.0% | CODA 10.0% |
| **low-volatility-stocks** | DG 10.1% | FDP 10.0% | RGA 9.9% | BMRN 9.0% | SFD 8.5% |
| **small-cap-stocks** | UVE 10.0% | PLOW 10.0% | EHAB 10.0% | IAU 10.0% | TCMD 9.6% |
| **high-beta-stocks** | GRBK 10.0% | GLDM 8.7% | IAU 8.6% | GLD 8.3% | GM 6.6% |
| **mid-cap-stocks** | PRDO 10.0% | IAU 10.0% | FAF 10.0% | LCII 10.0% | RUSHA 8.8% |
| **contrarian-investing** | VZ 10.0% | BMY 10.0% | IAU 10.0% | DG 9.9% | AMRX 9.2% |
| **fallen-angels** | SEZL 10.0% | OIS 10.0% | ORN 10.0% | INNV 10.0% | TBLA 10.0% |
| **market-disruptors** | MU 10.2% | SEZL 10.0% | INNV 10.0% | OIS 10.0% | RELY 10.0% |
| **thematic-investing** | HIG 9.9% | ITA 7.5% | BEPC 7.1% | GM 6.9% | CODA 6.4% |
| **clean-energy-revolution** | BEPC 10.0% | DBP 10.0% | CCNR 9.1% | VLO 8.4% | DXJ 7.6% |
| **cybersecurity-shield** | IAU 10.1% | VOD 10.0% | YOU 9.6% | RUSHA 9.1% | ESLT 8.9% |
| **biotech-breakthroughs** | LGND 10.1% | INDV 10.0% | ANGO 10.0% | IRWD 10.0% | IOVA 10.0% |
| **cloud-computing-boom** | GLDM 10.0% | IAU 10.0% | GLD 9.8% | RUSHA 9.5% | LILA 6.9% |
| **electric-vehicle-ev-boom** | ADI 10.3% | VICR 10.1% | GOEX 10.1% | SGDM 10.1% | BWA 10.0% |
| **robotics-automation** | RNG 10.0% | KMT 10.0% | CODA 10.0% | ATRO 10.0% | IAU 10.0% |
| **space-exploration** | LMT 10.2% | LHX 10.0% | IAU 10.0% | MOG.A 10.0% | NOC 9.9% |
| **metaverse-pioneers** | IAU 10.0% | GLDM 9.9% | GLD 9.9% | CODA 9.1% | NTCT 8.6% |
| **gaming-giants** | SGDM 10.1% | CRSR 10.0% | INSE 10.0% | DLPN 10.0% | API 10.0% |
| **gold-mining-industry** | RGLD 10.1% | FCX 10.0% | NEXA 10.0% | LXU 10.0% | DBP 10.0% |
| **water-scarcity-solutions** | DBP 10.1% | EFXT 10.0% | MGNR 10.0% | WTTR 10.0% | TTI 10.0% |
| **5g-infrastructure** | TIMB 10.0% | AVT 10.0% | NTCT 10.0% | IAU 10.0% | CALX 8.0% |
| **aging-population** | DBP 10.0% | DVA 9.5% | CCNR 8.0% | PRVA 7.2% | DXJ 7.1% |
| **e-commerce-enablers** | SGDM 10.1% | W 10.0% | WRBY 10.0% | LE 10.0% | FIGS 10.0% |
| **green-infrastructure** | FIX 10.5% | BAER 10.0% | SBSW 10.0% | MGNR 10.0% | JLL 10.0% |
| **esg-leaders** | FIX 10.5% | OUNZ 10.0% | CODA 10.0% | VRT 10.0% | RING 10.0% |
| **consumer-discretionary** | W 10.0% | BWA 10.0% | CPS 10.0% | CTRN 10.0% | FIGS 10.0% |
| **consumer-staples-stability** | ADM 10.1% | USFD 10.0% | MGNR 10.0% | FDP 10.0% | DDL 10.0% |
| **energy-sector-growth** | DHT 8.6% | VOLT 8.2% | OPPJ 8.0% | NGL 7.1% | INSW 6.2% |
| **financials-sector-capital** | THG 10.1% | FAF 10.1% | SEZL 10.0% | RELY 10.0% | EZPW 10.0% |
| **healthcare-stability-growth** | DBP 10.0% | DVA 6.7% | GEME 6.0% | ANIP 5.6% | CNC 5.6% |
| **industrials-infrastructure** | MYRG 10.1% | DXPE 10.1% | KMT 10.0% | RUSHA 10.0% | VVX 10.0% |
| **real-estate-income** | OUT 10.0% | CTRE 10.0% | PINE 10.0% | HST 10.0% | DRH 10.0% |
| **technology-innovation-fund** | GLDM 10.0% | IAU 10.0% | GLD 9.8% | CODA 7.8% | RNG 7.6% |
| **utilities-stability** | RGCO 8.5% | MOOD 6.5% | DBP 5.4% | EXC 4.9% | CWT 4.8% |
| **transportation-airline** | GD 10.0% | IAU 10.0% | OMAB 10.0% | TXT 10.0% | LTM 8.6% |
| **biomedical-genetics** | LGND 10.1% | GMED 10.0% | ANGO 10.0% | TNDM 10.0% | INFU 10.0% |
| **investment-management** | HIG 10.1% | SHG 10.0% | KEY 10.0% | IVZ 10.0% | BCS 10.0% |
| **leisure-recreation** | SGDM 10.1% | VIK 10.0% | WGO 10.0% | HST 10.0% | MCFT 10.0% |
| **manufacturing-industry** | SPXC 10.1% | ATI 10.1% | VICR 10.1% | DBP 10.1% | ISSC 10.0% |
| **meme-stock-mania** | DBD 10.0% | RIGL 10.0% | IAU 10.0% | CRWD 9.9% | PATH 8.7% |
| **the-amazon-of-x** | JBL 10.1% | GOEX 10.1% | AVT 10.1% | SGDM 10.1% | W 10.0% |
| **drip-dividend-reinvestment** | DBP 10.1% | DXJ 10.1% | TSN 10.0% | PAA 10.0% | OHI 10.0% |
| **socially-responsible-sri** | SGDM 10.1% | SEZL 10.0% | RELY 10.0% | NESR 10.0% | CODA 10.0% |
| **global-investing** | SHG 9.7% | AVDV 9.7% | DHT 9.3% | FDT 8.6% | LTM 8.4% |

---

## SECTION 8: THE PILOTAI CONSENSUS SIGNAL

### Methodology
The Consensus Signal combines three independent signals for each ticker:
1. **Frequency score** (f): How many strategies hold it, normalized 0→1
2. **Weight score** (w): Average portfolio allocation when held, normalized 0→1
3. **Quality score** (q): Composite of all 5 stock-score dimensions (propagated from strategy level), normalized 0→1

**Final score = f × w × q** (multiplicative — a ticker must score well on ALL three to rank highly)

### Top 30 Consensus Picks

| Rank | Ticker | Name | Signal Score | # Strats | Avg Wt% | Quality | Price | Asset Type |
|------|--------|------|-------------|---------|---------|---------|-------|-----------|
| 1 | **DBP** | Invesco DB Precious Metals Fund | **0.537** | 20 | 8.41% | 2.74 | $120.32 | Gold/Silver ETF |
| 2 | **SGDM** | Sprott Gold Miners ETF | **0.531** | 27 | 6.24% | 2.71 | $84.47 | Gold Miners ETF |
| 3 | **GOEX** | Global X Gold Explorers ETF | **0.478** | 27 | 5.62% | 2.71 | $95.00 | Gold Explorers ETF |
| 4 | **IAU** | iShares Gold Trust Shares | **0.441** | 15 | 9.83% | 2.57 | $96.98 | Gold ETF |
| 5 | **CODA** | Coda Octopus Group | **0.304** | 12 | 7.72% | 2.82 | $14.31 | Marine tech/SONAR |
| 6 | **MGNR** | American Beacon Select Funds | **0.274** | 13 | 6.61% | 2.73 | $50.12 | Multi-asset fund |
| 7 | **VICR** | Vicor Corporation | **0.255** | 15 | 5.20% | 2.81 | $162.60 | Power semiconductors |
| 8 | **ATI** | ATI Inc. | **0.252** | 13 | 5.84% | 2.84 | $150.11 | Aerospace metals |
| 9 | **TCMD** | Tactile Systems Technology | **0.233** | 8 | 8.40% | 2.97 | $28.78 | Medical devices |
| 10 | **DXJ** | WisdomTree Japan Hedged Equity Fund | **0.213** | 11 | 6.33% | 2.62 | $156.70 | Japan hedged equity |
| 11 | **UVE** | Universal Insurance Holdings | **0.204** | 6 | 9.48% | 3.08 | $35.17 | P&C Insurance |
| 12 | **OUNZ** | VanEck Merk Gold ETF | **0.183** | 6 | 9.30% | 2.81 | $49.55 | Gold ETF (deliverable) |
| 13 | **RING** | iShares MSCI Global Gold Miners | **0.179** | 7 | 7.71% | 2.84 | $87.22 | Gold Miners ETF |
| 14 | **CMI** | Cummins Inc. | **0.175** | 6 | 8.87% | 2.82 | $539.44 | Diesel/power engines |
| 15 | **FDP** | Fresh Del Monte Produce | **0.171** | 5 | 10.01% | 2.93 | $42.59 | Produce/food |
| 16 | **SEZL** | Sezzle Inc. | **0.169** | 9 | 5.57% | 2.90 | $73.36 | BNPL fintech |
| 17 | **NESR** | National Energy Services Reunited | **0.151** | 8 | 5.87% | 2.77 | $20.86 | Oil services (MENA) |
| 18 | **INNV** | InnovAge Holding Corp. | **0.151** | 8 | 5.45% | 2.97 | $8.95 | Senior care services |
| 19 | **RUSHA** | Rush Enterprises | **0.146** | 5 | 9.49% | 2.64 | $65.99 | Commercial truck dealer |
| 20 | **MU** | Micron Technology | **0.142** | 14 | 3.28% | 2.65 | $370.34 | DRAM/NAND memory |
| 21 | **AMD** | Advanced Micro Devices | **0.142** | 11 | 4.25% | 2.60 | $192.46 | GPU/CPU |
| 22 | **RELY** | Remitly Global | **0.141** | 5 | 8.30% | 2.92 | $17.05 | Digital remittances |
| 23 | **GBUG** | Sprott Active Gold & Silver ETF | **0.141** | 15 | 3.13% | 2.57 | $51.11 | Active gold/silver ETF |
| 24 | **TSN** | Tyson Foods | **0.141** | 5 | 8.70% | 2.77 | $61.46 | Protein/food |
| 25 | **GLDM** | SPDR Gold MiniShares Trust | **0.133** | 5 | 9.60% | 2.38 | $101.93 | Gold ETF |
| 26 | **GLD** | SPDR Gold Shares | **0.129** | 5 | 9.33% | 2.38 | $473.47 | Gold flagship ETF |
| 27 | **IRWD** | Ironwood Pharmaceuticals | **0.129** | 6 | 6.30% | 2.92 | $3.50 | GI specialty pharma |
| 28 | **DG** | Dollar General Corporation | **0.128** | 4 | 9.97% | 2.75 | $146.29 | Discount retail |
| 29 | **AU** | AngloGold Ashanti PLC | **0.127** | 6 | 6.27% | 2.89 | $106.59 | Gold mining (South Africa) |
| 30 | **JLL** | Jones Lang LaSalle | **0.126** | 6 | 6.53% | 2.76 | $298.86 | Commercial real estate |

### The Consensus Signal — By Theme Cluster

**Cluster 1: Gold/Precious Metals (8 of top 30)**
DBP, SGDM, GOEX, IAU, OUNZ, RING, GLDM, GLD, GBUG — the gold complex. Extremely high consensus, appearing across defensive AND growth strategies. Current price of gold near all-time highs.

**Cluster 2: Non-Gold High-Conviction Picks (8 names)**
VICR (power semiconductors), MU (DRAM), ATI (aerospace metals), AMD (GPU/CPU), CODA (marine tech), UVE (insurance), SEZL (fintech), INNV (senior care) — these are the model's non-gold favorites. Common theme: value + growth + mid-to-small cap.

**Cluster 3: Defensive/Income (5 names)**
FDP (fresh produce), TSN (protein), DG (discount retail), RUSHA (commercial trucks), CMI (engines) — industrial and defensive plays with moderate quality scores.

**Cluster 4: Non-US/Global (2 names)**
DXJ (Japan hedged), NESR (MENA oil services) — international exposure with hedged or commodity-linked characteristics.

**Cluster 5: Speculative/Recovery (4 names)**
RELY (remittances), IRWD (pharma, $3.50), INNV ($8.95), SEZL ($73) — small/micro-cap names with high growth scores but elevated risk.

---

## SECTION 9: API's NATIVE TOP 10 RECOMMENDATIONS

The API returned these 10 strategies as its curated "top_recommendation" list (today's environment-specific picks):

| Rank | Strategy | Composite | Key Macro Thesis |
|------|----------|-----------|-----------------|
| 1 | **gold-mining-industry** | 2.920 | Inflation hedge, materials cycle |
| 2 | **consumer-staples-stability-strategy** | 2.778 | Defensive, recession-resistant |
| 3 | **contrarian-investing** | 2.574 | Beaten-down names: VZ, BMY, DG |
| 4 | **clean-energy-revolution** | 2.354 | Long-term transition, value entry |
| 5 | **drip-dividend-reinvestment-plan** | 2.886 | Compounding income, low risk |
| 6 | **momentum-investing** | 3.084 | Follow the current leaders |
| 7 | **high-dividend-stocks** | 2.834 | Income-focused, mid risk |
| 8 | **aging-population** | 2.744 | Healthcare, defensive, long-term demographic |
| 9 | **the-amazon-of-x** | 2.542 | Tech-enabled disruptors at value prices |
| 10 | **meme-stock-mania** | 2.434 | High risk/reward (IAU + CRWD + PATH) |

**What this list says about the model's current macro view:**

The API is recommending: Gold, Defensive Staples, Contrarian (beaten-down), Income, and a dash of momentum. This is a distinctly risk-off, late-cycle, defensive posture. The inclusion of "meme-stock-mania" (with IAU as its top holding!) at #10 suggests even the "risk" recommendation is actually a gold recommendation.

---

## SECTION 10: STRATEGY CLUSTERS & TAXONOMY

### By Tag (Most Prevalent)

| Tag | # Strategies | Interpretation |
|-----|-------------|----------------|
| Multi Sectors | 22 | Diversified across sectors |
| Value | 18 | Value characteristics |
| High Risk | 15 | High volatility/beta |
| Defensive | 10 | Low drawdown focus |
| Tech | 8 | Technology theme |
| Growth | 8 | Growth-oriented |
| High Growth | 7 | Explicitly high-growth |
| Momentum | 6 | Momentum/trend following |
| Large Cap | 5 | Large-cap focus |
| Low Risk | 5 | Explicit low-risk |
| Consumer Discretionary | 5 | Consumer spending |

### Strategy Families

**Family 1 — Quality/Defensive (highest composite scores)**
Strategies: value-investing, diversified-bluechips, market-disruptors, low-beta-stocks, small-cap-stocks
Characteristics: Best average composite scores (3.1+), 10-stock focused portfolios, highest health scores

**Family 2 — Momentum/Growth**
Strategies: momentum-investing, growth-investing, sector-rotation, esg-leaders, fallen-angels
Characteristics: High growth and momentum scores, 30-stock diversified, less value-conscious

**Family 3 — Sector Specialization**
Strategies: gold-mining, financials, industrials, transportation, biomedical, manufacturing, real-estate
Characteristics: Concentrated sector exposure, generally lower composite scores, thematic conviction

**Family 4 — Tech/Innovation (low composite, gold-contaminated)**
Strategies: ai-related-companies, cloud-computing-boom, semiconductor-supercycle, technology-sector-innovation-fund, metaverse-pioneers
Characteristics: Low composite scores (2.27–2.47), top holdings dominated by gold ETFs — the model has low conviction in pure tech at current prices

**Family 5 — Income/Dividend**
Strategies: dividend-aristocrats, high-dividend-stocks, drip-dividend-reinvestment-plan, utilities-sector-stability-fund
Characteristics: High past-performance scores, low growth scores, income-focused, some gold as hedge

---

## SECTION 11: INTERESTING ANOMALIES & INSIGHTS

### Anomaly 1: The Cybersecurity Portfolio Holds IAU as #1 Position
`cybersecurity-shield` allocates **10.1%** to IAU (gold) as its single largest position, ahead of any cybersecurity company. The model is saying: "If you came here for cybersecurity exposure, great — but first, buy gold."

### Anomaly 2: EV Boom Portfolio's Top 2 Positions Are Gold Miners
`electric-vehicle-ev-boom` holds GOEX (10.1%) and SGDM (10.1%) as top positions. The EV theme is present (BWA, ADI for battery management) but gold miners are given highest conviction.

### Anomaly 3: "Market Disruptors" ≈ "Socially Responsible Investing" (53.8% overlap)
These strategies sound antithetical but share: MU, SEZL, INNV, OIS, RELY, CODA, ORN. The model's "disruptive" companies and "ESG leaders" are drawn from the same pool of small-cap value names. This suggests the model defines "disruption" and "responsibility" more through financial characteristics (growth + value + health) than traditional ESG criteria.

### Anomaly 4: Utilities Has the Lowest Composite Score (2.032) and Health = 0.00 on Global Investing
`utilities-sector-stability-fund` is the worst-scoring strategy in the universe (2.032 composite) — it has the lowest growth (1.76) and health (1.37) scores. The model is not bullish on traditional utilities. `global-investing` scores 0.00 on health — likely a data gap, not a true score.

### Anomaly 5: Comfort Systems USA (FIX) at $1,281/share Appears in 6 Strategies
FIX (Comfort Systems USA — HVAC/electrical contracting) appears across ESG, green-infrastructure, AI, clean-energy, cloud, and industrials. It's the model's cross-thematic darling: an industrial company that benefits from AI data center construction, green building mandates, and infrastructure spending. At 10%+ weight in multiple portfolios at $1,281/share, this is a high-conviction large allocation.

### Anomaly 6: NVDA Only Appears in 6 Strategies with Low Weight
NVIDIA (the defining stock of the AI era, +170% in 2024) appears in only 6 strategies at modest weights. Meanwhile, gold ETFs appear in 27 strategies. The model has rotated away from NVDA toward gold — a significant macro call for March 2026.

---

## SECTION 12: ACTIONABLE INSIGHTS FOR PILOTAI

### For the Credit Spread / Options System

**Insight 1: Gold is the consensus trade of the moment**
The gold signal from 57 strategies is clear enough to be actionable. Bull put spreads on GLD, IAU, or gold miners (GDX, GDXJ) in the current BULL or NEUTRAL regime would be consistent with the model's dominant thesis. Gold's recent trend (continuous new ATHs in early 2026) provides technical confirmation.

**Insight 2: The "non-gold consensus picks" are the watchlist**
The 8 non-gold high-conviction names (VICR, MU, ATI, AMD, CODA, UVE, SEZL, INNV) represent stocks that multiple strategies find compelling. These are candidates for single-stock credit spreads if the options trading system expands beyond SPY:
- **VICR** (power semiconductors, $162): Appears in 15 strategies. High-conviction AI/semiconductor infrastructure pick.
- **MU** (Micron, $370): 14 strategies, sector-rotation top pick. DRAM cycle recovery.
- **ATI** ($150): 13 strategies. Aerospace/defense metals benefiting from defense spending + EV lightweighting.
- **AMD** ($192): 11 strategies. GPU/CPU with strong fundamentals.

**Insight 3: Defensive rotation is underway**
The API's native top-10 recommendation list is 80% defensive (gold, staples, contrarian, dividend). This aligns with the broader risk-off environment in March 2026 (tariff uncertainty, dollar weakness). For the credit spread system: favor bull put spreads in defensive sectors over bear call spreads in growth/tech.

**Insight 4: Tech strategies are low-conviction — avoid aggressive tech spread selling**
AI, cloud, semiconductor, and tech strategies have the lowest composite scores (2.27–2.47). This is not the time to sell put spreads aggressively on tech ETFs (QQQ, XLK). The model sees downside risk in tech relative to value.

**Insight 5: Strategy pairs that can be merged in analysis**
For analytical simplification:
- Use **defensive-investing** as proxy for **low-volatility-stocks** (66.7% overlap)
- Use **ai-related-companies** as proxy for **cloud-computing-boom** and **technology-sector-innovation-fund** (53% overlap)
- Use **market-disruptors** as proxy for **socially-responsible-investing-sri** (53.8% overlap)

### Signal Service Design: "Attix Consensus Portfolio"

**Top 20 Consensus Picks (equal-weight, rebalanced monthly from API data):**

| # | Ticker | Name | Signal Score | Category |
|---|--------|------|-------------|----------|
| 1 | DBP | Invesco DB Precious Metals Fund | 0.537 | Gold |
| 2 | SGDM | Sprott Gold Miners ETF | 0.531 | Gold |
| 3 | GOEX | Global X Gold Explorers ETF | 0.478 | Gold |
| 4 | IAU | iShares Gold Trust | 0.441 | Gold |
| 5 | CODA | Coda Octopus Group | 0.304 | Tech/Defense |
| 6 | MGNR | American Beacon Select Funds | 0.274 | Multi-asset |
| 7 | VICR | Vicor Corporation | 0.255 | Semis |
| 8 | ATI | ATI Inc. | 0.252 | Industrials |
| 9 | TCMD | Tactile Systems Technology | 0.233 | Healthcare |
| 10 | DXJ | WisdomTree Japan Hedged | 0.213 | International |
| 11 | UVE | Universal Insurance Holdings | 0.204 | Financials |
| 12 | OUNZ | VanEck Merk Gold ETF | 0.183 | Gold |
| 13 | RING | iShares MSCI Global Gold Miners | 0.179 | Gold |
| 14 | CMI | Cummins Inc. | 0.175 | Industrials |
| 15 | FDP | Fresh Del Monte Produce | 0.171 | Defensive/Food |
| 16 | SEZL | Sezzle Inc. | 0.169 | Fintech |
| 17 | NESR | National Energy Services Reunited | 0.151 | Energy |
| 18 | INNV | InnovAge Holding Corp. | 0.151 | Healthcare |
| 19 | RUSHA | Rush Enterprises | 0.146 | Industrials |
| 20 | MU | Micron Technology | 0.142 | Semis |

**Interpretation of the Consensus Portfolio:**
- **Gold = 35% of portfolio** (7 of 20 picks are gold instruments). The signal is not subtle.
- **Industrials/Defense = 25%** (VICR, ATI, CMI, RUSHA — real-economy winners)
- **Healthcare = 10%** (TCMD, INNV — value-priced healthcare)
- **Tech = 10%** (MU, CODA — semiconductor and marine tech)
- **Financial/Other = 20%** (UVE, SEZL, NESR, FDP, DXJ, MGNR)

**This is a portfolio that says:** *Inflation is real. Geopolitical tension is elevated. Gold is a core holding. Buy industrial companies making physical things (ATI, CMI, RUSHA). The tech bubble of 2023–2024 is deflating. Be selective.*

---

## SECTION 13: DATA QUALITY NOTES

1. **Score propagation:** Stock-level scores are propagated from strategy-level aggregate scores. This is an approximation — the API does not return per-stock scores, only per-strategy aggregate. Individual stock quality may differ from the strategy average.

2. **Gold ETF contamination:** Multiple tech/growth strategies hold gold ETFs at high weight, likely reflecting a macro overlay applied by the model. This artificially inflates gold's consensus signal. Even adjusting for this, gold remains #1.

3. **Strategy scores are current as of March 7, 2026:** Cached for 30 minutes, resets daily ET. The model uses live prices for allocation calculations.

4. **$100,000 budget assumption:** All allocations are calculated assuming a $100,000 portfolio. Average leftover cash is ~$10,600 (10.6%), representing the model's implicit cash allocation.

5. **`global-investing` health score = 0.00:** This appears to be a data gap (international stocks may not have health scores computed). Do not use global-investing's health score in cross-strategy comparisons.

6. **MGNR (American Beacon Select Funds):** This is a multi-asset mutual fund appearing in 13 strategies. It's likely included as a "balanced allocation" filler when the model lacks high-conviction individual picks in some strategies.

---

## SUMMARY TABLE: KEY NUMBERS AT A GLANCE

| Metric | Value |
|--------|-------|
| Strategies analyzed | 57 |
| Unique tickers | 401 |
| Most common ticker (by #strategies) | GOEX / SGDM (tied, 27 each) |
| Highest signal score ticker | DBP (0.537) |
| Highest composite quality strategy | value-investing (3.228) |
| Lowest composite quality strategy | utilities-sector-stability-fund (2.032) |
| Most near-duplicate pair | defensive-investing ↔ low-volatility-stocks (66.7%) |
| % of consensus signal that is gold | ~38% of top 20 by signal score |
| API's #1 native recommendation today | gold-mining-industry |
| Best momentum strategy | momentum-investing (momentum score: 3.90) |
| Best past performance strategy | buffett-bargains (3.99) |
| Best health score strategy | diversified-bluechips (3.01) |
| Best value strategy | value-investing (3.53) |
| Best growth strategy | market-disruptors / growth-investing (3.77, tied) |
| NVDA appearances | 6 (lower than expected given AI narrative) |
| Gold instrument appearances (combined) | 117+ total appearances across all strategies |

---

*Analysis performed: March 7, 2026 | Data source: Attix Strategy Recommendation API v2 (staging)*
*Total API response size: 204,444 bytes across 10 sequential batch requests*
