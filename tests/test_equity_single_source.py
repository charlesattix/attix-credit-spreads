"""Single-source-of-truth equity: card == chart endpoint, backfill is past-only.

Acceptance criterion (Carlos): the Live Equity card and the chart's last point
MUST match to the dollar. Both read web_dashboard.data.current_equity; the chart's
final point is always that live value, replacing any stale today-dated snapshot.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import shared.equity_backfill as eb
from shared.database import get_equity_history, init_db
from web_dashboard.data import current_equity
from web_dashboard.html import _build_equity_points, _render_equity_chart

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# current_equity — the one source
# ---------------------------------------------------------------------------

def test_current_equity_returns_live_value():
    assert current_equity({"alpaca": {"equity": 101117.84}}) == 101117.84


def test_current_equity_none_when_no_alpaca():
    assert current_equity({}) is None
    assert current_equity({"alpaca": {}}) is None
    assert current_equity({"alpaca": {"equity": None}}) is None


# ---------------------------------------------------------------------------
# ACCEPTANCE: card equity == chart last point (to the dollar)
# ---------------------------------------------------------------------------

def test_card_and_chart_endpoint_match_to_the_dollar():
    # EXP-3311 scenario: live card equity vs a STALE today-dated history point.
    s = {"alpaca": {"equity": 101117.84}}
    card_equity = current_equity(s)
    history = [{"date": "2026-05-27", "equity": 100063.22},
               {"date": TODAY, "equity": 99436.16}]   # stale prior-close mislabeled today
    pts = _build_equity_points(history, today_equity=card_equity)
    assert pts[-1]["equity"] == card_equity            # endpoint == card, exactly
    assert pts[-1]["equity"] != 99436.16               # stale value NOT plotted
    assert len(pts) == 2                               # replaced, not duplicated


def test_chart_appends_today_when_history_has_no_today_point():
    history = [{"date": "2026-05-26", "equity": 100000.0},
               {"date": "2026-05-27", "equity": 100500.0}]
    pts = _build_equity_points(history, today_equity=101117.84)
    assert len(pts) == 3 and pts[-1]["date"] == TODAY and pts[-1]["equity"] == 101117.84


def test_build_points_noop_without_today_equity():
    history = [{"date": "2026-05-26", "equity": 100000.0}]
    assert _build_equity_points(history, None) == history


def test_render_endpoint_shows_live_not_stale():
    """End-to-end render: live value present, stale value not the endpoint."""
    history = [{"date": "2026-05-27", "equity": 100063.0},
               {"date": TODAY, "equity": 99436.0}]
    svg = _render_equity_chart(history, today_equity=101118.0)
    assert svg
    assert "$101,118" in svg            # live value rendered (today/live hover label)
    assert "today (live)" in svg


# ---------------------------------------------------------------------------
# backfill is PAST-only (never overwrites today's live point)
# ---------------------------------------------------------------------------

def test_backfill_excludes_today(tmp_path):
    db = str(tmp_path / "e.db")
    init_db(db)
    pts = [{"date": "2026-05-26", "equity": 100000.0, "profit_loss": 0},
           {"date": "2026-05-27", "equity": 100500.0, "profit_loss": 500.0},
           {"date": TODAY, "equity": 99436.16, "profit_loss": -627.0}]  # lagging today snapshot
    with patch.object(eb, "fetch_portfolio_history", return_value=pts):
        n = eb.backfill_equity_history("EXP-3311", db, "k", "s")
    stored = get_equity_history("EXP-3311", path=db)
    assert n == 2                                       # only the 2 past days written
    assert all(p["date"] != TODAY for p in stored)     # today NOT written by backfill


def test_backfill_only_today_writes_nothing(tmp_path):
    db = str(tmp_path / "e.db")
    init_db(db)
    with patch.object(eb, "fetch_portfolio_history",
                      return_value=[{"date": TODAY, "equity": 99436.16, "profit_loss": 0}]):
        assert eb.backfill_equity_history("EXP-3311", db, "k", "s") == 0
    assert get_equity_history("EXP-3311", path=db) == []
