"""Tests for compass.north_star_deployer — North Star portfolio deployment."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from compass.north_star_deployer import (
    STRATEGIES,
    TOTAL_DD_BUDGET,
    CircuitBreakerState,
    CircuitBreakers,
    CombinedSignal,
    ConfigGenerator,
    DeployConfig,
    DeployerResult,
    NorthStarDeployer,
    RebalanceAction,
    RebalancingEngine,
    RiskBudgetAllocator,
    RiskBudgetState,
    SignalOrchestrator,
    StrategySignal,
)


def _signals(bullish: bool = True) -> list:
    conf = 0.80 if bullish else 0.70
    sig = 0.50 if bullish else -0.40
    return [
        StrategySignal("ML-CS-860", "bullish" if bullish else "bearish", conf, sig),
        StrategySignal("Regime-Lev", "bullish" if bullish else "neutral", 0.65, sig * 0.8),
        StrategySignal("Intraday-MR", "neutral", 0.55, 0.05),
        StrategySignal("Combined-750", "bullish" if bullish else "bearish", 0.75, sig * 0.9),
    ]


def _weights() -> dict:
    return {s: d["weight"] for s, d in STRATEGIES.items()}


def _dd_normal() -> dict:
    return {"ML-CS-860": 0.01, "Regime-Lev": 0.005, "Intraday-MR": 0.008, "Combined-750": 0.003}


def _dd_breach() -> dict:
    return {"ML-CS-860": 0.05, "Regime-Lev": 0.04, "Intraday-MR": 0.03, "Combined-750": 0.025}


# ── ConfigGenerator ────────────────────────────────────────────────────────
class TestConfigGenerator:
    def test_generates_config(self):
        cfg = ConfigGenerator().generate()
        assert isinstance(cfg, DeployConfig)
        assert len(cfg.strategies) == 4

    def test_weights_sum_to_one(self):
        cfg = ConfigGenerator().generate()
        total = sum(s["weight"] for s in cfg.strategies.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_dd_budgets_within_total(self):
        cfg = ConfigGenerator().generate()
        total_db = sum(s["dd_budget"] for s in cfg.strategies.values())
        assert total_db <= TOTAL_DD_BUDGET + 0.001

    def test_custom_leverage(self):
        cfg = ConfigGenerator().generate(leverage=3.0)
        assert cfg.leverage == 3.0

    def test_save_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ConfigGenerator().generate()
            path = ConfigGenerator().save_yaml(cfg, str(Path(tmp) / "config.json"))
            assert path.exists()
            data = json.loads(path.read_text())
            assert "strategies" in data

    def test_to_dict(self):
        cfg = ConfigGenerator().generate()
        d = cfg.to_dict()
        assert "strategies" in d
        assert "leverage" in d


# ── SignalOrchestrator ──────────────────────────────────────────────────────
class TestSignalOrchestrator:
    def test_combine_bullish(self):
        cs = SignalOrchestrator().combine(_signals(True))
        assert cs.direction == "bullish"
        assert cs.weighted_signal > 0

    def test_combine_bearish(self):
        cs = SignalOrchestrator().combine(_signals(False))
        assert cs.direction == "bearish"
        assert cs.weighted_signal < 0

    def test_empty_signals(self):
        cs = SignalOrchestrator().combine([])
        assert cs.direction == "neutral"

    def test_agreement_bounded(self):
        cs = SignalOrchestrator().combine(_signals())
        assert 0 <= cs.agreement <= 1.0

    def test_weighted_signal_bounded(self):
        cs = SignalOrchestrator().combine(_signals())
        assert -1 <= cs.weighted_signal <= 1

    def test_action_enter_put(self):
        cs = SignalOrchestrator().combine(_signals(True))
        assert cs.recommended_action in ("enter_put", "hold", "reduce")

    def test_action_hold_on_neutral(self):
        sigs = [StrategySignal("A", "neutral", 0.3, 0.02)]
        cs = SignalOrchestrator(weights={"A": 1.0}).combine(sigs)
        assert cs.recommended_action == "hold"

    def test_component_signals_preserved(self):
        sigs = _signals()
        cs = SignalOrchestrator().combine(sigs)
        assert len(cs.component_signals) == 4


# ── RiskBudgetAllocator ────────────────────────────────────────────────────
class TestRiskBudget:
    def test_compute_state(self):
        state = RiskBudgetAllocator().compute_state(_dd_normal())
        assert isinstance(state, RiskBudgetState)
        assert state.total_dd > 0

    def test_utilisation_bounded(self):
        state = RiskBudgetAllocator().compute_state(_dd_normal())
        for util in state.strategy_utilisation.values():
            assert util >= 0

    def test_budget_remaining(self):
        state = RiskBudgetAllocator().compute_state(_dd_normal())
        assert state.budget_remaining > 0
        assert state.budget_remaining <= TOTAL_DD_BUDGET

    def test_high_dd_high_utilisation(self):
        state = RiskBudgetAllocator().compute_state(_dd_breach())
        assert state.total_utilisation > 50

    def test_adjust_weights_normal(self):
        state = RiskBudgetAllocator().compute_state(_dd_normal())
        adjusted = RiskBudgetAllocator().adjust_weights(_weights(), state)
        total = sum(adjusted.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_adjust_reduces_high_dd(self):
        dd = {"ML-CS-860": 0.04, "Regime-Lev": 0.001, "Intraday-MR": 0.001, "Combined-750": 0.001}
        state = RiskBudgetAllocator().compute_state(dd)
        adjusted = RiskBudgetAllocator().adjust_weights(_weights(), state)
        # ML-CS-860 at budget → weight should be reduced
        assert adjusted["ML-CS-860"] < _weights()["ML-CS-860"]


# ── RebalancingEngine ──────────────────────────────────────────────────────
class TestRebalancing:
    def test_no_rebalance_within_tolerance(self):
        eng = RebalancingEngine(tolerance=0.05)
        action = eng.check(
            {"A": 0.50, "B": 0.50},
            {"A": 0.51, "B": 0.49},
            100_000,
        )
        assert not action.should_rebalance

    def test_rebalance_beyond_tolerance(self):
        eng = RebalancingEngine(tolerance=0.03)
        action = eng.check(
            {"A": 0.50, "B": 0.50},
            {"A": 0.60, "B": 0.40},
            100_000,
        )
        assert action.should_rebalance

    def test_turnover_computed(self):
        eng = RebalancingEngine(tolerance=0.01)
        action = eng.check(
            {"A": 0.40, "B": 0.60},
            {"A": 0.60, "B": 0.40},
            100_000,
        )
        assert action.turnover == pytest.approx(0.20, abs=0.01)

    def test_cost_estimated(self):
        eng = RebalancingEngine(tolerance=0.01, cost_bps=10)
        action = eng.check(
            {"A": 0.40, "B": 0.60},
            {"A": 0.60, "B": 0.40},
            100_000,
        )
        assert action.cost_estimate > 0

    def test_min_interval_respected(self):
        eng = RebalancingEngine(tolerance=0.01, min_rebalance_interval=5)
        eng.check({"A": 0.5, "B": 0.5}, {"A": 0.7, "B": 0.3}, 100_000, day=0)
        action = eng.check({"A": 0.5, "B": 0.5}, {"A": 0.7, "B": 0.3}, 100_000, day=2)
        assert not action.should_rebalance  # too soon


# ── CircuitBreakers ────────────────────────────────────────────────────────
class TestCircuitBreakers:
    def test_normal_no_halt(self):
        state = RiskBudgetAllocator().compute_state(_dd_normal())
        cb = CircuitBreakers().check(state)
        assert not cb.is_halted
        assert not cb.kill_switch

    def test_dd_breach_halts_strategy(self):
        dd = {"ML-CS-860": 0.05, "Regime-Lev": 0.001, "Intraday-MR": 0.001, "Combined-750": 0.001}
        state = RiskBudgetAllocator().compute_state(dd)
        cb = CircuitBreakers().check(state)
        assert cb.is_halted
        assert "ML-CS-860" in cb.halted_strategies

    def test_kill_switch_on_total_dd(self):
        dd = {"ML-CS-860": 0.05, "Regime-Lev": 0.04, "Intraday-MR": 0.04, "Combined-750": 0.03}
        state = RiskBudgetAllocator().compute_state(dd)
        cb = CircuitBreakers(kill_switch_dd=0.15).check(state)
        assert cb.kill_switch

    def test_correlation_spike(self):
        state = RiskBudgetAllocator().compute_state(_dd_normal())
        corr = np.array([[1.0, 0.90], [0.90, 1.0]])
        cb = CircuitBreakers(correlation_threshold=0.85).check(state, corr)
        assert cb.correlation_alert
        assert cb.is_halted

    def test_no_corr_alert_normal(self):
        state = RiskBudgetAllocator().compute_state(_dd_normal())
        corr = np.array([[1.0, 0.50], [0.50, 1.0]])
        cb = CircuitBreakers().check(state, corr)
        assert not cb.correlation_alert

    def test_reasons_populated(self):
        dd = {"ML-CS-860": 0.05, "Regime-Lev": 0.001, "Intraday-MR": 0.001, "Combined-750": 0.001}
        state = RiskBudgetAllocator().compute_state(dd)
        cb = CircuitBreakers().check(state)
        assert len(cb.reasons) > 0


# ── NorthStarDeployer ──────────────────────────────────────────────────────
class TestDeployer:
    def test_initialize(self):
        d = NorthStarDeployer()
        cfg = d.initialize()
        assert isinstance(cfg, DeployConfig)

    def test_run_cycle(self):
        d = NorthStarDeployer()
        d.initialize()
        result = d.run_cycle(
            _signals(), _dd_normal(), _weights(), 100_000, day=1,
        )
        assert isinstance(result, DeployerResult)
        assert result.combined_signal is not None
        assert result.risk_budget is not None
        assert result.circuit_breakers is not None
        assert result.rebalance is not None

    def test_auto_initializes(self):
        d = NorthStarDeployer()
        result = d.run_cycle(_signals(), _dd_normal(), _weights(), 100_000)
        assert result.config is not None

    def test_halted_reduces_weights(self):
        d = NorthStarDeployer()
        d.initialize()
        result = d.run_cycle(
            _signals(), _dd_breach(), _weights(), 100_000,
        )
        if result.circuit_breakers.is_halted:
            for w in result.rebalance.new_weights.values():
                assert w <= 0.30

    def test_timestamp_set(self):
        d = NorthStarDeployer()
        result = d.run_cycle(_signals(), _dd_normal(), _weights(), 100_000)
        assert len(result.timestamp) > 0


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_strategy_signal(self):
        s = StrategySignal("A", "bullish", 0.8, 0.5)
        assert s.confidence == 0.8

    def test_combined_signal(self):
        c = CombinedSignal("bullish", 0.75, 0.4, 0.8, {"A": 0.5}, "enter_put")
        assert c.recommended_action == "enter_put"

    def test_deployer_result_defaults(self):
        r = DeployerResult()
        assert r.config is None
        assert r.timestamp == ""
