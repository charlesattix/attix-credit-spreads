# RUNBOOK — Launching an Experiment End-to-End

**Status:** Authoritative runbook for taking an experiment from `configuring` →
live paper trading → visible on the dashboard.
**Canonical command:** `python -m experiments.launch <EXP-ID>`

> **TL;DR** — Provision the experiment (registry entry + config + env file), then
> run **one command**:
> ```bash
> python -m experiments.launch EXP-NNN
> ```
> It validates, preflights, flips the registry status, stamps `live_since`,
> enrolls the experiment in SENTINEL, starts/verifies the worker, and confirms
> the dashboard shows it — **atomically, rolling everything back on any failure.**

---

## 0. Source-of-truth map

| Concern | Canonical location | Notes |
|---|---|---|
| Experiment metadata, status, lifecycle | `experiments/registry.json` (schema v3.0) | **The single source of truth.** Read/written via `experiments/registry.py` + `experiments/manager.py` (`ExperimentManager`). |
| Per-experiment secrets & `EXPERIMENT_ID` | `.env.expNNN` (local) / Railway env vars | Loaded by `utils.load_config()` via `dotenv`. |
| Strategy parameters | `configs/paper_expNNN.yaml` | Passed as `--config`. |
| Runtime guardrails / halt state | `sentinel_state.json` | Written by `sentinel/state.py`; read by every scanner at startup (`sentinel/guards.py`). |
| Live trade history | DB at the registry entry's `db_path` (SQLite) | Created by the scanner on first run. |
| Dashboard visibility filter | `experiments.registry.LIVE_STATUSES` = `{active, paused}` | Used by `web_dashboard/data.py`, `app.py`, and `scripts/sync_dashboard_data.py`. |
| Railway scanner scheduling | `scheduler/main.py` (APScheduler) | Registers a cron scanner per **live** experiment, read from the registry. |

**Removed (do not look for these):** `experiments.yaml` and `pilotctl.py` were
deleted in the ExperimentManager migration. The registry is the only experiment
list now.

**Statuses** (`experiments/registry.py`): `registered → configuring → active →
{paused,stopped,retired,failed}`; `completed` is terminal (research entries).
Transitions are validated by `VALID_TRANSITIONS` — you cannot jump
`registered → active`; you pass through `configuring`.

---

## 1. Provisioning (prerequisites for launch)

The launcher **requires** the experiment to already exist in the registry in
`configuring` status, with a config and env file present.

### 1.1 Register the experiment

```bash
python scripts/register_experiment.py \
  --id EXP-NNN --creator <maximus|charles> \
  --name "Short Name" --ticker SPY \
  --status configuring \
  --notes "One-line strategy summary."
```
Then set its `config_path`, `env_file`, `db_path`, `tmux_session`, and
`alpaca_account_id` (via `scripts/registry_cli.py` or by editing the entry).
`register_experiment.py` initialises `live_since = None`; the launcher stamps it.

### 1.2 Create `.env.expNNN`

Minimum (see an existing `.env.exp*` as a template):
```
EXPERIMENT_ID=EXP-NNN
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
POLYGON_API_KEY=...
```
On Railway, these come from per-experiment env vars instead of a file.

### 1.3 Create `configs/paper_expNNN.yaml`

Strategy parameters (use an existing `configs/paper_exp*.yaml` as a template).
Must satisfy `scripts/preflight_check.py` (db_path, experiment_id, paper_mode,
logging, strategy, risk sections).

---

## 2. Launch — the one command

```bash
python -m experiments.launch EXP-NNN                 # auto-detect local vs Railway
python -m experiments.launch EXP-NNN --mode local    # force local (tmux)
python -m experiments.launch EXP-NNN --mode railway  # force railway (scheduler-driven)
python -m experiments.launch EXP-NNN --dry-run       # validate + preflight only, no changes
```

### What it does — atomically, with rollback

| # | Step | Detail |
|---|---|---|
| 1 | **Validate** | env file exists, config exists, registry entry is in `configuring`. |
| 2 | **Preflight** | runs `scripts/preflight_check.py` on the config. |
| 3 | **Transition** | `configuring → active` via `registry.transition_status` (stamps `last_started_at`). |
| 4 | **Stamp `live_since`** | today's date — the launcher fills this; `transition_status` does not. |
| 5 | **SENTINEL enroll** | upserts the `sentinel_state.json` entry: `status=active`, config fingerprint, `account_id`, `live_since`, `enrolled_at`. |
| 6 | **Start worker + verify** | runs ONE `DRY_RUN` scan to exercise the pipeline, confirms the DB was created and is a valid sqlite file; then starts the persistent worker (tmux locally; on Railway the registry-driven `scheduler/main.py` picks it up — no local process). |
| 7 | **Verify dashboard** | confirms the experiment is in the live set queried by the dashboard. |

**Atomicity / rollback.** `registry.json` and `sentinel_state.json` are
byte-snapshotted before any mutation. If any step fails, both files are restored
and a started tmux session is killed — a failed launch leaves the system
**byte-identical** to before. The result prints which steps ran and, on failure,
exactly what was rolled back.

### Why the order is "flip status, then verify" (not the reverse)

SENTINEL's pre-scan guard (`sentinel/guards.py:_check_registry_status`)
hard-exits any scan whose registry status is not `active`/`paused`. So status
**must** be `active` and the experiment enrolled before a verification scan can
run. The smoke scan runs with `DRY_RUN=1` so verification never submits live
(paper) orders; the persistent worker started afterward runs normally. Rollback
is what makes this safe — verification failure reverts the status flip.

### Modes

- **local** — starts a detached tmux session running
  `main.py scheduler --config … --env-file …`. Requires `tmux`.
- **railway** — does **not** start a local process; `scheduler/main.py` reads
  the registry and runs every live experiment on its 09:25 ET cron. Flipping the
  status to `active` *is* the registration. Auto-detected via `RAILWAY_*` env vars.

---

## 3. Health check (read-only)

```bash
python -m experiments.launch --status EXP-NNN
```
Reports registry status, `live_since`, `last_started_at`, whether it's in the
live set, DB existence + trade count, and SENTINEL enrollment/halt state, with an
overall `healthy` verdict. Exit code: `0` healthy, `2` not fully live, `1` not
found.

---

## 4. Verification (what the launcher checks — and how to check manually)

1. **Process** — local: `tmux ls` shows the session; Railway: scheduler logs show
   `=== EXP-NNN COMPLETE … (rc=0) ===`.
2. **DB** — `sqlite3 <db_path> "SELECT COUNT(*) FROM trades;"`. No DB ⇒ never traded.
3. **Registry** — `python scripts/registry_cli.py status EXP-NNN` ⇒ `active` with
   `live_since` set.
4. **Validation** — `python scripts/registry_cli.py validate`.
5. **Dashboard** — once `active`, it appears (filter = `LIVE_STATUSES`). Push to
   Railway with `python scripts/sync_dashboard_data.py --push`. Quick check:
   ```bash
   python -c "from web_dashboard import data; print([e['id'] for e in data.get_live_experiments()])"
   ```

---

## 5. Low-level primitives (the launcher wraps these)

You normally won't call these directly — the launcher composes them atomically —
but they exist for surgical fixes:

| Action | Command |
|---|---|
| Flip status only | `python scripts/registry_cli.py activate EXP-NNN` (sets `last_started_at`, **not** `live_since`) |
| Pause / stop / retire | `python scripts/registry_cli.py {pause,stop,retire} EXP-NNN` |
| Update fingerprint after a config change | `python -c "from sentinel.state import update_fingerprint; update_fingerprint('EXP-NNN')"` |
| Start the worker by hand | `python main.py scheduler --config configs/paper_expNNN.yaml --env-file .env.expNNN` |

**Caution:** using these piecemeal is what historically left experiments
half-launched (status flipped but no worker, or a worker with no `live_since`,
or a SENTINEL `config drift` / `Non-functional — 0 trades` halt). Prefer
`python -m experiments.launch`.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `status is 'active', expected 'configuring'` | already launched | use `--status`; to relaunch, stop/retire first |
| `env file not found` (local) | missing `.env.expNNN` | create it, or use `--mode railway` if secrets are in Railway env |
| `preflight failed` | config missing required fields | fix `configs/paper_expNNN.yaml` per `preflight_check.py` |
| `smoke scan failed` / `DB was not created` | pipeline error (bad keys, data, config) | inspect the printed scan tail; launcher already rolled back |
| SENTINEL `config drift detected` halt | `config_fingerprint` ≠ on-disk config | re-run launch, or `update_fingerprint(...)` after an approved change |
| SENTINEL `Non-functional — 0 trades` halt | worker ran but never traded | check strategy/data; not a launch bug |

---

## 7. What the launcher consolidated

Before this orchestrator, launching meant three disconnected manual systems
(start a worker; separately edit the registry status + `live_since`; separately
edit `sentinel_state.json`), and the scanner list was hardcoded. Now:

- **Single command** runs the full sequence atomically with rollback.
- **`live_since`** is stamped automatically (was always manual; `transition_status`
  still only sets `last_started_at`).
- **SENTINEL enrollment** is automated with a computed config fingerprint.
- **`scheduler/main.py`** is registry-driven (`LIVE_STATUSES`) — no hardcoded list;
  adding an experiment to the schedule is just launching it.
- **Dashboard filters** (`data.py`, `app.py`, `sync_dashboard_data.py`) use the
  canonical `LIVE_STATUSES`, so list/count/detail/export all agree.

**Residual manual step:** registering an experiment and authoring its
`config`/`.env` (§1) is still by hand — the launcher validates these but does not
create them.
