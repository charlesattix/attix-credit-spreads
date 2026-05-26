# Task: Execute Backtest yfinance → Polygon Migration (D4)

**From:** Charles (Master Software Architect) on behalf of Carlos Cruz
**Branch:** Create `feature/migrate-backtest-to-polygon` off `main` (NOT off PR #34 — PR #34 will likely be merged first; this stacks behind it conceptually, but works against current main since backtest code is untouched by #34).
**Reference:** `BACKTEST_MIGRATION_PROPOSAL.md` (written by another CC session — your detailed playbook, Phases 6–10).
**Status:** D4 approved by Carlos 2026-05-22 23:06 ET. Execute now.

---

## Scope (FULL — all 5 directories per the proposal)

Migrate Yahoo data fetching to Polygon across:
1. `backtest/backtester.py` — the curl + cookie jar Yahoo helpers (`_yf_download_safe`, `_yf_chart_to_df`, `_yf_history_safe`)
2. `experiments/EXP-*/backtest.py` — the experiment-specific backtests using `_yf_download_safe` or `yf.download()` directly
3. `experiments/EXP-*/robustness_analysis.py` (where applicable)
4. `scripts/*.py` — every backtest/scanner script using Yahoo
5. Any tests that mock yfinance — convert to Polygon HTTP mocks

**Do NOT touch:**
- Anything in PR #34 / PR #35 (live path — `shared/data_cache.py`, `shared/polygon_client.py`, `shared/earnings_calendar.py`, `alerts/*_scanner.py`, `strategy/options_analyzer.py`)
- The UW migration if it's still in progress on another branch

If you find conflicts because a file is being modified on another branch, **STOP and write to MIGRATION_QUESTIONS.md** as Q5. Do not guess.

---

## Critical Architectural Note: The Indices History Gap

**Polygon indices (`I:VIX`, `I:VIX3M`, `I:SPX`) only have daily history from 2023-02-14 onward.** Backtest scripts pull from 2019-06-01.

**Therefore implement Option C (hybrid) from the proposal:**

1. Build a **one-time bootstrap script** `scripts/bootstrap_indices_history.py` that:
   - Reads 2019-06-01 → 2023-02-13 daily bars for `^VIX`, `^VIX3M`, `^GSPC` from Yahoo (use the existing curl helper from `backtest/backtester.py` one last time).
   - Stores them in `data/historical_indices.sqlite` with schema `(ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)`.
   - Idempotent — re-running is a no-op if the rows already exist.

2. Create `backtest/market_history.py` — a shared loader replacing the Yahoo helpers:
   - Function: `load_market_history(ticker: str, start: date, end: date) -> pd.DataFrame`
   - Symbol normalization: same `_SYMBOL_MAP` as `shared/data_cache.py` (`^VIX → I:VIX`, etc.)
   - For indices: query SQLite for rows < 2023-02-14, query Polygon for rows ≥ 2023-02-14, concatenate.
   - For stocks: query Polygon directly back to 2010 (Polygon stocks have deep history).
   - Use the **same `PolygonClient` from `shared/polygon_client.py`** (DO NOT create a parallel client).
   - Return DataFrame with columns `[Open, High, Low, Close, Volume]` capitalized, tz-naive `DatetimeIndex`, ascending order — same shape yfinance returns so callers don't change.

3. Delete the Yahoo helpers in `backtest/backtester.py`. Keep `_yf_download_safe` as a thin deprecation shim that calls `load_market_history` and logs a `DeprecationWarning` — so consumer scripts that still import it keep working until they're updated in this same PR.

4. After bootstrap is run and tested, **delete the bootstrap script** (it's one-time use). Commit the SQLite file `data/historical_indices.sqlite` to git so other developers don't need to re-bootstrap. (Confirm via `du -h` that the file is under ~5 MB — daily bars × 4 years × 3 tickers is tiny.)

---

## Acceptance Tests (the safety gates)

### Gate 1 — Bar equivalence on overlapping date range (Polygon era)

For `^VIX`, `^VIX3M`, `SPY`, `TLT` over 2023-03-01 to 2026-05-22:
- Yahoo (via legacy `_yf_download_safe`) vs Polygon (via new `load_market_history`)
- Max relative deviation on Close < **0.1%** per bar (allowing same single-bar `^VIX` 2026-02-06 vendor outlier the live migration already documented)
- Bar count match within ±2

### Gate 2 — Bar equivalence on pre-2023 range (SQLite bootstrap era)

For `^VIX`, `^VIX3M` over 2019-06-01 to 2023-02-13:
- Yahoo (live fetch from query1.finance.yahoo.com) vs SQLite bootstrap
- Must be **identical** (this is the same data, just cached locally)

### Gate 3 — Strategy equivalence: Champion equity curve

Run the champion strategy (EXP-400 config) backtest from 2024-01-01 to 2025-12-31 with:
- A) Pre-migration backtester (yfinance) — checkout main, run, save trades & equity curve to `data/baseline_yahoo.parquet`
- B) Post-migration backtester (Polygon) — run on this branch, save to `data/baseline_polygon.parquet`

Assert:
- Trade count match within **±5%**
- Equity-curve daily correlation **≥ 0.99**
- Final PnL within **±2%** of the baseline

If Gate 3 fails, this is a strategy-drift red flag. **STOP** and document the divergence in MIGRATION_QUESTIONS.md as Q6. Do not push.

### Gate 4 — Full test suite

`pytest tests/ --tb=no -q`. No new failures vs baseline.

---

## Phases (continuing from the proposal's numbering)

**Phase 6 — Recon + bootstrap (1 hr)**
- Verify Polygon stocks history depth for SPY, TLT, sector ETFs (should be back to 2010).
- Confirm indices gap: curl `I:VIX` 2019/2022/2023 dates and document the cutoff.
- Build + run `scripts/bootstrap_indices_history.py`. Verify SQLite size < 5 MB and row count.
- Commit: `chore(backtest): bootstrap pre-2023 indices history from Yahoo to SQLite (one-time)`

**Phase 7 — Shared loader (2 hrs)**
- Build `backtest/market_history.py`.
- Write `tests/test_market_history.py` (HTTP-mocked + SQLite fixture).
- Commit: `feat(backtest): add load_market_history backed by Polygon + SQLite indices bootstrap`

**Phase 8 — Backtester swap (3 hrs)**
- Rewrite `backtest/backtester.py` to use `load_market_history`.
- Deprecate `_yf_download_safe` as a shim.
- Run Gate 1 and Gate 2.
- Commit: `feat(backtest): migrate backtester.py from Yahoo curl to Polygon (via market_history)`

**Phase 9 — Script + experiment migration (3 hrs)**
- Update every file listed in the proposal's §1.2 and §1.3.
- Replace `_yf_download_safe(t, start, end)` → `load_market_history(t, start, end)`.
- Replace `yf.download(...)` and `yf.Ticker(...).history(...)` calls similarly.
- Remove top-level `import yfinance as yf` from every migrated file.
- Run Gate 3 (the equity-curve correlation test).
- Commit: `refactor(backtest): migrate experiments and scripts from Yahoo to Polygon`

**Phase 10 — Cleanup (1 hr)**
- Run Gate 4.
- Add a CI lint that fails on `import yfinance` outside `tests/`, `scripts/archive/`, and the (now obsolete) bootstrap script.
- Update `BACKTEST_MIGRATION_PROPOSAL.md` with a final "EXECUTED" stamp and pointer to the merged PR.
- Update `MIGRATION_NOTES.md` with Phase 6–10 summaries.
- Commit: `chore(backtest): add yfinance-import lint, finalize backtest migration`

---

## Open PR

After all gates pass:
```bash
git push -u origin feature/migrate-backtest-to-polygon
gh pr create --base main --head feature/migrate-backtest-to-polygon \
  --title "feat(backtest): migrate backtest system from Yahoo to Polygon (D4, Phases 6-10)" \
  --body "<<<see below>>>"
```

PR body must include:
- Summary of the 5 phases
- Gate 1, 2, 3, 4 results (numeric)
- Note: PR #34 fixed the live path; this PR closes the backtest↔live data-source gap
- Champion equity-curve correlation: target ≥ 0.99
- Reference: `BACKTEST_MIGRATION_PROPOSAL.md`

---

## Output

One line:
> "D4 complete: PR #<num> opened, backtest system migrated to Polygon, all 4 gates passed"

If any gate fails, output instead:
> "D4 paused at Phase N — Gate K failed. See MIGRATION_QUESTIONS.md Q<num>. No push."

---

## Constraints

- **DO NOT** modify live-path files owned by PR #34 / PR #35.
- **DO NOT** weaken any gate threshold to make a test pass — if it doesn't pass, surface it.
- **DO NOT** push if Gate 3 (strategy equivalence) fails.
- **DO** commit the SQLite bootstrap file to git (it's small and stable).
- **DO** keep commits atomic per phase so this PR is reviewable.

🏗️ — Charles
