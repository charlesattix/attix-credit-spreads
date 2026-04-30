# Research 2024-2026 — Capacity, Alt-Data, ML for v8a

**Date:** 2026-04-29
**Scope:** Specifically targets the v8a $50M AUM ceiling and the SLV-leg bottleneck. Cross-references but does not duplicate the canonical lit review.
**Canonical:** `compass/reports/literature_review_2024_2026.md` (VRP, term structure, cross-section, GEX, execution, SVI, RL, news-LLM)
**Companion:** `compass/reports/ironvault_testable_hypotheses_2024_2026.md` (5 IronVault-backtestable hypotheses)
**Companion:** `compass/reports/tail_hedge_regime_addendum_2024_2026.md` (4 hypotheses, EXP-3080 to EXP-3110)

This file adds 3 hypotheses focused on the **capacity** problem.

---

## 0. Provenance & rules

All numerical claims below are search-snippet-level. Rule-13 haircut applies (live ≈ 0.5-0.7× backtest). Rule-1: no synthetic data anywhere. Rule-9: portfolio-level re-test mandatory after any single-stream change.

---

## 1. Why SLV is the AUM bottleneck (problem framing)

The 8-stream v8a is constrained at ~$50M AUM. The binding constraint is the slv_cal stream's per-day capacity in IronVault chain depth: SLV options OI is 5-20× thinner than SPY at any given strike, and effective-spread expansion above ~50 lots/day is well-documented (Cboe LiveVol summary tables 2024). The other 5 underliers (SPY, QQQ, XLF, XLI, GLD) have headroom; SPY by 100×, QQQ by 30×, XLF/XLI by 5-10×, GLD by 3-5×.

Three remediation directions appear in the 2024-26 literature:

1. **Substitute the constrained leg with a basket of correlated alternatives** (capacity expansion through diversification of execution venue, not signal).
2. **Spread the same dollar exposure across a DTE ladder** (capacity expansion through time, since options at different expiries are largely separate order books).
3. **Be more selective per trade** (ML-based strike selection retains alpha while reducing notional).

Each maps to one hypothesis below.

---

## 2. Three capacity-focused hypotheses

Numbered to extend the EXP-3xxx series.

### EXP-3120 — SLV capacity expansion via correlated basket (SLV + GDX + GLD-cross)

**Paper anchor.** Capacity-aware portfolio construction for derivatives, 2024-25 SSRN strand (López de Prado et al.); plus practitioner notes on metals-options substitutability (silver / gold-miners / gold cross-vol).

**Snippet-level claim.** A basket of (SLV calendar 60%, GDX calendar 25%, GLD-on-SLV cross-vol 15%) replicates ~85% of slv_cal's vega exposure with 3× the per-day liquidity capacity.

**Falsifiable form.**
- H0: replacing slv_cal with the 3-asset basket produces v8a portfolio Sharpe < current backtest Sharpe by ≥ 0.3.
- H1: H0 fails AND aggregate per-day execution capacity rises by ≥ 2.5×.

**Test design.**
1. Construct the basket on IronVault SLV+GDX chains 2019-2024. (Verify GDX chain depth before starting — partial coverage suspected.)
2. Replace slv_cal in the v8a portfolio configuration.
3. Walk-forward 2019-2022 IS, 2023-2024 OOS.
4. Measure: (a) portfolio Sharpe delta, (b) max DD delta, (c) per-day-capacity proxy = min(daily volume × 0.05) across the basket vs SLV alone.

**Watchpoints.**
- Rule-1: GDX chain coverage 2019 may be partial. If so, scope the test to 2020-2024.
- Rule-9: must re-test full 8-stream Sharpe, not just slv_cal-replacement Sharpe.
- The 85% replication figure is snippet-level; PDF read required before sizing.

**Effort.** **Hard.** Estimated 5-7 days. New basket-construction code, capacity-proxy metric, full v8a re-run. Highest expected lift on AUM ceiling among the three.

**Expected impact.** AUM ceiling: $50M → $100-150M plausible. Sharpe: −0.1 to +0.05 expected (substitution drag offset by diversification benefit).

---

### EXP-3130 — DTE ladder on the constrained streams (14 + 28 + 42 instead of fixed 28)

**Paper anchor.** Andersen-Bondarenko 2024 term-structure dynamics; arXiv 2501.12397 "Stochastic Optimal Control of Iron Condor Portfolios" — both note that DTE-laddering increases capacity by sampling distinct order books with different liquidity windows (front-month dominance reverses 5-7d before expiry).

**Snippet-level claim.** A 14/28/42 DTE ladder triples per-day notional capacity vs single-DTE on liquid index options, at a Sharpe cost of -0.10 to -0.20 (different DTEs have different VRP harvests).

**Falsifiable form.**
- H0: DTE-laddering at 1/3 each of 14/28/42 reduces v8a portfolio Sharpe by > 0.3 vs fixed 28-DTE.
- H1: per-day capacity rises ≥ 2.5× AND Sharpe loss ≤ 0.2.

**Test design.**
1. Re-run exp1220 + qqq_cs + xlf_cs + xli_cs with 1/3 capital at each of 14/28/42 DTE.
2. Walk-forward IS/OOS as above.
3. Measure capacity proxy and full v8a Sharpe.

**Watchpoints.**
- Bug-6: don't optimise DTE weights in-sample. Pre-register equal 1/3 split.
- 14-DTE bucket has higher gamma risk; tx-cost realism matters.
- Rule-9: portfolio-level re-test.

**Effort.** **Medium.** Estimated 2-3 days. Existing exp1220 code parameterises DTE; mostly a config sweep + re-aggregation.

**Expected impact.** AUM ceiling: $50M → ~$120M. Sharpe: −0.10 to −0.20 expected (acceptable trade for 2.4× capacity).

---

### EXP-3140 — ML-based strike selection (capacity-friendly alpha enhancement)

**Paper anchor.** 2024-25 arXiv q-fin.PM and q-fin.CP papers on transformer-based / gradient-boosted strike selection; reported uplift 0.10-0.30 Sharpe over uniform-strike rules on liquid index options.

**Snippet-level claim.** Per-day ranking of (delta, IV-rank, term-slope, RV20, skew) with gradient-boosted regressor predicting next-30-day P&L picks the top-quartile strike with Sharpe lift ≥ 0.15 OOS.

**Falsifiable form.**
- H0: ML-ranked strike selection has OOS Sharpe ≤ uniform-rule baseline.
- H1: ML-ranked strike has OOS Sharpe ≥ baseline + 0.10 with no DD increase.

**Test design.**
1. Feature set from IronVault columns only (no alt-data): delta-bucket, IV-rank, IV30/IV90, RV20, 25d skew, OI, daily volume.
2. Walk-forward GBM (or simple logistic on next-30d outcome). Pre-register hyperparameters.
3. Apply ranker on top of exp1220 entry list daily; pick top decile.
4. Sharpe vs uniform-rule baseline; capacity proxy unchanged (same trade count, better selection).

**Watchpoints.**
- Bug-6: hyperparameter sweeps on the full sample will overfit. Use pre-registered defaults; nested CV optional.
- Rule-13: published 0.10-0.30 Sharpe uplifts on ML options selection are notoriously overstated. Expect 0.0-0.10 live.
- Rule-1: no alt-data needed — all features are IronVault native.

**Effort.** **Easy-Medium.** Estimated 2 days. GBM is in scikit-learn; feature pipeline straightforward.

**Expected impact.** AUM ceiling: unchanged. Sharpe: +0.0 to +0.10 expected (small but free).

---

## 3. Free / cheap alt-data sources surveyed (2024-26)

The user asked about free/cheap data. Surveyed and rejected most as not directly applicable to capacity expansion:

| Source | Cost | Useful for v8a? |
|--------|------|------------------|
| FRED API (FRED) | free | yes — already used for HY-OAS proxy in EXP-3100 |
| Cboe DataShop free tier | free (limited) | partial — sample tapes for execution research only |
| Polygon.io Options Basic | $79/mo | substitutes IronVault for live; not historical |
| FINRA OATS / CAT public reports | free aggregate | no granular signal value |
| SEC EDGAR 13F | free | quarterly only; lag too long |
| News-LLM (FinBERT/OPT public weights) | free compute | unclear capacity benefit; defer |
| On-chain crypto flows | free | n/a — no crypto in v8a |

**Verdict on alt-data:** none of the surveyed free sources directly address SLV capacity. EXP-3120/3130/3140 above use only IronVault columns plus FRED HY-OAS (already free). Don't pursue alt-data for capacity; pursue substitution + laddering + selection.

---

## 4. Ranked recommendation

| Rank | EXP | Difficulty | Expected AUM lift | Expected Sharpe Δ | Recommendation |
|------|-----|-----------|--------------------|--------------------|-----------------|
| 1 | EXP-3130 (DTE ladder) | medium | $50M → ~$120M | −0.10 to −0.20 | **start here** — best lift-per-day-of-effort |
| 2 | EXP-3120 (SLV basket) | hard | $50M → $100-150M | −0.10 to +0.05 | run after EXP-3130; bigger ceiling lift but more risk |
| 3 | EXP-3140 (ML strike pick) | easy-medium | unchanged | +0.0 to +0.10 | run in parallel; small free upside, doesn't address capacity |

If only 1 week of effort is available, do **EXP-3130 first**. If 2 weeks, EXP-3130 then EXP-3120. EXP-3140 is independent and cheap — run it in background.

---

## 5. Honest limits

- All Sharpe and capacity numbers are snippet-level + estimate; PDF reads required before any sizing.
- IronVault GDX chain coverage 2019 unverified — confirm before EXP-3120.
- Capacity proxy = `daily_volume × 0.05` is the simplest available; 2024 TCA literature suggests this overstates real capacity by ~20-40% in OTM strikes. Treat capacity numbers as upper bounds.
- Rule-9 portfolio-level re-test is non-negotiable for all three.
- Per existing flag: 6 lit-review files now exist in `compass/reports/` and `compass/`. Recommend consolidation/archive — see canonical §7.
