# Runbook — Shared SQLite cache rollout (Phase 2)

Per-experiment feature-flag rollout of the shared daily-bar cache
(`shared/shared_bar_cache.py`, proposal Option B). Goal: enable
`USE_SHARED_CACHE` for one experiment at a time, observe, and advance only when
clean — eliminating the cold-start Polygon 429 burst without risking signals.

> **Status gate:** Phases 0 (stagger/429) and 1 (cache layer, flag OFF) are
> merged + deployed. This runbook assumes the Phase 2 enablement PR
> (per-experiment flag scoping + `[cache]` logging) is also merged + deployed.

## How per-experiment scoping works

All experiment scanners run as subprocesses of **one** `attix-worker` container,
so a plain `USE_SHARED_CACHE` would flip every experiment at once. `railway_worker.py`
maps a **suffixed override** `USE_SHARED_CACHE_<SUFFIX>` → `USE_SHARED_CACHE` for
that subprocess only (same pattern as `ALPACA_API_KEY_<SUFFIX>`).

| Experiment | Env var to set on `attix-worker` |
|---|---|
| EXP-3309 | `USE_SHARED_CACHE_EXP3309=true` |
| EXP-3303b | `USE_SHARED_CACHE_EXP3303B=true` |
| EXP-3311 | `USE_SHARED_CACHE_EXP3311=true` |
| EXP-400 | `USE_SHARED_CACHE_EXP400=true` |
| EXP-401 | `USE_SHARED_CACHE_EXP401=true` |
| EXP-503 | `USE_SHARED_CACHE_EXP503=true` |
| EXP-1220 | `USE_SHARED_CACHE_EXP1220=true` |
| EXP-V8A | `USE_SHARED_CACHE_EXPV8A=true` |
| EXP-800 | `USE_SHARED_CACHE_EXP800=true` (LAST, only when flat) |

The suffix = experiment id with the dash removed, upper-cased.

## Enablement procedure (one experiment)

1. **Pre-check:** target experiment is **flat** (no open positions) and markets
   are closed or quiet. Never enable EXP-800 while it holds legs.
2. Set the experiment's env var on the **attix-worker** service via Railway
   GraphQL `variableUpsert` (project dynamic-charm, env prod, service
   `f76f1342-...`), e.g. `USE_SHARED_CACHE_EXP3309 = true`.
3. **Redeploy the worker** — env is read at subprocess spawn, so the flag only
   takes effect on a fresh worker deployment.
4. Confirm the override is live in the boot logs:
   `[EXP-3309] USE_SHARED_CACHE=true (per-experiment override)`.
5. Begin the soak (Wave A = 24h; Waves B–D = 3 days each).

## Rollout waves (least → most risky)

| Wave | Experiments | Soak |
|---|---|---|
| A | EXP-3309 | 24h |
| B | EXP-3303b, EXP-3311 | 3 days each |
| C | EXP-400, EXP-401, EXP-503 | 3 days each |
| D | EXP-1220, EXP-V8A | 3 days each |
| LAST | EXP-800 | only after all others stable **and** it is flat |

Advance to the next wave only if the prior wave met all "clean" criteria.

## Observability (worker logs)

The cache emits one greppable line per resolution:
`[cache] exp=<id> ticker=<TICKER> outcome=<outcome>`
outcomes: `l1_hit`, `shared_fresh`, `shared_stale`, `miss_fetch`,
`peer_wait_hit`, `wait_timeout_fetch`, `direct_fetch`.

```bash
# Per-experiment cache outcomes (last deployment's logs saved to a file LOG):
grep "\[cache\] exp=EXP-3309" LOG | grep -oE 'outcome=[a-z_]+' | sort | uniq -c

# Cache HIT ratio = (l1_hit + shared_fresh + peer_wait_hit) / all [cache] lines.
# A healthy migrated experiment is mostly l1_hit/shared_fresh after warm-up;
# miss_fetch should be rare (cold start only), wait_timeout_fetch ~0.

# Polygon request volume from this experiment (proxy for redundant fetching):
grep "\[cache\] exp=EXP-3309" LOG | grep -cE 'outcome=(miss_fetch|direct_fetch|wait_timeout_fetch)'

# Regime classification (must match peers on the same minute — no drift):
grep "ComboRegime" LOG | grep -i spy | tail

# Any cache/Polygon error or stale warning (should be ZERO):
grep -iE "Shared cache|429|too many|stale|SharedCacheError|Failed Polygon" LOG

# Signal / order parity vs peer experiments (ticker, side, strike, fills):
grep -E "alerts_exp3309|cs-.*exp3309" LOG | tail
```

Compare EXP-3309's regime / signal / strike / fills against a **non-migrated peer
experiment** on the same scan minute — they must agree.

## Abort criteria — roll back the flag if ANY:

- Cache hit ratio `< 50%` after 1h warm-up.
- Regime classification differs from peer experiments on the same minute.
- Any Polygon-related ERROR or stale-data warning attributable to the cache.
- Trade signal differs in **ticker, side, or strike** from the peer baseline.

## Rollback

1. Set the experiment's `USE_SHARED_CACHE_<SUFFIX>=false` on `attix-worker`
   (explicit `false` overrides any global), **or delete the var** to fall back to
   the global default (OFF).
2. Redeploy the worker.
3. Confirm boot log shows the override gone / `false`, and `[cache]` lines for
   that experiment return to `l1_hit` / `direct_fetch` only.
4. The cache is best-effort and never required, so rollback is immediate and
   safe — no data migration to undo (the `shared_bars.db` file is just left in
   place, unused by that experiment).

## Notes / safety

- The shared cache is read-through + write-through with graceful fallback: a
  corrupt or missing `shared_bars.db` makes the experiment fall back to direct
  Polygon fetches (current behaviour), never a hard failure.
- Options chains stay on UnusualWhales and are unaffected by this flag.
- No deploy during market hours unless explicitly cleared.
