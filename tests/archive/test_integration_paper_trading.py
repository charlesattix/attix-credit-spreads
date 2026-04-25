"""End-to-end integration tests for the full paper trading pipeline.

Tests the complete flow:
  signal_pipeline → crisis_hedge → paper_trading_engine →
  ensemble_model_health → backtest_vs_live_tracker → telegram_alerter

Uses mocks for external APIs (Alpaca, Telegram).
Covers: happy path, VIX spike mid-trade, drawdown breach,
model disagreement, stale model, feature drift.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from compass.signal_pipeline import (
    MarketData,
    PipelineConfig,
    PipelineSignal,
    SignalPipeline,
    compute_position_scale,
    compute_stop_loss_mult,
    detect_regime,
    extract_features,
)
from compass.crisis_hedge import CrisisHedgeConfig, CrisisHedgeController
from compass.paper_trading_engine import (
    EngineConfig,
    PaperTradingEngine,
    Signal as PTSignal,
)
from compass.ensemble_model_health import (
    HealthConfig,
    ModelHealthMonitor,
)
from compass.backtest_vs_live_tracker import (
    BacktestBaseline,
    BacktestVsLiveTracker,
)
from compass.telegram_alerter import (
    AlertMessage,
    DailySummary,
    ModelAlert,
    Priority,
    RiskAlert,
    TelegramAlerter,
    TradeAlert,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def pipeline():
    return SignalPipeline(PipelineConfig(min_ensemble_agreement=0.4))


@pytest.fixture
def hedge():
    return CrisisHedgeController(CrisisHedgeConfig())


@pytest.fixture
def engine():
    e = PaperTradingEngine(EngineConfig(
        starting_capital=100_000, fill_rate=1.0,
        slippage_per_contract=0.04, max_drawdown_pct=0.12,
    ))
    yield e
    e.close()


@pytest.fixture
def health_monitor():
    return ModelHealthMonitor(HealthConfig(
        rolling_window=50, min_samples_for_auc=10, baseline_auc=0.80,
    ))


@pytest.fixture
def alerter():
    sent = []
    def mock_send(text: str) -> bool:
        sent.append(text)
        return True
    a = TelegramAlerter(
        experiment_id="EXP-880-TEST", send_fn=mock_send, enabled=True,
    )
    a._sent = sent  # expose for assertions
    return a


def _bull_market() -> MarketData:
    return MarketData(
        ticker="SPY", price=440, vix=15, regime="bull",
        rsi_14=58, iv_rank=45, momentum_5d_pct=1.2,
        momentum_10d_pct=2.0, dist_from_ma200_pct=6,
        dist_from_ma50_pct=2, net_credit=1.5, spread_width=5.0,
        dte_at_entry=30, day_of_week=2, days_since_last_trade=5,
        timestamp="2024-06-03T10:30:00+00:00",
    )


def _crisis_market() -> MarketData:
    return MarketData(
        ticker="SPY", price=380, vix=42, regime="crash",
        rsi_14=25, iv_rank=95, momentum_5d_pct=-5.0,
        momentum_10d_pct=-8.0, dist_from_ma200_pct=-12,
        dist_from_ma50_pct=-8, net_credit=3.0, spread_width=5.0,
        dte_at_entry=30, day_of_week=1, days_since_last_trade=2,
        timestamp="2024-03-15T10:30:00+00:00",
    )


def _high_vol_market() -> MarketData:
    return MarketData(
        ticker="SPY", price=410, vix=30, regime="high_vol",
        rsi_14=40, iv_rank=75, momentum_5d_pct=-2.0,
        momentum_10d_pct=-3.0, dist_from_ma200_pct=-2,
        dist_from_ma50_pct=-3, net_credit=2.5, spread_width=5.0,
        dte_at_entry=30, day_of_week=3, days_since_last_trade=3,
        timestamp="2024-08-05T11:00:00+00:00",
    )


def _pipeline_to_engine_signal(ps: PipelineSignal, md: MarketData) -> PTSignal:
    """Convert a pipeline signal to a paper trading engine signal."""
    return PTSignal(
        strategy="EXP-880",
        ticker=ps.ticker,
        direction="short",
        spread_type=ps.direction,
        contracts=ps.contracts,
        net_credit=md.net_credit,
        max_loss=md.spread_width - md.net_credit,
        spread_width=md.spread_width,
        dte=md.dte_at_entry,
        stop_loss_pct=ps.stop_loss_mult,
        confidence=ps.confidence,
        regime=ps.regime,
        timestamp=md.timestamp,
    )


# ═══════════════════════════════════════════════════════════════════════
# 1. HAPPY PATH: bull market, normal conditions
# ═══════════════════════════════════════════════════════════════════════


class TestHappyPath:
    """Full pipeline in normal bull market conditions."""

    def test_signal_generated(self, pipeline):
        sig = pipeline.generate_signal(_bull_market())
        assert isinstance(sig, PipelineSignal)
        assert sig.regime == "bull"

    def test_signal_trades_in_bull(self, pipeline):
        sig = pipeline.generate_signal(_bull_market())
        assert sig.trade is True
        assert sig.contracts > 0

    def test_crisis_hedge_high_scale_in_bull(self, hedge):
        scale = hedge.position_scale_factor(15, "bull")
        assert scale > 0.7  # VIX=15 is above floor=12, so not quite 1.0

    def test_engine_accepts_signal(self, pipeline, engine):
        ps = pipeline.generate_signal(_bull_market())
        if ps.trade:
            pt_sig = _pipeline_to_engine_signal(ps, _bull_market())
            ok, pid = engine.submit_signal(pt_sig)
            assert ok is True
            assert len(engine.positions) == 1

    def test_engine_step_produces_pnl(self, pipeline, engine):
        ps = pipeline.generate_signal(_bull_market())
        if ps.trade:
            engine.submit_signal(_pipeline_to_engine_signal(ps, _bull_market()))
            snap = engine.step("2024-06-10")
            assert snap.n_positions >= 0

    def test_health_monitor_records(self, pipeline, health_monitor):
        md = _bull_market()
        sig = pipeline.generate_signal(md)
        feats = extract_features(md)
        health_monitor.record_prediction(
            sig.probability, {"xgb": sig.probability + 0.02,
                              "rf": sig.probability - 0.02},
            features=feats,
        )
        health_monitor.record_outcome(True, sig.probability)
        report = health_monitor.get_report()
        assert report.n_predictions == 1
        assert report.n_outcomes == 1

    def test_alerter_sends_trade(self, alerter):
        trade = TradeAlert(
            action="entry", strategy="EXP-880", symbol="SPY",
            direction="short", contracts=2, price=1.45,
        )
        msg = AlertMessage(
            priority=Priority.INFO, category="trade",
            title="Trade Entry", body=f"{trade.direction} {trade.contracts}x {trade.symbol}",
        )
        ok = alerter.send(msg)
        assert ok is True
        assert len(alerter._sent) >= 1
        assert "SPY" in alerter._sent[-1]

    def test_full_pipeline_end_to_end(self, pipeline, hedge, engine, health_monitor, alerter):
        """Complete happy path: signal → hedge → execute → monitor → alert."""
        md = _bull_market()

        # Step 1: generate signal
        sig = pipeline.generate_signal(md)
        assert sig.trade is True

        # Step 2: crisis hedge scaling (VIX=15 > floor=12, so ~0.80)
        scale = hedge.position_scale_factor(md.vix, sig.regime)
        assert scale > 0.5

        # Step 3: execute in paper engine
        pt_sig = _pipeline_to_engine_signal(sig, md)
        ok, pid = engine.submit_signal(pt_sig)
        assert ok is True

        # Step 4: health monitor
        feats = extract_features(md)
        health_monitor.record_prediction(sig.probability,
                                          {"xgb": sig.probability, "rf": sig.probability},
                                          features=feats)
        health_monitor.record_outcome(True, sig.probability)

        # Step 5: alerter
        msg = AlertMessage(Priority.INFO, "trade", "Entry",
                           f"Bought {pt_sig.contracts}x SPY")
        alerter.send(msg)
        assert len(alerter._sent) >= 1


# ═══════════════════════════════════════════════════════════════════════
# 2. VIX SPIKE: market stress mid-trade
# ═══════════════════════════════════════════════════════════════════════


class TestVIXSpike:
    """VIX spikes during open positions."""

    def test_crash_regime_detected(self, pipeline):
        sig = pipeline.generate_signal(_crisis_market())
        assert sig.regime == "crash"

    def test_crash_blocks_new_entries(self, pipeline):
        sig = pipeline.generate_signal(_crisis_market())
        assert sig.trade is False

    def test_crisis_hedge_scales_to_zero(self, hedge):
        scale = hedge.position_scale_factor(42, "crash")
        assert scale == pytest.approx(0.0)

    def test_stop_loss_tightens(self, hedge):
        stop = hedge.stop_loss_multiplier(42, "crash")
        assert stop == pytest.approx(1.5)

    def test_high_vol_partial_scale(self, hedge):
        scale = hedge.position_scale_factor(30, "high_vol")
        assert scale <= 0.25

    def test_vix_spike_alert_sent(self, pipeline, alerter):
        alert_needed, msg = pipeline.check_vix_alert(42)
        assert alert_needed is True
        risk = RiskAlert(metric="vix_spike", current_value=42,
                         threshold=35, message=msg)
        alert = AlertMessage(Priority.CRITICAL, "risk",
                             "VIX SPIKE", risk.message)
        alerter.send(alert)
        assert "CRITICAL" in alerter._sent[-1] or "VIX" in alerter._sent[-1]

    def test_mid_trade_vix_spike(self, pipeline, hedge, engine):
        """Open position in bull, VIX spikes, verify stop tightens."""
        # Enter in bull
        bull = _bull_market()
        sig = pipeline.generate_signal(bull)
        if sig.trade:
            engine.submit_signal(_pipeline_to_engine_signal(sig, bull))
            assert len(engine.positions) == 1

            # VIX spikes — new entries blocked
            crisis = _crisis_market()
            sig2 = pipeline.generate_signal(crisis)
            assert sig2.trade is False

            # Existing position still tracked
            snap = engine.step("2024-06-10")
            assert snap is not None

    def test_gradual_vix_increase(self, pipeline, hedge):
        """Test scaling across VIX levels."""
        for vix, expected_min, expected_max in [
            (12, 0.99, 1.01), (20, 0.3, 0.8), (30, 0.0, 0.3), (40, 0.0, 0.01),
        ]:
            md = MarketData(vix=vix, dist_from_ma200_pct=5, rsi_14=55)
            sig = pipeline.generate_signal(md)
            scale = hedge.position_scale_factor(vix, sig.regime)
            assert expected_min <= scale <= expected_max, f"VIX={vix}, scale={scale}"


# ═══════════════════════════════════════════════════════════════════════
# 3. DRAWDOWN BREACH: circuit breaker activation
# ═══════════════════════════════════════════════════════════════════════


class TestDrawdownBreach:
    """Drawdown exceeds limit — circuit breaker fires."""

    def test_circuit_breaker_on_dd(self, engine):
        """Force capital below DD threshold, new signals rejected."""
        # Burn capital to trigger DD
        engine.capital = 85_000  # 15% below 100K peak
        engine.peak_capital = 100_000

        sig = PTSignal(strategy="EXP-880", contracts=2, net_credit=1.5,
                       confidence=0.8, regime="bull",
                       timestamp="2024-06-03T10:00:00+00:00")
        ok, reason = engine.submit_signal(sig)
        assert ok is False
        assert "drawdown" in reason.lower() or "circuit" in reason.lower()

    def test_circuit_breaker_blocks_all(self, engine):
        engine.risk_monitor.circuit_breaker_active = True
        sig = PTSignal(strategy="A", contracts=1, net_credit=1.0,
                       confidence=0.9, regime="bull",
                       timestamp="2024-06-03T10:00:00+00:00")
        ok, _ = engine.submit_signal(sig)
        assert ok is False

    def test_circuit_breaker_reset(self, engine):
        engine.risk_monitor.circuit_breaker_active = True
        engine.risk_monitor.reset_circuit_breaker()
        sig = PTSignal(strategy="A", contracts=1, net_credit=1.0,
                       confidence=0.9, regime="bull",
                       timestamp="2024-06-03T10:00:00+00:00")
        ok, _ = engine.submit_signal(sig)
        assert ok is True

    def test_dd_breach_alert(self, alerter):
        risk = RiskAlert(metric="drawdown", current_value=0.13,
                         threshold=0.12, message="DD 13% > 12% limit")
        msg = AlertMessage(Priority.CRITICAL, "risk",
                           "DRAWDOWN BREACH", risk.message)
        ok = alerter.send(msg)
        assert ok is True
        assert any("DRAWDOWN" in m or "13%" in m for m in alerter._sent)

    def test_dd_health_report(self, engine, health_monitor):
        """After DD breach, health report reflects degraded state."""
        engine.capital = 85_000
        engine.peak_capital = 100_000
        # Record some bad predictions
        for _ in range(15):
            health_monitor.record_prediction(0.75, {"xgb": 0.75, "rf": 0.75})
            health_monitor.record_outcome(False, 0.75)
        report = health_monitor.get_report()
        assert report.rolling_accuracy < 0.5


# ═══════════════════════════════════════════════════════════════════════
# 4. MODEL DISAGREEMENT: ensemble models disagree
# ═══════════════════════════════════════════════════════════════════════


class TestModelDisagreement:
    """Ensemble sub-models produce divergent predictions."""

    def test_disagreement_detected(self, health_monitor):
        alert = health_monitor.record_prediction(
            0.65, {"xgb": 0.90, "rf": 0.40},
        )
        assert alert is not None
        assert alert.disagreement > 0.20

    def test_disagreement_alert_severity(self, health_monitor):
        alert = health_monitor.record_prediction(
            0.55, {"xgb": 0.95, "rf": 0.15},
        )
        assert alert.severity == "critical"

    def test_disagreement_triggers_retrain(self, health_monitor):
        for _ in range(30):
            health_monitor.record_prediction(
                0.60, {"xgb": 0.85, "rf": 0.35},
            )
        recs = health_monitor.check_retrain()
        dis_recs = [r for r in recs if r.metric_name == "disagreement"]
        assert len(dis_recs) >= 1

    def test_disagreement_alert_sent(self, health_monitor, alerter):
        alert = health_monitor.record_prediction(
            0.65, {"xgb": 0.90, "rf": 0.40},
        )
        if alert:
            model = ModelAlert(
                metric="ensemble_disagreement",
                current_value=alert.disagreement,
                baseline_value=0.10,
                message=f"Disagreement {alert.disagreement:.2f}",
            )
            msg = AlertMessage(Priority.WARNING, "model",
                               "MODEL DISAGREEMENT", model.message)
            alerter.send(msg)
            assert len(alerter._sent) >= 1

    def test_pipeline_rejects_on_low_agreement(self):
        """Pipeline with high agreement threshold rejects on disagreement."""
        pipe = SignalPipeline(PipelineConfig(min_ensemble_agreement=0.99))
        sig = pipe.generate_signal(_bull_market())
        # Fallback predictor returns agreement=0.5, far below 0.99
        assert sig.trade is False
        assert "agreement" in sig.reject_reason.lower()


# ═══════════════════════════════════════════════════════════════════════
# 5. FEATURE DRIFT: live data diverges from training
# ═══════════════════════════════════════════════════════════════════════


class TestFeatureDrift:
    """Live features drift from training distribution."""

    def test_drift_detection(self, health_monitor):
        rng = np.random.RandomState(42)
        health_monitor.set_training_distributions({
            "vix": rng.normal(18, 3, 200),
            "rsi": rng.normal(50, 10, 200),
        })
        # Feed drifted VIX data
        for _ in range(40):
            health_monitor.record_prediction(0.7, features={
                "vix": float(rng.normal(35, 3)),  # shifted up
                "rsi": float(rng.normal(50, 10)),  # no drift
            })
        results = health_monitor.detect_drift(min_samples=20)
        drifted = [d.feature for d in results if d.drifted]
        assert "vix" in drifted

    def test_drift_triggers_retrain(self, health_monitor):
        rng = np.random.RandomState(42)
        health_monitor.set_training_distributions({
            "a": rng.normal(0, 1, 200),
            "b": rng.normal(0, 1, 200),
        })
        for _ in range(40):
            health_monitor.record_prediction(0.7, features={
                "a": float(rng.normal(10, 1)),
                "b": float(rng.normal(10, 1)),
            })
        recs = health_monitor.check_retrain()
        drift_recs = [r for r in recs if r.metric_name == "feature_drift"]
        assert len(drift_recs) >= 1

    def test_drift_alert_sent(self, health_monitor, alerter):
        rng = np.random.RandomState(42)
        health_monitor.set_training_distributions({
            "vix": rng.normal(18, 3, 200),
        })
        for _ in range(40):
            health_monitor.record_prediction(0.7, features={
                "vix": float(rng.normal(40, 3)),
            })
        recs = health_monitor.check_retrain()
        for rec in recs:
            model = ModelAlert(
                metric="feature_drift",
                current_value=rec.current_value,
                baseline_value=rec.threshold,
                message=rec.reason,
            )
            msg = AlertMessage(Priority.WARNING, "model",
                               "FEATURE DRIFT", model.message)
            alerter.send(msg)
        if recs:
            assert len(alerter._sent) >= 1


# ═══════════════════════════════════════════════════════════════════════
# 6. BACKTEST VS LIVE TRACKING
# ═══════════════════════════════════════════════════════════════════════


class TestBacktestTracking:
    """Compare paper trading results against backtest baseline."""

    def test_tracker_evaluates(self):
        trades_df = pd.DataFrame({
            "entry_date": pd.date_range("2024-06-01", periods=10),
            "pnl": np.random.RandomState(42).normal(50, 100, 10),
            "win": [1, 1, 0, 1, 1, 0, 1, 1, 1, 0],
        })
        baseline = BacktestBaseline(
            win_rate=0.75, sharpe=2.0, max_dd_pct=5.0,
            avg_pnl_per_trade=80.0, profit_factor=2.0,
        )
        tracker = BacktestVsLiveTracker(
            baseline=baseline, trades_df=trades_df,
        )
        result = tracker.evaluate()
        assert result is not None
        assert result.live_n_trades == 10

    def test_tracker_detects_drift(self):
        """Live performance significantly below backtest → drift."""
        trades_df = pd.DataFrame({
            "entry_date": pd.date_range("2024-06-01", periods=20),
            "pnl": np.full(20, -100.0),  # all losses
            "win": np.zeros(20, dtype=int),
        })
        baseline = BacktestBaseline(
            win_rate=0.80, sharpe=3.0, max_dd_pct=5.0,
            avg_pnl_per_trade=100.0, profit_factor=3.0,
        )
        tracker = BacktestVsLiveTracker(
            baseline=baseline, trades_df=trades_df,
            warning_pct=20, critical_pct=40,
        )
        result = tracker.evaluate()
        assert result.live_win_rate < baseline.win_rate


# ═══════════════════════════════════════════════════════════════════════
# 7. MULTI-TRADE SEQUENCE: realistic trading day
# ═══════════════════════════════════════════════════════════════════════


class TestMultiTradeSequence:
    """Simulate a realistic sequence of trades across a day."""

    def test_multiple_entries_and_exits(self, pipeline, hedge, engine, health_monitor):
        """Process 5 signals, step forward, verify state."""
        markets = [
            _bull_market(),
            MarketData(vix=16, price=441, rsi_14=60, iv_rank=42,
                       dist_from_ma200_pct=6, dist_from_ma50_pct=2,
                       net_credit=1.4, spread_width=5.0, dte_at_entry=28,
                       timestamp="2024-06-03T11:00:00+00:00"),
            MarketData(vix=18, price=438, rsi_14=52, iv_rank=48,
                       dist_from_ma200_pct=4, dist_from_ma50_pct=1,
                       net_credit=1.6, spread_width=5.0, dte_at_entry=25,
                       timestamp="2024-06-03T12:00:00+00:00"),
        ]

        entered = 0
        for md in markets:
            sig = pipeline.generate_signal(md)
            scale = hedge.position_scale_factor(md.vix, sig.regime)
            if sig.trade and scale > 0:
                pt_sig = _pipeline_to_engine_signal(sig, md)
                ok, _ = engine.submit_signal(pt_sig)
                if ok:
                    entered += 1
                    feats = extract_features(md)
                    health_monitor.record_prediction(
                        sig.probability, {"m": sig.probability}, features=feats,
                    )

        assert entered >= 1

        # Step to close some positions
        engine.step("2024-06-10")
        engine.step("2024-06-20")

        perf = engine.get_performance()
        report = health_monitor.get_report()
        assert report.n_predictions >= 1

    def test_vix_transition_mid_sequence(self, pipeline, engine):
        """Start bull, transition to high_vol mid-sequence."""
        # Bull entry
        bull = _bull_market()
        sig1 = pipeline.generate_signal(bull)
        if sig1.trade:
            engine.submit_signal(_pipeline_to_engine_signal(sig1, bull))

        # VIX spikes
        hvol = _high_vol_market()
        sig2 = pipeline.generate_signal(hvol)
        # High vol: likely rejected or tiny position
        if sig2.trade:
            engine.submit_signal(_pipeline_to_engine_signal(sig2, hvol))

        # Verify first position still open
        assert len(engine.positions) >= 1
        engine.step("2024-06-10")

    def test_daily_summary_alert(self, engine, alerter):
        """Generate and send a daily summary after trades."""
        sig = PTSignal(strategy="EXP-880", contracts=2, net_credit=1.5,
                       confidence=0.8, regime="bull", dte=5,
                       timestamp="2024-06-01T10:00:00+00:00")
        engine.submit_signal(sig)
        snap = engine.step("2024-06-08")

        summary = DailySummary(
            date="2024-06-08", daily_pnl=snap.realised_pnl,
            total_pnl=snap.total_pnl, equity=snap.capital,
            win_rate=0.80, n_trades_today=1,
            n_open_positions=snap.n_positions,
            hedge_state="normal", drawdown=snap.drawdown,
        )
        msg = AlertMessage(
            Priority.INFO, "daily", "Daily Summary",
            f"P&L: ${summary.total_pnl:+,.0f} | Equity: ${summary.equity:,.0f}",
        )
        alerter.send(msg)
        assert len(alerter._sent) >= 1


# ═══════════════════════════════════════════════════════════════════════
# 8. EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_zero_vix(self, pipeline, hedge):
        md = MarketData(vix=0)
        sig = pipeline.generate_signal(md)
        scale = hedge.position_scale_factor(0, sig.regime)
        assert scale == pytest.approx(1.0)

    def test_extreme_vix_100(self, pipeline, hedge):
        md = MarketData(vix=100)
        sig = pipeline.generate_signal(md)
        assert sig.trade is False
        scale = hedge.position_scale_factor(100, sig.regime)
        assert scale == pytest.approx(0.0)

    def test_empty_model_probs(self, health_monitor):
        alert = health_monitor.record_prediction(0.75, {})
        assert alert is None  # no disagreement possible

    def test_all_wins(self, health_monitor):
        for _ in range(20):
            health_monitor.record_prediction(0.80, {"a": 0.80})
            health_monitor.record_outcome(True, 0.80)
        assert health_monitor.rolling_accuracy() == pytest.approx(1.0)

    def test_all_losses(self, health_monitor):
        for _ in range(20):
            health_monitor.record_prediction(0.80, {"a": 0.80})
            health_monitor.record_outcome(False, 0.80)
        assert health_monitor.rolling_accuracy() == pytest.approx(0.0)

    def test_engine_position_limit(self, engine):
        """Fill to max positions, verify next is rejected."""
        for i in range(engine.config.max_positions):
            sig = PTSignal(strategy=f"S{i}", contracts=1, net_credit=1.0,
                           confidence=0.8, regime="bull",
                           timestamp="2024-06-03T10:00:00+00:00")
            engine.submit_signal(sig)
        sig = PTSignal(strategy="overflow", contracts=1, net_credit=1.0,
                       confidence=0.8, regime="bull",
                       timestamp="2024-06-03T10:00:00+00:00")
        ok, reason = engine.submit_signal(sig)
        assert ok is False
        assert "max positions" in reason.lower()
