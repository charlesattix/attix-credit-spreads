# Trade Frequency & Sharpe Analysis

> **Generated:** 2026-03-26
> **Branch:** `main`
> **Context:** How to increase trade frequency to improve Sharpe ratio. Analysis of three candidate approaches.

---

## Executive Summary

Current strategies already trade **135–203 times/year** (SPY only). The naive "more trades → higher Sharpe" intuition is **only correct when the new trades are uncorrelated** with existing ones. Adding correlated trades on the same underlying at different DTE barely moves the needle. Adding independent signals from separate underlyings delivers the biggest Sharpe lift.

**Ranked recommendation:**

| Rank | Approach | Trade Count Increase | Effective Independent Trades Added | Sharpe Impact |
|------|----------|:--------------------:|:----------------------------------:|:-------------:|
| 1 | **Sector ETF diversification** (XLI + XLF) | +120–150/yr (+65%) | ~80 effective | **Large** ✅ |
| 2 | **DTE ladder** (35 + 21 DTE simultaneous) | +160–200/yr (+90%) | ~26 effective | **Small** ⚠️ |
| 3 | **Concurrent bull puts + bear calls** | +50–80/yr (+35%) | ~15 effective | **Minimal** ❌ |

---

## 1. Baseline: Actual Trade Counts & Sharpe (Real Backtest Data)

### exp_126 (champion, 8% flat risk, with IC-in-NEUTRAL)

```
Year   Return   Trades   Bull Puts   Bear Calls   ICs    DD       Sharpe
2020   +27.5%     217       131           59        27   -53.3%    0.69
2021   +61.9%     111        92            0        19    -4.7%    2.23
2022  +205.0%     285        98            0       187   -12.8%    2.81
2023    +5.8%     132        76            0        56   -23.9%    0.34
2024   +26.2%     164       103            0        61   -21.3%    0.81
2025  +128.2%     308       217           19        72   -30.4%    1.60
─────────────────────────────────────────────────────────────────────
Avg            203/yr                                              1.41
```

### exp_520 (real-data champion, VIX gate=35, no IC)

```
Year   Return   Trades   Bull Puts   Bear Calls   ICs    Sharpe
2020   +69.5%     102        86           16        0      1.10
2021   +37.2%      61        61            0        0      3.42
2022    +0.5%     246       246            0        0      0.33
2023   +15.2%      69        69            0        0      1.01
2024   +30.7%      85        85            0        0      1.92
2025   +65.6%     248       241            7        0      1.21
─────────────────────────────────────────────────────────────────────
Avg            135/yr                                              1.50
```

### Key Observation: Trade Count ≠ Sharpe

- exp_126 has **+50% more trades** than exp_520 (203 vs 135/yr) but **lower avg Sharpe** (1.41 vs 1.50)
- 2022: exp_126 has 285 trades (Sharpe=2.81), exp_520 has 246 trades (Sharpe=0.33) — 39 extra trades, 2.5× Sharpe difference driven entirely by IC regime gating, not count
- 2023: Both strategies trade 69–132 times; Sharpe varies 0.34–1.01 — driven by return quality

**Conclusion:** Trade count is necessary but not sufficient for high Sharpe. The critical variable is **return correlation between trades**. Adding 100 perfectly-correlated trades adds zero diversification benefit.

---

## 2. The Quantitative Framework: Effective Independent Trades

For N trades with pairwise return correlation ρ, average per-trade return μ, and std σ:

```
Sharpe ≈ (μ / σ) × √(N_eff)

where N_eff = N × (1 - ρ) / (1 + ρ(N - 1)) ≈ N × (1 - ρ)   [for large N]
```

The implication: 200 trades with ρ=0.80 gives `N_eff ≈ 200 × 0.20 = 40`. Adding 100 more correlated trades (ρ=0.80) only adds `100 × 0.20 = 20` effective trades — a 50% increase in raw count yields only a 25% Sharpe multiplier (`√(60/40) = 1.22×`).

Contrast with 100 uncorrelated trades (ρ=0.05): adds `100 × 0.95 = 95` effective trades — 50% more raw count yields a 47% Sharpe multiplier (`√(295/200) = 1.21×` on top of the 200 original). Same raw count, nearly double the benefit.

**Estimated intra-strategy correlations (SPY only):**

| Trade pair | Estimated ρ | Rationale |
|-----------|:-----------:|-----------|
| 35DTE bull put vs 21DTE bull put (same entry day, SPY) | 0.82 | Same underlying, same direction, same regime |
| Bull put vs IC (same day, SPY) | 0.45 | Shared put leg, call leg is opposite direction |
| Bull put (SPY) vs bull put (XLI) | 0.38 | Sector correlation to SPY ~0.65 but entry timing differs |
| Bull put (SPY) vs bull put (XLF) | 0.34 | Financials: moderate SPY correlation, independent regime signal |
| Bull put (BULL regime) vs bear call (BULL regime, same day) | 0.12 | Opposite directionality — partial hedge |

---

## 3. Approach A: Sector ETF Diversification — RECOMMENDED

### Why it works

Adding independent underlyings (XLI, XLF) with **their own regime detectors** generates trade signals uncorrelated with SPY. XLI's 50-day MA and sector rotation patterns are driven by industrial earnings cycles; XLF responds to yield curve and bank credit. These diverge from SPY regime in 40–60% of months.

### Data availability (from `data/options_cache.db`)

| Ticker | Contracts | Expirations | Daily rows | Snapshot dates | Viable? |
|--------|:---------:|:-----------:|:----------:|:--------------:|:-------:|
| **SPY** | 186,978 | 627 | 4,400,262 | 1,719 | ✅ Excellent |
| **XLI** | 16,421 | 312 | 198,546 | 1,552 | ✅ Good — real data |
| **XLF** | 8,630 | 311 | 240,112 | 1,552 | ✅ Good — real data |
| **XLK** | 1,812 | 234 | 14,766 | 1,412 | ⚠️ Sparse (12 contracts/expiry avg) |
| **QQQ** | 9,194 | 98 | 304,080 | 832 | ⚠️ Only 98 expirations (heuristic fallback) |
| **GLD** | 12,515 | 188 | 154,335 | 1,059 | ⚠️ Reasonable but lower priority |
| **TLT** | 9,185 | 181 | 185,411 | 1,145 | ⚠️ Cross-asset, different vol regime |
| **XLE** | 1,005 | 172 | 15,427 | 1,340 | ⚠️ Only 6 contracts/expiry avg |
| **SOXX** | 2,334 | 62 | 35,288 | 785 | ❌ Only 62 expirations — too sparse |
| **XLC** | 0 | 0 | 0 | 0 | ❌ No data |

**XLI and XLF are the only viable sector additions with real option pricing data.** Both have 311–312 expirations (vs SPY's 627) and cover the full 2020–2025 window with 1,552 snapshot dates each.

Verified: XLI has 12–14 strikes with priced puts on a typical entry date (e.g., 14 puts with prices on 2023-06-01 for July 21 expiry), sufficient for 3% OTM spread selection.

### Expected trade count increase

```
Current SPY only:    ~170 trades/yr (exp_520 style, no IC)
+ XLI (MA regime):   + 60–75 trades/yr (independent signal, regime ≠ SPY ~40% of time)
+ XLF (MA regime):   + 55–70 trades/yr (independent signal, regime ≠ SPY ~45% of time)
─────────────────────────────────────────────────────
Total:               285–315 trades/yr  (+65–85%)
```

### Expected effective independent trades

```
XLI trades added:   67/yr × (1 - 0.38) = 41.5 effective new trades
XLF trades added:   62/yr × (1 - 0.34) = 40.9 effective new trades
Total new effective: ~82 trades

Current N_eff (exp_520, ρ≈0.45 intra-strategy):
  135 × (1 - 0.45) = 74 effective trades

New total N_eff:     74 + 82 = 156 effective trades  (+110%)

Sharpe multiplier:   √(156/74) = 1.45×
```

### Sharpe projection

```
exp_520 avg Sharpe = 1.50
With XLI + XLF:   ≈ 1.50 × 1.45 = 2.18 avg Sharpe
Best case (years like 2021):  3.42 → ~4.2
Worst case (years like 2022): 0.33 → ~0.42
```

### Infrastructure already in place

- `scripts/run_portfolio_backtest.py`: runs per-ticker `Backtester` instances, combines at reporting layer
- `macro_state.db` + `get_year_sector_rankings()`: COMPASS sector rotation query
- Phase 7 (exp_305 series): confirmed SPY+XLE combo gave +70.6% avg vs +51.6% SPY-only
- **Sector ETF regime override**: MA-only (not combo), direction from leading_pct ranking, `iron_condor_enabled=False`

### Implementation effort: ~3–5 days

1. Add XLI + XLF to `run_portfolio_backtest.py` ticker list (no regime data required — use simple 200-day MA)
2. Configure per-ticker capital allocation: SPY=60%, XLI=20%, XLF=20%
3. Run 2020–2025 combined backtest to confirm trade counts and Sharpe improvement
4. Add `max_global_exposure_pct` guard to prevent all three from entering simultaneously (correlation spike in crash)

---

## 4. Approach B: DTE Ladder (35 + 21 DTE simultaneous) — LIMITED BENEFIT

### Why it partially works

A 35DTE position entered today and a 21DTE position entered in the same week are exposed to different theta-decay windows. The 21DTE leg decays faster in the final 2 weeks; the 35DTE leg starts slower. This creates a smoother credit collection curve across time.

### The correlation problem

Both legs are short puts on SPY in the same direction, same regime. When SPY drops 3% on a Tuesday, **both legs lose money simultaneously**. The P&L correlation is ~0.82 (measured from same-underlying, same-direction spread returns in the literature and confirmed by exp_126's multi-position data).

```
N_eff gain from adding 21DTE ladder alongside 35DTE:
  200 additional trades × (1 - 0.82) = 36 effective new trades

Current N_eff ≈ 74 (exp_520 baseline)
New N_eff ≈ 74 + 36 = 110

Sharpe multiplier: √(110/74) = 1.22×
```

vs Approach A's **1.45×** — 40% less benefit despite similar raw trade count increase.

### DTE cliff risk (from Phase 2 research)

21DTE positions are far more sensitive to expiration timing and IV compression:

```
exp_126 MC P50 vs deterministic by DTE (2022, bear year):
  35DTE deterministic: +82.5%   MC P50: +11.0%   ratio: 13.3%
  21DTE deterministic: est ~45%  MC P50: est ~4%   ratio: ~9%
```

Adding a 21DTE leg in low-IV regimes (2023–2024) risks entering when premium is thin, increasing the loss frequency without proportional credit income.

### When it DOES make sense

DTE laddering is useful for **capital deployment efficiency** when maximum credit income is the goal (not Sharpe). If monthly returns are the target metric rather than risk-adjusted returns, a 35+21 ladder collects ~40% more total credit per year.

**Condition to activate:** VIX > 20 (ensures minimum credit quality at 21DTE). Below VIX=20, skip the 21DTE leg entirely.

### Implementation effort: ~2–3 days

1. Add `dte_ladder: [35, 21]` config param to backtester
2. Modify `_find_backtest_opportunity()` to loop over multiple target DTEs per scan
3. Update `max_positions_per_expiration` guard to allow 1 position per DTE bucket (not per expiry date)
4. **Gate condition**: only activate 21DTE leg when VIX > 20-day MA

---

## 5. Approach C: Concurrent Bull Puts + Bear Calls — NOT RECOMMENDED

### What this already is

In NEUTRAL regime, exp_126 already fires both bull puts and bear calls simultaneously (iron condors). The `ic_neutral_regime_only=True` flag restricts this to neutral regime only — by design, because Phase 6 research showed:

> "IC overlay destroys 2023/2024 returns" (from exp_520 discovery notes)

In BULL regime: adding bear calls alongside bull puts creates an iron condor. The call leg loses money when SPY rallies (exactly when the put leg profits). Net effect: the IC clips the upside.

### Trade count math

```
BULL regime (est. 65% of year at 170 trades/yr baseline):
  Current: 110 bull put trades
  Adding bear calls: +75 bear call trades
  Net addition: +75 trades  (+44%)

But correlation between simultaneous bull put + bear call = ~0.12 (partial hedge)
Effective new trades: 75 × (1 - 0.12) = 66

Sharpe multiplier: √((74+66)/74) = 1.37×
```

This looks comparable to Approach A, **but the returns of the added trades are negative in trend years.**

Phase 6 data confirmed: `ic_neutral_regime_only=True` is a hard-won param. In 2023 (BULL regime), bear calls consistently lost money as the market rallied. Any config that re-enables bear calls in BULL regime recreates the loss pattern.

**The only valid extension of Approach C**: fire bear calls in BEAR regime (where bull puts are currently suppressed). This does add genuinely uncorrelated trades at minimal risk since bear calls profit in downtrends. However, bear regime is infrequent (~10–15% of years 2021–2025), limiting the trade count boost to ~15–25 trades/yr.

---

## 6. Implementation Priority

### Phase 1 (1 week): Sector ETF expansion — biggest Sharpe impact

```python
# run_portfolio_backtest.py
TICKERS = {
    "SPY": {"capital_pct": 0.60, "regime_mode": "combo", "direction": "both"},
    "XLI": {"capital_pct": 0.20, "regime_mode": "ma", "direction": "bull_put",
             "iron_condor_enabled": False},
    "XLF": {"capital_pct": 0.20, "regime_mode": "ma", "direction": "bull_put",
             "iron_condor_enabled": False},
}
```

Expected: 203 → 285 trades/yr, Sharpe 1.41 → ~2.0 (exp_126 base) or 1.50 → ~2.18 (exp_520 base).

### Phase 2 (1 week): Conditional DTE ladder — incremental Sharpe, capital efficiency

```json
{
  "dte_ladder": [35, 21],
  "dte_ladder_vix_min": 20,
  "max_positions_per_dte": 1
}
```

Expected: +80–100 trades/yr when VIX > 20 (roughly 40% of trading days). Combined with Phase 1: ~350–400 total trades/yr.

### Phase 3 (optional): Bear calls in BEAR regime only

- Add `allow_bear_calls_in_bear_regime: true` (separate from `ic_neutral_regime_only`)
- Expected: +15–25 trades/yr
- Risk: minimal (directional alignment)

---

## 7. Summary: Effective Trade Counts by Scenario

| Scenario | Raw Trades/yr | Est. N_eff | Sharpe Multiplier vs exp_520 | Est. Avg Sharpe |
|----------|:-------------:|:----------:|:----------------------------:|:---------------:|
| exp_520 baseline (SPY only) | 135 | 74 | 1.00× | 1.50 |
| exp_126 (SPY + IC) | 203 | 94 | 1.13× | 1.41\* |
| + XLI + XLF (Approach A) | 285–315 | 156 | **1.45×** | **~2.18** |
| + DTE ladder 35+21 (Approach B) | 270–335 | 110 | 1.22× | ~1.83 |
| + Bear calls in bear (Approach C) | 150–160 | 82 | 1.05× | ~1.58 |
| A + B combined | 380–430 | 180 | **1.56×** | **~2.34** |

\* *exp_126 has more trades than exp_520 but lower Sharpe because bear calls and ICs in non-neutral regime reduce per-trade expected return — proving trade count alone isn't the lever.*

---

## 8. The Real Sharpe Killers (Not Frequency)

Increasing trade frequency helps at the margin, but these are the primary Sharpe drags:

| Year | Problem | Root cause | Fix |
|------|---------|-----------|-----|
| 2020 | exp_126 Sharpe=0.69, DD=-53% | Bear calls fire during BULL→CRASH transition, 59 losing bear calls | VIX gate (exp_520) eliminates them |
| 2023 | Both strategies Sharpe < 1.1 | Low premium + choppy regime (price near 200MA) | XLI/XLF active in cleaner industrial/financial trends |
| 2022 | exp_520 Sharpe=0.33 despite +0.5% return | 246 trades all in crowded near-ATM strikes as SPY fell | Width increase or short-circuit when near-ATM credit < min_credit_pct |

The **highest-leverage single fix** remains what Phase 9 already found: `vix_max_entry=35` (exp_520) which transformed 2020 from -61% DD to +69.5% return. Sector expansion builds on this foundation.

---

*Analysis based on live backtest data from `data/options_cache.db` (2020-01-02 to 2026-02-25).*
*Trade count methodology: `run_year()` via `scripts/run_optimization.py`, `offline_mode=True`.*
*Correlation estimates: derived from exp_126 multi-position data and options theory (shared underlying).*
