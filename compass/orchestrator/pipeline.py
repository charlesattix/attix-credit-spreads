"""compass/orchestrator/pipeline.py — end-to-end orchestrator entry point.

This module is the deliberate seam between EXP-2690 signal generators
(intent) and the Alpaca broker (execution). It owns the daily run
lifecycle described in ORCHESTRATOR_PROPOSAL.md §6.

Public surface (the API contract Vesper / Atlas consume):

    result = run(date, mode="paper", audit_dir=None, **kwargs) -> PipelineResult
    result = cleanup(date, audit_dir=None, **kwargs)         -> PipelineResult
    result = reconcile(date, audit_dir=None, **kwargs)       -> PipelineResult

Each function returns a fully-populated :class:`PipelineResult` (never
raises for business-logic outcomes — only hard failures like missing
credentials propagate as a FAILED status with non-empty `errors`).

The three stages are invoked through small adapter functions
(_evaluate_gate / _size_orders / _route_orders) so the pipeline can be
unit-tested without the downstream modules landed; in production they
call into ``compass.orchestrator.entry_gate``, ``position_sizer``, and
``order_router`` (built by CC1–CC4).

Mode semantics:
    paper      -- submit to Alpaca paper (default)
    live       -- submit to Alpaca live (requires ALPACA_PAPER=false +
                  ORCHESTRATOR_CONFIRM_LIVE_TOKEN env var)
    dry-run    -- run full pipeline, write JSONL audit, no broker calls
    replay     -- read a historical audit, re-evaluate without broker
    cleanup    -- cancel unfilled DAY orders, snapshot equity
    reconcile  -- diff intended vs broker; flag mismatches
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from compass.orchestrator.portfolio_state import PortfolioState
from compass.orchestrator.types import (
    GatedSignal,
    PipelineResult,
    PipelineStatus,
    SignalIntent,
    SizedOrder,
)

LOG = logging.getLogger(__name__)

VALID_MODES = {"paper", "live", "dry-run", "replay", "cleanup", "reconcile"}

# Mode literals that actually call the broker
_BROKER_MODES = {"paper", "live"}


# ───────────────────────────────────────────────────────────────────────────
# Public entry points
# ───────────────────────────────────────────────────────────────────────────

def run(
    date: str,
    mode: str = "paper",
    audit_dir: Optional[str] = None,
    *,
    connector: Any = None,
    signal_generator: Optional[Callable[[str], List[Dict]]] = None,
    gate_fn: Optional[Callable[..., List[GatedSignal]]] = None,
    sizer_fn: Optional[Callable[..., List[SizedOrder]]] = None,
    router_fn: Optional[Callable[..., List[Any]]] = None,
    return_panel: Any = None,
    now: Optional[datetime] = None,
) -> PipelineResult:
    """Run the full intent → gate → size → route pipeline for ``date``.

    Parameters
    ----------
    date          ISO trading date (YYYY-MM-DD).
    mode          One of VALID_MODES. ``paper`` is the default.
    audit_dir     Directory root for JSONL audit logs. If None, audit
                  writes are silently skipped. The pipeline creates
                  ``<audit_dir>/<date>/0X_*.jsonl`` files.
    connector     AlpacaConnector handle. If None and the mode requires
                  the broker, it is built via ``AlpacaConnector.from_env()``.
    signal_generator / gate_fn / sizer_fn / router_fn
                  Test-injection hooks. In production every one of these
                  resolves to the canonical module via the lazy
                  ``_default_*`` helpers below.
    return_panel  Optional historical return DataFrame passed through to
                  PortfolioState.load for the correlation matrix.
    now           Wall-clock override for deterministic tests.

    Returns
    -------
    PipelineResult with run_id, per-stage counts, status, errors/warnings.
    """
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    run_id = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    errors: List[str] = []
    warnings: List[str] = []

    if mode not in VALID_MODES:
        return _failed(
            run_id=run_id, date=date, mode=mode,
            errors=[f"invalid mode: {mode!r}"],
            duration_seconds=time.monotonic() - started,
        )

    if mode == "cleanup":
        return cleanup(date, audit_dir=audit_dir, connector=connector, now=now)
    if mode == "reconcile":
        return reconcile(date, audit_dir=audit_dir, connector=connector, now=now)

    # Live-mode safety: refuse without explicit operator token.
    if mode == "live":
        token_ok = bool(os.environ.get("ORCHESTRATOR_CONFIRM_LIVE_TOKEN"))
        paper_env = os.environ.get("ALPACA_PAPER", "true").strip().lower()
        if not token_ok or paper_env != "false":
            return _failed(
                run_id=run_id, date=date, mode=mode,
                errors=["live mode requires ORCHESTRATOR_CONFIRM_LIVE_TOKEN and ALPACA_PAPER=false"],
                duration_seconds=time.monotonic() - started,
            )

    audit_paths = _audit_paths(audit_dir, date)
    _ensure_audit_dir(audit_paths)

    # ── Stage 0: portfolio snapshot ──────────────────────────────────
    if connector is None and mode in _BROKER_MODES:
        connector = _default_connector()
    portfolio = _load_portfolio(connector, return_panel, errors, warnings)
    if not portfolio.load_ok and mode in _BROKER_MODES:
        # Fail closed: portfolio snapshot is required for any broker mode.
        return _failed(
            run_id=run_id, date=date, mode=mode,
            errors=errors or ["portfolio snapshot failed"],
            duration_seconds=time.monotonic() - started,
            portfolio_equity=portfolio.equity,
        )

    # ── Stage 1: intent ──────────────────────────────────────────────
    sig_gen = signal_generator or _default_signal_generator()
    try:
        raw_signals = sig_gen(date) or []
    except Exception as exc:
        return _failed(
            run_id=run_id, date=date, mode=mode,
            errors=[f"signal_generator failed: {type(exc).__name__}: {exc}"],
            duration_seconds=time.monotonic() - started,
            portfolio_equity=portfolio.equity,
        )

    intents = _as_signal_intents(raw_signals)
    _write_jsonl(audit_paths.get("intent"), [asdict(i) for i in intents])

    # ── Stage 2: entry_gate ──────────────────────────────────────────
    gate = gate_fn or _default_gate_fn()
    try:
        gated: List[GatedSignal] = list(gate(intents, portfolio, date))
    except Exception as exc:
        return _failed(
            run_id=run_id, date=date, mode=mode,
            errors=[f"entry_gate failed: {type(exc).__name__}: {exc}"],
            n_intents=len(intents),
            duration_seconds=time.monotonic() - started,
            portfolio_equity=portfolio.equity,
        )
    _write_jsonl(audit_paths.get("gated"), [_gated_to_dict(g) for g in gated])

    n_allow = sum(1 for g in gated if g.gate_status == "ALLOW")
    n_block = sum(1 for g in gated if g.gate_status == "BLOCK")
    n_degrade = sum(1 for g in gated if g.gate_status == "DEGRADE")

    # ── Stage 3: position_sizer ──────────────────────────────────────
    sizer = sizer_fn or _default_sizer_fn()
    eligible = [g for g in gated if g.gate_status in ("ALLOW", "DEGRADE")]
    try:
        sized: List[SizedOrder] = list(sizer(eligible, portfolio, connector))
    except Exception as exc:
        return _failed(
            run_id=run_id, date=date, mode=mode,
            errors=[f"position_sizer failed: {type(exc).__name__}: {exc}"],
            n_intents=len(intents), n_allow=n_allow, n_block=n_block, n_degrade=n_degrade,
            duration_seconds=time.monotonic() - started,
            portfolio_equity=portfolio.equity,
        )
    _write_jsonl(audit_paths.get("sized"), [_sized_to_dict(s) for s in sized])

    # ── Stage 4: order_router ────────────────────────────────────────
    submit = mode in _BROKER_MODES
    router = router_fn or _default_router_fn()
    try:
        orders = list(router(sized, connector, submit=submit))
    except Exception as exc:
        return _failed(
            run_id=run_id, date=date, mode=mode,
            errors=[f"order_router failed: {type(exc).__name__}: {exc}"],
            n_intents=len(intents), n_allow=n_allow, n_block=n_block,
            n_degrade=n_degrade, n_sized=len(sized),
            duration_seconds=time.monotonic() - started,
            portfolio_equity=portfolio.equity,
            gross_risk_dollars=sum(s.risk_allocation for s in sized),
        )
    _write_jsonl(audit_paths.get("orders"), [_order_to_dict(o) for o in orders])

    n_submitted = sum(1 for o in orders if _order_status(o) in ("SUBMITTED", "FILLED", "PENDING"))
    n_filled = sum(1 for o in orders if _order_status(o) == "FILLED")
    n_rejected = sum(1 for o in orders if _order_status(o) in ("REJECTED", "REJECTED_NO_MLEG"))

    status: PipelineStatus = "OK"
    if errors:
        status = "FAILED"
    elif n_rejected or (sized and not n_submitted and submit):
        status = "PARTIAL"

    return PipelineResult(
        run_id=run_id,
        date=date,
        mode=mode,
        status=status,
        n_intents=len(intents),
        n_allow=n_allow,
        n_block=n_block,
        n_degrade=n_degrade,
        n_sized=len(sized),
        n_submitted=n_submitted,
        n_filled=n_filled,
        n_rejected=n_rejected,
        gross_risk_dollars=sum(s.risk_allocation for s in sized),
        portfolio_equity=portfolio.equity,
        duration_seconds=round(time.monotonic() - started, 3),
        errors=errors,
        warnings=warnings,
        per_stream_counts=_per_stream_counts(intents, gated, sized, orders),
    )


def cleanup(
    date: str,
    audit_dir: Optional[str] = None,
    *,
    connector: Any = None,
    now: Optional[datetime] = None,
) -> PipelineResult:
    """End-of-day cleanup: cancel unfilled DAY orders + snapshot equity.

    Returns a PipelineResult with mode='cleanup'. Per-stream counts are
    empty. `n_rejected` carries the number of orders the broker refused
    to cancel; `n_filled` is repurposed as the count of cancellations.
    """
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    run_id = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    errors: List[str] = []
    warnings: List[str] = []

    if connector is None:
        connector = _default_connector()

    n_canceled = 0
    try:
        n_canceled = int(connector.cancel_all() or 0)
    except Exception as exc:
        errors.append(f"cancel_all failed: {type(exc).__name__}: {exc}")

    portfolio = _load_portfolio(connector, return_panel=None,
                                  errors=errors, warnings=warnings)
    audit_paths = _audit_paths(audit_dir, date)
    _ensure_audit_dir(audit_paths)
    _write_jsonl(
        audit_paths.get("cleanup"),
        [{
            "run_id": run_id,
            "date": date,
            "timestamp": now.isoformat(),
            "canceled_orders": n_canceled,
            "equity": portfolio.equity,
            "cash": portfolio.cash,
            "positions": [asdict(p) for p in portfolio.positions],
        }],
    )

    status: PipelineStatus = "OK" if not errors else "FAILED"
    return PipelineResult(
        run_id=run_id,
        date=date,
        mode="cleanup",
        status=status,
        n_intents=0, n_allow=0, n_block=0, n_degrade=0,
        n_sized=0, n_submitted=0,
        n_filled=n_canceled,        # repurposed: cancelations completed
        n_rejected=0,
        gross_risk_dollars=portfolio.gross_dollar_at_risk(),
        portfolio_equity=portfolio.equity,
        duration_seconds=round(time.monotonic() - started, 3),
        errors=errors,
        warnings=warnings,
    )


def reconcile(
    date: str,
    audit_dir: Optional[str] = None,
    *,
    connector: Any = None,
    intended: Optional[Dict[str, float]] = None,
    now: Optional[datetime] = None,
) -> PipelineResult:
    """Diff intended vs broker positions; flag mismatches.

    If ``intended`` is None we read the most recent ``04_orders.jsonl``
    under ``<audit_dir>/<date>/`` and reconstruct the intended
    position map from it. Mismatches are written to ``05_reconcile.jsonl``.
    """
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    run_id = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    errors: List[str] = []
    warnings: List[str] = []

    if connector is None:
        connector = _default_connector()

    audit_paths = _audit_paths(audit_dir, date)
    _ensure_audit_dir(audit_paths)

    if intended is None:
        intended = _intended_from_audit(audit_paths.get("orders"))

    try:
        diff = connector.reconcile(intended)
    except Exception as exc:
        errors.append(f"reconcile failed: {type(exc).__name__}: {exc}")
        diff = {}

    _write_jsonl(
        audit_paths.get("reconcile"),
        [{"run_id": run_id, "date": date, "timestamp": now.isoformat(),
          "diff": diff}],
    )

    portfolio_equity = 0.0
    try:
        portfolio_equity = float(connector.snapshot().equity or 0.0)
    except Exception as exc:
        warnings.append(f"snapshot during reconcile failed: {exc}")

    n_match = sum(1 for d in diff.values() if d.get("status") == "MATCH")
    n_mismatch = len(diff) - n_match
    status: PipelineStatus = "OK" if not errors and n_mismatch == 0 else (
        "PARTIAL" if not errors else "FAILED"
    )

    return PipelineResult(
        run_id=run_id,
        date=date,
        mode="reconcile",
        status=status,
        n_intents=len(intended),
        n_allow=n_match,
        n_block=0,
        n_degrade=0,
        n_sized=0,
        n_submitted=len(intended),
        n_filled=n_match,
        n_rejected=n_mismatch,
        gross_risk_dollars=0.0,
        portfolio_equity=portfolio_equity,
        duration_seconds=round(time.monotonic() - started, 3),
        errors=errors,
        warnings=warnings,
        per_stream_counts={
            "_diff": {
                "MATCH": n_match,
                "MISSING": sum(1 for d in diff.values() if d.get("status") == "MISSING"),
                "ORPHAN": sum(1 for d in diff.values() if d.get("status") == "ORPHAN"),
                "UNDER": sum(1 for d in diff.values() if d.get("status") == "UNDER"),
                "OVER": sum(1 for d in diff.values() if d.get("status") == "OVER"),
            }
        },
    )


# ───────────────────────────────────────────────────────────────────────────
# Default-dependency resolution (lazy)
# ───────────────────────────────────────────────────────────────────────────

def _default_connector():
    """Build an AlpacaConnector from environment. Deferred import so the
    pipeline module can be imported without the SDK present."""
    from compass.alpaca_connector import AlpacaConnector
    return AlpacaConnector.from_env()


def _default_signal_generator() -> Callable[[str], List[Dict]]:
    from compass.exp2690_signal_generators import generate_all_signals
    return generate_all_signals


def _default_gate_fn() -> Callable[..., List[GatedSignal]]:
    """Adapter binding the pipeline's abstract (intents, portfolio, date)
    contract onto ``EntryGate().evaluate(intents, portfolio, market, today)``.

    MarketContext fields (vix, vix3m, is_market_open) are read from the
    environment for the simple paper-runner; richer integrations should
    inject their own gate_fn via the ``gate_fn=`` kwarg on ``run()``.
    """
    from compass.orchestrator.entry_gate import EntryGate, MarketContext  # type: ignore

    gate = EntryGate()

    def _adapter(intents, portfolio, date_str):
        try:
            today = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            today = None
        ctx = MarketContext(
            now_utc=datetime.now(timezone.utc),
            is_market_open=True,
            vix=None, vix3m=None,
            broker_mode=os.environ.get("ORCHESTRATOR_BROKER_MODE", "alpaca_paper"),
        )
        return gate.evaluate(intents, portfolio, ctx, today=today)

    return _adapter


def _default_sizer_fn() -> Callable[..., List[SizedOrder]]:
    """Adapter binding (gated, portfolio, connector) onto
    ``size_orders(gated, portfolio, chain_fetcher, registry)``.

    The default chain_fetcher returns None for every intent, which causes
    the sizer to skip orders that require a live chain — useful for
    dry-run / replay where no live quote is available. Callers needing
    real chain data should inject ``sizer_fn=`` directly.
    """
    from compass.orchestrator.position_sizer import size_orders  # type: ignore

    def _no_chain(_intent, _params):
        return None

    def _adapter(gated, portfolio, _connector):
        return size_orders(gated, portfolio, _no_chain)

    return _adapter


def _default_router_fn() -> Callable[..., List[Any]]:
    """Adapter binding (sized, connector, submit) onto
    ``OrderRouter(connector).submit(sized)``.

    When ``submit=False`` (dry-run / replay) we skip the broker entirely
    and emit placeholder PENDING entries so the audit log still records
    intent. The router is instantiated lazily so unit tests that never
    take this code path don't need broker_capability.yaml on disk.
    """
    def _adapter(sized, connector, submit: bool = True):
        if not submit:
            return [
                {
                    "stream": s.gated.intent.stream,
                    "client_order_id": f"dryrun-{s.gated.intent.date}-{s.gated.intent.stream}",
                    "status": "PENDING",
                    "broker_order_id": "",
                    "legs": [],
                    "contract_count": s.contract_count,
                }
                for s in sized
            ]
        from compass.orchestrator.order_router import OrderRouter  # type: ignore
        broker_mode = os.environ.get("ORCHESTRATOR_BROKER_MODE", "alpaca_paper")
        router = OrderRouter(connector, broker_id=broker_mode)
        return router.submit(sized)

    return _adapter


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _load_portfolio(
    connector: Any,
    return_panel: Any,
    errors: List[str],
    warnings: List[str],
) -> PortfolioState:
    if connector is None:
        warnings.append("no connector — using empty portfolio snapshot")
        return PortfolioState._empty(error="no_connector")
    try:
        state = PortfolioState.load(connector, return_panel=return_panel)
        if not state.load_ok:
            errors.extend(state.errors)
        return state
    except Exception as exc:
        errors.append(f"portfolio load failed: {type(exc).__name__}: {exc}")
        return PortfolioState._empty(error=str(exc))


def _as_signal_intents(raw: Iterable[Any]) -> List[SignalIntent]:
    """Coerce raw EXP-2690 dicts (or already-typed SignalIntents) into
    a list of SignalIntent. Malformed rows are skipped with a log
    rather than aborting the run."""
    out: List[SignalIntent] = []
    for r in raw:
        if isinstance(r, SignalIntent):
            out.append(r)
            continue
        if not isinstance(r, dict):
            LOG.warning("skipping non-dict signal row: %r", r)
            continue
        try:
            out.append(SignalIntent.from_dict(r))
        except KeyError as exc:
            LOG.warning("skipping signal with missing field %s: %r", exc, r)
    return out


def _audit_paths(audit_dir: Optional[str], date: str) -> Dict[str, Path]:
    if not audit_dir:
        return {}
    root = Path(audit_dir) / date
    return {
        "root": root,
        "intent":    root / "01_intent.jsonl",
        "gated":     root / "02_gated.jsonl",
        "sized":     root / "03_sized.jsonl",
        "orders":    root / "04_orders.jsonl",
        "reconcile": root / "05_reconcile.jsonl",
        "cleanup":   root / "06_cleanup.jsonl",
    }


def _ensure_audit_dir(paths: Dict[str, Path]) -> None:
    root = paths.get("root")
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)


def _write_jsonl(path: Optional[Path], rows: List[Dict]) -> None:
    if path is None:
        return
    with path.open("a") as fp:
        for row in rows:
            fp.write(json.dumps(row, default=_json_default) + "\n")


def _json_default(o: Any) -> Any:
    if dataclasses.is_dataclass(o):
        return asdict(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def _gated_to_dict(g: GatedSignal) -> Dict:
    return {
        "intent": asdict(g.intent),
        "gate_status": g.gate_status,
        "gate_reasons": list(g.gate_reasons),
        "confidence_adj": g.confidence_adj,
    }


def _sized_to_dict(s: SizedOrder) -> Dict:
    return {
        "gated": _gated_to_dict(s.gated),
        "contract_count": s.contract_count,
        "risk_allocation": s.risk_allocation,
        "short_strike": s.short_strike,
        "long_strike": s.long_strike,
        "expected_credit": s.expected_credit,
        "max_loss_dollars": s.max_loss_dollars,
        "expiration": s.expiration,
        "port_weight_consumed": s.port_weight_consumed,
        "sizing_reasons": list(s.sizing_reasons),
    }


def _order_to_dict(o: Any) -> Dict:
    if dataclasses.is_dataclass(o):
        return asdict(o)
    if isinstance(o, dict):
        return o
    return {"repr": repr(o)}


def _order_status(o: Any) -> str:
    s = getattr(o, "status", None)
    if s is None and isinstance(o, dict):
        s = o.get("status")
    return (s or "").upper()


def _order_stream(o: Any) -> str:
    s = getattr(o, "stream", None)
    if s is None and isinstance(o, dict):
        s = o.get("stream")
    return s or "_unknown"


def _per_stream_counts(
    intents: List[SignalIntent],
    gated: List[GatedSignal],
    sized: List[SizedOrder],
    orders: List[Any],
) -> Dict[str, Dict[str, int]]:
    streams = (
        {i.stream for i in intents}
        | {g.intent.stream for g in gated}
        | {s.gated.intent.stream for s in sized}
        | {_order_stream(o) for o in orders}
    )
    out: Dict[str, Dict[str, int]] = {}
    for st in streams:
        out[st] = {
            "intents":   sum(1 for i in intents if i.stream == st),
            "allow":     sum(1 for g in gated if g.intent.stream == st and g.gate_status == "ALLOW"),
            "block":     sum(1 for g in gated if g.intent.stream == st and g.gate_status == "BLOCK"),
            "degrade":   sum(1 for g in gated if g.intent.stream == st and g.gate_status == "DEGRADE"),
            "sized":     sum(1 for s in sized if s.gated.intent.stream == st),
            "submitted": sum(1 for o in orders if _order_stream(o) == st and _order_status(o) in ("SUBMITTED", "FILLED", "PENDING")),
            "filled":    sum(1 for o in orders if _order_stream(o) == st and _order_status(o) == "FILLED"),
        }
    return out


def _intended_from_audit(orders_path: Optional[Path]) -> Dict[str, float]:
    """Reconstruct intended {symbol: signed_qty} from 04_orders.jsonl.

    Returns {} if the file is absent. Per-leg signed quantity:
        BUY  → +qty
        SELL → -qty
    """
    if orders_path is None or not orders_path.exists():
        return {}
    intended: Dict[str, float] = {}
    for line in orders_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        legs = row.get("legs") or []
        contract_count = row.get("contract_count") or row.get("qty") or 1
        for leg in legs:
            sym = leg.get("symbol") or leg.get("occ") or _leg_symbol(leg)
            if not sym:
                continue
            sign = -1.0 if str(leg.get("side", "")).upper() == "SELL" else 1.0
            qty = float(leg.get("quantity", 1)) * float(contract_count)
            intended[sym] = intended.get(sym, 0.0) + sign * qty
    return intended


def _leg_symbol(leg: Dict) -> Optional[str]:
    """Build an OCC symbol from a leg dict if the ticker/expiration/strike
    are present. Returns None otherwise."""
    try:
        from compass.alpaca_connector import build_occ_symbol
        return build_occ_symbol(
            leg["ticker"], leg["expiration"], float(leg["strike"]),
            leg["option_type"],
        )
    except Exception:
        return None


def _failed(
    *,
    run_id: str,
    date: str,
    mode: str,
    errors: List[str],
    duration_seconds: float,
    n_intents: int = 0,
    n_allow: int = 0,
    n_block: int = 0,
    n_degrade: int = 0,
    n_sized: int = 0,
    portfolio_equity: float = 0.0,
    gross_risk_dollars: float = 0.0,
) -> PipelineResult:
    return PipelineResult(
        run_id=run_id,
        date=date,
        mode=mode,
        status="FAILED",
        n_intents=n_intents,
        n_allow=n_allow,
        n_block=n_block,
        n_degrade=n_degrade,
        n_sized=n_sized,
        n_submitted=0,
        n_filled=0,
        n_rejected=0,
        gross_risk_dollars=gross_risk_dollars,
        portfolio_equity=portfolio_equity,
        duration_seconds=round(duration_seconds, 3),
        errors=list(errors),
        warnings=[],
    )


# ───────────────────────────────────────────────────────────────────────────
# Module-level alias for Atlas/Vesper convenience
# ───────────────────────────────────────────────────────────────────────────

#: Atlas integration guide refers to ``orchestrator_run`` as the canonical
#: entry symbol. Both names point at the same function.
orchestrator_run = run


__all__ = [
    "run",
    "cleanup",
    "reconcile",
    "orchestrator_run",
    "VALID_MODES",
]
