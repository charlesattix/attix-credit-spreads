"""
compass/crisis_hedge.py — VIX-adaptive crisis drawdown mitigation.

Provides two controls:
  1. Position size scale factor (0.0–1.0): applied to ALL new entries.
  2. Stop-loss multiplier: tightens as VIX rises, protecting existing positions.

Usage:
    hedge = CrisisHedgeController(config)

    # At trade entry (in portfolio engine)
    scale = hedge.position_scale_factor(vix=current_vix, regime=current_regime)
    contracts = base_contracts * scale

    # In daily position management
    stop_mult = hedge.stop_loss_multiplier(vix=current_vix)
    stop_price = entry_credit * stop_mult

Design:
    VIX scaling (piecewise linear, default config floor=20, ceiling=50):
        VIX ≤ 20:        scale = 1.00
        VIX 20–30 (+10): scale = 1.00 → 0.50  (slope: −0.050/pt)
        VIX 30–40 (+10): scale = 0.50 → 0.10  (slope: −0.040/pt)
        VIX 40–50 (+10): scale = 0.10 → 0.00  (slope: −0.010/pt)
        VIX ≥ 50:        scale = 0.00 (no new entries)

    Stop tightening (linear, default floor=20, ceiling=45):
        VIX ≤ 20: stop = base_stop_multiplier (3.5×)
        VIX ≥ 45: stop = min_stop_multiplier  (1.5×)
        Between:  linear interpolation

    Regime hard gates (override VIX calculation):
        crash    → scale = crash_regime_scale  (default 0.0)
        high_vol → scale = min(vix_scale, high_vol_regime_scale)  (default 0.25)

    VIX term structure:
        When vix3m < vix (backwardation), apply additional scale penalty
        proportional to inversion depth.

Reference: compass/research/crisis_mitigation_design.md, Section 5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


@dataclass
class CrisisHedgeConfig:
    # VIX scaling thresholds
    vix_scale_floor: float = 20.0     # Below this: 100% position size
    vix_scale_ceiling: float = 50.0   # Above this: 0% position size (no new entries)

    # VIX stop-loss thresholds (override the strategy's base stop multiplier)
    vix_stop_floor: float = 20.0      # Below this: use base stop multiplier
    vix_stop_ceiling: float = 45.0    # Above this: use minimum stop multiplier
    base_stop_multiplier: float = 3.5  # Normal-market stop (from config)
    min_stop_multiplier: float = 1.5   # Crash-market stop (minimum allowed)

    # VIX term structure enhancement (if VIX3M data is available)
    use_vix_term_structure: bool = True
    vix_ts_backwardation_penalty: float = 0.25  # Additional scale reduction when backwardated

    # Regime hard gates
    crash_regime_scale: float = 0.0    # Hard stop on new entries in crash regime
    high_vol_regime_scale: float = 0.25  # Throttle to 25% in high_vol regime

    # Hysteresis: prevent rapid on/off cycling (VIX must drop this many points
    # below scale_floor before resuming full size after a scale-down)
    recovery_hysteresis_vix: float = 3.0

    # Audit logging
    log_decisions: bool = True


class CrisisHedgeController:
    """VIX-adaptive position sizing and stop-loss controller.

    Computes two scalars:
      - position_scale_factor (0.0–1.0): multiply base_contracts by this
      - stop_loss_multiplier (min_stop to base_stop): use in place of config stop

    Both are monotonically decreasing functions of VIX, with regime overrides.

    Thread-safe: all methods are stateless given inputs.
    """

    def __init__(self, config: Optional[CrisisHedgeConfig] = None):
        self.cfg = config or CrisisHedgeConfig()
        self._last_scale_factor: float = 1.0  # for hysteresis tracking
        self._below_hysteresis_threshold: bool = True  # True = can scale back up
        log.info(
            "CrisisHedgeController: VIX floor=%.0f ceiling=%.0f "
            "stop base=%.1f× min=%.1f×",
            self.cfg.vix_scale_floor,
            self.cfg.vix_scale_ceiling,
            self.cfg.base_stop_multiplier,
            self.cfg.min_stop_multiplier,
        )

    def position_scale_factor(
        self,
        vix: float,
        regime: Optional[str] = None,
        vix3m: Optional[float] = None,
    ) -> float:
        """Compute position size scale factor for a new trade entry.

        Args:
            vix:    Current VIX level (spot).
            regime: Regime label from ComboRegimeDetector (bull/bear/neutral/
                    high_vol/low_vol/crash). None = treat as neutral.
            vix3m:  VIX 3-month level (^VIX3M). If None, term structure check
                    is skipped.

        Returns:
            float in [0.0, 1.0]. Multiply base_contracts by this value.
            Returns 0.0 when no new entries should be opened.
        """
        r = (regime or "neutral").lower().strip()

        # Hard regime gates (override VIX calculation)
        if r == "crash":
            scale = self.cfg.crash_regime_scale
            self._log_decision("crash regime hard gate", scale, vix, regime)
            return scale
        if r == "high_vol":
            vix_scale = self._vix_scale(vix)
            scale = min(vix_scale, self.cfg.high_vol_regime_scale)
            self._log_decision("high_vol regime cap", scale, vix, regime)
            return scale

        # VIX-based continuous scaling
        scale = self._vix_scale(vix)

        # VIX term structure penalty: backwardation → additional reduction
        if self.cfg.use_vix_term_structure and vix3m is not None:
            ts_ratio = vix3m / max(vix, 1.0)
            if ts_ratio < 1.0:
                # Backwardation: term structure inverted (near-term fear > forward)
                # Apply additional scale reduction proportional to inversion depth
                inversion_depth = 1.0 - ts_ratio  # 0 = flat, 0.2 = 20% backwardation
                penalty = min(self.cfg.vix_ts_backwardation_penalty, inversion_depth * 2)
                scale = scale * (1.0 - penalty)
                self._log_decision(
                    f"VIX term structure backwardation penalty={penalty:.2f}",
                    scale, vix, regime,
                )

        self._last_scale_factor = scale
        self._log_decision("VIX scale", scale, vix, regime)
        return round(max(0.0, min(1.0, scale)), 4)

    def stop_loss_multiplier(
        self,
        vix: float,
        regime: Optional[str] = None,
    ) -> float:
        """Compute stop-loss multiplier for an open position.

        Returns the multiplier to apply to the entry credit. A lower multiplier
        means tighter stop-loss (closer to breakeven), protecting against
        accelerating losses in high-VIX environments.

        Args:
            vix:    Current VIX level.
            regime: Regime label. crash regime always returns min_stop_multiplier.

        Returns:
            float in [min_stop_multiplier, base_stop_multiplier].
        """
        r = (regime or "neutral").lower().strip()

        # Crash: minimum stop always
        if r == "crash":
            return self.cfg.min_stop_multiplier

        base = self.cfg.base_stop_multiplier
        min_m = self.cfg.min_stop_multiplier
        floor = self.cfg.vix_stop_floor
        ceiling = self.cfg.vix_stop_ceiling

        if vix <= floor:
            return base
        if vix >= ceiling:
            return min_m

        # Linear interpolation
        t = (vix - floor) / (ceiling - floor)  # 0 at floor, 1 at ceiling
        multiplier = base - t * (base - min_m)
        return round(max(min_m, min(base, multiplier)), 3)

    def get_audit_metadata(
        self,
        vix: float,
        regime: Optional[str] = None,
        vix3m: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Return full audit metadata for logging/Telegram alerts.

        Returns:
            Dict with scale_factor, stop_multiplier, regime, vix, vix3m,
            is_throttled (bool), is_halted (bool), reason (str).
        """
        scale = self.position_scale_factor(vix, regime, vix3m)
        stop = self.stop_loss_multiplier(vix, regime)

        ts_ratio = (vix3m / max(vix, 1.0)) if vix3m else None
        backwardated = (ts_ratio is not None and ts_ratio < 1.0)

        reason_parts = []
        if scale == 0.0:
            reason_parts.append("HALTED")
        elif scale < 0.5:
            reason_parts.append(f"HEAVY_THROTTLE ({scale:.0%})")
        elif scale < 1.0:
            reason_parts.append(f"LIGHT_THROTTLE ({scale:.0%})")
        if backwardated:
            reason_parts.append(f"VIX_BACKWARDATED (ratio={ts_ratio:.2f})")
        if stop < self.cfg.base_stop_multiplier:
            reason_parts.append(f"STOP_TIGHTENED ({stop:.1f}×)")

        return {
            "scale_factor":      scale,
            "stop_multiplier":   stop,
            "regime":            regime or "neutral",
            "vix":               vix,
            "vix3m":             vix3m,
            "ts_ratio":          ts_ratio,
            "is_backwardated":   backwardated,
            "is_throttled":      scale < 1.0,
            "is_halted":         scale == 0.0,
            "reason":            "; ".join(reason_parts) or "NORMAL",
        }

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _vix_scale(self, vix: float) -> float:
        """Piecewise linear VIX → scale mapping.

        Breakpoints (default config: floor=20, ceiling=50):
          VIX ≤ 20:         1.00
          VIX 20–30 (+10):  1.00 → 0.50  (slope: −0.050 per VIX point)
          VIX 30–40 (+10):  0.50 → 0.10  (slope: −0.040 per VIX point)
          VIX 40–50 (+10):  0.10 → 0.00  (slope: −0.010 per VIX point)
          VIX ≥ 50:         0.00
        """
        floor = self.cfg.vix_scale_floor
        ceiling = self.cfg.vix_scale_ceiling

        if vix <= floor:
            return 1.0
        if vix >= ceiling:
            return 0.0

        # Three equal-width segments between floor and ceiling
        span = ceiling - floor
        seg = span / 3.0

        t = vix - floor  # offset above floor
        if t <= seg:
            # Segment 1: 1.0 → 0.50
            return 1.0 - 0.50 * (t / seg)
        elif t <= 2 * seg:
            # Segment 2: 0.50 → 0.10
            return 0.50 - 0.40 * ((t - seg) / seg)
        else:
            # Segment 3: 0.10 → 0.00
            return 0.10 - 0.10 * ((t - 2 * seg) / seg)

    def _log_decision(self, reason: str, scale: float, vix: float, regime: Optional[str]) -> None:
        if self.cfg.log_decisions:
            log.info(
                "CrisisHedge [%s]: scale=%.2f vix=%.1f regime=%s",
                reason, scale, vix, regime or "neutral",
            )
