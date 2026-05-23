"""Live composite-stress calculator (EXP-3303b research formula).

Computes a continuous market-stress score from VIX term structure
(``VIX3M − VIX``), VVIX, and SKEW, matching the formula validated in the
EXP-3303 regime-transition drawdown backtest:

    composite_stress(t) = (term_spread_z + vvix_z + skew_z) / sqrt(3)

where each ``*_z`` is a rolling z-score over the most recent
``_ZSCORE_WINDOW`` (63) trading days, and ``term_spread_z`` carries an
inverted sign (a *contracting* term structure — VIX3M falling toward VIX
— is the stress signal, so we negate the spread's z-score).

Rule Zero: when any of the four daily series (VIX, VIX3M, VVIX, SKEW) is
unavailable, this module returns ``None`` rather than fabricating a
fallback value. Callers must fail closed (treat ``None`` as "do not
gate").

Public API
----------
    get_current_composite_stress() -> Optional[float]

Cache
-----
A daily on-disk pickle at ``compass/cache/live_composite_stress.pkl``
holds the most recently computed value so the live trading path does not
re-fetch four index series every scan. The cache is keyed by the UTC
date the value was computed on and is silently regenerated on date
rollover.
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

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Rolling window for z-score calculation. Pinned to the value used in the
# EXP-3303 backtest so live and historical signals are directly comparable.
_ZSCORE_WINDOW = 63

# How many trading days of history to pull when computing the latest
# composite. Must exceed _ZSCORE_WINDOW with enough buffer for missing
# days; ``2y`` (≈504 trading days) is plenty.
_FETCH_PERIOD = "1y"

# Cache file location (relative to repo root).
_CACHE_DIR = Path(__file__).resolve().parent / "cache"
_CACHE_FILE = _CACHE_DIR / "live_composite_stress.pkl"

# Tickers required to compute composite stress. Yahoo-style — DataCache
# translates these to Polygon ``I:*`` indices.
_REQUIRED_TICKERS = ("^VIX", "^VIX3M", "^VVIX", "^SKEW")

# Test hook: allow tests to inject a DataCache stand-in without monkey-
# patching the module-level import.
_data_cache_override = None


@dataclass(frozen=True)
class _CacheEntry:
    """One day's composite-stress value, persisted to disk."""

    date: str  # ISO date the value was computed for (UTC).
    value: Optional[float]


def _set_cache_for_test(cache) -> None:
    """Inject a DataCache stand-in. Tests only; production passes ``None``."""
    global _data_cache_override
    _data_cache_override = cache


def _get_data_cache():
    """Resolve the DataCache instance to use.

    Honors the test override; otherwise constructs a fresh DataCache.
    """
    if _data_cache_override is not None:
        return _data_cache_override
    from shared.data_cache import DataCache
    return DataCache()


def build_composite_stress(features: pd.DataFrame) -> pd.DataFrame:
    """Compute composite stress series from a daily-bar feature frame.

    Args:
        features: DataFrame indexed by date with columns
            ``vix``, ``vix3m``, ``vvix``, ``skew`` (lowercase close prices).

    Returns:
        Copy of ``features`` with added columns ``term_spread``,
        ``term_spread_z``, ``vvix_z``, ``skew_z``, ``composite_stress``.
        Rows lacking enough history (< _ZSCORE_WINDOW prior obs) will
        have NaN in the z-score and composite columns.
    """
    f = features.copy()
    f["term_spread"] = f["vix3m"] - f["vix"]

    for col, invert_sign in (("term_spread", True), ("vvix", False), ("skew", False)):
        roll = f[col].rolling(_ZSCORE_WINDOW, min_periods=_ZSCORE_WINDOW)
        z = (f[col] - roll.mean()) / roll.std(ddof=1)
        f[f"{col}_z"] = -z if invert_sign else z

    f["composite_stress"] = (
        f["term_spread_z"] + f["vvix_z"] + f["skew_z"]
    ) / math.sqrt(3.0)

    return f


def _read_cache() -> Optional[_CacheEntry]:
    """Return the disk-cached entry if its date matches today (UTC)."""
    try:
        if not _CACHE_FILE.exists():
            return None
        with open(_CACHE_FILE, "rb") as fh:
            entry: _CacheEntry = pickle.load(fh)
        if not isinstance(entry, _CacheEntry):
            return None
        today = datetime.now(timezone.utc).date().isoformat()
        if entry.date != today:
            return None
        return entry
    except Exception as exc:
        logger.warning("Composite-stress cache read failed: %s", exc)
        return None


def _write_cache(value: Optional[float]) -> None:
    """Persist today's composite-stress value to disk."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).date().isoformat()
        entry = _CacheEntry(date=today, value=value)
        tmp = _CACHE_FILE.with_suffix(".pkl.tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(entry, fh)
        os.replace(tmp, _CACHE_FILE)
    except Exception as exc:
        logger.warning("Composite-stress cache write failed: %s", exc)


def _fetch_series(ticker: str, cache) -> Optional[pd.Series]:
    """Fetch a single daily-close series from the DataCache.

    Returns the Close column as a pandas Series, or ``None`` on failure /
    empty result.
    """
    try:
        df = cache.get_history(ticker, period=_FETCH_PERIOD)
    except Exception as exc:
        logger.warning("Composite-stress: %s fetch failed: %s", ticker, exc)
        return None
    if df is None or df.empty or "Close" not in df.columns:
        logger.warning("Composite-stress: %s returned no usable data", ticker)
        return None
    return df["Close"].copy()


def _compute_latest() -> Optional[float]:
    """Compute the most recent composite-stress value from live data.

    Returns:
        The latest non-NaN composite_stress value, or ``None`` if any
        input series is missing or the rolling window has not warmed up.
    """
    cache = _get_data_cache()

    series_by_col = {}
    for tk in _REQUIRED_TICKERS:
        s = _fetch_series(tk, cache)
        if s is None:
            return None  # Rule Zero: fail closed.
        # Map ticker → feature column name.
        col = {"^VIX": "vix", "^VIX3M": "vix3m", "^VVIX": "vvix", "^SKEW": "skew"}[tk]
        series_by_col[col] = s

    # Align all four series on their shared dates (inner join).
    features = pd.concat(series_by_col.values(), axis=1, join="inner")
    features.columns = list(series_by_col.keys())
    features = features.dropna()

    if len(features) < _ZSCORE_WINDOW + 1:
        logger.warning(
            "Composite-stress: only %d aligned bars, need >= %d for rolling window",
            len(features), _ZSCORE_WINDOW + 1,
        )
        return None

    enriched = build_composite_stress(features)
    latest = enriched["composite_stress"].dropna()
    if latest.empty:
        return None
    value = float(latest.iloc[-1])
    if not np.isfinite(value):
        return None
    return value


def get_current_composite_stress() -> Optional[float]:
    """Return today's composite-stress value, or ``None`` if unavailable.

    Result is memoized to disk for one UTC day. The cache is silently
    regenerated on date rollover or when reading fails.
    """
    cached = _read_cache()
    if cached is not None:
        return cached.value

    value = _compute_latest()
    _write_cache(value)
    return value
