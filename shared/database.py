"""
Shared SQLite database module for PilotAI.
Single source of truth for trades, alerts, and regime snapshots.
Uses WAL mode for concurrent read access from Python and Node.js.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.constants import DATA_DIR

logger = logging.getLogger(__name__)

import os as _os

DB_PATH = Path(_os.environ.get('PILOTAI_DB_PATH', str(Path(DATA_DIR) / "pilotai.db")))


def get_db(path: Optional[str] = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode enabled."""
    db_path = Path(path) if path else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 5000")  # wait up to 5 s instead of failing immediately
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Optional[str] = None) -> None:
    """Create tables if they don't exist."""
    conn = get_db(path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                ticker TEXT NOT NULL,
                strategy_type TEXT,
                status TEXT DEFAULT 'open',
                short_strike REAL,
                long_strike REAL,
                expiration TEXT,
                credit REAL,
                contracts INTEGER DEFAULT 1,
                entry_date TEXT,
                exit_date TEXT,
                exit_reason TEXT,
                pnl REAL,
                metadata JSON,
                close_source TEXT,
                alpaca_close_activity_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                data JSON NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS regime_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                regime TEXT,
                confidence REAL,
                features JSON,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS reconciliation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details JSON,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS scanner_state (
                key TEXT PRIMARY KEY NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS alert_dedup (
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL,
                alert_type TEXT NOT NULL DEFAULT 'credit_spread',
                last_routed_at TEXT NOT NULL,
                PRIMARY KEY (ticker, direction, alert_type)
            );

            CREATE TABLE IF NOT EXISTS scanner_state (
                key TEXT PRIMARY KEY NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS deviation_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT UNIQUE NOT NULL,
                live_trades INTEGER,
                bt_trades INTEGER,
                live_win_rate REAL,
                bt_win_rate REAL,
                live_pnl REAL,
                bt_pnl REAL,
                live_return_pct REAL,
                bt_return_pct REAL,
                live_profit_factor REAL,
                bt_profit_factor REAL,
                live_max_dd REAL,
                bt_max_dd REAL,
                overall_status TEXT,
                details JSON,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS trade_deviations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE NOT NULL,
                paper_credit REAL,
                expected_credit REAL,
                paper_pnl_pct REAL,
                expected_pnl_pct REAL,
                paper_hold_days REAL,
                expected_hold_days REAL,
                paper_outcome TEXT,
                expected_outcome TEXT,
                deviation_score REAL,
                timestamp TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS trade_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL REFERENCES trades(id),
                leg_type TEXT NOT NULL,
                strike REAL NOT NULL,
                occ_symbol TEXT,
                status TEXT DEFAULT 'open',
                UNIQUE(trade_id, leg_type)
            );
            CREATE INDEX IF NOT EXISTS idx_trade_legs_occ ON trade_legs(occ_symbol);
            CREATE INDEX IF NOT EXISTS idx_trade_legs_trade_id ON trade_legs(trade_id);
        """)
        conn.commit()

        # alert_dedup schema migration: old schemas used (ticker, direction) or
        # (ticker, expiration, strike_type) PKs. New schema uses
        # (ticker, direction, alert_type). Since dedup data is transient (30-min window),
        # drop-and-recreate is safe — at worst one scan's worth of dedup state is lost.
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(alert_dedup)").fetchall()]
            needs_migration = (
                ("expiration" in cols and "direction" not in cols) or
                ("direction" in cols and "alert_type" not in cols)
            )
            if needs_migration:
                conn.execute("DROP TABLE IF EXISTS alert_dedup")
                conn.execute("""
                    CREATE TABLE alert_dedup (
                        ticker TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        alert_type TEXT NOT NULL DEFAULT 'credit_spread',
                        last_routed_at TEXT NOT NULL,
                        PRIMARY KEY (ticker, direction, alert_type)
                    )
                """)
                conn.commit()
                logger.info("Database: migrated alert_dedup to (ticker, direction, alert_type) PK")
        except Exception as _mig_err:
            logger.warning("Database: alert_dedup migration check failed (non-fatal): %s", _mig_err)

        # Safe column migrations — ADD IF NOT EXISTS (try/except for older SQLite)
        for migration_sql in [
            "ALTER TABLE trades ADD COLUMN alpaca_client_order_id TEXT",
            "ALTER TABLE trades ADD COLUMN alpaca_fill_price REAL",
            "ALTER TABLE trades ADD COLUMN alpaca_status TEXT",
            # Bug #2: existing DBs may have alert_dedup without direction column
            "ALTER TABLE alert_dedup ADD COLUMN direction TEXT DEFAULT ''",
            # C1 fix: existing DBs may have alert_dedup without alert_type column
            "ALTER TABLE alert_dedup ADD COLUMN alert_type TEXT DEFAULT 'credit_spread'",
        ]:
            try:
                conn.execute(migration_sql)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        logger.info(f"Database initialized at {path or DB_PATH}")
    finally:
        conn.close()


def upsert_trade(trade: Dict[str, Any], source: str = "scanner", path: Optional[str] = None) -> None:
    """Insert or update a trade."""
    conn = get_db(path)
    try:
        metadata = {k: v for k, v in trade.items() if k not in (
            "id", "ticker", "type", "strategy_type", "status",
            "short_strike", "long_strike", "expiration", "credit",
            "contracts", "entry_date", "exit_date", "exit_reason", "pnl",
        )}
        conn.execute("""
            INSERT INTO trades (id, source, ticker, strategy_type, status,
                short_strike, long_strike, expiration, credit, contracts,
                entry_date, exit_date, exit_reason, pnl, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                exit_date=excluded.exit_date,
                exit_reason=excluded.exit_reason,
                pnl=excluded.pnl,
                metadata=excluded.metadata,
                updated_at=datetime('now')
        """, (
            str(trade.get("id", "")),
            source,
            trade.get("ticker", ""),
            trade.get("type") or trade.get("strategy_type", ""),
            trade.get("status", "open"),
            trade.get("short_strike"),
            trade.get("long_strike"),
            str(trade.get("expiration", "")),
            trade.get("credit") or trade.get("credit_per_spread"),
            trade.get("contracts", 1),
            trade.get("entry_date"),
            trade.get("exit_date"),
            trade.get("exit_reason"),
            trade.get("exit_pnl") or trade.get("pnl"),
            json.dumps(metadata, default=str),
        ))
        conn.commit()
    finally:
        conn.close()


def batch_upsert_trades(trades: List[Dict[str, Any]], source: str = "scanner", path: Optional[str] = None) -> None:
    """Insert or update multiple trades in a single connection/transaction."""
    if not trades:
        return
    conn = get_db(path)
    try:
        for trade in trades:
            metadata = {k: v for k, v in trade.items() if k not in (
                "id", "ticker", "type", "strategy_type", "status",
                "short_strike", "long_strike", "expiration", "credit",
                "contracts", "entry_date", "exit_date", "exit_reason", "pnl",
            )}
            conn.execute("""
                INSERT INTO trades (id, source, ticker, strategy_type, status,
                    short_strike, long_strike, expiration, credit, contracts,
                    entry_date, exit_date, exit_reason, pnl, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    exit_date=excluded.exit_date,
                    exit_reason=excluded.exit_reason,
                    pnl=excluded.pnl,
                    metadata=excluded.metadata,
                    updated_at=datetime('now')
            """, (
                str(trade.get("id", "")),
                source,
                trade.get("ticker", ""),
                trade.get("type") or trade.get("strategy_type", ""),
                trade.get("status", "open"),
                trade.get("short_strike"),
                trade.get("long_strike"),
                str(trade.get("expiration", "")),
                trade.get("credit") or trade.get("credit_per_spread"),
                trade.get("contracts", 1),
                trade.get("entry_date"),
                trade.get("exit_date"),
                trade.get("exit_reason"),
                trade.get("exit_pnl") or trade.get("pnl"),
                json.dumps(metadata, default=str),
            ))
        conn.commit()
    finally:
        conn.close()


def get_trades(
    status: Optional[str] = None,
    source: Optional[str] = None,
    path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch trades with optional filters."""
    conn = get_db(path)
    try:
        query = "SELECT * FROM trades WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_trade(r) for r in rows]
    finally:
        conn.close()


def get_trade_by_id(trade_id: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch a single trade by its id (which doubles as client_order_id)."""
    conn = get_db(path)
    try:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return _row_to_trade(row) if row else None
    finally:
        conn.close()


def close_trade(
    trade_id: str,
    pnl: float,
    reason: str,
    path: Optional[str] = None,
    close_source: Optional[str] = None,
    alpaca_close_activity_id: Optional[str] = None,
) -> None:
    """Close a trade by setting status, exit_date, pnl, and exit_reason.

    Args:
        trade_id: Trade primary key.
        pnl: Realised P&L in dollars.
        reason: Human-readable close reason string.  Special values:
            "manual" → status=closed_manual
            "closed_external" → status=closed_external
            All others: status derived from pnl sign.
        close_source: How the close was detected — "system", "external",
            "expiration", "assignment", "expired_worthless", "expired_itm",
            "external_fill".  Stored in the close_source column.
        alpaca_close_activity_id: Alpaca activity ID that confirmed the close,
            stored in alpaca_close_activity_id column for audit trail.
    """
    conn = get_db(path)
    try:
        status = "closed_profit" if pnl > 0 else "closed_loss" if pnl < 0 else "closed_expiry"
        if reason == "manual":
            status = "closed_manual"
        elif reason == "closed_external":
            status = "closed_external"
        conn.execute("""
            UPDATE trades
            SET status=?, exit_date=?, exit_reason=?, pnl=?,
                close_source=COALESCE(?, close_source),
                alpaca_close_activity_id=COALESCE(?, alpaca_close_activity_id),
                updated_at=datetime('now')
            WHERE id=?
        """, (
            status,
            datetime.now(timezone.utc).isoformat(),
            reason,
            pnl,
            close_source,
            alpaca_close_activity_id,
            trade_id,
        ))
        conn.commit()
    finally:
        conn.close()


def insert_alert(alert: Dict[str, Any], path: Optional[str] = None) -> None:
    """Insert an alert."""
    conn = get_db(path)
    try:
        alert_id = f"alert-{alert.get('ticker', 'UNK')}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        conn.execute(
            "INSERT OR REPLACE INTO alerts (id, ticker, data) VALUES (?, ?, ?)",
            (alert_id, alert.get("ticker", ""), json.dumps(alert, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_alerts(limit: int = 50, path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get the most recent alerts."""
    conn = get_db(path)
    try:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {**json.loads(r["data"]), "id": r["id"], "created_at": r["created_at"]}
            for r in rows
        ]
    finally:
        conn.close()


def insert_regime_snapshot(
    regime: str,
    confidence: float,
    features: Optional[Dict] = None,
    path: Optional[str] = None,
) -> None:
    """Insert a regime detection snapshot."""
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT INTO regime_snapshots (regime, confidence, features) VALUES (?, ?, ?)",
            (regime, confidence, json.dumps(features or {}, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def insert_reconciliation_event(
    trade_id: str,
    event_type: str,
    details: Optional[Dict] = None,
    path: Optional[str] = None,
) -> None:
    """Append an audit entry to the reconciliation_events table."""
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT INTO reconciliation_events (trade_id, event_type, details) VALUES (?, ?, ?)",
            (trade_id, event_type, json.dumps(details or {}, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_dedup_entry(ticker: str, direction: str, alert_type: str, last_routed_at: str, path: Optional[str] = None) -> None:
    """Persist a dedup ledger entry so the router survives restarts.

    Key is (ticker, direction, alert_type). Dedup data is transient (30-min window).
    """
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO alert_dedup (ticker, direction, alert_type, last_routed_at) VALUES (?, ?, ?, ?)",
            (ticker, direction, alert_type, last_routed_at),
        )
        conn.commit()
    finally:
        conn.close()


def load_dedup_entries(window_seconds: int = 1800, path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return dedup entries younger than *window_seconds*."""
    # SECURITY: parameterized query prevents SQL injection (SECURITY_AUDIT.md finding #2)
    window_seconds = int(window_seconds)
    conn = get_db(path)
    try:
        rows = conn.execute(
            "SELECT ticker, direction, alert_type, last_routed_at FROM alert_dedup "
            "WHERE last_routed_at > datetime('now', ?)",
            (f"-{window_seconds} seconds",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_old_dedup_entries(window_seconds: int = 1800, path: Optional[str] = None) -> None:
    """Delete dedup entries older than *window_seconds* to keep the table small."""
    # SECURITY: parameterized query prevents SQL injection (SECURITY_AUDIT.md finding #2)
    window_seconds = int(window_seconds)
    conn = get_db(path)
    try:
        conn.execute(
            "DELETE FROM alert_dedup WHERE last_routed_at <= datetime('now', ?)",
            (f"-{window_seconds} seconds",),
        )
        conn.commit()
    finally:
        conn.close()


def save_scanner_state(key: str, value: str, path: Optional[str] = None) -> None:
    """Persist a scanner state value (e.g. peak_equity) that survives restarts."""
    conn = get_db(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO scanner_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def load_scanner_state(key: str, path: Optional[str] = None) -> Optional[str]:
    """Load a persisted scanner state value. Returns None if not found."""
    conn = get_db(path)
    try:
        row = conn.execute(
            "SELECT value FROM scanner_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def upsert_trade_legs(trade_id: str, legs: List[Dict[str, Any]], path: Optional[str] = None) -> None:
    """Insert or update leg records for a trade.

    Each leg dict must have: leg_type (str), strike (float).
    Optional: occ_symbol (str), status (str, default 'open').
    leg_type examples: 'short_put', 'long_put', 'short_call', 'long_call'.
    """
    if not legs:
        return
    conn = get_db(path)
    try:
        for leg in legs:
            conn.execute(
                """
                INSERT INTO trade_legs (trade_id, leg_type, strike, occ_symbol, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trade_id, leg_type) DO UPDATE SET
                    occ_symbol=excluded.occ_symbol,
                    status=excluded.status
                """,
                (
                    trade_id,
                    leg["leg_type"],
                    float(leg["strike"]),
                    leg.get("occ_symbol"),
                    leg.get("status", "open"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_open_leg_symbols(path: Optional[str] = None) -> set:
    """Return the set of OCC symbols for all open trade legs."""
    conn = get_db(path)
    try:
        rows = conn.execute(
            "SELECT occ_symbol FROM trade_legs WHERE status = 'open' AND occ_symbol IS NOT NULL"
        ).fetchall()
        return {r["occ_symbol"] for r in rows}
    finally:
        conn.close()


def _row_to_trade(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a database row to a trade dict, merging metadata."""
    d = dict(row)
    metadata = {}
    if d.get("metadata"):
        try:
            metadata = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    d.pop("metadata", None)
    return {**d, **metadata}
