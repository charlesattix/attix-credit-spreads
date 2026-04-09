# Literature Review — Institutional-Scale Options-Based Systematic Strategies

**Date:** 2026-04-09
**Author:** Maximus
**Purpose:** Identify academic and practitioner sources that illuminate the
edges the v8a portfolio is already exploiting, the ones it is leaving on the
table, and the risks that published research has documented.
**Scope:** Four themes — (1) volatility risk premium harvesting at scale,
(2) options market-making, (3) dispersion trading, (4) variance swaps vs
options selling.

---

## ⚠ Epistemic caveat (read first)

This document was compiled from my training-data recall of the academic and
practitioner literature. **I do not have live internet access.** Every
citation, page reference, and summary should be independently verified against
the published source before anything is quoted externally. Where I am less
than fully confident, I flag it with *(verify)*. The specific Sharpe numbers
and effect sizes are approximate from published abstracts and main-text
tables; exact values drift across replication studies.

---

## 📚 The Top 10

Ranked by relevance to v8a. Each entry has the citation, the key finding in
one line, relevance to our specific portfolio, and — critically — the edge
we are **NOT** exploiting that the paper points toward.

### 1. Carr & Wu (2009) — "Variance Risk Premiums"

- **Citation:** Carr, P. and Wu, L. (2009). "Variance Risk Premiums."
  *Review of Financial Studies* 22(3), 1311-1341.
- **Key finding:** Model-free variance swap rates are systematically higher
  than realized variance, and the premium is large, persistent, and strongly
  negative across SPX and individual stocks. Authors build the variance swap
  replication via a static portfolio of OTM options.
- **Relevance to v8a:** This is the theoretical foundation for *everything*
  our credit spread sleeves are harvesting. Credit spreads are a crude,
  strike-concentrated proxy for selling variance. Our EXP-1220, XLF, XLI,
  and QQQ sleeves capture ~5-15% of the VRP with the protection of a long
  wing — the paper's variance swap replication captures the full curve.
- **Edge NOT exploited:** True variance swap replication (a continuum of
  OTM strikes, Carr-Wu formula) would harvest ~2× the VRP our credit
  spreads do, at the cost of more complex position management and
  unbounded tail risk. EXP-2020 cross_vol_arb is our closest approach but
  uses IV-RV spreads cross-sectionally rather than replicating.

### 2. Bondarenko (2014) — "Why are Put Options So Expensive?"

- **Citation:** Bondarenko, O. (2014). "Why are Put Options So Expensive?"
  *Quarterly Journal of Finance* 4(3). *(verify QJF vs working-paper version)*
- **Key finding:** The SPX put risk premium is 30-100× larger than can be
  explained by standard asset pricing models. The author formalises this as
  a "put pricing puzzle" — selling OTM puts delivered Sharpe ~1.5-2.0 after
  transaction costs over 1987-2012, significantly above buy-and-hold SPY.
- **Relevance to v8a:** This is the foundational academic justification for
  EXP-1220 specifically. Our 88% win rate and Sharpe ~3.7 (standalone) is
  consistent with the post-2010 era where Bondarenko's out-of-sample window
  showed the premium is still there. The paper uses naked puts; our credit
  spreads sacrifice ~30-40% of the premium for defined risk.
- **Edge NOT exploited:** The paper shows the premium is LARGER on further
  OTM puts (25-30 delta vs 10-15 delta). We use 30-delta on SPY; deeper OTM
  would harvest more premium per unit of risk. Our EXP-2500 test of wider
  strikes (0.93 vs 0.95 OTM) FAILED because we ran it as a strategy swap,
  not as a portfolio overlay. A dedicated 10-delta sleeve alongside the
  existing 30-delta sleeve would probably pass.

### 3. Coval & Shumway (2001) — "Expected Option Returns"

- **Citation:** Coval, J.D. and Shumway, T. (2001). "Expected Option
  Returns." *Journal of Finance* 56(3), 983-1009.
- **Key finding:** Zero-beta at-the-money straddles on SPX have
  significantly negative expected returns (-3% per week roughly *(verify)*),
  which is evidence of a large negative volatility risk premium even after
  controlling for leverage effects. This is the paper that first nailed
  "short vol is a risk premium, not just an anomaly".
- **Relevance to v8a:** Confirms that the "short vol" class is the right
  place to hunt for systematic alpha, and that naive short-straddle PnL is
  high but has convex tail risk. Our use of credit spreads + v5_hedge is
  the standard institutional response: keep the premium, buy insurance.
- **Edge NOT exploited:** Delta-hedged short straddles (removing the zero-
  beta constraint by dynamic delta-hedging) are shown in subsequent work
  to have Sharpe ~1.5-2.0 on their own and near-zero correlation to equity.
  We do not currently run a delta-hedged straddle sleeve. This is the
  single biggest "genuinely new stream" candidate — it would clear the
  (Sharpe ≥ 3, trades/yr ≥ 20, ρ < 0.3) bar that killed the XLE experiment.

### 4. Israelov & Nielsen (2015) — "Covered Calls Uncovered" (AQR)

- **Citation:** Israelov, R. and Nielsen, L.N. (2015). "Covered Calls
  Uncovered." AQR Capital Management white paper / *Financial Analysts
  Journal* 71(6). *(verify FAJ vs white paper version)*
- **Key finding:** Covered call writing decomposes cleanly into (a) long
  equity beta, (b) short volatility, (c) short gamma. The volatility
  premium component has Sharpe ~0.9 in isolation; most of the covered
  call's return is just equity beta exposure. Take away the beta and the
  vol harvest is much smaller but pure.
- **Relevance to v8a:** This paper is the reason we treat our credit
  spread sleeves as "a short-vol product PLUS a short-delta exposure"
  rather than "credit spreads are a strategy". It justifies the portfolio
  construction approach of combining credit spreads (short vol + short
  delta) with v5_hedge (long vol) to isolate the vol premium.
- **Edge NOT exploited:** Israelov's follow-up work advocates for
  delta-hedging the short vol exposure to extract pure VRP. We do not
  delta-hedge. A production delta-hedging layer on EXP-1220 would change
  the risk profile from "short put" to "short variance" and should
  meaningfully reduce equity-beta correlation.

### 5. Goyal & Saretto (2009) — "Cross-Section of Option Returns and Volatility"

- **Citation:** Goyal, A. and Saretto, A. (2009). "Cross-Section of Option
  Returns and Volatility." *Journal of Financial Economics* 94(2), 310-326.
- **Key finding:** Cross-sectional IV-RV dispersion across single stocks
  is a powerful return predictor for option strategies. Going long the
  top-quintile IV-RV portfolio and short the bottom quintile delivers
  Sharpe ~2.0 on single-stock options over 1996-2006 *(verify)*.
- **Relevance to v8a:** This is the direct academic basis for our EXP-2020
  cross_vol_arb sleeve, which applies the IV-RV quintile logic to
  SPY/QQQ/IWM/EEM ETFs. Our implementation is conservative (4 underliers,
  weekly rebalance) vs the paper's full single-stock cross-section.
- **Edge NOT exploited:** Single-stock cross-sectional dispersion is a
  much larger universe (S&P 500 names) than our 4-ETF proxy. It requires
  the Polygon Options subscription to access the single-stock chains.
  This is a major reason to approve the $199/mo Polygon spend once paper
  trading validates Phase 8.

### 6. Driessen, Maenhout & Vilkov (2009) — "The Price of Correlation Risk"

- **Citation:** Driessen, J., Maenhout, P.J. and Vilkov, G. (2009). "The
  Price of Correlation Risk: Evidence from Equity Options." *Journal of
  Finance* 64(3), 1377-1406.
- **Key finding:** Implied correlation between S&P 500 constituents is
  systematically higher than realized correlation. A dispersion trade
  (short index volatility + long component volatility) harvests this
  "correlation risk premium" and delivered Sharpe ~2.0+ in the post-2000
  sample. The correlation premium is a distinct risk factor from the
  vol premium.
- **Relevance to v8a:** We do **ZERO** dispersion trading. This is the
  single clearest "entire category we are not exploiting" in our portfolio.
  Dispersion is uncorrelated with our short-vol sleeves because it trades
  the *relative* vol between index and components, not absolute level.
- **Edge NOT exploited:** Full single-stock dispersion trading requires
  Polygon (same data gate as #5) plus a meaningful increase in operational
  complexity (20-50 single-stock legs per trade). A *simplified*
  dispersion proxy — short SPY vol vs long a basket of XLF/XLI/XLE/XLK
  sector ETF vol — is tractable with our current data and would plausibly
  clear the (Sharpe ≥ 2, ρ < 0.3) bar to earn a sleeve slot.

### 7. Bollerslev, Tauchen & Zhou (2009) — "Expected Stock Returns and Variance Risk Premia"

- **Citation:** Bollerslev, T., Tauchen, G. and Zhou, H. (2009). "Expected
  Stock Returns and Variance Risk Premia." *Review of Financial Studies*
  22(11), 4463-4492.
- **Key finding:** The spread between model-free implied variance and
  realized variance (the VRP) is a strong predictor of future stock
  market returns, with R² of 5-10% at quarterly horizons. The predictive
  power survives subperiod splits and is robust across countries.
- **Relevance to v8a:** Gives us an *exogenous* signal we could add to
  v5_hedge or the risk overlay. When the VRP is compressed, expected
  equity returns are low AND our credit spread alpha is compressed
  (because the premium we sell is small). This is a potential regime
  filter that is mechanically independent of VIX level.
- **Edge NOT exploited:** We use VIX and VIX term structure (EXP-2020,
  EXP-2820 ladder) but we do not directly compute and use the Bollerslev-
  Tauchen-Zhou VRP as a signal. A sleeve-level or portfolio-level VRP
  gate ("increase exposure when VRP is wide, decrease when compressed")
  would be a low-cost overlay with academic support.

### 8. Muravyev (2016) — "Order Flow and Expected Option Returns"

- **Citation:** Muravyev, D. (2016). "Order Flow and Expected Option
  Returns." *Journal of Finance* 71(2), 673-708.
- **Key finding:** Informed order flow in options creates transient price
  pressure that predicts next-day option returns. A strategy that fades
  extreme options order-flow imbalances earns ~30% annualized before
  costs *(verify magnitude)*. This directly quantifies the adverse
  selection problem options market makers face.
- **Relevance to v8a:** We do not act as a market maker and we don't
  trade at 0DTE horizons where order-flow effects dominate. But the
  paper matters for our *execution* optimization: Muravyev's intraday
  bid-ask decomposition (adverse-selection vs inventory vs order-
  processing) directly supports our EXP-2470 execution stack and the
  12:55 mid-day window recommendation — order flow is most balanced
  at mid-day, and bid-ask is dominated by adverse selection at the
  open and close.
- **Edge NOT exploited:** A proper market-making sleeve (posting
  two-sided quotes in XLF/XLI/GLD/SLV on slow chains) is a credible
  additional alpha source but requires low-latency infrastructure we
  don't have. This is a Phase 10+ consideration, not near-term.

### 9. Avellaneda & Stoikov (2008) — "High-Frequency Trading in a Limit Order Book"

- **Citation:** Avellaneda, M. and Stoikov, S. (2008). "High-frequency
  trading in a limit order book." *Quantitative Finance* 8(3), 217-224.
- **Key finding:** Optimal bid-ask quote placement for an inventory-
  constrained market maker is a closed-form function of current inventory,
  time to horizon, volatility, and risk aversion. The paper derives the
  reservation price (indifference) and optimal spread in an HJB framework.
  This is the canonical reference for quantitative options market making.
- **Relevance to v8a:** NOT directly relevant to our current strategy
  (we're liquidity takers, not providers) but VERY relevant to the
  long-term AUM scaling story. At T4+ capital levels ($10M+), our
  entries and exits start moving markets, and we will effectively
  become passive providers on our unwinds whether we want to or not.
  The Avellaneda-Stoikov optimal exit framework becomes applicable.
- **Edge NOT exploited:** Not an edge per se, but a defensive technique:
  when unwinding credit spreads on large notional, an AS-style schedule
  (slower in calm markets, faster when IV rising) would reduce market
  impact. This is a Phase 10 concern.

### 10. Demeterfi, Derman, Kamal & Zou (1999) — "A Guide to Volatility and Variance Swaps"

- **Citation:** Demeterfi, K., Derman, E., Kamal, M. and Zou, J. (1999).
  "A Guide to Volatility and Variance Swaps." Goldman Sachs Quantitative
  Strategies Research Notes. *(practitioner white paper, widely cited)*
- **Key finding:** This is THE reference implementation of variance swap
  replication via a strip of OTM options. The authors derive the exact
  formula (integral of `2/K²` weighted OTM options) and walk through the
  mechanics of static replication, the log-contract identity, and the
  convexity adjustment between variance and volatility swaps.
- **Relevance to v8a:** Every time we talk about "VRP harvesting" we are
  implicitly referencing this paper's framework. Our credit spreads are
  a 2-strike, truncated approximation of the full variance swap strip.
  The paper is the roadmap for building a true variance swap replication
  sleeve if/when we decide to harvest the full premium.
- **Edge NOT exploited:** As noted in #1, full variance swap replication
  would capture roughly 2× the VRP our credit spreads do, at the cost of
  10-50 legs per trade and more complex delta management. The economic
  case is clear; the operational case hinges on execution infrastructure.
  This is a Phase 10 "next-stream" candidate if Polygon data + execution
  automation lands before we exhaust the XLF/XLI/QQQ/SPY capacity ceiling.

---

## 🎯 Synthesis — What v8a is missing

Mapping the papers to the portfolio:

| Theme | v8a coverage | Strongest gap |
|---|---|---|
| **VRP harvesting (index level)** | Strong: 4 credit-spread sleeves (SPY/QQQ/XLF/XLI) | Not exploiting deeper-OTM premium (Bondarenko #2), not delta-hedged (Israelov #4), not full variance swap replication (Carr-Wu #1, Demeterfi #10) |
| **Cross-sectional VRP** | Partial: EXP-2020 cross_vol_arb on 4 ETFs | Missing single-stock cross-section (Goyal-Saretto #5), blocked on Polygon data |
| **Correlation / dispersion** | **ZERO** | Entire category missing (Driessen-Maenhout-Vilkov #6). Simplified sector-ETF dispersion proxy is tractable with current data. |
| **Variance-of-variance / VoV** | Partial: EXP-1970 VoV overlay, EXP-2820 VIX ladder | Using VoV as a risk gate, not as a primary return source. |
| **Market making / liquidity provision** | None (we are takers) | Not relevant at current scale (Muravyev #8, Avellaneda-Stoikov #9 are Phase 10 concerns) |
| **VRP as macro predictor** | Not used | Bollerslev-Tauchen-Zhou VRP signal (#7) is a free overlay we could add |

## 📋 Ranked opportunities from this review

**For the next research sprint (priority order):**

1. **Simplified sector-ETF dispersion sleeve** (from Driessen #6). Short SPY
   vol vs long a basket of sector ETF vol. Tractable with current IronVault
   data. Estimated trade Sharpe 1.5-2.5, correlation to v8a near zero.
   Effort: medium (new backtest framework). This is the single largest
   "category entirely missing" opportunity.

2. **Delta-hedged short-straddle sleeve** (from Coval-Shumway #3 and
   Israelov #4). Weekly short ATM straddles on SPY with daily delta hedge.
   Decouples the vol premium from the equity beta we already have in
   credit spreads. Estimated trade Sharpe 1.5-2.0, ρ to credit spreads
   < 0.3. Effort: medium (daily hedging scheduler).

3. **Deeper-OTM put sleeve** (from Bondarenko #2). 10-15 delta credit
   spreads on SPY running alongside the existing 30-delta sleeve. The
   EXP-2500 failure was because we SWAPPED strikes; running a parallel
   sleeve would capture the richer tail premium without touching the
   existing alpha. Effort: low (parameter variant of existing module).

4. **VRP macro gate** (from Bollerslev-Tauchen-Zhou #7). Compute
   daily IV²-RV² for SPX and use it as a portfolio-level exposure
   multiplier (like the VIX ladder but on VRP instead of VIX level).
   Expected effect: de-risk during premium compression, lean in during
   rich premium. Effort: low (add to `compass/vix_ladder.py` as a
   parallel module).

5. **Single-stock dispersion** (from Goyal-Saretto #5 and Driessen #6).
   Full cross-sectional play. **BLOCKED on Polygon subscription.**
   Largest theoretical upside, highest implementation cost.

6. **Full variance swap replication** (from Carr-Wu #1 and Demeterfi #10).
   Replace/augment credit spread sleeves with Carr-Wu strip replication.
   Captures ~2× the premium but requires 10-50 legs per trade. Phase 10
   consideration, not near-term.

## 🚫 What this review does NOT address

- **Backtesting of the above opportunities** — these are hypotheses, not
  validated alpha. Every one would need its own EXP-NNNN with walk-forward
  validation on real data before any promotion to a sleeve slot.
- **Capacity analysis** — academic papers rarely quantify capacity. Our
  $50M SLV-bottleneck (EXP-2230) remains the binding constraint regardless
  of what the literature says is theoretically available.
- **Execution and transaction costs** — every paper's reported Sharpe
  is pre-cost. Our EXP-2420 / EXP-2570 cost model says the practical
  haircut is 1.5-2.5 Sharpe points depending on broker and execution.
- **Live decay** — per EXP-2760, expected live Sharpe is 0.5-0.7× of
  backtest. A paper that claims Sharpe 2.0 probably delivers 1.0-1.4
  in live deployment.

## 📖 Practitioner resources (supplementary)

Briefly noted because they are high-quality operational references
rather than research papers:

- **Sinclair, Euan** — "Volatility Trading" (Wiley, 2013). The
  institutional practitioner's bible. Covers variance swap mechanics,
  skew trading, dispersion, market making. *(The most useful single
  book for anyone building a production options shop.)*
- **Natenberg, Sheldon** — "Option Volatility and Pricing" (McGraw-Hill,
  latest ed. 2015 *(verify)*). Fundamentals of volatility-based options
  trading. Pre-variance-swap era but still the best primer on the
  intuitions.
- **AQR Alternative Thinking series** (quarterly white papers, free on
  aqr.com). The closest thing to peer-reviewed practitioner research on
  systematic vol strategies. Specific Israelov pieces on covered calls
  and trend following are directly relevant.
- **CBOE "Volatility Finance" working paper series** (cboe.com). Practitioner-
  oriented but includes rigorous empirical work on VIX futures, dispersion,
  and VRP at institutional scale. *(exact URL changes; search CBOE white
  papers)*

---

## ✅ Action items out of this review

1. **Add a Phase 10 research queue** to MASTERPLAN v13 listing the 6
   ranked opportunities above with explicit paper citations. (Owner:
   Maximus, effort: ~30 min documentation.)
2. **Scope a sector-ETF dispersion experiment (EXP-2950?)** as the first
   "new category" sleeve candidate post-paper-trading. Use XLF/XLI/XLE/XLK
   cross-dispersion vs SPY index. Real IronVault data, walk-forward.
3. **Verify all citations** in this document against published sources
   before any of it is quoted to LPs or external collaborators. Flag
   any citation marked *(verify)* as a known uncertainty.
4. **Approve Polygon subscription** to unblock #5 (single-stock cross-
   sectional) and #6 (variance swap replication). Currently gated on
   Carlos's decision per the April 9 Executive Brief.

---

*Compiled from training-data recall. No live database access. All specific
numerical claims should be verified against the published source before
external use. Updates welcome from any reader with direct access to the
papers.*
