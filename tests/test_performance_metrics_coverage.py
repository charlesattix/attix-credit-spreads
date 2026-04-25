"""Tests for backtest/performance_metrics.py — report generation and formatting."""

import pytest


def _sample_results(**overrides):
    base = {
        "total_trades": 50, "winning_trades": 44, "losing_trades": 6,
        "win_rate": 88.0, "starting_capital": 100_000, "ending_capital": 145_000,
        "total_pnl": 45_000, "return_pct": 45.0, "avg_win": 1200.0,
        "avg_loss": -800.0, "profit_factor": 2.5, "max_drawdown": 8.3,
        "sharpe_ratio": 3.85,
    }
    base.update(overrides)
    return base


class TestPerformanceMetricsInit:
    def test_init(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={"backtest": {"report_dir": "/tmp"}})
        assert pm.config is not None


class TestGenerateReport:
    def test_report_writes_files(self, tmp_path):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={"backtest": {"report_dir": str(tmp_path)}})
        result = pm.generate_report(_sample_results())
        assert result != ""
        assert tmp_path.exists()
        txt_files = list(tmp_path.glob("backtest_report_*.txt"))
        json_files = list(tmp_path.glob("backtest_results_*.json"))
        assert len(txt_files) >= 1
        assert len(json_files) >= 1

    def test_empty_results_returns_empty(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={"backtest": {"report_dir": "/tmp"}})
        assert pm.generate_report({}) == ""
        assert pm.generate_report(None) == ""


class TestTextReport:
    def test_report_contains_summary(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        text = pm._generate_text_report(_sample_results())
        assert "Total Trades: 50" in text
        assert "Win Rate: 88.00%" in text
        assert "Sharpe Ratio: 3.85" in text
        assert "$45,000.00" in text

    def test_win_rate_target_achieved(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        text = pm._generate_text_report(_sample_results(win_rate=92.0))
        assert "WIN RATE TARGET ACHIEVED" in text

    def test_win_rate_below_target(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        text = pm._generate_text_report(_sample_results(win_rate=80.0))
        assert "below 90% target" in text

    def test_profitable_badge(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        text = pm._generate_text_report(_sample_results(total_pnl=5000))
        assert "PROFITABLE STRATEGY" in text

    def test_loss_badge(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        text = pm._generate_text_report(_sample_results(total_pnl=-2000))
        assert "Strategy shows losses" in text

    def test_strategy_breakdown_included(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        results = _sample_results(
            bull_put_trades=30, bull_put_win_rate=90.0,
            bear_call_trades=20, bear_call_win_rate=85.0,
        )
        text = pm._generate_text_report(results)
        assert "Bull Put Spreads: 30" in text
        assert "Bear Call Spreads: 20" in text

    def test_no_breakdown_when_zero(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        text = pm._generate_text_report(_sample_results())
        assert "STRATEGY BREAKDOWN" not in text


class TestPrintSummary:
    def test_prints_without_error(self, capsys):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        pm.print_summary(_sample_results())
        output = capsys.readouterr().out
        assert "BACKTEST SUMMARY" in output
        assert "Win Rate: 88.00%" in output

    def test_prints_strategy_breakdown(self, capsys):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        pm.print_summary(_sample_results(bull_put_trades=25, bull_put_win_rate=90.0))
        output = capsys.readouterr().out
        assert "Bull Puts: 25" in output

    def test_no_breakdown_when_zero_trades(self, capsys):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        pm.print_summary(_sample_results())
        output = capsys.readouterr().out
        assert "Bull Puts" not in output


class TestTimestamp:
    def test_timestamp_format(self):
        from backtest.performance_metrics import PerformanceMetrics
        pm = PerformanceMetrics(config={})
        ts = pm._timestamp()
        assert len(ts) == 15  # YYYYMMDD_HHMMSS
        assert "_" in ts
