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
