# EXP-031 Audit Review

**Reviewer:** Claude (Attix audit)
**Date:** 2026-03-12
**Subject:** EXP-031 Third-Party Audit Package — SPY Credit Spread (Bull Put) with Compound Sizing
**Verdict: REJECT as ROBUST. Correct classification: SUSPECT (0.590)**

---

## 1. Overfit Score Is Miscalculated — Hard Gate Not Applied

**This is the most critical finding.** The audit package claims a composite overfit score of **0.834 (ROBUST)**. This is incorrect.

Check C (Parameter Sensitivity) **FAILED** — the package itself acknowledges a cliff parameter (`target_dte`: 35→28 drops returns by -58%). Per MASTERPLAN Step 3.5 and the project's `scripts/validate_params.py` (lines 460-471), Check C failure is a **HARD GATE** that caps the composite score at 0.59, regardless of other checks.

**Correct computation:**
```
Raw composite: 0.833*0.25 + 1.000*0.30 + 0.692*0.25 + 0.833*0.10 + 0.694*0.10 = 0.834
Hard gate applied (C_sensitivity FAILED): min(0.834, 0.59) = 0.590
Correct verdict: SUSPECT (not ROBUST)
```

The audit package either (a) was generated before the hard gate rule was implemented, or (b) intentionally omitted it. Either way, the 0.834 ROBUST claim is wrong.

---

## 2. Walk-Forward Validation Is Gamed by Compound Sizing

The WF check shows test years consistently outperforming training averages (ratio = 0.96, 1.00, 1.00). The package calls this "the opposite of overfitting." This is misleading.

**Why it's misleading:** With compound sizing (15% of current equity per trade), later-year returns naturally inflate because:
- Wins early in the year grow the equity base
- Subsequent positions are larger (sized to 15% of the larger equity)
- Returns are measured as % of **starting** capital ($100K), not average capital deployed

This creates a mechanical bias where any strategy with a positive edge and compound sizing will show accelerating returns in later years. The WF test isn't measuring whether the **edge** persists — it's measuring whether compound interest works. It does.

**Proof:** 2025 returns are +171.9%. Jun-Sep 2025 alone contributed $136,399 (136% of starting capital). By that point, the equity was ~$200K+, meaning positions were 2-3x the starting level. The test year "outperforming" is entirely a compounding artifact.

---

## 3. DTE Cliff Is a Serious Fragility

Changing `target_dte` from 35 to 28 (a -20% perturbation) drops average returns from 63.8% to 26.9% — a **58% decline**. Changing to 31 (-10%) still drops returns by 54%.

This is disqualifying. A robust strategy should tolerate ±10-20% parameter changes with <50% return impact. The strategy's edge appears to be concentrated at the exact DTE=35 sweet spot, suggesting either:
1. A genuine but narrow market microstructure effect (possible but hard to trade reliably)
2. Data overfitting to a specific time decay profile (more likely)

**Comparison:** Our regime-adaptive champion's jitter test shows ±10% perturbation across 6 params produces returns of +23% to +43% vs base +32.7%. No cliff params. Score: 0.98.

---

## 4. Concentration Risk: 119% of Equity at Risk

With 160 trades in 2025 (one every 1.6 trading days), ~35 DTE holding period, and ~12.5 day average hold time (90% early exits at profit target), the strategy carries approximately **8 concurrent positions** on average.

At 15% equity risk per position, that's **119% of equity at risk simultaneously**. Combined with a max_contracts cap of 35, this means:
- A single correlated market crash hits all 8 positions at once
- The 2.5x stop loss fires on all positions simultaneously
- Potential loss: 8 × 15% × 2.5x = **300% of equity** in a black swan

The drawdown circuit breaker (40%) provides some protection, but it uses cash-only (not unrealized P&L, as noted in bug E2), so it fires late.

---

## 5. 2022 Bear Year — Actually Realistic

The -20.7% loss in 2022 with only 12 trades is one of the **most credible** aspects of this audit. A bull-put-only strategy in a -19.4% SPY year should absolutely lose money. The MA50 trend filter correctly kept the strategy out of most of 2022 (only 12 entries vs 107 in 2020), and the 4 losses that did occur were expensive (66.7% WR × 12 trades = 4 losses × $7,162 avg).

This is consistent with our own findings: our regime-adaptive champion returned -1.9% in 2022, also the weakest year. A pure bull-put strategy should fare worse.

---

## 6. 2021 and 2023: 100% Win Rates Are Suspicious

- 2021: 44 trades, **100% win rate**, $0 average loss
- 2023: 36 trades, **100% win rate**, $0 average loss

Zero losses across 80 credit spread trades over two full years is extremely unusual. Even in strong bull markets, credit spreads occasionally breach strikes. Possible explanations:
1. The MA50 filter plus tight OTM (3%) was exceptionally well-fitted to 2021/2023 price action
2. Mid-price execution bias (bug B3) inflated credits enough to avoid marginal losses
3. Look-ahead bias in MA filter (bug A1 — verified for MA200 but "needs verification" for MA50)

Any of these would be concerning. A 100% win rate on 44 trades should trigger independent verification of individual trade fills against Polygon bid/ask spreads.

---

## 7. 2025 Outlier Distorts Everything

| Metric | With 2025 | Without 2025 |
|--------|-----------|--------------|
| Avg return | +63.8% | **+42.2%** |
| Best year | +171.9% | +81.1% |
| WF fold 3 ratio | 1.00 | N/A |

The +171.9% in 2025 contributes 28% of the 6-year total but is driven entirely by compound sizing on an equity curve that nearly tripled. Monthly P&L in Jun-Sep 2025 ($30K-$38K/month) is 15-19x the Jan-Mar levels, reflecting position sizes that grew with equity, not growing edge.

---

## 8. Data Provenance Check

The audit package claims "Polygon.io real intraday option chain data." Verified: `options_cache.db` contains 1.3-2.0M rows per year for 2020-2025, with download progress showing "complete" status across all months. The data is present.

However, bug B3 (mid-price vs bid-price execution) and B4 (64-66% zero H/L range bars) mean the **fill quality** of this data is questionable. On a $5 spread with $0.40 target credit, even $0.10 of slippage per leg (conservative for SPX options) would eliminate 50% of credits.

---

## 9. Comparison to Our Own Results

| Metric | EXP-031 (claimed) | EXP-031 (corrected) | Our Champion |
|--------|-------------------|---------------------|--------------|
| Avg return | +63.8% | ~42% (ex-2025) | **+32.7%** |
| Overfit score | 0.834 ROBUST | **0.590 SUSPECT** | **0.870 ROBUST** |
| Worst year | -20.7% (2022) | -20.7% | **-1.9%** |
| Cliff params | target_dte | target_dte | **None** |
| WF folds passing | 3/3 (inflated) | 3/3 (compound bias) | **3/3 (clean)** |
| Jitter stability | 0.692 (FAIL) | 0.692 (FAIL) | **0.98 (PASS)** |
| Max DD | -25.4% | -25.4% | **-12.1%** |

Our regime-adaptive champion has lower raw returns but vastly superior robustness. It passes all hard gates, has no cliff parameters, and its walk-forward isn't inflated by compound sizing.

---

## 10. Summary of Red Flags

| # | Red Flag | Severity |
|---|----------|----------|
| 1 | **Overfit score hard gate not applied** — 0.834 should be 0.590 | CRITICAL |
| 2 | **DTE cliff** — ±20% param change causes 58% return drop | HIGH |
| 3 | **Walk-forward gamed by compound sizing** — mechanical inflation | HIGH |
| 4 | **119% equity at risk** simultaneously — extreme concentration | HIGH |
| 5 | **2025 outlier** — 171.9% driven by compounding, not edge | MEDIUM |
| 6 | **100% WR in 2021+2023** — 80 trades with zero losses needs verification | MEDIUM |
| 7 | **Mid-price execution** — overstates credits by 5-60% | MEDIUM |
| 8 | **Look-ahead bias** — MA50 variant not verified as fixed | MEDIUM |

---

## 11. Recommendation

**REJECT as ROBUST.** Reclassify to **SUSPECT (0.590)**.

The strategy likely has a real edge in bull markets with credit spreads, but:
- The returns are inflated 50-100% by compound sizing artifacts
- The DTE cliff makes it fragile to deploy
- The overfit score is incorrectly computed
- The walk-forward test doesn't measure what it claims to measure

If this were to be re-evaluated, it should be run with **fixed** position sizing (not compound), **bid-price** execution, and the **MA50 look-ahead bias verified**. Expected "honest" returns with these corrections: **+15-25% avg** (still good, but not 63.8%).
