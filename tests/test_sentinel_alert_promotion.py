"""
Tests for SENTINEL alert promotion + alerts_log dedup migration.

Covers:
  - alerts_log dedup schema (count, first_seen, last_seen) — new DB + legacy migration
  - record_alert dedup window (5 min, scoped by severity + experiment + message[:280])
  - record_alert resolved-aware dedup (resolved alerts do not absorb new occurrences)
  - SentinelAlertLogHandler severity floor (only ERROR+ promoted; CRITICAL maps to "critical")
  - SentinelAlertLogHandler allowlist (execution., shared., strategy., strategies., sentinel., ml., compass.)
  - SentinelAlertLogHandler explicit excludes (sentinel.history, sentinel.alerter)
  - SentinelAlertLogHandler EXPERIMENT_ID env var wiring
  - install_log_handler idempotency + QueueHandler installation
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.history import SentinelDB  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _columns(db_path: str, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def _row(db: SentinelDB, alert_id: int) -> dict:
    conn = sqlite3.connect(str(db.path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM alerts_log WHERE id=?", (alert_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _make_record(name: str, level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


class TestAlertsLogDedupSchema:
    def test_new_db_has_dedup_columns(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        cols = _columns(str(db.path), "alerts_log")
        assert {"count", "first_seen", "last_seen"} <= cols

    def test_init_schema_idempotent(self, tmp_path):
        db_path = str(tmp_path / "s.db")
        SentinelDB(db_path)
        SentinelDB(db_path)
        SentinelDB(db_path)
        # No duplicate columns
        conn = sqlite3.connect(db_path)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(alerts_log)").fetchall()]
        finally:
            conn.close()
        assert cols.count("count") == 1
        assert cols.count("first_seen") == 1
        assert cols.count("last_seen") == 1

    def test_legacy_db_gets_migrated_without_data_loss(self, tmp_path):
        db_path = str(tmp_path / "s.db")
        # Simulate a pre-migration alerts_log
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE alerts_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_time      TEXT    NOT NULL DEFAULT (datetime('now')),
                    severity        TEXT    NOT NULL,
                    experiment_id   TEXT,
                    message         TEXT    NOT NULL,
                    resolved        INTEGER DEFAULT 0,
                    resolved_at     TEXT,
                    resolved_by     TEXT,
                    resolution_note TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO alerts_log (severity, experiment_id, message) VALUES (?,?,?)",
                ("warning", "EXP-LEGACY", "old message"),
            )
            conn.commit()
        finally:
            conn.close()

        db = SentinelDB(db_path)
        cols = _columns(db_path, "alerts_log")
        assert {"count", "first_seen", "last_seen"} <= cols

        rows = db.get_all_alerts()
        assert any(r["message"] == "old message" for r in rows)


# ---------------------------------------------------------------------------
# record_alert dedup
# ---------------------------------------------------------------------------


class TestRecordAlertDedup:
    def test_first_call_inserts_with_count_one(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid = db.record_alert("warning", "msg", experiment_id="EXP-1")
        row = _row(db, rid)
        assert row["count"] == 1
        assert row["first_seen"] is not None
        assert row["last_seen"] is not None
        assert row["resolved"] == 0

    def test_repeat_within_window_increments_count_on_same_row(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid1 = db.record_alert("warning", "same msg", experiment_id="EXP-1")
        rid2 = db.record_alert("warning", "same msg", experiment_id="EXP-1")
        rid3 = db.record_alert("warning", "same msg", experiment_id="EXP-1")
        assert rid1 == rid2 == rid3
        row = _row(db, rid1)
        assert row["count"] == 3

    def test_different_severity_does_not_dedup(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid1 = db.record_alert("warning", "msg", experiment_id="EXP-1")
        rid2 = db.record_alert("critical", "msg", experiment_id="EXP-1")
        assert rid1 != rid2

    def test_different_experiment_does_not_dedup(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid1 = db.record_alert("warning", "msg", experiment_id="EXP-1")
        rid2 = db.record_alert("warning", "msg", experiment_id="EXP-2")
        assert rid1 != rid2

    def test_different_message_does_not_dedup(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid1 = db.record_alert("warning", "msg A", experiment_id="EXP-1")
        rid2 = db.record_alert("warning", "msg B", experiment_id="EXP-1")
        assert rid1 != rid2

    def test_dedup_keys_off_first_280_chars(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        prefix = "x" * 280
        rid1 = db.record_alert("warning", prefix + " divergent A", experiment_id="EXP-1")
        rid2 = db.record_alert("warning", prefix + " divergent B", experiment_id="EXP-1")
        assert rid1 == rid2
        assert _row(db, rid1)["count"] == 2

    def test_outside_window_creates_new_row(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid1 = db.record_alert("warning", "msg", experiment_id="EXP-1")
        # Backdate this row's last_seen so it falls outside the dedup window.
        past_iso = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).isoformat(timespec="seconds")
        conn = sqlite3.connect(str(db.path))
        try:
            conn.execute(
                "UPDATE alerts_log SET first_seen=?, last_seen=?, alert_time=? WHERE id=?",
                (past_iso, past_iso, past_iso, rid1),
            )
            conn.commit()
        finally:
            conn.close()
        rid2 = db.record_alert("warning", "msg", experiment_id="EXP-1")
        assert rid2 != rid1
        assert _row(db, rid2)["count"] == 1

    def test_resolved_alert_does_not_absorb_new_occurrence(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid1 = db.record_alert("warning", "msg", experiment_id="EXP-1")
        db.resolve_alert(rid1, resolved_by="op", resolution_note="fixed")
        rid2 = db.record_alert("warning", "msg", experiment_id="EXP-1")
        assert rid2 != rid1
        # original stays resolved
        assert _row(db, rid1)["resolved"] == 1
        assert _row(db, rid2)["resolved"] == 0
        assert _row(db, rid2)["count"] == 1

    def test_system_wide_alert_dedup_with_null_experiment(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid1 = db.record_alert("warning", "system msg")
        rid2 = db.record_alert("warning", "system msg")
        assert rid1 == rid2
        assert _row(db, rid1)["count"] == 2

    def test_system_wide_does_not_match_experiment_scoped(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        rid1 = db.record_alert("warning", "msg")
        rid2 = db.record_alert("warning", "msg", experiment_id="EXP-1")
        assert rid1 != rid2


# ---------------------------------------------------------------------------
# SentinelAlertLogHandler
# ---------------------------------------------------------------------------


class TestSentinelAlertLogHandler:
    def _handler(self, db: SentinelDB):
        from sentinel.alerter import SentinelAlertLogHandler
        return SentinelAlertLogHandler(db=db)

    def test_below_error_is_dropped(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        h.emit(_make_record("execution.position_monitor", logging.WARNING, "warn"))
        h.emit(_make_record("execution.position_monitor", logging.INFO, "info"))
        assert db.get_all_alerts() == []

    def test_error_maps_to_warning_severity(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        h.emit(_make_record("execution.position_monitor", logging.ERROR, "err"))
        rows = db.get_all_alerts()
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"

    def test_critical_maps_to_critical_severity(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        h.emit(_make_record("execution.position_monitor", logging.CRITICAL, "boom"))
        rows = db.get_all_alerts()
        assert len(rows) == 1
        assert rows[0]["severity"] == "critical"

    def test_unallowed_module_dropped(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        h.emit(_make_record("urllib3.connectionpool", logging.ERROR, "ignored"))
        h.emit(_make_record("requests.api", logging.CRITICAL, "ignored"))
        h.emit(_make_record("__main__", logging.ERROR, "ignored"))
        assert db.get_all_alerts() == []

    def test_allowlist_includes_all_required_prefixes(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        names = [
            "execution.position_monitor",
            "shared.scheduler",
            "strategy.alpaca_provider",
            "strategies.credit_spread",
            "sentinel.runtime",
            "sentinel.guards",
            "sentinel.orchestrator",
            "ml.regime_model_router",
            "compass.production_monitor",
        ]
        for name in names:
            h.emit(_make_record(name, logging.ERROR, f"from {name}"))
        rows = db.get_all_alerts()
        assert len(rows) == len(names)
        seen_names = {r["message"] for r in rows}
        for name in names:
            assert any(name in m for m in seen_names)

    def test_excludes_sentinel_history(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        h.emit(_make_record("sentinel.history", logging.ERROR, "would recurse"))
        h.emit(_make_record("sentinel.history", logging.CRITICAL, "still recurse"))
        assert db.get_all_alerts() == []

    def test_excludes_sentinel_alerter(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        h.emit(_make_record("sentinel.alerter", logging.ERROR, "self-loop"))
        assert db.get_all_alerts() == []

    def test_message_format_includes_logger_name_and_text(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        h.emit(_make_record("execution.position_monitor", logging.ERROR, "broken pipe"))
        rows = db.get_all_alerts()
        assert "execution.position_monitor" in rows[0]["message"]
        assert "broken pipe" in rows[0]["message"]

    def test_long_message_is_truncated_at_280(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        long_msg = "y" * 1000
        h.emit(_make_record("execution.position_monitor", logging.ERROR, long_msg))
        rows = db.get_all_alerts()
        assert len(rows[0]["message"]) <= 280

    def test_experiment_id_picked_up_from_env(self, tmp_path, monkeypatch):
        db = SentinelDB(str(tmp_path / "s.db"))
        monkeypatch.setenv("EXPERIMENT_ID", "EXP-503")
        h = self._handler(db)
        h.emit(_make_record("execution.position_monitor", logging.ERROR, "msg"))
        rows = db.get_all_alerts()
        assert rows[0]["experiment_id"] == "EXP-503"

    def test_experiment_id_null_when_env_absent(self, tmp_path, monkeypatch):
        db = SentinelDB(str(tmp_path / "s.db"))
        monkeypatch.delenv("EXPERIMENT_ID", raising=False)
        h = self._handler(db)
        h.emit(_make_record("execution.position_monitor", logging.ERROR, "sys-wide"))
        rows = db.get_all_alerts()
        assert rows[0]["experiment_id"] is None

    def test_repeated_errors_dedup_via_record_alert(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        h = self._handler(db)
        for _ in range(5):
            h.emit(_make_record("execution.position_monitor", logging.ERROR, "same"))
        rows = db.get_all_alerts()
        assert len(rows) == 1
        assert rows[0]["count"] == 5

    def test_emit_swallows_db_exceptions(self):
        from sentinel.alerter import SentinelAlertLogHandler
        broken = MagicMock()
        broken.record_alert.side_effect = RuntimeError("db dead")
        h = SentinelAlertLogHandler(db=broken)
        # Logging must never raise — handleError absorbs it.
        h.emit(_make_record("execution.foo", logging.ERROR, "msg"))


# ---------------------------------------------------------------------------
# install_log_handler
# ---------------------------------------------------------------------------


class TestInstallLogHandler:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        # Route any default SentinelDB() created inside the listener to a tmp path.
        monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "s.db"))
        from sentinel.alerter import _uninstall_log_handler
        root = logging.getLogger()
        before = list(root.handlers)
        try:
            yield
        finally:
            _uninstall_log_handler()
            for h in list(root.handlers):
                if h not in before:
                    root.removeHandler(h)

    def test_install_adds_a_queue_handler_to_root(self):
        from sentinel.alerter import install_log_handler
        install_log_handler(experiment_id="EXP-X")
        root = logging.getLogger()
        assert any(isinstance(h, logging.handlers.QueueHandler) for h in root.handlers)

    def test_install_is_idempotent(self):
        from sentinel.alerter import install_log_handler
        install_log_handler(experiment_id="EXP-X")
        install_log_handler(experiment_id="EXP-X")
        install_log_handler(experiment_id="EXP-X")
        root = logging.getLogger()
        qhs = [h for h in root.handlers if isinstance(h, logging.handlers.QueueHandler)]
        assert len(qhs) == 1

    def test_install_sets_experiment_id_env_when_unset(self, monkeypatch):
        from sentinel.alerter import install_log_handler
        monkeypatch.delenv("EXPERIMENT_ID", raising=False)
        install_log_handler(experiment_id="EXP-700")
        assert os.environ.get("EXPERIMENT_ID") == "EXP-700"

    def test_install_does_not_overwrite_pre_existing_env(self, monkeypatch):
        from sentinel.alerter import install_log_handler
        monkeypatch.setenv("EXPERIMENT_ID", "EXP-PRESET")
        install_log_handler(experiment_id="EXP-LATER")
        assert os.environ["EXPERIMENT_ID"] == "EXP-PRESET"
