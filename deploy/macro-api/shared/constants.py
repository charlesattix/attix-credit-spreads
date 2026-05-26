"""Shared constants used across the trading system.

This is the single canonical location for all named constants.  Do NOT
create secondary ``constants.py`` files elsewhere in the tree.
"""

import logging
import os
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Standardized project paths (ARCH-PY-09)
# Override DATA_DIR via ATTIX_DATA_DIR env var for persistent volumes
# (e.g. Railway volume mount at /app/data).
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('ATTIX_DATA_DIR', os.path.join(PROJECT_ROOT, 'data'))
OUTPUT_DIR = os.environ.get('ATTIX_OUTPUT_DIR', os.path.join(PROJECT_ROOT, 'output'))
LOGS_DIR = os.environ.get('ATTIX_LOGS_DIR', os.path.join(PROJECT_ROOT, 'logs'))
MODELS_DIR = os.path.join(PROJECT_ROOT, 'ml', 'models')
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.yaml')

# ---------------------------------------------------------------------------
# MASTERPLAN risk management — HARD-CODED, never user-configurable
# ---------------------------------------------------------------------------
MAX_RISK_PER_TRADE = 0.05        # 5% max risk on any single trade
MAX_TOTAL_EXPOSURE = 0.15        # 15% max total portfolio exposure
DAILY_LOSS_LIMIT = 0.08          # 8% daily loss → stop all alerts for the day
WEEKLY_LOSS_LIMIT = 0.15         # 15% weekly loss → 50% size reduction
MIN_RISK_REWARD = 1.0            # minimum risk/reward ratio
MAX_CORRELATED_POSITIONS = 3     # max positions with same direction
COOLDOWN_AFTER_STOP = 30 * 60   # 30 minutes cooldown after stop-out (seconds)

# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
MAX_CONTRACTS_PER_TRADE = 10

# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------
MANAGEMENT_DTE_THRESHOLD = 21

# ---------------------------------------------------------------------------
# Options pricing
# ---------------------------------------------------------------------------
DEFAULT_RISK_FREE_RATE = float(os.environ.get('ATTIX_RISK_FREE_RATE', '0.045'))

# ---------------------------------------------------------------------------
# Backtesting defaults
# ---------------------------------------------------------------------------
BACKTEST_SHORT_STRIKE_OTM_FRACTION = 0.90
BACKTEST_CREDIT_FRACTION = 0.35

# ---------------------------------------------------------------------------
# Economic calendar
# ---------------------------------------------------------------------------

# Known FOMC meeting dates 2025-2026
FOMC_DATES = [
    datetime(2025, 1, 29, tzinfo=timezone.utc),
    datetime(2025, 3, 19, tzinfo=timezone.utc),
    datetime(2025, 5, 7, tzinfo=timezone.utc),
    datetime(2025, 6, 18, tzinfo=timezone.utc),
    datetime(2025, 7, 30, tzinfo=timezone.utc),
    datetime(2025, 9, 17, tzinfo=timezone.utc),
    datetime(2025, 11, 5, tzinfo=timezone.utc),
    datetime(2025, 12, 17, tzinfo=timezone.utc),
    datetime(2026, 1, 28, tzinfo=timezone.utc),
    datetime(2026, 2, 4, tzinfo=timezone.utc),
    datetime(2026, 3, 18, tzinfo=timezone.utc),
    datetime(2026, 5, 6, tzinfo=timezone.utc),
    datetime(2026, 6, 17, tzinfo=timezone.utc),
    datetime(2026, 7, 29, tzinfo=timezone.utc),
    datetime(2026, 9, 16, tzinfo=timezone.utc),
    datetime(2026, 11, 4, tzinfo=timezone.utc),
    datetime(2026, 12, 16, tzinfo=timezone.utc),
]

# CPI release dates (typically 2nd Tuesday-Thursday of month)
CPI_RELEASE_DAYS = [12, 13, 14]

# ---------------------------------------------------------------------------
# Staleness check — warn if FOMC dates are outdated
# ---------------------------------------------------------------------------
if FOMC_DATES and FOMC_DATES[-1] < datetime.now(timezone.utc):
    logging.getLogger(__name__).warning(
        "FOMC_DATES are stale — the latest date (%s) is in the past. "
        "Update shared/constants.py with the current year's FOMC schedule "
        "to keep event-risk detection accurate.",
        FOMC_DATES[-1].strftime("%Y-%m-%d"),
    )
