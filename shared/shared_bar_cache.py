"""Shared on-disk SQLite cache for daily OHLCV bars.

Phase 1 of the shared-cache architecture (see
``~/.openclaw/media/shared_cache_proposal.html``, Option B).

All experiment subprocesses run inside ONE Railway worker container and share
the mounted volume, so a single SQLite file is a genuine cross-process shared
cache: instead of 10 processes each fetching the same ~760-day SPY/TLT/^VIX
daily bars from Polygon on every cold start, the first miss populates the file
and the other nine read it from disk.

Scope / contract
----------------
* Stores **only daily OHLCV bars** (``Open/High/Low/Close/Volume``) keyed by
  ``(ticker, bar_date)``. This is exactly what the 760-day Polygon
  ``get_historical`` fetches return today.
* **Options chains and intraday are NOT handled here** — options stay on the
  UnusualWhales provider, untouched by this module.
* Concurrency: WAL journal mode + ``busy_timeout`` allow many concurrent reader
  subprocesses with a single writer.
* Freshness for stale-while-revalidate: :meth:`get_bars` classifies a ticker as
  ``FRESH`` / ``STALE`` / ``MISS`` so the caller can serve cached data
  immediately and refresh in the background when stale.
* **Best-effort / never required**: any SQLite failure raises
  :class:`SharedCacheError` so the caller can fall back to a direct Polygon
  fetch. The cache is an optimization, never a hard dependency.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from shared.constants import DATA_DIR

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_DB_FILENAME = "shared_bars.db"

# Canonical OHLCV columns we persist (matches PolygonProvider.get_historical()).
_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


class SharedCacheError(Exception):
    """Raised on any shared-cache failure so callers can fall back."""


class Freshness(str, Enum):
    FRESH = "fresh"   # within fresh_ttl — serve directly, no refetch
    STALE = "stale"   # past fresh_ttl but within max_stale — serve + revalidate
    MISS = "miss"     # no rows, or older than max_stale — caller must fetch


@dataclass
class BarResult:
    status: Freshness
    df: Optional[pd.DataFrame]
    age: Optional[float]          # seconds since last fetch, None on MISS
    last_fetch_ts: Optional[float]


def default_db_path() -> str:
    """Resolve the shared-cache DB path.

    ``SHARED_CACHE_DB`` env wins; otherwise ``<DATA_DIR>/shared_bars.db``.
    On Railway, ``DATA_DIR`` resolves to the mounted volume (``/app/data``).
    """
    env = os.environ.get("SHARED_CACHE_DB", "").strip()
    if env:
        return env
    return os.path.join(DATA_DIR, DEFAULT_DB_FILENAME)


class SharedBarCache:
    """Cross-process SQLite cache for daily OHLCV bars."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        fresh_ttl: float = 900.0,
        max_stale: float = 3 * 86_400.0,
        busy_timeout_ms: int = 5_000,
    ):
        self.db_path = db_path or default_db_path()
        self.fresh_ttl = float(fresh_ttl)
        self.max_stale = float(max_stale)
        self.busy_timeout_ms = int(busy_timeout_ms)
        # Connections are not shareable across threads; keep one per thread.
        self._local = threading.local()
        try:
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._init_schema()
        except sqlite3.Error as exc:
            raise SharedCacheError(f"Failed to initialise shared cache at {self.db_path}: {exc}") from exc
        logger.info("SharedBarCache ready at %s (fresh_ttl=%ss)", self.db_path, self.fresh_ttl)

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000.0)
            conn.row_factory = sqlite3.Row
            # WAL: concurrent readers + single writer; persists in the db file.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache_schema (version INTEGER NOT NULL)"
            )
            row = conn.execute("SELECT version FROM cache_schema LIMIT 1").fetchone()
            current = row["version"] if row else 0
            if current == 0:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS daily_bars (
                        ticker   TEXT NOT NULL,
                        bar_date TEXT NOT NULL,
                        open     REAL,
                        high     REAL,
                        low      REAL,
                        close    REAL,
                        volume   REAL,
                        PRIMARY KEY (ticker, bar_date)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bar_meta (
                        ticker        TEXT PRIMARY KEY,
                        last_fetch_ts REAL NOT NULL,
                        row_count     INTEGER NOT NULL
                    )
                    """
                )
                conn.execute("INSERT INTO cache_schema (version) VALUES (?)", (SCHEMA_VERSION,))
            # Future migrations: elif current < SCHEMA_VERSION: ... bump version.
            # Cross-process single-flight advisory locks (idempotent create so
            # an already-created v1 DB also gains the table).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fetch_locks (
                    lock_key   TEXT PRIMARY KEY,
                    owner_pid  INTEGER NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    def _now(self) -> float:
        return time.time()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_bars(self, ticker: str) -> BarResult:
        """Return cached daily bars for ``ticker`` with a freshness verdict.

        Raises :class:`SharedCacheError` on any SQLite failure.
        """
        key = ticker.upper()
        try:
            conn = self._conn()
            meta = conn.execute(
                "SELECT last_fetch_ts, row_count FROM bar_meta WHERE ticker = ?", (key,)
            ).fetchone()
            if meta is None:
                return BarResult(Freshness.MISS, None, None, None)

            age = self._now() - float(meta["last_fetch_ts"])
            if age > self.max_stale:
                # Too old to trust — force a synchronous refetch upstream.
                return BarResult(Freshness.MISS, None, age, float(meta["last_fetch_ts"]))

            rows = conn.execute(
                "SELECT bar_date, open, high, low, close, volume "
                "FROM daily_bars WHERE ticker = ? ORDER BY bar_date ASC",
                (key,),
            ).fetchall()
            if not rows:
                return BarResult(Freshness.MISS, None, age, float(meta["last_fetch_ts"]))

            df = self._rows_to_df(rows)
            status = Freshness.FRESH if age < self.fresh_ttl else Freshness.STALE
            return BarResult(status, df, age, float(meta["last_fetch_ts"]))
        except sqlite3.Error as exc:
            raise SharedCacheError(f"get_bars({ticker}) failed: {exc}") from exc

    def put_bars(self, ticker: str, df: pd.DataFrame, fetch_ts: Optional[float] = None) -> None:
        """Write-through ``df`` (daily OHLCV) for ``ticker``.

        Idempotent upsert; updates the freshness timestamp. Raises
        :class:`SharedCacheError` on failure.
        """
        key = ticker.upper()
        if df is None or df.empty:
            return
        ts = self._now() if fetch_ts is None else float(fetch_ts)
        try:
            records = self._df_to_rows(key, df)
        except Exception as exc:  # malformed frame — don't poison the cache
            raise SharedCacheError(f"put_bars({ticker}) bad frame: {exc}") from exc
        try:
            conn = self._conn()
            with conn:  # single transaction = single writer
                conn.executemany(
                    "INSERT OR REPLACE INTO daily_bars "
                    "(ticker, bar_date, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    records,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO bar_meta (ticker, last_fetch_ts, row_count) "
                    "VALUES (?, ?, ?)",
                    (key, ts, len(records)),
                )
        except sqlite3.Error as exc:
            raise SharedCacheError(f"put_bars({ticker}) failed: {exc}") from exc

    def healthy(self) -> bool:
        """True if the DB answers a trivial query (used for fast fallback checks)."""
        try:
            self._conn().execute("SELECT 1 FROM cache_schema LIMIT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------
    # Cross-process single-flight (advisory lock)
    # ------------------------------------------------------------------

    def try_acquire_fetch_lock(self, key: str, ttl: float = 30.0) -> bool:
        """Best-effort cross-process lock for ``key``.

        Returns True if this process won the right to fetch. Expired locks (a
        crashed holder) are reclaimed, so the lock can never block permanently.
        WAL serialises the delete+insert, so exactly one concurrent caller wins.
        Raises :class:`SharedCacheError` on SQLite failure (caller may then just
        fetch directly).
        """
        lk = key.upper()
        now = self._now()
        try:
            conn = self._conn()
            with conn:  # single write transaction
                conn.execute(
                    "DELETE FROM fetch_locks WHERE lock_key = ? AND expires_at < ?", (lk, now)
                )
                cur = conn.execute(
                    "INSERT OR IGNORE INTO fetch_locks (lock_key, owner_pid, expires_at) "
                    "VALUES (?, ?, ?)",
                    (lk, os.getpid(), now + float(ttl)),
                )
                return cur.rowcount == 1
        except sqlite3.Error as exc:
            raise SharedCacheError(f"try_acquire_fetch_lock({key}) failed: {exc}") from exc

    def release_fetch_lock(self, key: str) -> None:
        """Release a lock held by this process (no-op if not held)."""
        lk = key.upper()
        try:
            conn = self._conn()
            with conn:
                conn.execute(
                    "DELETE FROM fetch_locks WHERE lock_key = ? AND owner_pid = ?",
                    (lk, os.getpid()),
                )
        except sqlite3.Error as exc:
            raise SharedCacheError(f"release_fetch_lock({key}) failed: {exc}") from exc

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # ------------------------------------------------------------------
    # Frame <-> rows
    # ------------------------------------------------------------------

    @staticmethod
    def _df_to_rows(ticker: str, df: pd.DataFrame):
        records = []
        for idx, row in df.iterrows():
            # Index is a Timestamp (named "Date"); persist its ISO form verbatim
            # so the round-trip is lossless.
            bar_date = pd.Timestamp(idx).isoformat()
            records.append(
                (
                    ticker,
                    bar_date,
                    _f(row.get("Open")),
                    _f(row.get("High")),
                    _f(row.get("Low")),
                    _f(row.get("Close")),
                    _f(row.get("Volume")),
                )
            )
        return records

    @staticmethod
    def _rows_to_df(rows) -> pd.DataFrame:
        idx = pd.to_datetime([r["bar_date"] for r in rows])
        idx.name = "Date"
        data = {
            "Open": [r["open"] for r in rows],
            "High": [r["high"] for r in rows],
            "Low": [r["low"] for r in rows],
            "Close": [r["close"] for r in rows],
            "Volume": [r["volume"] for r in rows],
        }
        return pd.DataFrame(data, index=idx, columns=_OHLCV_COLS)


def _f(v):
    """Coerce to float or None (SQLite REAL)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
