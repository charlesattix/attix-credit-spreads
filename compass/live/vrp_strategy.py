"""compass/live/vrp_strategy.py — VRPMultiStreamStrategy engine (PR-B).

The live counterpart of ``compass/exp2850_v8a_with_vix_ladder.py``. Where the
backtest overlays risk-parity weights × a VIX ladder onto 8 pre-computed daily
return *series*, this engine runs the same composition forward on **live data**:

    snapshot (cc2 PR-A)
        │
        ├─ realized per-stream returns (PR-I)  ─┐
        │                                       ├─► cc3 compute_weights(scaled=True)
        │                                       │      → per-equity exposure fractions
        ├─ live VIX → ladder mult (cc4 PR-D) ───┘      × account_equity × vix_mult
        │                                              = per-stream CAPITAL ($)
        └─ per-stream signal generators (PR-B) ──────► OrderIntent[]  ──► OrderSink

It emits :class:`OrderIntent` objects and (optionally) hands them to an
:class:`OrderSink`. By default the sink is a :class:`RecordingOrderSink`, so a
cycle NEVER places a live order unless an Alpaca sink is explicitly injected
(the PR-E cutover). Fully testable with no live Alpaca.

ADDITIVE: not imported by ``main.py`` or any config. The cutover that loads this
as V8A's strategy is a separate, last step (orchestrator PR-E).
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Union

import pandas as pd

from compass.live.vrp_contracts import (
    CyclePlan,
    OrderSink,
    ReturnsProvider,
    StreamStatus,
    VixExposureProvider,
)
from compass.live.vrp_data import VRPSnapshot
from compass.live.vrp_risk_parity import DEFAULT_VOL_TARGET, compute_weights
from compass.live.vrp_sinks import RecordingOrderSink
from compass.live.vrp_streams import build_default_registry

logger = logging.getLogger(__name__)

EquitySource = Union[float, Callable[[], float]]


class VRPMultiStreamStrategy:
    """Orchestrates the 8 VRP streams: data → weights → ladder → intents.

    Parameters
    ----------
    data_feed:
        cc2 ``VRPDataFeed`` (or anything implementing ``snapshot()`` /
        ``get_bars()``).
    vix_provider:
        cc4 ladder (``current_exposure_multiplier()``). Defaults to a halting
        stub if omitted (no exposure without an explicit ladder).
    returns_provider:
        PR-I per-stream realized returns. Defaults to a cold-start stub over the
        active streams (→ cc3 prior-mode weights).
    account_equity:
        float or zero-arg callable returning current account equity ($).
    registry:
        ``{stream_id: StreamSignalGenerator}``; defaults to the canonical 8.
    vol_target:
        annualized portfolio vol target handed to cc3 (default 0.12).
    """

    def __init__(
        self,
        data_feed,
        *,
        account_equity: EquitySource,
        vix_provider: Optional[VixExposureProvider] = None,
        returns_provider: Optional[ReturnsProvider] = None,
        registry: Optional[Mapping[str, object]] = None,
        vol_target: float = DEFAULT_VOL_TARGET,
        dte_range=(25, 50),
    ) -> None:
        self._feed = data_feed
        self._registry: Dict[str, object] = dict(registry) if registry is not None else build_default_registry()
        self._equity_src = account_equity
        self._vol_target = float(vol_target)
        self._dte_range = dte_range

        # Streams that can actually place orders today (TRADEABLE). The allocator
        # sizes over these; deferred/blocked streams get $0 and report status.
        self._active_streams: List[str] = [
            sid for sid, gen in self._registry.items()
            if getattr(getattr(gen, "spec", None), "status", None) is StreamStatus.TRADEABLE
        ]
        self._active_symbols: List[str] = sorted({
            sym for sid in self._active_streams for sym in self._registry[sid].spec.symbols
        })

        # Default deps: a halting VIX stub (no exposure without a ladder) and a
        # cold-start returns stub over the active streams (→ cc3 prior mode).
        if vix_provider is None:
            from compass.live.vrp_stubs import LadderVixExposure
            vix_provider = LadderVixExposure(vix_source=self._feed_vix)
        self._vix = vix_provider
        if returns_provider is None:
            from compass.live.vrp_stubs import StaticReturnsProvider
            returns_provider = StaticReturnsProvider(self._active_streams)
        self._returns = returns_provider

    # ── helpers ───────────────────────────────────────────────────────────────

    def _feed_vix(self) -> Optional[float]:
        getter = getattr(self._feed, "get_vix_realtime", None)
        return getter() if callable(getter) else None

    def _equity(self) -> float:
        eq = self._equity_src() if callable(self._equity_src) else self._equity_src
        try:
            return max(0.0, float(eq))
        except (TypeError, ValueError):
            return 0.0

    def _allocate(self, equity: float, vix_mult: float) -> Dict[str, float]:
        """Per-stream capital ($) = equity × cc3 exposure fraction × ladder mult.

        Allocates over the active (tradeable) streams only; cc3 cold-starts to a
        prior covariance when live history is short/empty.
        """
        if equity <= 0 or vix_mult <= 0 or not self._active_streams:
            return {}
        returns_df = self._returns.stream_returns(lookback=252)
        # Guarantee the allocator sees the active-stream columns even at cold start.
        if returns_df is None or len(getattr(returns_df, "columns", [])) == 0:
            returns_df = pd.DataFrame(columns=self._active_streams)
        fractions = compute_weights(returns_df, self._vol_target, scaled=True)
        return {sid: equity * float(frac) * vix_mult for sid, frac in fractions.items()}

    # ── public ──────────────────────────────────────────────────────────────--

    def plan_cycle(self) -> CyclePlan:
        """Produce this cycle's order intents + full provenance. Places nothing."""
        reset = getattr(self._feed, "reset_cycle", None)
        if callable(reset):
            reset()

        snapshot: VRPSnapshot = self._feed.snapshot(
            option_symbols=self._active_symbols or None, dte_range=self._dte_range
        )
        vix_mult = float(self._vix.current_exposure_multiplier())
        equity = self._equity()
        capital = self._allocate(equity, vix_mult)

        notes: List[str] = []
        if vix_mult <= 0:
            notes.append("VIX ladder exposure 0.0 — no new entries this cycle.")
        if equity <= 0:
            notes.append("Account equity ≤ 0 — no allocation.")

        plan = CyclePlan(
            as_of=snapshot.as_of,
            account_equity=equity,
            vix_exposure=vix_mult,
            capital=capital,
            degraded_symbols=list(snapshot.degraded),
            notes=notes,
        )

        for sid, gen in self._registry.items():
            result = gen.generate(snapshot, capital.get(sid, 0.0))
            plan.stream_status[sid] = (
                f"{result.status}: {result.reason}" if result.reason else result.status
            )
            plan.intents.extend(result.intents)

        logger.info(
            "[vrp] cycle as_of=%s equity=$%.0f vix_mult=%.3f intents=%d active=%s degraded=%s",
            snapshot.as_of, equity, vix_mult, len(plan.intents),
            ",".join(plan.traded_streams) or "-", ",".join(snapshot.degraded) or "-",
        )
        return plan

    def execute_cycle(self, sink: Optional[OrderSink] = None):
        """Plan a cycle and submit its intents through ``sink``.

        Defaults to a :class:`RecordingOrderSink` — so this NEVER places a live
        order unless an Alpaca sink is explicitly passed (PR-E cutover).
        Returns ``(plan, results)``.
        """
        plan = self.plan_cycle()
        sink = sink or RecordingOrderSink()
        results: List[dict] = [sink.submit(intent) for intent in plan.intents]
        return plan, results

    @property
    def active_streams(self) -> Sequence[str]:
        return tuple(self._active_streams)
