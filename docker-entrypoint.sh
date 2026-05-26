#!/bin/sh
set -e
set -o pipefail 2>/dev/null || true  # pipefail not available in all sh implementations

# Verify required binaries exist
for bin in python3 node; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "ERROR: required binary '$bin' not found in PATH" >&2
        exit 1
    fi
done

# Ensure data directory exists and is writable
# The volume is mounted by Railway, so we just need to ensure subdirectories exist
DATA_DIR="${ATTIX_DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR" /app/output /app/logs 2>/dev/null || true

# Try to initialize database - if it fails (volume not ready), that's OK,
# the API will create it on first access
if python3 -c "from shared.database import init_db; init_db()" 2>&1; then
    echo "Database initialized successfully"
else
    echo "INFO: Database will be initialized on first API access"
fi

case "$1" in
  web)
    cd /app/web
    exec node server.js
    ;;
  scheduler)
    exec python3 /app/main.py scheduler
    ;;
  scan)
    exec python3 /app/main.py scan
    ;;
  backtest)
    exec python3 /app/main.py backtest
    ;;
  all)
    # Run both the web server and the scan scheduler.
    # Web server in background, scheduler in foreground.
    # tini propagates SIGTERM to the process group, so both shut down cleanly.
    cd /app/web && node server.js &
    WEB_PID=$!
    echo "Web server started (PID $WEB_PID)"

    # Wait for the web server to pass healthcheck before starting the
    # scheduler.  The scheduler's Python init (ML training, data downloads)
    # is CPU-heavy and will starve the web server if started too early,
    # causing Railway's healthcheck to fail.
    echo "Waiting for web server healthcheck..."
    TRIES=0
    MAX_TRIES=30
    while [ "$TRIES" -lt "$MAX_TRIES" ]; do
      if curl -sf http://localhost:8080/api/health >/dev/null 2>&1; then
        echo "Web server healthy after ${TRIES}s"
        break
      fi
      TRIES=$((TRIES + 1))
      sleep 1
    done
    if [ "$TRIES" -ge "$MAX_TRIES" ]; then
      echo "WARNING: Web server not healthy after ${MAX_TRIES}s, starting scheduler anyway"
    fi

    # Extra stabilisation delay after healthcheck passes: gives Railway time to
    # mark the deployment healthy before the CPU-heavy scheduler init begins.
    echo "Waiting 60s for deployment to stabilise before starting scheduler..."
    sleep 60

    if [ "${ENABLE_SCHEDULER:-false}" != "true" ]; then
      echo "ENABLE_SCHEDULER is not 'true' — skipping scheduler (web-only mode)"
      wait $WEB_PID
      exit $?
    fi

    echo "Starting scan scheduler..."
    python3 /app/main.py scheduler &
    SCHED_PID=$!

    # Wait for either process to exit, then stop the other
    wait -n $WEB_PID $SCHED_PID 2>/dev/null || true
    kill $WEB_PID $SCHED_PID 2>/dev/null || true
    wait
    ;;
  *)
    exec "$@"
    ;;
esac
