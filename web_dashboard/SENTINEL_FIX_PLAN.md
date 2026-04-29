# SENTINEL HEALTH RELIABILITY FIX — DESIGN PLAN

Status: Phase 2 (DESIGN) — written 2026-04-29
Branch: `feature/sentinel-health-reliability`

## 1. Mission

The Railway-hosted Sentinel dashboard at
`https://attix-dashboard-production.up.railway.app/sentinel`
shows scores stuck at ~90/100 for every experiment. Charles's diagnosis (score
cliff + lying counters + stale push) is correct, **and worse than reported**:
the push-to-Railway pipeline has run **0 times** out of 68 cron invocations
since registration. The dashboard data is genuinely stale; the score it shows
is just the additional lie on top.

## 2. Phase 1 — Verified Root Causes

### 2.1 Push pipeline never executed (CRITICAL — primary cause of stale data)

**File:** `scripts/sentinel-cron.sh:3` — `set -euo pipefail`
**File:** `scripts/run_sentinel.py:544` — `return 1 if issues_found > 0 else 0`

The launchd job runs `sentinel-cron.sh` hourly. The wrapper's first command,
`run_sentinel.py --daily`, returns exit code 1 whenever any sentinel issue is
detected (which is essentially always, given current G7/G9 HALTs). `set -e`
then aborts the wrapper before reaching the `sync_sentinel_data.py --push`
line.

Evidence:
- `~/Library/LaunchAgents/com.attix.sentinel.plist`: `StartInterval=3600`,
  `runs=27`, `last exit code=1`
- `/Users/charlesbot/logs/sentinel_cron.log`:
  - `Starting Sentinel daily` count = **68**
  - `Syncing to Railway dashboard` count = **0**
  - `Done.` count = **0**
- `data/sentinel_dashboard.json` mtime = **Apr 28 10:03** (stale; the only
  pushes were manual)

### 2.2 Push endpoint does not exist on Railway (would fail even if reached)

**File:** `scripts/sync_sentinel_data.py:640`
- POSTs to `/api/admin/push-sentinel` with header `X-API-Key`.

**Reality on Railway** (`web/app/api/admin/`):
- Only `push-experiments/route.ts` and `upload-db/route.ts` exist.
- `push-experiments/route.ts:37-41` requires `Authorization: Bearer <token>`,
  not `X-API-Key`.
- `web/app/api/sentinel/route.ts:31` reads `data/sentinel_dashboard.json`
  off the Railway volume — but nothing writes to that path on Railway.

So even if the cron reached the sync step, it would 404. The dashboard is
serving whatever was on the volume at last manual deploy.

### 2.3 Score cliff at 24h boundary (CONFIRMED — Charles's diagnosis correct)

**File:** `web_dashboard/html.py:1364` (`_compute_health_score`)
**File:** `web_dashboard/html.py:1477-1491` (G3 gate construction)

At t = 23.99h since `last_health_check`:
- G3 severity = `warning` → score -= 10
- stale-HC penalty = none (≤24h)
- **Score = 90/100**

At t = 24.01h since `last_health_check`:
- G3 severity = `critical` → score -= 30
- stale-HC penalty = -5 (>24h branch)
- **Score = 65/100**

A net **-25 cliff** at one boundary, double-counting the same staleness signal.
Beyond 48h another -15 step.

### 2.4 G3 threshold mismatched to actual cadence

**File:** `web_dashboard/html.py:1485-1491`

G3 says `ok` only if `age_h < 2`, `warning` at 2-24h, `critical` at ≥24h.
But the orchestrator cadence is **hourly** (3600s). After ~2h of any cron miss
G3 already trips warning. There is no source-of-truth read of the orchestrator
cadence — the values are hard-coded literals.

### 2.5 Summary counters lie (aggregate by score band, not gate severity)

**File:** `web_dashboard/html.py:1502-1507`

```python
if status == "halted":
    halted_count += 1
if score < 50:
    critical_count += 1
elif score < 80:
    warning_count += 1
```

`critical_count` and `warning_count` are derived from the broken score band, so
they double-launder the cliff bug. An experiment with two critical gates and
a halt currently counts as `halted=1` but `critical=0`. An experiment with
exactly one critical gate but score 65 counts as `warning=1` not `critical=1`.

### 2.6 No meta-monitoring (Sentinel doesn't detect its own staleness)

There is no alert if Railway hasn't received a push in N hours. There is no
last-push timestamp surfaced anywhere — failure is fully silent.

## 3. Proposed Fixes

### Fix A — Add the missing Railway push endpoint
**New file:** `web/app/api/admin/push-sentinel/route.ts`
- Mirror `push-experiments/route.ts` (Bearer auth, timing-safe compare, size
  guard).
- Required fields: `generated_at`, `sentinel_version`, `experiment_count`,
  `experiments`.
- Write to `data/sentinel_dashboard.json` on the Railway volume — same path
  `/api/sentinel/route.ts` already reads.
- Append `pushed_at` (server timestamp) into the written payload so the GET
  side can compute push freshness without trusting the client clock.
- Return JSON `{ success, pushed_at, bytes_written }`.

### Fix B — Make the sync script use the correct auth + record observability
**File:** `scripts/sync_sentinel_data.py`
- Switch header from `X-API-Key` → `Authorization: Bearer <token>` (match the
  rest of admin API).
- On success, write `data/.last_push.json` with
  `{pushed_at, bytes, http_status, railway_pushed_at}`.
- On failure, exit non-zero AND write `data/.last_push.json` with the error so
  the meta-monitor can pick it up next cycle.

### Fix C — Stop `set -e` from killing the wrapper before sync
**File:** `scripts/sentinel-cron.sh`
- Drop `set -e`; capture the daily run's exit code, log it, then **always**
  attempt the sync.
- Propagate the worst exit code at the end so launchd metadata still reflects
  reality, but never short-circuit sync.
- Add timestamps + tee log lines for the sync step so we can observe push
  failures in `sentinel_cron.log` (not just `sentinel_cron_err.log`).

### Fix D — Eliminate the score cliff (single-source staleness penalty)
**File:** `web_dashboard/html.py:_compute_health_score`
- Compute one staleness penalty from `last_health_check` age, not two.
- Use a smooth gradient:
  - age ≤ expected cadence + 1h buffer → 0
  - cadence+1h … cadence+12h → linear -1 per hour up to -10
  - cadence+12h … 48h → linear -10 → -25
  - >48h → -25 (clamped)
- Remove the stale-HC block entirely (it was the second deduction). G3 alone
  produces the staleness signal in the score; the gate panel still shows
  severity.

### Fix E — Make G3 threshold cadence-aware
**File:** `web_dashboard/html.py` G3 gate construction
- Read `EXPECTED_CADENCE_SECONDS` from a small new constants module
  (`sentinel/cadence.py`) — single source of truth, default 3600 (hourly).
- Severity thresholds:
  - `ok` if `age_h < cadence_h + 1`
  - `warning` if `cadence_h + 1 ≤ age_h < cadence_h + 12`
  - `critical` if `age_h ≥ cadence_h + 12` AND age_h < 48
  - `halt` if `age_h ≥ 48`
- For the default hourly cadence: ok <2h, warning 2-13h, critical 13-48h,
  halt ≥48h. No cliffs that line up with the staleness-penalty boundaries.

### Fix F — Counters aggregate per-gate severity, not score band
**File:** `web_dashboard/html.py:render_sentinel_page`
- For each experiment, walk `gates.values()` and tally:
  - any `severity == "halt"` → `halted_count` (and skip the per-gate critical
    bump for that experiment, to avoid double-counting halts as criticals).
  - else any `severity == "critical"` → `critical_count`
  - else any `severity == "warning"` → `warning_count`
- Counters become orthogonal to the score, so the dashboard never lies even
  if the score formula evolves.

### Fix G — Sentinel meta-monitoring (detect own staleness)
**New gate:** G_PUSH (or extend G3) in the sync script + a tiny check inside
`run_sentinel.py --daily`:
- Read `data/.last_push.json`. If missing or `pushed_at` older than
  `EXPECTED_CADENCE_SECONDS * 3` (default 3h), emit a CRITICAL alert via
  the existing telegram channel: "Sentinel push pipeline silent for Xh —
  Railway dashboard is stale".
- Surface the same fact in the local dashboard's Sentinel page as a banner.

## 4. Test Plan (every fix gets at least one new test)

New test file: `tests/test_sentinel_health_score.py`
- `test_score_no_cliff_at_24h_boundary` — score curve `|s(24h+ε) - s(24h-ε)|
  ≤ 5` (was 25).
- `test_score_curve_monotonic_in_age` — sweep 0–60h, assert non-increasing.
- `test_score_halted_returns_zero` — halt severity short-circuits to 0.
- `test_score_no_double_deduct_for_staleness` — given only G3-stale, only one
  bucket (G3) deducts.
- `test_g3_thresholds_default_cadence` — boundaries at cadence+1, +12, +48.
- `test_g3_thresholds_respect_custom_cadence` — pass cadence=3600 vs 86400,
  assert thresholds shift accordingly.

New test file: `tests/test_sentinel_summary_counters.py`
- `test_counter_critical_gate_counts_as_critical` — exp with crit gate,
  score 90 → `critical_count == 1`.
- `test_counter_halt_does_not_double_count_as_critical` — halted exp →
  `halted_count == 1`, `critical_count == 0`.
- `test_counter_warning_only` — only warning gates, no crit/halt → warning_count=1.
- `test_counter_orthogonal_to_score` — score band manipulated, counters
  unchanged.

New test file: `tests/test_sentinel_push_observability.py`
- `test_last_push_written_on_success` — mocks urlopen, asserts
  `.last_push.json` is created with `pushed_at` + `http_status=200`.
- `test_last_push_written_on_404` — asserts `.last_push.json` records error.
- `test_meta_monitor_alerts_when_push_stale` — ages `.last_push.json`,
  expects critical alert from `run_sentinel.py --daily`.
- `test_meta_monitor_silent_when_push_fresh` — fresh push, no alert.

New test file: `tests/test_sentinel_cron_wrapper.py`
- `test_wrapper_runs_sync_even_when_daily_exits_nonzero` — bash subshell
  test: stub `run_sentinel.py` returning 1, assert `sync_sentinel_data.py`
  is still called.
- `test_wrapper_propagates_worst_exit_code` — daily=1, sync=0 → wrapper exits 1
  but only after running both.

Update existing test file: any file that asserts old G3 thresholds or old
score formula gets its expected values bumped.

## 5. Rollout

1. Branch: `feature/sentinel-health-reliability` (created at start of Phase 3).
2. Per-fix commits in this order (each commit green on its own tests):
   - C1: `web/app/api/admin/push-sentinel/route.ts` + tests.
   - C2: `sync_sentinel_data.py` auth fix + last-push observability + tests.
   - C3: `sentinel-cron.sh` resilience + bash test.
   - C4: `sentinel/cadence.py` + `_compute_health_score` rewrite + score
     cliff/curve tests.
   - C5: G3 threshold rewrite + tests.
   - C6: counter aggregation rewrite + tests.
   - C7: meta-monitor + tests.
3. Run full pytest suite — must be green.
4. Manual verification:
   - Trigger one cron cycle on the Mac. Confirm Railway receives the push.
   - Inspect dashboard: scores must reflect actual gate severities, no
     cliffs, counters should match per-gate severity.
   - Capture HTML snapshot for the report.
5. Phase 5: write `web_dashboard/SENTINEL_FIX_REPORT.md`.

## 6. Hard-Rule Compliance

- ☑ Feature branch — all work in `feature/sentinel-health-reliability`.
- ☑ Tests added for every fix — see §4.
- ☑ No silent fail-open — every push attempt logs + writes
  `.last_push.json`; meta-monitor alerts on staleness.
- ☑ Sentinel detects own staleness — Fix G (`G_PUSH` / meta-monitor).
- ☑ Push observable — `.last_push.json` + cron log lines + dashboard banner.
