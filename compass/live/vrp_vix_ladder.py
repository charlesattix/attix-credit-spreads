"""compass/live/vrp_vix_ladder.py — PR-D: VIX ladder live trigger (EXP-V8A VRP).

Live wrapper around the pure EXP-2820 ladder (``compass.vix_ladder.VIXLadder``).
It fetches a live VIX, maps it to the exp2850 exposure multiplier, and emits a
per-stream signal (entry gate / sizing multiplier / exit gate) for the VRP
allocator + position monitor to consume each scan cycle.

Design source: docs/V8A_VRP_RECON_VIX_LADDER.md.
Scope rule: ADDITIVE — imported only by the EXP-V8A VRP path. Touches no other
experiment. Reads VIX, never places orders.

═══════════════════════════════════════════════════════════════════════════════
INTERFACE CONTRACT  (stable public API — cc1's PR-B / the PositionMonitor wire to
these; do not change signatures without pinging cc1)
═══════════════════════════════════════════════════════════════════════════════

    get_current_vix() -> float
        Latest live VIX with graceful degradation. Tries the live feed
        (Polygon I:VIX → yfinance ^VIX via scheduler.data_providers.get_vix_values);
        on failure falls back to the last-known persisted value IF it is fresh
        (≤ VRP_VIX_MAX_STALE_HOURS). Raises ``VixFeedUnavailable`` only when there
        is no live value AND no fresh last-known value. Persists every successful
        live read to the state file.

    vix_ladder_signal(current_vix: float) -> dict
        Pure function (no I/O). Maps a VIX level to the trading signal:
            {
              "vix": float,
              "sizing_multiplier": float,   # EXP-2820 ladder, == exp2850 exposure
              "entry_gate": bool,           # True ⇒ new entries allowed
              "exit_gate": bool,            # True ⇒ emergency exit signalled
              "regime": str,
              "per_stream": { stream: {sizing_multiplier, entry_gate, exit_gate} },
              "source": "computed", "degraded": False, "halted": False,
            }
        Raises ValueError on a non-finite / non-positive VIX.

    resolve_vix_ladder_signal() -> dict
        RECOMMENDED monitor entrypoint. Calls get_current_vix() then
        vix_ladder_signal(); on VixFeedUnavailable returns the HALT signal
        (all gates closed, sizing 0.0, exit_gate False, halted=True). NEVER raises
        and NEVER crashes the scan loop. Call ONCE per Tier-2 PositionMonitor scan
        (5-min market hours) and distribute the result to every stream.

    V8A_STREAMS : tuple[str, ...]   — the 8 VRP stream ids.
    class VixFeedUnavailable(RuntimeError)

Precedence note (recon §3.2): the live VIX **circuit breaker**
(scheduler/jobs.py: block@35, exit@45) is a separate hard gate that sits ABOVE
this ladder. ENTRY_BLOCK_VIX / EXIT_ALL_VIX below deliberately mirror those
thresholds so the VRP signal is consistent with the system-wide breaker.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from compass.vix_ladder import VIXLadder

logger = logging.getLogger(__name__)

# The 8 VRP streams (matches compass/exp2850_v8a_with_vix_ladder.py:79-80).
V8A_STREAMS: Tuple[str, ...] = (
    "exp1220", "v5_hedge", "gld_cal", "slv_cal",
    "cross_vol", "xlf_cs", "xli_cs", "qqq_cs",
)

# Live safety thresholds — mirror the existing circuit breaker
# (scheduler/jobs.py:295-296) so the VRP ladder agrees with the system breaker.
ENTRY_BLOCK_VIX = float(os.environ.get("VRP_VIX_ENTRY_BLOCK", "35.0"))  # VIX_CRISIS_BLOCK
EXIT_ALL_VIX = float(os.environ.get("VRP_VIX_EXIT_ALL", "45.0"))        # VIX_EMERGENCY_EXIT
# How stale the last-known VIX may be before we halt new entries (overnight/holiday gap).
MAX_STALE_HOURS = float(os.environ.get("VRP_VIX_MAX_STALE_HOURS", "26.0"))

# Single shared ladder instance — EXP-2820 default, identical mapping to exp2850.
_LADDER = VIXLadder()


class VixFeedUnavailable(RuntimeError):
    """Raised when neither a live VIX nor a fresh last-known value is available."""


# ─────────────────────────────────────────────────────────────────────────────
# Last-known VIX persistence (for the graceful-degradation fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _state_path() -> Path:
    """Path to the last-known-VIX state file. VRP_VIX_STATE_PATH overrides;
    otherwise <DATA_DIR>/vrp_vix_state.json (DATA_DIR defaults to ``data``)."""
    override = os.environ.get("VRP_VIX_STATE_PATH")
    if override:
        return Path(override)
    return Path(os.environ.get("DATA_DIR", "data")) / "vrp_vix_state.json"


def _read_last_known() -> Optional[Tuple[float, float]]:
    """Return (vix, ts_epoch_utc) from the state file, or None if absent/unreadable."""
    path = _state_path()
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        vix = float(data["vix"])
        ts = float(data["ts_epoch"])
        if not math.isfinite(vix) or vix <= 0:
            return None
        return vix, ts
    except Exception as exc:  # noqa: BLE001 — fallback must never raise
        logger.warning("vrp_vix_ladder: could not read last-known VIX state: %s", exc)
        return None


def _write_last_known(vix: float, ts_epoch: float) -> None:
    """Persist the latest good VIX read. Best-effort; never raises."""
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "vix": round(float(vix), 4),
            "ts_epoch": float(ts_epoch),
            "ts_iso": datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat(),
        }))
        tmp.replace(path)  # atomic
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        logger.warning("vrp_vix_ladder: could not persist last-known VIX: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Live VIX fetch (+ fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_live_vix() -> Optional[float]:
    """Fetch the latest live VIX, or None on any failure.

    Reuses the battle-tested scheduler.data_providers.get_vix_values()
    (Polygon I:VIX → yfinance ^VIX). Imported lazily so this module has no
    import-time coupling to the scheduler package. When cc2's PR-A
    ``compass.live.vrp_data`` ships a canonical live-VIX accessor, this is the
    single function to repoint.
    """
    try:
        from scheduler.data_providers import get_vix_values
    except Exception as exc:  # noqa: BLE001
        logger.warning("vrp_vix_ladder: VIX provider import failed: %s", exc)
        return None
    try:
        vix, _vix3m = get_vix_values()
    except Exception as exc:  # noqa: BLE001
        logger.warning("DATA_FALLBACK: vrp_vix_ladder live VIX fetch failed: %s", exc)
        return None
    if vix is None:
        return None
    try:
        vix = float(vix)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(vix) or vix <= 0:
        return None
    return vix


def _resolve_vix() -> Tuple[float, str]:
    """Resolve a usable VIX value with graceful degradation.

    Returns (vix, source) where source ∈ {"live", "last_known"}.
    Raises VixFeedUnavailable when the live feed is down AND last-known is
    stale/absent.
    """
    now = datetime.now(timezone.utc).timestamp()

    live = _fetch_live_vix()
    if live is not None:
        _write_last_known(live, now)
        return live, "live"

    last = _read_last_known()
    if last is not None:
        vix, ts = last
        age_h = (now - ts) / 3600.0
        if age_h <= MAX_STALE_HOURS:
            logger.warning(
                "DATA_FALLBACK: vrp_vix_ladder using last-known VIX %.2f (age %.1fh ≤ %.1fh)",
                vix, age_h, MAX_STALE_HOURS,
            )
            return vix, "last_known"
        raise VixFeedUnavailable(
            f"live VIX unavailable and last-known is stale "
            f"({age_h:.1f}h > {MAX_STALE_HOURS:.1f}h) — halting new entries"
        )

    raise VixFeedUnavailable(
        "live VIX unavailable and no last-known value persisted — halting new entries"
    )


def get_current_vix() -> float:
    """Latest live VIX with graceful fallback. See INTERFACE CONTRACT."""
    return _resolve_vix()[0]


# ─────────────────────────────────────────────────────────────────────────────
# Signal construction
# ─────────────────────────────────────────────────────────────────────────────

def _regime_label(vix: float) -> str:
    if vix >= EXIT_ALL_VIX:
        return "emergency"
    if vix >= ENTRY_BLOCK_VIX:
        return "crisis_block"
    if vix >= 30.0:
        return "elevated"
    if vix >= 20.0:
        return "normal"
    return "calm"


def vix_ladder_signal(current_vix: float) -> Dict:
    """Pure VIX → VRP signal. See INTERFACE CONTRACT. Raises ValueError on bad VIX."""
    vix = float(current_vix)
    if not math.isfinite(vix) or vix <= 0:
        raise ValueError(f"vix_ladder_signal requires a finite positive VIX, got {current_vix!r}")

    # EXP-2820 ladder multiplier — identical to the exp2850 backtest mapping.
    multiplier = float(_LADDER.exposure_at(vix))

    # Live-only safety gates (mirror the system circuit breaker; not present in
    # the exp2850 backtest, which has no live entry/exit gating).
    entry_gate = (vix < ENTRY_BLOCK_VIX) and (multiplier > 0.0)
    exit_gate = vix >= EXIT_ALL_VIX

    # Uniform across streams — faithful to exp2850, which applies the multiplier
    # to the whole portfolio. NOTE for cc1/PR-E/PR-H: the v5_hedge (tail-hedge)
    # stream arguably wants INVERTED behaviour (more hedge as VIX rises); that
    # per-stream override is intentionally NOT decided here — see recon §5.1 /
    # open question. The per-stream dict is the seam for that future override.
    per_stream = {
        s: {
            "sizing_multiplier": multiplier,
            "entry_gate": entry_gate,
            "exit_gate": exit_gate,
        }
        for s in V8A_STREAMS
    }

    return {
        "vix": vix,
        "sizing_multiplier": multiplier,
        "entry_gate": entry_gate,
        "exit_gate": exit_gate,
        "regime": _regime_label(vix),
        "per_stream": per_stream,
        "source": "computed",
        "degraded": False,
        "halted": False,
    }


def _halt_signal(reason: str) -> Dict:
    """Degraded signal when no usable VIX is available: stop new entries, hold
    existing positions (do NOT force-exit on missing data), never crash."""
    per_stream = {
        s: {"sizing_multiplier": 0.0, "entry_gate": False, "exit_gate": False}
        for s in V8A_STREAMS
    }
    return {
        "vix": None,
        "sizing_multiplier": 0.0,
        "entry_gate": False,
        "exit_gate": False,
        "regime": "unknown",
        "per_stream": per_stream,
        "source": "halt",
        "degraded": True,
        "halted": True,
        "reason": reason,
    }


def resolve_vix_ladder_signal() -> Dict:
    """Monitor entrypoint: resolve VIX → signal with full graceful degradation.
    Never raises. See INTERFACE CONTRACT."""
    try:
        vix, source = _resolve_vix()
    except VixFeedUnavailable as exc:
        logger.error("vrp_vix_ladder: HALTING new entries — %s", exc)
        return _halt_signal(str(exc))
    sig = vix_ladder_signal(vix)
    sig["source"] = source
    sig["degraded"] = source != "live"
    return sig
