#!/bin/bash
# Credit Spread Scanner — Cron Runner
# Called by crontab at scheduled market hours (ET, weekdays only).
#
# All active experiments are managed exclusively by their LaunchAgent schedulers:
#   EXP-400/401/503/600 — persistent com.pilotai.exp*.plist (main.py scheduler)
#   EXP-700/800         — calendar com.pilotai.exp*.plist (StartCalendarInterval)
#
# scan-cron.sh is now a no-op kept for legacy compatibility.
# DO NOT add scanner calls here — it causes double-execution with LaunchAgents.
# See: research/double-execution-investigation.md (2026-03-27)

set -euo pipefail

PROJECT_DIR="/Users/charlesbot/projects/pilotai-credit-spreads"
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

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] scan-cron.sh: all experiments managed by LaunchAgents — nothing to do"
exit 0
