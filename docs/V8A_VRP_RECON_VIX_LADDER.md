# EXP-V8A VRP — Recon & Design: VIX Ladder Live Trigger

**Status:** ✅ SHIPPED (PR-D). Recon + design below; implementation landed. **Date:** 2026-05-28.
**Scout:** PR-D assignment (VIX ladder live trigger). **Coordinator:** cc1.
**Companion docs:** `docs/V8A_VRP_BUILD_PLAN.md` (master plan).

> ### 📦 IMPLEMENTATION UPDATE (shipped) — supersedes the proposed naming in §5
> The recon proposed `compass/vrp/vix_exposure.py::current_exposure_multiplier()`.
> Per cc1's PR-D deliverable spec, the **shipped** module and public API are:
>
> - **Module:** `compass/live/vrp_vix_ladder.py` (NOT `compass/vrp/vix_exposure.py`).
>   Lives in the `compass.live` package (shared with cc2's PR-A `vrp_data`; we
>   ship a verbatim copy of cc2's `__init__.py` to keep the merge clean).
> - **Public API (interface contract for cc1's PR-B):**
>   - `get_current_vix() -> float` — live VIX (Polygon `I:VIX` → yfinance `^VIX`
>     via the existing `scheduler.data_providers.get_vix_values`) with graceful
>     fallback to a fresh last-known value; raises `VixFeedUnavailable` only when
>     live is down AND last-known is stale/absent.
>   - `vix_ladder_signal(current_vix: float) -> dict` — pure: `{vix,
>     sizing_multiplier, entry_gate, exit_gate, regime, per_stream{...}, source,
>     degraded, halted}`. `sizing_multiplier` is the **exact EXP-2820 ladder**
>     (reuses `VIXLadder`, so it reproduces exp2850).
>   - `resolve_vix_ladder_signal() -> dict` — **recommended monitor entrypoint**;
>     wires fetch+fallback, returns the HALT signal on `VixFeedUnavailable`, never
>     raises. Call once per Tier-2 (5-min) scan and distribute to all streams.
>   - `V8A_STREAMS`, `class VixFeedUnavailable(RuntimeError)`.
> - **Live-VIX source:** the existing `get_vix_values()` (does **not** depend on
>   cc2's unmerged PR-A). `_fetch_live_vix()` is the single seam to repoint at
>   cc2's `compass.live.vrp_data` accessor once that lands.
> - **Gates:** `ENTRY_BLOCK_VIX=35` / `EXIT_ALL_VIX=45` mirror the live circuit
>   breaker (`scheduler/jobs.py:295-296`). These live-only gates are NOT in
>   exp2850 (which has no entry/exit gating) — only `sizing_multiplier` reproduces
>   the backtest. **Open item for cc1:** per-stream hedge inversion for `v5_hedge`
>   is intentionally NOT decided here (kept uniform = exp2850-faithful); the
>   `per_stream` dict is the seam for that override in PR-E/PR-H. (§5.1)
>
> Everything below is the original recon; treat the box above as authoritative
> where it differs.

> ⚠️ **Lettering discrepancy to reconcile with cc1.** This assignment is labeled
> "PR-D" by the orchestrator, but in `V8A_VRP_BUILD_PLAN.md` the **VIX ladder
> live trigger is PR-F** (§3); "PR-D" in that plan is *cross-vol arbitrage + v5
> hedge*. This doc covers the **VIX ladder** (the output filename and task body
> both specify it). Treat "PR-D" here as the orchestrator's task ID, not the
> plan's PR-D.

---

## 0. TL;DR

- The VIX ladder is a **pure VIX→exposure-multiplier function** that already
  exists and is live-ready: `compass/vix_ladder.py::VIXLadder` (EXP-2820 winner,
  9 breakpoints, causal shift-1d). **No new math needed.**
- In the **backtest** (`exp2850`) it multiplies *daily portfolio return series*.
  In **live** it must instead **scale new-entry position sizing** each cycle —
  this is the only conceptual change, and it's small.
- A **live VIX fetch already exists** and is battle-tested:
  `scheduler/data_providers.py::get_vix_values()` (Polygon `I:VIX`/`I:VIX3M` →
  yfinance `^VIX` fallback). **We should consume this, not build a new feed.**
  *(Boundary note: cc2 owns the data-feed track — coordinate so the ladder reads
  cc2's canonical live-VIX interface if they standardize one.)*
- **One real hazard found:** `VIXLadder.exposure_at(None)` and `apply()` return
  **`max_exposure` (1.0 = full leverage) on missing/NaN VIX** (`vix_ladder.py:100,
  145`). That "permissive fallback" is correct for research but **dangerous live**
  — a dead feed would imply *full* exposure. The live trigger MUST override this
  with a conservative fallback (last-known VIX, then halt-new-entries).
- **Effort:** ~0.5–1 PR / ~3–5 focused hours, *gated on the live-VIX interface
  (cc2 / existing `get_vix_values`) being agreed*. The ladder itself is trivial;
  the production hardening (stale-data policy, persistence, tests) is the work.

---

## 1. What is the VIX ladder in VRP?

### 1.1 The function (`compass/vix_ladder.py`)
`VIXLadder` maps a VIX level → an **exposure multiplier** in `[0, 1]` via
piecewise-linear interpolation over the **EXP-2820 winning breakpoints**
(`vix_ladder.py:45-55`):

| VIX | ≤20 | 25 | 30 | 35 | 40 | 50 | 60 | 70 | >70 |
|---|---|---|---|---|---|---|---|---|---|
| Exposure × | 1.00 | 0.90 | 0.75 | 0.60 | 0.50 | 0.35 | 0.25 | 0.15 | 0.00 |

- **Causal by default** (`causal=True`): `apply()` shifts the series by 1 day so
  *today's* exposure uses *yesterday's* VIX close (`vix_ladder.py:148-152`). This
  is the anti-look-ahead guarantee and matters for how we wire live (below).
- **Pure**: numpy/pandas only, no network, no IronVault. "Callers supply the VIX
  series" (`vix_ladder.py:30-33`). → **directly reusable live.**
- Provenance: reduced a VIX→80 flash-crash DD from 43.1% → 0.80% while lifting
  normal-regime Sharpe +0.49 (`vix_ladder.py:5-7, 42-44`).

### 1.2 What triggers entries/exits/sizing changes?
The ladder is **not an entry/exit signal** and **not a regime label**. It is a
**continuous sizing multiplier**. There are no discrete "trigger levels" — every
VIX value maps to a multiplier. Practically:
- VIX ≤ 20 → full size (1.0×).
- VIX rising 20→40 → size scaled down smoothly to 0.50×.
- VIX ≥ 70 → 0.0× (effectively flat / no new exposure).

It changes **sizing only**. Exits are owned by each stream's own logic (PT/SL,
calendar rolls) and by the separate VIX **circuit breaker** (see §3.2), which is
a *distinct* mechanism from the ladder.

### 1.3 Cash VIX or term structure?
**Cash (spot) VIX only.** The ladder takes a single VIX level. In `exp2850` the
input is **Yahoo `^VIX` daily close** (`exp2850…py:215-219`, `fetch_vix()` at
`vix_ladder.py:179-194` → `yf.download("^VIX")`). VIX3M / term structure is **not**
used by the ladder. (Term structure *is* used elsewhere — the regime detector and
the circuit breaker, §3 — but those are separate consumers.)

### 1.4 Backtest data source (the cached pickles)
The `exp2850` *cube* is cached, but **VIX is not pickled** — it is fetched live
from Yahoo at run time (`exp2850…py:218 → fetch_vix()`). The cached/pickled inputs
are the **return-stream cube**, specifically:
- 7-stream sparse cube via `compass.exp2450_sparse_combined_honest::build_sparse_seven_stream_cube` (`exp2850…py:69`).
- QQQ stream from `compass/cache/exp2250_qqq_trades.pkl` (`exp2850…py:57, 71`) —
  **currently MISSING on disk** per the master plan (PR-0 must regenerate it).

So: **ladder VIX source = Yahoo `^VIX` EOD (live download at backtest time)**;
cube source = EXP-2450 cube + EXP-2250 QQQ pickle. The ladder math is independent
of the cube cache.

---

## 2. Live VIX data options

| Option | Real-time? | Cost | Verdict for the ladder |
|---|---|---|---|
| **Alpaca** | n/a | included | ❌ **No VIX.** VIX is a CBOE index, not a tradable equity/option Alpaca distributes. Confirmed by absence of any VIX path in `alpaca_live.py`/`shared/`. |
| **Polygon `I:VIX` daily aggs** | EOD/last-close (intraday bar updates) | paid (already have key) | ✅ **Already wired** — `get_vix_values()` L1. Uses `POLYGON_INDICES_API_KEY`. |
| **Yahoo `^VIX` (`yfinance.fast_info`)** | ~15-min delayed spot | free | ✅ **Already wired** — `get_vix_values()` L2 fallback. |
| **CBOE direct real-time feed** | true real-time | paid (new vendor) | ➖ Overkill. The ladder is causal shift-1d / daily-rebalance scope; sub-minute VIX is unnecessary. |

### 2.1 The recommended option already exists
`scheduler/data_providers.py::get_vix_values()` (`L415-470`) returns
`(vix, vix3m)` with a graceful fallback chain:
1. **Polygon** `GET /v2/aggs/ticker/I:VIX/range/1/day/{start}/{end}` `sort=desc limit=1`
   → **most recent daily close** (`data_providers.py:435-449`). Index tickers route
   to `POLYGON_INDICES_API_KEY` via `_pick_key` (`data_providers.py:35-39, 311-331`).
2. **yfinance** `yf.Ticker("^VIX").fast_info["last_price"]` (`data_providers.py:453-468`).

**Important nuance for the design:** L1 returns a **daily close**, not intraday
spot. Intraday, Polygon's "today" daily bar may be a partial/forming bar or
yesterday's close; the yfinance fallback is a ~15-min-delayed spot. **This is
fine** — the ladder is causal (yesterday's close) and the strategy rebalances
daily, so we explicitly *want* the latest stable daily VIX, not a twitchy spot
print.

### 2.2 Practical cadence
`get_vix_values()` has **no internal cache** — each call hits Polygon. At a 5-min
scan cadence (§4) that's ~80 index calls/day, trivial for the Polygon plan, but
we should still **memoize within a scan cycle** (one fetch per cycle, shared) to
avoid redundant calls when multiple streams ask for the multiplier.

---

## 3. What's in the codebase today (existing VIX consumers)

There are **already three live VIX consumers** — we are adding a fourth (sizing),
and must not collide with them.

### 3.1 Scanner regime detection — LIVE (`main.py:443-470`, `compass/regime.py`)
- `main.py:456` fetches `data_cache.get_history('^VIX', period='2y')` (+ `^VIX3M`
  at `:463`) and feeds `vix_by_date`/`vix3m_by_date` into `ComboRegimeDetector`.
- `compass/regime.py` (this is the live detector — note the stale memory pointer
  to `ml/combo_regime_detector.py`, which **no longer exists**): VIX>25 → BEAR
  contribution, VIX>30 → HIGH_VOL, VIX>40 → CRASH (`regime.py:6-9, 127-137`);
  also uses VIX/VIX3M term-structure ratio.
- Source chain: `shared/data_cache.py::get_history` → Polygon `I:VIX` → yfinance →
  stale cache. TTL ~1h.

### 3.2 VIX circuit breaker — LIVE (`scheduler/jobs.py:293-344`)
- `job_circuit_breaker_check()` runs **every 30 min, 09:00–15:30 ET**.
- Calls `get_vix_values()`; thresholds: **VIX ≥ 35 → block new entries**
  (`VIX_CRISIS_BLOCK`), **VIX ≥ 45 → exit all** (`VIX_EMERGENCY_EXIT`),
  plus term-structure-inverted warning (`jobs.py:295-312`).
- Persists `data/circuit_breaker.json` (`CB_JSON`) with `{vix, vix3m, ts_inverted,
  alerts, …}` (`jobs.py:331-338`), surfaced to the dashboard via
  `scheduler/api.py:33-51`.

> **Design implication:** the circuit breaker and the ladder **overlap at the top
> of the VIX range** (CB blocks entries at 35; ladder is already at 0.60× at 35,
> 0.50× at 40). They are complementary, not redundant: the **CB is a hard binary
> gate**; the **ladder is a soft continuous scaler**. The ladder must sit *under*
> the CB — if the CB says "block", entries are blocked regardless of the ladder
> multiplier. Document this precedence (CB > ladder) so they don't fight.

### 3.3 Health monitor poll — LIVE (`scheduler/jobs.py:245`)
- `job_monitor_poll()` every 5 min 09:30–16:00 records current VIX into health.

### 3.4 Paper-trading snapshot — LIVE (`shared/live_snapshot.py`)
- Builds a `MarketSnapshot` including VIX history / IV-rank for paper trading.

### 3.5 Sentinel-watchdog — does it use regime/VIX?
- **`railway_watchdog.py`: NO.** It monitors worker liveness/heartbeat, Alpaca
  reachability, DB recency — **no VIX/regime consumption.**
- **Sentinel (`sentinel/…`): YES, indirectly.** A regime-parity gate shadow-runs
  the regime detector and checks VIX-data freshness; VIX>40 forces BEAR there too.
  This is a *monitoring/QA* consumer, not a sizing input. The ladder does not need
  to touch Sentinel, but Sentinel may later want a gate asserting "ladder exposure
  applied matches live VIX" (out of scope here — flag for PR-J acceptance).

---

## 4. Scan cadence / where the trigger fires

**Verified cadence (`shared/scheduler.py:46-58`):** `SCAN_TIMES` generates
`SLOT_SCAN` slots **every 5 minutes from 9:05–15:55 ET, Mon–Fri**
(`for h in range(9,16) for m in range(…,60,5)`). *(The module docstring at
`scheduler.py:10` says "every 30 min" — that comment is **stale**; the code
generates 5-min slots. Worth a one-line fix, but out of scope.)*

- There is **no literal "Tier-2" label** in the scheduler — the unified 5-min
  `SLOT_SCAN` *is* the de-facto intraday tier the task refers to.
- Scan timeout is 600s (`scheduler.py:32`).

**Conclusion:** a 5-min refresh is the natural cadence and is far finer than the
ladder needs (it's a daily-close-driven, causal multiplier). One VIX fetch per
scan cycle, memoized, is correct.

---

## 5. Architecture proposal

### 5.1 Where the trigger lives
**A new thin VRP-specific sizing adapter, NOT the scanner and NOT the monitor.**

Proposed module: `compass/vrp/vix_exposure.py` (new; VRP-namespaced so it doesn't
entangle the shared scanner). Responsibilities:
1. Fetch the latest live VIX via the **existing** `get_vix_values()` (or cc2's
   canonical interface if they expose one) — **read-only consumer, builds no feed.**
2. Hold a single `VIXLadder()` instance (EXP-2820 default).
3. Expose `current_exposure_multiplier() -> float` with a **hardened fallback**
   (see §5.3) — overriding the library's permissive 1.0-on-missing default.
4. Memoize the fetched VIX for the current scan cycle.

This multiplier is then consumed by the **PR-E risk-parity allocator** (cc3's
track) as the final scalar on per-stream new-entry capital:
`contracts = round( base_capital × rp_weight × vol_target_scale × **vix_ladder_mult** / per_contract_risk )`.

> **Ownership boundary:** the *multiplier* is mine (PR-D/F). *Applying* it inside
> the allocator/sizer is cc3's PR-E surface. I will define the interface
> (`current_exposure_multiplier()`), cc3 calls it. We must agree the call site so
> we don't both edit the sizer.

### 5.2 Backtest→live semantic change (the one real port)
- **Backtest:** `gross_laddered = gross_returns × ladder.apply(vix_series)` — a
  *return* multiplier on realized daily returns (`exp2850…py:146-149`).
- **Live:** apply the multiplier to **new-entry sizing at decision time** — you
  cannot multiply realized returns live. Causality is preserved naturally: at a
  9:05 entry decision we use the **latest available VIX close** (yesterday's, or
  today's once settled), which is exactly the ladder's `causal` intent. We
  therefore call `ladder.exposure_at(latest_vix)` (the scalar API,
  `vix_ladder.py:96`) and **do not** use the series `apply()`/shift in live —
  the shift is a backtest-vectorization detail.

### 5.3 Failure mode / fallback policy (CRITICAL — must override library default)
`VIXLadder` returns **1.0 (full exposure) on `None`/NaN** (`vix_ladder.py:100,
145`). Live, a dead feed must **never** imply full leverage. Proposed policy in
the adapter:

1. **Feed OK** → use live VIX → ladder multiplier. Persist `{vix, ts}` to a small
   state file (e.g. `data/vrp_vix_state.json`) on every successful fetch.
2. **Feed fails this cycle, last-known VIX is fresh** (≤ ~1 trading day /
   configurable `VRP_VIX_MAX_STALE_HOURS`, default 26h to span an overnight gap)
   → use **last-known VIX** and log a `DATA_FALLBACK` warning.
3. **Feed fails AND last-known is stale/absent** → **HALT NEW ENTRIES**
   (multiplier → 0.0), log ERROR, optionally Telegram alert. Existing positions
   are untouched (their exits are owned elsewhere). This is the fail-loud,
   fail-flat stance — consistent with the FIX #4 philosophy of not running blind.
4. **Defense in depth:** the §3.2 circuit breaker still independently blocks at
   VIX≥35 / exits at ≥45, so even a buggy ladder can't size into a crisis.

Reuse, do not reinvent: `get_vix_values()` already logs structured
`DATA_SOURCE`/`DATA_FALLBACK` lines — mirror that format.

### 5.4 Refresh cadence
Once per `SLOT_SCAN` cycle (5 min). Memoize within the cycle. No separate timer
needed — the allocator runs in the scan path, so the multiplier is recomputed
each cycle for free.

### 5.5 Data flow (proposed)
```
SLOT_SCAN (every 5 min, 9:05–15:55 ET)
   └─ VRP allocator (cc3, PR-E)
        ├─ rolling LW cov → risk-parity weights → 12% vol-target scale
        └─ × compass/vrp/vix_exposure.current_exposure_multiplier()   ← THIS TRACK
                 ├─ get_vix_values()  [Polygon I:VIX → yfinance ^VIX]   (existing / cc2)
                 ├─ VIXLadder().exposure_at(vix)                        (compass/vix_ladder.py, exists)
                 └─ fallback: last-known (fresh) → else HALT (0.0×)     (new, hardened)
   └─ Circuit breaker (existing, every 30 min) — hard gate ABOVE the ladder
```

---

## 6. Effort estimate

Assumes the live-VIX interface is settled (either the existing `get_vix_values()`
or cc2's canonical wrapper) — that's the **key dependency**.

| Work item | Size |
|---|---|
| `compass/vrp/vix_exposure.py` adapter (wrap `VIXLadder` + `get_vix_values`) | ~1.5h |
| Hardened stale/halt fallback + `data/vrp_vix_state.json` persistence | ~1h |
| Define `current_exposure_multiplier()` interface; coordinate call site with cc3 (PR-E) | ~0.5h |
| Unit tests (ladder values at breakpoints already covered in lib; add: fresh/stale/dead-feed fallback, CB-precedence note) | ~1h |
| Docs + wire into config flag (`VRP_VIX_LADDER_ENABLED`) | ~0.5h |
| **Total** | **~4.5h ≈ 0.5–1 PR** |

This matches the master plan's PR-F estimate ("~0.5 PR, small", `BUILD_PLAN.md:106-107`).
The ladder math is done; the deliverable is **safe live plumbing + fallback**.

### Dependencies & sequencing
- **Depends on:** a live-VIX accessor (exists today as `get_vix_values()`; confirm
  with **cc2** whether they're standardizing one — consume theirs if so).
- **Feeds into:** **cc3** PR-E allocator (it multiplies my scalar). Interface must
  be agreed before either side edits the sizer.
- **Independent of:** the exotic streams (PR-C calendars, PR-D-plan cross-vol/hedge).
  The ladder is stream-agnostic — it scales whatever aggregate new-entry capital
  the allocator produces.

---

## 7. Open questions for cc1 / other scouts
1. **Lettering:** confirm this is plan-PR-F (VIX ladder), not plan-PR-D
   (cross-vol/hedge). (§0)
2. **cc2 boundary:** is cc2 building a new canonical live-VIX interface, or do we
   standardize on the existing `get_vix_values()`? I should consume, not duplicate.
3. **cc3 boundary:** agree the `current_exposure_multiplier()` call site inside the
   PR-E allocator so we don't both touch the sizer.
4. **Stale threshold:** is 26h (one overnight gap) the right `VRP_VIX_MAX_STALE_HOURS`
   default before halting new entries? (Weekends/holidays span >2 days.)
5. **CB vs ladder precedence:** confirm the documented rule (circuit breaker is a
   hard gate that overrides the ladder multiplier) is the intended behavior.

---

*Recon only. No code written. Files inspected: `compass/vix_ladder.py`,
`compass/exp2850_v8a_with_vix_ladder.py`, `scheduler/data_providers.py`,
`scheduler/jobs.py`, `shared/scheduler.py`, `compass/regime.py`, `main.py`,
`railway_watchdog.py`/sentinel (via sweep). All file:line citations verified
against working tree at `origin/main` HEAD.*
