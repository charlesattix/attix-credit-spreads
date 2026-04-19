#!/usr/bin/env python3
"""
scripts/fetch_vix_data.py — Fetch VIX and VIX3M daily close data from Polygon.

Uses POLYGON_INDICES_API_KEY (separate from POLYGON_API_KEY for equities/options).
Stores data in data/macro_cache/macro_cache.db in a dedicated vix_daily table.

Usage:
    # Backfill from 2020-01-01 to today
    python scripts/fetch_vix_data.py --backfill

    # Daily update (last 5 trading days)
    python scripts/fetch_vix_data.py

    # Custom date range
    python scripts/fetch_vix_data.py --from 2024-01-01 --to 2024-12-31

Designed to run as a daily cron job before market open (e.g. 08:00 ET).
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Resolve project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import requests

logger = logging.getLogger("fetch_vix_data")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = ROOT / "data" / "macro_cache" / "macro_cache.db"
POLYGON_BASE = "https://api.polygon.io"

# Polygon indices tickers
TICKERS = {
    "I:VIX": "vix_close",
    "I:VIX3M": "vix3m_close",
}

# Rate limit: 5 calls/min on free tier → 12s between calls (conservative)
RATE_LIMIT_INTERVAL = 12.5


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _init_db(db_path: Path) -> sqlite3.Connection:
    """Create vix_daily table if it doesn't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vix_daily (
            date       TEXT PRIMARY KEY,
            vix_close  REAL,
            vix3m_close REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def _upsert_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Upsert rows into vix_daily. Returns count of rows written."""
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO vix_daily (date, vix_close, vix3m_close, updated_at)
        VALUES (:date, :vix_close, :vix3m_close, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            vix_close  = COALESCE(:vix_close,  vix_close),
            vix3m_close = COALESCE(:vix3m_close, vix3m_close),
            updated_at = datetime('now')
        """,
        rows,
    )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Polygon API
# ---------------------------------------------------------------------------

def _fetch_polygon_aggs(
    ticker: str,
    from_date: str,
    to_date: str,
    api_key: str,
    session: requests.Session,
) -> list[dict]:
    """Fetch daily aggregates from Polygon for an index ticker.

    Returns list of {date: "YYYY-MM-DD", close: float}.
    Handles pagination via next_url.
    """
    results = []
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day"
        f"/{from_date}/{to_date}"
    )
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": api_key,
    }

    while url:
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            logger.warning("Rate limited, sleeping %ds", retry_after)
            time.sleep(retry_after)
            continue
        if resp.status_code != 200:
            logger.error(
                "Polygon %s returned %d: %s",
                ticker, resp.status_code, resp.text[:200],
            )
            break

        data = resp.json()
        for bar in data.get("results", []):
            # bar["t"] is epoch milliseconds
            dt = datetime.utcfromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d")
            results.append({"date": dt, "close": bar["c"]})

        # Pagination
        next_url = data.get("next_url")
        if next_url:
            url = next_url
            params = {"apiKey": api_key}
            time.sleep(RATE_LIMIT_INTERVAL)
        else:
            url = None

    return results


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def fetch_and_store(
    from_date: str,
    to_date: str,
    api_key: str,
    db_path: Path = DB_PATH,
) -> dict:
    """Fetch VIX + VIX3M from Polygon and store in DB.

    Returns {"vix_rows": int, "vix3m_rows": int, "merged": int}.
    """
    conn = _init_db(db_path)
    session = requests.Session()

    # Fetch both tickers
    raw = {}
    for ticker, col_name in TICKERS.items():
        logger.info("Fetching %s from %s to %s ...", ticker, from_date, to_date)
        bars = _fetch_polygon_aggs(ticker, from_date, to_date, api_key, session)
        raw[col_name] = {b["date"]: b["close"] for b in bars}
        logger.info("  → %d bars for %s", len(bars), ticker)
        time.sleep(RATE_LIMIT_INTERVAL)  # rate limit between tickers

    # Merge into unified rows
    all_dates = sorted(set(raw.get("vix_close", {}).keys()) | set(raw.get("vix3m_close", {}).keys()))
    rows = []
    for dt in all_dates:
        rows.append({
            "date": dt,
            "vix_close": raw.get("vix_close", {}).get(dt),
            "vix3m_close": raw.get("vix3m_close", {}).get(dt),
        })

    written = _upsert_rows(conn, rows)
    conn.close()

    stats = {
        "vix_rows": len(raw.get("vix_close", {})),
        "vix3m_rows": len(raw.get("vix3m_close", {})),
        "merged": written,
    }
    logger.info("Done: %s", stats)
    return stats


def load_vix3m_from_cache(db_path: Path = DB_PATH) -> dict:
    """Load VIX3M data from cache as {pd.Timestamp: float}.

    This is the integration point for live scanners and the regime detector.
    Returns empty dict if table doesn't exist or is empty.
    """
    import pandas as pd

    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT date, vix3m_close FROM vix_daily WHERE vix3m_close IS NOT NULL ORDER BY date"
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    return {pd.Timestamp(row[0]): float(row[1]) for row in rows}


def load_vix_from_cache(db_path: Path = DB_PATH) -> dict:
    """Load VIX data from cache as {pd.Timestamp: float}."""
    import pandas as pd

    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT date, vix_close FROM vix_daily WHERE vix_close IS NOT NULL ORDER BY date"
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    return {pd.Timestamp(row[0]): float(row[1]) for row in rows}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch VIX/VIX3M data from Polygon")
    parser.add_argument("--backfill", action="store_true", help="Backfill from 2020-01-01")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--db", default=str(DB_PATH), help="DB path override")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    api_key = os.environ.get("POLYGON_INDICES_API_KEY")
    if not api_key:
        logger.error("POLYGON_INDICES_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    today = datetime.now().strftime("%Y-%m-%d")

    if args.backfill:
        from_date = "2020-01-01"
        to_date = today
    elif args.from_date:
        from_date = args.from_date
        to_date = args.to_date or today
    else:
        # Daily mode: last 7 calendar days (covers weekends + holidays)
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = today

    db_path = Path(args.db)
    stats = fetch_and_store(from_date, to_date, api_key, db_path)
    print(f"VIX: {stats['vix_rows']} bars | VIX3M: {stats['vix3m_rows']} bars | Merged: {stats['merged']} rows")


if __name__ == "__main__":
    main()
