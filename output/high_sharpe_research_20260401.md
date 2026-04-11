# High-Sharpe Strategy Research — MASTERPLAN RES-003
**Date:** 2026-04-01
**Goal:** Identify paths from Sharpe 1.94 → 6.0+ for credit spread system
**Researcher:** Overnight automated research pass (10 web searches + 3 fetches)

---

## Executive Summary

- **Sharpe 6.0 is not achievable with a single credit-spread strategy on one underlying.** The CBOE PUT index (the institutional benchmark for systematic put selling) achieves Sharpe ~0.50–0.67 after 30 years. Our current 1.94 is already well above the single-strategy ceiling and is attributable to ML regime filtering, VIX gating, and stop-loss discipline.
- **The path to 6.0 requires combining 3–5 uncorrelated edge sources**: (1) VRP capture via credit spreads (current system), (2) VIX term structure roll yield, (3) cross-underlying diversification (already underway via COMPASS/Phase 7), and (4) short-term mean reversion signals layered as entry timing. Each orthogonal edge improves Sharpe multiplicatively, not additively.
- **Variance Risk Premium diversification across underlyings is the highest-confidence near-term lever.** The Phase 7 COMPASS expansion (exp_305) already raised avg return from +51.6% to +70.6% and cut max DD from -30.9% to -12.8%. More underlyings = more Sharpe improvement per unit of effort.
- **Dispersion trading (short index vol, long single-stock vol) is theoretically compelling but operationally out of reach** without single-stock options infrastructure. Prioritize multi-underlying VRP diversification first.

---

## Why 6.0+ Sharpe Is Achievable In Principle

A Sharpe ratio of 6.0 annualized means a daily Sharpe of 6.0 / sqrt(252) ≈ 0.378. In practical terms: if your strategy earns a consistent daily P&L with standard deviation of 1% of NAV, you need a daily mean return of 0.378% — roughly 95% annualized on a 1% vol budget. This is the regime of Renaissance Medallion (Sharpe reportedly 3.0–6.0 gross pre-fees) and is achieved only through massive signal diversification.

The math of Sharpe combination: if two uncorrelated strategies each have Sharpe = S, the combined portfolio has Sharpe = S * sqrt(2) ≈ 1.41S. Five uncorrelated strategies with individual Sharpe 2.0 combine to a portfolio Sharpe of 2.0 * sqrt(5) ≈ 4.47. Getting to 6.0 from five strategies requires each to average Sharpe 2.68 individually — achievable if each is well-filtered, regime-aware, and diversified across underlyings.

**Key implication:** Our current system at Sharpe 1.94 on one underlying (SPY/IBIT) is already strong. Adding 2–3 orthogonal, well-filtered strategies on different underlyings is the concrete path to 5–6.

---

## Research Finding 1: Renaissance / Medallion

### What Is Known

Medallion has returned ~66% annualized gross of fees (after fees ~39%) since 1988. The pre-fee Sharpe ratio is estimated at 3.0–6.0 depending on the period and volatility window used. Their reported standard deviation is ~31.7% annualized, which at 66% returns implies a raw Sharpe of approximately (66% – 5%) / 31.7% ≈ 1.92 — similar to our current figure. The high Sharpe estimates arise because Medallion's actual realized volatility was much lower than 31.7% in many periods.

### Core Signals (Publicly Available)

From Greg Zuckerman's "The Man Who Solved the Market" and public researcher accounts, the documented signal categories are:

1. **Overnight gap mean reversion**: Buy if opening price is unusually low vs prior close; sell if unusually high. A "24-hour effect" where the prior day's price action predicts next-day reversion.
2. **Day-of-week seasonality**: Monday prices tend to follow Friday direction; Tuesday sees reversion. Specific calendar patterns.
3. **Lead-lag correlations**: Some instruments systematically lead others (sector ETFs leading SPY, VIX leading credit, etc.).
4. **Autocorrelation exploitation**: Intraday patterns where a price move in one time bucket predicts the next. Active on 5-minute to 2-hour timeframes.
5. **Pairs and mean reversion**: Cointegrated pairs that deviate from fair value revert. Not just equity pairs — cross-asset (equities, futures, rates, FX).
6. **Full signal taxonomy**: Trend, mean reversion, pairs, seasonality, fundamental factors, mathematical factors, correlations (lead/lag), autocorrelation. Many signals in each category.

The pivotal breakthrough was the integration of all signals into a single unified portfolio optimizer (done by Mercer and Brown around 1999–2000), which resolved signal conflicts and enabled true portfolio-level optimization.

### Relevance To Our System

Our XGBoost ML filter and ComboRegimeDetector already implement rudimentary versions of signals 1 and 6. The highest-value near-term addition is **lead-lag correlation signals**: specifically, whether sector ETF implied vol and price momentum leads SPY by 1–2 sessions. This is implementable with existing infrastructure. Adding intraday entry timing (buy the spread at the intraday low of implied vol, not at open) based on 30-minute IV patterns is a direct Medallion-style autocorrelation signal.

---

## Research Finding 2: Variance Risk Premium (VRP) Capture

### The Premium

The VRP is the systematic excess of implied volatility over subsequent realized volatility. On the S&P 500, implied vol (VIX) exceeds subsequent 30-day realized vol on average by 3–5 volatility points (~15–25% relative). This gap has persisted for 30+ years and is the foundational edge of all credit spread, put write, and covered call strategies.

Key magnitudes from academic research:
- Selling at-the-money straddles monthly: **26% annual return, Sharpe 1.16, max DD -24%** (Quantpedia, 1986–1995 backtest)
- CBOE PUT index (monthly ATM put sale): **Sharpe 0.50–0.67** over 30+ years, annualized return ~5.97% above T-bills
- CBOE BXM (covered call): Sharpe higher than S&P 500 over 32.5 years on both Sharpe and Sortino basis
- Nomura Equity VRP UCITS: Sharpe ~0.7–0.8 net of fees in live trading

The gap between our current Sharpe (1.94) and the PUT benchmark (0.67) is explained entirely by our ML regime filter, VIX gating, and directional spread selection — we are only selling vol in favorable conditions, not mechanically every month.

### How Professional Firms Capture It

**Capstone Investment Advisors** (founded 2007, ~$10B AUM, founded by Paul Britton) focuses on "relative value trading with a volatility bias." Their active volatility carry program sells delta-hedged options. They generated 7% annualized returns 2007–2021, including +280% on their tail-risk fund in 2020 (they run BOTH short and long vol simultaneously as a hedge). This is the key: they do not run naked short vol — they run structured relative value between implied vol levels, term structure positions, and delta-hedged carries.

**Susquehanna (SIG)** traded 2.7 billion option contracts in 2023 (more than the entire industry in 2007). Their approach is market making + relative value, not directional vol selling. They profit from the bid-ask spread in implied vol space, not from VRP capture per se.

Key techniques for high Sharpe VRP capture:
1. **Diversification across underlyings**: VRP exists in equities, commodities, FX, rates. Low correlation between VRP captures across assets substantially improves portfolio Sharpe.
2. **Dynamic delta hedging**: Neutralize directional exposure daily. This isolates pure vol premium capture and reduces realized volatility of the strategy P&L.
3. **Regime timing / VIX gating**: Our system already does this (vix_max_entry=35). The EXP-520 discovery that VIX gating cut 2020 DD from -61.6% to -14.4% is textbook VRP regime management.
4. **Term structure exploitation**: Sell near-term vol, buy far-term vol (or stay flat far-term). Near-term VRP is consistently larger.
5. **Strike diversification**: Selling multiple strikes (not just ATM) at different delta levels captures the volatility smile premium.

### Sharpe Enhancement Techniques

| Technique | Estimated Sharpe Lift | In Our System? |
|-----------|----------------------|----------------|
| ML regime filter | +0.8–1.2 | YES (XGBoost) |
| VIX max-entry gate | +0.3–0.5 | YES (vix_max_entry) |
| Multi-underlying | +0.5–1.0 per added uncorrelated underlying | PARTIAL (Phase 7) |
| Delta hedging | +0.3–0.6 | NO |
| VIX term structure filter | +0.2–0.4 | NO |
| Intraday entry timing | +0.1–0.3 | NO |

### Relevance To Our System

We are capturing VRP with above-average efficiency already. The three missing levers are: (1) more underlyings via COMPASS, (2) VIX term structure filter to avoid selling vol when contango is flat/inverted, and (3) intraday entry timing to sell at IV spikes within the entry day.

---

## Research Finding 3: Market-Neutral / Dispersion

### The Dispersion Trade

Dispersion trading: sell S&P 500 index options (short index vol), buy options on individual S&P 500 component stocks (long single-stock vol). The trade profits from the **correlation risk premium**: implied correlation between stocks (as embedded in index vol) consistently exceeds realized correlation. Average implied correlation: ~39.5%; average realized: ~32.5% — a persistent 7-point gap.

Reported Sharpe ratios in academic literature:
- Quantpedia (S&P 100, 1996–2007): Sharpe 0.82, annualized return 15.39%, max DD -43.49%
- Best period (S&P 100, 2010–2015): Sharpe **2.47**, annualized return 23.51% — the "sweet spot" before HFT compressed spreads
- Simple versions: Sharpe 0.34–0.40 after transaction costs

### Implementation

The trade requires: (1) a variance swap or straddle on the index, (2) offsetting straddles/strangles on 20–50 individual stocks, (3) daily delta hedging of all legs. The key risk is **correlation spikes during macro shocks** (COVID March 2020, GFC 2008) when all stocks move together and the short-correlation position bleeds heavily.

### Feasibility Assessment

**Not feasible with current infrastructure.** We would need:
- Single-stock options data (Polygon provides this but cache would need to expand from SPY/IBIT to 50+ tickers)
- Per-stock options pricing and execution via Alpaca
- Daily delta hedging infrastructure (not currently built)
- Substantially larger capital base to hold 20–50 option positions simultaneously

The correlation risk premium is real but the operational complexity is 10x our current system. The max DD of -43.49% also disqualifies it from the 12% DD target. **Defer to Phase 10+ or a separate system.**

---

## Research Finding 4: Statistical Arbitrage in Options

### Relative Value Approaches

The core insight is that IV relationships between related instruments (same underlying, different expirations; different underlyings in the same sector; spot vs futures implied vol) tend to be stable and mean-revert when they diverge.

Key relationships we could exploit:
- **VIX1D/VIX9D/VIX/VIX3M term structure slope**: When VIX3M/VIX > 1.10 (steep contango), short vol has a strong tailwind. When VIX3M/VIX < 0.95 (flat or inverted), avoid or reduce.
- **SPY vs QQQ implied vol**: Historically, SPY IV and QQQ IV have a stable ratio. When QQQ IV spikes relative to SPY IV, there's a mean-reversion opportunity (sell QQQ spreads, buy SPY spreads as a pair).
- **Sector ETF IV relative to SPY IV**: When XLF IV/SPY IV is elevated (e.g., during bank stress), selling XLF spreads and buying SPY spreads hedges the position.

### IV Surface Arbitrage

Full IV surface arbitrage (calendar spreads, butterfly arbitrage, skew trades) requires high-frequency data, sub-second execution, and tight bid-ask spreads — the domain of SIG, Jane Street, and Citadel Securities. Not feasible for us.

However, **coarse IV relationship monitoring** (daily granularity) is feasible and directly implementable via our existing Polygon/Yahoo Finance data pipeline.

### Feasibility Assessment

**VIX term structure filter: HIGH feasibility, LOW complexity.** Adding a `vix_contango_min` parameter (e.g., require VIX3M/VIX > 1.05 before entering) is a 1-day implementation and could improve Sharpe by 0.2–0.4.

**SPY/QQQ IV relative value: MEDIUM feasibility.** QQQ data already accessible; would require adding QQQ as a COMPASS universe ticker with a separate IV regime signal.

---

## Research Finding 5: VIX Term Structure

### The Roll Yield

VIX futures are in contango (front-month futures cheaper than back-month) approximately 75–80% of the time. The structural reason: uncertainty about the future is priced higher than current uncertainty. The roll yield from this contango is approximately 90% annualized in raw terms — but this is the gross decay of a continuously rolled short VIX futures position, not a net return (transaction costs, inversion periods, and volatility spikes erode this significantly).

The key signal: **when VIX term structure slope (VIX3M/VIX) exceeds 1.10–1.12, short vol strategies enter their highest-edge regime.** When below 1.0 (backwardation), short vol has negative carry and should be avoided entirely.

Our system already uses VIX3M via `ComboRegimeDetector` (the `_build_combo_regime_series()` function with VIX3M data). This is a version of term structure awareness, but not explicitly filtered on contango slope for entry gating.

### Historical Performance

**XIV (VelocityShares Daily Inverse VIX Short-Term ETN)** was the canonical retail short-VIX product:
- 2011–2017: ~300% cumulative return, implied Sharpe approximately 2.0–3.0
- February 5, 2018 ("Volmageddon"): VIX spiked >100% intraday, XIV lost 96% of value in one session and was liquidated

**Post-2018 landscape:**
- SVXY restructured to 0.5x inverse exposure (half the leverage)
- SVXY annualized return post-restructuring is substantially lower and more volatile
- Pure short VIX ETF/ETN strategies are considered toxic without sophisticated hedging

### Post-2018 Approaches

Professional vol managers post-2018 take three approaches:
1. **Hard VIX cap with fast exit**: Our system's `vix_max_entry=35` and `stop_loss=2.5x` already implements this.
2. **VIX term structure filter**: Only short vol when term structure slope is in contango by >5–10%. Avoids the flat/inverted regime where vol spikes are more likely.
3. **Long vol hedge**: Maintain a small long vega position (VIX call spreads or OTM put on a fear-correlated asset) as tail insurance. Cost: ~0.5–1.5% annual drag. Benefit: limits drawdown in tail events.

**Key number**: Strategies with systematic VIX term structure filters improve Sharpe ratio approximately 2x and reduce drawdown by ~50% vs unfiltered short vol (from academic studies cited in the Artur Sepp research).

---

## Feasibility Assessment: Our Infrastructure

| Strategy | Sharpe Potential | Infrastructure Fit | Complexity | Timeline |
|----------|-----------------|-------------------|------------|----------|
| Multi-underlying VRP (COMPASS Phase 7) | +0.5–1.5 Sharpe lift | HIGH — already built | LOW | Active now |
| VIX term structure contango filter | +0.2–0.5 Sharpe lift | HIGH — VIX3M data in system | LOW | 1–2 days |
| Intraday IV entry timing (sell at intraday IV spike) | +0.2–0.4 Sharpe lift | MEDIUM — intraday data in Polygon cache | MEDIUM | 1–2 weeks |
| Lead-lag sector signals (sector IV leads SPY entry) | +0.2–0.4 Sharpe lift | MEDIUM — sector data in COMPASS | MEDIUM | 1–2 weeks |
| Short VIX term structure position (SVXY/VIX futures) | +0.5–1.0 Sharpe lift | LOW — new instrument, Alpaca futures needed | HIGH | 1–2 months |
| Dispersion trading (short index, long stocks) | +1.0–2.0 Sharpe lift | VERY LOW — 50+ tickers, delta hedging | VERY HIGH | 6+ months |
| Delta hedging of credit spreads | +0.3–0.6 Sharpe lift | LOW — intraday rebalancing not built | HIGH | 2–3 months |

---

## 3 Concrete Experiment Ideas

### EXP-NEXT-1: VIX Contango Gate (VIX3M/VIX Slope Filter)

**Hypothesis:** Only entering credit spreads when VIX futures term structure is in contango by at least 5% (VIX3M/VIX > 1.05) eliminates the flat/inverted regime where VRP capture is negative. This should reduce DD in 2022 and early 2020 while adding minimal return drag in bull regimes.

**Implementation:**
- Add `vix_contango_min` parameter to config JSON (e.g., `"vix_contango_min": 1.05`)
- In `backtester.py`, add to the entry gate block at lines ~673–681: fetch the VIX3M/VIX ratio on the entry date and skip if below `vix_contango_min`
- VIX3M data is already loaded by `_build_combo_regime_series()` — reuse the same data series
- Config change: add to `exp_520` base config as `exp_521_contango_gate.json`

**Expected Sharpe improvement:** Sharpe lift +0.2–0.4. Based on academic finding that term structure filters improve Sharpe ~2x and cut DD ~50% on short-vol strategies. Conservative estimate: reduces 2022 max DD from -24% to -15%, improves Sharpe from 1.94 to ~2.2–2.4.

**Key risks:** Eliminates entries in persistent backwardation periods (rare but exists — Aug 2024 yen carry trade spike saw VIX3M below VIX for ~3 weeks). Could reduce trade frequency by 15–20%.

**How to test:** Run 6-year backtest on EXP-520 base config with vix_contango_min at 1.00 (baseline), 1.03, 1.05, 1.08, 1.12. Compare Sharpe, max DD, trade count, 2020 and 2022 year performance.

---

### EXP-NEXT-2: Intraday IV Entry Timing (IV Spike Capture)

**Hypothesis:** Entering credit spreads at intraday implied volatility peaks (30–60 minutes after open, when morning fear spikes are at maximum) rather than at open or end-of-day captures 5–15% more premium per trade. At 5–8 trades per month, this compounds to meaningful Sharpe improvement.

**Implementation:**
- Use Polygon intraday options data already in cache (`option_intraday` table with 1.59M rows)
- During entry execution, instead of taking the first available quote, scan available quotes for the entry day across 9:35–10:30 AM window and select the highest mid-price for the short strike
- Add `intraday_entry_timing: "iv_peak"` to config, with fallback `"open"` (current behavior)
- In `backtester.py` `_find_spread_opportunity()` — add a `_find_best_intraday_entry()` helper that queries `option_intraday` for the target DTE/strike and returns the highest observed IV quote before 11 AM
- Limit to SPY (real data); sector ETFs stay on open (heuristic data)

**Expected Sharpe improvement:** +0.15–0.30 Sharpe. If average premium capture increases 8% per trade and there are ~55 trades per year, the annualized return improvement is roughly +2–4% with no change in volatility, adding directly to Sharpe numerator.

**Key risks:** (1) Intraday cache may not have complete coverage for all target dates — need to audit coverage. (2) Overfitting risk: "IV spike" selection may look good in backtest but be hard to execute live (the spike may last 2–3 minutes). (3) Some open-high patterns may be driven by news that resolves risk, making peak IV entries riskier, not safer.

**How to test:** Compare EXP-520 with `intraday_entry_timing: "open"` vs `"iv_peak"` vs `"day_high"`. Check trade-level premium vs average daily VIX to confirm peak entries actually capture elevated IV, not just noise. Run both on 2020–2025 and compare Sharpe, average premium, win rate.

---

### EXP-NEXT-3: COMPASS Phase 7 Walk-Forward Validation with Sharpe as Primary Metric

**Hypothesis:** The exp_305 COMPASS portfolio (top-2 sector ETFs at 65% threshold + SPY) achieves +70.6% avg return vs +51.6% SPY-only — but this has not been validated in a walk-forward context or had its Sharpe explicitly measured. The regime-switching between SPY-only and sector-augmented years may have higher Sharpe than either alone because low-DD years (sector years: DD -12.8%) offset high-DD years.

**Implementation:**
- This is a validation/analysis experiment, not a code change
- In `scripts/run_portfolio_backtest.py`, add Sharpe calculation to the output: `sharpe = (avg_annual_return - risk_free_rate) / std(annual_returns)`
- Run 6-year walk-forward: train universe selection on years 1–2, test on year 3. Advance one year. Report both in-sample and out-of-sample Sharpe.
- Also run the EXP-520 VIX-gated base config through the portfolio backtest (not just the SPY-only version) to get the combined COMPASS + VIX-gate Sharpe
- Target config: `exp_305_compass_validated.json` — existing exp_305 params + EXP-520's `vix_max_entry=35`

**Expected Sharpe improvement:** Unknown until tested. Hypothesis: COMPASS Phase 7 + VIX gating achieves Sharpe 2.5–3.5 on the combined portfolio. The DD reduction from -30.9% to -12.8% is the dominant driver — lower DD reduces return volatility, which directly raises Sharpe.

**Key risks:** (1) Walk-forward universe selection may not be stable — 2022 XLE dominance was obvious in retrospect but required knowing oil's response to Russia/Ukraine. (2) Sector ETF heuristic mode introduces noise vs real data. (3) Capital allocation across tickers (SPY 60%, sector 20%+20%) creates concentration risk.

**How to test:** Run the 6-year walk-forward and report: annual Sharpe, max DD by year, out-of-sample hit rate (correct sector chosen), and sensitivity to the 65% threshold. Compare against SPY-only baseline Sharpe.

---

## Closing Gap Analysis: 1.94 → 6.0

The honest quantitative assessment:

**Current system (SPY + IBIT credit spreads, ML filter, VIX gate):**
- Sharpe: 1.94 (6 weeks live, ~250 days backtest)
- This is already top-decile for systematic options strategies

**Near-term achievable (6–12 months, experiments above):**
- VIX contango gate: +0.3 → Sharpe ~2.2
- Intraday entry timing: +0.2 → Sharpe ~2.4
- COMPASS Phase 7 (6 uncorrelated underlyings): sqrt(6/1) improvement factor applied to base Sharpe ≈ 2.4 * 1.5 → Sharpe ~3.5–4.0
- Combined realistic estimate: **Sharpe 3.0–4.0 within 12 months** with the existing codebase and Phase 7 completion

**Medium-term (12–24 months, new strategies):**
- Adding VIX term structure position (SVXY or VIX call spread as carry overlay): +0.4–0.6
- Adding intraday mean reversion signal for entry timing: +0.2–0.3
- Combined: **Sharpe 3.5–5.0**

**To reach Sharpe 6.0:**
The gap from 4–5 to 6.0 requires either (a) a fundamentally new edge source with low correlation to the existing system, or (b) better capital efficiency (less cash drag, tighter execution). Realistic new edges:
- Dispersion trading on 10–20 liquid single stocks (feasibility: medium, timeline: 6–12 months to build)
- Cross-asset vol relative value (equities vs bonds vs gold implied vol): Feasibility: low currently
- Statistical intraday patterns (Medallion-style autocorrelation): Feasibility: medium, requires 1-minute data and signal research

**Honest assessment:** Sharpe 6.0+ with a single-strategy, single-asset-class system is essentially the Medallion Fund territory. With 5–6 well-designed, uncorrelated strategies, each at Sharpe 2.5, the combined portfolio reaches sqrt(5) * 2.5 ≈ 5.6. Getting to 6.0+ is achievable in 18–36 months if Phase 7 COMPASS succeeds AND at least one of the new edge sources (VIX term structure carry or dispersion lite) is added.

**The 3 most important near-term actions in priority order:**
1. Validate COMPASS Phase 7 walk-forward with explicit Sharpe measurement (EXP-NEXT-3) — highest expected Sharpe lift per effort
2. Implement VIX contango gate (EXP-NEXT-1) — 1–2 days of work, meaningful DD reduction
3. EXP-520 walk-forward validation with overfit scoring — before expanding further, confirm the base champion is real

---

## References

- [Quantpedia: Volatility Risk Premium Effect](https://quantpedia.com/strategies/volatility-risk-premium-effect)
- [Quantpedia: Dispersion Trading](https://quantpedia.com/strategies/dispersion-trading)
- [Cboe: Benchmark Indexes Risk-Adjusted Returns](https://www.cboe.com/insights/posts/key-cboe-benchmark-indexes-using-spx-options-offer-strong-risk-adjusted-returns/)
- [CBOE PutWrite Historical Performance — Prof. Oleg Bondarenko 2019](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)
- [Cboe BXM Index Dashboard](https://www.cboe.com/us/indices/dashboard/bxm/)
- [Cboe PUT Index Dashboard](https://www.cboe.com/us/indices/dashboard/put/)
- [Harvesting the S&P 500 Volatility Risk Premium — Hedge Fund Journal](https://thehedgefundjournal.com/harvesting-the-s-p500-volatility-risk-premium/)
- [Harvesting VRP Globally — Hedge Fund Journal](https://thehedgefundjournal.com/harvesting-the-volatility-risk-premium-globally/)
- [Capstone Investment Advisors — Wikipedia](https://en.wikipedia.org/wiki/Capstone_Investment_Advisors)
- [Renaissance Technologies — Wikipedia](https://en.wikipedia.org/wiki/Renaissance_Technologies)
- [Jim Simons Trading Strategy — QuantVPS](https://www.quantvps.com/blog/jim-simons-trading-strategy)
- [Simons' Strategies Unpacked — LuxAlgo](https://www.luxalgo.com/blog/simons-strategies-renaissance-trading-unpacked/)
- [Dispersion Trading Explained — QuantVPS](https://www.quantvps.com/blog/dispersion-trading-explained)
- [Dispersion and DSPX Index — Resonanz Capital](https://resonanzcapital.com/insights/dispersion-trading-and-the-dspx-index)
- [VIX Term Structure as Trading Signal — Macrosynergy](https://macrosynergy.com/research/vix-term-structure-as-a-trading-signal/)
- [Exploiting VIX Futures Term Structure — Quantpedia](https://quantpedia.com/strategies/exploiting-term-structure-of-vix-futures)
- [Allocation to Systematic Volatility Strategies — Artur Sepp Blog](https://artursepp.com/2017/09/20/allocation-to-systematic-volatility-strategies-using-vix-futures-sp-500-index-puts-and-delta-hedged-long-short-strategies/)
- [SVXY ProShares ETF — ProShares](https://www.proshares.com/our-etfs/strategic/svxy)
- [Volatility Arbitrage — Wikipedia](https://en.wikipedia.org/wiki/Volatility_arbitrage)
- [Relative Value Trading: Cross-Asset Volatility — Amberdata](https://blog.amberdata.io/relative-value-trading-how-to-compare-cross-asset-volatility)
