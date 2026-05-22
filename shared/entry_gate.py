"""Entry gating for event-driven blackouts.

EXP-3311 — NFP Entry Filter. Skips new entries on days *before* Non-Farm
Payrolls announcements to avoid payroll-vol exposure.

The list of NFP dates is read from a JSON blacklist file (default:
``configs/event_blacklist.json``).  The schedule is published by BLS at
https://www.bls.gov/schedule/news_release/empsit.htm and updated quarterly.

Usage:
    from shared.entry_gate import should_skip_entry_for_nfp
    skip, reason = should_skip_entry_for_nfp(today)
    if skip:
        logger.info("Entry gate: %s", reason)
        return  # don't scan
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_BLACKLIST_PATH = "configs/event_blacklist.json"


def load_nfp_dates(blacklist_path: str = DEFAULT_BLACKLIST_PATH) -> list[date]:
    """Load NFP dates from the JSON blacklist.

    Returns an empty list (with a warning) when the file is missing or
    malformed — the gate then defaults to permissive (no skip).
    """
    path = Path(blacklist_path)
    if not path.is_absolute():
        # Resolve relative to repo root (cwd of the scheduler)
        path = Path.cwd() / path

    if not path.exists():
        logger.warning("entry_gate: blacklist file not found at %s — gate disabled", path)
        return []

    try:
        with path.open("r") as fh:
            data = json.load(fh)
    except Exception as e:
        logger.warning("entry_gate: failed to parse %s: %s — gate disabled", path, e)
        return []

    raw = data.get("nfp_dates", [])
    parsed: list[date] = []
    for s in raw:
        try:
            parsed.append(datetime.strptime(s, "%Y-%m-%d").date())
        except (TypeError, ValueError):
            logger.warning("entry_gate: skipping invalid date %r in %s", s, path)
    return parsed


def should_skip_entry_for_nfp(
    today: Optional[date] = None,
    blacklist_path: str = DEFAULT_BLACKLIST_PATH,
    nfp_dates: Optional[Iterable[date]] = None,
) -> Tuple[bool, str]:
    """Return ``(skip, reason)`` indicating whether to gate new entries.

    The gate triggers when the *next calendar day* is an NFP release date.
    This matches the instruction-file semantics ("skip entries day-before-NFP").

    Args:
        today: Reference date (defaults to ``date.today()``).
        blacklist_path: Path to the JSON blacklist (used when ``nfp_dates``
            is not provided).
        nfp_dates: Optional pre-loaded iterable of NFP dates (for tests).

    Returns:
        ``(True, reason_string)`` when entries should be skipped; otherwise
        ``(False, "")``.
    """
    today = today or date.today()
    dates: Iterable[date] = (
        nfp_dates if nfp_dates is not None else load_nfp_dates(blacklist_path)
    )
    tomorrow = today + timedelta(days=1)
    if tomorrow in set(dates):
        return True, f"NFP entry filter: tomorrow ({tomorrow.isoformat()}) is an NFP release date"
    return False, ""
