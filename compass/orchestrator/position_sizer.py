"""compass/orchestrator/position_sizer.py — GatedSignal → SizedOrder.

The sizer is the layer that turns a *gated intent* into a *number of
contracts*. It enforces per-sleeve risk-per-trade, the portfolio gross
cap, broker liquidity limits, and a correlation haircut so two
positively-correlated sleeves don't both fully load the book on the
same day.

Pipeline (proposal §4.2) — applied in order, per intent:
    0. BLOCK signals are skipped.
    1. Look up canonical params for the sleeve. Skip if missing /
       untradeable / not a credit-spread structure (v1 scope).
    2. Resolve a live chain quote via `chain_fetcher`.
    3. Re-check canonical drift against the live chain. Skip on drift.
    4. risk_$ = equity × sleeve.risk_per_trade_pct × effective_confidence
    5. contracts = floor(risk_$ / (max_loss_per_spread × 100))
    6. contracts = min(contracts, sleeve.max_contracts)
    7. liquidity cap: contracts ≤ 5% × min(short_oi, long_oi)
    8. portfolio gross cap: cumulative dollar-at-risk ≤ equity × port_risk_cap_pct
    9. correlation haircut from PortfolioState.correlation_matrix
   10. Drop if contracts < 1.

The sizer is *pure* — no broker calls, no I/O beyond reading the chain
through the injected callable. That keeps it deterministic and unit
testable against hand-computed golden masters.

Notes on the correlation matrix
-------------------------------
PortfolioState.correlation_matrix is built from an empirical .corr() of
the per-sleeve return panel by default. If you want a shrinkage-style
estimator (Ledoit-Wolf, Oracle Approximating, etc.), substitute it in
PortfolioState.load — the sizer just reads whatever ρ values the
matrix exposes via `portfolio.correlation(a, b)`.

See ORCHESTRATOR_PROPOSAL.md §4.2.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, List, Optional

from compass.orchestrator.canonical_params import (
    CanonicalParams,
    CanonicalRegistry,
    PortfolioParams,
    load_canonical_params,
)
from compass.orchestrator.portfolio_state import PortfolioState
from compass.orchestrator.types import GatedSignal, SignalIntent, SizedOrder

LOG = logging.getLogger(__name__)

# Liquidity rule: never take more than 5% of the lesser-side open interest.
_LIQUIDITY_FRAC = 0.05


# ───────────────────────────────────────────────────────────────────────────
# Live-chain quote contract
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiveSpreadQuote:
    """Chain-resolved view of a single spread, returned by the
    `chain_fetcher` callable injected into `size_orders`.

    All prices are per-contract in dollars (1.25 == $125 / contract).
    """

    short_strike: float
    long_strike: float
    expiration: str            # ISO date YYYY-MM-DD
    short_delta: float         # actual short-leg delta from chain
    short_dte: int             # actual short-leg DTE
    width: float               # |short − long| in dollars
    mid_credit: float          # net credit, per contract
    short_oi: int              # open interest at short strike
    long_oi: int               # open interest at long strike

    @property
    def max_loss_per_contract(self) -> float:
        """Worst-case loss per contract = width − credit (in dollars)."""
        return max(self.width - self.mid_credit, 0.0)


# Signature for the chain-fetcher callable. The fetcher receives both the
# signal intent (so it knows which sleeve / ticker / direction) and the
# resolved canonical params (so it knows the target delta / DTE / width
# to look up on the live chain).
ChainFetcher = Callable[[SignalIntent, CanonicalParams], Optional[LiveSpreadQuote]]


# ───────────────────────────────────────────────────────────────────────────
# Public entry point
# ───────────────────────────────────────────────────────────────────────────

def size_orders(
    gated_signals: List[GatedSignal],
    portfolio: PortfolioState,
    chain_fetcher: ChainFetcher,
    registry: Optional[CanonicalRegistry] = None,
) -> List[SizedOrder]:
    """Convert a list of gated intents into sized orders.

    The remaining dollar-at-risk budget is shared across all orders in
    the batch, so the portfolio gross cap is enforced over the whole
    run rather than per-order in isolation.

    Parameters
    ----------
    gated_signals:  output of entry_gate.evaluate(). BLOCK signals are
                    silently dropped; ALLOW / DEGRADE are sized.
    portfolio:      live PortfolioState (one snapshot per run).
    chain_fetcher:  callable returning a LiveSpreadQuote (or None) for
                    a given intent + canonical params pair. The sizer
                    never touches the broker directly.
    registry:       optional pre-loaded CanonicalRegistry. Default loads
                    from compass/orchestrator/canonical_params.yaml.

    Returns
    -------
    A list of SizedOrder in the same order as the input, minus any
    intents that were skipped or sized to zero. Each SizedOrder carries
    a `sizing_reasons` audit trail documenting which rules bound.
    """
    reg = registry or load_canonical_params()
    port = reg.portfolio

    if not portfolio.load_ok:
        LOG.error(
            "sizer: portfolio.load_ok=False (errors=%s) — refusing to size any orders",
            portfolio.errors,
        )
        return []
    if portfolio.equity <= 0:
        LOG.error("sizer: equity=%.2f ≤ 0 — refusing to size any orders", portfolio.equity)
        return []

    used_risk = portfolio.gross_dollar_at_risk()
    total_cap = portfolio.equity * port.port_risk_cap_pct
    remaining_cap_at_start = max(0.0, total_cap - used_risk)

    LOG.info(
        "sizing batch: equity=$%.0f used_risk=$%.0f cap=$%.0f remaining=$%.0f n_intents=%d",
        portfolio.equity, used_risk, total_cap, remaining_cap_at_start, len(gated_signals),
    )

    out: List[SizedOrder] = []
    running_added = 0.0

    for gs in gated_signals:
        if gs.gate_status == "BLOCK":
            continue
        if not reg.has(gs.intent.stream):
            LOG.warning(
                "sizer: no canonical params for sleeve %s — skipping intent",
                gs.intent.stream,
            )
            continue

        params = reg.get(gs.intent.stream)
        sized = _size_one(
            gated=gs,
            params=params,
            portfolio=portfolio,
            chain_fetcher=chain_fetcher,
            port_params=port,
            remaining_cap=remaining_cap_at_start - running_added,
        )
        if sized is None:
            continue
        out.append(sized)
        running_added += sized.risk_allocation

    LOG.info(
        "sizing batch complete: %d / %d intents sized; gross_risk_added=$%.0f",
        len(out), len(gated_signals), running_added,
    )
    return out


# ───────────────────────────────────────────────────────────────────────────
# Per-intent sizing
# ───────────────────────────────────────────────────────────────────────────

def _size_one(
    gated: GatedSignal,
    params: CanonicalParams,
    portfolio: PortfolioState,
    chain_fetcher: ChainFetcher,
    port_params: PortfolioParams,
    remaining_cap: float,
) -> Optional[SizedOrder]:
    """Size a single gated intent. Returns None when the intent is
    skipped or sizes to zero contracts."""
    intent = gated.intent
    reasons: List[str] = []

    # ── Tradability / structure gates ─────────────────────────────────
    if not params.tradable:
        LOG.info(
            "sizer: %s untradeable (%s) — skip",
            intent.stream, params.untradeable_reason or "disabled",
        )
        return None
    if not params.is_credit_spread:
        LOG.info(
            "sizer: %s structure=%s not handled by v1 sizer — skip",
            intent.stream, params.structure,
        )
        return None

    # ── Live chain lookup ─────────────────────────────────────────────
    try:
        quote = chain_fetcher(intent, params)
    except Exception as e:  # broker / data layer failures are non-fatal
        LOG.warning("sizer: chain_fetcher raised for %s: %s", intent.stream, e)
        return None
    if quote is None:
        LOG.info("sizer: no chain quote for %s — skip", intent.stream)
        return None

    # ── Defensive drift re-check against the live chain ───────────────
    drift = _quote_drift_violations(quote, params)
    if drift:
        LOG.warning(
            "sizer: live-chain drift for %s: %s — skip",
            intent.stream, "; ".join(drift),
        )
        return None

    max_loss_per_contract = quote.max_loss_per_contract
    if max_loss_per_contract <= 0:
        LOG.warning(
            "sizer: %s zero/negative max_loss (width=$%.2f credit=$%.2f) — skip",
            intent.stream, quote.width, quote.mid_credit,
        )
        return None
    max_loss_dollars_per = max_loss_per_contract * 100.0  # contract multiplier

    # ── 1. Per-sleeve risk-per-trade × effective confidence ───────────
    eff_conf = _clamp(gated.effective_confidence, 0.0, 1.0)
    sleeve_risk_budget = portfolio.equity * params.risk_per_trade_pct * eff_conf
    if sleeve_risk_budget <= 0:
        LOG.info(
            "sizer: %s zero sleeve risk budget (conf=%.3f, rpt=%.4f) — skip",
            intent.stream, eff_conf, params.risk_per_trade_pct,
        )
        return None

    # ── 2. Contracts from max-loss ────────────────────────────────────
    contracts = int(math.floor(sleeve_risk_budget / max_loss_dollars_per))
    reasons.append(
        f"sleeve_risk=${sleeve_risk_budget:,.0f} ÷ max_loss=${max_loss_dollars_per:,.0f} "
        f"= {contracts}"
    )

    # ── 3. Per-sleeve contract cap ────────────────────────────────────
    if contracts > params.max_contracts:
        reasons.append(f"capped at sleeve max_contracts={params.max_contracts}")
        contracts = params.max_contracts

    # ── 4. Liquidity cap ──────────────────────────────────────────────
    min_oi = max(0, min(int(quote.short_oi), int(quote.long_oi)))
    liq_cap = int(math.floor(_LIQUIDITY_FRAC * min_oi))
    if contracts > liq_cap:
        reasons.append(
            f"liquidity capped at {liq_cap} (5% of min OI {min_oi})"
        )
        contracts = liq_cap

    # ── 5. Portfolio gross cap ────────────────────────────────────────
    if contracts > 0:
        projected = contracts * max_loss_dollars_per
        if projected > remaining_cap:
            feasible = (
                int(math.floor(remaining_cap / max_loss_dollars_per))
                if max_loss_dollars_per > 0 else 0
            )
            reasons.append(
                f"portfolio cap binds: remaining=${remaining_cap:,.0f} "
                f"→ {feasible} contracts"
            )
            contracts = max(0, feasible)

    # ── 6. Correlation haircut ────────────────────────────────────────
    if contracts > 0:
        scale, avg_rho = _correlation_scale(
            stream=intent.stream,
            portfolio=portfolio,
            threshold=port_params.corr_threshold,
            min_scale=port_params.corr_min_scale,
        )
        if scale < 1.0:
            new_contracts = int(math.floor(contracts * scale))
            reasons.append(
                f"correlation scale={scale:.2f} (avg ρ={avg_rho:.2f}) "
                f"→ {contracts}→{new_contracts}"
            )
            contracts = new_contracts

    # ── 7. Minimum floor ──────────────────────────────────────────────
    if contracts < 1:
        LOG.info(
            "sizer: %s ended at %d contracts (<1) — drop. trail: %s",
            intent.stream, contracts, "; ".join(reasons),
        )
        return None

    final_risk = contracts * max_loss_dollars_per
    port_weight = (final_risk / portfolio.equity) if portfolio.equity > 0 else 0.0

    return SizedOrder(
        gated=gated,
        contract_count=contracts,
        risk_allocation=final_risk,
        short_strike=quote.short_strike,
        long_strike=quote.long_strike,
        expected_credit=quote.mid_credit,
        max_loss_dollars=max_loss_per_contract,
        expiration=quote.expiration,
        port_weight_consumed=port_weight,
        sizing_reasons=reasons,
    )


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _quote_drift_violations(
    quote: LiveSpreadQuote, params: CanonicalParams
) -> List[str]:
    """Compare a resolved chain quote to canonical params; return drift lines."""
    violations: List[str] = []
    if params.delta is not None:
        d = abs(quote.short_delta - params.delta)
        if d > params.delta_tol:
            violations.append(
                f"delta={quote.short_delta:.3f} vs canonical {params.delta:.3f} "
                f"(|Δ|={d:.3f} > tol={params.delta_tol})"
            )
    if params.dte is not None:
        d = abs(int(quote.short_dte) - params.dte)
        if d > params.dte_tol:
            violations.append(
                f"dte={quote.short_dte} vs canonical {params.dte} "
                f"(|Δ|={d} > tol={params.dte_tol})"
            )
    if params.width is not None:
        d = abs(float(quote.width) - params.width)
        if d > params.width_tol:
            violations.append(
                f"width={quote.width:.2f} vs canonical {params.width:.2f} "
                f"(|Δ|={d:.2f} > tol={params.width_tol})"
            )
    return violations


def _correlation_scale(
    stream: str,
    portfolio: PortfolioState,
    threshold: float,
    min_scale: float,
) -> tuple:
    """Markowitz-style haircut on contract count.

    Multiplier in [min_scale, 1.0]:
        avg ρ ≤ threshold   →  1.0 (no haircut)
        avg ρ = 1.0         →  min_scale
        otherwise           →  linear interpolation between threshold→1.0
                               and 1.0→min_scale.

    Returns (scale, avg_rho). When the matrix is empty or the sleeve has
    no peers in the book, returns (1.0, 0.0).
    """
    if not portfolio.open_streams:
        return 1.0, 0.0
    corr_matrix = portfolio.correlation_matrix
    if corr_matrix is None or getattr(corr_matrix, "empty", True):
        return 1.0, 0.0

    pairwise: List[float] = []
    for s in portfolio.open_streams:
        if s == stream:
            continue
        rho = portfolio.correlation(stream, s)
        if rho is None:
            continue
        pairwise.append(float(rho))

    if not pairwise:
        return 1.0, 0.0

    avg_rho = sum(pairwise) / len(pairwise)
    if avg_rho <= threshold:
        return 1.0, avg_rho

    span = max(1.0 - threshold, 1e-9)
    frac = min(1.0, (avg_rho - threshold) / span)
    scale = max(min_scale, 1.0 - (1.0 - min_scale) * frac)
    return scale, avg_rho


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
