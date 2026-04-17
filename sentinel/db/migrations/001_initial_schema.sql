-- SENTINEL DB — Migration 001: Initial schema
-- Applied automatically by SentinelDB._init_schema() on first run.
-- All tables use CREATE TABLE IF NOT EXISTS for idempotency.

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
    api_status      TEXT    DEFAULT 'ok',
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS config_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL,
    changed_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    field_name      TEXT    NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    approved_by     TEXT,
    approval_reason TEXT,
    detected_by     TEXT    DEFAULT 'sentinel'
);

CREATE TABLE IF NOT EXISTS deployment_certificates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL,
    certified_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    fingerprint     TEXT    NOT NULL,
    gates_passed    INTEGER DEFAULT 0,
    equivalence_days INTEGER DEFAULT 0,
    certified_by    TEXT    DEFAULT 'sentinel',
    grandfathered   INTEGER DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS alerts_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_time      TEXT    NOT NULL DEFAULT (datetime('now')),
    severity        TEXT    NOT NULL,
    experiment_id   TEXT,
    message         TEXT    NOT NULL,
    resolved        INTEGER DEFAULT 0,
    resolved_at     TEXT,
    resolved_by     TEXT,
    resolution_note TEXT
);

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
