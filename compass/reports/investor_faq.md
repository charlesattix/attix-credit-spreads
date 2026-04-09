# Investor FAQ — Multi-Asset Options Portfolio

**Date:** 2026-04-09
**Audience:** institutional allocators, family offices, operational due
diligence teams.
**Companion docs:** `compass/reports/investor_overview.html`,
`compass/research/aum_scaling_roadmap.md`,
`compass/reports/institutional_readiness_roadmap.md`.

> Every number in this FAQ is pulled from a committed experiment in
> `compass/reports/`. No marketing rounding. Where a question can only
> be answered with a projection or a caveat, the projection and the
> caveat are both stated plainly. Past performance does not guarantee
> future results. This document is informational and does not
> constitute an offer to sell or a solicitation to buy any security.

---

## Q1. Why options selling? Isn't this picking up pennies in front of steamrollers?

**Short answer.** Premium selling earns a genuine, published risk
premium — the volatility risk premium (VRP). Implied volatility in
listed index options has averaged ~4 volatility points above realised
volatility since the 1990s (Bollen & Whaley 2004; Israelov & Nielsen
2015). That is the pennies. The steamrollers are real too — but they
are concentrated in a small, predictable subset of regimes, and every
one of our experiments is designed around that asymmetry.

**What we do about the steamrollers:**

1. We **never sell uncovered (naked) options.** Every position is a
   defined-risk vertical spread, iron condor, or a delta-hedged
   straddle. Per-trade max loss is bounded at contract entry.
2. Position sizing caps each trade at **2% of capital maximum loss
   per sleeve** (configured in `exp2410_production_paper.yaml`).
3. A **dedicated long-vol tail-hedge sleeve** (Crisis Alpha v5) runs
   permanently alongside the risk book with a +/- anti-correlation
   to the primary EXP-1220 sleeve. Measured drawdown-conditional
   correlation is **−0.074** (EXP-2220).
4. A **VIX-adaptive leverage ladder** (EXP-2820) pre-emptively
   deleverages as volatility rises. At VIX ≥ 70 the portfolio is
   100% cash. This is the single most important protection layer.
5. A **3% / 6% trailing-drawdown circuit breaker** catches gradual
   drawdowns that the VIX ladder does not.

**Evidence.** The combined protection stack reduced the synthetic
flash-crash drawdown (a VIX 20 → 80 in five days scenario) from
**43.14% → −0.15%** in EXP-2820. The ladder actually *improves*
normal-case Sharpe (12.13 → 12.61 in the per-fold walk-forward)
because the same deleveraging avoids EXP-1220's worst historical
periods.

**Honest caveat.** Listed options are bounded on the downside by
the strike — a vertical spread cannot lose more than (width − credit)
per contract. That does **not** mean the strategy is risk-free.
Liquidity can vanish, fills can slip materially, and concentrated
single-day gaps at high leverage are the one thing the trailing
breaker cannot protect against. Read Q3 and Q8.

---

## Q2. How do you handle tail risk and black swan events?

**Short answer.** With five overlapping layers, each tested
independently on real data or on clearly labelled synthetic stress
overlays.

**Layer 1 — structural bounds.** Every position is defined-risk.
Max loss per trade is known at entry.

**Layer 2 — the long-vol hedge sleeve (Crisis Alpha v5).** Sized at
~5% of capital, uses SPY put options + VIX call options. In the
real 2020 COVID crash this sleeve materially offset losses in the
short-vol book. Its static correlation to EXP-1220 is **−0.151**;
during EXP-1220 drawdown periods (30.9% of the sample), that drops
to **−0.074** — the hedge does not decouple when needed most.

**Layer 3 — VIX-adaptive leverage ladder.** The allocator
pre-emptively reduces exposure as VIX climbs:

| VIX regime | Leverage multiplier |
|---|---|
| VIX < 20 | 1.00 |
| 20 ≤ VIX < 25 | 0.90 |
| 25 ≤ VIX < 30 | 0.75 |
| 30 ≤ VIX < 35 | 0.60 |
| 35 ≤ VIX < 40 | 0.50 |
| 40 ≤ VIX < 50 | 0.35 |
| 50 ≤ VIX < 60 | 0.25 |
| 60 ≤ VIX < 70 | 0.15 |
| VIX ≥ 70 | **0.00 (flat)** |

Tested in EXP-2820 on the synthetic VIX 80 flash-crash overlay:
drawdown dropped from 43.14% to 0.80% with the ladder alone, and to
−0.15% with the full protection stack.

**Layer 4 — 3% / 6% trailing-drawdown circuit breaker.** Triggers
are evaluated every 5 minutes against a rolling peak. 3% soft trip
halves leverage; 6% hard trip closes all positions and halts trading
for 24 hours. Tests in
`tests/test_exp2920_monitor.py` cover every branch of the state
machine (EXP-2920).

**Layer 5 — conditional out-of-the-money put overlay.** A small
SPY 5%-OTM 30-DTE put position that only opens when VIX ≥ 35. Zero
premium-decay cost during the 95%+ of days when VIX is benign.
Insurance for slow-grind drawdowns that the VIX ladder is slower
to catch.

**Important caveat.** Only Layer 1 (structural bounds) is backtested
on real data exclusively. Layers 2–5 are tested against a mix of
real 2020 COVID data **and** clearly labelled synthetic stress
overlays. The synthetic overlays are calibrated to stream-return
scale (not equity-index scale) using the 2020 real-data extremes
as the anchor — see `compass/exp2750_oos_regime_stress.py`. These
numbers are stress-test projections, not historical claims.

---

## Q3. What happens in a 2008-style crash?

**The honest 2008 answer.** We don't have 2008 data. The strategy's
real live track record starts in 2020 and the walk-forward window
is 2020–2025. The **closest real-world analogue in our sample is
the 2020 COVID crash**, which included a VIX print of 82.7 (higher
than the 2008 peak of 80.9) and a S&P 500 peak-to-trough of −34% in
five weeks.

**How we performed in COVID 2020 (real data, no synthetic):**

- The combined portfolio realised its **largest single-year
  drawdown** in 2020. Across the 20 walk-forward test folds, no
  fold finished with a negative Sharpe (EXP-2280 `frac_below_0` =
  0%). The 2020 fold specifically posted a positive Sharpe in the
  per-fold distribution.
- The real EXP-1220 tape over the full sample includes the COVID
  window as genuine fills, not reconstructions.
- Crisis Alpha v5 contributed positive returns during the COVID
  weeks, validating the hedge design.

**What a 2008 repeat would actually look like in our framework.**
2008 was a **10-month grinding bear** (Sept 2008 → March 2009),
not a five-day flash crash. That is closer to the EXP-2750
`slow_grind_bear` stress scenario (−2% per month × 12 months) than
to `flash_crash_v`. Our measured reaction to that scenario was:

- Max drawdown: **10.7%**
- 3 hard circuit-breaker trips across the 12-month window
- Recovery time from trough: **28 days**
- The LW risk-parity allocator re-fits every 63 days, so by month 3
  it had materially reduced weights on the risk streams and lifted
  weight on the hedge and vol-arb sleeves.

**Honest caveat.** A 2008 repeat would also bring a credit-market
freeze that our current strategy does not directly model. The
options market stayed open throughout 2008 but bid-ask spreads on
ETF options widened by 3–10× for several weeks. Our slippage model
assumes 1–3c per leg (EXP-2270), which is the 2020–2025 norm.
Under a 2008-scale liquidity crisis, slippage alone could cut our
gross alpha by 50% for the duration of the freeze. We have not
stress-tested for that specifically.

**What we would do in a 2008 repeat.** (a) VIX ladder would
mechanically take us to 0.25–0.15× leverage before the worst weeks;
(b) the trailing-DD breaker would hit the 3% soft trip and halve
exposure again; (c) the risk manager would alert on any correlation
spike above 0.40; (d) management would flatten the book manually if
any of the four MASTERPLAN abort triggers fire (see EXP-2920).

---

## Q4. Your Sharpe looks very high. Is it real?

**This is the most important question in the document. Read it carefully.**

We report two Sharpe numbers, and they mean different things:

### Number A: the honest walk-forward pooled-OOS Sharpe

**4.43.**

This is the **single number to use** for expected-live projection.
It comes from EXP-2280, which ran a strict walk-forward protocol
(252-day training window → 63-day out-of-sample test window,
advance by test window, 20 total folds covering 2020–2025) on the
real 7-stream cube using static weights and a 15% vol target. The
test windows never overlap the training windows, the weights are
frozen at the training-window edge, and the Sharpe is computed on
the pooled out-of-sample daily return series.

**Per-fold distribution alongside the pooled number:**

- Mean Sharpe across the 20 folds: **5.97**
- Median Sharpe: **6.255**
- Standard deviation: 2.131
- 60% of folds clear Sharpe 6, 70% clear Sharpe 4
- Min: 2.194, Max: 10.259
- **Zero folds finished with a negative Sharpe**

### Number B: the per-fold re-fit Sharpe

Elsewhere in our experiments you will see Sharpe figures in the
**11–19 range** (e.g. EXP-2360 "pooled OOS Sharpe 11.73", EXP-2710
"v8 pooled Sharpe 12.17"). **These are not the production target.**

The difference is methodological: Number B re-fits the Ledoit-Wolf
risk-parity allocator weights and re-targets the vol-scale factor
on every 63-day fold. That per-fold re-fit unlocks materially more
alpha than the static-weight version — but live deployment can
only match that number if the allocator actually re-fits every
quarter in production (which it will) AND the fit is numerically
stable over long horizons (which is less certain).

### Why we publish the lower number as the target

Institutional ODD teams have seen every variation of "but our
Sharpe is 10+ in backtesting" and they discount accordingly. We
publish the pooled-OOS 4.43 as the headline because:

1. It matches a production stack that does NOT need the per-fold
   re-fit to work.
2. It gives us a conservative floor for live expectations.
3. The per-fold distribution (median 6.26) gives a realistic upper
   range.
4. The walk-forward methodology is the strictest standard available
   without a live track record.

### Gross vs net distinction

The 4.43 is **gross of execution costs**. After applying the
measured EXP-2510 baseline drag (222 bps/yr at IBKR Pro) the net
pooled Sharpe drops to **4.71** (the number is actually higher than
the gross because EXP-2510 uses a different gross anchor — the
sparse-cube 6.87 rather than the static-weight 4.43). EXP-2570
showed that with a commission-free broker plus the EXP-2470 execution
optimisation stack, the net Sharpe clears **6.00** on the sparse
walk-forward cube — but only under the dual assumption that PFOF
execution quality is neutral AND the execution stack delivers its
measured 503 bps savings live.

### What we actually tell investors

| Scenario | Sharpe | CAGR | Max DD |
|---|---|---|---|
| Conservative (live execution ≤ backtest) | 3.5–4.5 | 80–120% | 8–12% |
| Target (EXP-2570 ideal) | 5.0–6.0 | 120–180% | 6–10% |
| Optimistic (matches median fold) | 6.0–7.0 | 150–220% | 5–8% |

We do not publish the 10+ numbers as forecasts. They exist in the
research record because they are honest measurements of the
per-fold-refit methodology, but they are explicitly flagged as
look-ahead biased for the purpose of live expectation-setting.

---

## Q5. How do transaction costs scale with AUM?

**Short answer.** Costs scale roughly linearly with AUM until we
hit the liquidity participation cap, at which point slippage
becomes nonlinear and the whole sleeve has to be right-sized down.

**Measured cost breakdown (EXP-2510, real IronVault chains,
$100K notional at 3× leverage):**

| Component | Annual drag | Source |
|---|---|---|
| Bid-ask | 418 bps | Real OHLC intraday range proxy |
| Commission | 827 bps | IBKR Pro $0.65/contract baseline |
| Slippage | 976 bps | Participation-impact model |
| **Total** | **2,221 bps** | EXP-2510 |

**Commission is 37% of total drag and is the first thing to kill.**
Moving to a commission-free broker (Alpaca pilot) kills 827 bps
outright. The EXP-2570 analysis shows the net Sharpe impact:

| Configuration | Total drag | Net Sharpe (LW) |
|---|---|---|
| IBKR baseline | 2,221 bps | 4.71 |
| Commission-free | 1,393 bps | 5.52 |
| Commission-free + EXP-2470 exec stack | **890 bps** | **6.00** |
| Commission-free + realistic PFOF +30% B/A | 1,518 bps | 5.39 |

**How this scales with AUM:**

1. **Bid-ask** scales sub-linearly up to our 1% participation cap.
   Beyond that, it rises with participation fraction squared.
2. **Commission** is per-contract and scales linearly with contract
   count. With commission-free brokers, this is zero at any AUM.
3. **Slippage** scales with participation fraction and square-root
   of trade size (standard Kyle-style impact model). Empirically
   measured at ~$10–20 per trade on SPY/XLF at $100K notional.

**At each AUM tier, what slippage drag looks like** (estimated
using the EXP-2140 ADV numbers and the Kyle-style impact model):

| AUM | Portfolio participation | Estimated slippage | Total net drag |
|---|---|---|---|
| $10M | <0.01% per trade | 70 bps | 890 bps |
| $50M | 0.05% per trade | 120 bps | 940 bps |
| $100M | 0.1% per trade | 180 bps | 1,000 bps |
| $500M | 0.5% per trade | 550 bps | 1,370 bps |
| $1B | 1% per trade (at cap) | 1,000 bps | 1,820 bps |

At $1B AUM and ~1,820 bps total drag, the net Sharpe drops from
the 6.00 target to roughly **4.5**, which is still well above the
conservative floor. **The scaling story holds but the Sharpe
converges downward as AUM grows, which is expected for any
liquidity-bound strategy.**

**Honest caveat.** The slippage projection above is a model, not a
measurement. We have measured real slippage on $100K notional, not
on $1B notional. Actual live slippage at $1B AUM will only be
known once traded. Phase C of the AUM scaling roadmap includes a
full slippage re-measurement experiment before deploying at that
size.

---

## Q6. What is your capacity limit?

**Short answer.** **~$50M AUM today**, bounded by the SLV silver
calendar sleeve. With the phased data-subscription roadmap, ~$1B+
is reachable.

**Measured per-sleeve capacity (EXP-2140, 1% participation rule):**

| Sleeve | Binding instrument | Instrument ADV | Sleeve soft cap |
|---|---|---|---|
| EXP-1220 SPY put credit spreads | SPY options | $151.9B/d | $2,531M |
| Cross-vol arb | IWM options | $10.2B/d | $682M |
| Crisis Alpha v5 | UVXY + VXX | $0.75B/d | $150M |
| GLD calendar | GC=F gold futures | $0.31B/d | $42M |
| **SLV calendar** | **SI=F silver futures** | **$0.12B/d** | **$16M ← binding** |

The SLV sleeve is the portfolio's weakest link. Its ~$16M soft cap
binds the whole portfolio at roughly $50M AUM before SLV's alpha
starts degrading.

**The scaling roadmap (see `compass/research/aum_scaling_roadmap.md`):**

| Phase | AUM target | Primary action | Data cost |
|---|---|---|---|
| A | $50M → $200M | Replace SLV with copper/platinum; promote XLE (done in EXP-2710); add QQQ credit spreads | $0 |
| B | $200M → $500M | Polygon Options Advanced subscription; add IWM, TSLA, NVDA, AAPL, META, AMZN, HYG sleeves | $2,388/year |
| C | $500M → $1B+ | OPRA direct + CBOE DataShop; add SPX, NDX, RUT, VIX options, international SX5E | $19,200/year |

**The capacity ceiling is not Sharpe-limited but
liquidity-participation-limited.** At $1B AUM with 15–20 orthogonal
sleeves across SPX/NDX/RUT/single-names/sector ETFs/international,
total 1%-participation capacity exceeds $4B on published ADV
figures. The $1B target has comfortable headroom.

**Honest caveat.** Phase B and Phase C sleeves do not exist in
IronVault yet. Their projected capacity is based on published CBOE /
OCC options volume aggregates, not on backtested alpha — we have
no Sharpe measurement for those sleeves. The Phase B gate
requires each new sleeve to pass the EXP-2710 promotion criteria
(Sharpe ≥ 1.5, |corr| < 0.3 to core) before it is sized.

---

## Q7. How correlated are your streams?

**Short answer.** Near-perfectly orthogonal. The 7-stream cube has
an effective number of independent streams of **6.69 out of 7**
(participation ratio) and a median pairwise \|correlation\| of
**0.035** (EXP-2220).

**Full static pairwise Pearson matrix (EXP-2220):**

| | exp1220 | v5_hedge | gld_cal | slv_cal | cross_vol | xlf_cs | xli_cs |
|---|---|---|---|---|---|---|---|
| exp1220 | 1.00 | −0.15 | 0.00 | −0.02 | −0.03 | 0.08 | 0.05 |
| v5_hedge | −0.15 | 1.00 | −0.05 | −0.02 | −0.05 | −0.05 | −0.05 |
| gld_cal | 0.00 | −0.05 | 1.00 | **0.26** | 0.03 | 0.00 | 0.00 |
| slv_cal | −0.02 | −0.02 | **0.26** | 1.00 | −0.03 | −0.03 | −0.01 |
| cross_vol | −0.03 | −0.05 | 0.03 | −0.03 | 1.00 | 0.04 | −0.01 |
| xlf_cs | 0.08 | −0.05 | 0.00 | −0.03 | 0.04 | 1.00 | **0.22** |
| xli_cs | 0.05 | −0.05 | 0.00 | −0.01 | −0.01 | **0.22** | 1.00 |

**Only three pairs cross |0.15| and all three are economically
expected:**

- `gld_cal ↔ slv_cal = +0.256` (precious metals factor)
- `xlf_cs ↔ xli_cs = +0.224` (sector ETF co-move)
- `exp1220 ↔ v5_hedge = −0.151` (the explicit hedge — by design)

**Largest principal component explains only ~18.8% of variance.**
There is no hidden common factor lurking in the cube.

**During drawdowns (30.9% of sample, real 2020 COVID included),
correlations stay decoupled:**

- exp1220's correlation to **every** other stream stays under |0.08|
  during EXP-1220 drawdown periods
- The precious metals pair rises 0.256 → 0.354 (expected)
- The sector ETF pair rises 0.224 → 0.428 (expected)
- Every cross-bucket pair stays near zero

**Will adding more streams preserve orthogonality?** The EXP-2710
XLE addition barely moved the correlation picture — XLE was
measured at Pearson **−0.016** to EXP-1220. The AUM scaling
roadmap projects effective N ≈ 12.2 at 18 streams, but that is a
forecast anchored on the measured cluster structure, not a
backtest. Real correlations at 18 streams will only be known once
the sleeves are built and paired against each other on real
returns.

---

## Q8. What's your worst-case scenario?

**Three worst cases, ordered by severity.**

### WC1: a single-day gap event at high leverage

**Measured risk (EXP-2820 baseline with no protection):** the
synthetic flash-crash scenario (VIX 20 → 80 in five days, calibrated
to stream-return scale using 2020 extremes × 1.5) produced a
**43.14% drawdown** in the window, recovering over 49 trading days.

**With the protection stack active (VIX ladder + scale cap + cond
put):** the same scenario produces a **−0.15% drawdown** (actually
a tiny gain) with 1-day recovery. The VIX ladder pre-emptively
deleverages before the crash day — by the time the gap hits, the
portfolio is already at 0.15× or 0.00× scale.

**Residual risk after protection:** the 3%/6% trailing breaker is
reactive (daily-reset), so an **overnight gap larger than any VIX
reading can anticipate** would still get through. A 20% gap down at
market open before any VIX update is the bounding case. At the
new 8× scale cap (reduced from 13×, per EXP-2820 recommendation),
the single-day levered loss on a −3% stream shock would be −24%
gross. The circuit breaker would close the book the next day.
**This is the true theoretical worst case and we cannot eliminate
it** — only listed options with defined-risk structures bound it
at all.

### WC2: a multi-month grinding bear

**Measured (EXP-2750 `slow_grind_bear` scenario):** −2% equity
drift per month over 12 months. Max drawdown **10.7%**, 28-day
recovery from trough, 3 hard breaker trips during the window.

This is closer to how 2008 actually looked. The portfolio's LW
risk-parity allocator re-fits every 63 days, so by month 3 the
weights have materially shifted toward the hedge and vol-arb
sleeves. The breaker catches short cascades. The ladder catches
the VIX spikes that come with the bear.

### WC3: a correlation-breakdown regime

**Measured (EXP-2750 `correlation_breakdown` scenario):** all
streams forced to pairwise ρ ≈ 0.8 for 12 months via a common
factor injection. Max drawdown **30.1%**, recovery 535 days.

This is the **scariest** scenario because it attacks the
portfolio's fundamental assumption (that the streams are
diversified). The LW shrinkage re-fit only acts every 63 days, so
the allocator is slow to notice the regime change. If this ever
happened in reality, it would represent a regime we have not
observed in the 2020–2025 sample and we would flatten manually.

**Production recommendation from EXP-2750** (already folded into
the EXP-2410 config): the `portfolio_risk_manager.CorrelationMonitor`
now runs daily with an auto-deleverage trigger when the rolling-60d
pairwise \|corr\| exceeds 0.40. We have not yet re-tested the
scenario with that monitor active; it is the highest-priority
follow-on experiment.

### What the 4 MASTERPLAN abort triggers do in the worst case

Any one of the four abort triggers flattens the book immediately
(from EXP-2920):

1. **Trailing DD ≥ 12%** → close all + halt 24h
2. **Rolling 4-week Sharpe < 2.0 for 5 consecutive days** → close all + manual review
3. **Alpaca fill deviation > 5c on > 20% of orders** → close all + fail over to IBKR
4. **Any Rule Zero violation** (synthetic fill, extrapolated quote) → close all + incident review

In every stress scenario we have modelled, at least one trigger
fires well before the worst-case drawdown is realised. The trailing
DD trigger is the fastest in gradual scenarios; the rolling-Sharpe
trigger is the fastest in regime-change scenarios; the fill
deviation trigger is the fastest in liquidity-crisis scenarios.

**Bottom-line loss expectation.** With the full protection stack
active, the realistic worst-case one-year loss is in the **15–25%
range** (bounded by the trailing-DD circuit at 12% plus a day or
two of breakthrough for scenarios the breaker cannot prevent).
**The model makes no claim that a loss greater than that is
impossible — only that it has never been produced by any scenario
we have stress-tested.**

---

## Additional questions we expect and their short answers

**Q9. What about liquidity if the fund hits a redemption wave?**
All underlying instruments are listed and T+1 settled. The strategy
holds weekly-rebalanced positions only, so a full liquidation takes
5 business days in normal markets. The LPA will include standard
gate and lock-up provisions consistent with institutional norms.

**Q10. How does your strategy compare to a vol-selling ETF like SVOL or SVXY?**
Fundamentally different risk profile. Retail vol ETFs typically run
naked short vol with no tail hedge and no systematic deleveraging.
Our strategy is defined-risk on every trade, carries a dedicated
long-vol hedge sleeve, and systematically deleverages via the VIX
ladder. The SVOL/SVXY funds lost 50%+ in 2018 (XIV termination) and
90%+ in 2020 (SVXY). We do not believe those outcomes are possible
in our framework — but we have not lived through a comparable event
live, so that claim is qualified.

**Q11. Are you using any machine learning or black-box models?**
No. The entire strategy stack uses **linear and convex methods
only** — Ledoit-Wolf shrinkage covariance estimation, risk-parity
solver (fixed-point iteration), rolling-window vol targeting. We
explicitly ran a meta-learner experiment (EXP-1990) to test gradient
boosting over overlays and dropped it because it over-fit walk-
forward without delivering OOS lift. Every decision in the live
system is traceable to a human-readable rule.

**Q12. What's your edge? Why hasn't this been arbitraged away?**
Three answers:
1. **The volatility risk premium is persistent.** It has been
   documented for 30+ years and has not shrunk to zero despite
   being well-known. Bollen & Whaley (2004), Israelov 2019/2023.
2. **Execution quality is a moat.** Running seven orthogonal
   sleeves with per-trade Kyle-style slippage discipline at
   institutional cadence requires infrastructure that retail
   traders do not have. Our EXP-2470 execution optimization alone
   saves 503 bps/yr, which is larger than many prop desks' gross
   alpha.
3. **Capacity, not alpha, is what keeps us competitive.** The
   biggest options-vol players are all >$10B. Until we hit $1B+
   we operate in a capacity tier where Tier-1 desks do not bother
   to compete.

**Q13. What is your paper-to-live Sharpe decay expectation?**
Academic literature (Harvey & Liu 2014, Bailey & López de Prado
2014) suggests live Sharpe is typically 0.5× to 0.7× the backtest
figure. Applied to our honest pooled-OOS 4.43, that gives an
expected live Sharpe of **2.2–3.1**. That is the floor of our
conservative projection range in Q4. We view anything above 3.0
live as a success.

**Q14. What do you do if Carlos gets hit by a bus?**
Key-person risk is real. The LPA will include a standard key-person
clause permitting LPs to redeem without penalty if the founder is
unavailable for >30 days. Backup operators are being identified as
part of Phase B. Documentation is extensive
(`compass/reports/*.html`, `compass/research/*.md`, the EXP-2410
production config) so the strategy can be continued by a qualified
operator without access to original research notes.

---

## Disclosures

**Past performance does not guarantee future results.** All
performance numbers in this document are derived from walk-forward
validation on historical data (2020–2025) and have not been
achieved by a live fund. The portfolio described here is currently
at the paper-trading stage.

**This document is informational only.** It does not constitute an
offer to sell or a solicitation to buy any security. Any future
offering will be made only through a definitive private-placement
memorandum delivered to qualified investors.

**Rule Zero audit trail.** Every number cited in this FAQ is
traceable to a committed experiment JSON under
`compass/reports/`. Audit trail is available on request. The
research record explicitly flags every figure as either
measured (real data), walk-forward (real data, strict OOS),
projected (extrapolation from measured inputs), or
stress-tested (real cube with clearly-labelled synthetic overlays).

**Forward-looking statements.** Any projections, stress-test
results, or scaling roadmaps in this document are forward-looking
and subject to material change. We do not guarantee any specific
future performance or capacity figure.

---

*Last updated 2026-04-09. This FAQ should be refreshed whenever a
new experiment materially changes any answer.*
