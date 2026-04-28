"""
SENTINEL — History DB

SQLite audit trail for all experiment governance data.

Tables
------
  experiment_snapshots   — daily equity / position snapshots
  config_changes         — config drift log with change approvals
  deployment_certificates— per-experiment certification records
  alerts_log             — alert history with resolution tracking

Usage
-----
  from sentinel.history import SentinelDB
  db = SentinelDB()                         # uses sentinel/db/sentinel.db
  db.record_snapshot("EXP-400", equity=84977, ...)
  db.record_alert("critical", "EXP-800", "Config drift detected")

All public methods are safe to call concurrently (WAL mode).
"""

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Dedup window for record_alert: repeat alerts within this many minutes
# update an existing open row instead of creating a new one.
_DEDUP_WINDOW_MIN = 5


def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    """Parse either 'YYYY-MM-DD HH:MM:SS' (sqlite datetime('now')) or ISO 8601."""
    if not s:
        return None
    try:
        cleaned = s.replace(" ", "T", 1) if "T" not in s else s
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

# Default DB path — lives beside this file in sentinel/db/sentinel.db
_SENTINEL_DIR = Path(__file__).parent
_DEFAULT_DB_PATH = Path(
    os.environ.get("SENTINEL_DB_PATH", str(_SENTINEL_DIR / "db" / "sentinel.db"))
)

# ─────────────────────────────────────────────────────────────────────────────
# Schema SQL
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
-- Tracks daily equity/position snapshots per experiment.
-- Config hash lets us correlate performance against config versions.
CREATE TABLE IF NOT EXISTS experiment_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL,
    snapshot_time   TEXT    NOT NULL DEFAULT (datetime('now')),
    equity          REAL,
    open_positions  INTEGER DEFAULT 0,
    day_pnl         REAL,
    total_pnl       REAL,
    total_trades    INTEGER,
    win_rate        REAL,
    config_hash     TEXT,
    api_status      TEXT    DEFAULT 'ok',   -- 'ok' | '401' | 'timeout' | 'error'
    notes           TEXT
);

-- Records every detected config drift event.
-- approved_by=NULL means unauthorized change (sentinel blocks experiment).
-- approved_by='carlos'/'charles' means operator-approved intentional change.
CREATE TABLE IF NOT EXISTS config_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL,
    changed_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    field_name      TEXT    NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    approved_by     TEXT,               -- NULL = unauthorized
    approval_reason TEXT,
    detected_by     TEXT    DEFAULT 'sentinel'
);

-- Issued when an experiment passes all 5 gates (or is retroactively onboarded).
-- fingerprint is SHA-256 of locked parameter set.
-- gates_passed=0 and certified_by='retroactive_onboard' marks grandfathered entries.
CREATE TABLE IF NOT EXISTS deployment_certificates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL,
    certified_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    fingerprint     TEXT    NOT NULL,
    gates_passed    INTEGER DEFAULT 0,    -- max 10 (Gate 5 pre-flight)
    equivalence_days INTEGER DEFAULT 0,   -- max 5 (Gate 2+ behavioral test)
    certified_by    TEXT    DEFAULT 'sentinel',
    grandfathered   INTEGER DEFAULT 0,    -- 1 if retroactively registered
    notes           TEXT
);

-- Alert log with full resolution history.
-- resolved=0 means the alert is still open.
CREATE TABLE IF NOT EXISTS alerts_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_time      TEXT    NOT NULL DEFAULT (datetime('now')),
    severity        TEXT    NOT NULL,   -- 'critical' | 'warning' | 'info'
    experiment_id   TEXT,               -- NULL = system-wide alert
    message         TEXT    NOT NULL,
    resolved        INTEGER DEFAULT 0,
    resolved_at     TEXT,
    resolved_by     TEXT,
    resolution_note TEXT
);

-- Per-scanner heartbeat (G22).  scanner_id is opaque (e.g. 'scan-EXP-503').
-- record_heartbeat() is an UPSERT — there is exactly one row per scanner.
CREATE TABLE IF NOT EXISTS scanner_heartbeats (
    scanner_id   TEXT PRIMARY KEY,
    last_seen    TEXT NOT NULL,
    last_status  TEXT DEFAULT 'ok',
    notes        TEXT
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_snapshots_exp_time
    ON experiment_snapshots (experiment_id, snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_changes_exp
    ON config_changes (experiment_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_certs_exp
    ON deployment_certificates (experiment_id, certified_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_open
    ON alerts_log (resolved, severity, alert_time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_exp
    ON alerts_log (experiment_id, alert_time DESC);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Fingerprint helpers
# ─────────────────────────────────────────────────────────────────────────────

# Parameters extracted from the paper YAML config when building a fingerprint.
# Grouped by sensitivity tier (matches Gate 2 in the proposal).
_ZERO_TOLERANCE_KEYS = [
    # strategy section
    ("strategy", "direction"),
    ("strategy", "regime_mode"),
    # top-level
    ("tickers",),          # list of tickers
]
_TIGHT_KEYS = [
    ("strategy", "min_dte"),
    ("strategy", "max_dte"),
    ("strategy", "target_dte"),
    ("strategy", "spread_width"),
    ("strategy", "otm_pct"),
    ("risk", "stop_loss_multiplier"),
    ("risk", "max_risk_per_trade"),
    ("risk", "profit_target"),
    ("risk", "drawdown_cb_pct"),
]
_LOOSE_KEYS = [
    ("risk", "max_positions"),
    ("risk", "max_contracts"),
]


def _get_nested(d: dict, *keys) -> Any:
    """Walk a nested dict with multiple keys; return None if any key is missing."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def compute_fingerprint(config: dict) -> str:
    """
    Compute a deterministic SHA-256 fingerprint over the locked parameters.

    Only includes parameters from the zero-tolerance and tight-tolerance tiers
    (Gate 2).  Loose/monitor parameters are excluded to avoid spurious drift
    signals on operational tuning.

    Returns "sha256:<hex>" string.
    """
    locked: Dict[str, Any] = {}

    for path in _ZERO_TOLERANCE_KEYS + _TIGHT_KEYS + _LOOSE_KEYS:
        val = _get_nested(config, *path)
        key = ".".join(str(k) for k in path)
        locked[key] = val

    canonical = json.dumps(locked, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{digest[:16]}"   # first 16 hex = 64-bit prefix (readable)


# ─────────────────────────────────────────────────────────────────────────────
# Main class
# ─────────────────────────────────────────────────────────────────────────────


class SentinelDB:
    """
    Thin wrapper around the sentinel SQLite database.

    Initialise once and reuse; each method opens + closes its own connection
    so calls are safe to use from concurrent processes (WAL mode).
    """

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else _DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA_SQL)
            self._migrate_alerts_log_dedup(conn)
            conn.commit()
            logger.debug("SentinelDB: schema initialised at %s", self.path)
        finally:
            conn.close()

    @staticmethod
    def _migrate_alerts_log_dedup(conn: sqlite3.Connection) -> None:
        """
        Idempotently add (count, first_seen, last_seen) columns to alerts_log.

        Keeps existing rows intact: legacy entries get count=1 default and
        NULL timestamps until they are touched again.
        """
        cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts_log)").fetchall()}
        if "count" not in cols:
            conn.execute("ALTER TABLE alerts_log ADD COLUMN count INTEGER DEFAULT 1")
        if "first_seen" not in cols:
            conn.execute("ALTER TABLE alerts_log ADD COLUMN first_seen TEXT")
        if "last_seen" not in cols:
            conn.execute("ALTER TABLE alerts_log ADD COLUMN last_seen TEXT")

    # ── Snapshots ─────────────────────────────────────────────────────────────

    def record_snapshot(
        self,
        experiment_id: str,
        *,
        equity: Optional[float] = None,
        open_positions: int = 0,
        day_pnl: Optional[float] = None,
        total_pnl: Optional[float] = None,
        total_trades: Optional[int] = None,
        win_rate: Optional[float] = None,
        config_hash: Optional[str] = None,
        api_status: str = "ok",
        notes: Optional[str] = None,
    ) -> int:
        """Insert a daily snapshot; returns the new row id."""
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO experiment_snapshots
                    (experiment_id, equity, open_positions, day_pnl, total_pnl,
                     total_trades, win_rate, config_hash, api_status, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (experiment_id, equity, open_positions, day_pnl, total_pnl,
                 total_trades, win_rate, config_hash, api_status, notes),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_snapshots(
        self,
        experiment_id: str,
        limit: int = 90,
    ) -> List[Dict[str, Any]]:
        """Return the *limit* most recent snapshots for an experiment."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM experiment_snapshots
                WHERE  experiment_id = ?
                ORDER  BY snapshot_time DESC
                LIMIT  ?
                """,
                (experiment_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Config changes ────────────────────────────────────────────────────────

    def record_config_change(
        self,
        experiment_id: str,
        field_name: str,
        old_value: Any,
        new_value: Any,
        *,
        approved_by: Optional[str] = None,
        approval_reason: Optional[str] = None,
        detected_by: str = "sentinel",
    ) -> int:
        """Log a config drift event; returns the new row id."""
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO config_changes
                    (experiment_id, field_name, old_value, new_value,
                     approved_by, approval_reason, detected_by)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    experiment_id,
                    field_name,
                    json.dumps(old_value, default=str),
                    json.dumps(new_value, default=str),
                    approved_by,
                    approval_reason,
                    detected_by,
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def approve_config_change(
        self,
        change_id: int,
        approved_by: str,
        reason: str,
    ) -> bool:
        """Mark a config change as approved; returns True on success."""
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                UPDATE config_changes
                SET    approved_by=?, approval_reason=?
                WHERE  id=? AND approved_by IS NULL
                """,
                (approved_by, reason, change_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_config_changes(
        self,
        experiment_id: Optional[str] = None,
        unapproved_only: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            q = "SELECT * FROM config_changes WHERE 1=1"
            params: list = []
            if experiment_id:
                q += " AND experiment_id=?"
                params.append(experiment_id)
            if unapproved_only:
                q += " AND approved_by IS NULL"
            q += " ORDER BY changed_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Deployment certificates ───────────────────────────────────────────────

    def record_deployment_cert(
        self,
        experiment_id: str,
        fingerprint: str,
        gates_passed: int = 10,
        equivalence_days: int = 5,
        *,
        certified_by: str = "sentinel",
        grandfathered: bool = False,
        notes: Optional[str] = None,
    ) -> int:
        """Issue or update a deployment certificate; returns the new row id."""
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO deployment_certificates
                    (experiment_id, fingerprint, gates_passed, equivalence_days,
                     certified_by, grandfathered, notes)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    experiment_id, fingerprint, gates_passed, equivalence_days,
                    certified_by, int(grandfathered), notes,
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_latest_cert(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        """Return the most recent deployment certificate for an experiment."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM deployment_certificates
                WHERE  experiment_id=?
                ORDER  BY certified_at DESC
                LIMIT  1
                """,
                (experiment_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_certs(self) -> List[Dict[str, Any]]:
        """Return the latest certificate for every experiment."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT dc.*
                FROM   deployment_certificates dc
                INNER JOIN (
                    SELECT experiment_id, MAX(certified_at) AS max_cert
                    FROM   deployment_certificates
                    GROUP  BY experiment_id
                ) latest ON dc.experiment_id = latest.experiment_id
                         AND dc.certified_at = latest.max_cert
                ORDER  BY dc.experiment_id
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Alerts ────────────────────────────────────────────────────────────────

    def record_alert(
        self,
        severity: str,
        message: str,
        *,
        experiment_id: Optional[str] = None,
    ) -> int:
        """
        Record an alert with dedup-on-repeat semantics.

        If an *unresolved* alert with the same (severity, experiment_id,
        first 280 chars of message) was last seen within the dedup window
        (_DEDUP_WINDOW_MIN), this call updates that row's count and
        last_seen, and returns its id.  Otherwise a new row is inserted.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat(timespec="seconds")
        msg_key = message[:280]
        cutoff = now - timedelta(minutes=_DEDUP_WINDOW_MIN)

        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id, alert_time, first_seen, last_seen, count
                FROM   alerts_log
                WHERE  resolved = 0
                  AND  severity = ?
                  AND  substr(message, 1, 280) = ?
                  AND  ((experiment_id IS NULL AND ? IS NULL)
                        OR experiment_id = ?)
                ORDER  BY id DESC
                LIMIT  1
                """,
                (severity, msg_key, experiment_id, experiment_id),
            ).fetchone()

            if row is not None:
                ref_ts = _parse_ts(row["last_seen"]) or _parse_ts(row["alert_time"])
                if ref_ts is not None and ref_ts >= cutoff:
                    conn.execute(
                        """
                        UPDATE alerts_log
                        SET    count     = COALESCE(count, 1) + 1,
                               last_seen = ?
                        WHERE  id = ?
                        """,
                        (now_iso, row["id"]),
                    )
                    conn.commit()
                    return int(row["id"])

            cur = conn.execute(
                """
                INSERT INTO alerts_log
                    (alert_time, severity, experiment_id, message,
                     count, first_seen, last_seen)
                VALUES (?,?,?,?,?,?,?)
                """,
                (now_iso, severity, experiment_id, message, 1, now_iso, now_iso),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def resolve_alert(
        self,
        alert_id: int,
        resolved_by: str,
        resolution_note: str,
    ) -> bool:
        """Mark an alert resolved; returns True on success."""
        conn = self._connect()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                """
                UPDATE alerts_log
                SET    resolved=1, resolved_at=?, resolved_by=?, resolution_note=?
                WHERE  id=? AND resolved=0
                """,
                (now, resolved_by, resolution_note, alert_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_active_alerts(
        self,
        experiment_id: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return all unresolved alerts, optionally filtered."""
        conn = self._connect()
        try:
            q = "SELECT * FROM alerts_log WHERE resolved=0"
            params: list = []
            if experiment_id:
                q += " AND experiment_id=?"
                params.append(experiment_id)
            if severity:
                q += " AND severity=?"
                params.append(severity)
            q += " ORDER BY alert_time DESC"
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_alerts(
        self,
        experiment_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            q = "SELECT * FROM alerts_log WHERE 1=1"
            params: list = []
            if experiment_id:
                q += " AND experiment_id=?"
                params.append(experiment_id)
            q += " ORDER BY alert_time DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Scanner heartbeats (G22) ──────────────────────────────────────────────

    def record_heartbeat(
        self,
        scanner_id: str,
        *,
        status: str = "ok",
        notes: Optional[str] = None,
        last_seen: Optional[str] = None,
    ) -> None:
        """
        UPSERT a scanner heartbeat.  Called once per scanner tick.

        Exactly one row per scanner_id; subsequent calls update last_seen,
        last_status, and notes in place.  *last_seen* defaults to now (UTC,
        ISO8601, seconds resolution) but can be supplied for tests.
        """
        ts = last_seen or datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO scanner_heartbeats (scanner_id, last_seen, last_status, notes)
                VALUES (?,?,?,?)
                ON CONFLICT(scanner_id) DO UPDATE SET
                    last_seen   = excluded.last_seen,
                    last_status = excluded.last_status,
                    notes       = excluded.notes
                """,
                (scanner_id, ts, status, notes),
            )
            conn.commit()
        finally:
            conn.close()

    def get_heartbeats(self) -> List[Dict[str, Any]]:
        """Return all known scanner heartbeats, most-recent first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM scanner_heartbeats ORDER BY last_seen DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Timeline (for --history CLI) ──────────────────────────────────────────

    def get_experiment_timeline(self, experiment_id: str) -> Dict[str, Any]:
        """
        Return a unified timeline for one experiment, suitable for the
        --history CLI command and per-experiment HTML report.
        """
        cert = self.get_latest_cert(experiment_id)
        snapshots = self.get_snapshots(experiment_id, limit=180)
        changes = self.get_config_changes(experiment_id, limit=50)
        alerts = self.get_all_alerts(experiment_id, limit=50)
        active_alerts = [a for a in alerts if not a["resolved"]]

        return {
            "experiment_id": experiment_id,
            "certificate": cert,
            "snapshots": snapshots,
            "config_changes": changes,
            "alerts": alerts,
            "active_alerts": active_alerts,
        }

    # ── Summary (for daily report) ────────────────────────────────────────────

    def get_daily_summary(
        self,
        experiment_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Return aggregated data for the daily health report.

        Collects latest snapshot, latest cert, and open alerts for each
        experiment.  If *experiment_ids* is None, returns data for all
        experiments with at least one snapshot.
        """
        conn = self._connect()
        try:
            if experiment_ids is None:
                rows = conn.execute(
                    "SELECT DISTINCT experiment_id FROM experiment_snapshots"
                ).fetchall()
                experiment_ids = [r[0] for r in rows]
        finally:
            conn.close()

        experiments: Dict[str, Any] = {}
        for exp_id in sorted(experiment_ids):
            snaps = self.get_snapshots(exp_id, limit=1)
            latest = snaps[0] if snaps else None
            cert = self.get_latest_cert(exp_id)
            alerts = self.get_active_alerts(exp_id)
            experiments[exp_id] = {
                "latest_snapshot": latest,
                "certificate": cert,
                "active_alerts": alerts,
            }

        all_alerts = self.get_active_alerts()
        crit = [a for a in all_alerts if a["severity"] == "critical"]
        warn = [a for a in all_alerts if a["severity"] == "warning"]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "experiments": experiments,
            "total_active_alerts": len(all_alerts),
            "critical_alerts": crit,
            "warning_alerts": warn,
        }
