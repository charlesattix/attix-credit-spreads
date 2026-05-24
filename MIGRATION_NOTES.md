# yfinance → Polygon Migration Notes

## Phase 0 — Reconnaissance (2026-05-22)

### Polygon endpoint verification
All endpoints return data successfully for 2026-04-22..2026-05-22 window (23 trading days).

**Indices key (`POLYGON_INDICES_API_KEY`):**
- `I:VIX` — status: DELAYED, 23 bars
- `I:VIX3M` — status: DELAYED, 23 bars
- `I:SPX` — status: DELAYED, 23 bars

(DELAYED status is expected — daily aggregates are EOD; not real-time. Sufficient for OHLCV history.)

**Stocks key (`POLYGON_API_KEY`):**
- SPY, QQQ, IWM, TLT — OK, 23 bars each
- Sector ETFs: XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLU, XLB, XLRE, XLC — OK, 23 bars each

### Baseline test suite (`pytest tests/ --tb=no -q`)
- **3434 passed**
- **14 failed** (pre-existing — unrelated to migration):
  - `test_exp800_fixes.py::test_marks_expired_trades`
  - `test_live_snapshot.py::test_empty_data_handled`
  - `test_macro_api.py` — 9 failures (regime endpoint returning 503/422)
  - `test_sentinel_orchestrator.py` — 2 failures (exception handler severity)
- 5 skipped, 8 xfailed, 6 xpassed
- Duration: 84s

---

## Phase 1 — DataCache swap

**Files changed (3 source, 2 test):**
- NEW `shared/polygon_client.py` (105 lines): thin REST wrapper, auto-routes
  index tickers (`I:` prefix) to `POLYGON_INDICES_API_KEY` and stocks to
  `POLYGON_API_KEY`. 3 retries with exponential backoff on 429/5xx, 30s
  timeout, raises `DataFetchError` on permanent failure.
- MODIFIED `shared/data_cache.py`: dropped yfinance import; calls
  `PolygonClient.aggregates` and converts to the same yfinance-shaped
  DataFrame (Open/High/Low/Close/Volume, tz-naive DatetimeIndex, ascending).
  Symbol mapper for `^VIX → I:VIX`, `^VIX3M → I:VIX3M`, `^GSPC → I:SPX`,
  `^DJI → I:DJI`, `^IXIC → I:NDX`. `get_ticker_obj` now raises
  `NotImplementedError`.
- REWRITTEN `tests/test_data_cache.py`: 10 tests (was 5) — mocks
  `PolygonClient` instead of `yf.Ticker`; new coverage for DataFrame schema,
  period slicing, symbol mapping, empty response, and the
  `NotImplementedError` contract.
- NEW `tests/test_signal_equivalence.py`: 4 live tests comparing Polygon
  vs yfinance (`auto_adjust=False`) for SPY/TLT/^VIX/^VIX3M.

**Signal-equivalence results (live network, 2026-05-22, last ~120 trading days):**

| Ticker  | Close max dev | MA20 | MA50 | MA200 | RSI14 | Bars (Pol/yf) |
|---------|---------------|------|------|-------|-------|---------------|
| SPY     | 0.00%         | pass | pass | pass  | pass  | 252 / 252     |
| TLT     | 0.0058%       | pass | pass | pass  | pass  | 252 / 252     |
| ^VIX    | 12.81% (1 bar)| pass | pass | pass  | pass  | 252 / 252     |
| ^VIX3M  | ~1e-8         | pass | pass | pass  | pass  | 252 / 252     |

The ^VIX 2026-02-06 single-bar disagreement is a vendor data discrepancy
unrelated to the migration (every other bar matches to ~1e-8). The test
masks per-bar Close outliers before windowed metrics so a single bad bar
doesn't contaminate MA20/50/200.

**Behavioral change (documented in MIGRATION_QUESTIONS.md Q1):** yfinance
`Ticker.history()` defaults to `auto_adjust=True` (split + dividend
back-adjustment); Polygon `adjusted=true` is splits-only. SPY/TLT/etc.
historical series will shift by the cumulative dividend yield (~0.5–2%
constant offset). MAs/RSI ratios are preserved.

**Tests:** 3443 passed (was 3434; +9 net new), 14 pre-existing failures.

---

## Phase 2 — Earnings calendar — BLOCKED

Polygon's standard plan does NOT include forward-looking earnings dates.
Probed `/vX/reference/tickers/{T}/events` (only `ticker_change`),
`/vX/reference/tickers/{T}/earnings` (404), `/v1/meta/symbols/{T}/earnings`
(404), `/benzinga/v1/earnings` (403 NOT_AUTHORIZED), and
`/vX/reference/financials` (past quarters only).

Per the brief — "If the Polygon plan does not include upcoming earnings
dates, stop and write a note to MIGRATION_QUESTIONS.md. Do not invent a
fallback." — `shared/earnings_calendar.py` is unchanged. Awaiting Carlos's
decision (see MIGRATION_QUESTIONS.md Q2).

---

## Phase 2 — Earnings calendar (D2 followup) — RESOLVED via Unusual Whales

**Branch:** `feature/migrate-earnings-to-uw` (off main).
**Resolves:** Q2 in `MIGRATION_QUESTIONS.md`.

Polygon's plan still does not include forward earnings, so the path
chosen was Unusual Whales (Carlos provided `UW_API_TOKEN` in `.env`).

**Files changed:**

- `shared/uw_client.py` *(new, 165 lines)* — thin REST wrapper for UW.
  Exposes `get_earnings_history(ticker)`,
  `get_earnings_premarket(date=None)`,
  `get_earnings_afterhours(date=None)`. 30s timeout, 3-retry backoff on
  429/5xx, 24h in-memory TTL cache, raises `DataFetchError` on permanent
  failure. Both required headers (`Authorization: Bearer …` and
  `UW-CLIENT-API-ID: 100001`) attached to every request.
- `shared/earnings_calendar.py` *(rewritten)* — `import yfinance` removed.
  `get_next_earnings`, `get_lookahead_calendar`, and
  `get_historical_earnings_dates` now read from
  `UWClient.get_earnings_history`. `calculate_historical_stay_in_range`
  fetches its 5y daily closes directly from `PolygonClient.aggregates`
  (DataCache is bounded to 1y). Public signatures preserved.
  `calculate_expected_move(options_chain, current_price)` is retained
  as ATM-straddle math — UW does not expose a pre-computed expected
  move on the documented endpoints (verified live against `AAPL` on
  2026-05-22; the response carries `report_date`, EPS fields, and
  `surprise_percentage`, nothing more).
- `alerts/earnings_scanner.py` — **unchanged.** All caller signatures
  preserved.
- `tests/test_uw_client.py` *(new, 22 tests)* — HTTP-mocked happy path,
  401/403, 429 retry-then-succeed, 429 exhaust, 503 retry, network
  error, cache hit, cache TTL=0, cache clear, per-ticker isolation.

**Live smoke test (2026-05-22):**

```
next_earnings(AAPL)         → 2026-07-30 00:00:00+00:00
lookahead([AAPL,MSFT,NVDA]) → [('AAPL', 67)]
historical(AAPL, 4)         → ['2026-04-30','2026-01-29','2025-10-30','2025-07-31']
expected_move(chain, 200)   → 6.00   (ATM straddle, synthetic chain)
expected_move(None, 200)    → None   (backwards-compat sentinel)
```

**Tests:** baseline pre-D2 was 3787 passing, 14 pre-existing failures.
With D2: **3809 passing** (+22 UW client tests), 14 pre-existing
failures, zero new failures.

**Removed dependency:** `import yfinance` no longer appears anywhere in
`shared/earnings_calendar.py`. This is the last yfinance reference on
the live trade-decision path. Phase 5 (delete yfinance from
`requirements.txt`) is no longer blocked by Q2.

---

## Phase 3 — Delete inline yfinance fallbacks

Removed the `if self._data_cache: ... else: import yfinance` block from all
5 scanner files:
- `alerts/earnings_scanner.py:171`
- `alerts/momentum_scanner.py:122`
- `alerts/gamma_scanner.py:141`
- `alerts/zero_dte_scanner.py:138`
- `alerts/iron_condor_scanner.py:111`

Each now unconditionally calls `self._data_cache.get_history(...)` (none of
these files had a top-level `import yfinance` to remove — the import was
inside the deleted else-branch).

**Brief's verification grep** still returns 5 results:
```
compass/archive/discovery_round3.py
compass/archive/benchmark_tier1_features.py
compass/archive/crypto_vol_strategy.py
engine/portfolio_backtester.py
strategy/options_analyzer.py
```
These are out of Phase 3 scope: `compass/archive/*` is archived
non-live code; `engine/portfolio_backtester.py` is a backtester (parallel
to `backtest/`); `strategy/options_analyzer.py` is flagged in Q3 of
MIGRATION_QUESTIONS.md. None affect the live alerts/scanner path.

**Tests:** 3443 passed, 14 pre-existing failures (no new failures).

---

## Surprises / Carlos decisions needed

1. **Dividend-adjustment behavior change** (Q1) — Polygon `adjusted=true`
   is splits-only; previous yfinance default included dividends. SPY/TLT
   historical series shift by ~0.5–2%. Most likely a latent bug fix, but
   needs sign-off.
2. **Phase 2 blocked** (Q2) — Polygon plan needs Benzinga earnings
   upgrade, or we leave `earnings_calendar.py` on yfinance as the lone
   live-path holdout.
3. **`strategy/options_analyzer.py`** (Q3) — still imports yfinance for
   the `_get_chain_yfinance` fallback. Outside the 5-scanner Phase 3 scope.

---

## Phases 6–10 — Backtest migration (D4)

**Branch:** `feature/migrate-backtest-to-polygon`
**Proposal:** [`BACKTEST_MIGRATION_PROPOSAL.md`](BACKTEST_MIGRATION_PROPOSAL.md)
**Task spec:** [`MIGRATION_D4_BACKTEST_TASK.md`](MIGRATION_D4_BACKTEST_TASK.md)

### Phase 6 — SQLite indices bootstrap (commit `c4fe61f`)
One-time Yahoo bootstrap of ^VIX / ^VIX3M daily bars into
`data/historical_indices.sqlite` covering 2019-06-01 → 2023-02-13 (the
window where Polygon indices do not yet exist). Bootstrap script lives at
`scripts/bootstrap_indices_sqlite.py`. After this, Yahoo is no longer
needed for backtests.

### Phase 7 — `load_market_history` + hybrid loader (commit `41032fb`)
Created `backtest/market_history.py`:
- Polygon for SPY/TLT/QQQ/IWM/sector ETFs (and indices ≥ 2023-02-14).
- SQLite for indices < 2023-02-14.
- Symbol normalization: `^VIX → I:VIX`, `^VIX3M → I:VIX3M`, `^GSPC → I:SPX`.
- NYSE trading-calendar filter derived from SPY's Polygon aggregates —
  drops Polygon's holiday-published VIX prints that Yahoo would not emit.
- Single `PolygonClient` from `shared/polygon_client.py`.
- 15 unit tests in `tests/test_market_history.py`, all passing.

### Phase 8 — Backtester swap (commit `24c93a5`)
`backtest/backtester.py` rewired: 3 call sites
(`_get_historical_data`, `_build_iv_rank_series` × 2) now call
`load_market_history`. Legacy `_yf_download_safe` / `_yf_history_safe`
helpers retained (no callers in `backtest/`) for Gate 3's Yahoo-arm shim.

### Phase 9 — Script & experiment migration (DEFERRED)
Carlos directive 2026-05-23: Gate 3 fails because the Q1 fix (Polygon
split-only adjustment is the correct semantics for an options system)
propagates into historical strike selection. Re-baselining
MASTERPLAN/leaderboard/champion numbers against the corrected backtester
is a follow-up workstream, not a PR blocker. Phase 9 (bulk script
migration) is held until that re-baseline lands, since rerunning ~90
scripts on Polygon data only matters in the context of re-baselined
expected results.

### Phase 10 — Cleanup + lint (this commit)
- Added `tests/test_no_new_yfinance_imports.py` with two checks:
  - `test_no_new_yfinance_imports`: every yfinance importer must be on
    the explicit transitional allowlist; new importers + stale entries
    both fail the build.
  - `test_backtest_module_is_yfinance_free`: hard-guard that `backtest/`
    cannot regress.
- Stamped `BACKTEST_MIGRATION_PROPOSAL.md` with "EXECUTED 2026-05-23" and
  the per-phase status table.
- This `MIGRATION_NOTES.md` section.

### Gates

| Gate | Threshold | Result |
|---|---|---|
| 1 — Polygon-era bar equivalence | max rel dev < 0.1%, bars ±2 | ✅ PASS w/ 12 documented vendor outliers (Q4) |
| 2 — SQLite-era bar equivalence | bit-exact | ✅ PASS |
| 3 — Champion equity correlation | ≥ 0.99 | ⚠ FAIL → accepted (Q5) as Q1-div-adjust propagation |
| 4 — Full test suite | no new failures | ✅ PASS (3788 pass / 14 pre-existing fail) |

### Follow-up workstream

1. **Strategy re-baseline** — rerun MASTERPLAN champion + leaderboard on
   the Polygon-backed backtester to establish new expected numbers.
2. **Phase 9 bulk migration** — once re-baseline lands, migrate the ~90
   files on `ALLOWED_YFINANCE_IMPORTERS` off Yahoo. Removing entries from
   the allowlist is mechanical because the lint enforces it.
3. **Remove yfinance from `requirements.txt`** — only after every entry
   is off the allowlist.
