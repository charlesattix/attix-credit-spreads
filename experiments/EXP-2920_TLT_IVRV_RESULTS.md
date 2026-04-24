# EXP-2920: TLT IV-RV Arbitrage via MOVE Index — Results

**Date:** 2026-04-23 13:39
**Status:** KILLED
**Rule Zero:** CLEAN

---

## 1. Data Sources

| Source | Series | Coverage |
|---|---|---|
| Yahoo Finance | ^MOVE (ICE BofA MOVE Index) | 2019-2026, 4 calibration params |
| Yahoo Finance | TLT (iShares 20+ Year Treasury Bond ETF) | 2019-2026 |
| Yahoo Finance | ^VIX | 2019-2026 |
| IronVault | TLT options (via EXP-2910 cache) | 2020-2025 |
| IronVault | cross_vol trades (EXP-2020 cache) | 2020-2025 |

---

## 2. MOVE-TLT Calibration

| Parameter | Value |
|---|---|
| MOVE/TLT_RV ratio (median) | 6.108588772142741 |
| MOVE-VIX correlation | 0.1296 |
| IV-RV spread autocorrelation (1d) | 0.9793 |
| IV-RV spread autocorrelation (5d) | 0.8761 |

**Key insight:** MOVE-VIX correlation is only 0.1296, confirming MOVE captures a genuinely different volatility factor (rates vs equities). The IV-RV spread has high autocorrelation (0.9793 at 1-day), suggesting the signal is persistent and tradeable.

---

## 3. Approach A: MOVE-Filtered TLT Put Credit Spreads

Can we improve EXP-2910's Sharpe (0.76) by only entering when the IV-RV spread is elevated?

| Filter | Trades | Sharpe | TPY | Win Rate | Avg PnL |
|---|---|---|---|---|---|
| *Unfiltered (EXP-2910)* | *52* | *0.76* | *9.2* | — | — |
| z>0.0 | 25 | **0.47** | 4.7 | 92% | $89.92 |
| z>0.5 | 19 | **0.43** | 3.5 | 90% | $109.47 |
| z>1.0 | 10 | **0.36** | 1.9 | 80% | $173.40 |
| z>1.5 | 3 | too few trades | — | — | — |

---

## 4. Approach B: Standalone TLT IV-RV Mean-Reversion (Equity-Based)

Long TLT when MOVE is elevated relative to TLT realized vol (causal, 1-day lag).

| Variant | Sharpe | CAGR | Max DD | Vol | Exposure |
|---|---|---|---|---|---|
| z>0.0 | **-0.73** | -4.6% | 47.5% | 11.8% | 55% |
| z>0.5 | **-0.45** | -0.6% | 32.3% | 10.2% | 39% |
| z>1.0 | **-0.54** | -0.3% | 25.3% | 8.3% | 22% |
| z>-0.5 | **-0.44** | -2.0% | 40.2% | 12.9% | 67% |
| buy_hold_tlt | **-0.33** | -2.2% | 48.4% | 16.6% | 100% |

---

## 5. Approach C: MOVE Overlay for Existing cross_vol Arb

Does filtering cross_vol trades by MOVE regime improve Sharpe?

**Baseline cross_vol:** 264 trades, Sharpe 2.29, TPY 44.1

| Filter | Trades | Sharpe | Delta | Win Rate | TPY |
|---|---|---|---|---|---|
| move_z>0 | 157 | **1.25** | -1.04 | 62% | 26.3 |
| move_z>0.5 | 111 | **0.93** | -1.36 | 61% | 18.8 |
| move_z>1.0 | 72 | **0.87** | -1.42 | 67% | 12.2 |
| move_z<0 | 107 | **2.38** | +0.09 | 71% | 18.5 |
| move_high (>90) | 152 | **1.61** | -0.68 | 65% | 27.9 |
| move_low (<70) | 71 | **1.19** | -1.10 | 63% | 12.0 |

---

## 6. Approach D: MOVE as VIX Leading Indicator

**Same-day MOVE-VIX correlation:** 0.3906

| Lead-lag | Correlation |
|---|---|
| move_leads_vix_by_1d | -0.0330 |
| move_leads_vix_by_2d | -0.0080 |
| move_leads_vix_by_3d | -0.0023 |
| move_leads_vix_by_5d | +0.0519 |
| move_leads_vix_by_10d | -0.0070 |
| move_leads_vix_by_20d | -0.0080 |

### Conditional VIX Response to MOVE Spikes

| MOVE Regime | Events | Avg VIX 5d Fwd | % VIX Up |
|---|---|---|---|
| move_spike_z>1 | 420 | +1.26% | 43% |
| move_spike_z>1.5 | 276 | +1.52% | 45% |
| move_spike_z>2 | 159 | +1.78% | 45% |
| move_calm_z<-1 | 406 | +3.16% | 55% |

---

## 7. Approach E: MOVE-Conditioned TLT Daily Strategy

| Variant | Sharpe | CAGR | Max DD | Vol | Exposure |
|---|---|---|---|---|---|
| long_only_z>1 | **-0.31** | 0.7% | 24.3% | 10.5% | 24.1% |
| long_only_z>0.5 | **-0.28** | 0.6% | 28.3% | 11.6% | 33.3% |
| long_short_z1 | **0.26** | 7.2% | 19.6% | 12.4% | 47.4% |
| long_short_z0.5 | **-0.02** | 3.1% | 30.7% | 14.8% | 74.9% |
| move_momentum_ls | **-0.02** | 3.9% | 17.3% | 9.7% | 31.2% |

---

## 8. Portfolio Integration

*No viable signal for portfolio integration.*

---

## 9. Root Cause Analysis — Why Every Approach Failed

### The TLT Directional Headwind (2020-2026)

TLT declined ~40% from its 2020 peak ($170) to the 2023 trough (~$88) as rates rose from near-zero to 5%+. This directional trend overwhelms all volatility-based signals:

- **Buy-and-hold TLT Sharpe: -0.33** — the underlying asset lost money over the test period
- Any long TLT signal inherits this headwind unless perfectly timed
- The IV-RV spread predicted volatility compression, but TLT still fell as rates rose

### Approach A (MOVE-Filtered Spreads): Filtering Removes Good Trades

Counterintuitively, filtering TLT credit spreads by elevated MOVE *reduced* Sharpe from 0.76 → 0.47. The 52 original EXP-2910 trades were already sparse; filtering removed trades that happened to be profitable, not just bad ones. The IV-RV signal is not selective enough on 52 observations.

### Approach B (Standalone IV-RV): Vol Mean-Reversion Doesn't Work for Bonds

Equity vol mean-reverts strongly (VIX spikes and drops). Bond vol is more persistent — the post-2020 rate uncertainty regime kept MOVE elevated for years, not days. Going long TLT when MOVE was high meant being long during a structural bear market in bonds.

### Approach C (MOVE Overlay for cross_vol): Most Interesting Finding

The cross_vol arb actually works BETTER when MOVE is low (z < 0): Sharpe 2.38 vs 2.29 baseline (+0.09). When MOVE is HIGH, cross_vol Sharpe drops to 1.25 (−1.04). This makes sense: when bond vol is elevated, the macro regime is noisy and equity cross-sectional vol relationships break down. **Low MOVE = quiet macro = equity cross-vol arb works better.**

However, the +0.09 Sharpe improvement is within noise (0.09 < 0.2 significance threshold from EXP-1950). Not worth the added complexity.

### Approach D (Leading Indicator): MOVE Has No Predictive Power for VIX

Lead-lag correlations from 1 to 20 days are all near zero (max |ρ| = 0.05). MOVE captures *concurrent* correlation with VIX (ρ = 0.39 same-day) but has zero *lead*. It's a coincident indicator, not a leading one.

Counterintuitively, calm MOVE (z < -1) precedes VIX UP moves (+3.16% in 5 days). This may be a contrarian signal: when bond vol is extremely low, complacency → equity vol spikes. But the effect is too weak (55% directional accuracy) to trade.

### Approach E (MOVE-Conditioned TLT Daily): Best Is 0.26 Sharpe

The long/short z=1 variant (long TLT when MOVE z > 1, short when z < -1) produces the highest Sharpe at 0.26 — still far below the 1.0 threshold. The long-short structure helps but can't overcome the low signal-to-noise in the MOVE-TLT relationship.

### Summary: Bond VRP Is Not Easily Harvestable

The AUM_CAPACITY_RESEARCH hypothesis that "bond VRP is persistent and uncorrelated with equity VRP" is CORRECT on the correlation dimension (MOVE-VIX ρ = 0.13, MOVE-SPY ρ ≈ 0) but INCORRECT on the harvestability dimension. The bond VRP during 2020-2026 was dominated by:
1. A structural rate regime change (ZIRP → 5%+) that made long TLT toxic
2. Persistent (not mean-reverting) MOVE elevation that broke vol-timing signals
3. TLT option premiums too thin ($0.045 median credit) for meaningful credit spread income

**Recommendation: Abandon TLT as an alpha source. The correlation benefit is real but the return stream is not viable with current instruments. Focus capacity expansion on high-liquidity equity strategies (sector momentum, overnight premium) that don't require new underliers.**

---

## 10. Verdict

**KILLED — Best individual Sharpe 0.26 < 1.0. None of the five MOVE-based approaches (filtered spreads, standalone IV-RV, cross_vol overlay, leading indicator, daily equity) clear the quality bar. The bond VRP is real but not harvestable with current instruments during a structural rate regime.**

---

## 11. Methodology

- **MOVE index:** ICE BofA MOVE Index from Yahoo ^MOVE — measures expected 1-month Treasury yield volatility in basis points
- **TLT realized vol:** 20-day trailing annualized standard deviation of log returns
- **IV-RV spread:** MOVE (normalized to TLT-equivalent scale) minus TLT realized vol
- **Z-score:** 60-day rolling standardization of the IV-RV spread
- **Causality:** All signals use 1-day lag (yesterday's MOVE → today's position)
- **Walk-forward:** 252d train / 63d test expanding window
- **Cost model:** 890 bps/yr (analytical drag via net_sharpe_from_drag)
- **Sharpe formula:** mean(daily_returns) / std(daily_returns, ddof=0) × √252

### Rule Zero Verification

- `np.random.call`: 0 occurrences OK
- `random.normal.call`: 0 occurrences OK
- `generate_prices.call`: 0 occurrences OK

---

*Generated by compass/exp2920_tlt_ivrv_arb.py*
*All data from Yahoo Finance (^MOVE, TLT, ^VIX) and IronVault. No synthetic data.*
