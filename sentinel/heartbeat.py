"""
SENTINEL — Gate 22 producer helper.

Wraps SentinelDB.record_heartbeat() so scanners can emit a heartbeat in a
single line without dragging in the DB import path or wrapping every call
in try/except.  The helper NEVER raises — a heartbeat-write failure must
not break a scanner.

Usage::

    from sentinel.heartbeat import emit_heartbeat

    # After a confirmed-alive Alpaca call:
    acct = client.get_account()
    emit_heartbeat("EXP-503", notes="account ok")

    # At the end of a scan iteration:
    emit_heartbeat("EXP-503", notes="scan complete")
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def emit_heartbeat(
    scanner_id: str,
    *,
    status: str = "ok",
    notes: Optional[str] = None,
) -> None:
    """
    UPSERT a heartbeat for *scanner_id* into sentinel.scanner_heartbeats.

    Errors are caught and logged at DEBUG only; the call is fire-and-forget.
    """
    try:
        from sentinel.history import SentinelDB
        SentinelDB().record_heartbeat(scanner_id, status=status, notes=notes)
    except Exception:  # noqa: BLE001
        logger.debug("emit_heartbeat failed for %s", scanner_id, exc_info=True)
