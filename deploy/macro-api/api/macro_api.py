"""
Attix Macro Intelligence REST API
=====================================
FastAPI server exposing macro snapshot data for external consumers.

Run:
    python3 api/macro_api.py                   # port 8420
    python3 api/macro_api.py --port 8421       # custom port
    uvicorn api.macro_api:app --port 8420      # via uvicorn directly

Auth:
    All endpoints (except /api/v1/health) require header:
        X-API-Key: <key>

    Valid keys set via env var MACRO_API_KEYS (comma-separated).
    Default dev key: dev-attix-macro-2026

Rate limit: 100 requests/minute per API key (in-memory sliding window).
"""

import logging
import os
import sys
import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

# ── Project root on path ───────────────────────────────────────────────────────
API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from shared.macro_event_gate import compute_composite_scaling, get_upcoming_events
from shared.macro_state_db import (
    MACRO_DB_PATH,
    get_current_macro_score,
    get_crypto_regime_history,
    get_db,
    get_eligible_underlyings,
    get_event_scaling_factor,
    get_latest_crypto_regime,
    get_latest_snapshot_date,
    get_sector_rankings,
    get_snapshot_count,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"
DEFAULT_PORT = 8420


# ─────────────────────────────────────────────────────────────────────────────
# Auth + rate limiting
# ─────────────────────────────────────────────────────────────────────────────

# SECURITY AUDIT #4: fail fast if MACRO_API_KEYS is not set — no hardcoded fallback.
_raw_keys = os.getenv("MACRO_API_KEYS")
if not _raw_keys:
    raise RuntimeError("MACRO_API_KEYS environment variable must be set before starting")
_VALID_KEYS: set = {k.strip() for k in _raw_keys.split(",") if k.strip()}

_rate_windows: Dict[str, deque] = defaultdict(deque)
_RATE_LIMIT = 100      # requests per 60s window
_RATE_WINDOW = 60.0    # seconds

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _valid_keys() -> set:
    return _VALID_KEYS


def _check_rate_limit(api_key: str) -> bool:
    now = time.monotonic()
    window = _rate_windows[api_key]
    while window and window[0] < now - _RATE_WINDOW:
        window.popleft()
    if len(window) >= _RATE_LIMIT:
        return False
    window.append(now)
    return True


def require_api_key(x_api_key: Optional[str] = Security(_api_key_header)) -> str:
    if not x_api_key or x_api_key not in _valid_keys():
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Pass X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not _check_rate_limit(x_api_key):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {_RATE_LIMIT} requests per minute.",
            headers={"Retry-After": "60"},
        )
    return x_api_key


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic response models
# ─────────────────────────────────────────────────────────────────────────────

class SectorItem(BaseModel):
    ticker:       str
    name:         Optional[str] = None
    category:     Optional[str] = None          # "sector" | "thematic"
    close:        Optional[float] = None
    rs_3m:        Optional[float] = Field(None, description="% outperformance vs SPY over 3M")
    rs_12m:       Optional[float] = Field(None, description="% outperformance vs SPY over 12M")
    rs_ratio:     Optional[float] = Field(None, description="RRG RS-Ratio (100 = universe avg)")
    rs_momentum:  Optional[float] = Field(None, description="RRG RS-Momentum (100 = avg)")
    rrg_quadrant: Optional[str]   = Field(None, description="Leading | Weakening | Lagging | Improving")
    rank_3m:      Optional[int]   = None
    rank_12m:     Optional[int]   = None


class MacroIndicators(BaseModel):
    cfnai_3m:           Optional[float] = Field(None, description="Chicago Fed National Activity Index, 3M avg")
    payrolls_3m_avg_k:  Optional[float] = Field(None, description="Nonfarm payrolls 3M avg (thousands)")
    cpi_yoy_pct:        Optional[float] = Field(None, description="CPI YoY % change")
    core_cpi_yoy_pct:   Optional[float] = Field(None, description="Core CPI YoY % change")
    breakeven_5y:       Optional[float] = Field(None, description="5Y inflation breakeven (%)")
    t10y2y:             Optional[float] = Field(None, description="10Y-2Y Treasury yield spread (%)")
    fedfunds:           Optional[float] = Field(None, description="Effective Fed Funds Rate (%)")
    vix:                Optional[float] = Field(None, description="CBOE VIX close")
    hy_oas_pct:         Optional[float] = Field(None, description="HY OAS spread (%)")


class MacroScoreResponse(BaseModel):
    overall:            Optional[float] = Field(None, description="Composite macro score 0–100")
    overall_v2:         Optional[float] = Field(None, description="v2 formula score (reserved; currently NULL)")
    growth:             Optional[float] = None
    inflation:          Optional[float] = None
    fed_policy:         Optional[float] = None
    risk_appetite:      Optional[float] = None
    score_velocity:     Optional[float] = Field(None, description="Week-over-week change in overall score")
    risk_app_velocity:  Optional[float] = Field(None, description="Week-over-week change in risk appetite score")
    regime:             Optional[str]   = Field(None, description="BULL_MACRO | NEUTRAL_MACRO | BEAR_MACRO")
    indicators:         Optional[MacroIndicators] = None
    as_of_date:         Optional[str]   = None


class MacroEventItem(BaseModel):
    event_date:     str
    event_type:     str                          # FOMC | CPI | NFP
    description:    Optional[str]  = None
    days_out:       int
    scaling_factor: float = Field(..., description="Position size multiplier (0.50–1.00)")
    is_emergency:   bool  = Field(False, description="True for unscheduled emergency events (e.g. COVID FOMC)")


class SnapshotResponse(BaseModel):
    date:            str
    spy_close:       Optional[float] = None
    top_sector_3m:   Optional[str]   = None
    top_sector_12m:  Optional[str]   = None
    leading_sectors: List[str]        = []
    lagging_sectors: List[str]        = []
    macro_score:     Optional[MacroScoreResponse] = None
    sector_rankings: List[SectorItem]  = []
    upcoming_events: List[MacroEventItem] = []
    event_scaling:   float             = 1.0


class SnapshotSummary(BaseModel):
    """Lightweight snapshot for history list responses."""
    date:           str
    spy_close:      Optional[float] = None
    top_sector_3m:  Optional[str]   = None
    top_sector_12m: Optional[str]   = None
    macro_overall:  Optional[float] = None
    regime:         Optional[str]   = None
    growth:         Optional[float] = None
    inflation:      Optional[float] = None
    fed_policy:     Optional[float] = None
    risk_appetite:  Optional[float] = None


class EligibleResponse(BaseModel):
    tickers:        List[str]
    macro_score:    float
    macro_regime:   str
    event_scaling:  float
    as_of_date:     Optional[str] = None


class HealthResponse(BaseModel):
    status:          str
    version:         str
    snapshot_count:  int
    latest_date:     Optional[str]
    db_path:         str
    timestamp:       str


class CryptoRegimeResponse(BaseModel):
    snapshot_date:        str
    btc_price:            Optional[float] = Field(None, description="BTC/USD spot price")
    eth_price:            Optional[float] = Field(None, description="ETH/USD spot price")
    fear_greed_value:     Optional[int]   = Field(None, description="Crypto Fear & Greed index (0-100)")
    fear_greed_class:     Optional[str]   = Field(None, description="Fear & Greed classification")
    btc_funding_rate:     Optional[float] = Field(None, description="BTC perpetual funding rate (%/8h)")
    eth_funding_rate:     Optional[float] = Field(None, description="ETH perpetual funding rate (%/8h)")
    btc_realized_vol_7d:  Optional[float] = Field(None, description="BTC 7-day annualized realized volatility")
    btc_realized_vol_30d: Optional[float] = Field(None, description="BTC 30-day annualized realized volatility")
    btc_iv_percentile:    Optional[float] = Field(None, description="BTC IV percentile (1-year rank, 0-100)")
    btc_dominance:        Optional[float] = Field(None, description="BTC market cap dominance (%)")
    btc_put_call_ratio:   Optional[float] = Field(None, description="BTC options put/call ratio by OI")
    composite_score:      Optional[float] = Field(None, description="Weighted composite regime score (0-100)")
    score_band:           Optional[str]   = Field(
        None,
        description="EXTREME_FEAR | CAUTIOUS | NEUTRAL | BULLISH | EXTREME_GREED",
    )
    ma200_position:       Optional[str]   = Field(None, description="BTC vs 200-day MA: above | below | crossing")
    overnight_gap_pct:    Optional[float] = Field(None, description="BTC close-to-close daily return (proxy for IBIT/ETHA gap)")
    created_at:           Optional[str]   = None


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

def _macro_regime(score: Optional[float]) -> str:
    if score is None:
        return "NEUTRAL_MACRO"
    return "BULL_MACRO" if score >= 65 else ("BEAR_MACRO" if score < 45 else "NEUTRAL_MACRO")


def _load_snapshot_from_db(target_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load a full snapshot from macro_state.db."""
    conn = get_db()
    try:
        if target_date is None:
            row = conn.execute("SELECT MAX(date) AS d FROM snapshots").fetchone()
            target_date = row["d"] if row else None
        if not target_date:
            return None

        snap_row = conn.execute(
            "SELECT * FROM snapshots WHERE date = ?", (target_date,)
        ).fetchone()
        if not snap_row:
            return None

        ms_row = conn.execute(
            "SELECT * FROM macro_score WHERE date = ?", (target_date,)
        ).fetchone()

        sector_rows = conn.execute(
            "SELECT * FROM sector_rs WHERE date = ? ORDER BY rank_3m ASC",
            (target_date,),
        ).fetchall()

        return {
            "snap": dict(snap_row),
            "macro": dict(ms_row) if ms_row else {},
            "sectors": [dict(r) for r in sector_rows],
            "date": target_date,
        }
    finally:
        conn.close()


def _build_snapshot_response(data: Dict) -> SnapshotResponse:
    snap = data["snap"]
    ms = data["macro"]
    events = get_upcoming_events(
        as_of=date.fromisoformat(data["date"]),
        horizon_days=14,
    )

    import json
    leading = json.loads(snap.get("leading_sectors") or "[]")
    lagging = json.loads(snap.get("lagging_sectors") or "[]")

    macro_resp = MacroScoreResponse(
        overall=ms.get("overall"),
        overall_v2=ms.get("overall_v2"),
        growth=ms.get("growth"),
        inflation=ms.get("inflation"),
        fed_policy=ms.get("fed_policy"),
        risk_appetite=ms.get("risk_appetite"),
        score_velocity=ms.get("score_velocity"),
        risk_app_velocity=ms.get("risk_app_velocity"),
        regime=ms.get("regime"),
        as_of_date=data["date"],
        indicators=MacroIndicators(
            cfnai_3m=ms.get("cfnai_3m"),
            payrolls_3m_avg_k=ms.get("payrolls_3m_avg_k"),
            cpi_yoy_pct=ms.get("cpi_yoy_pct"),
            core_cpi_yoy_pct=ms.get("core_cpi_yoy_pct"),
            breakeven_5y=ms.get("breakeven_5y"),
            t10y2y=ms.get("t10y2y"),
            fedfunds=ms.get("fedfunds"),
            vix=ms.get("vix"),
            hy_oas_pct=ms.get("hy_oas_pct"),
        ),
    ) if ms else None

    sector_items = [
        SectorItem(
            ticker=r["ticker"],
            name=r.get("name"),
            category=r.get("category"),
            close=r.get("close"),
            rs_3m=r.get("rs_3m"),
            rs_12m=r.get("rs_12m"),
            rs_ratio=r.get("rs_ratio"),
            rs_momentum=r.get("rs_momentum"),
            rrg_quadrant=r.get("rrg_quadrant"),
            rank_3m=r.get("rank_3m"),
            rank_12m=r.get("rank_12m"),
        )
        for r in data["sectors"]
    ]

    event_items = [
        MacroEventItem(
            event_date=ev["event_date"],
            event_type=ev["event_type"],
            description=ev.get("description"),
            days_out=ev["days_out"],
            scaling_factor=ev["scaling_factor"],
            is_emergency=bool(ev.get("is_emergency", False)),
        )
        for ev in events
    ]

    return SnapshotResponse(
        date=data["date"],
        spy_close=snap.get("spy_close"),
        top_sector_3m=snap.get("top_sector_3m"),
        top_sector_12m=snap.get("top_sector_12m"),
        leading_sectors=leading,
        lagging_sectors=lagging,
        macro_score=macro_resp,
        sector_rankings=sector_items,
        upcoming_events=event_items,
        event_scaling=compute_composite_scaling(events),
    )


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Attix Macro Intelligence API",
    description=(
        "Weekly macro snapshots, sector relative strength rankings, "
        "RRG quadrant classifications, 4-dimension macro scores, "
        "and FOMC/CPI/NFP event scaling factors.\n\n"
        "**Auth:** Pass `X-API-Key: <key>` header on all endpoints except `/api/v1/health`.\n\n"
        "**Rate limit:** 100 requests / minute per key."
    ),
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check — no auth required",
)
def health_check() -> HealthResponse:
    """Returns server health, DB connectivity, and snapshot coverage stats."""
    try:
        count = get_snapshot_count()
        latest = get_latest_snapshot_date()
        db_ok = True
    except Exception:
        count = 0
        latest = None
        db_ok = False

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=API_VERSION,
        snapshot_count=count,
        latest_date=latest,
        db_path=str(MACRO_DB_PATH),
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


@app.get(
    "/api/v1/macro/snapshot/latest",
    response_model=SnapshotResponse,
    tags=["Snapshots"],
    summary="Latest weekly snapshot (full)",
)
def get_latest_snapshot(
    _key: str = Depends(require_api_key),
) -> SnapshotResponse:
    """
    Returns the most recent weekly macro snapshot including:
    - Sector RS rankings (all 15 ETFs, sorted by 3M RS)
    - RRG quadrant classifications
    - 4-dimension macro score with raw indicators
    - Upcoming FOMC/CPI/NFP events with position scaling factors
    """
    data = _load_snapshot_from_db()
    if not data:
        raise HTTPException(status_code=404, detail="No snapshots found in database.")
    return _build_snapshot_response(data)


@app.get(
    "/api/v1/macro/snapshot/{snapshot_date}",
    response_model=SnapshotResponse,
    tags=["Snapshots"],
    summary="Snapshot for a specific date",
)
def get_snapshot_by_date(
    snapshot_date: str,
    _key: str = Depends(require_api_key),
) -> SnapshotResponse:
    """
    Returns the macro snapshot for a specific Friday date (YYYY-MM-DD).
    Coverage: 2020-01-03 to present (323 weekly snapshots).
    """
    try:
        date.fromisoformat(snapshot_date)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date format: '{snapshot_date}'. Use YYYY-MM-DD.")

    data = _load_snapshot_from_db(snapshot_date)
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"No snapshot found for {snapshot_date}. "
                   f"Coverage: 2020-01-03 to present (Fridays only).",
        )
    return _build_snapshot_response(data)


@app.get(
    "/api/v1/macro/sectors",
    response_model=List[SectorItem],
    tags=["Sectors"],
    summary="Current sector RS rankings",
)
def get_sectors(
    category: Optional[str] = Query(None, description="Filter by 'sector' or 'thematic'"),
    _key: str = Depends(require_api_key),
) -> List[SectorItem]:
    """
    Returns sector relative strength rankings from the latest snapshot,
    sorted by 3-month RS rank ascending (rank 1 = strongest).

    RRG quadrants:
    - **Leading** — strong RS, gaining momentum (buy signal)
    - **Weakening** — strong RS, losing momentum (watch for exit)
    - **Improving** — weak RS, gaining momentum (early entry signal)
    - **Lagging** — weak RS, losing momentum (avoid / short candidate)
    """
    rankings = get_sector_rankings()
    if not rankings:
        raise HTTPException(status_code=404, detail="No sector data available.")
    if category:
        rankings = [r for r in rankings if r.get("category") == category]
    return [SectorItem(**r) for r in rankings]


@app.get(
    "/api/v1/macro/score",
    response_model=MacroScoreResponse,
    tags=["Macro Score"],
    summary="Current macro score breakdown",
)
def get_macro_score(
    _key: str = Depends(require_api_key),
) -> MacroScoreResponse:
    """
    Returns the 4-dimension macro score from the latest snapshot.

    **Score ranges (each dimension 0–100):**
    - **Growth** — CFNAI 3M avg (50%) + NFP 3M avg (50%)
    - **Inflation** — CPI YoY (35%) + Core CPI YoY (40%) + 5Y breakeven (25%). Goldilocks curve peaks at 2–2.5%.
    - **Fed Policy** — 10Y-2Y spread (55%) + Fed Funds rate (45%)
    - **Risk Appetite** — VIX (50%) + HY OAS spread (50%)

    **Regime:**
    - ≥ 65 → BULL_MACRO (favorable conditions)
    - 45–65 → NEUTRAL_MACRO (mixed signals)
    - < 45 → BEAR_MACRO (adverse conditions)
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM macro_score ORDER BY date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="No macro score data available.")

    d = dict(row)
    return MacroScoreResponse(
        overall=d.get("overall"),
        overall_v2=d.get("overall_v2"),
        growth=d.get("growth"),
        inflation=d.get("inflation"),
        fed_policy=d.get("fed_policy"),
        risk_appetite=d.get("risk_appetite"),
        score_velocity=d.get("score_velocity"),
        risk_app_velocity=d.get("risk_app_velocity"),
        regime=d.get("regime"),
        as_of_date=d.get("date"),
        indicators=MacroIndicators(
            cfnai_3m=d.get("cfnai_3m"),
            payrolls_3m_avg_k=d.get("payrolls_3m_avg_k"),
            cpi_yoy_pct=d.get("cpi_yoy_pct"),
            core_cpi_yoy_pct=d.get("core_cpi_yoy_pct"),
            breakeven_5y=d.get("breakeven_5y"),
            t10y2y=d.get("t10y2y"),
            fedfunds=d.get("fedfunds"),
            vix=d.get("vix"),
            hy_oas_pct=d.get("hy_oas_pct"),
        ),
    )


@app.get(
    "/api/v1/macro/events",
    response_model=List[MacroEventItem],
    tags=["Events"],
    summary="Upcoming macro events with position scaling factors",
)
def get_macro_events(
    horizon_days: int = Query(14, ge=1, le=90, description="Look-ahead window in calendar days"),
    _key: str = Depends(require_api_key),
) -> List[MacroEventItem]:
    """
    Returns scheduled FOMC, CPI, and NFP events within `horizon_days`.

    **Scaling factors** (position size multiplier):
    - FOMC: T-5→1.00, T-4→0.90, T-3→0.80, T-2→0.70, T-1→0.60, T-0→0.50
    - CPI:  T-2→1.00, T-1→0.75, T-0→0.65
    - NFP:  T-2→1.00, T-1→0.80, T-0→0.75

    Composite factor (used by trading system) = min() across all active events.
    """
    events = get_upcoming_events(horizon_days=horizon_days)
    return [
        MacroEventItem(
            event_date=ev["event_date"],
            event_type=ev["event_type"],
            description=ev.get("description"),
            days_out=ev["days_out"],
            scaling_factor=ev["scaling_factor"],
            is_emergency=bool(ev.get("is_emergency", False)),
        )
        for ev in events
    ]


@app.get(
    "/api/v1/macro/eligible",
    response_model=EligibleResponse,
    tags=["Trading"],
    summary="Eligible underlyings for credit spread trading",
)
def get_eligible(
    regime: str = Query("NEUTRAL", description="ComboRegimeDetector output: BULL | NEUTRAL | BEAR"),
    _key: str = Depends(require_api_key),
) -> EligibleResponse:
    """
    Returns eligible underlying tickers based on current macro state and the
    regime classification from `ComboRegimeDetector`.

    **Logic:**
    - Base universe always included: SPY, QQQ, IWM
    - BULL or NEUTRAL + macro score ≥ 45: top-4 liquid sectors by 3M RS added
    - BEAR macro (score < 45): contract to base universe only
    - Liquid sectors eligible for expansion: XLE, XLF, XLV, XLK, XLI, XLU, XLY
    """
    regime_upper = regime.upper()
    if regime_upper not in ("BULL", "NEUTRAL", "BEAR"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid regime '{regime}'. Must be BULL, NEUTRAL, or BEAR."
        )

    score = get_current_macro_score()
    tickers = get_eligible_underlyings(regime_upper)
    scaling = get_event_scaling_factor()
    latest = get_latest_snapshot_date()

    return EligibleResponse(
        tickers=tickers,
        macro_score=score,
        macro_regime=_macro_regime(score),
        event_scaling=scaling,
        as_of_date=latest,
    )


@app.get(
    "/api/v1/macro/history",
    response_model=List[SnapshotSummary],
    tags=["Snapshots"],
    summary="Historical snapshot summaries for a date range",
)
def get_history(
    from_date: str = Query(..., alias="from", description="Start date YYYY-MM-DD"),
    to_date: str   = Query(..., alias="to",   description="End date YYYY-MM-DD"),
    _key: str      = Depends(require_api_key),
) -> List[SnapshotSummary]:
    """
    Returns lightweight snapshot summaries (date, SPY close, top sector,
    macro score) for every Friday in the requested range.

    Max range: 3 years (to keep response sizes manageable).
    For full sector detail, use `/snapshot/{date}` on individual dates.
    """
    try:
        d_from = date.fromisoformat(from_date)
        d_to   = date.fromisoformat(to_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date: {exc}")

    if d_to < d_from:
        raise HTTPException(status_code=422, detail="'to' must be >= 'from'.")

    if (d_to - d_from).days > 365 * 3:
        raise HTTPException(status_code=422, detail="Max range is 3 years.")

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT s.date, s.spy_close, s.top_sector_3m, s.top_sector_12m,
                   m.overall AS macro_overall, m.regime,
                   m.growth, m.inflation, m.fed_policy, m.risk_appetite
            FROM snapshots s
            LEFT JOIN macro_score m ON s.date = m.date
            WHERE s.date >= ? AND s.date <= ?
            ORDER BY s.date ASC
            """,
            (from_date, to_date),
        ).fetchall()
    finally:
        conn.close()

    return [
        SnapshotSummary(
            date=r["date"],
            spy_close=r["spy_close"],
            top_sector_3m=r["top_sector_3m"],
            top_sector_12m=r["top_sector_12m"],
            macro_overall=r["macro_overall"],
            regime=r["regime"],
            growth=r["growth"],
            inflation=r["inflation"],
            fed_policy=r["fed_policy"],
            risk_appetite=r["risk_appetite"],
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Crypto endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/api/v1/crypto/regime",
    response_model=CryptoRegimeResponse,
    tags=["Crypto"],
    summary="Latest crypto regime snapshot",
)
def get_crypto_regime(
    _key: str = Depends(require_api_key),
) -> CryptoRegimeResponse:
    """
    Returns the most recent daily crypto regime snapshot including:
    - BTC and ETH spot prices
    - Crypto Fear & Greed index
    - BTC perpetual funding rates (Binance)
    - BTC realized volatility (7-day and 30-day, annualized)
    - BTC market dominance
    - BTC options put/call ratio (Deribit)
    - Composite regime score (0–100) and band

    **Score bands:**
    - 0–25: EXTREME_FEAR — wide put premiums, high crash risk
    - 25–40: CAUTIOUS — reduce size
    - 40–60: NEUTRAL — iron condors preferred
    - 60–75: BULLISH — sell puts
    - 75–100: EXTREME_GREED — sell calls

    Updated daily via `scripts/run_crypto_snapshot.py --daily`.
    """
    row = get_latest_crypto_regime()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="No crypto regime snapshot found. Run: python3 scripts/run_crypto_snapshot.py --daily",
        )
    return CryptoRegimeResponse(**row)


@app.get(
    "/api/v1/crypto/regime/history",
    response_model=List[CryptoRegimeResponse],
    tags=["Crypto"],
    summary="Historical crypto regime snapshots",
)
def get_crypto_regime_history_endpoint(
    days: int = Query(30, ge=1, le=365, description="Number of recent daily snapshots to return"),
    _key: str = Depends(require_api_key),
) -> List[CryptoRegimeResponse]:
    """
    Returns up to `days` daily crypto regime snapshots, newest first.

    Useful for charting composite score trends, funding rate history,
    and Fear & Greed momentum over time.

    Max: 365 days. Coverage starts from when `run_crypto_snapshot.py --daily`
    was first deployed.
    """
    rows = get_crypto_regime_history(days=days)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No crypto regime history found. Run: python3 scripts/run_crypto_snapshot.py --daily",
        )
    return [CryptoRegimeResponse(**r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Attix Macro Intelligence API")
    parser.add_argument("--port",   type=int,  default=DEFAULT_PORT, help="Port (default: 8420)")
    parser.add_argument("--host",   type=str,  default="0.0.0.0",    help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--reload", action="store_true",             help="Auto-reload on file changes (dev only)")
    args = parser.parse_args()

    logger.info("Starting Attix Macro API v%s on %s:%d", API_VERSION, args.host, args.port)
    logger.info("Swagger UI: http://localhost:%d/docs", args.port)
    logger.info("ReDoc:      http://localhost:%d/redoc", args.port)
    # SECURITY AUDIT #4: do not log key values or announce defaults.
    logger.info("Auth: MACRO_API_KEYS loaded (%d key(s))", len(_VALID_KEYS))

    uvicorn.run(
        "api.macro_api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
