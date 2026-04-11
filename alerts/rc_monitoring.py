"""
RC Monitoring — Real-time alert functions for the 5 root cause fixes.

Each function takes a live sqlite3.Connection and config values, queries
the DB for a specific risk condition, and returns a human-readable alert
string (or None / empty list when no issue is found).

These are intended to be called from the execution engine or reconciler
during normal operation so that regressions are caught immediately.

Alert functions
---------------
1. check_position_limit_warning   — RC#1: position slots nearly full
2. check_stuck_pending_open       — RC#1: pending_open older than threshold
3. check_zero_pnl_on_close        — RC#2: closed trade recorded with $0 PnL
4. check_zombie_records           — RC#4: synthetic-monitor-* records detected
5. check_expiration_concentration — RC#5: too many positions on one expiration
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RC#1 — Position limit warning
# ---------------------------------------------------------------------------


def check_position_limit_warning(
    conn: sqlite3.Connection,
    max_positions: int,
) -> Optional[str]:
    """Return an alert string when active positions reach max_positions - 1.

    "Active" means status IN ('open', 'pending_open', 'pending_close').

    Returns:
        Alert string if 1 slot remains, None otherwise.
    """
    if max_positions <= 0:
        return None

    row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status IN ('open','pending_open','pending_close')"
    ).fetchone()
    active_count = row[0] if row else 0

    if active_count >= max_positions - 1:
        return (
            f"⚠️ Position limit {active_count}/{max_positions} — 1 slot remaining"
        )
    return None


# ---------------------------------------------------------------------------
# RC#1 — Stuck pending_open detection
# ---------------------------------------------------------------------------


def check_stuck_pending_open(
    conn: sqlite3.Connection,
    threshold_minutes: int = 60,
) -> List[str]:
    """Return alert strings for pending_open trades older than threshold_minutes.

    Returns:
        List of alert strings (one per stuck trade), empty list if none.
    """
    rows = conn.execute(
        "SELECT id, ticker, created_at FROM trades "
        "WHERE status = 'pending_open' "
        "AND created_at < datetime('now', ?)",
        (f"-{threshold_minutes} minutes",),
    ).fetchall()

    alerts = []
    now = datetime.now(timezone.utc)
    for row in rows:
        trade_id, ticker, created_at_str = row[0], row[1], row[2]
        try:
            # SQLite stores datetimes without timezone; treat as UTC
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_min = int((now - created_at).total_seconds() / 60)
        except (ValueError, AttributeError):
            age_min = threshold_minutes  # conservative fallback

        alerts.append(
            f"🚨 STUCK ORDER: {trade_id} ({ticker}) pending_open for {age_min} min — investigate"
        )
    return alerts


# ---------------------------------------------------------------------------
# RC#2 — Zero PnL on close
# ---------------------------------------------------------------------------


def check_zero_pnl_on_close(
    conn: sqlite3.Connection,
    trade_id: str,
    credit: float,
    pnl: float,
) -> Optional[str]:
    """Return an alert string when a closing trade records $0 PnL despite non-zero credit.

    Intended to be called immediately after a trade is marked closed.

    Args:
        conn:     Active DB connection (unused but kept for interface consistency).
        trade_id: ID of the trade that was just closed.
        credit:   Original credit received (per spread, not total).
        pnl:      PnL recorded on close.

    Returns:
        Alert string if pnl == 0 and credit > 0, None otherwise.
    """
    if pnl == 0 and credit is not None and credit > 0:
        return (
            f"⚠️ ZERO PnL on close: {trade_id} (credit={credit:.2f}) — possible RC#2 regression"
        )
    return None


# ---------------------------------------------------------------------------
# RC#4 — Zombie record detection
# ---------------------------------------------------------------------------


def check_zombie_records(
    conn: sqlite3.Connection,
) -> List[str]:
    """Return alert strings for any open synthetic-monitor-* (zombie) records.

    A zombie record is a trade whose ID starts with 'synthetic-monitor-' and
    whose status is still 'open'. Their existence indicates RC#4 has regressed.

    Returns:
        List of alert strings (one per zombie), empty list if none.
    """
    rows = conn.execute(
        "SELECT id, ticker FROM trades "
        "WHERE id LIKE 'synthetic-monitor-%' AND status = 'open'"
    ).fetchall()

    return [
        f"🚨 ZOMBIE DETECTED: synthetic record {row[0]} ({row[1]}) — RC#4 regression?"
        for row in rows
    ]


# ---------------------------------------------------------------------------
# RC#5 — Expiration concentration
# ---------------------------------------------------------------------------


def check_expiration_concentration(
    conn: sqlite3.Connection,
    max_same_expiration: int,
) -> List[str]:
    """Return alert strings when any expiration date hits max_same_expiration.

    Counts trades with status IN ('open', 'pending_open').

    Returns:
        List of alert strings (one per over-concentrated expiration), empty if none.
    """
    if max_same_expiration <= 0:
        return []

    rows = conn.execute(
        "SELECT expiration, COUNT(*) as cnt FROM trades "
        "WHERE status IN ('open','pending_open') "
        "GROUP BY expiration "
        "HAVING cnt >= ?",
        (max_same_expiration,),
    ).fetchall()

    return [
        f"⚠️ Expiration concentration: {row[0]} has {row[1]}/{max_same_expiration} positions"
        for row in rows
    ]
