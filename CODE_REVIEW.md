# 7-PANEL CODE REVIEW: Attix Credit Spreads

**Date:** 2026-02-13
**Reviewers:** 7 specialized AI agents (Architecture, Code Quality, Security, Performance, Error Handling, Testing, Production Readiness)
**Scope:** Full codebase -- ~100 files, ~5,000 lines Python backend + Next.js frontend
**Overall Verdict: NOT FIT FOR PURPOSE**

This is a trading system -- one config flag away from real money -- with zero tests, zero auth, zero deployment infrastructure, hardcoded API keys committed to git, and a core options filtering bug that means it literally cannot find trades when using its configured data provider.

**Total findings: 157** across all panels.

---

## SCORE SUMMARY

| Panel | Score | CRIT | HIGH | MED | LOW | Total |
|---|---|---|---|---|---|---|
| Architecture | 2/10 | 6 | 10 | 10 | 1 | 27 |
| Code Quality | 3/10 | 3 | 8 | 13 | 11 | 35 |
| Security | 1/10 | 4 | 4 | 4 | 4 | 16 |
| Performance | 2/10 | 4 | 8 | 5 | 2 | 19 |
| Error Handling | 1/10 | 7 | 10 | 8 | 0 | 25 |
| Testing | 0/10 | 7 | 7 | 5 | 2 | 21 |
| Production Readiness | 1.5/10 | 3 | 5 | 5 | 1 | 14 |
| **TOTAL** | **1.5/10** | **34** | **52** | **50** | **21** | **157** |

**Worst panels:** Testing (literally zero tests), Security (3-request full takeover chain), Error Handling (bare excepts hiding financial losses).

**Single most dangerous finding:** An unauthenticated attacker can steal all API keys, flip paper-to-live, and trigger real trades -- all without logging in. Three HTTP requests. Zero authentication.

---

## TABLE OF CONTENTS

1. [Architecture](#panel-1-architecture)
2. [Code Quality](#panel-2-code-quality)
3. [Security](#panel-3-security)
4. [Performance](#panel-4-performance)
5. [Error Handling](#panel-5-error-handling)
6. [Testing](#panel-6-testing)
7. [Production Readiness](#panel-7-production-readiness)
8. [Cross-Panel Critical Bugs](#cross-panel-critical-bugs)
9. [Top 10 Actions](#top-10-actions--in-order)

---

## PANEL 1: ARCHITECTURE

**Score: 2/10** | 6 CRITICAL, 10 HIGH, 10 MEDIUM, 1 LOW

The system has a split-brain architecture. Two completely independent paper trading engines exist -- one in Python (`paper_trader.py`), one in Next.js (`paper-trades/route.ts`) -- with different data schemas, different P&L formulas, and different storage files. A trade in one is invisible to the other.

### CRITICAL Findings

#### A-1: File-Based IPC Between Python and Next.js

**Files:**
- `paper_trader.py:81-99` (writes `paper_trades.json`, `trades.json`)
- `alerts/alert_generator.py:90-97` (writes `alerts.json`)
- `tracker/trade_tracker.py:57-65` (writes `trades.json`, `positions.json`)
- `web/app/api/alerts/route.ts:14-19` (reads from 3 different guessed paths)
- `web/app/api/positions/route.ts:42-46` (reads from 3 different guessed paths)
- `web/app/api/paper-trades/route.ts:32-34` (writes its own `user_trades/<userId>.json`)

The entire Python-to-Next.js communication layer is JSON files on disk. There is no locking, no atomic operations, no shared schema. If Python writes `trades.json` while Next.js is reading it, you get corrupted data. The web paper trading system (`paper-trades/route.ts`) maintains completely separate files from the Python `PaperTrader` -- these two systems do not know about each other.

**Fix:** Use a database (SQLite at minimum, PostgreSQL for production). Create a single source of truth for trade state. Have the Python backend expose a REST API that the Next.js frontend consumes.

#### A-2: Unauthenticated Config Write Endpoint Allows Full System Takeover

**File:** `web/app/api/config/route.ts:18-29`

The `POST /api/config` endpoint accepts any JSON body and writes it directly to `config.yaml` without authentication, authorization, or input validation. Any HTTP client can overwrite your entire trading system configuration, including brokerage credentials, risk parameters, and the paper/live toggle.

**Fix:** Add authentication middleware. Validate the config schema before writing. Never allow brokerage credentials to be set via an API. Hardcode paper mode as an environment variable.

#### A-3: Shell Command Execution from API Routes

**Files:** `web/app/api/scan/route.ts:11-14`, `web/app/api/backtest/run/route.ts:14-17`

These routes execute shell commands (`python3 main.py scan`, `python3 main.py backtest`) via `child_process.exec`. No authentication. No request queuing. No concurrency control. Simultaneous users could spawn unlimited Python processes.

**Fix:** Use a task queue (BullMQ, Celery). Communicate between Next.js and Python via a proper API (FastAPI/Flask). Add authentication and rate limiting.

#### A-4: Two Independent Paper Trading Engines with Different Data Models

**Files:** `paper_trader.py` (Python), `web/app/api/paper-trades/route.ts` (Next.js)

The Python `PaperTrader` (trade ID: incrementing integer, stored in `data/paper_trades.json`) and the Next.js paper trades API (trade ID: `PT-{timestamp}-{random}`, stored in `data/user_trades/<userId>.json`) are completely separate systems with different data schemas, different P&L formulas, different exit rules, and different storage files.

The P&L estimation logic differs:
- `positions/route.ts`: `Math.pow(daysHeld / totalDays, 0.7) * maxProfit * 0.65`
- `paper-trades/route.ts`: `Math.pow(daysHeld / dte_at_entry, 0.7) * max_profit * 0.7`
- `paper_trader.py`: entirely different intrinsic-value-based model with decay factors

Users see different P&L numbers depending on which page of the dashboard they look at.

**Fix:** Single paper trading engine. Either Python is the backend and Next.js is a dumb frontend, or consolidate into one. Never duplicate business logic across languages.

#### A-5: API Keys and Secrets Committed to Source Control

**Files:** `config.yaml:88-89,103`, `strategy/polygon_provider.py:278`

Live Alpaca API credentials and Polygon API key are hardcoded directly in `config.yaml` which is tracked in git. The `.gitignore` excludes `config.local.yaml` and `secrets.yaml` but NOT `config.yaml`. The Polygon key is also hardcoded a second time in `polygon_provider.py:278`.

**Fix:** Rotate all keys immediately. Move to environment variables. Add `config.yaml` to `.gitignore`. Purge git history with `git filter-repo`.

#### A-6: Delta Handling Inconsistency Breaks Core Product

**Files:** `strategy/options_analyzer.py:210`, `strategy/polygon_provider.py:132`, `strategy/spread_strategy.py:191-194`

The yfinance delta estimate (`options_analyzer.py:210`) returns `np.abs(delta)` (always positive). The Polygon provider (`polygon_provider.py:132`) also returns `abs(greeks.get("delta", 0))`. But the bull put spread finder (`spread_strategy.py:191-194`) filters for `puts['delta'] >= -target_delta_max` (expecting negative delta for puts).

When using Polygon or the fallback delta estimator, the filter `delta >= -0.15 AND delta <= -0.10` will match zero rows since all deltas are positive. **The system cannot find bull put spreads with its configured data provider.**

**Fix:** Establish a convention: puts have negative delta, calls have positive delta. Enforce at the provider boundary. Remove `np.abs()` from `_estimate_delta`.

### HIGH Findings

#### A-7: CreditSpreadSystem is a God Object
**File:** `main.py:34-78`

The `__init__` instantiates 9 objects. `scan_opportunities` orchestrates scanning, analysis, ML scoring, alerting, paper trading, and position management in one method. `_analyze_ticker` does data fetching, technical analysis, options analysis, IV analysis, spread evaluation, ML scoring, and score blending.

**Fix:** Use a pipeline/mediator pattern. Separate orchestration concerns.

#### A-8: Every Class Takes the Entire Config Dictionary
**Files:** Every Python class constructor

Every class accepts a raw `Dict` config and reaches into it to extract what it needs. No schema validation. A typo in a config key produces `KeyError` at runtime.

**Fix:** Create typed dataclasses for each config section. Use Pydantic for validation.

#### A-9: Data Provider Selection Scattered Across Codebase
**Files:** `strategy/options_analyzer.py:36-51`, `main.py:117-124`, `backtest/backtester.py:119-134`

Config says `provider: "polygon"`, but the system still uses yfinance directly in three places. The `OptionsAnalyzer` initializes a Polygon provider but `calculate_iv_rank()` ignores it and uses yfinance anyway. The system silently mixes delayed yfinance data with real-time Polygon data.

**Fix:** Create a proper `DataProvider` interface (Protocol class). All data access goes through this interface.

#### A-10: No Interface/Protocol Definitions Anywhere
**Files:** Entire codebase

No abstract base classes, Protocol definitions, or interface contracts. `TradierProvider`, `PolygonProvider`, and yfinance all return DataFrames with different column names and conventions.

**Fix:** Define a canonical `OptionsChainSchema`. All providers must conform.

#### A-11: Implicit Data Contracts via Dictionary Keys
**Files:** Throughout codebase

The entire system communicates via untyped dictionaries. `spread_strategy.py` returns `List[Dict]`. `main.py` adds ML fields. `alert_generator.py` expects specific keys. `paper_trader.py` expects different keys. None are documented or enforced.

**Fix:** Use dataclasses or Pydantic models: `SpreadOpportunity`, `TradeRecord`, `AlertPayload`.

#### A-12: Hardcoded Relative Paths with Multiple Fallback Guesses
**Files:** `web/app/api/alerts/route.ts:15-18`, `web/app/api/positions/route.ts:42-46`, `tracker/trade_tracker.py:31`, `paper_trader.py:17-19`

File paths are relative and guessed. The alerts route tries 3 different paths. `TradeTracker` writes relative to CWD, `PaperTrader` writes relative to file location. These are different directories depending on how the system is invoked.

**Fix:** Use a single configurable base path from an environment variable. Never use relative paths for data storage.

#### A-13: Backtester Uses Fabricated Options Data
**File:** `backtest/backtester.py:136-210`

The backtester does not use historical options data. It fabricates: `short_strike = price * 0.90`, `credit = spread_width * 0.35`. The spread value estimation uses crude heuristics, not options pricing models. Only simulates bull put spreads.

**Fix:** Integrate historical options data. Use Black-Scholes for spread valuation. At minimum, document loudly that this is a simulation, not a backtest.

#### A-14: ML Model Trained on Synthetic Data
**File:** `ml/signal_model.py:454-607`

When no saved model exists, the ML pipeline trains on synthetic data from `generate_synthetic_training_data()` using hardcoded distributions and handcrafted win/loss logic. The 60% weighting given to ML scores means the majority of the trading signal comes from a model that has never seen real market data.

**Fix:** Use real historical trade data for training, or remove the ML component and use rule-based scoring alone. Set ML weight to 0 until validated.

#### A-15: No Concurrency Protection on File Writes
**Files:** `paper_trader.py:80-99`, `tracker/trade_tracker.py:57-65`, `web/app/api/paper-trades/route.ts:32-34`

No locking, no atomic writes, no error recovery. If the process crashes between sequential file writes, data is inconsistent.

**Fix:** Use atomic writes (write to temp, then rename). Add file locking. Better yet, use a database.

#### A-16: No Authentication on Any API Route
**Files:** All files in `web/app/api/`

Zero authentication on any endpoint. Config allows writing. Paper trading allows opening/closing positions. Scan triggers system-wide operations. All publicly accessible.

**Fix:** Add authentication middleware (NextAuth.js, JWT, or API keys).

### MEDIUM Findings

- **A-17:** Config is a flat dumping ground -- `account_size` appears in `risk.account_size` and `backtest.starting_capital`
- **A-18:** ML pipeline config not in `config.yaml` -- entire ML subsystem configured via hardcoded defaults
- **A-19:** `OptionsAnalyzer` has dual responsibilities (data provider + analyzer) -- violates SRP
- **A-20:** ML package circular import risk via `__init__.py` re-exports
- **A-21:** Hardcoded strategy types -- only bull put and bear call, no plugin system
- **A-22:** Duplicated P&L calculation in 3 files with 3 different formulas
- **A-23:** No input validation on paper trade creation
- **A-24:** String type mismatch: `main.py` uses `'bull_put'` while `spread_strategy.py` uses `'bull_put_spread'`
- **A-25:** `generate_alerts_only` is misleadingly named -- actually runs a full scan
- **A-26:** `TechnicalAnalyzer` mutates input DataFrame

---

## PANEL 2: CODE QUALITY

**Score: 3/10** | 3 CRITICAL, 8 HIGH, 13 MEDIUM, 11 LOW

### CRITICAL Findings

#### CQ-1: Hardcoded API Key in Source Code
**File:** `strategy/polygon_provider.py:278`

```python
provider = PolygonProvider(api_key="REDACTED_POLYGON_KEY")
```

A live Polygon.io API key is in the `__main__` block. Committed to source control.

**Fix:** Remove immediately. Use `os.environ.get('POLYGON_API_KEY')`. Rotate the key.

#### CQ-2: Bare `except` Clauses Swallowing All Errors
**Files:** `main.py:123-124`, `ml/feature_engine.py:332-333`

```python
except:
    pass
```

Catches `SystemExit`, `KeyboardInterrupt`, `MemoryError`. The `except: pass` in `main.py` silently discards errors while fetching live market prices used for position management.

**Fix:** Use `except Exception as e:` with logging at minimum.

#### CQ-3: `_estimate_delta` Returns Absolute Delta, Breaking Spread Strategy
**File:** `strategy/options_analyzer.py:210`

```python
return pd.Series(np.round(np.abs(delta), 4), index=df.index)
```

Delta is always positive due to `np.abs()`, but `spread_strategy.py` expects negative deltas for puts. No put spreads will ever match.

**Fix:** Return signed delta values.

### HIGH Findings

#### CQ-4: `_find_bull_put_spreads` and `_find_bear_call_spreads` Are Nearly Identical
**File:** `strategy/spread_strategy.py:164-324`

80+ lines each with the same structure. Only differences: `put` vs `call`, delta sign, long strike direction.

**Fix:** Extract a single `_find_spreads()` parameterized by spread direction.

#### CQ-5: RSI Calculation Duplicated 3 Times
**Files:** `strategy/technical_analysis.py:120-124`, `ml/feature_engine.py:475-482`, `ml/regime_detector.py:343-354`

Same RSI formula implemented three times across three modules.

**Fix:** Create a shared `indicators.py` utility module.

#### CQ-6: IV Rank Calculation Duplicated 3 Times
**Files:** `strategy/options_analyzer.py:212-266`, `strategy/polygon_provider.py:243-271`, `ml/iv_analyzer.py:243-294`

Slight variations in structure but identical math. Could diverge silently.

**Fix:** Centralize in a single utility function.

#### CQ-7: FOMC Dates Hardcoded in Two Locations with Discrepancies
**Files:** `ml/feature_engine.py:44-57`, `ml/sentiment_scanner.py:38-56`

`feature_engine.py` is missing `datetime(2026, 1, 28)` that `sentiment_scanner.py` has. Both lists will go stale.

**Fix:** Define FOMC dates in a single constants file. Better yet, fetch from an API.

#### CQ-8: Duplicated Pagination Logic in Polygon Provider (4x)
**File:** `strategy/polygon_provider.py`

Same pagination block repeated 4 times (lines 66-76, 97-103, 157-168, and in `get_options_chain`). Each bypasses the `_get` method.

**Fix:** Extract `_paginated_get(self, path, params)`.

#### CQ-9: Row-Building Dictionary Copy-Pasted Between Methods
**File:** `strategy/polygon_provider.py:105-141, 170-213`

16-field dictionary is character-for-character identical between `get_options_chain` and `get_full_chain`.

**Fix:** Extract `_parse_option_item()` helper.

#### CQ-10: Repeated yfinance Downloads Without Caching
**Files:** `ml/feature_engine.py:139,207,271,284`, `ml/regime_detector.py:200-206,266-268`

Same ticker downloaded twice in `feature_engine.py` (6mo then 3mo -- the 6mo already contains the 3mo data). `regime_detector` downloads SPY/VIX/TLT in both `_fetch_training_data` and `_get_current_features`.

**Fix:** Implement a data cache layer. The `feature_cache` and `cache_timestamps` already exist in `FeatureEngine` but are never used.

#### CQ-11: `_estimate_delta` Returns Absolute Delta Breaking Strategy
**File:** `strategy/options_analyzer.py:210`

(See CQ-3 above -- cross-referenced for emphasis)

### MEDIUM Findings

- **CQ-12:** Magic numbers throughout scoring logic (`spread_strategy.py:352-397`, `ml/ml_pipeline.py:240-290`)
- **CQ-13:** Stringly-typed enums for spread types and regimes -- `main.py` uses `'bull_put'`, `spread_strategy.py` uses `'bull_put_spread'` (will never match)
- **CQ-14:** ML vs rules blending weight (`0.6 * ml_score + 0.4 * rules_score`) hardcoded in `main.py:198`
- **CQ-15:** Event risk threshold `0.7` hardcoded in `main.py:205`
- **CQ-16:** `generate_alerts_only` runs a full scan despite name and docstring
- **CQ-17:** Pickle deserialization security risk (`ml/signal_model.py:424`)
- **CQ-18:** Unused `feature_cache` and `cache_timestamps` in `FeatureEngine`
- **CQ-19:** No abstract base class for data providers
- **CQ-20:** `lookback_days` parameter in `sentiment_scanner.scan()` actually looks FORWARD
- **CQ-21:** All classes accept raw `Dict` config -- no typed config objects
- **CQ-22:** Backtester `_estimate_spread_value` uses undocumented magic numbers (`1.05`, `0.95`, `35`, `0.3`, `0.7`)
- **CQ-23:** `rebalance_positions` divides by `current_size` which can be 0 (`position_sizer.py:349`)
- **CQ-24:** Alert score threshold `60` hardcoded in both `main.py:237` and `alert_generator.py:53`

### LOW Findings

- **CQ-25:** Unused import `scipy.stats` in `feature_engine.py:19`
- **CQ-26:** Unused `recent_data` variable in `technical_analysis.py:149`
- **CQ-27:** `sys.path.insert(0, ...)` hack in `main.py:20`
- **CQ-28:** Emoji characters in log messages (encoding issues on some systems)
- **CQ-29:** Unused `Tuple` import in `spread_strategy.py:8`
- **CQ-30:** `PnLDashboard.__init__` takes untyped `tracker` parameter
- **CQ-31:** `send_alerts` takes `formatter` with no type hint
- **CQ-32:** Unused `interpolate` import in `iv_analyzer.py:16`
- **CQ-33:** Unused `norm` import in `iv_analyzer.py:17`
- **CQ-34:** Redundant inline `datetime` import in `performance_metrics.py:133`
- **CQ-35:** Direct `print()` instead of logger in dashboard and metrics

---

## PANEL 3: SECURITY

**Score: 1/10** | 4 CRITICAL, 4 HIGH, 4 MEDIUM, 4 LOW

### CRITICAL ATTACK CHAIN

An attacker can chain multiple vulnerabilities into a devastating attack with **3 HTTP requests and zero authentication**:

1. **Recon:** `GET /api/config` -- steal all API keys
2. **Weaponize:** `POST /api/config` -- set `alpaca.paper: false`, `max_risk_per_trade: 100`, `max_positions: 999`
3. **Execute:** `POST /api/scan` -- trigger real trades with maximum position sizing

**Result:** The system submits massive real options orders to Alpaca's LIVE trading API.

### CRITICAL Findings

#### S-1: Live API Keys Committed in Plaintext to Source Code
**Files:** `config.yaml:88-89,103`, `strategy/polygon_provider.py:278`

- **Alpaca API Key:** `REDACTED_ALPACA_KEY`
- **Alpaca API Secret:** `REDACTED_ALPACA_SECRET`
- **Polygon.io API Key:** `REDACTED_POLYGON_KEY`

`config.yaml` is NOT in `.gitignore`. Anyone with repo access has your brokerage credentials.

**Remediation:**
1. IMMEDIATELY rotate all three API keys
2. Add `config.yaml` to `.gitignore`
3. Move all secrets to environment variables
4. Use `git filter-repo` to purge keys from git history

#### S-2: Unauthenticated Config Write API -- Full System Takeover
**File:** `web/app/api/config/route.ts:18-29`

The `POST` endpoint accepts any JSON body and writes directly to `config.yaml` without authentication. An attacker can set `max_risk_per_trade: 100`, change the Alpaca endpoint to non-paper mode, or inject malicious config.

**Remediation:** Add authentication middleware. Validate config schema. Never allow brokerage credentials to be set via API.

#### S-3: Zero Authentication on ALL API Endpoints
**Files:** All files in `web/app/api/`

No `middleware.ts`. No session checks. No JWT validation. No API key validation. Every endpoint is completely open to the internet.

**Remediation:** Implement authentication middleware using NextAuth.js, Clerk, or a custom JWT solution.

#### S-4: Paper-to-Live Safety Boundary is a Single Boolean
**Files:** `config.yaml:90`, `paper_trader.py:36-48`, `strategy/alpaca_provider.py:35-36`

The ONLY thing preventing real trades with real money is `paper: true` in config.yaml. Combined with S-2, anyone can flip this with a single unauthenticated API call.

**Remediation:**
1. Hardcode paper mode as a compile-time constant or environment variable
2. Add a confirmation workflow for switching to live trading
3. Implement a `LIVE_TRADING_ENABLED` environment variable
4. Blacklist `alpaca.paper` from the config POST endpoint

### HIGH Findings

#### S-5: Command Injection via Process Execution
**Files:** `web/app/api/scan/route.ts:10-16`, `web/app/api/backtest/run/route.ts:9-17`

`child_process.exec()` runs through a shell. Currently hardcoded commands but the pattern is dangerous. Unauthenticated -- denial-of-service by repeatedly calling `POST /api/scan`.

**Fix:** Use `execFile()`. Add authentication. Add rate limiting. Max 1 concurrent scan.

#### S-6: Config GET Endpoint Exposes All Secrets
**File:** `web/app/api/config/route.ts:6-16`

`GET /api/config` returns the entire `config.yaml` including all API keys and secrets to any unauthenticated caller.

**Fix:** Add authentication. Strip sensitive fields before returning.

#### S-7: IDOR in Paper Trades
**File:** `web/app/api/paper-trades/route.ts:17-21`

`userId` comes from query parameter. No authentication. Any user can read/modify any other user's portfolio via `GET /api/paper-trades?userId=admin`.

**Fix:** Authenticate users and derive userId from the session.

#### S-8: Hardcoded API Key in Python Source Code
**File:** `strategy/polygon_provider.py:278`

Polygon API key hardcoded in `__main__` block, separate from config.

**Fix:** Remove. Use `os.environ.get('POLYGON_API_KEY')`.

### MEDIUM Findings

- **S-9:** Unsafe YAML deserialization -- `yaml.load()` instead of `yaml.safeLoad()` (`web/app/api/config/route.ts:10`)
- **S-10:** No CORS policy or security headers (`web/next.config.js`) -- no CSP, X-Frame-Options, HSTS
- **S-11:** No rate limiting on any endpoint -- DoS vector via scan/backtest routes, cost amplification via chat (OpenAI bills)
- **S-12:** No input validation on paper trade creation (`web/app/api/paper-trades/route.ts:108-174`) -- no field validation, `contracts` not capped

### LOW Findings

- **S-13:** SSRF potential via chat API prompt injection
- **S-14:** Error messages leak internal paths and stack traces
- **S-15:** Missing `.env.example` -- inconsistent secret management
- **S-16:** Dependencies not pinned -- supply chain risk

---

## PANEL 4: PERFORMANCE

**Score: 2/10** | 4 CRITICAL, 8 HIGH, 5 MEDIUM, 2 LOW

**Estimated scan time: 3-10 MINUTES. Should be under 1 minute.**

### Latency Budget

| Component | Current (10 tickers) | After Fixes |
|---|---|---|
| Sequential ticker scanning | 30-90s | 6-18s |
| Duplicate yfinance calls | 80-200s | 0s (cached) |
| Market data re-fetches (SPY/VIX/TLT) | 20-60s | 2-3s (once) |
| yfinance all expirations | 50-150s | 10-30s |
| Regime detection per-opportunity | 30-75s | 2-5s (once) |
| ML pipeline overhead | 5-15s | 1-2s (persisted) |
| Web subprocess cold start | 5-10s | 0s (persistent server) |
| **TOTAL** | **220-600s (3-10 min)** | **21-58s** |

### CRITICAL Findings

#### P-1: Sequential Ticker Scanning -- Zero Parallelism
**File:** `main.py:87-91`

Every ticker analyzed one at a time in a simple `for` loop. Each `_analyze_ticker()` involves 3+ blocking HTTP requests.

**Fix:**
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(self._analyze_ticker, t): t for t in tickers}
    for future in as_completed(futures):
        all_opportunities.extend(future.result())
```

#### P-2: Same Data Fetched 3-5 Times Per Ticker
**Files:** `main.py:147-148`, `ml/feature_engine.py:139,207`, `strategy/options_analyzer.py:229`, `ml/regime_detector.py:266-268`, `ml/iv_analyzer.py:313`

For a single ticker, the system makes 8-10 separate HTTP calls to yfinance:
1. `main.py` fetches 3mo price data
2. `options_analyzer.calculate_iv_rank` fetches 1y price data
3. `feature_engine._compute_technical_features` fetches 6mo price data
4. `feature_engine._compute_volatility_features` fetches 3mo price data (same ticker, again!)
5. `feature_engine._compute_market_features` fetches VIX 5d + SPY 3mo
6. `feature_engine._compute_event_risk_features` calls `yf.Ticker(ticker).calendar`
7. `regime_detector._get_current_features` fetches SPY 3mo, VIX 3mo, TLT 3mo
8. `iv_analyzer._get_iv_history` fetches historical data again

**Fix:** Implement a per-scan `MarketDataCache` that fetches each ticker once and passes data through the pipeline.

#### P-3: Market-Wide Data Re-fetched Per Ticker
**Files:** `ml/feature_engine.py:271-284`, `ml/regime_detector.py:266-268`

Both `feature_engine._compute_market_features()` and `regime_detector._get_current_features()` are called once per ticker but download SPY, VIX, and TLT every time. For 10 tickers, SPY is downloaded ~20+ times.

**Fix:** Fetch SPY, VIX, TLT exactly once at scan start.

#### P-4: Web API Spawns Full Python Subprocess Per Request
**Files:** `web/app/api/scan/route.ts:11-16`, `web/app/api/backtest/run/route.ts:14-17`

Every scan request spawns `python3 main.py scan`: Python cold start (1-2s) + imports (3-5s) + ML init (2-5s) + scan (30-90s) = 40-120 seconds per web request.

**Fix:** Run Python as a persistent FastAPI/Flask server. The Next.js route calls the API.

### HIGH Findings

#### P-5: Tradier Provider Makes Sequential Per-Expiration API Calls
**File:** `strategy/tradier_provider.py:136-144`

`get_full_chain()` loops through each expiration calling `get_options_chain()` individually. 3-4 sequential HTTP requests per ticker.

**Fix:** Use `ThreadPoolExecutor` for parallel expiration fetches.

#### P-6: yfinance Fetches ALL Expirations When Only 2-3 Are Needed
**File:** `strategy/options_analyzer.py:116-133`

Iterates over ALL available expirations and downloads the full chain for each. SPY has 20+ expirations. DTE filtering only happens later.

**Fix:** Filter expirations before fetching.

#### P-7: Second Sequential yfinance Loop for Current Prices
**File:** `main.py:117-124`

After scanning all tickers, a SECOND loop fetches current prices for all tickers one by one. This data was already fetched during the scan.

**Fix:** Cache current prices from the initial scan and reuse.

#### P-8: `iterrows()` in Spread Finder Hot Path
**Files:** `strategy/spread_strategy.py:198,279`

`iterrows()` is the slowest way to iterate a DataFrame.

**Fix:** Use vectorized merge-based approach.

#### P-9: JSON File "Database" -- Full Load/Save on Every Operation
**File:** `tracker/trade_tracker.py:43-55,57-65`

Entire files loaded into memory on init. Every mutation re-serializes and writes the entire file.

**Fix:** Migrate to SQLite.

#### P-10: Alerts Route Reads JSON From Disk on Every GET
**File:** `web/app/api/alerts/route.ts:12-19`

Every request to `/api/alerts` reads 3 different file paths and parses JSON. No caching headers.

**Fix:** Add `Cache-Control` headers and in-memory caching.

#### P-11: Regime Detector Re-trains on Every Scan If Not Trained
**Files:** `ml/regime_detector.py:146-149,80-81`

Model not persisted. Every cold start (including every web-triggered scan) retrains HMM + Random Forest on 252+ days of data.

**Fix:** Persist the trained regime detector to disk.

#### P-12: ML Pipeline Calls `analyze_trade()` Per-Opportunity Instead of Batching
**Files:** `main.py:184-186`, `ml/ml_pipeline.py:153`

`regime_detector.detect_regime()` re-fetches SPY/VIX/TLT for EVERY opportunity. Regime is market-wide and identical for all opportunities in the same scan. For 15 opportunities = 30-75 seconds of redundant work.

**Fix:** Detect regime ONCE before the loop.

### MEDIUM Findings

- **P-13:** `feature_engine` cache exists but is never used (`feature_engine.py:40-41`)
- **P-14:** Backtest iterates every calendar day including weekends (`backtester.py:79-106`)
- **P-15:** Polygon provider fetches ALL options then filters by DTE client-side (`polygon_provider.py:157-168`)
- **P-16:** No rate limit handling on any API provider (`polygon_provider.py:27-34`, `tradier_provider.py:37`)
- **P-17:** Backtest returns entire equity curve and all trades in memory (`backtester.py:396-398`)

### LOW Findings

- **P-18:** `_analyze_trend` mutates input DataFrame (`technical_analysis.py:84-85`)
- **P-19:** Synthetic training data generated in a Python loop instead of vectorized (`signal_model.py:487-599`)
- **P-20:** Chat route has no streaming or caching (`web/app/api/chat/route.ts:45-60`)

---

## PANEL 5: ERROR HANDLING

**Score: 1/10** | 7 CRITICAL, 10 HIGH, 8 MEDIUM

### CRITICAL Findings

#### EH-1: Bare `except: pass` on Price Fetches During Active Scan
**File:** `main.py:118-124`

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

If yfinance API is down or throttling, this silently skips ALL tickers. `current_prices` ends up empty. Open positions are never evaluated for stop-loss or profit-target exits.

**Failure scenario:** Market crashes. API is slow. Every ticker fetch fails silently. All open positions miss their stop-loss triggers. You eat max loss on every position.

**Fix:** Catch specific exceptions. Log errors. Use `(KeyboardInterrupt, SystemExit)` re-raise pattern.

#### EH-2: Bare `except:` Fabricates Expiration Dates
**File:** `paper_trader.py:257-263`

```python
try:
    exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
except:
    try:
        exp_date = datetime.fromisoformat(trade["expiration"])
    except:
        exp_date = now + timedelta(days=30)
```

Failed date parsing silently assigns expiration 30 days in the future. A trade that SHOULD have expired and been closed continues tracking for another month.

**Fix:** On parse failure, treat as expired (not 30 days future) and log critical error.

#### EH-3: Non-Atomic JSON Writes for Trade State
**Files:** `paper_trader.py:80-84`, `tracker/trade_tracker.py:57-65`

```python
def _save_trades(self):
    with open(PAPER_LOG, "w") as f:
        json.dump(self.trades, f, indent=2, default=str)
```

`open("file", "w")` truncates BEFORE writing. Process crash during `json.dump()` = truncated/corrupted file. On restart, `json.load()` fails and ALL trade history is lost.

**Fix:** Write to temp file, then `os.replace()` to target (atomic on POSIX).

#### EH-4: No Error Handling on JSON Load
**Files:** `paper_trader.py:56-59`, `tracker/trade_tracker.py:43-48`

```python
def _load_trades(self) -> Dict:
    if PAPER_LOG.exists():
        with open(PAPER_LOG) as f:
            return json.load(f)
```

No try/except around `json.load()`. Corrupted file crashes the entire system. No backup mechanism.

**Fix:** Add try/except for `JSONDecodeError`. Backup corrupted file. Initialize fresh state.

#### EH-5: Unauthenticated Config Write Endpoint
**File:** `web/app/api/config/route.ts:18-29`

No authentication, no validation, no schema enforcement. Any HTTP client can overwrite `config.yaml`.

(See Security panel for full details)

#### EH-6: Unauthenticated Process Spawning
**Files:** `web/app/api/scan/route.ts:10-14`, `web/app/api/backtest/run/route.ts:9-17`

No authentication on endpoints that spawn Python processes. Denial-of-service by spamming scan requests.

#### EH-7: Hardcoded API Key in Source Code
**File:** `strategy/polygon_provider.py:278`

(See Security panel)

### HIGH Findings

#### EH-8: Bare `except:` on Earnings Date Lookup
**File:** `ml/feature_engine.py:332-333`

```python
except:
    features['days_to_earnings'] = 999
```

Hides any failure in earnings lookup. System thinks no earnings nearby. Opens credit spreads right into earnings announcements.

**Fix:** On unexpected failure, assume earnings are imminent (`days_to_earnings = 0`) as the safe default.

#### EH-9: `_analyze_ticker` Catches All Exceptions and Returns Empty List
**File:** `main.py:145-216`

Single massive try/except wraps entire analysis flow. If any sub-operation fails, the entire ticker is silently dropped. No distinction between "no opportunities" and "analysis crashed."

**Fix:** Separate try/except blocks for each stage. Track and report failed tickers. Alert if ALL tickers fail.

#### EH-10: Zero Retry Logic on Any API Call
**Files:** `strategy/polygon_provider.py:32`, `strategy/tradier_provider.py:37`, all yfinance calls

Single transient network error causes entire call chain to fail.

**Fix:** Add retry with exponential backoff using `tenacity` or `urllib3.util.retry`.

#### EH-11: Division by Zero in RSI Calculation
**Files:** `strategy/technical_analysis.py:120-124`, `ml/feature_engine.py:476-481`, `ml/regime_detector.py:343-354`

If price is flat: `gain/loss = 0/0 = NaN`. NaN propagates into technical signals. `NaN < 70` evaluates to `False`, so the trade passes filters it shouldn't.

**Fix:** Replace inf with 100.0, fill NaN with 50.0 (neutral).

#### EH-12: Negative Credit Spreads Not Validated
**File:** `strategy/spread_strategy.py:211,292`

If bid/ask is inverted (illiquid options), credit can be negative. System generates a "credit spread" where you PAY to enter.

**Fix:** Add `if credit <= 0: continue` guard.

#### EH-13: Delta Sign Mismatch Breaks Put Selection
**Files:** `strategy/options_analyzer.py:210`, `strategy/spread_strategy.py:191`

`_estimate_delta` returns absolute values. Strategy expects negative for puts. Result: zero short candidates, zero opportunities, "No opportunities found" when the system is actually broken.

**Fix:** Return signed deltas. Add debug logging when no candidates found.

#### EH-14: `max(1, contracts)` Forces Minimum 1 Contract
**File:** `strategy/spread_strategy.py:420-426`

```python
return max(1, contracts)
```

Overrides all position sizing discipline. If account is $5k and risk per spread is $50k, still forces 1 contract.

**Fix:** Return 0 when risk math says position is unaffordable.

#### EH-15: Pickle Deserialization -- Arbitrary Code Execution Risk
**File:** `ml/signal_model.py:422-424`

`pickle.load()` on model files. Tampered pkl = arbitrary code execution.

**Fix:** Add hash verification. Consider safer serialization (ONNX, joblib with signatures).

#### EH-16: No Crash Recovery -- Phantom Broker Positions
**Architectural issue across multiple files**

If the system crashes after submitting an Alpaca order but before recording locally, you have a live position in the broker with no local record. Position is never monitored.

**Fix:** Implement write-ahead log pattern. On startup, reconcile local state with Alpaca positions.

#### EH-17: `_estimate_delta` Uses Median Strike as Spot Price
**File:** `strategy/options_analyzer.py:186-210`

```python
spot = df['strike'].median()  # WRONG
```

Should use actual stock price. Produces systematically wrong deltas for every trade.

**Fix:** Accept `spot_price` parameter from caller.

### MEDIUM Findings

- **EH-18:** Mutating input DataFrame in technical analysis (`technical_analysis.py:84-85`)
- **EH-19:** Pagination without infinite loop protection in Polygon (`polygon_provider.py:66-76`)
- **EH-20:** No timeout on OpenAI API call in chat route (`web/app/api/chat/route.ts:44-67`)
- **EH-21:** Backtest `return_pct` divides by zero when `max_loss` is 0 (`backtester.py:335`)
- **EH-22:** Profit factor divides by zero when losers sum is 0 (`backtester.py:390`)
- **EH-23:** `rebalance_positions` divides by zero on zero-size positions (`position_sizer.py:349`)
- **EH-24:** `validate_config` missing validation for critical nested keys (`utils.py:96-132`)
- **EH-25:** Alpaca `get_account`, `get_orders`, `get_positions` have no error handling (`alpaca_provider.py:56-68`)

---

## PANEL 6: TESTING

**Score: 0/10** | 7 CRITICAL, 7 HIGH, 5 MEDIUM, 2 LOW

### Test Coverage: 0.00%

Zero test files. Zero test directories. No `pytest.ini`, no `jest.config`, no `.coveragerc`, no CI/CD. `pytest` and `pytest-cov` are in `requirements.txt` labeled "(optional)" but no tests exist to use them. `TESTING.md` explicitly instructs users to "create `tests/test_all.py`" -- confirming the test suite was never written.

### CRITICAL Testing Gaps

#### T-1: No Tests for Credit/Debit Calculation (Spread Pricing)
**File:** `strategy/spread_strategy.py:211,293`

`credit = short_put['bid'] - long_put['ask']` and `max_loss = spread_width - credit` determine how much money is at risk. Zero test coverage. If bid/ask are swapped, the system opens trades that instantly lose money.

#### T-2: No Tests for Position Sizing / Kelly Criterion
**File:** `ml/position_sizer.py:147-185`

Kelly formula chains 6 transformations with no tests. Math error = 100% portfolio on single trade. Known zero-division bug in `rebalance_positions`.

#### T-3: No Tests for Paper Trading P&L Tracking
**File:** `paper_trader.py:280-342,344-399`

P&L formula at line 319 is calculated then immediately overwritten on line 321. Dead code suggests confusion about the correct formula.

#### T-4: No Tests for Alpaca Order Submission (Real Money Path)
**File:** `strategy/alpaca_provider.py`

`_build_occ_symbol` constructs OCC symbols. `.replace(" ", " ")` on line 95 replaces space-with-space (no-op). Wrong OCC symbol = wrong option contract traded.

#### T-5: No Tests for ML Score Blending
**File:** `main.py:196-207`

`0.6 * ml_score + 0.4 * rules_score` -- if either is NaN, final score becomes NaN, and all comparisons become False.

#### T-6: No Tests for JSON File Persistence
**Files:** `tracker/trade_tracker.py`, `paper_trader.py`

No tests for corrupted files, concurrent read/write, missing fields, or disk-full scenarios.

#### T-7: No Tests for Unauthenticated Shell Execution Routes
**File:** `web/app/api/scan/route.ts`

No tests for auth, timeouts, concurrent scans, or input sanitization.

### HIGH Testing Gaps

- **T-8:** No tests for delta estimation (uses wrong spot price -- `options_analyzer.py:194`)
- **T-9:** No tests for regime detection model (HMM state mapping could flip crisis/trending)
- **T-10:** No tests for backtest P&L calculation (uses different formula than paper trader)
- **T-11:** No tests for scoring algorithm (`spread_strategy.py:333-398`)
- **T-12:** No tests for IV rank calculation (unit mismatch risk -- decimal vs percentage)
- **T-13:** No tests for RSI (manual implementation differs from TA-Lib when toggle changes)
- **T-14:** No tests for web API paper trading routes

### Confirmed Bugs Found During Review (No Tests to Catch Them)

1. **`_estimate_delta` uses `df['strike'].median()` as spot price** (`options_analyzer.py:194`) -- should use actual stock price
2. **`paper_trader.py:319` P&L calculated then overwritten on line 321** -- dead code, unclear which formula is correct
3. **`alpaca_provider.py:95` OCC symbol padding** -- `.replace(" ", " ")` is a no-op
4. **`position_sizer.py:349` divides by `current_size`** -- ZeroDivisionError when 0
5. **FOMC dates hardcoded only through June 2026** (`feature_engine.py:57`) -- event risk silently stops working after that

---

## PANEL 7: PRODUCTION READINESS

**Score: 1.5/10** | 3 CRITICAL, 5 HIGH, 4 MEDIUM, 1 LOW

### CRITICAL Findings

#### PR-1: The System Does Not Run Continuously
**File:** `main.py`

`main.py` is a one-shot CLI tool. No scheduling loop, no cron config, no systemd service, no daemon mode. The web chat claims "scans every 30 minutes during market hours" but no code implements this. If nobody types `python main.py scan`, no scanning occurs. Open positions are not monitored.

**Fix:** Add scheduling loop with `APScheduler`. Create systemd service file. As interim: `*/30 9-16 * * 1-5 cd /path && python main.py scan`

#### PR-2: Zero Deployment Infrastructure

No Dockerfile, no docker-compose, no Kubernetes, no Terraform, no systemd service files, no deploy scripts. The deployment model is SSH + `git clone` + run `setup.sh` interactively + `python main.py scan` in a terminal.

**Fix:** Create Dockerfile + docker-compose. Add health check endpoints. Document deployment.

#### PR-3: API Keys Committed in Plaintext
(See Security panel)

### HIGH Findings

#### PR-4: Zero CI/CD Pipeline

No `.github/workflows/`. No pre-commit hooks. No linting. No type checking. Code changes go directly to production with no automated validation.

**Fix:** GitHub Actions for linting (ruff), type checking (mypy), unit tests. Block merges without passing CI.

#### PR-5: No Monitoring, Health Checks, or System-Level Alerting

No health check endpoint. No uptime monitoring. No alerting when scanner crashes. No metrics collection. If the scanner dies at 10 AM, nobody knows until they manually check.

**Fix:** Add `/health` endpoint. Set up heartbeat monitor. Send Telegram alert on crash. Dead-man's switch if no scan in 45 minutes during market hours.

#### PR-6: No Graceful Shutdown

No signal handlers. No `atexit`. `KeyboardInterrupt` catch in `main.py:358` does nothing except log and exit. Kill signal during JSON write = corrupted state.

**Fix:** SIGTERM handler that finishes current writes and flushes state. Atomic file writes.

#### PR-7: JSON File Database with No Backups

All trade data in local JSON files. `data/` directory is in `.gitignore`. No backup scripts, no replication, no cloud sync. Disk dies = all trade history permanently lost.

**Fix:** Migrate to SQLite. Add automated backups to cloud storage.

#### PR-8: No Market Hours Awareness

No concept of market hours, holidays, pre/post market, or early close days. Would scan on Christmas Day. Burns API rate limits overnight.

**Fix:** Add `MarketCalendar` utility. Check `is_market_open()` before every scan.

### MEDIUM Findings

- **PR-9:** Dependencies not pinned -- `>=` specifiers everywhere. Non-reproducible builds.
- **PR-10:** Bare except clauses swallow errors silently (see Error Handling panel)
- **PR-11:** No API rate limit handling on Polygon/Tradier providers
- **PR-12:** Non-atomic file writes (see Error Handling panel)
- **PR-13:** Web frontend may be incomplete (`package.json` not found during review)

---

## CROSS-PANEL CRITICAL BUGS

Findings that appeared across multiple panels, indicating systemic issues:

| Bug | Panels | Impact |
|---|---|---|
| Delta sign mismatch (`abs()` vs negative filter) | Architecture, Code Quality, Error Handling, Testing | **Core product broken -- cannot find put spreads** |
| Bare `except: pass` on price fetches | Code Quality, Error Handling, Production Readiness | **Stop losses silently stop working** |
| API keys in plaintext in git | Architecture, Security, Production Readiness | **Full brokerage account takeover** |
| Unauthenticated config write | Architecture, Security, Error Handling | **Anyone can switch paper to live trading** |
| Non-atomic JSON writes | Architecture, Error Handling, Production Readiness | **Crash = corrupted state = lost positions** |
| Two independent paper trading engines | Architecture, Code Quality | **Users see different P&L everywhere** |
| No tests whatsoever | Testing, every panel | **Every bug exists because nothing is verified** |

---

## TOP 10 ACTIONS -- IN ORDER

| # | Action | Risk Addressed | Effort |
|---|---|---|---|
| 1 | **Rotate ALL API keys immediately.** Move to env vars. Add `config.yaml` to `.gitignore`. Purge git history. | Account takeover | 1 hour |
| 2 | **Add authentication to all API routes.** Even a simple API key check in middleware.ts. | Full system takeover via 3 HTTP requests | 2-4 hours |
| 3 | **Fix the delta sign convention.** Remove `np.abs()` from `_estimate_delta`. Ensure puts have negative delta. | Core product doesn't work | 1 hour |
| 4 | **Replace ALL bare `except: pass`** with specific exception types + logging. | Silent failures lose money | 2 hours |
| 5 | **Implement atomic JSON writes** (write-to-temp + rename) and add error handling on JSON load with backup/recovery. | Data corruption on crash | 3-4 hours |
| 6 | **Add a shared data cache** -- fetch each ticker's data once per scan, pass through the pipeline. | 3-10 min scans become under 1 min | 1-2 days |
| 7 | **Unify the two paper trading systems** into one. Pick Python OR Next.js, not both. | Split-brain P&L, user confusion | 1-2 days |
| 8 | **Write tests for financial calculations** -- spread pricing, position sizing, P&L, OCC symbols. | Every calculation is unverified | 2-3 days |
| 9 | **Add a persistent Python API server** (FastAPI) instead of spawning subprocesses from Next.js. | 40-120s per web request | 2-3 days |
| 10 | **Create Dockerfile + CI/CD pipeline** with linting, type checking, and the new tests. | No deployment, no safety net | 1-2 days |

---

## APPENDIX: ALL FINDINGS BY SEVERITY

### CRITICAL (27)
- A-1: File-based IPC between Python and Next.js
- A-2: Unauthenticated config write endpoint
- A-3: Shell command execution from API routes
- A-4: Two independent paper trading engines
- A-5: API keys committed to source control
- A-6: Delta handling inconsistency breaks core product
- CQ-1: Hardcoded API key in polygon_provider.py
- CQ-2: Bare except clauses swallowing all errors
- CQ-3: _estimate_delta returns absolute delta
- S-1: Live API keys in plaintext
- S-2: Unauthenticated config write
- S-3: Zero authentication on all endpoints
- S-4: Paper-to-live boundary is single boolean
- P-1: Zero parallelism in scanning
- P-2: Same data fetched 3-5 times per ticker
- P-3: Market-wide data re-fetched per ticker
- P-4: Web API spawns Python subprocess per request
- EH-1: Bare except on price fetches (missed stop losses)
- EH-2: Bare except fabricates expiration dates
- EH-3: Non-atomic JSON writes
- EH-4: No error handling on JSON load
- EH-5: Unauthenticated config write
- EH-6: Unauthenticated process spawning
- EH-7: Hardcoded API key
- T-1 through T-7: Seven critical untested paths
- PR-1: System does not run continuously
- PR-2: Zero deployment infrastructure
- PR-3: API keys committed in plaintext

### HIGH (39)
- A-7 through A-16 (10 findings)
- CQ-4 through CQ-11 (8 findings)
- S-5 through S-8 (4 findings)
- P-5 through P-12 (8 findings)
- EH-8 through EH-17 (10 findings)
- T-8 through T-14 (7 findings)
- PR-4 through PR-8 (5 findings)

### MEDIUM (49)
- A-17 through A-26 (10 findings)
- CQ-12 through CQ-24 (13 findings)
- S-9 through S-12 (4 findings)
- P-13 through P-17 (5 findings)
- EH-18 through EH-25 (8 findings)
- T-15 through T-19 (5 findings)
- PR-9 through PR-13 (5 findings)

### LOW (20)
- A-25 (1 finding)
- CQ-25 through CQ-35 (11 findings)
- S-13 through S-16 (4 findings)
- P-18 through P-20 (3 findings)
- T-20 through T-21 (2 findings)
- PR-14 (1 finding)

---

*This review was conducted by 7 specialized AI agents analyzing the full codebase simultaneously. Each agent read 20-40+ files and focused on their domain expertise. Findings were cross-referenced and deduplicated where they appeared across multiple panels.*
