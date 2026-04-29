"""
Tests for the Sentinel push pipeline observability.

Verifies:
  - sync_sentinel_data.push_to_railway writes data/.last_push.json on every
    attempt (success AND failure)
  - run_sentinel.check_push_pipeline_freshness emits a CRITICAL alert when
    the last push is missing, failed, or stale
  - the meta-monitor stays silent when the pipeline is healthy
"""

from __future__ import annotations

import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Ensure project root on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_db():
    """Stand-in SentinelDB exposing only record_alert."""
    db = MagicMock()
    db.record_alert = MagicMock()
    return db


# ─────────────────────────────────────────────────────────────────────────────
# Sync-side observability — .last_push.json on every attempt
# ─────────────────────────────────────────────────────────────────────────────


def test_last_push_written_on_success(tmp_path, monkeypatch):
    """200 OK push must write data/.last_push.json with ok=True."""
    import scripts.sync_sentinel_data as sync_mod

    last_push = tmp_path / ".last_push.json"
    monkeypatch.setattr(sync_mod, "LAST_PUSH_PATH", last_push)

    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = json.dumps({"pushed_at": "2026-04-29T10:00:00Z"}).encode()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None

    with patch("urllib.request.urlopen", return_value=fake_resp):
        ok = sync_mod.push_to_railway(
            payload={"x": 1},
            railway_url="https://example.com",
            token="abc",
        )

    assert ok is True
    assert last_push.exists()
    record = json.loads(last_push.read_text())
    assert record["ok"] is True
    assert record["http_status"] == 200
    assert record["railway_pushed_at"] == "2026-04-29T10:00:00Z"
    assert "attempted_at" in record


def test_last_push_written_on_404(tmp_path, monkeypatch):
    """A 404 (the actual prod failure mode) must record the failure."""
    import scripts.sync_sentinel_data as sync_mod

    last_push = tmp_path / ".last_push.json"
    monkeypatch.setattr(sync_mod, "LAST_PUSH_PATH", last_push)

    err = urllib.error.HTTPError(
        url="https://example.com/api/admin/push-sentinel",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=err):
        ok = sync_mod.push_to_railway(
            payload={"x": 1},
            railway_url="https://example.com",
            token="abc",
        )

    assert ok is False
    assert last_push.exists()
    record = json.loads(last_push.read_text())
    assert record["ok"] is False
    assert record["http_status"] == 404


def test_last_push_written_on_network_failure(tmp_path, monkeypatch):
    import scripts.sync_sentinel_data as sync_mod

    last_push = tmp_path / ".last_push.json"
    monkeypatch.setattr(sync_mod, "LAST_PUSH_PATH", last_push)

    with patch("urllib.request.urlopen", side_effect=OSError("DNS fail")):
        ok = sync_mod.push_to_railway(
            payload={"x": 1},
            railway_url="https://example.com",
            token="abc",
        )

    assert ok is False
    record = json.loads(last_push.read_text())
    assert record["ok"] is False
    assert "DNS fail" in (record.get("error") or "")


def test_push_uses_bearer_auth_header(monkeypatch, tmp_path):
    """sync must send Authorization: Bearer (NOT X-API-Key) — the prod-broken header."""
    import scripts.sync_sentinel_data as sync_mod

    monkeypatch.setattr(sync_mod, "LAST_PUSH_PATH", tmp_path / ".last_push.json")

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b"{}"
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        sync_mod.push_to_railway({"x": 1}, "https://example.com", "secret-token")

    # Header keys are normalized to title-case by urllib
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers.get("authorization") == "Bearer secret-token"
    assert "x-api-key" not in headers
    assert captured["url"].endswith("/api/admin/push-sentinel")


# ─────────────────────────────────────────────────────────────────────────────
# Meta-monitor — detects own staleness
# ─────────────────────────────────────────────────────────────────────────────


def test_meta_monitor_alerts_when_last_push_missing(tmp_path, fake_db):
    """No .last_push.json at all → CRITICAL alert."""
    from scripts.run_sentinel import check_push_pipeline_freshness

    result = check_push_pipeline_freshness(fake_db, last_push_path=tmp_path / "missing.json")

    assert result is not None
    assert result["ok"] is False
    fake_db.record_alert.assert_called_once()
    sev, msg = fake_db.record_alert.call_args[0][:2]
    assert sev == "critical"
    assert "never reported" in msg or "never" in msg


def test_meta_monitor_alerts_when_last_push_failed(tmp_path, fake_db):
    """Most recent push attempt failed → CRITICAL alert (regardless of age)."""
    from scripts.run_sentinel import check_push_pipeline_freshness

    last = tmp_path / ".last_push.json"
    last.write_text(json.dumps({
        "ok": False,
        "http_status": 404,
        "error": "Not Found",
        "attempted_at": datetime.now(timezone.utc).isoformat(),
    }))

    result = check_push_pipeline_freshness(fake_db, last_push_path=last)

    assert result is not None
    fake_db.record_alert.assert_called_once()
    assert fake_db.record_alert.call_args[0][0] == "critical"


def test_meta_monitor_alerts_when_last_push_stale(tmp_path, fake_db, monkeypatch):
    """Last push was OK but is older than 3x cadence → CRITICAL alert."""
    from scripts.run_sentinel import check_push_pipeline_freshness

    monkeypatch.setenv("SENTINEL_CADENCE_SECONDS", "3600")  # hourly → stale at 3h

    last = tmp_path / ".last_push.json"
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    last.write_text(json.dumps({
        "ok": True,
        "http_status": 200,
        "attempted_at": stale_at,
    }))

    result = check_push_pipeline_freshness(fake_db, last_push_path=last)

    assert result is not None
    fake_db.record_alert.assert_called_once()
    assert fake_db.record_alert.call_args[0][0] == "critical"
    assert "silent" in fake_db.record_alert.call_args[0][1]


def test_meta_monitor_silent_when_push_fresh(tmp_path, fake_db, monkeypatch):
    """Recent successful push → no alert."""
    from scripts.run_sentinel import check_push_pipeline_freshness

    monkeypatch.setenv("SENTINEL_CADENCE_SECONDS", "3600")

    last = tmp_path / ".last_push.json"
    fresh_at = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    last.write_text(json.dumps({
        "ok": True,
        "http_status": 200,
        "attempted_at": fresh_at,
    }))

    result = check_push_pipeline_freshness(fake_db, last_push_path=last)

    assert result is None
    fake_db.record_alert.assert_not_called()


def test_meta_monitor_handles_corrupt_last_push(tmp_path, fake_db):
    from scripts.run_sentinel import check_push_pipeline_freshness

    last = tmp_path / ".last_push.json"
    last.write_text("not json at all")

    result = check_push_pipeline_freshness(fake_db, last_push_path=last)

    assert result is not None
    fake_db.record_alert.assert_called_once()
    assert fake_db.record_alert.call_args[0][0] == "critical"


def test_meta_monitor_handles_missing_attempted_at(tmp_path, fake_db):
    from scripts.run_sentinel import check_push_pipeline_freshness

    last = tmp_path / ".last_push.json"
    last.write_text(json.dumps({"ok": True}))  # no attempted_at

    result = check_push_pipeline_freshness(fake_db, last_push_path=last)

    assert result is not None
    fake_db.record_alert.assert_called_once()


def test_meta_monitor_respects_custom_cadence(tmp_path, fake_db, monkeypatch):
    """Daily cadence: 5h-old push is fine; 4-day-old push is stale."""
    from scripts.run_sentinel import check_push_pipeline_freshness

    monkeypatch.setenv("SENTINEL_CADENCE_SECONDS", "86400")  # daily

    last = tmp_path / ".last_push.json"
    # 5h old — well under 3 days threshold → no alert
    last.write_text(json.dumps({
        "ok": True,
        "attempted_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
    }))
    assert check_push_pipeline_freshness(fake_db, last_push_path=last) is None

    # 4d old — over 3-day threshold → alert
    last.write_text(json.dumps({
        "ok": True,
        "attempted_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
    }))
    fake_db.record_alert.reset_mock()
    result = check_push_pipeline_freshness(fake_db, last_push_path=last)
    assert result is not None
    fake_db.record_alert.assert_called_once()
