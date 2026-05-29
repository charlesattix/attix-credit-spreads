# EXP-V8A VRP — PR-A Recon: Live Multi-Symbol Data + IV Chains

**Scope:** PR-A (data layer only). **Author:** cc2 (data scout). **Date:** 2026-05-28.
**Status:** SHIPPED (branch `pr-a-vrp-live-data`). Recon + design below; the
implementation notes in §4 are updated to match what landed. Coordinated with cc1
(orchestrator); LW estimator (cc3) and VIX-ladder trigger (cc4) untouched.

> **IMPLEMENTATION UPDATE (post-recon, reconciled with reality):**
> - **Module landed at `compass/live/vrp_data.py`** (not `strategy/vrp_data_feed.py`
>   as first proposed). Reason: isolating it under `compass/live/` keeps it fully
>   additive — no shared `strategy/` file is touched, so no other experiment's data
>   path can be affected. Same class/dataclass names (`VRPDataFeed`, `VRPSnapshot`).
> - **Public API is three functions + the feed class** (cc1/cc3 code against these):
>   `get_bars(symbol, lookback)`, `get_iv_chain(symbol, dte_range)`,
>   `get_vix_realtime()`, plus `VRPDataFeed.snapshot()` for the coherent cross-section.
> - **Provider clarification confirmed in code:** IV chains come from **Polygon**
>   (`PolygonProvider.get_full_chain`), bars+VIX from **DataCache**. **Alpaca is NOT a
>   data source** (order-placement only) — so the task's "cache to avoid hammering
>   Alpaca / mock Alpaca responses" maps to caching/mocking Polygon+DataCache. The
>   recon already said this; flagging again because the task framing said "Alpaca."
> - **Futures (GC=F/SI=F) remain OUT OF SCOPE for PR-A** (blocker B1). `get_bars` on
>   those symbols degrades to empty rather than failing the scan.

> **Bottom line up front:** PR-A is *smaller and differently-shaped* than the build
> plan assumes. The build plan (PR-A, L89) calls for **"term-structure IV (two
> expiries) for the calendar streams."** That is wrong: `gld_cal`/`slv_cal` are **not
> options calendar spreads** — they are **ETF-vs-continuous-front-month-future basis
> trades** (`log(GLD) − log(GC=F)`). They need **futures price data (GC=F, SI=F)**, a
> *new data class the system has never touched*, and **no option chains at all.**
> Meanwhile the option-chain symbols the plan flags as new (QQQ, XLF, XLI, GLD, SLV)
> are **already reachable today** through the existing ticker-parameterized
> `PolygonProvider` — the gap there is orchestration/caching, not data access.
> Net: the equity-option + VIX half of PR-A is **~1 small PR (reuse)**; the futures
> half is **a flagged blocker coupled to execution (PR-C), not just data.**

---

## 1. What live data does VRP actually need?

Traced from `compass/exp2850_v8a_with_vix_ladder.py::build_v8a_cube` (L68-80) down
through each stream's definition. **Critical correction up front:** the exp2850 "cube"
is 8 columns of *pre-computed daily returns*. Several streams' return series are
generated from data classes that differ from what the build plan assumes. Here is what
each stream's live *entry* engine would actually need to consume:

| Stream | Structure (what the backtest series really is) | Symbols | Live data needed | Source file |
|---|---|---|---|---|
| `exp1220` | SPY put-credit spread (leveraged) | SPY | equity bars + option chain (IV/Greeks) | `experiments/EXP-1220-real` |
| `xlf_cs` | XLF put-credit spread | XLF | equity bars + option chain | `compass/exp2390…` (sparse) |
| `xli_cs` | XLI put-credit spread | XLI | equity bars + option chain | `compass/exp2390…` (sparse) |
| `qqq_cs` | QQQ put-credit spread | QQQ | equity bars + option chain | `compass/cache/exp2250_qqq_trades.pkl` *(missing — PR-0)* |
| `cross_vol` | vega-matched IV−RV pairs across surfaces | SPY, QQQ, XLF, XLI | **ATM ~30-DTE IV** (chain) + 20d realized vol (bars) | `compass/exp2020_cross_vol_arb.py` |
| `gld_cal` | **ETF − front-future basis** (NOT an options calendar) | GLD **+ GC=F** | GLD bars **+ continuous front-month gold-future close** | `compass/exp1770_commodity_calendars.py:86-96` |
| `slv_cal` | **ETF − front-future basis** (NOT an options calendar) | SLV **+ SI=F** | SLV bars **+ continuous front-month silver-future close** | `compass/exp1770_commodity_calendars.py:86-96` |
| `v5_hedge` | trend/crisis-alpha overlay (long/short **shares**, vol-targeted) | 13 ETFs† | daily **closes only** — no options | `compass/crisis_alpha_v3.py:36-45` |
| VIX ladder | exposure multiplier on new-entry sizing | `^VIX` | latest VIX close (scalar) | `compass/vix_ladder.py:179-194` |

† `v5_hedge` universe (`UNIVERSE_V3`): **SPY, IWM, EFA, EEM, QQQ, TLT, LQD, HYG, GLD,
USO, DBA, DBB, UUP** — all liquid equity/ETF daily closes; executed as directional
share positions, not options.

### Data-class summary (this is the real PR-A surface)
1. **Equity option chains w/ IV + Greeks** for SPY, QQQ, XLF, XLI, GLD, SLV — for the 4
   credit-spread streams and cross_vol's ATM-IV read.
2. **Realized vol (20d)** on SPY/QQQ/XLF/XLI — derived from daily bars (already available).
3. **Continuous front-month futures closes**: **GC=F, SI=F** — *NEW data class*, for the
   two basis ("calendar") streams.
4. **13 ETF daily closes** for `v5_hedge` — bars only.
5. **Latest VIX scalar** for the ladder.

> The backtest never models per-trade options for GLD/SLV. Building those streams as
> *options* calendars (build-plan PR-C) would be a **new strategy that does not
> reproduce the cube's Sharpe 6.39.** Flagged to cc1 — see §5.

### Bar granularity
The live worker is **EOD/intraday-snapshot, not tick**. Entries scan a handful of times
per day (`SLOT_SCAN` = ~14 scans/day, `main.py`); chains are pulled per scan via Polygon
*snapshot* endpoints (point-in-time, work after hours). Daily bars (period `2y`) drive RV
and IV-rank. **No 1-min/5-min bar feed is required** by any VRP stream — all stream
signals are daily-frequency. This is a meaningful de-scope vs. a generic "live data" build.

---

## 2. What the existing system has today

The live data layer is **already multi-symbol and already fetches IV/Greek chains**, via
Polygon (primary) → Tradier (fallback) → yfinance (last resort), unified by
`OptionsAnalyzer`. **Alpaca is order-placement only** — it does *not* sell IV chains;
`AlpacaProvider` just resolves OCC symbols (`find_option_symbol`) and submits legs.

### Option chains / IV / Greeks — `strategy/polygon_provider.py`
- `get_full_chain(ticker, min_dte=25, max_dte=50)` — **L246-292**. Ticker-parameterized.
  Returns a DataFrame across the DTE window from `/v3/snapshot/options/{ticker}`.
- `get_options_chain(ticker, expiration)` — **L215-244**. Single-expiry.
- `_build_option_row(...)` — **L132-180**. Standardized row schema (see §6).
- `get_expirations(ticker)` — **L202-213**. Lists all available expiries (term structure).
- Rate limit: **5 calls/sec** (`min_call_interval = 0.2s`, L58); `Retry(total=5…)`;
  circuit breaker (5 failures / 60s).

**Key fact:** these methods take `ticker` as an argument. **None of the chain path is
hardcoded to SPY.** Fetching QQQ/XLF/XLI/GLD/SLV chains works *today* by passing the
symbol — the only question is Polygon coverage/liquidity, not new code.

### Live VIX — already solved
- `shared/data_cache.py::DataCache.get_history('^VIX', period=…)` auto-translates
  `^VIX → I:VIX` (and `^VIX3M → I:VIX3M`) via `_INDEX_TICKER_MAP` (**L29-38**) and pulls
  daily bars from Polygon. TTL cache 900s. The ladder needs only the **latest close**,
  which is `get_history('^VIX','5d').Close.iloc[-1]`. **VIX3M term structure is also
  already available** (`I:VIX3M`) should cross_vol/regime want it.

### Which symbols are already wired live
- **SPY** — primary, full coverage (bars + chains + orders).
- **QQQ** — `config.yaml` default ticker (`tickers: [SPY, QQQ, IWM]`); chain-accessible.
- **XLF, XLI** — in `compass/macro_db.py::LIQUID_SECTOR_ETFS` (`["XLE","XLF","XLV","XLK",
  "XLI","XLU","XLY"]`, **L64**); selected dynamically by COMPASS (`main.py::
  _get_compass_universe`, L191-252) and their chains *are fetched in production today*
  when selected.
- **GLD, SLV** — referenced only in **backtest/research** (`compass/exp1770…`,
  `crisis_alpha_v3`); never fetched in the live worker. But they are standard US-listed
  ETFs, so `PolygonProvider.get_full_chain("GLD"|"SLV", …)` will work without new code,
  and Alpaca options trading covers their listed options.
- **^VIX / ^VIX3M** — bars available live via DataCache.

### Genuinely NEW for VRP (never touched by the live path)
| New item | Class | Available via existing code? | Notes |
|---|---|---|---|
| GLD, SLV option chains | equity options | ✅ yes (`get_full_chain`) | only if we (re)interpret calendars as options — see §5 |
| **GC=F, SI=F futures closes** | **futures** | ❌ **no** | new data class; `DataCache`/Polygon equity path doesn't cover futures |
| v5_hedge tail of universe (EFA, EEM, TLT, LQD, HYG, USO, DBA, DBB, UUP) | equity bars | ✅ yes (`DataCache.get_history`) | bars only; no chains |
| Latest-VIX-as-scalar accessor | derived | ✅ trivial | one helper over existing bars |

### Cost of adding the new symbols
- **Equity option chains (QQQ/XLF/XLI/GLD/SLV):** **~zero marginal cost.** Same Polygon
  options entitlement already used for SPY/sector ETFs. Adds ~5 paginated snapshot calls
  per scan cycle. At 5 calls/sec + ~14 scans/day this is comfortably inside limits
  (each `get_full_chain` is a few pages). No new subscription.
- **Equity daily bars (v5_hedge ETFs):** ~zero — same Polygon stocks entitlement,
  cached 900s; ~9 extra tickers fetched once/window.
- **Futures (GC=F, SI=F):** **real cost / blocker.** Polygon futures is a **separate paid
  entitlement** not currently provisioned (`POLYGON_API_KEY` / `POLYGON_INDICES_API_KEY`
  are stocks+indices). yfinance serves `GC=F`/`SI=F` free but is fragile (the repo
  already carries a LibreSSL `curl` workaround for Yahoo). **And Alpaca cannot execute
  futures at all** — so this is not only a data gap.

---

## 3. Gap analysis

### What PR-A must build (code)
1. **Multi-symbol chain snapshot** (`VRPDataFeed.snapshot()`, §4): loop
   `PolygonProvider.get_full_chain` over `[SPY,QQQ,XLF,XLI,GLD,SLV]` in one cycle and
   return a single immutable, same-timestamp snapshot object. *Reuses* the existing
   provider — no new fetch logic. **Small.**
2. **Live-VIX scalar accessor**: `latest_vix()` over `DataCache.get_history('^VIX','5d')`.
   **Trivial.** (cc4 consumes this; PR-A just exposes it.)
3. **cross_vol live ATM-IV read**: cross_vol's backtest pulls ATM ~30-DTE IV from the
   **`IronVault` SQLite cache** (`shared/iron_vault.py` — *backtest-only, `offline_mode=
   True`, never live*). Live equivalent = read `iv` (or BS-invert the mid) from the
   PolygonProvider snapshot for SPY/QQQ/XLF/XLI. **Small** — data is in the §1 snapshot;
   this is a thin adapter. (Execution of cross_vol itself is cc-D's PR-D scope.)
4. **v5_hedge bar bundle**: `daily_closes([...13 ETFs...])` over `DataCache`. **Small.**
5. **Futures price feed** (GC=F, SI=F): a `FuturesPriceFeed` adapter behind an interface.
   **Medium + risky** — source undecided (Polygon-futures entitlement vs yfinance-curl).
   **Keep isolated** so the credit-spread MVP doesn't block on it.

### Blockers
- **B1 — Futures data + execution (the big one).** `gld_cal`/`slv_cal` need GC=F/SI=F
  continuous-front closes (no current entitlement) *and* a way to trade the basis. Alpaca
  has **no futures**. Options to surface to cc1: (a) buy Polygon futures data + add a
  futures broker (large, out of PR-A); (b) **proxy the future with an ETF** (e.g. trade
  GLD vs a gold-miners/futures-tracking ETF) — changes the strategy; (c) **defer
  calendars** (MVP path in build plan L123). PR-A can deliver the *data adapter* for (a)
  but cannot resolve execution.
- **B2 — Missing `exp2250_qqq_trades.pkl`** (build-plan PR-0). The `qqq_cs` *return
  series* can't be reproduced offline today. Doesn't block live QQQ chain access (which
  works now), but blocks backtest re-validation. Not PR-A's to fix; noted for cc1.
- **B3 — `compass/cache/` absent in workspace.** All stream caches regenerate at runtime
  from Yahoo/IronVault. Backtest reproducibility concern, not a live-data blocker.

### Effort estimate (PR-A, split for shippability)
| Sub-PR | Content | Size | Unblocks |
|---|---|---|---|
| **PR-A1** | Multi-symbol equity-chain snapshot + `latest_vix()` + v5_hedge bar bundle | ~1 day, S | MVP credit-spread streams + cross_vol IV + VIX ladder |
| **PR-A2** | cross_vol live ATM-IV adapter (off the PR-A1 snapshot) | ~0.5 day, S | cross_vol entry signal (PR-D) |
| **PR-A3** | Futures feed (GC=F/SI=F) behind interface | ~2-3 days, M + **blocked by B1** | calendar streams (PR-C) — only if execution resolved |

**PR-A1+A2 alone is the data foundation for the MVP** (build-plan L123: credit-spread
streams + allocator + ladder). That matches the plan's "~1 PR, medium" for PR-A, minus
the (incorrectly-scoped) term-structure-IV work. PR-A3 should be **gated behind a cc1
decision on B1** and is genuinely optional for the MVP.

---

## 4. Architecture proposal

### One multi-symbol abstraction, not per-symbol adapters
Build a single **`VRPDataFeed`** (new module, e.g. `strategy/vrp_data_feed.py`) that
produces **one coherent, same-timestamp snapshot per scan cycle** — *not* a per-symbol
adapter each fetching on its own clock.

**Rationale:** the risk-parity allocator (PR-E) and cc3's LW estimator attribute
per-stream daily returns and build a cross-sectional covariance. They need every stream's
entry/mark data pinned to **the same instant**. Staggered per-ticker fetches (today's
`ThreadPoolExecutor(4)` per-ticker model in `main.py`) would smear timestamps across the
cross-section and bias the covariance. A single snapshot object is the clean seam.

```python
# strategy/vrp_data_feed.py  (sketch)
@dataclass(frozen=True)
class VRPSnapshot:
    as_of: datetime                       # single timestamp for the whole cross-section
    chains: dict[str, pd.DataFrame]       # {"SPY": <_build_option_row schema>, ...}
    spot:   dict[str, float]              # last underlying price per symbol
    rv20:   dict[str, float]              # 20d realized vol (SPY/QQQ/XLF/XLI)
    futures: dict[str, float]             # {"GC=F": .., "SI=F": ..}  (may be {} if deferred)
    hedge_closes: pd.DataFrame            # 13-ETF daily closes for v5_hedge
    vix: float                            # latest VIX close (ladder input)

class VRPDataFeed:
    def __init__(self, polygon: PolygonProvider, cache: DataCache,
                 futures: FuturesPriceFeed | None = None): ...
    def snapshot(self) -> VRPSnapshot:    # one cycle = one consistent cross-section
        ...
```

- **Equity chains:** loop `PolygonProvider.get_full_chain(sym, min_dte, max_dte)` over the
  6 symbols. Pure reuse.
- **Futures:** delegate to an injected `FuturesPriceFeed` (interface), `None`-able so the
  MVP runs without it. Isolates blocker B1.
- **VIX / bars / RV:** reuse `DataCache`.

### How cc3's LW estimator gets fed — **clarify the boundary**
PR-A's snapshot feeds the **entry/sizing** path (chains, IV, futures, VIX) so each stream
can *place trades*. **It does NOT feed the LW estimator directly.** The LW covariance runs
on **realized per-stream daily-return time series**, which come from **PnL attribution in
the PositionMonitor / `trades` table (build-plan PR-H / PR-I)** — not from the chain
snapshot. So the data flow is:

```
VRPDataFeed.snapshot() ─┬─> per-stream entry engines (PR-B/C/D)  → orders (Alpaca)
                        └─> VIX scalar → ladder (cc4) → entry sizing
                                              │
realized fills → trades(stream,symbol) → daily per-stream returns ──> cc3 LW estimator (PR-E)
                                                                       → risk-parity weights
```

**Action for cc3:** do not expect chain/IV data from PR-A's feed; the LW input is the
realized-returns matrix produced downstream. PR-A only guarantees a clean same-timestamp
*entry* cross-section. (Confirmed boundary with cc1 recommended.)

### Caching strategy — per-cycle in-memory snapshot, **no Redis**
- The worker is **one process per experiment** (`railway_worker.py` spawns
  `main.py scheduler` subprocesses). There is no cross-service fan-out that would justify
  Redis. **Pass the immutable `VRPSnapshot` object** to all 8 stream builders + the
  allocator within a cycle → guarantees consistency and zero re-fetch.
- **Daily bars** already have the right cache: `DataCache` TTL 900s, plus the optional
  cross-process SQLite bar cache (`USE_SHARED_CACHE`, `shared/data_cache.py:84-98`).
  Reuse it for the 13 v5_hedge closes and (if Polygon-sourced) futures bars.
- **Option chains** are intraday and *not* cached across scans by design (the system
  re-pulls per scan today) — correct for VRP too; the snapshot just dedupes within a
  single cycle.
- Net: **in-memory per-cycle snapshot + existing `DataCache` for bars.** No new infra.

---

## 5. Coordination notes (for cc1 / cc3 / cc4)

- **cc1 — scope correction (high priority):** build-plan PR-A line 89 ("term-structure IV
  for the calendar streams") is based on a misread. `gld_cal`/`slv_cal` are **futures-
  basis** trades, not options calendars. Real PR-A data need is **GC=F/SI=F futures
  closes**, and the true blocker is **execution** (Alpaca has no futures), which couples
  PR-A3 to PR-C and to a broker/data decision. Recommend the **MVP path** (credit-spread
  streams + cross_vol + ladder; defer calendars) unless a futures execution venue is
  approved. The MVP's PR-A surface (PR-A1+A2) is **~1 small PR of reuse**.
- **cc3 (LW estimator):** your input is the **realized per-stream daily-return matrix**
  from PnL attribution (PR-H/I), *not* PR-A's chain snapshot. PR-A guarantees a clean,
  same-timestamp entry cross-section only. Plan your cold-start seed accordingly.
- **cc4 (VIX ladder):** the **latest-VIX scalar** is available today via
  `DataCache.get_history('^VIX','5d')` (Polygon `I:VIX`). PR-A will expose
  `VRPDataFeed.snapshot().vix`; you consume it for entry-sizing. Note source differs from
  the backtest (live Polygon `I:VIX` vs backtest Yahoo `^VIX` EOD) — values match closely
  but confirm sign/lag conventions (backtest uses causal shift-1d).

## 6. Reference: standardized option-chain row schema
Produced by `PolygonProvider._build_option_row` (`strategy/polygon_provider.py:132-180`);
matched by Tradier/yfinance providers, so the snapshot schema is provider-agnostic:

```
contract_symbol, strike, type{call|put}, bid, ask, last, volume, open_interest,
iv, delta, raw_delta, gamma, theta, vega, mid, expiration, itm
```
After-hours fallback: when `last_quote` is empty, `bid=ask=day.close` (L153-156); rows
with no pricing are dropped (`bid>0 & ask>0`, L242/290). IV/Greeks come straight from
Polygon's snapshot `greeks` block — sufficient for both credit-spread sizing and
cross_vol's ATM-IV read (no client-side BS inversion needed in the live path, unlike the
backtest's `invert_iv` in `exp2020`).

---

### Appendix — exact source anchors
- VRP cube + streams: `compass/exp2850_v8a_with_vix_ladder.py:68-80`
- Calendar = futures basis: `compass/exp1770_commodity_calendars.py:56-96` (PAIRS, `load_pair`)
- cross_vol IV via IronVault: `compass/exp2020_cross_vol_arb.py:79-85,130-159`
- v5_hedge universe: `compass/crisis_alpha_v3.py:36-45`
- Live chains/IV/Greeks: `strategy/polygon_provider.py:132-180,215-292`
- Live VIX translation: `shared/data_cache.py:29-38`, `_get_provider` index key `:114-120`
- Order placement (chains NOT here): `strategy/alpaca_provider.py` (`find_option_symbol`, `submit_credit_spread`)
- Symbol selection live: `main.py:191-252` (`_get_compass_universe`), `compass/macro_db.py:64` (`LIQUID_SECTOR_ETFS`)
- IronVault is backtest-only: `shared/iron_vault.py:29-39` (`offline_mode=True`, cache-only)
