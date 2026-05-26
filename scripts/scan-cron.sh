#!/bin/bash
# Credit Spread Scanner — Cron Runner
# Runs one scan cycle for each active paper trading experiment.
# Called by crontab at scheduled market hours (ET, weekdays only).
#
# Experiments:
#   champion — configs/paper_champion.yaml  .env.champion  data/attix_champion.db
#   exp401   — configs/paper_exp401.yaml    .env.exp401    data/pilotai_exp401.db

set -euo pipefail

PROJECT_DIR="/Users/charlesbot/projects/attix-credit-spreads"
LOG_DIR="${PROJECT_DIR}/logs"
MAX_LOG_SIZE=$((5 * 1024 * 1024))  # 5 MB

mkdir -p "$LOG_DIR"

# Skip weekends (extra safety — cron schedule is Mon-Fri but TZ edge cases exist)
DOW=$(TZ=America/New_York date +%u)  # 1=Mon, 7=Sun
if [ "$DOW" -gt 5 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Skipping scan — weekend (day=$DOW)"
  exit 0
fi

cd "$PROJECT_DIR"

# ── Pre-flight: validate API keys ──────────────────────────────────────────────
KEY_LOG="${LOG_DIR}/validate-keys.log"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Running key validation..." >> "$KEY_LOG"
if ! /usr/bin/python3 scripts/validate_keys.py >> "$KEY_LOG" 2>&1; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] CRITICAL: Key validation failed — aborting scans" >> "$KEY_LOG"
  # Alert via telegram if notifier available
  /usr/bin/python3 -c "
from shared.notifier import send_alert
send_alert('CRITICAL: validate_keys.py failed — scans aborted. Check $KEY_LOG')
" 2>/dev/null || true
  exit 1
fi
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Key validation passed" >> "$KEY_LOG"

_run_scan() {
  local EXP="$1"
  local CONFIG="$2"
  local ENV_FILE="$3"
  local DB="$4"
  local LOG_FILE="${LOG_DIR}/scan-cron-${EXP}.log"

  # Rotate log if too large
  if [ -f "$LOG_FILE" ] && [ "$(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_SIZE" ]; then
    mv "$LOG_FILE" "${LOG_FILE}.1"
  fi

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting ${EXP} scan (ET: $(TZ=America/New_York date '+%H:%M %Z'))" >> "$LOG_FILE"

  /usr/bin/python3 main.py scan \
    --config "$CONFIG" \
    --env-file "$ENV_FILE" \
    --db "$DB" \
    >> "$LOG_FILE" 2>&1
  local EXIT_CODE=$?

  if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ${EXP} scan completed successfully" >> "$LOG_FILE"
  else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ${EXP} scan failed with exit code $EXIT_CODE" >> "$LOG_FILE"
  fi
  echo "---" >> "$LOG_FILE"
}

_run_scan "exp400" "configs/paper_champion.yaml" ".env.exp400" "data/pilotai_exp400.db"
_run_scan "exp401" "configs/paper_exp401.yaml"   ".env.exp401" "data/pilotai_exp401.db"
_run_scan "exp503" "configs/paper_exp503.yaml"   ".env.exp503" "data/pilotai_exp503.db"
_run_scan "exp600" "configs/paper_exp600.yaml"   ".env.exp600" "data/pilotai_exp600.db"
_run_scan "exp1220" "configs/paper_exp1220.yaml" ".env.exp1220" "data/pilotai_exp1220.db"
