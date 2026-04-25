"""Tests for shared/types.py — verify all TypedDict shapes are constructable and well-formed."""

from datetime import datetime

from shared.types import (
    AlertsConfig,
    AppConfig,
    BacktestConfig,
    IronCondorOpportunity,
    MLConfig,
    PositionSizeResult,
    PredictionResult,
    RiskConfig,
    ScoredSpreadOpportunity,
    SpreadOpportunity,
    StrategyConfig,
    TradeAnalysis,
    TradeRecommendation,
)


class TestPositionSizeResult:
    def test_construct_with_all_fields(self):
        r = PositionSizeResult(
            recommended_size=5.0, kelly_size=8.0, fractional_kelly=4.0,
            confidence_adjusted=3.5, capped_size=3.0, applied_constraints=["max_pos"],
            expected_value=120.0, kelly_fraction_used=0.5, ml_confidence=0.82,
        )
        assert r["recommended_size"] == 5.0
        assert r["applied_constraints"] == ["max_pos"]
        assert r["ml_confidence"] == 0.82

    def test_key_access(self):
        r = PositionSizeResult(
            recommended_size=1, kelly_size=2, fractional_kelly=1,
            confidence_adjusted=1, capped_size=1, applied_constraints=[],
            expected_value=0, kelly_fraction_used=0.25, ml_confidence=0.5,
        )
        assert set(r.keys()) == {
            "recommended_size", "kelly_size", "fractional_kelly",
            "confidence_adjusted", "capped_size", "applied_constraints",
            "expected_value", "kelly_fraction_used", "ml_confidence",
        }


class TestPredictionResult:
    def test_minimal_construct(self):
        r: PredictionResult = {"prediction": 1, "probability": 0.8}
        assert r["prediction"] == 1

    def test_with_fallback(self):
        r: PredictionResult = {
            "prediction": 0, "probability": 0.5, "confidence": 0.5,
            "signal": "neutral", "signal_strength": 0.0,
            "timestamp": "2026-01-01T00:00:00Z", "fallback": True,
        }
        assert r["fallback"] is True
        assert r["signal"] == "neutral"


class TestSpreadOpportunity:
    def _make(self, **overrides):
        base = dict(
            ticker="SPY", type="bull_put_spread", expiration=datetime(2026, 5, 16),
            dte=30, short_strike=540.0, long_strike=535.0, short_delta=-0.12,
            credit=1.50, max_loss=350.0, max_profit=150.0, profit_target=75.0,
            stop_loss=525.0, spread_width=5.0, current_price=560.0,
            distance_to_short=20.0, pop=0.82, risk_reward=0.43,
        )
        base.update(overrides)
        return SpreadOpportunity(**base)

    def test_construct(self):
        opp = self._make()
        assert opp["ticker"] == "SPY"
        assert opp["spread_width"] == 5.0

    def test_iron_condor_extends_spread(self):
        ic = IronCondorOpportunity(
            ticker="SPY", type="iron_condor", expiration=datetime(2026, 5, 16),
            dte=30, short_strike=540.0, long_strike=535.0, short_delta=-0.12,
            credit=3.00, max_loss=200.0, max_profit=300.0, profit_target=150.0,
            stop_loss=0, spread_width=5.0, current_price=560.0,
            distance_to_short=20.0, pop=0.78, risk_reward=1.5,
            call_short_strike=580.0, call_long_strike=585.0,
            put_credit=1.50, call_credit=1.50,
            distance_to_put_short=20.0, distance_to_call_short=20.0,
        )
        assert ic["call_short_strike"] == 580.0
        assert ic["put_credit"] + ic["call_credit"] == 3.00

    def test_scored_adds_score(self):
        scored = ScoredSpreadOpportunity(
            ticker="SPY", type="bull_put_spread", expiration=datetime(2026, 5, 16),
            dte=30, short_strike=540.0, long_strike=535.0, short_delta=-0.12,
            credit=1.50, max_loss=350.0, max_profit=150.0, profit_target=75.0,
            stop_loss=525.0, spread_width=5.0, current_price=560.0,
            distance_to_short=20.0, pop=0.82, risk_reward=0.43, score=85.0,
        )
        assert scored["score"] == 85.0


class TestTradeAnalysis:
    def test_minimal_fallback(self):
        ta: TradeAnalysis = {"ticker": "SPY", "spread_type": "bull_put", "error": True}
        assert ta["error"] is True

    def test_full(self):
        rec = TradeRecommendation(
            action="ENTER", confidence="high", score=88.0,
            position_size=2.0, reasoning=["IV > 30", "bull regime"],
            ml_probability=0.85,
        )
        ta: TradeAnalysis = {
            "ticker": "SPY", "spread_type": "bull_put",
            "timestamp": "2026-01-01", "recommendation": rec,
        }
        assert ta["recommendation"]["action"] == "ENTER"
        assert ta["recommendation"]["reasoning"][0] == "IV > 30"


class TestConfigTypes:
    def test_strategy_config(self):
        c: StrategyConfig = {"min_dte": 25, "max_dte": 45, "symbols": ["SPY", "QQQ"]}
        assert c["min_dte"] == 25

    def test_risk_config(self):
        c: RiskConfig = {"max_position_size": 0.05, "kelly_fraction": 0.25}
        assert c["kelly_fraction"] == 0.25

    def test_alerts_config(self):
        c: AlertsConfig = {"telegram_enabled": False, "min_score": 60.0}
        assert c["telegram_enabled"] is False

    def test_ml_config(self):
        c: MLConfig = {"min_confidence": 0.6, "feature_set": ["vix", "rsi"]}
        assert len(c["feature_set"]) == 2

    def test_backtest_config(self):
        c: BacktestConfig = {"initial_capital": 100_000, "commission": 0.65}
        assert c["initial_capital"] == 100_000

    def test_app_config_nested(self):
        c: AppConfig = {
            "strategy": {"min_dte": 25},
            "risk": {"max_positions": 10},
            "paper_trading": True,
            "log_level": "INFO",
        }
        assert c["paper_trading"] is True
        assert c["strategy"]["min_dte"] == 25
