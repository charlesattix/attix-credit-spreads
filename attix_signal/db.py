"""
Database layer — SQLite schema, connection management, and query helpers.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator, List, Optional

from . import config

logger = logging.getLogger(__name__)

# ── DDL ───────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strategy_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date  DATE    NOT NULL,
    strategy_slug  TEXT    NOT NULL,
    strategy_name  TEXT    NOT NULL,
    total_cost     REAL,
    leftover       REAL,
    n_holdings     INTEGER,
    collected_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(snapshot_date, strategy_slug)
);

CREATE TABLE IF NOT EXISTS snapshot_holdings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES strategy_snapshots(id) ON DELETE CASCADE,
    ticker      TEXT    NOT NULL,
    name        TEXT,
    price       REAL,
    quantity    INTEGER,
    weight      REAL,
    cost        REAL
);
CREATE INDEX IF NOT EXISTS idx_holdings_snapshot ON snapshot_holdings(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker   ON snapshot_holdings(ticker);

CREATE TABLE IF NOT EXISTS snapshot_scores (
    snapshot_id          INTEGER PRIMARY KEY REFERENCES strategy_snapshots(id) ON DELETE CASCADE,
    value_score          REAL,
    growth_score         REAL,
    health_score         REAL,
    momentum_score       REAL,
    past_performance     REAL,
    composite_qscore     REAL
);

CREATE TABLE IF NOT EXISTS ticker_signals (
    signal_date      DATE    NOT NULL,
    ticker           TEXT    NOT NULL,
    frequency        INTEGER NOT NULL,
    total_portfolios INTEGER NOT NULL,
    freq_pct         REAL    NOT NULL,
    avg_weight       REAL    NOT NULL,
    weighted_qscore  REAL    NOT NULL,
    days_in_signal   INTEGER NOT NULL,
    conviction       REAL    NOT NULL,
    PRIMARY KEY(signal_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_signals_date       ON ticker_signals(signal_date);
CREATE INDEX IF NOT EXISTS idx_signals_ticker     ON ticker_signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_conviction ON ticker_signals(signal_date, conviction DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_date        DATE    NOT NULL,
    alert_type        TEXT    NOT NULL,
    ticker            TEXT    NOT NULL,
    conviction_before REAL,
    conviction_after  REAL,
    days_in_signal    INTEGER,
    message           TEXT,
    telegram_sent     INTEGER DEFAULT 0,
    sent_at           TIMESTAMP,
    UNIQUE(alert_date, alert_type, ticker)
);

CREATE TABLE IF NOT EXISTS collection_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        DATE    NOT NULL,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    status          TEXT    NOT NULL,
    strategies_ok   INTEGER DEFAULT 0,
    strategies_fail INTEGER DEFAULT 0,
    error_msg       TEXT,
    duration_sec    REAL
);
"""


# ── Connection ────────────────────────────────────────────────────────────────

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """Create all tables if they don't exist."""
    with transaction(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
    logger.info("Database initialized at %s", db_path or config.DB_PATH)


# ── Write helpers ─────────────────────────────────────────────────────────────

def upsert_snapshot(
    conn: sqlite3.Connection,
    snapshot_date: date,
    slug: str,
    name: str,
    total_cost: float,
    leftover: float,
    n_holdings: int,
) -> int:
    """Insert or replace strategy snapshot; returns snapshot_id."""
    conn.execute(
        """INSERT INTO strategy_snapshots
               (snapshot_date, strategy_slug, strategy_name, total_cost, leftover, n_holdings)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(snapshot_date, strategy_slug) DO UPDATE SET
               strategy_name=excluded.strategy_name,
               total_cost=excluded.total_cost,
               leftover=excluded.leftover,
               n_holdings=excluded.n_holdings,
               collected_at=CURRENT_TIMESTAMP""",
        (snapshot_date.isoformat(), slug, name, total_cost, leftover, n_holdings),
    )
    row = conn.execute(
        "SELECT id FROM strategy_snapshots WHERE snapshot_date=? AND strategy_slug=?",
        (snapshot_date.isoformat(), slug),
    ).fetchone()
    return row["id"]


def insert_holdings(
    conn: sqlite3.Connection,
    snapshot_id: int,
    holdings: List[dict],
) -> None:
    """Delete-then-insert holdings for a snapshot (idempotent)."""
    conn.execute("DELETE FROM snapshot_holdings WHERE snapshot_id=?", (snapshot_id,))
    conn.executemany(
        """INSERT INTO snapshot_holdings (snapshot_id, ticker, name, price, quantity, weight, cost)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                snapshot_id,
                h.get("ticker", ""),
                h.get("name", ""),
                h.get("price"),
                h.get("quantity"),
                h.get("weights"),
                h.get("cost"),
            )
            for h in holdings
        ],
    )


def upsert_scores(
    conn: sqlite3.Connection,
    snapshot_id: int,
    scores: dict,
    qscore: float,
) -> None:
    conn.execute(
        """INSERT INTO snapshot_scores
               (snapshot_id, value_score, growth_score, health_score, momentum_score, past_performance, composite_qscore)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(snapshot_id) DO UPDATE SET
               value_score=excluded.value_score,
               growth_score=excluded.growth_score,
               health_score=excluded.health_score,
               momentum_score=excluded.momentum_score,
               past_performance=excluded.past_performance,
               composite_qscore=excluded.composite_qscore""",
        (
            snapshot_id,
            scores.get("value"),
            scores.get("growth"),
            scores.get("health"),
            scores.get("momentum"),
            scores.get("past_performance"),
            qscore,
        ),
    )


def upsert_signal(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """INSERT INTO ticker_signals
               (signal_date, ticker, frequency, total_portfolios, freq_pct,
                avg_weight, weighted_qscore, days_in_signal, conviction)
           VALUES (:signal_date, :ticker, :frequency, :total_portfolios, :freq_pct,
                   :avg_weight, :weighted_qscore, :days_in_signal, :conviction)
           ON CONFLICT(signal_date, ticker) DO UPDATE SET
               frequency=excluded.frequency,
               total_portfolios=excluded.total_portfolios,
               freq_pct=excluded.freq_pct,
               avg_weight=excluded.avg_weight,
               weighted_qscore=excluded.weighted_qscore,
               days_in_signal=excluded.days_in_signal,
               conviction=excluded.conviction""",
        row,
    )


def insert_alert(conn: sqlite3.Connection, alert: dict) -> bool:
    """Insert alert; returns True if new (not a duplicate)."""
    try:
        conn.execute(
            """INSERT INTO alerts
                   (alert_date, alert_type, ticker, conviction_before, conviction_after,
                    days_in_signal, message)
               VALUES (:alert_date, :alert_type, :ticker, :conviction_before,
                       :conviction_after, :days_in_signal, :message)""",
            alert,
        )
        return True
    except sqlite3.IntegrityError:
        return False  # duplicate


def mark_alert_sent(conn: sqlite3.Connection, alert_id: int) -> None:
    conn.execute(
        "UPDATE alerts SET telegram_sent=1, sent_at=CURRENT_TIMESTAMP WHERE id=?",
        (alert_id,),
    )


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_signals_for_date(
    conn: sqlite3.Connection, signal_date: date
) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ticker_signals WHERE signal_date=? ORDER BY conviction DESC",
        (signal_date.isoformat(),),
    ).fetchall()


def get_signal_for_ticker(
    conn: sqlite3.Connection, ticker: str, signal_date: date
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ticker_signals WHERE signal_date=? AND ticker=?",
        (signal_date.isoformat(), ticker),
    ).fetchone()


def _to_date(v) -> date:
    """Convert SQLite date value (str or date) to date object."""
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def get_latest_signal_date(conn: sqlite3.Connection) -> Optional[date]:
    row = conn.execute(
        "SELECT MAX(signal_date) AS d FROM ticker_signals"
    ).fetchone()
    if row and row["d"]:
        return _to_date(row["d"])
    return None


def get_previous_signal_date(
    conn: sqlite3.Connection, before: date
) -> Optional[date]:
    row = conn.execute(
        "SELECT MAX(signal_date) AS d FROM ticker_signals WHERE signal_date < ?",
        (before.isoformat(),),
    ).fetchone()
    if row and row["d"]:
        return _to_date(row["d"])
    return None


def date_has_snapshot(conn: sqlite3.Connection, snapshot_date: date) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM strategy_snapshots WHERE snapshot_date=?",
        (snapshot_date.isoformat(),),
    ).fetchone()
    return row["n"] > 0


def get_collection_log(
    conn: sqlite3.Connection, limit: int = 10
) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM collection_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def get_ticker_history(
    conn: sqlite3.Connection, ticker: str, days: int = 30
) -> List[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM ticker_signals
           WHERE ticker=?
           ORDER BY signal_date DESC
           LIMIT ?""",
        (ticker, days),
    ).fetchall()


def get_snapshots_for_date(
    conn: sqlite3.Connection, snapshot_date: date
) -> List[sqlite3.Row]:
    return conn.execute(
        """SELECT ss.*, sc.composite_qscore
           FROM strategy_snapshots ss
           LEFT JOIN snapshot_scores sc ON sc.snapshot_id = ss.id
           WHERE ss.snapshot_date = ?""",
        (snapshot_date.isoformat(),),
    ).fetchall()


def get_holdings_for_snapshot(
    conn: sqlite3.Connection, snapshot_id: int
) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM snapshot_holdings WHERE snapshot_id=?", (snapshot_id,)
    ).fetchall()
