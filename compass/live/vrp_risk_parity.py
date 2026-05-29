"""compass/live/vrp_risk_parity.py — Live Ledoit-Wolf risk-parity sizing (PR-C).

Live allocator for the EXP-V8A 8-stream Variance-Risk-Premium portfolio. Turns a
matrix of per-stream daily returns into equal-risk-contribution (ERC) weights via
a Ledoit-Wolf shrunk covariance, then (optionally) scales the whole portfolio to a
12% annualized vol target. Cold-start aware: when too little live history exists,
it blends the live covariance with a backtest prior so V8A can size sanely from
day one.

This module is PURE + ADDITIVE:
  * No network, no DB, no order placement, no global state.
  * Does NOT import the heavy research pipelines. The Ledoit-Wolf estimator and the
    ERC fixed-point solver are PORTED verbatim from
    ``compass/exp2360_robust_cov.py`` (importing that module would pull in
    ``exp2080``/``exp2160``/``IronVault`` side-effects — unsafe in the live worker).
    The math is identical, so live weights match the research/backtest weights.
  * Touches no other experiment's sizing path.

═══════════════════════════════════════════════════════════════════════════════
INTERFACE CONTRACT  (stable surface for cc1 / PR-B strategy engine)
═══════════════════════════════════════════════════════════════════════════════

    compute_weights(
        returns_df: pd.DataFrame,          # index=date, columns=stream_id, daily returns
        vol_target: float = 0.12,
        *,
        scaled: bool = False,              # False → ERC weights (Σ=1); True → vol-scaled gross
        min_live_days: int = 60,
        prior_cov: np.ndarray | None = None,
    ) -> dict[str, float]
        # Primary entry point. Keys are returns_df's columns (stream ids).
        # Default (scaled=False): non-negative ERC weights summing to 1.0 (the allocation).
        # scaled=True: weights × vol_scale — per-equity gross exposure fractions
        #   (multiply by account equity to get per-stream capital). Sum ≈ gross exposure.
        # Robust to NaN / missing / degenerate input — never raises on data quality.

    scale_to_vol_target(
        weights: dict[str, float],
        recent_returns: pd.DataFrame | pd.Series,
        vol_target: float = 0.12,
    ) -> dict[str, float]
        # Scale `weights` so the weighted portfolio's annualized realized vol ≈ vol_target.
        # scale = clip(vol_target / realized_vol, MIN_SCALE, MAX_SCALE). Returns scaled dict.

    ledoit_wolf_covariance(returns: np.ndarray) -> np.ndarray
    risk_parity_weights(cov: np.ndarray, n_iter: int = 500, tol: float = 1e-10) -> np.ndarray
    load_prior_covariance(stream_order, path=None) -> np.ndarray | None
    cold_start_covariance(returns_df, stream_order=None, min_live_days=60, prior_cov=None)
        -> tuple[np.ndarray, str, float]      # (cov, source∈{"live","blend","prior"}, lambda)

Recommended cadence: recompute weekly (covariance is stable day-to-day); cache the
result. The 12% vol target is applied FORWARD via scaled=True / scale_to_vol_target —
multiply the returned exposure fractions by account equity to get per-stream capital,
then hand to compass.dollar_notional_sizer.DollarNotionalSizer for contract counts.

NOTE on leverage: scale is hard-capped at MAX_SCALE=3.0 (NOT the research backtest's
20×) to reconcile with DollarNotionalSizer.max_leverage and live portfolio circuit
breakers. See docs/V8A_VRP_RECON_RISK_PARITY.md §5.
"""

from __future__ import annotations

import json
import logging
import math
import warnings
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
TRADING_DAYS = 252
DEFAULT_VOL_TARGET = 0.12

#: Live history (usable days) at/above which the live covariance is fully trusted.
#: Below this we blend toward the backtest prior; at 0 days we use the prior alone.
MIN_LIVE_DAYS = 60

#: Hard scale (leverage) clamp on the vol-target multiplier. Deliberately 3.0, not
#: the research backtest's 20× — reconciled with DollarNotionalSizer.max_leverage.
MAX_SCALE = 3.0
MIN_SCALE = 0.1

#: Canonical 8-stream order (matches compass/exp2850 build_v8a_cube). Used to align a
#: stored prior covariance with caller-supplied columns.
VRP_STREAMS: Tuple[str, ...] = (
    "exp1220", "v5_hedge", "gld_cal", "slv_cal",
    "cross_vol", "xlf_cs", "xli_cs", "qqq_cs",
)

#: Optional serialized backtest prior: {"streams": [...], "cov": [[...]]}.
#: Dropped in by PR-0/PR-E once the exp2850 cube reproduces; absent today, in which
#: case load_prior_covariance() returns None and the cold-start falls back to a
#: diagonal (inverse-variance / equal-risk) prior — a safe default.
PRIOR_COV_PATH = Path(__file__).resolve().parent / "data" / "vrp_prior_cov.json"

#: Fallback per-stream daily variance for the diagonal prior when neither a stored
#: prior nor live data is available (≈ 1.6%/day ⇒ ~25%/yr, a generic options-stream vol).
_DEFAULT_DAILY_VAR = 0.016 ** 2


# ═══════════════════════════════════════════════════════════════════════════
# Pure covariance + ERC math (ported from compass/exp2360_robust_cov.py)
# ═══════════════════════════════════════════════════════════════════════════

def ledoit_wolf_covariance(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrunk covariance of a (T, N) return matrix.

    Identical to ``compass.exp2360_robust_cov.cov_ledoit_wolf`` —
    ``sklearn.covariance.LedoitWolf().fit(R).covariance_`` with sklearn's
    fit-time warnings suppressed. Requires T ≥ 2 and a clean (finite) matrix.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return LedoitWolf().fit(returns).covariance_


def risk_parity_weights(cov: np.ndarray, n_iter: int = 500,
                        tol: float = 1e-10) -> np.ndarray:
    """Equal-risk-contribution (ERC) weights via the Chaves-Hsu-Li-Shakernia
    (2011) fixed-point iteration. Ported verbatim from
    ``compass.exp2360_robust_cov.risk_parity_weights``.

    Finds w ≥ 0, Σw = 1 such that every asset contributes the same share of
    portfolio variance: ``w_i · (Σ w)_i`` is constant across i. PSD-protected and
    scale-invariant (critical for the tiny absolute entries of a daily-return
    covariance). For a diagonal Σ the closed form is ``w_i ∝ 1/σ_i`` (inverse vol).
    """
    cov = np.asarray(cov, dtype=float)
    n = cov.shape[0]
    if n == 0:
        return np.array([], dtype=float)
    if n == 1:
        return np.array([1.0])

    # Symmetrize and ensure numerically PSD.
    cov = (cov + cov.T) / 2.0
    eig_min = float(np.linalg.eigvalsh(cov).min())
    if eig_min < 1e-14:
        cov = cov + np.eye(n) * (1e-14 - eig_min + 1e-14)

    w = np.ones(n) / n
    for _ in range(n_iter):
        mrc = cov @ w                       # marginal risk contribution
        rc = w * mrc                        # actual risk contribution
        target = rc.mean()
        if target <= 1e-30:
            break
        scale = np.sqrt(target / np.maximum(rc, 1e-30))
        w_new = w * scale
        w_new = np.maximum(w_new, 1e-10)
        w_new = w_new / w_new.sum()
        if np.max(np.abs(w_new - w)) < tol:
            w = w_new
            break
        w = w_new
    return w


# ═══════════════════════════════════════════════════════════════════════════
# Input hygiene
# ═══════════════════════════════════════════════════════════════════════════

def _clean_returns(returns_df: pd.DataFrame) -> pd.DataFrame:
    """Return a sorted, de-duplicated, finite, numeric copy of returns_df.

    - coerces to numeric, ±inf → NaN
    - sorts by index, drops duplicate index labels (keep last)
    - drops rows that are entirely NaN (no stream traded that day)
    - leaves per-cell NaN in place (callers fill 0.0 just before the cov fit)
    """
    if returns_df is None or returns_df.empty:
        return pd.DataFrame()
    df = returns_df.apply(pd.to_numeric, errors="coerce")
    df = df.replace([np.inf, -np.inf], np.nan)
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna(how="all")
    return df


def _usable_days(clean_df: pd.DataFrame) -> int:
    """Rows with at least one finite observation across streams."""
    if clean_df.empty:
        return 0
    return int(clean_df.notna().any(axis=1).sum())


# ═══════════════════════════════════════════════════════════════════════════
# Cold-start covariance resolution
# ═══════════════════════════════════════════════════════════════════════════

def load_prior_covariance(
    stream_order: Sequence[str],
    path: Optional[Union[str, Path]] = None,
) -> Optional[np.ndarray]:
    """Load a serialized backtest prior covariance aligned to ``stream_order``.

    Reads ``{"streams": [...], "cov": [[...]]}`` from PRIOR_COV_PATH (or ``path``),
    then reorders/sub-selects to match ``stream_order``. Any requested stream absent
    from the stored prior gets a diagonal default-variance entry (uncorrelated). If
    the file is missing or unreadable, returns ``None`` (caller builds a fallback).
    """
    p = Path(path) if path is not None else PRIOR_COV_PATH
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
        prior_streams = list(blob["streams"])
        prior_cov = np.asarray(blob["cov"], dtype=float)
    except Exception as exc:  # malformed file — fall back rather than crash live
        logger.warning("[vrp_risk_parity] prior cov unreadable at %s: %s", p, exc)
        return None

    idx = {s: i for i, s in enumerate(prior_streams)}
    n = len(stream_order)
    out = np.eye(n) * _DEFAULT_DAILY_VAR
    for a, sa in enumerate(stream_order):
        ia = idx.get(sa)
        if ia is None:
            continue
        for b, sb in enumerate(stream_order):
            ib = idx.get(sb)
            if ib is None:
                continue
            out[a, b] = prior_cov[ia, ib]
    return out


def _fallback_prior(clean_df: pd.DataFrame, stream_order: Sequence[str]) -> np.ndarray:
    """Diagonal prior from per-stream sample variance (uncorrelated assumption).

    Yields inverse-variance (≈ equal-risk) ERC weights — a safe, neutral cold-start
    when no stored backtest prior exists. Streams with no/zero live variance get
    _DEFAULT_DAILY_VAR so they still receive a finite (small) allocation.
    """
    n = len(stream_order)
    var = np.full(n, _DEFAULT_DAILY_VAR, dtype=float)
    if not clean_df.empty:
        for i, s in enumerate(stream_order):
            if s in clean_df.columns:
                col = clean_df[s].dropna()
                if len(col) >= 2:
                    v = float(col.var(ddof=1))
                    if np.isfinite(v) and v > 1e-18:
                        var[i] = v
    return np.diag(var)


def cold_start_covariance(
    returns_df: pd.DataFrame,
    stream_order: Optional[Sequence[str]] = None,
    min_live_days: int = MIN_LIVE_DAYS,
    prior_cov: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, str, float]:
    """Resolve the covariance to use, blending live data with a backtest prior.

    Returns ``(cov, source, lambda_)`` where ``source`` is one of ``"live"``,
    ``"blend"`` or ``"prior"`` and ``lambda_`` ∈ [0,1] is the live weight
    (``Σ = λ·Σ_live + (1−λ)·Σ_prior``, ``λ = clip(usable_days/min_live_days, 0, 1)``).

      * usable_days ≥ min_live_days → pure live covariance (λ=1).
      * 0 usable days (or <2 rows)  → pure prior (λ=0).
      * in between                  → blend.
    """
    clean = _clean_returns(returns_df)
    cols = list(returns_df.columns) if returns_df is not None else []
    if stream_order is None:
        stream_order = cols
    n = len(stream_order)
    if n == 0:
        return np.zeros((0, 0)), "prior", 0.0

    # Prior covariance (explicit arg > stored file > diagonal fallback).
    if prior_cov is not None:
        prior = np.asarray(prior_cov, dtype=float)
    else:
        prior = load_prior_covariance(stream_order)
        if prior is None:
            prior = _fallback_prior(clean, stream_order)

    days = _usable_days(clean)
    if days < 2:
        return prior, "prior", 0.0

    # Live covariance over the aligned, 0-filled matrix (no-trade day = 0 return).
    mat = clean.reindex(columns=stream_order).fillna(0.0).to_numpy(dtype=float)
    cov_live = ledoit_wolf_covariance(mat)

    lam = float(np.clip(days / max(min_live_days, 1), 0.0, 1.0))
    if lam >= 1.0:
        return cov_live, "live", 1.0
    cov_blend = lam * cov_live + (1.0 - lam) * prior
    return cov_blend, "blend", lam


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def compute_weights(
    returns_df: pd.DataFrame,
    vol_target: float = DEFAULT_VOL_TARGET,
    *,
    scaled: bool = False,
    min_live_days: int = MIN_LIVE_DAYS,
    prior_cov: Optional[np.ndarray] = None,
) -> Dict[str, float]:
    """Ledoit-Wolf risk-parity weights for the VRP streams (see module contract).

    Default returns ERC weights summing to 1.0. With ``scaled=True`` returns the
    vol-target-scaled gross exposure fractions. Cold-start aware and robust to
    NaN/missing/degenerate input.
    """
    if vol_target <= 0:
        raise ValueError(f"vol_target must be positive, got {vol_target}")

    if returns_df is None or len(getattr(returns_df, "columns", [])) == 0:
        return {}
    stream_order = list(returns_df.columns)
    n = len(stream_order)
    if n == 1:
        result = {stream_order[0]: 1.0}
        return scale_to_vol_target(result, returns_df, vol_target) if scaled else result

    cov, source, lam = cold_start_covariance(
        returns_df, stream_order, min_live_days=min_live_days, prior_cov=prior_cov
    )
    w = risk_parity_weights(cov)
    # Guard against any non-finite leak before normalizing.
    if w.size != n or not np.all(np.isfinite(w)) or w.sum() <= 0:
        w = np.ones(n) / n
    else:
        w = w / w.sum()
    weights = {stream_order[i]: float(w[i]) for i in range(n)}
    logger.debug("[vrp_risk_parity] weights source=%s lambda=%.3f n=%d", source, lam, n)

    return scale_to_vol_target(weights, returns_df, vol_target) if scaled else weights


def scale_to_vol_target(
    weights: Dict[str, float],
    recent_returns: Union[pd.DataFrame, pd.Series],
    vol_target: float = DEFAULT_VOL_TARGET,
) -> Dict[str, float]:
    """Scale ``weights`` so the weighted portfolio targets ``vol_target`` annualized vol.

    ``scale = clip(vol_target / realized_annual_vol, MIN_SCALE, MAX_SCALE)``. If the
    realized vol can't be measured (≤1 row, all-zero, or degenerate), scale defaults
    to 1.0. Returns ``{stream_id: weight × scale}`` (gross exposure fractions).
    """
    if not weights:
        return {}
    if isinstance(recent_returns, pd.Series):
        recent_returns = recent_returns.to_frame()

    clean = _clean_returns(recent_returns)
    scale = 1.0
    if not clean.empty and len(clean) >= 2:
        cols = list(weights.keys())
        mat = clean.reindex(columns=cols).fillna(0.0).to_numpy(dtype=float)
        w_vec = np.array([weights[c] for c in cols], dtype=float)
        port = mat @ w_vec
        sd = float(np.std(port, ddof=1))
        realized_vol = sd * math.sqrt(TRADING_DAYS)
        if realized_vol > 1e-10:
            scale = float(np.clip(vol_target / realized_vol, MIN_SCALE, MAX_SCALE))

    return {k: float(v * scale) for k, v in weights.items()}
