"""PR2 contract tests: the durable equity_history shape (PR1 / get_equity_history)
renders in the dashboard chart, and the empty case still yields the placeholder.

This locks the data contract end-to-end: PositionMonitor writes points (PR1) →
worker pushes them → dashboard renders them — without ever silently blanking
when points exist.
"""
from web_dashboard.html import _render_equity_chart


def _curve(n):
    return [
        {"date": f"2026-05-{20 + i:02d}", "equity": 100000.0 + i * 500, "profit_loss": i * 500.0}
        for i in range(n)
    ]


def test_multi_point_curve_renders_chart():
    out = _render_equity_chart(_curve(3), today_equity=None)
    assert out                              # not the placeholder
    assert ("svg" in out or "polyline" in out or "path" in out)


def test_empty_history_no_live_equity_is_placeholder():
    assert _render_equity_chart([], today_equity=None) == ""


def test_single_point_plus_live_equity_renders():
    # One persisted point + today's live equity is enough to draw a line.
    assert _render_equity_chart(_curve(1), today_equity=101800.0)


def test_shape_matches_get_equity_history_output(tmp_path):
    """The exact dict shape get_equity_history() returns must render."""
    from shared.database import get_equity_history, init_db, upsert_equity_point
    db = str(tmp_path / "eq.db")
    init_db(db)
    upsert_equity_point("EXP-3311", "2026-05-26", 100000.0, realized_pnl=0, path=db)
    upsert_equity_point("EXP-3311", "2026-05-27", 101000.0, realized_pnl=1000.0, path=db)
    history = get_equity_history("EXP-3311", path=db)
    assert _render_equity_chart(history, today_equity=None)   # renders from real DB output
