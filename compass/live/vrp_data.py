"""PR-A — Live multi-symbol data + IV chains for the EXP-V8A VRP strategy.

Recon: ``docs/V8A_VRP_RECON_DATA.md``. This module is the single data seam the
VRP streams consume at ENTRY time. It is **additive** — it composes the existing
``strategy.polygon_provider.PolygonProvider`` (IV chains/Greeks) and
``shared.data_cache.DataCache`` (daily bars + VIX, with Yahoo→Polygon index
mapping and the shared bar cache). It does **not** modify any existing provider
and is not imported by any other experiment's live path.

Provider note (clarifies the task framing): IV chains come from **Polygon**, not
Alpaca. ``AlpacaProvider`` is order-placement only (it resolves OCC symbols and
submits legs); it sells no IV chains. So "mock the data provider" here means
mocking Polygon/DataCache. ``AlpacaProvider`` is untouched by PR-A.

═══════════════════════════════════════════════════════════════════════════════
PUBLIC API  (cc1 PR-B strategy engine + cc3 PR-C risk-parity code against THIS)
═══════════════════════════════════════════════════════════════════════════════

  Symbol universes
    VRP_OPTION_SYMBOLS : list[str]   # ["SPY","QQQ","XLF","XLI","GLD","SLV"]
    VRP_HEDGE_SYMBOLS  : list[str]   # 13 ETFs for the v5_hedge overlay (bars only)
    DEFAULT_DTE_RANGE  : tuple[int,int] = (25, 50)

  Module-level convenience functions (delegate to a process-global feed)
    get_bars(symbol: str, lookback: int = 252) -> pd.DataFrame
        Daily OHLCV (cols: Open,High,Low,Close,Volume; DatetimeIndex), tail
        `lookback` rows. Returns EMPTY DataFrame on any failure (never raises).
    get_iv_chain(symbol: str, dte_range: tuple[int,int] = (25,50)) -> pd.DataFrame
        Option chain across the DTE window with IV + Greeks. Columns:
        contract_symbol, strike, type{call|put}, bid, ask, last, volume,
        open_interest, iv, delta, raw_delta, gamma, theta, vega, mid,
        expiration(datetime), itm(bool). EMPTY DataFrame on failure.
    get_vix_realtime() -> float | None
        Latest VIX close (live Polygon I:VIX via DataCache). None on failure.

  VRPDataFeed  — per-scan-cycle cached, thread-safe, dependency-injectable
    VRPDataFeed(polygon=None, data_cache=None, cycle_ttl=300.0)
    .get_bars(symbol, lookback=252)            -> pd.DataFrame
    .get_iv_chain(symbol, dte_range=(25,50))   -> pd.DataFrame
    .get_vix_realtime()                        -> float | None
    .get_spot(symbol)                          -> float | None  (last daily close)
    .snapshot(option_symbols=None, dte_range=(25,50)) -> VRPSnapshot
        Coherent same-cycle cross-section for the allocator/strategy.
    .reset_cycle()                             -> None  (call at top of each scan)

  VRPSnapshot  (frozen dataclass)
    as_of: datetime | None
    chains: dict[str, pd.DataFrame]   # symbol -> chain (only non-empty included)
    spot:   dict[str, float]          # symbol -> last close (best-effort)
    vix:    float | None
    degraded: list[str]               # symbols that failed → SKIP those streams

  Contract notes
    • Graceful degradation: a failed symbol is logged + omitted; callers must
      treat an empty chain / missing snapshot key as "skip that stream", never
      as a crash. `VRPSnapshot.degraded` lists what was dropped.
    • cc3 (risk-parity): this feed supplies ENTRY data only. The Ledoit-Wolf
      covariance input is the realized per-stream daily-return matrix produced
      downstream by PnL attribution — NOT these chains. See recon §4.
    • Futures (GC=F/SI=F) for the gld_cal/slv_cal basis streams are OUT OF SCOPE
      for PR-A (blocker B1: no futures entitlement + Alpaca can't trade futures).
      get_bars() on those symbols will simply degrade to empty.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ── VRP symbol universes (from recon §1) ──────────────────────────────────────
# Option-bearing symbols: the 4 credit-spread streams (SPY/QQQ/XLF/XLI) + the two
# basis streams' ETFs (GLD/SLV) + cross_vol reads ATM IV off SPY/QQQ/XLF/XLI.
VRP_OPTION_SYMBOLS: List[str] = ["SPY", "QQQ", "XLF", "XLI", "GLD", "SLV"]

# v5_hedge trend overlay universe (UNIVERSE_V3) — daily closes only, no options.
VRP_HEDGE_SYMBOLS: List[str] = [
    "SPY", "IWM", "EFA", "EEM", "QQQ",
    "TLT", "LQD", "HYG",
    "GLD", "USO", "DBA", "DBB",
    "UUP",
]

DEFAULT_DTE_RANGE: Tuple[int, int] = (25, 50)

# lookback (trading days) → DataCache period string. We fetch the smallest window
# that covers the request, then tail-slice to the exact row count.
_PERIOD_LADDER: Tuple[Tuple[int, str], ...] = (
    (5, "5d"), (21, "1mo"), (63, "3mo"), (126, "6mo"), (252, "1y"), (504, "2y"),
)


def _period_for(lookback: int) -> str:
    for days, period in _PERIOD_LADDER:
        if lookback <= days:
            return period
    return "2y"


@dataclass(frozen=True)
class VRPSnapshot:
    """One coherent, same-cycle cross-section for the VRP allocator/strategy."""

    as_of: Optional[datetime]
    chains: Dict[str, pd.DataFrame] = field(default_factory=dict)
    spot: Dict[str, float] = field(default_factory=dict)
    vix: Optional[float] = None
    degraded: List[str] = field(default_factory=list)

    @property
    def symbols(self) -> List[str]:
        """Symbols with a usable (non-empty) chain this cycle."""
        return sorted(self.chains.keys())


class VRPDataFeed:
    """Multi-symbol live data feed for the VRP strategy.

    Composes Polygon (chains) + DataCache (bars/VIX). Results are memoized for
    the current scan cycle (``cycle_ttl`` seconds, default 300) so repeated
    same-cycle reads by the 8 streams + allocator hit the API at most once per
    (symbol, query). Call :meth:`reset_cycle` at the top of each scan to force a
    fresh cross-section. Thread-safe (the live worker scans tickers in a pool).

    All public getters degrade gracefully: on any provider error they log and
    return an empty frame / ``None`` rather than raising, so one bad symbol can
    never crash the whole VRP scan.
    """

    def __init__(
        self,
        polygon=None,
        data_cache=None,
        cycle_ttl: float = 300.0,
    ) -> None:
        self._polygon = polygon            # strategy.polygon_provider.PolygonProvider
        self._data_cache = data_cache      # shared.data_cache.DataCache
        self._cycle_ttl = float(cycle_ttl)
        self._cache: Dict[tuple, tuple] = {}   # key -> (value, monotonic_ts)
        self._lock = threading.RLock()

    # ── lazy dependency construction (injectable for tests) ───────────────────

    def _get_polygon(self):
        if self._polygon is None:
            from strategy.polygon_provider import PolygonProvider
            api_key = os.environ.get("POLYGON_API_KEY", "")
            self._polygon = PolygonProvider(api_key=api_key)
        return self._polygon

    def _get_cache(self):
        if self._data_cache is None:
            from shared.data_cache import DataCache
            self._data_cache = DataCache()
        return self._data_cache

    # ── per-cycle memo ────────────────────────────────────────────────────────

    def reset_cycle(self) -> None:
        """Drop all memoized cross-section data — call at the start of a scan."""
        with self._lock:
            self._cache.clear()

    def _cached(self, key: tuple):
        entry = self._cache.get(key)
        if entry is None:
            return None, False
        value, ts = entry
        if time.monotonic() - ts < self._cycle_ttl:
            return value, True
        return None, False

    def _store(self, key: tuple, value) -> None:
        self._cache[key] = (value, time.monotonic())

    # ── public getters ────────────────────────────────────────────────────────

    def get_bars(self, symbol: str, lookback: int = 252) -> pd.DataFrame:
        """Daily OHLCV bars, tail ``lookback`` rows. Empty frame on failure."""
        sym = symbol.upper()
        key = ("bars", sym, int(lookback))
        with self._lock:
            cached, hit = self._cached(key)
            if hit:
                return cached.copy()
        try:
            df = self._get_cache().get_history(sym, period=_period_for(lookback))
            if df is None or df.empty:
                logger.warning("[vrp_data] no bars for %s — degrading to empty", sym)
                df = pd.DataFrame()
            else:
                df = df.tail(int(lookback)).copy()
        except Exception as exc:  # noqa: BLE001 — graceful degradation by design
            logger.warning("[vrp_data] get_bars(%s) failed: %s", sym, exc)
            df = pd.DataFrame()
        with self._lock:
            self._store(key, df)
        return df.copy()

    def get_iv_chain(
        self,
        symbol: str,
        dte_range: Tuple[int, int] = DEFAULT_DTE_RANGE,
    ) -> pd.DataFrame:
        """Option chain (IV + Greeks) across the DTE window. Empty on failure."""
        sym = symbol.upper()
        lo, hi = int(dte_range[0]), int(dte_range[1])
        key = ("chain", sym, lo, hi)
        with self._lock:
            cached, hit = self._cached(key)
            if hit:
                return cached.copy()
        try:
            df = self._get_polygon().get_full_chain(sym, min_dte=lo, max_dte=hi)
            if df is None or df.empty:
                logger.warning(
                    "[vrp_data] empty IV chain for %s (%d-%d DTE) — skip stream",
                    sym, lo, hi,
                )
                df = pd.DataFrame()
        except Exception as exc:  # noqa: BLE001 — graceful degradation by design
            logger.warning("[vrp_data] get_iv_chain(%s) failed: %s", sym, exc)
            df = pd.DataFrame()
        with self._lock:
            self._store(key, df)
        return df.copy()

    def get_vix_realtime(self) -> Optional[float]:
        """Latest VIX close (live Polygon I:VIX via DataCache). None on failure."""
        key = ("vix",)
        with self._lock:
            cached, hit = self._cached(key)
            if hit:
                return cached
        value: Optional[float] = None
        try:
            df = self._get_cache().get_history("^VIX", period="5d")
            if df is not None and not df.empty and "Close" in df.columns:
                value = float(df["Close"].iloc[-1])
            else:
                logger.warning("[vrp_data] no VIX data returned — ladder will degrade")
        except Exception as exc:  # noqa: BLE001 — graceful degradation by design
            logger.warning("[vrp_data] get_vix_realtime() failed: %s", exc)
            value = None
        with self._lock:
            self._store(key, value)
        return value

    def get_spot(self, symbol: str) -> Optional[float]:
        """Best-effort last daily close for ``symbol``. None on failure."""
        bars = self.get_bars(symbol, lookback=1)
        if bars.empty or "Close" not in bars.columns:
            return None
        try:
            return float(bars["Close"].iloc[-1])
        except (IndexError, ValueError, TypeError):
            return None

    def snapshot(
        self,
        option_symbols: Optional[List[str]] = None,
        dte_range: Tuple[int, int] = DEFAULT_DTE_RANGE,
    ) -> VRPSnapshot:
        """Build one coherent same-cycle cross-section for the allocator.

        Fetches every option symbol's chain + spot and the live VIX, reusing the
        per-cycle memo so nothing is fetched twice. Symbols whose chain fails are
        omitted from ``chains``/``spot`` and listed in ``degraded`` — callers
        skip those streams rather than failing the scan.
        """
        symbols = list(option_symbols) if option_symbols is not None else list(VRP_OPTION_SYMBOLS)
        chains: Dict[str, pd.DataFrame] = {}
        spot: Dict[str, float] = {}
        degraded: List[str] = []

        for sym in symbols:
            chain = self.get_iv_chain(sym, dte_range=dte_range)
            if chain.empty:
                degraded.append(sym)
                continue
            chains[sym] = chain
            px = self.get_spot(sym)
            if px is not None:
                spot[sym] = px

        vix = self.get_vix_realtime()

        if degraded:
            logger.warning("[vrp_data] snapshot degraded symbols: %s", ", ".join(degraded))

        return VRPSnapshot(
            as_of=datetime.now(timezone.utc),
            chains=chains,
            spot=spot,
            vix=vix,
            degraded=degraded,
        )


# ── process-global default feed + module-level convenience API ────────────────
_default_feed: Optional[VRPDataFeed] = None
_default_lock = threading.Lock()


def get_default_feed() -> VRPDataFeed:
    """Return (constructing once) the process-global VRP data feed."""
    global _default_feed
    if _default_feed is None:
        with _default_lock:
            if _default_feed is None:
                _default_feed = VRPDataFeed()
    return _default_feed


def reset_cycle() -> None:
    """Reset the process-global feed's per-cycle cache (top of each scan)."""
    get_default_feed().reset_cycle()


def get_bars(symbol: str, lookback: int = 252) -> pd.DataFrame:
    """Daily OHLCV bars (tail ``lookback`` rows); empty frame on failure."""
    return get_default_feed().get_bars(symbol, lookback=lookback)


def get_iv_chain(
    symbol: str,
    dte_range: Tuple[int, int] = DEFAULT_DTE_RANGE,
) -> pd.DataFrame:
    """Option chain with IV + Greeks across ``dte_range``; empty on failure."""
    return get_default_feed().get_iv_chain(symbol, dte_range=dte_range)


def get_vix_realtime() -> Optional[float]:
    """Latest VIX close (live Polygon I:VIX); None on failure."""
    return get_default_feed().get_vix_realtime()
