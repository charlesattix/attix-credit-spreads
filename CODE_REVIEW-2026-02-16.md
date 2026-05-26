# Attix Credit Spreads -- Full Code Review

**Date:** 2026-02-16
**Reviewers:** 7 specialized Claude Opus 4.6 agents
**Scope:** Full codebase at commit eca4cec (main branch)

---

## Score Summary

**Overall: 5.5 / 10**

| Panel | Score | Key Concern |
|-------|-------|-------------|
| Architecture | **5.5** / 10 | Dual paper trading systems, file-based IPC, divergent P&L logic |
| Code Quality | **5.5** / 10 | Duplicate Alert types (CRITICAL), 6 DRY violations (HIGH), broken LivePositions |
| Security | **5.5** / 10 | Git-tracked pickle RCE (CRITICAL), browser-exposed auth token, missing auth headers |
| Performance | **5.5** / 10 | Subprocess cold-start per scan, Polygon fetches ALL options, iterrows() anti-pattern |
| Error Handling | **7.5** / 10 | No JSON corruption recovery, SWR errors not rendered, non-atomic config write |
| Testing | **6.0** / 10 | Non-functional contract tests, zero ML pipeline coverage, reimplementation antipatterns |
| Production Readiness | **3.0** / 10 | Ephemeral filesystem loses all data, ignoreBuildErrors:true, no database |

---

# Architecture Review: Attix Credit Spreads

## 1. OVERALL SYSTEM DESIGN

### Architecture Summary
The system is a two-tier application: a Python backend (trading engine, ML pipeline, backtesting) and a Next.js 15 frontend (dashboard, paper trading, alerts UI). They are deployed together in a single Docker container on Railway, communicating via the filesystem (JSON files) rather than a network API.

### Communication Between Python and Node.js

**Finding 1.1: File-based IPC between Python and Node.js** [HIGH]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (lines 103-120), `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/positions/route.ts` (lines 27-31)
- The Python backend writes JSON files to `data/paper_trades.json` and `data/trades.json`. The Next.js API routes read these files. There is no real-time communication channel (no WebSocket, no HTTP API between them). The Node.js scan route (`/api/scan`) shells out to `python3 main.py scan` via `child_process.execFile`. This is fragile: concurrent reads/writes can cause corruption, there is no schema contract enforcement between the two sides, and there is no notification mechanism when data changes.

**Finding 1.2: Two separate paper trading systems** [HIGH]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py`, `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts`
- The Python `PaperTrader` writes to `data/paper_trades.json` and the Node.js paper-trades API writes to `data/user_trades/{userId}.json`. These are completely independent systems with different data formats, different trade IDs, and different P&L calculation logic. The `/api/positions` route reads the Python file, while `/api/paper-trades` reads the Node.js file. This creates confusion about which is the "source of truth."

**Finding 1.3: Monolithic Docker container with two runtimes** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/Dockerfile`
- The single container runs either Node.js OR Python (via the entrypoint script), not both simultaneously. The `web` command only starts Node.js. Python scans are triggered on-demand via `execFile` from Node.js. This means the system cannot run scheduled scans without external cron. The separation is clean in some ways but means the ML pipeline and scanner only run when explicitly triggered.

## 2. MODULE COUPLING & COHESION

**Finding 2.1: Duplicate constants across root and shared/** [LOW]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/constants.py`, `/home/pmcerlean/projects/attix-credit-spreads/shared/constants.py`
- Two separate constants files exist. `constants.py` at root has `MAX_CONTRACTS_PER_TRADE`, `MANAGEMENT_DTE_THRESHOLD`, `DEFAULT_RISK_FREE_RATE`, `BACKTEST_SHORT_STRIKE_OTM_FRACTION`, `BACKTEST_CREDIT_FRACTION`. `shared/constants.py` has `FOMC_DATES`, `CPI_RELEASE_DAYS`. These should be consolidated.

**Finding 2.2: Duplicate type definitions on the frontend** [MEDIUM]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/web/lib/types.ts`, `/home/pmcerlean/projects/attix-credit-spreads/web/lib/api.ts`
- The `Alert` interface is defined in both `lib/types.ts` (lines 51-82) and `lib/api.ts` (lines 1-20) with different shapes. `lib/types.ts` has a richer `Alert` type with `legs`, `reasoning`, `aiConfidence` while `lib/api.ts` has a simpler Python-aligned shape. The homepage imports `Alert` from `lib/api.ts` (line 11) but also uses properties from the `lib/types.ts` shape. This creates type safety gaps.

**Finding 2.3: Good dependency injection in CreditSpreadSystem** [POSITIVE]
- File: `/home/pmcerlean/projects/attix-credit-spreads/main.py` (lines 52-63)
- The main system class accepts optional pre-built dependencies, enabling easy testing and configuration. This is a well-implemented constructor injection pattern.

**Finding 2.4: IV rank calculation duplicated in three places** [MEDIUM]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/shared/indicators.py` (lines 28-67), `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` (lines 257-285), `/home/pmcerlean/projects/attix-credit-spreads/strategy/options_analyzer.py` (lines 218-264)
- The Polygon provider has its own IV rank implementation that does not delegate to `shared.indicators.calculate_iv_rank`. The `OptionsAnalyzer.calculate_iv_rank` does delegate correctly, but having the inline version in Polygon means behavior can diverge.

## 3. API DESIGN

**Finding 3.1: No REST API between Python and Node.js** [HIGH]
- The Python backend has no HTTP server. All communication is file-based or via subprocess invocation. For a production trading system, this is a significant limitation. If the Python process needs to be on a separate machine for scaling, the entire architecture breaks.

**Finding 3.2: Inconsistent API response shapes** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/alerts/route.ts` (lines 23-31)
- The alerts endpoint returns both `alerts` and `opportunities` with the same data for backward compatibility. The client (`page.tsx` line 26) reads both: `alertsData?.alerts || alertsData?.opportunities || []`. This is a code smell indicating the contract changed but was never fully migrated.

**Finding 3.3: Good input validation with Zod on Node.js API** [POSITIVE]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` (lines 12-32), `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts` (lines 26-88)
- Both paper-trades and config POST endpoints use Zod schemas for validation. The config endpoint also strips secrets from GET responses. This is well done.

**Finding 3.4: Authentication is token-based but simplistic** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/web/middleware.ts`
- Uses `NEXT_PUBLIC_API_AUTH_TOKEN` which is exposed to the browser (by design for single-user deployment as documented in `.env.example`). The timing-safe comparison is good. The userId derivation from token via `simpleHash` is a weak hash (32-bit JS number). Not suitable for multi-user production.

## 4. DATA FLOW & STATE MANAGEMENT

**Finding 4.1: File-based persistence is a scalability bottleneck** [HIGH]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (lines 89-101), `/home/pmcerlean/projects/attix-credit-spreads/tracker/trade_tracker.py` (lines 60-72)
- Both use atomic JSON write (temp file + rename), which is good for crash safety. However, JSON files on disk are single-process, non-queryable, and will degrade with large trade histories. There is no database.

**Finding 4.2: Good SWR usage for frontend state** [POSITIVE]
- File: `/home/pmcerlean/projects/attix-credit-spreads/web/lib/hooks.ts`
- SWR hooks with appropriate `refreshInterval` (5 min), `dedupingInterval` (1 min), and `revalidateOnFocus`. The polling intervals are reasonable for a trading dashboard.

**Finding 4.3: Two P&L calculation engines with different logic** [HIGH]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (lines 303-363), `/home/pmcerlean/projects/attix-credit-spreads/web/lib/pnl.ts` (lines 1-48)
- Python `_evaluate_position` uses intrinsic value and time decay with a 1.2x acceleration factor. TypeScript `calcUnrealizedPnL` uses a `0.7` exponent power decay plus 70/30 theta/price weighting. These will produce **different P&L numbers for the same position**. The web and Python systems will disagree on whether a position should be closed.

**Finding 4.4: Race condition in paper-trades file locking** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` (lines 46-54)
- The `withLock` implementation uses promise chaining per-userId, which is correct for single-process Node.js. However, if Railway runs multiple instances or the server restarts during a write, the lock is lost. The atomic write pattern mitigates file corruption but not lost updates.

## 5. CONFIGURATION MANAGEMENT

**Finding 5.1: Secrets in config.yaml with env var substitution** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/config.yaml` (lines 82-103)
- API keys use `${ENV_VAR}` syntax which is resolved by `utils.py:_resolve_env_vars`. If the env var is not set, the raw `${POLYGON_API_KEY}` string becomes the API key value, which will fail silently on API calls. There is no validation that required environment variables are actually set.

**Finding 5.2: Config POST endpoint allows writing to config.yaml** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts` (lines 102-119)
- The shallow merge (`{ ...existing, ...parsed.data }`) will overwrite nested objects entirely rather than deep-merging. If a client sends `{ strategy: { min_dte: 35 } }`, it will erase `max_dte`, `technical`, etc.

**Finding 5.3: Good config validation** [POSITIVE]
- File: `/home/pmcerlean/projects/attix-credit-spreads/utils.py` (lines 114-150)
- The `validate_config` function checks required sections and validates key constraints (min_dte < max_dte, positive account_size, valid risk_per_trade range).

## 6. DEPENDENCY ARCHITECTURE

**Finding 6.1: Test dependencies in production requirements.txt** [LOW]
- File: `/home/pmcerlean/projects/attix-credit-spreads/requirements.txt` (lines 49-52)
- `pytest`, `pytest-cov`, and `hypothesis` are listed in the main requirements.txt without separation. They get installed in the Docker image unnecessarily.

**Finding 6.2: Missing tsconfig.json from tracked files** [LOW]
- The web project lacks a visible `tsconfig.json` (it may be generated or in the Next.js defaults). This is not necessarily a bug but could lead to inconsistent TypeScript behavior.

**Finding 6.3: Version ranges are reasonably pinned** [POSITIVE]
- Both `requirements.txt` and `package.json` use minimum version constraints (`>=` for Python, `^` for Node). This is reasonable for development velocity.

## 7. SCALABILITY CONCERNS

**Finding 7.1: Single-process architecture** [HIGH]
- The entire system runs in a single Docker container. The Python scanner runs as a subprocess invoked by Node.js. There is no worker queue, no message broker, no ability to run multiple scanner instances. If the scan takes 2 minutes (timeout set at 120s in `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` line 36), the Node.js event loop is unblocked but the server is effectively running one scan at a time (enforced by `scanInProgress` flag).

**Finding 7.2: In-memory rate limiting does not survive restarts** [MEDIUM]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` (lines 10-12), `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts` (lines 17-19)
- Both use in-memory arrays/maps for rate limiting. On server restart or Railway redeploy, all limits reset.

**Finding 7.3: DataCache is in-memory only** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/shared/data_cache.py`
- The `DataCache` uses a thread-safe in-memory dict with a 15-minute TTL. This is fine for single-process but cannot be shared across workers. Good that it uses copy-on-read (`data.copy()`) to prevent mutation.

**Finding 7.4: yfinance as fallback data source** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/strategy/options_analyzer.py` (lines 68-76)
- yfinance has rate limiting and is 15-min delayed. It's used as a fallback when Tradier/Polygon fail. This is good for resilience but could lead to stale data affecting trading decisions without the user knowing.

## 8. FRONTEND ARCHITECTURE

**Finding 8.1: Good error boundary implementation** [POSITIVE]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/web/app/error.tsx`, `/home/pmcerlean/projects/attix-credit-spreads/web/app/global-error.tsx`
- Both page-level and global error boundaries are implemented with structured logging and user-friendly reset buttons.

**Finding 8.2: Component decomposition is clean** [POSITIVE]
- The `components/` directory is well-organized by feature: `layout/`, `alerts/`, `positions/`, `sidebar/`, `ui/`. UI primitives (card, button, badge, input, table, tabs) are properly extracted.

**Finding 8.3: Homepage does excessive inline computation** [LOW]
- File: `/home/pmcerlean/projects/attix-credit-spreads/web/app/page.tsx` (lines 48-56)
- Win rate, avg winner, avg loser, and profit factor are computed inline in the component body. These should be memoized with `useMemo` or extracted to the `calculatePortfolioStats` function that already exists.

**Finding 8.4: Alert key uses array index** [LOW]
- File: `/home/pmcerlean/projects/attix-credit-spreads/web/app/page.tsx` (line 135)
- `key={idx}` is used for AlertCard rendering. If alerts reorder (they are sorted by score), this causes unnecessary re-renders. Should use a stable key like `${alert.ticker}-${alert.short_strike}-${alert.expiration}`.

## 9. INTEGRATION POINTS

**Finding 9.1: Circuit breaker pattern well implemented** [POSITIVE]
- Files: `/home/pmcerlean/projects/attix-credit-spreads/shared/circuit_breaker.py`, `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` (line 33), `/home/pmcerlean/projects/attix-credit-spreads/strategy/tradier_provider.py` (line 39)
- Both Polygon and Tradier providers wrap API calls in circuit breakers with 5-failure threshold and 60s reset timeout. This is excellent resilience engineering.

**Finding 9.2: Provider fallback chain** [POSITIVE]
- File: `/home/pmcerlean/projects/attix-credit-spreads/strategy/options_analyzer.py` (lines 68-76)
- Clear fallback: Tradier -> Polygon -> yfinance. Each level logs warnings. The `_get_chain_from_provider` method (lines 78-100) provides a clean abstraction over fallback logic.

**Finding 9.3: Retry with backoff on Alpaca** [POSITIVE]
- File: `/home/pmcerlean/projects/attix-credit-spreads/strategy/alpaca_provider.py` (lines 35-55)
- Decorator-based retry with exponential backoff and jitter. Well-implemented pattern.

**Finding 9.4: Pagination handling in Polygon provider** [POSITIVE]
- File: `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` (lines 80-92, 110-117)
- Properly follows `next_url` pagination for Polygon API responses.

**Finding 9.5: Duplicated snapshot fetching in Polygon** [MEDIUM]
- File: `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py`
- `get_options_chain` (line 94) and `get_full_chain` (line 164) both fetch the full options snapshot from `/v3/snapshot/options/{ticker}` and filter locally. If called in sequence for the same ticker, this doubles the API cost.

## 10. DESIGN PATTERNS

**Patterns Present:**
- **Factory Method**: `create_system()` in `main.py` (line 330)
- **Strategy Pattern**: Provider abstraction (Tradier, Polygon, yfinance)
- **Circuit Breaker**: External API resilience
- **Observer**: SWR's automatic revalidation on the frontend
- **Dependency Injection**: `CreditSpreadSystem.__init__` accepts all components
- **Atomic Write**: Both Python and Node.js use temp file + rename
- **TypedDict**: Python type hints for major data shapes
- **Fallback with Counter**: ML pipeline tracks fallback frequency for alerting

**Anti-patterns Detected:**

**Finding 10.1: God Config** [LOW]
- File: `/home/pmcerlean/projects/attix-credit-spreads/config.yaml`
- A single configuration file controls strategy, risk, alerts, data providers, logging, and backtesting. While manageable at current size, it conflates deployment config (log paths, API keys) with business logic (strategy params, risk limits).

**Finding 10.2: ML model trained on synthetic data only** [HIGH]
- File: `/home/pmcerlean/projects/attix-credit-spreads/ml/signal_model.py` (lines 468-621)
- The `generate_synthetic_training_data` method creates artificial training samples with hand-crafted feature distributions and label logic. The ML model has never been validated on real trade outcomes. In the `initialize` flow (ml_pipeline.py lines 105-109), if no saved model exists, it trains on 2000 synthetic samples. This creates a false sense of ML sophistication -- the model effectively encodes the same rules a heuristic would, but with added complexity and opacity.

**Finding 10.3: Missing patterns** [MEDIUM]
- No repository pattern for data access (raw JSON file reads everywhere)
- No event bus for cross-module communication
- No Command/CQRS pattern for trade execution
- No health monitoring beyond the basic `/api/health` endpoint
- No structured telemetry or metrics (Sentry is optional)

---

## SUMMARY TABLE

| Severity | Count | Description |
|----------|-------|-------------|
| CRITICAL | 0 | - |
| HIGH | 5 | Dual paper trading systems (1.2), file-based IPC (1.1), divergent P&L calculations (4.3), single-process architecture (7.1), synthetic-only ML training (10.2) |
| MEDIUM | 11 | Duplicate types (2.2), duplicate IV rank (2.4), inconsistent API responses (3.2), simplistic auth (3.4), file-based persistence (4.1*), race conditions (4.4), unvalidated env vars (5.1), shallow config merge (5.2), in-memory rate limits (7.2), yfinance delay (7.4), Polygon duplicate fetch (9.5), missing patterns (10.3) |
| LOW | 5 | Duplicate constants (2.1), test deps in prod (6.1), missing tsconfig (6.2), inline computation (8.3), index keys (8.4), god config (10.1) |
| POSITIVE | 9 | DI in main system (2.3), Zod validation (3.3), SWR hooks (4.2), config validation (5.3), version pinning (6.3), error boundaries (8.1), component structure (8.2), circuit breakers (9.1), retry/backoff (9.3) |

*Note: Finding 4.1 is at the HIGH/MEDIUM boundary; listed as HIGH in the text but MEDIUM in practical urgency since the system is early-stage.

## PRIORITIZED FIX LIST

1. **Unify the paper trading systems** (HIGH) -- Choose either Python or Node.js as the single source of truth for paper trades. Eliminate the dual-write / dual-read confusion.

2. **Unify P&L calculation** (HIGH) -- Implement one canonical P&L formula and use it in both Python and TypeScript. The current divergence means positions show different values depending on which UI/system queries them.

3. **Replace file-based IPC with a proper communication layer** (HIGH) -- Either make the Python backend a FastAPI/Flask HTTP service that Node.js calls, or use a shared SQLite/PostgreSQL database. The current approach of shelling out to `python3` and sharing JSON files will not survive scaling.

4. **Add a database** (HIGH) -- Replace JSON file persistence with SQLite (minimum) or PostgreSQL. This enables concurrent access, querying, and eliminates race conditions.

5. **Validate required environment variables at startup** (MEDIUM) -- In `utils.py:_resolve_env_vars`, if a critical env var like `POLYGON_API_KEY` resolves to the literal `${POLYGON_API_KEY}`, log a clear error and fail fast.

6. **Fix the shallow config merge** (MEDIUM) -- Use deep merge in the config POST handler to prevent accidental deletion of nested config keys.

7. **Consolidate duplicate type definitions** (MEDIUM) -- Remove the `Alert` interface from `lib/api.ts` and use only the canonical one from `lib/types.ts`.

8. **Train ML model on real data** (HIGH) -- The synthetic data training is a placeholder. Either collect real trade outcomes and retrain, or drop the ML scoring and rely on the well-understood rules-based system until real data exists.

9. **Add scheduled scanning** (MEDIUM) -- Implement cron-like scheduled scans (e.g., using Railway cron jobs or a Python scheduler) instead of relying solely on user-triggered scans.

10. **Separate test from production dependencies** (LOW) -- Create a `requirements-dev.txt` for pytest/hypothesis.

---

## OVERALL SCORE: 5.5 / 10

**Justification**: The codebase demonstrates solid domain knowledge (options math, risk management, market regime detection), good resilience patterns (circuit breakers, retries, fallbacks, atomic writes), and reasonable frontend architecture (SWR, error boundaries, Zod validation), but is fundamentally undermined by the dual paper trading systems with divergent P&L logic, file-based IPC between two runtimes, the absence of a database, and an ML pipeline trained entirely on synthetic data -- these collectively make it unsuitable for production trading without significant rearchitecture.

---

# Code Quality Review: Attix Credit Spreads

## Findings

### CQ-01 | CRITICAL | Duplicate `Alert` Type Definitions
**Files:** `/home/pmcerlean/projects/attix-credit-spreads/web/lib/types.ts` (line 51) and `/home/pmcerlean/projects/attix-credit-spreads/web/lib/api.ts` (line 1)

Two completely different `Alert` interfaces are defined. `lib/types.ts` defines a rich `Alert` with fields like `id`, `company`, `legs[]`, `reasoning[]`, `aiConfidence`, etc. `lib/api.ts` defines a simpler `Alert` with `ticker`, `credit`, `score`, etc. Components import from whichever file they choose, creating a **type schism** across the frontend. `page.tsx` imports `Alert` from `lib/api` while `mockData.ts` imports from `lib/types`. These two types are structurally incompatible -- the `lib/types.ts` `Alert` has an `id: number` while `lib/api.ts` `Alert` has no `id` at all. This means `alert-card.tsx` works with `lib/api.Alert` but `mockData.ts` conforms to `lib/types.Alert`, leading to silent property-access failures at runtime.

**Severity:** CRITICAL -- this causes type confusion across the entire frontend and can lead to runtime `undefined` access errors.

---

### CQ-02 | HIGH | DRY Violation: IV Rank/Percentile Calculated in Three Places
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/shared/indicators.py` (line 28) -- canonical `calculate_iv_rank`
- `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` (line 257) -- `calculate_iv_rank` reimplemented inline
- `/home/pmcerlean/projects/attix-credit-spreads/strategy/options_analyzer.py` (line 218) -- delegates to shared, but also duplicates the HV calculation pattern

`PolygonProvider.calculate_iv_rank()` (lines 257-285) replicates the exact same IV rank/percentile formula from `shared/indicators.py` instead of calling the shared implementation. If the formula is corrected in one place, the other will be out of sync.

**Severity:** HIGH -- logic drift risk in financial calculations.

---

### CQ-03 | HIGH | DRY Violation: `_atomic_json_write` Duplicated Verbatim
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (line 88-101)
- `/home/pmcerlean/projects/attix-credit-spreads/tracker/trade_tracker.py` (line 59-72)

The exact same `_atomic_json_write` static method (tempfile + rename pattern) is copy-pasted into both classes. This should be a shared utility function.

**Severity:** HIGH -- maintenance burden; if one is fixed/improved the other will be missed.

---

### CQ-04 | HIGH | DRY Violation: `tryRead` Helper Duplicated Across API Routes
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/alerts/route.ts` (line 6-10)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/positions/route.ts` (line 9-13)

Identical `tryRead` helper function is duplicated. Should be extracted to a shared utility.

**Severity:** HIGH -- DRY violation.

---

### CQ-05 | HIGH | DRY Violation: Rate Limiter Pattern Duplicated
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` (lines 10-14, 18-24)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/run/route.ts` (lines 11-15, 19-25)

Near-identical rate-limiting code using a module-level timestamps array with the same window-shift logic. Should be a reusable middleware or utility.

**Severity:** HIGH

---

### CQ-06 | HIGH | `formatCurrency` Duplicated With Different Semantics
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/lib/utils.ts` (line 8) -- Intl.NumberFormat, always shows 2 decimal places, no +/- prefix
- `/home/pmcerlean/projects/attix-credit-spreads/web/components/positions/live-positions.tsx` (line 31) -- manual implementation with +/- prefix, no decimals

Two different `formatCurrency` functions with completely different formatting rules. `live-positions.tsx` defines a local version that shadows the shared one.

**Severity:** HIGH -- inconsistent currency display across the UI.

---

### CQ-07 | HIGH | Side Effect: `_compute_term_structure` Mutates Input DataFrame
**File:** `/home/pmcerlean/projects/attix-credit-spreads/ml/iv_analyzer.py` (line 197)

```python
options_chain['dte'] = (options_chain['expiration'] - now).dt.days
```

This line mutates the caller's `options_chain` DataFrame by adding a `dte` column directly, without making a copy first. This can cause unexpected behavior in downstream consumers of the same DataFrame.

**Severity:** HIGH -- silent data corruption of shared mutable state.

---

### CQ-08 | MEDIUM | Dead Code: Unused Imports
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/main.py` (line 42): `import yfinance as yf` -- never used directly (data_cache wraps yf)
- `/home/pmcerlean/projects/attix-credit-spreads/strategy/spread_strategy.py` (line 9): `import numpy as np` -- never used
- `/home/pmcerlean/projects/attix-credit-spreads/strategy/spread_strategy.py` (line 10): `import pandas as pd` -- only used for type hints in method signatures, but pd itself isn't used for operations
- `/home/pmcerlean/projects/attix-credit-spreads/ml/feature_engine.py` (line 19): `from scipy import stats` -- never used
- `/home/pmcerlean/projects/attix-credit-spreads/ml/iv_analyzer.py` (line 16): `from scipy import interpolate` -- never used
- `/home/pmcerlean/projects/attix-credit-spreads/ml/iv_analyzer.py` (line 17): `from scipy.stats import norm` -- never used
- `/home/pmcerlean/projects/attix-credit-spreads/web/components/positions/live-positions.tsx` (line 3): `TrendingUp, TrendingDown, Clock` imported but never used
- `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` (line 8): `import os` -- only used in `__main__` block but imported at module level
- `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` (line 14): `from typing import ... List, Optional` -- `List` used but `Optional` not used in main class

**Severity:** MEDIUM -- clutters the codebase and creates false dependency impressions.

---

### CQ-09 | MEDIUM | Inconsistent `distance_to_short` Calculation (Logic Error)
**File:** `/home/pmcerlean/projects/attix-credit-spreads/strategy/spread_strategy.py` (lines 270-273)

For bull put spreads:
```python
distance_to_short = short_strike - current_price  # This will be NEGATIVE
```

For a bull put spread, the short strike is *below* the current price, so `short_strike - current_price` produces a *negative* value. But this metric is meant to represent how far the price needs to drop to reach the short strike (a positive distance). The bear call side correctly computes `current_price - short_strike`. The bull put side should be `current_price - short_strike`.

**Severity:** MEDIUM -- semantically incorrect value that could affect scoring and display.

---

### CQ-10 | MEDIUM | Magic Numbers Throughout
**Files and locations:**
- `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (line 335): `1.2` in decay acceleration factor
- `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (line 341): `0.3` in remaining extrinsic calculation
- `/home/pmcerlean/projects/attix-credit-spreads/ml/ml_pipeline.py` (line 193-194): `0.30` and `-1.0` for expected return/loss
- `/home/pmcerlean/projects/attix-credit-spreads/main.py` (line 236): `0.6` and `0.4` for ML/rules score blending
- `/home/pmcerlean/projects/attix-credit-spreads/main.py` (line 243): `0.7` for event risk threshold
- `/home/pmcerlean/projects/attix-credit-spreads/main.py` (line 276): `60` for alert score threshold
- `/home/pmcerlean/projects/attix-credit-spreads/web/lib/pnl.ts` (line 23): `0.7` exponent for time decay
- `/home/pmcerlean/projects/attix-credit-spreads/web/lib/pnl.ts` (line 34-35): `0.3`, `2`, `3`, `0.5` for price movement factors
- `/home/pmcerlean/projects/attix-credit-spreads/backtest/backtester.py` (lines 276-288): `1.05`, `0.95`, `0.3`, `0.7`, `35` in spread value estimation

These are unexplained numerical constants critical to P&L calculations and scoring.

**Severity:** MEDIUM -- makes financial logic opaque and hard to audit/tune.

---

### CQ-11 | MEDIUM | Inconsistent P&L Models Between Backend and Frontend
**Files:**
- Backend: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` `_evaluate_position()` (lines 303-363)
- Frontend: `/home/pmcerlean/projects/attix-credit-spreads/web/lib/pnl.ts` `calcUnrealizedPnL()` (lines 8-48)

These two completely different P&L estimation models exist for the same purpose. The backend uses a linear decay model with a `1.2` acceleration factor and intrinsic-value-based ITM calculation. The frontend uses a power-law decay (`Math.pow(ratio, 0.7)`) with 70/30 theta/price weighting. They will produce different unrealized P&L values for the same trade, confusing users who see different numbers from the Python scanner vs. the web dashboard.

**Severity:** MEDIUM -- produces inconsistent P&L across views of the same trade.

---

### CQ-12 | MEDIUM | FOMC Dates Will Become Stale
**File:** `/home/pmcerlean/projects/attix-credit-spreads/shared/constants.py` (lines 6-24)

FOMC dates are hardcoded through 2026. After December 2026, the list becomes empty, and all FOMC-based event risk detection silently stops working. There is no warning or fallback mechanism.

**Severity:** MEDIUM -- the system will silently degrade after the last date passes.

---

### CQ-13 | MEDIUM | `_calculate_rsi` Wrapper Pattern Repeated
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/ml/regime_detector.py` (line 359-363): `_calculate_rsi` method that just delegates to `shared.indicators.calculate_rsi`
- `/home/pmcerlean/projects/attix-credit-spreads/ml/feature_engine.py` (line 474-476): `_calculate_rsi` method that just delegates to `shared.indicators.calculate_rsi`

Both classes define private wrapper methods that add zero logic. They should call the shared function directly.

**Severity:** MEDIUM -- unnecessary indirection.

---

### CQ-14 | MEDIUM | Potential Division by Zero
**File:** `/home/pmcerlean/projects/attix-credit-spreads/strategy/spread_strategy.py` (line 292)

```python
'risk_reward': round(credit / max_loss, 2) if max_loss > 0 else 0,
```

While `max_loss` is guarded against zero, credit could be extremely close to `spread_width`, making `max_loss` a very small positive number but not zero. This isn't a bug per se, but earlier in the function `max_loss = spread_width - credit` could theoretically be zero if `credit == spread_width` (edge case with bad data). The `min_credit_pct` check helps but doesn't fully prevent it.

**Severity:** MEDIUM

---

### CQ-15 | MEDIUM | `LivePositions` Component Receives `data` Prop But Is Called Without It
**File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/page.tsx` (line 78)

```tsx
<LivePositions />
```

The `LivePositions` component at `/home/pmcerlean/projects/attix-credit-spreads/web/components/positions/live-positions.tsx` (line 40) expects `data?: PositionsData | null` as a prop, but `page.tsx` renders it without passing any data. The component will immediately return `null` since `data` is `undefined` and `!data` is truthy. It was likely supposed to be `<LivePositions data={positions} />`.

**Severity:** MEDIUM -- the "Live System Positions" section on the homepage will never render.

---

### CQ-16 | MEDIUM | Inconsistent Naming: `constants.py` vs `shared/constants.py`
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/constants.py` -- contains `MAX_CONTRACTS_PER_TRADE`, `MANAGEMENT_DTE_THRESHOLD`, etc.
- `/home/pmcerlean/projects/attix-credit-spreads/shared/constants.py` -- contains `FOMC_DATES`, `CPI_RELEASE_DAYS`

Two separate constants files at different locations. It is confusing which to import from. `paper_trader.py` imports from `constants` (root), `feature_engine.py` imports from `shared.constants`.

**Severity:** MEDIUM -- confusing module structure.

---

### CQ-17 | MEDIUM | `generate_alerts_only` Is Misleading
**File:** `/home/pmcerlean/projects/attix-credit-spreads/main.py` (lines 317-327)

The docstring says "Generate alerts from recent scans without new scanning" but the implementation calls `self.scan_opportunities()` which performs a full scan. The name and documentation are misleading.

**Severity:** MEDIUM -- naming is deceptive.

---

### CQ-18 | LOW | Inconsistent Error Handling in API Routes
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/alerts/route.ts` -- returns 200 with empty data on error
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/trades/route.ts` -- returns 500 with error message on error
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/route.ts` -- returns 200 with zero data inside a nested try, 500 on outer catch

Some routes return 200 with empty data on failure (soft fail), others return 500. This inconsistency makes error handling on the client unpredictable.

**Severity:** LOW -- mostly a consistency issue; the frontend handles both patterns.

---

### CQ-19 | LOW | Type Annotations Missing on Several Python Functions
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/shared/indicators.py` (line 70): `sanitize_features(X)` -- no type hint on parameter or return
- `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (line 303): `_evaluate_position` returns `tuple` but no type annotation
- `/home/pmcerlean/projects/attix-credit-spreads/backtest/backtester.py` multiple methods have return types specified as `Dict` but could use `TypedDict` for precision

**Severity:** LOW

---

### CQ-20 | LOW | Commented-Out / Dead Variables
**File:** `/home/pmcerlean/projects/attix-credit-spreads/ml/feature_engine.py` (line 53)

```python
self.cpi_months = list(range(1, 13))
```

This variable is assigned but never read anywhere in the codebase.

**Severity:** LOW

---

### CQ-21 | LOW | `feature_cache` and `cache_timestamps` Never Used
**File:** `/home/pmcerlean/projects/attix-credit-spreads/ml/feature_engine.py` (lines 46-47)

```python
self.feature_cache = {}
self.cache_timestamps = {}
```

These instance variables are initialized but never read or written to anywhere in the class.

**Severity:** LOW -- dead code.

---

### CQ-22 | LOW | `Tuple` Type Hint Could Be More Specific
**File:** `/home/pmcerlean/projects/attix-credit-spreads/ml/signal_model.py` (line 15)

`from typing import ... Tuple` is imported but `generate_synthetic_training_data` returns `Tuple[pd.DataFrame, np.ndarray]` which is good. However `_features_to_array` return type is `Optional[np.ndarray]` which is fine. Minor: the `backtest` method at line 269 could specify return type more precisely than `Dict`.

**Severity:** LOW

---

### CQ-23 | LOW | `UserPortfolio` Interface Duplicates `Portfolio` from Types
**File:** `/home/pmcerlean/projects/attix-credit-spreads/web/lib/paper-trades.ts` (lines 10-14)

```typescript
export interface UserPortfolio {
  trades: PaperTrade[];
  starting_balance: number;
  created_at: string;
}
```

This is nearly identical to `Portfolio` from `lib/types.ts` (lines 34-39) which has `trades`, `starting_balance`, `created_at`, and `user_id`. `UserPortfolio` just drops `user_id`.

**Severity:** LOW

---

### CQ-24 | LOW | `sys.path.insert` Workaround in Multiple Files
**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/main.py` (line 32)
- `/home/pmcerlean/projects/attix-credit-spreads/demo.py` (line 12)

Both files manipulate `sys.path`. This is a sign of missing proper package configuration (e.g., a `pyproject.toml` or `setup.py` with an editable install).

**Severity:** LOW

---

### CQ-25 | LOW | `lookback_days` Parameter Name Reused With Different Semantics
**File:** `/home/pmcerlean/projects/attix-credit-spreads/ml/sentiment_scanner.py` (line 63)

The `scan()` method has `lookback_days: int = 7` but it actually means "days to look **ahead**" (see line 83: `scan_end = now + timedelta(days=lookback_days)`). The name is misleading -- it should be `lookahead_days` or `days_ahead`.

**Severity:** LOW -- naming confusion.

---

## Summary Table

| ID | Severity | Category | File(s) | Brief Description |
|----|----------|----------|---------|-------------------|
| CQ-01 | CRITICAL | API Contracts | `web/lib/types.ts`, `web/lib/api.ts` | Duplicate incompatible `Alert` interfaces |
| CQ-02 | HIGH | DRY | `shared/indicators.py`, `polygon_provider.py` | IV rank calculation duplicated |
| CQ-03 | HIGH | DRY | `paper_trader.py`, `trade_tracker.py` | `_atomic_json_write` copy-pasted |
| CQ-04 | HIGH | DRY | `api/alerts/route.ts`, `api/positions/route.ts` | `tryRead` helper duplicated |
| CQ-05 | HIGH | DRY | `api/scan/route.ts`, `api/backtest/run/route.ts` | Rate limiter pattern duplicated |
| CQ-06 | HIGH | DRY / Consistency | `web/lib/utils.ts`, `live-positions.tsx` | `formatCurrency` defined twice with different behavior |
| CQ-07 | HIGH | Side Effects | `ml/iv_analyzer.py` | Mutates input DataFrame without copy |
| CQ-08 | MEDIUM | Dead Code | Multiple files | Unused imports (numpy, scipy, yfinance, lucide icons) |
| CQ-09 | MEDIUM | Logic Error | `spread_strategy.py` | `distance_to_short` is negative for bull put spreads |
| CQ-10 | MEDIUM | Magic Numbers | Multiple files | Unexplained constants in financial calculations |
| CQ-11 | MEDIUM | Consistency | `paper_trader.py`, `web/lib/pnl.ts` | Two different P&L models for the same trades |
| CQ-12 | MEDIUM | Consistency | `shared/constants.py` | Hardcoded FOMC dates expire end of 2026 |
| CQ-13 | MEDIUM | DRY | `regime_detector.py`, `feature_engine.py` | Needless `_calculate_rsi` wrappers |
| CQ-14 | MEDIUM | Logic Error | `spread_strategy.py` | Possible division by very small `max_loss` |
| CQ-15 | MEDIUM | Logic Error | `web/app/page.tsx` | `LivePositions` rendered without data prop |
| CQ-16 | MEDIUM | Naming | `constants.py`, `shared/constants.py` | Two constant files with confusing scoping |
| CQ-17 | MEDIUM | Naming | `main.py` | `generate_alerts_only` runs a full scan |
| CQ-18 | LOW | Consistency | API routes | Inconsistent error response patterns |
| CQ-19 | LOW | Type Safety | Multiple Python files | Missing type annotations |
| CQ-20 | LOW | Dead Code | `feature_engine.py` | `cpi_months` assigned but never used |
| CQ-21 | LOW | Dead Code | `feature_engine.py` | `feature_cache` / `cache_timestamps` never used |
| CQ-22 | LOW | Type Safety | `signal_model.py` | Return types could be more specific |
| CQ-23 | LOW | DRY | `web/lib/paper-trades.ts` | `UserPortfolio` nearly duplicates `Portfolio` |
| CQ-24 | LOW | Code Smell | `main.py`, `demo.py` | `sys.path.insert` instead of proper packaging |
| CQ-25 | LOW | Naming | `sentiment_scanner.py` | `lookback_days` actually means lookahead |

---

## Top 5 Priority Fixes

1. **CQ-01 (CRITICAL):** Consolidate the dual `Alert` type definitions. Delete the `Alert` interface from `web/lib/api.ts` and have all components import from `web/lib/types.ts`. Add any missing fields needed by the API layer to the canonical type.

2. **CQ-15 (MEDIUM):** Pass the `positions` data to `<LivePositions data={positions} />` in `page.tsx`. Without this fix, the live positions panel is invisible on the homepage -- a core user-facing feature is broken.

3. **CQ-02 + CQ-03 (HIGH):** Extract shared utilities: move `_atomic_json_write` to a shared utility module and have `PolygonProvider.calculate_iv_rank` delegate to `shared.indicators.calculate_iv_rank` instead of reimplementing the formula.

4. **CQ-07 (HIGH):** Fix the DataFrame mutation in `IVAnalyzer._compute_term_structure` by adding `options_chain = options_chain.copy()` before modifying it.

5. **CQ-11 (MEDIUM):** Unify the P&L model. Choose one canonical formula (the frontend's power-law decay model is more realistic) and use it on both backend and frontend, or ensure the web dashboard always uses the backend-computed P&L values rather than recalculating.

---

## Overall Score: 5.5 / 10

**Justification:** The codebase demonstrates solid architectural thinking (clean module separation, dependency injection, circuit breakers, typed dicts, proper error hierarchies) but suffers from significant DRY violations across both the Python backend and TypeScript frontend, a critical type definition conflict that undermines frontend reliability, inconsistent financial calculation models between backend and frontend, and a non-rendering core UI component -- collectively these issues place it below production-ready quality while acknowledging the strong foundational design.

---

# Attix Credit Spreads -- Security Audit Report

**Auditor:** Claude Opus 4.6 (Automated Security Audit)
**Date:** 2026-02-16
**Scope:** Full codebase at `/home/pmcerlean/projects/attix-credit-spreads`
**Classification:** CONFIDENTIAL

---

## Executive Summary

This is a full-stack trading system comprising a Python backend (ML pipeline, options analysis, paper trading) and a Next.js 14/15 frontend deployed on Railway. The system handles financial trading operations, AI chat (via OpenAI), and user paper portfolios. The codebase shows evidence of iterative security hardening (timing-safe token comparison, `execFile` instead of `exec`, Zod validation, secret stripping), but several significant issues remain. The most critical concern is the architectural pattern of exposing the API authentication token to the browser via `NEXT_PUBLIC_` prefix, combined with several client-side `fetch()` calls that omit the Authorization header entirely, and a git-tracked pickle file that enables arbitrary code execution if tampered with.

---

## Detailed Findings

### SEC-01: API Auth Token Exposed to Client-Side via NEXT_PUBLIC_ Prefix (HIGH)

**CVSS 3.1:** 7.5 (High) -- AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N

**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/.env.example` (line 5)
- `/home/pmcerlean/projects/attix-credit-spreads/web/lib/hooks.ts` (line 4)
- `/home/pmcerlean/projects/attix-credit-spreads/web/lib/api.ts` (line 143)

**Description:** The `NEXT_PUBLIC_API_AUTH_TOKEN` env var is shipped to the browser via Next.js's `NEXT_PUBLIC_` convention. This means the single secret protecting all API routes is embedded in the JavaScript bundle served to every visitor. Any browser DevTools inspection or source map reveals the token. A comment in `.env.example` acknowledges this is "by design for self-hosted, single-user deployments behind a VPN/firewall," but there is no enforcement that a VPN is in place, and Railway deployments are typically internet-facing.

**Impact:** Anyone who can access the frontend URL can extract the API token and call any authenticated API endpoint directly (scan, backtest, config read/write, paper trades CRUD).

**Recommendation:** Implement a server-side session/cookie-based auth proxy. The token should never appear in client-side code.

---

### SEC-02: Multiple Client-Side fetch() Calls Missing Authorization Header (HIGH)

**CVSS 3.1:** 7.5 (High) -- AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:H/A:N

**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/settings/page.tsx` (lines 17, 36)
- `/home/pmcerlean/projects/attix-credit-spreads/web/components/alerts/alert-card.tsx` (line 39)
- `/home/pmcerlean/projects/attix-credit-spreads/web/components/sidebar/ai-chat.tsx` (line 55)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/my-trades/page.tsx` (line 42)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/backtest/page.tsx` (line 39)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/positions/page.tsx` (line 16)
- `/home/pmcerlean/projects/attix-credit-spreads/web/components/layout/header.tsx` (line 14)

**Description:** While `lib/hooks.ts` and `lib/api.ts` correctly attach the `Authorization: Bearer <token>` header, at least 7 direct `fetch('/api/...')` calls in components do NOT include any Authorization header. The middleware at `/web/middleware.ts` will reject these calls with 401 (or 503 if token is configured). This means core functionality (settings, chat, paper trading, alert viewing from the header component) is broken when auth is enabled, or conversely, auth is disabled/misconfigured in production to make things work.

**Impact:** Either auth is effectively bypassed in production (all API routes unprotected), or core features are broken for authenticated users. Both outcomes are severe for a trading system.

**Recommendation:** Route all API calls through the `apiFetch` helper in `lib/api.ts` or the SWR hooks in `lib/hooks.ts` that already attach auth headers. Audit every `fetch('/api/')` call.

---

### SEC-03: Git-Tracked Pickle File Enables Arbitrary Code Execution (CRITICAL)

**CVSS 3.1:** 9.8 (Critical) -- AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/ml/models/signal_model_20260213.pkl` (tracked in git)
- `/home/pmcerlean/projects/attix-credit-spreads/ml/signal_model.py` (line 434)

**Description:** A `.pkl` (pickle) file is committed to the git repository and loaded at runtime via `joblib.load(filepath)` (line 434 of `signal_model.py`). Python pickle deserialization executes arbitrary code embedded in the file. If an attacker can submit a PR that modifies this file, or if the repo is compromised, they gain arbitrary code execution on the server when the ML pipeline initializes. The file is 1.5KB and could contain a payload. Joblib uses pickle under the hood and is equally vulnerable.

**Impact:** Remote code execution on the server. Complete system compromise.

**Recommendation:** (1) Remove the `.pkl` file from git tracking. (2) Add `*.pkl`, `*.joblib`, `*.pickle` to `.gitignore`. (3) Generate models from code at deployment time or load from a verified artifact store with checksums. (4) Consider `skops` for safe model serialization.

---

### SEC-04: Child Process Execution in API Routes (MEDIUM)

**CVSS 3.1:** 5.3 (Medium) -- AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:H

**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` (line 35)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/run/route.ts` (line 36)

**Description:** Both routes use `execFile("python3", ["main.py", "scan/backtest"])` to spawn Python subprocesses. While `execFile` (not `exec`) is used and arguments are hardcoded (no user input injection), each invocation cold-starts the entire Python system including ML model training, yfinance downloads, etc. The 120s/300s timeouts and rate limits (5/hr for scan, 3/hr for backtest) are helpful but in-memory only (lost on server restart). A sustained attack can exhaust server resources.

**Impact:** Denial of service via resource exhaustion. Each subprocess consumes significant CPU/memory for model initialization.

**Recommendation:** Replace with a persistent Python service (FastAPI/Flask) behind a message queue. If subprocess pattern must remain, add per-IP rate limiting at the middleware level and consider a semaphore to limit concurrent subprocess count.

---

### SEC-05: Weak User Identity Derivation (MEDIUM)

**CVSS 3.1:** 6.5 (Medium) -- AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N

**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/middleware.ts` (lines 38, 43-51)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` (line 34-36)

**Description:** The `simpleHash()` function (lines 43-51 of middleware.ts) uses a non-cryptographic DJB2-like hash to derive a userId from the auth token: `hash = ((hash << 5) - hash) + char`. This produces very short identifiers (base-36 of a 32-bit integer). Since all users sharing the same `API_AUTH_TOKEN` produce the same userId, there is no multi-user isolation. Furthermore, the paper trades route falls back to `'default'` if x-user-id is missing (line 35-36), meaning all unauthenticated users share one portfolio.

**Impact:** No user isolation. All authenticated users share the same paper trading portfolio. In a single-token-shared deployment, this is by design but becomes a vulnerability if multiple people use the system.

**Recommendation:** Implement proper per-user authentication (e.g., JWT, session cookies) or at minimum use `crypto.createHash('sha256')` for the userId derivation.

---

### SEC-06: Config File Write Enables Server-Side YAML Injection (MEDIUM)

**CVSS 3.1:** 6.5 (Medium) -- AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:L

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts` (lines 102-119)

**Description:** The `POST /api/config` endpoint reads the existing `config.yaml`, shallow-merges it with user-supplied data, and writes it back. While Zod validation is applied, the schema uses `.optional()` on all fields and the merge is shallow (`{ ...existing, ...parsed.data }`). This means a malicious request could overwrite top-level keys. The `alerts.json_file` and `alerts.text_file` fields accept arbitrary strings, potentially enabling path traversal when the Python backend later reads/writes these files. Additionally, `js-yaml.load()` is used (line 94, 110) without explicit safe schema specification, though js-yaml v4 defaults to safe loading.

**Impact:** An attacker with the API token could modify the trading system configuration (change tickers, risk parameters, alert file paths) to manipulate trading behavior or write files to arbitrary paths.

**Recommendation:** (1) Validate file path fields against a whitelist or restrict to a known directory. (2) Use `yaml.load(data, { schema: yaml.JSON_SCHEMA })` explicitly. (3) Implement deep merge with strict key validation rather than shallow spread.

---

### SEC-07: Rate Limiter Bypass via X-Forwarded-For Spoofing (MEDIUM)

**CVSS 3.1:** 5.3 (Medium) -- AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:L

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts` (lines 66-69)

**Description:** The chat endpoint rate limiter extracts client IP from `x-forwarded-for` header. The code takes the *last* IP in the comma-separated list (`.pop()`), which is slightly better than taking the first (since proxies append), but still attacker-controllable. Railway's proxy may set this header, but without configuring Next.js to trust only Railway's proxy, an attacker can inject arbitrary IPs to bypass the 10 req/min rate limit.

**Impact:** Rate limit bypass on the chat endpoint, potentially leading to OpenAI API cost exhaustion.

**Recommendation:** Configure trusted proxy settings. Use Railway-specific headers or the connection's remote address. Consider middleware-level rate limiting instead of per-route implementations.

---

### SEC-08: In-Memory Rate Limiters Reset on Deploy/Restart (LOW)

**CVSS 3.1:** 3.7 (Low)

**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` (lines 10-13)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/run/route.ts` (lines 12-15)
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts` (lines 17-42)

**Description:** All rate limiters use in-memory arrays/maps. On Railway, each deploy or process restart (including auto-scaling) resets all rate limit counters. The scan and backtest rate limits (5/hr and 3/hr respectively) can be circumvented by timing requests around deploys.

**Impact:** Rate limiting is unreliable in a PaaS environment. An attacker who triggers deploys can reset limits.

**Recommendation:** Use Redis or a persistent store for rate limit state, or use a dedicated rate limiting service.

---

### SEC-09: CSP Policy Allows 'unsafe-inline' and 'unsafe-eval' (MEDIUM)

**CVSS 3.1:** 4.7 (Medium) -- AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:N/A:N

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/next.config.js` (lines 19-20)

**Description:** The Content-Security-Policy header includes `script-src 'self' 'unsafe-inline' 'unsafe-eval'` and `style-src 'self' 'unsafe-inline'`. These directives effectively negate much of the XSS protection CSP provides. The `'unsafe-eval'` is particularly concerning as it allows `eval()` and similar constructs that are common XSS payloads.

**Impact:** Reduced XSS protection. If an injection vector is found, the CSP will not block it.

**Recommendation:** Use nonce-based CSP for scripts. Next.js supports `nonce` via `next/script`. Remove `'unsafe-eval'` unless strictly required by a dependency.

---

### SEC-10: Third-Party Script Inclusion (TradingView Widget) (MEDIUM)

**CVSS 3.1:** 5.4 (Medium) -- AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/components/layout/ticker.tsx` (lines 10-36)

**Description:** The ticker component dynamically creates a `<script>` element loading from `https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js`. This is done via `document.createElement('script')` with `innerHTML` set to JSON config. The `containerRef.current.innerHTML = ''` on line 10 and the dynamic script insertion means if TradingView's CDN is compromised, arbitrary JavaScript executes in the application context. There is no Subresource Integrity (SRI) hash.

**Impact:** Supply chain attack vector. TradingView script has full access to the page DOM, including the API auth token in memory.

**Recommendation:** Add `integrity` and `crossorigin` attributes to the script element. Pin to a specific version. Consider self-hosting the widget script.

---

### SEC-11: OpenAI API Key Used Server-Side Without Proxy Isolation (LOW)

**CVSS 3.1:** 3.1 (Low)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts` (lines 90-127)

**Description:** The OpenAI API key (`process.env.OPENAI_API_KEY`) is used in the chat API route. User messages are forwarded to OpenAI with minimal sanitization (only message array truncation to last 10). There is no content filtering for prompt injection attacks that could manipulate the system prompt or extract sensitive information about the system's configuration.

**Impact:** Prompt injection could leak the system prompt or cause the AI to generate misleading trading advice. Cost abuse if rate limits are bypassed.

**Recommendation:** Implement content filtering on user messages. Add spend limits on the OpenAI account. Consider logging all chat interactions for audit purposes.

---

### SEC-12: No CORS Configuration (INFO)

**CVSS 3.1:** 2.0 (Low)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/next.config.js`

**Description:** No explicit CORS headers are configured. Next.js API routes default to same-origin, which is generally safe, but the lack of explicit `Access-Control-Allow-Origin` configuration means there is no defense-in-depth against cross-origin attacks if the SameSite cookie policy or other browser protections fail.

**Impact:** Low. Default same-origin policy provides adequate protection, but explicit CORS configuration would be more robust.

**Recommendation:** Add explicit CORS headers restricting to the deployment's origin.

---

### SEC-13: TypeScript Build Errors Ignored (LOW)

**CVSS 3.1:** 2.0 (Low)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/next.config.js` (lines 27-29)

**Description:** `typescript: { ignoreBuildErrors: true }` means type errors do not prevent deployment. Type errors can mask security issues like incorrect type coercions, missing null checks, or misused APIs.

**Impact:** Type safety violations may go undetected, potentially leading to runtime errors or security issues.

**Recommendation:** Enable strict TypeScript checking (`ignoreBuildErrors: false`) and fix all type errors.

---

### SEC-14: Math.random() for Trade IDs (LOW)

**CVSS 3.1:** 2.0 (Low)

**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` (line 165)
- `/home/pmcerlean/projects/attix-credit-spreads/web/lib/paper-trades.ts` (line 18)

**Description:** Trade IDs are generated using `Math.random()` which is not cryptographically secure: `PT-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`. While the server-side route also uses `crypto.randomUUID()` for temp file names, the trade ID itself uses a predictable PRNG. However, the file already imports `randomUUID` from `crypto` (line 6) but does not use it for trade IDs.

**Impact:** Trade IDs are predictable, potentially allowing enumeration of other users' trades (if multi-user were implemented).

**Recommendation:** Use `crypto.randomUUID()` for trade IDs.

---

### SEC-15: Docker Entrypoint Wildcard Exec (LOW)

**CVSS 3.1:** 2.5 (Low)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/docker-entrypoint.sh` (line 15)

**Description:** The entrypoint script has a catch-all `*) exec "$@" ;;` that executes any command passed as Docker arguments. While Docker generally controls the CMD, if someone misconfigures the container, arbitrary commands could be run.

**Impact:** Low in normal Docker deployment. Increases attack surface if container orchestration is misconfigured.

**Recommendation:** Remove the catch-all or restrict to known commands.

---

### SEC-16: Sensitive Data in Error Responses (LOW)

**CVSS 3.1:** 3.1 (Low)

**Files:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts` (line 107)

**Description:** The config POST endpoint returns Zod validation errors directly to the client: `apiError('Validation failed', 400, parsed.error.flatten())`. While this aids development, in production it can reveal internal schema structure and field names to attackers probing the API.

**Impact:** Information disclosure of internal API structure.

**Recommendation:** Return generic validation error messages in production. Log detailed errors server-side.

---

### SEC-17: Python YAML Uses safe_load Correctly (GOOD)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/utils.py` (line 47)

**Note:** The Python side correctly uses `yaml.safe_load()`. This is not a finding but confirms a positive security practice.

---

### SEC-18: Security Headers Properly Configured (GOOD)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/next.config.js` (lines 7-25)

**Note:** The following headers are correctly set: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`, `Permissions-Policy`, `HSTS` with preload, `frame-ancestors: 'none'`. This is a strong baseline.

---

### SEC-19: Timing-Safe Token Comparison (GOOD)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/middleware.ts` (lines 4-8)

**Note:** Token comparison uses `crypto.timingSafeEqual` with SHA-256 hashing to equalize buffer lengths. This correctly prevents timing attacks on token validation.

---

### SEC-20: Fail-Closed Auth When Token Not Configured (GOOD)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/middleware.ts` (lines 26-28)

**Note:** When `API_AUTH_TOKEN` is not set, the middleware returns 503 rather than allowing access. This is a correct fail-closed design.

---

### SEC-21: Non-Root Docker User (GOOD)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/Dockerfile` (lines 44-47)

**Note:** The production Dockerfile creates a non-root `appuser` and switches to it before running the application. This limits the impact of container escapes.

---

### SEC-22: Secret Stripping in Config API (GOOD)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts` (lines 9-24)

**Note:** The `stripSecrets()` function properly redacts `api_key`, `api_secret`, `bot_token`, and `chat_id` fields before returning config data to the client.

---

### SEC-23: File Path Sanitization in Paper Trades (GOOD)

**File:**
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` (line 61)

**Note:** The `userFile()` function sanitizes the userId with `userId.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 64)`, preventing path traversal in file names.

---

## Summary Table

| ID | Severity | CVSS | Category | Description |
|--------|----------|------|-------------------------------|---------------------------------------------------|
| SEC-01 | HIGH | 7.5 | Sensitive Data Exposure | API token exposed to browser via NEXT_PUBLIC_ |
| SEC-02 | HIGH | 7.5 | Broken Authentication | 7+ client fetch() calls missing auth headers |
| SEC-03 | CRITICAL | 9.8 | Insecure Deserialization | Git-tracked .pkl file loaded via joblib.load() |
| SEC-04 | MEDIUM | 5.3 | Denial of Service | Child process spawning in API routes |
| SEC-05 | MEDIUM | 6.5 | Broken Access Control | Weak non-crypto hash for user identity |
| SEC-06 | MEDIUM | 6.5 | Injection / Access Control | Config write with path traversal potential |
| SEC-07 | MEDIUM | 5.3 | Rate Limit Bypass | X-Forwarded-For spoofing in chat rate limiter |
| SEC-08 | LOW | 3.7 | Security Misconfiguration | In-memory rate limiters reset on restart |
| SEC-09 | MEDIUM | 4.7 | XSS | CSP allows unsafe-inline and unsafe-eval |
| SEC-10 | MEDIUM | 5.4 | Supply Chain | Third-party TradingView script without SRI |
| SEC-11 | LOW | 3.1 | Injection | No prompt injection filtering for OpenAI chat |
| SEC-12 | INFO | 2.0 | Security Misconfiguration | No explicit CORS configuration |
| SEC-13 | LOW | 2.0 | Security Misconfiguration | TypeScript build errors ignored |
| SEC-14 | LOW | 2.0 | Cryptographic Issues | Math.random() for trade IDs |
| SEC-15 | LOW | 2.5 | Security Misconfiguration | Docker entrypoint wildcard exec |
| SEC-16 | LOW | 3.1 | Information Disclosure | Zod validation errors returned to client |

---

## Attack Chain Analysis

### Chain 1: Full System Compromise via Pickle Poisoning
1. Attacker forks the repo or submits a malicious PR
2. Modifies `ml/models/signal_model_20260213.pkl` with a payload
3. If merged and deployed, the Python backend executes the payload during ML pipeline initialization
4. Attacker gains remote code execution on the server, accessing all API keys, trading credentials, and database

### Chain 2: Unauthorized Trading Configuration Manipulation
1. Attacker visits the deployed web app in a browser
2. Opens DevTools, extracts `NEXT_PUBLIC_API_AUTH_TOKEN` from the JavaScript bundle
3. Uses the token to call `POST /api/config` with modified risk parameters (e.g., `max_risk_per_trade: 100`, `max_positions: 999`)
4. Next scan cycle uses the manipulated config, potentially causing the paper trader (or the Alpaca-connected real broker) to open oversized positions
5. Financial loss ensues

### Chain 3: Chat API Cost Exhaustion
1. Attacker spoofs `X-Forwarded-For` header with rotating IPs
2. Sends thousands of requests to `POST /api/chat` bypassing the per-IP rate limit
3. Each request incurs OpenAI API costs (gpt-4o-mini)
4. Monthly bill spikes without limit

---

## Positive Security Practices Observed

1. **Timing-safe token comparison** using `crypto.timingSafeEqual` (SEC-19)
2. **Fail-closed authentication** when API_AUTH_TOKEN is unset (SEC-20)
3. **Non-root Docker user** in production Dockerfile (SEC-21)
4. **Secret stripping** in config API responses (SEC-22)
5. **File path sanitization** for user trade files (SEC-23)
6. **`execFile` over `exec`** -- commands are not shell-interpreted
7. **Zod validation** on config POST and paper-trades POST
8. **Python uses `yaml.safe_load()`** (SEC-17)
9. **Strong security headers** (HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) (SEC-18)
10. **Atomic file writes** with temp-file-then-rename pattern in both Python and TypeScript
11. **Circuit breaker pattern** on external API calls (Tradier, Polygon)
12. **Concurrency guards** (scanInProgress, backtestInProgress flags)
13. **Environment variable substitution** for secrets in config.yaml rather than hardcoding

---

## Overall Score: 5.5 / 10

**Justification:** The codebase demonstrates security awareness (timing-safe comparison, fail-closed auth, secret stripping, execFile, non-root Docker) but has a critical deserialization vulnerability (git-tracked pickle), an architectural auth token exposure pattern that undermines the entire authentication system, and inconsistent auth header usage that suggests the auth layer may be non-functional in practice -- all unacceptable for a system that connects to real brokerage APIs (Alpaca) and handles financial operations.

---

# Performance Review: Attix Credit Spreads

## 1. Network I/O

### Finding 1.1: Redundant yfinance Downloads Across ML Components
**Severity: HIGH** | **Impact: 3-10s per ticker per analysis cycle**

The ML pipeline downloads the same ticker data multiple times per analysis call. `FeatureEngine.compute_features()` calls `_compute_technical_features()` which downloads ticker history, then `_compute_volatility_features()` downloads the same ticker again, then `_compute_market_features()` downloads VIX and SPY again. While `DataCache` mitigates this via TTL caching, each call still acquires a lock and performs a dict lookup + `DataFrame.copy()`.

- `/home/pmcerlean/projects/attix-credit-spreads/ml/feature_engine.py` lines 137, 205, 269, 282
- `/home/pmcerlean/projects/attix-credit-spreads/ml/regime_detector.py` lines 282-284

### Finding 1.2: Cache Bypass in Event Risk Features
**Severity: MEDIUM** | **Impact: 1-3s per ticker**

`_compute_event_risk_features` creates a raw `yf.Ticker(ticker)` object directly, completely bypassing the DataCache system.

- `/home/pmcerlean/projects/attix-credit-spreads/ml/feature_engine.py` line 318

### Finding 1.3: Polygon Provider Fetches ALL Options Then Filters
**Severity: HIGH** | **Impact: 5-30s per scan depending on option chain size**

`get_full_chain` fetches the entire options snapshot via paginated API calls (potentially thousands of contracts), then filters by DTE in Python. The Polygon API supports query parameters for expiration date filtering, but these are not used.

- `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` lines 164-236
- Same issue in `get_options_chain` at lines 94-162

### Finding 1.4: Tradier N+1 Query Pattern
**Severity: MEDIUM** | **Impact: ~0.5s per additional expiration date**

`get_full_chain` makes one HTTP request per expiration date sequentially, rather than fetching all expirations in a single request.

- `/home/pmcerlean/projects/attix-credit-spreads/strategy/tradier_provider.py` lines 161-169

### Finding 1.5: Sequential Cache Pre-warming
**Severity: MEDIUM** | **Impact: 3-6s at startup (1-2s per ticker)**

`DataCache.pre_warm()` downloads data for each ticker sequentially in a for-loop. With 3 configured tickers plus VIX and TLT, this takes 5-10s unnecessarily.

- `/home/pmcerlean/projects/attix-credit-spreads/shared/data_cache.py` lines 46-57

### Finding 1.6: Backtester Bypasses DataCache Entirely
**Severity: LOW** | **Impact: Variable, depends on backtest duration**

The backtester creates `yf.Ticker` objects directly and downloads data without using the shared `DataCache`.

- `/home/pmcerlean/projects/attix-credit-spreads/backtest/backtester.py` lines 130-131

---

## 2. Data Processing

### Finding 2.1: iterrows() on Options DataFrame
**Severity: HIGH** | **Impact: 10-100x slower than vectorized alternatives for large chains**

`_find_spreads` uses `iterrows()` to iterate over the options DataFrame. This is a well-known pandas anti-pattern that creates a new Series object per row.

- `/home/pmcerlean/projects/attix-credit-spreads/strategy/spread_strategy.py` line 241

### Finding 2.2: Unnecessary DataFrame.copy() on Every Cache Hit
**Severity: MEDIUM** | **Impact: 0.5-5ms per call, adds up across multiple components**

`DataCache.get_history()` returns `data.copy()` both on cache hits (line 32) and fresh downloads (line 41). While defensive copying prevents mutation bugs, it creates unnecessary allocations when callers only read the data.

- `/home/pmcerlean/projects/attix-credit-spreads/shared/data_cache.py` lines 32, 41

### Finding 2.3: Synthetic Training Data Generated via Python For-Loop
**Severity: LOW** | **Impact: ~1-2s for 2000 samples at startup**

`generate_synthetic_training_data` uses a Python for-loop over `n_samples` (default 2000) to generate training rows one at a time, rather than vectorized numpy operations.

- `/home/pmcerlean/projects/attix-credit-spreads/ml/signal_model.py` lines 501-611

### Finding 2.4: _analyze_trend Copies Entire DataFrame
**Severity: LOW** | **Impact: <1ms typically, negligible**

Creates a full copy of the input DataFrame to avoid mutating caller's data.

- `/home/pmcerlean/projects/attix-credit-spreads/strategy/technical_analysis.py` line 85

---

## 3. Memory

### Finding 3.1: Multiple Independent Cache Systems
**Severity: MEDIUM** | **Impact: 2-5x memory usage for cached data**

The codebase maintains at least 4 separate caches:
1. `DataCache` in `shared/data_cache.py` (15 min TTL)
2. `iv_history_cache` in `ml/iv_analyzer.py` (24 hour TTL, line 47-48)
3. `earnings_cache` in `ml/sentiment_scanner.py` (line 54)
4. `feature_cache` in `ml/feature_engine.py` (lines 47-48, appears unused)

Each may hold overlapping data with different TTLs and no coordination.

### Finding 3.2: Unbounded In-Memory Rate Limiter
**Severity: LOW** | **Impact: Minimal in practice due to 500-entry cap**

The chat route's rate limiter Map grows to 500 entries before cleaning. While capped, entries are only cleaned reactively.

- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts` line 29

### Finding 3.3: Full Portfolio JSON Loaded Into Memory Per Request
**Severity: LOW** | **Impact: Negligible for current scale (<1000 trades)**

Every paper-trades API request reads and parses the entire portfolio JSON file.

- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` lines 56-70

---

## 4. Frontend Performance

### Finding 4.1: SWR Polling Intervals May Be Too Aggressive
**Severity: LOW** | **Impact: Unnecessary network traffic during off-hours**

Alerts and positions poll every 5 minutes, paper-trades every 2 minutes. These run 24/7 regardless of market hours.

- `/home/pmcerlean/projects/attix-credit-spreads/web/lib/hooks.ts` lines 18-22, 26-30, 34-38

### Finding 4.2: Inline Stats Computation on Page Render
**Severity: LOW** | **Impact: <1ms, negligible**

The home page computes winner/loser statistics inline during render from the positions array.

- `/home/pmcerlean/projects/attix-credit-spreads/web/app/page.tsx` lines 48-56

### Finding 4.3: No Bundle Splitting or Dynamic Imports for Heavy Components
**Severity: LOW** | **Impact: Increased initial page load by ~50-100KB**

The Recharts library is imported statically. For pages that don't use charts, this adds unnecessary bundle weight.

- `/home/pmcerlean/projects/attix-credit-spreads/web/components/positions/live-positions.tsx`

---

## 5. File I/O

### Finding 5.1: Entire JSON File Rewritten on Every Trade Operation
**Severity: MEDIUM** | **Impact: 1-10ms per write, potential data loss under concurrent access**

Both `paper_trader.py` and `trade_tracker.py` serialize and write the complete JSON state on every single trade operation (open, close, update). Atomic writes via `tempfile + os.replace` prevent corruption but the full-file rewrite pattern degrades as trade history grows.

- `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` lines 103-106
- `/home/pmcerlean/projects/attix-credit-spreads/tracker/trade_tracker.py` (save methods)

### Finding 5.2: Dual JSON File Writes Per Save in Paper Trader
**Severity: LOW** | **Impact: 2x file I/O**

`_save_state` writes to both `paper_trades.json` and `paper_trades_dashboard.json` every time.

- `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` lines 103-106

### Finding 5.3: Config YAML Read on Every API Request
**Severity: LOW** | **Impact: <1ms, filesystem-cached by OS**

The config API route reads and parses `config.yaml` on every GET request.

- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts`

---

## 6. Subprocess Management

### Finding 6.1: Python Subprocess Spawned Per Scan Request
**Severity: HIGH** | **Impact: 2-5s startup overhead per scan**

The scan API route spawns a fresh Python process via `execFile` for each scan request. This includes Python interpreter startup, module imports (numpy, pandas, xgboost, sklearn, etc.), and configuration loading.

- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` lines 35-38

### Finding 6.2: Python Subprocess Spawned Per Backtest
**Severity: MEDIUM** | **Impact: 2-5s startup overhead per backtest**

Same subprocess pattern for backtests, with a generous 300s (5 min) timeout.

- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/run/route.ts` lines 36-39

### Finding 6.3: No Process Pool or Long-Running Worker
**Severity: HIGH** | **Impact: Cumulative 2-5s wasted per request**

There is no persistent Python process or process pool. Each request pays the full cold-start cost. A long-running Python worker process (e.g., via FastAPI/Flask with IPC, or a task queue like Celery) would amortize startup costs.

---

## 7. Algorithm Complexity

### Finding 7.1: O(n * window) Support/Resistance Level Detection
**Severity: MEDIUM** | **Impact: ~10-50ms for typical 252-day window**

`_find_support_levels` and `_find_resistance_levels` iterate over the price array and compute `min()` / `max()` over a rolling window at each position, yielding O(n * window) complexity. A deque-based sliding window would achieve O(n).

- `/home/pmcerlean/projects/attix-credit-spreads/strategy/technical_analysis.py` lines 186-189, 204-207

### Finding 7.2: Linear Search for Trade/Position by ID
**Severity: LOW** | **Impact: O(n) per lookup, negligible at current scale**

`trade_tracker.py` searches positions and trades lists linearly by ID.

- `/home/pmcerlean/projects/attix-credit-spreads/tracker/trade_tracker.py` lines 123-126, 165-168

### Finding 7.3: Closed Trades Iterated Every Time a Trade Closes
**Severity: LOW** | **Impact: O(n) per close, negligible for < 1000 trades**

`_close_trade` in `paper_trader.py` iterates all closed trades to compute `avg_winner` and `avg_loser` statistics on every single close operation, rather than maintaining running averages.

- `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` lines 411-414

### Finding 7.4: ML Pipeline Analyzes Opportunities Sequentially
**Severity: MEDIUM** | **Impact: Linear scaling with number of opportunities**

`batch_analyze` processes each trading opportunity in a serial loop, despite the work being embarrassingly parallel (each opportunity is independent).

- `/home/pmcerlean/projects/attix-credit-spreads/ml/ml_pipeline.py` lines 389-416

---

## 8. Caching

### Finding 8.1: get_ticker_obj() Creates New yf.Ticker Every Call
**Severity: MEDIUM** | **Impact: Minor object creation overhead, but bypasses any caching of ticker metadata**

`DataCache.get_ticker_obj()` returns `yf.Ticker(symbol)` without caching the object, meaning metadata like earnings dates, options expirations, etc. are re-fetched from Yahoo Finance each time.

- `/home/pmcerlean/projects/attix-credit-spreads/shared/data_cache.py` line 60

### Finding 8.2: Unused Feature Cache
**Severity: LOW** | **Impact: None (dead code)**

`FeatureEngine` declares `self.feature_cache` and `self.cache_timestamps` dicts (lines 47-48) but they are never populated or read, suggesting an abandoned caching attempt.

- `/home/pmcerlean/projects/attix-credit-spreads/ml/feature_engine.py` lines 47-48

### Finding 8.3: No HTTP Response Caching Headers
**Severity: LOW** | **Impact: Browsers re-request unchanged data**

API routes do not set `Cache-Control` or `ETag` headers, so browsers cannot cache responses.

- All files in `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/`

---

## 9. Startup Time

### Finding 9.1: ML Model Training at Startup
**Severity: HIGH** | **Impact: 10-30s at application startup**

When no saved model exists, the system trains an XGBoost model on 2000 synthetic samples, trains an HMM regime detector, and initializes all ML components before the system is ready to serve requests.

- `/home/pmcerlean/projects/attix-credit-spreads/main.py` lines 102-108
- `/home/pmcerlean/projects/attix-credit-spreads/ml/ml_pipeline.py` lines 82-118
- `/home/pmcerlean/projects/attix-credit-spreads/ml/signal_model.py` lines 106-109

### Finding 9.2: Sequential Import of Heavy Libraries
**Severity: MEDIUM** | **Impact: 3-5s for numpy/pandas/sklearn/xgboost imports**

All heavy ML libraries are imported at module level, contributing to cold start time for each subprocess spawn (see Finding 6.1).

- Various files across `/home/pmcerlean/projects/attix-credit-spreads/ml/`

### Finding 9.3: Cache Pre-warming is Sequential
**Severity: MEDIUM** | **Impact: 3-6s at startup**

Covered under Network I/O Finding 1.5. The pre-warm phase blocks startup.

---

## 10. Database/Storage

### Finding 10.1: JSON File as Primary Data Store
**Severity: HIGH** | **Impact: Poor scalability, no indexing, no concurrent access safety from multiple processes**

The entire system uses JSON files (`paper_trades.json`, `paper_trades_dashboard.json`, `trades.json`, `positions.json`) as its database. This means:
- No query capability beyond loading entire files
- No indexing
- No concurrent write safety across processes (only within a single process via threading locks or Promise chains)
- Full serialization/deserialization on every read/write

- `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py`
- `/home/pmcerlean/projects/attix-credit-spreads/tracker/trade_tracker.py`
- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts`

### Finding 10.2: No Data Retention or Archival Strategy
**Severity: LOW** | **Impact: JSON files grow unbounded**

Closed trades accumulate indefinitely in JSON files with no archival, rotation, or cleanup mechanism.

### Finding 10.3: Per-User File Storage
**Severity: MEDIUM** | **Impact: Filesystem pollution with many users**

The paper-trades API creates a separate JSON file per user (`paper-trades-{userId}.json`). With many concurrent users, this creates many small files.

- `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` line 40

---

## Summary Table

| # | Finding | Area | Severity | Est. Impact |
|---|---------|------|----------|-------------|
| 1.1 | Redundant yfinance downloads across ML | Network I/O | HIGH | 3-10s/ticker |
| 1.2 | Cache bypass in event risk features | Network I/O | MEDIUM | 1-3s/ticker |
| 1.3 | Polygon fetches ALL options then filters | Network I/O | HIGH | 5-30s/scan |
| 1.4 | Tradier N+1 query pattern | Network I/O | MEDIUM | 0.5s/expiry |
| 1.5 | Sequential cache pre-warming | Network I/O | MEDIUM | 3-6s startup |
| 1.6 | Backtester bypasses DataCache | Network I/O | LOW | Variable |
| 2.1 | iterrows() on options DataFrame | Data Processing | HIGH | 10-100x slower |
| 2.2 | Unnecessary DataFrame.copy() per cache hit | Data Processing | MEDIUM | 0.5-5ms/call |
| 2.3 | Python for-loop for synthetic data | Data Processing | LOW | 1-2s startup |
| 2.4 | DataFrame copy in _analyze_trend | Data Processing | LOW | <1ms |
| 3.1 | Multiple independent cache systems | Memory | MEDIUM | 2-5x memory |
| 3.2 | Unbounded rate limiter Map | Memory | LOW | Minimal |
| 3.3 | Full JSON loaded per request | Memory | LOW | Negligible |
| 4.1 | SWR polling regardless of market hours | Frontend | LOW | Extra requests |
| 4.2 | Inline stats computation on render | Frontend | LOW | <1ms |
| 4.3 | No dynamic imports for Recharts | Frontend | LOW | 50-100KB |
| 5.1 | Full JSON rewrite per trade operation | File I/O | MEDIUM | 1-10ms/write |
| 5.2 | Dual JSON file writes per save | File I/O | LOW | 2x I/O |
| 5.3 | Config YAML read per request | File I/O | LOW | <1ms |
| 6.1 | Python subprocess per scan | Subprocess | HIGH | 2-5s overhead |
| 6.2 | Python subprocess per backtest | Subprocess | MEDIUM | 2-5s overhead |
| 6.3 | No process pool or worker | Subprocess | HIGH | Cumulative waste |
| 7.1 | O(n*window) support/resistance | Algorithm | MEDIUM | 10-50ms |
| 7.2 | Linear search for trade by ID | Algorithm | LOW | O(n) |
| 7.3 | Closed trades iterated every close | Algorithm | LOW | O(n) |
| 7.4 | Sequential ML batch_analyze | Algorithm | MEDIUM | Linear scaling |
| 8.1 | get_ticker_obj() not cached | Caching | MEDIUM | Repeated fetches |
| 8.2 | Unused feature cache (dead code) | Caching | LOW | None |
| 8.3 | No HTTP cache headers | Caching | LOW | Extra requests |
| 9.1 | ML model training at startup | Startup | HIGH | 10-30s |
| 9.2 | Sequential heavy library imports | Startup | MEDIUM | 3-5s |
| 9.3 | Sequential cache pre-warming | Startup | MEDIUM | 3-6s |
| 10.1 | JSON file as primary data store | Storage | HIGH | Poor scalability |
| 10.2 | No data retention/archival | Storage | LOW | Unbounded growth |
| 10.3 | Per-user file storage | Storage | MEDIUM | FS pollution |

---

## Prioritized Optimization List

### Tier 1: High Impact, Moderate Effort
1. **Replace subprocess spawning with a persistent Python worker** (Findings 6.1, 6.2, 6.3) -- Eliminate 2-5s cold start per request by running a long-lived Python process (FastAPI/Flask sidecar or Unix socket IPC). This is the single largest user-facing latency improvement.

2. **Use Polygon API date filters instead of fetching all options** (Finding 1.3) -- Pass expiration date range parameters to the Polygon snapshot API to reduce response size by 90%+ and cut scan time from 30s to under 5s.

3. **Persist trained ML models to disk** (Finding 9.1) -- Save models via joblib after first training, load on subsequent starts. Reduces cold start from 30s+ to 3-5s. (Partially implemented in signal_model.py but not fully wired up.)

4. **Replace iterrows() with vectorized operations** (Finding 2.1) -- Use `DataFrame.apply()`, boolean indexing, or numpy vectorization in `_find_spreads`. Expected 10-100x speedup for option chain filtering.

### Tier 2: Medium Impact, Low-Medium Effort
5. **Parallelize cache pre-warming** (Finding 1.5) -- Use `concurrent.futures.ThreadPoolExecutor` in `DataCache.pre_warm()` to download all tickers simultaneously. Saves 3-6s at startup.

6. **Parallelize ML batch_analyze** (Finding 7.4) -- Use ThreadPoolExecutor to analyze multiple opportunities concurrently since each is independent.

7. **Consolidate cache systems** (Finding 3.1) -- Merge `iv_history_cache`, `earnings_cache`, and `feature_cache` into the shared `DataCache` with configurable per-key TTLs.

8. **Cache yf.Ticker objects** (Finding 8.1) -- Add an LRU cache to `get_ticker_obj()` to avoid re-creating Ticker objects.

9. **Parallelize Tradier chain fetching** (Finding 1.4) -- Fetch multiple expiration dates concurrently with `asyncio.gather` or `ThreadPoolExecutor`.

10. **Use sliding window for support/resistance** (Finding 7.1) -- Replace O(n*window) with O(n) deque-based algorithm.

### Tier 3: Low Impact, Quick Wins
11. **Add market-hours-aware polling** (Finding 4.1) -- Reduce SWR polling frequency or pause entirely outside market hours (9:30 AM - 4:00 PM ET, weekdays).

12. **Add Cache-Control headers** (Finding 8.3) -- Set appropriate `max-age` on API responses.

13. **Dynamic import for Recharts** (Finding 4.3) -- Use `next/dynamic` for chart components.

14. **Remove dead feature_cache code** (Finding 8.2) -- Clean up unused cache declarations.

15. **Maintain running averages instead of recomputing** (Finding 7.3) -- Track cumulative win/loss stats incrementally.

### Tier 4: Strategic / Long-term
16. **Migrate from JSON files to SQLite or PostgreSQL** (Findings 10.1, 10.2, 10.3) -- Provides indexing, concurrent access safety, query capability, and natural data retention. SQLite is a zero-configuration drop-in replacement; PostgreSQL for multi-user production.

17. **Lazy-load ML libraries** (Finding 9.2) -- Defer imports of numpy/pandas/sklearn/xgboost until first use, or pre-load in the persistent worker process.

---

## Overall Score

**5.5 / 10**

**Justification:** The codebase is functional and includes solid defensive patterns (atomic writes, circuit breakers, timing-safe auth, fail-closed middleware), but carries significant performance debt in three critical areas: subprocess cold-start overhead on every scan/backtest request (2-5s wasted per call), unfiltered bulk API fetches from Polygon (5-30s of unnecessary data transfer), and JSON-file storage that will not scale beyond a single-user prototype. The ML pipeline's redundant data fetches and sequential processing further compound latency. Addressing the top 4 items in the prioritized list would likely raise the score to 7-8/10.

---

# Error Handling & Resilience Review -- Attix Credit Spreads

## Detailed Findings

---

### 1. Exception Handling Patterns

**EH-01 | Bare `except` on Sentry init -- Severity: LOW**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/main.py`, lines 23-29
- The bare `except ImportError: pass` silently ignores the import failure. While acceptable for an optional dependency, it swallows all errors during `sentry_sdk.init()` since the `except ImportError` only catches the import; any error in `sentry_sdk.init(dsn=...)` would propagate. This is actually fine as structured -- the `try` block imports AND initializes, but if `import` succeeds but `init()` fails, that exception would propagate unhandled. However, in practice `init()` only raises on extreme edge cases.
- **Risk**: If Sentry init fails with a non-ImportError, it bubbles up and crashes startup.
- **Fix**: Catch `Exception` for the entire block or separate the import from the init.

**EH-02 | Broad `except Exception` throughout ML pipeline -- Severity: LOW**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/ml/ml_pipeline.py` (lines 231, 303, 358, 428), `/home/pmcerlean/projects/attix-credit-spreads/ml/feature_engine.py` (lines 127, 188, 247, 297, etc.), `/home/pmcerlean/projects/attix-credit-spreads/ml/signal_model.py` (line 230)
- Nearly every method uses `except Exception` with a fallback return. This is actually a deliberate and documented pattern: the ML pipeline is optional infrastructure that should never crash the core trading scanner. All catch blocks log with `exc_info=True` and return well-typed default values.
- **Assessment**: This is the correct pattern for an optional enhancement layer. The fallback counters (EH-03) properly track how often this occurs.

**EH-03 | Fallback counter monitoring with critical threshold -- Severity: POSITIVE**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/ml/ml_pipeline.py` (lines 232-236), `/home/pmcerlean/projects/attix-credit-spreads/ml/signal_model.py` (lines 231-235), `/home/pmcerlean/projects/attix-credit-spreads/ml/position_sizer.py` (lines 148-152)
- Each component tracks fallback counts via `Counter` and logs `CRITICAL` when a threshold (10) is exceeded. This is a strong observability pattern for detecting systemic failures.

---

### 2. Data Corruption Prevention

**EH-04 | Atomic JSON writes -- Severity: POSITIVE**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (lines 89-101), `/home/pmcerlean/projects/attix-credit-spreads/tracker/trade_tracker.py` (lines 59-72), `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` (lines 74-85)
- Both Python and TypeScript layers implement atomic writes using `tempfile.mkstemp` + `os.replace` (Python) and `rename()` (Node.js). The Python implementation also cleans up the temp file on error. This is correct and prevents partial writes on crash.

**EH-05 | In-memory mutex for concurrent file access in web API -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts`, lines 46-54
- The `withLock(userId, fn)` pattern serializes read-modify-write operations per user. This prevents race conditions in concurrent API requests modifying the same user's portfolio file.

**EH-06 | Non-atomic config write -- Severity: MEDIUM**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts`, line 113
- `await fs.writeFile(configPath, yamlStr, 'utf-8')` writes directly to `config.yaml` without atomic rename. If the process crashes mid-write, the config file can be left partially written and corrupt.
- **Fix**: Use the same temp-file + rename pattern used elsewhere.

**EH-07 | Alert file writes are non-atomic -- Severity: LOW**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/alerts/alert_generator.py`, lines 92-93, 156-157, 176-177
- All three alert output methods (`_generate_json`, `_generate_text`, `_generate_csv`) use direct `open(file, 'w')` without atomic write. Since these are output files (not state), this is lower risk but could still produce corrupt files if interrupted.

**EH-08 | TradeTracker `_load_trades` has no corruption recovery -- Severity: MEDIUM**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/tracker/trade_tracker.py`, lines 45-50
- If `tracker_trades.json` is corrupted (partial write from a previous crash), `json.load(f)` will throw `JSONDecodeError` and crash the entire system startup. There is no try-except around the load.
- **Fix**: Wrap in try-except, log warning, and fall back to empty list (same as the file-not-found case).

**EH-09 | PaperTrader `_load_trades` has no corruption recovery -- Severity: MEDIUM**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py`, lines 59-62
- Same issue as EH-08. `json.load(f)` on a corrupted file will crash initialization. Despite atomic writes, a process kill during `os.replace` on certain filesystems could theoretically leave the file corrupt.
- **Fix**: Wrap in try-except with fallback to the default trade structure.

---

### 3. External Service Resilience

**EH-10 | Circuit breaker pattern on Tradier and Polygon -- Severity: POSITIVE**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/strategy/tradier_provider.py` (line 39), `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` (line 33)
- Both providers wrap all API calls through `CircuitBreaker.call()` with 5-failure threshold and 60-second reset. The circuit breaker implementation at `/home/pmcerlean/projects/attix-credit-spreads/shared/circuit_breaker.py` is thread-safe and correctly implements closed/open/half-open states.

**EH-11 | HTTP retry with backoff on Tradier and Polygon sessions -- Severity: POSITIVE**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/strategy/tradier_provider.py` (line 37), `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py` (line 31)
- Both use `urllib3.util.retry.Retry` with 3 retries, 0.5s backoff factor, jitter, and retry on 429/500/502/503/504. This is production-quality retry configuration.

**EH-12 | Alpaca retry with exponential backoff + jitter -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/strategy/alpaca_provider.py`, lines 35-55
- The `_retry_with_backoff` decorator implements exponential backoff with jitter for both `submit_credit_spread` and `close_spread`. Max 2 retries with ~1s base delay.

**EH-13 | Timeouts on all HTTP calls -- Severity: POSITIVE**
- **Files**: Tradier (line 48: `timeout=10`), Polygon (line 43: `timeout=10`, line 108: `timeout=30` for snapshot), Alpaca (inherits from SDK), Telegram (line 89-90: `read_timeout=10, write_timeout=10`)
- All external HTTP calls have explicit timeouts, preventing indefinite hangs.

**EH-14 | Polygon pagination lacks circuit breaker on follow-up pages -- Severity: MEDIUM**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/strategy/polygon_provider.py`, lines 82-90, 112-117, 177-182
- The pagination loops (`while next_url:`) make direct `self.session.get()` calls bypassing the circuit breaker. If the first page succeeds but subsequent pages fail repeatedly, the circuit breaker never opens, and errors propagate directly.
- **Fix**: Route paginated requests through the circuit breaker, or add a maximum page count to prevent infinite pagination.

**EH-15 | No timeout on yfinance calls in DataCache -- Severity: MEDIUM**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/shared/data_cache.py`, line 36
- `yf.download(ticker, period='1y', progress=False)` has no explicit timeout. The yfinance library can hang on network issues. Since yfinance uses `requests` internally, it inherits whatever default timeout is set (often none).
- **Fix**: Set `requests` session timeout via yfinance's session parameter, or wrap in a timeout context.

**EH-16 | Frontend `apiFetch` has retry logic -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/lib/api.ts`, lines 141-173
- The `apiFetch` wrapper retries on 500/503 and network errors, with 1-second delays, up to 2 retries. This provides client-side resilience.

**EH-17 | Chat API has timeout via AbortSignal -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts`, line 114
- `signal: AbortSignal.timeout(15000)` ensures the OpenAI call times out after 15 seconds, preventing hang.

---

### 4. Graceful Degradation

**EH-18 | Provider cascading: Tradier -> Polygon -> yfinance -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/strategy/options_analyzer.py`, lines 68-76, 78-100
- `get_options_chain` tries Tradier first, falls back to Polygon, then to yfinance. Each provider failure logs the error and falls to the next. The `_get_chain_from_provider` method at lines 78-100 wraps the provider call in try-except and falls back to yfinance on both empty data and exceptions.

**EH-19 | ML pipeline graceful degradation to rules-based scoring -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/main.py`, lines 102-108, 220-248
- If the ML pipeline fails to initialize, the system continues with rules-based scoring only. If ML scoring fails mid-analysis, it catches the exception and keeps the rules-based score.

**EH-20 | Alpaca fallback to JSON-only tracking -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py`, lines 39-50, 243-246
- If Alpaca initialization fails, `self.alpaca = None` and trades are tracked in local JSON only. If an individual Alpaca order submission fails, the trade is still recorded locally with `alpaca_status: "fallback_json"`.

**EH-21 | Chat fallback to local responses when OpenAI is unavailable -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts`, lines 130-133
- After exhausting retries or if no API key is configured, `generateLocalResponse()` provides keyword-based responses so the feature continues to work without LLM access.

**EH-22 | Default predictions/analyses when ML components fail -- Severity: POSITIVE**
- **Files**: `signal_model.py` (`_get_default_prediction`), `ml_pipeline.py` (`_get_default_analysis`), `regime_detector.py` (`_get_default_regime`), `iv_analyzer.py` (`_get_default_analysis`), `position_sizer.py` (`_get_default_sizing`), `sentiment_scanner.py` (`_get_default_scan`)
- Every ML component returns well-typed, safe defaults when errors occur. These defaults use conservative values (e.g., neutral signal, 50% probability, 0 position size, "pass" recommendation).

---

### 5. Input Validation

**EH-23 | Division by zero guards -- Severity: POSITIVE**
- **Files**: `paper_trader.py` (line 318: `if current_price > 0`), `spread_strategy.py` (line 292: `if max_loss > 0`), `position_sizer.py` (lines 176-178: validates `win_prob`, `win_amount`, `loss_amount`), `feature_engine.py` (lines 168, 463, 468: `if vol_ma20 > 0`, `if rv > 0`, `if atr_pct > 0`), `indicators.py` (lines 55-58: `if iv_max > iv_min`)
- Consistent division-by-zero guards are present throughout the numeric computation pipeline.

**EH-24 | NaN/Inf sanitization in ML features -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/shared/indicators.py`, lines 70-84
- `sanitize_features()` replaces NaN with 0.0, +Inf with 1e6, -Inf with -1e6. Used before every model prediction and training step.

**EH-25 | NaN guard in P&L calculation -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/lib/pnl.ts`, line 45
- `const result = isNaN(clamped) ? 0 : Math.round(clamped * 100) / 100;` explicitly guards against NaN propagation in the unrealized P&L calculation. The function also clamps between `-maxLoss` and `maxProfit`.

**EH-26 | Zod validation on all POST API endpoints -- Severity: POSITIVE**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts` (lines 12-32), `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts` (lines 26-88)
- Paper trade creation validates ticker length, positive credit, positive spread_width, and that `spread_width > credit`. Config updates validate all fields with appropriate ranges. Both return structured error responses on validation failure.

**EH-27 | Config validation in Python backend -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/utils.py`, lines 114-150
- `validate_config()` checks required sections, non-empty tickers, DTE ordering (`min_dte < max_dte`), delta ordering, positive account size, and risk percentage bounds.

**EH-28 | Missing validation for expiration date parsing in PaperTrader -- Severity: LOW**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py`, lines 278-286
- The expiration parsing tries multiple formats and falls back to `now + 30d` on failure, which is a reasonable default. However, this fallback silently changes trade behavior (DTE calculation) without alerting the user.

**EH-29 | Position limit enforcement on paper trades -- Severity: POSITIVE**
- **Files**: `paper_trader.py` (lines 144-149: `max_positions`), `web/app/api/paper-trades/route.ts` (lines 145-147: `MAX_OPEN_POSITIONS = 10`)
- Both the backend scanner and frontend API enforce maximum position limits, preventing excessive risk accumulation.

**EH-30 | Duplicate position detection -- Severity: POSITIVE**
- **Files**: `paper_trader.py` (lines 155-161), `web/app/api/paper-trades/route.ts` (lines 150-158)
- Both layers check for duplicate positions (same ticker + strike + expiration) before opening.

---

### 6. Logging & Observability

**EH-31 | Structured JSON logging in frontend -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/lib/logger.ts`, lines 1-24
- The logger outputs structured JSON with timestamp, level, message, and metadata. Supports info/warn/error levels with appropriate `console` methods.

**EH-32 | Rotating file handler in Python backend -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/utils.py`, lines 87-92
- 10 MB max file size with 5 backup files. Color-coded console output with `colorlog`.

**EH-33 | Missing debug logging level in frontend logger -- Severity: LOW**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/lib/logger.ts`
- No `debug` level supported. The type definition only includes `'info' | 'error' | 'warn'`. This limits diagnostic capability in production.

**EH-34 | `exc_info=True` consistently used in Python error logging -- Severity: POSITIVE**
- Across all Python files, `logger.error()` calls consistently include `exc_info=True` to capture full stack traces. This is crucial for debugging production issues.

**EH-35 | Sentry integration -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/main.py`, lines 23-29
- Optional Sentry SDK integration with 10% traces sample rate. Configured via `SENTRY_DSN` environment variable.

---

### 7. Trade Safety

**EH-36 | Stop-loss and profit-target enforcement -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py`, lines 345-361
- Position evaluation checks profit target, stop loss, expiration (at 1 DTE), and management DTE threshold in priority order. Each condition has a named close reason for auditability.

**EH-37 | Alpaca close failure records sync error but does not block local close -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py`, lines 367-381
- If Alpaca close order fails, the error is logged and recorded in `trade["alpaca_sync_error"]`, but the local trade is still closed. This prevents a remote API failure from blocking position management.

**EH-38 | Ticker concentration limit -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py`, lines 164-171
- Maximum 3 positions per ticker prevents dangerous concentration in a single underlying.

**EH-39 | High event risk auto-skip -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/main.py`, lines 242-245
- Opportunities with event risk > 0.7 are automatically zeroed out, preventing entry during earnings/FOMC/CPI.

**EH-40 | Backtest P&L uses max_loss as zero-trade guard -- Severity: LOW**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/tracker/trade_tracker.py`, line 148
- `return_pct` divides by `position.get('max_loss', 1) * 100`. The default of `1` prevents division by zero but could produce a misleading return percentage if `max_loss` is genuinely absent.

---

### 8. Frontend Error Handling

**EH-41 | Error boundaries (route and global) -- Severity: POSITIVE**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/error.tsx`, `/home/pmcerlean/projects/attix-credit-spreads/web/app/global-error.tsx`
- Both route-level and global error boundaries are implemented with reset functionality. Error messages are logged as structured JSON. The global error boundary wraps its own `<html>` root for catastrophic failures.

**EH-42 | SWR hooks with refresh intervals but no error render -- Severity: MEDIUM**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/lib/hooks.ts`, lines 17-39
- The `useAlerts()`, `usePositions()`, and `usePaperTrades()` hooks return SWR's `error` property, but in consumers like `/home/pmcerlean/projects/attix-credit-spreads/web/app/page.tsx`, the `error` state is not destructured or rendered. If the API fails, the page shows the loading spinner briefly, then renders with empty data -- no user-facing error message.
- **Fix**: Destructure `error` from the SWR hooks and display an error state with retry button.

**EH-43 | Positions page fetch lacks HTTP status check -- Severity: MEDIUM**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/positions/page.tsx`, lines 14-27
- The fetch call at line 16 calls `res.json()` without checking `res.ok`. If the server returns a 500 error, `res.json()` will still parse the error response body and `setTrades` will receive it. This could set trades to an error object rather than an array.
- **Fix**: Add `if (!res.ok) throw new Error(...)` before parsing.

**EH-44 | Empty state handling -- Severity: POSITIVE**
- **Files**: `page.tsx` (lines 124-131), `positions/page.tsx` (lines 97-100)
- Both the main page and positions page render appropriate empty state messages when no data is available.

**EH-45 | Rate limiting on scan and backtest endpoints -- Severity: POSITIVE**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` (lines 10-23), `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/run/route.ts` (lines 11-25)
- In-flight deduplication (`scanInProgress`) and per-hour rate limits (5 scans/hr, 3 backtests/hr) prevent abuse.

**EH-46 | Rate limit memory leak potential in chat endpoint -- Severity: LOW**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts`, lines 28-31
- The rate limit map caps at 500 entries with cleanup of expired entries, preventing unbounded growth. This is a good safeguard.

---

### 9. Resource Cleanup

**EH-47 | `finally` block for scan/backtest in-progress flags -- Severity: POSITIVE**
- **Files**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` (lines 49-51), `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/run/route.ts` (lines 63-65)
- Both use `finally` blocks to reset the `inProgress` flag, ensuring it is cleared even on error.

**EH-48 | Subprocess timeout on Python invocation -- Severity: POSITIVE**
- **Files**: `scan/route.ts` (line 37: `timeout: 120000`), `backtest/run/route.ts` (line 38: `timeout: 300000`)
- Python subprocesses have explicit timeouts (2 min for scan, 5 min for backtest), preventing zombie processes.

**EH-49 | ThreadPoolExecutor with context manager -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/main.py`, line 120
- `with ThreadPoolExecutor(max_workers=4) as executor:` ensures threads are joined and cleaned up on completion or error.

**EH-50 | Requests session reuse but no explicit close -- Severity: LOW**
- **Files**: `tradier_provider.py` (line 35), `polygon_provider.py` (line 30)
- Both providers create `requests.Session()` instances that persist for the lifetime of the provider object. These sessions keep connection pools alive. While Python's garbage collector will eventually clean up, there is no explicit `session.close()` or context manager usage.

**EH-51 | File handles properly scoped with `with` statements -- Severity: POSITIVE**
- All file operations in the codebase use `with open(...)` context managers, ensuring file handles are always closed even on exception.

---

### 10. Recovery

**EH-52 | Health check endpoint -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/health/route.ts`
- Returns 200 (ok) or 503 (degraded) based on config file accessibility. Suitable for container orchestration health checks.

**EH-53 | Signal handlers for graceful shutdown -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/main.py`, lines 399-408
- SIGTERM and SIGINT handlers log the signal and exit cleanly.

**EH-54 | Data cache pre-warm errors are non-fatal -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/shared/data_cache.py`, lines 46-57
- `pre_warm()` catches exceptions per-ticker and logs warnings, so a single failed ticker does not prevent the rest from warming.

**EH-55 | No backup/recovery for corrupted JSON state files -- Severity: MEDIUM**
- While atomic writes (EH-04) prevent most corruption, there is no backup mechanism. If `paper_trades.json` or `tracker_trades.json` becomes corrupt (e.g., filesystem error, disk full), the system has no recovery path other than manual intervention.
- **Fix**: Keep the last N good copies (e.g., `paper_trades.json.bak`) and attempt to load from backup on `JSONDecodeError`.

**EH-56 | Middleware fails closed when API_AUTH_TOKEN not set -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/middleware.ts`, lines 26-28
- When no auth token is configured, the middleware returns 503 rather than allowing unauthenticated access. This is the correct fail-closed approach for a trading system.

**EH-57 | User file path sanitization -- Severity: POSITIVE**
- **File**: `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts`, lines 60-63
- `userId.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 64)` prevents path traversal attacks in user-specific file storage.

---

## Summary

### Positive Findings

The codebase demonstrates strong error handling maturity in several areas:

1. **Atomic writes** are implemented consistently in both Python (paper_trader, trade_tracker) and Node.js (paper-trades API).
2. **Circuit breakers** protect all external API providers (Tradier, Polygon).
3. **Retry with backoff** is implemented at three levels: HTTP session (urllib3 Retry), application (Alpaca decorator), and frontend (apiFetch wrapper).
4. **Provider cascading** (Tradier -> Polygon -> yfinance) with automatic fallback ensures data availability.
5. **ML pipeline graceful degradation** with fallback counters, default values, and critical-level alerts is production-quality.
6. **Zod input validation** on frontend API endpoints catches malformed requests early.
7. **NaN/Inf sanitization** is centralized in `shared/indicators.py` and used consistently.
8. **Error boundaries** exist at both route and global levels in the Next.js frontend.
9. **Rate limiting** protects expensive operations (scan, backtest, chat).
10. **Security**: Timing-safe token comparison, fail-closed middleware, path traversal protection, secret stripping in config GET.
11. **Thread safety**: Lock-guarded data cache, in-memory mutex per user for file operations.
12. **Custom exception hierarchy** in `shared/exceptions.py` enables specific error handling.

### Severity Table

| ID | Severity | Component | Summary |
|--------|----------|-----------|---------|
| EH-06 | MEDIUM | web/api/config | Non-atomic config.yaml write |
| EH-08 | MEDIUM | tracker/trade_tracker | No corruption recovery on JSON load |
| EH-09 | MEDIUM | paper_trader | No corruption recovery on JSON load |
| EH-14 | MEDIUM | polygon_provider | Pagination bypasses circuit breaker |
| EH-15 | MEDIUM | shared/data_cache | No timeout on yfinance downloads |
| EH-42 | MEDIUM | web/lib/hooks | SWR error states not rendered to user |
| EH-43 | MEDIUM | web/positions/page | HTTP status not checked before parse |
| EH-55 | MEDIUM | system-wide | No backup/recovery for JSON state files |
| EH-01 | LOW | main.py | Sentry init error path edge case |
| EH-07 | LOW | alerts/alert_generator | Non-atomic alert file writes |
| EH-28 | LOW | paper_trader | Silent fallback on bad expiration |
| EH-33 | LOW | web/lib/logger | No debug log level |
| EH-40 | LOW | tracker | max_loss fallback of 1 may mislead |
| EH-46 | LOW | web/api/chat | Rate limit map has cap (acceptable) |
| EH-50 | LOW | providers | Session not explicitly closed |

### Prioritized Remediation Order

1. **EH-08 / EH-09**: Add try-except around JSON loads in `_load_trades` for both `paper_trader.py` and `trade_tracker.py`. Quick fix, prevents startup crash.
2. **EH-42 / EH-43**: Add error states to SWR consumers and HTTP status checks in positions page. Quick frontend fix, improves user experience.
3. **EH-06**: Make `config.yaml` writes atomic via temp-file + rename.
4. **EH-15**: Add timeout wrapper for yfinance calls.
5. **EH-14**: Route paginated Polygon requests through circuit breaker, add max-page limit.
6. **EH-55**: Implement JSON backup/recovery pattern for state files.

---

## Overall Score: **7.5 / 10**

**Justification**: The codebase demonstrates above-average error handling with production-quality patterns (circuit breakers, atomic writes, provider cascading, ML fallback chains, structured logging, input validation) but has several medium-severity gaps in JSON corruption recovery, frontend error state rendering, and a few bypassed safety mechanisms that should be addressed before handling real capital.

---

# Testing & Test Coverage Review: Attix Credit Spreads

## Executive Summary

This codebase has an unusually comprehensive test suite for an early-stage trading system. There are **19 Python test files** covering backend modules and **22 frontend test files** covering the Next.js layer. The test infrastructure includes property-based testing (Hypothesis), contract tests with frozen API fixtures, API route integration tests, and component tests. However, several significant gaps exist in critical trading paths, and a few test antipatterns undermine confidence.

---

## 1. Test Coverage Assessment

### Python (Backend)

**Configuration** (`/home/pmcerlean/projects/attix-credit-spreads/pytest.ini`, line 6):
- `--cov-fail-under=60` threshold is set, meaning CI enforces 60% minimum.
- Coverage scopes: `strategy`, `ml`, `alerts`, `shared`, `backtest`, `tracker`, `paper_trader`.

**Notable coverage omissions** (`/home/pmcerlean/projects/attix-credit-spreads/.coveragerc`, lines 8-12):
- `strategy/tradier_provider.py` -- explicitly excluded from coverage
- `strategy/polygon_provider.py` -- explicitly excluded from coverage
- `strategy/alpaca_provider.py` -- explicitly excluded from coverage
- `ml/iv_analyzer.py` -- explicitly excluded from coverage
- `ml/ml_pipeline.py` -- explicitly excluded from coverage
- `ml/sentiment_scanner.py` -- explicitly excluded from coverage

**Severity: HIGH** -- These 6 files are excluded from coverage measurement entirely. The `ml_pipeline.py` is the main ML orchestrator (494 lines) and `iv_analyzer.py` is the IV surface analyzer (413 lines). Both contain critical trading logic with zero test coverage and no coverage enforcement. The `alpaca_provider.py` handles live broker integration.

### Frontend (Vitest)

**Configuration** (`/home/pmcerlean/projects/attix-credit-spreads/web/vitest.config.ts`, line 19):
- Thresholds: lines 50%, functions 50%, branches 40%, statements 50%.
- These thresholds are appropriate for the current development stage.

**Estimated coverage by module**:
- `lib/pnl.ts` -- well tested (pnl-calc.test.ts has 7 tests)
- `lib/paper-trades.ts` -- well tested (paper-trades-lib.test.ts + paper-trades.test.ts = ~28 tests)
- `lib/utils.ts` -- tested (utils.test.ts has 12 tests)
- `lib/logger.ts` -- tested (logger.test.ts has 4 tests)
- `middleware.ts` -- tested (middleware.test.ts has 5 tests)
- `lib/hooks.ts` -- **ZERO tests**
- `lib/api.ts` -- only type-checking tests (api-helpers.test.ts), no actual fetch/logic tests
- `lib/user-id.ts` -- **ZERO tests**
- `lib/mockData.ts` -- **ZERO tests**
- All page components (`app/*.tsx`) -- **ZERO tests** (except `error.tsx`)
- All feature components (`components/alerts/*.tsx`, `components/sidebar/*.tsx`, etc.) -- **ZERO tests**
- API routes `scan/route.ts`, `trades/route.ts`, `backtest/run/route.ts`, `paper-trades/route.ts` -- **ZERO integration tests**

---

## 2. Test Quality Assessment

### Strong Tests

**`/home/pmcerlean/projects/attix-credit-spreads/tests/test_paper_trader.py`** (lines 220-360) -- **HIGH quality**. Tests `_evaluate_position` for both bull put and bear call spreads, covering OTM, ITM, profit target, stop loss, expiration, management DTE, boundary cases, and division-by-zero edge cases. Tests actually invoke the real PaperTrader code via `_evaluate_position`.

**`/home/pmcerlean/projects/attix-credit-spreads/tests/test_backtester.py`** (lines 69-284) -- **HIGH quality**. Tests `_estimate_spread_value`, `_close_position`, and `_calculate_results` with meaningful assertions including P&L calculations, Sharpe ratio, max drawdown, and edge cases like all-losers and zero-losers scenarios.

**`/home/pmcerlean/projects/attix-credit-spreads/tests/test_property_based.py`** -- **HIGH quality**. Genuine Hypothesis-driven property-based tests validating mathematical invariants: POP is always in [0,100], Kelly criterion is non-negative and at most 1, IV rank/percentile are bounded, sanitize_features removes NaN/Inf, and PnL is bounded by spread width. These test actual production code, not reimplementations.

**`/home/pmcerlean/projects/attix-credit-spreads/tests/test_signal_model.py`** -- **GOOD quality**. Tests the full train/predict/save/load lifecycle using real XGBoost models on synthetic data.

### Moderate Tests

**`/home/pmcerlean/projects/attix-credit-spreads/tests/test_spread_strategy_full.py`** -- Tests DTE filtering, spread finding, condition checking, and evaluation. Reasonable but could test more edge cases around delta filtering boundaries.

**`/home/pmcerlean/projects/attix-credit-spreads/web/tests/integration/*.test.ts`** -- Good API route integration tests that call actual route handlers. The health, alerts, config, positions, chat, and backtest routes all have tests. However, the `scan/route.ts`, `trades/route.ts`, `backtest/run/route.ts`, and `paper-trades/route.ts` have no integration tests.

### Weak/Superficial Tests

**`/home/pmcerlean/projects/attix-credit-spreads/web/tests/health.test.ts`** -- **LOW quality**. Simply checks that the file exists and contains certain strings. This is a "phantom test" that will pass even if the health endpoint is broken. The real test exists in `integration/health.test.ts`.
**Severity: LOW** (redundant, not harmful)

**`/home/pmcerlean/projects/attix-credit-spreads/web/tests/error-boundary.test.ts`** -- **LOW quality**. Also just checks file existence and string content. The real rendering test exists in `error-boundary.test.tsx`.
**Severity: LOW** (redundant)

**`/home/pmcerlean/projects/attix-credit-spreads/web/tests/dockerfile.test.ts`** -- Tests that Dockerfile exists and contains certain strings. While this catches accidental deletion of deployment files, it is a very superficial test.
**Severity: LOW**

---

## 3. Test Antipatterns

### CRITICAL: Missing Fixture Directory for Contract Tests
**File**: `/home/pmcerlean/projects/attix-credit-spreads/tests/test_contracts.py`, line 17
```python
FIXTURES = Path(__file__).parent / "fixtures"
```
The `tests/fixtures/` directory does not exist. This means:
- `test_contracts.py` will fail at import time when `_load_fixture()` is called
- All 8 contract test classes (TestYFinanceFixture, TestTradierFixture, TestTelegramFixture, TestCrossFixtureConsistency) are non-functional
- The CI pipeline will either skip these or fail

**Severity: CRITICAL** -- Contract tests are supposed to validate that API response schemas match what the code expects. They are completely non-functional.

### HIGH: Tests Testing Local Reimplementations
**File**: `/home/pmcerlean/projects/attix-credit-spreads/web/tests/rate-limit.test.ts`, lines 4-27
```typescript
function createRateLimiter(maxRequests: number, windowMs: number) {
  // ... entire rate limiter implemented locally in test file
}
```
This test file implements its own rate limiter from scratch and tests that. It does not import any rate limiting code from the actual application. The actual rate limiting in `scan/route.ts` (lines 10-24) and `backtest/run/route.ts` (lines 12-25) uses a completely different implementation (array-based timestamp tracking in module scope). This test provides zero confidence that the actual rate limiting works.
**Severity: HIGH**

**File**: `/home/pmcerlean/projects/attix-credit-spreads/web/tests/paper-trades.test.ts`, lines 6-37
```typescript
function validateTradeInput(alert: any, contracts: number) {
  // ... local reimplementation of validation
}
function buildTrade(alert: any, contracts: number) {
  // ... local reimplementation of trade building
}
```
These test local reimplementations of the validation and trade-building logic rather than importing from the actual API route (`paper-trades/route.ts`). The actual route uses Zod schemas (`AlertSchema`, `PostTradeSchema`) which are not tested at all here.
**Severity: HIGH**

**File**: `/home/pmcerlean/projects/attix-credit-spreads/web/tests/config-validation.test.ts`, lines 5-32
The `ConfigSchema` is recreated from scratch in the test file rather than being imported from the route. If someone changes the schema in the route, this test will still pass with the old schema.
**Severity: MEDIUM**

### MEDIUM: Tests That Always Pass
**File**: `/home/pmcerlean/projects/attix-credit-spreads/web/tests/api-helpers.test.ts`
This file only tests TypeScript type interfaces by constructing objects and checking their fields. These are compile-time guarantees that will always pass at runtime. There is no logic being tested.
**Severity: MEDIUM**

---

## 4. Python Testing Inventory (19 test files)

| Test File | Module Covered | Test Count (approx) | Quality |
|-----------|---------------|---------------------|---------|
| test_config.py | utils.py (load_config, validate_config) | 7 | Good |
| test_options_analyzer.py | strategy/options_analyzer.py | 8 | Good |
| test_backtester.py | backtest/backtester.py | 13 | Excellent |
| test_trade_tracker.py | tracker/trade_tracker.py | 12 | Good |
| test_feature_engine.py | ml/feature_engine.py | 11 | Good |
| test_contracts.py | Multiple (fixture-based) | 8 | **Broken** (no fixtures) |
| test_iv_rank.py | shared/indicators.py | 3 | Good |
| test_regime_detector.py | ml/regime_detector.py | 6 | Good |
| test_spread_strategy_full.py | strategy/spread_strategy.py | 7 | Good |
| test_alert_generator.py | alerts/alert_generator.py | 5 | Moderate |
| test_technical_analysis.py | shared/indicators.py (RSI) | 4 | Good |
| test_position_sizer.py | ml/position_sizer.py | 7 | Excellent |
| test_paper_trader.py | paper_trader.py | 17 | Excellent |
| test_signal_model.py | ml/signal_model.py | 8 | Good |
| test_data_cache.py | shared/data_cache.py | 5 | Good |
| test_telegram_bot.py | alerts/telegram_bot.py | 6 | Good |
| test_technical_analyzer.py | strategy/technical_analysis.py | 11 | Good |
| test_spread_scoring.py | strategy/spread_strategy.py | 5 | Good |
| test_property_based.py | Multiple (property-based) | ~15 | Excellent |

**Total Python tests: ~157**

### Python Modules With ZERO Test Coverage:
1. **`main.py`** (443 lines) -- CreditSpreadSystem, the main orchestrator. No unit tests for `scan_opportunities()`, `_analyze_ticker()`, `_generate_alerts()`, `run_backtest()`. **Severity: HIGH**
2. **`ml/ml_pipeline.py`** (499 lines) -- MLPipeline orchestrator. No tests for `analyze_trade()`, `_calculate_enhanced_score()`, `_generate_recommendation()`, `batch_analyze()`. **Severity: CRITICAL**
3. **`ml/iv_analyzer.py`** (413 lines) -- IV surface analysis. No tests for `analyze_surface()`, `_compute_skew_metrics()`, `_compute_term_structure()`. **Severity: HIGH**
4. **`ml/sentiment_scanner.py`** (533 lines) -- Event risk scanner. No tests for `scan()`, `_check_earnings()`, `_check_fomc()`, `_check_cpi()`, `should_avoid_trade()`, `adjust_position_for_events()`. **Severity: HIGH**
5. **`backtest/performance_metrics.py`** (150 lines) -- Report generation. **Severity: LOW**
6. **`tracker/pnl_dashboard.py`** (182 lines) -- Dashboard display. **Severity: LOW**
7. **`shared/circuit_breaker.py`** (93 lines) -- Thread-safe circuit breaker for API calls. **Severity: MEDIUM**
8. **`shared/exceptions.py`** -- Custom exceptions. **Severity: LOW**
9. **`strategy/alpaca_provider.py`** -- Live broker integration. **Severity: MEDIUM** (excluded from coverage)
10. **`strategy/polygon_provider.py`** -- Market data provider. **Severity: MEDIUM** (excluded from coverage)

---

## 5. Frontend Testing Inventory (22 test files)

| Test File | Type | Test Count (approx) | Quality |
|-----------|------|---------------------|---------|
| pnl-calc.test.ts | Unit (lib/pnl.ts) | 7 | Excellent |
| pnl.test.ts | Unit (lib/paper-trades.ts) | 15 | Excellent |
| paper-trades-lib.test.ts | Unit (lib/paper-trades.ts) | 13 | Excellent |
| paper-trades.test.ts | Unit (local reimpl.) | 6 | **Weak** (reimpl.) |
| positions.test.ts | Unit (lib/paper-trades.ts) | 5 | Good |
| utils.test.ts | Unit (lib/utils.ts) | 12 | Good |
| logger.test.ts | Unit (lib/logger.ts) | 4 | Good |
| middleware.test.ts | Unit (middleware.ts) | 5 | Good |
| config-validation.test.ts | Unit (local reimpl.) | 7 | **Weak** (reimpl.) |
| rate-limit.test.ts | Unit (local reimpl.) | 4 | **Weak** (reimpl.) |
| api-helpers.test.ts | Type-checking only | 3 | **Weak** (always pass) |
| health.test.ts | File existence | 1 | **Superficial** |
| dockerfile.test.ts | File existence | 4 | **Superficial** |
| error-boundary.test.ts | File existence | 2 | **Superficial** |
| error-boundary.test.tsx | Component render | 4 | Good |
| ui.test.tsx | Component render | 9 | Good |
| integration/health.test.ts | API route | 4 | Good |
| integration/chat.test.ts | API route | 9 | Good |
| integration/config.test.ts | API route | 5 | Good |
| integration/backtest.test.ts | API route | 1 | Minimal |
| integration/alerts.test.ts | API route | 5 | Good |
| integration/positions.test.ts | API route | 3 | Good |

**Total frontend tests: ~143**

### Frontend Modules With ZERO Test Coverage:
1. **`app/api/paper-trades/route.ts`** (245 lines) -- The full CRUD API for paper trading. POST validation, DELETE with PnL settlement, file locking, duplicate detection. **Severity: CRITICAL**
2. **`app/api/scan/route.ts`** -- Triggers Python scan with rate limiting. **Severity: HIGH**
3. **`app/api/backtest/run/route.ts`** -- Triggers Python backtest with rate limiting. **Severity: HIGH**
4. **`app/api/trades/route.ts`** -- Reads trade data. **Severity: MEDIUM**
5. **`lib/hooks.ts`** -- SWR hooks (useAlerts, usePositions, usePaperTrades). **Severity: MEDIUM**
6. **All page components** (page.tsx, my-trades/page.tsx, settings/page.tsx, positions/page.tsx, paper-trading/page.tsx, backtest/page.tsx) -- **ZERO tests**. **Severity: MEDIUM**
7. **All feature components** (alert-card.tsx, charts.tsx, live-positions.tsx, ai-chat.tsx, heatmap.tsx, sidebar.tsx, etc.) -- **ZERO tests**. **Severity: MEDIUM**

---

## 6. Mock Data vs Real Data

### Strengths
- Python test fixtures use **realistic synthetic data**: random walks for prices, reasonable strike spacings, proper delta ranges.
- The `conftest.py` fixture generates 100 business days of SPY-like data with realistic OHLCV structure.
- The `test_contracts.py` was designed to use frozen API fixtures (yfinance, Tradier, Telegram), though the fixtures are missing.

### Weaknesses
- **Severity: CRITICAL** -- The frozen API fixtures directory (`tests/fixtures/`) does not exist. The contract tests reference `yfinance_spy_history.json`, `tradier_chain_response.json`, and `telegram_send_message.json`, none of which are present on disk. This means contract validation is non-functional.
- The frontend PaperTrade mock objects are comprehensive and match the `PaperTrade` type interface well. Good.
- All yfinance calls in Python tests are properly mocked with `@patch('...yf.download')` or `@patch('...yf.Ticker')`.

---

## 7. Critical Untested Paths

### CRITICAL Priority

1. **ML Pipeline Orchestration** (`/home/pmcerlean/projects/attix-credit-spreads/ml/ml_pipeline.py`)
   - `analyze_trade()` (lines 120-237) -- The core function that combines regime detection, IV analysis, feature building, ML prediction, event risk, and position sizing. Zero tests.
   - `_calculate_enhanced_score()` (lines 239-305) -- The scoring algorithm that weights ML probability, regime, IV signals, event risk, and features. Zero tests.
   - `_generate_recommendation()` (lines 307-367) -- Generates strong_buy/buy/consider/pass recommendations. Zero tests.

2. **Paper Trading API** (`/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts`)
   - POST handler (lines 128-197) -- Validates input with Zod, checks position limits, detects duplicates, calculates max_profit/max_loss, persists to disk.
   - DELETE handler (lines 200-245) -- Closes trades, settles PnL using `calcUnrealizedPnL`, updates status.
   - File locking mechanism (lines 48-54) -- Promise-chain mutex for concurrent access.

3. **Contract Tests are Dead** (`/home/pmcerlean/projects/attix-credit-spreads/tests/test_contracts.py`)
   - Entire file is non-functional due to missing `tests/fixtures/` directory.

### HIGH Priority

4. **IV Surface Analysis** (`/home/pmcerlean/projects/attix-credit-spreads/ml/iv_analyzer.py`)
   - `_compute_skew_metrics()` (lines 102-182) -- Complex financial calculation involving put/call skew ratios, 25-delta approximations, moneyness calculations.
   - `_compute_term_structure()` (lines 184-244) -- Contango/backwardation classification.

5. **Event Risk Scanner** (`/home/pmcerlean/projects/attix-credit-spreads/ml/sentiment_scanner.py`)
   - `scan()` (lines 59-136) -- Checks earnings, FOMC, CPI events.
   - `adjust_position_for_events()` (lines 448-488) -- Position size adjustment based on risk score.

6. **Main System Orchestration** (`/home/pmcerlean/projects/attix-credit-spreads/main.py`)
   - `scan_opportunities()` (lines 112-172) -- The primary workflow combining scanning, alerting, paper trading, and position checking.

---

## 8. Test Infrastructure

### Strengths

**CI/CD** (`/home/pmcerlean/projects/attix-credit-spreads/.github/workflows/ci.yml`):
- Both Python and frontend tests run on every push/PR to main.
- Docker build depends on both test jobs passing.
- Deploy gate only activates after all 3 jobs succeed.
- **Severity: N/A** -- Good setup.

**Makefile** (`/home/pmcerlean/projects/attix-credit-spreads/Makefile`):
- `make test` runs both `test-python` and `test-web`.
- `make all` includes linting and testing before building.

**pytest configuration**:
- Coverage reporting with term-missing for visibility.
- Proper test paths and naming conventions.
- Good conftest.py with reusable fixtures.

**vitest configuration**:
- jsdom environment for component tests.
- v8 coverage provider.
- Proper path aliases matching Next.js `@/` imports.

### Weaknesses

**Coverage enforcement**: The Python `--cov-fail-under=60` threshold is low for a trading system. The `.coveragerc` omitting 6 significant files means the 60% threshold is artificially easier to reach. The frontend thresholds (50% lines, 40% branches) are also relatively low.
**Severity: MEDIUM**

**No coverage upload**: CI runs coverage but does not upload reports (e.g., to Codecov/Coveralls). This means coverage trends are not tracked over time.
**Severity: LOW**

---

## 9. Property-Based Testing

**File**: `/home/pmcerlean/projects/attix-credit-spreads/tests/test_property_based.py`

This file is **genuinely excellent**. It contains 5 property-based test classes:

1. **TestPopBounded** -- POP is in [0,100] for any delta in [-1,1]. Tests actual `CreditSpreadStrategy._calculate_pop()`.
2. **TestKellyNonNegative** -- Kelly fraction is in [0,1] for any valid inputs. Tests actual `PositionSizer._calculate_kelly()`.
3. **TestIVRankBounded** -- IV percentile is always in [0,100]. Tests actual `calculate_iv_rank()`.
4. **TestSanitizeFeatures** -- Output never contains NaN or Inf. Tests actual `sanitize_features()`.
5. **TestEvaluatePositionBounded** -- PnL is bounded by spread width for both bull put and bear call. Tests actual `PaperTrader._evaluate_position()` with 200 examples each.

**Notable**: These tests use `importlib.util.spec_from_file_location` to load modules without pulling in heavy dependencies like xgboost. This is a smart engineering choice.

**Missing property tests that would add value**:
- Kelly criterion monotonicity (higher win_prob -> higher Kelly)
- Spread scoring idempotency
- IV analyzer signal consistency (if IV rank > 70, signal should include favorable)
- PnL calculation consistency between Python `_evaluate_position` and TypeScript `calcUnrealizedPnL`

**Severity: LOW** (what exists is good; additional properties would strengthen it)

---

## 10. Prioritized Recommendations: Tests to Add First

### Priority 1 (CRITICAL -- add immediately)

1. **Create `tests/fixtures/` directory with frozen API response files**: `yfinance_spy_history.json`, `tradier_chain_response.json`, `telegram_send_message.json`. Without these, the contract tests in `test_contracts.py` are dead code.

2. **Add `tests/test_ml_pipeline.py`**: Test `MLPipeline.analyze_trade()` and `_calculate_enhanced_score()`. These are the core ML-enhanced trading decisions. Mock the sub-components (regime_detector, iv_analyzer, signal_model, etc.) and verify the orchestration logic produces correct scores and recommendations.

3. **Add `web/tests/integration/paper-trades.test.ts`**: Test the POST/GET/DELETE handlers of `paper-trades/route.ts`. This is the user-facing paper trading API handling real money tracking (even if simulated). Test validation, duplicate prevention, position limits, and PnL settlement.

### Priority 2 (HIGH -- add within next sprint)

4. **Add `tests/test_iv_analyzer.py`**: Test `IVAnalyzer.analyze_surface()`, `_compute_skew_metrics()`, and `_compute_term_structure()`. Test with realistic options chain data covering steep put skew, contango, and backwardation scenarios.

5. **Add `tests/test_sentiment_scanner.py`**: Test `SentimentScanner.scan()`, `_check_fomc()`, `_check_cpi()`, `adjust_position_for_events()`. These directly affect trade sizing and risk management.

6. **Fix reimplementation antipatterns**: Refactor `web/tests/rate-limit.test.ts` to import and test the actual rate limiting code from `scan/route.ts`. Refactor `web/tests/config-validation.test.ts` to import the actual `ConfigSchema` from the config route.

7. **Add `tests/test_circuit_breaker.py`**: Test the thread-safe circuit breaker states (closed->open->half_open->closed), failure counting, and timeout-based reset.

### Priority 3 (MEDIUM -- add within next month)

8. **Add `tests/test_main_system.py`**: Test `CreditSpreadSystem.scan_opportunities()` and `_analyze_ticker()` with fully mocked sub-components. Verify the orchestration logic including ML score blending and event risk filtering.

9. **Add component tests for critical UI**: `alert-card.tsx`, `live-positions.tsx`, and page components that render trading data.

10. **Add cross-language PnL consistency test**: Verify that the Python `PaperTrader._evaluate_position()` and TypeScript `calcUnrealizedPnL()` produce equivalent results for the same inputs. This is critical since both calculate P&L independently.

11. **Raise coverage thresholds**: Increase Python `--cov-fail-under` from 60 to 70, and remove `.coveragerc` exclusions for `ml/ml_pipeline.py`, `ml/iv_analyzer.py`, and `ml/sentiment_scanner.py` once tests are added.

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Python test files | 19 |
| Python tests (approx) | ~157 |
| Frontend test files | 22 |
| Frontend tests (approx) | ~143 |
| Total tests | ~300 |
| Python source files (non-init, non-test) | ~22 |
| Python files with zero tests | 10 (~45%) |
| Frontend source files (non-config) | ~38 |
| Frontend files with zero tests | ~28 (~74%) |
| Property-based test classes | 5 (excellent) |
| Contract test classes | 4 (all broken) |
| Integration test files | 6 frontend |
| E2E tests | 0 |
| Tests with reimplementation antipattern | 3 |
| CI/CD enforced | Yes (GitHub Actions) |
| Coverage reporting | Yes (term-missing) |
| Coverage threshold (Python) | 60% |
| Coverage threshold (Frontend) | 50% lines |

---

## Overall Score: **6/10**

**Justification**: The test suite demonstrates genuine engineering effort with excellent property-based tests, meaningful unit tests for core trading logic (backtester, paper trader, position sizer), and functioning CI/CD enforcement -- but it is significantly undermined by completely non-functional contract tests due to missing fixtures, zero coverage of the ML pipeline orchestrator and IV analyzer (which are explicitly excluded from coverage metrics), reimplementation antipatterns in several frontend tests, and no integration tests for the paper-trades API which is the primary user-facing trading interface.

---

# Production Readiness & DevOps Review: Attix Credit Spreads

Based on my thorough examination of every file in the codebase, here are the findings organized by the 10 review areas requested.

---

## 1. Deployment Configuration

### CRITICAL: `typescript: { ignoreBuildErrors: true }` masks production type errors
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/next.config.js`, lines 27-28
- **Impact:** Type errors are silently swallowed during `next build`. Production code may contain type mismatches, null reference paths, or incorrect API contracts that TypeScript would normally catch at build time. For a trading system handling real financial data, this is unacceptable.

### HIGH: Two conflicting Dockerfiles with different Node.js versions
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/Dockerfile` (uses `node:20-slim` at build stage)
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/Dockerfile` (uses `node:18-alpine`)
- **Impact:** Different Node.js major versions may produce different build artifacts. The web Dockerfile also deletes `package-lock.json` before building (`rm -f package-lock.json`), producing non-reproducible builds.

### MEDIUM: `docker-entrypoint.sh` lacks error handling
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/docker-entrypoint.sh`
- **Impact:** No `set -e` or `set -o pipefail`. If a prerequisite command fails, execution continues silently. No startup health validation before accepting traffic.

### LOW: Railway healthcheck timeout may be too aggressive
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/railway.toml`
- **Impact:** 10-second healthcheck timeout with only 3 restart retries. Cold starts (Python import + model load) may exceed this window.

---

## 2. Environment & Secrets Management

### HIGH: `NEXT_PUBLIC_API_AUTH_TOKEN` exposed to browser bundle
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/.env.example`, line 4
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/lib/api.ts`, lines 4-5 (uses `process.env.NEXT_PUBLIC_API_AUTH_TOKEN`)
- **Impact:** The `NEXT_PUBLIC_` prefix means this token is embedded in the client-side JavaScript bundle, visible to anyone inspecting the page source. The comment says "intentional for single-user deployments" but this is a Bearer token protecting all API routes. Anyone who loads the page can extract the token and call APIs directly.

### MEDIUM: `config.yaml` committed to repository with environment variable placeholders
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/config.yaml`
- **Impact:** While secrets use `${ENV_VAR}` placeholders resolved at runtime (via `_resolve_env_vars()` in `/home/pmcerlean/projects/attix-credit-spreads/utils.py`, line 28), the config file structure and all non-secret parameters are in version control. A developer could accidentally commit actual secret values. There is no `.gitignore` entry preventing a `config.local.yaml` override pattern.

### MEDIUM: No validation that required environment variables are set at startup
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/utils.py`, `_resolve_env_vars()` function (lines 28-38)
- **Impact:** Environment variable substitution silently returns an empty string if the variable is not set (`os.environ.get(var_name, '')`). This means missing API keys produce empty strings rather than clear startup failures.

---

## 3. Monitoring & Observability

### HIGH: Health check is superficial
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/health/route.ts`
- **Impact:** Only checks if `config.yaml` is readable. Does not verify: Python backend availability, external API connectivity (Tradier, Polygon, Alpaca), disk space for JSON file storage, memory usage, or whether the ML model is loaded. A "healthy" response does not mean the system can actually process trades.

### MEDIUM: No centralized log aggregation for web tier
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/lib/logger.ts`
- **Impact:** Logs go to `console.log` as JSON. On Railway, these are captured but there is no structured log forwarding to Datadog, Loki, or similar. Python backend has rotating file logs (`utils.py`, line 60: `RotatingFileHandler` with 10MB/5 backups) but these are on ephemeral disk.

### MEDIUM: Sentry integration is optional and partially configured
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/main.py`, lines 16-23
- **Impact:** Sentry is initialized only if `SENTRY_DSN` is set. No Sentry integration exists on the web/Next.js side. Error tracking is therefore Python-only and opt-in.

### LOW: No performance metrics or APM
- **Impact:** No request duration tracking, no P99 latency monitoring, no throughput metrics. For a trading system where latency matters (market data freshness, order execution speed), this is a significant observability gap.

---

## 4. Data Persistence

### CRITICAL: All trade data stored as JSON files on ephemeral filesystem
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts`, line 12 (`DATA_DIR = path.join(process.cwd(), 'data')`)
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/paper_trader.py` (stores to `data/paper_trades.json` and `data/trades.json`)
- **Impact:** Railway uses ephemeral filesystems. **Every redeploy destroys all trade data, P&L history, backtest results, and paper trading positions.** For a financial application, this is the single most critical issue. There is no database, no external storage, no volume mount.

### HIGH: In-memory state lost on restart
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts`, line 15 (`fileLocks` Map)
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts`, lines 11-15 (rate limit map, `scanInProgress` boolean)
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/run/route.ts`, lines 10-14 (rate limit map)
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/chat/route.ts`, lines 9-13 (rate limit map)
- **Impact:** All rate limits, mutexes, and in-progress flags are in-memory. A restart resets all rate limits (allowing abuse), drops file locks (risking concurrent writes), and loses scan-in-progress state (potentially spawning duplicate Python processes).

### MEDIUM: Atomic write implementation has a race condition window
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/paper-trades/route.ts`, lines 20-35 (`writeAtomic` function)
- **Impact:** The temp-file-then-rename pattern is good, but the in-memory mutex (`fileLocks` Map using Promise chains) only works within a single Node.js process. If Railway runs multiple instances or the process crashes between temp write and rename, data can be lost.

---

## 5. Security Headers

### HIGH: CSP allows `'unsafe-inline'` and `'unsafe-eval'` for scripts
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/next.config.js`, line 20
- **Value:** `script-src 'self' 'unsafe-inline' 'unsafe-eval'`
- **Impact:** `'unsafe-eval'` enables `eval()`, `Function()`, and similar dynamic code execution, negating much of CSP's XSS protection. `'unsafe-inline'` allows inline `<script>` tags. Together, these make the CSP largely decorative for script injection attacks.

### MEDIUM: No CORS configuration
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/next.config.js`
- **Impact:** No explicit CORS headers are set. Next.js defaults to same-origin for API routes, which is reasonable, but there is no explicit Access-Control-Allow-Origin configuration. The `connect-src` in CSP allows `https://api.openai.com` but no CORS preflight handling is defined for the API routes themselves.

### LOW: Good security headers present
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/next.config.js`, lines 12-17
- **Note (positive):** X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy strict-origin-when-cross-origin, HSTS with includeSubDomains and preload, Permissions-Policy restricting camera/microphone/geolocation are all properly configured.

---

## 6. CI/CD Pipeline

### HIGH: No automated deployment step
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/.github/workflows/ci.yml`
- **Impact:** CI runs tests and a Docker build but has no deployment step. The `deploy-gate` job just checks that other jobs passed. Deployment relies entirely on Railway's auto-deploy from the main branch, meaning there is no gate between CI passing and production deployment. A failing CI would not block Railway's auto-deploy.

### MEDIUM: No security scanning in CI
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/.github/workflows/ci.yml`
- **Impact:** No `npm audit`, no `pip audit` or `safety check`, no SAST (Semgrep, CodeQL), no container image scanning (Trivy, Snyk). For a financial application handling API keys and trade execution, this is a meaningful gap.

### MEDIUM: No pre-commit hooks configured
- **Impact:** No `.pre-commit-config.yaml` file exists. No Husky or lint-staged configuration. Developers can commit code that fails linting, type checking, or formatting without any local gate.

### LOW: Coverage thresholds are modest
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/vitest.config.ts`, lines 18-19 (50% lines/functions/statements, 40% branches)
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/pytest.ini` (60% fail-under)
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/.coveragerc` (omits providers, ML pipeline, IV analyzer, sentiment scanner)
- **Impact:** Critical financial logic in providers and ML pipeline is explicitly excluded from coverage measurement.

---

## 7. Dependency Management

### HIGH: Python dependencies use `>=` specifiers, not pinned versions
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/requirements.txt`
- **Impact:** Every `pip install` may produce a different set of dependency versions. Example: `pandas>=2.2.0` could install 2.2.0 today and 3.0.0 tomorrow. No `requirements.lock` or `pip-compile` output exists. For a trading system, non-reproducible builds are dangerous.

### HIGH: Testing dependencies included in production requirements
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/requirements.txt`
- **Lines:** pytest, pytest-cov, pytest-mock, hypothesis, coverage are all in the main requirements file
- **Impact:** Production Docker image includes ~50MB+ of testing frameworks. No `requirements-dev.txt` separation exists.

### MEDIUM: `@types/` packages in `dependencies` instead of `devDependencies`
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/package.json`, lines 14-17
- **Packages:** `@types/js-yaml`, `@types/node`, `@types/react`, `@types/react-dom`
- **Impact:** Type definition packages are included in the production dependency tree. While not harmful (they are tree-shaken), it indicates sloppy dependency hygiene.

### MEDIUM: `web/Dockerfile` deletes `package-lock.json` before build
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/Dockerfile` (contains `rm -f package-lock.json`)
- **Impact:** Deliberately produces non-reproducible builds. Combined with `^` version ranges in package.json, each build may resolve different dependency versions.

### LOW: No Dependabot or Renovate configuration
- **Impact:** No automated dependency update PRs. Vulnerabilities in transitive dependencies can persist indefinitely.

---

## 8. Scaling & Reliability

### HIGH: Cannot horizontally scale
- **Impact:** The entire architecture assumes a single instance: in-memory mutexes (`fileLocks` Map), in-memory rate limits, file-based persistence, `scanInProgress` boolean flags. Running two Railway instances would cause: duplicate scans, bypassed rate limits, corrupted JSON files from concurrent writes, and split-brain trade state.

### HIGH: `execFile` calls to Python are blocking and resource-intensive
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/scan/route.ts` (spawns `python3 main.py scan`, 120s timeout)
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/backtest/run/route.ts` (spawns `python3 main.py backtest`, 300s timeout)
- **Impact:** Each scan/backtest spawns a full Python process, imports all modules (pandas, numpy, xgboost, scikit-learn), loads config, and initializes providers. On Railway's limited RAM, concurrent requests could OOM the container. The `scanInProgress` boolean only prevents duplicate scans, not concurrent backtests + scans.

### MEDIUM: No graceful shutdown handling in the web tier
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/docker-entrypoint.sh`
- **Impact:** The entrypoint uses `exec` which forwards signals to the Node.js process, but there is no SIGTERM handler in the Next.js application. In-flight requests (especially long-running scan/backtest) will be killed without draining. The Python backend in `main.py` does handle SIGTERM (lines 107-114).

### LOW: Thread pool in Python backend is fixed at 4 workers
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/main.py`, line 88
- **Impact:** `ThreadPoolExecutor(max_workers=4)` is hardcoded. On a constrained Railway instance, 4 parallel ticker analyses plus the Node.js process plus potential backtest subprocess could exhaust resources.

---

## 9. Backup & Recovery

### CRITICAL: No backup strategy for trade data
- **Impact:** All trade data, paper trading positions, P&L history, and backtest results live as JSON files on an ephemeral filesystem. There is no backup mechanism, no data export feature, no scheduled snapshots, and no external storage integration. A Railway redeploy destroys everything.

### HIGH: No disaster recovery plan
- **Impact:** If the Railway deployment fails, there is no documented recovery procedure. No data migration scripts exist. No database seed or restore capability. The `data/` directory is not in version control (correctly, as it contains runtime data), but this means there is zero recovery path.

### MEDIUM: Config changes have no audit trail
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts`
- **Impact:** The POST endpoint overwrites `config.yaml` directly with a shallow merge. No backup of the previous config is made, no change history is recorded. A bad config push could break the system with no rollback.

### MEDIUM: Config POST uses shallow merge
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/app/api/config/route.ts`, POST handler
- **Impact:** `{ ...existing, ...parsed.data }` is a shallow merge. Nested configuration objects (like `strategy.spread_strategy` or `providers.tradier`) would be entirely replaced rather than deep-merged, potentially losing keys that the user did not intend to change.

---

## 10. Documentation

### MEDIUM: No runbooks or operational documentation
- **Impact:** No documentation exists for: how to respond to alerts, how to perform a manual deployment, how to recover from data loss, how to rotate API keys, or how to investigate failed trades. For a financial application, operational runbooks are essential.

### MEDIUM: No API documentation
- **Impact:** The API routes have no OpenAPI/Swagger spec, no request/response schema documentation outside the code itself. The Zod schemas in the code serve as implicit documentation but are not exposed as a reference.

### LOW: `.env.example` files serve as partial environment variable reference
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/.env.example`
- **File:** `/home/pmcerlean/projects/attix-credit-spreads/web/.env.example`
- **Note (positive):** These files document the required environment variables with comments explaining each one. This is good practice.

---

## Summary Table

| # | Finding | Severity | Area | File(s) |
|---|---------|----------|------|---------|
| 1 | JSON file storage on ephemeral filesystem | CRITICAL | Data Persistence | `web/app/api/paper-trades/route.ts`, `paper_trader.py` |
| 2 | No backup strategy for trade data | CRITICAL | Backup & Recovery | (systemic) |
| 3 | `ignoreBuildErrors: true` in Next.js config | CRITICAL | Deployment | `web/next.config.js:27` |
| 4 | `NEXT_PUBLIC_API_AUTH_TOKEN` exposed in browser | HIGH | Secrets | `web/lib/api.ts:4`, `web/.env.example:4` |
| 5 | In-memory state (rate limits, mutexes) lost on restart | HIGH | Scaling | Multiple API route files |
| 6 | Cannot horizontally scale (single-instance architecture) | HIGH | Scaling | (systemic) |
| 7 | Health check is superficial | HIGH | Monitoring | `web/app/api/health/route.ts` |
| 8 | CSP allows `unsafe-inline` and `unsafe-eval` | HIGH | Security | `web/next.config.js:20` |
| 9 | Python deps not pinned (uses `>=`) | HIGH | Dependencies | `requirements.txt` |
| 10 | Testing deps in production requirements | HIGH | Dependencies | `requirements.txt` |
| 11 | No automated deployment gate (CI does not block Railway) | HIGH | CI/CD | `.github/workflows/ci.yml` |
| 12 | `execFile` Python subprocess per request is resource-heavy | HIGH | Scaling | `web/app/api/scan/route.ts`, `web/app/api/backtest/run/route.ts` |
| 13 | Two conflicting Dockerfiles (node:18 vs node:20) | HIGH | Deployment | `Dockerfile`, `web/Dockerfile` |
| 14 | No disaster recovery plan | HIGH | Backup & Recovery | (systemic) |
| 15 | No security scanning in CI | MEDIUM | CI/CD | `.github/workflows/ci.yml` |
| 16 | No pre-commit hooks | MEDIUM | CI/CD | (absent) |
| 17 | No CORS configuration | MEDIUM | Security | `web/next.config.js` |
| 18 | Missing env var validation at startup | MEDIUM | Secrets | `utils.py:28-38` |
| 19 | No centralized log aggregation | MEDIUM | Monitoring | `web/lib/logger.ts` |
| 20 | Sentry only on Python side, optional | MEDIUM | Monitoring | `main.py:16-23` |
| 21 | Config POST shallow merge loses nested keys | MEDIUM | Backup & Recovery | `web/app/api/config/route.ts` |
| 22 | Config changes have no audit trail | MEDIUM | Backup & Recovery | `web/app/api/config/route.ts` |
| 23 | `@types/` packages in dependencies | MEDIUM | Dependencies | `web/package.json:14-17` |
| 24 | `web/Dockerfile` deletes package-lock.json | MEDIUM | Dependencies | `web/Dockerfile` |
| 25 | No graceful shutdown in web tier | MEDIUM | Scaling | `docker-entrypoint.sh` |
| 26 | No runbooks or operational docs | MEDIUM | Documentation | (absent) |
| 27 | No API documentation / OpenAPI spec | MEDIUM | Documentation | (absent) |
| 28 | `config.yaml` in version control | MEDIUM | Secrets | `config.yaml` |
| 29 | `docker-entrypoint.sh` lacks error handling | MEDIUM | Deployment | `docker-entrypoint.sh` |
| 30 | No Dependabot/Renovate | LOW | Dependencies | (absent) |
| 31 | Coverage excludes critical financial code | LOW | CI/CD | `.coveragerc` |
| 32 | No APM / performance metrics | LOW | Monitoring | (absent) |
| 33 | Fixed thread pool size | LOW | Scaling | `main.py:88` |
| 34 | Railway healthcheck timeout may be tight | LOW | Deployment | `railway.toml` |

---

## Prioritized Remediation Plan

### P0 - Fix Immediately (blocks production use)

1. **Add a real database or external storage** (Findings #1, #2, #14)
   - Migrate from JSON files to PostgreSQL (Railway offers managed Postgres) or at minimum an S3-compatible object store
   - This is the single most impactful change: without it, every deploy destroys all financial data
   - Estimated effort: 2-3 days

2. **Remove `ignoreBuildErrors: true`** (Finding #3)
   - Set `typescript: { ignoreBuildErrors: false }` in `web/next.config.js`
   - Fix all resulting type errors
   - Estimated effort: 0.5-2 days depending on error count

3. **Resolve the auth token browser exposure** (Finding #4)
   - Either: (a) use server-side API calls only (no client-side auth token), or (b) implement proper session-based auth (e.g., NextAuth.js), or (c) at minimum document the risk clearly and add IP allowlisting
   - Estimated effort: 1-3 days

### P1 - Fix Before Scaling (high risk at current scale)

4. **Pin Python dependency versions** (Finding #9)
   - Run `pip freeze > requirements.lock` and use the lock file in Docker builds
   - Separate `requirements-dev.txt` for testing deps (Finding #10)
   - Estimated effort: 0.5 day

5. **Consolidate Dockerfiles** (Finding #13)
   - Remove `web/Dockerfile` or align Node.js versions and build practices
   - Stop deleting `package-lock.json` (Finding #24)
   - Estimated effort: 0.5 day

6. **Improve health check** (Finding #7)
   - Add checks for: Python process availability, external API connectivity, disk space, memory usage
   - Estimated effort: 0.5 day

7. **Tighten CSP headers** (Finding #8)
   - Replace `'unsafe-inline'` with nonce-based CSP using Next.js built-in support
   - Remove `'unsafe-eval'` (may require adjusting Recharts/other libs)
   - Estimated effort: 1 day

8. **Add CI deployment gate** (Finding #11)
   - Configure Railway to deploy only on CI success (use Railway's GitHub integration with required checks)
   - Estimated effort: 0.5 day

### P2 - Fix Within Next Sprint (moderate risk)

9. **Add security scanning to CI** (Finding #15)
   - Add `npm audit --audit-level=moderate`, `pip-audit`, and CodeQL or Semgrep
   - Estimated effort: 0.5 day

10. **Move in-memory state to Redis** (Findings #5, #6)
    - Railway offers managed Redis; use it for rate limits and distributed locks
    - This also enables horizontal scaling
    - Estimated effort: 1-2 days

11. **Add Sentry to web tier and make it non-optional** (Finding #20)
    - Install `@sentry/nextjs`, configure in `next.config.js` and `instrumentation.ts`
    - Estimated effort: 0.5 day

12. **Validate environment variables at startup** (Finding #18)
    - Add a startup check that fails fast if required env vars are missing
    - Estimated effort: 0.25 day

13. **Add graceful shutdown to web tier** (Finding #25)
    - Handle SIGTERM in a custom server or via Next.js middleware to drain in-flight requests
    - Estimated effort: 0.5 day

14. **Add pre-commit hooks** (Finding #16)
    - Set up `lint-staged` + Husky for linting, type checking, and formatting
    - Estimated effort: 0.25 day

15. **Fix config POST to use deep merge** (Finding #21)
    - Replace shallow spread with `lodash.merge` or equivalent
    - Add config backup before write (Finding #22)
    - Estimated effort: 0.25 day

### P3 - Improve When Possible (low risk, good hygiene)

16. **Add Dependabot or Renovate** (Finding #30) - 0.25 day
17. **Expand coverage to include financial logic** (Finding #31) - 1 day
18. **Add APM / request metrics** (Finding #32) - 1 day
19. **Write operational runbooks** (Finding #26) - 1 day
20. **Generate OpenAPI spec from Zod schemas** (Finding #27) - 0.5 day
21. **Move `@types/` to devDependencies** (Finding #23) - 0.1 day
22. **Add `set -e` to entrypoint script** (Finding #29) - 0.1 day
23. **Configure CORS explicitly** (Finding #17) - 0.25 day
24. **Make thread pool size configurable** (Finding #33) - 0.1 day
25. **Tune Railway healthcheck timeout** (Finding #34) - 0.1 day

---

## Overall Score: 3/10

**Justification:** The system has thoughtful application-level code (circuit breakers, atomic writes, retry logic, input validation) but critical infrastructure gaps -- ephemeral file storage for financial data, suppressed type checking, exposed auth tokens, no database, and inability to scale -- make it unsuitable for production use with real money without significant remediation of the P0 items.

---
