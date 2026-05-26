# Task: Author a Backtest yfinance → Polygon Migration Proposal

**From:** Charles (Master Software Architect) on behalf of Carlos Cruz
**Mode:** PROPOSAL ONLY — do not modify production code. Read-only analysis + a written proposal file.
**Repo:** `pilotai-credit-spreads`
**Sibling work in flight:** A separate CC session (in tmux `claude-deploy`) is currently executing the LIVE-path migration (Phases 0–3 of `MIGRATION_YFINANCE_TO_POLYGON.md`). DO NOT touch the same files.

---

## Context

The live trade-decision path is being migrated from yfinance → Polygon tonight. A follow-up audit revealed the **backtest system is also Yahoo-dependent** — and worse, it uses two parallel pathways:

1. `backtest/backtester.py` — hand-rolled curl + cookie jar hitting `query1.finance.yahoo.com/v8/finance/chart` directly (functions `_yf_download_safe`, `_yf_chart_to_df`, `_yf_history_safe`).
2. Multiple experiment scripts use `yfinance` library directly (`yf.download()`, `yf.Ticker()`).

This causes a **backtest ↔ live data-source mismatch** that silently breaks Carlos's #1 Decision Filter: *"Is each experiment replicating exactly the backtesting environment?"*

## Your Job

Produce a written migration proposal at `BACKTEST_MIGRATION_PROPOSAL.md` in the repo root. **Do not modify any production .py files.** Read-only analysis + the proposal document only.

The proposal must cover:

### 1. Complete inventory of Yahoo dependencies in backtest/experiments/scripts
Use `grep` to find every reference. Categorize by:
- Critical path (drives backtest decisions/PnL)
- Diagnostic/validation only
- Historical (one-shot data pulls cached to disk)

Include line numbers and a short note on what each call fetches.

### 2. Mapping table: each Yahoo call → Polygon equivalent
- Yahoo `SPY` daily → Polygon `/v2/aggs/ticker/SPY/range/1/day/{from}/{to}`
- Yahoo `^VIX` → Polygon `/v2/aggs/ticker/I:VIX/range/1/day/...`
- Yahoo `^VIX3M` → Polygon `I:VIX3M`
- Yahoo `^GSPC` → Polygon `I:SPX`
- Any other symbols you find — propose the Polygon equivalent.

### 3. History depth gap analysis
Several backtests pull data back to 2019-06-01. Run a curl test (read-only, just GET) to confirm Polygon has daily aggregates back that far for SPY, ^VIX, ^VIX3M:
```bash
curl -s "https://api.polygon.io/v2/aggs/ticker/I:VIX/range/1/day/2019-06-01/2019-12-31?apiKey=$POLYGON_INDICES_API_KEY" | jq '.resultsCount'
```
(Read `POLYGON_API_KEY` and `POLYGON_INDICES_API_KEY` from `.env` — do not print or commit them.)

Report what you find. If Polygon's history is shallower than 2019-06-01 for any required ticker, propose a hybrid (Option C: one-time Yahoo bootstrap cached to local SQLite).

### 4. Architecture proposal: shared loader pattern
The live migration tonight creates `shared/polygon_client.py` (the PolygonClient class with retry/backoff). Your proposal should:
- Reuse that same client — do NOT propose a parallel one.
- Propose a single new utility (e.g. `backtest/market_history.py` with `load_market_history(ticker, start, end) -> pd.DataFrame`) that all backtest code calls.
- Show how each yfinance caller swaps to it.
- Define the DataFrame schema (columns: `Open, High, Low, Close, Volume` capitalized; tz-naive DatetimeIndex) to match the existing yfinance return shape so callers don't change.

### 5. Equivalence/safety gates
Propose specific acceptance tests:
- **Per-ticker bar equivalence**: Yahoo vs Polygon daily Close max relative error < 0.1% over last 12 months for SPY, TLT, ^VIX, ^VIX3M.
- **Strategy equivalence**: Champion config (EXP-400) backtested over last 12 months produces equity-curve correlation ≥ 0.99 between Yahoo-data run and Polygon-data run, and trade count within ±5%.
- **Warmup integrity**: 252-day warmup window starting 2019-06-01 produces identical regime classifications on day 1 of trading.

### 6. Phasing & risk plan
Propose phases (similar style to `MIGRATION_YFINANCE_TO_POLYGON.md` in repo root — read it as the reference template). Include:
- Phase numbers (continue from Phase 5 in the live spec — yours start at Phase 6).
- Time estimates per phase.
- Commit message for each phase.
- Specific risks and mitigations (rate limits, dividend adjustments, VIX source differences, etc.).
- Rollback plan if equivalence test fails.

### 7. Why this matters
Short closing section connecting to Carlos's $100M North Star: a trading system whose backtests don't match live data can't be trusted at scale.

---

## Constraints

- **READ-ONLY.** No modifications to `backtest/`, `experiments/`, `scripts/`, or `shared/`. The only file you create is `BACKTEST_MIGRATION_PROPOSAL.md`.
- **Do not** start any background processes that hit external APIs aggressively — a few curl calls to verify Polygon history depth is fine.
- **Do not** commit or push. Just write the proposal file.
- **Do not** duplicate the existing `MIGRATION_YFINANCE_TO_POLYGON.md` — your proposal extends it; reference it explicitly.
- **Be specific.** File names, line numbers, function names, exact curl commands. No vague hand-waving.
- **Use the existing reference template** `MIGRATION_YFINANCE_TO_POLYGON.md` for tone and structure.

## Deliverable

When done, output a 1-line summary to stdout and stop:
> "Proposal complete: BACKTEST_MIGRATION_PROPOSAL.md (N words, M sections)"

Do NOT begin any implementation. This is a planning artifact only.

🏗️ — Charles
