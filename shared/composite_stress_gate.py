"""Composite-stress entry gate (EXP-3303b research formula).

A second, *coexisting* regime gate alongside ``shared.regime_gate``:

* ``shared.regime_gate.should_gate_for_regime`` — label-based, per-ticker
  (skips SPY/QQQ when the combo regime label is ``transition`` or
  ``high_stress``). Paper-deployed in PR #30.
* ``shared.composite_stress_gate.should_gate_for_composite_stress`` (here)
  — continuous score (term_spread_z + vvix_z + skew_z)/√3 measured live
  from VIX/VIX3M/VVIX/SKEW. Pinned to the EXP-3303 backtest formula.

Both gates are evaluated independently in ``main.scan_opportunities``.
Either gate firing skips the ticker; gating is conservative-OR.

Usage::

    from shared.composite_stress_gate import should_gate_for_composite_stress
    skip, reason = should_gate_for_composite_stress(ticker, config)
    if skip:
        logger.info("Composite-stress gate: %s", reason)
        return []

Config layout (read from ``config.risk.composite_stress_gate``)::

    risk:
      composite_stress_gate:
        enabled: false           # default OFF — opt-in
        theta: 2.5               # stress threshold; gate fires when >= theta
        sensitive_tickers: ["SPY", "QQQ"]   # which tickers this gate covers
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_THETA = 2.5
DEFAULT_SENSITIVE_TICKERS = ("SPY", "QQQ")


def should_gate_for_composite_stress(
    ticker: str,
    config: Optional[dict] = None,
    *,
    theta: Optional[float] = None,
    sensitive_tickers: Optional[Iterable[str]] = None,
    enabled: Optional[bool] = None,
    stress_value: Optional[float] = None,
) -> Tuple[bool, str]:
    """Return ``(skip, reason)`` for the composite-stress gate.

    Gating triggers when ALL of:
      1. The gate is enabled.
      2. ``ticker`` is in the configured sensitive set.
      3. The live composite-stress value is >= ``theta``.

    Rule Zero: when the composite-stress value is ``None`` (live inputs
    unavailable), the gate does NOT fire. Warm-up days and data outages
    fail open — the label-based ``regime_gate`` still runs and is the
    safety net.

    Args:
        ticker: Underlying ticker symbol (case-insensitive).
        config: App config; reads ``risk.composite_stress_gate.*``.
        theta: Override threshold; defaults to config or ``DEFAULT_THETA``.
        sensitive_tickers: Override sensitive set.
        enabled: Override enabled flag.
        stress_value: Override the live composite-stress value. Tests
            use this to avoid hitting the network; production passes
            ``None`` so the live calculator is consulted.

    Returns:
        ``(True, reason)`` to skip the ticker, otherwise ``(False, "")``.
    """
    cfg = (config or {}).get("risk", {}).get("composite_stress_gate", {}) or {}

    is_enabled = enabled if enabled is not None else bool(cfg.get("enabled", False))
    if not is_enabled:
        return False, ""

    tickers_raw = (
        sensitive_tickers
        if sensitive_tickers is not None
        else cfg.get("sensitive_tickers", DEFAULT_SENSITIVE_TICKERS)
    )
    tickers = {t.upper() for t in tickers_raw}
    if ticker.upper() not in tickers:
        return False, ""

    threshold = theta if theta is not None else float(cfg.get("theta", DEFAULT_THETA))

    if stress_value is None:
        from compass.live_composite_stress import get_current_composite_stress
        stress_value = get_current_composite_stress()

    if stress_value is None:
        # Rule Zero: data unavailable -> do not fire. Label-based gate
        # remains the safety net.
        return False, ""

    if stress_value < threshold:
        return False, ""

    return True, (
        f"Composite-stress gate: {ticker.upper()} skipped — "
        f"stress={stress_value:.3f} >= theta={threshold:.3f}"
    )
