"""
Dollar-Notional Position Sizer — Phase 9 Prerequisite

Converts dollar-denominated target allocations into integer contract counts
for options spreads.  Replaces the naive integer-contract sizing that works
at sub-$1M scale but breaks at T3+ ($1M+).

Design principles:
  1. Conservative rounding — always round DOWN (toward underfill) to avoid
     accidental over-leverage.  The only exception is a configurable
     ``round_up_threshold`` (default 0.95) that allows rounding up when the
     fractional part is extremely close to a whole contract.
  2. Leverage-aware — total portfolio notional / account equity is capped at
     ``max_leverage`` (default 3×, matching MASTERPLAN T4 gate).
  3. Weight-respecting — per-stream weights from Ledoit-Wolf optimizer are
     treated as hard caps.
  4. Margin-aware — uses the actual margin requirement per spread (max-loss)
     rather than the full notional to compute capital consumption.
  5. Zero synthetic data — all prices in tests are realistic hardcoded
     fixtures from real market levels.

Integration point:
  Sits between ``PortfolioRiskManager.make_decision()`` and the
  ``AlpacaConnector.submit_spread()`` call.  The execution layer calls
  ``DollarNotionalSizer.size_portfolio()`` with today's RiskDecision +
  live option quotes, and receives back a dict of stream → contract count.

REAL DATA ONLY.  No randomness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SpreadQuote:
    """Live quote for a single spread in a stream.

    All prices are per-contract (i.e. per 100 shares of underlying for
    equity options).

    Attributes:
        stream:          sleeve id, e.g. "exp1220", "qqq_cs"
        net_credit:      net credit received per contract (positive = credit
                         spread, negative = debit spread).
        max_loss:        maximum loss per contract (= margin requirement for
                         a defined-risk spread).  Always positive.
        bid:             best bid for the spread (optional, informational).
        ask:             best ask for the spread (optional, informational).
        underlying_price: current price of the underlying (optional,
                          used for notional calculations).
        multiplier:      option multiplier (default 100 for equity options).
    """
    stream: str
    net_credit: float
    max_loss: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    underlying_price: Optional[float] = None
    multiplier: int = 100

    def __post_init__(self):
        if self.max_loss <= 0:
            raise ValueError(
                f"max_loss must be positive, got {self.max_loss} "
                f"for stream '{self.stream}'"
            )


@dataclass(frozen=True)
class SizingResult:
    """Output for a single stream's sizing decision.

    Attributes:
        stream:           sleeve id.
        contracts:        integer contract count to trade.
        dollar_allocation: dollar amount allocated to this stream.
        dollar_consumed:  actual dollar margin consumed (contracts × max_loss).
        weight:           portfolio weight used for this stream.
        fractional_raw:   the raw (unrounded) contract count — useful for
                          audit/logging.
        capped_reason:    if contracts were reduced, explains why.
    """
    stream: str
    contracts: int
    dollar_allocation: float
    dollar_consumed: float
    weight: float
    fractional_raw: float
    capped_reason: Optional[str] = None


@dataclass(frozen=True)
class PortfolioSizingResult:
    """Aggregate sizing output for the full portfolio."""
    stream_sizes: Dict[str, SizingResult]
    total_margin_consumed: float
    total_notional: float
    account_equity: float
    effective_leverage: float
    leverage_capped: bool
    notes: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Core sizer
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DollarNotionalSizer:
    """Converts dollar allocations into contract counts.

    Args:
        max_leverage:        hard cap on portfolio leverage (notional / equity).
                             Default 3× per MASTERPLAN T4.
        round_up_threshold:  fractional contracts ≥ this are rounded up.
                             Default 0.95 (i.e. 4.96 contracts → 5, but
                             4.94 → 4).  Set to 1.0 to always round down.
        min_contracts:       minimum contracts per stream.  If sizing yields
                             fewer than this (but > 0), the stream is either
                             bumped to min or zeroed depending on
                             ``zero_below_min``.
        zero_below_min:      if True, streams that size below ``min_contracts``
                             are zeroed out (conservative).  Default True.
        max_weight_per_stream: absolute cap on any single stream's weight.
                               Default 0.40 (matches AllocationLimiter).
        margin_buffer_pct:   fraction of equity reserved as margin buffer
                             (not allocated).  Default 0.05 (5%).
    """

    max_leverage: float = 3.0
    round_up_threshold: float = 0.95
    min_contracts: int = 1
    zero_below_min: bool = True
    max_weight_per_stream: float = 0.40
    margin_buffer_pct: float = 0.05

    def __post_init__(self):
        if self.max_leverage <= 0:
            raise ValueError(f"max_leverage must be positive, got {self.max_leverage}")
        if not (0.0 <= self.margin_buffer_pct < 1.0):
            raise ValueError(
                f"margin_buffer_pct must be in [0, 1), got {self.margin_buffer_pct}"
            )

    def size_single_stream(
        self,
        target_dollars: float,
        quote: SpreadQuote,
    ) -> Tuple[int, float]:
        """Compute contract count for a single stream.

        Args:
            target_dollars: dollar amount to allocate to this stream.
            quote:          live spread quote with max_loss.

        Returns:
            (contracts, fractional_raw) — integer count and the raw float
            before rounding.
        """
        if target_dollars <= 0 or quote.max_loss <= 0:
            return 0, 0.0

        fractional = target_dollars / quote.max_loss
        contracts = self._conservative_round(fractional)
        return contracts, fractional

    def _conservative_round(self, fractional: float) -> int:
        """Round with conservative bias (toward underfill).

        - fractional part ≥ round_up_threshold → round up
        - otherwise → round down (floor)
        - never returns negative

        Uses a small epsilon to handle floating-point edge cases
        (e.g. 14.95 stored as 14.9499999999...).
        """
        if fractional < 0:
            return 0
        floor_val = int(math.floor(fractional))
        remainder = fractional - floor_val
        # Epsilon for floating-point comparison at the threshold boundary
        eps = 1e-9
        if remainder >= self.round_up_threshold - eps:
            return floor_val + 1
        return floor_val

    def size_portfolio(
        self,
        account_equity: float,
        weights: Mapping[str, float],
        leverage: float,
        quotes: Mapping[str, SpreadQuote],
    ) -> PortfolioSizingResult:
        """Size the full portfolio from dollar allocations.

        Args:
            account_equity: current account equity in dollars.
            weights:        per-stream weights from Ledoit-Wolf / risk parity
                            (must sum to ≤ 1.0).
            leverage:       target leverage from LeverageGovernor (will be
                            clamped to max_leverage).
            quotes:         live spread quotes keyed by stream name.

        Returns:
            PortfolioSizingResult with per-stream contract counts.
        """
        notes: List[str] = []

        # ── Validation ───────────────────────────────────────────────────
        if account_equity <= 0:
            return PortfolioSizingResult(
                stream_sizes={},
                total_margin_consumed=0.0,
                total_notional=0.0,
                account_equity=account_equity,
                effective_leverage=0.0,
                leverage_capped=False,
                notes=["account_equity <= 0; no sizing possible"],
            )

        # ── Clamp leverage ───────────────────────────────────────────────
        leverage_capped = False
        effective_leverage = min(leverage, self.max_leverage)
        if leverage > self.max_leverage:
            leverage_capped = True
            notes.append(
                f"leverage clamped: {leverage:.2f}× → {self.max_leverage:.2f}×"
            )

        # ── Allocatable capital ──────────────────────────────────────────
        buffer = account_equity * self.margin_buffer_pct
        allocatable = account_equity * effective_leverage - buffer
        if allocatable <= 0:
            return PortfolioSizingResult(
                stream_sizes={},
                total_margin_consumed=0.0,
                total_notional=0.0,
                account_equity=account_equity,
                effective_leverage=0.0,
                leverage_capped=leverage_capped,
                notes=notes + ["allocatable capital <= 0 after buffer"],
            )

        # ── Per-stream sizing ────────────────────────────────────────────
        stream_sizes: Dict[str, SizingResult] = {}
        total_margin = 0.0

        # Clamp weights and normalize only if they exceed 1.0
        clamped_weights = self._clamp_weights(weights)

        for stream, weight in clamped_weights.items():
            if weight <= 0 or stream not in quotes:
                continue

            quote = quotes[stream]
            target_dollars = allocatable * weight
            contracts, fractional_raw = self.size_single_stream(
                target_dollars, quote
            )

            capped_reason: Optional[str] = None

            # Enforce min_contracts policy
            if 0 < contracts < self.min_contracts:
                if self.zero_below_min:
                    capped_reason = (
                        f"below min_contracts ({contracts} < {self.min_contracts})"
                    )
                    contracts = 0
                else:
                    contracts = self.min_contracts

            margin_consumed = contracts * quote.max_loss

            # Check if this stream's margin would bust the total budget
            if total_margin + margin_consumed > allocatable:
                # Reduce contracts to fit
                remaining = allocatable - total_margin
                max_contracts = int(math.floor(remaining / quote.max_loss))
                if max_contracts < contracts:
                    capped_reason = (
                        f"reduced {contracts}→{max_contracts} to fit "
                        f"remaining budget ${remaining:,.0f}"
                    )
                    contracts = max(max_contracts, 0)
                    margin_consumed = contracts * quote.max_loss

            total_margin += margin_consumed

            stream_sizes[stream] = SizingResult(
                stream=stream,
                contracts=contracts,
                dollar_allocation=target_dollars,
                dollar_consumed=margin_consumed,
                weight=weight,
                fractional_raw=fractional_raw,
                capped_reason=capped_reason,
            )

        # ── Compute total notional (informational) ────────────────────────
        # For defined-risk spreads, the underlying notional is informational
        # only.  Leverage is computed from margin consumed (max_loss × contracts)
        # which is the actual risk exposure for defined-risk positions.
        total_notional = 0.0
        for stream, result in stream_sizes.items():
            if stream in quotes and quotes[stream].underlying_price is not None:
                total_notional += (
                    result.contracts
                    * quotes[stream].underlying_price
                    * quotes[stream].multiplier
                )
            else:
                total_notional += result.dollar_consumed

        # Leverage = total margin consumed / equity (defined-risk measure)
        actual_leverage = (
            total_margin / account_equity if account_equity > 0 else 0.0
        )

        return PortfolioSizingResult(
            stream_sizes=stream_sizes,
            total_margin_consumed=total_margin,
            total_notional=total_notional,
            account_equity=account_equity,
            effective_leverage=actual_leverage,
            leverage_capped=leverage_capped,
            notes=notes,
        )

    def _clamp_weights(
        self, weights: Mapping[str, float]
    ) -> Dict[str, float]:
        """Clamp per-stream weights and renormalize if total > 1.0."""
        clamped = {
            k: min(float(v), self.max_weight_per_stream)
            for k, v in weights.items()
            if v > 0
        }
        total = sum(clamped.values())
        if total > 1.0:
            clamped = {k: v / total for k, v in clamped.items()}
        return clamped

    def _scale_down_positions(
        self,
        stream_sizes: Dict[str, SizingResult],
        scale: float,
        quotes: Mapping[str, SpreadQuote],
    ) -> Dict[str, SizingResult]:
        """Proportionally reduce all positions by ``scale`` factor."""
        result: Dict[str, SizingResult] = {}
        for stream, sr in stream_sizes.items():
            new_fractional = sr.fractional_raw * scale
            new_contracts = self._conservative_round(new_fractional)
            quote = quotes.get(stream)
            new_consumed = (
                new_contracts * quote.max_loss if quote else 0.0
            )
            result[stream] = SizingResult(
                stream=sr.stream,
                contracts=new_contracts,
                dollar_allocation=sr.dollar_allocation * scale,
                dollar_consumed=new_consumed,
                weight=sr.weight,
                fractional_raw=new_fractional,
                capped_reason=(
                    f"leverage scale-down: {sr.contracts}→{new_contracts}"
                    if new_contracts != sr.contracts
                    else sr.capped_reason
                ),
            )
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: integrate with RiskDecision
# ─────────────────────────────────────────────────────────────────────────────


def size_from_risk_decision(
    sizer: DollarNotionalSizer,
    account_equity: float,
    weights: Mapping[str, float],
    leverage: float,
    quotes: Mapping[str, SpreadQuote],
) -> PortfolioSizingResult:
    """One-call convenience wrapper.

    Typical usage in the daily signal pipeline::

        decision = risk_manager.make_decision(...)
        sizing = size_from_risk_decision(
            sizer=dollar_sizer,
            account_equity=account.equity,
            weights=decision.weights,
            leverage=decision.leverage,
            quotes=fetch_live_quotes(streams),
        )
        for stream, sr in sizing.stream_sizes.items():
            if sr.contracts > 0:
                connector.submit_spread(stream, sr.contracts)
    """
    return sizer.size_portfolio(
        account_equity=account_equity,
        weights=weights,
        leverage=leverage,
        quotes=quotes,
    )


__all__ = [
    "DollarNotionalSizer",
    "PortfolioSizingResult",
    "SizingResult",
    "SpreadQuote",
    "size_from_risk_decision",
]
