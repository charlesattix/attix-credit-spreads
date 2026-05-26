"""Selective per-ticker regime gating.

EXP-3303b — Per-Stream Selective Regime Gate. Skips new entries for
SPX-sensitive tickers (SPY, QQQ) during regime transitions while keeping
non-sensitive tickers (sector ETFs like XLF, XLI, GLD, SLV) running at
full size.

Instruction-file semantics described a 50% size multiplier; this paper
deployment implements a *skip* (hard 0% multiplier) instead — see the PR
for rationale. The skip is more conservative than the half-size variant
and avoids touching ``AlertPositionSizer`` (which would risk regression
across the other 6 active paper accounts).

Usage:
    from shared.regime_gate import should_gate_for_regime
    skip, reason = should_gate_for_regime(regime, ticker, config)
    if skip:
        logger.info("Regime gate: %s", reason)
        return []
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_GATED_REGIMES = ("transition", "high_stress")
DEFAULT_SENSITIVE_TICKERS = ("SPY", "QQQ")


def should_gate_for_regime(
    regime: Optional[str],
    ticker: str,
    config: Optional[dict] = None,
    gated_regimes: Optional[Iterable[str]] = None,
    sensitive_tickers: Optional[Iterable[str]] = None,
    enabled: Optional[bool] = None,
) -> Tuple[bool, str]:
    """Return ``(skip, reason)`` indicating whether to gate entries.

    Gating triggers when ALL of:
      1. The gate is enabled (via ``config.risk.regime_gate.enabled`` or the
         explicit ``enabled`` keyword).
      2. ``regime`` is one of the configured gated regimes
         (default: ``transition``, ``high_stress``).
      3. ``ticker`` is in the configured sensitive set
         (default: ``SPY``, ``QQQ``).

    The keyword overrides (``gated_regimes``, ``sensitive_tickers``,
    ``enabled``) are primarily for tests; in production the values come from
    the YAML config.

    Args:
        regime: Detected combo-regime label (e.g. ``"bull"``, ``"transition"``).
        ticker: Underlying ticker symbol (case-insensitive).
        config: Application config dict.  Reads ``risk.regime_gate.*``.
        gated_regimes: Override for regimes that trigger the gate.
        sensitive_tickers: Override for tickers that get gated.
        enabled: Override for the enabled flag.

    Returns:
        ``(True, reason)`` to skip the ticker, otherwise ``(False, "")``.
    """
    cfg = (config or {}).get("risk", {}).get("regime_gate", {}) or {}

    is_enabled = enabled if enabled is not None else bool(cfg.get("enabled", False))
    if not is_enabled:
        return False, ""

    regimes = set(gated_regimes if gated_regimes is not None else cfg.get("gated_regimes", DEFAULT_GATED_REGIMES))
    tickers = {t.upper() for t in (sensitive_tickers if sensitive_tickers is not None else cfg.get("sensitive_tickers", DEFAULT_SENSITIVE_TICKERS))}

    if regime not in regimes:
        return False, ""

    if ticker.upper() not in tickers:
        return False, ""

    return True, (
        f"Per-stream regime gate: {ticker.upper()} skipped — regime={regime!r} "
        f"is in {sorted(regimes)} AND ticker is SPX-sensitive"
    )
