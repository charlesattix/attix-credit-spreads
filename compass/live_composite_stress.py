"""Live composite-stress calculator for EXP-3303b (Polygon/UW data).

Computes the regime-stress composite that EXP-3303b uses to gate the SPX
streams. The formula is identical to
``compass/exp3303_regime_transition_dd.py::build_composite_stress`` so
the live signal matches the backtest exactly:

    term_spread = VIX3M - VIX
    z63(x)      = (x - rolling63.mean()) / rolling63.std(ddof=1)
    term_spread_z = -z63(term_spread)        # invert so high-z = stress
    vvix_z        =  z63(VVIX)
    skew_z        =  z63(SKEW)
    composite_stress = (term_spread_z + vvix_z + skew_z) / sqrt(3)

Data sources
------------
All four indices are fetched through ``shared.data_cache.DataCache``
which is Polygon-backed after the PR #34 migration. Yahoo-style tickers
(``^VIX``, ``^VIX3M``, ``^VVIX``, ``^SKEW``) are translated to the
Polygon index symbols (``I:VIX``, …) inside data_cache.

If the Polygon plan in use does not include CBOE indices for VVIX/SKEW,
``data_cache.get_history`` raises ``DataFetchError``; this module then
returns ``None`` (Rule Zero: fail closed, never synthesise).
``UNUSUAL_WHALES_API_KEY`` is reserved for a future UW fallback — see
``# TODO(uw-fallback)`` below.

Caching
-------
One pickle per UTC day at ``compass/cache/live_composite_stress.pkl``.
The cache is reused for the rest of the trading day; the next UTC day's
first call refetches.

Wiring
------
The intended consumer is the regime gate (``shared.regime_gate``) which
calls ``should_gate_spx_streams(theta=...)`` before sizing SPX streams.
"""
from __future__ import annotations

import logging
import math
import os
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from shared.data_cache import DataCache
from shared.exceptions import DataFetchError

logger = logging.getLogger(__name__)

# Formula constants (must mirror exp3303_regime_transition_dd.py)
ZSCORE_WINDOW = 63

# Index tickers (Yahoo-style — data_cache translates internally).
_VIX = "^VIX"
_VIX3M = "^VIX3M"
_VVIX = "^VVIX"
_SKEW = "^SKEW"

# On-disk cache
CACHE_PATH = Path(__file__).parent / "cache" / "live_composite_stress.pkl"
CACHE_MAX_AGE_SECONDS = 24 * 3600  # 1 day


# ---------------------------------------------------------------------------
# Disk cache helpers
# ---------------------------------------------------------------------------

@dataclass
class _CachedStress:
    fetched_at_utc: datetime
    frame: pd.DataFrame  # full composite-stress DataFrame


def _load_disk_cache() -> Optional[_CachedStress]:
    if not CACHE_PATH.exists():
        return None
    try:
        with CACHE_PATH.open("rb") as fh:
            obj = pickle.load(fh)
    except Exception as exc:
        logger.warning("Failed to load %s: %s", CACHE_PATH, exc)
        return None
    if not isinstance(obj, _CachedStress):
        return None
    age = (datetime.now(timezone.utc) - obj.fetched_at_utc).total_seconds()
    if age > CACHE_MAX_AGE_SECONDS:
        logger.info("Disk cache stale (%.0fs old); refetching", age)
        return None
    return obj


def _save_disk_cache(frame: pd.DataFrame) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    obj = _CachedStress(fetched_at_utc=datetime.now(timezone.utc), frame=frame)
    try:
        with CACHE_PATH.open("wb") as fh:
            pickle.dump(obj, fh)
    except Exception as exc:
        logger.warning("Failed to write %s: %s", CACHE_PATH, exc)


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def _fetch_features(cache: DataCache) -> Optional[pd.DataFrame]:
    """Pull the four indices via Polygon-backed DataCache.

    Returns a DataFrame indexed by Date with columns
    ``vix``, ``vix3m``, ``vvix``, ``skew`` — or ``None`` if any source
    is unavailable (Rule Zero: no fabricated fallback).
    """
    cols = {
        "vix":   _VIX,
        "vix3m": _VIX3M,
        "vvix":  _VVIX,
        "skew":  _SKEW,
    }
    series: dict[str, pd.Series] = {}
    for name, ticker in cols.items():
        try:
            df = cache.get_history(ticker, period="1y")
        except DataFetchError as exc:
            # TODO(uw-fallback): if exc is for VVIX/SKEW, fall back to
            # Unusual Whales using UNUSUAL_WHALES_API_KEY. No UW client
            # exists in the repo yet; that's a separate PR.
            logger.warning("composite_stress: %s unavailable (%s); failing closed", ticker, exc)
            return None
        if df is None or df.empty or "Close" not in df.columns:
            logger.warning("composite_stress: %s returned no Close column; failing closed", ticker)
            return None
        series[name] = df["Close"].rename(name)

    feats = pd.concat(series.values(), axis=1)
    feats = feats.sort_index().ffill().dropna()
    if len(feats) < ZSCORE_WINDOW:
        logger.warning(
            "composite_stress: only %d rows after align; need >= %d for z63",
            len(feats), ZSCORE_WINDOW,
        )
        return None
    return feats


# ---------------------------------------------------------------------------
# Formula (must match exp3303_regime_transition_dd.build_composite_stress)
# ---------------------------------------------------------------------------

def build_composite_stress(features: pd.DataFrame) -> pd.DataFrame:
    """Add term spread + trailing z-scores + composite stress score.

    Identical to ``compass/exp3303_regime_transition_dd.build_composite_stress``;
    duplicated here to avoid importing the backtest entry-point module
    (which would pull in unused fixtures). The two functions are pinned
    together by ``tests/test_live_composite_stress.py``.
    """
    f = features.copy()
    f["term_spread"] = f["vix3m"] - f["vix"]
    for col, neg in [("term_spread", True), ("vvix", False), ("skew", False)]:
        roll = f[col].rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW)
        z = (f[col] - roll.mean()) / roll.std(ddof=1)
        f[f"{col}_z"] = -z if neg else z
    f["composite_stress"] = (
        f["term_spread_z"] + f["vvix_z"] + f["skew_z"]
    ) / math.sqrt(3.0)
    return f


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Allow tests to inject a mock DataCache via ``_set_cache(...)``.
_data_cache_singleton: Optional[DataCache] = None


def _get_data_cache() -> DataCache:
    global _data_cache_singleton
    if _data_cache_singleton is None:
        _data_cache_singleton = DataCache()
    return _data_cache_singleton


def _set_cache_for_test(cache: Optional[DataCache]) -> None:
    """Test hook: swap in a mock DataCache (or None to reset)."""
    global _data_cache_singleton
    _data_cache_singleton = cache


def _compute_stress_frame(use_disk_cache: bool = True) -> Optional[pd.DataFrame]:
    """Return the full stress DataFrame, using disk cache when fresh."""
    if use_disk_cache:
        cached = _load_disk_cache()
        if cached is not None:
            return cached.frame

    feats = _fetch_features(_get_data_cache())
    if feats is None:
        return None
    frame = build_composite_stress(feats)
    _save_disk_cache(frame)
    return frame


def get_current_composite_stress() -> Optional[float]:
    """Return the most recent composite-stress reading, or ``None``.

    ``None`` means data was unavailable — callers MUST fail closed and
    must not substitute a fabricated value (Rule Zero).
    """
    frame = _compute_stress_frame()
    if frame is None or frame.empty:
        return None
    series = frame["composite_stress"].dropna()
    if series.empty:
        return None
    value = float(series.iloc[-1])
    if not math.isfinite(value):
        return None
    return value


def should_gate_spx_streams(theta: float = 2.5) -> bool:
    """Return ``True`` if composite stress exceeds ``theta`` — gate streams off.

    Matches the EXP-3303b gate logic: ``apply_regime_gate`` uses
    ``composite[t-1] > theta`` to decide whether to scale streams down.
    For the live path we use the latest available reading because there
    is no "t" yet — the gate is consulted before the day's signal fires.

    Fails closed (returns ``False`` — do NOT gate) when data is
    unavailable: the gate is conservative-off until features are ready,
    matching the backtest's warm-up handling.
    """
    value = get_current_composite_stress()
    if value is None:
        logger.info("composite_stress unavailable; not gating (warm-up behaviour)")
        return False
    return value > theta


# Convenience for the watchdog / status dashboard.
def status_summary() -> dict:
    """Return a small dict for log lines or health endpoints."""
    value = get_current_composite_stress()
    return {
        "composite_stress": value,
        "available":        value is not None,
        "cache_path":       str(CACHE_PATH),
        "cache_exists":     CACHE_PATH.exists(),
    }


# Manual entry-point (handy for ops): ``python -m compass.live_composite_stress``
if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    s = get_current_composite_stress()
    print(f"composite_stress = {s}")
