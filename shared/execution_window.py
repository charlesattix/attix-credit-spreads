"""Execution-window gating.

EXP-3309 — Pre-Close Execution Window. Restricts the scanner to a specific
intraday window (default 15:30–16:00 ET) so that all new entries land in
the pre-close liquidity surge, capturing tighter bid-ask spreads.

Usage:
    from shared.execution_window import should_skip_for_window
    skip, reason = should_skip_for_window(window="15:30-16:00")
    if skip:
        logger.info("Execution window gate: %s", reason)
        return
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Optional, Tuple

try:
    from zoneinfo import ZoneInfo  # Python 3.9+ standard library
except ImportError:  # pragma: no cover - py<3.9 environments
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_WINDOW = "15:30-16:00"
DEFAULT_TZ = "America/New_York"


def parse_window(window: str) -> Tuple[time, time]:
    """Parse a ``"HH:MM-HH:MM"`` window string into a ``(start, end)`` tuple.

    Raises ``ValueError`` on malformed input.
    """
    try:
        start_s, end_s = window.split("-")
        start_h, start_m = (int(x) for x in start_s.strip().split(":"))
        end_h, end_m = (int(x) for x in end_s.strip().split(":"))
        return time(start_h, start_m), time(end_h, end_m)
    except Exception as e:  # noqa: BLE001 - re-raise with context
        raise ValueError(f"Invalid execution window {window!r} (expected HH:MM-HH:MM): {e}") from e


def is_in_window(
    now: Optional[datetime] = None,
    window: str = DEFAULT_WINDOW,
    tz: str = DEFAULT_TZ,
) -> bool:
    """Return ``True`` when ``now`` (default: current time in ``tz``) falls
    inside the ``window`` ``HH:MM-HH:MM``.

    The window is half-open ``[start, end)`` — i.e. 16:00 is treated as
    *outside* a "15:30-16:00" window, which matches market-close semantics.
    """
    start, end = parse_window(window)
    if now is None:
        if ZoneInfo is not None:
            now = datetime.now(ZoneInfo(tz))
        else:
            now = datetime.now()  # naive fallback — tests should always pass `now`
    current = now.time()
    return start <= current < end


def should_skip_for_window(
    now: Optional[datetime] = None,
    window: str = DEFAULT_WINDOW,
    tz: str = DEFAULT_TZ,
) -> Tuple[bool, str]:
    """Return ``(skip, reason)`` indicating whether to gate the scan.

    Skips when the current time is *outside* the configured window.
    """
    if is_in_window(now=now, window=window, tz=tz):
        return False, ""
    if now is None:
        if ZoneInfo is not None:
            now = datetime.now(ZoneInfo(tz))
        else:
            now = datetime.now()
    return True, (
        f"Pre-close execution window: now ({now.strftime('%H:%M %Z') or now.strftime('%H:%M')}) "
        f"outside {window} {tz}"
    )
