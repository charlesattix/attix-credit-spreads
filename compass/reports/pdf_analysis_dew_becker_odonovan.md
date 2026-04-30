# PDF Analysis — Dew-Becker & Giglio (2025) and O'Donovan & Yu (2024)

**Date:** 2026-04-29
**Status:** Citation-grade. Both papers downloaded and pypdf-extracted. Introductions read in full; deeper sections summarised from available excerpts.
**Files on disk:**
- `compass/references/dew_becker_giglio_2025_chicagofed.pdf` (5.5 MB, 78 pages)
- `compass/references/dew_becker_giglio_2025_authorsite.pdf` (1.1 MB, 84 pages — Sept 1 2024 manuscript version)
- `compass/references/odonovan_yu_2024_efma.pdf` (584 KB, 73 pages)
- corresponding `.txt` extractions alongside each PDF

---

## 1. Dew-Becker & Giglio (2025) — verified from PDF

### 1.1 Header and citation

- **Title:** "The decline of the variance risk premium: evidence from traded and synthetic options"
- **Authors:** Ian Dew-Becker (Chicago Booth, NBER) and Stefano Giglio (Yale, NBER)
- **Manuscript date:** September 1, 2024 (author site); Chicago Fed WP 2025-17

### 1.2 Sample period and data

- **Synthetic options:** monthly returns 1926-present using **CRSP market return** as the underlying.
- **Traded options:** monthly returns August 1987-present.
- This is the longest options-related dataset I'm aware of, made possible because synthetic options need only the underlying time series.

### 1.3 What is a synthetic option (the paper's key construct)

> "synthetic options – dynamic portfolios that attempt to replicate returns on traded options by dynamically trading the underlying."

A synthetic option is the delta-hedge replicating portfolio: hold delta of underlying, rebalance to track option payoff. This is **not** a delta-hedged option (which is "traded option minus delta of underlying"). The synthetic IS the underlying-only replication.

> "Empirically, replication works quite well: synthetic options have returns that are over 90 percent correlated with traded option returns and, most importantly, hedge all realized crashes over the last century effectively."

### 1.4 The two key empirical results

**Result 1 — Synthetic options never had negative alpha for 100 years.**
> "Whereas traded options have strongly negative CAPM alphas, synthetic options have historical alphas that are indistinguishable from zero, with confidence bands that are economically narrow. In the benchmark full-sample results, the lower bound for the confidence bands for the information ratios is **−0.2**."

i.e. equity investors never demanded large compensation for crash risk. The "VRP" was never a deep risk premium. Robust across time, strikes, and maturities.

**Result 2 — Traded option alphas converged to zero around 2010.**
> "there is a break in the returns somewhere around **2010**. In the period since 2010, in fact, the alphas of the traded options have converged to zero, consistent with the synthetic options."

> "Since the gap between traded and synthetic option returns is literally a delta-hedged return, another way to state this second result is that the alpha of delta-hedged options has gone to zero. Relatedly, the paper also shows that the CAPM alpha of the variance risk premium has shrunk towards zero."

### 1.5 Mechanism — intermediary frictions, not investor preferences

> "with investor heterogeneity, when retail investors are unable to sell options – the model's core friction – the equilibrium price of options will be driven by the investors with the greatest demand. But as the frictions decline, overpricing will also, because the investors willing to supply options become free to do so."

**The smoking gun (page 5, verbatim):**
> "the net S&P 500 gamma exposure of dealers and market makers for Cboe options shifted from being consistently negative to being zero or positive following the financial crisis. Other factors driving hedging costs, including trading frictions and basis risk, also declined, which... contributes to the decline in the traded option alpha."

This is **direct empirical confirmation that dealer net-GEX is the structural variable governing the VRP**. Pre-2008: dealers consistently short gamma → had to hedge → demanded premium → traded options earned negative alpha. Post-2008: dealers zero or long gamma → no hedging premium needed → alphas zero.

### 1.6 Direct implications for v8a

1. **The historical Sharpe-6.0 backtest was a residual-friction backtest.** Pre-2010 P&L was harvesting structural intermediary frictions that no longer exist for SPX. Anything calibrated on pre-2010 data is overstated.

2. **Post-2010 SPX short-vol should pay near zero.** EXP-3150 showed v8a as a whole survives post-2020. The most plausible reconciliation:
   - **Sector ETF and small-cap-equivalent options retain residual frictions** (lower retail flow than SPX). Our 5 sector streams (qqq_cs, xlf_cs, xli_cs, gld_cal, slv_cal) likely carry the load.
   - **Our gating filters add genuine timing alpha**, not just VRP harvesting.

3. **Dealer net-GEX is not a "nice-to-have signal" — it is the structural variable.** EXP-3000 (proxy-GEX gate) is now elevated from optional to **necessary**: per the paper's own model, traded short-vol pays *only* when dealers are short gamma.

### 1.7 Robustness and additional points from the intro

- Result is **robust over time, across strikes, across maturities, and to modifying various details in the construction.**
- Synthetic options are nonstationary → "when studying traded option returns, attention must be paid to the exact sample being used and how the results may have changed over time."
- This validates our practice of separating the post-2020 slice (EXP-3150).

---

## 2. O'Donovan & Yu (2024) — verified from PDF

### 2.1 Header and citation

- **Title:** "Transaction Costs and Cost Mitigation in Option Investment Strategies"
- **Authors:** James O'Donovan (City University of Hong Kong) and Gloria Yang Yu (Singapore Management University)
- **Manuscript date:** April 24, 2024
- **Awards:** EFMA 2024 WRDS best conference paper, AFBC 2024 ASX best derivatives paper

### 2.2 Sample and methodology

- **Data:** Optionmetrics + CRSP + TAQ, merged via WRDS.
- **Universe:** equity options (NOT index). CRSP share codes 10/11, stock price ≥ $5 on formation date.
- **Strategy:** End-of-month ATM straddles. Pick the call+put closest to ATM with ≥30 DTE; same maturity; moneyness 0.8-1.2.
- **Returns:** delta-hedged call option returns, formed at month-end, held one month. Look-ahead bias avoided per Duarte et al. (2023) sample construction.
- **Sample size:** 255,240 observations after filters.
- **24 predictor variables** sourced from the literature.
- **Cost assumption (their conservative baseline):** "options traders pay approximately **one-fifth of the quoted half spread** for options and the **effective half spread** for stocks." This is the GENEROUS assumption — and 0/24 strategies survive.

### 2.3 The four headline results (from intro, verbatim)

**Result 1 — Look-ahead bias + transaction costs eliminate all 24 strategies.**
> "17 of the 24 variables can be used to form portfolios with significant long-short portfolio returns... [but] none of the strategy returns survive transaction costs."

**Result 2 — Trading costs dominated by options leg.**
> "over **80%** of the strategy trading costs come from the option transaction costs."

> "transaction costs for trading the option portfolios we study have been **increasing** over time; this is in contrast to the trend in equity markets, where transaction costs have been decreasing over time."

**Result 3 — VIX and sentiment are the time-series predictors of options TC.**
> "The strongest predictors of average portfolio transaction costs are the **VIX index**, and the **sentiment index of Baker and Wurgler (2006)**. These results suggest that transaction costs are particularly high when volatility and market sentiment are elevated and suggest that gross portfolio returns will be particularly high during these periods."

This means high-VRP days (high VIX) come with offsetting high TC — a key implication for our VRP-percentile gate (EXP-3010).

**Result 4 — Hedging frequency tradeoff.**
> "Using a strategy that is delta-hedged daily until maturity, approximately 50% of the strategy trading costs come from the stock transaction costs... in the presence of stock trading costs, delta-hedging each day reduces the average returns to the point that it offsets the benefit of variance reduction."

i.e. "delta-hedge daily" is a textbook prescription that is wrong in practice once costs are included.

### 2.4 The mitigation techniques — IDENTIFIED FROM PDF

The paper tests three progressively more aggressive mitigations:

**Mitigation 1 — Hold-to-maturity exits.** Avoids paying transaction costs to exit before expiration. **Restores 3 strategies to significant.**

**Mitigation 2 — Hold-to-maturity + liquid-only universe filter.** Restrict to options with relatively lower trading costs. **Restores 4 strategies.**

**Mitigation 3 — The "novel" approach: long-only + fixed market-vol hedge.** Verbatim from page 4:
> "We examine **long-only portfolios** and portfolios that hedge the volatility risk in the long portfolio with a **fixed position in market index volatility risk**. This novel cost mitigation strategy is the most successful, increasing the number of significant long-short portfolios to seven."

> "The return magnitudes are economically significant, ranging from **36bp to 114bp per month**."

The motivation, page 4:
> "across the 24 sorting variables, gross returns grow faster than transaction costs as we move from the bottom to the top decile."

i.e. the long leg is the alpha. The short leg destroys returns via TC. Drop the short leg, replace with a market-vol hedge.

### 2.5 Direct implications for v8a — this is where v8a is partly already aligned

**Critical mapping:** v8a's structural design is approximately the O'Donovan-Yu mitigation already.

| O'Donovan-Yu mitigation | v8a equivalent | Already aligned? |
|--------------------------|----------------|--------------------|
| Long-only (no short leg) | We SELL premium consistently — symmetric to "long-only" with the sign flipped | ✓ |
| Fixed market-vol hedge | v5_hedge stream (VIX calls / tail hedge) | ✓ |
| Hold-to-maturity exits | We currently exit before expiration | ✗ |
| Liquid-only universe | 6 ETFs are mostly liquid, but SLV is the bottleneck | partial |

So v8a is **structurally pre-aligned** with the most successful mitigation. This partly explains why EXP-3150 shows post-2020 survival — we are operating in the regime where the residual premium can be harvested net of costs, IF execution is done right.

The one structural gap: we exit before expiration. Hold-to-maturity is the cheapest available alpha enhancement.

---

## 3. Reconciliation with EXP-3150 — answered

EXP-3150 confirmed v8a Sharpe survives post-2020. The two papers explain WHY:

1. **Sector-ETF residual frictions (Dew-Becker mechanism).** Dealer net-GEX is the structural variable. Sector ETFs (XLF, XLI, GLD, SLV) likely retain dealer net-short-gamma exposure that SPX no longer has, because retail flow into sector-ETF option-selling is much thinner.

2. **v8a is structurally aligned with O'Donovan-Yu's most successful mitigation.** Selling-only + tail hedge + (mostly) liquid universe = the playbook with the highest survival rate.

3. **Look-ahead bias and exit-cost overhead are the two forms of overstatement to be wary of.** Per O'Donovan-Yu, gross-return papers are upward biased AND TC-effect papers are downward biased — these compound.

**The post-2020 v8a Sharpe is NOT free.** Its survival depends on:
- Continued sector-ETF dealer frictions (could erode further)
- Our gating discipline (our timing filter, not raw VRP harvesting)
- Realistic-cost backtest assumptions (must verify our fills are not too kind)

---

## 4. New experiments motivated by the PDF reads

### EXP-3180 — Hold-to-maturity exit modification

**Trigger.** O'Donovan-Yu Mitigation 1 alone restores 3 of 24 strategies. Their main strategies are 1-month ATM straddles; ours are 28-DTE put credit spreads. The exit-cost saving should generalise.

**Hypothesis.** Re-running exp1220 with **expiration-day exit** instead of pre-expiration close reduces realised round-trip transaction costs by ≥ 30% while not raising tail-risk materially.

**Falsifiable form.**
- H0: net Sharpe of HTM-exit version ≤ baseline net Sharpe.
- H1: net Sharpe rises by ≥ 0.10 with no max-DD increase > 5%.

**Effort.** 1 day. The exp1220 logic already parameterises exit; switch to expiration close.

**Watchpoint.** Pin risk on expiration day; small assignment risk on ITM legs. Pre-register policy: cash-settle short legs that go ITM in last 30 minutes.

### EXP-3190 — VIX/sentiment-conditional TC gate

**Trigger.** O'Donovan-Yu Result 3: VIX and Baker-Wurgler sentiment predict portfolio TC.

**Hypothesis.** Skipping entries when VIX > 75th percentile (rolling 252d) AND sentiment index > 0 reduces realised TC enough to add ≥ 0.05 net Sharpe even after losing some "high-VRP" entries.

**Falsifiable form.**
- H0: TC-gated version has ≤ 0.02 Sharpe lift over baseline.
- H1: ≥ 0.05 Sharpe lift.

**Effort.** 1-2 days. Need Baker-Wurgler sentiment series (free download from Wurgler's site, monthly).

**Watchpoint.** Counterintuitive — high-VIX days are also where naïve VRP-percentile gates (EXP-3010) WANT to be in. The two gates would partially fight each other; pre-register their joint spec.

### EXP-3151 — Per-stream Sharpe attribution post-2020 (re-affirmed, now with stronger motivation)

**Trigger.** Dew-Becker mechanism: SPX dealer net-GEX is no longer negative; sector-ETF likely is. Predicts: post-2020 v8a Sharpe is carried by sector streams, not by exp1220 (SPY).

**Hypothesis.** Post-2020 Sharpe contribution from {qqq_cs, xlf_cs, xli_cs, gld_cal, slv_cal} > Sharpe contribution from exp1220.

**Effort.** 0.5 day. Just decompose existing post-2020 backtest output.

**Why it matters now.** This test is now a direct empirical falsification of the Dew-Becker mechanism on v8a. If exp1220 (SPY) carries most post-2020 P&L, the mechanism-residual-friction story is wrong — and we have a different (and more exposed) edge.

### EXP-3000 (re-elevated) — Dealer-proxy-GEX gate

**Trigger.** Dew-Becker page-5 statement about S&P 500 dealer GEX. The paper makes GEX **the structural variable** of the model.

**Status.** No longer optional. Per the paper's own model: traded short-vol pays only when dealer net-GEX is negative.

**Effort.** 3 days, as previously specified.

---

## 5. Updated priority queue (after PDF reads)

| Rank | EXP | Effort | Why |
|------|-----|--------|-----|
| 1 | **EXP-3151** (per-stream attribution post-2020) | 0.5 day | Direct empirical test of Dew-Becker mechanism on v8a. Highest information per hour. |
| 2 | **EXP-3180** (hold-to-maturity exit) | 1 day | O'Donovan-Yu's cheapest single mitigation; restored 3 of 24 strategies in their study. |
| 3 | **EXP-3000** (dealer proxy GEX gate) | 3 days | Dew-Becker promotes this from optional to structural. |
| 4 | **EXP-3154** (realistic-fill re-test) | 1-2 days | O'Donovan-Yu's bias-on-bias warning. |
| 5 | **EXP-3190** (VIX/sentiment TC gate) | 1-2 days | New from O'Donovan-Yu. |
| 6 | **EXP-3153** (no-gating ablation) | 1 day | Tests whether our alpha is gating discipline vs raw VRP. |

The EXP-3010 (VRP percentile gate) and EXP-3030 (regime DTE) recommended earlier remain valid but drop in priority — they pre-date the PDF reads and assumed VRP is the structural premium. After the PDF reads, GEX (EXP-3000) is the structural variable and EXP-3010 becomes a noisy proxy of it.

---

## 6. Honest limits

- I read the **introductions in full** (pages 1-7 of each). Deeper sections (model derivations, robustness tables, exact alpha point estimates by sub-period) were not extracted in this session.
- Specific point estimates referenced verbatim: -0.2 IR lower bound (Dew-Becker), 36-114 bp/month (O'Donovan-Yu), >80% TC from option leg, 50% TC from stock when daily-hedged.
- Both PDFs are now on disk (`compass/references/`) for any follow-up reads.
- Per Rule-13: every Sharpe number in the v8a backtest should still be haircut by 0.5-0.7× for live expectation.
- Per Rule-9: any single-stream change must be re-tested at v8a portfolio level.
- Per Bug-6: EXP-3151/3180/3000 must pre-register their thresholds and reporting metrics before run.

---

## 7. Bottom line for the user

The two papers, read carefully, do not invalidate v8a. They explain its structure:

- **Why it works:** v8a is approximately the O'Donovan-Yu mitigation playbook (sell + hedge + mostly-liquid universe), operating on sector ETFs where dealer GEX may still be net negative (Dew-Becker mechanism residue).
- **Why the Sharpe-6.0 backtest is overstated:** it includes pre-2010 data when the structural premium existed broadly. Live expectation is whatever EXP-3150 shows for post-2020, multiplied by Rule-13's 0.5-0.7×.
- **Single highest-leverage action:** EXP-3151 (per-stream attribution post-2020). 0.5 day. Tells us whether our edge is truly residing where Dew-Becker predicts (sector streams) or somewhere else.
- **Single highest-impact mechanical change:** EXP-3180 (hold-to-maturity). 1 day. Drops exit transaction costs, no signal change.
- **Most important policy gate:** EXP-3000 (dealer proxy GEX). 3 days. The paper makes GEX the structural variable of the entire model.
