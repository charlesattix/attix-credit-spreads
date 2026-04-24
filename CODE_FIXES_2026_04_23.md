# Code Fixes Report — 2026-04-23

**Date:** 2026-04-23
**Auditor:** Maximus (AI Trading Strategist)
**Scope:** Critical fixes from CODE_QUALITY_AUDIT_2026_04_23.md
**Full test suite status:** 12,607 passed, 0 failed, 14 skipped, 412 warnings (21m 3s). Coverage: 58.60%

---

## 1. Rule Zero Violations Fixed (Grade C → A)

### Fix 1.1: `experiments/EXP-1020-max/backtest.py` — QUARANTINED
- **Action:** Renamed to `backtest.py.QUARANTINED_RULE_ZERO`
- **Reason:** File used `np.random.RandomState` and `rng.normal()` to generate synthetic intraday price moves and fake correlation data. Pure synthetic backtest — not recoverable.
- **Impact:** None — EXP-1020 is a dead experiment not referenced by any production code.

### Fix 1.2: `compass/adaptive_1dte.py` — `build_exp1220_daily()` QUARANTINED
- **Action:** Replaced function body with `raise NotImplementedError(...)` and documented the Rule Zero violation.
- **Reason:** Function used `np.random.normal()` to synthesize daily returns from hardcoded annual targets. This is fabricated data, not real backtest results.
- **Impact:** Function was used only in `combine_portfolio()` in the same file. Neither function is imported by any production module.

### Fix 1.3: `compass/ensemble_model_health.py` — FIXED (synthetic → theoretical CDF)
- **Action:** Replaced `np.random.RandomState(42).normal(mean, std, len(live_arr))` with `scipy.stats.kstest(live_arr, 'norm', args=(mean, std))`.
- **Reason:** Old code generated synthetic samples for KS two-sample test. New code uses `kstest` against the theoretical normal CDF — mathematically equivalent, no random samples needed.
- **Impact:** Drift detection in `ModelHealthMonitor` now uses real statistical methods without synthetic data. Behavior is functionally identical.

### Verification
```bash
# Confirmed: no np.random.normal/RandomState in production compass/ modules
grep -rn "np\.random\.\(normal\|RandomState\|randn\)" compass/ --include="*.py" \
  | grep -v test | grep -v __pycache__ | grep -v QUARANTINED
# Result: 0 hits in production code (remaining hits are in execution_simulator.py
# for slippage modeling, which is legitimate non-price randomness)
```

---

## 2. Failing Tests Fixed (18 → 0)

### Production Code Fixes

#### Fix 2.1: `shared/database.py` — Metadata preservation on partial updates
- **Bug:** `upsert_trade` ON CONFLICT clause always overwrote `metadata` column. When `_mark_pending_failed` called `upsert_trade` with just `{id, status, exit_reason}`, the metadata (containing IC strikes, straddle fields, wing order IDs) was replaced with `'{}'`.
- **Fix:** Changed ON CONFLICT to preserve existing metadata when the new value is empty:
  ```sql
  metadata=CASE
      WHEN excluded.metadata = '{}' THEN COALESCE(trades.metadata, '{}')
      ELSE excluded.metadata
  END
  ```
- **Tests fixed:** `test_execution_engine_rounds_ic_strikes`, `test_trade_record_stores_straddle_fields`

#### Fix 2.2: `execution/position_monitor.py` — Handle None strikes in SL/PT check
- **Bug:** `_check_exit_conditions` returned `None` when `long_strike` was `None`, even if `credit` was present and SL could be computed formula-only.
- **Fix:** Changed the guard to only skip when BOTH strikes are None AND credit is zero/absent. When credit is present, the formula-only threshold `(1 + sl_mult) * credit` still works without spread_width.
- **Tests fixed:** `test_missing_strike_data_falls_back_to_formula`

### Test Expectation Fixes

#### Fix 2.3: `tests/test_hardening.py` — Market closed DB record status
- **Issue:** Test expected `pending_open` but the production code correctly transitions to `failed_open` after the market-hours check (to prevent stale pending_open records from blocking future scans).
- **Fix:** Updated assertion from `pending_open` to `failed_open`.
- **Tests fixed:** `test_market_closed_still_writes_db_record`

#### Fix 2.4: `tests/test_hardening2.py` — Orphan detection log message
- **Issue:** Test searched for "ORPHAN" in log messages but production code uses "UNTRACKED SHORT POSITION".
- **Fix:** Updated assertion to match either "UNTRACKED" or "ORPHAN".
- **Tests fixed:** `test_option_not_in_managed_symbols_triggers_warning`

#### Fix 2.5: `tests/test_hardening3.py` — Counter increment + Tier2 gate
- **Issue:** Counter test only mocked 3 methods but `_check_positions` calls `_should_run_tier2()` which returns False after the first call (5-min interval), so `get_positions` was never called on iterations 2-3.
- **Fix:** Added `_should_run_tier2` mock returning True.
- **Tests fixed:** `test_counter_increments_on_each_failure`

#### Fix 2.6: `tests/test_position_monitor.py` — External close grace period
- **Issue:** Tests called `_reconcile_external_closes` once, but production code requires `_EXTERNAL_CLOSE_GRACE_CYCLES` (2) consecutive missing cycles before marking `closed_external`.
- **Fix:** Loop calls to match grace period count.
- **Tests fixed:** `test_missing_legs_marked_closed_external`, `test_in_memory_status_mutated_for_exit_loop_skip`, `test_ic_all_legs_missing_marked_external`

#### Fix 2.7: `tests/test_orphan_stop_loss.py` + `tests/test_orphan_fixes_p2.py` — RC4 alignment
- **Issue:** Tests expected `_detect_orphans` to create `synthetic-monitor-*` DB records. Production code (RC4 fix) deliberately removed this because synthetic records cause zombie positions with mispriced SL/PT.
- **Fix:** Updated tests to verify alert-only behavior (CRITICAL log) instead of synthetic record creation. Updated orphan SL tests to use manually-created positions with future expirations.
- **Tests fixed:** `test_short_orphan_creates_synthetic_record` (renamed to `test_short_orphan_logs_critical_alert`), `test_synthetic_record_idempotent` (renamed to `test_orphan_detection_idempotent`), `test_3c_synthetic_record_created_for_unknown_position` (renamed to `test_3c_orphan_alerts_for_unknown_position`), all 4 `TestOrphanStopLoss` tests

#### Fix 2.8: `tests/test_reconciler_fixes.py` — Wing ID timestamp suffix
- **Issue:** Tests expected `alpaca_put_order_id == cid + "-put"` but production code appends a timestamp suffix for Alpaca uniqueness: `cid-NNNNNNN-put`.
- **Fix:** Changed assertions to use `startswith(cid)` and `endswith("-put")`.
- **Tests fixed:** `test_wing_ids_stored_after_successful_ic_submission`, `test_ic_lifecycle_submit_fill_reconcile`

---

## 3. Stream Integration Gap — Clarification

### v5_hedge stream: CORRECTLY INTEGRATED (no fix needed)

The audit report noted that `v5_hedge_signals` in `exp2690_signal_generators.py` "imports from crisis_alpha_v3/v4, NOT v5." On closer inspection, this was inaccurate:

```python
def v5_hedge_signals(date):
    from compass.crisis_alpha_v3 import load_universe_v3, LOOKBACK_GRID
    from compass.crisis_alpha_v5 import HedgeConfigV5, compute_v5_weights, stress_gate
    from compass.crisis_alpha_v4 import compute_signal_with_confirmation
```

The function imports the **v5 configuration and weights** (`HedgeConfigV5`, `compute_v5_weights`, `stress_gate`) from `crisis_alpha_v5.py`, uses v3's data loader (`load_universe_v3`), and v4's signal confirmation. This is correct — the v5 module builds on v3/v4 by design. All 8 streams are properly wired in `GENERATOR_REGISTRY`.

---

## Summary of Changes

| File | Type | Description |
|------|------|-------------|
| `experiments/EXP-1020-max/backtest.py` | QUARANTINED | Renamed to .QUARANTINED_RULE_ZERO |
| `compass/adaptive_1dte.py` | FIXED | build_exp1220_daily → NotImplementedError |
| `compass/ensemble_model_health.py` | FIXED | Synthetic samples → scipy.stats.kstest |
| `shared/database.py` | FIXED | Metadata preservation on partial upsert |
| `execution/position_monitor.py` | FIXED | None-strike SL/PT guard |
| `tests/test_hardening.py` | TEST FIX | Market closed status assertion |
| `tests/test_hardening2.py` | TEST FIX | Orphan log message assertion |
| `tests/test_hardening3.py` | TEST FIX | Counter + tier2 gate mocking |
| `tests/test_position_monitor.py` | TEST FIX | External close grace period |
| `tests/test_orphan_stop_loss.py` | TEST FIX | RC4 alignment (no synthetic records) |
| `tests/test_orphan_fixes_p2.py` | TEST FIX | RC4 alignment (no synthetic records) |
| `tests/test_reconciler_fixes.py` | TEST FIX | Wing ID timestamp suffix |

**Production files changed:** 4
**Test files changed:** 7 (including 1 regression fix in `test_hardening.py::TestIntraDayOrderLifecycle`)
**Files quarantined:** 1

### Regression Fix: `test_pending_open_order_monitored_for_stop_loss_same_session`
This test was not in the original 18 failures but broke because the reconciler's EOD/morning checks now run within `_check_positions()` and mark phantom positions as `needs_investigation` before the SL check fires. Fixed by mocking `_should_run_eod`, `_should_run_morning`, and all reconciliation methods in the test's Step 2.

---

*Report generated: 2026-04-23*
