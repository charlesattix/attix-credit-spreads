"""compass/live/vrp_runner.py — PR-E cutover wiring for EXP-V8A.

Composes the live VRP dependencies into a runnable cycle and exposes the seam
the scheduler calls when ``vrp_engine.enabled`` is set in an experiment's config:

    cc2 PR-A  VRPDataFeed            (live multi-symbol chains + VIX)
    cc3 PR-C  compute_weights        (LW risk-parity, via the engine)
    cc4 PR-D  resolve_vix_ladder_signal  (wrapped by Cc4VixExposure → multiplier)
    PR-B      VRPMultiStreamStrategy (the engine, merged #77)
              AlpacaOrderSink        (order placement — only when not dry-run)

ADDITIVE + INERT BY DEFAULT. The scheduler hook is guarded on
``config['vrp_engine']['enabled']`` (absent for every other experiment), and the
shipped EXP-V8A config sets ``enabled: false`` / ``dry_run: true``. The actual
Champion→VRP cutover is a one-line config toggle performed only AFTER the legacy
positions are flat (the flush — see docs/V8A_VRP_RECON_FLUSH_PLAN.md).
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

from compass.live.vrp_contracts import STREAM_SPECS, StreamStatus

logger = logging.getLogger(__name__)


def _default_vix_signal() -> Dict:
    """Call cc4's PR-D live ladder. Lazy import (avoids feed/state side effects
    until actually used)."""
    from compass.live.vrp_vix_ladder import resolve_vix_ladder_signal
    return resolve_vix_ladder_signal()


class Cc4VixExposure:
    """Adapts cc4's PR-D ``resolve_vix_ladder_signal() -> Dict`` to PR-B's
    ``VixExposureProvider`` protocol (``current_exposure_multiplier() -> float``).

    Returns the signal's ``sizing_multiplier``, but **halts (0.0) when cc4's
    ``entry_gate`` is False** — i.e. the live circuit-breaker block (VIX ≥ 35)
    overrides the soft ladder multiplier (CB > ladder; recon cc4 §3.2). When cc4
    ships the ``current_exposure_multiplier()`` convenience, this adapter can call
    it directly; until then it reads the dict it already returns.
    """

    def __init__(self, signal_fn: Optional[Callable[[], Dict]] = None) -> None:
        self._signal_fn = signal_fn or _default_vix_signal

    def current_exposure_multiplier(self) -> float:
        try:
            sig = self._signal_fn()
        except Exception as exc:  # noqa: BLE001 — fail flat, never crash sizing
            logger.error("[vrp_runner] vix signal failed: %s — halting new entries", exc)
            return 0.0
        if not sig.get("entry_gate", True):
            return 0.0
        try:
            return float(sig.get("sizing_multiplier", 0.0))
        except (TypeError, ValueError):
            return 0.0


def build_vrp_strategy(config: dict, alpaca_provider, *, data_feed=None, vix_provider=None):
    """Construct a configured ``VRPMultiStreamStrategy`` for the live worker.

    Account equity is read live from Alpaca each cycle (falls back to 0 → no
    allocation if unavailable). ``data_feed``/``vix_provider`` are injectable for
    tests; defaults are cc2's process-global feed and the cc4 adapter.
    """
    from compass.live.vrp_data import get_default_feed
    from compass.live.vrp_strategy import VRPMultiStreamStrategy

    cfg = config.get("vrp_engine", {}) or {}
    feed = data_feed if data_feed is not None else get_default_feed()
    vix = vix_provider if vix_provider is not None else Cc4VixExposure()

    def _equity() -> float:
        if alpaca_provider is None:
            return 0.0
        try:
            return float(alpaca_provider.get_account().get("equity", 0.0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[vrp_runner] equity fetch failed: %s", exc)
            return 0.0

    dte = cfg.get("dte_range", [25, 50])
    return VRPMultiStreamStrategy(
        feed,
        account_equity=_equity,
        vix_provider=vix,
        vol_target=float(cfg.get("vol_target", 0.12)),
        dte_range=(int(dte[0]), int(dte[1])),
    )


def vrp_enabled(config: dict) -> bool:
    """True only when the experiment's config opts into the VRP engine.

    Guard for the shared scheduler: absent/false for every non-VRP experiment, so
    the legacy scan path is completely unaffected.
    """
    return bool((config.get("vrp_engine") or {}).get("enabled", False))


def run_vrp_cycle(system, *, strategy=None):
    """One VRP scan cycle for the scheduler. Plans intents; places them only when
    ``vrp_engine.dry_run`` is false AND an Alpaca provider exists.

    Returns the :class:`CyclePlan` (also when dry-run) for logging/telemetry.
    """
    cfg = system.config.get("vrp_engine", {}) or {}
    provider = getattr(system, "alpaca_provider", None)
    strat = strategy or build_vrp_strategy(system.config, provider)

    dry_run = bool(cfg.get("dry_run", True)) or provider is None
    if dry_run:
        plan = strat.plan_cycle()
        results = []
    else:
        from compass.live.vrp_sinks import AlpacaOrderSink
        plan, results = strat.execute_cycle(sink=AlpacaOrderSink(provider))

    # Per-stream visibility, incl. the deferred futures sleeves.
    for sid, status in plan.stream_status.items():
        if STREAM_SPECS.get(sid) and STREAM_SPECS[sid].status is StreamStatus.BLOCKED:
            logger.info("[vrp_runner] %s: futures venue pending (deferred)", sid)
        else:
            logger.info("[vrp_runner] %s: %s", sid, status)

    logger.info(
        "[vrp_runner] cycle %s equity=$%.0f vix_mult=%.3f intents=%d placed=%d streams=%s%s",
        "DRY-RUN" if dry_run else "LIVE",
        plan.account_equity, plan.vix_exposure, len(plan.intents), len(results),
        ",".join(plan.traded_streams) or "-",
        f" notes={plan.notes}" if plan.notes else "",
    )
    return plan
