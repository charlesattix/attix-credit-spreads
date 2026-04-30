# Options Pricing Literature Review 2024-2026 — Top 15

**Generated:** 2026-04-29
**Mandate:** Top 10-15 papers across (1) VRP strategies, (2) credit spread optimization, (3) volatility surface arbitrage, (4) execution cost models. Tradeable insights for the v8a 8-stream portfolio.
**Provenance:** All claims from web-search snippets retrieved 2026-04-29. **No PDFs were fetched and read end-to-end.** Numerical claims are headline-level until verified.
**Path note:** Requested `~/pilotai-credit-spreads/...` — that path does not exist. Saved to actual workspace at `/home/node/.openclaw/workspace/pilotai-credit-spreads/compass/reports/lit_review_2024_2026.md`.
**Companion docs (overlapping content):** `compass/reports/literature_review_2024_2026.md` · `compass/research_apr29_literature_review.md` · `research/lit_review_2024_2026.md`. This report is the **focused top-15 cut**.

---

## Top-15 Ranking

| # | Tier | Paper | Theme | Tradeable insight |
|---|---|---|---|---|
| 1 | ★★★ | Dew-Becker & Giglio (SSRN 5525882) — "The Decline of the VRP" | VRP | Post-2022 sub-sample Sharpe is structurally lower; report it alongside full-sample 6.00 |
| 2 | ★★★ | Dim, Eraker & Vilkov (SSRN 4692190) — "0DTEs: Trading, Gamma Risk, Vol Propagation" | VRP/Microstructure | Dealer-GEX sign predicts intraday vol regime — usable as causal regime gate |
| 3 | ★★★ | Adams, Dim, Eraker, Fontaine, Ornthanalai, Vilkov (SSRN 5641974) — "Do S&P500 Options Increase Market Volatility? Evidence from 0DTEs" | Microstructure | Confirms market-maker hedging predicts intraday reversals |
| 4 | ★★★ | Patel et al. (arXiv 2508.16598) — "Sizing the Risk: Kelly, VIX & Hybrid in Put-Writing" | Credit-spread / Sizing | Direct comparator to v8a's risk-parity sizing on SPXW put-writing |
| 5 | ★★ | "Stochastic Optimal Control of Iron Condor Portfolios" (arXiv 2501.12397, Jan 2025) | Credit-spread | MC-based iron-condor optimization with profit-and-risk objective |
| 6 | ★★ | Fouhy (SSRN 6570380) — "Hierarchical ML for VRP Estimation" | VRP / ML | External benchmark for v8a cross_vol stream |
| 7 | ★★ | Nguyen (SSRN 6521981) — "Regime-Adaptive Volatility Surface Arbitrage" | Vol-surface arb | SVI + HMM + Kalman; reported OOS Sharpe 1.73 over 2021-2024 |
| 8 | ★★ | "VolGAN" (Taylor & Francis, *Applied Mathematical Finance* 2025) | Vol-surface arb | Arbitrage-free generative IV surfaces — useful for synthetic-stress testing of v8a's cost model |
| 9 | ★★ | "Joint Calibration of the Volatility Surface and Variance Term Structure" (arXiv 2509.08096, Sep 2025) | VRP / Surface | Cleaner VRP feature engineering |
| 10 | ★★ | Doshi et al., "Risky Intraday Order Flow and Option Liquidity" (Bauer May 2025) | Execution / Microstructure | Empirical evidence on which exchanges actually capture flow |
| 11 | ★★ | "Binary Tree Option Pricing Under Market Microstructure: Random Forest" (arXiv 2507.16701, Jul 2025) | Execution / Microstructure | Order-flow imbalance is the dominant predictor (43.2% RF feature importance) |
| 12 | ★★ | Papagelis (J. Futures Markets, 2025) — "VRP Over Trading and Nontrading Periods" | VRP | Overnight-vs-intraday VRP decomposition |
| 13 | ★ | "Optimal Kelly Portfolio under Risk Constraints" (SciRP 2025) | Sizing | Risk-penalised Kelly; ML clustering for asset selection |
| 14 | ★ | "On Transaction Costs in Minimum-Risk Portfolios" (Frontiers 2025) | Execution / Sizing | TC penalty term inside the optimizer changes annual returns by 0.5-2% |
| 15 | ★ | de Saint-Cyr (SSRN 4643378) — "A Simple Historical Analysis of Iron Condors on SPX" | Credit-spread | Empirical baseline for iron-condor benchmarking |

---

## 1. Variance Risk Premium Strategies

### #1 Dew-Becker & Giglio (2025) — "The Decline of the Variance Risk Premium" (SSRN 5525882) ★★★
- **Key finding (per snippet):** Both *traded* and *synthetic* option-portfolio VRP measures show structural decline in post-2022 regime vs 2010-2019 baseline.
- **Tradeable insight:** Re-run exp1220 on 2023-01-01 → present sub-sample, report sub-sample Sharpe alongside the full-sample 6.00. This is the highest-leverage credibility move available — it directly addresses MASTERPLAN Rule 13 ("expected live = 0.5-0.7× backtest") with empirical scaffolding.
- **Effort:** ~1 day. Re-runs existing exp1220 on a date sub-range.
- **Link:** https://papers.ssrn.com/sol3/Delivery.cfm/5525882.pdf?abstractid=5525882&mirid=1

### #6 Fouhy (2026) — "Hierarchical ML for Variance Risk Premium Estimation" (SSRN 6570380) ★★
- **Key finding (per snippet):** Hierarchical ML pipeline: VIX forecast → systematic SPX options trading.
- **Tradeable insight:** External benchmark. v8a's cross_vol stream Sharpe must beat (or match within 1 SE) Fouhy's reported number on a comparable OOS window. If not, redesign cross_vol.
- **Effort:** ~1-2 weeks (faithful replication).
- **Link:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6570380

### #9 "Joint Calibration of the Vol Surface and Variance Term Structure" (arXiv 2509.08096, Sep 2025) ★★
- **Key finding (per snippet):** Couples implied-vol-surface fit with variance-term-structure consistency.
- **Tradeable insight:** Cleaner VRP feature engineering as input to the cross_vol stream's RV forecaster.
- **Link:** https://arxiv.org/html/2509.08096

### #12 Papagelis (2025) — "VRP Over Trading and Nontrading Periods" (J. Futures Markets) ★
- **Key finding (per snippet):** Decomposes VRP into overnight vs. intraday components.
- **Tradeable insight:** Overnight-VRP percentile gate on credit-spread streams to avoid gap risk.
- **Link:** https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22589

---

## 2. Credit Spread / Iron Condor Optimization

### #4 Patel et al. (2025) — "Sizing the Risk: Kelly, VIX, and Hybrid Approaches in Put-Writing" (arXiv 2508.16598) ★★★
- **Key finding (per snippet):** Direct study of put-writing on SPXW (S&P 500 weeklys) with Kelly, VIX-scaled, and hybrid sizing rules. Confirms PutWrite Index outperformance is VRP exposure.
- **Tradeable insight:**
  - Implement hybrid Kelly + VIX-scaled sizing as an alternative to v8a's Ledoit-Wolf risk-parity, benchmark via 20-fold WF on identical IronVault data. Adopt if Sharpe lift ≥ 0.10 with no DD degradation.
  - Aligns with MASTERPLAN Phase 8 EXP-2580 SPY-weekly capacity work.
- **Effort:** Medium — ~1 week.
- **Link:** https://arxiv.org/pdf/2508.16598

### #5 "Stochastic Optimal Control of Iron Condor Portfolios" (arXiv 2501.12397, Jan 2025) ★★
- **Key finding (per snippet):** Fast Monte Carlo option-pricing algo inside an iron-condor-portfolio optimizer; 10K MC paths × 30 repeats for robust statistics.
- **Tradeable insight:** v8a does not currently run iron-condor structures. If a 0DTE / weekly iron-condor sleeve is added (per MASTERPLAN's Phase-8 capacity discussion), this paper's MC framework is a candidate optimizer. Use as a reference design.
- **Effort:** High — only relevant if the iron-condor sleeve is greenlit.
- **Link:** https://arxiv.org/html/2501.12397v1

### #15 de Saint-Cyr — "Iron Condors on SPX" (SSRN 4643378) ★
- **Key finding:** Empirical historical performance of iron-condor structures on SPX. Baseline benchmarks for any iron-condor sleeve.
- **Link:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4643378

---

## 3. Volatility Surface Arbitrage

### #7 Nguyen (2025) — "Regime-Adaptive Volatility Surface Arbitrage" (SSRN 6521981) ★★
- **Key finding (per snippet):** Combines arbitrage-free SVI surface calibration (butterfly + calendar no-arb) with **HMM regime identification + Kalman-filtered delta-hedge ratios**. Validated on 2021-2024 OOS, **annualized Sharpe ≈ 1.73**.
- **Tradeable insight:**
  - The 1.73 Sharpe is well below v8a's headline but the architecture (HMM regimes + Kalman delta) is portable. Could augment cross_vol stream with regime-conditional delta hedging — small expected lift but free real-data signal.
  - More importantly: the paper's no-arb SVI fit can replace v8a's interpolation-based surface (Rule Zero compliance).
- **Effort:** Medium — adopt SVI library; integrate HMM regime tag into existing pipeline.
- **Link:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6521981

### #8 VolGAN — "A Generative Model for Arbitrage-Free Implied Volatility Surfaces" (Taylor & Francis 2025) ★★
- **Key finding (per snippet):** GAN trained on time-series of IV surfaces and underlyings; generates arbitrage-free joint scenarios.
- **Tradeable insight:** Synthetic *stress-testing* (not signal generation) — feed VolGAN scenarios into EXP-2370 DD circuit simulation to find adversarial regimes. Note: this would be a *test-only* use; Rule 1 forbids using synthetic surfaces as backtest data for return claims.
- **Effort:** Medium — only if stress testing is upgraded.
- **Link:** https://www.tandfonline.com/doi/full/10.1080/1350486X.2025.2471317

### Background reference — Gatheral & Jacquier (2014, foundational)
- Arbitrage-free SVI: foundational closed-form parameterizations. Cited because Nguyen's #7 builds on it directly.
- **Link:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2033323

---

## 4. Execution Cost Models for Options

### #2 Dim, Eraker & Vilkov (SSRN 4692190) — "0DTEs: Trading, Gamma Risk, Volatility Propagation" ★★★
- **Key finding (per snippet):** Dealer net gamma is on average positive and *negatively* related to future intraday volatility. Sign of dealer net gamma flips intraday vol regime.
- **Tradeable insight:** **EXP-3000 candidate** — dealer-GEX-sign regime gate on credit-spread streams. Free, computable from CBOE OI; published causal mechanism.
- **Link:** https://papers.ssrn.com/sol3/Delivery.cfm/4692190.pdf?abstractid=4692190

### #3 Adams et al. (2025) — "Do S&P500 Options Increase Market Volatility? Evidence from 0DTEs" (SSRN 5641974) ★★★
- **Key finding (per snippet):** Intraday market-maker hedging needs predict order-flow reversals, lower momentum returns, lower volatility.
- **Tradeable insight:** Validates the EXP-3000 dealer-GEX gate from a complementary angle. Use both papers as joint authority for the gate's causal mechanism.
- **Link:** https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5641974

### #10 Doshi et al. (May 2025) — "Risky Intraday Order Flow and Option Liquidity" (Bauer Houston WP) ★★
- **Key finding (per snippet):** Cross-sectional empirical study — transactions occur on the exchange offering best quoted spread; liquidity adjusts post-trade.
- **Tradeable insight:** Audit EXP-2470 stack-component C ("route to cheapest $/notional") against this paper's empirical results. If our routing assumptions diverge, slippage estimates need a correction term.
- **Effort:** Low — cost-model audit, ~2 days.
- **Link:** https://www.bauer.uh.edu/hdoshi/docs/DPS_May_2025.pdf

### #11 "Binary Tree Option Pricing Under Microstructure: Random Forest" (arXiv 2507.16701, Jul 2025) ★★
- **Key finding (per snippet):** Minute-level SPY data Jan-Jun 2025; RF model achieves 88.25% AUC for short-term price-direction prediction; **order-flow imbalance is the dominant feature (43.2% importance)**.
- **Tradeable insight:** Add an order-flow-imbalance (OFI) feature to v8a's entry-timing layer; defer entry by ≤ 60s if trailing 5-min OFI is in worst-quintile. Target: ≥ 5 bps slippage reduction at portfolio level.
- **Effort:** Medium — needs intraday flow data (Polygon free tier minute aggregates suffice); ~1 week.
- **Link:** https://arxiv.org/html/2507.16701v1

### #14 "On Transaction Costs in Minimum-Risk Portfolios" (Frontiers 2025) ★
- **Key finding (per snippet):** TC penalty inside the optimizer (vs. post-hoc deduction) shifts annual portfolio returns by 0.5-2%.
- **Tradeable insight:** Bring EXP-2420's TC model into the v8a portfolio-overlay optimizer as a true penalty term, not just an after-the-fact adjustment.
- **Effort:** Medium.
- **Link:** https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2025.1585187/full

### Microstructure / OPRA capacity context (auxiliary)
- **Databento OPRA microstructure guide** — Feb-2024 96-line migration: capacity 400B → 1T txns/day; 99th-pct latency 543.5 µs → 57.5 µs. Apr-2025 sell-off peak 23.7M packets/sec. https://databento.com/microstructure/opra
- **CBOE 2024 Options Market Structure white paper** — https://cdn.cboe.com/resources/government_relations/FINAL-Options-Market-Structure-Document-v14-2024.pdf
- **Implication for v8a:** Update EXP-2470 cost model with post-Feb-2024 OPRA latency distribution; current slippage assumptions may be conservative — a positive surprise for AUM-capacity claims.

---

## 5. Sharpe-Ratio Sanity Check (cross-paper)

| Strategy / paper | Sharpe (per snippet) | Window | Notes |
|---|---:|---|---|
| Nguyen vol-surface arb (#7) | 1.73 | 2021-2024 OOS | Single-strategy; portable to v8a as overlay |
| Kirtac/Germano LLM long-short (companion docs) | 3.05 | 2021-2023 | Equity LS, not options |
| Optionalpha SPY put-credit-spread benchmark | 0.56 - 0.83 | 5-year | Single-strategy, no overlays |
| Hedge fund industry (long-run net) | 1 - 2 | — | HighStrike summary 2025 |
| Medallion (long-run net) | ~2.5 | — | MASTERPLAN benchmark |
| **v8a expected-live (Rule 13 decay)** | **3.5 - 4.5** | — | Underwriting target |
| **v8a Alpaca net (headline)** | **6.00** | EXP-2570 | 2-3× SOTA; treat as backtest ceiling |

**Read:** v8a's expected-live 3.5-4.5 is in the upper-tail of the 2024-26 published universe but plausible. The headline 6.00 remains an extraordinary claim, as MASTERPLAN already documents.

---

## 6. Top-3 Tradeable Actions Drawn from this Literature

1. **EXP-3000 — Dealer-GEX regime gate** (papers #2 + #3). 3 days. Expected ≥ +0.10 Sharpe at portfolio level. Highest causal-mechanism strength of any signal in this review.
2. **EXP-3010 — Post-2022 VRP-percentile gate** + **report 2023-onward sub-sample Sharpe alongside the full-sample number** (paper #1). 1 day. Zero downside (worst case = no behaviour change), large upside in honest reporting.
3. **EXP-3020 — Patel hybrid Kelly+VIX sizing** as A/B vs Ledoit-Wolf risk-parity on at least one stream (paper #4). 1 week.

A 4th-tier: **EXP-3030 — OFI-aware entry timing** (paper #11), targeting ≥ 5 bps slippage reduction.

---

## 7. Honest Caveats

1. **All numerical claims are search-snippet-level.** None of the 15 papers were fetched and read end-to-end. Each priority paper must be read before any EXP-3xxx is launched.
2. **Sharpe comparisons across papers are imperfect** — different cost models, leverage, windows.
3. **MASTERPLAN Bug 6** ("parameter sweeps require OOS validation") and **Bug 8** ("smeared inputs are synthetic inputs") are the dominant failure modes for any ML-flavoured experiment derived from these papers. Pre-register OOS windows.
4. **MASTERPLAN Rule 9** — every overlay must be re-tested at the 8-stream portfolio level. Stream-level Sharpe lift ≠ portfolio Sharpe lift.
5. **Author and venue verification** required for several entries (especially #4 Patel — search returned slightly inconsistent year/version metadata) before any external citation.
6. **Path note repeated:** the user's `~/pilotai-credit-spreads/...` path does not exist on this filesystem; this report was saved to the actual workspace location.

---

## Appendix — All Sources

### VRP / volatility surface
- [Dew-Becker & Giglio, "The Decline of the VRP" (SSRN 5525882, 2025)](https://papers.ssrn.com/sol3/Delivery.cfm/5525882.pdf?abstractid=5525882&mirid=1)
- [Fouhy, "Hierarchical ML for VRP Estimation" (SSRN 6570380, 2026)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6570380)
- [Joint Calibration of Vol Surface (arXiv 2509.08096)](https://arxiv.org/html/2509.08096)
- [Papagelis, "VRP Over Trading and Nontrading Periods" (J. Futures Markets 2025)](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22589)
- [Nguyen, "Regime-Adaptive Volatility Surface Arbitrage" (SSRN 6521981, 2025)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6521981)
- [VolGAN — Arbitrage-Free IV Surfaces (T&F 2025)](https://www.tandfonline.com/doi/full/10.1080/1350486X.2025.2471317)
- [Gatheral & Jacquier, Arbitrage-Free SVI (SSRN 2033323)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2033323)
- [Khalil, "0DTE and VRP" (CBS WP)](https://research-api.cbs.dk/ws/portalfiles/portal/105671291/1775874_O._Khalil_Zero_Day_to_Expiry_Options_Trading_and_Variance_Risk_Premium.pdf)

### Credit spreads / sizing
- [Patel et al., "Sizing the Risk: Kelly, VIX, and Hybrid" (arXiv 2508.16598, 2025)](https://arxiv.org/pdf/2508.16598)
- [Stochastic Optimal Control of Iron Condor Portfolios (arXiv 2501.12397, Jan 2025)](https://arxiv.org/html/2501.12397v1)
- [de Saint-Cyr, "Iron Condors on SPX" (SSRN 4643378)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4643378)
- [Optimal Kelly Portfolio under Risk Constraints (SciRP 2025)](https://www.scirp.org/pdf/eng2025173_38104721.pdf)
- [On TC in Minimum-Risk Portfolios (Frontiers 2025)](https://www.frontiersin.org/journals/applied-mathematics-and-statistics/articles/10.3389/fams.2025.1585187/full)

### Microstructure / execution / 0DTE
- [Dim, Eraker & Vilkov, "0DTEs" (SSRN 4692190)](https://papers.ssrn.com/sol3/Delivery.cfm/4692190.pdf?abstractid=4692190)
- [Adams et al., "Do S&P500 Options Increase Market Volatility?" (SSRN 5641974, 2025)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5641974)
- [Doshi et al., "Risky Intraday Order Flow & Option Liquidity" (Bauer May 2025)](https://www.bauer.uh.edu/hdoshi/docs/DPS_May_2025.pdf)
- [Binary Tree Pricing under Microstructure (arXiv 2507.16701)](https://arxiv.org/html/2507.16701v1)
- [Garmash, "Zero DTE Options Gamma Hedging" (SSRN 5329719)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5329719)
- [Databento OPRA microstructure guide](https://databento.com/microstructure/opra)
- [CBOE 2024 Options Market Structure WP](https://cdn.cboe.com/resources/government_relations/FINAL-Options-Market-Structure-Document-v14-2024.pdf)

---

*Read-only literature review. Snippet-level provenance only. No code or models changed.*
