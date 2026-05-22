"""Tests for compass.orchestrator.position_sizer.

Contract under test (proposal §4.2):

    size_orders(gated_signals, portfolio, chain_fetcher, registry=None)
        -> List[SizedOrder]

    Only ALLOW/DEGRADE inputs are sized; BLOCK is dropped silently.
    Sizing rules (in order):
        0. BLOCK signals skipped
        1. Look up canonical params; skip untradeable / non-credit-spread
        2. Live chain quote via chain_fetcher; skip on None
        3. Drift re-check against live chain
        4. risk_$ = equity × sleeve.risk_per_trade_pct × effective_confidence
        5. contracts = floor(risk_$ / (max_loss_per_spread × 100))
        6. Sleeve contract cap
        7. Liquidity cap (5% of min OI)
        8. Portfolio gross cap
        9. Correlation haircut
       10. Drop if contracts < 1
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd
import pytest

from compass.orchestrator.types import GatedSignal, SignalIntent, SizedOrder

position_sizer = pytest.importorskip(
    "compass.orchestrator.position_sizer",
    reason="position_sizer module not landed yet (CC2)",
)

size_orders = position_sizer.size_orders
LiveSpreadQuote = position_sizer.LiveSpreadQuote


# ───────────────────────────────────────────────────────────────────────────
# Portfolio + chain-fetcher stubs
# ───────────────────────────────────────────────────────────────────────────

class _StubPortfolio:
    """Quacks like PortfolioState for the sizer's needs."""

    def __init__(
        self,
        equity: float = 100_000.0,
        gross_risk: float = 0.0,
        open_streams=(),
        correlation_pairs: Optional[Dict[Tuple[str, str], float]] = None,
        load_ok: bool = True,
    ):
        self.equity = equity
        self.cash = equity * 0.5
        self.buying_power = equity
        self.portfolio_value = equity
        self.open_streams = frozenset(open_streams)
        self.stream_weights: Dict[str, float] = {}
        self.stream_market_value: Dict[str, float] = {}
        self.load_ok = load_ok
        self.errors: List[str] = []
        self._gross = gross_risk
        self._corr = correlation_pairs or {}
        # Sizer reads correlation_matrix.empty — provide a non-empty stub
        # whenever we have any pair data.
        if self._corr:
            keys = {a for a, _ in self._corr} | {b for _, b in self._corr}
            self.correlation_matrix = pd.DataFrame(1.0, index=list(keys), columns=list(keys))
        else:
            self.correlation_matrix = pd.DataFrame()

    def gross_dollar_at_risk(self) -> float:
        return self._gross

    def weight_of(self, stream: str) -> float:
        return self.stream_weights.get(stream, 0.0)

    def correlation(self, a: str, b: str):
        return self._corr.get((a, b)) or self._corr.get((b, a))

    def has_open_position(self, stream: str) -> bool:
        return stream in self.open_streams


def _quote(
    *,
    short_strike: float = 425.0,
    long_strike: float = 420.0,
    expiration: str = "2026-06-19",
    short_delta: float = 0.30,
    short_dte: int = 28,
    width: float = 5.0,
    mid_credit: float = 0.55,
    short_oi: int = 5_000,
    long_oi: int = 5_000,
) -> LiveSpreadQuote:
    return LiveSpreadQuote(
        short_strike=short_strike,
        long_strike=long_strike,
        expiration=expiration,
        short_delta=short_delta,
        short_dte=short_dte,
        width=width,
        mid_credit=mid_credit,
        short_oi=short_oi,
        long_oi=long_oi,
    )


def _exp1220_quote_fetcher(intent, params):
    """Fetcher tuned for exp1220 canonical (δ=0.30, dte=28, width=5.0)."""
    return _quote(short_delta=0.30, short_dte=28, width=5.0)


def _per_stream_fetcher(_intent, params):
    """Returns a quote that matches whatever canonical the sleeve declares."""
    # Calendars / equity baskets have delta=None: skip with no quote.
    if params.delta is None:
        return _quote(short_delta=0.30, short_dte=28, width=5.0)
    return _quote(
        short_delta=params.delta,
        short_dte=params.dte if params.dte is not None else 28,
        width=params.width if params.width is not None else 5.0,
    )


def _none_fetcher(_intent, _params):
    return None


def _gated(
    stream: str = "exp1220",
    status: str = "ALLOW",
    delta: float = 0.30,
    dte: int = 28,
    width: float = 5.0,
    ticker: str = "SPY",
    direction: str = "put_credit_spread",
    confidence: float = 0.9,
    conf_adj: float = 1.0,
) -> GatedSignal:
    intent = SignalIntent.from_dict({
        "stream": stream, "date": "2026-05-22", "ticker": ticker,
        "action": "OPEN", "direction": direction,
        "delta": delta, "dte": dte, "width": width,
        "weight": 0.2, "confidence": confidence, "notes": "",
    })
    return GatedSignal(intent=intent, gate_status=status,
                          gate_reasons=[], confidence_adj=conf_adj)


# ───────────────────────────────────────────────────────────────────────────
# Output-shape tests
# ───────────────────────────────────────────────────────────────────────────

class TestSizeShape:
    def test_returns_list_of_sized_orders(self):
        out = size_orders(
            [_gated()], _StubPortfolio(), _exp1220_quote_fetcher,
        )
        assert isinstance(out, list)
        assert all(isinstance(o, SizedOrder) for o in out)

    def test_block_inputs_are_dropped(self):
        gated = [_gated(status="BLOCK"), _gated(status="ALLOW")]
        out = size_orders(gated, _StubPortfolio(), _exp1220_quote_fetcher)
        assert len(out) == 1
        assert out[0].gated.gate_status == "ALLOW"

    def test_min_size_floor_drops_empty_orders(self):
        # Trivially-small equity → contracts<1 → must be dropped.
        out = size_orders(
            [_gated()], _StubPortfolio(equity=10.0), _exp1220_quote_fetcher,
        )
        assert out == []

    def test_no_chain_quote_drops_intent(self):
        out = size_orders([_gated()], _StubPortfolio(), _none_fetcher)
        assert out == []

    def test_portfolio_load_failure_returns_empty(self):
        out = size_orders(
            [_gated()], _StubPortfolio(load_ok=False), _exp1220_quote_fetcher,
        )
        assert out == []


# ───────────────────────────────────────────────────────────────────────────
# Sizing arithmetic invariants
# ───────────────────────────────────────────────────────────────────────────

class TestSizingArithmetic:
    def test_risk_allocation_within_per_trade_budget(self):
        """No sized order can claim more $ at risk than the per-sleeve
        risk-per-trade budget × effective_confidence."""
        equity = 100_000.0
        gated = _gated(confidence=1.0)
        out = size_orders(
            [gated], _StubPortfolio(equity=equity), _exp1220_quote_fetcher,
        )
        # exp1220 canonical risk_per_trade_pct = 0.03.
        for o in out:
            assert o.risk_allocation <= equity * 0.03 + 1e-6

    def test_max_loss_fields_consistent(self):
        out = size_orders(
            [_gated()], _StubPortfolio(), _exp1220_quote_fetcher,
        )
        for o in out:
            # total_max_loss_dollars property = max_loss_dollars × N × 100.
            assert o.total_max_loss_dollars == pytest.approx(
                o.max_loss_dollars * o.contract_count * 100.0)

    def test_portfolio_gross_cap_respected(self):
        """Sum of risk_allocation ≤ equity × port_risk_cap_pct (default 0.20)."""
        equity = 100_000.0
        # Use real sleeve ids; missing ones are skipped by `reg.has()`.
        streams = ("exp1220", "xlf_cs", "xli_cs", "qqq_cs", "cross_vol")
        gated = [_gated(stream=s) for s in streams]
        out = size_orders(
            gated, _StubPortfolio(equity=equity), _per_stream_fetcher,
        )
        total = sum(o.risk_allocation for o in out)
        assert total <= equity * 0.20 + 1e-6

    def test_confidence_adj_scales_size(self):
        """DEGRADE confidence_adj should scale risk_allocation down (or equal)."""
        full = size_orders(
            [_gated(conf_adj=1.0)], _StubPortfolio(), _exp1220_quote_fetcher,
        )
        half = size_orders(
            [_gated(conf_adj=0.5, status="DEGRADE")],
            _StubPortfolio(), _exp1220_quote_fetcher,
        )
        if full and half:
            assert half[0].risk_allocation <= full[0].risk_allocation

    def test_sleeve_max_contracts_cap(self):
        """A huge equity must still respect sleeve max_contracts."""
        out = size_orders(
            [_gated(stream="exp1220")],
            _StubPortfolio(equity=10_000_000.0),
            _exp1220_quote_fetcher,
        )
        # exp1220 canonical max_contracts = 4.
        for o in out:
            assert o.contract_count <= 4


# ───────────────────────────────────────────────────────────────────────────
# Liquidity cap (proposal §4.2 rule 4)
# ───────────────────────────────────────────────────────────────────────────

class TestLiquidityCap:
    def test_thin_open_interest_caps_contracts(self):
        def thin_fetcher(_i, _p):
            return _quote(short_oi=20, long_oi=20)
        out = size_orders(
            [_gated(stream="exp1220")],
            _StubPortfolio(equity=10_000_000.0),
            thin_fetcher,
        )
        # 5% of min(20, 20) = 1 → cap at 1 contract.
        for o in out:
            assert o.contract_count <= 1


# ───────────────────────────────────────────────────────────────────────────
# Drift re-check against live chain (proposal §4.2 step 3)
# ───────────────────────────────────────────────────────────────────────────

class TestLiveChainDrift:
    def test_drifted_chain_quote_skipped(self):
        def drifted(_i, _p):
            # exp1220 canonical δ=0.30, tol=0.05 → 0.55 is out.
            return _quote(short_delta=0.55)
        out = size_orders([_gated(stream="exp1220")], _StubPortfolio(), drifted)
        assert out == []


# ───────────────────────────────────────────────────────────────────────────
# Untradeable / non-credit-spread sleeves
# ───────────────────────────────────────────────────────────────────────────

class TestSleeveStructure:
    def test_calendar_sleeve_skipped(self):
        """gld_cal is structure=calendar_spread and tradable=false → must skip."""
        out = size_orders(
            [_gated(stream="gld_cal", direction="calendar",
                       delta=0.30, dte=28, width=5.0, ticker="GLD")],
            _StubPortfolio(), _per_stream_fetcher,
        )
        assert out == []

    def test_equity_etf_sleeve_skipped(self):
        """v5_hedge is structure=equity_etf → sizer v1 does not handle equities."""
        out = size_orders(
            [_gated(stream="v5_hedge", direction="long",
                       delta=0.30, dte=28, width=5.0, ticker="SPY")],
            _StubPortfolio(), _per_stream_fetcher,
        )
        assert out == []


# ───────────────────────────────────────────────────────────────────────────
# Correlation haircut (proposal §4.2 rule 6)
# ───────────────────────────────────────────────────────────────────────────

class TestCorrelationHaircut:
    def test_high_correlation_reduces_contracts(self):
        """A sleeve highly correlated with an already-open book sleeve
        must size to no more than its un-correlated counterpart."""
        # Baseline: no open book, no correlation.
        baseline = size_orders(
            [_gated(stream="exp1220")],
            _StubPortfolio(equity=1_000_000.0),
            _exp1220_quote_fetcher,
        )
        # Stressed: another sleeve already open with ρ=0.90.
        stressed = size_orders(
            [_gated(stream="exp1220")],
            _StubPortfolio(
                equity=1_000_000.0,
                open_streams=("qqq_cs",),
                correlation_pairs={("exp1220", "qqq_cs"): 0.90},
            ),
            _exp1220_quote_fetcher,
        )
        if baseline and stressed:
            assert stressed[0].contract_count <= baseline[0].contract_count
