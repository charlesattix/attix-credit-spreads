"""
Tests for SENTINEL G22 — scanner heartbeat gate.

Covers:
  - scanner_heartbeats schema (idempotent create)
  - record_heartbeat UPSERT semantics (insert + update last_seen)
  - get_heartbeats returns all known scanners
  - _is_market_hours_et utility (weekday RTH only)
  - check_scanner_heartbeats:
      * outside market hours → no alerts even if stale
      * fresh heartbeat → no alert
      * stale heartbeat during market hours → one warning alert
      * stale-duration text included in alert message
      * gate id is "G22"
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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


def _table_exists(db_path: str, table: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestScannerHeartbeatsSchema:
    def test_table_created_on_init(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        assert _table_exists(str(db.path), "scanner_heartbeats")

    def test_required_columns(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        cols = _columns(str(db.path), "scanner_heartbeats")
        assert {"scanner_id", "last_seen", "last_status", "notes"} <= cols

    def test_init_idempotent(self, tmp_path):
        path = str(tmp_path / "s.db")
        SentinelDB(path)
        SentinelDB(path)
        SentinelDB(path)
        assert _table_exists(path, "scanner_heartbeats")


# ---------------------------------------------------------------------------
# record_heartbeat / get_heartbeats
# ---------------------------------------------------------------------------


class TestRecordHeartbeat:
    def test_first_call_inserts(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        db.record_heartbeat("scan-EXP-503", status="ok", notes="cron tick")
        rows = db.get_heartbeats()
        assert len(rows) == 1
        assert rows[0]["scanner_id"] == "scan-EXP-503"
        assert rows[0]["last_status"] == "ok"
        assert rows[0]["notes"] == "cron tick"
        assert rows[0]["last_seen"] is not None

    def test_repeat_upserts_same_row(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        db.record_heartbeat("scan-EXP-503")
        first = db.get_heartbeats()[0]["last_seen"]
        # Force a different timestamp by passing one through the API.
        future = (
            datetime.now(timezone.utc) + timedelta(seconds=5)
        ).isoformat(timespec="seconds")
        db.record_heartbeat("scan-EXP-503", last_seen=future)
        rows = db.get_heartbeats()
        assert len(rows) == 1  # same row, not a new one
        assert rows[0]["last_seen"] == future
        assert rows[0]["last_seen"] != first

    def test_distinct_scanner_ids_get_distinct_rows(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        db.record_heartbeat("scan-EXP-503")
        db.record_heartbeat("scan-EXP-800")
        ids = {r["scanner_id"] for r in db.get_heartbeats()}
        assert ids == {"scan-EXP-503", "scan-EXP-800"}

    def test_default_status_ok(self, tmp_path):
        db = SentinelDB(str(tmp_path / "s.db"))
        db.record_heartbeat("scan-X")
        rows = db.get_heartbeats()
        assert rows[0]["last_status"] == "ok"


# ---------------------------------------------------------------------------
# Market-hours utility
# ---------------------------------------------------------------------------


class TestIsMarketHoursET:
    def test_weekday_rth_open(self):
        from sentinel.runtime import _is_market_hours_et
        # 2026-04-28 is a Tuesday.  14:30 UTC = 10:30 ET (DST).
        ts = datetime(2026, 4, 28, 14, 30, tzinfo=timezone.utc)
        assert _is_market_hours_et(ts) is True

    def test_weekday_before_open(self):
        from sentinel.runtime import _is_market_hours_et
        # 12:00 UTC Tuesday = 08:00 ET (pre-market).
        ts = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
        assert _is_market_hours_et(ts) is False

    def test_weekday_after_close(self):
        from sentinel.runtime import _is_market_hours_et
        # 21:00 UTC Tuesday = 17:00 ET (after-hours).
        ts = datetime(2026, 4, 28, 21, 0, tzinfo=timezone.utc)
        assert _is_market_hours_et(ts) is False

    def test_saturday_closed(self):
        from sentinel.runtime import _is_market_hours_et
        # 2026-05-02 is Saturday.
        ts = datetime(2026, 5, 2, 15, 0, tzinfo=timezone.utc)
        assert _is_market_hours_et(ts) is False

    def test_sunday_closed(self):
        from sentinel.runtime import _is_market_hours_et
        # 2026-05-03 is Sunday.
        ts = datetime(2026, 5, 3, 15, 0, tzinfo=timezone.utc)
        assert _is_market_hours_et(ts) is False


# ---------------------------------------------------------------------------
# check_scanner_heartbeats
# ---------------------------------------------------------------------------


class TestCheckScannerHeartbeats:
    def _market_hours_now(self) -> datetime:
        # 2026-04-28 14:30 UTC = Tuesday 10:30 ET.
        return datetime(2026, 4, 28, 14, 30, tzinfo=timezone.utc)

    def _after_hours_now(self) -> datetime:
        # 2026-04-28 22:00 UTC = Tuesday 18:00 ET.
        return datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)

    def test_outside_market_hours_returns_no_alerts(self, tmp_path):
        from sentinel.runtime import check_scanner_heartbeats
        db = SentinelDB(str(tmp_path / "s.db"))
        # Stale heartbeat — 2 days old.
        old = (self._after_hours_now() - timedelta(days=2)).isoformat(timespec="seconds")
        db.record_heartbeat("scan-EXP-503", last_seen=old)

        alerts = check_scanner_heartbeats(db, now=self._after_hours_now())
        assert alerts == []

    def test_fresh_heartbeat_during_market_hours_no_alert(self, tmp_path):
        from sentinel.runtime import check_scanner_heartbeats
        db = SentinelDB(str(tmp_path / "s.db"))
        recent = (self._market_hours_now() - timedelta(minutes=2)).isoformat(timespec="seconds")
        db.record_heartbeat("scan-EXP-503", last_seen=recent)

        alerts = check_scanner_heartbeats(db, now=self._market_hours_now())
        assert alerts == []

    def test_stale_heartbeat_during_market_hours_emits_alert(self, tmp_path):
        from sentinel.runtime import check_scanner_heartbeats
        db = SentinelDB(str(tmp_path / "s.db"))
        stale = (self._market_hours_now() - timedelta(minutes=45)).isoformat(timespec="seconds")
        db.record_heartbeat("scan-EXP-503", last_seen=stale)

        alerts = check_scanner_heartbeats(db, now=self._market_hours_now())
        assert len(alerts) == 1
        a = alerts[0]
        assert a["gate"] == "G22"
        assert a["severity"] == "warning"
        assert a["scanner_id"] == "scan-EXP-503"
        assert "scan-EXP-503" in a["message"]

    def test_no_known_scanners_no_alerts(self, tmp_path):
        from sentinel.runtime import check_scanner_heartbeats
        db = SentinelDB(str(tmp_path / "s.db"))
        alerts = check_scanner_heartbeats(db, now=self._market_hours_now())
        assert alerts == []

    def test_only_stale_scanners_alerted(self, tmp_path):
        from sentinel.runtime import check_scanner_heartbeats
        db = SentinelDB(str(tmp_path / "s.db"))
        recent = (self._market_hours_now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        stale = (self._market_hours_now() - timedelta(minutes=120)).isoformat(timespec="seconds")
        db.record_heartbeat("scan-fresh", last_seen=recent)
        db.record_heartbeat("scan-stale", last_seen=stale)

        alerts = check_scanner_heartbeats(db, now=self._market_hours_now())
        ids = {a["scanner_id"] for a in alerts}
        assert ids == {"scan-stale"}

    def test_threshold_is_configurable(self, tmp_path):
        from sentinel.runtime import check_scanner_heartbeats
        db = SentinelDB(str(tmp_path / "s.db"))
        # 20 min stale.
        ts = (self._market_hours_now() - timedelta(minutes=20)).isoformat(timespec="seconds")
        db.record_heartbeat("scan-A", last_seen=ts)

        # Default 30 min: not stale.
        assert check_scanner_heartbeats(db, now=self._market_hours_now()) == []

        # Tighter 10 min: stale.
        alerts = check_scanner_heartbeats(
            db, now=self._market_hours_now(), threshold_minutes=10,
        )
        assert len(alerts) == 1
