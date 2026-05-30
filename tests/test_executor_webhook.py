"""Tests for compass.live.executor_webhook — the fill receiver.

Uses FastAPI's TestClient (in-process, no socket) and a per-test SQLite path
under tmp_path so each test is hermetic.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from compass.live import executor_webhook as ew  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "fills.db")


@pytest.fixture()
def client(db_path, monkeypatch):
    # No secret by default — auth optional.
    monkeypatch.delenv(ew._SHARED_SECRET_ENV, raising=False)
    # Reset in-process subscribers between tests.
    monkeypatch.setattr(ew, "_subscribers", [])
    app = ew.build_app(db_path=db_path)
    return TestClient(app)


def _payload(**overrides):
    base = {
        "broker_order_id": "bk-1",
        "status": "filled",
        "filled_quantity": 5,
        "avg_fill_price": 0.97,
        "remaining_quantity": 0,
        "symbol": "SPY",
        "side": "sell",
        "event_data": {"raw": "ok"},
    }
    base.update(overrides)
    return base


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_post_fill_persists_to_journal(client, db_path):
    r = client.post("/webhooks/executor/fills", json=_payload())
    assert r.status_code == 200
    assert r.json()["ok"] is True
    rows = ew.drain_unconsumed(db_path=db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["broker_order_id"] == "bk-1"
    assert row["status"] == "filled"
    assert row["symbol"] == "SPY"
    assert row["filled_quantity"] == 5
    assert row["avg_fill_price"] == pytest.approx(0.97)
    # Full payload round-trips through payload_json
    assert json.loads(row["payload_json"])["event_data"]["raw"] == "ok"


def test_drain_marks_rows_consumed(client, db_path):
    client.post("/webhooks/executor/fills", json=_payload(broker_order_id="a"))
    client.post("/webhooks/executor/fills", json=_payload(broker_order_id="b"))
    first = ew.drain_unconsumed(db_path=db_path)
    assert [r["broker_order_id"] for r in first] == ["a", "b"]
    # Second drain — empty (atomic mark-consumed)
    assert ew.drain_unconsumed(db_path=db_path) == []


def test_in_process_subscribers_fired_with_payload(client, db_path):
    received = []
    ew.subscribe(received.append)
    client.post("/webhooks/executor/fills", json=_payload())
    assert len(received) == 1
    assert received[0]["broker_order_id"] == "bk-1"


def test_subscriber_exception_does_not_break_response(client, db_path):
    def angry(_):
        raise RuntimeError("boom")
    ew.subscribe(angry)
    r = client.post("/webhooks/executor/fills", json=_payload())
    # Subscriber error is logged, not propagated — executor must NOT see 5xx,
    # otherwise it will retry + DLQ the event.
    assert r.status_code == 200
    rows = ew.drain_unconsumed(db_path=db_path)
    assert len(rows) == 1


def test_invalid_json_400(client):
    r = client.post(
        "/webhooks/executor/fills",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert "invalid JSON" in r.json()["detail"]


def test_missing_required_fields_400(client):
    r = client.post("/webhooks/executor/fills", json={"symbol": "SPY"})
    assert r.status_code == 400
    assert "broker_order_id" in r.json()["detail"]


def test_signature_required_when_secret_set(client, db_path, monkeypatch):
    secret = "topsecret"
    monkeypatch.setenv(ew._SHARED_SECRET_ENV, secret)
    body = json.dumps(_payload()).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    # No signature header → 401
    r1 = client.post(
        "/webhooks/executor/fills",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r1.status_code == 401

    # Correct signature (with sha256= prefix) → 200
    r2 = client.post(
        "/webhooks/executor/fills",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": f"sha256={sig}"},
    )
    assert r2.status_code == 200

    # Tampered body → 401
    bad_body = body.replace(b"SPY", b"QQQ")
    r3 = client.post(
        "/webhooks/executor/fills",
        content=bad_body,
        headers={"Content-Type": "application/json", "X-Signature": f"sha256={sig}"},
    )
    assert r3.status_code == 401
