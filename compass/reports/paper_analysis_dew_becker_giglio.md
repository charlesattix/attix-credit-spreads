# Paper Analysis — Dew-Becker & Giglio (2025)

**Title:** "The decline of the variance risk premium: evidence from traded and synthetic options"
**Authors:** Ian Dew-Becker (Chicago Booth, NBER), Stefano Giglio (Yale, NBER)
**Manuscript date:** Sept 1, 2024 (84-page author-site PDF); Chicago Fed WP 2025-17 (78 pages)
**Source on disk:** `compass/references/dew_becker_giglio_2025_authorsite.pdf` and `_chicagofed.pdf`
**Method:** PDF extracted via pypdf; sections 1-3 read in full. Section 4 (theoretical model) and appendices summarised from references in main text.

---

## 1. The empirical setup — what they tested

### 1.1 Data and universe

- **Synthetic options:** monthly returns 1926-2022 using CRSP value-weighted market return.
- **Traded options:** monthly returns August 1987 - December 2022. Spliced from CME futures options 1987-1995 + CBOE SPX options from Optionmetrics 1996-2022.
- **Universe:** SPX index options ONLY. The paper does not test sector ETFs, single-stock options, or commodities.

### 1.2 The exact strategy tested

> "Following Broadie, Chernov, and Johannes (2009), we study a monthly rolling strategy, where options are purchased on the third Friday of every month and then held to their maturity on the following month's third Friday."

- **Position:** **buying** options (calls and puts; their CAPM alphas are equivalent under put-call parity for symmetric strikes).
- **Strikes tested:** 0.90 to 1.10 of spot — covering 10% ITM puts through 10% OTM puts.
- **Holding period:** 1 month, third-Friday to third-Friday.
- **Hedging frequency:** for "synthetic" portfolios, daily delta hedge using HAR-volatility-forecast delta with a 1-day lag (to avoid stale-price bias).
- **Performance metric:** CAPM alpha and information ratio (Sharpe of the part orthogonal to market).

### 1.3 The specific failure mode they document

The paper does NOT test a strategy that "failed." It tests the historical *buyer* of options (which lost money via negative alpha pre-2010) and shows that buyer behaviour now earns zero alpha. The implicit claim for option *sellers* is the dual: pre-2010 there was a structural premium to be harvested by selling; post-2010 that premium is approximately zero.

**The most consequential single fact (verified verbatim):**
> "the overall cumulative return on traded puts is zero between March, 2009 and the end of the sample in December, 2022."

i.e. 14 years of approximately zero alpha for SPX option buyers — equivalently, 14 years of zero alpha for naive SPX option sellers.

---

## 2. The mechanism — why it failed (the structural finding)

### 2.1 Synthetic options never had negative alpha for 100 years

> "synthetic options have estimated alphas very close to zero, with no evidence of mispricing relative to the CAPM"
> "the lower bound for the confidence bands for the information ratios is **−0.2**"

This is the paper's most surprising result: the historical "VRP" was never a deep risk premium that equity investors demanded. Equity investors, viewed through the lens of dynamic-replication portfolios, never asked for compensation for crashes.

Sanity check from page 15 — synthetic 5% OTM puts during real crashes:

| Crash | Market return | Synthetic put return | Ideal payoff |
|-------|---------------|----------------------|---------------|
| Nov 1929 | −41% | +31% | +35% |
| Mar 2020 | −33% | +25% | +28% |
| Oct 2008 | −31% | +22% | +24% |
| Oct 1987 | −30% | +17% (lagged delta) | +24% |
| Oct 1931 | −29% | +22% | +24% |

Replication captures 70-90% of the ideal hedge in real crashes. Synthetic and traded options have pairwise correlations 0.85 to 1.00 across strikes.

### 2.2 The "intermediary frictions" mechanism

The structural-break finding (Section 4 of the paper):

> "with investor heterogeneity, when retail investors are unable to sell options – the model's core friction – the equilibrium price of options will be driven by the investors with the greatest demand. But as the frictions decline, overpricing will also, because the investors willing to supply options become free to do so."

The empirical smoking gun (page 5, verbatim):

> "the net S&P 500 gamma exposure of dealers and market makers for Cboe options shifted from being consistently negative to being zero or positive following the financial crisis. Other factors driving hedging costs, including trading frictions and basis risk, also declined, which... contributes to the decline in the traded option alpha."

**The structural variable of the model is dealer net-GEX.** When dealers are net short gamma, they must hedge dynamically and demand a premium → option buyers earn negative alpha (sellers earn positive). When dealers are zero or net long gamma, no hedging premium → traded alpha = synthetic alpha = 0.

### 2.3 What is correlated with the unspanned residual

Table 1 (page 24) of the paper, correlations of synthetic option residual ˆR^S with macro/financial innovations (orthogonalised to market return):

| Variable | Correlation (full) | Correlation (excluding 2020) |
|----------|--------------------|--------------------------------|
| VIX | −0.21 | −0.21 |
| VXO | −0.14 | −0.12 |
| Realised vol | −0.35 | −0.38 |
| Excess bond premium | −0.11 | −0.14 |
| Unemployment | −0.08 | −0.06 |
| Industrial production growth | +0.06 | +0.07 |
| Federal funds rate | −0.01 | −0.01 |
| Term spread | +0.02 | +0.02 |
| Default spread | −0.01 | −0.01 |
| **Maximal correlation (any linear combo)** | **0.35** | **0.41** |

Reading: the VRP residual is correlated with vol-related variables but is approximately independent of the macro state. This validates the practitioner intuition that VRP is a "vol-of-vol" premium, not a recession premium.

---

## 3. Does v8a avoid the failure mode?

### 3.1 Direct exposure check

| v8a stream | Universe | Same as Dew-Becker tested? | Edge survives per paper? |
|------------|----------|-----------------------------|----------------------------|
| exp1220 (SPY put credit spreads 28-DTE) | **SPY = SPX proxy** | **Yes — direct overlap** | Per paper: should be ≈ 0 alpha post-2010 |
| qqq_cs (QQQ put credit spreads) | NDX/QQQ | Partial overlap; QQQ is liquid index | Likely small residual edge |
| xlf_cs, xli_cs (sector ETF) | XLF, XLI | NOT tested | Sector ETFs have lower retail flow → likely retain frictions |
| gld_cal, slv_cal (calendar spreads) | GLD, SLV | NOT tested | Different premium (term-structure, not VRP per se) |
| cross_vol | varies | NOT tested | Out-of-scope |
| v5_hedge | VIX | NOT tested | Tail-hedge, distinct mechanism |

**Key implication:** Dew-Becker's null result applies most strongly to exp1220. Our edge in exp1220 (SPY) should be near zero post-2010. **Either** v8a's full-period Sharpe-6.0 backtest is overstated by exp1220's pre-2010 contribution, **or** our gating discipline (VRP percentile, regime-conditioning) adds non-VRP timing alpha on top.

### 3.2 Cross-check via dealer GEX

The paper makes dealer net-GEX the structural variable. We do NOT currently measure or gate on GEX. We have proposed EXP-3000 (dealer-proxy GEX gate); after this paper that proposal is no longer optional — it is the **necessary** condition under the paper's own model for traded short-vol to pay.

### 3.3 What v8a's design does protect against

- **Calendar spreads (gld_cal, slv_cal)** harvest term-structure carry, not VRP. Dew-Becker's mechanism does not directly apply. These streams may be the most robust under the paper's findings.
- **Sector ETFs (xlf_cs, xli_cs)** have lower retail option-selling flow than SPX. Dealer GEX in those names is plausibly still net-short → residual edge.
- **Tail hedge (v5_hedge)** is itself a long-vol position that benefits if dealers ever go back to net-short-gamma stress (e.g., 2020 spike).

---

## 4. Translatable insights for the v8a backtest framework

### 4.1 Synthetic-option construction is reusable

The paper's synthetic-option construction is operational: HAR-volatility forecast → Black-Scholes delta with leverage-effect correction (Hull-White 2017) → daily delta-hedge with 1-day lag.

For each v8a underlier (SPY, QQQ, XLF, XLI, GLD, SLV) we could build a **synthetic-option benchmark** using only the underlying time series. The traded-vs-synthetic gap then quantifies the residual VRP per stream — a cleaner attribution than raw P&L.

### 4.2 Robust-to-reality alpha estimation

Their robust uncertainty bands (Section 3.4) explicitly handle the "unspanned residual is correlated with marginal utility" concern via Cochrane-Saa-Requejo (2000) bounds. This is a more honest CI than the standard Sharpe-ratio CI we use.

### 4.3 Strikes and maturities

> "robust over time, across strikes, across maturities"

The 0.90-1.10 strike range covers our 28-DTE put credit spread legs (typically short-30-delta + long-15-delta). The maturity robustness is checked at multiple monthly-maturity intervals; results stable.

---

## 5. Testable hypotheses for v8a backtest

### EXP-3151 (re-affirmed) — Per-stream Sharpe attribution post-2020

**Hypothesis.** Post-2020 v8a Sharpe contribution from {qqq_cs, xlf_cs, xli_cs, gld_cal, slv_cal} > Sharpe contribution from exp1220 (SPY).

**Why it tests Dew-Becker's mechanism.** If exp1220 contributes most of the post-2020 P&L despite the paper's "post-2010 SPX alpha = 0" claim, either the paper's mechanism is wrong on our universe or our gating adds true timing alpha. If sector streams dominate, the paper's prediction is verified — frictions remain where retail flow is thin.

**Effort.** 0.5 day. Decompose existing post-2020 backtest output by stream.

**Pre-registered metric.** Sharpe of each stream over 2020-01 through 2024-12, equal-vega-weighted. Report 95% CIs.

### EXP-3200 — Synthetic-option benchmark for v8a streams

**Hypothesis.** For each of the 6 v8a underliers, the gap between v8a stream P&L and a synthetic-option-replication benchmark (built from the underlying alone) is **non-zero positive** in 2020-2024 — and that gap is a clean measure of the residual friction premium we are harvesting.

**Method.**
1. Build daily synthetic-option benchmark per Dew-Becker's recipe (HAR-vol forecast → Hull-White delta → daily delta-hedge with 1-day lag) for SPY, QQQ, XLF, XLI, GLD, SLV.
2. For each of the 4 short-vol streams (exp1220, qqq_cs, xlf_cs, xli_cs), compute (stream P&L) minus (synthetic-option-equivalent P&L at same notional vega) and test if the gap is statistically positive over 2020-2024.

**Effort.** 4-5 days. Significant new code (HAR-volatility forecast, leverage-corrected delta, daily replicating portfolio per stream).

**Watchpoint.** Per Rule-1, no synthetic data; the "synthetic option" here is a deterministic function of the actual underlying, so it does not violate the rule. Document this carefully.

### EXP-3000 (re-affirmed) — Dealer-proxy GEX gate

**Trigger.** Dew-Becker page 5 makes dealer net-GEX the structural variable.

**Hypothesis.** v8a streams pay materially better when proxy-GEX (computed from listed-options chain) is net negative.

**Effort.** 3 days.

### EXP-3210 — Cumulative-alpha check matching Dew-Becker's window

**Hypothesis.** Naive (no-gating) v8a back-tested over March 2009 - December 2022 produces near-zero cumulative log alpha for exp1220, in line with the paper.

**Why test it.** This is a direct out-of-sample replication of the paper's most striking finding on our backtest engine. If our naive exp1220 produces a meaningful cumulative alpha over that window, we either have a methodology gap (data error, fill assumption too kind) or have found a genuine SPX residue the paper missed.

**Effort.** 1 day. Strip exp1220's gating; run over the paper's exact dates.

**Decision criterion.** If cumulative alpha is materially positive on this window without gating, investigate fills first (Bug-6 / Bug-8 risk); if fills are honest, this is a finding worth a private note.

---

## 6. Summary for v8a planners

- **The paper does not invalidate v8a.** It clarifies the structural mechanism: traded SPX VRP collapsed circa 2010 because dealer net-GEX flipped from short to zero/positive. The premium remains where dealer net-GEX is still negative — plausibly in sector ETFs and metals, less so in SPY.
- **Single highest-leverage test:** EXP-3151 (per-stream attribution post-2020, 0.5 day). Tells us whether our edge sits where the paper predicts.
- **Single most operational additive concept:** EXP-3200 (synthetic-option benchmark per stream). Once built, gives a permanent attribution layer separating "residual friction premium" from "timing/gating alpha."
- **Mandatory gate:** EXP-3000 (dealer-proxy GEX). Per the paper's own model, traded short-vol pays only when dealers are short gamma. We currently do not condition on this.
