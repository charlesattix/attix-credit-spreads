# IronVault-Testable Hypotheses from 2024-2026 Options Literature

**Date:** 2026-04-29
**Author:** research synthesis (Claude)
**Dataset scope:** IronVault — SPY / QQQ / XLF / XLI / GLD / SLV options chains, 2019-2024
**Deliverable form:** 5 hypotheses, each (a) tied to a specific 2024-2026 paper, (b) mapped to a concrete backtest design that runs on existing IronVault columns.

---

## 0. Provenance & relationship to prior reviews

Four prior lit-review documents already exist on disk (see appendix). This file is **not another lit review**. It extracts the subset of findings that can actually be backtested on IronVault as-is, and writes each as a falsifiable H0/H1 with a defined success metric. If a paper's claim cannot be tested without data we don't have (e.g., dealer-inventory feed, NBBO microstructure tape, signed order flow), it is excluded here even if cited elsewhere.

All paper claims below are **search-snippet level** unless marked `[PDF read]`. Do not propagate Sharpe numbers from snippets into MASTERPLAN without reading the underlying paper.

---

## 1. Top 5 hypotheses (ranked by expected lift × ease of test)

| # | Hypothesis | Paper anchor | IronVault columns needed | Expected effort | Expected lift on v8a |
|---|------------|--------------|--------------------------|-----------------|----------------------|
| H1 | Post-2022 VRP percentile gate raises hit-rate on SPY/QQQ put credit spreads | Cboe / Israelov 2024 update on VRP regime shift | IV ATM 30D, RV20D realised, daily | 1 day | freq ↓ ~30%, Sharpe +0.05–0.10 |
| H2 | Dealer-GEX sign predicts next-day SPY straddle return | Brogaard/Han/Won 2024-25 GEX papers; Barbon-Buraschi 2024 | OI × delta × gamma per strike (we have OI + greeks) | 3 days | gate, not standalone — ~+0.10 Sharpe |
| H3 | Cross-sectional IV-rank long/short across our 6 underliers earns positive carry | Christoffersen/Goyenko style cross-sectional options factors, 2024-25 | IV ATM 30D per ticker, daily | 2 days | new stream candidate, target Sharpe 0.6-1.0 standalone |
| H4 | Term-structure slope (IV30/IV90) regime-switches credit-spread DTE choice | Andersen et al. 2024 vol-of-vol term structure | IV at multiple expiries per ticker | 2 days | retunes existing streams; lift uncertain |
| H5 | Realised semi-variance asymmetry (downside RV − upside RV) is a better short-vol filter than VIX9D/VIX ratio | Patton/Sheppard semivar work, 2024 extensions | 1-min or 5-min underlying returns 2019-2024 | 4 days | gate, ~+0.05 Sharpe; mostly drawdown reduction |

---

## 2. Hypothesis details

### H1 — Post-2022 VRP percentile gate

**Paper anchor.** Israelov & co-authors and the Cboe research desk have published 2024 notes arguing that the structural VRP shrank after 2022 due to (a) 0DTE supply, (b) systematic vol-selling ETFs (JEPI, QYLD, SVOL), (c) lower realised-implied wedge in regime-2 (low-VIX) days. Snippet-level claim: VRP > 75th percentile days carry most of the post-2022 short-vol P&L; sub-50th-percentile days are roughly zero-EV after costs.

**Falsifiable form.**
- H0: SPY put-credit-spread 28-DTE returns are independent of pre-trade VRP percentile (rolling 252d window).
- H1: top-quartile VRP days have mean per-trade P&L > bottom-quartile by ≥ 0.3× contract premium.

**IronVault test design.**
1. Compute VRP_t = IV_ATM_30D_t² − RV_20D_t² (standard squared form). We already have both inputs.
2. Rolling 252d percentile rank.
3. Re-run exp1220 with a gate: enter only when VRP_pct ≥ 50 (and again ≥ 75 as a sensitivity).
4. Walk-forward, OOS 2023-2024.
5. Report: trades/year, win-rate, Sharpe, max DD, vs ungated baseline.

**Watchpoints.**
- Bug-6 risk: if we sweep the percentile threshold on the same window we used to score it, the result will lie. Pre-register the threshold (50 and 75) before any OOS run.
- Rule-9: if H1 holds standalone, must re-test inside the v8a portfolio — covariance with cross_vol and v5_hedge will eat some of the lift.

**Decision criterion.** Accept H1 if portfolio-level Sharpe rises by ≥ 0.05 with no DD increase OOS.

---

### H2 — Dealer-GEX sign as a directional filter

**Paper anchor.** Two 2024-25 strands: (i) academic work on dealer gamma-positioning and intraday SPX mean-reversion (Barbon & Buraschi line), (ii) practitioner notes that negative-GEX days have ~2× the realised vol of positive-GEX days. Snippet claim: sign of net dealer GEX at SPX open predicts that day's straddle P&L sign with > 55% accuracy.

**Falsifiable form.**
- H0: signed daily SPY straddle return is independent of net-GEX sign computed at prior close.
- H1: |E[ret | GEX<0] − E[ret | GEX>0]| > 0 with t > 2.

**IronVault test design.**
1. We do **not** have dealer position feed. We can only **proxy** GEX from listed-options OI × Γ × spot² × 100, summed across strikes, assuming dealers are short calls / long puts (the standard heuristic).
2. Compute proxy_GEX_t at each daily close from IronVault chain.
3. Bin next-day SPY ATM straddle simulated P&L by sign and magnitude of proxy_GEX.
4. If signal exists in proxy form, decide whether to (a) use as exp1220 gate, (b) build new straddle stream, or (c) shelve pending real GEX feed.

**Watchpoints.**
- The proxy is **directionally fragile** — it ignores OTC, ETF-creation flows, and dealer-hedging in futures. Treat positive results as suggestive, not conclusive.
- Provenance: I have **not** read the Barbon-Buraschi PDF; the 55% accuracy figure is snippet-level. Verify before sizing.

**Decision criterion.** Accept H2 only if proxy-GEX edge is robust to (i) excluding OPEX week, (ii) sub-period 2019-21 vs 2022-24, (iii) re-test inside v8a.

---

### H3 — Cross-sectional IV-rank long/short

**Paper anchor.** Goyenko, Christoffersen, and co-authors have published several 2024-25 pieces on cross-sectional options return predictors (IV-rank, skew, term-slope). Snippet claim: long top-decile IV-rank short bottom-decile IV-rank (delta-hedged straddles) earns 6-9% annualised on US equities universe, Sharpe 0.7-1.0.

**Falsifiable form.**
- H0: average forward 1-month delta-hedged straddle return is equal across IV-rank quintiles in our 6-ticker universe.
- H1: top minus bottom IV-rank straddle portfolio has positive mean return, t > 2.

**IronVault test design.**
1. Each Friday close, compute IV_rank_252d for each of {SPY, QQQ, XLF, XLI, GLD, SLV}.
2. Build a 2-2 sort: long top-2 IV-rank tickers, short bottom-2, equal vega.
3. Hold to next Friday, delta-hedge daily at close (we already have spot bars).
4. Walk-forward 2019-2024.

**Watchpoints.**
- 6 tickers is a tiny cross-section. Effects published on 100s of names may not survive.
- Vega-equalising across SPY (deep, tight) and SLV (wider) costs more than expected; tx-cost model must be honest.
- Rule-1: no synthetic data — if any ticker has gappy chains, drop the day, don't fill.

**Decision criterion.** Accept H3 if standalone Sharpe ≥ 0.5 net of tx costs, AND portfolio-level Sharpe rises ≥ 0.10 when added to v8a.

---

### H4 — Term-structure slope as DTE selector

**Paper anchor.** Andersen, Bondarenko, et al. 2024 papers on vol-of-vol term-structure and how the IV-curve slope flips in stress. Snippet claim: when IV30/IV90 > 1 (backwardation), short-dated short-vol underperforms; when < 0.95, long-dated short-vol underperforms.

**Falsifiable form.**
- H0: 28-DTE vs 14-DTE put-credit-spread returns do not vary with IV30/IV90 ratio.
- H1: regime-conditional DTE selection beats fixed 28-DTE OOS.

**IronVault test design.**
1. Compute IV30/IV90 daily for SPY (we have multi-expiry chain).
2. Define 3 regimes: backwardation (>1.0), flat (0.95-1.0), contango (<0.95).
3. For each regime, backtest 14, 21, 28, 35, 42 DTE put credit spreads.
4. Pre-register regime → DTE mapping, then OOS 2023-2024.

**Watchpoints.**
- Bug-6: we will be tempted to sweep DTE × regime in-sample. Pre-register or this is overfit.
- Sample size in backwardation regime is small (~10-15% of days). Confidence intervals will be wide.

**Decision criterion.** Accept H4 only if OOS Sharpe lift ≥ 0.10 over fixed-28-DTE baseline, with t > 1.5.

---

### H5 — Realised semi-variance asymmetry as short-vol filter

**Paper anchor.** Patton & Sheppard's semi-variance line, plus 2024 extensions. Snippet claim: downside RV minus upside RV (signed) is a leading indicator of vol expansion; days with high downside-RV-minus-upside-RV in past week have ~2× realised vol the following week.

**Falsifiable form.**
- H0: short-vol P&L is independent of lagged signed semi-variance asymmetry.
- H1: bottom decile of (downRV − upRV) days earn higher short-vol P&L than top decile, t > 2.

**IronVault test design.**
1. From SPY 5-min bars (do we have these in IronVault? — verify; if only daily, downsample is too coarse).
2. Compute downRV_t = Σ r_t² · 1{r_t<0} over past 5 days; upRV_t analogously.
3. Asymmetry_t = (downRV − upRV) / (downRV + upRV).
4. Gate exp1220 to skip top-decile-asymmetry days.

**Watchpoints.**
- **Data dependency:** if IronVault lacks intraday bars, this hypothesis is **deferred** — daily semi-var is too noisy.
- Likely correlated with H1 (VRP) and a VIX9D/VIX ratio — must orthogonalise before sizing.

**Decision criterion.** Accept H5 only if it adds ≥ 0.03 Sharpe **after** controlling for H1. Otherwise it's a redundant version of the VRP signal.

---

## 3. Suggested sequencing

1. **Week 1:** H1 (1 day to code) and H4 (2 days). Both run on data we already have, both inform existing streams.
2. **Week 2:** H3 (2 days). New stream candidate; runs in parallel.
3. **Week 3:** H2 with proxy-GEX (3 days). Quarantine result behind "real-GEX-feed" gate before sizing it live.
4. **Deferred:** H5, until intraday bars are confirmed in IronVault.

Total: ~8 working days for first 3 hypotheses; deliverable is an EXP-3xxx series with formal verdict notes.

---

## 4. What this list deliberately excludes

- **VolGAN / Heston-NN / generative IV-surface models** — interesting but require infra (GPU + arbitrage-free constraints) we don't have for $0; classic build-vs-buy problem; not a 1-day backtest.
- **RL hedging (SAC/PPO)** — same reason; also Rule-13 risk of backtest fantasy.
- **News-LLM signals** — would need an alt-data feed and label set we don't yet have.
- **0DTE-specific strategies** — IronVault 2019-2024 has limited 0DTE history (0DTE listing on SPX was 2022, on SPY 2023); too short for OOS.

These are documented in the broader lit reviews (paths in §5) but excluded here to keep the deliverable testable on present infrastructure.

---

## 5. Source documents and prior reviews

- `compass/reports/literature_review_2024_2026.md` (24,198 bytes) — broad cross-cut.
- `compass/reports/lit_review_2024_2026.md` (18,297 bytes) — top-15 with tier ratings.
- `compass/research_apr29_literature_review.md` (17,304 bytes) — session log version.
- `research/lit_review_2024_2026.md` (25,104 bytes) — H1-H19 hypothesis enumeration.

**Recommend (still):** consolidate the 4 above into a single canonical `compass/reports/literature_review_2024_2026.md`, archive the others under `compass/archive/lit_reviews_2026Q2/`.

---

## 6. Honest limits

- Every Sharpe / hit-rate number cited from a paper is **snippet-level** — none of the underlying PDFs has been read end-to-end in this session.
- Per Rule-13, expected live ≈ 0.5-0.7× any backtest figure achieved here.
- Per Bug-6, threshold sweeps must be pre-registered before OOS evaluation; the design notes above pre-commit thresholds intentionally.
- Per Rule-9, any single-hypothesis acceptance must be re-tested at v8a portfolio level before sizing.
