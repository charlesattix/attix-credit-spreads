"""Tests for inception→now equity backfill + off-hours push + 24/7 refresh."""
from unittest.mock import MagicMock, patch

import shared.equity_backfill as eb
from shared.database import (
    bulk_upsert_equity_points,
    get_equity_history,
    init_db,
)


def _db(tmp_path):
    p = str(tmp_path / "eq.db")
    init_db(p)
    return p


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


# ---------------------------------------------------------------------------
# bulk upsert (idempotent)
# ---------------------------------------------------------------------------

def test_bulk_upsert_idempotent(tmp_path):
    db = _db(tmp_path)
    pts = [{"date": "2026-05-25", "equity": 100000.0, "profit_loss": 0},
           {"date": "2026-05-26", "equity": 100500.0, "profit_loss": 500.0}]
    assert bulk_upsert_equity_points("EXP-3311", pts, path=db) == 2
    bulk_upsert_equity_points("EXP-3311", pts, path=db)          # re-run
    h = get_equity_history("EXP-3311", path=db)
    assert len(h) == 2                                          # no duplication


def test_bulk_upsert_overwrites_same_day(tmp_path):
    db = _db(tmp_path)
    bulk_upsert_equity_points("EXP-3311", [{"date": "2026-05-26", "equity": 100.0}], path=db)
    bulk_upsert_equity_points("EXP-3311", [{"date": "2026-05-26", "equity": 999.0}], path=db)
    h = get_equity_history("EXP-3311", path=db)
    assert len(h) == 1 and h[0]["equity"] == 999.0


# ---------------------------------------------------------------------------
# fetch_portfolio_history parsing
# ---------------------------------------------------------------------------

def test_fetch_filters_zero_equity_and_maps_fields():
    payload = {"timestamp": [1716595200, 1716681600, 1716768000],
               "equity": [100000, 0, 101000], "profit_loss": [0, 0, 1000]}
    with patch.object(eb.requests, "get", return_value=_resp(payload)):
        out = eb.fetch_portfolio_history("k", "s")
    assert len(out) == 2                                       # the 0-equity point dropped
    assert set(out[0]) == {"date", "equity", "profit_loss"}
    assert out[-1]["equity"] == 101000.0 and out[-1]["profit_loss"] == 1000.0


def test_fetch_empty_history():
    with patch.object(eb.requests, "get", return_value=_resp({"timestamp": [], "equity": []})):
        assert eb.fetch_portfolio_history("k", "s") == []


# ---------------------------------------------------------------------------
# backfill_equity_history
# ---------------------------------------------------------------------------

def test_backfill_populates_table(tmp_path):
    db = _db(tmp_path)
    pts = [{"date": "2026-05-25", "equity": 100000.0, "profit_loss": 0},
           {"date": "2026-05-26", "equity": 101000.0, "profit_loss": 1000.0}]
    with patch.object(eb, "fetch_portfolio_history", return_value=pts):
        n = eb.backfill_equity_history("EXP-3311", db, "k", "s")
    assert n == 2
    assert len(get_equity_history("EXP-3311", path=db)) == 2


def test_backfill_empty_is_zero(tmp_path):
    db = _db(tmp_path)
    with patch.object(eb, "fetch_portfolio_history", return_value=[]):
        assert eb.backfill_equity_history("EXP-3311", db, "k", "s") == 0


def test_backfill_fetch_error_nonfatal(tmp_path):
    db = _db(tmp_path)
    with patch.object(eb, "fetch_portfolio_history", side_effect=RuntimeError("alpaca down")):
        assert eb.backfill_equity_history("EXP-3311", db, "k", "s") == 0   # no raise


# ---------------------------------------------------------------------------
# push_portfolio_snapshot
# ---------------------------------------------------------------------------

def test_push_includes_curve_from_db(tmp_path):
    db = _db(tmp_path)
    bulk_upsert_equity_points("EXP-3311",
                              [{"date": "2026-05-25", "equity": 100000.0},
                               {"date": "2026-05-26", "equity": 101000.0}], path=db)
    posted = {}

    def fake_get(url, **kw):
        if "positions" in url:
            return _resp([])
        if "orders" in url:
            return _resp([])
        return _resp({"equity": 101000.0, "cash": 5000.0, "buying_power": 9000.0})

    def fake_post(url, json=None, **kw):
        posted["url"] = url
        posted["json"] = json
        return _resp({"status": "ok"})

    with patch.object(eb.requests, "get", side_effect=fake_get), \
         patch.object(eb.requests, "post", side_effect=fake_post):
        ok = eb.push_portfolio_snapshot("EXP-3311", db, "https://dash", "tok", "k", "s")
    assert ok is True
    assert posted["url"].endswith("/api/v1/experiments/EXP-3311/push-portfolio")
    assert len(posted["json"]["equity_history"]) == 2          # curve carried in payload


def test_push_missing_creds_is_noop(tmp_path):
    assert eb.push_portfolio_snapshot("EXP-3311", _db(tmp_path), "", "", "", "") is False


# ---------------------------------------------------------------------------
# refresh_and_push (env defaults) + PositionMonitor 24/7 hook
# ---------------------------------------------------------------------------

def test_refresh_and_push_calls_both(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    monkeypatch.setenv("DASHBOARD_API_KEY", "tok")
    monkeypatch.setenv("RAILWAY_SERVICE_ATTIX_DASHBOARD_URL", "https://dash")
    with patch.object(eb, "backfill_equity_history", return_value=3) as bf, \
         patch.object(eb, "push_portfolio_snapshot", return_value=True) as pu:
        n = eb.refresh_and_push("EXP-3311", str(tmp_path / "x.db"))
    assert n == 3
    bf.assert_called_once()
    pu.assert_called_once()


def test_position_monitor_refresh_throttled_and_forced(tmp_path):
    from execution.position_monitor import PositionMonitor
    pm = PositionMonitor(alpaca_provider=MagicMock(), config={}, db_path=str(tmp_path / "pm.db"))
    calls = []
    with patch("shared.equity_backfill.refresh_and_push", side_effect=lambda *a, **k: calls.append(1)):
        pm._maybe_refresh_equity(force=True)   # startup → runs
        pm._maybe_refresh_equity()             # immediately after → throttled (skipped)
    assert len(calls) == 1
