"""Read-path fix: pushed equity_history curve must surface even when the
dashboard has LIVE Alpaca data for the experiment (otherwise the chart shows
'no equity history yet' despite a pushed curve)."""
import json

from web_dashboard.data import apply_pushed_equity_history


def _write_portfolio(portfolio_dir, exp_id, curve):
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    norm = exp_id.upper().replace("-", "")
    (portfolio_dir / f"{norm}.json").write_text(json.dumps({
        "equity": 101000.0, "positions": [], "orders": [],
        "equity_history": curve,
    }))


CURVE = [{"date": "2026-05-25", "equity": 100000.0, "profit_loss": 0},
         {"date": "2026-05-26", "equity": 101000.0, "profit_loss": 1000.0}]


def test_curve_surfaced_even_with_live_alpaca(tmp_path):
    # EXP-3311 already has a LIVE alpaca block (no curve) — the bug case.
    results = [{"id": "EXP-3311", "alpaca": {"equity": 101000.0, "positions": []}}]
    _write_portfolio(tmp_path, "EXP-3311", CURVE)
    apply_pushed_equity_history(results, tmp_path)
    assert results[0]["alpaca_equity_history"] == CURVE   # curve now present for the chart


def test_does_not_overwrite_existing_curve(tmp_path):
    results = [{"id": "EXP-3311", "alpaca_equity_history": [{"date": "x", "equity": 1.0}]}]
    _write_portfolio(tmp_path, "EXP-3311", CURVE)
    apply_pushed_equity_history(results, tmp_path)
    assert results[0]["alpaca_equity_history"] == [{"date": "x", "equity": 1.0}]


def test_no_portfolio_file_is_noop(tmp_path):
    results = [{"id": "EXP-9999", "alpaca": {"equity": 1.0}}]
    apply_pushed_equity_history(results, tmp_path)   # dir empty
    assert "alpaca_equity_history" not in results[0]


def test_missing_dir_is_noop(tmp_path):
    results = [{"id": "EXP-1", "alpaca": {"equity": 1.0}}]
    apply_pushed_equity_history(results, tmp_path / "nope")
    assert "alpaca_equity_history" not in results[0]
