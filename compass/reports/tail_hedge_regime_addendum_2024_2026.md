# Addendum: Tail Hedging & Regime Detection — 2024-2026

**Date:** 2026-04-29
**Relationship to canonical:** Extends `compass/reports/literature_review_2024_2026.md`. Covers two pillars under-treated there: (a) cheaper tail-hedge alternatives to VIX calls, (b) regime-detection methods for options strategies. VRP and liquidity pillars are NOT repeated — see canonical.

**Provenance:** All numerical claims below are search-snippet-level. Rule-13 haircut applies (live ≈ 0.5-0.7× backtest).

---

## 1. Cheaper tail hedging vs VIX calls — what 2024-26 literature says

The post-2020 critique of VIX calls is well known: long-run cost-of-carry of ~5-9%/yr, payoff timing-fragile, tracking error to SPX drawdowns ~0.4-0.6 even in crisis. The 2024-26 literature canvasses 5 alternatives, ranked by reported cost-efficiency.

| Alt-hedge | Reported annual carry cost | Payoff convexity vs SPX -10% | IronVault testability |
|-----------|----------------------------|------------------------------|------------------------|
| **Cross-asset put on credit indices (HYG/CDX)** | 1-3% | strong in credit-led crises | partial (HYG only, no CDX) |
| **VIX call ladders (rolling, OTM)** | 3-5% (vs 5-9% naïve) | comparable to ATM VIX calls | yes |
| **Put-spread collars on SPX/SPY** | near zero | bounded but real | yes |
| **Dispersion / index-vs-component vol swap** | 0-2% (long index gamma, short single-name) | medium | partial |
| **Synthetic VIX from SPX 30D variance swap proxy** | depends on roll yield | medium-strong | no (no var-swap quotes) |

Two papers stand out:

- **"Tail Hedging is Cheap When You Buy It Inside Out" — practitioner note, 2024.** Argues the cheapest tail hedge is a long put-spread (e.g. SPY 5-delta / 2-delta) financed by a closer-to-money short call. Net carry: near zero. Convexity loss: ~30% vs naked puts. Snippet-level claim: drawdown reduction of ~40% on 2008-2020 SPX with no positive-carry sacrifice.
- **arXiv 2024 q-fin.RM — "Credit-Equity Co-movement in Tails: Pricing the Hedge."** Documents that HYG put hedges have run-cost ~⅓ of VIX-call hedges per unit of SPX-drawdown protection, conditional on credit-led regimes (2008, 2020). Misses pure-equity flash crashes (2018Q4, 2022).

**Implication for v5_hedge stream.** Today's v5_hedge sleeve is a cost-of-carry drag in calm regimes. A regime-conditional hedge (HYG puts when credit spreads widening, SPY put-spread collar otherwise, VIX calls only when VIX term-structure flips backwardated) is the consensus 2024-25 design.

---

## 2. Regime detection — 2024-26 methods for options sizing

Five families surfaced:

1. **HMM on realised-vol features.** Classic, refined in 2024 with exogenous covariates (yield-curve slope, credit spread, USD index). Reported regime persistence: 40-60 trading days.
2. **Markov-switching GARCH.** Joint vol-and-regime estimation. Better in-sample fit than HMM-on-RV; OOS regime classification accuracy similar (~70-75%).
3. **Change-point detection (BOCPD, PELT).** Useful for catching abrupt transitions (2020-03, 2022-02) but noisy in regime-2 calm.
4. **Vol-of-vol / IV-skew curvature regime classifier.** Andersen-Bondarenko 2024 line. Uses options-implied features only — no underlying-return data needed. Reported IV-curvature shift leads SPX vol changes by 3-7 days.
5. **LLM/news-based regime tagging.** OPT/FinBERT classifies headlines into regime categories. Hit-rate ~60-65% on held-out 2023-24 sample. Requires alt-data feed.

Strongest 2024-26 finding (snippet-level): **a 2-state HMM on (RV20, IV30/IV90, HY-credit-spread) is parsimonious and beats single-feature HMMs OOS by 0.10-0.15 Sharpe when used to gate short-vol exposure.**

**v8a relevance.** EXP-900-max (recent commit `15f7e29`) already implements an HMM regime detector. Worth re-checking whether its feature set includes the credit-spread covariate; if not, that's the cheapest enhancement.

---

## 3. Four testable hypotheses

Numbered to extend the EXP-3xxx series in the canonical review.

### EXP-3080 — HYG-put tail hedge replaces fraction of VIX-call sleeve

- **Falsifiable:** v5_hedge restructured as 50% HYG 30D 5-delta puts + 50% existing VIX calls underperforms 100% VIX calls in pure-equity drawdowns but outperforms over full 2019-2024 OOS by ≥ +0.05 portfolio Sharpe with no max-DD increase.
- **Effort:** 2 days. IronVault has HYG chains.
- **Watchpoints:** HYG OI/spread is wide; tx-cost honesty decisive. Rule-9 portfolio-level re-test mandatory.

### EXP-3090 — Put-spread collar on SPY as zero-carry tail hedge

- **Falsifiable:** rolling 30D SPY 5d/2d put-spread financed by 25d call short, sized to 0.5% portfolio vega, reduces 2020-Mar and 2022-Feb drawdowns by ≥ 30% with annual carry cost < 50bps.
- **Effort:** 2 days.
- **Watchpoints:** short-call leg caps upside in melt-up regimes; pre-register sizing rule before OOS.

### EXP-3100 — HMM regime detector with credit-spread covariate

- **Falsifiable:** 2-state HMM trained on (RV20, IV30/IV90, HY-OAS) classifies regimes with persistence ≥ 30 trading days OOS and gates exp1220 with ≥ +0.10 portfolio Sharpe vs no-gate baseline.
- **Effort:** 3 days (extend EXP-900-max if HY-OAS not already a feature).
- **Watchpoints:** regime-label OOS validity is the standard pitfall — pre-register state-mapping by feature centroids, not by post-hoc P&L.

### EXP-3110 — IV-skew-curvature change-point as crisis early-warn

- **Falsifiable:** BOCPD on SPX 25d-call-IV minus 25d-put-IV (skew) generates change-point alerts that precede VIX > 30 events by ≥ 3 trading days with hit-rate ≥ 50% and false-alarm rate ≤ 1/quarter.
- **Effort:** 2 days.
- **Watchpoints:** false-alarm rate is the metric that kills change-point methods in production. Must report both.

---

## 4. Sequencing recommendation

After the canonical-review top-3 actions (EXP-3010, EXP-3060, EXP-3030), insert this addendum's hypotheses as follows:

- **Week 3:** EXP-3080 (HYG put hedge) and EXP-3100 (HMM with HY-OAS) — both extend live infrastructure with modest code.
- **Week 4:** EXP-3090 (put-spread collar) and EXP-3110 (BOCPD change-point) — independent of v8a streams; safer to run in parallel.

---

## 5. Caveats

- Every paper claim above is snippet-level. Verify before sizing.
- HYG and CDX are NOT options-data feeds we have full historical depth for — confirm IronVault HYG chain coverage 2019-2024 before EXP-3080.
- Regime-detection EXP-3100/3110 are bug-6-fragile: thresholds and state-mappings must be pre-registered before any OOS run.
- v5_hedge restructuring (EXP-3080/3090) is a portfolio-level change; Rule-9 re-test inside v8a is non-negotiable.

---

## 6. Files this addendum extends

Canonical: `compass/reports/literature_review_2024_2026.md`
Companion: `compass/reports/ironvault_testable_hypotheses_2024_2026.md`

The 3 superseded drafts listed in the canonical's §7 still apply.
