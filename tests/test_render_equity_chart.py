"""Test _render_equity_chart restores per-experiment equity sparkline.

Regression test for the dashboard refactor that dropped the inline-SVG
equity chart from each experiment card. The full chart implementation
lived originally in commit a15e857 ("feat: hoverable equity charts with
today live value") and was lost in subsequent dashboard work.
"""

from web_dashboard.html import _render_equity_chart


def test_render_equity_chart_returns_svg_with_polyline():
    history = [
        {"date": "2026-03-12", "equity": 100000.0},
        {"date": "2026-03-13", "equity": 100450.0},
        {"date": "2026-03-14", "equity": 100120.0},
        {"date": "2026-03-15", "equity": 101230.0},
    ]
    html = _render_equity_chart(history)
    assert "<svg" in html
    assert "</svg>" in html
    assert "<polyline" in html
    # The polyline must carry at least one coordinate pair from the data.
    assert 'points="' in html


def test_render_equity_chart_with_today_appends_live_point():
    history = [
        {"date": "2026-03-12", "equity": 100000.0},
        {"date": "2026-03-13", "equity": 100450.0},
    ]
    html = _render_equity_chart(history, today_equity=101000.0)
    assert "<svg" in html
    assert "<polyline" in html
    # Hover overlay for the "today" point should be labelled as live.
    assert "today (live)" in html


def test_render_equity_chart_returns_empty_for_insufficient_history():
    # Zero / one point with no today_equity should produce no chart.
    assert _render_equity_chart([]) == ""
    assert _render_equity_chart([{"date": "2026-03-12", "equity": 100000.0}]) == ""


def test_render_equity_chart_single_point_with_today_still_renders():
    # One historical point + today's live equity = 2 plottable points.
    html = _render_equity_chart(
        [{"date": "2026-03-12", "equity": 100000.0}],
        today_equity=100500.0,
    )
    assert "<svg" in html
    assert "<polyline" in html
