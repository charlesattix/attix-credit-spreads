# Cleanup EXP-2830 + Generic ALPACA_API_KEY — Report

**Branch:** `chore/disable-exp2830-and-remove-generic-alpaca`
**Date:** 2026-05-23
**Status:** PR open, **not merged**

## Summary

- Disabled EXP-2830 signal generator (was submitting orders via dead generic ALPACA_API_KEY → `[ORDER FAIL]` Telegram spam on Tue 09:25 ET).
- Removed generic `ALPACA_API_KEY` / `ALPACA_API_SECRET` references from the **vesper hot path** (`scheduler/*`).
- Per-experiment scanners (EXP-400/401/503/600/800/1220/3309/3311) registered at the same 09:25 ET slot in `scheduler/main.py` continue to fire — they use their own per-experiment keys.

## Files changed

| File | Change |
|---|---|
| `scheduler/main.py` | Removed `job_signal_generator` import + its `add_job(...)` block (EXP-2830 signal generator schedule). |
| `scheduler/jobs.py` | Removed `job_signal_generator()` function entirely. `get_alpaca_client(exp_id)` now requires `exp_id` (no generic fallback). `job_pre_market_check` removes generic-fallback branch and probes each configured per-experiment key. `job_monitor_poll` and `job_post_market` now use `get_alpaca_client("EXP400")` for the equity probe (champion account). |
| `scheduler/data_providers.py` | Removed L2 Alpaca data fallback (used generic `ALPACA_API_KEY`). Now: L1 Polygon → L3 yfinance → L4 cache. |

## Vesper job count

- **Before:** 20 jobs registered (1 `signal_generator` + 19 others).
- **After:** 19 jobs registered (signal_generator removed; 8 per-experiment scanners untouched).

## `os.environ` references to generic ALPACA_API_KEY/SECRET removed (vesper hot path)

| Location | Before | After |
|---|---|---|
| `scheduler/jobs.py:84-85` (`get_alpaca_client`) | `os.environ.get(f"ALPACA_API_KEY{suffix}") or os.environ.get("ALPACA_API_KEY", "")` | Per-exp only; raises `RuntimeError` if missing |
| `scheduler/jobs.py:136-137` (`job_pre_market_check`) | Generic-key fallback check | Removed; failure if no per-exp keys |
| `scheduler/data_providers.py:312-313` (`fetch_market_data`) | `alpaca_key/secret = os.environ.get(...)` for L2 Alpaca fallback | Removed; L2 path disabled |

## Callers that MUST stay (deferred to follow-up PR)

These read generic `ALPACA_API_KEY` but are **NOT on the Tue 09:25 ET vesper signal path**. They run in other Railway services (sentinel-watchdog, dashboard) or in CLI scripts. Leaving them to avoid scope creep:

| File | Reason |
|---|---|
| `sentinel/guards.py`, `sentinel/monitor.py`, `sentinel/runtime.py`, `sentinel/v2/cadence_engine.py` | sentinel-watchdog service — out of scope for vesper cleanup. Will also fail with dead key. |
| `shared/portfolio_risk.py` | Reads `creds` dict (per-env), not `os.environ` directly. Safe; the env file values may still be per-exp. |
| `shared/credentials.py` | Reads from per-experiment `.env` files (e.g. `.env.exp400`), not Railway env vars. Files contain per-exp keys mapped to generic names — standard adapter pattern, safe. |
| `pilotai_signal/trade_notifications.py` | Legacy notifier; not imported by vesper. |
| `scripts/*.py` (`daily_report.py`, `compare_leverage_sweep.py`, `monitor_exp880.py`, `north_star_monitor.py`, etc.) | One-off CLI tools, not scheduled. |
| `tests/test_*.py`, `tests/archive/*`, `experiments/EXP-1570-max/*` | Test fixtures use dummy keys; archive code; not on hot path. |
| `scheduler/jobs.py:_get_experiment_env` writes `env["ALPACA_API_KEY"]` | NOT a read of generic key — this maps per-exp Railway key into the subprocess env under the name the subprocess expects. Standard adapter; kept. |

## Test results

```
python3 -m pytest tests/ --no-cov -q
```

- **3736 passed**, 12 failed, 5 skipped, 8 xfailed, 6 xpassed.
- **All 12 failures pre-exist on this branch's base (verified by stashing P0-4 changes and re-running):** `test_macro_api` (10), `test_exp800_fixes` (1), `test_reconciler_stress` (1).
- Scheduler-touching tests: `tests/test_scheduler.py` + `tests/test_dollar_notional_sizing.py` → **49/49 pass**.

## Smoke test

```
python3 -c "import scheduler.jobs as j; assert not hasattr(j, 'job_signal_generator')"
```

- ✅ `scheduler.jobs` imports cleanly.
- ✅ `job_signal_generator` symbol removed.
- ✅ `get_alpaca_client()` now requires `exp_id` (raises `TypeError`/`RuntimeError` otherwise).

## Railway env vars to remove (post-merge, Charles to execute)

| Variable | Services |
|---|---|
| `ALPACA_API_KEY` (generic, dead key `PKSOPYPC…YY3P`) | vesper, sentinel-watchdog, dashboard |
| `ALPACA_API_SECRET` (generic) | vesper, sentinel-watchdog, dashboard |

⚠ Sentinel service will start failing once the generic key is removed from Railway; the sentinel cleanup is a **follow-up PR** (see "Callers that MUST stay" above).

## Notes

- `compass/exp2830_paper_signal_generator.py` and `compass/archive/exp2860_paper_dry_run.py` are left on disk (no scheduler references them; deletion is a separate housekeeping task).
- No yfinance imports added; existing yfinance usage in `scheduler/data_providers.py` L3 is unchanged.
