# P0-2 FIX REPORT — Polygon index key routing in scheduler

**Branch:** `fix/p0-2-polygon-indices-routing`
**Status:** PR open, **NOT MERGED** — awaiting Charles review

## Summary

`scheduler/data_providers.py` was passing `POLYGON_API_KEY` (stocks plan) to every Polygon
request, including `I:VIX`, `I:VIX3M`, `I:VVIX`. Polygon's stocks plan returns
**403 NOT_AUTHORIZED** for index tickers, so every fetch failed silently and the scheduler
fell through to yfinance — stale/wrong data into the circuit-breaker and regime gates.

## Fix

Single source of truth: added `_pick_key(ticker)` in `shared/polygon_client.py` that returns
`POLYGON_INDICES_API_KEY` for any `I:`-prefixed ticker, else `POLYGON_API_KEY`.
`scheduler/data_providers.py` now imports `_pick_key` and uses it at every Polygon call site:
- `_polygon_get_historical(ticker, days)` — was taking a hard-coded `api_key`; now routes per-ticker.
- `_polygon_fetch_all` — drops the `api_key` parameter (per-ticker routing in the leaf).
- `fetch_market_data` — reads both env vars, warns on either missing.
- `get_vix_values` — was the most direct manifestation; now uses indices key for `I:VIX`/`I:VIX3M`.
- `get_spot_price` — also routed via `_pick_key` for consistency.

## Live evidence

### Before (stocks key against indices — the bug)
| Ticker | Key used | Status |
|---|---|---|
| `I:VIX` | `POLYGON_API_KEY` | **403 NOT_AUTHORIZED** |
| `I:VIX3M` | `POLYGON_API_KEY` | **403 NOT_AUTHORIZED** |
| `I:VVIX` | `POLYGON_API_KEY` | **403 NOT_AUTHORIZED** |
| `I:SKEW` | `POLYGON_API_KEY` | **403 NOT_AUTHORIZED** |

### After (routed via `_pick_key`)
| Ticker | Key routed | Status |
|---|---|---|
| `SPY` | `POLYGON_API_KEY` | ✅ 200 |
| `QQQ` | `POLYGON_API_KEY` | ✅ 200 |
| `I:VIX` | `POLYGON_INDICES_API_KEY` | ✅ 200 |
| `I:VIX3M` | `POLYGON_INDICES_API_KEY` | ✅ 200 |
| `I:VVIX` | `POLYGON_INDICES_API_KEY` | ✅ 200 |
| `I:SKEW` | `POLYGON_INDICES_API_KEY` | ✅ 200 |

Reproducible via `python3 scripts/_p0_2_live_probe.py`.

## Unit tests

`tests/test_polygon_key_routing.py` — **6 passed**:
- `_pick_key` returns indices key for `I:VIX`/`I:VIX3M`/`I:VVIX`/`I:SKEW`/`i:vix` (case-insensitive).
- `_pick_key` returns stocks key for `SPY`/`QQQ`/`TLT`/`AAPL`/`XLF`.
- Empty string when the relevant env var is unset (no silent reuse of the wrong key).
- `_polygon_get_historical("I:VIX")` sends `apiKey=INDICES_KEY`; `("SPY")` sends `apiKey=STOCKS_KEY` (mocked `requests.get`, asserts captured params).
- `get_vix_values()` sends the indices key for both VIX requests.

## Routes touched

- `shared/polygon_client.py:28-36` — added `_pick_key`
- `scheduler/data_providers.py:27` — import `_pick_key`
- `scheduler/data_providers.py:89-110` — `_polygon_get_historical` routes per ticker
- `scheduler/data_providers.py:124-146` — `_polygon_fetch_all` drops `api_key`
- `scheduler/data_providers.py:296-320` — `fetch_market_data` reads both env vars
- `scheduler/data_providers.py:378` — `get_spot_price` routes via `_pick_key`
- `scheduler/data_providers.py:409-430` — `get_vix_values` routes per ticker (the core bug)

## Action required from Carlos

`POLYGON_INDICES_API_KEY` must be present in Railway env vars for **all three services**:
- `vesper`
- `sentinel-watchdog`
- `dashboard`

If any service is missing this var, indices will still fail there (now with an empty-key
warning instead of a silent 403). The local `.env` has it set — verified during the live probe.

## Compliance with task rules

- ✅ Reused routing pattern from `shared/polygon_client.py` (added `_pick_key` as the shared helper).
- ✅ No new `yfinance` imports.
- ✅ PR opened, not merged.
