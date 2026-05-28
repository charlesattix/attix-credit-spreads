"""Tests for durable equity_history (PR1): DB helpers + PositionMonitor write."""
from unittest.mock import MagicMock

from shared.database import get_equity_history, init_db, upsert_equity_point


def _db(tmp_path):
    p = str(tmp_path / "eq.db")
    init_db(p)
    return p


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def test_upsert_then_get_roundtrip(tmp_path):
    db = _db(tmp_path)
    upsert_equity_point("EXP-3311", "2026-05-27", 101000.0, realized_pnl=5324.0, path=db)
    h = get_equity_history("EXP-3311", path=db)
    assert h == [{"date": "2026-05-27", "equity": 101000.0, "profit_loss": 5324.0}]


def test_same_day_upsert_overwrites(tmp_path):
    db = _db(tmp_path)
    upsert_equity_point("EXP-3311", "2026-05-28", 101500.0, realized_pnl=5324.0, path=db)
    upsert_equity_point("EXP-3311", "2026-05-28", 101800.0, realized_pnl=5400.0, path=db)
    h = get_equity_history("EXP-3311", path=db)
    assert len(h) == 1                      # one point per day
    assert h[0]["equity"] == 101800.0       # latest scan wins
    assert h[0]["profit_loss"] == 5400.0


def test_ascending_by_date(tmp_path):
    db = _db(tmp_path)
    for d, eq in [("2026-05-28", 3), ("2026-05-26", 1), ("2026-05-27", 2)]:
        upsert_equity_point("EXP-3311", d, float(eq), path=db)
    dates = [p["date"] for p in get_equity_history("EXP-3311", path=db)]
    assert dates == ["2026-05-26", "2026-05-27", "2026-05-28"]


def test_per_experiment_isolation(tmp_path):
    db = _db(tmp_path)
    upsert_equity_point("EXP-3311", "2026-05-28", 100.0, path=db)
    upsert_equity_point("EXP-400", "2026-05-28", 200.0, path=db)
    assert len(get_equity_history("EXP-3311", path=db)) == 1
    assert get_equity_history("EXP-400", path=db)[0]["equity"] == 200.0


def test_null_realized_pnl_renders_as_zero(tmp_path):
    db = _db(tmp_path)
    upsert_equity_point("EXP-3311", "2026-05-28", 100.0, path=db)  # no realized_pnl
    assert get_equity_history("EXP-3311", path=db)[0]["profit_loss"] == 0


# ---------------------------------------------------------------------------
# PositionMonitor write
# ---------------------------------------------------------------------------

def _monitor(tmp_path, equity=None, raises=False):
    from execution.position_monitor import PositionMonitor
    alpaca = MagicMock()
    if raises:
        alpaca.get_account.side_effect = RuntimeError("alpaca down")
    else:
        alpaca.get_account.return_value = {"equity": equity}
    return PositionMonitor(alpaca_provider=alpaca, config={}, db_path=str(tmp_path / "pm.db"))


def test_position_monitor_writes_equity_point(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPERIMENT_ID", "EXP-3311")
    pm = _monitor(tmp_path, equity=101234.56)
    pm._record_equity_point()
    h = get_equity_history("EXP-3311", path=pm.db_path)
    assert len(h) == 1 and h[0]["equity"] == 101234.56


def test_position_monitor_skips_nonpositive_equity(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPERIMENT_ID", "EXP-3311")
    pm = _monitor(tmp_path, equity=0.0)
    pm._record_equity_point()
    assert get_equity_history("EXP-3311", path=pm.db_path) == []


def test_position_monitor_account_error_is_nonfatal(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPERIMENT_ID", "EXP-3311")
    pm = _monitor(tmp_path, raises=True)
    pm._record_equity_point()  # must not raise
    assert get_equity_history("EXP-3311", path=pm.db_path) == []


def test_position_monitor_realized_pnl_from_closed_trades(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPERIMENT_ID", "EXP-3311")
    from shared.database import upsert_trade
    pm = _monitor(tmp_path, equity=100000.0)
    db = pm.db_path
    upsert_trade({"id": "t1", "ticker": "SPY", "status": "closed_profit", "pnl": 500.0}, path=db)
    upsert_trade({"id": "t2", "ticker": "SPY", "status": "closed_loss", "pnl": -120.0}, path=db)
    upsert_trade({"id": "t3", "ticker": "SPY", "status": "open", "pnl": None}, path=db)
    pm._record_equity_point()
    assert get_equity_history("EXP-3311", path=db)[0]["profit_loss"] == 380.0
