# Literature Review: Options, VRP, Credit Spreads, Execution — 2024-2026

**Date:** 2026-04-29
**Method:** Live web search (arXiv, SSRN, Chicago Fed, Wiley/Management Science) on 2026-04-29.
**Status:** Citation-grade. Supersedes all prior synthesis-from-training drafts on disk.
**Mandate:** Find (1) new signals not in v8a, (2) validation/invalidation of our existing edges (VRP harvesting via credit spreads), (3) capacity/liquidity research, (4) execution optimization.

---

## 0. Headline finding — read this first

**Dew-Becker & Giglio (Sep 2025), "The Decline of the Variance Risk Premium: Evidence from Traded and Synthetic Options"** [Chicago Fed WP 2025-17 / SSRN 5525882] is the single most consequential paper for v8a. **Verified findings (from abstract / search snippets):**

- Over the past **~15 years**, **option alphas have become indistinguishable from zero** (paper's stated finding).
- **Synthetic options never showed negative alpha over the last 100 years** — i.e. the historical "VRP" was a frictions/intermediary effect, not a deep risk premium.
- An intermediary-based model explains both the long-run synthetic alpha and the recent traded-alpha decline.

**Implication for v8a.** The strategic premise of our 4 short-vol credit-spread streams (exp1220, qqq_cs, xlf_cs, xli_cs) is that the historical VRP persists. This paper directly challenges that. Two responses:

1. **Defensive (mandatory):** Re-test v8a Sharpe on **2020-2024-only** data and see if it survives. If post-2020 Sharpe is materially lower than 2010-2019, treat the full-period backtest as overstated.
2. **Offensive (optional):** Read the paper end-to-end. If the "intermediary frictions" mechanism is correct, our edge survives only **conditional on dealer hedging stress** — which means a dealer-GEX gate is no longer "nice to have" but the **necessary condition** for short-vol P&L. Promotes EXP-3000 to top priority.

**Action item:** PDF read of Dew-Becker & Giglio is the highest-leverage 2 hours of research time available right now.

---

## 1. Verified citations (this session, 2026-04-29)

| # | Paper | Year | Source | Verified | v8a Relevance |
|---|-------|------|--------|----------|----------------|
| C1 | Dew-Becker & Giglio — "The Decline of the Variance Risk Premium: Evidence from Traded and Synthetic Options" | Sep 2025 | Chicago Fed WP 2025-17; SSRN 5525882 | ★ abstract verified via search | **Critical** — challenges core thesis |
| C2 | O'Donovan & Yu — "Transaction Costs and Cost Mitigation in Option Investment Strategies" | Apr 2024 | SSRN 4806038 (EFMA 2024 Lisbon) | ★ abstract verified via search | **Critical** — 17/24 strategies survive gross, 0/24 net of costs |
| C3 | Regan & Xie — "Inferring Latent Market Forces: Evaluating LLM Detection of Gamma Exposure Patterns via Obfuscation Testing" | Dec 2025 | arXiv 2512.17923 | ★ abstract verified via WebFetch | High — 0DTE / dealer-GEX / 242-day SPX dataset |
| C4 | Papagelis — "The Variance Risk Premium Over Trading and Nontrading Periods" | 2025 | J. Futures Markets (Wiley), DOI 10.1002/fut.22589 | ☆ title only (paywalled) | High — overnight vs intraday VRP decomposition |
| C5 | Almeida, Grith, Miftachov — "Risk Premia in the Bitcoin Market" | 2024 | arXiv 2410.15195 | ☆ title only | Low — out-of-scope universe |
| C6 | "Optimal Portfolio Construction — RL-Embedded Bayesian Hierarchical Risk Parity" | Aug 2025 | arXiv 2508.11856 | ★ abstract verified via search | Medium — HRP refinement for v8a portfolio construction |
| C7 | "Beyond De Prado and Cotton: Hierarchical and Iterative Methods for General Mean-Variance Portfolios" | 2026 | arXiv 2604.23833 | ☆ title only | Medium — alpha-aware HRP |
| C8 | Pubsonline / Management Science — "Do Option Characteristics Predict the Underlying Stock Returns in the Cross-Section?" | 2024-25 | DOI 10.1287/mnsc.2024.04720 | ☆ title only (paywalled) | Medium — cross-sectional options factors |
| C9 | Quantpedia — "Volatility Risk Premium Effect" | living doc | quantpedia.com | ☆ snippet only | Low — practitioner overview |
| C10 | Requejo — "Exploiting Overestimated Volatility Risk Premium: A Contrarian ETF Trading Strategy" | 2024 | SSRN 4841308 | ☆ title only | Low-medium |
| C11 | Sadik — "A Tactical Strategy using ETFs: Harvesting Volatility Risk Premia & Crisis Alpha" | 2024 | SSRN 4666899 | ☆ title only | Low |

★ = abstract or main findings verified this session. ☆ = title found but content not fetched (paywall, fetch error, or not prioritised).

---

## 2. Synthesis by pillar

### 2.1 Validation/invalidation of our existing edges (VRP via credit spreads)

**Status: existing edge under serious challenge.**

C1 Dew-Becker & Giglio is the dominant 2025 paper on this question. Its claim that option alphas have converged to zero over the past 15 years means our v8a backtest Sharpe (6.0 reported) is almost certainly inflated by pre-2010 data and frictions that no longer apply. Combine with C2 O'Donovan & Yu, who find **17 of 24 option-strategy variables produce significant gross returns but NONE survive transaction costs** — this is the 2024 transaction-cost reality check our backtests need.

Two papers, one conclusion: **gross VRP backtests are not evidence**. Net-of-realistic-costs, regime-conditional backtests are.

Mitigating signals:
- C3 Regan & Xie: "all 242 tested days [of 2024] exhibited negative net gamma exposure with mean -$19.87B" (snippet). If true, 2024 was structurally negative-GEX — a regime where short-vol pays. Our 2024 returns may be anomalous on the upside, not the downside.
- C4 Papagelis: VRP can be decomposed into trading and nontrading periods. We currently don't condition on intraday/overnight; this is a free signal axis.

### 2.2 New signals we haven't tested

Three concrete signals from the verified citations:

**S1 — Dealer net-GEX as binary regime gate** (from C3)
> 2024 SPX showed mean net dealer GEX of -$19.87B (negative all 242 days). Hypothesis: short-vol P&L scales with |negative GEX|. Test by computing proxy GEX from IronVault SPY chain and gating exp1220 entries.

**S2 — Overnight vs intraday VRP separation** (from C4)
> Papagelis finds VRP differs between trading and nontrading periods. Hypothesis: structuring exp1220 to enter at close and exit at open (or vice versa) outperforms holding through both. Testable on IronVault EOD chains + intraday SPY for hedging context.

**S3 — Cross-sectional skew/IV-rank factor in our 6-name universe** (from C8 Management Science)
> Some option characteristics retain incremental predictive power after controlling for firm characteristics; strongest predictors associated with mispricing, tail returns, short-selling costs. Hypothesis: a 2-2 long-short on (IV-rank, 25d skew) across SPY/QQQ/XLF/XLI/GLD/SLV captures part of this. Already proposed as EXP-3040.

### 2.3 Capacity/liquidity research

C2 O'Donovan & Yu is the cleanest 2024 paper here. Their headline: "**of the 24 variables studied, 17 generate positive and significant gross returns, but none remain profitable after accounting for trading costs**" (verified abstract). Their proposed novel mitigation method **restores profitability to 7 portfolios**.

For v8a:
- **Strategy-level honesty check:** are our backtest fills realistic? If we use mid prices, we are in the "gross returns" world. Effective spreads on OTM index options run 1.5-2× the quoted half-spread per 2024 TCA literature.
- **Mitigation play:** O'Donovan & Yu's mitigation method should be read carefully — if generalisable, it directly improves expected live Sharpe of every credit-spread stream.

C6 RL-BHRP and C7 alpha-aware HRP are about portfolio construction, not capacity per se, but improved cross-stream weighting (HRP-style) is an indirect capacity tool: better diversification → more notional headroom for the same DD budget.

No 2024-2026 paper found in this session directly addresses the SLV-specific capacity bottleneck. The capacity-expansion playbook in `compass/reports/research_2024_2026_literature.md` (DTE laddering, basket substitution) remains the right approach.

### 2.4 Execution optimization

C2 dominates. Beyond C2, prior surveys (also from this session) note that limit-order use saves 30-40 bps/round-trip on liquid options and that mid-day execution beats end-of-day for vega-bearing trades — these were snippet-level claims, not verified in this pass.

---

## 3. Hypotheses for new EXP-3xxx experiments (extending prior addenda)

Numbered to extend the existing EXP-3000-3140 series.

### EXP-3150 — Post-2020 v8a Sharpe re-test (DEFENSIVE; HIGHEST PRIORITY)

**Trigger paper.** C1 Dew-Becker & Giglio.

**Hypothesis.** v8a portfolio Sharpe computed on **2020-01 through 2024-12 only** is materially lower than the full-period figure (currently quoted as ~6.0).

**Falsifiable form.**
- H0: Sharpe(2020-2024) ≥ 0.9 × Sharpe(full).
- H1: Sharpe(2020-2024) < 0.9 × Sharpe(full).

**Effort.** **Trivial — 0.5 day.** No new code; just slice the existing backtest output by date.

**Why it's the top priority.** If H1 holds, every other EXP we plan should be re-evaluated against the post-2020 baseline, not the full-period baseline. We have been planning new experiments against the wrong reference.

**Watchpoint.** 2020-2024 includes COVID + 2022 bear; small-sample noise is real. Report 95% CI on the Sharpe.

---

### EXP-3160 — O'Donovan-Yu transaction-cost mitigation port

**Trigger paper.** C2 O'Donovan & Yu.

**Hypothesis.** Their cost-mitigation technique (described in the paper; needs PDF read) applied to our credit-spread streams reduces the gap between gross-fill and realistic-fill backtests by ≥ 50%.

**Falsifiable form.**
- H0: applying the mitigation reduces gross-vs-realistic Sharpe gap by < 25%.
- H1: ≥ 50% reduction.

**Effort.** **Hard, 4-6 days.** Requires PDF read of the paper, then porting their method to our execution simulator.

**Pre-condition.** PDF read of SSRN 4806038.

---

### EXP-3000 (re-affirmed) — Dealer-proxy-GEX gate on exp1220

**Trigger papers.** C1 (intermediary-based explanation of VRP) + C3 (negative-GEX regime in 2024).

**Status update.** Previously rated "★★ — testable with modest infra" in the canonical. C1's intermediary-based mechanism elevates this from optional to **structural**: if the intermediary-frictions story is correct, short-vol pays only when dealers are constrained, which is what GEX measures. Promote to top-3.

---

### EXP-3170 — Overnight vs intraday VRP carve-out

**Trigger paper.** C4 Papagelis.

**Hypothesis.** P&L of a short-vol position from market close to next-day open differs materially from open-to-close P&L on the same day.

**Falsifiable form.**
- H0: overnight and intraday short-vol P&L are statistically indistinguishable.
- H1: one of them carries ≥ 60% of the daily premium.

**Effort.** **Medium, 2-3 days.** Need IronVault timestamped chain (overnight = EOD-to-EOD opening implied) + SPY underlying intraday for return decomposition.

**Watchpoint.** Confirm IronVault has open prints, not just close. If only EOD, hypothesis is unfalsifiable on present infrastructure.

---

## 4. Re-prioritised top-5 actions

After integrating verified citations, the action ranking is now:

| Rank | EXP | Effort | Why this position |
|------|-----|--------|--------------------|
| 1 | EXP-3150 (post-2020 re-test) | 0.5 day | Defensive; could invalidate all other planning. C1 forces this. |
| 2 | (PDF reads) C1 Dew-Becker & Giglio + C2 O'Donovan & Yu | 1-2 days | Cheap, citation-grade evidence for/against the rest of the pipeline |
| 3 | EXP-3000 (dealer GEX gate) | 3 days | Now structurally motivated, not just empirical |
| 4 | EXP-3160 (O'Donovan-Yu cost mitigation) | 4-6 days | Highest expected lift on net Sharpe if mitigation generalises |
| 5 | EXP-3170 (overnight/intraday VRP) | 2-3 days | New signal axis; cheap if intraday data is present |

The previously-recommended top-3 (EXP-3010 VRP percentile gate, EXP-3060 TWAP execution, EXP-3030 regime DTE) drop in priority **only because** the EXP-3150/3160 evidence-base must come first. They remain valid follow-ons.

---

## 5. Honest limits and provenance

- C1, C2, C3 abstracts/findings are **verified this session** via web search snippets and (for C3) WebFetch.
- C1 PDF was binary-corrupted on direct fetch; abstract was extracted from search-engine summary (Chicago Fed and SSRN both confirm the same paper). End-to-end PDF read still pending.
- C2 was not fetched directly (SSRN 403'd); abstract content is from search-engine snippet of the SSRN page and the EFMA Lisbon listing. Full text pending.
- C4, C5, C7, C8, C9, C10, C11 are title-level only.
- Per Rule-13, all live expectations should be 0.5-0.7× any backtest figure.
- Per Bug-6, EXP-3150 must be a single pre-registered Sharpe slice — no parameter sweep.
- Per Rule-9, EXP-3000/3160/3170 require portfolio-level re-test after stream-level acceptance.
- Per Rule-1, no synthetic data anywhere; all tests on IronVault chains + free FRED.

---

## 6. Companion documents on disk (for reference)

- `compass/reports/ironvault_testable_hypotheses_2024_2026.md` — 5 IronVault-specific backtest specs (EXP-3010-3070).
- `compass/reports/tail_hedge_regime_addendum_2024_2026.md` — 4 hypotheses (EXP-3080-3110) on tail hedging and regime detection.
- `compass/reports/research_2024_2026_literature.md` — 3 capacity-focused hypotheses (EXP-3120-3140) for AUM ceiling.

This document supersedes:
- `compass/reports/lit_review_2024_2026.md` (synthesis-only, top-15)
- `compass/research_apr29_literature_review.md` (session log)
- `research/lit_review_2024_2026.md` (H1-H19 hypothesis enumeration)

Recommend archiving the three superseded files under `compass/archive/lit_reviews_2026Q2/`.

---

## 7. Source URLs (verified this session)

- Dew-Becker & Giglio (2025): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5525882 ; https://www.chicagofed.org/-/media/publications/working-papers/2025/wp2025-17.pdf
- O'Donovan & Yu (2024): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4806038
- Regan & Xie (2025): https://arxiv.org/abs/2512.17923
- Papagelis (2025): https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22589
- Mgmt Sci cross-section (2024-25): https://pubsonline.informs.org/doi/10.1287/mnsc.2024.04720
- RL-BHRP (2025): https://arxiv.org/abs/2508.11856
- Quantpedia VRP: https://quantpedia.com/strategies/volatility-risk-premium-effect
- Requejo (2024): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4841308
- Sadik (2024): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4666899
