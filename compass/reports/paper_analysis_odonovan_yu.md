# Paper Analysis — O'Donovan & Yu (2024)

**Title:** "Transaction Costs and Cost Mitigation in Option Investment Strategies"
**Authors:** James O'Donovan (CUHK Business School), Jianfeng Yu (PBC School of Finance, Tsinghua)
**SSRN:** 4806038 (also presented at EFMA Lisbon 2024)
**Source on disk:** `compass/references/odonovan_yu_2024_efma.pdf` (584 KB, 73 pages) and `_efma.txt` (157 KB extract)
**Method:** PDF extracted via pypdf; abstract, sections 1-3, Tables 1-10 read.

---

## 1. The empirical setup — what they tested

### 1.1 Data and universe

- **Universe:** equity options on individual U.S. optionable stocks (NOT ETFs, NOT index options as the primary universe — but SPX index options are used in their novel mitigation strategy).
- **Source:** OptionMetrics IvyDB, U.S. equity options.
- **Sample:** September 2003 - December 2021. **255,240** firm-month option observations after filters.
- **Strikes/maturity:** ATM call options (closest-to-spot) at month-end with at least 30 days to maturity. Buy-and-hold for one month is the main strategy; hold-to-maturity (≈ 50 days on average) is the alternative.

### 1.2 The exact strategies tested

The paper builds 24 long-short decile portfolios of **delta-hedged written call options** sorted on each of 24 firm/option characteristics (Goyal-Saretto-Zhan tradition). For each variable, the strategy:

1. At month-end, rank stocks by the variable.
2. Long top decile delta-hedged written calls, short bottom decile delta-hedged written calls.
3. Hold one month, rebalance.

**The 24 sort variables** (Section 3.1 of the paper):

```
CFV (cash-flow volatility), CH (cash holdings), DISP (analyst dispersion),
ISSUE_1Y, ISSUE_5Y, PM (profit margin), Ln(PRICE), PROFIT, TEF (total external finance),
ZS (Z-score), VOL_deviation (Goyal-Saretto vol mispricing), IVOL (Ang et al.),
AMIHUD, Size, BM (book-to-market), Stock_REV, Stock_MOM,
VTS (vol term-structure slope, Vasquez 2017), VOV (vol of vol, Ruan 2020),
Option_MOM, Option_REV, Option_Price, ILLIQ (Christoffersen et al. option illiquidity),
RN_SKEW (risk-neutral skew, Bali-Murray 2013)
```

### 1.3 Transaction-cost assumptions

Based on Heston, Jones, Khorram, Li, and Mo (2023) and Muravyev-Pearson (2020):

- **Option effective half-spread = 20.3% of quoted half-spread** ("sophisticated traders time execution").
- **Stock effective spread:** measured directly from quoted bid-ask using TAQ.
- **Quoted option bid-ask at entry: 23.90% of mid** (Table 1 Panel A, average across 255K obs).
- **Quoted option bid-ask at exit: ~57% of mid** — the asymmetric finding that drives much of the result. *Why:* at entry, options are ATM (tight spreads); at exit they are deep ITM/OTM (wider spreads).
- **Effective stock spread at entry: 0.13%** of price (Panel A).
- For SPX index options used in their novel mitigation strategy, quoted spreads are ~4.16% (Table 8) — about **one-sixth the single-stock options spread**.

### 1.4 Look-ahead bias correction (Duarte et al. 2023)

The paper applies the Duarte et al. correction: traditional filters use end-of-holding-period information. Correcting this **lowers gross returns and raises measured transaction costs** simultaneously — so naive papers double-bias their results. O'Donovan-Yu apply the corrected filter throughout.

---

## 2. The failure mode — what failed and why

### 2.1 Headline empirical result

> *"Out of 24 variables studied, 17 generate positive and significant gross returns, but none remain profitable after accounting for trading costs."* (Abstract)

| Result type | # of significant strategies (out of 24) |
|------------|----------------------------------------|
| Gross returns | **17** |
| Net of TC, monthly hold | **0** |
| Net of TC, hold-to-maturity (HtM) | a "handful" — most prominent: VOL_deviation, VOV, CH (Table 6) |
| Net of TC, HtM × low-cost universe | **highest count** — VTS, CH, VOV, VOL_deviation all become significant |
| Net of TC, novel long-only + 0.9 × short-SPX-index-option | **7 of 24** (returns 36-114 bp/month) |

### 2.2 Mechanism — why transaction costs kill the equity-option premium

**(a) The asymmetric entry-exit spread cliff** (Section 2.4):
- Entry spread on options: ~24% of mid.
- Exit spread one month later: ~57% of mid (deep-ITM/OTM widens spreads).
- Entry-exit asymmetry means the cost of *closing* the position is more than 2× the cost of *opening* it. The 20.3% effective-spread assumption applied to the 57% exit quote → exit cost alone is comparable to gross signal magnitude.

**(b) Turnover compounding for daily delta hedge** (Section 4):
- Daily delta-hedge raises gross Sharpe via variance reduction (e.g., VOV strategy: 2.01 → 3.36).
- *But* daily-hedged strategies pay stock-trading costs ~30 days / month vs ~1 day for monthly-hedge.
- After realistic stock TC (Heston et al. 2023), **all the Sharpe lift from daily-hedging is wiped out**. Stock TC ≈ 50% of total TC when daily-hedged.
- "Approximately equal contribution to total transaction costs from stock trades and option trades" — Section 1, paragraph 4.

**(c) Determinants of TC** (Table 5, time-series regression of portfolio TC on macro/market variables):
- **VIX** and **Baker-Wurgler sentiment index** are the only robust elevated-TC predictors.
- → high-VIX regimes have **both** elevated gross returns *and* elevated transaction costs; the TC inflation often dominates.

**(d) Look-ahead bias's interactive effect:**
- "Look-ahead bias and transaction costs have an interactive effect on delta-hedged call option returns" — Section 1.
- Filters at exit date upward-bias gross returns *and* downward-bias measured TC simultaneously. Earlier literature (pre-Duarte 2023) overstates net returns from both directions.

### 2.3 Verbatim summary statement

> *"None of the 24 studied long-short portfolios deliver returns net of transaction costs that are statistically significant for our most direct comparison strategy."*

---

## 3. v8a stream-by-stream evasion check

For each v8a stream, two questions: **(Q1) Is it in O'Donovan-Yu's tested universe?** and **(Q2) Does it apply or evade their cost-mitigation prescriptions?**

| Stream | Universe match? | Premium direction | Hold-to-mat? | Universe filter? | Daily delta-hedge? | TC-failure-mode exposure |
|--------|-----------------|-------------------|--------------|------------------|---------------------|--------------------------|
| **exp1220** SPY 28DTE PCS | Closest to their SPX novel strategy (index, not equity) | **Sell** premium (we are net short) | Closes ~7DTE early-expire (NOT HtM) | SPY (low-cost ETF, ~4-6% spread vs 24% equity) | No (vertical structure self-hedges) | **Partial:** we use the cheap-universe insight automatically (SPY ≈ SPX cousin); we do **not** hold to expiration — we exit at 50% profit or short-DTE, hitting the 57%-exit-spread tax |
| **qqq_cs** | Same as exp1220 (ETF index option, sell side) | Sell | Same as exp1220 | QQQ — similar to SPY | No | Same as exp1220 |
| **xlf_cs / xli_cs** | NOT tested directly (sector ETFs) — extrapolation only | Sell | Early-exit | Sector ETFs have **wider** spreads than SPY/QQQ; closer to single-stock spread regime | No | **Most exposed:** the universe filter benefit is partial; entry-exit spread asymmetry is a real risk |
| **gld_cal / slv_cal** | NOT tested (commodity ETFs, calendars not delta-hedged-write) | Calendar (sell front, buy back month — different P&L mechanism) | Front-month exits at expiry; back-month rolled | GLD spread is moderate; SLV is **wider** | No | **Different mechanism:** P&L driven by term-structure decay, not gross premium captured. O'Donovan-Yu findings do NOT directly apply |
| **cross_vol** | NOT tested | Vol-arb between underlyings | N/A | N/A | N/A | Out-of-scope for this paper |
| **v5_hedge** | Closest analog to their **0.9-units-short-SPX-index-option leg** | **Buy** SPX puts | Held through expiry | SPX option (their cheapest universe) | No | **Direct match to their winning prescription** — long-vol hedge sleeve fixes vol-beta, exactly the role their 0.9× SPX short leg plays |

### 3.1 Where v8a is structurally aligned with their cost-mitigation playbook

1. **Cheap-universe filter**: 6 of 8 streams trade ETF options (SPY, QQQ, XLF, XLI, GLD, SLV), where quoted spreads are ~4-15% vs 24% on single-stock equity options. We get most of the universe-filter benefit by construction.
2. **SPX vol-hedge sleeve**: v5_hedge plays approximately the role of O'Donovan-Yu's "0.9 units short SPX index option" — neutralising vol-beta of the short-premium book and generating uncorrelated tail alpha. This is the closest known v8a-paper alignment.
3. **Selling premium, not buying**: Their long-only-restoring-significance result requires going long the top decile (i.e., the side most exposed to the volatility risk premium when short). v8a is on that side directly — exp1220/qqq_cs/xlf_cs/xli_cs are all short-volatility.
4. **Not delta-hedging daily**: Vertical structures (credit spreads, calendars) self-hedge at the structure level. We avoid the stock-TC dominance documented in their daily-hedge results.

### 3.2 Where v8a is exposed to their failure modes

1. **Early exit on credit spreads** — we close at 50% profit or 7-DTE, both points where the long leg is deep OTM with very wide spreads. The 57% exit-spread cliff is a live concern. This is the single most actionable cost-finding: see EXP-3180 below.
2. **Sector ETF spreads (XLF/XLI)** are wider than SPY/QQQ; 2270 already documented slippage on these — the paper provides theoretical justification for that observation.
3. **High-VIX TC inflation** (Table 5): in stressed regimes both returns *and* costs spike. v8a regime-aware sizing partly addresses this, but does not gate explicitly on TC.
4. **Look-ahead bias risk** (Duarte et al. 2023): our backtests use IronVault chains with end-of-day data. We should re-verify our filter rules don't inadvertently use exit-date information.

---

## 4. Translatable cost-mitigation techniques

The paper provides three techniques that recover net-of-TC significance, in increasing power:

### Technique A — Low-cost universe filter (Section 3.3.1)
- Restrict universe to bottom four deciles of bid-ask-spread (their relative version of Heston et al.'s 10% cap).
- v8a equivalent: already implicitly applied (we exclude wide-spread chains via volume/OI screens). **Action:** make the filter explicit and verify the threshold matches their bottom-4-decile rule.

### Technique B — Hold-to-maturity (Section 3.3.2)
- Hold options until expiration; pay only entry-side TC, no exit-side TC.
- Avoids the 57%-exit-spread cliff entirely.
- Recovers a handful of strategies (VOV, CH, VOL_deviation) to net-significance.
- v8a equivalent: We currently exit at 50% profit or 7DTE. **Action:** test "hold to expiration" variant (let winners ride to 0DTE) — see EXP-3180.

### Technique C — Long-only top decile + 0.9 × short SPX index option (Section 3.3.4)
- Restores 7 of 24 strategies to net-significance with returns **36-114 bp/month** (economically significant).
- Mechanism: top-decile short-vol load is concentrated in market vol-beta; subtracting 0.9 × SPX vol-hedge leaves an alpha that is orthogonal to broad-market vol risk.
- Replaces a portfolio of single-stock-option positions (high TC) with one index-option position (low TC) to neutralise vol-beta.
- v8a equivalent: **v5_hedge already plays this role** for our short-premium portfolio. **Action:** quantify the v5_hedge weight relative to the 0.9-unit prescription — see EXP-3160.

---

## 5. Testable hypotheses for the v8a backtest framework

| EXP # | Hypothesis | Effort | Pre-registered metric | Decision rule |
|-------|-----------|--------|------------------------|----------------|
| **EXP-3180** | Hold credit spreads to expiration instead of 50%-profit exit. Captures 100% of theta and avoids the 57%-exit-spread cliff. | 1 day | Net Sharpe of exp1220 with HtM exit vs 50%-profit exit, 2019-2024 IronVault | Adopt HtM if net-Sharpe lift ≥0.3 *and* max-drawdown does not increase >25%. |
| **EXP-3220** | Universe filter — restrict v8a stream candidates to bottom-4-decile of quoted bid-ask spread. Approximates the Heston 10% cap. | 1 day | Compare gross-vs-net Sharpe gap before and after the filter for XLF/XLI specifically | Apply filter if it tightens the gap by ≥20% with <10% AUM-capacity loss. |
| **EXP-3190** | VIX/sentiment TC gate — at VIX ≥ 75th-pctl OR Baker-Wurgler sentiment ≥ 75th-pctl, scale stream sizing 0.5×. Mirrors their Table 5 finding that these two regimes inflate TC. | 2 days | Net Sharpe with vs without the TC gate; tail-risk metrics unchanged | Adopt if net-Sharpe lift ≥0.2 *and* CVaR-95 does not worsen. |
| **EXP-3160** | Vol-hedge sizing audit — measure v5_hedge dollar-vega vs aggregate dollar-vega of exp1220+qqq_cs+xlf_cs+xli_cs. Confirm we are at or near the 0.9-unit O'Donovan-Yu prescription. | 0.5 day | Vega-ratio time series; verify ≥0.7 and ≤1.1 for the 2019-2024 sample | If outside band, re-tune v5_hedge weight in EXP-3170. |
| **EXP-3154** | Realistic-fill re-test of v8a — re-run net Sharpe using O'Donovan-Yu's 20.3%-of-half-spread cost (not our current bid+slippage model). Reconciles our TC model against the literature standard. | 1 day | Difference in net Sharpe between 20.3%-rule and current TC model | If gap >0.5 Sharpe, escalate TC-model audit. |
| **EXP-3155** | Look-ahead bias audit — verify our IronVault data filters don't use exit-date information (zero-volume-on-exit, max-spread-on-exit, etc.). Apply Duarte et al. 2023 procedure. | 1 day | List of filters applied; flag any that depend on exit-date data | If any flagged, re-run regression with corrected filters and report Sharpe delta. |

### 5.1 Recommended sequencing

1. **EXP-3155** first (cheapest, highest insurance value — verifies our prior backtests are not overstated).
2. **EXP-3180** second (if HtM is significantly better, this is a low-friction implementation change with direct mechanistic backing from the paper).
3. **EXP-3160** third (audits an existing structural alignment; confirms v5_hedge is approximately the right size).
4. **EXP-3154** fourth (cost-model reconciliation).
5. **EXP-3190** and **EXP-3220** are optional optimisations after the audit set is clean.

---

## 6. Summary for v8a planners

**The good news.** v8a is approximately the O'Donovan-Yu mitigation playbook by construction:
- ETF universe (cheap-universe filter ≈ already applied).
- Selling premium, not buying (long-only top-decile prescription ≈ matched).
- v5_hedge sleeve (0.9× SPX index option ≈ matched).
- Vertical structures avoid daily delta-hedging stock-TC dominance.

**The bad news.** Three v8a design choices are exposed to their failure modes:
- Early exit at 50% profit hits the 57%-exit-spread cliff (EXP-3180 fix).
- XLF/XLI sector ETFs sit closer to single-stock TC regime than SPY/QQQ (EXP-3220 fix).
- Our TC model is not directly the 20.3%-half-spread literature standard (EXP-3154 reconciliation).

**The structural insight.** EXP-3150 already showed v8a edge survives post-2020. O'Donovan-Yu provide the *mechanism*: most equity-option strategies fail because of TC, but v8a is structured to avoid the dominant TC failure modes. Confirming this story rigorously requires the EXP-3155/EXP-3180/EXP-3160 audit chain.

**The single most-actionable test.** EXP-3180 (hold-to-expiration on exp1220) — both the mechanism is mechanistically clear (avoid the 57%-exit spread), implementation is trivial, and the paper explicitly identifies HtM as the most-effective simple cost-mitigation strategy.

---

## 7. Companion document

Joint analysis with Dew-Becker & Giglio: see `compass/reports/paper_analysis_dew_becker_giglio.md` and the earlier joint synthesis at `compass/reports/pdf_analysis_dew_becker_odonovan.md`.
