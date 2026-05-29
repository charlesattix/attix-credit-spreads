"""compass/live/vrp_streams.py — per-stream entry-signal generators (PR-B).

Each VRP stream is a :class:`~compass.live.vrp_contracts.StreamSignalGenerator`
that turns a live :class:`~compass.live.vrp_data.VRPSnapshot` + a capital budget
(from cc3's allocator) into zero or more :class:`OrderIntent` objects.

PR-B fully implements the four **credit-spread** streams (the build-plan PR-B
scope: ``exp1220``→SPY, ``xlf_cs``→XLF, ``xli_cs``→XLI, ``qqq_cs``→QQQ), reusing
the EXP-1220/EXP-2240 entry parameters (≈5%-OTM short put, $5-wide, 28–30 DTE,
VIX<40 gate) that produced the backtest streams. Exits (PT/SL/roll) are NOT here
— they belong to the multi-symbol PositionMonitor (build-plan PR-H).

The other four streams are registered with honest, non-trading status:
  * ``v5_hedge`` / ``cross_vol`` → **DEFERRED** (signal port is build-plan PR-D).
  * ``gld_cal`` / ``slv_cal``    → **BLOCKED** (ETF-vs-futures basis; Alpaca has
    no futures — recon cc2 B1).

This keeps the engine complete for all 8 streams while being truthful about what
can actually trade today. The allocator re-normalizes over the streams that
actually emit intents.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

import pandas as pd

from compass.live.vrp_contracts import (
    STREAM_SPECS,
    OrderIntent,
    OrderLeg,
    StreamResult,
    StreamSpec,
    StreamStatus,
)
from compass.live.vrp_data import VRPSnapshot

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100  # US equity options


# ── chain helpers (operate on cc2's vrp_data chain schema) ────────────────────

def _mid(row: pd.Series) -> Optional[float]:
    """Mid price for a chain row: prefer ``mid``, else (bid+ask)/2, else None."""
    m = row.get("mid")
    if m is not None and pd.notna(m) and float(m) > 0:
        return float(m)
    bid, ask = row.get("bid"), row.get("ask")
    if bid is not None and ask is not None and pd.notna(bid) and pd.notna(ask):
        b, a = float(bid), float(ask)
        if b > 0 and a > 0:
            return (b + a) / 2.0
    return None


def _is_put(value: object) -> bool:
    s = str(value).strip().lower()
    return s in ("put", "p")


def _as_date(value: object) -> Optional[datetime]:
    try:
        ts = pd.Timestamp(value)
        return ts.to_pydatetime()
    except (ValueError, TypeError):
        return None


def _select_expiration(puts: pd.DataFrame, as_of: datetime, target_dte: int) -> Optional[object]:
    """Pick the expiration in the chain whose DTE is closest to ``target_dte``."""
    if puts.empty or "expiration" not in puts.columns:
        return None
    best_exp, best_gap = None, None
    for exp in puts["expiration"].dropna().unique():
        ed = _as_date(exp)
        if ed is None:
            continue
        dte = (ed.date() - as_of.date()).days
        if dte <= 0:
            continue
        gap = abs(dte - target_dte)
        if best_gap is None or gap < best_gap:
            best_exp, best_gap = exp, gap
    return best_exp


# ── credit-spread stream (TRADEABLE) ──────────────────────────────────────────

class CreditSpreadStream:
    """Bull-put credit-spread entry generator for a single underlying.

    Parameters mirror the EXP-1220/EXP-2240 streams that produced the backtest
    cube: short put ≈ ``otm_pct`` of spot, ``width``-wide, nearest ``target_dte``,
    require ``min_credit``, block new entries when VIX > ``vix_max_entry``.

    Sizing comes from the allocator's per-stream ``capital`` (dollars): contracts
    = floor(capital / max_loss_per_spread). The VIX *ladder* already scaled that
    capital upstream; this gate is the per-stream hard VIX block, and the separate
    live circuit breaker (VIX≥35) sits above both (recon cc4 §3.2: CB > ladder).
    """

    def __init__(
        self,
        spec: StreamSpec,
        *,
        otm_pct: float = 0.95,
        width: float = 5.0,
        target_dte: int = 30,
        min_credit: float = 0.05,
        vix_max_entry: float = 40.0,
        strike_tol: float = 1.0,
    ) -> None:
        if spec.status is not StreamStatus.TRADEABLE:
            raise ValueError(f"CreditSpreadStream requires TRADEABLE spec, got {spec.status} for {spec.stream_id}")
        self.spec = spec
        self.symbol = spec.symbols[0]
        self.otm_pct = otm_pct
        self.width = width
        self.target_dte = target_dte
        self.min_credit = min_credit
        self.vix_max_entry = vix_max_entry
        self.strike_tol = strike_tol

    @property
    def stream_id(self) -> str:
        return self.spec.stream_id

    def generate(self, snapshot: VRPSnapshot, capital: float) -> StreamResult:
        sid = self.stream_id
        # VIX hard gate (per-stream). Circuit breaker is a separate gate above us.
        if snapshot.vix is not None and snapshot.vix > self.vix_max_entry:
            return StreamResult(sid, status="vix_gated",
                                reason=f"VIX {snapshot.vix:.1f} > {self.vix_max_entry:.0f}")
        if capital <= 0:
            return StreamResult(sid, status="no_capital", reason="allocated capital ≤ 0")

        chain = snapshot.chains.get(self.symbol)
        if chain is None or chain.empty:
            return StreamResult(sid, status="degraded", reason=f"no chain for {self.symbol}")

        spot = snapshot.spot.get(self.symbol)
        if spot is None or spot <= 0:
            return StreamResult(sid, status="degraded", reason=f"no spot for {self.symbol}")

        as_of = snapshot.as_of or datetime.now(timezone.utc)
        puts = chain[chain["type"].apply(_is_put)].copy()
        if puts.empty:
            return StreamResult(sid, status="no_entry", reason="no puts in chain")

        exp = _select_expiration(puts, as_of, self.target_dte)
        if exp is None:
            return StreamResult(sid, status="no_entry", reason="no future expiration")
        leg_pool = puts[puts["expiration"] == exp]

        spread = self._select_spread(leg_pool, spot)
        if spread is None:
            return StreamResult(sid, status="no_entry", reason="no qualifying spread")

        short_row, long_row, credit = spread
        max_loss = (float(short_row["strike"]) - float(long_row["strike"])) - credit
        if max_loss <= 0:
            return StreamResult(sid, status="no_entry", reason="non-positive max loss")

        per_spread_risk = max_loss * CONTRACT_MULTIPLIER
        contracts = int(capital // per_spread_risk)
        if contracts < 1:
            return StreamResult(
                sid, status="no_capital",
                reason=f"capital ${capital:,.0f} < one spread risk ${per_spread_risk:,.0f}",
            )

        expiration = _as_date(exp)
        exp_str = expiration.strftime("%Y-%m-%d") if expiration else None
        intent = OrderIntent(
            stream=sid,
            symbol=self.symbol,
            structure="bull_put",
            legs=(
                OrderLeg("sell", "option", str(short_row["contract_symbol"]), contracts,
                         strike=float(short_row["strike"]), expiration=exp_str, right="P"),
                OrderLeg("buy", "option", str(long_row["contract_symbol"]), contracts,
                         strike=float(long_row["strike"]), expiration=exp_str, right="P"),
            ),
            contracts=contracts,
            est_credit=round(credit, 4),
            est_max_loss=round(max_loss, 4),
            rationale=(f"{self.symbol} bull put {short_row['strike']:.0f}/{long_row['strike']:.0f} "
                       f"exp {exp_str} credit {credit:.2f} ×{contracts}"),
            meta={"spot": spot, "vix": snapshot.vix, "target_dte": self.target_dte,
                  "capital": capital, "per_spread_risk": per_spread_risk},
        )
        return StreamResult(sid, intents=[intent], status="entered", reason=intent.rationale)

    def _select_spread(self, puts: pd.DataFrame, spot: float):
        """Pick (short_row, long_row, credit) or None.

        Short = put nearest ``spot * otm_pct``; long = nearest put ``width`` below
        the short (within ``strike_tol``). Require a positive ``min_credit``.
        """
        target_short = spot * self.otm_pct
        puts = puts.dropna(subset=["strike"]).copy()
        if puts.empty:
            return None
        puts["_short_gap"] = (puts["strike"] - target_short).abs()
        for _, short_row in puts.sort_values("_short_gap").head(8).iterrows():
            short_k = float(short_row["strike"])
            short_mid = _mid(short_row)
            if short_mid is None:
                continue
            target_long = short_k - self.width
            below = puts[puts["strike"] < short_k].copy()
            if below.empty:
                continue
            below["_long_gap"] = (below["strike"] - target_long).abs()
            long_row = below.sort_values("_long_gap").iloc[0]
            if abs(float(long_row["strike"]) - target_long) > self.strike_tol:
                continue
            long_mid = _mid(long_row)
            if long_mid is None:
                continue
            credit = short_mid - long_mid
            if credit > self.min_credit:
                return short_row, long_row, round(credit, 4)
        return None


# ── non-trading streams (DEFERRED / BLOCKED) ──────────────────────────────────

class InactiveStream:
    """A stream with no live entry engine yet — emits no intents, only status.

    Used for DEFERRED streams (signal port pending build-plan PR-D) and BLOCKED
    streams (no Alpaca execution path — futures basis). Honest placeholder so the
    engine handles all 8 streams; the allocator re-normalizes over active ones.
    """

    def __init__(self, spec: StreamSpec) -> None:
        if spec.status is StreamStatus.TRADEABLE:
            raise ValueError(f"InactiveStream is for non-tradeable specs, got TRADEABLE {spec.stream_id}")
        self.spec = spec

    @property
    def stream_id(self) -> str:
        return self.spec.stream_id

    def generate(self, snapshot: VRPSnapshot, capital: float) -> StreamResult:
        status = "blocked" if self.spec.status is StreamStatus.BLOCKED else "deferred"
        return StreamResult(self.stream_id, status=status,
                            reason=self.spec.note or f"{status} ({self.spec.owner})")


# ── registry ──────────────────────────────────────────────────────────────────

def build_default_registry() -> Dict[str, object]:
    """Build the canonical 8-stream generator registry from :data:`STREAM_SPECS`.

    Credit-spread streams get a :class:`CreditSpreadStream`; everything else gets
    an :class:`InactiveStream`. Insertion order follows the canonical cube order.
    """
    registry: Dict[str, object] = {}
    for sid, spec in STREAM_SPECS.items():
        if spec.status is StreamStatus.TRADEABLE:
            registry[sid] = CreditSpreadStream(spec)
        else:
            registry[sid] = InactiveStream(spec)
    return registry
