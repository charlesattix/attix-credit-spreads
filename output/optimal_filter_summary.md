# Optimal Filter Summary: Win Rate Boost (EXP-305)

**Source:** `output/win_rate_boost_report.md` (2026-03-26)

---

## Best Filter: ML Threshold = 0.75

| Config | Win rate | N/yr | SR_annual |
|--------|----------|------|-----------|
| ML ≥ 0.65 (current) | 93.39% | 126.0 | 10.02 |
| **ML ≥ 0.75 (optimal)** | **93.94%** | **118.3** | **10.37** |

- Trade count reduction: 756 → 710 (6.1% fewer trades, 94% retained)
- SR_annual gain: +0.35 (+3.5%)
- Exceeds 86% win rate target: YES (93.94%)

---

## Why Not Stack Additional Filters?

Every non-ML filter tested (VIX spike, expiry week, earnings week) **reduces SR_annual** when added on top of ML ≥ 0.65 because the trade-count loss outweighs the win-rate gain:

| Stack | SR_annual | vs ML-0.65 |
|-------|-----------|------------|
| ML ≥ 0.65 + VIX spike | 9.12 | **-0.90** |
| ML ≥ 0.65 + expiry week | 9.68 | **-0.34** |
| ML ≥ 0.65 + earnings week | 8.93 | **-1.09** |
| ML ≥ 0.65 + all three | 7.88 | **-2.13** |

The ML model already captures VIX/timing/earnings signals implicitly. Adding rule-based filters on top double-penalizes trade count without enough incremental win-rate lift.

---

## Key Constraint

> Removing 10% of trades requires +0.51pp win rate just to break even on SR.
> At ML ≥ 0.65 baseline (93.39%), the VIX/expiry/earnings filters each add only +0.2–1.1pp
> while removing 17–24% of trades — a net negative.

---

## Recommendation

**Set ML threshold = 0.75** (not 0.65). Re-tune annually on the prior-year validation set.
Do NOT stack VIX spike, expiry week, or earnings week filters — they all reduce SR_annual.

For further SR improvement beyond ML threshold tuning, adding an uncorrelated second strategy
(e.g., Mode B covered puts) increases N without within-strategy correlation — higher ceiling
than any single win-rate filter.
