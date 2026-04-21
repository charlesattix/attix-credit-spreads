"""
shared/reset_filter.py — Centralized reset-date filtering for trade queries.

After an experiment is reset to a fresh Alpaca account, pre-reset trades must
be excluded from PnL calculations.  This module provides a single source of
truth for the reset cutoff date, derived from experiments/registry.json.

Usage::

    from shared.reset_filter import get_reset_date, add_reset_filter

    # Get the cutoff: trades with entry_date <= cutoff are pre-reset
    cutoff = get_reset_date("EXP-400")  # "2026-04-20" or None

    # Or add the filter directly to a SQL query:
    query = "SELECT * FROM trades WHERE status LIKE 'closed%'"
    query, params = add_reset_filter(query, [], "EXP-400")
    # → "SELECT ... AND (pre_reset = 0 OR pre_reset IS NULL)"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REGISTRY_PATH = _PROJECT_ROOT / "experiments" / "registry.json"

# Cache to avoid re-reading registry on every call within a process
_reset_date_cache: dict[str, Optional[str]] = {}


def get_reset_date(exp_id: str) -> Optional[str]:
    """
    Return the latest reset date for an experiment, or None if never reset.

    The reset date is the date the account was swapped (e.g. "2026-04-20").
    Trades with entry_date <= this date belong to the old account and should
    be excluded from post-reset PnL calculations.
    """
    if exp_id in _reset_date_cache:
        return _reset_date_cache[exp_id]

    result = None
    try:
        with open(_REGISTRY_PATH) as f:
            registry = json.load(f)
        exp = registry.get("experiments", {}).get(exp_id, {})
        history = exp.get("reset_history", [])
        if history:
            result = max(h["date"] for h in history)
    except Exception as exc:
        logger.debug("reset_filter: could not read registry for %s: %s", exp_id, exc)

    _reset_date_cache[exp_id] = result
    return result


def add_reset_filter(
    query: str,
    params: list,
    exp_id: str,
) -> Tuple[str, list]:
    """
    Append a reset-date filter to a SQL query.

    Prefers the pre_reset column (set by migration script) for speed.
    Falls back to entry_date filtering if the column hasn't been added yet.

    Args:
        query:  SQL query string (must already have a WHERE clause)
        params: list of query parameters (modified in place and returned)
        exp_id: experiment ID to look up reset date

    Returns:
        (modified_query, params) tuple
    """
    reset_date = get_reset_date(exp_id)
    if not reset_date:
        return query, params

    # Use pre_reset column (added by migration) — fast, no date comparison
    # Falls back gracefully: if column doesn't exist, SQLite will error and
    # the caller should catch and retry with entry_date filter
    query += " AND (pre_reset = 0 OR pre_reset IS NULL)"
    return query, params


def add_reset_filter_by_date(
    query: str,
    params: list,
    exp_id: str,
) -> Tuple[str, list]:
    """
    Append an entry_date-based reset filter (works without migration).

    Use this as a fallback when the pre_reset column may not exist.
    """
    reset_date = get_reset_date(exp_id)
    if not reset_date:
        return query, params

    query += " AND entry_date > ?"
    params.append(reset_date)
    return query, params


def get_post_reset_filter_sql(exp_id: str) -> Tuple[str, list]:
    """
    Return a standalone SQL fragment and params for filtering post-reset trades.

    Returns ("", []) if no reset, or (" AND entry_date > ?", [reset_date]).
    """
    reset_date = get_reset_date(exp_id)
    if not reset_date:
        return "", []
    return " AND entry_date > ?", [reset_date]
