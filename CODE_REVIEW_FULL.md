# Attix Credit Spreads — 7-Panel Code Review

**Date:** 2026-02-14
**Reviewers:** 7 specialized Claude Opus 4.6 agents
**Codebase:** Full-stack credit spread trading system (Python backend + Next.js 14 frontend)
**Overall Health Score:** 4.5/10

---

## Score Summary

| Panel | Score | CRIT | HIGH | MED | LOW | Total |
|---|---|---|---|---|---|---|
| Architecture | 5/10 | 3 | 10 | 13 | 4 | 30 |
| Code Quality | 4/10 | 4 | 11 | 18 | 12 | 45 |
| Security | 3/10 | 3 | 5 | 7 | 5 | 20 |
| Performance | 4/10 | 2 | 6 | 8 | 4 | 20 |
| Error Handling | 4/10 | 4 | 6 | 9 | 6 | 25 |
| Testing | 3/10 | 3 | 4 | 4 | 1 | 12 |
| Production Readiness | 3/10 | 3 | 5 | 1 | 1 | 10 |
| **OVERALL** | **4.5/10** | **22** | **47** | **60** | **33** | **162** |

**Worst panels:** Security (3/10 — broken auth, RCE, config takeover), Testing (3/10 — 0% Python coverage, phantom tests), Production Readiness (3/10 — no Dockerfile, ephemeral data, no CI/CD).

**Most dangerous finding:** Authentication is completely broken — middleware checks for bearer tokens but no client-side code ever sends them. Combined with the config write API's `.passthrough()`, an attacker can flip paper-to-live trading and modify all strategy parameters.

---

## Table of Contents

1. [Architecture Review](#panel-1-architecture)
2. [Code Quality Review](#panel-2-code-quality)
3. [Security Audit](#panel-3-security)
4. [Performance Review](#panel-4-performance)
5. [Error Handling Review](#panel-5-error-handling)
6. [Testing & Coverage Review](#panel-6-testing)
7. [Production Readiness Review](#panel-7-production-readiness)

---

## Cross-Panel Top 10 Most Urgent Findings

| # | Severity | Finding | Panels |
|---|----------|---------|--------|
| 1 | CRITICAL | Missing `web/lib/logger.ts` — imported by 7 API routes, build fails | All 7 |
| 2 | CRITICAL | Authentication broken — middleware checks tokens but no client sends them | Security, Architecture |
| 3 | CRITICAL | RCE surface via `child_process.exec` in scan/backtest API routes | Security, Production, Code Quality |
| 4 | CRITICAL | JSON files on ephemeral filesystem — all data lost on deploy | Production, Architecture, Error Handling |
| 5 | CRITICAL | Bare `except:` clauses silently swallow errors in trading logic | Error Handling, Code Quality |
| 6 | CRITICAL | Dockerfile deleted — cannot deploy | Production |
| 7 | HIGH | Config API allows full system takeover via `.passthrough()` | Security, Architecture |
| 8 | HIGH | Redundant yfinance downloads — 5-10x per ticker per scan | Performance, Code Quality |
| 9 | HIGH | Zero Python tests for 7,700+ lines of trading/ML code | Testing |
| 10 | HIGH | Three incompatible `Alert` type definitions | Code Quality, Architecture |

---

<a id="panel-1-architecture"></a>

# Architecture Review: Attix Credit Spreads System

## Executive Summary

This is a full-stack options trading system combining a Python strategy/ML backend with a Next.js 14 dashboard frontend. The system scans ETF options chains for credit spread opportunities, scores them using rules-based and ML-enhanced analysis, executes paper trades, and presents results through a web dashboard. The architecture is functional for a prototype/early-stage product, but has several structural issues that would impede scaling, reliability, and maintainability.

---

## 1. Overall System Design

**Rating: Medium**

### Strengths
- Clean separation between Python modules: `strategy/`, `ml/`, `alerts/`, `backtest/`, `tracker/` each own a distinct concern.
- Graceful ML fallback: If the ML pipeline fails, the system falls back to rules-based scoring (`/home/pmcerlean/projects/pilotai-credit-spreads/main.py:67-75`).
- Multi-provider data architecture: Options data can come from yfinance, Tradier, or Polygon with automatic fallback (`/home/pmcerlean/projects/pilotai-credit-spreads/strategy/options_analyzer.py:33-51`).

### Issues

**[High] No inter-process communication layer between Python backend and Node.js frontend.** The only integration is through JSON files on disk (`data/trades.json`, `data/paper_trades.json`, `output/alerts.json`). The frontend reads these files via `fs.readFile`. This is fragile -- there is no atomicity guarantee, no file locking for the Python side, and path resolution depends on the working directory.
- `web/app/api/alerts/route.ts:14-19` -- reads from multiple possible paths
- `web/app/api/positions/route.ts:26-30` -- same pattern
- `paper_trader.py:81-84` -- writes without locking

**[High] Two separate paper trading engines exist with incompatible data models.** The Python `PaperTrader` (`paper_trader.py`) writes trades with `status: "open"/"closed"`, while the Next.js paper trading API (`web/app/api/paper-trades/route.ts`) uses `status: "open"/"closed_profit"/"closed_loss"/"closed_expiry"/"closed_manual"`. The positions API route (`web/app/api/positions/route.ts:47-51`) attempts backward compatibility, but the two systems operate on different files (`data/paper_trades.json` vs `data/user_trades/<userId>.json`) with different schemas.

**[Medium] Two separate `Alert` type definitions exist.** `web/lib/api.ts:1-20` defines one `Alert` interface and `web/lib/types.ts:51-82` defines another with a completely different shape (one has `legs`, `aiConfidence`, `netPremium`; the other has `credit`, `pop`, `score`). The homepage imports `Alert` from `@/lib/api` (line 11 of `page.tsx`), while mock data uses `@/types/alert`. This creates type confusion and potential runtime errors.

---

## 2. Module Coupling & Cohesion

**Rating: Medium**

### Strengths
- Python modules have clean `__init__.py` exports.
- Each strategy module (TechnicalAnalyzer, OptionsAnalyzer, CreditSpreadStrategy) has high internal cohesion.
- ML pipeline uses a clean orchestrator pattern (`ml/ml_pipeline.py`).

### Issues

**[Medium] RSI calculation duplicated in 3 places.**
- `strategy/technical_analysis.py:107-135` -- `_analyze_rsi()`
- `ml/feature_engine.py:475-482` -- `_calculate_rsi()`
- `ml/regime_detector.py:343-354` -- `_calculate_rsi()`

All three implementations compute RSI identically but live in separate classes.

**[Medium] FOMC dates duplicated across modules.** Hardcoded FOMC meeting dates appear in both:
- `ml/feature_engine.py:44-57`
- `ml/sentiment_scanner.py:38-56`

These lists are slightly different (feature_engine has fewer dates), creating a consistency risk.

**[Low] Heavy config coupling.** Every Python class takes the full config dictionary and extracts what it needs. For example, `CreditSpreadStrategy.__init__` accesses `config['strategy']` and `config['risk']` (`strategy/spread_strategy.py:28-30`). This means every component has implicit knowledge of the full config schema. A typed config dataclass would be more appropriate.

---

## 3. API Design

**Rating: Medium**

### Strengths
- API routes follow Next.js App Router conventions.
- Input validation using Zod schemas in `web/app/api/paper-trades/route.ts:9-29` and `web/app/api/config/route.ts:25-92`.
- Secrets are properly stripped from config responses (`web/app/api/config/route.ts:8-23`).
- Auth middleware provides fail-closed behavior (`web/middleware.ts:19-21`).

### Issues

**[Critical] Remote Code Execution via API.** The scan and backtest endpoints shell out to Python using `child_process.exec`:
- `web/app/api/scan/route.ts:21` -- `exec("python3 main.py scan")`
- `web/app/api/backtest/run/route.ts:22` -- `exec("python3 main.py backtest")`

While no user input is interpolated into the command string currently, this pattern is inherently dangerous. There is also no sandboxing, no resource limiting beyond a timeout, and the 120-second / 300-second timeouts are extremely long for a web request.

**[High] Config write endpoint overwrites the entire config file.** `web/app/api/config/route.ts:106-121` accepts a JSON body, converts it to YAML, and writes it directly to `../config.yaml`. This means any API caller with a valid auth token can overwrite production configuration including changing the data provider, enabling Alpaca live trading, or corrupting strategy parameters. The schema uses `.passthrough()` (line 92), allowing arbitrary extra fields.

**[High] Missing logger module.** Seven API route files import `{ logger } from "@/lib/logger"`, but `web/lib/logger.ts` does not exist in the repository. A test file (`web/tests/logger.test.ts`) tests it, implying it was deleted or never committed. This will cause build failures.

**[Medium] Inconsistent API response shapes.** The `/api/alerts` route returns `{ alerts, opportunities, timestamp, count }` while the client library (`web/lib/api.ts:163-165`) expects `AlertsResponse` with `{ timestamp, opportunities, count }`. The frontend `page.tsx:26` bridges this with `alertsData?.alerts || alertsData?.opportunities || []`, but this is fragile.

**[Medium] No pagination on any API endpoint.** The `/api/positions`, `/api/trades`, and `/api/alerts` endpoints return all data in a single response with no limit/offset support.

---

## 4. Data Flow & State Management

**Rating: Medium-Low**

### Data Flow

```
Market Data (yfinance/Polygon/Tradier)
    -> Python Strategy Engine (scan)
    -> JSON files on disk
    -> Next.js API routes (read files)
    -> SWR hooks in frontend
    -> React components
```

### Issues

**[High] File-based data store is not safe for concurrent access.** The Python `PaperTrader._save_trades()` (`paper_trader.py:80-84`) and the Next.js paper trades API both write to JSON files. There is no cross-process locking mechanism. The Next.js side has an in-memory mutex per user (`web/app/api/paper-trades/route.ts:43-51`), but this does not protect against the Python process writing simultaneously, nor does it survive server restarts.

**[Medium] yfinance called redundantly across modules.** During a single scan, yfinance is called for the same ticker in:
1. `main.py:119-123` (for current prices)
2. `strategy/options_analyzer.py:107` (for options chain)
3. `strategy/options_analyzer.py:229` (for IV rank history)
4. `ml/feature_engine.py:139` (for technical features, 6mo)
5. `ml/feature_engine.py:207` (for volatility features, 3mo)
6. `ml/feature_engine.py:271-284` (for VIX and SPY, for market features)
7. `ml/regime_detector.py:266-268` (for SPY/VIX/TLT)
8. `ml/iv_analyzer.py:313` (for IV history)
9. `ml/sentiment_scanner.py:170` (for earnings calendar)

For a single ticker scan, this results in 10+ separate HTTP calls to Yahoo Finance. Many are for the same data (SPY close price is fetched at least 4 times).

**[Medium] SWR polling intervals are aggressive.** `web/lib/hooks.ts` polls positions every 30 seconds and alerts every 60 seconds. Given that the data source is static JSON files that only update when a Python scan runs, this creates unnecessary load.

---

## 5. Configuration Management

**Rating: Medium**

### Strengths
- Environment variable substitution via `${ENV_VAR}` syntax in config.yaml is well-implemented (`utils.py:13-24`).
- Config validation at startup catches structural issues (`utils.py:114-150`).
- `.env` files are loaded via `python-dotenv` (`utils.py:38-39`).

### Issues

**[High] API keys stored in config.yaml with placeholder values.** `config.yaml:88-89` has `api_key: "${ALPACA_API_KEY}"` which is good (uses env vars), but `config.yaml:99` has `api_key: "YOUR_TRADIER_API_KEY"` which is a raw placeholder string that would be used as an actual API key if Tradier is selected as the provider.

**[Medium] Hardcoded risk-free rate.** The Black-Scholes delta estimation uses a hardcoded risk-free rate of 0.045 (`strategy/options_analyzer.py:199`). This should be configurable or dynamically fetched.

**[Medium] ML pipeline parameters not in config.yaml.** Parameters like `regime_lookback_days`, `kelly_fraction`, `max_position_size`, and `model_dir` are accessed from config (`ml/ml_pipeline.py:51-68`) but are not defined in `config.yaml`. They fall through to defaults via `.get()`. This makes it unclear which ML parameters are tunable.

**[Low] Magic numbers throughout strategy code.** Examples:
- `paper_trader.py:175` -- `contracts = min(max_contracts, 10)` -- 10-contract cap undocumented
- `paper_trader.py:339` -- `elif dte <= 21 and pnl > 0` -- 21 DTE management rule hardcoded, not from `config.strategy.manage_dte`
- `backtester.py:167` -- `short_strike = price * 0.90` -- 10% OTM approximation
- `backtester.py:172` -- `credit = self.strategy_params['spread_width'] * 0.35` -- 35% credit estimate

---

## 6. Dependency Architecture

**Rating: Medium**

### Python Dependencies (`requirements.txt`)

**[High] `ta-lib` requires system library installation.** TA-Lib is a C library that needs separate installation (`apt-get install ta-lib` or homebrew). If unavailable, the code gracefully falls back (`technical_analysis.py:10-14`), but having it in `requirements.txt:17` means `pip install` will fail on systems without the C library.

**[Medium] No `alpaca-py` in requirements.txt.** The `strategy/alpaca_provider.py` imports `from alpaca.trading.client import TradingClient` and several other alpaca SDK classes, but `alpaca-py` is not listed in `requirements.txt`. This will cause `ImportError` when Alpaca is enabled.

**[Medium] `python-dotenv` used but not in requirements.txt.** `utils.py:38` calls `from dotenv import load_dotenv` but `python-dotenv` is not listed in `requirements.txt`.

### JavaScript Dependencies (`web/package.json`)

**[Medium] React 19 with Next.js 14 compatibility.** `react: "^19.2.4"` paired with `next: "^14.2.0"` -- Next.js 14 was designed for React 18. While this may work with `legacy-peer-deps` (`.npmrc` exists), it is an unsupported configuration. The presence of `.npmrc` for `legacy-peer-deps` confirms dependency conflicts.

**[Low] `@types/*` packages in `dependencies` instead of `devDependencies`.** `@types/node`, `@types/react`, `@types/react-dom`, and `@types/js-yaml` are in `dependencies` rather than `devDependencies` (`web/package.json:14-17`).

---

## 7. Scalability Concerns

**Rating: High Severity**

**[Critical] JSON file persistence will not scale.** The entire system uses JSON files for all persistent state:
- `data/paper_trades.json` -- all trades in a single file
- `data/trades.json` -- dashboard export
- `data/user_trades/<userId>.json` -- per-user paper trades
- `output/alerts.json` -- scan results

There are no indexes, no query capability, no atomic operations, no concurrent write safety. Adding users to the paper trading system creates one JSON file per user in a single flat directory.

**[High] No rate limiting on external API calls.** The Polygon and Tradier providers make unlimited API calls. Polygon snapshot pagination (`polygon_provider.py:97-104`) can result in many sequential requests. The yfinance calls are not rate-limited at all, and multiple modules fetch the same data independently.

**[High] Single-threaded scanning.** `main.py:87-91` iterates through tickers sequentially. Each ticker involves multiple yfinance calls, options chain retrieval, technical analysis, and ML inference. With just 3 tickers (SPY, QQQ, IWM), this is manageable, but adding more would linearly increase scan time.

**[Medium] In-memory concurrency guards reset on restart.** Both `web/app/api/scan/route.ts:9` and `web/app/api/backtest/run/route.ts:11` use module-level `let` variables for concurrency guards. These reset when the server restarts, and do not protect across multiple server instances (e.g., in a scaled deployment).

**[Medium] Chat rate limiter is in-memory per process.** `web/app/api/chat/route.ts:16` uses a `Map` that grows unboundedly (with lazy cleanup at 1000 entries). This does not work across server instances and leaks memory.

---

## 8. Frontend Architecture

**Rating: Medium**

### Strengths
- Clean use of SWR for data fetching with revalidation (`web/lib/hooks.ts`).
- Good component decomposition: layout components, alert cards, sidebar widgets, positions display.
- Tailwind CSS with shadcn/ui primitives (badge, button, card, etc.) provides consistent styling.
- Error boundaries at both page and app level (`error.tsx`, `global-error.tsx`).
- Zod validation on API inputs.

### Issues

**[High] Missing `@/lib/logger` module.** As noted in Section 3, this module is imported by 7 API routes but does not exist. The build should fail, meaning either the build is not being tested or there is a path alias issue.

**[Medium] Type system is fragmented.** There are at least three places where types are defined:
1. `web/lib/types.ts` -- Canonical domain types (`PaperTrade`, `Portfolio`, `PositionsSummary`)
2. `web/lib/api.ts` -- API types (`Alert`, `Trade`, `Position`, `BacktestResult`, `Config`)
3. `web/types/alert.ts` -- Used only by mock data

The `Alert` type in `api.ts` (lines 1-20) has `ticker`, `type`, `credit`, `pop`, `score` fields. The `Alert` type in `types.ts` (lines 51-82) has `legs`, `aiConfidence`, `netPremium`, `reasoning` fields. These are fundamentally different data shapes used under the same name.

**[Medium] Mock data diverges from real data shape.** `web/lib/mockData.ts` uses the `Alert` type from `@/types/alert` with properties like `legs`, `netPremium`, `reasoning`, `aiConfidence`. The real API returns data shaped like the `Alert` from `@/lib/api.ts` with `credit`, `pop`, `score`. The mock data cannot drive the real UI correctly.

**[Low] `LivePositions` component defines its own `Position` interface.** `web/components/positions/live-positions.tsx:6-19` defines a local `Position` interface rather than using the ones from `@/lib/types.ts`. The component receives `data` as a prop but the homepage passes `positions` data (from the SWR hook) which reads `paper_trades.json` -- a different data shape than what the component expects (the component expects `open_positions` with `days_held`, `pnl_pct` which are not in the API response).

---

## 9. Integration Points

**Rating: High Severity**

**[Critical] Python-to-Node communication is file-based only.** There is no message queue, no REST API on the Python side, no WebSocket, no database as a shared store. The architecture requires both processes to run on the same filesystem. This means:
- Cannot deploy Python backend and Next.js frontend separately
- No real-time updates (frontend polls static files)
- Race conditions on file writes
- Path resolution issues across environments (the config API uses `../config.yaml`)

**[High] Scan API spawns Python subprocess.** `web/app/api/scan/route.ts:19-24` calls `python3 main.py scan` as a child process with a 120-second timeout. This:
- Requires Python to be installed on the Next.js server
- Has a 2-minute timeout for a web request (most load balancers timeout at 30s)
- Blocks a server worker for the entire duration
- Assumes relative path `../` to find the Python project

**[Medium] Alpaca integration is tightly coupled to paper trading.** The Alpaca provider is instantiated inside `PaperTrader.__init__` (`paper_trader.py:36-47`). If Alpaca is enabled, every paper trade attempts a real broker order submission. There is no clear separation between "simulate in JSON" and "execute on broker."

---

## 10. Design Patterns

**Rating: Medium**

### Patterns Used
- **Strategy Pattern**: Data providers (yfinance/Tradier/Polygon) implement a common interface via duck typing.
- **Pipeline Pattern**: `MLPipeline` orchestrates multiple ML stages sequentially.
- **Observer/Fallback**: ML pipeline initializes with try/except and falls back to rules-based scoring.
- **Builder Pattern (partial)**: `FeatureEngine.build_features()` constructs feature dictionaries incrementally.
- **Repository Pattern (rudimentary)**: `TradeTracker` acts as a repository for trades via JSON files.

### Missing Patterns

**[High] No Dependency Injection.** All classes instantiate their own dependencies internally. `OptionsAnalyzer.__init__` conditionally imports and creates `TradierProvider` or `PolygonProvider` (`options_analyzer.py:36-50`). This makes testing impossible without hitting real APIs. A factory or DI container would significantly improve testability.

**[High] No abstraction for persistence.** The system should have a `TradeRepository` interface with implementations for JSON files, SQLite, PostgreSQL, etc. Currently, file I/O logic is scattered across `PaperTrader`, `TradeTracker`, `AlertGenerator`, and all Next.js API routes.

**[Medium] No event system.** When a trade is opened or closed, multiple things need to happen (update balance, update stats, export for dashboard, potentially send alert). Currently, this is handled procedurally in `PaperTrader._close_trade()` (`paper_trader.py:344-399`). An event/observer pattern would decouple these concerns.

**[Medium] No caching layer.** Despite `config.yaml` having cache settings (`use_cache: true`, `cache_expiry_minutes: 15`), there is no caching implementation. The `IVAnalyzer._get_iv_history()` has a per-instance cache (`iv_analyzer.py:296-306`), but it is lost between process invocations. The `FeatureEngine` has `feature_cache` and `cache_timestamps` attributes (line 40-41) but never uses them.

---

## Summary of Findings by Severity

### Critical (3)
1. **Remote code execution surface** via `exec()` in scan/backtest API routes
2. **JSON file persistence** cannot support concurrent access or scaling
3. **File-based integration** between Python and Node.js is fragile and prevents independent deployment

### High (10)
1. Two incompatible paper trading engines with different schemas
2. Config write API can overwrite production configuration with minimal validation
3. Missing `@/lib/logger` module (7 files import it)
4. No rate limiting on external API calls (yfinance, Polygon, Tradier)
5. 10+ redundant yfinance HTTP calls per ticker scan
6. `alpaca-py` and `python-dotenv` missing from `requirements.txt`
7. No dependency injection -- all dependencies are hard-wired
8. No persistence abstraction
9. Alpaca live trading tightly coupled to paper trading
10. API key placeholder as literal string in config

### Medium (13)
1. Duplicate `Alert` type definitions with incompatible shapes
2. RSI calculation duplicated in 3 modules
3. FOMC dates duplicated and inconsistent across modules
4. Hardcoded risk-free rate (4.5%)
5. ML parameters not surfaced in config.yaml
6. `ta-lib` C library dependency
7. React 19 with Next.js 14 (unsupported)
8. No pagination on API endpoints
9. Aggressive SWR polling for static data
10. In-memory concurrency guards not durable
11. Mock data shape diverges from real data
12. No event system for trade lifecycle
13. Unused caching infrastructure

### Low (4)
1. Full config passed to every class
2. `@types/*` in dependencies instead of devDependencies
3. Magic numbers without documentation
4. `LivePositions` defines its own types locally

---

## Top 5 Actionable Recommendations

1. **Replace JSON file storage with SQLite or PostgreSQL.** This is the single highest-impact change. Use a shared database that both Python and Node.js can access. This solves concurrent access, enables querying, and allows independent deployment.

2. **Build a Python REST API** (Flask/FastAPI) instead of shelling out to `python3 main.py`. Expose `/scan`, `/backtest`, `/positions` endpoints. The Next.js API routes become thin proxies. This eliminates the `exec()` security risk and the 2-minute web request timeout.

3. **Unify the type system.** Merge `web/lib/types.ts` and `web/lib/api.ts` into a single source of truth. Remove `web/types/alert.ts`. Ensure mock data matches real API shapes. Create the missing `web/lib/logger.ts`.

4. **Add a data caching layer.** Create a `DataCache` class that stores yfinance/Polygon data with TTL. Pass it to all modules that need market data. This could reduce API calls per scan from 10+ to 2-3 per ticker.

5. **Fix dependency management.** Add `alpaca-py`, `python-dotenv` to `requirements.txt`. Make `ta-lib` optional (it already has a fallback). Downgrade React to 18.x to match Next.js 14 requirements, or upgrade to Next.js 15.

---

<a id="panel-2-code-quality"></a>

# Deep Code Quality Review: Attix Credit Spreads

## Executive Summary

This is a well-structured, purpose-built credit spread trading system. The architecture is clean with good separation of concerns (strategy engine, ML pipeline, alerts, tracking, web frontend). Code is generally readable with decent documentation. However, the review uncovered **4 Critical**, **11 High**, **18 Medium**, and **12 Low** severity issues across the codebase. The most impactful problems are: duplicated logic across modules (RSI, IV rank, FOMC dates), bare `except:` clauses masking errors, non-atomic JSON file writes risking data corruption, and a triplicated `Alert` type definition in the TypeScript frontend.

---

## 1. CRITICAL Issues

### CQ-01: Three Separate `Alert` Interface Definitions (TypeScript)

**Severity:** Critical
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/api.ts:1-20`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/types.ts:51-82`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/types/alert.ts:11-31`

There are **three separate `Alert` interface definitions** with divergent shapes. `web/lib/api.ts` has a flat options-data `Alert` (with `credit`, `pop`, `score` as required fields). `web/types/alert.ts` has a UI-oriented `Alert` (with `legs`, `reasoning`, `aiConfidence` as strings). `web/lib/types.ts` has yet another with a superset of optional fields from both.

```typescript
// web/lib/api.ts (flat options data)
export interface Alert {
  ticker: string; credit: number; pop: number; score: number; // ...
}

// web/types/alert.ts (UI-oriented)
export interface Alert {
  id: number; type: "Bullish" | "Bearish" | "Neutral";
  legs: TradeLeg[]; reasoning: string[]; aiConfidence: string; // ...
}

// web/lib/types.ts (superset with optionals)
export interface Alert {
  id: number; type: "Bullish" | "Bearish" | "Neutral";
  credit?: number; pop?: number; // ...
}
```

**Impact:** Import path determines which `Alert` a component receives. Refactoring or moving imports will silently break type checking. `mockData.ts` imports from `@/types/alert`, while `paper-trades/route.ts` validates against a Zod schema that matches neither interface exactly.

**Recommended Fix:** Consolidate into a single canonical `Alert` interface in `web/lib/types.ts`. Create separate interfaces like `ScannerAlert` and `UIAlert` if semantically distinct. Use a single source and re-export.

---

### CQ-02: Bare `except:` Clauses Silently Swallowing All Exceptions

**Severity:** Critical
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/main.py:123`
- `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py:259,262`
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:332`

```python
# main.py:121-124
try:
    stock = yf.Ticker(ticker)
    hist = stock.history(period='1d')
    if not hist.empty:
        current_prices[ticker] = hist['Close'].iloc[-1]
except:    # <-- catches KeyboardInterrupt, SystemExit, MemoryError, etc.
    pass

# paper_trader.py:257-263
try:
    exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
except:
    try:
        exp_date = datetime.fromisoformat(trade["expiration"])
    except:
        exp_date = now + timedelta(days=30)
```

**Impact:** These catch `SystemExit`, `KeyboardInterrupt`, and `MemoryError`, preventing graceful shutdown and masking serious runtime failures. In the paper_trader, a corrupted expiration silently defaults to 30 days ahead, which could keep a position open far past its actual expiration.

**Recommended Fix:** Replace with `except Exception as e:` at minimum. Better: catch specific expected exceptions (`ValueError`, `KeyError`, `requests.RequestException`). Log the error.

---

### CQ-03: Non-Atomic JSON File Writes Risk Data Corruption

**Severity:** Critical
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py:80-84`
- `/home/pmcerlean/projects/pilotai-credit-spreads/tracker/trade_tracker.py:57-65`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts:71-74`

```python
# paper_trader.py:80-84
def _save_trades(self):
    with open(PAPER_LOG, "w") as f:      # Truncates the file immediately
        json.dump(self.trades, f, indent=2, default=str)
    self._export_for_dashboard()          # Second non-atomic write
```

If the process crashes mid-write (or the system loses power), the JSON file will be truncated or partially written, resulting in corrupted data and total loss of trade history. The `_save_trades` method writes two files sequentially -- if the first succeeds but the second fails, the state becomes inconsistent.

**Recommended Fix:** Write to a temporary file, then `os.replace()` atomically:
```python
import tempfile
def _save_trades(self):
    with tempfile.NamedTemporaryFile('w', dir=DATA_DIR, delete=False, suffix='.tmp') as f:
        json.dump(self.trades, f, indent=2, default=str)
        tmp_path = f.name
    os.replace(tmp_path, PAPER_LOG)
```

---

### CQ-04: Command Injection via `exec` in Backtest API Route

**Severity:** Critical
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts:22`

```typescript
const { stdout, stderr } = await execPromise('python3 main.py backtest', {
    cwd: systemPath,
    timeout: 300000,
});
```

While the command itself is hardcoded (no user input in the string), executing shell commands from an API route is a serious architectural anti-pattern. The `systemPath` is constructed using `path.join(process.cwd(), '..')`, which traverses up from the web directory. If the deployment structure changes, this could point to an unexpected location. More importantly, `stdout` is returned directly in the JSON response, potentially leaking system information.

**Recommended Fix:** Use `execFile` instead of `exec` (avoids shell interpretation). Do not return `stdout` to the client. Consider running the backtest as a background job with status polling rather than a synchronous HTTP call with a 5-minute timeout.

---

## 2. HIGH Issues

### CQ-05: RSI Calculation Duplicated in Three Locations

**Severity:** High (DRY violation)
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py:120-124`
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:475-482`
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/regime_detector.py:343-354`

All three files implement an identical RSI calculation:
```python
def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
```

**Recommended Fix:** Extract into a shared `utils.py` or `indicators.py` module, e.g. `from utils import calculate_rsi`.

---

### CQ-06: IV Rank Calculation Duplicated Across Three Modules

**Severity:** High (DRY violation)
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/options_analyzer.py:212-266`
- `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/polygon_provider.py:244-272`
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/iv_analyzer.py:243-294`

The same IV rank formula `(current - min) / (max - min) * 100` is implemented three times with slightly different fallback behaviors and return shapes.

**Recommended Fix:** Create a single `calculate_iv_rank(current_iv, historical_values)` function in `utils.py`.

---

### CQ-07: FOMC Dates Hardcoded in Two Separate Lists That May Diverge

**Severity:** High (DRY violation / Data inconsistency risk)
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/sentiment_scanner.py:38-56`
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:44-57`

```python
# sentiment_scanner.py has 17 dates (2025-2026) including datetime(2026, 1, 28)
# feature_engine.py has 12 dates (2025 + partial 2026), MISSING datetime(2026, 1, 28)
```

The two lists are already inconsistent: `sentiment_scanner.py` includes `datetime(2026, 1, 28)` and the full 2026 calendar; `feature_engine.py` does not. When one gets updated, the other will likely be forgotten.

**Recommended Fix:** Define FOMC dates in a single shared constant, e.g. `KNOWN_FOMC_DATES` in a `constants.py` file. Import from both modules.

---

### CQ-08: Portfolio Stats Computation Duplicated in Three Places

**Severity:** High (DRY violation)
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/paper-trades.ts:50-78` (function `calculatePortfolioStats`)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts:94-116` (inline in GET handler)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/positions/route.ts:39-72` (inline in GET handler)

All three compute the same metrics: filter open/closed trades, count winners/losers, sum realized/unrealized PnL, calculate win rate. Any bug fix must be applied three times.

**Recommended Fix:** Use the `calculatePortfolioStats` function from `paper-trades.ts` in both API routes.

---

### CQ-09: Multiple yfinance Downloads for Same Ticker Per Feature Build

**Severity:** High (Performance)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py`

```python
# _compute_technical_features downloads ticker data (6mo, line 139)
stock = yf.download(ticker, period='6mo', progress=False)

# _compute_volatility_features downloads the SAME ticker again (3mo, line 207)
stock = yf.download(ticker, period='3mo', progress=False)

# _compute_market_features downloads VIX + SPY (lines 271, 284)
vix = yf.download('^VIX', period='5d', progress=False)
spy = yf.download('SPY', period='3mo', progress=False)
```

For a single `build_features()` call, this makes 4+ HTTP requests to Yahoo Finance, including downloading the same ticker's data twice (once for 6 months, once for 3 months). When `_analyze_ticker()` in `main.py` also calls `yf.Ticker(ticker).history()` and `options_analyzer.calculate_iv_rank()` (which downloads 1yr of data), the same ticker gets downloaded 5+ times per scan cycle.

The `feature_cache` attribute (line 40) is initialized but never used.

**Recommended Fix:** Pass price data into `build_features()` as a parameter. Cache downloads with a TTL. Use the existing `feature_cache` dict.

---

### CQ-10: `_estimate_delta` Uses Median Strike as Spot Price

**Severity:** High (Logic Error)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/options_analyzer.py:194`

```python
def _estimate_delta(self, df: pd.DataFrame) -> pd.Series:
    spot = df['strike'].median()  # Wrong! This is the median strike, not the spot price
```

The spot price should be the current underlying price, not the median of available strike prices. If the options chain is skewed (e.g., many deep OTM puts), the median strike will not approximate the spot price.

**Recommended Fix:** Pass `current_price` as a parameter: `def _estimate_delta(self, df, current_price)`.

---

### CQ-11: `_estimate_delta` Returns Absolute Values for All Options

**Severity:** High (Logic Error)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/options_analyzer.py:210`

```python
return pd.Series(np.round(np.abs(delta), 4), index=df.index)
```

Delta is returned as absolute values. But in `spread_strategy.py:191-193`, the bull put spread search filters puts by:
```python
short_candidates = puts[
    (puts['delta'] >= -target_delta_max) &
    (puts['delta'] <= -target_delta_min)
]
```

This expects **negative** deltas for puts. The absolute value transform means this filter will never match any rows when using estimated deltas, silently producing zero opportunities.

**Recommended Fix:** Return signed deltas: negative for puts, positive for calls.

---

### CQ-12: `sys.path.insert(0, ...)` Hack in Entry Points

**Severity:** High (Code Smell)
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/main.py:20`
- `/home/pmcerlean/projects/pilotai-credit-spreads/demo.py:12`

```python
sys.path.insert(0, str(Path(__file__).parent))
```

**Recommended Fix:** Create a proper `pyproject.toml` or `setup.py` with the package structure. Use `pip install -e .` for development.

---

### CQ-13: P&L Calculation Dead Code in `_evaluate_position`

**Severity:** High (Dead Code / Logic Error)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py:317-321`

```python
# ITM — losing money
current_spread_value = min(intrinsic * contracts * 100, trade["total_max_loss"])
remaining_extrinsic = credit * max(0, 1 - time_passed_pct) * 0.3
pnl = round(credit - current_spread_value + remaining_extrinsic - credit, 2)  # Dead: immediately overwritten
# Simplified: pnl = -(current_spread_value - remaining_extrinsic)
pnl = round(-(current_spread_value - remaining_extrinsic), 2)  # This is what actually runs
```

Line 319 computes a P&L value that is immediately overwritten on line 321. The comment says "simplified" but it is actually a different formula (the first includes `credit - credit` which cancels to zero, so they are algebraically equivalent). The dead assignment is confusing and should be removed.

**Recommended Fix:** Remove line 319. Keep only line 321 with a clear comment.

---

### CQ-14: `TradeLeg` Interface Defined in Two Places

**Severity:** High (DRY violation)
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/types.ts:41-49`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/types/alert.ts:1-9`

Identical `TradeLeg` interface defined in both files. `mockData.ts` imports from `@/types/alert` while `types.ts` defines its own.

**Recommended Fix:** Define once in `web/lib/types.ts`, re-export from `web/types/alert.ts` if needed for backward compatibility.

---

### CQ-15: In-Memory Rate Limiter in Chat Route Resets on Deployment

**Severity:** High (Design Flaw)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts:16`

```typescript
const rateLimitMap = new Map<string, { count: number; resetAt: number }>();
```

In a serverless / edge deployment (common with Next.js on Vercel/Railway), each instance has its own map. A user hitting different instances is never rate-limited. Conversely, on a single instance, the map grows unbounded (cleanup only triggers at 1000 entries).

**Recommended Fix:** Use a proper rate-limiting middleware (e.g., `upstash/ratelimit`) backed by Redis or KV store if serverless; document that in-memory is only suitable for single-instance deployment.

---

## 3. MEDIUM Issues

### CQ-16: Magic Numbers Throughout Python Backend

**Severity:** Medium
**Files:** Multiple

```python
# paper_trader.py:175
contracts = min(max_contracts, 10)  # Magic: why 10?

# paper_trader.py:339
elif dte <= 21 and pnl > 0:  # Magic: why 21?

# spread_strategy.py:233
'profit_target': round(credit * 0.5, 2),  # Magic: 50% hardcoded (also in config)

# backtester.py:172
credit = self.strategy_params['spread_width'] * 0.35  # Magic: 35%

# ml/ml_pipeline.py:187-188
expected_return = 0.30  # Magic: 30%
expected_loss = -1.0    # Magic: -100%
```

**Recommended Fix:** Define named constants or pull from config.

---

### CQ-17: `_check_bullish_conditions` and `_check_bearish_conditions` are Near-Duplicates

**Severity:** Medium (DRY violation)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/spread_strategy.py:96-162`

These two methods share ~80% of their logic. The only difference is which trend is checked and which RSI bound is used.

**Recommended Fix:** Extract a single `_check_directional_conditions(direction, technical_signals, iv_data)` method.

---

### CQ-18: `_find_bull_put_spreads` and `_find_bear_call_spreads` are Near-Duplicates

**Severity:** Medium (DRY violation)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/spread_strategy.py:164-324`

These two 80-line methods differ only in:
1. Filtering puts vs calls
2. Long strike = short - width vs short + width
3. `distance_to_short` calculation direction

**Recommended Fix:** Create a parameterized `_find_spreads(option_type, direction, ...)` method.

---

### CQ-19: `get_options_chain` and `get_full_chain` Have Massive Code Duplication in Polygon Provider

**Severity:** Medium (DRY violation)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/polygon_provider.py:81-223`

Lines 106-141 (inside `get_options_chain`) and lines 185-213 (inside `get_full_chain`) contain nearly identical row-building logic for parsing Polygon API response items into DataFrames.

**Recommended Fix:** Extract `_parse_option_item(item, expiration)` as a helper.

---

### CQ-20: `_analyze_trend` Mutates the Input DataFrame

**Severity:** Medium (Side Effect)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py:84-85`

```python
def _analyze_trend(self, price_data: pd.DataFrame) -> Dict:
    price_data['MA_fast'] = price_data['Close'].rolling(window=fast_period).mean()
    price_data['MA_slow'] = price_data['Close'].rolling(window=slow_period).mean()
```

Adding columns to the input DataFrame is a side effect. If `analyze()` is called multiple times with the same DataFrame, columns accumulate.

**Recommended Fix:** Work on a copy: `df = price_data.copy()` or compute without assignment.

---

### CQ-21: `recent_data` Variable Assigned but Never Used

**Severity:** Medium (Dead Code)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py:149`

```python
def _analyze_support_resistance(self, price_data: pd.DataFrame) -> Dict:
    current_price = price_data['Close'].iloc[-1]
    recent_data = price_data.tail(20)  # Never used
    support_levels = self._find_support_levels(price_data)  # Uses full data, not recent_data
```

**Recommended Fix:** Remove the unused variable or use it in the support/resistance calculation.

---

### CQ-22: Synthetic Training Data Hard-Wires Feature Logic That Should Come from the Model

**Severity:** Medium (Design Smell)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/signal_model.py:454-607`

The `generate_synthetic_training_data` method creates 2000 samples with hardcoded logic determining labels (e.g., "if IV rank > 70, add 30 to win score"). The model is then trained on this synthetic data, which means it will only ever learn the rules the developer hardcoded.

**Recommended Fix:** This should be documented clearly as a bootstrapping mechanism. Ideally, collect real trade outcomes and retrain with historical data. The synthetic data approach means the ML model adds no value beyond the rules -- it just wraps them in an XGBoost wrapper.

---

### CQ-23: `position_sizer.rebalance_positions` Uses `win_prob` Parameter Name Inconsistently

**Severity:** Medium (API inconsistency)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/position_sizer.py:337`

```python
sizing = self.calculate_position_size(
    win_prob=win_prob,          # Parameter name doesn't match
    expected_return=expected_return,
    expected_loss=expected_loss,
    ml_confidence=ml_confidence,
    ...
)
```

But `calculate_position_size` defines the parameter as `win_probability`:
```python
def calculate_position_size(self, win_probability: float, ...):
```

This will cause a `TypeError` at runtime if `rebalance_positions` is ever called. Python passes `win_prob` as a positional argument by luck since it is the first parameter.

**Recommended Fix:** Change `win_prob=win_prob` to `win_probability=win_prob`.

---

### CQ-24: `rebalance_positions` Divides by `current_size` Without Zero-Check

**Severity:** Medium (Runtime Error)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/position_sizer.py:349`

```python
if abs(recommended_size - current_size) / current_size > 0.20:  # ZeroDivisionError if current_size == 0
```

**Recommended Fix:** Guard: `if current_size > 0 and abs(recommended_size - current_size) / current_size > 0.20:`

---

### CQ-25: Missing `ticker` Field in SentimentScanner Default Scan

**Severity:** Medium (Data inconsistency)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/sentiment_scanner.py:502-514`

```python
def _get_default_scan(self) -> Dict:
    return {
        'scan_date': datetime.now().isoformat(),
        # 'ticker' is missing but present in the normal return (line 98)
        'scan_window_days': 0,
        ...
    }
```

**Recommended Fix:** Add `'ticker': ''` to maintain a consistent return shape.

---

### CQ-26: Emoji Characters in Log Statements Will Corrupt Some Log Sinks

**Severity:** Medium (Operational)
**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/ml_pipeline.py:74,106,414,462`
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/signal_model.py:165,314,392`
- `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py:429` (emoji in print, not log)

```python
logger.info("✓ ML pipeline initialized")
logger.info("✓ Model training complete")
```

**Recommended Fix:** Use `[OK]` or `[DONE]` instead of emoji in log output.

---

### CQ-27: `_compute_term_structure` Mutates Options Chain Input

**Severity:** Medium (Side Effect)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/iv_analyzer.py:194`

```python
options_chain['dte'] = (options_chain['expiration'] - now).dt.days
```

This modifies the caller's DataFrame. Other code downstream may not expect the `dte` column to exist.

**Recommended Fix:** Use a local copy: `chain = options_chain.copy()`.

---

### CQ-28: `close_spread` Uses `ratio_qty=contracts` Instead of `ratio_qty=1`

**Severity:** Medium (Potential Logic Error)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/alpaca_provider.py:261-271`

In `submit_credit_spread`, legs use `ratio_qty=1` and `qty=contracts` on the order. In `close_spread`, legs use `ratio_qty=contracts` and no `qty` on the order:

```python
# submit_credit_spread (correct)
OptionLegRequest(symbol=short_sym, ratio_qty=1, side=OrderSide.SELL, ...)
# order: qty=contracts

# close_spread (inconsistent)
OptionLegRequest(symbol=short_sym, ratio_qty=contracts, side=OrderSide.BUY, ...)
# order: no qty specified
```

**Recommended Fix:** Use `ratio_qty=1` and set `qty=contracts` on the order request, matching the open order pattern.

---

### CQ-29: Double Balance Accounting in Backtester

**Severity:** Medium (Logic Error)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/backtest/backtester.py:206,322`

```python
# When opening: credit added to capital (line 206)
self.capital += (credit * contracts * 100) - commission_cost

# When closing with profit_target: credit added again (line 309)
pnl = position['profit_target'] * position['contracts'] * 100
self.capital += pnl
```

The credit is received at entry (added to capital), and then on close the "profit target" amount (which is 50% of credit) is added again. This double-counts the credit portion for winning trades, inflating backtest returns.

**Recommended Fix:** At entry, hold the credit as part of the position collateral (do not add to capital). At close, compute PnL = credit_received - cost_to_close.

---

### CQ-30: `_consolidate_levels` Divides by `consolidated[-1]` Without Zero-Check

**Severity:** Medium
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py:227`

```python
if abs(level - consolidated[-1]) / consolidated[-1] > threshold:
```

If `consolidated[-1]` is 0 (very unlikely for stock prices but possible for derived values), this raises `ZeroDivisionError`.

---

### CQ-31: Stale Data Risk: `lookback_days` Parameter Name is Misleading in `SentimentScanner.scan()`

**Severity:** Medium (Naming)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/sentiment_scanner.py:75`

```python
def scan(self, ticker, expiration_date=None, lookback_days=7):
```

`lookback_days` actually means "look-ahead days" (line 95: `scan_end = now + timedelta(days=lookback_days)`). This is the opposite of what "lookback" conventionally means.

**Recommended Fix:** Rename to `lookahead_days` or `horizon_days`.

---

### CQ-32: `_build_occ_symbol` Has Redundant `.replace(" ", " ")`

**Severity:** Medium (Code Smell)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/alpaca_provider.py:95`

```python
return f"{ticker.upper():<6}{date_str}{cp}{strike_int:08d}".replace(" ", " ").strip()
```

`.replace(" ", " ")` replaces a space with a space -- a no-op. The intent was likely `.replace(" ", "")` (remove spaces from padding).

**Recommended Fix:** Change to `.replace(" ", "")`.

---

### CQ-33: Date Parsing Loop in `_build_occ_symbol` Tries Same Format Twice

**Severity:** Medium (Code Smell)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/alpaca_provider.py:83-89`

```python
for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
    try:
        exp_dt = datetime.strptime(expiration.split(" ")[0], "%Y-%m-%d")  # Always uses %Y-%m-%d!
        break
    except ValueError:
        continue
```

Both iterations of the loop parse with `"%Y-%m-%d"` (the `split(" ")[0]` strips the time component before parsing). The `fmt` variable from the loop is never used.

**Recommended Fix:** Remove the loop, parse once: `exp_dt = datetime.strptime(expiration.split(" ")[0], "%Y-%m-%d")`.

---

## 4. LOW Issues

### CQ-34: Missing Return Type Annotations on Several Python Functions

**Severity:** Low
**Files:** `backtest/backtester.py:_close_position`, `tracker/trade_tracker.py:update_position`, `alerts/alert_generator.py:multiple methods`

### CQ-35: `unused import os` in polygon_provider.py

**Severity:** Low
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/polygon_provider.py:8`

`os` is only used in the `__main__` block for `os.environ.get()`, not in the class itself.

### CQ-36: `Tuple` Import Unused in Several Files

**Severity:** Low
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/spread_strategy.py:8`

`Tuple` is imported but never used.

### CQ-37: No Input Length Validation on Chat Messages

**Severity:** Low (Security)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts:67`

Messages array length and individual message content length are not validated before being sent to the OpenAI API, potentially allowing abuse (large payloads).

### CQ-38: `scipy.stats` Import Unused in feature_engine.py

**Severity:** Low
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:19`

`from scipy import stats` is imported but never used in the module.

### CQ-39: `scipy.stats.norm` Import Unused in iv_analyzer.py

**Severity:** Low
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/iv_analyzer.py:17`

`from scipy.stats import norm` is imported but never used.

### CQ-40: `scipy.interpolate` Import Unused in iv_analyzer.py

**Severity:** Low
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/iv_analyzer.py:16`

`from scipy import interpolate` is imported but never used.

### CQ-41: `_compute_skew_metrics` Filters by `volume > 10` Using Potentially Missing Column

**Severity:** Low
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/iv_analyzer.py:112`

If the options chain from yfinance doesn't have a `volume` column (which is possible), this will raise a `KeyError`.

### CQ-42: Test Files Use `any` Type Extensively

**Severity:** Low (Tests only)
**Files:** `web/tests/paper-trades.test.ts:6,18`, `web/tests/middleware.test.ts:12,19,23`

While less critical in test files, using `any` reduces type safety.

### CQ-43: `console.log` Used Instead of Logger in Alerts Route

**Severity:** Low (Inconsistency)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/alerts/route.ts:32`

```typescript
console.log("Failed to read alerts:", error);
```

Other routes use the structured `logger`. This one uses `console.log`.

### CQ-44: `uuid` Import in alpaca_provider.py When Only Used for Random String

**Severity:** Low
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/alpaca_provider.py:8`

The full `uuid` module is imported but only `uuid.uuid4().hex[:8]` is used. This is fine functionally but could use `secrets.token_hex(4)` for a lighter alternative.

### CQ-45: `generate_alerts_only` Calls `scan_opportunities` Which Does Everything

**Severity:** Low (Design Smell)
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/main.py:279-289`

The docstring says "Generate alerts from recent scans without new scanning" but the implementation calls `self.scan_opportunities()` which does a full scan. The method name and docstring are misleading.

---

## 5. Summary by Category

| Category | Critical | High | Medium | Low | Total |
|---|---|---|---|---|---|
| DRY Violations | 0 | 5 | 2 | 0 | 7 |
| Code Smells / Dead Code | 0 | 2 | 3 | 5 | 10 |
| Logic Errors | 0 | 2 | 3 | 0 | 5 |
| Type Safety | 1 | 1 | 1 | 2 | 5 |
| Error Handling | 1 | 0 | 1 | 0 | 2 |
| Security / Safety | 1 | 1 | 0 | 1 | 3 |
| Data Integrity | 1 | 0 | 1 | 0 | 2 |
| Performance | 0 | 1 | 0 | 0 | 1 |
| Naming | 0 | 0 | 2 | 0 | 2 |
| Side Effects | 0 | 0 | 2 | 0 | 2 |
| Design | 0 | 0 | 2 | 2 | 4 |
| Unused Code | 0 | 0 | 1 | 4 | 5 |
| **Total** | **4** | **11** | **18** | **12** | **45** |

## 6. Top 5 Priority Fixes

| Priority | Issue | Impact | Est. Effort |
|---|---|---|---|
| 1 | **CQ-02: Replace bare `except:` with specific exceptions** | Silent failure masking, can't Ctrl-C | 30 min |
| 2 | **CQ-03: Implement atomic JSON writes** (write-to-temp + rename) | Data loss on crash | 1 hour |
| 3 | **CQ-01: Consolidate Alert type definitions** into single source of truth | Type confusion, silent breakage | 2 hours |
| 4 | **CQ-05/06/07: Extract shared utilities** (RSI, IV rank, FOMC dates) | Diverging implementations | 2 hours |
| 5 | **CQ-11: Fix `_estimate_delta` to return signed deltas** | Zero opportunities found when using yfinance | 30 min |

## 7. Positive Observations

While there are many issues, the codebase has notable strengths:

- **Good separation of concerns**: Strategy, ML, alerts, tracking, and frontend are cleanly separated into packages.
- **Graceful degradation**: ML pipeline, Alpaca integration, and data providers all have fallback paths when unavailable.
- **Comprehensive type definitions**: The TypeScript types (despite duplication) cover the domain model well.
- **Input validation**: The paper-trades API route uses Zod schemas for request validation.
- **Consistent logging**: Almost every module uses Python's logging module consistently.
- **Docstrings**: Most Python classes and methods have clear docstrings with parameter documentation.
- **Academic references**: ML modules cite relevant research papers, showing thoughtful design intent.
- **File-based concurrency**: The `withLock` mutex pattern in the paper-trades route handles concurrent file access correctly for a single-instance deployment.

---

<a id="panel-3-security"></a>

# SECURITY AUDIT REPORT: Attix Credit Spreads

**Auditor:** Senior Security Engineer
**Date:** 2026-02-13
**Scope:** Full-stack security audit -- Python backend, Next.js 14 frontend, API integrations
**Classification:** CONFIDENTIAL -- Financial Application

---

## EXECUTIVE SUMMARY

This application has several **critical** and **high**-severity security vulnerabilities that, combined, could allow an unauthenticated attacker to execute arbitrary commands on the server, read/write system configuration, tamper with financial data, and access other users' trading portfolios. The most urgent issues are the broken authentication architecture and the command injection surface.

**Overall Risk Rating: HIGH**

| Severity | Count |
|----------|-------|
| Critical | 3     |
| High     | 6     |
| Medium   | 6     |
| Low      | 4     |

---

## CRITICAL VULNERABILITIES

### CRITICAL-1: Authentication Completely Broken -- Frontend Never Sends Auth Tokens (CVSS 9.8)

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/middleware.ts` (lines 16-26)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/hooks.ts` (lines 3, 6, 13, 20)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/api.ts` (lines 141-197)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/settings/page.tsx` (lines 16, 35)

**Description:** The middleware at `middleware.ts` checks for a `Bearer` token in the `Authorization` header and blocks requests without it (returning 401). However, **no client-side code ever sends an authorization header**. The `hooks.ts` SWR fetcher is a plain `fetch(url).then(res => res.json())` with no auth headers. The `api.ts` functions use `fetch(url, { cache: 'no-store' })` with no auth. The settings page calls `fetch('/api/config')` with no auth.

This means one of two things:
1. The `API_AUTH_TOKEN` environment variable is not set in production, which triggers the fail-closed 503 response on every API call, **or**
2. The application simply does not work with authentication enabled.

If `API_AUTH_TOKEN` is unset and the middleware returns 503, the frontend is completely non-functional. If someone "fixes" this by removing the middleware check, all endpoints become unauthenticated.

**Attack Scenario:** If the middleware is disabled or bypassed (e.g., by not setting `API_AUTH_TOKEN` and changing the fail-closed logic to fail-open), any internet user can access all API endpoints: scan for trades, modify system configuration, access all users' paper trades, and execute arbitrary Python commands via the scan/backtest endpoints.

**Remediation:**
1. Implement proper authentication (OAuth 2.0, NextAuth.js, or Clerk) with session-based auth.
2. All client-side fetchers must include the authentication credentials.
3. Until proper auth is implemented, if using a static token, it must be injected into every client-side API call.

---

### CRITICAL-2: Remote Code Execution via `child_process.exec` (CVSS 9.1)

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/scan/route.ts` (lines 2, 21)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts` (lines 3, 22)

**Description:** Both the `/api/scan` (POST) and `/api/backtest/run` (POST) endpoints execute shell commands via `child_process.exec`:

```typescript
// scan/route.ts line 21
const { stdout, stderr } = await execPromise(command, {
  cwd: pythonDir,
  timeout: 120000,
});
```

While the command string itself (`"python3 main.py scan"`) is hardcoded and does not include user input, this architecture is inherently dangerous. The `exec` function runs through a shell interpreter, which means:

1. If any future developer modifies the command to include request parameters (e.g., `python3 main.py scan --ticker ${ticker}`), it becomes a direct command injection.
2. The `stdout` and `stderr` from the Python process execution are returned directly in the API response (lines 27-28, 34-38), exposing internal system details.

Additionally, the scan endpoint returns `err.message`, `err.stdout`, and `err.stderr` in error responses (lines 33-39), which leak file paths, Python tracebacks, and system information.

**Attack Scenario:** An authenticated attacker (or any attacker if auth is broken per CRITICAL-1) can trigger arbitrary Python execution. If the command construction ever accepts user input, full RCE is achieved. Even without user input, the returned stdout/stderr leaks internal paths and configuration.

**Remediation:**
1. Replace `exec` with `execFile` (which does not use a shell) or better yet, use a task queue (e.g., Bull/BullMQ with Redis).
2. Never return raw `stdout`/`stderr` to the client. Log them server-side and return only sanitized status messages.
3. Add input validation if any user parameters will ever be passed to the subprocess.

---

### CRITICAL-3: Arbitrary Configuration Write via `/api/config` POST -- System Takeover (CVSS 9.0)

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/config/route.ts` (lines 92, 106-120)

**Description:** The POST handler for `/api/config` accepts a JSON body, validates it with Zod, and writes it to `../config.yaml`. Two critical problems:

1. **The Zod schema uses `.passthrough()`** (line 92), which means **any additional keys not in the schema are accepted and written to the config file**. An attacker can inject arbitrary YAML keys including custom Python-evaluated fields.

2. **The schema accepts `api_key`, `api_secret`, and `bot_token` as writable fields** (lines 62-70). An attacker can overwrite the Alpaca API key/secret, the Tradier API key, the Polygon API key, and the Telegram bot token with their own values.

3. **The write overwrites the entire config.yaml**, not merging. This means an attacker can replace all settings with malicious ones.

**Attack Scenario:**
- Attacker sends POST to `/api/config` with `{"alpaca": {"enabled": true, "api_key": "ATTACKER_KEY", "api_secret": "ATTACKER_SECRET", "paper": false}}`. This switches the system from paper trading to **live trading** with the attacker's brokerage account, potentially redirecting real trades.
- Attacker sets `tickers` to arbitrary symbols and manipulates `risk.max_risk_per_trade` to 100% and `risk.max_positions` to 999, causing the system to make massive trades.
- With `.passthrough()`, attacker can inject arbitrary top-level YAML keys that might be consumed by other parts of the system.

**Remediation:**
1. Remove `.passthrough()` from the Zod schema. Use `.strict()` instead.
2. Remove `api_key`, `api_secret`, `bot_token`, and `chat_id` from the writable schema entirely. These should only be set via environment variables.
3. Prevent writing `alpaca.paper: false` (never allow switching to live trading via the API).
4. Implement config change auditing/logging.
5. Consider making config read-only via the API and requiring manual file edits for sensitive changes.

---

## HIGH VULNERABILITIES

### HIGH-1: Token Comparison Vulnerable to Timing Attack (CVSS 7.5)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/middleware.ts` (line 24)

**Description:** The authentication token comparison uses direct string equality:

```typescript
if (!token || token !== expectedToken) {
```

This is vulnerable to timing side-channel attacks. JavaScript's `!==` operator compares character by character and returns early on the first mismatch, allowing an attacker to determine the correct token one character at a time by measuring response times.

**Remediation:** Use a constant-time comparison:

```typescript
import { timingSafeEqual } from 'crypto';
const tokenBuffer = Buffer.from(token);
const expectedBuffer = Buffer.from(expectedToken);
if (tokenBuffer.length !== expectedBuffer.length || !timingSafeEqual(tokenBuffer, expectedBuffer)) {
  return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
}
```

---

### HIGH-2: User ID Derivable from Token -- Broken User Isolation (CVSS 7.5)

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/middleware.ts` (lines 29-33, 36-44)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts` (lines 31-33)

**Description:** The userId is derived from the auth token using a trivially weak hash (DJB2-style, line 36-44):

```typescript
function simpleHash(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash |= 0;
  }
  return Math.abs(hash).toString(36);
}
```

Since there is only one `API_AUTH_TOKEN`, **all users share the same userId** (`user_<hash>`). This means there is zero user isolation -- everyone's paper trades are stored in the same file. Furthermore, the userId is set via a response header `x-user-id`, but the paper-trades route reads it from the request header (line 32: `request.headers.get('x-user-id') || 'default'`). Next.js middleware sets response headers, not request headers for downstream route handlers. The `x-user-id` header may never actually reach the route handler, causing all trades to go to the `default` user.

**Remediation:**
1. Implement proper user authentication with unique user identifiers (JWTs, sessions).
2. If using a shared token, at minimum allow a user-identifying header from the client.
3. Verify that middleware-set headers are accessible in route handlers (they may not be in Next.js middleware).

---

### HIGH-3: No Rate Limiting on Critical Endpoints (CVSS 7.0)

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/scan/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/config/route.ts`

**Description:** Only the `/api/chat` endpoint has rate limiting. The `/api/scan` and `/api/backtest/run` endpoints have a concurrency guard (only one at a time) but no rate limit -- an attacker can trigger scans/backtests continuously, consuming server resources and making excessive calls to third-party APIs (Polygon, Tradier, Alpaca), potentially exhausting API quotas or incurring costs.

The paper-trades endpoint has no rate limiting, allowing rapid creation/deletion of trades.

**Attack Scenario:** An attacker repeatedly triggers `/api/scan`, which calls `python3 main.py scan`. This calls multiple external APIs (yfinance, Polygon, Tradier) and can exhaust rate limits or incur charges on paid API tiers.

**Remediation:**
1. Add rate limiting to all mutable API endpoints (POST/DELETE).
2. For `/api/scan` and `/api/backtest/run`, add a cooldown period (e.g., minimum 5 minutes between scans).
3. Use a proper rate limiter (e.g., `upstash/ratelimit` or a Redis-backed solution).

---

### HIGH-4: Secrets Stored in config.yaml Instead of Environment Variables (CVSS 7.0)

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/config.yaml` (lines 82-83, 88-89, 99, 103)

**Description:** While environment variable substitution is supported (`${ALPACA_API_KEY}`), the config file contains placeholder secrets as defaults:

```yaml
bot_token: "YOUR_BOT_TOKEN_HERE"
chat_id: "YOUR_CHAT_ID_HERE"
api_key: "YOUR_TRADIER_API_KEY"
```

The `config.yaml` file is tracked in git (it is not in `.gitignore`). If a developer accidentally replaces these placeholders with real keys and commits, the secrets are permanently in git history. The Tradier `api_key` on line 99 does **not** use environment variable substitution unlike Alpaca and Polygon.

**Remediation:**
1. Add `config.yaml` to `.gitignore` and track only `config.yaml.example`.
2. Convert **all** secrets to use `${ENV_VAR}` substitution.
3. Remove any hardcoded placeholder values that look like real keys.
4. Use a secrets manager in production (AWS Secrets Manager, HashiCorp Vault).

---

### HIGH-5: No Security Headers (CVSS 6.5)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/next.config.js`

**Description:** The Next.js configuration has no security headers configured. Missing headers include:

- **Content-Security-Policy (CSP):** No CSP means the application is vulnerable to XSS attacks. The TradingView widget (loaded from `s3.tradingview.com` in `ticker.tsx`) injects external JavaScript, which would need to be whitelisted.
- **X-Frame-Options / frame-ancestors:** The application can be embedded in iframes, enabling clickjacking attacks on the trading dashboard.
- **X-Content-Type-Options:** Missing `nosniff` header allows MIME-type confusion attacks.
- **Referrer-Policy:** No policy means the full URL (potentially with tokens) is sent in referrer headers.
- **Strict-Transport-Security (HSTS):** Not enforced.
- **Permissions-Policy:** Camera, microphone, geolocation not restricted.

**Remediation:** Add security headers in `next.config.js`:

```javascript
const nextConfig = {
  async headers() {
    return [{
      source: '/(.*)',
      headers: [
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        { key: 'X-DNS-Prefetch-Control', value: 'on' },
        { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains' },
        { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        { key: 'Content-Security-Policy', value: "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' s3.tradingview.com; ..." },
      ],
    }];
  },
};
```

---

### HIGH-6: Error Messages Expose Internal State (CVSS 6.0)

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/scan/route.ts` (lines 33-39)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts` (line 51)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/error.tsx` (line 14)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/global-error.tsx` (line 9)

**Description:** The scan endpoint returns raw error messages, stdout, and stderr in the API response:

```typescript
return NextResponse.json({
  success: false,
  error: err.message,      // Contains file paths, stack traces
  output: err.stdout || null,  // Raw Python output
  errors: err.stderr || null,  // Python tracebacks with paths
}, { status: 500 });
```

The error boundary components also display `error.message` directly to the user (error.tsx line 14, global-error.tsx line 9), which in Next.js can contain server-side error details.

**Remediation:**
1. Never return `stdout`, `stderr`, or raw error messages in API responses. Return generic error messages and log details server-side.
2. In error boundaries, display generic messages and use the `digest` property only for error tracking.

---

## MEDIUM VULNERABILITIES

### MEDIUM-1: Third-Party Script Injection via TradingView Widget (CVSS 6.0)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/components/layout/ticker.tsx` (lines 10-36)

**Description:** The component directly sets `innerHTML` and injects an external script from `s3.tradingview.com`:

```typescript
containerRef.current.innerHTML = ''
const script = document.createElement('script')
script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js'
script.innerHTML = JSON.stringify({...})
containerRef.current.appendChild(script)
```

This loads and executes third-party JavaScript from TradingView's CDN. If TradingView's CDN is compromised or serves malicious content, it has full access to the application's DOM, cookies, and local storage. The `innerHTML = ''` also bypasses React's virtual DOM.

**Remediation:**
1. Add Subresource Integrity (SRI) to the script tag.
2. Configure CSP to only allow this specific script source.
3. Load the widget in a sandboxed iframe instead of injecting directly into the DOM.

---

### MEDIUM-2: Config POST Enables Path Traversal via File Paths (CVSS 5.5)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/config/route.ts` (lines 57-59, 83-84, 91-92)

**Description:** The config schema accepts `json_file`, `text_file`, `csv_file`, `report_dir`, and `logging.file` as writable string fields without path validation. Combined with `.passthrough()`, an attacker can set these to arbitrary paths:

```json
{
  "alerts": {"json_file": "/etc/cron.d/malicious"},
  "logging": {"file": "/tmp/evil.log"},
  "backtest": {"report_dir": "/home/user/.ssh/"}
}
```

When the Python backend reads this config and writes alert files or logs, it writes to attacker-controlled paths.

**Remediation:**
1. Validate all file path fields to ensure they are relative paths within the project directory.
2. Use `path.resolve()` and verify the resolved path starts with the project root.
3. Remove file path fields from the writable API schema entirely.

---

### MEDIUM-3: In-Memory Rate Limiter Bypass (CVSS 5.0)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts` (lines 16-36, 60)

**Description:** The rate limiter uses `x-forwarded-for` header for IP identification (line 60):

```typescript
const ip = request.headers.get('x-forwarded-for')?.split(',')[0]?.trim() || 'unknown';
```

Problems:
1. The `x-forwarded-for` header is client-controlled unless stripped by a trusted reverse proxy. An attacker can set arbitrary values to bypass rate limiting.
2. The rate limiter is in-memory, so it resets on every server restart or serverless cold start.
3. The cleanup threshold of 1000 entries (line 23) allows memory exhaustion by sending requests with 1000+ unique forged IPs.

**Remediation:**
1. Use a trusted source for client IP (e.g., `request.ip` from Next.js, or configure the deployment platform's trusted proxy headers).
2. Use a persistent rate limiter (Redis-backed or Upstash Ratelimit).
3. Add a maximum Map size with eviction.

---

### MEDIUM-4: Client-Side User ID Forgeable (CVSS 5.0)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/user-id.ts` (lines 10-18)

**Description:** The client-side `getUserId()` generates a random UUID stored in `localStorage`. This ID is used for paper trading. An attacker can:

1. Modify `localStorage` to set any user ID, including another user's ID, to access their portfolio.
2. Clear `localStorage` to get a fresh portfolio with $100,000 balance.
3. The function returns `'server'` when running server-side, which could conflict with actual users.

Note: This client-side user ID system is separate from the middleware-based `x-user-id` system, creating confusion about which identity mechanism is actually used.

**Remediation:**
1. Consolidate on a single identity mechanism tied to proper authentication.
2. Never trust client-supplied user IDs. Derive them from authenticated sessions server-side.

---

### MEDIUM-5: Prompt Injection in Chat Endpoint (CVSS 5.0)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts` (lines 65, 73-78, 100)

**Description:** User messages are forwarded directly to the OpenAI API without sanitization:

```typescript
const { messages, alerts } = await request.json();
// ...
messages: [
  { role: 'system', content: contextPrompt },
  ...messages.slice(-10),  // User messages passed directly
],
```

An attacker can inject prompt instructions to override the system prompt, extract the system prompt content, or cause the model to generate misleading financial advice.

**Remediation:**
1. Sanitize user messages to remove prompt injection patterns.
2. Limit message content length.
3. Add output filtering for the model's responses.
4. Add a disclaimer that AI responses are not financial advice.

---

### MEDIUM-6: ESLint Disabled During Builds (CVSS 4.0)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/next.config.js` (lines 4-6)

**Description:**

```javascript
eslint: {
  ignoreDuringBuilds: true,
},
```

ESLint catches security issues (e.g., `dangerouslySetInnerHTML`, missing `rel="noopener"`, etc.). Ignoring it during builds means security-relevant lint rules are silently bypassed.

**Remediation:** Enable ESLint during builds. Fix any lint errors rather than suppressing them.

---

## LOW VULNERABILITIES

### LOW-1: CORS Not Explicitly Configured (CVSS 3.5)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/next.config.js`

**Description:** No CORS configuration is set. Next.js API routes default to same-origin, which is generally safe, but there is no explicit deny policy for cross-origin requests.

**Remediation:** Add explicit CORS headers in API routes or middleware to deny cross-origin requests unless specifically needed.

---

### LOW-2: Weak Trade ID Generation (CVSS 3.0)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts` (line 158)

**Description:**

```typescript
id: `PT-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`,
```

`Math.random()` is not cryptographically secure. Trade IDs are predictable, potentially allowing an attacker to guess valid trade IDs and close other users' trades.

**Remediation:** Use `crypto.randomUUID()` for trade ID generation.

---

### LOW-3: Alpaca Account Number Logged (CVSS 2.5)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/alpaca_provider.py` (lines 43-47)

**Description:**

```python
logger.info(
    f"Alpaca connected | Account: {acct.account_number} | "
    f"Status: {acct.status} | Cash: ${float(acct.cash):,.2f} | "
    f"Options Level: {acct.options_trading_level}"
)
```

The account number and cash balance are logged to the rotating log file. If log files are accessible, this leaks financial account details.

**Remediation:** Redact account numbers in logs. Log only the last 4 characters.

---

### LOW-4: Telegram Bot Token in Example Code (CVSS 2.0)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/alerts/telegram_bot.py` (line 159)

**Description:**

```python
bot_token: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
```

While this is a placeholder in documentation, it demonstrates the exact format of a Telegram bot token, which could lead to accidental real token commits.

**Remediation:** Use a clearly fake placeholder like `"<YOUR_BOT_TOKEN_HERE>"`.

---

## ADDITIONAL OBSERVATIONS

### Dependency Concerns

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/package.json`

- `next: ^14.2.0` -- Next.js 14.2.x has had several security patches. Ensure the latest 14.2.x patch is installed.
- `react: ^19.2.4` / `react-dom: ^19.2.4` -- React 19 with Next.js 14 may have compatibility issues.
- `zod: ^4.3.6` -- Zod v4 is relatively new; ensure it handles edge cases correctly with `.passthrough()`.

### Architecture Concerns

1. **File-based data storage** (`data/user_trades/*.json`) is not suitable for a multi-user financial application. Race conditions can corrupt data even with the in-memory mutex. Use a database.

2. **Single auth token for all users** means there is no user-level access control. Every authenticated user can access every other user's data.

3. **The Python backend runs as a subprocess** with no sandboxing. The Next.js process has full filesystem access to the parent directory.

---

## REMEDIATION PRIORITY

| Priority | Issue | Effort |
|----------|-------|--------|
| P0 (Immediate) | CRITICAL-1: Fix authentication -- frontend must send auth tokens | Medium |
| P0 (Immediate) | CRITICAL-3: Remove `.passthrough()`, block secret fields from config write | Low |
| P0 (Immediate) | CRITICAL-2: Replace `exec` with `execFile`, sanitize output | Low |
| P1 (This Week) | HIGH-1: Use `timingSafeEqual` for token comparison | Low |
| P1 (This Week) | HIGH-4: Move all secrets to env vars, gitignore config.yaml | Low |
| P1 (This Week) | HIGH-5: Add security headers to next.config.js | Low |
| P1 (This Week) | HIGH-6: Stop returning stdout/stderr in API responses | Low |
| P2 (This Sprint) | HIGH-2: Fix user isolation / middleware header propagation | Medium |
| P2 (This Sprint) | HIGH-3: Add rate limiting to scan/backtest/config endpoints | Medium |
| P3 (Next Sprint) | MEDIUM-1 through MEDIUM-6 | Medium-High |
| P4 (Backlog) | LOW-1 through LOW-4 | Low |

---

## CONCLUSION

The most urgent finding is that **authentication is architecturally broken** (CRITICAL-1). The middleware enforces auth, but no client code sends auth tokens, meaning the application either does not function or has been deployed without authentication. Combined with the command execution endpoints (CRITICAL-2) and the unrestricted config write (CRITICAL-3), this creates a chain where an unauthenticated attacker could reconfigure the entire trading system and execute arbitrary code.

For a financial application, these issues are unacceptable for production deployment. I recommend halting any production deployment until at minimum the three critical and six high-severity issues are resolved.

---

<a id="panel-4-performance"></a>

# Performance Review: Attix Credit Spreads

## Executive Summary

The system is a Python + Next.js 14 trading platform. The Python backend handles strategy execution, ML analysis, and paper trading. The Next.js frontend provides a dashboard with polling-based updates. The most severe performance issues are in the **ML pipeline's redundant API calls** (multiple yfinance downloads of the same data), **sequential per-ticker processing**, and **file-based storage that lacks atomicity**. The frontend has moderate issues around duplicate data fetching and missing memoization.

---

## 1. CRITICAL: Redundant yfinance API Calls in ML Pipeline (Multiple Files)

**Severity: Critical**
**Estimated Impact: Adds 15-30 seconds per ticker scan; 45-90 seconds for the default 3-ticker scan**

During a single `analyze_trade()` call for one ticker, the system downloads historical price data from yfinance **at least 5 separate times**:

1. `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:139` -- `yf.download(ticker, period='6mo')` in `_compute_technical_features`
2. `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:207` -- `yf.download(ticker, period='3mo')` in `_compute_volatility_features`
3. `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:271` -- `yf.download('^VIX', period='5d')` in `_compute_market_features`
4. `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:284` -- `yf.download('SPY', period='3mo')` in `_compute_market_features`
5. `/home/pmcerlean/projects/pilotai-credit-spreads/ml/regime_detector.py:266-268` -- Downloads `SPY`, `^VIX`, AND `TLT` in `_get_current_features`
6. `/home/pmcerlean/projects/pilotai-credit-spreads/ml/iv_analyzer.py:313` -- `yf.download(ticker, ...)` in `_get_iv_history`

Then at the `main.py` level (line 148), `stock.history(period='3mo')` is called **again**, and `calculate_iv_rank` at line 168 calls `stock.history(period='1y')` yet **again**.

For 3 tickers, this is approximately **18-24 separate HTTP requests** to yfinance for data that could be fetched once and shared.

**Recommendation:**
```python
# Create a DataCache class that fetches once and shares
class MarketDataCache:
    def __init__(self):
        self._cache = {}
    
    def get_price_data(self, ticker: str, period: str) -> pd.DataFrame:
        key = f"{ticker}_{period}"
        if key not in self._cache:
            self._cache[key] = yf.download(ticker, period=period, progress=False)
        return self._cache[key]
```
Pass this cache through the entire pipeline.

---

## 2. CRITICAL: Sequential Ticker Processing

**Severity: Critical**
**Estimated Impact: 3x slower than necessary for the default 3-ticker scan**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/main.py:87-91`

```python
for ticker in self.config['tickers']:
    logger.info(f"Analyzing {ticker}...")
    opportunities = self._analyze_ticker(ticker)
    all_opportunities.extend(opportunities)
```

Each ticker is processed sequentially. Since the work is I/O-bound (API calls to yfinance, Tradier, or Polygon), these could be parallelized with `concurrent.futures.ThreadPoolExecutor` or `asyncio`.

Similarly, in `ml_pipeline.py:379-416`, `batch_analyze` loops sequentially:

```python
for opp in opportunities:
    analysis = self.analyze_trade(...)
```

**Recommendation:**
```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=3) as executor:
    results = list(executor.map(self._analyze_ticker, self.config['tickers']))
all_opportunities = [opp for result in results for opp in result]
```

---

## 3. HIGH: Polygon Provider Fetches Full Snapshot Twice

**Severity: High**
**Estimated Impact: Doubles API call time for options chain retrieval (~2-5s wasted per ticker)**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/polygon_provider.py:81-149` and `:151-223`

The `get_options_chain()` method fetches the **entire** options snapshot for a ticker (all expirations), then filters for one expiration. The `get_full_chain()` method does the **exact same full fetch** but filters by DTE range. If `get_full_chain()` is used (which is the normal path via `options_analyzer.py:94`), the data is fetched correctly -- but `get_options_chain()` is wasteful when called directly because it fetches everything and throws most of it away.

More importantly, the Polygon snapshot uses pagination (`next_url` loop), and each page is fetched **synchronously** with `requests.get`. This pattern appears in both methods identically (copy-pasted code at lines 96-104 and 160-169).

**Recommendation:** Extract the snapshot fetch into a single cached method. Use it from both `get_options_chain` and `get_full_chain`.

---

## 4. HIGH: Synchronous HTTP Requests Throughout Python Backend

**Severity: High**
**Estimated Impact: Blocks thread during every external API call; adds cumulative seconds per scan**

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/tradier_provider.py:37,47,76` -- all use `requests.get` (synchronous)
- `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/polygon_provider.py:33` -- `requests.get` (synchronous)
- All `yf.download()` and `yf.Ticker()` calls across the codebase

The entire backend uses synchronous `requests` library. For a system that makes 18+ HTTP calls per scan cycle, this is a significant bottleneck.

**Recommendation:** Either use `httpx` with `async` support throughout, or use `requests_futures` / `concurrent.futures` for parallel API calls. At minimum, batch the independent calls.

---

## 5. HIGH: yfinance Downloads ALL Expirations When Only a Few Are Needed

**Severity: High**
**Estimated Impact: Downloads 10-20x more data than needed, adding 3-8 seconds per ticker**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/options_analyzer.py:116-133`

```python
for exp_date_str in expirations:  # ALL expirations
    opt_chain = stock.option_chain(exp_date_str)
    # ... appends to all_options
```

For SPY, there are typically 30+ expiration dates. This code fetches **every single one**, even though the strategy only needs 30-45 DTE. The filtering happens later in `spread_strategy.py:84-94`.

**Recommendation:** Filter expirations by DTE range **before** fetching:
```python
for exp_date_str in expirations:
    exp_date = datetime.strptime(exp_date_str, '%Y-%m-%d')
    dte = (exp_date - datetime.now()).days
    if self.config['strategy']['min_dte'] - 5 <= dte <= self.config['strategy']['max_dte'] + 5:
        opt_chain = stock.option_chain(exp_date_str)
        # ...
```

---

## 6. HIGH: O(n^2) Support/Resistance Detection

**Severity: High**
**Estimated Impact: Negligible for current data sizes (~250 bars), but poor algorithmic design**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py:190-198`

```python
for i in range(window, len(lows) - window):
    if lows[i] == min(lows[i - window:i + window + 1]):
        support.append(lows[i])
```

The inner `min()` call creates a slice and scans it each iteration, making this O(n * window). With the current window of 5 and ~250 data points, this is trivial. However, a better approach would use scipy's `argrelextrema` or a sliding window minimum.

**Recommendation:** Use `scipy.signal.argrelextrema(lows, np.less_equal, order=window)` for O(n) detection.

---

## 7. HIGH: Heatmap Component Makes Redundant API Call

**Severity: High**
**Estimated Impact: Extra unnecessary API request on every page load**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/components/sidebar/heatmap.tsx:9-38`

```tsx
useEffect(() => {
    const fetchData = async () => {
        const res = await fetch('/api/positions')  // Fetches positions AGAIN
        // ...
    }
    fetchData()
}, [])
```

The `Heatmap` component makes its own `fetch('/api/positions')` call, even though the parent `HomePage` already fetches positions via the `usePositions()` SWR hook. The same data is fetched twice on every page load.

**Recommendation:** Pass the positions data from the parent via props, or use the shared `usePositions()` hook:
```tsx
export function Heatmap() {
  const { data } = usePositions()  // Reuses cached SWR data
  // ...
}
```

---

## 8. HIGH: Paper Trading Page Does Not Use SWR

**Severity: High**
**Estimated Impact: No automatic refresh; stale data; redundant manual fetch logic**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/paper-trading/page.tsx:64-76`

```tsx
const fetchData = async () => {
    const res = await fetch('/api/positions')
    const json = await res.json()
    setData(json)
}
useEffect(() => { fetchData() }, [])
```

This page uses raw `fetch` + `useState` instead of the project's established `usePositions()` SWR hook. This means:
- No automatic polling/refresh (the hook does 30s intervals)
- No deduplication with other components
- No SWR cache sharing
- Manual loading state management

**Recommendation:** Replace with `const { data, isLoading } = usePositions()`.

---

## 9. MEDIUM: File-Based Storage Without Caching

**Severity: Medium**
**Estimated Impact: Disk I/O on every API request; ~1-5ms per read but scales poorly**

**Files:**
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/positions/route.ts:26-30` -- reads JSON from disk on every GET
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/alerts/route.ts:15-19` -- reads JSON from disk on every GET
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts:62-74` -- reads + writes JSON on every request

Every API request reads the full JSON file from disk. With polling intervals of 30-60 seconds and potentially multiple users, this becomes a bottleneck. The `paper-trades` route also writes the entire portfolio to disk on every trade operation.

The `paper_trader.py:80-84` writes to disk on every trade operation (and calls `_export_for_dashboard` which writes a second file):
```python
def _save_trades(self):
    with open(PAPER_LOG, "w") as f:
        json.dump(self.trades, f, indent=2, default=str)
    self._export_for_dashboard()  # Writes ANOTHER file
```

**Recommendation:**
- Add in-memory caching with TTL for reads (e.g., cache for 5 seconds)
- For the Python backend, only write when data actually changes
- Consider SQLite for structured trade data instead of flat JSON files

---

## 10. MEDIUM: `open_trades` and `closed_trades` Properties Scan Full List Every Time

**Severity: Medium**
**Estimated Impact: O(n) per property access; called multiple times per operation**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py:101-107`

```python
@property
def open_trades(self) -> List[Dict]:
    return [t for t in self.trades["trades"] if t["status"] == "open"]

@property
def closed_trades(self) -> List[Dict]:
    return [t for t in self.trades["trades"] if t["status"] == "closed"]
```

These are called multiple times per operation:
- `execute_signals` calls `open_trades` twice (lines 123, 134-136)
- `check_positions` calls `open_trades` once (line 251)
- `_close_trade` calls `closed_trades` twice (lines 384, 385)
- `get_summary` calls `open_trades` twice (lines 409, 419)

With hundreds of trades, this creates unnecessary iteration. In `_close_trade`, the `closed_trades` property is called to compute `avg_winner` and `avg_loser`, scanning the entire list for every close.

**Recommendation:** Cache the filtered lists and invalidate on mutation:
```python
def _invalidate_cache(self):
    self._open_cache = None
    self._closed_cache = None
```

---

## 11. MEDIUM: Next.js Config Missing Output Standalone and Image Optimization

**Severity: Medium**
**Estimated Impact: Larger bundle size; slower Docker builds**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/next.config.js`

```javascript
const nextConfig = {
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
}
```

Missing configurations:
- `output: 'standalone'` for optimized Docker builds (the Dockerfile references standalone mode)
- No `images` configuration for external image optimization
- No `experimental.optimizeCss` or `swcMinify`
- `eslint.ignoreDuringBuilds: true` masks potential issues

**Recommendation:**
```javascript
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  eslint: { ignoreDuringBuilds: false },
  experimental: { optimizeCss: true },
}
```

---

## 12. MEDIUM: Regime Detector Downloads 3 Tickers for Training on Every Initialization

**Severity: Medium**
**Estimated Impact: 5-10 seconds added to startup**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/regime_detector.py:200-206`

```python
spy = yf.download('SPY', start=start_date, end=end_date, progress=False)
vix = yf.download('^VIX', start=start_date, end=end_date, progress=False)
tlt = yf.download('TLT', start=start_date, end=end_date, progress=False)
```

On every startup (when `self.trained` is False), the regime detector downloads 252+ days of data for 3 tickers. This happens synchronously and sequentially.

**Recommendation:** Download in parallel, and cache the training data to disk with a timestamp so it only needs to be refreshed daily:
```python
with ThreadPoolExecutor(max_workers=3) as executor:
    spy_future = executor.submit(yf.download, 'SPY', ...)
    vix_future = executor.submit(yf.download, '^VIX', ...)
    tlt_future = executor.submit(yf.download, 'TLT', ...)
```

---

## 13. MEDIUM: `_analyze_trend` Mutates Input DataFrame

**Severity: Medium**
**Estimated Impact: Unexpected side effects; potential bugs**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py:84-85`

```python
price_data['MA_fast'] = price_data['Close'].rolling(window=fast_period).mean()
price_data['MA_slow'] = price_data['Close'].rolling(window=slow_period).mean()
```

This modifies the caller's DataFrame by adding columns. If the same DataFrame is used elsewhere (e.g., for support/resistance analysis), it now has extra columns and may trigger a `SettingWithCopyWarning`.

**Recommendation:** Use `.copy()` or compute locally without modifying the input.

---

## 14. MEDIUM: scan/route.ts and backtest/run/route.ts Spawn Python Subprocess

**Severity: Medium**
**Estimated Impact: 10-120 seconds per invocation; no streaming; blocks Next.js server worker**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/scan/route.ts:21-24`

```typescript
const { stdout, stderr } = await execPromise(command, {
    cwd: pythonDir,
    timeout: 120000,  // 2 minutes!
});
```

And `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts:22-25`:
```typescript
const { stdout, stderr } = await execPromise('python3 main.py backtest', {
    cwd: systemPath,
    timeout: 300000,  // 5 minutes!
});
```

These spawn a full Python process, which re-initializes everything (loads config, creates all class instances, downloads market data, trains ML models). The 2-5 minute timeouts indicate awareness of how slow this is. The concurrency guard (`let scanInProgress = false`) is module-level but not persistent across serverless function invocations.

**Recommendation:** Run the Python backend as a long-lived service (e.g., FastAPI) and have the Next.js API routes call it via HTTP. This eliminates the startup overhead on every invocation.

---

## 15. MEDIUM: ML Model Trained on Synthetic Data at Startup

**Severity: Medium**
**Estimated Impact: 2-5 seconds at startup; questionable predictive value**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/ml_pipeline.py:96-103`

```python
if not self.signal_model.trained:
    if not self.signal_model.load():
        features_df, labels = self.signal_model.generate_synthetic_training_data(
            n_samples=2000, win_rate=0.65
        )
        self.signal_model.train(features_df, labels)
```

If no saved model exists, 2000 synthetic samples are generated and an XGBoost model is trained at startup. This adds latency and the model is trained on artificial data.

**Recommendation:** Ship a pre-trained model file. The synthetic training should be a one-time setup step, not a startup task.

---

## 16. MEDIUM: Rate Limiter Memory Leak in Chat Route

**Severity: Medium**
**Estimated Impact: Unbounded memory growth under sustained load**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts:17-36`

```typescript
const rateLimitMap = new Map<string, { count: number; resetAt: number }>();
// ...
if (rateLimitMap.size > 1000) {
    for (const [key, val] of Array.from(rateLimitMap)) {
        if (now > val.resetAt) rateLimitMap.delete(key);
    }
}
```

The cleanup only triggers when the map exceeds 1000 entries, and only removes expired entries. Under sustained traffic from many IPs, this map grows unbounded until 1000 entries, then does a full scan. The `Array.from()` creates a copy of all entries for iteration.

**Recommendation:** Use a proper TTL map, or clean up on every request (only checking the current IP's entry), or use a LRU cache.

---

## 17. LOW: Missing `useMemo`/`useCallback` in HomePage

**Severity: Low**
**Estimated Impact: Unnecessary re-renders on filter/state changes**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/page.tsx:36-56`

```tsx
const filteredAlerts = alerts.filter(alert => { ... })
const avgPOP = alerts.length > 0 ? alerts.reduce(...) / alerts.length : 0
const closedTrades: PaperTrade[] = positions?.closed_trades || []
const winners = closedTrades.filter((t) => ...)
// ... etc
```

All these computations run on every render. When the user changes the filter, all trade statistics are recomputed even though only `filteredAlerts` depends on the filter state.

**Recommendation:**
```tsx
const closedTrades = useMemo(() => positions?.closed_trades || [], [positions])
const stats = useMemo(() => {
    const winners = closedTrades.filter(...)
    // ...
}, [closedTrades])
```

---

## 18. LOW: AlertCard Component Missing Stable Keys

**Severity: Low**
**Estimated Impact: Potential React reconciliation issues**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/page.tsx:134`

```tsx
{filteredAlerts.map((alert, idx) => (
    <AlertCard key={idx} alert={alert} isNew={idx < 2} />
))}
```

Using array index as key is problematic when items are filtered/reordered. This can cause state leaks (e.g., the `expanded` state in `AlertCard` persisting across different alerts after a filter change).

**Recommendation:** Use a stable identifier:
```tsx
key={`${alert.ticker}-${alert.type}-${alert.short_strike}-${alert.expiration}`}
```

---

## 19. LOW: RSI Calculated Redundantly Across Multiple Modules

**Severity: Low**
**Estimated Impact: Wasted CPU cycles; code duplication**

The RSI calculation is implemented identically in three places:
- `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py:107-124`
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py:475-482`
- `/home/pmcerlean/projects/pilotai-credit-spreads/ml/regime_detector.py:343-354`

**Recommendation:** Extract to a shared `utils/indicators.py` module.

---

## 20. LOW: `_estimate_delta` Uses Median Strike as Spot Price

**Severity: Low (correctness issue more than performance)**
**Estimated Impact: Incorrect delta estimates when strike distribution is skewed**

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/options_analyzer.py:194`

```python
spot = df['strike'].median()
```

When estimating delta, the spot price is set to the median of all strike prices instead of the actual current stock price. This produces inaccurate deltas, especially for highly skewed chains.

**Recommendation:** Pass `current_price` from the caller instead of guessing from strikes.

---

## Summary Table

| # | Severity | Issue | Impact | File |
|---|----------|-------|--------|------|
| 1 | Critical | Redundant yfinance downloads | +15-30s per ticker | feature_engine.py, regime_detector.py, iv_analyzer.py |
| 2 | Critical | Sequential ticker processing | 3x slower than needed | main.py:87 |
| 3 | High | Polygon full snapshot fetched redundantly | +2-5s per ticker | polygon_provider.py |
| 4 | High | Synchronous HTTP throughout backend | Cumulative seconds | All providers |
| 5 | High | yfinance fetches ALL expirations | +3-8s per ticker | options_analyzer.py:116 |
| 6 | High | O(n*w) support/resistance | Negligible now | technical_analysis.py:190 |
| 7 | High | Heatmap duplicates /api/positions fetch | Extra HTTP request | heatmap.tsx:12 |
| 8 | High | Paper trading page bypasses SWR | No auto-refresh, stale data | paper-trading/page.tsx:64 |
| 9 | Medium | File-based storage, no read caching | ~1-5ms per read, scales poorly | positions/route.ts, alerts/route.ts |
| 10 | Medium | open_trades/closed_trades re-scan list | O(n) per call, called multiple times | paper_trader.py:101-107 |
| 11 | Medium | Missing Next.js config optimizations | Larger bundles | next.config.js |
| 12 | Medium | Regime detector downloads 3 tickers sequentially | +5-10s startup | regime_detector.py:200 |
| 13 | Medium | `_analyze_trend` mutates input DataFrame | Side effects | technical_analysis.py:84 |
| 14 | Medium | Subprocess spawn for scan/backtest | 10-120s per call | scan/route.ts, backtest/run/route.ts |
| 15 | Medium | ML model trained at startup from synthetic data | +2-5s startup | ml_pipeline.py:96 |
| 16 | Medium | Rate limiter unbounded memory | Memory leak | chat/route.ts:17 |
| 17 | Low | Missing useMemo in HomePage | Unnecessary re-renders | page.tsx:36 |
| 18 | Low | Array index as React key | Potential state bugs | page.tsx:134 |
| 19 | Low | RSI implemented 3 times | Code duplication | 3 files |
| 20 | Low | Delta estimation uses median strike as spot | Incorrect estimates | options_analyzer.py:194 |

---

## Top 3 Recommendations by Impact

1. **Implement a shared market data cache** that eliminates redundant yfinance/API calls. A single scan should fetch each ticker's price data **once** and pass it through the pipeline. This alone could reduce scan time from ~90 seconds to ~15 seconds for 3 tickers.

2. **Parallelize ticker processing** in `main.py` using `ThreadPoolExecutor`. Since the work is I/O-bound (network requests), parallel execution would cut total scan time by roughly 3x (the number of tickers).

3. **Replace subprocess spawning** (`scan/route.ts`, `backtest/run/route.ts`) with a persistent Python service (FastAPI/Flask). The current approach re-initializes the entire system (config loading, ML model training, API client setup) on every request, adding 10+ seconds of startup overhead each time.

---

<a id="panel-5-error-handling"></a>

# Error Handling and Resilience Review: Attix Credit Spreads

## Executive Summary

This trading system has **reasonable high-level error handling** (graceful ML fallback, try/catch around ticker analysis, error boundaries on the frontend) but suffers from several **critical and high-severity gaps** that could lead to silent data loss, missed trade closings, stale positions, and financial exposure. The most severe issues are: (1) bare `except:` clauses that silently swallow errors during position management, (2) no atomic write protection on JSON persistence files, (3) missing retry logic on external data provider HTTP calls, and (4) a missing `@/lib/logger` module that means all server-side API logging may be broken at runtime.

---

## CRITICAL Issues

### C1. Bare `except:` Swallowing Errors During Price Fetch for Position Management

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/main.py`, lines 118-124
**Severity:** CRITICAL

```python
for ticker in self.config['tickers']:
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period='1d')
        if not hist.empty:
            current_prices[ticker] = hist['Close'].iloc[-1]
    except:
        pass
```

**Risk scenario:** If yfinance raises ANY exception (network timeout, rate limit, malformed response, KeyError), the error is completely silenced. `current_prices` will be empty or partial, meaning `check_positions()` will either not run or use stale `entry_price` as the fallback. Open positions that should be closed (profit target hit, stop loss breached, or expired) will remain open indefinitely, exposing the account to uncapped losses.

**Recommended fix:**
```python
except Exception as e:
    logger.warning(f"Failed to fetch current price for {ticker}: {e}")
```
Additionally, if no prices can be fetched at all, log a CRITICAL-level alert and consider sending a notification.

---

### C2. Bare `except:` Clauses in Paper Trader Expiration Date Parsing

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py`, lines 257-263
**Severity:** CRITICAL

```python
try:
    exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
except:
    try:
        exp_date = datetime.fromisoformat(trade["expiration"])
    except:
        exp_date = now + timedelta(days=30)
```

**Risk scenario:** If the expiration date is malformed, the system silently assigns a fallback of 30 days from now. This means a trade that should have been closed at expiration stays open for another month, fully exposed to the market. For a bull put spread on a ticker that has crashed, this turns a defined-risk trade into an extended losing position.

**Recommended fix:**
```python
except ValueError as e:
    logger.warning(f"Could not parse expiration '{exp_str}' for trade {trade.get('id')}: {e}")
    try:
        exp_date = datetime.fromisoformat(trade["expiration"])
    except (ValueError, TypeError) as e2:
        logger.error(f"Fallback expiration parse also failed for trade {trade.get('id')}: {e2}. "
                     "Defaulting to 30 days.")
        exp_date = now + timedelta(days=30)
```

---

### C3. No Atomic Writes on JSON Trade Files (Data Corruption Risk)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py`, lines 80-84
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/tracker/trade_tracker.py`, lines 57-65
**Severity:** CRITICAL

```python
# paper_trader.py
def _save_trades(self):
    with open(PAPER_LOG, "w") as f:
        json.dump(self.trades, f, indent=2, default=str)
```

```python
# trade_tracker.py
def _save_trades(self):
    with open(self.trades_file, 'w') as f:
        json.dump(self.trades, f, indent=2, default=str)
```

**Risk scenario:** If the process crashes mid-write (power failure, OOM kill, SIGKILL), the JSON file will be truncated or corrupted. On next load, `json.load()` will raise a `JSONDecodeError`, and all trade history is lost. This includes both open positions (no visibility into what is at risk) and closed trade records (P&L history gone). The `_load_trades()` method in both files has no exception handling around `json.load()`.

**Recommended fix:** Use atomic write (write to temp file, then rename):
```python
import tempfile

def _save_trades(self):
    tmp_fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, suffix='.json')
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            json.dump(self.trades, f, indent=2, default=str)
        os.replace(tmp_path, PAPER_LOG)  # Atomic on POSIX
    except Exception:
        os.unlink(tmp_path)
        raise
```

Also add corrupted-file recovery to `_load_trades()`:
```python
def _load_trades(self) -> Dict:
    if PAPER_LOG.exists():
        try:
            with open(PAPER_LOG) as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.critical(f"Trade file corrupted: {e}. Backing up and starting fresh.")
            PAPER_LOG.rename(PAPER_LOG.with_suffix('.corrupted'))
    return { ... default ... }
```

---

### C4. Missing `@/lib/logger` Module -- All API Route Logging May Be Broken

**Files:** All 7 API route files import `{ logger } from "@/lib/logger"`:
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/positions/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/trades/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/config/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts`
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts`

**Severity:** CRITICAL

The file `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/logger.ts` does not exist on disk. The test file (`web/tests/logger.test.ts`) tests a structured JSON logger, but the implementation module is not present. This means either the build fails (blocking deployment) or, if the module was somehow bundled previously, all server-side error logging is silent in production, and you will have zero visibility into API failures.

**Recommended fix:** Create `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/logger.ts`:
```typescript
type LogLevel = 'info' | 'warn' | 'error';

function log(level: LogLevel, msg: string, meta?: Record<string, unknown>) {
  const entry = { level, msg, ts: new Date().toISOString(), ...meta };
  const fn = level === 'error' ? console.error : level === 'warn' ? console.warn : console.log;
  fn(JSON.stringify(entry));
}

export const logger = {
  info: (msg: string, meta?: Record<string, unknown>) => log('info', msg, meta),
  warn: (msg: string, meta?: Record<string, unknown>) => log('warn', msg, meta),
  error: (msg: string, meta?: Record<string, unknown>) => log('error', msg, meta),
};
```

---

## HIGH Issues

### H1. No Retry Logic or Timeout Handling on Polygon/Tradier API Calls

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/polygon_provider.py`, lines 28-35
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/tradier_provider.py`, lines 33-41
**Severity:** HIGH

```python
# polygon_provider.py
def _get(self, path: str, params: Optional[Dict] = None, timeout: int = 10) -> Dict:
    params = params or {}
    params["apiKey"] = self.api_key
    url = f"{self.base_url}{path}"
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
```

**Risk scenario:** A single transient 503 or network blip from Polygon or Tradier kills the entire scan for that ticker. No retry, no backoff. During market hours when data is most critical, a brief API hiccup means missed opportunities. The pagination loops in `get_expirations()`, `get_options_chain()`, and `get_full_chain()` (lines 69-77, 98-104, 163-169) all make raw `requests.get()` calls without retry as well.

**Recommended fix:** Use `requests.adapters.HTTPAdapter` with `urllib3.util.retry.Retry`:
```python
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))
```

---

### H2. Alpaca Trade Execution: No Verification After Order Submission

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py`, lines 205-225
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/alpaca_provider.py`, lines 185-233
**Severity:** HIGH

```python
# paper_trader.py
if self.alpaca:
    try:
        alpaca_result = self.alpaca.submit_credit_spread(...)
        trade["alpaca_order_id"] = alpaca_result.get("order_id")
        trade["alpaca_status"] = alpaca_result.get("status")
        if alpaca_result["status"] == "error":
            logger.warning(f"Alpaca order failed: ...")
        else:
            logger.info(f"Alpaca order submitted: {alpaca_result['order_id']}")
    except Exception as e:
        logger.warning(f"Alpaca submission failed, recording in JSON: {e}")
```

**Risk scenario:** The order is submitted with status "submitted" but there is no follow-up to verify it was actually filled. The paper trade is recorded as "open" regardless of whether the order was filled, partially filled, or rejected by Alpaca. When closing, if the open order was never filled, the close order will also fail, but the JSON record shows a position that doesn't actually exist. This creates a phantom position that will never be properly closed.

**Recommended fix:** Add a post-submission verification step:
```python
import time

if alpaca_result.get("status") == "submitted":
    # Poll for fill confirmation (with timeout)
    for _ in range(10):
        time.sleep(2)
        status = self.alpaca.get_order_status(alpaca_result["order_id"])
        if status["status"] in ("filled", "partially_filled"):
            trade["alpaca_status"] = status["status"]
            break
        elif status["status"] in ("canceled", "expired", "rejected"):
            logger.error(f"Order {alpaca_result['order_id']} was {status['status']}")
            trade["alpaca_status"] = status["status"]
            break
```

---

### H3. Alpaca Close Failure Doesn't Prevent Local Trade From Being Marked Closed

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py`, lines 344-359
**Severity:** HIGH

```python
def _close_trade(self, trade: Dict, pnl: float, reason: str):
    if self.alpaca and trade.get("alpaca_order_id"):
        try:
            self.alpaca.close_spread(...)
            logger.info(f"Alpaca close order submitted for {trade['ticker']}")
        except Exception as e:
            logger.warning(f"Alpaca close failed: {e}")

    trade["status"] = "closed"   # <-- Always executes regardless of Alpaca failure
```

**Risk scenario:** If the Alpaca close order fails, the local JSON shows the position as closed, but on Alpaca the position is still open. This creates a desynchronized state where the system stops monitoring the position but it remains live on the broker, exposed to unlimited further loss until manual intervention.

**Recommended fix:** Only mark as closed if Alpaca close succeeds, or at minimum flag the trade:
```python
if self.alpaca and trade.get("alpaca_order_id"):
    try:
        result = self.alpaca.close_spread(...)
        if result.get("status") == "error":
            trade["close_sync_error"] = result.get("message")
            logger.critical(f"ALPACA CLOSE FAILED for {trade['ticker']} - position may still be open on broker!")
    except Exception as e:
        trade["close_sync_error"] = str(e)
        logger.critical(f"ALPACA CLOSE EXCEPTION for {trade['ticker']}: {e}")
```

---

### H4. Command Injection via API Scan/Backtest Routes

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/scan/route.ts`, lines 17-24
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts`, lines 19-25
**Severity:** HIGH

```typescript
const command = "python3 main.py scan";
const { stdout, stderr } = await execPromise(command, {
    cwd: pythonDir,
    timeout: 120000,
});
```

While the current code uses hardcoded commands, the scan route returns raw `stdout` and `stderr` in the response, potentially leaking system information (file paths, Python tracebacks, environment details). Additionally, if parameters are ever added to these endpoints (e.g., a ticker parameter), the pattern of using `exec` invites command injection.

**Recommended fix:** Do not return raw `stdout`/`stderr` to clients. Filter or omit them in the response. If parameters must be passed, use `execFile` with an argument array instead of string interpolation.

---

### H5. SWR Hooks Do Not Check HTTP Response Status

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/hooks.ts`, lines 1-24
**Severity:** HIGH

```typescript
const fetcher = (url: string) => fetch(url).then(res => res.json())
```

**Risk scenario:** If the API returns a 401 (auth middleware denies), 500, or 503, the `fetcher` parses the JSON error body and treats it as valid data. The SWR hook returns this error object as `data` rather than `error`. Components downstream like `HomePage` do `alertsData?.alerts || alertsData?.opportunities || []`, which will silently produce an empty array, showing "No alerts" rather than an error state. The user sees an empty dashboard and thinks there are no opportunities, when in fact the system is broken.

**Recommended fix:**
```typescript
const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
};
```

---

### H6. Config POST Overwrites Entire File With Partial Data (Data Loss)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/config/route.ts`, lines 106-121
**Severity:** HIGH

```typescript
export async function POST(request: Request) {
  try {
    const body = await request.json()
    const parsed = ConfigSchema.safeParse(body)
    // ...
    const yamlStr = yaml.dump(parsed.data)
    await fs.writeFile(configPath, yamlStr, 'utf-8')
```

**Risk scenario:** The `ConfigSchema` uses `.optional()` for every field and `.passthrough()`. A POST with `{ "tickers": ["SPY"] }` will overwrite the entire config.yaml, destroying all strategy parameters, risk settings, API keys, and logging configuration. Since `parsed.data` only contains what was sent, everything else is lost.

**Recommended fix:** Merge the incoming partial config with the existing config:
```typescript
const existingData = yaml.load(await fs.readFile(configPath, 'utf-8'));
const merged = deepMerge(existingData, parsed.data);
await fs.writeFile(configPath, yaml.dump(merged), 'utf-8');
```

---

## MEDIUM Issues

### M1. Bare `except:` in Feature Engine Earnings Date Parsing

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py`, line 332
**Severity:** MEDIUM

```python
except:
    features['days_to_earnings'] = 999
```

**Risk scenario:** Any exception type is silently swallowed, including `KeyboardInterrupt`, `SystemExit`, or an `AttributeError` indicating a bug. A code bug here would be invisible in production.

**Recommended fix:** `except Exception as e:` with `logger.debug(f"...")`.

---

### M2. Division by Zero Not Guarded in RSI Calculation

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py`, lines 120-124
**Severity:** MEDIUM

```python
rs = gain / loss
rsi = 100 - (100 / (1 + rs))
```

**Risk scenario:** If `loss` is zero (e.g., 14 consecutive up days), `rs` becomes `inf`, and RSI correctly evaluates to 100. However, if both `gain` and `loss` are zero (no price movement for 14 days -- rare but possible on illiquid securities), `rs` is `NaN`, and RSI is `NaN`, which will propagate through technical signals causing downstream `KeyError` or comparison failures.

**Recommended fix:**
```python
rs = gain / loss.replace(0, np.nan)
rsi = 100 - (100 / (1 + rs))
rsi = rsi.fillna(50)  # Default RSI when no movement
```

---

### M3. `_analyze_trend` Mutates Input DataFrame

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py`, lines 83-85
**Severity:** MEDIUM

```python
price_data['MA_fast'] = price_data['Close'].rolling(window=fast_period).mean()
price_data['MA_slow'] = price_data['Close'].rolling(window=slow_period).mean()
```

**Risk scenario:** The `price_data` DataFrame passed from `_analyze_ticker()` is the same object used for other purposes (e.g., `current_price = price_data['Close'].iloc[-1]`). While adding columns doesn't break that, it pollutes the DataFrame with extra columns on every call, and if `_analyze_trend` is called multiple times, columns accumulate. In pandas, adding columns to a view can trigger `SettingWithCopyWarning`.

**Recommended fix:** Operate on a copy:
```python
df = price_data.copy()
df['MA_fast'] = df['Close'].rolling(window=fast_period).mean()
```

---

### M4. No Rate Limiting or Backoff When yfinance Fails Repeatedly

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/feature_engine.py`, lines 139, 207, 271, 284
**Severity:** MEDIUM

The `FeatureEngine` makes 4 separate `yf.download()` calls per ticker analyzed (technical features, volatility features, VIX, SPY). These are all independent network calls with no caching, no batching, and no rate limiting. For a scan of 3 tickers, this is 12+ yfinance calls. Yahoo Finance actively throttles and can return empty DataFrames or raise exceptions when rate limited.

**Recommended fix:** Add caching at the class level:
```python
def _get_cached_data(self, ticker: str, period: str) -> pd.DataFrame:
    cache_key = f"{ticker}_{period}"
    if cache_key in self.feature_cache:
        age = (datetime.now() - self.cache_timestamps.get(cache_key, datetime.min)).seconds
        if age < 300:  # 5 minute cache
            return self.feature_cache[cache_key]
    data = yf.download(ticker, period=period, progress=False)
    self.feature_cache[cache_key] = data
    self.cache_timestamps[cache_key] = datetime.now()
    return data
```

---

### M5. Backtest Profit Factor Division by Zero

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/backtest/backtester.py`, line 390
**Severity:** MEDIUM

```python
'profit_factor': round(abs(winners['pnl'].sum() / losers['pnl'].sum()), 2) if len(losers) > 0 else 0,
```

**Risk scenario:** If `losers['pnl'].sum()` is exactly 0.0 (e.g., break-even trades classified as losers), this will produce a `ZeroDivisionError` despite the `len(losers) > 0` guard.

**Recommended fix:**
```python
losers_sum = losers['pnl'].sum()
'profit_factor': round(abs(winners['pnl'].sum() / losers_sum), 2) if losers_sum != 0 else float('inf'),
```

---

### M6. Rebalance Positions Division by Zero

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/position_sizer.py`, line 349
**Severity:** MEDIUM

```python
if abs(recommended_size - current_size) / current_size > 0.20:
```

**Risk scenario:** If `current_size` is 0 (which can happen when a position has been reduced to zero), this raises `ZeroDivisionError`.

**Recommended fix:** Guard with `if current_size > 0 and abs(...) / current_size > 0.20:`.

---

### M7. Middleware Auth Token Comparison Is Not Timing-Safe

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/middleware.ts`, line 24
**Severity:** MEDIUM

```typescript
if (!token || token !== expectedToken) {
```

**Risk scenario:** String comparison in JavaScript is not constant-time. An attacker could theoretically perform a timing attack to recover the API token character by character.

**Recommended fix:** Use `crypto.timingSafeEqual`:
```typescript
import { timingSafeEqual } from 'crypto';

const tokensMatch = token && Buffer.byteLength(token) === Buffer.byteLength(expectedToken) &&
    timingSafeEqual(Buffer.from(token), Buffer.from(expectedToken));
```

---

### M8. Paper Trade File Lock Is In-Memory Only (Multi-Instance Unsafe)

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts`, lines 43-51
**Severity:** MEDIUM

```typescript
const fileLocks = new Map<string, Promise<void>>();
function withLock<T>(userId: string, fn: () => Promise<T>): Promise<T> {
```

**Risk scenario:** In a multi-instance deployment (Railway scales to multiple instances, Vercel uses multiple lambdas), the in-memory lock provides zero protection. Two instances could simultaneously read, modify, and write the same user's JSON file, causing lost writes.

**Recommended fix:** For file-based storage, use `proper-lockfile` or `lockfile` npm package for filesystem-level locking. Better yet, migrate to a database with proper transactions.

---

### M9. Pickle Model Loading Is Insecure

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/ml/signal_model.py`, lines 422-424
**Severity:** MEDIUM

```python
with open(filepath, 'rb') as f:
    model_data = pickle.load(f)
```

**Risk scenario:** If the `ml/models/` directory is ever writable by untrusted code (e.g., via a deployment vulnerability or shared filesystem), a malicious pickle file can execute arbitrary code. This is a known Python security issue.

**Recommended fix:** Use `joblib` or `safetensors` for model serialization, or at minimum validate the file hash before loading. Add a comment documenting the trust boundary.

---

## LOW Issues

### L1. Telegram Bot `send_alert` Has No Timeout

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/alerts/telegram_bot.py`, lines 84-89
**Severity:** LOW

The `bot.send_message()` call has no explicit timeout, potentially blocking indefinitely if Telegram's API is slow.

---

### L2. Empty Catch Blocks in Frontend Components

**Files:** Multiple frontend files with `} catch {` empty blocks:
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/components/sidebar/ai-chat.tsx`, line 70
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/components/sidebar/heatmap.tsx`, line 33
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/my-trades/page.tsx`, line 53
**Severity:** LOW

These suppress errors silently. While less critical in UI code, they make debugging difficult.

---

### L3. No Market Hours Check

**Severity:** LOW

The system will scan and generate alerts on weekends and holidays, fetching stale data from yfinance and potentially opening paper trades on Friday-after-close data that won't be actionable until Monday. There is no check for market hours anywhere in the codebase.

---

### L4. `consolidate_levels` Potential Division by Zero

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/strategy/technical_analysis.py`, line 227
**Severity:** LOW

```python
if abs(level - consolidated[-1]) / consolidated[-1] > threshold:
```

If `consolidated[-1]` is 0 (theoretically possible if a price level is 0), this would raise `ZeroDivisionError`.

---

### L5. Error Boundaries Don't Log to Server

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/error.tsx`
**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/global-error.tsx`
**Severity:** LOW

Neither error boundary reports errors to any server-side logging or monitoring service. Client-side errors are invisible to operators.

---

### L6. Health Endpoint Does Not Check Backend Dependencies

**File:** `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/health/route.ts`
**Severity:** LOW

```typescript
export async function GET() {
  return NextResponse.json({ status: 'ok', ... })
}
```

The health check always returns "ok" regardless of whether the Python backend, data files, or config are accessible. A deployment health check based on this endpoint will pass even if the system is functionally broken.

---

## Summary of Positive Findings

The codebase does several things well:

1. **ML pipeline graceful fallback** (`main.py:67-75`): ML failure doesn't kill the system.
2. **Per-ticker error isolation** (`main.py:145-216`): One ticker failing doesn't abort the entire scan.
3. **Options chain fallback chain** (`options_analyzer.py:54-72`): Tradier -> Polygon -> yfinance cascading providers.
4. **Zod validation on POST routes** (`paper-trades/route.ts:9-29`): Input validation with proper 400 responses.
5. **Rate limiting on chat endpoint** (`chat/route.ts:16-36`): In-memory rate limiter with lazy cleanup.
6. **Concurrency guards on scan/backtest** (`scan/route.ts:9`, `backtest/run/route.ts:11`): Prevents concurrent executions.
7. **Retry wrapper on frontend API calls** (`api.ts:141-161`): Client-side retry for 500/503 errors.
8. **React error boundaries** (`error.tsx`, `global-error.tsx`): Catches rendering errors at both segment and root level.
9. **Secret stripping in config API** (`config/route.ts:8-23`): API keys are redacted before sending to client.
10. **NaN guarding in P&L calculation** (`pnl.ts:45`): Explicit `isNaN` check prevents NaN from propagating.

---

## Priority Remediation Order

1. **C4** - Create the missing `logger.ts` module (blocks deployment or silences all API logging)
2. **C3** - Add atomic writes + corrupted file recovery for trade JSON files
3. **C1** - Fix bare `except: pass` in price fetch loop
4. **C2** - Fix bare `except:` in expiration parsing with proper logging
5. **H3** - Prevent local close when Alpaca close fails
6. **H5** - Fix SWR fetcher to throw on non-OK responses
7. **H6** - Merge partial config instead of overwriting
8. **H1** - Add retry logic to Polygon/Tradier providers
9. **H2** - Add order fill verification after Alpaca submission
10. **H4** - Remove raw stdout/stderr from API responses

---

<a id="panel-6-testing"></a>

# Testing & Test Coverage Review Report
## Attix Credit Spreads

---

## 1. EXECUTIVE SUMMARY

| Metric | Value |
|--------|-------|
| Python source lines | ~7,708 |
| TypeScript/TSX source lines | ~3,183 |
| Test lines (TS only) | ~1,469 |
| Python test files | **0** |
| Frontend test files | 22 |
| Estimated frontend line coverage | ~20-30% |
| Estimated Python coverage | **0%** |
| CI/CD pipeline | **None** |

---

## 2. TEST COVERAGE ASSESSMENT

### Severity: CRITICAL

**Python Backend: 0% coverage.** There are zero Python test files in the entire repository. No `conftest.py`, no `pytest.ini`, no `pyproject.toml`, no test directory in the Python project root. The `requirements.txt` lists `pytest>=7.4.0` and `pytest-cov>=4.1.0` as optional dependencies, but no tests exist.

- File: `/home/pmcerlean/projects/pilotai-credit-spreads/requirements.txt`, lines 39-40 -- pytest is listed but never used.

**Frontend: Partial coverage (~25-30% estimated).** The `web/tests/` directory contains 22 test files covering:

**Covered:**
- `lib/pnl.ts` -- well-tested with edge cases
- `lib/paper-trades.ts` -- generateTradeId, shouldAutoClose, calculatePortfolioStats, calculateUnrealizedPnL
- `lib/utils.ts` -- formatCurrency, formatPercent, formatDate, formatDateTime, getScoreColor, getScoreBgColor
- `middleware.ts` -- auth token checks
- `components/ui/badge.tsx`, `button.tsx`, `card.tsx` -- basic rendering
- API routes: health, alerts (GET), backtest (GET), config (GET/POST), chat (POST), positions (GET)
- Error boundary pages

**Not covered at all:**
- `lib/api.ts` -- the `apiFetch` retry logic (lines 141-161) has zero tests; only type contracts are tested
- `lib/hooks.ts` -- no tests for `useAlerts`, `usePositions`, `usePaperTrades`
- `lib/user-id.ts` -- no tests for `getUserId`, `clearUserId`
- `lib/mockData.ts` -- no tests (less critical)
- `app/api/paper-trades/route.ts` -- 238 lines, zero integration tests for POST/DELETE handlers
- `app/api/scan/route.ts` -- zero tests (executes python subprocess)
- `app/api/trades/route.ts` -- zero tests
- `app/api/backtest/run/route.ts` -- zero tests (executes python subprocess)
- All page components (`page.tsx`, `paper-trading/page.tsx`, `positions/page.tsx`, `backtest/page.tsx`, `my-trades/page.tsx`, `settings/page.tsx`) -- zero tests (1,183 lines total)
- All business-logic components: `alert-card.tsx` (225 lines), `ai-chat.tsx` (263 lines), `live-positions.tsx` (131 lines), `heatmap.tsx`, `performance-card.tsx`
- UI components: `table.tsx`, `tabs.tsx`, `input.tsx`, `label.tsx`

---

## 3. TEST QUALITY ANALYSIS

### Severity: MEDIUM

**Strengths:**
- The P&L calculation tests (`pnl.test.ts`, `pnl-calc.test.ts`) are genuinely robust -- they test edge cases like zero values, expired trades, NaN guards, and boundary conditions. See `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/pnl.test.ts`, lines 85-95 for comprehensive edge case testing.
- `paper-trades-lib.test.ts` properly tests portfolio stats calculations including profitFactor, avgWin, avgLoss, openRisk.
- The middleware test (`middleware.test.ts`) properly mocks NextRequest/NextResponse and tests auth bypass for health endpoint.

**Weaknesses:**

1. **Several tests verify only structure, not behavior.**
   - `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/health.test.ts` -- This test reads the file system to check that strings exist in source code. It verifies `content.toContain('export async function GET')` (line 13). This is a source-code-scan test, not a behavior test. The integration test at `tests/integration/health.test.ts` is better.
   
   - `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/error-boundary.test.ts` -- Similarly reads source files and checks for string contents (line 13: `expect(content).toContain("'use client'")`). The companion `.tsx` file is a proper render test.

   - `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/dockerfile.test.ts` -- Checks for `FROM`, `npm`, `EXPOSE` strings in Dockerfile. This is a CI lint check masquerading as a test.

2. **The rate-limit test tests a standalone reimplementation, not actual code.**
   - `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/rate-limit.test.ts` -- This test defines its own `createRateLimiter` function (lines 4-27) and tests that. There is no actual rate limiter in the codebase that this test validates. The test passes regardless of whether the application implements rate limiting.

3. **The paper-trades validation test tests standalone functions, not the actual route handler.**
   - `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/paper-trades.test.ts` -- Lines 6-16 define `validateTradeInput` and `buildTrade` locally. The actual validation in `app/api/paper-trades/route.ts` uses a Zod schema (`PostTradeSchema`, lines 9-29). The test does not exercise the real code.

4. **The config-validation test recreates the Zod schema instead of importing it.**
   - `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/config-validation.test.ts` -- Lines 5-32 redefine the config schema. If the actual schema in `app/api/config/route.ts` drifts from this copy, the test becomes meaningless.

5. **The API helpers test only verifies TypeScript type instantiation.**
   - `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/api-helpers.test.ts` -- Tests that you can create objects conforming to interfaces. Does not test `apiFetch`, `fetchAlerts`, `fetchPositions`, or any actual network logic.

---

## 4. MISSING TEST CATEGORIES

### 4a. Python Unit Tests -- Severity: CRITICAL

Zero Python tests exist for 7,708 lines of critical trading logic:

| Module | Lines | What needs testing |
|--------|-------|--------------------|
| `strategy/spread_strategy.py` | 426 | `evaluate_spread_opportunity`, `_find_bull_put_spreads`, `_find_bear_call_spreads`, `_score_opportunities`, `calculate_position_size`, `_calculate_pop` |
| `strategy/technical_analysis.py` | 230 | `_analyze_trend`, `_analyze_rsi`, `_find_support_levels`, `_find_resistance_levels`, `_consolidate_levels` |
| `strategy/options_analyzer.py` | 289 | Options chain parsing, IV calculation |
| `ml/signal_model.py` | 607 | Model training, prediction, calibration |
| `ml/position_sizer.py` | 449 | Kelly Criterion calculation (`_calculate_kelly`), portfolio constraints, rebalancing |
| `ml/regime_detector.py` | 400 | HMM-based regime detection |
| `ml/feature_engine.py` | 567 | Feature building pipeline |
| `ml/iv_analyzer.py` | 415 | IV surface analysis |
| `ml/sentiment_scanner.py` | 544 | Event risk scanning |
| `ml/ml_pipeline.py` | 539 | Pipeline orchestration, `_calculate_enhanced_score`, `_generate_recommendation` |
| `paper_trader.py` | 449 | `execute_signals`, `_open_trade`, `check_positions`, `_evaluate_position`, `_close_trade` |
| `backtest/backtester.py` | 400 | Full backtesting engine |
| `backtest/performance_metrics.py` | 149 | Sharpe ratio, drawdown, profit factor |
| `tracker/trade_tracker.py` | 247 | Trade tracking |
| `alerts/alert_generator.py` | 234 | Alert generation |
| `alerts/telegram_bot.py` | 164 | Telegram integration |
| `utils.py` | 150 | `load_config`, `setup_logging`, `validate_config` |

### 4b. Frontend Integration Tests -- Severity: HIGH

Missing integration tests for:

- **`/api/paper-trades` POST handler** (`/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts`, lines 121-190) -- Opening a paper trade. This is the most critical user-facing write operation. Tests needed: validation rejection, max position limit, duplicate detection, trade field calculation (max_profit, max_loss, profit_target, stop_loss).

- **`/api/paper-trades` DELETE handler** (lines 193-238) -- Closing a paper trade. Tests needed: missing trade ID, trade not found, already-closed trade, P&L calculation on close, status assignment based on reason.

- **`/api/paper-trades` GET handler** (lines 77-118) -- Fetching trades with computed unrealized P&L. Tests needed: PAPER_TRADING_ENABLED=false path.

- **`/api/scan/route.ts`** -- Concurrency guard, child process execution, timeout handling.

- **`/api/backtest/run/route.ts`** -- Concurrency guard, child process execution.

### 4c. Frontend Component Tests -- Severity: MEDIUM

No component tests for:
- `alert-card.tsx` (225 lines) -- Renders trade alerts with interactive paper trading buttons
- `ai-chat.tsx` (263 lines) -- Chat interface with message handling
- `live-positions.tsx` (131 lines) -- Position table with P&L display
- `performance-card.tsx` -- Portfolio stats display
- `heatmap.tsx` -- Visual data display
- All page-level components (6 pages, 1,183 lines)

### 4d. Hook Tests -- Severity: MEDIUM

No tests for custom hooks in `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/hooks.ts`:
- `useAlerts()` -- SWR configuration, refresh interval, dedup interval
- `usePositions()` -- SWR configuration
- `usePaperTrades()` -- SWR configuration, userId parameter passing

### 4e. E2E Tests -- Severity: MEDIUM

No end-to-end tests exist. No Playwright or Cypress configuration found. For a trading system, critical E2E flows would be:
- View alerts -> Open paper trade -> See position in portfolio -> Close position
- Configure strategy -> Run scan -> View results
- Run backtest -> View equity curve and stats

---

## 5. MOCK DATA vs REAL DATA

### Severity: MEDIUM

**Good:** The PnL and portfolio stat tests use realistic credit spread data with real-world values (SPY at $480, 5-wide spreads, $1.50 credit, 30 DTE). The mock trade helper function (`makeTrade()`) produces trades with internally consistent fields (max_profit = credit * 100 * contracts, max_loss = (spread_width - credit) * 100 * contracts).

**Problematic:**

1. **Config integration test mocks fs to return hardcoded YAML** (`/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/integration/config.test.ts`, lines 7-31). This means the test never validates that real `config.yaml` can be parsed. The mock includes secrets (`real-secret-key`) that should be redacted, which is what the test verifies -- but the mock is tightly coupled to the specific redaction logic.

2. **Backtest and positions integration tests mock `fs.readFile` to throw ENOENT** (e.g., `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/integration/backtest.test.ts`, lines 7-15). This only tests the empty/default case. There are no tests with populated data files.

3. **No mock for the chat API's Anthropic/OpenAI call** -- The chat route test (`/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/integration/chat.test.ts`) only tests the fallback path (no API key set). There are zero tests for the path where an actual LLM API key is configured and a real API call would be made.

---

## 6. CRITICAL UNTESTED PATHS

### Severity: CRITICAL

These are the highest-risk untested code paths in the system:

1. **Kelly Criterion Position Sizing** (`/home/pmcerlean/projects/pilotai-credit-spreads/ml/position_sizer.py`, lines 147-185)
   - `_calculate_kelly()` implements the Kelly formula. A bug here could lead to catastrophic over-sizing of positions. The formula `(p * b - q) / b` needs verification with known inputs/outputs. Edge cases: win_prob=0, win_prob=1, win_amount=0.

2. **Spread P&L Evaluation** (`/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py`, lines 280-342)
   - `_evaluate_position()` determines whether to close a trade. This contains the live P&L simulation model. Bugs here mean paper trades get closed at wrong prices. The ITM/OTM branching logic (lines 309-321) has complex arithmetic that could have off-by-one errors.

3. **Trade Execution and Position Limits** (`/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py`, lines 109-162)
   - `execute_signals()` applies max position limits, duplicate filtering, ticker concentration limits, and then opens trades. Bugs here could lead to over-allocation.

4. **Backtest P&L Calculation** (`/home/pmcerlean/projects/pilotai-credit-spreads/backtest/backtester.py`, lines 290-340)
   - `_close_position()` and `_calculate_results()` compute backtest performance. `return_pct` calculation on line 335 divides by `max_loss * contracts * 100` -- this could divide by zero if max_loss is 0.

5. **ML Enhanced Score** (`/home/pmcerlean/projects/pilotai-credit-spreads/ml/ml_pipeline.py`, lines 229-295)
   - `_calculate_enhanced_score()` combines ML predictions, regime data, IV analysis, and event risk into a 0-100 score that drives trading decisions. This is the core decision-making function.

6. **Paper Trade API - POST/DELETE Handlers** (`/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts`)
   - POST (line 121): Creates new paper trades with Zod validation, duplicate checking, position limits.
   - DELETE (line 193): Closes trades and calculates realized P&L. The `withLock` mutex pattern (lines 45-51) for concurrent access is untested.

7. **Spread Strategy Scoring** (`/home/pmcerlean/projects/pilotai-credit-spreads/strategy/spread_strategy.py`, lines 333-398)
   - `_score_opportunities()` combines credit score, risk/reward score, POP score, technical alignment, and IV score. Each component has specific weighting and capping logic that needs validation.

---

## 7. TEST INFRASTRUCTURE

### Severity: HIGH

1. **No CI/CD pipeline.** There is no `.github/workflows/` directory, no `Jenkinsfile`, no `gitlab-ci.yml`, no `Dockerfile` test stage. Tests are never run automatically on commits or PRs.

2. **No Python test infrastructure.** No `conftest.py`, `pytest.ini`, `pyproject.toml`, or `setup.cfg`. The project would need a test directory structure, fixtures, and mock factories.

3. **Missing logger module.** Multiple source files and the logger test import `@/lib/logger`, but `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/logger.ts` does not exist. The logger test (`/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/logger.test.ts`) likely fails at import time. This could cause cascading test failures in integration tests that import routes depending on the logger.

4. **Coverage reporting is configured but likely not run.** The `vitest.config.ts` (line 15-18) has coverage configured with `v8` provider, but there is no `test:coverage` script in `package.json`. Coverage is not collected by default with `vitest run`.

5. **No test:coverage script.** `package.json` only has `"test": "vitest run"` and `"test:watch": "vitest"`. There should be a `"test:coverage": "vitest run --coverage"` script.

6. **Duplicate error-boundary tests.** Both `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/error-boundary.test.ts` and `error-boundary.test.tsx` exist. The `.ts` version is a file-existence check; the `.tsx` version is a proper render test. The `.ts` version is redundant.

---

## 8. ADDITIONAL FINDINGS

### Missing File Issue (Severity: HIGH)
The file `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/logger.ts` does not exist, yet it is imported by:
- `app/api/positions/route.ts` (line 1)
- `app/api/trades/route.ts` (line 1)
- `app/api/config/route.ts` (line 1)
- `app/api/paper-trades/route.ts` (line 1)
- `app/api/backtest/route.ts` (line 1)
- `app/api/backtest/run/route.ts` (line 1)
- `app/api/chat/route.ts` (line 1)
- `tests/logger.test.ts` (line 2)

This means the logger test and several integration tests would fail. The logger may have been deleted or moved without updating imports.

### Tests Testing Their Own Reimplementations (Severity: MEDIUM)
Three test files test local reimplementations rather than actual source code:
1. `rate-limit.test.ts` -- tests a self-contained rate limiter, not any application code
2. `paper-trades.test.ts` -- tests local `validateTradeInput`/`buildTrade`, not the Zod schema in the route
3. `config-validation.test.ts` -- tests a local copy of the Zod schema, not the actual route's schema

---

## 9. PRIORITIZED RECOMMENDATIONS

Listed in order of priority (highest first):

### Priority 1: CRITICAL -- Fix broken infrastructure
1. **Create `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/logger.ts`** or fix imports. Multiple tests and all API routes depend on this module.
2. **Add `test:coverage` script** to `package.json`: `"test:coverage": "vitest run --coverage"`
3. **Add CI/CD pipeline** (`.github/workflows/test.yml`) that runs both Python and JS tests on every push/PR.

### Priority 2: CRITICAL -- Add Python test infrastructure and core tests
4. **Create Python test infrastructure**: `tests/conftest.py` with shared fixtures, `pytest.ini` or `pyproject.toml` config.
5. **Test `ml/position_sizer.py::_calculate_kelly()`** -- This is a pure mathematical function with known correct answers. Test with: `win_prob=0.65, win_amount=0.30, loss_amount=1.0` should yield `Kelly = (0.65*0.3 - 0.35)/0.3 = 0.3`
6. **Test `strategy/spread_strategy.py::_score_opportunities()`** -- Feed known opportunities and verify score components and ranking.
7. **Test `strategy/spread_strategy.py::_calculate_pop()`** -- Pure function: `delta=0.15` should yield `POP=85.0`
8. **Test `paper_trader.py::_evaluate_position()`** -- Test OTM and ITM scenarios, time decay, profit target, stop loss, and expiration triggers.
9. **Test `strategy/technical_analysis.py::_analyze_trend()`** and `_analyze_rsi()` -- Feed synthetic DataFrames with known patterns.

### Priority 3: HIGH -- Add frontend API route integration tests
10. **Test `/api/paper-trades` POST** -- Import the actual route handler, mock `fs`, test: valid trade creation, Zod validation rejection, max position limit, duplicate detection.
11. **Test `/api/paper-trades` DELETE** -- Test: missing ID, trade not found, already closed, P&L snapshot on close.
12. **Refactor `paper-trades.test.ts`** to import and test the actual Zod schema from the route, not a local copy.
13. **Refactor `config-validation.test.ts`** to import the actual schema from the config route.
14. **Remove or refactor `rate-limit.test.ts`** -- Either implement a rate limiter and test it, or remove this phantom test.

### Priority 4: HIGH -- Add Python integration tests
15. **Test `paper_trader.py::execute_signals()`** end-to-end with mock data.
16. **Test `ml/ml_pipeline.py::_calculate_enhanced_score()`** with known analysis dictionaries.
17. **Test `ml/ml_pipeline.py::_generate_recommendation()`** with boundary scores (49.9, 50, 60, 75).
18. **Test `backtest/backtester.py::_calculate_results()`** with known trade lists.

### Priority 5: MEDIUM -- Add frontend component and hook tests
19. **Test `lib/hooks.ts`** -- Mock SWR, verify correct URLs and refresh intervals.
20. **Test `lib/api.ts::apiFetch`** -- Mock `fetch`, test retry on 500/503, test max retries, test success path.
21. **Test `lib/user-id.ts`** -- Mock localStorage, test ID generation and persistence.
22. **Test `components/alerts/alert-card.tsx`** -- Render with mock alert data, verify paper trade button interaction.
23. **Test `components/positions/live-positions.tsx`** -- Render with mock position data.

### Priority 6: LOW -- Cleanup and E2E
24. **Remove duplicate `error-boundary.test.ts`** -- The `.tsx` version covers everything the `.ts` version does and more.
25. **Remove `dockerfile.test.ts`** -- This is a build-system lint check, not a meaningful test. Move checks to CI pipeline.
26. **Add Playwright E2E tests** for the critical user flow: view alerts, open paper trade, verify in positions, close trade.
27. **Add `tests/integration/paper-trades.test.ts`** with actual file system mock for populated portfolios (not just empty/ENOENT).

---

<a id="panel-7-production-readiness"></a>

# Production Readiness Review: Attix Credit Spreads

**Review Date:** 2026-02-14
**Reviewer:** Claude Opus 4.6 (Automated DevOps Review)
**Codebase:** `/home/pmcerlean/projects/pilotai-credit-spreads`
**Stack:** Python backend (strategy/ML/paper-trading) + Next.js 14 frontend (dashboard/API routes), deployed on Railway

---

## 1. DEPLOYMENT CONFIGURATION

### Severity: CRITICAL

**1a. Dockerfile is missing**

The Dockerfile was committed in `cd1482d` ("Dockerfile + standalone Next.js for Railway") but has since been deleted from the working tree. No `Dockerfile` exists anywhere in the repository. The existing test at `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/dockerfile.test.ts` (lines 9-16) explicitly asserts `Dockerfile` exists and will fail.

Similarly, there is no `.dockerignore` file, despite the test at line 18-24 of the same file asserting one exists.

**1b. next.config.js is missing `output: 'standalone'`**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/next.config.js`
```js
const nextConfig = {
  reactStrictMode: true,
  eslint: {
    ignoreDuringBuilds: true,
  },
}
```
The `output: 'standalone'` setting, which is essential for Docker/Railway deployment (it bundles `node_modules` into a self-contained build), is absent. The test at `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/dockerfile.test.ts` line 29 checks for this and will fail. The commit message `cd1482d` mentions "standalone Next.js for Railway" but the configuration was removed or never committed to `main`.

**1c. No Railway configuration**

No `railway.toml`, `railway.json`, `nixpacks.toml`, or `Procfile` exists. Railway will try to auto-detect the build setup, but without explicit configuration, behavior is unpredictable -- especially since this is a monorepo with both Python and Node.js.

**1d. ESLint disabled during builds**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/next.config.js`, line 5
```js
eslint: { ignoreDuringBuilds: true }
```
This suppresses all lint errors during production builds, meaning broken code can ship silently.

---

## 2. ENVIRONMENT & SECRETS MANAGEMENT

### Severity: HIGH

**2a. Hardcoded placeholder secrets in config.yaml committed to git**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/config.yaml`
- Line 82: `bot_token: "YOUR_BOT_TOKEN_HERE"`
- Line 83: `chat_id: "YOUR_CHAT_ID_HERE"`
- Line 99: `api_key: "YOUR_TRADIER_API_KEY"`

While these are placeholders (not real keys), `config.yaml` is tracked in git and users may inadvertently commit real secrets by editing in place. Some keys use `${ENV_VAR}` syntax (lines 88-89, 103) which is better, but the pattern is inconsistent.

**2b. `python-dotenv` is not in requirements.txt**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/utils.py`, line 38
```python
from dotenv import load_dotenv
```
The `python-dotenv` package is imported but not listed in `/home/pmcerlean/projects/pilotai-credit-spreads/requirements.txt`. This will cause `ImportError` in fresh environments.

**2c. Only 3 environment variables documented**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/.env.example` has only:
```
ALPACA_API_KEY=your_alpaca_api_key
ALPACA_API_SECRET=your_alpaca_api_secret
POLYGON_API_KEY=your_polygon_api_key
```
File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/.env.example` has:
```
API_AUTH_TOKEN=
OPENAI_API_KEY=
PAPER_TRADING_ENABLED=true
```
There is no unified `.env.example` documenting ALL required environment variables across both stacks (Python + Node).

---

## 3. MONITORING & OBSERVABILITY

### Severity: HIGH

**3a. Frontend logger module is MISSING**

Seven API route files and one test file import `@/lib/logger`:
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/config/route.ts`, line 1
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/trades/route.ts`, line 1
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/positions/route.ts`, line 1
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts`, line 1
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts`, line 1
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/route.ts`, line 1
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts`, line 1

However, the file `/home/pmcerlean/projects/pilotai-credit-spreads/web/lib/logger.ts` does **not exist**. This means the build will fail with a module resolution error. Based on the test at `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/logger.test.ts`, the expected interface is a structured JSON logger with `.info()`, `.error()`, `.warn()` methods.

**3b. Health endpoint is minimal**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/health/route.ts`
```ts
return NextResponse.json({ 
  status: 'ok', 
  timestamp: new Date().toISOString(),
  version: process.env.npm_package_version || '1.0.0'
})
```
No dependency health checks (filesystem access, Python backend reachability, data file freshness). This means Railway's health checks will report "ok" even when the system is non-functional.

**3c. No error tracking service**

No Sentry, Datadog, or any APM integration. Errors are logged to console (when the logger exists) but never aggregated or alerted on. For a trading system where missed errors can mean financial loss, this is concerning.

**3d. Alerts route uses `console.log` instead of structured logger**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/alerts/route.ts`, line 32:
```ts
console.log("Failed to read alerts:", error);
```
This is inconsistent with all other API routes that use the `logger` import.

---

## 4. DATA PERSISTENCE

### Severity: CRITICAL

**4a. All trade data stored as JSON files on ephemeral filesystem**

Railway containers have ephemeral filesystems. On every deploy, restart, or scale event, all data is lost. The following critical data is stored as local files:

- Paper trade portfolios: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts`, line 36: `const DATA_DIR = path.join(process.cwd(), "data")` and per-user JSON files under `data/user_trades/`
- System paper trades: `/home/pmcerlean/projects/pilotai-credit-spreads/paper_trader.py`, line 18-19: `TRADES_FILE = DATA_DIR / "trades.json"` and `PAPER_LOG = DATA_DIR / "paper_trades.json"`
- Alert outputs: `/home/pmcerlean/projects/pilotai-credit-spreads/alerts/alert_generator.py`, line 33: `self.output_dir = Path('output')`
- Backtest results: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/route.ts`, line 9: reads from `../output/backtest_results.json`
- Configuration: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/config/route.ts`, line 115: `await fs.writeFile(configPath, yamlStr, 'utf-8')` writes to `../config.yaml`

**Every Railway deploy will wipe all user trades, positions, alerts, and backtest results.**

**4b. Multiple fallback paths for data files create confusion**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/positions/route.ts`, lines 26-30:
```ts
const content = await tryRead(
  path.join(cwd, 'data', 'paper_trades.json'),
  path.join(cwd, 'public', 'data', 'paper_trades.json'),
  path.join(cwd, '..', 'data', 'paper_trades.json'),
);
```
Three different paths are tried for the same file. This pattern appears in the alerts route too. It indicates uncertainty about the deployment layout and makes debugging data issues very difficult.

**4c. No database**

There is no PostgreSQL, Redis, SQLite, or any other persistent data store. For a trading system managing financial positions, this is a critical gap.

---

## 5. SECURITY HEADERS

### Severity: HIGH

**5a. No security headers configured**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/next.config.js` contains no `headers()` configuration. Missing:
- Content-Security-Policy (CSP)
- Strict-Transport-Security (HSTS)
- X-Frame-Options
- X-Content-Type-Options
- Referrer-Policy
- Permissions-Policy

**5b. No CORS configuration**

No CORS headers are set on any API route. The API routes accept requests from any origin.

**5c. Command injection risk in scan and backtest routes**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/scan/route.ts`, lines 1-2, 19-24:
```ts
import { exec } from "child_process";
const command = "python3 main.py scan";
const { stdout, stderr } = await execPromise(command, {
  cwd: pythonDir,
  timeout: 120000,
});
```
File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts`, lines 3-4, 22-25:
```ts
import { exec } from 'child_process';
const { stdout, stderr } = await execPromise('python3 main.py backtest', {
  cwd: systemPath,
  timeout: 300000,
});
```
While the commands are hardcoded (no user input concatenation), using `exec` from `child_process` in a web-facing API route is a security anti-pattern. The `exec` function spawns a shell, which increases the attack surface. These should use `execFile` instead, and stderr content should not be returned in API responses (lines 29, 33-39 in scan/route.ts expose internal system paths and error details).

**5d. Auth token comparison is not timing-safe**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/middleware.ts`, line 24:
```ts
if (!token || token !== expectedToken) {
```
Simple string comparison (`!==`) is vulnerable to timing attacks. Should use `crypto.timingSafeEqual()`.

**5e. Rate limiter is in-memory only**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts`, line 16:
```ts
const rateLimitMap = new Map<string, { count: number; resetAt: number }>();
```
This resets on every deploy/restart and doesn't work across multiple instances. An attacker can bypass it by triggering a redeploy.

---

## 6. CI/CD PIPELINE

### Severity: HIGH

**6a. No GitHub Actions workflows**

No `.github/` directory exists. There are no automated CI/CD pipelines for:
- Running tests on pull requests
- Linting/type checking
- Building and deploying
- Security scanning

**6b. No pre-commit hooks**

No `.pre-commit-config.yaml` or `husky` configuration exists. Nothing prevents committing broken code, secrets, or failing tests.

**6c. Tests will fail in CI**

The test at `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/dockerfile.test.ts` will fail because:
- No Dockerfile exists (line 11)
- No .dockerignore exists (line 19)
- `next.config.js` doesn't contain 'standalone' (line 29)

Additionally, the missing `web/lib/logger.ts` module means any test that imports an API route will fail at module resolution.

---

## 7. DEPENDENCY MANAGEMENT

### Severity: MEDIUM

**7a. Python dependencies use >= version ranges with no upper bounds**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/requirements.txt`
```
numpy>=1.24.0
pandas>=2.0.0
xgboost>=2.0.0
...
```
Every dependency uses `>=` without `<` upper bounds. A `pip install` could pull a breaking major version at any time. No `requirements.lock` or `pip freeze` output exists.

**7b. Missing `python-dotenv` dependency**

As noted in section 2b, `python-dotenv` is used in `utils.py` but not listed in `requirements.txt`.

**7c. `legacy-peer-deps=true` in .npmrc**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/.npmrc`
```
legacy-peer-deps=true
```
This suppresses peer dependency resolution errors, which can mask compatibility issues between packages (notably react@19 with next@14 and @testing-library/react@14).

**7d. React 19 with Next.js 14**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/package.json`, lines 26-27:
```json
"react": "^19.2.4",
"react-dom": "^19.2.4",
```
But Next.js is at `^14.2.0` (line 24). Next.js 14 officially supports React 18, not React 19. This mismatch is likely the reason `legacy-peer-deps=true` was added.

**7e. No vulnerability scanning configured**

No `npm audit`, `snyk`, `dependabot`, or `renovate` configuration exists.

---

## 8. SCALING & RELIABILITY

### Severity: HIGH

**8a. In-memory state prevents horizontal scaling**

Several modules use in-memory state that would be lost or inconsistent across multiple instances:

- Chat rate limiter: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts`, line 16
- Scan concurrency guard: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/scan/route.ts`, line 9: `let scanInProgress = false`
- Backtest concurrency guard: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/backtest/run/route.ts`, line 11: `let backtestInProgress = false`
- File lock map: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/paper-trades/route.ts`, line 43: `const fileLocks = new Map<string, Promise<void>>()`

With multiple Railway instances, two users could trigger concurrent scans or backtests, and rate limits would not apply across instances.

**8b. No graceful shutdown handling**

No signal handlers (`SIGTERM`, `SIGINT`) are configured in the Node.js application. Railway sends `SIGTERM` before killing containers. Without graceful shutdown, in-flight requests (especially long-running backtests with 300s timeout) will be abruptly terminated.

**8c. Shell-spawned Python processes**

The scan API (`/api/scan`) and backtest API (`/api/backtest/run`) spawn Python subprocesses via `child_process.exec`. These have timeouts of 120s and 300s respectively, but:
- No resource limits (memory, CPU) on the child process
- If the Node.js process is killed, the Python child may become orphaned
- No stdout/stderr streaming -- output is buffered in memory until completion, which could cause OOM for large backtest outputs

**8d. Unbounded rate limit map growth**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/chat/route.ts`, lines 23-26:
```ts
if (rateLimitMap.size > 1000) {
  for (const [key, val] of Array.from(rateLimitMap)) {
    if (now > val.resetAt) rateLimitMap.delete(key);
  }
}
```
Cleanup only triggers at 1000 entries, and it copies the entire map to an array. Under high load, memory can grow until cleanup triggers.

---

## 9. BACKUP & RECOVERY

### Severity: CRITICAL

**9a. No backup strategy for trade data**

All trade data is stored in local JSON files with no backup mechanism. There is no:
- Scheduled backup job
- External storage (S3, cloud storage)
- Database with WAL/replication
- Export/import functionality

The `sync-data.sh` script at `/home/pmcerlean/projects/pilotai-credit-spreads/web/sync-data.sh` copies files locally but provides no disaster recovery.

**9b. No data migration strategy**

If the JSON schema changes (e.g., new fields on `PaperTrade`), there is no migration path for existing data files. Old data files may cause runtime errors.

**9c. Config can be overwritten via API**

File: `/home/pmcerlean/projects/pilotai-credit-spreads/web/app/api/config/route.ts`, line 115:
```ts
await fs.writeFile(configPath, yamlStr, 'utf-8')
```
The POST endpoint overwrites `config.yaml` with parsed/validated data, but this strips comments and may lose sections not covered by the schema (due to `.passthrough()` on line 92). No backup is created before overwriting.

---

## 10. DOCUMENTATION

### Severity: LOW

**10a. Documentation exists but is incomplete**

The following docs exist:
- `/home/pmcerlean/projects/pilotai-credit-spreads/README.md` (general overview)
- `/home/pmcerlean/projects/pilotai-credit-spreads/QUICKSTART.md` (setup guide)
- `/home/pmcerlean/projects/pilotai-credit-spreads/TESTING.md` (test guide)
- `/home/pmcerlean/projects/pilotai-credit-spreads/CODE_REVIEW.md` (code review)
- `/home/pmcerlean/projects/pilotai-credit-spreads/web/README.md` (frontend)

Missing:
- No API documentation (OpenAPI/Swagger or equivalent)
- No architecture diagram
- No runbook for incident response
- No deployment guide for Railway
- No environment variable reference

**10b. No TypeScript config (tsconfig.json)**

No `tsconfig.json` was found in the web directory (glob for `**/tsconfig*` returned empty). Next.js generates one on first build, but having it committed ensures consistent TypeScript behavior across environments.

---

## SUMMARY TABLE

| # | Area | Severity | Key Finding |
|---|------|----------|-------------|
| 1 | Deployment Configuration | **CRITICAL** | Dockerfile missing, `output: 'standalone'` missing, no Railway config |
| 2 | Environment & Secrets | **HIGH** | Placeholder secrets in committed config.yaml, missing python-dotenv dep |
| 3 | Monitoring & Observability | **HIGH** | `web/lib/logger.ts` module missing (build will fail), minimal health check, no APM |
| 4 | Data Persistence | **CRITICAL** | All data in JSON files on ephemeral filesystem, no database |
| 5 | Security Headers | **HIGH** | No CSP/HSTS/X-Frame-Options, `exec` in API routes, timing-unsafe auth |
| 6 | CI/CD Pipeline | **HIGH** | No GitHub Actions, no pre-commit hooks, tests will fail |
| 7 | Dependency Management | **MEDIUM** | Unbounded Python versions, React 19 / Next 14 mismatch |
| 8 | Scaling & Reliability | **HIGH** | In-memory state, no graceful shutdown, orphan-prone subprocesses |
| 9 | Backup & Recovery | **CRITICAL** | No backup strategy, data lost on every deploy |
| 10 | Documentation | **LOW** | Missing API docs, runbooks, deployment guide, tsconfig.json |

---

## PRIORITIZED REMEDIATION PLAN

### P0 -- Fix before deploying (Blocks production)

1. **Create `web/lib/logger.ts`** -- The build literally cannot succeed without it. Based on the test at `/home/pmcerlean/projects/pilotai-credit-spreads/web/tests/logger.test.ts`, implement a structured JSON logger with `info`, `error`, `warn` methods.

2. **Restore Dockerfile and .dockerignore** -- Recreate the multi-stage Docker build with `output: 'standalone'` in `next.config.js`. Add `output: 'standalone'` to the `nextConfig` object.

3. **Add a persistent data store** -- Replace JSON file storage with PostgreSQL (Railway provides managed Postgres). Migrate paper trades, config, and alert data to the database. This is the single most important architectural change.

4. **Add `python-dotenv`** to `requirements.txt`.

### P1 -- Fix within first sprint (Critical for reliability)

5. **Add security headers** to `next.config.js` -- CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy.

6. **Replace `exec` with `execFile`** in scan and backtest API routes. Do not return stderr in responses.

7. **Use `crypto.timingSafeEqual`** for auth token comparison in middleware.

8. **Create GitHub Actions CI workflow** -- Run `npm test`, `npm run build`, and `npm run lint` on every PR. Add Python test runner for `pytest`.

9. **Add Railway configuration** -- Create `railway.toml` with proper build commands, health check path, and start command.

10. **Pin Python dependency versions** -- Run `pip freeze` and create a `requirements.lock` or switch to `poetry.lock`/`uv.lock`.

### P2 -- Fix within second sprint (Important for operations)

11. **Add comprehensive health check** -- Check filesystem writability, data file freshness, Python backend reachability.

12. **Add graceful shutdown handlers** for SIGTERM.

13. **Move rate limiting and concurrency guards** to Redis (Railway provides managed Redis).

14. **Add Sentry or equivalent** error tracking.

15. **Create `.env.example`** with ALL variables across both stacks.

16. **Remove or rename placeholder secrets** from `config.yaml` -- use `config.example.yaml` that's committed and `config.yaml` that's gitignored.

### P3 -- Fix when capacity allows (Operational excellence)

17. **Add API documentation** (OpenAPI spec).

18. **Add `dependabot.yml`** or Renovate for automated dependency updates.

19. **Create deployment runbook** for Railway.

20. **Resolve React 19 / Next.js 14 version mismatch** -- either upgrade to Next.js 15+ or downgrade to React 18.

21. **Add pre-commit hooks** via Husky for lint, type-check, and secret scanning.

22. **Commit a `tsconfig.json`** to the web directory.

---

