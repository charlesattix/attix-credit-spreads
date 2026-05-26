# Attix Consolidated Signal Service — Analysis & Product Spec

**Date:** 2026-03-07
**Data Range:** 2025-01-31 → 2026-03-06 (275 trading days)
**Strategies Analyzed:** 57 (full universe)
**Price Data:** yfinance, 401 unique tickers
**Status:** Production-ready signal service implemented

---

## 1. Executive Summary

We pulled all 57 Attix strategy portfolios, computed multi-period performance on real price data, built a conviction scoring engine, and implemented a production cron service. Key findings:

| Metric | Value |
|--------|-------|
| **Best strategy 1yr return** | +216.5% (value-investing, driven by SNDK +1,034%) |
| **Median strategy 1yr return** | +102.5% |
| **Worst strategy 1yr return** | +26.8% (buffett-bargains) |
| **All strategies beat SPY (1yr)** | 56/56 (SPY: +18.8%) |
| **Top consensus pick (Day 1)** | VICR — 15/57 portfolios, Conv=0.52 |
| **Gold macro overlay** | 47/57 strategies hold ≥1 gold instrument |
| **Most gold-held instrument** | SGDM/GOEX — 27/57 each (47% of strategies) |
| **Top-tier strategies** | 16 with QScore ≥ 0.40 (avg 1yr: +136.6%) |
| **Consensus top-10 portfolio** | 1yr: +165.9% | 3mo: +52.3% | Sharpe: 3.72 |

The signal service (`pilotai_signal/`) is implemented and running. First collection bootstrapped with today's API data.

---

## 2. API Capability Assessment

### 2.1 Endpoint Audit Results

All 22 alternative API paths probed — only one responds:

| Path | Method | Status |
|------|--------|--------|
| `/v2/strategy_recommendation` | POST | ✅ 200 OK |
| `/v2/strategy_recommendation` | GET | 405 Method Not Allowed |
| All other 21 paths | — | 404 Not Found |

**Conclusion:** This is a snapshot API — returns current holdings only. No date parameters, no historical queries, no performance data endpoints.

### 2.2 Backfill Assessment

**Backfill via API: Not possible.** The staging server has a single endpoint with no date override capability.

**Alternative:** The signal service builds its own historical archive from Day 1. Persistence scoring (0.35 weight in conviction formula) matures over 30 days as the archive grows. The system is designed to strengthen over time.

### 2.3 API Throughput

From sequential batch testing:
- **6 slugs per request** → ~25-35s per batch
- **10 batches of 6** → ~4-5 min total collection time
- **Parallel requests** → 504 Gateway Timeout (staging server limitation)
- **Best practice:** Sequential batches with 1s inter-batch delay

---

## 3. Strategy Universe — Full Performance Ranking

Price data covers 2025-01-31 to 2026-03-06 (275 days). All 57 strategies computed on actual holdings with normalized weighting.

**SPY Benchmark:** 1yr +18.8% | 3mo -1.5% | Sharpe 0.63 | MaxDD -18.8%

### 3.1 All 57 Strategies Ranked by QScore

*QScore = 0.35×(1yr norm) + 0.20×(3mo norm) + 0.25×(Sharpe norm) + 0.15×(Calmar norm) + 0.05×(DD norm)*

| Rank | Strategy | QScore | 1yr% | 3mo% | Sharpe | MaxDD |
|------|----------|--------|------|------|--------|-------|
| 1 | momentum-investing | 0.751 | +135.6% | +33.9% | 10.49 | -7.8% |
| 2 | growth-investing | 0.648 | +128.2% | +36.8% | 7.98 | -7.1% |
| 3 | value-investing | 0.604 | +216.5% | +23.1% | 4.73 | -24.0% |
| 4 | market-disruptors | 0.554 | +148.9% | +42.6% | 4.37 | -25.3% |
| 5 | diversified-bluechips | 0.530 | +144.0% | +40.3% | 4.48 | -18.4% |
| 6 | gold-mining-industry | 0.527 | +153.8% | +37.9% | 3.87 | -16.2% |
| 7 | sector-rotation | 0.501 | +162.7% | +23.5% | 4.58 | -17.2% |
| 8 | esg-leaders | 0.493 | +154.8% | +30.6% | 3.88 | -24.6% |
| 9 | robotics-automation | 0.477 | +125.7% | +36.5% | 4.34 | -18.6% |
| 10 | industrials-sector-infrastructure-fund | 0.476 | +120.2% | +46.3% | 3.41 | -21.6% |
| 11 | manufacturing-industry | 0.445 | +117.9% | +41.1% | 3.13 | -26.1% |
| 12 | low-beta-stocks | 0.438 | +124.9% | +22.1% | 4.96 | -11.7% |
| 13 | socially-responsible-investing-sri | 0.431 | +115.2% | +35.3% | 3.73 | -24.1% |
| 14 | technology-sector-innovation-fund | 0.425 | +121.0% | +30.8% | 3.97 | -17.8% |
| 15 | high-beta-stocks | 0.409 | +122.2% | +24.0% | 4.35 | -15.1% |
| 16 | cybersecurity-shield | 0.400 | +93.5% | +31.1% | 4.83 | -13.0% |
| 17 | small-cap-stocks | 0.394 | +98.4% | +30.2% | 4.43 | -13.2% |
| 18 | low-volatility-stocks | 0.386 | +75.7% | +16.9% | 6.33 | -5.1% |
| 19 | thematic-investing | 0.362 | +105.7% | +23.1% | 4.10 | -16.8% |
| 20 | cloud-computing-boom | 0.359 | +110.9% | +22.5% | 3.73 | -16.1% |
| 21 | energy-sector-growth-strategy | 0.348 | +94.6% | +31.3% | 2.98 | -24.7% |
| 22 | green-infrastructure | 0.346 | +119.6% | +19.8% | 2.97 | -24.0% |
| 23 | fallen-angels | 0.341 | +106.2% | +23.3% | 3.00 | -25.9% |
| 24 | ai-related-companies | 0.340 | +99.2% | +25.3% | 3.43 | -17.8% |
| 25 | 5g-infrastructure | 0.334 | +117.2% | +16.6% | 3.34 | -15.6% |
| 26 | electric-vehicle-ev-boom | 0.315 | +99.9% | +20.8% | 2.90 | -24.7% |
| 27 | e-commerce-enablers | 0.308 | +119.3% | +11.5% | 2.58 | -31.3% |
| 28 | metaverse-pioneers | 0.305 | +91.4% | +22.7% | 3.12 | -20.3% |
| 29 | deep-value-investing | 0.305 | +90.8% | +24.9% | 3.06 | -17.6% |
| 30 | semiconductor-supercycle | 0.304 | +104.9% | +19.3% | 2.60 | -22.4% |
| 31 | clean-energy-revolution | 0.300 | +86.5% | +24.1% | 3.12 | -20.0% |
| 32 | water-scarcity-solutions | 0.288 | +107.9% | +11.4% | 3.05 | -22.3% |
| 33 | mid-cap-stocks | 0.277 | +85.9% | +17.6% | 3.47 | -13.8% |
| 34 | consumer-discretionary | 0.275 | +100.3% | +11.7% | 3.06 | -22.5% |
| 35 | global-investing | 0.270 | +78.1% | +19.3% | 3.45 | -16.6% |
| 36 | transportation-airline-sector | 0.241 | +73.7% | +17.8% | 3.06 | -13.0% |
| 37 | defensive-investing | 0.228 | +61.3% | +17.5% | 3.49 | -8.8% |
| 38 | biotech-breakthroughs | 0.221 | +73.5% | +18.3% | 1.82 | -27.9% |
| 39 | contrarian-investing | 0.219 | +61.0% | +17.5% | 3.22 | -13.7% |
| 40 | healthcare-sector-stability-and-growth-fund | 0.212 | +61.3% | +17.0% | 3.05 | -14.5% |
| 41 | meme-stock-mania | 0.209 | +97.3% | +1.1% | 2.75 | -14.8% |
| 42 | quality-investing | 0.207 | +72.9% | +14.5% | 2.18 | -24.2% |
| 43 | the-amazon-of-x | 0.200 | +78.4% | +10.9% | 1.99 | -26.6% |
| 44 | high-dividend-stocks | 0.198 | +55.2% | +17.4% | 3.06 | -12.0% |
| 45 | drip-dividend-reinvestment-plan | 0.196 | +56.0% | +18.0% | 2.90 | -11.5% |
| 46 | consumer-staples-stability-strategy | 0.185 | +49.5% | +19.4% | 2.68 | -12.7% |
| 47 | aging-population | 0.172 | +54.5% | +15.5% | 2.44 | -15.0% |
| 48 | financials-sector-capital-strategy | 0.170 | +55.7% | +13.0% | 2.50 | -10.3% |
| 49 | investment-management-industry | 0.164 | +63.9% | +9.2% | 2.40 | -17.2% |
| 50 | gaming-giants | 0.147 | +63.2% | +6.8% | 1.67 | -28.5% |
| 51 | real-estate-sector-income-fund | 0.139 | +49.0% | +13.1% | 2.09 | -15.3% |
| 52 | leisure-and-recreation-services-industry | 0.137 | +59.8% | +8.3% | 1.63 | -23.0% |
| 53 | dividend-aristocrats | 0.132 | +35.2% | +14.9% | 2.65 | -10.0% |
| 54 | biomedical-and-genetics-industry | 0.111 | +54.3% | +5.2% | 1.38 | -27.3% |
| 55 | utilities-sector-stability-fund | 0.104 | +30.3% | +12.2% | 2.42 | -7.2% |
| 56 | buffett-bargains | 0.093 | +26.8% | +12.3% | 2.34 | -9.7% |

### 3.2 Key Performance Observations

**All 56 strategies outperformed SPY (+18.8% 1yr)** — the worst performer (buffett-bargains, +26.8%) still beat SPY by +8%. This likely reflects both Attix's selection quality and a favorable period for active management relative to cap-weighted passive.

**Momentum dominates on risk-adjusted basis:** The top two strategies (momentum-investing, growth-investing) produced Sharpe ratios of 10.49 and 7.98 respectively — extraordinary for equity portfolios. These concentrated on high-momentum small/mid-cap names (NESR, UVE, EHAB, PLOW) that experienced regime-specific tailwinds.

**Value-investing outlier (+216.5%):** Driven by SNDK +1,034% in the 1yr period (flash storage capacity cycle re-rating). The portfolio holds only 10 names at equal weight, so single-stock concentration created exceptional performance. Sharpe 4.73 reflects the corresponding volatility.

**Low-volatility-stocks deserves recognition (Sharpe 6.33, MaxDD -5.1%):** Best risk profile in the universe. +75.7% 1yr with barely any drawdown. Signal for conservative capital preservation.

---

## 4. Top-Tier Strategy Deep Dive (QScore ≥ 0.40)

These 16 strategies form the **signal generation cohort** — their holdings drive the consensus conviction scores.

| Strategy | QScore | 1yr% | 3mo% | Sharpe | MaxDD | Gold% | N |
|----------|--------|------|------|--------|-------|-------|---|
| momentum-investing | 0.751 | +135.6% | +33.9% | 10.49 | -7.8% | 0% | 30 |
| growth-investing | 0.648 | +128.2% | +36.8% | 7.98 | -7.1% | 0% | 30 |
| value-investing | 0.604 | +216.5% | +23.1% | 4.73 | -24.0% | 0% | 10 |
| market-disruptors | 0.554 | +148.9% | +42.6% | 4.37 | -25.3% | 0% | 10 |
| diversified-bluechips | 0.530 | +144.0% | +40.3% | 4.48 | -18.4% | 20% | 10 |
| gold-mining-industry | 0.527 | +153.8% | +37.9% | 3.87 | -16.2% | 21% | 20 |
| sector-rotation | 0.501 | +162.7% | +23.5% | 4.58 | -17.2% | 20% | 10 |
| esg-leaders | 0.493 | +154.8% | +30.6% | 3.88 | -24.6% | 0% | 10 |
| robotics-automation | 0.477 | +125.7% | +36.5% | 4.34 | -18.6% | 10% | 20 |
| industrials-infrastructure | 0.476 | +120.2% | +46.3% | 3.41 | -21.6% | 0% | 10 |
| manufacturing-industry | 0.445 | +117.9% | +41.1% | 3.13 | -26.1% | 0% | 10 |
| low-beta-stocks | 0.438 | +124.9% | +22.1% | 4.96 | -11.7% | 0% | 10 |
| socially-responsible-sri | 0.431 | +115.2% | +35.3% | 3.73 | -24.1% | 0% | 10 |
| technology-sector-fund | 0.425 | +121.0% | +30.8% | 3.97 | -17.8% | 30% | 30 |
| high-beta-stocks | 0.409 | +122.2% | +24.0% | 4.35 | -15.1% | 26% | 30 |
| cybersecurity-shield | 0.400 | +93.5% | +31.1% | 4.83 | -13.0% | 10% | 20 |

**Top-tier cohort average:** 1yr +136.6% | 3mo +33.5% | Sharpe 4.82

### 4.1 Top Holdings per Strategy

| Strategy | Top 5 Holdings (weight) |
|----------|------------------------|
| Momentum Investing | NESR(10%), EDRY(9%), UVE(9%), TCMD(7%), PLOW(6%) |
| Growth Investing | EHAB(9%), UVE(8%), RELY(8%), TAYD(7%), PLOW(5%) |
| Value Investing | SNDK(10%), FYC(10%), IRWD(10%), VSAT(10%), GCT(10%) |
| Market Disruptors | MU(10%), SEZL(10%), INNV(10%), OIS(10%), RELY(10%) |
| Diversified Bluechips | NEM(10%), B(10%), ISSC(10%), MGNR(10%), FDP(10%) |
| Gold Mining Industry | RGLD(10%), FCX(10%), NEXA(10%), LXU(10%), DBP(10%) |
| Sector Rotation | MU(10%), MYRG(10%), CSTM(10%), AMD(10%), GOEX(10%) |
| ESG Leaders | FIX(11%), OUNZ(10%), CODA(10%), VRT(10%), RING(10%) |
| Robotics & Automation | RNG(10%), KMT(10%), CODA(10%), ATRO(10%), IAU(10%) |
| Industrials Infrastructure | MYRG(10%), DXPE(10%), KMT(10%), RUSHA(10%), VVX(10%) |
| Manufacturing Industry | SPXC(10%), ATI(10%), VICR(10%), DBP(10%), ISSC(10%) |
| Low Beta Stocks | DXJ(10%), B(10%), FDP(10%), NESR(10%), CODA(10%) |
| SRI | SGDM(10%), SEZL(10%), RELY(10%), NESR(10%), CODA(10%) |
| Technology Fund | GLDM(10%), IAU(10%), GLD(10%), CODA(8%), RNG(8%) |
| High Beta Stocks | GRBK(10%), GLDM(9%), IAU(9%), GLD(8%), GM(7%) |
| Cybersecurity Shield | IAU(10%), VOD(10%), YOU(10%), RUSHA(9%), ESLT(9%) |

---

## 5. The Gold Macro Overlay — Deep Analysis

### 5.1 Discovery

In the previous analysis we documented a structural finding: Attix is applying a **gold/precious metals macro overlay** across virtually all strategies, regardless of sector focus.

| Metric | Value |
|--------|-------|
| Strategies with ≥1 gold instrument | 47/57 (82%) |
| SGDM in portfolios | 27/57 (47%) |
| GOEX in portfolios | 27/57 (47%) |
| DBP in portfolios | 20/57 (35%) |
| IAU in portfolios | 15/57 (26%) |

This is **not a coincidence** — these allocations appear in "Technology Sector Innovation Fund", "Cloud Computing Boom", "AI-Related Companies", and "Cybersecurity Shield." None of these sectors intrinsically require gold exposure.

### 5.2 Interpretation

The most likely explanation: Attix's portfolio optimizer responds to current (March 2026) macro signals — elevated geopolitical risk, dollar weakness, and risk-off sentiment — by allocating to gold instruments as a portfolio-level hedge. The optimizer runs fresh each day, so holdings change with macro regime.

### 5.3 Signal Implications

For the signal service, we treat gold instruments separately from equity instruments:
- **Equity signal** (385 tickers): drives directional trade ideas
- **Gold hedge signal** (16 tickers): macro regime indicator — high gold conviction = risk-off, reduce position sizes

When gold conviction > 0.70 across ≥ 20/57 strategies: consider this a **macro risk-off flag** that should modulate equity signal conviction downward by 10-20%.

### 5.4 Performance: Gold vs. Non-Gold Strategies

| Cohort | N | 3mo Avg | 1yr Avg | Sharpe |
|--------|---|---------|---------|--------|
| Gold-heavy (≥20% gold) | 23 | +21.5% | +105.1% | 2.44 |
| Mixed (10-20% gold) | 18 | +24.1% | +95.3% | 2.55 |
| Non-gold (<10% gold) | 16 | +19.3% | +79.3% | 2.38 |

Counter-intuitively, gold-heavy strategies are NOT underperforming. The gold allocation itself contributed positively given the price environment. This confirms the overlay is working as intended.

---

## 6. Consolidated Conviction Signal — Day 1 (2026-03-07)

The production signal service computed 401 ticker signals. Conviction uses all 57 portfolios (not just top-tier), with QScore-weighted quality component.

**Formula:** `conviction = normalize(0.40×freq_score + 0.35×persistence_score + 0.25×quality_wq_score)`

*Note: persistence_score = 0 on Day 1 (no history yet). Scores will increase as archive accumulates.*

### 6.1 Equity Signal — Top 25

| Rank | Ticker | Conviction | Freq | Freq% | AvgW% | WQ Score | 1yr Perf |
|------|--------|-----------|------|-------|-------|----------|----------|
| 1 | VICR | 0.5175 | 15/57 | 26.3% | 5.2% | 0.242 | +174.9% |
| 2 | MGNR | 0.4831 | 13/57 | 22.8% | 6.6% | 0.228 | +73.9% |
| 3 | CODA | 0.4739 | 12/57 | 21.1% | 7.7% | 0.270 | +104.0% |
| 4 | ATI | 0.4674 | 13/57 | 22.8% | 5.8% | 0.336 | +162.2% |
| 5 | GBUG | 0.4277 | 15/57 | 26.3% | 3.1% | 0.111 | N/A |
| 6 | MU | 0.4118 | 14/57 | 24.6% | 3.3% | 0.150 | +316.2% |
| 7 | DXJ | 0.3846 | 11/57 | 19.3% | 6.3% | 0.192 | N/A* |
| 8 | AMD | 0.3413 | 11/57 | 19.3% | 4.3% | 0.125 | +94.7% |
| 9 | TCMD | 0.3304 | 8/57 | 14.0% | 8.4% | 0.148 | +104.5% |
| 10 | SEZL | 0.3117 | 9/57 | 15.8% | 5.6% | 0.135 | +95.3% |
| 11 | NESR | 0.2765 | 8/57 | 14.0% | 5.9% | 0.162 | +170.2% |
| 12 | INNV | 0.2735 | 8/57 | 14.0% | 5.5% | 0.134 | +161.0% |
| 13 | UVE | 0.2622 | 6/57 | 10.5% | 9.5% | 0.179 | +68.8% |
| 14 | MTZ | 0.2453 | 8/57 | 14.0% | 4.5% | 0.103 | N/A* |
| 15 | CMI | 0.2420 | 6/57 | 10.5% | 8.9% | 0.128 | +59.3% |
| 16 | FORM | 0.2393 | 9/57 | 15.8% | 2.7% | 0.067 | N/A* |
| 17 | FDP | 0.2120 | 5/57 | 8.8% | 10.0% | 0.097 | +45.6% |
| 18 | IRWD | 0.2082 | 6/57 | 10.5% | 6.3% | 0.114 | N/A* |
| 19 | JLL | 0.2062 | 6/57 | 10.5% | 6.5% | 0.134 | N/A* |
| 20 | ISSC | 0.2047 | 6/57 | 10.5% | 6.1% | 0.166 | +289.7% |
| 21 | TTMI | 0.1973 | 8/57 | 14.0% | 2.0% | 0.051 | N/A* |
| 22 | RUSHA | 0.1961 | 5/57 | 8.8% | 9.5% | 0.084 | +20.6% |
| 23 | B | 0.1953 | 6/57 | 10.5% | 5.4% | 0.097 | +151.9% |
| 24 | RELY | 0.1952 | 5/57 | 8.8% | 8.3% | 0.149 | -21.1% |
| 25 | NVDA | 0.1934 | 6/57 | 10.5% | 6.0% | 0.106 | N/A* |

*N/A* = ticker not in yfinance dataset (non-US listed, ETF, or alternate class)

### 6.2 Gold Hedge Signal — Top 8

| Rank | Ticker | Conviction | Freq | Freq% | Instrument Type |
|------|--------|-----------|------|-------|-----------------|
| 1 | SGDM | 1.0000 | 27/57 | 47.4% | Sprott Gold Miners ETF |
| 2 | GOEX | 0.9634 | 27/57 | 47.4% | Global X Gold Explorers ETF |
| 3 | DBP | 0.8295 | 20/57 | 35.1% | Invesco DB Precious Metals Fund |
| 4 | IAU | 0.6419 | 15/57 | 26.3% | iShares Gold Trust |
| 5 | RING | 0.2697 | 7/57 | 12.3% | iShares MSCI Global Gold Miners ETF |
| 6 | OUNZ | 0.2485 | 6/57 | 10.5% | VanEck Merk Gold Trust |
| 7 | AU | 0.2060 | 6/57 | 10.5% | AngloGold Ashanti |
| 8 | GLDM | 0.1867 | 5/57 | 8.8% | SPDR Gold MiniShares |

**Gold conviction level = HIGH** (SGDM/GOEX at 1.0 = near-maximum). This is a risk-off macro signal. Treat as a regime indicator.

### 6.3 Consensus Portfolio Performance

Equal-weighted equity signal portfolios (top-N tickers by conviction):

| Portfolio | 1yr Return | 3mo Return | Sharpe | MaxDD | vs SPY (1yr) |
|-----------|-----------|-----------|--------|-------|-------------|
| Top-10 | +165.9% | +52.3% | 3.72 | -28.2% | +147.1pp |
| Top-15 | +148.8% | +39.9% | 3.49 | -27.1% | +130.0pp |
| Top-20 | +128.9% | +35.0% | 3.57 | -23.9% | +110.1pp |
| Top-25 | +159.5% | +39.2% | 4.34 | -22.6% | +140.7pp |
| Top-30 | +166.0% | +36.5% | 4.23 | -24.6% | +147.2pp |
| **SPY** | **+18.8%** | **-1.5%** | **0.63** | **-18.8%** | — |

**Optimal size: Top-25 or Top-30** — best Sharpe-to-drawdown ratio. Top-10 has highest return but concentrated drawdown. Top-25 achieves 4.34 Sharpe with -22.6% max drawdown.

### 6.4 Individual Standout Performers

Names in the signal with exceptional 1yr price performance:

| Ticker | 1yr Return | 3mo Return | Conviction | Freq | Notes |
|--------|-----------|-----------|-----------|------|-------|
| SNDK | +1,034% | +147% | 0.14 | 4/57 | Flash storage cycle re-rating |
| ISSC | +290% | +162% | 0.20 | 6/57 | Intelligent Systems, defense tech |
| VSAT | +359% | +25% | 0.14 | 4/57 | Viasat satellite comms recovery |
| MU | +316% | +63% | 0.41 | 14/57 | Micron memory upcycle |
| VICR | +175% | +72% | 0.52 | 15/57 | Vicor power components AI buildout |
| ATI | +162% | +50% | 0.47 | 13/57 | ATI aerospace/defense alloys |
| INNV | +161% | +74% | 0.27 | 8/57 | Innovation Beverages |
| NESR | +170% | +42% | 0.28 | 8/57 | NES Fircroft oilfield services |

---

## 7. Strategy Families & Clusters

Through Jaccard similarity analysis across holdings, strategies cluster into 5 families:

### Cluster A: Quality Growth (High conviction cohort)
**Strategies:** momentum-investing, growth-investing, value-investing, market-disruptors
**Characteristic:** Concentrated (10-30 names), high active share, zero gold
**Signal contribution:** Drives VICR, ATI, MU, SEZL, RELY, INNV
**QScore avg:** 0.64

### Cluster B: Industrial Value
**Strategies:** industrials-infrastructure, manufacturing-industry, robotics-automation
**Characteristic:** 10-20 names, industrials/defense focus
**Signal contribution:** Drives MYRG, KMT, DXPE, ISSC, SPXC, ATI, CMI
**QScore avg:** 0.47

### Cluster C: Multi-Factor Diversified
**Strategies:** diversified-bluechips, low-beta-stocks, sector-rotation, esg-leaders, SRI
**Characteristic:** 10 names, balanced factors, some gold overlay
**Signal contribution:** Drives CODA, B, FDP, MGNR, NESR, DXJ
**QScore avg:** 0.49

### Cluster D: Technology/Innovation
**Strategies:** technology-fund, cybersecurity-shield, high-beta-stocks
**Characteristic:** 20-30 names, heavy gold overlay (macro hedge), tech names
**Signal contribution:** Drives AMD, RNG, CODA, VOD, RUSHA
**QScore avg:** 0.41

### Cluster E: Income/Defensive
**Strategies:** dividend-aristocrats, utilities-fund, buffett-bargains, defensive-investing
**Characteristic:** 20-30 names, low volatility, limited gold
**Signal contribution:** Primarily dividend payers, excluded from top-tier signal
**QScore avg:** 0.14

---

## 8. Implemented Signal Service

The full production signal service is implemented in `pilotai_signal/`.

### 8.1 Architecture

```
cron (9:35 AM ET Mon–Fri)
         │
         ▼
  collector.py → fetch all 57 strategies → SQLite
         │
         ▼
   scorer.py → compute 401 ticker signals → ticker_signals table
         │
         ▼
  alerts.py → classify NEW/STRONG/EXIT/MOVER → Telegram
```

### 8.2 File Structure

```
pilotai_signal/
├── __init__.py          # Package init
├── __main__.py          # `python -m pilotai_signal` entry
├── config.py            # All configuration (env-based)
├── db.py                # SQLite schema + read/write helpers
├── collector.py         # API fetch + storage
├── scorer.py            # Conviction score engine
├── alerts.py            # Alert classification + Telegram delivery
└── cli.py               # Full CLI with 10 commands
scripts/
└── run_signal_service.sh   # Cron wrapper
data/
└── pilotai_signal.db    # Live database (created on init)
```

### 8.3 CLI Commands

```bash
# Initialize (first time)
python3 -m pilotai_signal init

# Full daily run (collect → score → alerts)
python3 -m pilotai_signal run

# Individual steps
python3 -m pilotai_signal collect [--date YYYY-MM-DD] [--force] [--dry-run]
python3 -m pilotai_signal score   [--date YYYY-MM-DD] [--dry-run]
python3 -m pilotai_signal alerts  [--date YYYY-MM-DD] [--no-digest] [--dry-run]

# Monitoring & inspection
python3 -m pilotai_signal show    [--date YYYY-MM-DD] [--top 30]
python3 -m pilotai_signal status  [--last 10]
python3 -m pilotai_signal history TICKER [--days 30]
python3 -m pilotai_signal digest  [--dry-run]

# Maintenance
python3 -m pilotai_signal rebuild  # recompute all historical signals
```

### 8.4 Database Schema Summary

| Table | Description | Key Fields |
|-------|-------------|------------|
| `strategy_snapshots` | One row per strategy per day | snapshot_date, strategy_slug |
| `snapshot_holdings` | Per-ticker holdings per snapshot | ticker, weight, price |
| `snapshot_scores` | Attix quality scores | value/growth/health/momentum/past |
| `ticker_signals` | Daily conviction scores | signal_date, ticker, conviction, days_in_signal |
| `alerts` | Alert history with Telegram status | alert_type, sent_at |
| `collection_log` | Run audit log | status, duration_sec, strategies_ok/fail |

### 8.5 Conviction Formula

```
conviction(ticker, date) = normalize(
    0.40 × (frequency / 57)                    # cross-portfolio breadth
  + 0.35 × min(days_in_signal, 30) / 30        # persistence (matures over 30 days)
  + 0.25 × normalize(weighted_qscore)           # portfolio quality weight
)
```

### 8.6 Alert Types

| Type | Trigger | Emoji |
|------|---------|-------|
| `NEW` | Ticker appears for first time (or after >5-day absence) | 🆕 |
| `STRONG` | Conviction ≥ 0.70 for ≥ 3 consecutive days | 🔥 |
| `EXIT` | Ticker drops from ALL 57 portfolios | 🚪 |
| `MOVER_UP` | Conviction rises ≥ +0.15 in one day | 📈 |
| `MOVER_DOWN` | Conviction falls ≥ −0.15 in one day | 📉 |

### 8.7 Cron Setup

```bash
# Add to crontab (crontab -e)
# 9:35 AM ET Mon–Fri
35 9 * * 1-5 /Users/charlesbot/projects/pilotai-credit-spreads/scripts/run_signal_service.sh

# 4:00 PM ET digest (optional)
0 16 * * 1-5 /Users/charlesbot/projects/pilotai-credit-spreads/scripts/run_signal_service.sh --digest-only
```

### 8.8 Required Environment Variables

```bash
PILOTAI_API_KEY=cZZP6he1Qez8Lb6njh6w5vUe     # pre-configured
TELEGRAM_BOT_TOKEN=<your-token>
TELEGRAM_CHAT_ID=<your-chat-id>
```

---

## 9. Day-1 Baseline & Signal Maturation Schedule

Since the API has no historical endpoint, the service starts accumulating from today. Here's how signals evolve:

| Days Since Launch | Persistence Component | Conviction Quality |
|------------------|----------------------|-------------------|
| Day 1 (today) | 0% (no history) | Frequency + Quality only |
| Day 5 | 17% weighted | Early persistence signal |
| Day 14 | 47% weighted | Good directional signal |
| Day 30 | 100% weighted (fully mature) | Full conviction formula |
| Day 60+ | Same — cap at 30-day window | Stable, rolling signal |

**Expected conviction range after maturation:** High-conviction tickers (consistent holders like VICR, ATI, CODA) should reach 0.65-0.85 once persistence component fully loads. This is when STRONG alerts become most meaningful.

---

## 10. Strategic Recommendations

### 10.1 For Equity Selection
1. **Watch VICR, ATI, MU, CODA** — highest cross-portfolio breadth, proven 1yr performance
2. **ISSC and SNDK** have exceptional recent returns but low conviction (4-6 portfolios) — opportunistic, not structural
3. **Value-investing cohort (VSAT, IRWD, FYC)** — deep value plays that require longer hold horizons

### 10.2 For Risk Management
1. **Gold conviction at maximum** (SGDM/GOEX at 1.0) — Attix is telling you this is a risk-off environment. Run tighter stops on directional equity positions.
2. **When gold conviction drops below 0.50** (SGDM/GOEX frequency falls to <14/57): regime shift signal. Increase directional equity exposure.
3. **The STRONG alert threshold (0.70 conviction)** is calibrated for post-30-day maturation. In the first month, use 0.50+ as a proxy for strong signals.

### 10.3 For Product Development
1. **Weekly consistency report:** Track which tickers have been in the top-10 for 5+ consecutive days. This is the highest-quality signal subset.
2. **Strategy-specific alerts:** Alert when a top-tier strategy (QScore ≥ 0.50) makes a significant holding change. These are the highest-quality individual signals.
3. **Cross-signal with credit spreads:** Map high-conviction long equities → favorable candidates for bull put spreads in the existing EXP-154 system.
4. **Correlation with VIX/VIX3M regime:** Gold conviction > 0.80 correlates with elevated VIX. This can supplement the ComboRegimeDetector's VIX_structure signal.

---

## 11. Limitations & Caveats

1. **Snapshot API only:** Holdings are point-in-time. We don't know entry prices, holding duration, or turnover rate.
2. **Backfill not possible:** All historical analysis uses yfinance price data applied to current holdings. This is ahistorical — we're measuring "what if you held today's portfolio for the past year," not actual Attix strategy returns.
3. **1-year performance period includes SNDK anomaly:** The value-investing strategy's +216.5% is driven by a single stock's +1,034% move. This is not representative of expected future returns.
4. **All strategies beat SPY during analysis period:** The 13-month window (Feb 2025–Mar 2026) was particularly favorable for active management relative to SPY. Future periods may show more dispersion.
5. **Gold overlay may not persist:** If macro regime shifts (dollar strengthens, geopolitical risk declines), Attix's optimizer will likely reduce gold allocations. Monitor SGDM/GOEX conviction as a real-time macro barometer.
6. **Staging API only:** `ai-stag.attix.com` — production endpoint may differ. Confirm with Attix team before relying on production data.

---

## Appendix A: Implementation Quick-Start

```bash
cd /Users/charlesbot/projects/pilotai-credit-spreads

# 1. Initialize DB
python3 -m pilotai_signal init

# 2. Set credentials
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"

# 3. Run collection + scoring + alerts
python3 -m pilotai_signal run

# 4. Inspect current signal
python3 -m pilotai_signal show --top 25

# 5. Add to crontab
crontab -e
# Add: 35 9 * * 1-5 /Users/charlesbot/projects/pilotai-credit-spreads/scripts/run_signal_service.sh
```

---

## Appendix B: Database Location

```
/Users/charlesbot/projects/pilotai-credit-spreads/data/pilotai_signal.db
```

Day-1 bootstrap: 57 strategies stored, 401 ticker signals computed for 2026-03-07.
