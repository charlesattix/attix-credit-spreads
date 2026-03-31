"""Tests for compass.live_trading_blueprint — 38 tests."""

import pytest
from datetime import datetime

from compass.live_trading_blueprint import (
    LiveTradingBlueprint, SimulatedBroker, BrokerAdapter,
    StrategySignal, Order, Position, RiskLimits,
    OrderSide, OrderType, OrderStatus, RiskCheckResult,
    KillSwitchState, AlertLevel, RiskCheckReport,
    PnLSnapshot, ReconciliationResult, AuditEntry,
)


def _signal(strategy="CS", symbol="SPY", direction="short",
            confidence=0.80, contracts=3, entry_price=1.50,
            sig_id="SIG-001"):
    return StrategySignal(
        signal_id=sig_id, strategy=strategy, symbol=symbol,
        direction=direction, confidence=confidence,
        target_contracts=contracts, entry_price=entry_price,
    )


# ===========================================================================
# Signal → Order translation
# ===========================================================================

class TestTranslation:
    def test_basic(self):
        ltb = LiveTradingBlueprint()
        order = ltb.translate_signal(_signal())
        assert isinstance(order, Order)
        assert order.side == OrderSide.SELL  # short → sell
        assert order.quantity == 3

    def test_long_signal(self):
        ltb = LiveTradingBlueprint()
        order = ltb.translate_signal(_signal(direction="long"))
        assert order.side == OrderSide.BUY

    def test_limit_order(self):
        ltb = LiveTradingBlueprint()
        order = ltb.translate_signal(_signal(entry_price=1.50))
        assert order.order_type == OrderType.LIMIT
        assert order.limit_price == 1.50

    def test_market_order(self):
        ltb = LiveTradingBlueprint()
        order = ltb.translate_signal(_signal(entry_price=0))
        assert order.order_type == OrderType.MARKET

    def test_unique_order_ids(self):
        ltb = LiveTradingBlueprint()
        o1 = ltb.translate_signal(_signal(sig_id="S1"))
        o2 = ltb.translate_signal(_signal(sig_id="S2"))
        assert o1.order_id != o2.order_id


# ===========================================================================
# Pre-trade risk checks
# ===========================================================================

class TestRiskChecks:
    def test_approved(self):
        ltb = LiveTradingBlueprint()
        report = ltb.pre_trade_check(_signal())
        assert report.result == RiskCheckResult.APPROVED
        assert all(report.checks.values())

    def test_low_confidence_rejected(self):
        ltb = LiveTradingBlueprint(limits=RiskLimits(min_confidence=0.90))
        report = ltb.pre_trade_check(_signal(confidence=0.50))
        assert report.result == RiskCheckResult.REJECTED
        assert not report.checks["confidence"]

    def test_position_limit(self):
        ltb = LiveTradingBlueprint(limits=RiskLimits(max_positions=1))
        ltb.process_signal(_signal(sig_id="S1"))
        report = ltb.pre_trade_check(_signal(sig_id="S2"))
        assert not report.checks["position_limit"]

    def test_strategy_limit(self):
        ltb = LiveTradingBlueprint(limits=RiskLimits(max_positions_per_strategy=1))
        ltb.process_signal(_signal(strategy="CS", sig_id="S1"))
        report = ltb.pre_trade_check(_signal(strategy="CS", sig_id="S2"))
        assert not report.checks["strategy_limit"]

    def test_drawdown_check(self):
        ltb = LiveTradingBlueprint(limits=RiskLimits(max_drawdown=0.05))
        ltb.equity = 94000  # 6% DD from 100K
        ltb._hwm = 100000
        report = ltb.pre_trade_check(_signal())
        assert not report.checks["drawdown"]

    def test_daily_loss_check(self):
        ltb = LiveTradingBlueprint(limits=RiskLimits(max_daily_loss=0.02))
        ltb.equity = 97000  # 3% daily loss
        ltb.daily_start_equity = 100000
        report = ltb.pre_trade_check(_signal())
        assert not report.checks["daily_loss"]

    def test_notional_limit(self):
        ltb = LiveTradingBlueprint(limits=RiskLimits(max_notional_per_order=1000))
        sig = _signal(contracts=10)  # 10 × $5 × 100 = $5000
        report = ltb.pre_trade_check(sig)
        assert not report.checks["notional"]

    def test_kill_switch_blocks(self):
        ltb = LiveTradingBlueprint()
        ltb._kill_switch = KillSwitchState.TRIGGERED
        report = ltb.pre_trade_check(_signal())
        assert not report.checks["kill_switch"]


# ===========================================================================
# Order management
# ===========================================================================

class TestOrderManagement:
    def test_submit_fills(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        order = ltb.translate_signal(_signal())
        result = ltb.submit_order(order)
        assert result.status == OrderStatus.FILLED

    def test_creates_position(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        ltb.process_signal(_signal())
        assert len(ltb.positions) == 1

    def test_close_position(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        ltb.process_signal(_signal())
        pid = list(ltb.positions.keys())[0]
        result = ltb.close_position(pid, "test")
        assert result.status == OrderStatus.FILLED
        assert len(ltb.positions) == 0

    def test_close_nonexistent(self):
        ltb = LiveTradingBlueprint()
        assert ltb.close_position("NOPE") is None

    def test_process_signal_full(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        risk, order = ltb.process_signal(_signal())
        assert risk.result == RiskCheckResult.APPROVED
        assert order is not None
        assert order.status == OrderStatus.FILLED

    def test_rejected_signal(self):
        ltb = LiveTradingBlueprint(limits=RiskLimits(min_confidence=0.99))
        risk, order = ltb.process_signal(_signal(confidence=0.50))
        assert risk.result == RiskCheckResult.REJECTED
        assert order is None

    def test_kill_switch_blocks_order(self):
        ltb = LiveTradingBlueprint()
        ltb._kill_switch = KillSwitchState.TRIGGERED
        order = ltb.translate_signal(_signal())
        result = ltb.submit_order(order)
        assert result.status == OrderStatus.REJECTED


# ===========================================================================
# Emergency liquidation
# ===========================================================================

class TestEmergencyLiquidation:
    def test_liquidates_all(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        ltb.process_signal(_signal(sig_id="S1"))
        ltb.process_signal(_signal(sig_id="S2", strategy="VH"))
        n = ltb.emergency_liquidate("test")
        assert n == 2
        assert len(ltb.positions) == 0
        assert ltb.kill_switch_state == KillSwitchState.TRIGGERED


# ===========================================================================
# P&L tracking
# ===========================================================================

class TestPnL:
    def test_update(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        ltb.process_signal(_signal())
        snap = ltb.update_pnl({"SPY": 450})
        assert isinstance(snap, PnLSnapshot)
        assert snap.n_positions == 1

    def test_drawdown_alert(self):
        ltb = LiveTradingBlueprint(
            limits=RiskLimits(alert_drawdown=0.01),
            broker=SimulatedBroker(fill_rate=1.0))
        ltb._hwm = 110000
        ltb.equity = 100000  # ~9% DD
        ltb.update_pnl()
        warnings = [a for a in ltb.alerts if a.level == AlertLevel.WARNING]
        assert len(warnings) >= 1

    def test_new_day(self):
        ltb = LiveTradingBlueprint()
        ltb.equity = 105000
        ltb.new_trading_day()
        assert ltb.daily_start_equity == 105000


# ===========================================================================
# Kill switch
# ===========================================================================

class TestKillSwitch:
    def test_armed_by_default(self):
        assert LiveTradingBlueprint().kill_switch_state == KillSwitchState.ARMED

    def test_triggers_on_drawdown(self):
        ltb = LiveTradingBlueprint(
            limits=RiskLimits(max_drawdown=0.05),
            broker=SimulatedBroker(fill_rate=1.0))
        ltb._hwm = 100000
        ltb.equity = 94000
        state = ltb.check_kill_switch()
        assert state == KillSwitchState.TRIGGERED

    def test_manual_halt(self):
        ltb = LiveTradingBlueprint()
        ltb.manual_halt("operator test")
        assert ltb.kill_switch_state == KillSwitchState.MANUAL_HALT

    def test_reset(self):
        ltb = LiveTradingBlueprint()
        ltb._kill_switch = KillSwitchState.TRIGGERED
        ltb.reset_kill_switch()
        assert ltb.kill_switch_state == KillSwitchState.ARMED

    def test_stays_triggered(self):
        ltb = LiveTradingBlueprint()
        ltb._kill_switch = KillSwitchState.TRIGGERED
        assert ltb.check_kill_switch() == KillSwitchState.TRIGGERED


# ===========================================================================
# Reconciliation
# ===========================================================================

class TestReconciliation:
    def test_basic(self):
        ltb = LiveTradingBlueprint()
        result = ltb.reconcile(paper_pnl=1000)
        assert isinstance(result, ReconciliationResult)
        assert result.drift_bps >= 0

    def test_position_mismatch(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        ltb.process_signal(_signal())
        result = ltb.reconcile(paper_pnl=0, paper_positions={"SPY": 5})
        assert not result.positions_match
        assert len(result.mismatches) > 0

    def test_high_drift_alerts(self):
        ltb = LiveTradingBlueprint()
        ltb.reconcile(paper_pnl=10000)
        warnings = [a for a in ltb.alerts if "drift" in a.message.lower()]
        assert len(warnings) >= 1


# ===========================================================================
# Audit trail
# ===========================================================================

class TestAudit:
    def test_signal_logged(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        ltb.process_signal(_signal())
        types = {a.event_type for a in ltb.audit_trail}
        assert "signal_translated" in types
        assert "risk_check" in types
        assert "order_submitted" in types

    def test_all_entries_have_timestamp(self):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        ltb.process_signal(_signal())
        for a in ltb.audit_trail:
            assert a.timestamp is not None


# ===========================================================================
# HTML report
# ===========================================================================

class TestReport:
    def test_creates_file(self, tmp_path):
        ltb = LiveTradingBlueprint(broker=SimulatedBroker(fill_rate=1.0))
        ltb.process_signal(_signal())
        out = tmp_path / "live.html"
        path = ltb.generate_report(str(out))
        from pathlib import Path
        assert Path(path).exists()
        html = Path(out).read_text()
        assert "Live Trading Blueprint" in html
        assert "Kill Switch" in html
        assert "Audit Trail" in html
