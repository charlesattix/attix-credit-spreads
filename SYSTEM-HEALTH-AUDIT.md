# SYSTEM HEALTH AUDIT — Attix Credit Spreads
**Date**: 2026-05-22
**Auditor**: Claude Sonnet 4.6 (automated)
**Trigger**: EXP-400 crash loop, EXP-800/Sentinel offline, zero trades today across all 8 experiments

---

## EXECUTIVE SUMMARY

The system has been executing **zero trades** because of a cascade of deployment failures that
started when the Mac launchd plists died and was never fully remediated by the Railway migration.
Here are the five most critical issues:

1. **`compass/retrain_scheduler.py` was archived but `main.py` still imports it** — every
   experiment running `main.py scheduler` crashes with `ModuleNotFoundError` on startup, creating
   the observed crash loop. This single line is responsible for EXP-400's crash loop.

2. **The launchd plists are dead** — they point to `/Users/charlesbot/projects/...` and run on
   a Mac that no longer has these processes loaded. Zero experiments are running via launchd.
   Railway only runs the web dashboard and (as of today) the compass-scheduler.

3. **The compass-scheduler NEVER submits orders** — `job_signal_generator` explicitly writes
   signals to disk only. The Alpaca order submission code is commented out behind an
   "EXP-2890 SEAM not wired" comment. Even when the scheduler fires correctly, zero orders reach
   Alpaca.

4. **The compass-scheduler doesn't run EXP-400/401/503/600/800/1220 at all** — it exclusively
   runs EXP-2830 (a separate portfolio strategy). The 6 registered active experiments have no
   Railway deployment mechanism whatsoever.

5. **EXP-503 and EXP-800 are HALTED in sentinel_state.json** — sentinel guards block their
   scanners even if they were running. EXP-503 has an inconsistent state (status="halted" but
   halted=false), the result of a botched resume operation.

---

## BUGS BY SEVERITY

---

### P0 — CRITICAL (System Down, Trades Blocked)

---

#### P0-1: `compass.retrain_scheduler` Missing — Direct Crash Loop Cause
**File**: `main.py:1111`
**Code**:
```python
from compass.retrain_scheduler import RetrainScheduler
```
**Root Cause**: `RetrainScheduler` was moved to `compass/archive/retrain_scheduler.py` but
`main.py` was not updated. Every invocation of `python main.py scheduler` (the mode all launchd
plists use) hits this import and throws `ModuleNotFoundError: No module named
'compass.retrain_scheduler'` before any scan logic executes.

**Evidence**: `ls compass/retrain_scheduler.py` — file does not exist.
`ls compass/archive/retrain_scheduler.py` — file exists with `class RetrainScheduler`.

**Blast radius**: EXP-400 (Champion), EXP-401, EXP-503, EXP-600, EXP-1220 — all crash on
startup. Combined with `KeepAlive=true` in the launchd plists (ThrottleInterval=10s), this
creates a rapid restart storm that exhausts CPU before anyone notices.

**Fix**: Move `compass/archive/retrain_scheduler.py` back to `compass/retrain_scheduler.py`, OR
update the import in `main.py:1111` to `from compass.online_retrain import RetrainScheduler`
(check class name parity first).

---

#### P0-2: Launchd Plists Are Dead — Mac Paths, No Railway Equivalent
**Files**: `deploy/com.pilotai.exp400.plist`, `com.pilotai.exp401.plist`,
`com.pilotai.exp503.plist`, `com.pilotai.exp600.plist`, `com.pilotai.exp1220.plist`
**Root Cause**: Every plist hardcodes `WorkingDirectory = /Users/charlesbot/projects/pilotai-credit-spreads`
and uses `/usr/bin/python3` with a Mac-only environment. These have not been loaded into launchctl
since migration to Railway.

**Evidence**: Five `.plist` files in `deploy/` reference a Mac user home directory. Railway is
Linux. There is no `launchctl` on Railway.

**Blast radius**: EXP-400, EXP-401, EXP-503, EXP-600, EXP-1220 have **no running process
anywhere**. They are not on Railway (no Procfile entries, no `railway.json` service entries for
them) and not on Mac (plists dead).

**Fix**: Deploy each experiment as a Railway service with its own Dockerfile and start command
(`python main.py scheduler --config configs/paper_<exp>.yaml`), OR add them as separate
scheduler jobs to the compass-scheduler. This is a deployment architecture decision that needs
Carlos's approval on which path to take.

---

#### P0-3: compass-scheduler NEVER Submits Orders — EXP-2890 SEAM Not Wired
**File**: `scheduler/jobs.py:273-296`
**Root Cause**: The order submission block in `job_signal_generator` is **entirely commented out**
with the note:
```python
# NOTE: EXP-2890 bridge not wired — signals on disk only, no Alpaca orders
```
The scheduler generates signals and writes them to `/data/signals/{date}.json` but never calls
`AlpacaConnector.submit_spread()`. Zero orders have ever been submitted via this path.

**Evidence**: Lines 278-291 in `scheduler/jobs.py` are all commented out. Line 295 explicitly
logs that the bridge is missing.

**Blast radius**: Even if compass-scheduler were fixed and running perfectly, **no trades would
execute**. This is a fundamental missing piece — the signal→order bridge (EXP-2890) was never
built.

**Fix**: Implement the `AlpacaConnector.submit_spread()` call at the commented-out seam, wire in
the retry logic, and connect signal files to live Alpaca paper orders.

---

#### P0-4: compass-scheduler Runs EXP-2830 Only — Does Not Run EXP-400/401/503/600/800/1220
**File**: `scheduler/jobs.py:205`
**Root Cause**: `job_signal_generator` calls `from compass.exp2830_paper_signal_generator import
generate_all_signals`. EXP-2830 is a separate 8-stream portfolio (SPY, XLF, XLI, GLD, SLV,
vol_arb, hedge, QQQ). It has zero relationship to the 6 registered experiments. The scheduler
does not invoke `main.py` for any experiment.

**Evidence**: Registry shows 6 active experiments (EXP-400, EXP-401, EXP-503, EXP-600,
EXP-800, EXP-1220). None are in `scheduler/jobs.py`. EXP-800 config explicitly says to run via
`scripts/exp800_safe_kelly_scanner.py` — no deployment mechanism exists.

**Blast radius**: All 6 registered experiments have **no active execution path on Railway**.
Signals for EXP-2830 are also never submitted (see P0-3). The entire Railway scheduler is
producing nothing.

**Fix**: Either add per-experiment jobs to the APScheduler, or deploy each experiment as its own
Railway service with `main.py scheduler --config configs/paper_<exp>.yaml`.

---

#### P0-5: EXP-503 Halted + State Inconsistency; EXP-800 Halted
**File**: `sentinel_state.json:46-101`
**Root Cause**:
- EXP-503: `"status": "halted"` but `"halted": false`. The experiment was halted for config
  drift, then a resume was attempted (`resumed_at` set) but the `status` field was never changed
  from `"halted"` to `"active"`. The sentinel guard checks `status`, finds `"halted"`, and calls
  `sys.exit(1)` — scanner exits before any scan logic runs.
- EXP-800: Marked `"status": "halted"` with `"halt_reason": "Non-functional — 0 completed
  trades since launch"`. Has no deployment mechanism (no plist, no Railway service).

**Evidence**: `sentinel_state.json` lines 49, 87. Sentinel guard at `sentinel/guards.py:79-83`
does `if status == "halted": sys.exit(1)`.

**Blast radius**: EXP-503 and EXP-800 cannot trade regardless of fix. Combined with P0-2, these
represent 2 of 6 active experiments permanently blocked.

**Fix**: For EXP-503 — if the config is aligned and the resume was genuine, change `"status"` to
`"active"` in sentinel_state.json. For EXP-800 — decide whether to retire it or build a
deployment path.

---

#### P0-6: `compass/__init__.py` Blast Radius — Heavy ML Imports at Package Level
**File**: `compass/__init__.py:1-30`
**Root Cause**: The compass package `__init__.py` eagerly imports every ML subsystem at package
load time:
```python
from compass.signal_model import SignalModel
from compass.ensemble_signal_model import EnsembleSignalModel
from compass.ml_strategy import MLEnhancedStrategy, ...
from compass.stress_test import StressTester, ...
```
`main.py:26` does `from compass.macro_db import ...` which triggers the full `compass/__init__.py`.
If ANY of these ML imports fail (sklearn version conflict, missing xgboost, etc.), **all 6
experiments crash on startup** simultaneously.

**Evidence**: The test environment shows `FAIL compass.macro_db: No module named 'requests'`
caused by a transitive chain through `compass/__init__.py`. One broken dependency cascades to
all experiments.

**Fix**: Convert `compass/__init__.py` to lazy imports or explicit `__all__` without side-effect
imports. `main.py` should import `from compass.macro_db import ...` directly, bypassing the
package-level import cascade.

---

### P1 — HIGH (Major Functionality Broken)

---

#### P1-1: Sentinel v1 Goes Silent When Scanner Crashes
**Root Cause**: Sentinel v1 is embedded as a library called from inside the scanner process. When
the scanner crashes (e.g., due to P0-1), sentinel never fires. The G22 heartbeat only emits
during `scan_and_sync()`. No scan = no heartbeat = no crash detection.

**Evidence**: `sentinel/heartbeat.py` — `emit_heartbeat` is called at `main.py:1171,1175,1179`
only inside `scan_and_sync()`. If the process crashes before reaching that function, no heartbeat.
`sentinel/orchestrator.py` gates only run when invoked by a scanner startup.

**Fix**: Deploy Sentinel v2 (`sentinel/v2/watchdog.py` — exists, never deployed). It inverts the
architecture: watchdog owns the schedule, calls scanners as subprocesses, and monitors them
independently.

---

#### P1-2: Position Monitor Has 30-Minute Blind Spot Every Hour
**File**: `scheduler/main.py:138-162`
**Root Cause**: The monitor poll cron uses `hour="9-15", minute="30,35,40,45,50,55"`. This covers
minutes :30 to :55 in hours 9-15, plus one poll at 16:00. But minutes :00 to :29 of hours 10-15
have **zero coverage**. Between 10:00 and 10:29, 11:00-11:29, etc., stop-loss and profit-target
conditions go unchecked.

**Evidence**: `scheduler/main.py:143-161`. The pattern explicitly lists only :30,:35,:40,:45,:50,:55.

**Fix**: Add `minute="0,5,10,15,20,25,30,35,40,45,50,55"` or use `*/5` to cover every 5 minutes
across the full hour.

---

#### P1-3: EXP-503 Status/Halted Field Inconsistency — Resume Was Botched
**File**: `sentinel_state.json:49,65`
**Root Cause**: `"status": "halted"` and `"halted": false` coexist. The `resumed_at` and
`resume_reason` fields indicate someone tried to resume EXP-503 (Carlos/Charles approved it on
2026-04-20) but the `status` field was never updated. Sentinel's guard checks `status` first —
finds `"halted"` — exits. The `halted=false` flag is never evaluated.

**Fix**: Set `"status": "active"` in `sentinel_state.json` for EXP-503 if Carlos still wants it
running.

---

#### P1-4: EXP-800 Has No Deployment Path Anywhere
**File**: `configs/paper_exp800.yaml:26-30`
**Root Cause**: EXP-800 config says "run via `scripts/exp800_safe_kelly_scanner.py`". No launchd
plist exists for EXP-800. It is not a job in the compass-scheduler. No Railway service exists for
it. The sentinel_state.json marks it halted with "0 completed trades since launch" — because it
has literally never had a process running it.

**Fix**: Build a launchd plist or Railway service that calls
`python scripts/exp800_safe_kelly_scanner.py --config configs/paper_exp800.yaml`.

---

#### P1-5: `macro_sizing_flag` Logic is Semantically Inverted
**File**: `main.py:270-275`
**Root Cause**:
```python
if macro_score < 45:
    state['macro_sizing_flag'] = 'boost'   # BUG: bearish macro → boost?
elif macro_score > 75:
    state['macro_sizing_flag'] = 'reduce'  # BUG: bullish macro → reduce?
```
A macro_score < 45 means bearish conditions. Setting the flag to `'boost'` in a bearish
environment is the opposite of safe behavior. The correct mapping should be:
- macro_score < 45 (bearish) → `'reduce'`
- macro_score > 75 (bullish) → `'boost'`

**Fix**: Swap the two flag values, or rename them so the semantics are clear.

---

#### P1-6: `requests` Missing from `requirements.txt`
**Files**: `requirements.txt`, `requirements-scheduler.txt`
**Root Cause**: `requests>=2.31.0` appears in `requirements-scheduler.txt` but NOT in
`requirements.txt`. The Polygon API connector and other data fetchers use `requests`. Any
deployment that installs `requirements.txt` (not the scheduler variant) will fail on Polygon
calls.

**Evidence**: Test import of `compass.macro_db` failed with `No module named 'requests'` in the
base environment.

**Fix**: Add `requests>=2.31.0` to `requirements.txt`.

---

### P2 — MEDIUM (Significant Gaps in Safety or Correctness)

---

#### P2-1: Zero Experiments Have `sentinel_certified_at` Set
**File**: `sentinel_state.json`
**Root Cause**: All 5 experiments in sentinel state have `"sentinel_certified_at": null`.
Gate G5 (Certification) returns WARNING for every experiment but never blocks. The system has been
operating without formal certification since launch (2026-03-15).

**Fix**: Run the SENTINEL certification procedure for each active experiment. Set
`sentinel_certified_at` to the current timestamp after verifying all gates pass.

---

#### P2-2: Sentinel Gate 11 (Signal Voting Audit) Always PASS — Permanently Deferred
**File**: `sentinel/orchestrator.py:654-658`
**Root Cause**:
```python
def _run_gate11_signal_votes(exp_id: str) -> GateOutcome:
    return GateOutcome(
        "G11", "Signal Voting Audit", GateResult.PASS,
        "Deferred — fires in live scanner path with runtime vote context",
    )
```
This gate always returns PASS. Signal voting anomalies (e.g., all 3 regime signals always
agreeing, or a single signal dominating) are never detected.

**Fix**: Wire in `check_signal_votes()` from `sentinel/gates_data_quality.py` with the most
recent scan state.

---

#### P2-3: Sentinel Gate 12 (Regime Parity) Always PASS — Permanently Deferred
**File**: `sentinel/orchestrator.py:661-664`
**Root Cause**: Same pattern as G11 — permanently deferred, always returns PASS.

**Fix**: Wire in `check_regime_parity()` from `sentinel/gates_data_quality.py`.

---

#### P2-4: Sentinel v2 Built but Never Deployed
**File**: `sentinel/v2/watchdog.py` — fully implemented
**Root Cause**: Sentinel v2 solves the fundamental v1 design flaw (gates only fire inside
scanner invocations). The watchdog runs as an independent process, owns the cron schedule, and
calls scanners as subprocesses. It exists in `sentinel/v2/` but has no Railway service, no
Dockerfile, and no Procfile entry.

**Fix**: Deploy `python -m sentinel.v2.watchdog` as a Railway service. Wire in
`HEALTHCHECKS_PING_URL` for the dead man's switch.

---

#### P2-5: `job_event_gate_check` Has No Actual Calendar Integration
**File**: `scheduler/jobs.py:144-182`
**Root Cause**: The event gate job only reads a manually-created `event_gate_override.json` file.
There is no FOMC/CPI calendar lookup, no API call to economic data, and no automated population
of the override. If nobody manually creates the file, the event gate is always inactive and all
FOMC/CPI weeks trade at full size.

**Evidence**: `scheduler/jobs.py:155-163` — the only data source is a static JSON override file.

**Fix**: Connect `job_event_gate_check` to the existing `shared/economic_calendar.py` or
`compass/events.py` FOMC date list to automatically detect event weeks.

---

#### P2-6: sentinel_state.json Is 24 Days Stale
**File**: `sentinel_state.json:7`
**Root Cause**: `"last_updated": "2026-04-28T11:21"`. Today is 2026-05-22. No sentinel health
check has run in 24 days. Gate outcomes, health scores, and halt decisions are all based on
month-old data.

**Fix**: Run `python -m sentinel.orchestrator audit_all_experiments` (or equivalent CLI command)
to refresh state. Then deploy Sentinel v2 so this never happens again.

---

#### P2-7: `HEALTHCHECKS_PING_URL` Not Documented or Configured
**File**: `sentinel/v2/dead_man_switch.py:17`; `.env.example`
**Root Cause**: The dead man's switch requires `HEALTHCHECKS_PING_URL` but it is not in any
`.env.example` file and not mentioned in the Railway deployment docs. If Railway goes entirely
offline, no external alert fires.

**Fix**: Register a healthchecks.io check, set `HEALTHCHECKS_PING_URL` in Railway environment
variables, and add it to `.env.example`.

---

#### P2-8: `compass/__init__.py` Imports `EnsembleSignalModel` Even When ML Not Needed
**File**: `compass/__init__.py:25`
**Root Cause**:
```python
from compass.ensemble_signal_model import EnsembleSignalModel  # GAP-8
```
This imports sklearn, xgboost, and joblib at package load time for every import of `compass.*`,
even for simple DB operations. The `# GAP-8` comment indicates this was a known gap.

**Fix**: Move ML model imports to lazy initialization inside `CreditSpreadSystem.__init__` where
`ml_enhanced.enabled: true` is checked.

---

### P3 — LOW (Operational Quality / Minor Issues)

---

#### P3-1: Railway `restartPolicyMaxRetries = 3` — Too Aggressive For Crash Loops
**File**: `deploy/compass-scheduler/railway.toml:8`
**Root Cause**: After 3 failures, Railway stops restarting the service. If the scheduler crashes
on startup due to a missing env var or import error, it will permanently stop after 3 attempts
and need a manual redeploy.

**Fix**: Either increase `maxRetries` or configure Railway's restart policy to `ALWAYS` with
a backoff.

---

#### P3-2: launchd `ThrottleInterval=10` Creates Crash Storm
**File**: All `deploy/*.plist`
**Root Cause**: With P0-1 active, each crash restarts in 10 seconds. Over an 8-hour trading day,
this produces ~2,880 crash-restart cycles per experiment — 5 experiments = ~14,400 process
launches doing nothing except failing on import.

**Fix**: Increase `ThrottleInterval` to 300 (5 minutes) at minimum. Better: fix P0-1 so crashes
stop.

---

#### P3-3: Stray `=6.90.0` File in Project Root
**File**: `/=6.90.0`
**Root Cause**: Someone ran `pip install =6.90.0` (with leading `=`) instead of `pip install
package==6.90.0`. The shell interpreted `=6.90.0` as a command and created a file.

**Fix**: `git rm =6.90.0 && git commit -m "chore: remove stray file from botched pip install"`.

---

#### P3-4: `compass/macro_db.py` DATA_DIR May Point to Wrong Location on Railway
**File**: `shared/constants.py:17`; `compass/macro_db.py:31`
**Root Cause**: `DATA_DIR` defaults to `<project_root>/data`. On Railway, if no persistent volume
is mounted at that path, `macro_state.db` is in the ephemeral container filesystem and is lost
on every restart. All macro scores and sector rankings reset to empty on every deploy.

**Fix**: Mount a Railway persistent volume at `/app/data` and set `PILOTAI_DATA_DIR=/app/data`.

---

#### P3-5: Stale `.pids_both` File
**File**: `.pids_both`
**Root Cause**: Contains stale PIDs from old `run_both.sh` invocations on the Mac.

**Fix**: `git rm .pids_both` or add it to `.gitignore`.

---

#### P3-6: `STARTING_CAPITAL` in Scheduler Not Per-Experiment
**File**: `scheduler/jobs.py:343,450`
**Root Cause**: P&L calculations in `job_monitor_poll` and `job_post_market` use a single
`STARTING_CAPITAL` env var. For a multi-experiment deployment, each experiment has a different
account and potentially different starting capital. The current design would show wrong P&L for
all experiments except the one whose capital matches the env var.

**Fix**: Pass per-experiment capital as part of each experiment's env file, or calculate P&L
relative to current equity minus initial deposit as fetched from Alpaca.

---

#### P3-7: G13-G16 Account Gates Skip When No Alpaca Data — Silently Pass
**File**: `sentinel/orchestrator.py:693-696`
**Root Cause**: When Alpaca credentials are unavailable (e.g., missing env vars on Railway), the
account gates return `WARNING: "Skipped — no Alpaca account data"` and the experiment proceeds.
A hard gate that can't authenticate should be BLOCK, not skip.

**Fix**: If `account_id` is set in the registry but Alpaca credentials are missing,
return `GateResult.BLOCK` not `GateResult.WARNING`.

---

## ARCHITECTURAL RECOMMENDATIONS

### 1. Fix the Deployment Gap Immediately (This Week)
The root cause of zero trades is not code bugs — it's that **no process is running any of the 6
experiments**. Priority order:
1. Fix P0-1 (retrain_scheduler import) so `main.py scheduler` doesn't crash
2. Deploy EXP-400 as a Railway service (or re-enable on Mac with correct paths)
3. Wire the EXP-2890 order submission bridge (P0-3)
4. Fix EXP-503 state (P0-5 / P1-3) so it can trade again

### 2. Replace Sentinel v1 With v2
Sentinel v2 (`sentinel/v2/watchdog.py`) is fully built and solves the fundamental v1 design flaw.
Deploy it as a Railway service. This is 1 deployment step, not a code change.

### 3. Consolidate Requirements Files
`requirements.txt` and `requirements-scheduler.txt` diverged. Merge them into a single
`requirements.txt` that works for all deployments. Add `requests>=2.31.0`.

### 4. Fix the compass `__init__.py` Blast Radius
Move heavy ML imports out of the package `__init__.py` into lazy initialization. A broken xgboost
version should not bring down all 6 experiments simultaneously.

### 5. Database Persistence on Railway
Railway containers are ephemeral. Without a persistent volume for `data/`, every deployment
loses all DBs (trades, macro state, sentinel history). Mount a volume and set `PILOTAI_DATA_DIR`.

### 6. Instrument the Deployment Gap
Add a Telegram alert when an experiment has not emitted a heartbeat in > 4 hours during market
hours. Currently, a silent crash produces zero alerts via Sentinel v1.

---

## AFFECTED EXPERIMENTS STATUS MATRIX

| Experiment | Registry Status | Sentinel State | Has Process? | Trades Today | Root Causes |
|------------|----------------|----------------|-------------|-------------|-------------|
| EXP-400    | active         | active         | NO (plist dead) | 0 | P0-1, P0-2, P0-4 |
| EXP-401    | active         | active         | NO (plist dead) | 0 | P0-1, P0-2, P0-4 |
| EXP-503    | active         | **HALTED**     | NO (plist dead + halted) | 0 | P0-1, P0-2, P0-4, P0-5, P1-3 |
| EXP-600    | active         | active         | NO (plist dead) | 0 | P0-1, P0-2, P0-4 |
| EXP-800    | active         | **HALTED**     | NEVER existed | 0 | P0-4, P0-5, P1-4 |
| EXP-1220   | active         | active         | NO (plist dead) | 0 | P0-1, P0-2, P0-4 |
| EXP-2830   | not in registry| n/a            | compass-scheduler | 0 | P0-3 (no orders) |

---

## BUG COUNT SUMMARY

| Severity | Count | Description |
|----------|-------|-------------|
| P0 Critical | 6 | System down, trades impossible |
| P1 High | 6 | Major functionality broken |
| P2 Medium | 8 | Significant safety/correctness gaps |
| P3 Low | 7 | Operational quality issues |
| **Total** | **27** | |

---

*Generated by automated audit of codebase as of 2026-05-22. All findings are based on static
code analysis, config inspection, and import chain tracing. No live API calls were made.*
