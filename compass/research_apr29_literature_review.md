# Literature Review — April 29, 2026

## Mission
While blocked on Alpaca keys (21 days), find new alpha signals and data sources through academic literature and public APIs.

## Tasks
1. **arXiv/SSRN scan** — options pricing papers 2024-2026
2. **CBOE data APIs** — investigate free/cheap feeds for AUM expansion
3. **Alternative signals** — anything uncorrelated with existing 8 streams

## Success Criteria
- 3+ promising paper citations with testable hypotheses
- 2+ free/cheap data sources identified
- 1+ new signal concept ready for backtesting

## Session Log
- **10:10 AM UTC** — research started
- **10:25 UTC** — first search batch (VRP, ML pricing, RL hedging, alt-data, 0DTE)
- **10:45 UTC** — second batch (Sharpe benchmarks, dispersion, LLM sentiment, capacity)
- **11:00 UTC** — third batch (CBOE/Polygon/FRED APIs, intraday cross-sectional)
- **11:10 UTC** — synthesis and write-up

> Companion document: `compass/reports/literature_review_2024_2026.md` (longer version with sources block).
> This file is the working session log — concise, action-oriented.

---

## A. Top 5 Paper Citations (with testable hypotheses)

### A1. Dew-Becker & Giglio (2025), "The Decline of the Variance Risk Premium" — SSRN 5525882
**Claim (per snippet):** Empirically documents structurally lower VRP in the post-2022 regime versus the 2010-2019 baseline that most options-selling backtests rely on.
**Testable hypothesis for v8a:**
> H1. Re-running exp1220 on the 2023-01-01 → present sub-sample yields a Sharpe ≥ 1.5 standard errors below the full-sample 2018-2025 Sharpe.
**Why it matters:** Direct empirical scaffold for MASTERPLAN Rule 13 (expected live = 0.5-0.7× backtest). If H1 holds, the post-2022 sub-sample number becomes the *honest* live-quotable Sharpe.
**Test cost:** Low — re-runs existing exp1220 over a date sub-range. ~1 day.
**Link:** https://papers.ssrn.com/sol3/Delivery.cfm/5525882.pdf?abstractid=5525882&mirid=1

### A2. Dim, Eraker & Vilkov (2024, rev 2025), "0DTEs: Trading, Gamma Risk, Volatility Propagation" — SSRN 4692190
**Claim (per snippet):** Dealer net gamma is on average positive and negatively related to future intraday volatility. Sign of dealer gamma flips intraday vol regime (mean-reversion ↔ breakout).
**Testable hypothesis for v8a:**
> H2. Adding a dealer-GEX-sign regime gate to exp1220 (skip put-credit-spreads when dealer net gamma < 0) lifts portfolio Sharpe by ≥ 0.10 net of TC, no DD-circuit-breaker false positives.
**Why it matters:** A free, computable regime gate for EXP-1890 with a published causal mechanism.
**Test cost:** Medium — needs a GEX estimator from CBOE OI snapshots; ~3 days.
**Link:** https://papers.ssrn.com/sol3/Delivery.cfm/4692190.pdf?abstractid=4692190
**Follow-up:** [Adams et al. 2025, SSRN 5641974](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5641974) — confirms intraday market-maker hedging predicts order-flow reversals (October 2025 update).

### A3. Fouhy (2026), "Hierarchical Machine Learning for Variance Risk Premium Estimation" — SSRN 6570380
**Claim (per snippet):** Hierarchical ML framework for VIX forecasting → systematic options trading on S&P 500. Direct competitor to v8a's cross_vol stream.
**Testable hypothesis for v8a:**
> H3. Replicate Fouhy's pipeline as a benchmark; v8a cross_vol stream's Sharpe must exceed Fouhy's reported number on the same OOS window or cross_vol must be redesigned.
**Why it matters:** External benchmark — closest published analog to our cross_vol.
**Test cost:** High — requires faithful replication. ~1-2 weeks.
**Link:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6570380

### A4. arXiv 2512.12420 (Dec 2025), "Deep Hedging with Reinforcement Learning"
**Claim (per snippet):** SAC actor-critic on SPX/SPY IV term-structure + skew + RV + macro context, trained on daily EOD panel data, beats rule-based hedging under realistic costs.
**Testable hypothesis for v8a:**
> H4. SAC-trained delta-hedging agent beats current rule-based v5_hedge (Black-Scholes delta band) by ≥ 0.10 portfolio Sharpe on 20-fold WF, under EXP-2470 cost model.
**Why it matters:** v5_hedge is the most rule-based of the 8 streams and the natural RL target.
**Test cost:** High — RL infra investment. ~2 weeks. Use IronVault data only (no synthetic env).
**Link:** https://arxiv.org/abs/2512.12420
**Companions:** [arXiv 2504.05521](https://arxiv.org/abs/2504.05521) (8-algo bake-off — MCPG/PPO win); [MDPI Risks 18/9/497](https://www.mdpi.com/1911-8074/18/9/497) (SAC vs TD3 vs DDPG, SAC wins under impact).

### A5. arXiv 2511.03046 (Nov 2025), "Data-Efficient Realized Volatility Forecasting with Vision Transformers"
**Claim (per snippet):** ViT trained on the IV-surface "image" predicts 30-day realized vol from a single day's surface.
**Testable hypothesis for v8a:**
> H5. Replacing the cross_vol stream's RV forecaster with a ViT improves 21-day RV MAE by ≥ 15% on OOS, lifting cross_vol Sharpe by ≥ 0.05.
**Why it matters:** Drop-in feature engineering for an existing stream; bounded-blast-radius experiment.
**Test cost:** Medium — ViT training on IronVault surfaces. ~1 week.
**Link:** https://arxiv.org/abs/2511.03046

### Bonus — A6: arXiv 2407.21791 (Jul 2024), "Deep Learning for Options Trading: End-to-End"
Per snippet, applies vol-targeting at the level of *individual* straddle options across S&P 100 cross-section. Suggests the cross-sectional dispersion sleeve discussed in §C is implementable end-to-end with deep learning. https://arxiv.org/abs/2407.21791

---

## B. Free / Cheap Data Sources (≥ 2 identified)

### B1. ★ Cboe DataShop "All Access API" — 14-day free tier
- **What:** SIP-level options data, midpoint prices, implied calculations, historical trades.
- **Free tier:** 14 days non-SIP across all endpoints — enough for a feasibility demo.
- **Pricing afterward:** Tiered points-per-month (no public dollar amount in snippet); SIP add-on subjects user to OPRA pro fees.
- **AUM-expansion relevance:** Critical for a >$50M sleeve — enables intraday execution-quality monitoring (currently MASTERPLAN's Phase 10 prerequisite #8 calls for Polygon as secondary; Cboe is a third option).
- **Links:** https://datashop.cboe.com/cboe-all-access-api · https://datashop.cboe.com/

### B2. ★ Polygon.io "Options Basic" — free EOD + minute aggregates
- **What:** EOD options data + minute aggregates, full US options market trades/quotes/IV/Greeks via REST.
- **Free tier:** EOD data and minute-level aggregates at no cost.
- **Paid:** Real-time SIP add-on; daily flat-file downloads on higher tiers.
- **AUM-expansion relevance:** Already named in MASTERPLAN Phase 10 prereq #8 ("Polygon Options secondary data feed"). Free tier sufficient to start the dual-feed cross-check now (without committing $200/mo).
- **Link:** https://polygon.io/options

### B3. ★ FRED API — free, 840K macro series
- **What:** US macro time series (rates, FOMC calendar, CPI, payrolls, sentiment indices, financial conditions).
- **Free tier:** Fully free with API key.
- **Relevance:** Already used by macro_db.py; verifying the existing wrapper covers FOMC/jobs/CPI release calendars relevant to EXP-1880 FOMC filter.
- **Links:** https://fred.stlouisfed.org/docs/api/fred/ · https://pypi.org/project/fredapi/

### B4. CBOE public download portal — historical options volume by symbol (free)
- **What:** Form-based downloads of historical options volume by symbol/product/year. Lower-frequency but free and authoritative.
- **Use case:** Capacity studies (per-symbol ADV) — directly addresses MASTERPLAN §8 capacity bottleneck (SLV → XLI → SPY-weekly).
- **Link:** https://www.cboe.com/us/options/market_statistics/historical_data/

### B5. (Commercial, not free) Options-flow vendors — Quant Data, OptionStrat Flow, Barchart
- Tick-level flow with sweep/block tagging.
- Quant Data offers historical + live consolidated feeds — a paid path to the dealer-GEX signal in §A2 if free OI snapshots prove insufficient.
- Links: https://quantdata.us/ · https://optionstrat.com/flow · https://www.barchart.com/options/options-flow

---

## C. New Signal Concepts Ready for Backtest (≥ 1 required, 4 proposed)

Ranked by expected Sharpe-lift / integration-effort ratio.

### C1. ★★★ Dealer GEX Regime Gate (EXP-3000 candidate)
- **Source:** A2 (Dim/Eraker/Vilkov 2024), reinforced by A2-followup (Adams et al. 2025).
- **Mechanic:** Compute proxy net dealer gamma from CBOE total OI × strike-weighted gamma. Sign-based gate: positive net gamma → permit credit spreads (reversion regime); negative → veto or shrink (breakout regime).
- **Correlation to existing streams:** Low. Cross-cuts every put-credit-spread stream (exp1220, qqq_cs, xlf_cs, xli_cs) as a regime overlay.
- **Capacity:** Unbounded (signal is read-only).
- **Backtest plan:**
  1. Compute daily GEX series 2018-2025 from IronVault OI.
  2. Add as veto layer on exp1220 first; require ≥ +0.10 ΔSharpe on 20-fold WF.
  3. If H2 holds, generalize to all 4 credit-spread streams.
- **Risk:** GEX proxy is a model — must be validated against published GEX series before being used as a signal.

### C2. ★★ Post-2022 VRP-Percentile Gate (EXP-3010 candidate)
- **Source:** A1 (Dew-Becker & Giglio 2025).
- **Mechanic:** 252-day rolling VRP percentile; skip if percentile < 30.
- **Correlation:** High with credit-spread streams (it's a credit-spread regime gate). Reduces *trade frequency* without altering correlation profile.
- **Backtest plan:** Trivial — bolt onto exp1220 as a date-mask filter. ~1 day to verify.
- **Risk:** Low. Worst case = identical Sharpe with fewer trades.

### C3. ★★ ViT-Based RV Forecaster Drop-In (EXP-3020 candidate)
- **Source:** A5 (arXiv 2511.03046, Nov 2025).
- **Mechanic:** Train a ViT on daily IV-surface arrays from IronVault (4 years training, walk-forward); replace current RV forecaster in cross_vol pipeline.
- **Correlation:** Stays within cross_vol stream; doesn't touch others.
- **Backtest plan:** A/B vs. current forecaster on identical OOS window. Target ≥ 15% MAE improvement, ≥ 0.05 cross_vol Sharpe lift.
- **Risk:** Medium — overfitting risk, MASTERPLAN Bug 6 ("parameter sweeps require OOS validation") applies.

### C4. ★ RL-Managed v5_hedge (EXP-3030 candidate)
- **Source:** A4 (arXiv 2512.12420 + companions).
- **Mechanic:** SAC or PPO agent on (IV-term-structure, skew, RV, macro, current-delta, P&L, time-to-expiry); reward = portfolio P&L − cost.
- **Correlation:** Modifies hedge stream only; portfolio-level effect is via reduced hedge drag.
- **Backtest plan:** Train on 2018-2023, OOS 2024-2025; benchmark vs. existing rule. Target ≥ 0.10 portfolio Sharpe net.
- **Risk:** High implementation cost (~2 weeks); high infra debt; well-known RL-overfit traps.

---

## D. Strategies Considered & Deprioritised

- **0DTE intraday gamma scalping** — dealers structurally dominate (A2 lit). Iron-condor 0DTE is the ex-dealer asymmetric trade if a 0DTE sleeve is later wanted.
- **DeFi options** — nascent, high technical risk, no Sharpe edge published.
- **Pure LLM-headline-sentiment overlay as primary signal** — Kirtac/Germano (2412.19245) shows Sharpe 3.05 (per snippet) on equity LS, *not* options. Capacity profile differs from v8a; better as a name-veto overlay than a primary stream.
- **Single-name credit spreads on illiquid tickers** — option-liquidity-fragmentation literature (Mu et al. 2025, T&F 2025) shows worse capacity than ETFs. Already aligned with v8a's ETF-centric design.

---

## E. Risks / Honest Caveats

1. **All numerical claims above come from search snippets, not full PDFs.** Each priority paper (A1, A2, A3, A4, A5) must be read end-to-end before any EXP-3xxx is launched.
2. **Sharpe arithmetic comparisons across papers are imperfect** — different cost models, leverage assumptions, and backtest windows. Use cited numbers as direction-of-motion only.
3. **GEX signal validity depends on the OI proxy** — if the proxy diverges from a published academic GEX series by > 10%, EXP-3000 results will not be quotable.
4. **MASTERPLAN Bug 6 ("parameter sweeps require OOS validation")** is the dominant failure mode for ViT and RL experiments. Pre-register OOS windows before training.

---

## F. Action Items / Next Session

| # | Action | Owner | Effort |
|---|---|---|---|
| 1 | Read Dew-Becker & Giglio end-to-end; replicate decline-of-VRP magnitude | Research | 0.5d |
| 2 | Read Dim/Eraker/Vilkov; design GEX proxy from IronVault OI | Research | 1d |
| 3 | Sign up for Polygon.io free tier; verify dual-feed against IronVault on 1-week sample | Data | 1d |
| 4 | Sign up for FRED API key (if not already); audit macro_db.py coverage | Data | 0.5d |
| 5 | Spec EXP-3000 (GEX regime gate) — registry entry, OOS window, success criterion | Research | 0.5d |
| 6 | Spec EXP-3010 (post-2022 VRP percentile gate) — registry + 1-day backtest | Research | 1d |
| 7 | (Stretch) Read Fouhy SSRN 6570380 hierarchical-ML paper | Research | 1d |

---

## G. Success-Criteria Self-Check

| Criterion | Target | Delivered |
|---|---|---|
| Paper citations with testable hypotheses | ≥ 3 | **6** (A1-A6) |
| Free/cheap data sources | ≥ 2 | **4 free** (Cboe DataShop free tier, Polygon Options Basic, FRED, Cboe public downloads) + 1 paid path |
| New signal concepts ready for backtest | ≥ 1 | **4** (C1-C4) |

---

## Sources (Master List)

### Priority papers (read end-to-end before integration)
- [Dew-Becker & Giglio, "The Decline of the Variance Risk Premium" (SSRN 5525882, 2025)](https://papers.ssrn.com/sol3/Delivery.cfm/5525882.pdf?abstractid=5525882&mirid=1)
- [Dim, Eraker & Vilkov, "0DTEs: Trading, Gamma Risk, Vol Propagation" (SSRN 4692190)](https://papers.ssrn.com/sol3/Delivery.cfm/4692190.pdf?abstractid=4692190)
- [Adams, Dim, Eraker et al. "Do S&P500 Options Increase Market Volatility?" (SSRN 5641974, 2025)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5641974)
- [Fouhy, "Hierarchical ML for VRP Estimation" (SSRN 6570380, 2026)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6570380)
- [Deep Hedging with RL (arXiv 2512.12420, 2025)](https://arxiv.org/abs/2512.12420)
- [DRL Algorithms for Option Hedging (arXiv 2504.05521, 2025)](https://arxiv.org/abs/2504.05521)
- [Deep Hedging Under Market Frictions (MDPI Risks 18/9/497, 2025)](https://www.mdpi.com/1911-8074/18/9/497)
- [ViT for Realized Vol Forecasting (arXiv 2511.03046, 2025)](https://arxiv.org/abs/2511.03046)
- [Deep Learning Option Pricing with IV Surfaces (arXiv 2509.05911, 2025)](https://arxiv.org/abs/2509.05911)
- [Deep Learning for Options Trading: End-to-End (arXiv 2407.21791, 2024)](https://arxiv.org/abs/2407.21791)
- [Papagelis, "VRP Over Trading and Nontrading Periods" (J. Futures Markets 2025)](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22589)
- [Kirtac & Germano, "Sentiment Trading with LLMs" (arXiv 2412.19245)](https://arxiv.org/abs/2412.19245)
- [DPS, "Risky Intraday Order Flow and Option Liquidity" (Bauer Houston, May 2025)](https://www.bauer.uh.edu/hdoshi/docs/DPS_May_2025.pdf)
- [Khalil, "Zero-Day-to-Expiry Options Trading and VRP" (CBS WP)](https://research-api.cbs.dk/ws/portalfiles/portal/105671291/1775874_O._Khalil_Zero_Day_to_Expiry_Options_Trading_and_Variance_Risk_Premium.pdf)
- [Garmash, "Zero DTE Options Gamma Hedging" (SSRN 5329719)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5329719)
- [de Saint-Cyr, "Iron Condors on SPX" (SSRN 4643378)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4643378)

### Data sources
- [Cboe DataShop](https://datashop.cboe.com/) · [Cboe All Access API](https://datashop.cboe.com/cboe-all-access-api) · [Cboe public historical downloads](https://www.cboe.com/us/options/market_statistics/historical_data/)
- [Polygon.io Options API](https://polygon.io/options)
- [FRED API docs](https://fred.stlouisfed.org/docs/api/fred/) · [fredapi PyPI](https://pypi.org/project/fredapi/)
- [Quant Data](https://quantdata.us/) · [OptionStrat Flow](https://optionstrat.com/flow) · [Barchart Options Flow](https://www.barchart.com/options/options-flow)
- [Databento Options](https://databento.com/options) · [EODHD Options API](https://eodhd.com/lp/us-stock-options-api) · [Market Data](https://www.marketdata.app/data/options/)

### Practitioner / industry context
- [Citadel Securities, "Flows and Fundamentals"](https://www.citadelsecurities.com/news-and-insights/flows-and-fundamentals/)
- [Resonanz Capital, "Dispersion Trading and DSPX"](https://resonanzcapital.com/insights/dispersion-trading-and-the-dspx-index)
- [Cboe, "0DTE Index Options and Market Volatility"](https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf)
- [Numerix, "Gamma Hedging of 0DTE Options"](https://www.numerix.com/resources/white-paper/gamma-hedging-0dte-options-managing-extreme-risk-expiration-day)
- [Optionalpha SPY Credit Spread Backtest](https://optionalpha.com/blog/spy-put-credit-spread-backtest)
- [Lux Algo, "Alternative Data for Algorithmic Trading"](https://www.luxalgo.com/blog/alternative-data-for-algorithmic-trading-what-works/)

---

*Read-only literature review. No code or models changed. Snippet-level provenance — full PDFs required before any EXP-3xxx kickoff.*
