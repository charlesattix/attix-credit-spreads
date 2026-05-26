# EXPERIMENT PROTOCOL

> The constitution for all agents (Charles and Maximus) operating in this repo.
> Last updated: 2026-03-22. Approved by: Carlos.

---

## The Prime Directive

**`experiments/registry.json` is the single source of truth for all experiments.**
MASTERPLAN.md is a human-readable summary. When they conflict, registry.json wins.

---

## ID Ranges

| Agent   | ID Range  | Example  |
|---------|-----------|----------|
| Maximus | EXP-000 – EXP-599 | EXP-503 |
| Charles | EXP-600+          | EXP-600 |

No agent may create an experiment outside their assigned range.

---

## One Account = One Experiment

Each live paper trading experiment runs in its own isolated Alpaca account.
No two experiments share an account. Ever.

---

## Experiment Lifecycle

```
idea
  │
  ▼
register in registry.json (status: in_development)
  │  • Add to registry.json with id, name, created_by, ticker, phase, next_step
  │  • Add row to MASTERPLAN.md In Development table
  │  • No Carlos approval needed for registration
  │
  ▼
build & backtest (status: in_development)
  │  • Run backtests, tune params, log to leaderboard
  │  • Must pass: overfit_score ≥ 0.70, 5/6 years profitable, WF 3/3
  │
  ▼
validate (status: validated)
  │  • Walk-forward 3/3 profitable
  │  • Monte Carlo P50 passes threshold
  │  • Preflight check passes: python scripts/preflight_check.py <config>
  │
  ▼
Carlos approval ← GATE (required before any deploy)
  │  • Run: python scripts/pre_deploy_check.py <EXP-ID> <config>
  │  • Carlos reviews results in person and answers "yes"
  │  • Approval logged to experiments/approvals.log
  │
  ▼
deploy (status: paper_trading)
  │  • Create configs/paper_expNNN.yaml
  │  • Create deploy/com.attix.expNNN.plist
  │  • Copy plist to ~/Library/LaunchAgents/, launchctl load
  │  • Verify process running: launchctl list | grep attix
  │  • Run dryrun: python scripts/dryrun_expNNN.py
  │  • Update registry.json: status, account_id, live_since
  │  • Commit + push immediately
  │
  ▼
8-week paper validation (status: paper_trading)
  │  • Clock starts on live_since date
  │  • Victory conditions: results within 30% of backtest, win rate >70%, DD <20%
  │  • Monitor via daily_report.py and compare_experiments.py
  │
  ▼
retire OR go live
     • retire → status: retired, add retired_reason, commit + push
     • go live → separate live trading protocol (TBD)
```

---

## Rules

### Before ANY experiment work
```
python scripts/list_experiments.py --all
```
Know what's running before you touch anything.

### Adding to development (free — no approval needed)
1. Pick an ID in your range. Check `list_experiments.py --all` to avoid collisions.
2. Add entry to `registry.json` with `status: in_development`.
3. Add row to MASTERPLAN.md In Development table.
4. Commit + push.

### Moving to live (requires Carlos approval)
1. Run preflight: `python scripts/preflight_check.py configs/paper_expNNN.yaml`
2. Run pre-deploy gate: `python scripts/pre_deploy_check.py EXP-NNN configs/paper_expNNN.yaml`
3. Carlos answers "yes" at the prompt — approval logged to `experiments/approvals.log`.
4. Deploy (plist + launchctl).
5. Update registry.json status → `paper_trading`, add `account_id` and `live_since`.
6. **Commit + push immediately.**

### After ANY status change
Commit and push to the current branch immediately. Do not batch registry changes.
Other agents read registry.json from the repo — stale state causes conflicts.

### Retiring an experiment
1. `launchctl unload ~/Library/LaunchAgents/com.attix.expNNN.plist`
2. Update registry.json: `status: retired`, add `retired_reason`.
3. Update MASTERPLAN.md: move row from Live/Dev table to Retired table.
4. Commit + push.

---

## Config Naming Conventions

| Artifact | Pattern | Example |
|----------|---------|---------|
| Paper config | `configs/paper_expNNN.yaml` | `configs/paper_exp600.yaml` |
| Backtest config | `configs/expNNN_<descriptor>.json` | `configs/exp503_lowvol.json` |
| Plist | `deploy/com.attix.expNNN.plist` | `deploy/com.attix.exp600.plist` |
| Log | `~/logs/expNNN.log` | `~/logs/exp600.log` |
| DB | `data/expNNN/pilotai_expNNN.db` | `data/exp600/pilotai_exp600.db` |
| Dryrun | `scripts/dryrun_expNNN.py` | `scripts/dryrun_exp600.py` |

---

## Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/list_experiments.py --all` | See all experiments (run first, always) |
| `scripts/preflight_check.py <config>` | Validate paper config before deploy |
| `scripts/pre_deploy_check.py <ID> <config>` | Carlos approval gate |
| `scripts/dryrun_expNNN.py` | Post-deploy smoke test |
| `scripts/compare_experiments.py` | Compare live P&L across experiments |
| `scripts/daily_report.py` | Daily performance summary |

---

*This protocol is binding. Violations (deploying without Carlos approval, sharing accounts,
creating IDs outside assigned range) must be flagged immediately in the daily memory log.*
