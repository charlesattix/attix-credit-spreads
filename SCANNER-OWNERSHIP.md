# SCANNER OWNERSHIP

> **This document is authoritative.** When in doubt about who runs what, stop and read this file.
> Last updated: 2026-03-27. Approved by: Charles.

---

## The Core Rule

```
ONE experiment = ONE executor = ONE database = ONE Alpaca account
```

No two processes write to the same database. No two experiments share an account.
No agent starts a scanner that belongs to another agent. No exceptions.

---

## Role Definitions

### Charles — Scanner Execution Owner

Charles owns **all** live scanner execution. This includes:

- Creating LaunchAgent plists and loading them via `launchctl`
- Starting, stopping, and restarting scanner processes
- Monitoring running scanners via logs and process list
- Provisioning new Alpaca paper accounts (via Carlos)
- Deploying new experiments to paper trading

**Charles is the only agent authorized to run `main.py scheduler` or any scanner entrypoint.**

### Maximus — Research Only

Maximus role is **research only**:

- Backtesting and parameter optimization
- Strategy design and signal analysis
- ML model training and evaluation
- Writing configs, proposals, and backtest reports
- Updating `experiments/registry.json` during development phases

**Maximus must NEVER start a scanner, load a LaunchAgent, or write to a live experiment's database.**

---

## Before Starting Any Scanner — Mandatory Pre-flight

Run these three checks **every time**, no exceptions:

```bash
# 1. Check for running scanner processes
ps aux | grep main.py

# 2. Check loaded LaunchAgents
launchctl list | grep attix

# 3. Read this file
cat SCANNER-OWNERSHIP.md
```

If a process is already running for an experiment, do not start another one.
If unsure whether a process should be running, ask Charles before proceeding.

---

## Experiment Registry — Accounts and Executors

### Active (paper_trading)

| Experiment | Name | Ticker | Alpaca Account ID | Executor | Infrastructure |
|------------|------|--------|-------------------|----------|----------------|
| EXP-400 | The Champion | SPY | `PA36XFVLG0WE` | Charles | LaunchAgent: `com.attix.exp400.plist` |
| EXP-401 | The Blend | SPY | `PA3Y2XDYB9I3` | Charles | LaunchAgent: `com.attix.exp401.plist` |
| EXP-503 | ML V2 Aggressive | SPY | `PA3Z9PLVYUL5` | Charles | LaunchAgent: `com.attix.exp503.plist` |
| EXP-600 | IBIT Adaptive | IBIT | `PA3O14JAJHJ0` | Charles | LaunchAgent: `com.attix.exp600.plist` |
| EXP-700 | ML-Filtered Champion | SPY | `PA3D44G9ZYRC` | Charles | scan-cron (no LaunchAgent yet) |
| EXP-800 | Safe Kelly 4/7/9 | SPY | ⚠️ NOT YET PROVISIONED | Charles | No LaunchAgent, no .env |
| EXP-307 | Sector ETF Diversification | SPY/XLI/XLF | ⚠️ NOT YET PROVISIONED | Charles | No LaunchAgent, no .env |

### In Development (no scanner, no account needed yet)

| Experiment | Name | Ticker | Status |
|------------|------|--------|--------|
| EXP-500 | ML Champion | SPY | Waiting on EXP-503 validation results |
| EXP-501 | ML Blend | SPY | Blocked on EXP-500 |
| EXP-601 | IBIT ML Signal Filter | IBIT | Backtest/ML phase |

### Retired (accounts may still exist, scanners must NOT be running)

| Experiment | Name | Alpaca Key ID | Retired Reason |
|------------|------|---------------|----------------|
| EXP-036 | Compound Bull Put (MA200) | `PK4SGNFT3BGN54TCVOE4G44OYQ` | Superseded by EXP-400 |
| EXP-059 | Various | `PK6URS6OBCSSHZZ2RQZSE2FOAH` | Superseded by EXP-400/401 |
| EXP-154 | Various | `PKANAYVKHZX24Z3KCYNI2PLSCR` | Superseded by EXP-400/401 |
| EXP-305 | COMPASS Portfolio | `PKSPAM5732NK425PEUR7ZBELCB` | Multi-ticker approach retired |

---

## Provisioning New Experiments

New Alpaca paper accounts come from **Carlos**. The process:

1. Maximus completes backtest + walk-forward validation (≥0.70 overfit, WF 3/3, MC P50 passes)
2. Charles runs `python scripts/pre_deploy_check.py <EXP-ID> <config>`
3. Carlos reviews results in person and approves (logged to `experiments/approvals.log`)
4. **Carlos creates a new Alpaca paper account** and provides credentials to Charles
5. Charles creates `.env.expNNN`, `configs/paper_expNNN.yaml`, `deploy/com.attix.expNNN.plist`
6. Charles loads the LaunchAgent and verifies it's running
7. Charles updates `experiments/registry.json`: `status`, `account_id`, `live_since`
8. Commit and push immediately

**Do not reuse an existing account for a new experiment, even if the old experiment is retired.**

---

## Known Infrastructure Gaps (as of 2026-03-27)

| Gap | Experiment | Action Required |
|-----|------------|-----------------|
| Missing `ALPACA_PAPER=true` in `.env.exp700` | EXP-700 | Charles to verify — all other env files have this line |
| No LaunchAgent plist | EXP-700 | Charles to create `com.attix.exp700.plist` |
| No account, no .env, no plist | EXP-800 | Charles to provision when ready to deploy |
| No account, no .env, no plist | EXP-307 | Charles to provision when ready to deploy |
