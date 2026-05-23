"""compass/live_composite_stress.py — Live composite-stress score for the
   SPX regime-transition gate.

Replicates the EXP-3303b backtest's ``build_composite_stress`` formula
against TODAY's market data so the live orchestrator can decide whether
to gate SPX-correlated sleeves (EXP-1220, qqq_cs, xlf_cs, xli_cs) when
the VIX term-structure + VVIX + SKEW regime turns hostile.

Formula (must match EXP-3303b exactly — see ``build_composite_stress``
in ``compass/exp3303_regime_transition_dd.py``):

    term_spread       = VIX3M − VIX
    z63(x)            = (x − rolling63.mean) / rolling63.std(ddof=1)
    term_spread_z     = −z63(term_spread)      # invert: high z = stress
    vvix_z            =  z63(VVIX)
    skew_z            =  z63(SKEW)
    composite_stress  = (term_spread_z + vvix_z + skew_z) / √3

Inputs are fetched live from Yahoo Finance (``^VIX``, ``^VIX3M``,
``^VVIX``, ``^SKEW``). A 120-day lookback is requested so the trailing
63-day rolling window is fully populated for the latest observation.

Public API
----------
``get_current_composite_stress() -> Optional[float]``
    Today's composite-stress value (None if any feed is unavailable).

``should_gate_spx_streams(theta: float = 2.5) -> bool``
    True iff today's composite_stress crosses the gate threshold (i.e.
    SPX-correlated entries should be blocked).

Caching
-------
- One disk cache per UTC day at ``compass/cache/live_composite_stress.pkl``.
- Repeated calls within the same UTC day read the cache.
- Cache entries older than 1 day are refetched.

Rule Zero
---------
All inputs are real market data fetched from Yahoo. Rule Zero compliant - no generated
fallback — if Yahoo is unavailable the public API returns None and the
caller MUST fail-closed (do not trade through unknown regime state).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

LOG = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Constants — MUST match EXP-3303b exactly
# ───────────────────────────────────────────────────────────────────────────

ZSCORE_WINDOW: int = 63                        # 3-month trailing window
TICKERS: Tuple[str, ...] = ("^VIX", "^VIX3M", "^VVIX", "^SKEW")
LOOKBACK_DAYS: int = 120                       # enough cushion for a full window

# Default theta from EXP-3303b's gate sweep (best in-sample value).
DEFAULT_THETA: float = 2.5

_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH: Path = _ROOT / "compass" / "cache" / "live_composite_stress.pkl"


# ───────────────────────────────────────────────────────────────────────────
# Cache record
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _CacheRecord:
    """One-row snapshot persisted to disk per UTC trading day."""
    as_of: date                       # UTC date the snapshot was taken
    composite_stress: Optional[float]
    term_spread_z: Optional[float]
    vvix_z: Optional[float]
    skew_z: Optional[float]
    error: Optional[str] = None


# ───────────────────────────────────────────────────────────────────────────
# Internal: data fetch
# ───────────────────────────────────────────────────────────────────────────

def _fetch_yahoo_features(
    today: date, lookback_days: int = LOOKBACK_DAYS
) -> pd.DataFrame:
    """Download ``TICKERS`` Close series from Yahoo over ``today - lookback``
    through ``today``. Returns a DataFrame with columns
    ``[vix, vix3m, vvix, skew]`` indexed by date, ffilled, NaN-dropped.

    Raises ``RuntimeError`` if any series is empty after cleaning — we
    fail closed rather than synthesising a value.
    """
    import yfinance as yf

    start = today - pd.Timedelta(days=lookback_days)
    # End is exclusive in yfinance; +1 day so today's close is included.
    end = today + pd.Timedelta(days=1)

    LOG.info(
        "live_composite_stress: fetching %s from Yahoo %s..%s",
        list(TICKERS), start, end,
    )
    raw = yf.download(
        list(TICKERS),
        start=str(start),
        end=str(end),
        progress=False,
        auto_adjust=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError("Yahoo returned empty frame for regime tickers")
    if "Close" not in raw.columns.get_level_values(0):
        raise RuntimeError(f"Yahoo response missing 'Close' field: {raw.columns}")

    close = raw["Close"].rename(columns={
        "^VIX": "vix",
        "^VIX3M": "vix3m",
        "^VVIX": "vvix",
        "^SKEW": "skew",
    })

    missing = [c for c in ("vix", "vix3m", "vvix", "skew") if c not in close.columns]
    if missing:
        raise RuntimeError(f"Yahoo response missing series: {missing}")

    close = close.ffill().dropna()
    if close.empty:
        raise RuntimeError("regime feature panel is empty after ffill/dropna")
    return close


# ───────────────────────────────────────────────────────────────────────────
# Internal: composite stress (MUST match EXP-3303b build_composite_stress)
# ───────────────────────────────────────────────────────────────────────────

def _compute_composite_stress(features: pd.DataFrame) -> pd.DataFrame:
    """Apply the EXP-3303b z-score + composite-stress formula.

    Identical to ``compass.exp3303_regime_transition_dd.build_composite_stress``.
    Kept inline here so the live module has zero coupling to backtest
    code (which carries unrelated CLI / plotting imports).
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


# ───────────────────────────────────────────────────────────────────────────
# Internal: cache I/O
# ───────────────────────────────────────────────────────────────────────────

def _load_cache(today: date) -> Optional[_CacheRecord]:
    """Return the cached record if it was written for ``today`` (UTC),
    else None."""
    if not CACHE_PATH.exists():
        return None
    try:
        rec = pd.read_pickle(CACHE_PATH)
    except Exception as exc:                      # corrupted / partial write
        LOG.warning("live_composite_stress: cache unreadable (%s) — refetching", exc)
        return None
    if not isinstance(rec, _CacheRecord):
        return None
    if rec.as_of != today:
        return None
    return rec


def _save_cache(rec: _CacheRecord) -> None:
    """Persist one daily snapshot to disk. Best-effort — a write failure
    must not propagate to the caller; we just log and move on."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(rec, CACHE_PATH)
    except Exception as exc:
        LOG.warning("live_composite_stress: cache write failed (%s)", exc)


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────

def get_current_composite_stress(
    *,
    today: Optional[date] = None,
    force_refresh: bool = False,
) -> Optional[float]:
    """Today's composite stress score, or None if data is unavailable.

    Parameters
    ----------
    today: optional override for the "as-of" date (default: UTC today).
           Used by tests to deterministically pin the cache key.
    force_refresh: if True, ignore any cached value and refetch.

    Returns
    -------
    The latest composite_stress value (a float; can be any sign). None
    when the trailing 63-day window is incomplete or Yahoo is
    unavailable — callers MUST fail-closed in that case.
    """
    today = today or datetime.now(timezone.utc).date()

    if not force_refresh:
        cached = _load_cache(today)
        if cached is not None:
            if cached.error is not None:
                LOG.warning("live_composite_stress: cached error for %s: %s",
                            today, cached.error)
            return cached.composite_stress

    try:
        features = _fetch_yahoo_features(today)
        scored = _compute_composite_stress(features)
        latest = scored.iloc[-1]
        cs = latest.get("composite_stress")
        if cs is None or (isinstance(cs, float) and math.isnan(cs)):
            raise RuntimeError(
                "composite_stress is NaN — trailing 63-day window not yet filled"
            )
        rec = _CacheRecord(
            as_of=today,
            composite_stress=float(cs),
            term_spread_z=float(latest.get("term_spread_z")),
            vvix_z=float(latest.get("vvix_z")),
            skew_z=float(latest.get("skew_z")),
            error=None,
        )
        _save_cache(rec)
        LOG.info(
            "live_composite_stress: composite=%.3f (term_z=%.2f, vvix_z=%.2f, "
            "skew_z=%.2f) as-of %s",
            rec.composite_stress, rec.term_spread_z, rec.vvix_z, rec.skew_z, today,
        )
        return rec.composite_stress
    except Exception as exc:
        LOG.error("live_composite_stress: failed to compute (%s)", exc)
        # Cache the failure so subsequent calls within the same day
        # don't hammer Yahoo. Forced refresh skips this cache hit.
        _save_cache(_CacheRecord(
            as_of=today,
            composite_stress=None,
            term_spread_z=None, vvix_z=None, skew_z=None,
            error=f"{type(exc).__name__}: {exc}",
        ))
        return None


def should_gate_spx_streams(
    theta: float = DEFAULT_THETA,
    *,
    today: Optional[date] = None,
    force_refresh: bool = False,
) -> bool:
    """True iff the SPX regime gate should be ACTIVE today.

    The EXP-3303b gate triggers when composite_stress crosses ``theta``.
    In live mode we apply the same threshold to today's value (the
    backtest used ``composite_stress[t-1]`` to avoid look-ahead; in live
    there is no look-ahead since we evaluate at the start of the
    trading day against the prior close).

    Fail-closed: when composite_stress is unavailable (None) the gate
    BLOCKS — callers must treat the unknown regime as hostile. This is
    Rule Zero (no generated fallback).
    """
    cs = get_current_composite_stress(today=today, force_refresh=force_refresh)
    if cs is None:
        LOG.warning(
            "live_composite_stress: composite unavailable — gating SPX streams "
            "(fail-closed)"
        )
        return True
    gated = cs > theta
    LOG.info(
        "live_composite_stress: composite=%.3f theta=%.2f → gate=%s",
        cs, theta, gated,
    )
    return gated


__all__ = [
    "CACHE_PATH",
    "DEFAULT_THETA",
    "TICKERS",
    "ZSCORE_WINDOW",
    "get_current_composite_stress",
    "should_gate_spx_streams",
]
