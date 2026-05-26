# Backtest vs Live Paper Trading Safety Audit

**Date:** 2026-03-10
**Auditor:** Claude Code (exhaustive line-by-line analysis)
**Scope:** backtest/backtester.py (source of truth) vs main.py → spread_strategy.py → alert_generator.py → alert_router.py → risk_gate.py → alert_position_sizer.py → execution_engine.py

---

## Executive Summary

The live scanner and backtester share **the same config YAML and many of the same helper modules**, but they follow fundamentally different code paths with multiple significant discrepancies. The backtester is a tightly-integrated simulation loop that controls entry, sizing, and exit with real historical data. The live path is an alert pipeline that adds an entirely different scoring layer (rules-based 0–100), filters at score ≥ 60 (AlertRouter) and score ≥ 28 (AlertGenerator), and relies on external monitoring (PositionMonitor) for exits.

**There are 7 CRITICAL discrepancies, 8 HIGH, 9 MEDIUM, and 5 LOW discrepancies documented below.**

---

## Summary Table of Discrepancies

| # | Dimension | Severity | Backtester Behavior | Live Scanner Behavior | Impact |
|---|-----------|----------|--------------------|-----------------------|--------|
| 1 | Scoring / entry gate | CRITICAL | No scoring system — entry decided by regime + credit threshold | 0–100 composite score; must be ≥ 60 to be routed | Live rejects many valid backtested trades |
| 2 | Min credit gate | CRITICAL | `min_credit_pct` applied to raw credit after slippage; config value (e.g. 15%) | Live `min_credit_pct` applies to credit before slippage via `spread_strategy.py` — different denominator | Divergent entry set |
| 3 | Iron condor max_loss formula | CRITICAL | Backtester: `max_loss = 2 × spread_width − combined_credit` (BOTH wings can lose) | Live `alert_position_sizer.py`: `max_loss = (spread_width − credit) × 100` ONE wing only | Live over-sizes IC positions by ~2×; understates true max risk |
| 4 | Macro event gate (FOMC/CPI/NFP) | CRITICAL | NOT APPLIED to entry — no event scaling in backtester entry path | Live has `macro_event_gate.py` with position-size scaling; but it is NOT wired into the scan path (only run_daily_event_check writes to DB; scanner never reads event_scaling_factor) | Event gate exists in code but is effectively dead in the live path too — neither path enforces it |
| 5 | Stop-loss threshold definition | CRITICAL | `stop_loss = credit × stop_loss_multiplier` (relative to credit received) | PositionMonitor: `threshold = min((1+mult)×credit, spread_width × 0.90)` — adds a hard 90%-of-width cap the backtester does not have | Live closes positions earlier than backtested |
| 6 | DTE management exit (live only) | CRITICAL | No DTE-based closure — positions held to expiration or profit/stop | PositionMonitor: closes at `manage_dte=21` days before expiration | Live exits ~21 days early; backtester holds through expiration; P&L profile fundamentally different |
| 7 | IC construction: legs used to price | CRITICAL | Backtester fetches real intraday bid/ask from Polygon for each leg, applies slippage from bar H-L | Live `spread_strategy._find_spreads()` reads `short_leg['bid'] - long_leg['ask']` from options chain (uses real-time Polygon/Tradier feed); no explicit slippage deduction | No slippage modeled in live IC entry; position may pay more than expected |
| 8 | Regime detection mode default | HIGH | `regime_mode` defaults to `'combo'` (ComboRegimeDetector) in backtester | `CreditSpreadStrategy.__init__` defaults to `regime_mode = 'hmm'` | Unless config says `regime_mode: combo`, live scanner uses HMM (or falls back) — opposite to backtester default |
| 9 | Trend MA period in live spread_strategy | HIGH | Backtester: `trend_ma_period` read from config (default 20, champion configs use 200) | Live `_check_bullish_conditions` / `_check_bearish_conditions` uses `technical_signals['trend']` from TechnicalAnalyzer; fast_ma=20, slow_ma=200 in config.yaml but the actual decision is `trend in ['bullish','neutral']` — not the same MA computation | Direction filter is structurally different |
| 10 | IV rank entry gate | HIGH | Backtester: `iv_rank_min_entry` gate — blocks when IV rank < threshold (default 0 = disabled) | Live: `min_iv_rank: 12` + `min_iv_percentile: 12` AND must pass both checks (`iv_check` OR condition) in `_check_bullish/bearish_conditions` | Backtester default has no IV floor; live config.yaml has 12% floor |
| 11 | Position sizing: IV-scaled path | HIGH | Backtester `iv_scaled` mode calls `calculate_dynamic_risk()` with 40% portfolio heat cap | Live `alert_position_sizer._legacy_size()` calls same function but adds `MAX_RISK_PER_TRADE × account_value` hard cap before the heat cap | Two-layer capping in legacy path vs one-layer in backtester |
| 12 | Options data source (live chain vs Polygon cache) | HIGH | Backtester uses Polygon historical options cache (SQLite) — real bid/ask from 5-min bars | Live uses `OptionsAnalyzer.get_options_chain()` → Polygon live API or Tradier — real-time quotes but different data structure (delta available); no slippage model | Pricing methodology differs; live has delta but no slippage; backtest has slippage but no delta (unless `use_delta_selection=true`) |
| 13 | Max positions per ticker | HIGH | Config `max_positions_per_ticker` enforced via `_open_keys` dedup set — per (expiration, strike, type) | Live: `RiskGate` rule 5.5 checks `max_positions_per_ticker` at ticker level only — doesn't distinguish expiration; AlertRouter dedup window is time-based (30 min) | Backtester deduplication is contract-level; live is ticker+direction-level within 30-min window |
| 14 | Drawdown circuit breaker reference | HIGH | Backtester: compound mode → peak equity (high-water mark); non-compound → starting_capital | Live `RiskGate`: always uses `peak_equity` from account_state regardless of compound/flat mode | In flat mode, live CB will never fire as early as backtester (starting_capital is always lower than peak) |
| 15 | Slippage: entry vs live | MEDIUM | Explicit slippage deducted from credit at entry: `prices.get('slippage') * multiplier`; varies by bar width | Live path: `CreditSpreadStrategy._find_spreads()` uses `short_leg['bid'] - long_leg['ask']` — natural bid/ask spread already captures some slippage, but no explicit additional slippage model | Live entry price likely more pessimistic (natural spread) but non-deterministic |
| 16 | Commission model | MEDIUM | Backtester: `commission_per_contract × 2` (entry only); exit commission deducted from pnl in `_record_close` | Live PositionMonitor: `commission_per_contract × contracts × num_legs × 2` (round trip) but defaults to 0 (`execution.commission_per_contract=0`) — paper trading | Default live commission is 0; backtester uses 0.65/contract; live paper trading is under-costing commissions |
| 17 | Profit target calculation | MEDIUM | `profit_target = credit × profit_target_pct` (fraction; config stores as %, e.g. 50 → 0.50) | PositionMonitor: `profit_target_pct = float(risk.profit_target, 50)` — **used as percentage (50 means 50%)** | Same semantic meaning but `spread_strategy` builds `profit_target = credit × risk.profit_target / 100` — correctly interprets the config. PositionMonitor checks `pnl_pct >= profit_target_pct` (50%) — this matches |
| 18 | VIX entry gate | MEDIUM | `vix_max_entry` gate in backtester (default 0 = disabled) — blocks entries when VIX > threshold | Live: no dedicated `vix_max_entry` check; VIX dynamic sizing in `AlertPositionSizer` reduces to 0 contracts at VIX ≥ quarter_below (25 by default) — effectively blocks at VIX ≥ 25 | Semantically similar but implemented differently; backtester uses hard block, live uses size=0 which then passes risk gate with contracts=0 — behavior may differ |
| 19 | Spread width selection (dynamic IV) | MEDIUM | Backtester: `spread_width` is a single config value — no dynamic IV-based width switching | Live `CreditSpreadStrategy._select_spread_width()`: width = `spread_width_high_iv` when IVR ≥ 50, `spread_width_low_iv` when IVR ≥ 25, else default | Live dynamically widens spreads in high IV; backtester always uses single width — different positions |
| 20 | Strike selection method | MEDIUM | OTM% method: target = `price × (1 ± otm_pct)`; finds closest available strike from Polygon contract list | Live: delta-range filter `min_delta/max_delta` (0.20–0.30) applied to options chain delta field | Different selection: OTM% vs delta range — will select different strikes; config `use_delta_selection: false` in config.yaml means live also uses delta range, not target-delta — but OTM% vs delta range are still different |
| 21 | Expiration selection / MWF logic | MEDIUM | Backtester: `_nearest_weekday_expiration` (all 5 days post-2022) or `_nearest_mwf_expiration` with Friday fallback; exact expiration date computed from current_date | Live: `_filter_by_dte` in `spread_strategy` filters from options chain by DTE range (min_dte=25, max_dte=45) — accepts ALL expirations in range | Live may enter multiple expirations in same scan; backtester targets one specific date per scan |
| 22 | Weekly loss breach sizing | MEDIUM | Not implemented in backtester — no weekly breach size reduction | Live `AlertPositionSizer`: 50% size reduction when `weekly_pnl_pct < -WEEKLY_LOSS_LIMIT` (15%) | Live is more conservative after bad week; backtester doesn't model this |
| 23 | COMPASS macro event scaling | MEDIUM | Backtester `_build_compass_series` reads `macro_state.db` for risk_appetite score → 1.2×/0.85× sizing multiplier | Live `_augment_with_compass_state` reads same DB; `AlertPositionSizer._portfolio_risk_size` applies macro scaling; but `_flat_risk_size` (used when portfolio_mode=false) does NOT apply macro scaling | In non-portfolio-mode flat-risk configs, live skips macro score scaling that backtester applies |
| 24 | Regime default starting state | LOW | ComboRegimeDetector starts at `BULL` (optimistic prior) | Live: `technical_signals['combo_regime'] = 'NEUTRAL'` on detector failure (defense-in-depth fallback) | On detector failure, behaviors diverge: backtester would continue with prior regime, live defaults to NEUTRAL |
| 25 | Friday/expiration week handling | LOW | Backtester explicitly counts and logs `_friday_fallback_count` | Live: no equivalent Friday fallback logic — uses whichever expirations exist in chain within DTE range | Backtester actively tries non-Friday expirations first; live accepts any in DTE range |
| 26 | Lookback for regime data | LOW | Backtester: `data_fetch_start = start_date - max(30, trend_ma_period × 1.4) + 15` calendar days extra | Live: `price_data = data_cache.get_history(ticker, period='2y')` fixed 2-year window | Same practical outcome for MA200 warmup, but different calculation method |
| 27 | Score threshold in AlertGenerator | LOW | Not applicable — backtester has no min_score concept | AlertGenerator filters at `min_score=28`; AlertRouter filters at score ≥ 60 — two separate thresholds on same pipeline | Two different thresholds create confusion about what "passes" |

---

## Detailed Section Analysis

### 1. Scoring Function and Thresholds

**Backtester:**
No scoring. Entry is decided solely by:
- Regime label (BULL/BEAR/NEUTRAL) from ComboRegimeDetector
- Momentum filter (10-day price change ≥ -momentum_filter_pct)
- Min credit floor: `spread_width × (min_credit_pct / 100)`
- VIX entry gate (`vix_max_entry`, default disabled)
- IV rank entry gate (`iv_rank_min_entry`, default disabled)

**Live Scanner:**
`CreditSpreadStrategy._score_opportunities()` computes a 0–100 composite score:
- Credit component (0–25 pts): `credit_pct × 0.5`, capped at 25
- Risk/reward component (0–25 pts): `risk_reward × 8`, capped at 25
- POP component (0–25 pts): `(pop / 85) × 25`, capped at 25
- Technical alignment (0–15 pts): trend/regime matching
- IV rank component (0–10 pts): `iv_rank / 10`

Then filtered:
- `AlertGenerator.generate_alerts()`: requires score ≥ 28 (min_alert_score)
- `AlertRouter.route_opportunities()`: requires score ≥ 60

**Discrepancy (CRITICAL):**
The backtester never uses this scoring function. A trade that scores 55/100 in the live scanner would be rejected by AlertRouter (< 60) but would happily enter in the backtester if regime and credit conditions pass. The live scanner requires both regime-based conditions AND a high composite score. Trades that pass backtester criteria can easily fail the live 60-point threshold.

The double-filtering (AlertGenerator at 28 + AlertRouter at 60) also means the AlertGenerator step is effectively dead code — anything AlertGenerator accepts at ≥28 that is < 60 gets rejected by AlertRouter anyway.

**Recommended Fix:**
Either lower AlertRouter threshold to match backtester criteria (removing scoring as a gate), or add an equivalent scoring filter to the backtester so results reflect live selectivity.

---

### 2. Position Sizing (Kelly, flat, compound)

**Backtester (`_find_real_spread`):**
- `sizing_mode='flat'`: `trade_dollar_risk = account_base × (max_risk_per_trade / 100)`
  - `account_base = self.capital` if compound else `self.starting_capital`
  - VIX dynamic scaling applied if `vix_dynamic_sizing` configured
  - COMPASS multiplier applied if `compass_enabled`
  - Seasonal multiplier applied
- `sizing_mode='iv_scaled'`: calls `calculate_dynamic_risk()` with 40% heat cap

**Live Scanner (`AlertPositionSizer._flat_risk_size`):**
- `sizing_mode='flat'`: `account_base = starting_capital` if flat, else `account_value`
- VIX dynamic scaling applied via `_compute_vix_scale()`
- Weekly loss breach → 50% size reduction (NOT in backtester)
- COMPASS macro scaling only in `_portfolio_risk_size()` (portfolio_mode=true); NOT in `_flat_risk_size()`

**Discrepancy (HIGH):**
The flat-risk path is broadly consistent but:
1. Backtester applies COMPASS macro score multiplier in non-portfolio-mode; `_flat_risk_size` does not. Only `_portfolio_risk_size` applies macro scaling.
2. Weekly loss breach (50% reduction) exists in live but not backtester.
3. The seasonal multiplier (`_current_seasonal_mult`) in backtester has no live equivalent.

**Iron Condor Sizing (CRITICAL):**
- Backtester: `max_loss = 2 × spread_width − combined_credit` (worst case BOTH wings ITM simultaneously)
- Live `_flat_risk_size`: `max_loss_per_spread = (spread_width − credit) × 100` (ONE wing)

This means live IC position sizes are approximately 2× too large relative to true risk. For a $5-wide IC at 2× combined credit, backtester max_loss per spread = $8, live max_loss = $4. Live will calculate ~2× as many contracts.

**Recommended Fixes:**
1. Add weekly loss breach logic to backtester to accurately model live behavior.
2. Fix IC max_loss formula in `_flat_risk_size` to use `2 × spread_width - combined_credit`.
3. Add COMPASS macro scaling to `_flat_risk_size` path.

---

### 3. Entry Filters (Delta, DTE, IV Rank, Min Credit, Spread Width)

**Backtester:**
- DTE: `_nearest_weekday_expiration(date, target_dte=35, min_dte=25)` — computes specific date
- Strike: OTM% method: `target_short = price × (1 - otm_pct)` (default 5%)
  - OR delta-based if `use_delta_selection=True`
- Min credit: `spread_width × (min_credit_pct / 100)` applied AFTER slippage deduction
- IV rank gate: `iv_rank_min_entry` (default 0 = disabled)
- No spread_width dynamic switching

**Live Scanner (`spread_strategy._find_spreads`):**
- DTE: `_filter_by_dte()` returns ALL expirations in `[min_dte, max_dte]` range — may find multiple
- Strike: delta range filter `min_delta=0.20, max_delta=0.30` (not OTM%) by default
- Min credit: `spread_width × (min_credit_pct / 100)` applied to `short_leg['bid'] - long_leg['ask']` — BEFORE slippage (no explicit slippage deduction in live path)
- IV rank gate: min_iv_rank=12 (AND gate with min_iv_percentile=12) as OR condition
- Spread width: dynamic — `spread_width_high_iv` (10) when IVR ≥ 50, `spread_width_low_iv` (5) when IVR ≥ 25

**Discrepancies (MEDIUM):**
1. Strike selection: OTM% (backtester) vs delta-range filter (live) produce different strikes
2. Min credit applied before vs after slippage
3. Live may evaluate multiple expirations per scan; backtester targets one
4. Dynamic spread width in live vs fixed in backtester

---

### 4. Regime Detection (MA, Combo, COMPASS)

**Backtester:**
- `regime_mode` defaults to `'combo'` (line 407: `self._regime_mode = self.strategy_params.get('regime_mode', 'combo')`)
- ComboRegimeDetector: 3 signals (price_vs_ma200, rsi_momentum, vix_structure)
- Asymmetric voting: BULL=2/3, BEAR=3/3
- Hysteresis: `cooldown_days=10`
- VIX circuit breaker: VIX > 40 → BEAR
- All signals use prior-day data (no lookahead via `.shift(1)`)
- Series computed once over entire backtest window; very efficient

**Live Scanner (`CreditSpreadStrategy.__init__`):**
- `regime_mode` defaults to `'hmm'` (line 59: `self.regime_mode = self.strategy_params.get('regime_mode', 'hmm')`)
- When `regime_mode='combo'`: same ComboRegimeDetector class instantiated
- Regime computed in `_analyze_ticker` by calling `compute_regime_series()` on the last 2 years of data, then taking `last_key = max(regime_series.keys())` for current regime

**Discrepancy (HIGH/CRITICAL):**
The **defaults are opposite**:
- Backtester defaults to `'combo'`
- Live scanner defaults to `'hmm'`

This means unless `regime_mode: combo` is explicitly set in config.yaml (it IS set in paper_exp305.yaml but NOT in config.yaml), the live scanner falls back to the HMM model — a completely different signal. config.yaml (the default config) does not have `regime_mode` in the strategy section, so the live scanner uses 'hmm'.

Additionally, in `_analyze_ticker` when ComboRegimeDetector fails, live sets `technical_signals['combo_regime'] = 'NEUTRAL'` (conservative), while the backtester would continue with the last valid `current_regime` state.

**Recommended Fix:**
Set `regime_mode: combo` in the default `config.yaml` strategy section to match the backtester default.

---

### 5. Deduplication / Trade Frequency Controls

**Backtester:**
- `_entered_today` set: prevents same (exp, strike, type) in multiple intraday scans on same day
- `_open_keys` set: prevents opening position if same (exp, strike, type) already open across days
- Max positions: `risk_params['max_positions']` hard check before each scan time

**Live Scanner (AlertRouter):**
- `_dedup_ledger`: (ticker, direction) → last_routed_at; 30-minute window (`_DEDUP_WINDOW = 30 * 60`)
- Persisted to SQLite (`alert_dedup` table) across restarts
- Within-scan dedup re-check prevents multiple same-ticker/direction in one batch
- Key: `(ticker, direction.value)` — e.g., `('SPY', 'bullish')`

**Discrepancy (HIGH):**
Backtester dedup is contract-level: `(expiration_date, strike_price, option_type)`. Live dedup is coarser: `(ticker, direction)` within 30 minutes. This means:
1. Live could open two different SPY bull puts at different strikes within 30 min (first is deduped, second blocked — actually prevents this correctly).
2. But live will NOT prevent same (ticker, direction) from re-opening after 30 minutes if market is still open, while backtester's `_open_keys` prevents re-opening if that expiration/strike is still active.
3. Different SPY expirations on the same day would be blocked by live dedup but allowed by backtester.

---

### 6. Max Positions Per Ticker / Total

**Backtester:**
- `risk_params['max_positions']` (e.g., 50) — total open positions
- `_open_keys` prevents duplicate (expiration, strike, type) entries
- No explicit per-ticker limit — `max_positions_per_ticker` config key exists but is NOT read by backtester; only used in live `RiskGate`

**Live Scanner:**
- `RiskGate` rule 5.5: `max_positions_per_ticker` from `config.risk.max_positions_per_ticker` (set to 2 in config.yaml)
- `MAX_CORRELATED_POSITIONS = 3` hardcoded constant — limits same-direction positions

**Discrepancy (HIGH):**
The backtester does NOT enforce `max_positions_per_ticker`. Config.yaml has `max_positions_per_ticker: 2`. Live enforces this strictly. In a BULL regime with multiple scan times, the backtester can open many more SPY bull puts than live would allow.

---

### 7. Stop Loss / Profit Target Logic

**Backtester:**
- `stop_loss = credit × stop_loss_multiplier` (config value, e.g., 2.5)
- Exit fires when `spread_value - credit >= stop_loss` (i.e., loss ≥ 2.5× credit received)
- Profit target: `credit × profit_target_pct` (50% by default)
- Exit fires when `credit - spread_value >= profit_target`
- Intraday exit check: uses actual 30-min bar prices from Polygon historical cache
- VIX-scaled exit slippage applied on all exits

**Live Scanner (PositionMonitor):**
- `stop_loss_mult = float(risk.stop_loss_multiplier, 3.5)` — default 3.5 vs backtester config 2.5
- Threshold: `min((1.0 + mult) × credit, spread_width × 0.90)` — 90% of width cap
- This 90% cap is not in the backtester
- `profit_target_pct` = 50% (matches backtester)
- Exit fire: `pnl_pct >= profit_target_pct` — correct

**Discrepancy (CRITICAL):**
1. `stop_loss_multiplier` default in PositionMonitor is 3.5, but config.yaml says 2.5 and paper_exp305.yaml says 3.5. Which value is used depends on whether config is properly passed to PositionMonitor.
2. The 90%-of-width backstop (`spread_width × 0.90`) causes earlier exits than backtester. For a $5-wide spread at $0.50 credit: backtester stops at `$0.50 × 2.5 = $1.25` loss; live stops at `min($0.50 × 3.5 = $1.75, $5 × 0.90 = $4.50) = $1.75` loss. At credit = $0.25: backtester stops at $0.625; live stops at min($1.125, $4.50) = $1.125.
3. Backtester applies VIX-scaled exit slippage; live uses actual fill prices from Alpaca.

---

### 8. Risk Gate Rules (Daily Loss, Drawdown, Circuit Breaker, Correlation)

**Backtester:**
- Drawdown CB: `_cb_threshold = -abs(drawdown_cb_pct) / 100` (config, e.g., 20%)
  - Compound mode: peak equity reference; non-compound: starting_capital reference
- No daily loss limit
- No weekly loss limit
- No correlation position limit
- VIX too high: `vix_max_entry` gate (optional)
- Ruin stop: blocks entries when capital ≤ 0

**Live Scanner (RiskGate):**
- Rule 0: Circuit breaker (Alpaca unavailable) → block ALL
- Rule 1: Per-trade risk cap: `alert.risk_pct > max_risk_per_trade` → block
- Rule 2: Total exposure: `open_risk + alert.risk_pct > max_total_exposure` (15% default, configurable)
- Rule 3: Daily loss limit: `daily_pnl_pct < -8%` → block all alerts for day
- Rule 4: Weekly loss limit: 15% → 50% size reduction flag (doesn't block)
- Rule 5: Same-direction positions ≥ 3 (`MAX_CORRELATED_POSITIONS`) → block
- Rule 5.5: Per-ticker positions ≥ `max_positions_per_ticker` → block
- Rule 6: Cooldown after stop-out: 30 min
- Rule 7: Drawdown CB (config-driven, default 0 = disabled in RiskGate unless set)
- Rules 8-10: COMPASS portfolio limits (optional)

**Discrepancies (CRITICAL/HIGH):**
1. Daily loss limit (8%) and weekly loss limit (15%) do NOT exist in the backtester
2. 30-minute stop-out cooldown does NOT exist in the backtester
3. Total exposure cap (15%) is different from backtester's `max_portfolio_exposure_pct` (default 100%) — a major sizing gap
4. Correlation limit (same-direction ≤ 3) does NOT exist in backtester
5. Drawdown CB reference: live always uses peak_equity; backtester uses starting_capital in non-compound mode

**Recommended Fix:**
The backtester should model daily loss limit, weekly loss limit, and correlation limit to better simulate live behavior. Alternatively, acknowledge the backtester is an "optimal" bound without these constraints.

---

### 9. Database Interaction

**Backtester:**
- Uses SQLite cache at `data/options_cache.db` for Polygon historical data (read-only)
- Uses `data/macro_state.db` for COMPASS data (read-only)
- Writes results to in-memory `self.trades` list; dumps to JSON at end
- No real-time DB writes during simulation

**Live Scanner:**
- Primary DB: `data/pilotai.db` (or `ATTIX_DB_PATH` env var, isolated per experiment)
- `AlertGenerator.generate_alerts()` → `insert_alert()` → alerts table
- `ExecutionEngine.submit_opportunity()` → `upsert_trade()` → trades table (pending_open)
- `PositionMonitor._record_close_pnl()` → `close_trade()` → trades table
- `AlertRouter._mark_dedup()` → `upsert_dedup_entry()` → alert_dedup table
- Paper configs use separate DB files per experiment (e.g., `pilotai_exp305.db`)

**Discrepancy (MEDIUM):**
Backtester does not write trade-level data to the shared SQLite DB. Live trades are in SQLite. The `DeviationTracker` in `shared/deviation_tracker.py` is intended to compare live vs backtest but the backtester never writes to pilotai.db, so comparison must be done manually.

The deploy path uses `deploy/macro-api/shared/macro_state_db.py` — a separate copy that may diverge from `shared/macro_state_db.py`.

---

### 10. Options Chain Data Source

**Backtester:**
- `HistoricalOptionsData` (Polygon historical API + SQLite cache): real historical bid/ask from 5-min bars
- Strike list from `option_contracts` table → exact available strikes on that date
- Intraday 5-min bar: `get_intraday_spread_prices()` — models entry at specific scan time
- Daily close: `get_spread_prices()` fallback
- Slippage modeled from bar H-L: `(high - low) / 2` per leg

**Live Scanner:**
- `OptionsAnalyzer.get_options_chain()` → Polygon live API (or Tradier)
- Full chain with delta, bid, ask from real-time quotes
- No slippage deduction; uses natural bid/ask (`short_leg['bid'] - long_leg['ask']`)

**Discrepancy (HIGH):**
Live gets real-time delta values (enabling delta-based selection natively); backtester must approximate delta from Black-Scholes. Live spread pricing is bid/ask mid (natural spread); backtester uses mid with explicit slippage model. These produce different entry credits for identical market conditions.

---

### 11. Order Execution (Market vs Limit, Slippage)

**Backtester:**
- No actual order submission
- Entry credit deducted from simulated capital; commission deducted from capital immediately
- Slippage modeled: `prices.get('slippage', self.slippage) × slippage_multiplier` subtracted from credit
- Exit slippage: `_vix_scaled_exit_slippage()` added to exit cost

**Live Scanner (ExecutionEngine):**
- `alpaca.submit_credit_spread()` with `limit_price=credit` (the natural bid/ask mid)
- No explicit slippage — relies on broker fill vs limit price
- If limit is not filled (Alpaca may fill at different price), no adjustment
- PositionMonitor closes with `limit_price=None` (market order) on exits

**Discrepancy (MEDIUM):**
Entry: limit orders at mid (live) vs explicit slippage subtraction (backtest). Exit: market orders (live) vs VIX-scaled slippage model (backtest). Live exit market orders may get worse fills in fast markets; backtest applies VIX-scaled friction as proxy.

---

### 12. Position Monitoring / Exit Logic

**Backtester:**
- `_manage_positions()` called daily; checks expiration, intraday profit/stop, daily close
- 30-min intraday scan times mirror live scanner cadence
- Expired positions: uses Polygon historical prices or underlying price for settlement

**Live Scanner (PositionMonitor):**
- Runs every 5 minutes (`_CHECK_INTERVAL_SECONDS = 300`)
- Additional DTE-based exit at `manage_dte=21` — NOT in backtester
- Reconciles pending_open → open via Alpaca polling
- Detects external closes, orphan positions, and assignment
- P&L from actual Alpaca fill price; backtester from model

**Discrepancy (CRITICAL):**
The DTE management exit (`manage_dte=21`) is the biggest structural difference. Backtester holds positions to expiration (or profit/stop triggers). PositionMonitor closes at 21 DTE. This changes the entire P&L distribution:
- A 35 DTE position never gets to expiration in live — it always closes at 21 DTE (14 days early)
- Positions that would expire worthless (full profit) in backtester may be closed at mid-premium at 21 DTE (partial profit)
- Avoids pin risk and gamma risk; reduces average holding period
- Backtester results are NOT equivalent to live trading on this dimension alone

---

### 13. Iron Condor Construction (Leg Selection, Width, Credit Calc)

**Backtester (`_find_iron_condor_opportunity`):**
- Calls `_find_real_spread` for put leg (min_credit_override=0.0, skip_commission)
- Uses same expiration for both legs (call leg fetches from put_leg['expiration'])
- Validates non-overlapping: `put_leg['short_strike'] < call_leg['short_strike']`
- Combined credit minimum: `(2 × spread_width) × (min_combined_credit_pct / 100)`
- Max loss: `2 × spread_width − combined_credit` (BOTH wings can lose)
- Commission: 4 legs at entry, 4 legs at exit (total 8 legs)

**Live Scanner (`spread_strategy.find_iron_condors`):**
- Calls `_find_spreads` for both wings from same option chain
- Pairs top-3 bull puts with top-3 bear calls (O(N^2) bounded at 9 pairs max)
- Validates non-overlapping: `bp['short_strike'] < bc['short_strike']`
- Combined credit minimum: `(combined_credit / spread_width) × 100 < min_combined_credit_pct`
  - **Note: denominator is single spread_width, not 2×**
- Max loss: `spread_width − combined_credit` (ONE wing only — WRONG)
- Credit cap: `if combined_credit > spread_width × 0.50: combined_credit = spread_width × 0.35`
  - This synthetic credit cap does not exist in backtester

**Discrepancy (CRITICAL):**
1. Min credit check uses different denominator: live uses single `spread_width`; backtester uses `2 × spread_width`. For a $5-wide IC with 20% min_combined_credit_pct: backtester requires $2.00 combined; live requires $1.00 combined — live enters 2× more ICs than backtester would.
2. Max loss formula: backtester uses `2 × width − credit`; live uses `width − credit`. Live understates risk by 2× for ICs.
3. Credit cap (`min(combined, width × 0.35)`) in live has no backtester equivalent.

---

### 14. Config Loading and Parameter Resolution

**Backtester:**
- Loaded as Python dict from JSON files (e.g., `configs/exp_090_risk10_nocompound_newcode.json`) via `run_optimization.py`
- Parameters accessed directly: `config['strategy']`, `config['risk']`, `config['backtest']`
- Per-experiment JSON configs in `configs/` folder

**Live Scanner:**
- Loaded from YAML via `utils.load_config()` → `config.yaml` by default
- Paper experiment configs in `configs/paper_expXXX.yaml`
- Env var substitution for `${ALPACA_API_KEY}` etc.
- `ATTIX_DB_PATH` env var for isolated DB path

**Discrepancy (MEDIUM):**
Backtester uses JSON configs; live uses YAML. The JSON experiment configs do not always have a 1:1 mapping to YAML paper configs. For example, exp_090 JSON has `drawdown_cb_pct=20` while paper_exp305.yaml has `drawdown_cb_pct=30`. There is no automated validation that a paper config matches its corresponding backtest config.

Additionally, `config.yaml` has parameters not present in most JSON backtest configs (e.g., `alerts`, `alpaca`, `data`, `logging`). The backtester ignores these but they exist in the dict.

---

### 15. Account State / Equity Tracking

**Backtester:**
- `self.capital` tracks cash: starts at `starting_capital`, modified by commission, PnL
- `self._peak_capital` tracks high-water mark for drawdown CB
- `self.equity_curve`: daily (date, total_equity) including unrealized MTM value of open positions

**Live Scanner:**
- `_build_account_state()` reads from Alpaca: `portfolio_value` = real account equity
- Daily/weekly PnL computed from closed trades in SQLite
- `self._peak_equity` tracked in-process (resets on restart)
- `open_positions[].risk_pct` computed from spread geometry, not real MTM value

**Discrepancy (HIGH):**
1. `peak_equity` resets on process restart in live — DB does not persist it. After a restart, drawdown CB may not fire correctly until the peak is re-established.
2. Backtester uses full MTM equity (including unrealized). Live account state uses `portfolio_value` from Alpaca which is real MTM — this is consistent, but the `risk_pct` per position is estimated from geometry, not actual market value.
3. When Alpaca is unavailable, live falls back to `starting_capital` with `circuit_breaker=True` — backtester has no equivalent fail-safe.

---

### 16. Cooldown Periods Between Trades

**Backtester:**
- No cooldown between trades — same ticker can re-enter immediately after a stop-out
- Only dedup by (expiration, strike, type) prevents same contract re-entry

**Live Scanner:**
- `COOLDOWN_AFTER_STOP = 30 * 60` (30 minutes) enforced by `RiskGate` rule 6
- `recent_stops` list tracks stop-out events from last 7 days in `_build_account_state`

**Discrepancy (MEDIUM):**
Backtester can immediately re-enter after a stop, which is the optimistic scenario. Live has a 30-minute cooldown. In volatile markets with multiple same-day stop-outs, this could significantly reduce live trade count vs backtested.

---

### 17. Friday/Expiration Week Handling

**Backtester:**
- Explicit expiration selection logic: `_nearest_weekday_expiration` (post-2022), `_nearest_mwf_expiration` (pre-2022)
- Friday fallback: if primary expiration has no Polygon data, tries nearest Friday
- `_friday_fallback_count` tracks this for diagnostics

**Live Scanner:**
- `_filter_by_dte()` accepts ALL expirations in `[min_dte, max_dte]` range from options chain
- No Friday-specific logic — takes whatever the broker returns

**Discrepancy (LOW):**
Live may enter Mon/Wed/Fri/Tue/Thu expirations without preference ordering. Backtester tries specific dates in sequence. In practice this should be similar since Polygon and live chains should both show the same available expirations.

---

### 18. VIX / Volatility Gates

**Backtester:**
- `_iv_too_low`: `iv_rank < iv_rank_min_entry` (default 0 = disabled)
- `_vix_too_high`: `vix > vix_max_entry` (default 0 = disabled)
- `vix_close_all`: force-close all positions when VIX > threshold (optional)
- VIX dynamic sizing: reduces contract count at elevated VIX levels

**Live Scanner:**
- `min_iv_rank: 12` + `min_iv_percentile: 12` in spread_strategy as OR gate
- VIX gate: only via `AlertPositionSizer._compute_vix_scale()` — returns 0 contracts at VIX ≥ quarter_below (default 25), but this is a sizing action, not a hard block. An alert with 0 contracts could still pass RiskGate (risk_pct=0 passes rule 1).
- No `vix_close_all` equivalent in live (force-close on VIX spike would need separate logic)

**Discrepancy (MEDIUM):**
1. Live has IV rank floor (12); backtester default is 0 (no floor)
2. Live VIX gate is a sizing gate (size=0); backtester has option for hard block
3. `vix_close_all` behavior (force-close all positions on VIX spike) has no live equivalent

---

### 19. Hardcoded Values vs Config-Driven Values

**Hardcoded in Live (not overridable by config):**
- `MAX_RISK_PER_TRADE = 0.05` (5%) in `shared/constants.py` — but this IS overridable via `config.risk.max_risk_per_trade` in RiskGate
- `DAILY_LOSS_LIMIT = 0.08` (8%) — hardcoded, not configurable per-experiment
- `WEEKLY_LOSS_LIMIT = 0.15` (15%) — hardcoded, not configurable per-experiment
- `MAX_CORRELATED_POSITIONS = 3` — hardcoded, not configurable
- `COOLDOWN_AFTER_STOP = 30 * 60` (30 min) — hardcoded
- `_DEDUP_WINDOW = 30 * 60` (30 min) — hardcoded in alert_router
- AlertRouter top-5 limit: `approved[:5]` — hardcoded, not configurable
- Score threshold in AlertRouter: 60 — hardcoded in `route_opportunities()`

**Backtester config-driven equivalents:**
- All risk parameters from config dict
- No daily/weekly loss limits (they're backtest parameters, not implemented)
- `max_positions` from config
- No dedup window concept

**Discrepancy (MEDIUM):**
Several live operational constants are hardcoded and can never be adjusted in backtesting. The backtester cannot simulate a 30-minute stop cooldown or a daily loss limit exactly matching live behavior because those are implemented as hardcoded constants in live-only code.

---

### 20. Error Handling / Fallback Behavior

**Backtester:**
- Missing Polygon data: tries adjacent strikes (+/-1, +/-2)
- No data for expiration: uses underlying price for settlement
- VIX data failure: defaults to IV rank=25
- Log warnings; never crashes the run

**Live Scanner:**
- ComboRegimeDetector failure → `technical_signals['combo_regime'] = 'NEUTRAL'` (conservative)
- Alpaca unavailable → `circuit_breaker=True` → all trades blocked
- AlertGenerator failure → non-fatal (`try/except` in `_generate_alerts`)
- Alert router failure → non-fatal
- PositionMonitor API failure → retries, escalates at 3 consecutive failures
- WAL write on DB failure in close_trade

**Discrepancy (LOW):**
Backtester is optimistic on data gaps (continues with fallback). Live is conservative (blocks on Alpaca failure). This means live performance in market microstructure scenarios may diverge from backtest.

---

## Architectural Issues

### A. Dual-Database Architecture
**Issue:** Two separate SQLite databases exist in the system:
1. `data/pilotai.db` (or per-experiment path) — trades, alerts, dedup entries
2. `data/macro_state.db` — COMPASS macro scores, sector rankings, event scaling

The backtester reads from `macro_state.db` for COMPASS data but never writes to `pilotai.db`. The live scanner writes to `pilotai.db` and reads from both. There is no automated integrity check ensuring `macro_state.db` has current data when a live scan runs.

**Risk:** If `macro_state.db` is stale or empty (e.g., on a new deployment), COMPASS signals are silently disabled rather than failing loudly. The backtester also silently disables COMPASS in this scenario.

### B. Deploy Path Code Duplication
**Issue:** `deploy/macro-api/shared/macro_event_gate.py` and `deploy/macro-api/shared/macro_state_db.py` are copies of the files in `shared/`. The git diff shows both have been modified, meaning they may have diverged.

**Risk:** Production deploy uses different macro gate logic than the code being tested locally. Specifically, the deploy path's `macro_event_gate.py` may have different FOMC dates or scaling factors than `shared/macro_event_gate.py`.

### C. Macro Event Gate Not Wired Into Entry Path
**Issue:** `shared/macro_event_gate.py` computes FOMC/CPI/NFP scaling factors. `run_daily_event_check()` writes the scaling factor to `macro_state.db` key `event_scaling_factor`. However, **no code in the live scan path reads `event_scaling_factor` and applies it to position sizing**.

The `_augment_with_compass_state()` in main.py does not read event scaling. `AlertPositionSizer` does not read event scaling. The macro event gate is fully implemented but completely disconnected from the live scan path.

**The backtester also does not implement macro event scaling** — so in this case both paths are consistent (both missing it). But the live path has infrastructure that appears to promise scaling that never actually happens.

### D. Shared Alpaca Account Across Multiple Experiments
**Issue:** Multiple paper experiments (exp036, exp059, exp154, exp305) each have separate `.env` files and separate SQLite databases, but they all use the same Alpaca paper trading account (credentials from the env file). Positions opened by exp059 are visible to exp305's PositionMonitor, which could:
- Incorrectly attempt to close positions belonging to another experiment
- Report orphan positions from other experiments
- Report inflated account equity that includes other experiments' positions

The DB isolation (separate `pilotai_expXXX.db`) prevents double-entry in accounting, but Alpaca sees all positions together.

### E. Peak Equity Reset on Restart
**Issue:** `CreditSpreadSystem._peak_equity` is an in-process variable initialized to `config.risk.account_size`. On process restart, this resets to `starting_capital` regardless of actual peak. The drawdown circuit breaker will not fire correctly until the peak is re-established.

**Recommended Fix:** Persist `peak_equity` to `macro_state.db` or `pilotai.db` and restore on startup.

---

## Priority Recommended Fixes

### Immediate (CRITICAL — affects live capital at risk)

1. **Fix IC max_loss formula in live** (`alert_position_sizer.py`, line 184/305):
   - Change `max_loss_per_spread = (spread_width - credit) × 100` to `(2 × spread_width - credit) × 100`
   - IC positions are currently 2× oversized

2. **Fix IC min_combined_credit_pct denominator** (`spread_strategy.find_iron_condors`, line 365):
   - Change `(combined_credit / spread_width) × 100 < min_combined_credit_pct`
   - To `(combined_credit / (2 × spread_width)) × 100 < min_combined_credit_pct`
   - Live enters 2× more ICs than backtester intends

3. **Wire manage_dte=21 into backtester** (or acknowledge it as an untested assumption):
   - Backtester holds to expiration; live closes at 21 DTE — fundamentally different P&L
   - Add `manage_dte` exit logic to backtester to accurately model live behavior

4. **Align regime_mode default** (`spread_strategy.py`, line 59):
   - Change default from `'hmm'` to `'combo'` to match backtester default

### High Priority

5. **Add weekly loss breach to backtester** to model live 50% size reduction
6. **Add DTE management exit to backtester** to match PositionMonitor's `manage_dte`
7. **Persist peak_equity to DB** across process restarts
8. **Align stop_loss_multiplier defaults** — PositionMonitor hardcodes 3.5 as default; config.yaml says 2.5
9. **Evaluate score threshold** — 60/100 minimum in AlertRouter is not validated against backtester; many valid trades may be rejected

### Medium Priority

10. **Wire macro event gate into live scan** — or remove the infrastructure if not used
11. **Add COMPASS macro scaling to `_flat_risk_size`** path (currently only in `_portfolio_risk_size`)
12. **Audit deploy/ path files** against `shared/` — resolve divergence in macro_event_gate.py and macro_state_db.py
13. **Document Alpaca account sharing** — make clear that multiple experiments share one account
14. **Add backtester config validation** that warns when a paper config's key params differ from corresponding backtest config

---

## Files Audited

| File | Role |
|------|------|
| `backtest/backtester.py` | Source of truth — full simulation engine |
| `main.py` | Live entry point — scan, regime detection, account state |
| `strategy/spread_strategy.py` | Live opportunity finder, scoring, IC construction |
| `alerts/alert_generator.py` | Alert formatting, score >= 28 filter, DB insert |
| `alerts/alert_router.py` | Pipeline: dedup, sizing, risk gate, execution |
| `alerts/risk_gate.py` | Hard risk rules: daily limit, drawdown CB, correlation |
| `alerts/alert_position_sizer.py` | Flat/portfolio/legacy sizing modes |
| `alerts/alert_schema.py` | Alert dataclass, Direction enum |
| `execution/execution_engine.py` | Alpaca order submission |
| `execution/position_monitor.py` | Position management: DTE exit, profit/stop, reconcile |
| `ml/combo_regime_detector.py` | Multi-signal regime classifier (BULL/BEAR/NEUTRAL) |
| `ml/position_sizer.py` | IV-scaled position sizer, get_contract_size |
| `shared/constants.py` | Hardcoded limits: DAILY_LOSS_LIMIT, MAX_RISK_PER_TRADE |
| `shared/macro_event_gate.py` | FOMC/CPI/NFP scaling (disconnected from live path) |
| `shared/macro_state_db.py` | COMPASS DB schema and read API |
| `shared/database.py` | Trades/alerts SQLite DB |
| `config.yaml` | Default live config |
| `configs/paper_exp305.yaml` | COMPASS portfolio paper config |
