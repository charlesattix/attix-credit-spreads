"""compass/orchestrator/portfolio_state.py — Live portfolio snapshot used by the orchestrator.

The entry_gate and position_sizer both need a coherent view of "what does
the book look like RIGHT NOW?" — equity, cash, open positions, per-sleeve
weights, and the correlation matrix between sleeves. This module is the
single source of truth for that snapshot, derived from one Alpaca
`snapshot()` call plus optional historical-return inputs.

Design points
-------------
- One snapshot per orchestrator run. The state is immutable; downstream
  modules read but do not mutate.
- The Alpaca call is performed once at construction (`PortfolioState.load`).
  Any failure surfaces in `errors` rather than raising — the entry_gate
  fails closed (BLOCK everything) if `load_ok` is False.
- Stream attribution re-uses `alpaca_connector._infer_stream` to keep one
  canonical OCC-symbol → sleeve mapping in the codebase.
- The correlation matrix is OPTIONAL. If a 5-stream return panel is
  provided (e.g. EXP-2080's load_streams() output, or a rolling 60-day
  window of pooled OOS returns), we compute the empirical correlation.
  If not, callers receive an empty matrix and the position_sizer skips
  the correlation haircut.

See ORCHESTRATOR_PROPOSAL.md §3.3 + §4.2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from compass.alpaca_connector import (
    AccountSnapshot,
    AlpacaConnector,
    Position,
    _infer_stream,
)

LOG = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# PortfolioState
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PortfolioState:
    """Snapshot of the live book at the moment the orchestrator runs.

    Attributes
    ----------
    timestamp           ISO-8601 UTC, when the Alpaca snapshot was taken.
    equity              Total account equity, $.
    cash                Cash available, $.
    buying_power        Buying power reported by the broker, $.
    portfolio_value     Portfolio value (positions market value + cash), $.
    positions           List[Position] from alpaca_connector.snapshot().
    open_streams        set of sleeve ids that already have ≥ 1 open
                        position. The entry_gate uses this to enforce
                        "one open spread per sleeve".
    stream_market_value Per-sleeve absolute market value, $.
    stream_unrealized_pl Per-sleeve unrealized P&L, $.
    stream_weights      Per-sleeve weight as a fraction of |portfolio_value|.
                        Sums to ≤ 1; the residual is cash.
    correlation_matrix  pd.DataFrame indexed and columned by sleeve id;
                        empty if no return panel was provided to load().
    pending_orders      Open broker orders (one dict per order).
    load_ok             True iff the Alpaca snapshot returned without error.
    errors              List of error strings (empty if load_ok).
    """

    timestamp: str
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    positions: List[Position]
    open_streams: frozenset
    stream_market_value: Dict[str, float]
    stream_unrealized_pl: Dict[str, float]
    stream_weights: Dict[str, float]
    correlation_matrix: pd.DataFrame
    pending_orders: List[Dict]
    load_ok: bool
    errors: List[str] = field(default_factory=list)

    # ── factory ─────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        connector: AlpacaConnector,
        return_panel: Optional[pd.DataFrame] = None,
        corr_window_days: int = 60,
    ) -> "PortfolioState":
        """Pull one snapshot from Alpaca and build the state.

        Parameters
        ----------
        connector:      live AlpacaConnector handle.
        return_panel:   optional DataFrame of daily returns, indexed by
                        date, columned by sleeve id. The trailing
                        `corr_window_days` rows are used for the
                        correlation matrix. Pass None to skip
                        correlation computation (matrix is empty).
        corr_window_days: trailing window for the correlation matrix
                          (default 60 trading days).
        """
        snap: AccountSnapshot = connector.snapshot()

        if snap.raw_error:
            LOG.error("portfolio snapshot failed: %s", snap.raw_error)
            return cls._empty(error=snap.raw_error)

        stream_mv = _aggregate_by_stream(snap.positions, "market_value")
        stream_pl = _aggregate_by_stream(snap.positions, "unrealized_pl")
        weights = _compute_weights(stream_mv, snap.portfolio_value)
        open_streams = frozenset(
            s for s, mv in stream_mv.items() if abs(mv) > 1e-9
        )

        corr = _build_correlation(return_panel, corr_window_days)

        return cls(
            timestamp=snap.timestamp,
            equity=snap.equity,
            cash=snap.cash,
            buying_power=snap.buying_power,
            portfolio_value=snap.portfolio_value,
            positions=snap.positions,
            open_streams=open_streams,
            stream_market_value=stream_mv,
            stream_unrealized_pl=stream_pl,
            stream_weights=weights,
            correlation_matrix=corr,
            pending_orders=snap.pending_orders,
            load_ok=True,
            errors=[],
        )

    @classmethod
    def _empty(cls, error: str) -> "PortfolioState":
        """Construct a fail-closed snapshot (used when the Alpaca call errors)."""
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            equity=0.0,
            cash=0.0,
            buying_power=0.0,
            portfolio_value=0.0,
            positions=[],
            open_streams=frozenset(),
            stream_market_value={},
            stream_unrealized_pl={},
            stream_weights={},
            correlation_matrix=pd.DataFrame(),
            pending_orders=[],
            load_ok=False,
            errors=[error],
        )

    # ── query helpers (used by entry_gate / position_sizer) ─────────────

    def has_open_position(self, stream: str) -> bool:
        """True if `stream` already has at least one open position."""
        return stream in self.open_streams

    def weight_of(self, stream: str) -> float:
        """Current weight of `stream` as a fraction of |portfolio_value|.
        Returns 0.0 if the sleeve has no open positions."""
        return self.stream_weights.get(stream, 0.0)

    def market_value_of(self, stream: str) -> float:
        return self.stream_market_value.get(stream, 0.0)

    def correlation(self, stream_a: str, stream_b: str) -> Optional[float]:
        """Pairwise correlation between two sleeves, or None if either is
        missing from the correlation matrix."""
        corr = self.correlation_matrix
        if corr.empty or stream_a not in corr.index or stream_b not in corr.columns:
            return None
        return float(corr.at[stream_a, stream_b])

    def gross_dollar_at_risk(self) -> float:
        """Sum of absolute market values across all sleeves — a proxy for
        gross dollar at risk. (For options spreads this approximates
        gross exposure; the precise per-spread max-loss view lives in
        the position_sizer.)"""
        return sum(abs(v) for v in self.stream_market_value.values())

    def gross_risk_pct(self) -> float:
        """Gross dollar at risk as a fraction of equity."""
        if self.equity <= 0:
            return 0.0
        return self.gross_dollar_at_risk() / self.equity


# ───────────────────────────────────────────────────────────────────────────
# Internal helpers
# ───────────────────────────────────────────────────────────────────────────

def _aggregate_by_stream(positions: List[Position], attr: str) -> Dict[str, float]:
    """Sum a numeric Position attribute by sleeve id.

    Uses the position's stream_attribution if already set by the connector,
    else re-runs the OCC → sleeve inference.
    """
    out: Dict[str, float] = {}
    for p in positions:
        sleeve = p.stream_attribution or _infer_stream(p.symbol)
        out[sleeve] = out.get(sleeve, 0.0) + float(getattr(p, attr, 0.0) or 0.0)
    return out


def _compute_weights(
    stream_mv: Dict[str, float], portfolio_value: float
) -> Dict[str, float]:
    """Per-sleeve weight = |market_value| / |portfolio_value|.

    Uses absolute values so short legs contribute positively to gross
    weight. Returns {} when portfolio_value is zero or negative.
    """
    if portfolio_value is None or abs(portfolio_value) < 1e-9:
        return {}
    denom = abs(portfolio_value)
    return {s: abs(mv) / denom for s, mv in stream_mv.items()}


def _build_correlation(
    return_panel: Optional[pd.DataFrame], window_days: int
) -> pd.DataFrame:
    """Empirical correlation over the trailing `window_days` of returns.

    Returns an empty DataFrame if no panel is supplied or the panel has
    fewer than `window_days` rows.
    """
    if return_panel is None or return_panel.empty:
        return pd.DataFrame()
    if len(return_panel) < window_days:
        LOG.warning(
            "return panel has %d rows (< %d) — correlation matrix skipped",
            len(return_panel), window_days,
        )
        return pd.DataFrame()
    tail = return_panel.tail(window_days)
    # corr() ignores NaN per-pair; std of constant-zero columns is 0 and
    # would yield NaN — coerce those to 0 so callers can rely on a
    # fully-populated matrix.
    corr = tail.corr().fillna(0.0)
    # Clip numerical noise into [-1, 1].
    return corr.clip(lower=-1.0, upper=1.0)
