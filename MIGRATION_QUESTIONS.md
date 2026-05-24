# Migration Questions for Carlos

## Q1 — Dividend-adjustment behavior change (Phase 1 finding)

**RESOLUTION (2026-05-22, Carlos via Charles):** ACCEPTED. Polygon's splits-only
adjustment is the more correct semantics for an options-trading system — option
strikes are not dividend-adjusted, so historical MAs/RSI on splits-only series
compare like-for-like to live strike levels. The previous yfinance behavior was
a latent bug that this migration silently fixes. No code change.

---

**Context:** The previous `DataCache` used `yfinance.Ticker(t).history(period='1y')`,
which defaults to `auto_adjust=True`. yfinance with `auto_adjust=True` applies
**both split and dividend back-adjustment** to historical OHLC.

Polygon's `adjusted=true` (the default we send) applies **split adjustment only**.

**Measured impact (2026-05-22, last 120 trading days, Close):**
- SPY: yfinance-default vs Polygon ≈ **0.57% constant offset** before recent
  dividend dates
- TLT: yfinance-default vs Polygon ≈ **1.89% constant offset** before recent
  distributions
- ^VIX, ^VIX3M, ^GSPC: no dividends → identical (after one ^VIX 2026-02-06
  vendor data discrepancy)

**Apples-to-apples test (`yfinance auto_adjust=False` vs Polygon):**
all four reference tickers agree within 0.1% on every bar except one ^VIX
day (vendor mismatch — not the migration's doing).

**The semantic change:** after this migration, every alert/scanner reading
historical SPY/TLT/sector-ETF prices will see prices ~0.5–2% higher in the
past than before. This shifts all backward-looking indicators (MA20, MA50,
MA200, RSI14, percent-change) by the same fraction.

**Decision needed:** is this acceptable? My read:

1. **For options/strikes**, split-only is more correct — option strikes are not
   dividend-adjusted, so MAs computed on split-only series compare like-for-like
   to live strike levels. This argues **the previous behavior was a latent bug**
   and the migration silently fixes it.
2. **For threshold triggers** (e.g. "price > MA200"), the constant offset is in
   the same direction on both numerator and denominator → ratios are unchanged.
   So the bull/bear regime detection should be unaffected.
3. **For absolute price comparisons** (e.g. "price < $X"), magnitudes shift.
   I don't see any such comparison in the live path, but worth a sanity check.

**My recommendation:** accept the change (it's the more correct semantics for
options analysis). Document it in `MIGRATION_NOTES.md`. No code change.

If you want strict bit-identical behavior, the alternative is to apply the
yfinance dividend adjustment manually — but that re-introduces a yfinance
dependency on the live path, defeating the migration.

---

## Q2 — Phase 2 BLOCKED: Polygon plan lacks upcoming earnings dates

**RESOLUTION (2026-05-22, Carlos via Charles):** RESOLVED via Unusual Whales.
`shared/earnings_calendar.py` migrated to UW (see `shared/uw_client.py`).
Polygon plan upgrade not needed. `import yfinance` removed from the file —
zero references remain on the live trade-decision path.

Note: the brief expected UW's `/api/stock/{ticker}/earnings` to return a
pre-computed `expected_move_perc`. **It does not.** Live probe of the
endpoint returned only `report_date`, `reported_eps`, `estimated_eps`,
`surprise`, `surprise_percentage`, `report_time` (verified against
`AAPL`, 2026-05-22). No dedicated UW expected-move endpoint exists in
the documented skill.md.

Therefore the ATM-straddle math in `calculate_expected_move(options_chain,
current_price)` is **retained** — it has no yfinance dependency, operates
on a chain the caller already fetches, and is the only available source
of an implied expected move given the UW response shape. Net code change
in this file is a reduction (yfinance branches deleted, UW-backed branches
added) but the ATM-straddle helper survives.

---

**Probed endpoints (2026-05-22):**

| Endpoint | Status | Notes |
|---|---|---|
| `/vX/reference/tickers/{T}/events` | OK | Only `ticker_change` events — no earnings |
| `/vX/reference/tickers/{T}/earnings` | 404 | Endpoint does not exist |
| `/v1/meta/symbols/{T}/earnings` | 404 | Legacy endpoint removed |
| `/benzinga/v1/earnings?ticker={T}` | 403 NOT_AUTHORIZED | Subscription required |
| `/benzinga/v1/earnings/calendar` | 404 | Endpoint shape unknown / inaccessible |
| `/vX/reference/financials?ticker={T}` | OK | Filing dates of **past** quarters only |

**Conclusion:** the current Polygon plan does not include forward-looking
earnings dates. The Benzinga-backed earnings endpoints require a plan
upgrade.

Per the brief — "If the Polygon plan does not include upcoming earnings
dates, stop and write a note to MIGRATION_QUESTIONS.md. Do not invent a
fallback." — I am stopping Phase 2 and NOT modifying
`shared/earnings_calendar.py`.

**Decision needed (one of):**
1. **Upgrade the Polygon plan** to include Benzinga earnings, then
   re-trigger Phase 2.
2. **Leave `earnings_calendar.py` on yfinance** for now (accept the
   one-file remaining yfinance dependency on the live path). This means
   `import yfinance as yf` will remain inside the module — Phase 5
   (delete yfinance from requirements.txt) cannot complete cleanly until
   this is resolved.
3. **Wire in a different earnings source** (e.g. FMP, Alpaca, NASDAQ
   calendar). Outside the brief — would need explicit scope expansion.

Phase 3 (scanner fallback removal) does NOT depend on Phase 2 output and
proceeded independently.

I am NOT touching `shared/earnings_calendar.py` in this session.

---

## Q3 — `get_ticker_obj` callers outside the live OHLCV path

**Status:** resolved without escalation, but worth flagging.

`shared/data_cache.py::get_ticker_obj` is referenced by two live-path files:

- `compass/features.py:447` — uses `stock.calendar` (earnings date). Wrapped in
  `try/except`; gracefully degrades to `days_to_earnings=999` if it raises.
- `strategy/options_analyzer.py:118` — uses `stock.options` and
  `stock.option_chain(...)` as a fallback when neither Tradier nor Polygon
  options providers are configured. Wrapped in `try/except`; degrades to empty
  DataFrame.

After Phase 1, both callers will silently lose access to those features. In
production both paths have alternatives configured (Polygon options client,
earnings_calendar.py), so this is fine. **I did not modify those files** —
Phase 3 explicitly enumerates the five alerts/* scanners and not strategy/ or
compass/. Flagging here so Phase 5 can address fully if desired.

The Phase-2 rewrite of `shared/earnings_calendar.py` makes the compass
fallback path doubly redundant.

---

## Q5 — D4 backtest migration: branch base contradiction with PolygonClient requirement

**Raised:** 2026-05-22, during attempted execution of `MIGRATION_D4_BACKTEST_TASK.md`.
**Status:** BLOCKED. No code changed. No branch created. No push attempted.

### The contradiction

`MIGRATION_D4_BACKTEST_TASK.md` (the execution wrapper) says:

> Create `feature/migrate-backtest-to-polygon` off `main` (NOT off PR #34 — PR
> #34 will likely be merged first; this stacks behind it conceptually, but
> works against current main since backtest code is untouched by #34).

`BACKTEST_MIGRATION_PROPOSAL.md` §4.1 and §4.3 (the detailed playbook the task
points to as the source of truth) says:

> Use the **same `PolygonClient` from `shared/polygon_client.py`** (DO NOT
> create a parallel client). … Any reviewer who sees `import requests` next
> to a `polygon.io` URL string outside of `shared/polygon_client.py` should
> reject the PR.

### Verified state (2026-05-22)

```
$ git ls-tree main -- shared/polygon_client.py shared/data_cache.py shared/exceptions.py
100644 blob a5f6520…  shared/data_cache.py    (yfinance-backed — pre-migration)
100644 blob 166965…   shared/exceptions.py
                       shared/polygon_client.py  ←  ABSENT on main
```

`shared/polygon_client.py` exists only on `feature/migrate-yfinance-to-polygon`
(commit `2d75151 feat(data): migrate DataCache from yfinance to Polygon (preserve interface)`),
which is the live-path PR #34 branch. PR #34 is **not yet merged**.

### Why this blocks Phase 7

Phase 7 builds `backtest/market_history.py` whose public surface (§4.1)
delegates the HTTP call to `PolygonClient.aggregates(...)`. With the file
absent on main, none of the following are runnable on a branch cut from main:

- `from shared.polygon_client import PolygonClient` → ImportError
- `tests/test_market_history.py` mocked HTTP fixtures (the test mocks
  `PolygonClient.aggregates`, not raw `requests.get`)
- Gate 1 (bar equivalence on Polygon era) — the Polygon arm has no client
- Gate 3 (champion equity curve correlation) — same blocker, plus the
  yfinance baseline arm requires Yahoo's curl path to still work at runtime
  (LibreSSL/cookie-jar fragility; no fallback if Yahoo has changed since
  last successful pull)

### Options I considered and rejected

1. **Branch off `feature/migrate-yfinance-to-polygon` instead** — gives me
   `PolygonClient` but directly contradicts the task's explicit branch-base
   instruction. Also entangles the backtest PR's diff with whatever Phases
   2-5 of the live migration finally end up looking like (Q2 still
   unresolved; Phase 2 BLOCKED on the earnings endpoint).
2. **Create a parallel client in `backtest/market_history.py`** — explicitly
   forbidden by §4.3. Would be a reviewer auto-reject.
3. **Vendor a copy of `shared/polygon_client.py` from the other branch into
   this branch** — same architectural smell as #2 (two clients in tree
   until PR #34 merges; merge would then need manual conflict resolution).
4. **Wait for PR #34 to merge, then start** — externally gated; not
   actionable by me right now.

### What I need from Carlos / Charles

Choose one and confirm:

- **(A)** Merge PR #34 first; I rebase off the new main and proceed.
- **(B)** Approve branching off `feature/migrate-yfinance-to-polygon` (the
  PR #34 branch) — `feature/migrate-backtest-to-polygon` becomes a stacked
  PR that auto-resolves to a clean diff once #34 lands on main.
- **(C)** Approve a single-file vendoring of `shared/polygon_client.py`
  into this branch with the understanding that the merge will collapse it
  against #34's version.
- **(D)** Some other path I'm missing.

I lean **(B)** — it's the closest to the proposal's stated intent
("stacks behind it conceptually"), keeps the backtest PR's diff
self-contained to backtest/* and scripts/* files, and avoids dual-client
smell. The cost is the dependency on #34 landing first; the benefit is
that no code on this branch will be wasted regardless of the order things
land.

### Pre-flight concern on Gate 3 (independent of Q5)

Gate 3 requires running the EXP-400 champion config end-to-end TWICE
(once on main with yfinance, once on this branch with Polygon) over
2024-01-01 → 2025-12-31, then asserting daily equity-curve correlation
≥ 0.99. The yfinance arm depends on Yahoo's `query1.finance.yahoo.com/v8/`
endpoint still serving daily bars to the LibreSSL-on-macOS-Python-3.9
cookie-jar workaround in `backtest/backtester.py:38-66`. If Yahoo has
silently changed its cookie/consent flow since the last successful run,
the baseline arm cannot be produced, and Gate 3 has no documented
fallback. Suggest a Phase-6 sub-step: run a single-bar Yahoo fetch
under the existing helper before committing to a multi-hour bake-off,
so we discover this failure mode at minute 5 instead of hour 4.

— D4 executor session, paused awaiting decision on (A)/(B)/(C)/(D).
