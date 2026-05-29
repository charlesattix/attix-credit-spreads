"""compass/live/vrp_stubs.py — reference stubs for not-yet-shipped VRP deps.

The VRP engine depends on PR-D (VIX ladder, cc4) and PR-I (per-stream realized
returns). Those are in flight; these stubs let the engine run end-to-end today
and in tests. Each is a thin, SAFE placeholder with a clear ``TODO`` marking the
seam the real owner replaces. They satisfy the protocols in ``vrp_contracts``.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Sequence

import pandas as pd

from compass.vix_ladder import VIXLadder

logger = logging.getLogger(__name__)


class LadderVixExposure:
    """Minimal :class:`VixExposureProvider` over the real EXP-2820 ``VIXLadder``.

    TODO(cc4/PR-D): replace with the hardened live trigger
    (``compass.live.vix_exposure``): last-known-fresh fallback + persisted state,
    consuming cc2's live VIX. This stub already applies the one non-negotiable
    safety rule cc4 specified (recon §5.3): a missing/NaN VIX must NOT imply full
    leverage — we HALT (return 0.0) rather than the library's permissive 1.0.
    """

    def __init__(self, vix_source: Callable[[], Optional[float]], ladder: Optional[VIXLadder] = None) -> None:
        self._vix_source = vix_source
        self._ladder = ladder or VIXLadder()

    def current_exposure_multiplier(self) -> float:
        try:
            vix = self._vix_source()
        except Exception as exc:  # noqa: BLE001 — fail safe, never crash sizing
            logger.warning("[vrp_stubs] vix_source failed: %s — halting new entries", exc)
            return 0.0
        if vix is None or not pd.notna(vix):
            logger.warning("[vrp_stubs] no live VIX — halting new entries (exposure 0.0)")
            return 0.0
        return float(self._ladder.exposure_at(float(vix)))


class StaticReturnsProvider:
    """Minimal :class:`ReturnsProvider` returning a fixed per-stream returns frame.

    TODO(PR-I): replace with a provider that reconstructs realized per-stream
    daily returns from ``trades.stream`` / a ``stream_equity_history`` table.

    Default (no seed) returns an EMPTY-rows frame carrying just the requested
    stream columns — which drives cc3's allocator into cold-start *prior* mode
    (the correct day-1 behavior for a ~3-day-old account). A seed frame can be
    supplied in tests to exercise the live/blend covariance paths.
    """

    def __init__(self, stream_columns: Sequence[str], seed: Optional[pd.DataFrame] = None) -> None:
        self._columns = list(stream_columns)
        self._seed = seed

    def stream_returns(self, lookback: int = 252) -> pd.DataFrame:
        if self._seed is not None and not self._seed.empty:
            df = self._seed.tail(int(lookback)).copy()
            return df
        return pd.DataFrame(columns=self._columns)
