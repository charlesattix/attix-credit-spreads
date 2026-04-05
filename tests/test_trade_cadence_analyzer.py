"""Tests for compass/trade_cadence_analyzer.py."""

import numpy as np
import pandas as pd
import pytest

from compass.trade_cadence_analyzer import (
    TradeTimeline, OverlapSnapshot, CadenceResult,
    analyze_timeline, compute_cadence_metrics, generate_report,
)


def _make_trades(n=30, cooldown=10, seed=42):
    rng = np.random.RandomState(seed)
    trades = []
    for i in range(n):
        yr = 2020 + i // 6; m = (i % 12) + 1; d = min(28, 5 + i % 20)
        hold = 10 + rng.randint(0, 15)
        entry = pd.Timestamp(f"{yr}-{m:02d}-{d:02d}")
        exit_dt = entry + pd.Timedelta(days=hold)
        trades.append({
            "entry_date": entry.strftime("%Y-%m-%d"),
            "exit_date": exit_dt.strftime("%Y-%m-%d"),
            "pnl": 100 + rng.normal(0, 150),
            "hold_days": hold, "contracts": 2,
            "credit": 0.65, "vix": 18.0, "exit_reason": "profit",
            "cooldown": cooldown,
        })
    return trades


class TestTimeline:
    def test_basic(self):
        tl, ov = analyze_timeline(_make_trades(10))
        assert len(tl) == 10
        assert len(ov) > 0

    def test_empty(self):
        tl, ov = analyze_timeline([])
        assert len(tl) == 0

    def test_overlap_counts(self):
        trades = [
            {"entry_date": "2022-01-03", "exit_date": "2022-01-20", "pnl": 100,
             "hold_days": 17, "contracts": 2},
            {"entry_date": "2022-01-10", "exit_date": "2022-01-28", "pnl": 150,
             "hold_days": 18, "contracts": 2},
        ]
        _, ov = analyze_timeline(trades)
        max_active = max(s.active_positions for s in ov)
        assert max_active == 2  # overlap between Jan 10-20

    def test_timeline_has_winners_losers(self):
        trades = [
            {"entry_date": "2022-01-03", "exit_date": "2022-01-20", "pnl": 200,
             "hold_days": 17, "contracts": 2},
            {"entry_date": "2022-02-01", "exit_date": "2022-02-15", "pnl": -100,
             "hold_days": 14, "contracts": 2},
        ]
        tl, _ = analyze_timeline(trades)
        assert tl[0].is_winner
        assert not tl[1].is_winner


class TestCadenceMetrics:
    def test_basic(self):
        cr = compute_cadence_metrics(_make_trades(), 10, "test")
        assert cr.n_trades == 30
        assert cr.trades_per_year > 0
        assert cr.avg_hold_days > 0

    def test_empty(self):
        cr = compute_cadence_metrics([], 10, "empty")
        assert cr.n_trades == 0
        assert cr.sharpe == 0

    def test_concurrent(self):
        cr = compute_cadence_metrics(_make_trades(30), 10, "test")
        assert cr.max_concurrent >= 1
        assert cr.avg_concurrent >= 0

    def test_capital_util(self):
        cr = compute_cadence_metrics(_make_trades(30), 10, "test")
        assert 0 <= cr.capital_util_pct <= 100

    def test_shorter_cooldown_more_trades(self):
        short = compute_cadence_metrics(_make_trades(50, cooldown=3), 3, "short")
        long = compute_cadence_metrics(_make_trades(20, cooldown=21), 21, "long")
        assert short.n_trades >= long.n_trades

    def test_win_rate(self):
        cr = compute_cadence_metrics(_make_trades(), 10, "test")
        assert 0 <= cr.win_rate <= 1

    def test_sharpe_computed(self):
        cr = compute_cadence_metrics(_make_trades(50, seed=1), 5, "test")
        assert isinstance(cr.sharpe, float)


class TestReport:
    def test_generates(self, tmp_path):
        trades = _make_trades()
        cr = [compute_cadence_metrics(trades, 10, "Current (10d)")]
        tl, ov = analyze_timeline(trades)
        missed = {"total_expirations": 500, "valid_opportunities": 400,
                  "priceable_spreads": 300, "pricing_hit_rate": 75.0}
        out = tmp_path / "cadence.html"
        generate_report(cr, tl, ov, missed, str(out))
        assert out.exists()
        c = out.read_text()
        assert "Cadence" in c
        assert "Timeline" in c

    def test_contains_svg(self, tmp_path):
        trades = _make_trades(20)
        cr = [compute_cadence_metrics(trades, 10, "test")]
        tl, ov = analyze_timeline(trades)
        out = tmp_path / "c.html"
        generate_report(cr, tl, ov, {"total_expirations": 0, "valid_opportunities": 0,
                                      "priceable_spreads": 0, "pricing_hit_rate": 0}, str(out))
        assert "<svg" in out.read_text()

    def test_empty_trades(self, tmp_path):
        out = tmp_path / "c.html"
        generate_report([], [], [], {"total_expirations": 0, "valid_opportunities": 0,
                                      "priceable_spreads": 0, "pricing_hit_rate": 0}, str(out))
        assert out.exists()
