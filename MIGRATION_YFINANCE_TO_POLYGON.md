# yfinance → Polygon Migration — Execution Brief for Claude Code

**Author:** Charles (Master Software Architect)
**For:** Carlos Cruz
**Repo:** `attix-credit-spreads`
**Goal:** Replace yfinance with Polygon.io as the single source of truth for OHLCV history on the live trade-decision path. Keep `DataCache` public interface identical.

---

## Tonight's Scope: Phases 0–3 (Phase 4 is Monday paper-trade validation)

Work the phases **in order**. **Commit after each phase** with a clear message. **Do not start a phase until the previous one passes tests.** If anything is ambiguous, stop and write a question to `MIGRATION_QUESTIONS.md` — do not guess.

---

## Phase 0 — Reconnaissance (~30 min)

1. Confirm Polygon plan covers daily aggregates for SPY, TLT, QQQ, IWM, sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLU, XLB, XLRE, XLC).
2. Verify the Polygon Indices key works for `I:VIX`, `I:VIX3M`, `I:SPX`. Use curl:
   ```bash
   curl -s "https://api.polygon.io/v2/aggs/ticker/I:VIX/range/1/day/2026-04-22/2026-05-22?apiKey=$POLYGON_INDICES_API_KEY" | jq '.resultsCount, .results[:2]'
   ```
3. Run the baseline test suite: `pytest tests/ -x --tb=short` and record pass/fail counts in `MIGRATION_NOTES.md`.

**Commit:** `chore(migration): phase 0 recon — verified polygon endpoints & baseline tests`

---

## Phase 1 — DataCache Swap (~3 hrs) — THE CORE CHANGE

### 1.1 Create `shared/polygon_client.py`

Thin wrapper around `requests` for Polygon aggregates. Requirements:
- Constructor reads `POLYGON_API_KEY` and `POLYGON_INDICES_API_KEY` from env.
- Method: `aggregates(ticker: str, multiplier: int, timespan: str, from_date: str, to_date: str) -> list[dict]`
- Auto-routes index tickers (`I:` prefix) to `POLYGON_INDICES_API_KEY`, stocks to `POLYGON_API_KEY`.
- Retry/backoff: 3 retries on 429/5xx with exponential backoff (1s, 2s, 4s).
- 30s timeout.
- Raise `DataFetchError` (from `shared.exceptions`) on permanent failure — same exception type the existing code expects.

### 1.2 Rewrite `shared/data_cache.py`

**MUST preserve the public surface exactly.** Methods that callers depend on:
- `__init__(self, ttl_seconds: int = 900)`
- `get_history(self, ticker: str, period: str = '1y') -> pd.DataFrame`
- `pre_warm(self, tickers: List[str]) -> None`
- `clear(self) -> None`
- `get_ticker_obj(self, ticker: str)` — change to raise `NotImplementedError` with a message telling callers to use `PolygonOptionsClient` for option chains. (Verify with grep that no live-path caller still uses it; if any do, migrate them in this phase too.)

**DataFrame schema returned by `get_history` MUST be identical:**
- Columns: `['Open', 'High', 'Low', 'Close', 'Volume']` (capitalized — matches yfinance)
- Index: `DatetimeIndex` (timezone-naive, date-only)
- Sorted ascending by date

**Symbol normalization mapper** (inside the file):
```python
_SYMBOL_MAP = {
    '^VIX':   'I:VIX',
    '^VIX3M': 'I:VIX3M',
    '^GSPC':  'I:SPX',
    '^DJI':   'I:DJI',
    '^IXIC':  'I:NDX',
}
```

**Caching logic stays identical** — same TTL, same lock, same period slicing via `_PERIOD_DAYS`.

**Always fetch 1y of daily aggregates** (matching current behavior), slice locally.

### 1.3 Update `tests/test_data_cache.py`

Replace yfinance mocks with HTTP mocks of Polygon aggregates response. The expected output DataFrame should be unchanged.

### 1.4 Add `tests/test_signal_equivalence.py` — THE SAFETY GATE

For tickers `['SPY', 'TLT', '^VIX', '^VIX3M']` over the last 90 trading days:
1. Fetch via the new Polygon-backed `DataCache`.
2. Fetch via yfinance directly (`yf.Ticker(t).history(period='1y')`).
3. Align on dates (inner join).
4. For each ticker, compute and assert max relative deviation < **0.1%** on:
   - Close price
   - MA20, MA50, MA200
   - 14-day RSI
5. Assert the count of bars matches within ±2 (allow for minor calendar edge differences).

If the test fails, the migration is **not safe** — stop and surface to Carlos.

### 1.5 Run full suite

```bash
pytest tests/ -x --tb=short
```

All tests that passed in Phase 0 must still pass. Plus the new equivalence test.

**Commit:** `feat(data): migrate DataCache from yfinance to Polygon (preserve interface)`

---

## Phase 2 — Earnings Calendar (~2 hrs)

Rewrite `shared/earnings_calendar.py`:
- Replace `yf.Ticker(t).calendar` → Polygon `/vX/reference/tickers/{ticker}/events` (or the financials/dividends endpoints, whichever exposes upcoming earnings dates on the current plan).
- Replace `yf.Ticker(t).earnings_dates` → same source.
- Keep the ATM-straddle expected-move logic intact (it uses option chains from `PolygonOptionsClient`, already on Polygon).
- Return shapes must match what `main.py` and the scanners currently consume.
- Add unit tests with mocked HTTP responses.

**If the Polygon plan does not include upcoming earnings dates,** stop and write a note to `MIGRATION_QUESTIONS.md`. Do not invent a fallback.

**Commit:** `feat(earnings): migrate earnings calendar from yfinance to Polygon`

---

## Phase 3 — Delete Inline yfinance Fallbacks (~1 hr)

Inside each of these files, remove the `import yfinance as yf` + `yf.Ticker(...).history(...)` fallback blocks. Keep the `data_cache.get_history()` call as the only source:

- `alerts/earnings_scanner.py` (line ~175)
- `alerts/momentum_scanner.py` (line ~126)
- `alerts/gamma_scanner.py` (line ~145)
- `alerts/zero_dte_scanner.py` (line ~142)
- `alerts/iron_condor_scanner.py` (line ~115)

Also remove the now-unused `import yfinance` at the top of these files.

Run `pytest tests/` again — must still pass.

Grep verify no production file (anything outside `backtest/`, `experiments/`, `scripts/`, `tests/`) imports yfinance:
```bash
grep -rnE "^import yfinance|^from yfinance" --include="*.py" . \
  | grep -vE "backtest/|experiments/|scripts/|tests/" \
  | grep -v __pycache__
```
Expected output: **empty**.

**Commit:** `refactor(alerts): remove yfinance fallbacks from scanners`

---

## DO NOT DO TONIGHT

- ❌ Do not touch `backtest/`, `experiments/EXP-*/backtest.py`, or `scripts/validate_signal_alignment.py`, `scripts/paper_trading_deviation.py` — these are out of scope.
- ❌ Do not delete `yfinance` from `requirements.txt` — that's Phase 5.
- ❌ Do not deploy to any live experiment account — Phase 4 is the paper-trade validation Monday on a **new** Alpaca account (per Carlos's SACRED rule in `~/.openclaw/workspace/TOOLS.md`).
- ❌ Do not push to remote unless all phases pass tests locally. Commit locally; we'll push together after review.

---

## When You're Done

Append a summary to `MIGRATION_NOTES.md` with:
- Files changed (count + names)
- Tests passing (count + new tests added)
- Signal-equivalence test results (max deviation per ticker)
- Anything that surprised you or needs Carlos's decision

Then say "Migration phases 0–3 complete" and wait. **Do not push, do not start Phase 4.**

🏗️ — Charles
