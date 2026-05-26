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
GAMMA_LOTTO_MAX_RISK_PCT = 0.005 # 0.5% max risk per lotto play

# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
MAX_CONTRACTS_PER_TRADE = 10

# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------
MANAGEMENT_DTE_THRESHOLD = 5

# 0DTE strategy
ZERO_DTE_TICKERS = ["SPY", "QQQ"]
ZERO_DTE_STRATEGY_NAME = "ZeroDTESpreadStrategy"

# Per-strategy and delta caps (backtester parity)
MAX_POSITIONS_PER_STRATEGY = 5
MAX_ABS_DELTA = 50.0

# 0DTE strategy
ZERO_DTE_TICKERS = ["SPY", "QQQ"]
ZERO_DTE_STRATEGY_NAME = "zero_dte_spread"
ZERO_DTE_MANAGEMENT_DTE_THRESHOLD = 0

# Per-strategy and delta caps (backtester parity)
MAX_POSITIONS_PER_STRATEGY = 5
MAX_ABS_DELTA = 50.0

# ---------------------------------------------------------------------------
# Options pricing
# ---------------------------------------------------------------------------
DEFAULT_RISK_FREE_RATE = float(os.environ.get('ATTIX_RISK_FREE_RATE', '0.045'))

# Historical average Fed Funds effective rate by year (for dynamic BS pricing).
# Source: FRED (DFF series) — annual averages rounded to 3 decimal places.
RISK_FREE_RATE_BY_YEAR = {
    2020: 0.004,   # ~0.4% avg (0% from Mar onward)
    2021: 0.001,   # ~0.1% (near zero all year)
    2022: 0.017,   # ~1.7% avg (0% Jan → 4.3% Dec, weighted)
    2023: 0.051,   # ~5.1% avg
    2024: 0.053,   # ~5.3% avg
    2025: 0.045,   # ~4.5% est (rate cuts beginning)
    2026: 0.038,   # ~3.8% est
}


def get_risk_free_rate(date) -> float:
    """Return the risk-free rate appropriate for a given date.

    Uses year-level granularity from RISK_FREE_RATE_BY_YEAR.
    Falls back to DEFAULT_RISK_FREE_RATE for unknown years.
    """
    year = date.year if hasattr(date, 'year') else date
    return RISK_FREE_RATE_BY_YEAR.get(year, DEFAULT_RISK_FREE_RATE)

# ---------------------------------------------------------------------------
# Backtesting defaults
# ---------------------------------------------------------------------------
BACKTEST_SHORT_STRIKE_OTM_FRACTION = 0.90
BACKTEST_CREDIT_FRACTION = 0.35

# ---------------------------------------------------------------------------
# Economic calendar
# ---------------------------------------------------------------------------

# Known FOMC meeting/decision dates 2020-2026
# Sources: federalreserve.gov/monetarypolicy/fomccalendars.htm
# Dates are the announcement day (last day of 2-day meetings).
# 2020 includes 3 emergency COVID meetings (Mar 3, Mar 15, Mar 23).
FOMC_DATES = [
    # --- 2020 (7 scheduled + 3 emergency) ---
    datetime(2020, 1, 29, tzinfo=timezone.utc),
    datetime(2020, 3, 3, tzinfo=timezone.utc),   # emergency rate cut
    datetime(2020, 3, 15, tzinfo=timezone.utc),  # emergency rate cut to 0%
    datetime(2020, 3, 23, tzinfo=timezone.utc),  # emergency credit expansion
    datetime(2020, 4, 29, tzinfo=timezone.utc),
    datetime(2020, 6, 10, tzinfo=timezone.utc),
    datetime(2020, 7, 29, tzinfo=timezone.utc),
    datetime(2020, 9, 16, tzinfo=timezone.utc),
    datetime(2020, 11, 5, tzinfo=timezone.utc),
    datetime(2020, 12, 16, tzinfo=timezone.utc),
    # --- 2021 (8 scheduled) ---
    datetime(2021, 1, 27, tzinfo=timezone.utc),
    datetime(2021, 3, 17, tzinfo=timezone.utc),
    datetime(2021, 4, 28, tzinfo=timezone.utc),
    datetime(2021, 6, 16, tzinfo=timezone.utc),
    datetime(2021, 7, 28, tzinfo=timezone.utc),
    datetime(2021, 9, 22, tzinfo=timezone.utc),
    datetime(2021, 11, 3, tzinfo=timezone.utc),
    datetime(2021, 12, 15, tzinfo=timezone.utc),
    # --- 2022 (8 scheduled) ---
    datetime(2022, 1, 26, tzinfo=timezone.utc),
    datetime(2022, 3, 16, tzinfo=timezone.utc),
    datetime(2022, 5, 4, tzinfo=timezone.utc),
    datetime(2022, 6, 15, tzinfo=timezone.utc),
    datetime(2022, 7, 27, tzinfo=timezone.utc),
    datetime(2022, 9, 21, tzinfo=timezone.utc),
    datetime(2022, 11, 2, tzinfo=timezone.utc),
    datetime(2022, 12, 14, tzinfo=timezone.utc),
    # --- 2023 (8 scheduled) ---
    datetime(2023, 2, 1, tzinfo=timezone.utc),
    datetime(2023, 3, 22, tzinfo=timezone.utc),
    datetime(2023, 5, 3, tzinfo=timezone.utc),
    datetime(2023, 6, 14, tzinfo=timezone.utc),
    datetime(2023, 7, 26, tzinfo=timezone.utc),
    datetime(2023, 9, 20, tzinfo=timezone.utc),
    datetime(2023, 11, 1, tzinfo=timezone.utc),
    datetime(2023, 12, 13, tzinfo=timezone.utc),
    # --- 2024 (8 scheduled) ---
    datetime(2024, 1, 31, tzinfo=timezone.utc),
    datetime(2024, 3, 20, tzinfo=timezone.utc),
    datetime(2024, 5, 1, tzinfo=timezone.utc),
    datetime(2024, 6, 12, tzinfo=timezone.utc),
    datetime(2024, 7, 31, tzinfo=timezone.utc),
    datetime(2024, 9, 18, tzinfo=timezone.utc),
    datetime(2024, 11, 7, tzinfo=timezone.utc),
    datetime(2024, 12, 18, tzinfo=timezone.utc),
    # --- 2025 (8 scheduled) ---
    datetime(2025, 1, 29, tzinfo=timezone.utc),
    datetime(2025, 3, 19, tzinfo=timezone.utc),
    datetime(2025, 5, 7, tzinfo=timezone.utc),
    datetime(2025, 6, 18, tzinfo=timezone.utc),
    datetime(2025, 7, 30, tzinfo=timezone.utc),
    datetime(2025, 9, 17, tzinfo=timezone.utc),
    datetime(2025, 11, 5, tzinfo=timezone.utc),
    datetime(2025, 12, 17, tzinfo=timezone.utc),
    # --- 2026 (8 scheduled) — source: federalreserve.gov/monetarypolicy/fomccalendars.htm ---
    datetime(2026, 1, 28, tzinfo=timezone.utc),   # Jan 27-28
    datetime(2026, 3, 18, tzinfo=timezone.utc),   # Mar 17-18
    datetime(2026, 4, 29, tzinfo=timezone.utc),   # Apr 28-29
    datetime(2026, 6, 17, tzinfo=timezone.utc),   # Jun 16-17
    datetime(2026, 7, 29, tzinfo=timezone.utc),   # Jul 28-29
    datetime(2026, 9, 16, tzinfo=timezone.utc),   # Sep 15-16
    datetime(2026, 10, 28, tzinfo=timezone.utc),  # Oct 27-28
    datetime(2026, 12, 9, tzinfo=timezone.utc),   # Dec 8-9
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
