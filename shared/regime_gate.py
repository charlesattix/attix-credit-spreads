"""Regime gating for the live trading pipeline.

Two independent gates coexist:

1. **Per-ticker selective gate** (``should_gate_for_regime``) — EXP-3303b
   skips SPX-sensitive tickers during regime transitions.

2. **Composite-stress gate** (``RegimeGate``) — wires the live composite-
   stress signal into the SPX stream sizer.  Callers ask::

       gate = RegimeGate.from_env()
       if gate.should_gate_spx_streams():
           # scale down
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

# =====================================================================
# 1. Per-ticker selective regime gate (used by main.py scan loop)
# =====================================================================

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
      2. ``regime`` is one of the configured gated regimes.
      3. ``ticker`` is in the configured sensitive set.
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


# =====================================================================
# 2. Composite-stress regime gate (used by sizer)
# =====================================================================

DEFAULT_THETA = 2.5


def _theta_from_env(default: float = DEFAULT_THETA) -> float:
    raw = os.environ.get("REGIME_GATE_THETA")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "REGIME_GATE_THETA=%r is not a float; falling back to %.2f",
            raw, default,
        )
        return default


@dataclass
class RegimeGate:
    """Thin façade over ``compass.live_composite_stress``.

    Holds the gate threshold so the sizer reads a single object rather
    than reaching into the composite module directly.
    """

    theta: float = DEFAULT_THETA

    @classmethod
    def from_env(cls) -> "RegimeGate":
        return cls(theta=_theta_from_env())

    def current_stress(self) -> Optional[float]:
        from compass.live_composite_stress import get_current_composite_stress
        return get_current_composite_stress()

    def should_gate_spx_streams(self) -> bool:
        """Return True iff the SPX streams should be scaled down right now."""
        from compass.live_composite_stress import should_gate_spx_streams as _should_gate
        return _should_gate(theta=self.theta)
