#!/usr/bin/env bash
# smoke_dashboard.sh — Post-deploy smoke test for the Attix dashboard.
#
# Hits /api/v1/health and asserts the dashboard is not silently regressed:
#   - alpaca_keys_discovered >= MIN_KEYS  (always enforced)
#   - live_injection_ok       == true     (enforced during market hours)
#   - combined_equity_nonzero == true     (enforced during market hours)
#
# A regression like a lost env var or broken live-Alpaca injection renders a
# $0 dashboard or "No Alpaca credentials" — the Railway liveness probe never
# catches it. This does.
#
# Usage:
#   scripts/smoke_dashboard.sh [BASE_URL]
#
# Env overrides:
#   DASHBOARD_URL  base URL (default: live production URL; arg wins over env)
#   MIN_KEYS       minimum alpaca keys expected (default: 9)
#   MAX_RETRIES    health-fetch attempts before giving up (default: 10)
#   RETRY_DELAY    seconds between attempts (default: 15)
#   FORCE_MARKET_HOURS  set to "1" to force market-hours assertions regardless
#                       of clock (useful for testing), "0" to force off-hours.
#
# Exit codes: 0 = healthy, 1 = regression detected, 2 = unreachable/bad JSON.
set -uo pipefail

DEFAULT_URL="https://attix-production.up.railway.app"
BASE_URL="${1:-${DASHBOARD_URL:-$DEFAULT_URL}}"
BASE_URL="${BASE_URL%/}"   # strip trailing slash
HEALTH_URL="${BASE_URL}/api/v1/health"

MIN_KEYS="${MIN_KEYS:-9}"
MAX_RETRIES="${MAX_RETRIES:-10}"
RETRY_DELAY="${RETRY_DELAY:-15}"

command -v jq >/dev/null 2>&1 || { echo "FATAL: jq is required but not installed"; exit 2; }

# --- Determine whether US equity markets are open -------------------------
# Regular session: Mon-Fri, 09:30-16:00 America/New_York. (Holidays are not
# modelled; a holiday simply means live checks run as warnings if data is stale.)
is_market_hours() {
  if [[ "${FORCE_MARKET_HOURS:-}" == "1" ]]; then return 0; fi
  if [[ "${FORCE_MARKET_HOURS:-}" == "0" ]]; then return 1; fi
  local dow hm
  dow="$(TZ='America/New_York' date +%u)"   # 1=Mon .. 7=Sun
  hm="$(TZ='America/New_York' date +%H%M)"   # zero-padded HHMM
  # 10# forces base-10 so leading-zero times like 0930 aren't read as octal.
  hm=$((10#$hm))
  if (( dow >= 1 && dow <= 5 )) && (( hm >= 930 && hm < 1600 )); then
    return 0
  fi
  return 1
}

# --- Fetch /api/v1/health with retries (deploys take time to settle) ------
BODY=""
HTTP_CODE=""
for attempt in $(seq 1 "$MAX_RETRIES"); do
  RESP="$(curl -fsS -m 20 -w $'\n%{http_code}' "$HEALTH_URL" 2>/dev/null || true)"
  HTTP_CODE="$(printf '%s' "$RESP" | tail -n1)"
  BODY="$(printf '%s' "$RESP" | sed '$d')"
  if [[ "$HTTP_CODE" == "200" ]] && printf '%s' "$BODY" | jq -e . >/dev/null 2>&1; then
    break
  fi
  echo "attempt ${attempt}/${MAX_RETRIES}: health not ready (http=${HTTP_CODE:-none}); retrying in ${RETRY_DELAY}s..."
  BODY=""
  sleep "$RETRY_DELAY"
done

if [[ -z "$BODY" ]]; then
  echo "FATAL: ${HEALTH_URL} unreachable or returned invalid JSON after ${MAX_RETRIES} attempts (last http=${HTTP_CODE:-none})"
  exit 2
fi

echo "Health response from ${HEALTH_URL}:"
printf '%s\n' "$BODY" | jq .

# --- Parse fields ----------------------------------------------------------
status="$(printf '%s' "$BODY" | jq -r '.status // "missing"')"
keys="$(printf '%s' "$BODY" | jq -r '.alpaca_keys_discovered // 0')"
inj="$(printf '%s' "$BODY" | jq -r '.live_injection_ok // false')"
equity_ok="$(printf '%s' "$BODY" | jq -r '.combined_equity_nonzero // false')"
experiments="$(printf '%s' "$BODY" | jq -r '.experiments_active // 0')"

fail=0

if [[ "$status" != "ok" ]]; then
  echo "FAIL: status is '${status}' (expected 'ok')"
  fail=1
fi

if (( keys < MIN_KEYS )); then
  echo "FAIL: alpaca_keys_discovered=${keys} < ${MIN_KEYS} (env keys dropped?)"
  fail=1
else
  echo "PASS: alpaca_keys_discovered=${keys} (>= ${MIN_KEYS})"
fi

if is_market_hours; then
  echo "Market is OPEN — enforcing live-data assertions."
  if [[ "$inj" != "true" ]]; then
    echo "FAIL: live_injection_ok=${inj} (live Alpaca data not reaching dashboard)"
    fail=1
  else
    echo "PASS: live_injection_ok=true"
  fi
  if [[ "$equity_ok" != "true" ]]; then
    echo "FAIL: combined_equity_nonzero=${equity_ok} (dashboard would render \$0 / 'No Alpaca credentials')"
    fail=1
  else
    echo "PASS: combined_equity_nonzero=true"
  fi
else
  echo "Market is CLOSED — live-data assertions are advisory only:"
  echo "  live_injection_ok=${inj}  combined_equity_nonzero=${equity_ok}  experiments_active=${experiments}"
fi

if (( fail == 0 )); then
  echo "SMOKE TEST PASSED ✅"
  exit 0
fi
echo "SMOKE TEST FAILED ❌"
exit 1
