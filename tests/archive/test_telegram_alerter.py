"""Tests for compass.telegram_alerter — 35 tests."""

import time
import pytest

from compass.telegram_alerter import (
    TelegramAlerter, Priority, AlertMessage,
    TradeAlert, RiskAlert, ModelAlert,
    DailySummary, WeeklyReport,
    PRIORITY_EMOJI, DEFAULT_COOLDOWNS,
)


# ---------------------------------------------------------------------------
# Capture helper: collect all sent messages
# ---------------------------------------------------------------------------

class MessageCapture:
    def __init__(self):
        self.messages: list[str] = []
    def __call__(self, text: str) -> bool:
        self.messages.append(text)
        return True


def _alerter(**kw) -> tuple[TelegramAlerter, MessageCapture]:
    cap = MessageCapture()
    ta = TelegramAlerter(send_fn=cap, enabled=True, **kw)
    return ta, cap


# ===========================================================================
# Core send / formatting
# ===========================================================================

class TestCoreSend:
    def test_sends_message(self):
        ta, cap = _alerter()
        msg = AlertMessage(Priority.INFO, "test", "Title", "Body")
        assert ta.send(msg)
        assert len(cap.messages) == 1
        assert "Title" in cap.messages[0]

    def test_includes_experiment_id(self):
        ta, cap = _alerter(experiment_id="EXP-999")
        ta.send(AlertMessage(Priority.INFO, "t", "Hello", "World"))
        assert "EXP-999" in cap.messages[0]

    def test_includes_emoji(self):
        ta, cap = _alerter()
        ta.send(AlertMessage(Priority.CRITICAL, "t", "Fire", ""))
        assert "🚨" in cap.messages[0]

    def test_disabled_does_not_send(self):
        cap = MessageCapture()
        ta = TelegramAlerter(send_fn=cap, enabled=False)
        result = ta.send(AlertMessage(Priority.INFO, "t", "X", "Y"))
        assert not result
        assert len(cap.messages) == 0

    def test_history_tracked(self):
        ta, _ = _alerter()
        ta.send(AlertMessage(Priority.INFO, "t", "A", ""))
        ta.send(AlertMessage(Priority.WARNING, "t", "B", ""))
        assert len(ta.history) == 2

    def test_message_count(self):
        ta, _ = _alerter(cooldowns={Priority.INFO: 0, Priority.WARNING: 0, Priority.CRITICAL: 0})
        ta.send(AlertMessage(Priority.INFO, "t", "A", ""))
        ta.send(AlertMessage(Priority.INFO, "t", "B", ""))
        assert ta.message_count == 2


# ===========================================================================
# Rate limiting
# ===========================================================================

class TestRateLimiting:
    def test_critical_never_limited(self):
        ta, cap = _alerter(cooldowns={Priority.CRITICAL: 0, Priority.INFO: 9999})
        ta.send(AlertMessage(Priority.CRITICAL, "r", "A", ""))
        ta.send(AlertMessage(Priority.CRITICAL, "r", "B", ""))
        assert len(cap.messages) == 2

    def test_info_rate_limited(self):
        ta, cap = _alerter(cooldowns={Priority.INFO: 9999, Priority.WARNING: 0, Priority.CRITICAL: 0})
        ta.send(AlertMessage(Priority.INFO, "r", "A", ""))
        result = ta.send(AlertMessage(Priority.INFO, "r", "B", ""))
        assert not result  # second message rate-limited
        assert len(cap.messages) == 1

    def test_different_categories_not_limited(self):
        ta, cap = _alerter(cooldowns={Priority.INFO: 9999, Priority.WARNING: 0, Priority.CRITICAL: 0})
        ta.send(AlertMessage(Priority.INFO, "cat1", "A", ""))
        ta.send(AlertMessage(Priority.INFO, "cat2", "B", ""))
        assert len(cap.messages) == 2  # different categories

    def test_rate_limited_count(self):
        ta, _ = _alerter(cooldowns={Priority.INFO: 9999, Priority.WARNING: 0, Priority.CRITICAL: 0})
        ta.send(AlertMessage(Priority.INFO, "r", "A", ""))
        ta.send(AlertMessage(Priority.INFO, "r", "B", ""))
        assert ta.rate_limited_count == 1

    def test_reset_rate_limits(self):
        ta, cap = _alerter(cooldowns={Priority.INFO: 9999, Priority.WARNING: 0, Priority.CRITICAL: 0})
        ta.send(AlertMessage(Priority.INFO, "r", "A", ""))
        ta.reset_rate_limits()
        ta.send(AlertMessage(Priority.INFO, "r", "B", ""))
        assert len(cap.messages) == 2


# ===========================================================================
# 1. Trade alerts
# ===========================================================================

class TestTradeAlerts:
    def test_entry(self):
        ta, cap = _alerter()
        trade = TradeAlert("entry", "CS", "SPY", "short", 3, 1.50)
        assert ta.trade_alert(trade)
        assert "ENTRY" in cap.messages[0]
        assert "SPY" in cap.messages[0]

    def test_exit_profit(self):
        ta, cap = _alerter()
        trade = TradeAlert("exit", "CS", "SPY", "short", 3, 0.75,
                            pnl=225, pnl_pct=0.50, exit_reason="profit_target")
        ta.trade_alert(trade)
        assert "✅" in cap.messages[0]
        assert "+$225" in cap.messages[0] or "+225" in cap.messages[0]

    def test_exit_loss(self):
        ta, cap = _alerter()
        trade = TradeAlert("exit", "CS", "SPY", "short", 3, 2.50,
                            pnl=-300, pnl_pct=-0.20, exit_reason="stop_loss")
        ta.trade_alert(trade)
        assert "❌" in cap.messages[0]


# ===========================================================================
# 2. Risk alerts
# ===========================================================================

class TestRiskAlerts:
    def test_drawdown_warning(self):
        ta, cap = _alerter()
        alert = RiskAlert("drawdown", 0.04, 0.08, "Approaching limit")
        ta.risk_alert(alert)
        assert "Drawdown" in cap.messages[0]

    def test_drawdown_critical(self):
        ta, cap = _alerter()
        alert = RiskAlert("drawdown", 0.09, 0.08, "EXCEEDED")
        ta.risk_alert(alert)
        assert "CRITICAL" in cap.messages[0] or "🚨" in cap.messages[0]

    def test_vix_spike(self):
        ta, cap = _alerter()
        alert = RiskAlert("vix_spike", 45.0, 30.0, "VIX above 40")
        ta.risk_alert(alert)
        assert "VIX" in cap.messages[0]

    def test_daily_loss(self):
        ta, cap = _alerter()
        alert = RiskAlert("daily_loss", 0.035, 0.03, "Daily loss exceeded")
        ta.risk_alert(alert)
        assert "Daily Loss" in cap.messages[0]


# ===========================================================================
# 3. Model alerts
# ===========================================================================

class TestModelAlerts:
    def test_retrain(self):
        ta, cap = _alerter()
        alert = ModelAlert("retrain_triggered", 0, 0, "Scheduled quarterly retrain")
        ta.model_alert(alert)
        assert "Retrain" in cap.messages[0]

    def test_auc_decay(self):
        ta, cap = _alerter()
        alert = ModelAlert("auc_decay", 0.55, 0.72, "AUC dropped below threshold")
        ta.model_alert(alert)
        assert "AUC" in cap.messages[0]

    def test_feature_drift(self):
        ta, cap = _alerter()
        alert = ModelAlert("feature_drift", 0.15, 0.05, "Feature distribution shifted")
        ta.model_alert(alert)
        assert "Drift" in cap.messages[0]


# ===========================================================================
# 4. Daily summary
# ===========================================================================

class TestDailySummary:
    def test_positive_day(self):
        ta, cap = _alerter()
        summary = DailySummary(
            date="2026-04-01", daily_pnl=1500, total_pnl=8000,
            equity=108000, win_rate=0.85, n_trades_today=2,
            n_open_positions=3, hedge_state="normal", drawdown=0.02, vix=18.5)
        ta.daily_summary(summary)
        assert "🟢" in cap.messages[0]
        assert "Daily Summary" in cap.messages[0]
        assert "$1,500" in cap.messages[0] or "1,500" in cap.messages[0]

    def test_negative_day(self):
        ta, cap = _alerter()
        summary = DailySummary(
            date="2026-04-01", daily_pnl=-800, total_pnl=7200,
            equity=107200, win_rate=0.50, n_trades_today=1,
            n_open_positions=2, hedge_state="reducing", drawdown=0.04)
        ta.daily_summary(summary)
        assert "🔴" in cap.messages[0]

    def test_emergency_hedge(self):
        ta, cap = _alerter()
        summary = DailySummary(
            date="2026-04-01", daily_pnl=-2000, total_pnl=5000,
            equity=105000, win_rate=0.40, n_trades_today=0,
            n_open_positions=1, hedge_state="emergency", drawdown=0.07)
        ta.daily_summary(summary)
        assert "emergency" in cap.messages[0].lower()


# ===========================================================================
# 5. Weekly report
# ===========================================================================

class TestWeeklyReport:
    def test_healthy(self):
        ta, cap = _alerter()
        report = WeeklyReport(
            week_ending="2026-04-04", weekly_pnl=3000, weekly_return=0.03,
            ytd_return=0.08, sharpe_rolling=3.5, max_dd=0.04,
            win_rate=0.88, n_trades=4, backtest_expected_pnl=2800,
            drift_pct=0.07, strategy_health="healthy")
        ta.weekly_report(report)
        assert "Weekly Report" in cap.messages[0]
        assert "healthy" in cap.messages[0].lower() or "HEALTHY" in cap.messages[0]

    def test_degrading(self):
        ta, cap = _alerter()
        report = WeeklyReport(
            week_ending="2026-04-04", weekly_pnl=-500, weekly_return=-0.005,
            ytd_return=0.02, sharpe_rolling=1.2, max_dd=0.08,
            win_rate=0.55, n_trades=3, backtest_expected_pnl=2500,
            drift_pct=-1.20, strategy_health="degrading")
        ta.weekly_report(report)
        assert "⚠️" in cap.messages[0] or "WARNING" in cap.messages[0].upper()

    def test_drift_flag(self):
        ta, cap = _alerter()
        report = WeeklyReport(
            week_ending="2026-04-04", weekly_pnl=100, weekly_return=0.001,
            ytd_return=0.01, sharpe_rolling=2.0, max_dd=0.03,
            win_rate=0.70, n_trades=2, backtest_expected_pnl=3000,
            drift_pct=-0.95, strategy_health="healthy")
        ta.weekly_report(report)
        assert "drift" in cap.messages[0].lower()


# ===========================================================================
# Kill switch
# ===========================================================================

class TestKillSwitch:
    def test_sends(self):
        ta, cap = _alerter()
        ta.kill_switch_alert("max_drawdown", 0.09)
        assert "KILL SWITCH" in cap.messages[0]
        assert "LIQUIDATED" in cap.messages[0]

    def test_always_critical(self):
        ta, _ = _alerter()
        ta.kill_switch_alert("daily_loss", 0.035)
        assert ta.history[-1].priority == Priority.CRITICAL
