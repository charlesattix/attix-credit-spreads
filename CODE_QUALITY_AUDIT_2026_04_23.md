# Code Quality Audit — pilotai-credit-spreads

**Date:** 2026-04-23
**Auditor:** Maximus (AI Trading Strategist)
**Scope:** Full codebase audit for Phase 9 (live trading) production readiness
**Codebase:** 1,184 Python files, ~540,915 lines of code
**MASTERPLAN version:** v12 (2026-04-09)

---

## Executive Summary

| Area | Grade | Notes |
|------|-------|-------|
| **Test Suite** | B+ | 12,589 passed, 18 failed (0.14% failure rate) |
| **Rule Zero Compliance** | C | 3 confirmed violations in non-test production/research code |
| **Stream Interface Consistency** | B- | 6/8 streams consistent; 2 have integration gaps |
| **TODO/FIXME Debt** | A | Only 2 TODO comments found; minimal deferred work |
| **Import Hygiene** | A- | Minor unused imports; no circular dependencies detected |
| **Test Coverage** | B | 58.12% on tracked modules (threshold: 50%) |
| **Dead Code** | D | ~270+ potentially unused compass modules; needs systematic cleanup |
| **Compass Organization** | C+ | 435 files in flat structure; research/production boundary unclear |
| **Technical Debt** | C+ | Several items must be resolved before Phase 9 |

**Overall Production Readiness: NOT YET READY for Phase 9 (live trading)**

Three blockers must be resolved: Rule Zero violations, failing tests, and stream integration gaps. Phase 8 (paper trading) can proceed with caveats.

---

## 1. Test Suite Results

**Command:** `python3 -m pytest tests/ --cov=strategy --cov=ml --cov=alerts --cov=shared --cov=backtest --cov=tracker`

### Summary

| Metric | Value |
|--------|-------|
| **Total tests collected** | 12,634 |
| **Passed** | 12,589 (99.6%) |
| **Failed** | 18 (0.14%) |
| **Skipped** | 14 |
| **xfailed** | 8 |
| **xpassed** | 6 |
| **Warnings** | 412 |
| **Runtime** | 21m 36s |

### 18 Failing Tests (Grouped by Module)

#### Execution Engine (4 failures)
| Test | File | Issue |
|------|------|-------|
| `test_execution_engine_rounds_ic_strikes` | test_execution_fixes.py | Strike price rounding for iron condors |
| `test_trade_record_stores_straddle_fields` | test_execution_straddle.py | Straddle field storage |
| `test_market_closed_still_writes_db_record` | test_hardening.py | Market-closed DB write path |
| `test_missing_strike_data_falls_back_to_formula` | test_hardening3.py | Strike fallback logic |

#### Orphan Position Handling (6 failures)
| Test | File | Issue |
|------|------|-------|
| `test_option_not_in_managed_symbols_triggers_warning` | test_hardening2.py | Orphan detection warning |
| `test_3c_synthetic_record_created_for_unknown_position` | test_orphan_fixes_p2.py | Synthetic record creation |
| `test_short_orphan_creates_synthetic_record` | test_orphan_stop_loss.py | Short orphan handling |
| `test_synthetic_record_idempotent` | test_orphan_stop_loss.py | Idempotency check |
| `test_stop_loss_fires_when_loss_exceeds_threshold` | test_orphan_stop_loss.py | Stop loss trigger |
| `test_stop_loss_does_not_fire_below_threshold` | test_orphan_stop_loss.py | Stop loss threshold |
| `test_no_credit_skips_stop_loss` | test_orphan_stop_loss.py | No-credit edge case |
| `test_full_cycle_detect_and_stop` | test_orphan_stop_loss.py | Full lifecycle |

#### Position Monitor (3 failures)
| Test | File | Issue |
|------|------|-------|
| `test_missing_legs_marked_closed_external` | test_position_monitor.py | External close detection |
| `test_in_memory_status_mutated_for_exit_loop_skip` | test_position_monitor.py | In-memory status mutation |
| `test_ic_all_legs_missing_marked_external` | test_position_monitor.py | IC external close |

#### Reconciler (2 failures)
| Test | File | Issue |
|------|------|-------|
| `test_wing_ids_stored_after_successful_ic_submission` | test_reconciler_fixes.py | Wing ID persistence |
| `test_ic_lifecycle_submit_fill_reconcile` | test_reconciler_fixes.py | IC lifecycle integration |

#### Other (1 failure)
| Test | File | Issue |
|------|------|-------|
| `test_counter_increments_on_each_failure` | test_hardening3.py | API failure counter |

### Assessment

The 18 failures cluster around **iron condor (IC) handling** and **orphan position management** — both are execution-layer features critical to live trading. These are **Phase 9 blockers**. The core strategy, signal generation, and backtesting layers are clean (0 failures).

### Recommendation
- **P0 (before Phase 9):** Fix all 18 failing tests. The IC and orphan clusters suggest a regression in the execution engine API.
- **P1:** Investigate the 6 xpassed tests — these were expected to fail but now pass, indicating the test expectations may be stale.

---

## 2. Rule Zero Compliance

**Rule Zero: NO SYNTHETIC DATA. EVER. PERIOD.**

Grep targets: `np.random`, `random.normal`, `simulate`, `generate_prices`, `synthetic` in non-test files.

### CRITICAL VIOLATIONS (3 confirmed)

#### Violation 1: `experiments/EXP-1020-max/backtest.py`
- **Lines:** 75, 85, 98, 108, 202
- **Pattern:** `rng.normal()` generates fake intraday price moves
- **Code:**
  ```python
  def simulate_intraday_move(row, rng):
      actual_move = rng.normal(0, expected_range_pct * 0.7)  # SYNTHETIC
  ```
- **Impact:** Synthetic intraday moves feed into trade trigger decisions in this backtest
- **Severity:** CRITICAL — direct Rule Zero violation

#### Violation 2: `compass/adaptive_1dte.py`
- **Lines:** 219, 233
- **Pattern:** `np.random.normal()` synthesizes EXP-1220 daily returns from hardcoded yearly targets
- **Code:**
  ```python
  def build_exp1220_daily(spy_rets):
      rng = np.random.RandomState(42)
      days = rng.normal(daily_mean, daily_vol, n) + noise  # SYNTHETIC
  ```
- **Impact:** Generates entirely fabricated daily P&L from predetermined annual return targets (52.97%, 49.13%, etc.)
- **Severity:** CRITICAL — direct Rule Zero violation

#### Violation 3: `compass/ensemble_model_health.py`
- **Line:** 291
- **Pattern:** `np.random.RandomState(42).normal()` creates synthetic feature distributions for drift testing
- **Code:**
  ```python
  train_synthetic = np.random.RandomState(42).normal(mean, std, len(live_arr))
  ks_stat, p_val = sp_stats.ks_2samp(live_arr, train_synthetic)
  ```
- **Impact:** Uses synthetic distribution as reference baseline in KS test for model drift detection
- **Severity:** CRITICAL — synthetic data used as a comparison baseline in production monitoring

### WARNING-LEVEL (Not Violations — Legitimate Randomness)

These use `np.random` for non-price purposes and are **not Rule Zero violations**:

| File | Usage | Classification |
|------|-------|----------------|
| `compass/execution_simulator.py` | Slippage modeling (prices from IronVault) | WARNING — acceptable |
| `compass/bayesian_selector.py` | Bayesian parameter sampling | WARNING — acceptable |
| `compass/config_optimizer.py` | Hyperparameter optimization | WARNING — acceptable |
| `compass/dynamic_kelly.py` | Kelly sizing simulation | WARNING — acceptable |
| `compass/crisis_hedge_v2.py` | Hedge strategy sampling | WARNING — acceptable |

### FALSE POSITIVES

| File | Context |
|------|---------|
| `scripts/validated_only_portfolio.py` | HTML report *discussing* the np.random problem |
| `execution/position_monitor.py` | "synthetic-monitor" database record type (not price synthesis) |
| `compass/backtest_auditor.py` | Auditing tool that *detects* synthetic data |
| `compass/capital_utilization.py` | Comment: "No synthetic data. No np.random." |

### Recommendation
- **P0 (immediate):** Delete or quarantine `experiments/EXP-1020-max/backtest.py` and `compass/adaptive_1dte.py`. These are legacy files generating fake data.
- **P0 (immediate):** Fix `compass/ensemble_model_health.py` to use actual training data distributions instead of synthetic normals for drift testing.
- **P1:** Add a pre-commit hook that greps for `np.random.normal`, `np.random.randn`, `generate_prices`, `simulate.*price` in non-test files and blocks the commit.

---

## 3. Stream Module Interface Consistency

### Expected Interface (per MASTERPLAN + EXP-2690)

All 8 streams should:
1. Have a `generate_today_signals(date)` entry point
2. Return `List[Dict]` matching the unified signal schema
3. Be registered in `GENERATOR_REGISTRY` in `compass/exp2690_signal_generators.py`

### Unified Signal Schema (EXP-2690, lines 18-32)

```python
{
    "stream": str,       "date": str,        "ticker": str,
    "action": "OPEN"|"HOLD"|"BLOCKED"|"NONE"|"ERROR",
    "direction": str,    "delta": float,     "dte": int,
    "width": float,      "weight": float,    "confidence": float,
    "notes": str,        "legs": list,
}
```

### Compliance Matrix

| Stream | Entry Point | Return Type | Error Handling | Registry | Grade |
|--------|-------------|-------------|----------------|----------|-------|
| exp1220 | `generate_today_signals()` | List[Dict] | Bare `except:` (L125, L190) | exp1220 | B |
| xlf_cs | `generate_today_signals()` | List[Dict] | Specific except types | xlf_cs | A |
| xli_cs | `generate_today_signals()` | List[Dict] | Specific except types | xli_cs | A |
| qqq_cs | `generate_today_signals()` | List[Dict] | Specific except types | qqq_cs | A- |
| gld_cal | `generate_today_signals()` | List[Dict] | Specific except + logging | gld_cal | A |
| slv_cal | `generate_today_signals()` | List[Dict] | Specific except + logging | slv_cal | A |
| cross_vol | **MISSING entry point** | N/A | Good in exp2020 | cross_vol (self-contained in exp2690) | C |
| v5_hedge | **MISSING entry point** | N/A | Good in v3/v4 | v5_hedge (imports v3/v4, NOT v5) | C |

### Critical Issues

**1. `exp2020_cross_vol_arb.py` has no `generate_today_signals()` entry point**
- The module defines `atm_iv()` and `weekly_signal_panel()` but no delegating entry
- `exp2690_signal_generators.py` reimplements the cross-vol logic internally instead of importing from exp2020
- This creates **code duplication** and a **maintenance risk**

**2. `crisis_alpha_v5.py` is not wired into the signal pipeline**
- `exp2690_signal_generators.py` `v5_hedge_signals()` imports from `crisis_alpha_v3`/`v4`, NOT v5
- v5 was the "winning" version per the MASTERPLAN but is not actually integrated
- **Risk:** Paper/live trading may run v3/v4 logic instead of the intended v5

**3. Bare `except:` clauses in `exp1220_standalone.py`**
- Line 125: `except: continue` — swallows all errors on VIX/price fetch
- Line 190: `except: pass` — swallows all errors on date conversion
- These mask data availability issues that should surface as `action=BLOCKED` signals

### Graceful Degradation (Good)

All streams that ARE properly integrated handle data gaps well:
- `exp2690_signal_generators.generate_all_signals()` wraps per-stream exceptions as `action=ERROR`
- Individual streams return `action=BLOCKED` when data is unavailable
- `vix_ladder.py` returns max_exposure on NaN VIX (safe default)
- `portfolio_risk_manager.py` returns zero weights on empty returns (safe default)

### Hardcoded Values

| Module | Values | Status |
|--------|--------|--------|
| exp1220_standalone.py | `otm_pct=0.95`, `width=5.0`, `profit_pct=0.50`, `stop_mult=2.0` | Should be module-level constants |
| exp2160_high_capacity_alts.py | `CS_TARGET_DTE=30`, `CS_SHORT_DELTA=-0.30` | Module-level constants (good) |
| exp2240_qqq_iwm_credit_spreads.py | Strategy params at module top | Module-level constants (good) |
| exp1770_commodity_calendars.py | `SIGNAL_WINDOW=60`, `Z_THRESH=1.0` | Module-level constants (good) |
| exp2020_cross_vol_arb.py | `HOLDING_DAYS=21`, `VEGA_NOTIONAL=10_000` | Module-level constants (good) |

### Recommendation
- **P0:** Wire `crisis_alpha_v5.py` into `exp2690_signal_generators.py` or confirm v3/v4 is intentional
- **P1:** Add `generate_today_signals()` to `exp2020_cross_vol_arb.py` and have exp2690 import from it
- **P1:** Replace bare `except:` with `except Exception as e:` + logging in `exp1220_standalone.py`
- **P2:** Extract exp1220 hardcoded values to module-level constants

---

## 4. TODO/FIXME/HACK Comments

**Result: Remarkably clean — only 2 TODO comments found in the entire codebase.**

| File | Line | Comment | Severity |
|------|------|---------|----------|
| `compass/exp2300_portfolio_runner.py` | 452 | `# TODO: replace with proper scheduler that respects cadence + market hours` | MEDIUM |
| `scripts/run_crypto_snapshot.py` | 150 | `# TODO: implement via deribit.get_volatility_index_data() when DVOL history is available` | LOW |

### Assessment

The scheduler TODO in `exp2300_portfolio_runner.py` is relevant to Phase 8/9 — the paper portfolio runner currently uses a `scan_only` guard instead of a proper scheduler. This works for cron-based invocation but won't support intraday re-evaluation if needed.

The Deribit TODO is crypto-specific and not on the critical path.

**No FIXME, HACK, XXX, WORKAROUND, TEMPORARY, or KLUDGE comments found.** This is exceptional hygiene for a ~540K LOC codebase.

---

## 5. Import Hygiene

### Unused Imports (Minor)

| File | Import | Severity |
|------|--------|----------|
| `compass/exp2690_signal_generators.py` | `sqlite3` (unused in main code) | LOW |
| `compass/exp2690_signal_generators.py` | `dataclass` (imported, no @dataclass used) | LOW |
| `compass/exp2690_signal_generators.py` | `Tuple` from typing (unused) | LOW |
| `compass/portfolio_risk_manager.py` | `Sequence` from typing (unused) | LOW |
| `compass/metrics.py` | `Optional` from typing (unused) | LOW |

### Circular Dependencies

**None detected.** The import graph appears clean with clear layering:
- `shared/` → base utilities (no upward imports)
- `strategy/`, `backtest/` → import from `shared/`
- `compass/` → imports from `shared/`, `strategy/`
- `alerts/`, `execution/` → import from all above
- `main.py` → orchestrates everything

### `__init__.py` Health

All checked `__init__.py` files resolve to existing modules. No broken import chains.

### Architectural Note

`main.py` has an intentional `# noqa: F401` for `DataProvider` import — marked as architectural requirement (ARCH-PY-06). This is properly documented.

---

## 6. Test Coverage

### Coverage Summary (full run, all 12,634 tests)

| Module | Statements | Missed | Coverage |
|--------|-----------|--------|----------|
| **alerts/** | — | — | ~85% avg |
| **backtest/** | — | — | ~75% avg |
| **ml/** | — | — | ~70% avg |
| **shared/** | — | — | ~65% avg |
| **strategy/** | — | — | ~83% avg |
| **tracker/** | — | — | ~50% avg |
| **TOTAL** | **9,001** | **3,770** | **58.12%** |

**Threshold: 50% — PASSED**

### Low-Coverage Modules (below 50%)

| Module | Coverage | Risk |
|--------|----------|------|
| `shared/reconciler.py` | 46% | HIGH — 1,464 lines, critical for live reconciliation |
| `shared/strike_selector.py` | 27% | MEDIUM — strike selection logic |
| `tracker/pnl_dashboard.py` | 14% | LOW — display only |
| `shared/earnings_calendar.py` | 8% | LOW — event data |

### Assessment

58.12% overall coverage meets the 50% CI gate but is thin for a system managing real capital. The `shared/reconciler.py` at 46% is the highest-risk gap — this module handles trade reconciliation between the system and the broker, and untested paths could cause position tracking errors in live trading.

### Recommendation
- **P0 (before Phase 9):** Raise `shared/reconciler.py` coverage to 70%+. Focus on the IC lifecycle paths (lines 691-835, 886-946).
- **P1:** Raise overall target to 65% for production modules.
- **P2:** Add integration tests for the full IC submission → fill → reconcile lifecycle.

---

## 7. Dead Code & Unused Modules

### Compass Directory: 435 Files

The `compass/` directory is the largest module at 435 files (255,382 lines). It contains:
- ~76 modules actively imported across the codebase
- **~270+ modules NOT imported anywhere and NOT referenced in tests**

### Categories of Potentially Dead Code

#### A. Versioned Iterations (Keep Latest, Archive Rest)
| Pattern | Files | Notes |
|---------|-------|-------|
| `crisis_alpha*.py` | v1, v2, v3, v4, v5 | Only v3/v4 are imported; v5 should be but isn't |
| `combined_portfolio*.py` | v1, v2 | Superseded by v8a architecture |
| `dynamic_leverage*.py` | base, hedged | Superseded by VIX ladder |

#### B. Benchmark/Research Files (Archive)
| Pattern | Count | Notes |
|---------|-------|-------|
| `benchmark_*.py` + `.md` + `.json` | ~12 | Historical benchmark results |
| `discovery_*.py` | ~5 | Alpha discovery scripts |
| `*_sweep.py` | ~8 | Parameter sweep scripts |

#### C. Likely Dead Production Code
| File | Evidence |
|------|----------|
| `compass/alpaca_connector.py` | Not imported anywhere (exp2890_alpaca_connector.py is the active one) |
| `compass/adaptive_1dte.py` | Not imported + contains Rule Zero violation |
| `compass/anomaly_detector.py` | Not imported |
| `compass/dispersion*.py` | 3 files, none imported (strategy was explored then dropped) |

#### D. Experiment Archives
~200+ `exp*_*.py` files in `compass/` represent the ~95 experiments from the April 6-8 sprint. Most are historical research code, not production code. However, they live alongside production modules with no clear boundary.

### Recommendation
- **P1:** Create `compass/archive/` directory and move all non-production experiment files there
- **P1:** Create `compass/production/` directory (or a manifest file) listing the ~20 modules that are actually part of the v8a production config
- **P2:** Delete confirmed dead code (`adaptive_1dte.py`, old `alpaca_connector.py`, dispersion files)

---

## 8. Compass Directory Organization

### Current Structure

```
compass/                    # 435 files, 255K lines — FLAT
├── __init__.py             # Exports core classes
├── cache/                  # Cached data
├── crypto/                 # 11 crypto modules
├── experiments/            # Some experiments (others at top level)
├── logs/                   # Log files (should not be in source tree)
├── paper_trading/          # Paper trading engines
├── reports/                # Strategy reports (245+ files)
├── research/               # Research notebooks
├── scripts/                # Utility scripts
├── tests/                  # Internal tests
├── [~350 .py files]        # FLAT — production + research + dead code mixed
└── [~30 .md/.json files]   # Benchmark results, docs
```

### Issues

1. **Flat structure:** 350+ Python files in a single directory with no subdirectory organization. Production code (`vix_ladder.py`, `metrics.py`, `exp2690_signal_generators.py`) is mixed with dead experiments and archived research.

2. **Naming inconsistency:** Some files use experiment IDs (`exp2690_signal_generators.py`), others use descriptive names (`vix_ladder.py`, `portfolio_risk_manager.py`). The experiment ID naming made sense during rapid development but is opaque for maintenance.

3. **Logs in source tree:** `compass/logs/` should be gitignored, not tracked.

4. **Reports in source tree:** `compass/reports/` (245+ files) contains JSON reports that are referenced by the MASTERPLAN as "truth sources." These should be kept but potentially compressed or archived for older experiments.

5. **No manifest:** There is no file that declares which modules are "production" vs "research" vs "archived."

### Recommended Structure (Phase 9 prep)

```
compass/
├── __init__.py
├── production/             # The ~20 modules that run in paper/live
│   ├── exp1220_standalone.py
│   ├── exp2160_high_capacity_alts.py
│   ├── exp2240_qqq_iwm_credit_spreads.py
│   ├── exp1770_commodity_calendars.py
│   ├── exp2020_cross_vol_arb.py
│   ├── crisis_alpha_v5.py
│   ├── exp2690_signal_generators.py
│   ├── exp2890_alpaca_connector.py
│   ├── vix_ladder.py
│   ├── portfolio_risk_manager.py
│   ├── metrics.py
│   └── ...
├── research/               # Experimental code, kept for reproducibility
├── archive/                # Dead code, old versions
├── reports/                # Backtest result JSONs
├── scripts/                # One-off and cron scripts
└── tests/                  # Compass-specific tests
```

---

## 9. Technical Debt — Phase 9 Blockers

### P0 — Must Fix Before Live Trading

| Item | Description | Risk | Effort |
|------|-------------|------|--------|
| **18 failing tests** | IC handling, orphan detection, reconciler — all execution-layer | Position tracking errors in live | Medium |
| **Rule Zero violations** | 3 files with synthetic data generation | Contaminated results if these modules are referenced | Low |
| **crisis_alpha_v5 not wired** | v5_hedge imports v3/v4, not v5 | Running wrong hedge logic in production | Low |
| **Reconciler coverage 46%** | Critical module for broker position sync | Untested reconciliation paths in live | Medium |
| **Bare except in exp1220** | Swallows all errors silently | Masked data issues during trading | Low |

### P1 — Should Fix Before Live Trading

| Item | Description | Risk | Effort |
|------|-------------|------|--------|
| **cross_vol code duplication** | exp2690 reimplements exp2020 logic instead of importing | Drift between implementations | Low |
| **No pre-commit Rule Zero hook** | Synthetic data could be reintroduced | Future Rule Zero violations | Low |
| **~270 dead compass modules** | Cognitive overhead, accidental usage | Maintenance burden | Medium |
| **Flat compass structure** | Production/research boundary unclear | Wrong module loaded in production | Medium |
| **Scheduler TODO** | `exp2300_portfolio_runner.py` lacks proper scheduler | Can't do intraday re-evaluation | Medium |

### P2 — Nice to Have

| Item | Description | Risk | Effort |
|------|-------------|------|--------|
| **Missing docstrings** | exp1220, exp2160, exp2240 entry points undocumented | Onboarding friction | Low |
| **exp1220 hardcoded params** | Strategy params inline instead of module-level constants | Harder to tune | Low |
| **Raise coverage to 65%** | Current 58% is thin for live capital | Untested edge cases | Medium |
| **cross_vol/vol_arb naming** | EXP-2900 flagged this; partially resolved | Confusion in configs | Low |
| **412 pytest warnings** | Mostly matplotlib tight_layout + sklearn convergence | Noisy test output | Low |

---

## 10. Summary of Recommendations

### Immediate (before Phase 8 paper trading continues)

1. **Quarantine Rule Zero violators:** Remove or archive `experiments/EXP-1020-max/backtest.py`, `compass/adaptive_1dte.py`. Fix `compass/ensemble_model_health.py` to use real training distributions.
2. **Verify crisis_alpha version:** Confirm whether v5_hedge should import from v3/v4 or v5 and fix `exp2690_signal_generators.py` accordingly.

### Before Phase 9 (live trading)

3. **Fix all 18 failing tests.** The IC and orphan test clusters indicate execution-layer regressions that are unacceptable for live capital.
4. **Raise reconciler test coverage** to 70%+ (currently 46%).
5. **Replace bare `except:` clauses** in `exp1220_standalone.py` with specific exception types.
6. **Add pre-commit hook** to block synthetic data patterns in non-test files.
7. **Wire `exp2020_cross_vol_arb.py`** properly into the signal pipeline (eliminate duplication).

### Post-Phase 9 Launch

8. **Reorganize compass directory** — separate production, research, and archive.
9. **Archive ~270 unused modules** to reduce cognitive load.
10. **Raise overall test coverage target** to 65%.

---

## Appendix A: Test Failure Details

```
FAILED tests/test_execution_fixes.py::TestStrikePricePreservation::test_execution_engine_rounds_ic_strikes
FAILED tests/test_execution_straddle.py::TestStraddleDryRun::test_trade_record_stores_straddle_fields
FAILED tests/test_hardening.py::TestExecutionEngineMarketGuard::test_market_closed_still_writes_db_record
FAILED tests/test_hardening2.py::TestOrphanDetection::test_option_not_in_managed_symbols_triggers_warning
FAILED tests/test_hardening3.py::TestStopLossThreshold::test_missing_strike_data_falls_back_to_formula
FAILED tests/test_hardening3.py::TestConsecutiveAPIFailureAlerting::test_counter_increments_on_each_failure
FAILED tests/test_orphan_fixes_p2.py::TestOrphanRecovery::test_3c_synthetic_record_created_for_unknown_position
FAILED tests/test_orphan_stop_loss.py::TestDetectOrphansSynthetic::test_short_orphan_creates_synthetic_record
FAILED tests/test_orphan_stop_loss.py::TestDetectOrphansSynthetic::test_synthetic_record_idempotent
FAILED tests/test_orphan_stop_loss.py::TestOrphanStopLoss::test_stop_loss_fires_when_loss_exceeds_threshold
FAILED tests/test_orphan_stop_loss.py::TestOrphanStopLoss::test_stop_loss_does_not_fire_below_threshold
FAILED tests/test_orphan_stop_loss.py::TestOrphanStopLoss::test_no_credit_skips_stop_loss
FAILED tests/test_orphan_stop_loss.py::TestOrphanStopLoss::test_full_cycle_detect_and_stop
FAILED tests/test_position_monitor.py::TestExternalCloseDetection::test_missing_legs_marked_closed_external
FAILED tests/test_position_monitor.py::TestExternalCloseDetection::test_in_memory_status_mutated_for_exit_loop_skip
FAILED tests/test_position_monitor.py::TestExternalCloseDetection::test_ic_all_legs_missing_marked_external
FAILED tests/test_reconciler_fixes.py::TestFix1ICWingIDsStoredInDB::test_wing_ids_stored_after_successful_ic_submission
FAILED tests/test_reconciler_fixes.py::TestICLifecycleIntegration::test_ic_lifecycle_submit_fill_reconcile
```

## Appendix B: Coverage by Module

| Module | Coverage | Status |
|--------|----------|--------|
| shared/types.py | 100% | Excellent |
| shared/exceptions.py | 100% | Excellent |
| shared/signal_scorer.py | 100% | Excellent |
| shared/macro_event_gate.py | 100% | Excellent |
| strategy/__init__.py | 100% | Excellent |
| tracker/__init__.py | 100% | Excellent |
| shared/tail_hedge.py | 98% | Excellent |
| shared/realistic_benchmarks.py | 96% | Excellent |
| shared/healthcheck.py | 95% | Excellent |
| shared/indicators.py | 92% | Good |
| strategy/spread_strategy.py | 89% | Good |
| strategy/technical_analysis.py | 90% | Good |
| shared/telegram_alerts.py | 90% | Good |
| shared/database.py | 90% | Good |
| shared/deviation_tracker.py | 90% | Good |
| shared/live_pricing.py | 89% | Good |
| shared/scheduler.py | 86% | Good |
| shared/economic_calendar.py | 83% | Good |
| shared/portfolio_risk.py | 81% | Good |
| shared/feature_logger.py | 80% | Good |
| tracker/trade_tracker.py | 79% | Good |
| shared/io_utils.py | 79% | Good |
| shared/strategy_factory.py | 75% | Adequate |
| strategy/options_analyzer.py | 71% | Adequate |
| shared/notifier.py | 68% | Adequate |
| shared/provider_protocol.py | 67% | Adequate |
| shared/snapshot_builder.py | 65% | Adequate |
| shared/iron_vault.py | 56% | Marginal |
| shared/reconciler.py | **46%** | **Below threshold** |
| shared/strike_selector.py | **27%** | **Below threshold** |
| tracker/pnl_dashboard.py | **14%** | **Below threshold** |
| shared/earnings_calendar.py | **8%** | **Below threshold** |

---

*Report generated: 2026-04-23*
*Next audit recommended: After Phase 8 paper trading window (4+ weeks)*
