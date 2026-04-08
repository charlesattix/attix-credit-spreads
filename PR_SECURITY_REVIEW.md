# PR Security Review: origin/maximus/ensemble-ml

**Date:** 2026-03-24
**Reviewer:** Claude Code (automated static analysis of `git diff main..origin/maximus/ensemble-ml`)
**Verdict:** **DO NOT MERGE** — 2 critical regressions, 1 new critical vulnerability, 2 medium regressions

---

## Verdict Summary

| # | Severity | Category | File | Status |
|---|----------|----------|------|--------|
| 1 | CRITICAL | New hardcoded secrets | `scripts/check_accounts.py` | NEW VULNERABILITY |
| 2 | CRITICAL | SQL injection re-introduced | `shared/database.py` | REGRESSION |
| 3 | MEDIUM | Command injection re-introduced | `pilotctl.py` | REGRESSION |
| 4 | MEDIUM | Web dashboard deleted without security-reviewed replacement | `web_dashboard/` | UNKNOWN |
| 5 | LOW | Logging downgraded | `shared/credentials.py` | REGRESSION |
| 6 | — | New ML dependencies | `requirements.txt` | PASS |
| 7 | — | New Python files (ensemble, tests) | `compass/` | PASS |
| 8 | — | Docker / deployment config | `Dockerfile`, `railway.toml` | PASS (with caveats) |

---

## BLOCKING ISSUES (must fix before merge)

---

### 1. CRITICAL — Hardcoded Alpaca Secrets Re-introduced in `scripts/check_accounts.py`

**This is a new vulnerability introduced by this branch, not a regression.**

The main branch had this file rewritten to load credentials from `.env.expNNN` files (the secure pattern). This branch **replaces that with a hardcoded dict of live API credentials**:

```python
# scripts/check_accounts.py (lines 14–38 in the branch)
ACCOUNTS = {
    "exp036": {
        "key":        "PK4SGNFT3BGN54TCVOE4G44OYQ",
        "secret":     "D3pVjqqBF9kLjyW1W9UMJcoqzVvqex5azhGB15fzgTCh",
        "account_id": "PA3D6UPXF5F2",
    },
    "exp059": {
        "key":        "PK6URS6OBCSSHZZ2RQZSE2FOAH",
        "secret":     "4PTrX1ppT5iZRAnwpcY7282of8UiFyN9pCEE2ZcmjzJ1",
        "account_id": "PA3LP867WNGU",
    },
    "exp154": {
        "key":        "PKANAYVKHZX24Z3KCYNI2PLSCR",
        "secret":     "GyBN2gCyuXfG7yTqFKs5JKTHL8eyC8SYTQ77Y3oyQp4J",
        "account_id": "PA3UNOV58WGK",
    },
    "exp305": {
        "key":        "PKSPAM5732NK425PEUR7ZBELCB",
        "secret":     "4Xmjn5wynCWoiJboiAf95tGozQCCBD96rnQYujNTNuiZX",
        "account_id": "PA3W9FZKK6XD",
    },
}
```

Four live Alpaca API key/secret/account-ID triplets are committed to source. Even if the file is later deleted, these credentials are permanently stored in git history.

**Required actions before merge:**
1. Rotate all 4 Alpaca key/secret pairs via the Alpaca dashboard immediately (the old keys from the original audit may already have been rotated — rotate these new ones too).
2. Revert `scripts/check_accounts.py` to the env-file-based credential loading pattern from `main`.
3. If credentials already landed in a remote branch, use `git filter-repo` to purge from history.

---

### 2. CRITICAL — SQL Injection Re-introduced in `shared/database.py`

The parameterized queries added in today's security commit are **reverted** to raw f-string interpolation.

**Branch version (vulnerable):**
```python
# load_dedup_entries
cutoff = f"datetime('now', '-{window_seconds} seconds')"
rows = conn.execute(
    f"SELECT ticker, direction, alert_type, last_routed_at FROM alert_dedup WHERE last_routed_at > {cutoff}"
).fetchall()

# delete_old_dedup_entries
conn.execute(
    f"DELETE FROM alert_dedup WHERE last_routed_at <= datetime('now', '-{window_seconds} seconds')"
)
```

**main version (safe):**
```python
window_seconds = int(window_seconds)
rows = conn.execute(
    "SELECT ticker, direction, alert_type, last_routed_at FROM alert_dedup "
    "WHERE last_routed_at > datetime('now', ?)",
    (f"-{window_seconds} seconds",),
).fetchall()
```

The security comments and `int()` cast are also stripped. This directly undoes SECURITY_AUDIT.md finding #2.

**Required action:** Restore the parameterized query pattern from `main` (commit `88e59ba`).

---

### 3. MEDIUM — Command Injection Re-introduced in `pilotctl.py`

The safe list-based subprocess pattern from today's commit is reverted to shell-interpolated string construction:

**Branch version (vulnerable):**
```python
cmd = (
    f"python main.py scheduler "
    f"--config {cfg['config_file']} "
    f"--env-file {cfg['env_file']}"
)
subprocess.run(
    ["tmux", "new-session", "-d", "-s", session, cmd],
    check=True,
)
```

**main version (safe):**
```python
config_path = Path(cfg["config_file"])
env_path    = Path(cfg["env_file"])
if not config_path.exists(): ...
if not env_path.exists(): ...
subprocess.run(
    [
        "tmux", "new-session", "-d", "-s", session,
        sys.executable, "main.py", "scheduler",
        "--config", str(config_path),
        "--env-file", str(env_path),
    ],
    check=True,
)
```

The mitigating factor is that `config_file` and `env_file` values come from the hardcoded experiment dict in `pilotctl.py` rather than direct user input. However this is still a regression from the secure pattern and SECURITY_AUDIT.md finding #5.

**Required action:** Restore list-based subprocess args with path existence checks.

---

## NON-BLOCKING ISSUES (should fix but not merge blockers)

---

### 4. MEDIUM — Web Dashboard Deleted; Replacement Not Reviewed

The entire `web_dashboard/` directory is deleted:
- `web_dashboard/__init__.py`
- `web_dashboard/app.py` — FastAPI app with auth, rate limiting, CSRF fixes
- `web_dashboard/auth.py` — session token logic
- `web_dashboard/data.py` — data loading
- `web_dashboard/html.py` — HTML generation with html.escape() fixes

The replacement appears to be a Next.js app (built in the Dockerfile). This means the following security controls from today's fixes are gone and need to be verified in the new app:

| Control | Old location | Status in new app |
|---------|-------------|-------------------|
| X-API-Key auth | `app.py:require_api_key` | Unknown |
| Session cookie auth (httponly, samesite) | `app.py:login_submit` | Unknown |
| CSRF protection on admin endpoint | `app.py:require_api_key_only` | Unknown |
| Security headers (X-Frame, CSP, HSTS) | `app.py:_SecurityHeadersMiddleware` | Unknown |
| Per-IP + per-key rate limiting | `app.py:_check_rate` | Unknown |
| Query param bounds (limit ge=1,le=1000) | `app.py:experiment_trades` | Unknown |
| HTML escaping | `html.py:html.escape()` | Next.js escapes by default — likely OK |

**Required action:** Before merging, confirm the new Next.js app has equivalent auth and security controls, or document what is pending.

---

### 5. LOW — Logging Level Regression in `shared/credentials.py`

Line 92 is downgraded from `WARNING` back to `DEBUG`:

```python
# Branch (regressed):
logger.debug("Alpaca %s failed: %s", url, exc)

# main (correct):
logger.warning("Alpaca %s failed: %s", url, exc)  # SECURITY AUDIT #14
```

In production (INFO log level), Alpaca API failures become completely invisible. This undoes SECURITY_AUDIT.md finding #14.

**Required action:** Restore `logger.warning`.

---

## PASSING ITEMS

### New Dependencies (`requirements.txt`)

FastAPI/uvicorn removed (old web app gone). New packages added:

```
numpy==2.0.2, pandas==2.3.3, scipy==1.13.1, xgboost==2.1.4,
scikit-learn==1.6.1, hmmlearn==0.3.3, joblib==1.5.3,
yfinance==1.1.0, py_vollib==1.0.1, alpaca-py==0.21.1,
python-telegram-bot==21.10, requests==2.32.5, sentry-sdk==2.19.2,
colorlog==6.10.1, python-dateutil==2.9.0.post0, pytz==2025.2
```

All are standard data science / trading libraries. No known CVEs flagged. No suspicious packages. **PASS.**

### New Python Files (`compass/ensemble_signal_model.py`, etc.)

Reviewed for: hardcoded secrets, `shell=True` subprocess, raw SQL f-strings, `eval()` / `exec()` usage.

All new files — `ensemble_signal_model.py`, `online_retrain.py`, `portfolio_optimizer.py`, `stress_test.py`, `walk_forward.py`, and all test files — are clean. **PASS.**

### `scan-cron.sh`

Changed from exp400/401/503/600 to exp036/059/154/305. Config and DB paths updated accordingly. No security issues. **PASS.**

### `Dockerfile` + `docker-entrypoint.sh`

- Non-root user (`pilotai:1001`) enforced ✓
- Specific Node.js version pinned ✓
- Tini init process for signal handling ✓
- `exec "$@"` fallback in entrypoint is standard Docker pattern ✓
- No hardcoded secrets ✓
- **PASS** (with deployment caveat below).

### Railway Deployment Config

`Procfile` deleted; `railway.json` → `railway.toml` with Docker builder.

**Deployment caveat (not a security issue, but an ops risk):** The new Docker-based deploy requires `DASHBOARD_API_KEY` env var set in Railway. If it's missing, the app will fail at startup. Verify Railway environment variables are configured before the next deploy.

---

## Summary of Required Actions Before Merge

1. **[CRITICAL]** Rotate the 4 Alpaca API key/secret pairs committed in `scripts/check_accounts.py`.
2. **[CRITICAL]** Revert `scripts/check_accounts.py` to env-file-based credential loading.
3. **[CRITICAL]** Restore parameterized SQL queries in `shared/database.py`.
4. **[MEDIUM]** Restore list-based subprocess args in `pilotctl.py`.
5. **[MEDIUM]** Confirm or document auth/rate-limiting/headers in the new Next.js app.
6. **[LOW]** Restore `logger.warning` in `shared/credentials.py`.
7. **[OPS]** Verify `DASHBOARD_API_KEY` (and other required env vars) are set in Railway before deploying.

---

*Review generated via static analysis of `git diff main..origin/maximus/ensemble-ml`. No dynamic testing performed.*
