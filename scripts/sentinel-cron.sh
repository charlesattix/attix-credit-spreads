#!/bin/bash
# Sentinel cron wrapper: run daily health check + sync to Railway dashboard.
#
# IMPORTANT: this wrapper deliberately does NOT use `set -e`. The daily run
# returns exit code 1 whenever issues are found (which is essentially always
# in production), and `set -e` was previously aborting the wrapper before the
# Railway sync ever ran — leaving the dashboard frozen on stale data.
#
# Instead, we:
#   1. capture the daily run's exit code,
#   2. ALWAYS attempt the sync,
#   3. exit with the worst exit code at the end, so launchd metadata still
#      reflects reality.
set -uo pipefail
cd /Users/charlesbot/projects/pilotai-credit-spreads

PYTHON=/usr/bin/python3
DAILY_EXIT=0
SYNC_EXIT=0

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting Sentinel daily..."
"$PYTHON" scripts/run_sentinel.py --daily --operator charles-launchd
DAILY_EXIT=$?
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Daily run finished (exit=$DAILY_EXIT)"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Syncing to Railway dashboard..."
"$PYTHON" scripts/sync_sentinel_data.py --push
SYNC_EXIT=$?
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Sync finished (exit=$SYNC_EXIT)"

# Worst exit code wins: any non-zero is surfaced to launchd.
if [[ $DAILY_EXIT -ne 0 || $SYNC_EXIT -ne 0 ]]; then
    WORST=$(( DAILY_EXIT > SYNC_EXIT ? DAILY_EXIT : SYNC_EXIT ))
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Done with errors (worst exit=$WORST, daily=$DAILY_EXIT, sync=$SYNC_EXIT)."
    exit "$WORST"
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Done."
