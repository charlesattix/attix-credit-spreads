# Backtest yfinance → Polygon Migration Proposal

**Author:** Claude Code (planning session)
**For:** Charles (Master Software Architect) / Carlos Cruz
**Repo:** `attix-credit-spreads`
**Date:** 2026-05-22
**Status:** **EXECUTED 2026-05-23** — Phases 6, 7, 8, 10 landed on branch `feature/migrate-backtest-to-polygon`. Phase 9 (compass/experiments/scripts bulk migration) deferred to a follow-up wave; see *Execution Stamp* below.
**Extends:** [`MIGRATION_YFINANCE_TO_POLYGON.md`](MIGRATION_YFINANCE_TO_POLYGON.md) (live-path Phases 0–5).
**Numbering:** Continues from the live spec — this document defines **Phases 6–10**.

---

## Execution Stamp (2026-05-23)

| Phase | Status | Notes |
|---|---|---|
| 6 — SQLite indices bootstrap | ✅ DONE | `data/historical_indices.sqlite` populated 2019-06-01 → 2023-02-13 (^VIX, ^VIX3M). Commit `c4fe61f`. |
| 7 — `load_market_history` + hybrid loader | ✅ DONE | `backtest/market_history.py` with Polygon+SQLite hybrid + NYSE calendar filter. Commit `41032fb`. |
| 8 — Backtester swap | ✅ DONE | `backtest/backtester.py` 3 call sites swapped to `load_market_history`. Commit `24c93a5`. |
| 9 — Script + experiment migration | ⏸ DEFERRED | Carlos directive 2026-05-23: accept Q1 div-adjust drift, skip bulk migration, ship core swap. ~90 importers tracked via lint allowlist in `tests/test_no_new_yfinance_imports.py`. |
| 10 — Cleanup, lint, docs | ✅ DONE | This stamp + `tests/test_no_new_yfinance_imports.py` (2 tests, both pass). |

**Gates:**
- Gate 1 (Polygon-era bar equivalence): ✅ PASS — 12 documented vendor outliers ([MIGRATION_QUESTIONS.md#Q4](MIGRATION_QUESTIONS.md)).
- Gate 2 (SQLite-era bar equivalence): ✅ PASS — bit-exact.
- Gate 3 (champion equity curve): ⚠ FAIL by threshold, **accepted by Carlos** as a Q1-div-adjust propagation. See [MIGRATION_QUESTIONS.md#Q5](MIGRATION_QUESTIONS.md). Strategy re-baseline is a tracked follow-up, not a blocker.
- Gate 4 (full test suite): ✅ PASS — 3788 passed, 14 pre-existing unrelated failures (no new breakage attributable to this branch).

**Lint:** `tests/test_no_new_yfinance_imports.py` enforces (a) `backtest/` stays yfinance-free, (b) only transitionally-allowlisted files may import yfinance, (c) stale allowlist entries fail the build.

---

## 0. Executive Summary

The live trade-decision path is being migrated to Polygon tonight (live spec Phases 0–3 in flight). That migration leaves a **silent backtest/live data-source mismatch** that violates Carlos's #1 Decision Filter ("Is each experiment replicating exactly the backtesting environment?"). This proposal closes the gap.

**Key finding from this audit:** Polygon **stocks** (SPY/TLT/QQQ/IWM/sector ETFs) have daily aggregates back to ≥2010, well beyond the 2019-06-01 backtest warmup boundary. Polygon **indices** (`I:VIX`, `I:VIX3M`, `I:SPX`) only start at **2023-02-14**. This is verified by curl below. We therefore propose a **hybrid (Option C)** strategy: Polygon-live for everything from 2023-02-14 onward; one-time Yahoo bootstrap of indices into a SQLite table for 2019-06-01 → 2023-02-13. After bootstrap, Yahoo can be deleted from `requirements.txt`.

**Scope:** 5 directories, ~50 Python files, ~150 call sites. Estimated **8–11 engineer-hours** end-to-end, plus the ~2 minutes of one-time index bootstrap.

---

## 1. Inventory of Yahoo Dependencies (Backtest Surface)

`grep -rn "yfinance\|yf\.\|query1\.finance\.yahoo\|_yf_" --include="*.py"` returned **548 matches** across the repo. After excluding (a) the files the live migration owns (`shared/data_cache.py`, `shared/polygon_client.py`, `shared/earnings_calendar.py`, `alerts/*_scanner.py`) and (b) test fixtures, the backtest surface is:

### 1.1 Critical-path: hand-rolled Yahoo curl helpers in `backtest/backtester.py`

| Lines | Function | What it fetches |
|---|---|---|
| 33–35 | `_YF_COOKIE_FILE` | Path to persistent cookie jar in `data/yf_cookies.txt`. |
| 38–66 | `_curl_yf_chart(ticker_encoded, p1, p2)` | Calls `https://query1.finance.yahoo.com/v8/finance/chart/{T}?period1=&period2=&interval=1d`. TLS-1.3 workaround for LibreSSL on macOS Python 3.9. |
| 69–99 | `_yf_chart_to_df(chart_data)` | Parses v8 JSON → DataFrame `[Open, High, Low, Close, Volume]`, tz-naive DatetimeIndex. |
| 102–135 | `_yf_download_safe(ticker, start, end, …)` | Public-ish helper; absorbs old `progress=`/`auto_adjust=` kwargs; one retry on empty (cookie warm-up). |
| 138–151 | `_yf_history_safe(ticker, start, end)` | Wraps `_yf_download_safe` to mimic `yf.Ticker(t).history()`. |
| 1054 | `Backtester._get_historical_data` | Underlying OHLCV per backtest run. |
| 1074, 1106 | `Backtester._build_iv_rank_series` | Downloads `^VIX` and `^VIX3M` daily closes; builds 252-day rolling IV-Rank used for IV-scaled sizing and `combo_regime` `vix_structure` signal. |

These are the **drivers of trade decisions and PnL** in every backtest. They cannot remain.

### 1.2 Critical-path: experiment scripts using `_yf_download_safe`

All import `from backtest.backtester import _yf_download_safe` and gate strategy decisions on the returned bars:

| File | Lines |
|---|---|
| `experiments/EXP-1220-real/backtest.py` | 28, 49 |
| `experiments/EXP-1230-real/backtest.py` | 27, 51 |
| `experiments/EXP-1640-max/backtest.py` | 31, 49 |
| `experiments/EXP-1220-max/robustness_analysis.py` | 32, 54 |
| `scripts/exp1730_hedge_analysis.py` | 56, 80 |
| `scripts/run_exp1220_stress.py` | 29, 60 |
| `scripts/portfolio_combination_backtest.py` | 33, 49 |
| `scripts/exp1660_vrp_portfolio.py` | 47, 80 |
| `scripts/exp1660_vrp_deepening.py` | 43, 68 |
| `scripts/exp1660_vrp_universe.py` | 67, 126 |
| `scripts/exp1660_vol_risk_premium.py` | 28, 46 |
| `scripts/exp1630_pair_optimization.py` | 40, 54 |
| `scripts/exp1630_optimization.py` | 31, 49 |
| `scripts/exp1220_dynamic_leverage_backtest.py` | 27, 50 |
| `scripts/exp1220_leverage_optimization.py` | 31, 46 |
| `scripts/exp800_safe_kelly_scanner.py` | 129, 132, 355, 357 |
| `scripts/exp700_ml_scanner.py` | 123, 126, 298, 300 |
| `scripts/exp307_sector_etf_scanner.py` | 127, 130 |
| `scripts/ultimate_portfolio.py` | 38, 58 |
| `scripts/combined_portfolio_backtest.py` | 32, 49 |
| `scripts/win_rate_boost_analysis.py` | 115–118 |
| `scripts/retrain_exp700_20260401.py` | 170–187 (uses `_yf_download_safe` then falls back to `yfinance` import) |

### 1.3 Critical-path: experiment scripts importing `yfinance` directly

| File | Lines | Symbols fetched |
|---|---|---|
| `experiments/EXP-1270-real/backtest.py` | 53–66 | `SPY`, `^VIX` (2019-12 → 2026-01) |
| `experiments/EXP-1320-real/backtest.py` | 61–73 | `SPY`, `^VIX` (2019-12 → 2026-01) |
| `experiments/EXP-1650-max/backtest.py` | 70–71 | configurable ticker (2019-06 → 2026-01) |
| `scripts/run_exp1880_backtest.py` | 93–99 | `SPY`, `^VIX` (2019-06 → 2026-07) |
| `scripts/exp1220_dynamic_leverage_backtest.py` (note: separate import inside `dynamic_leverage_audit.py`) | — | — |
| `scripts/dynamic_leverage_audit.py` | 140–145 | `SPY`, `^VIX`, `^VIX3M` (2019-06 → 2026-01) |
| `scripts/exp700_yearly_walkforward.py` | 97–105 | `SPY`, `^VIX` (2019-06 → 2026-01) |
| `scripts/exp600_trade_flow_debug.py` | 70–77 | `SPY` (2019-12 → 2026-01) |
| `scripts/safe_kelly_backtest.py` | 688–693 | configurable ticker (2019-06 → 2025-12) via subprocess shell-out |
| `scripts/run_exp1220.py` | 162–171 | direct curl `query1.finance.yahoo.com` (no `yfinance` import but same upstream) |
| `scripts/deep_dive_2x_hedge.py` | 53–… | direct curl `query1.finance.yahoo.com` |

### 1.4 Diagnostic / validation only

| File | Lines | Note |
|---|---|---|
| `scripts/validate_signal_alignment.py` | 140, 143, 158, 177 | Quantifies live-vs-backtest signal drift. Must migrate together with the live path so the "alignment" is between two Polygon-backed sources, not a yfinance-vs-Polygon delta. |
| `scripts/paper_trading_deviation.py` | 250, 256, 263 | Compares live paper trades vs backtester re-runs. Same reasoning. |
| `scripts/live_readiness_check.py` | 610, 615, 622 | Pre-flight check before deploy. |
| `scripts/run_zero_dte_ic.py` | 138 | HTML report text only; no fetch. |
| `tests/test_options_analyzer.py`, `tests/test_data_cache.py`, `tests/test_contracts.py`, `tests/test_snapshot_builder.py`, `tests/conftest.py`, `tests/run_phaseN_tests.py` | various | Fixtures and mocks. Live spec already updates `tests/test_data_cache.py`. The other test files mock `yfinance` and exercise schema invariants — keep the fixture file (`tests/fixtures/yfinance_spy_history.json`) as a *frozen-shape canary* so the new loader's DataFrame contract cannot silently drift. |

### 1.5 Historical / one-shot data pulls (cached to disk)

None on the backtest side. All listed callers re-fetch every run.

---

## 2. Mapping: Yahoo symbol → Polygon equivalent

| Yahoo symbol | Polygon ticker | API key | Endpoint |
|---|---|---|---|
| `SPY`, `TLT`, `QQQ`, `IWM`, `XLK`, `XLF`, `XLE`, `XLV`, `XLY`, `XLP`, `XLI`, `XLU`, `XLB`, `XLRE`, `XLC` | same | `POLYGON_API_KEY` | `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}` |
| `^VIX` | `I:VIX` | `POLYGON_INDICES_API_KEY` | same shape |
| `^VIX3M` | `I:VIX3M` | `POLYGON_INDICES_API_KEY` | same shape |
| `^GSPC` | `I:SPX` | `POLYGON_INDICES_API_KEY` | same shape |
| `^DJI` (occasional) | `I:DJI` | `POLYGON_INDICES_API_KEY` | same shape |
| `^IXIC` (occasional) | `I:NDX` | `POLYGON_INDICES_API_KEY` | same shape |

These match the `_SYMBOL_MAP` constant already defined in `shared/data_cache.py:29-36`, so the new backtest loader reuses that mapping verbatim — no parallel translation table.

---

## 3. History-Depth Gap Analysis (verified via curl)

Backtest warm-up needs daily bars from **2019-06-01** (252-trading-day warmup before the production 2020-01-02 backtest start).

Each curl below was run against the keys in `.env` (not printed/committed). Reproduce with:

```bash
export $(grep -v '^#' .env | xargs)
curl -s "https://api.polygon.io/v2/aggs/ticker/<T>/range/1/day/<from>/<to>?apiKey=<key>" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); r=d.get('results') or []; \
    from datetime import datetime; \
    print('queryCount=', d.get('queryCount'), \
          'first=', datetime.utcfromtimestamp(r[0]['t']/1000).date().isoformat() if r else None, \
          'last=',  datetime.utcfromtimestamp(r[-1]['t']/1000).date().isoformat() if r else None)"
```

### 3.1 Stocks (POLYGON_API_KEY) — **PASS**

| Ticker | Probe window | queryCount | First date returned |
|---|---|---:|---|
| SPY | 2019-06-01 → 2019-12-31 | 148 | 2019-06-03 |
| SPY | 2010-01-01 → 2010-06-30 | 124 | 2010-01-04 |
| TLT | 2019-06-01 → 2019-12-31 | 148 | 2019-06-03 |
| QQQ | 2019-06-01 → 2019-12-31 | 148 | 2019-06-03 |
| IWM | 2019-06-01 → 2019-12-31 | 148 | 2019-06-03 |

All stocks return ≥10 years of history. The 2019-06-01 warmup boundary is well inside Polygon's coverage.

### 3.2 Indices (POLYGON_INDICES_API_KEY) — **FAIL pre-2023-02-14**

| Ticker | Window | queryCount | First date returned |
|---|---|---:|---|
| I:VIX | 2020-01-01 → 2020-12-31 | **0** | — |
| I:VIX | 2021-01-01 → 2021-12-31 | **0** | — |
| I:VIX | 2022-01-01 → 2022-12-31 | **0** | — |
| I:VIX | 2023-01-01 → 2023-12-31 | 225 | **2023-02-14** |
| I:VIX | 2024-01-01 → 2024-12-31 | 259 | 2024-01-02 |
| I:VIX3M | 2022-01-01 → 2022-12-31 | **0** | — |
| I:VIX3M | 2023-01-01 → 2023-12-31 | 221 | **2023-02-14** |
| I:VIX3M | 2024-01-01 → 2024-12-31 | 252 | 2024-01-02 |
| I:SPX | 2022-01-01 → 2022-12-31 | **0** | — |
| I:SPX | 2023-01-01 → 2023-12-31 | 221 | **2023-02-14** |
| I:SPX | 2024-01-01 → 2024-12-31 | 252 | 2024-01-02 |

**Conclusion:** On the current Polygon Indices plan, daily aggregates for `I:VIX`, `I:VIX3M`, `I:SPX` **only exist from 2023-02-14 onward**. SPY/TLT/QQQ/IWM are clean back to ≥2010. The backtest path therefore needs a hybrid solution for indices.

### 3.3 Recommendation: Option C — One-Time Bootstrap to SQLite

Create a new table inside the existing `data/options_cache.db`:

```sql
CREATE TABLE IF NOT EXISTS index_daily_bootstrap (
    ticker        TEXT NOT NULL,     -- canonical: 'I:VIX', 'I:VIX3M', 'I:SPX'
    date          TEXT NOT NULL,     -- ISO YYYY-MM-DD
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL NOT NULL,
    source        TEXT NOT NULL,     -- 'yahoo_bootstrap_2026_05_xx'
    PRIMARY KEY (ticker, date)
);
```

Populate exactly once for each of `I:VIX`, `I:VIX3M`, `I:SPX` over `2010-01-01 → 2023-02-13` using the existing `_curl_yf_chart` helper (it stays alive solely for this one-shot script — see Phase 7 below). After commit, that table is treated as **immutable read-only history**. Any caller asking for an index date < 2023-02-14 reads from this table; ≥ 2023-02-14 reads from Polygon. The seam is hidden inside the new loader.

This is intentionally narrower than yfinance's general role: only three index tickers, only one historical window, source is recorded in the row for auditability.

---

## 4. Architecture: A Single Backtest Market-History Loader

### 4.1 New utility — `backtest/market_history.py`

The live spec creates `shared/polygon_client.py` (already on disk at the time of writing, see `shared/polygon_client.py:27`). **Do not create a second client.** Reuse it.

Public surface:

```python
# backtest/market_history.py
from datetime import datetime
import pandas as pd

def load_market_history(
    ticker: str,                # 'SPY' | 'TLT' | '^VIX' | '^VIX3M' | '^GSPC' | etc.
    start: str | datetime,      # inclusive, ISO 'YYYY-MM-DD' or datetime
    end:   str | datetime,      # inclusive, ISO 'YYYY-MM-DD' or datetime
) -> pd.DataFrame:
    """Return daily OHLCV bars in the canonical yfinance shape:

    Columns: ['Open', 'High', 'Low', 'Close', 'Volume']
    Index:   tz-naive DatetimeIndex (date-only), sorted ascending.

    Symbol normalization: '^VIX'→'I:VIX', '^VIX3M'→'I:VIX3M',
    '^GSPC'→'I:SPX', '^DJI'→'I:DJI', '^IXIC'→'I:NDX'.
    Stocks/ETFs pass through unchanged.

    For index tickers, dates < 2023-02-14 are served from the
    `index_daily_bootstrap` table in data/options_cache.db; dates ≥
    that boundary are served from Polygon. The two sources are
    concatenated and de-duplicated by date (Polygon wins on overlap).

    Raises shared.exceptions.DataFetchError on permanent failure
    (matches the existing yfinance helper's exception contract — see
    shared/exceptions.py:9).
    """
```

Internals:

1. Normalize `ticker` via `shared.data_cache._SYMBOL_MAP`.
2. If the resulting ticker is index-prefixed (`I:*`) and `start < 2023-02-14`, query the `index_daily_bootstrap` SQLite table for the overlap and concatenate. Otherwise skip the DB.
3. Call `PolygonClient.aggregates(ticker, 1, 'day', from_date, to_date)`.
4. Convert the result with the **already-existing** `shared.data_cache._polygon_to_dataframe` helper (`shared/data_cache.py:39-60`). It already produces the exact `[Open, High, Low, Close, Volume]` / tz-naive shape the legacy code expects, so the contract is enforced in one place for both live and backtest.
5. Slice to `[start, end]`, sort, return.

A small process-local LRU keyed on `(ticker, start, end)` keeps the optimizer (which calls into this hundreds of times per grid search) from hammering the API.

### 4.2 Per-caller swap

Each existing callsite becomes a 1-line edit. Examples (these are illustrative diffs only; **no code is modified in this proposal**):

```python
# backtest/backtester.py:1054
- data = _yf_history_safe(ticker, start=start_date, end=end_date)
+ from backtest.market_history import load_market_history
+ data = load_market_history(ticker, start_date, end_date)
```

```python
# backtest/backtester.py:1074
- raw = _yf_download_safe("^VIX",
-     fetch_start.strftime("%Y-%m-%d"),
-     (end_date + timedelta(days=1)).strftime("%Y-%m-%d"))
+ from backtest.market_history import load_market_history
+ raw = load_market_history("^VIX", fetch_start,
+                            end_date + timedelta(days=1))
```

```python
# experiments/EXP-1270-real/backtest.py:53-58
- import yfinance as yf
- df = yf.download("SPY", start="2019-12-01", end="2026-01-01", progress=False)
- if isinstance(df.columns, pd.MultiIndex):
-     df.columns = df.columns.get_level_values(0)
+ from backtest.market_history import load_market_history
+ df = load_market_history("SPY", "2019-12-01", "2026-01-01")
```

```python
# scripts/dynamic_leverage_audit.py:140-145
- import yfinance as yf
- spy   = yf.download("SPY",    start="2019-06-01", end="2026-01-01", progress=False)
- vix   = yf.download("^VIX",   start="2019-06-01", end="2026-01-01", progress=False)
- vix3m = yf.download("^VIX3M", start="2019-06-01", end="2026-01-01", progress=False)
+ from backtest.market_history import load_market_history
+ spy   = load_market_history("SPY",    "2019-06-01", "2026-01-01")
+ vix   = load_market_history("^VIX",   "2019-06-01", "2026-01-01")
+ vix3m = load_market_history("^VIX3M", "2019-06-01", "2026-01-01")
```

Because the return shape is identical to yfinance's (capitalized columns, tz-naive index, ascending sort), all downstream calculations — including the MultiIndex-flattening shim, the `Close.dropna()` calls, the `tz_localize(None)` calls — become **inert no-ops**. They keep working but are no longer needed.

### 4.3 Hard rule: no parallel client

Production code MUST go through `shared.polygon_client.PolygonClient`. The new `backtest/market_history.py` instantiates exactly one process-level client. Any reviewer who sees `import requests` next to a `polygon.io` URL string outside of `shared/polygon_client.py` should reject the PR.

---

## 5. Equivalence / Safety Gates

Three independent acceptance gates must all pass before any phase commit lands on `main`.

### 5.1 Bar-level equivalence

Test `tests/test_backtest_market_history.py` (new). For each ticker in `['SPY', 'TLT', 'QQQ', 'IWM', '^VIX', '^VIX3M']` over the **last 12 calendar months ending at the day before today**:

1. Fetch via `backtest.market_history.load_market_history(t, start, end)`.
2. Fetch via Yahoo (`_yf_download_safe(t, start, end)` — the existing helper, which we have not removed yet).
3. Inner-join on date.
4. Assert:
   - `len(joined)` ≥ 90% of the smaller series.
   - `abs(close_polygon - close_yahoo).max() / close_yahoo.mean() < 0.001` (0.1%).
   - Same condition on `Open`, `High`, `Low`.
   - For stocks only: bar count within ±2.

`^VIX3M` is exempt from the OHLC variant of the test because index OHLC convention differs between CBOE and Polygon's source — assert close only.

### 5.2 Strategy equivalence — Champion EXP-500 run

Test `tests/test_champion_backtest_equivalence.py` (new, marked `@pytest.mark.slow`). Run the `exp_500_realdata_champion.json` config end-to-end with the new loader over the last 12 backtest months:

1. Snapshot the existing run's final equity curve from `output/leaderboard/` (or re-run once with yfinance).
2. Run the new Polygon-backed loader.
3. Assert:
   - Pearson correlation of daily equity ≥ **0.99**.
   - Trade count within ±5%.
   - Total return delta within ±2 percentage points.
   - First and last 5 trade entries have identical `(date, ticker, expiration, short_strike, long_strike, direction)`.

Failure of this gate **halts the migration**. Surface to Carlos.

### 5.3 Warmup integrity — regime classification on day 1

Test `tests/test_warmup_regime_equivalence.py` (new). For the 252-bar warmup window `2019-06-03 → 2020-06-01`:

1. Build IV-rank series, MA200/MA50, `combo_regime` classification both ways (yfinance + new loader).
2. Assert the regime label on **trading day 1** (2020-06-02 in the standard backtest start) is identical, and that the day-1 IV-Rank value differs by < 0.5 percentile.

This is the gate that catches the bootstrap-table seam (Section 3.3) being applied incorrectly.

### 5.4 Schema canary — preserved

`tests/test_contracts.py` continues to load `tests/fixtures/yfinance_spy_history.json` and assert the column set / dtype / index shape on a frozen DataFrame. The fixture stays even after yfinance is removed — its purpose post-migration is to **detect silent contract drift in the new Polygon loader's output shape**.

---

## 6. Phasing, Time Estimates, Commits, Risks

Phase numbers continue from the live spec's Phase 5.

### Phase 6 — Reconnaissance & Bootstrap-Table Scaffold (~45 min)

1. Re-run the curl probes in Section 3.2 against the production keys to confirm Polygon's index-history boundary hasn't shifted since this proposal was written.
2. Add the `index_daily_bootstrap` table DDL to `backtest/historical_data.py` (it owns `data/options_cache.db`). DDL only — no rows.
3. Run `pytest tests/ -q` and record pass/fail counts in `MIGRATION_NOTES.md`.

**Commit:** `chore(backtest-migration): phase 6 — recon, verify index gap, add bootstrap table DDL`

### Phase 7 — One-Time Yahoo → SQLite Bootstrap (~90 min, ~2 min runtime)

1. Add `scripts/bootstrap_index_history.py` (new file). Single-purpose: for each of `('I:VIX','^VIX')`, `('I:VIX3M','^VIX3M')`, `('I:SPX','^GSPC')`, call `_curl_yf_chart` for **2010-01-01 → 2023-02-13** and insert into `index_daily_bootstrap`. Idempotent: `INSERT OR IGNORE`.
2. Run once. Record row counts in `MIGRATION_NOTES.md`. Expect ~3,300 rows per ticker.
3. Add `tests/test_index_bootstrap_table.py`: smoke-checks row counts, asserts no gaps > 4 calendar days inside trading weeks, asserts `close > 0`.

**Commit:** `feat(backtest-migration): phase 7 — bootstrap I:VIX/I:VIX3M/I:SPX history (2010-01 → 2023-02-13) into SQLite`

**Risk:** Yahoo rate-limits or returns a holiday-gap with `None`s. **Mitigation:** the existing `_curl_yf_chart` already retries once on empty (`backtest/backtester.py:125-128`); the bootstrap script wraps each ticker in a `try/except` that requeues failures up to 3 times with exponential backoff. If after 3 retries any ticker has < 3000 rows, abort and surface to Carlos — do not partially populate.

### Phase 8 — New Loader (`backtest/market_history.py`) + Unit Tests (~2 hr)

1. Create `backtest/market_history.py` per the design in §4.1.
2. Create `tests/test_backtest_market_history.py`:
   - Mocked HTTP test of stock fetch (mirrors the live `tests/test_data_cache.py` pattern).
   - Mocked HTTP + in-memory SQLite test of index fetch crossing the 2023-02-14 seam.
   - Schema test (columns, dtype, index tz-naivety).
   - Symbol-map test (`^VIX → I:VIX`).
3. Run `pytest tests/test_backtest_market_history.py -v`.

**Commit:** `feat(backtest-migration): phase 8 — add backtest/market_history.py with hybrid index loader`

**Risk:** Polygon's `t` field is epoch-ms with UTC market-close semantics for stocks but **start-of-trading-day** for some index feeds — possible 1-day off-by-one. **Mitigation:** `_polygon_to_dataframe` uses `dt.normalize()` (already at `shared/data_cache.py:56`); the new equivalence test in §5.1 will catch any off-by-one by date.

### Phase 9 — Backtester Core Swap (~2 hr)

1. Replace the two call sites in `backtest/backtester.py` (lines 1054, 1074, 1106 — three calls).
2. **Do not delete** `_curl_yf_chart`, `_yf_chart_to_df`, `_yf_download_safe`, `_yf_history_safe` yet — they remain for: (a) the bootstrap script, (b) the equivalence-gate tests. Mark them with a module-level comment `# DEPRECATED — retained for tests/bootstrap only; do not use in new code`.
3. Run the §5.1 bar-equivalence test (`pytest tests/test_backtest_market_history.py`).
4. Run the §5.2 EXP-500 equivalence test (`pytest -m slow tests/test_champion_backtest_equivalence.py`).
5. Run the §5.3 warmup test (`pytest tests/test_warmup_regime_equivalence.py`).
6. Run the full suite: `pytest tests/ -q`.

**All three equivalence gates must pass.** If §5.2 (champion equity correlation) drops below 0.99, **stop and surface** — do not commit.

**Commit:** `feat(backtest-migration): phase 9 — backtester uses load_market_history (Polygon + bootstrap)`

**Risks:**
- **Dividend adjustment drift.** Polygon's `adjusted=true` (the default we use, `shared/polygon_client.py:73`) applies dividends differently from Yahoo's `auto_adjust=True`. **Mitigation:** SPY's quarterly dividend is ~$1.50 on a ~$500 base = 0.3% — above the 0.1% gate tolerance in §5.1. We **must** confirm equality with `adjusted=true`. If the gate fails specifically because of dividend math, the loader switches to `adjusted=false` and we compare against Yahoo's `auto_adjust=False` instead. Either-or, but the choice must be the same on live and backtest paths.
- **VIX source divergence.** Yahoo's `^VIX` is delayed-by-15min CBOE; Polygon's `I:VIX` is the same CBOE feed but timestamped at the bar close. Daily closes should match within 2bp. The Section 3.2 probes confirm the data is present 2023-02-14 onward; bar-equivalence test will quantify the drift.
- **Rate limits during optimizer grid runs.** A 144-combo grid_search × 6 years × 4 tickers = ~3,500 fetches. **Mitigation:** the loader's process-local LRU collapses these to 4 unique calls per process. The `PolygonClient` already has 3-retry backoff (`shared/polygon_client.py:82-89`).

### Phase 10 — Experiment & Script Swap (~3 hr)

For each file in §1.2 and §1.3, perform the 1-line swap shown in §4.2.

Run order (to minimize risk):
1. Replace in `experiments/EXP-1220-real/`, `EXP-1230-real/`, `EXP-1640-max/` (currently-active leaderboard champions).
2. Run each experiment's backtest, compare final return to its leaderboard entry. Allow ±2pp drift.
3. Replace in the `scripts/exp16xx*`, `exp700*`, `exp800*` family (used by ongoing experiments).
4. Replace in `experiments/EXP-1270-real/`, `EXP-1320-real/`, `EXP-1650-max/` and remaining ad-hoc scripts.
5. Grep verify no production .py file outside `tests/` and `scripts/bootstrap_index_history.py` imports yfinance:
   ```bash
   grep -rnE "^import yfinance|^from yfinance|_yf_(download|history|chart)" --include="*.py" . \
     | grep -vE "tests/|scripts/bootstrap_index_history|__pycache__"
   ```
   Expected output: **empty**.
6. Run full test suite once.

**Commit:** `refactor(backtest-migration): phase 10 — all experiments + scripts call load_market_history`

**Risk:** Some experiment scripts are not under CI (no test coverage). **Mitigation:** the §5.2 champion equivalence is the canonical gate; for non-champion experiments, we accept "runs without crashing and produces a leaderboard row within ±2pp of historical" as the bar.

### Phase 11 — Delete Yahoo Code Paths & Dependency (~45 min) — **deferred until after Phase 4 paper-trade validation in the live spec**

1. Delete `_curl_yf_chart`, `_yf_chart_to_df`, `_yf_download_safe`, `_yf_history_safe` from `backtest/backtester.py`.
2. Delete `data/yf_cookies.txt` and the `_YF_COOKIE_FILE` constant.
3. Delete `scripts/bootstrap_index_history.py` (one-shot, finished).
4. Remove `yfinance` from `requirements.txt`.
5. Keep `tests/fixtures/yfinance_spy_history.json` and `tests/test_contracts.py` — rename the fixture file to `frozen_ohlcv_history.json` and update test names to `test_frozen_ohlcv_*`; **rename only**, no semantic change.
6. Grep verify the dependency is gone:
   ```bash
   grep -rnE "yfinance" --include="*.py" --include="*.txt" . | grep -v __pycache__
   ```
   Expected: only the rename markers, nothing executable.

**Commit:** `chore(backtest-migration): phase 11 — drop yfinance from backtest, alerts, scripts, requirements`

**Risk:** A late-discovered cron job or notebook still imports yfinance. **Mitigation:** the grep above is the gate; CI fails if non-empty.

### Rollback plan

Each phase is its own commit. To roll back:

- **Phase 6 → revert DDL commit.** No data lost (table is empty).
- **Phase 7 → keep the table** (immutable history; safe to leave). Revert the script commit if needed.
- **Phase 8 → revert the loader commit.** Callers haven't been swapped yet, so no production behavior depends on it.
- **Phase 9 → revert + restore the yfinance call sites in `backtest/backtester.py`.** All four `_yf_*` helpers are still present (we deferred their deletion to Phase 11 precisely for this reason).
- **Phase 10 → revert per-file commits.** If grouped into one commit, `git revert <sha>` restores all callers atomically.
- **Phase 11 → must restore `yfinance` to `requirements.txt` + re-`pip install`** before reverting code. Highest-friction rollback. **This is why Phase 11 is gated on a successful live Phase 4 paper-trade validation.**

---

## 7. Time Budget Summary

| Phase | Wall time | Critical-path test |
|---|---|---|
| 6 | 45 min | baseline suite green |
| 7 | 90 min | bootstrap-row count + gap check |
| 8 | 2 hr | new loader unit tests |
| 9 | 2 hr | §5.1, §5.2, §5.3 all green |
| 10 | 3 hr | leaderboard parity ±2pp |
| 11 | 45 min | grep returns empty |
| **Total** | **~10 hr** | + 24h paper-trade soak before Phase 11 |

---

## 8. Why This Matters — $100M North Star

A trading system whose backtests do not match live data **cannot be trusted at scale**. Every leaderboard champion (EXP-500, EXP-213, EXP-191) was selected on yfinance-fed backtests. Tonight's live migration moves the trading hand to Polygon. The instant that ships, every existing leaderboard entry is a comparison against a different dataset than the one actually trading.

Carlos's #1 Decision Filter is unambiguous: *"Is each experiment replicating exactly the backtesting environment?"* Today, post-live-migration, the honest answer is **no**: backtests fetch Yahoo, live trades fetch Polygon. The two feeds differ at the basis-point level on stocks and at the structural level on indices (different timestamping conventions, different dividend adjustment). At 1× leverage that mismatch buries in noise. At the leverage ranges in the champion configs (compound + 23% risk + IC + max_c=100), basis-point drifts on the VIX regime signal can flip a trade direction and turn a +800%-year into a circuit-breaker year.

This proposal closes that gap with a single shared client, a single shared loader, one immutable bootstrap table for the index pre-history Polygon does not cover, and three independent equivalence gates that fail loud and refuse to advance until the two data paths agree to 0.1%. After Phase 11, "backtest equals live" is enforced by import-graph topology, not by reviewer attention.

That is the precondition for trusting any leaderboard number ever again.

🏗️
