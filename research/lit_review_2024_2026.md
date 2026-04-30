# Options Pricing Literature Review 2024–2026

**Generated:** 2026-04-29
**Author:** Read-only literature scan, web-sourced 2026-04-29
**Mandate:** Survey arXiv & SSRN for 2024-2026 options-pricing research across four focus areas:
1. VRP extraction techniques
2. Portfolio construction for options strategies
3. Alternative data for options alpha
4. Market microstructure improvements

Findings are mapped to the **North Star targets** defined in `MASTERPLAN.md` §2.
**Provenance discipline:** Citations come from search-snippet metadata. Numerical claims attributed to a paper come from the search excerpt and have not been re-derived from the full PDF. Each priority paper must be read end-to-end before any EXP-3xxx integration.

**Companion documents:**
- `compass/reports/literature_review_2024_2026.md` — broader cross-cut version
- `compass/research_apr29_literature_review.md` — session-log version
- `research/AUM_CAPACITY_RESEARCH.md` (existing)
- `research/ML_IMPROVEMENT_ANALYSIS.md` (existing)

---

## North Star Targets — recap

| Target | Goal | Current v8a |
|---|---|---|
| Sharpe | ≥ 6.0 | 6.00 net Alpaca (3.5–4.5 expected live) |
| CAGR | ≥ 100% | ~93% net Alpaca |
| Max DD | ≤ 12% | 4.2% (with EXP-2370 circuit) |
| 6/6 years positive | yes | yes |
| AUM capacity | ≥ $500M | ~$50M (SLV-bottlenecked) |
| Multi-strategy streams | ≥ 5 | 8 |
| Rule Zero (real data) | 100% | 100% |

---

## 1. VRP Extraction Techniques

### 1.1 Dew-Becker, I. & Giglio, S. (2025) — "The Decline of the Variance Risk Premium: Evidence from Traded and Synthetic Options"
- **Venue:** SSRN id 5525882, Sep 2025.
- **Key insight (per snippet):** VRP is structurally lower in post-2022 regime than the 2010-2019 baseline most options-selling backtests rely on. Both *traded* and *synthetic* option-portfolio VRP measures show the decline.
- **Testable idea — North Star Sharpe & CAGR:**
  > **H1.** Re-running exp1220 on the 2023-01-01 → present sub-sample yields a Sharpe ≥ 1.5 SE below the 2018-2025 full-sample Sharpe.
  > Reporting both numbers honestly tightens the gap between v8a's headline 6.00 and the live-quotable expected 3.5-4.5 (MASTERPLAN Rule 13).
- **Effort:** ~1 day; reuses exp1220 with a date filter.
- **Link:** https://papers.ssrn.com/sol3/Delivery.cfm/5525882.pdf?abstractid=5525882&mirid=1

### 1.2 (Author per AEA program) — "Exploring the Variance Risk Premium Across Assets"
- **Venue:** AEA 2024 program · SSRN 4373509.
- **Key insight (per snippet):** Constructs *model-independent* option-portfolios whose payoffs correlate with realized variance at median ρ > 99% across assets — better than the CBOE VIX formula's correlation. Easily implementable, no interpolation, no model assumptions.
- **Testable idea — Multi-strategy & Rule Zero:**
  > **H2.** Replace the existing VRP estimator inside cross_vol stream with the model-independent option-portfolio formulation. Target ≥ 0.05 cross_vol Sharpe lift on OOS while **eliminating** any interpolation step (improving Rule Zero compliance).
- **Effort:** Medium — ~3 days to re-implement and benchmark.
- **Links:** https://www.aeaweb.org/conference/2024/program/paper/hiTeT8SE · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4373509

### 1.3 Papagelis (2025) — "The Variance Risk Premium Over Trading and Nontrading Periods"
- **Venue:** *Journal of Futures Markets*, 2025.
- **Key insight (per snippet):** Decomposes model-free VRP into *overnight* and *intraday* components across U.S., Europe, and Asia indices.
- **Testable idea — Sharpe & DD:**
  > **H3.** Add an overnight-VRP gate to v8a credit-spread streams: open positions only when overnight VRP percentile is in upper half of trailing 252-day window. Target: lower DD (gap-risk avoidance) without Sharpe degradation.
- **Effort:** Medium — overnight implied-variance series construction needed.
- **Link:** https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22589

### 1.4 Fouhy (2026) — "Hierarchical Machine Learning for Variance Risk Premium Estimation"
- **Venue:** SSRN 6570380, Mar 2026.
- **Key insight (per snippet):** Hierarchical ML predicts VIX → systematic SPX options trading. Direct competitor to cross_vol stream.
- **Testable idea — Sharpe benchmark:**
  > **H4.** Replicate Fouhy's pipeline as an external benchmark; v8a cross_vol Sharpe must beat (or match within 1 SE) Fouhy's reported number on the same OOS window. If not, redesign cross_vol.
- **Effort:** High — ~1-2 weeks to faithfully replicate.
- **Link:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6570380

### 1.5 Khalil (CBS WP) — "Zero-Day-to-Expiry Options Trading and Variance Risk Premium"
- **Venue:** Copenhagen Business School working paper.
- **Key insight (per snippet):** 0DTE growth biases standard VRP measurement (term-structure end-points contaminated by intraday flow).
- **Testable idea — Rule Zero / Sharpe:**
  > **H5.** Compute v8a's VRP-related signals using only ≥ 7-day expiry options (excluding 0DTE & 1DTE) and verify Sharpe stability vs. existing implementation.
- **Effort:** Low — filter on `dte` field; ~1 day.
- **Link:** https://research-api.cbs.dk/ws/portalfiles/portal/105671291/1775874_O._Khalil_Zero_Day_to_Expiry_Options_Trading_and_Variance_Risk_Premium.pdf

### 1.6 arXiv 2509.08096 — "Joint Calibration of the Volatility Surface and Variance Term Structure"
- **Venue:** arXiv preprint Sep 2025.
- **Key insight (per snippet):** Joint calibration — couples implied-vol-surface fit with variance-term-structure consistency.
- **Use case:** Cleaner VRP feature engineering for ML-based estimators (links to A4 / A5 in companion doc).
- **Link:** https://arxiv.org/html/2509.08096

---

## 2. Portfolio Construction for Options Strategies

### 2.1 Patel et al. (2024-25) — "Sizing the Risk: Kelly, VIX, and Hybrid Approaches in Put-Writing on Index Options"
- **Venue:** arXiv 2508.16598 (Aug 2025; cited as Patel et al. 2024 in some search results — version uncertain, verify on arXiv abs page).
- **Key insight (per snippet):** Direct study of **put-writing on SPXW (weekly SPX)** with Kelly, VIX-scaled, and hybrid sizing. Confirms CBOE PutWrite Index outperformance is VRP exposure.
- **Testable idea — Sharpe & AUM capacity:**
  > **H6.** v8a currently sizes by Ledoit-Wolf risk-parity. Implement Patel's hybrid Kelly+VIX sizing as a competing rule on exp1220; benchmark on identical 20-fold WF. If Sharpe lift ≥ 0.10 with no DD degradation, adopt for at least one stream.
  > **H7.** SPXW (weekly) put-writing also extends MASTERPLAN Phase-8 EXP-2580 SPY-weekly capacity work — Patel paper provides published comparators.
- **Effort:** Medium — ~1 week to implement two new sizing rules in the position-sizer + benchmark.
- **Link:** https://arxiv.org/pdf/2508.16598

### 2.2 "Optimal Kelly Portfolio under Risk Constraints" (2025)
- **Venue:** SciRP 2025.
- **Key insight (per snippet):** Adds a portfolio unit-risk penalty term to standard Kelly objective; uses ML clustering for asset selection. Empirical work on A-shares.
- **Testable idea — DD reduction:**
  > **H8.** Apply the risk-penalised Kelly objective at the *cross-stream* level (replacing Ledoit-Wolf risk-parity) and measure max DD under EXP-2370 circuit. Hypothesis: DD ≤ 4% (current 4.2%) without Sharpe loss.
- **Effort:** Medium — alternative cross-stream weighting test.
- **Link:** https://www.scirp.org/pdf/eng2025173_38104721.pdf

### 2.3 "On Transaction Costs in Minimum-Risk Portfolios" (Frontiers 2025)
- **Venue:** Frontiers in Applied Mathematics and Statistics, 2025.
- **Key insight (per snippet):** Mean-variance and CVaR-risk-parity comparison under realistic TC. Costs can shift annual portfolio returns by 0.5-2% depending on rebalance frequency.
- **Testable idea — Sharpe & CAGR:**
  > **H9.** Re-run v8a's portfolio overlay with an explicit TC-penalty in the optimizer (not as post-hoc deduction) using the EXP-2420 cost model. Quantify Sharpe difference vs current pipeline.
- **Effort:** Medium — modifies existing risk-parity solver.
- **Link:** https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2025.1585187/full

### 2.4 Hierarchical Risk Parity (HRP) — multiple 2024-2025 studies
- **Venues:** arXiv 2509.03712 · *Journal of Economic Analysis* (Anser Press) 2024 · PLOS One Aug 2025.
- **Key insight (per snippet):** Non-parametric, no-matrix-inversion portfolio allocation using cluster hierarchies. Avoids covariance estimation noise that hurts Markowitz at low N.
- **Testable idea — DD & Sharpe:**
  > **H10.** Replace Ledoit-Wolf weights with HRP across the 8 streams. HRP's robustness to covariance noise is precisely the failure mode that drove EXP-2360 retractions (smeared inputs). Target ≥ 0.05 Sharpe lift OR strictly lower DD with same Sharpe.
- **Effort:** Low-medium — `pyportfolioopt` and similar libraries provide HRP off the shelf.
- **Links:** https://arxiv.org/pdf/2509.03712 · https://www.anserpress.org/journal/jea/3/3/68 · https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0330547&type=printable

### 2.5 "Portfolio Allocation Across Variance Risk Premia"
- **Venue:** Existing literature (ResearchGate index — version date uncertain).
- **Key insight:** Cross-asset VRP allocation framework (relevant to current v8a's SPX/QQQ/XLF/XLI/GLD/SLV mix).
- **Testable idea:** Use the framework's cross-asset VRP correlation structure as a sanity-check on v8a's empirical pairwise ρ = +0.016.
- **Link:** https://www.researchgate.net/publication/336721722_Portfolio_allocation_across_variance_risk_premia

---

## 3. Alternative Data for Options Alpha

### 3.1 Kirtac, K. & Germano, G. — "Sentiment Trading with Large Language Models"
- **Venue:** arXiv 2412.19245 · SSRN 4706629.
- **Authors:** Kemal Kirtac, Guido Germano (UCL).
- **Key insight (per snippet):** OPT-based long-short on 965K U.S. financial news 2010-2023; long-short Sharpe 3.05 (10 bps TC); 355% return Aug 2021–Jul 2023; OPT 74.4% sentiment accuracy vs. BERT 72.5%, FINBERT 72.2%.
- **Testable idea — Multi-strategy ≥ 5 / capacity ≥ $500M:**
  > **H11.** Add an LLM-headline-sentiment **veto layer** to v8a credit spreads: skip put-credit-spreads on names with sentiment percentile < 5 in trailing 5 days. Hypothesis: reduces tail-loss frequency without Sharpe penalty.
  > **H12.** A *separate stream* (long-only equity LS based on LLM sentiment) is plausibly capacity-rich, but is **outside the options remit** and would alter the system's identity. Defer.
- **Effort:** Medium — Open-source FinBERT; 1 week. (Don't re-train OPT.)
- **Link:** https://arxiv.org/abs/2412.19245

### 3.2 Citadel Securities — "Flows and Fundamentals"
- **Venue:** Citadel Securities news, 2024-25.
- **Key insight (per snippet):** Documents the "calm index / busy constituents" 2024-25 regime. Aggregates options flow + fundamentals into a unified market view.
- **Testable idea — Sharpe & multi-strategy:**
  > **H13.** Cross-sectional dispersion sleeve: long top-10 SPX-component vol, short SPX vol. Operationally heavy but addresses a regime v8a is structurally short.
- **Effort:** High — new infra (basket vol portfolio); ~2-3 weeks.
- **Link:** https://www.citadelsecurities.com/news-and-insights/flows-and-fundamentals/

### 3.3 Dim, Eraker, Vilkov — "0DTEs: Trading, Gamma Risk and Volatility Propagation" (SSRN 4692190); follow-up Adams et al. (SSRN 5641974, Oct 2025)
- **Authors:** Chukwuma Dim, Bjørn Eraker, Grigory Vilkov; followup adds Adams, Fontaine, Ornthanalai.
- **Key insight (per snippet):** Dealer net-gamma sign predicts intraday vol regime: positive net gamma → mean-reversion, negative → momentum. The follow-up paper adds intraday market-maker hedging needs predict order-flow reversals.
- **Testable idea — Sharpe & DD:**
  > **H14 (★ priority).** Build a dealer-GEX proxy from CBOE OI snapshots; gate v8a credit-spread streams on net-gamma sign. Target: ≥ 0.10 Sharpe lift, no DD-circuit-breaker false positives.
- **Effort:** Medium — ~3 days for proxy + backtest; high priority because the signal is causal-mechanism-explained.
- **Links:** https://papers.ssrn.com/sol3/Delivery.cfm/4692190.pdf?abstractid=4692190 · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5641974

### 3.4 Industry alt-data benchmarks (J.P. Morgan / Lowenstein 2025 surveys)
- **Key insight (per snippet):** ~65% of hedge funds use alt data; +3% annual returns vs. those that don't. Social/sentiment 13.6% of alt-data spend; satellite 5.1% (fastest-growing as commercial constellation costs fall 30-40%/yr).
- **Testable idea — AUM capacity:**
  > **H15.** Capacity-additive thesis: a 9th stream consuming alt-data, even at modest Sharpe, lifts portfolio Sharpe via diversification (low ρ to existing VRP-driven streams). Worth a single-stream feasibility study, not a primary investment.
- **Links:** https://www.luxalgo.com/blog/alternative-data-for-algorithmic-trading-what-works/ · https://www.lowenstein.com/media/jujd45bp/alt-data-report-2025_final.pdf

---

## 4. Market Microstructure Improvements

### 4.1 OPRA capacity — Feb 2024 96-line migration
- **Venues:** Databento OPRA microstructure guide; CBOE 2024 Options Market Structure white paper.
- **Key insight (per snippet):** OPRA capacity 400B → 1T transactions/day. 99th-percentile latency 543.5 µs → 57.5 µs. Median latency 19.5-20.5 µs since Pillar.
- **Implication for v8a — AUM capacity:**
  > **H16.** EXP-2470 execution stack assumes specific bid-ask realizations. Update its TC simulator with post-Feb-2024 OPRA latency distribution; the assumed slippage may be over-conservative now (which is good for capacity claims).
- **Effort:** Low — parameter update + re-run sensitivity.
- **Links:** https://databento.com/microstructure/opra · https://cdn.cboe.com/resources/government_relations/FINAL-Options-Market-Structure-Document-v14-2024.pdf

### 4.2 April 2025 OPRA stress data point
- **Source:** Databento microstructure analysis.
- **Key insight (per snippet):** April 2025 sell-off saw 1ms-burst peaks > 23.7M packets/sec, > 187M msgs/sec on OPRA.
- **Implication for v8a — DD & circuit-breaker:**
  > **H17.** Validate EXP-2370 DD circuit-breaker against the Apr 2025 sell-off as a real OOS stress (not synthetic MC). Quantify if circuit fired correctly and pricing-feed loss probability under that load.
- **Effort:** Low — date-window backtest.

### 4.3 "Risky Intraday Order Flow and Option Liquidity" (Doshi et al., May 2025)
- **Venue:** Bauer College working paper, May 2025.
- **Key insight (per snippet):** Cross-sectional study — transactions occur on the exchange offering best quoted spread; liquidity adjusts after trades. Has direct bearing on smart-router effectiveness.
- **Implication for v8a — Sharpe (cost) & capacity:**
  > **H18.** Audit EXP-2470 stack-component C ("route to cheapest $/notional") against this paper's empirical route-quality results. If our routing assumption diverges from observed empirics, slippage estimates need a correction term.
- **Effort:** Low-medium — cost-model audit.
- **Link:** https://www.bauer.uh.edu/hdoshi/docs/DPS_May_2025.pdf

### 4.4 arXiv 2507.16701 — "Binary Tree Option Pricing Under Market Microstructure Effects: Random Forest Approach"
- **Venue:** arXiv preprint Jul 2025.
- **Key insight (per snippet):** Minute-level SPY data (Jan-Jun 2025); RF model 88.25% AUC for short-term price-direction prediction. **Order-flow imbalance is the dominant feature (43.2% importance).**
- **Testable idea — Sharpe (intraday execution):**
  > **H19.** Add an order-flow-imbalance feature to the v8a entry timing logic; defer entry by up to 60s if the trailing 5-min imbalance is in the worst-quintile. Target: ≥ 5 bps slippage reduction at portfolio level.
- **Effort:** Medium — requires intraday flow data (Polygon free tier minute aggregates suffice); ~1 week.
- **Link:** https://arxiv.org/html/2507.16701v1

### 4.5 "Increase Alpha: Performance and Risk of an AI-Driven Trading Framework"
- **Venue:** arXiv 2509.16707, Sep 2025.
- **Key insight (per snippet):** Identifies pockets of cross-sectional opportunity that, harvested repeatedly, build a high-quality return stream.
- **Implication for v8a:** Useful as inspiration for a cross-sectional vol sleeve (links to §3.2 dispersion idea); **not** a turnkey component.
- **Link:** https://arxiv.org/html/2509.16707v1

### 4.6 SpotV2Net (Jan 2024) — "Multivariate Intraday Spot Volatility Forecasting"
- **Venue:** arXiv 2401.06249.
- **Key insight (per snippet):** Multivariate spot-vol forecaster — relevant to cross_vol stream's vol forecasting piece.
- **Link:** https://www.arxiv.org/pdf/2401.06249v1

---

## 5. Cross-Cutting Synthesis — North Star Mapping

| North Star Target | Most-Promising Hypotheses | Combined Impact |
|---|---|---|
| **Sharpe ≥ 6.0** (currently met on backtest, decay-uncertain on live) | H1 (post-2022 sub-sample), H4 (Fouhy benchmark), H6 (Patel sizing), H10 (HRP), H14 (GEX gate ★) | If GEX gate + HRP each lift ≥ 0.05 Sharpe, that buffers the Rule-13 decay from 6.00 → 4.0 |
| **CAGR ≥ 100%** (~93% currently) | H6 (Patel hybrid Kelly), H9 (TC-aware optimizer) | Sizing improvements unlock CAGR without leverage |
| **Max DD ≤ 12%** (currently 4.2%) | H3 (overnight VRP gate), H8 (risk-penalised Kelly), H17 (Apr 2025 OOS validation) | Already well under target — prioritise validation over reduction |
| **AUM capacity ≥ $500M** (currently ~$50M) | H6/H7 (SPXW weekly stream), H13 (dispersion sleeve), H15 (alt-data 9th stream), H16 (post-OPRA-upgrade slippage re-estimate) | The bottleneck remains structural (ETF-vega capacity). Mostly addressed by *adding* high-capacity streams, not optimizing existing |
| **Multi-strategy ≥ 5** (currently 8) | H11 (LLM veto), H13 (dispersion), H19 (OFI-aware execution) | Already exceeded; new ideas are *complements*, not replacements |
| **Rule Zero (real data 100%)** | H2 (model-independent VRP, eliminates interpolation), H5 (≥7-DTE filter for VRP) | Improvements eliminate edge-cases where real data was being smoothed |

---

## 6. Top-3 EXP-3xxx Candidates (priority-ranked)

| Rank | EXP candidate | Source | Effort | Expected Sharpe Δ | Risk |
|---|---|---|---|---|---|
| ★1 | **EXP-3000 — Dealer GEX Regime Gate** | Dim/Eraker/Vilkov + Adams et al. | ~3d | +0.10 | Proxy-validity risk (must benchmark against published GEX) |
| ★2 | **EXP-3010 — Post-2022 VRP-percentile Gate** | Dew-Becker & Giglio | ~1d | 0 (frequency-reducer) | Zero — worst case = identical Sharpe with fewer trades |
| ★3 | **EXP-3020 — HRP cross-stream weighting** | HRP literature | ~3d | +0.05 to +0.10 | Curve-fitting to current 8-stream selection |

A 4th-tier is **EXP-3030 — Patel hybrid-Kelly sizing** (longer integration but addresses both Sharpe and CAGR).

---

## 7. Honest Caveats & Risks

1. **All numerical claims above are search-snippet-level.** None of the cited papers were fetched and read end-to-end in this session. Each priority paper must be read before any EXP-3xxx is launched. Section 6's expected Sharpe Δs are *expected-value priors*, not predictions.
2. **Sharpe comparisons across papers are imperfect** — different cost models, leverage, windows.
3. **MASTERPLAN Bug 6 ("parameter sweeps require OOS validation")** is the dominant failure mode for any ML-flavored experiment. Pre-register OOS windows.
4. **MASTERPLAN Rule 1 (NO SYNTHETIC DATA)** applies — every EXP-3xxx must use IronVault real data only.
5. **MASTERPLAN Rule 9 (every overlay re-tested at portfolio level)** — H1-H19 stream-level wins must be revalidated at the 8-stream level before any production move.
6. **Author disambiguation needed** for several papers — search snippets did not always include full author lists or final venue confirmation. Cross-check via arXiv abs / SSRN profile pages before citing externally.

---

## 8. Recommended Next Session

1. Read end-to-end:
   - Dew-Becker & Giglio (SSRN 5525882)
   - Dim/Eraker/Vilkov (SSRN 4692190) + Adams et al. follow-up (SSRN 5641974)
   - Patel et al. (arXiv 2508.16598)
   - Fouhy (SSRN 6570380)
2. Spec EXP-3000, EXP-3010, EXP-3020 in `experiments/registry.json` with pre-registered OOS windows.
3. Compute the 2023-onward sub-sample Sharpe for exp1220 (H1) — this single number, alongside the full-sample 6.00, is the most credibility-positive headline addition possible.

---

## Appendix — Master Source List

### VRP extraction
- [Dew-Becker & Giglio, "The Decline of the VRP" (SSRN 5525882, 2025)](https://papers.ssrn.com/sol3/Delivery.cfm/5525882.pdf?abstractid=5525882&mirid=1)
- [Exploring the VRP Across Assets (AEA 2024)](https://www.aeaweb.org/conference/2024/program/paper/hiTeT8SE) · [SSRN 4373509](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4373509)
- [Papagelis, "VRP Over Trading and Nontrading Periods" (J. Futures Markets 2025)](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22589)
- [Fouhy, "Hierarchical ML for VRP Estimation" (SSRN 6570380, 2026)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6570380)
- [Khalil, "0DTE and VRP" (CBS WP)](https://research-api.cbs.dk/ws/portalfiles/portal/105671291/1775874_O._Khalil_Zero_Day_to_Expiry_Options_Trading_and_Variance_Risk_Premium.pdf)
- [Joint Calibration of Vol Surface (arXiv 2509.08096)](https://arxiv.org/html/2509.08096)

### Portfolio construction
- [Patel et al., "Sizing the Risk: Kelly, VIX, and Hybrid Approaches" (arXiv 2508.16598)](https://arxiv.org/pdf/2508.16598)
- [Optimal Kelly Portfolio under Risk Constraints (SciRP 2025)](https://www.scirp.org/pdf/eng2025173_38104721.pdf)
- [On TC in Minimum-Risk Portfolios (Frontiers 2025)](https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2025.1585187/full)
- [HRP for Portfolio Allocation (arXiv 2509.03712)](https://arxiv.org/pdf/2509.03712)
- [HRP Study (J. Economic Analysis 2024)](https://www.anserpress.org/journal/jea/3/3/68)
- [HRP comparative study (PLOS One 2025)](https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0330547&type=printable)
- [Portfolio Allocation Across VRP](https://www.researchgate.net/publication/336721722_Portfolio_allocation_across_variance_risk_premia)

### Alternative data
- [Kirtac & Germano, "Sentiment Trading with LLMs" (arXiv 2412.19245)](https://arxiv.org/abs/2412.19245) · [SSRN 4706629](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4706629)
- [Dim, Eraker & Vilkov, "0DTEs" (SSRN 4692190)](https://papers.ssrn.com/sol3/Delivery.cfm/4692190.pdf?abstractid=4692190)
- [Adams et al., "Do S&P500 Options Increase Market Volatility?" (SSRN 5641974)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5641974)
- [Citadel Securities, "Flows and Fundamentals"](https://www.citadelsecurities.com/news-and-insights/flows-and-fundamentals/)
- [Lowenstein 2025 alt-data survey](https://www.lowenstein.com/media/jujd45bp/alt-data-report-2025_final.pdf)
- [Lux Algo, "Alt Data for Algorithmic Trading"](https://www.luxalgo.com/blog/alternative-data-for-algorithmic-trading-what-works/)

### Market microstructure
- [Databento OPRA microstructure guide](https://databento.com/microstructure/opra)
- [CBOE 2024 Options Market Structure](https://cdn.cboe.com/resources/government_relations/FINAL-Options-Market-Structure-Document-v14-2024.pdf)
- [Doshi et al., "Risky Intraday Order Flow and Option Liquidity" (Bauer May 2025)](https://www.bauer.uh.edu/hdoshi/docs/DPS_May_2025.pdf)
- [Binary Tree Pricing under Microstructure (arXiv 2507.16701)](https://arxiv.org/html/2507.16701v1)
- [AI-Driven Trading Framework (arXiv 2509.16707)](https://arxiv.org/html/2509.16707v1)
- [SpotV2Net Intraday Spot Vol (arXiv 2401.06249)](https://www.arxiv.org/pdf/2401.06249v1)
- [Forecasting Intraday Volume with ML (arXiv 2505.08180)](https://arxiv.org/html/2505.08180v1)
- [Latency & Trade Classification (microstructure.exchange WP 2024)](https://microstructure.exchange/slides/Latency_TME_2024_JW.pdf)

### Earlier/Companion citations (also relevant)
- [Deep Learning Option Pricing with IV Surfaces (arXiv 2509.05911)](https://arxiv.org/abs/2509.05911)
- [ViT for Realized Vol Forecasting (arXiv 2511.03046)](https://arxiv.org/abs/2511.03046)
- [Deep Hedging with RL (arXiv 2512.12420)](https://arxiv.org/abs/2512.12420)
- [DRL Algorithms for Option Hedging (arXiv 2504.05521)](https://arxiv.org/abs/2504.05521)
- [Deep Hedging Under Market Frictions (MDPI Risks 18/9/497)](https://www.mdpi.com/1911-8074/18/9/497)
- [Deep Learning for Options Trading: End-to-End (arXiv 2407.21791)](https://arxiv.org/abs/2407.21791)
- [Garmash, "Zero DTE Gamma Hedging" (SSRN 5329719)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5329719)
- [de Saint-Cyr, "Iron Condors on SPX" (SSRN 4643378)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4643378)

---

*Read-only literature review. No code or models were modified. Snippet-level provenance — full PDFs required before any EXP-3xxx kickoff.*
