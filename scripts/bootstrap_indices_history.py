#!/usr/bin/env python3
"""One-time bootstrap of pre-2023-02-14 index daily bars from Yahoo to SQLite.

Polygon's Indices plan only provides daily aggregates for ``I:VIX``,
``I:VIX3M``, ``I:SPX`` from 2023-02-14 onward (verified by curl in
``BACKTEST_MIGRATION_PROPOSAL.md`` §3.2). Backtests pull from 2019-06-01
warmup. This script closes that gap by pulling those three index series
from Yahoo Finance via the legacy curl helper in ``backtest.backtester``
and writing them to ``data/historical_indices.sqlite``.

After the SQLite file is committed, this script is no longer needed.
Re-running is a no-op (idempotent via ``INSERT OR IGNORE``).

Schema:
    historical_indices(
        ticker TEXT,           -- 'I:VIX' | 'I:VIX3M' | 'I:SPX' (Polygon canonical)
        date   TEXT,           -- 'YYYY-MM-DD'
        open   REAL,
        high   REAL,
        low    REAL,
        close  REAL,
        volume REAL,
        PRIMARY KEY (ticker, date)
    )

Run::

    export $(grep -v '^#' .env | xargs)
    python3 scripts/bootstrap_indices_history.py
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

# Import path setup so this script works from repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.backtester import _yf_download_safe  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bootstrap_indices")

# Yahoo symbol → Polygon canonical ticker (stored canonical for cross-source joins)
_TICKERS = [
    ("^VIX",   "I:VIX"),
    ("^VIX3M", "I:VIX3M"),
    ("^GSPC",  "I:SPX"),
]

# Bootstrap range: warmup boundary → Polygon coverage start.
# Polygon's first index bar is 2023-02-14. Yahoo's end-date is exclusive, so
# passing 2023-02-14 yields bars up to and including 2023-02-13 (the last
# trading day before Polygon coverage begins).
_START = "2019-06-01"
_END = "2023-02-14"

_DB_PATH = ROOT / "data" / "historical_indices.sqlite"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_indices (
            ticker TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL,
            high   REAL,
            low    REAL,
            close  REAL,
            volume REAL,
            PRIMARY KEY (ticker, date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_indices_date "
        "ON historical_indices(date)"
    )
    conn.commit()


def _fetch_one(yahoo_sym: str, polygon_ticker: str, conn: sqlite3.Connection) -> int:
    """Fetch one ticker series from Yahoo and insert rows. Returns rows inserted."""
    logger.info("Fetching %s (→ %s) from Yahoo: %s..%s",
                yahoo_sym, polygon_ticker, _START, _END)
    df = _yf_download_safe(yahoo_sym, _START, _END)
    if df.empty:
        logger.error("Yahoo returned 0 rows for %s — aborting (do not partial-populate)",
                     yahoo_sym)
        raise RuntimeError(f"Yahoo returned empty for {yahoo_sym}")

    rows = []
    for ts, row in df.iterrows():
        date_str = ts.strftime("%Y-%m-%d")
        # Yahoo ^VIX/^VIX3M have no volume; default to 0.0
        vol = float(row.get("Volume") or 0.0) if "Volume" in df.columns else 0.0
        rows.append((
            polygon_ticker,
            date_str,
            float(row["Open"]) if row.get("Open") is not None else None,
            float(row["High"]) if row.get("High") is not None else None,
            float(row["Low"]) if row.get("Low") is not None else None,
            float(row["Close"]) if row.get("Close") is not None else None,
            vol,
        ))

    cur = conn.cursor()
    cur.executemany(
        "INSERT OR IGNORE INTO historical_indices "
        "(ticker, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    inserted = cur.rowcount if cur.rowcount is not None else 0
    conn.commit()
    logger.info("  %s: fetched %d rows, inserted %d new", polygon_ticker, len(rows), inserted)
    return inserted


def main() -> int:
    os.makedirs(_DB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    try:
        _ensure_schema(conn)
        for yahoo_sym, polygon_ticker in _TICKERS:
            _fetch_one(yahoo_sym, polygon_ticker, conn)

        # Final inventory
        cur = conn.cursor()
        cur.execute(
            "SELECT ticker, COUNT(*), MIN(date), MAX(date) "
            "FROM historical_indices GROUP BY ticker ORDER BY ticker"
        )
        logger.info("Inventory of %s:", _DB_PATH)
        for ticker, count, dmin, dmax in cur.fetchall():
            logger.info("  %s: %d rows, %s..%s", ticker, count, dmin, dmax)
        size_mb = _DB_PATH.stat().st_size / 1024 / 1024
        logger.info("File size: %.2f MB", size_mb)
        if size_mb > 5.0:
            logger.warning("SQLite file exceeds 5MB budget — investigate")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
