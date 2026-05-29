"""Shared synthetic fixtures for the compass.live VRP engine tests (PR-B).

No network, no Alpaca, no Polygon — everything here is in-memory so the
``signal → sizing → order intent`` path is exercised deterministically.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

from compass.live.vrp_data import VRPSnapshot


def make_put_chain(symbol: str, spot: float, as_of: datetime, dte: int = 30) -> pd.DataFrame:
    """A deterministic single-expiry put+call chain matching cc2's vrp_data schema.

    Put mids increase linearly with strike (≈ +0.30 per $1), so a $5-wide bull put
    yields ~$1.50 credit, max_loss ~$3.50 → $350 per-spread risk. Strikes span
    0.88×–1.02× spot in $1 steps. A few calls are included to test put-filtering.
    """
    exp = (as_of + timedelta(days=dte)).date()
    exp_ts = pd.Timestamp(exp)
    exp_tag = exp.strftime("%y%m%d")
    low = int(round(spot * 0.88))
    high = int(round(spot * 1.02))
    rows: List[dict] = []
    for k in range(low, high + 1):
        put_mid = round(max(0.05, 0.30 * (k - low) + 0.20), 2)
        rows.append({
            "contract_symbol": f"{symbol}{exp_tag}P{int(k * 1000):08d}",
            "strike": float(k),
            "type": "put",
            "bid": round(put_mid - 0.05, 2),
            "ask": round(put_mid + 0.05, 2),
            "last": put_mid,
            "volume": 100,
            "open_interest": 500,
            "iv": 0.20,
            "delta": -0.30,
            "raw_delta": -0.30,
            "gamma": 0.01,
            "theta": -0.05,
            "vega": 0.10,
            "mid": put_mid,
            "expiration": exp_ts,
            "itm": k > spot,
        })
        call_mid = round(max(0.05, 0.30 * (high - k) + 0.20), 2)
        rows.append({
            "contract_symbol": f"{symbol}{exp_tag}C{int(k * 1000):08d}",
            "strike": float(k),
            "type": "call",
            "bid": round(call_mid - 0.05, 2),
            "ask": round(call_mid + 0.05, 2),
            "last": call_mid,
            "volume": 100,
            "open_interest": 500,
            "iv": 0.20,
            "delta": 0.30,
            "raw_delta": 0.30,
            "gamma": 0.01,
            "theta": -0.05,
            "vega": 0.10,
            "mid": call_mid,
            "expiration": exp_ts,
            "itm": k < spot,
        })
    return pd.DataFrame(rows)


# Representative spots for the four tradeable credit-spread underlyings.
DEFAULT_SPOTS = {"SPY": 500.0, "QQQ": 430.0, "XLF": 42.0, "XLI": 130.0}


def make_snapshot(
    spots: Optional[Dict[str, float]] = None,
    vix: Optional[float] = 18.0,
    as_of: Optional[datetime] = None,
    degraded: Optional[List[str]] = None,
    dte: int = 30,
) -> VRPSnapshot:
    """Build a VRPSnapshot with put/call chains for each symbol in ``spots``."""
    spots = DEFAULT_SPOTS if spots is None else spots
    as_of = as_of or datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
    chains = {sym: make_put_chain(sym, px, as_of, dte=dte) for sym, px in spots.items()}
    return VRPSnapshot(
        as_of=as_of,
        chains=chains,
        spot=dict(spots),
        vix=vix,
        degraded=list(degraded or []),
    )


class FakeFeed:
    """In-memory stand-in for cc2's VRPDataFeed (DataFeed protocol)."""

    def __init__(self, snapshot: VRPSnapshot) -> None:
        self._snapshot = snapshot
        self.reset_calls = 0

    def reset_cycle(self) -> None:
        self.reset_calls += 1

    def snapshot(self, option_symbols=None, dte_range=(25, 50)) -> VRPSnapshot:
        if option_symbols is None:
            return self._snapshot
        chains = {s: c for s, c in self._snapshot.chains.items() if s in option_symbols}
        spot = {s: p for s, p in self._snapshot.spot.items() if s in option_symbols}
        return VRPSnapshot(
            as_of=self._snapshot.as_of, chains=chains, spot=spot,
            vix=self._snapshot.vix, degraded=list(self._snapshot.degraded),
        )

    def get_vix_realtime(self) -> Optional[float]:
        return self._snapshot.vix

    def get_bars(self, symbol: str, lookback: int = 252) -> pd.DataFrame:
        return pd.DataFrame()


class FixedVixExposure:
    """VixExposureProvider returning a constant multiplier."""

    def __init__(self, mult: float = 1.0) -> None:
        self._mult = float(mult)

    def current_exposure_multiplier(self) -> float:
        return self._mult


class MockAlpacaProvider:
    """Records submit_credit_spread calls instead of hitting Alpaca."""

    def __init__(self) -> None:
        self.calls: List[dict] = []

    def submit_credit_spread(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"status": "submitted", "order_id": f"mock-{len(self.calls)}"}
