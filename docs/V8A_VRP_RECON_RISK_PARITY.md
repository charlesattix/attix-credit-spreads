# EXP-V8A VRP — Recon: Ledoit-Wolf Risk-Parity Sizing Module

**Scope:** PR-C reconnaissance (allocator/sizing). **Status:** design only — no code shipped.
**Date:** 2026-05-28. **Author:** scout (coordinated with cc1 orchestrator).
**Reads:** `docs/V8A_VRP_BUILD_PLAN.md`, `compass/exp2850_v8a_with_vix_ladder.py`,
`compass/exp2360_robust_cov.py`, `compass/vix_ladder.py`, `shared/database.py`,
`configs/paper_expv8a.yaml`, `compass/dollar_notional_sizer.py`.

> **⚠️ PR-label mapping.** The recon split (cc1) calls this work **PR-C = "LW risk-parity
> sizing"**. In `V8A_VRP_BUILD_PLAN.md` the *same allocator* is **PR-E**, while that
> doc's **PR-C is the calendar-spread engine**. This doc = the allocator, regardless of
> label. Where I say "PR-A (data feed)" / "PR-B (strategy engine)" I mean the recon-split
> labels from my task brief, which map to build-plan **PR-A** and **PR-B** respectively.

> **TL;DR.** The pure math (Ledoit-Wolf covariance + ERC risk-parity + 12% vol-target
> scaling) already exists and is live-usable today: `compass/exp2360_robust_cov.py`,
> `sklearn>=1.4` is a declared dep. The allocator itself is **~1 medium PR**. The real
> work is **not the estimator — it's the input**: the backtest feeds LW an 8-column
> matrix of *per-stream* daily returns, but live storage (`equity_history`) only persists
> **one combined equity per experiment**, with **no per-stream attribution** (the `trades`
> table has no `stream` column). So PR-C's hard dependencies are (a) per-stream P&L
> attribution (build-plan PR-I) and (b) a cold-start policy for the ~252-day window that
> V8A (≈3 days old) cannot fill. Net allocator effort ~1 PR; *blocked* until per-stream
> returns exist.

---

## 1. What VRP needs from the LW estimator

Source of truth = `walk_forward_with_ladder()` in `compass/exp2850_v8a_with_vix_ladder.py:105-176`.
Per walk-forward fold it does exactly this (L135-144):

```python
train  = cube.iloc[i-252 : i]            # 252 trading days, 8 stream-return columns
test   = cube.iloc[i : i+63]             # 63-day OOS hold
Sigma  = cov_ledoit_wolf(train.values)   # (8x8) shrunk covariance  → exp2360_robust_cov.py:106
w      = risk_parity_weights(Sigma)      # ERC weights, w>=0, sum(w)=1 → exp2360_robust_cov.py:164
train_port = train.values @ w
train_vol  = np.std(train_port, ddof=1) * sqrt(252)   # annualized in-sample vol
scale  = clip(TARGET_VOL / train_vol, 0.1, 20.0)      # TARGET_VOL=0.12, SCALE_CAP=20  (L63-64)
gross  = test.values @ w * scale                      # OOS daily returns at 12% vol
gross  = gross * vix_ladder_exposure                  # PR-F, causal shift-1d
net    = gross - daily_drag                           # 890.3 bps/yr / 252  (L65, L130)
```

**Inputs the estimator consumes**
- **Return series = DAILY**, one column per stream, fraction-of-capital units
  (`pnl / 100_000`, see `build_v8a_cube` L72-76). Not intraday. Not prices — *returns*.
- **Window = 252 trading days** (`TRAIN_DAYS`, L61). LW needs the full matrix `train.values`
  shape `(252, 8)`.

**Outputs**
- `risk_parity_weights(Sigma) -> np.ndarray` of 8 ERC weights (non-negative, sum to 1).
  Equal *risk* contribution, not equal *dollar* — low-vol streams get larger weights.

**How the 12% vol target is applied** — it is a **scalar multiplier on the whole
portfolio's return stream**, derived from the *in-sample* (training-window) realized vol:
`scale = 0.12 / train_vol`, clipped to `[0.1, 20.0]`. In backtest it multiplies returns;
**live, it must instead multiply the per-stream capital/contract count** (you can't scale a
return after the fact — you size the position so the *forward* portfolio targets 12%). So
live: `stream_capital_i = account_equity * w_i * scale * vix_exposure`, then convert to
contracts. The `20×` scale cap is the dangerous knob — at 12% target with a low-vol training
window it can lever 20× notional; live this **must** be reconciled with the existing
`DollarNotionalSizer` `max_leverage=3.0` cap (see §2) and portfolio circuit breakers.

**Refresh cadence** — backtest recomputes `Sigma`/`w`/`scale` **once per 63-day fold**
(weights held constant across the hold). For live there is no "fold"; the natural cadence is
**recompute on a fixed schedule, not every scan**. Recommendation: **weekly re-estimate**
of `Sigma`→`w`→`scale` (cheap; LW on 252×8 is milliseconds), apply those weights to all
new entries that week. Daily is acceptable but adds turnover/rebalance cost with little
covariance change day-to-day. Per-scan is wrong (noisy, over-trades). Cache the weights
(see §4).

---

## 2. What's already in the codebase

**`sklearn.covariance.LedoitWolf` — available.** `scikit-learn>=1.4` is declared in
`requirements.txt:23`. Already imported and used at `compass/exp2360_robust_cov.py:57,106`.

**The exact estimator + solver VRP uses — already written, pure, live-usable:**
- `compass/exp2360_robust_cov.py:106 cov_ledoit_wolf(R: np.ndarray) -> np.ndarray`
  — `LedoitWolf().fit(R).covariance_`, wrapped in warning suppression. No I/O, no network.
- `compass/exp2360_robust_cov.py:164 risk_parity_weights(Sigma, n_iter=500, tol=1e-10)
  -> np.ndarray` — Chaves–Hsu–Li–Shakernia ERC fixed-point; PSD-protected (L178-182),
  scale-invariant. Pure numpy.
  These two functions ARE the allocator math. The live module should **import/reuse them
  verbatim** (or lift them into a shared location), not reimplement.
- `compass/vix_ladder.py::VIXLadder` (PR-F) — pure, live-ready VIX→exposure multiplier.

**Existing live "weights → contracts" sizing (reuse candidate for the back half of PR-C):**
- `compass/dollar_notional_sizer.py:119 class DollarNotionalSizer` (`max_leverage=3.0`
  default, L140). `size_portfolio(account_equity, weights, leverage, quotes) -> PortfolioSizingResult`
  (L197). Core: `fractional = target_dollars / quote.max_loss`; `_conservative_round`
  (L177) floors except ≥0.95→ceil. **This is the natural target for converting RP weights
  ×capital into option contracts** — PR-C should feed it, not duplicate it.
  ⚠️ Its `max_leverage=3.0` directly conflicts with the allocator's `20×` vol-target cap —
  reconcile explicitly.
- `compass/orchestrator/position_sizer.py:99 size_orders(...)` — alternate live sizer with
  sleeve risk budget `equity * risk_per_trade_pct * eff_conf` (L245), liquidity/correlation
  caps. Not yet wired into `main.py`.

**No live risk-parity allocator exists.** `compass/risk_parity.py` and
`compass/risk_budget_allocator.py` are **gone from the live tree** — only
`compass/archive/risk_parity.py` (`RiskParityOptimizer`: `erc`/`hrp`/`inverse_vol`) and
`compass/archive/risk_budget_allocator.py` survive, and their tests
(`tests/archive/test_risk_parity.py`, `test_risk_budget_allocator.py`) import dead paths
(`from compass.risk_parity import ...`). Treat as reference only, not a dependency.

**⚠️ Name collision footgun:** `experiments/EXP-840-max/backtest.py:177
risk_parity_weights(returns, lookback=60)` is **inverse-vol**, *not* ERC — same name,
different (simpler) math. Do **not** confuse it with the canonical ERC solver in
`exp2360_robust_cov.py:164`. The VRP card's Sharpe was produced by the **exp2360 ERC**
version; the live module must use that one.

**How the current Champion (what V8A runs today) sizes — for diff/comparison.**
`configs/paper_expv8a.yaml` is literally the SPY-only champion clone (header L1-6;
`tickers: [SPY]` L27-29; `max_risk_per_trade: 33.15` L104; `sizing_mode: flat` L107;
`max_contracts: 50` L112). Live sizing path = `alerts/alert_position_sizer.py:189`:

```python
dollar_risk         = account_base * (max_risk_per_trade/100) * macro_scale   # L174-179
max_loss_per_spread = (spread_width - credit) * 100                            # L185 (×2 for IC)
contracts           = int(dollar_risk / max_loss_per_spread)                  # L189
contracts           = max(min_contracts, min(contracts, effective_max))       # L191-201
```

**The diff that matters:** Champion = **single symbol, single strategy, flat %-risk per
trade, no covariance.** VRP = **8 streams across 6 symbols, ERC-weighted by a live
covariance, scaled to a portfolio vol target, then VIX-laddered.** These are different
sizing philosophies — the VRP allocator sits *above* per-trade sizing and hands each
stream a capital budget; per-stream contract conversion can still reuse the existing
`max_loss`-based contract math.

---

## 3. Live vs backtest differences (the crux)

| Aspect | Backtest (exp2850) | Live | Gap |
|---|---|---|---|
| Return series | 8 pre-computed daily columns from cached pickles (`build_v8a_cube`) | must be **realized daily returns per stream**, computed from live fills | **the whole problem** |
| Per-stream attribution | inherent (cube has 8 columns) | `equity_history` stores **one combined equity per experiment** (`shared/database.py:91-100`, keyed `(exp_id, as_of_date)`); `trades` has **no `stream` column** (only `ticker`, `strategy_type`, `metadata`, `shared/database.py:43-63`) | **blocking** — needs build-plan PR-I |
| Window | fixed 252d slice always available | V8A is **~3 days old** → 0–3 rows of per-stream returns | **cold start** |
| Covariance refresh | per 63d fold | weekly recompute (proposed) | design choice |
| Vol-target application | multiply returns | size positions forward | re-derived in §1 |

**Live returns source.** `shared/database.py:485 get_equity_history(exp_id, limit=365)`
returns `[{"date","equity","profit_loss"}, ...]` ascending — written daily by the
PositionMonitor via `upsert_equity_point()` (`shared/database.py:409`, idempotent per
`(exp_id, date)`). **This is per-experiment, account-level only.** To build the 8×N
per-stream return matrix the LW estimator needs, we must either:
1. **(preferred)** add `trades.stream` (+ `trades.symbol`) tagging — build-plan **PR-I** —
   and reconstruct a per-stream daily equity curve from per-stream realized+unrealized PnL,
   stored as a new `stream_equity_history(exp_id, stream, as_of_date, equity, …)` table; or
2. derive per-stream returns from each stream's own sub-account equity *if* streams are
   ever split across Alpaca accounts (they are not today — V8A is one account).

Option 1 is the real path and makes **PR-C hard-depend on PR-I**.

**How many days for stable LW?** The backtest uses **252** (`TRAIN_DAYS`). LedoitWolf
shrinks toward a structured target, so it degrades gracefully on short samples, but for an
8-stream covariance the practical floor for a *trustworthy* off-diagonal estimate is
**≈60–90 trading days** (≥ ~8–11× the dimension). Below ~40d the correlation structure is
noise and ERC weights will swing. Recommendation: **require ≥60 live days before trusting
the live covariance; prefer the full 252 once available.**

**Cold-start problem (V8A ≈3 days in).** Weeks 1–8 there is insufficient live history.
Proposed cold-start policy (this is the key design decision for PR-C):
- **Phase 0 (days 0–~20): backtest-cube prior.** Seed `Sigma` from the backtest cube's
  covariance (the exp2450/exp2250 streams) → fixed ERC weights + a conservative vol-target
  scale (cap the `20×` hard, e.g. to `3×` to match `DollarNotionalSizer`). Trade the real
  8 streams at these prior weights. Equivalent to "trust the research until live data
  earns its place."
- **Phase 1 (≈days 20–60): shrink live→prior.** Blend
  `Sigma_used = λ·Sigma_live + (1−λ)·Sigma_prior`, with λ ramping `0→1` as live days
  accrue (e.g. `λ = min(1, live_days/60)`). LedoitWolf-on-live handles the within-estimate
  shrinkage; this outer blend handles the prior.
- **Phase 2 (≥60–252 live days): live covariance**, prior fully retired.
- **Alternative considered & rejected:** "equal-risk until history accrues" (ignore
  covariance, 1/N risk) — simpler but throws away the research edge during the very period
  the account is smallest/most fragile. The cube prior is strictly more informed.
- **Champion-clone warmup is NOT usable as covariance history** — it's single-stream SPY,
  so it yields no 8-stream covariance. (It *is* relevant to the build plan's "run champion
  off to flat before flipping to VRP" decision, but not as allocator input.)

---

## 4. Architecture proposal

**Module path:** `compass/live/risk_parity.py` (new `compass/live/` package for live
allocator/sizing glue; keeps research `exp2360`/`exp2850` untouched, satisfies "don't touch
other scouts' code"). It **imports** the canonical math rather than copying it:

```python
from compass.exp2360_robust_cov import cov_ledoit_wolf, risk_parity_weights
```

(If cc1 prefers the math live-owned rather than imported from an `expNNNN` research file,
lift those two pure functions into `compass/live/covariance.py` and have `exp2360` import
*back* — but that edits a research file, so propose, don't do, in recon.)

**Public API (proposed signatures):**

```python
# Pure allocator — given the per-stream return matrix, return ERC weights + vol scale.
def compute_weights(
    returns_df: pd.DataFrame,          # index=date, columns=stream_id, daily returns
    target_vol: float = 0.12,
    scale_cap: float = 3.0,            # NOT 20.0 — reconciled w/ DollarNotionalSizer
    min_days: int = 60,
) -> WeightResult: ...
# WeightResult: { weights: dict[str, float], vol_scale: float,
#                 n_days: int, cov_source: "live"|"blend"|"prior", lambda_: float }

# Cold-start aware wrapper: blends live cov with the backtest-cube prior per §3.
def compute_weights_with_coldstart(
    live_returns_df: pd.DataFrame,
    prior_cov: np.ndarray,             # from backtest cube, loaded once
    stream_order: list[str],
    target_vol: float = 0.12,
) -> WeightResult: ...

# Capital map → hand off to the EXISTING contract sizer.
def stream_capital(
    account_equity: float,
    weights: dict[str, float],
    vol_scale: float,
    vix_exposure: float = 1.0,         # from VIXLadder (PR-F)
) -> dict[str, float]:                 # {stream_id: dollars}
    # capital_i = equity * w_i * vol_scale * vix_exposure
```

`stream_capital()` output then feeds `compass/dollar_notional_sizer.py::DollarNotionalSizer`
(§2) for the dollars→contracts step — **do not** re-implement contract math.

**Live per-stream returns provider (the dependency, owned with PR-I / PR-B):**
```python
def load_stream_returns(exp_id: str, lookback_days: int = 252) -> pd.DataFrame: ...
# reads a new stream_equity_history table → pct-change → daily return matrix
```

**Caching strategy:**
- Recompute weights **weekly** (or on first scan of the week); persist `WeightResult` to a
  small JSON/SQLite (`vrp_weights(exp_id, as_of_date, weights_json, vol_scale, cov_source)`),
  re-read each scan. Mirrors the dashboard's existing 30–60s cache pattern but at weekly
  cadence. Avoids recomputing LW every scan and gives an audit trail of weight drift.
- Cache the **prior covariance** (from the backtest cube) once at process start — it's static.

**Test plan:**
1. **Unit (pure, deterministic):** `compute_weights` on a known returns matrix →
   assert `w ≥ 0`, `sum(w)=1`, equal risk contributions `w_i·(Σw)_i` equal within tol;
   assert `vol_scale = clip(0.12/in_sample_vol, 0.1, scale_cap)`. Reuse the math by
   golden-testing against `exp2360_robust_cov` outputs on the cube (parity test).
2. **Cold-start:** with `n_days < min_days` → `cov_source == "prior"`; ramp λ at 30/60 days;
   at ≥60 → `"live"`. Assert weights continuous across the λ ramp (no jumps).
3. **Vol-target / leverage:** assert `scale_cap` honored and that resulting notional via
   `DollarNotionalSizer` never exceeds `max_leverage` (the 20×→3× reconciliation).
4. **Degenerate inputs:** single non-zero stream, all-zero returns, NaN rows, <2 rows →
   safe fallback (equal weight, scale=1), never raises.
5. **Integration (paper, gated):** dry-run that V8A produces an 8-stream capital map summing
   to `equity·vol_scale·vix_exposure`; no orders placed (acceptance lives in build-plan PR-J).

---

## 5. Effort estimate & dependencies

| Piece | Effort | Notes |
|---|---|---|
| `compute_weights` + cold-start blend (reusing exp2360 math) | **~0.5 PR** | math exists; this is glue + cold-start policy + caching |
| Live per-stream returns provider (`load_stream_returns` + `stream_equity_history` table) | **~0.5–1 PR** | **needs** `trades.stream`/`trades.symbol` (build-plan PR-I) first |
| Wire `stream_capital` → `DollarNotionalSizer`, reconcile 20×→3× cap + portfolio CB | **~0.5 PR** | reuse, plus leverage reconciliation |
| Tests (§4) | included | mostly pure/deterministic |
| **PR-C total (allocator)** | **~1 medium PR** | *if* per-stream returns already exist |

**Hard dependencies (PR-C cannot ship faithfully without these):**
- **PR-I (DB per-stream/per-symbol tagging)** — *blocking.* No per-stream covariance is
  possible until `trades` rows carry a `stream` and a per-stream equity curve is persisted.
  Until then PR-C can only run in **cold-start/prior mode** (backtest-cube covariance, fixed
  weights) — shippable as an MVP allocator but not the adaptive live one.
- **PR-A (multi-symbol data feed)** — needed so the streams *generate* returns to attribute;
  the allocator itself doesn't read chains, but there's nothing to allocate over without it.
- **PR-B (strategy engine wiring)** — produces the per-stream fills that become returns.
- **PR-F (VIX ladder)** — independent; `VIXLadder` is ready. PR-C just multiplies its output
  in `stream_capital`. Soft dependency (defaults to `vix_exposure=1.0` if absent).

**Sequencing recommendation:** PR-C can land in **two stages** —
(C1) the pure allocator + cold-start *prior* mode (no PR-I needed; uses the backtest-cube
covariance and fixed ERC weights) ships early and de-risks the leverage/cap reconciliation;
(C2) the live-covariance path switches on once PR-I + ≥60 days of per-stream returns exist.
This lets the allocator be correct-by-construction from day one (prior mode) and become
adaptive later without a rewrite.

**Open questions for cc1:**
1. Reconcile the vol-target `scale_cap`: research uses `20×`, live `DollarNotionalSizer`
   caps `3×`. Which governs? (Recommend a hard live cap ≤3× + portfolio CB regardless of
   what the 12% target asks for.)
2. Own the canonical LW/ERC math where? Import from `exp2360` (no research edit) vs lift to
   `compass/live/covariance.py` (cleaner, but edits exp2360 to import back).
3. Per-stream equity reconstruction precision: realized-only vs realized+unrealized
   mark-to-market daily (affects covariance quality; MTM is better but needs live Greeks
   from PR-A).

---

## Addendum — PR-C as built (2026-05-28) · resolves the open questions

Module shipped: **`compass/live/vrp_risk_parity.py`** + `tests/test_vrp_risk_parity.py`
(23 tests, all passing). Two recon claims diverged from reality and were resolved:

- **Open-Q2 resolved → LIFT, not import.** `compass/exp2360_robust_cov.py` runs heavy
  side-effect imports at module load (`exp2080.load_streams`, `exp2160`, `IronVault`),
  so importing it in the live worker is unsafe. The two pure functions (`cov_ledoit_wolf`,
  `risk_parity_weights`) are therefore **ported verbatim** into `vrp_risk_parity.py` as
  `ledoit_wolf_covariance` / `risk_parity_weights`. Math is identical (regression-tested
  against `sklearn.LedoitWolf` directly), so live weights == research weights. **cc1: if
  you'd rather the math be shared, lift these into `compass/live/covariance.py` and have
  `exp2360` import back — that edits a research file, so deferred.**
- **Open-Q1 resolved → live cap 3×.** `MAX_SCALE = 3.0` (not the research `20×`), matching
  `DollarNotionalSizer.max_leverage`. The vol-target multiplier is clamped to `[0.1, 3.0]`.
- **Prior file absent today (PR-0 dependency).** `load_prior_covariance()` reads
  `compass/live/data/vrp_prior_cov.json` if present; it isn't (the exp2250 pickle is still
  missing), so cold-start falls back to a **diagonal inverse-variance prior** (≈ equal-risk)
  — safe and neutral. Drop the real cube covariance into that path (PR-0/PR-E) and the
  loader picks it up automatically; no code change needed.

**Interface delivered to cc1 / PR-B** (see contract block at top of the module):
`compute_weights(returns_df, vol_target=0.12, *, scaled=False, min_live_days=60,
prior_cov=None) -> dict[str, float]` — default returns ERC weights summing to 1.0;
`scaled=True` returns vol-target-scaled gross exposure fractions. Plus
`scale_to_vol_target`, `cold_start_covariance`, `load_prior_covariance`,
`ledoit_wolf_covariance`, `risk_parity_weights`. **Open-Q3 (realized vs MTM returns)
remains open — it lives in the per-stream returns *provider* (PR-I/PR-B), not the allocator.**
