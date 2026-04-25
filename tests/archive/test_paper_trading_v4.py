"""Tests for compass/paper_trading_v4.py — Paper Trading Harness v4.

50+ tests covering: strategy metadata, allocation engine, harness lifecycle,
health checks, readiness checklist, trade frequency, regime weights, signals,
portfolio state, circuit breaker, edge cases, report generation.
"""

import pytest
from compass.paper_trading_v4 import (
    StrategyId, STRATEGY_META, REGIME_WEIGHTS, DEFAULT_WEIGHTS, DEFAULT_LEVERAGE,
    MarketState, PortfolioState, AllocationDecision, Signal, HealthCheck,
    ReadinessItem, AllocationEngine, PaperTradingHarness,
    build_readiness_checklist, expected_trade_frequency,
    generate_readiness_report, TRADING_DAYS,
)


# ═══════════════════════════════════════════════════════════════════════════
# Strategy Metadata (7 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestStrategyMeta:
    def test_five_strategies(self):
        assert len(StrategyId) == 5

    def test_all_have_metadata(self):
        for sid in StrategyId:
            assert sid in STRATEGY_META

    def test_metadata_has_required_fields(self):
        for sid, m in STRATEGY_META.items():
            assert "name" in m
            assert "tickers" in m
            assert "type" in m
            assert "trades_per_month" in m
            assert "capital_pct" in m

    def test_capital_pcts_sum_to_one(self):
        total = sum(m["capital_pct"] for m in STRATEGY_META.values())
        assert abs(total - 1.0) < 0.01

    def test_exp1220_is_primary(self):
        assert STRATEGY_META[StrategyId.EXP1220]["capital_pct"] >= 0.40

    def test_all_have_positive_frequency(self):
        for sid, m in STRATEGY_META.items():
            assert m["trades_per_month"] > 0
            assert m["avg_hold_days"] > 0

    def test_tickers_are_lists(self):
        for sid, m in STRATEGY_META.items():
            assert isinstance(m["tickers"], list)
            assert len(m["tickers"]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Regime Weights (8 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestRegimeWeights:
    def test_all_regimes_defined(self):
        for r in ["bull", "bear", "crash", "high_vol", "low_vol"]:
            assert r in REGIME_WEIGHTS

    def test_weights_sum_to_one(self):
        for regime, alloc in REGIME_WEIGHTS.items():
            total = sum(alloc["weights"].values())
            assert abs(total - 1.0) < 0.01, f"{regime} weights sum to {total}"

    def test_bull_high_leverage(self):
        assert REGIME_WEIGHTS["bull"]["leverage"] >= 2.0

    def test_crash_low_leverage(self):
        assert REGIME_WEIGHTS["crash"]["leverage"] <= 0.5

    def test_bear_reduced_leverage(self):
        assert REGIME_WEIGHTS["bear"]["leverage"] < REGIME_WEIGHTS["bull"]["leverage"]

    def test_bull_exp1220_dominant(self):
        assert REGIME_WEIGHTS["bull"]["weights"][StrategyId.EXP1220] >= 0.60

    def test_crash_diversified(self):
        w = REGIME_WEIGHTS["crash"]["weights"]
        assert w[StrategyId.EXP1220] <= 0.30
        assert w[StrategyId.PAIRS] >= 0.20

    def test_high_vol_volterm_boosted(self):
        assert REGIME_WEIGHTS["high_vol"]["weights"][StrategyId.VOL_TERM] >= 0.15


# ═══════════════════════════════════════════════════════════════════════════
# MarketState (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestMarketState:
    def test_create(self):
        ms = MarketState(timestamp="2024-01-02", vix=14, vix3m=16,
                         spy_price=450, spy_return_20d=0.03, realized_vol_20d=0.10,
                         regime="bull")
        assert ms.vix == 14

    def test_vix_ratio_computed(self):
        ms = MarketState(timestamp="t", vix=20, vix3m=18, spy_price=450,
                         spy_return_20d=0.01, realized_vol_20d=0.12, regime="bull")
        assert abs(ms.vix_ratio - 20/18) < 0.01

    def test_vix_ratio_zero_vix3m(self):
        ms = MarketState(timestamp="t", vix=20, vix3m=0, spy_price=450,
                         spy_return_20d=0.01, realized_vol_20d=0.12, regime="bull")
        assert ms.vix_ratio == 20.0

    def test_regime_stored(self):
        ms = MarketState(timestamp="t", vix=50, vix3m=35, spy_price=400,
                         spy_return_20d=-0.05, realized_vol_20d=0.40, regime="crash")
        assert ms.regime == "crash"

    def test_all_fields(self):
        ms = MarketState("t", 14, 16, 450, 0.03, 0.10, "bull")
        assert hasattr(ms, "vix") and hasattr(ms, "spy_price")


# ═══════════════════════════════════════════════════════════════════════════
# Allocation Engine (12 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestAllocationEngine:
    @pytest.fixture
    def engine(self):
        return AllocationEngine()

    def _ms(self, regime="bull", vix=14, vix3m=16, trend=0.03, rvol=0.10):
        return MarketState("t", vix, vix3m, 450, trend, rvol, regime)

    def _ps(self, dd=0.01, equity=100_000):
        return PortfolioState(100_000, equity, max(equity, 100_000),
                              dd, 0, 0, equity-100_000)

    def test_bull_allocation(self, engine):
        d = engine.decide(self._ms("bull"), self._ps())
        assert d.regime == "bull"
        assert d.leverage >= 1.5

    def test_crash_allocation(self, engine):
        d = engine.decide(self._ms("crash", vix=50, vix3m=35, trend=-0.05), self._ps())
        assert d.leverage <= 0.5

    def test_bear_allocation(self, engine):
        d = engine.decide(self._ms("bear", vix=25, trend=-0.03), self._ps())
        assert d.leverage <= 1.0

    def test_circuit_breaker_triggers(self, engine):
        d = engine.decide(self._ms(), self._ps(dd=0.09))
        assert d.regime == "circuit_breaker"
        assert d.leverage == 0.5

    def test_circuit_breaker_holds(self, engine):
        engine.decide(self._ms(), self._ps(dd=0.09))  # trigger
        d = engine.decide(self._ms(), self._ps(dd=0.05))  # still above recovery
        assert d.regime == "circuit_breaker"

    def test_circuit_breaker_recovers(self, engine):
        engine.decide(self._ms(), self._ps(dd=0.09))  # trigger
        d = engine.decide(self._ms(), self._ps(dd=0.02))  # below recovery
        assert d.regime != "circuit_breaker"

    def test_high_vix_caps_leverage(self, engine):
        d = engine.decide(self._ms("bull", vix=35), self._ps())
        assert d.leverage <= 0.5

    def test_low_vix_bull_boosts(self, engine):
        d = engine.decide(self._ms("low_vol", vix=12, trend=0.04), self._ps())
        assert d.leverage >= 2.0

    def test_inversion_reduces(self, engine):
        d_normal = engine.decide(self._ms("bull", vix=18, vix3m=20), self._ps())
        engine.reset()
        d_inv = engine.decide(self._ms("bull", vix=22, vix3m=18), self._ps())
        assert d_inv.leverage < d_normal.leverage

    def test_leverage_bounded(self, engine):
        d = engine.decide(self._ms("low_vol", vix=10, trend=0.10), self._ps())
        assert 0.5 <= d.leverage <= 2.5

    def test_hedge_active_in_crisis(self, engine):
        d = engine.decide(self._ms("crash", vix=50, vix3m=35, trend=-0.05), self._ps())
        assert d.hedge_active

    def test_hedge_budget_positive(self, engine):
        d = engine.decide(self._ms("bear", vix=28, trend=-0.02), self._ps(dd=0.04))
        assert d.hedge_budget > 0


# ═══════════════════════════════════════════════════════════════════════════
# Paper Trading Harness (10 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestHarness:
    @pytest.fixture
    def harness(self):
        return PaperTradingHarness(capital=100_000)

    def _ms(self, regime="bull", vix=14):
        return MarketState("t", vix, 16, 450, 0.03, 0.10, regime)

    def test_initial_state(self, harness):
        assert harness._equity == 100_000
        assert harness.drawdown == 0

    def test_run_cycle(self, harness):
        d = harness.run_cycle(self._ms())
        assert isinstance(d, AllocationDecision)

    def test_apply_return(self, harness):
        harness.apply_return(0.01)
        assert harness._equity > 100_000

    def test_apply_negative_return(self, harness):
        harness.apply_return(-0.05)
        assert harness._equity < 100_000
        assert harness.drawdown > 0

    def test_drawdown_tracking(self, harness):
        harness.apply_return(0.10)  # peak at 110K
        harness.apply_return(-0.05) # drop to 104.5K
        assert harness.drawdown > 0.04

    def test_portfolio_state(self, harness):
        ps = harness.portfolio_state
        assert ps.capital == 100_000
        assert ps.equity == 100_000

    def test_health_checks(self, harness):
        checks = harness.run_health_checks(self._ms())
        assert len(checks) >= 3
        assert all(isinstance(c, HealthCheck) for c in checks)

    def test_health_critical_high_vix(self, harness):
        checks = harness.run_health_checks(self._ms(vix=50))
        vix_check = next(c for c in checks if c.name == "VIX")
        assert vix_check.status == "critical"

    def test_reset(self, harness):
        harness.apply_return(0.05)
        harness.run_cycle(self._ms())
        harness.reset()
        assert harness._equity == 100_000
        assert harness._cycle_count == 0

    def test_multiple_cycles(self, harness):
        for _ in range(10):
            harness.run_cycle(self._ms())
            harness.apply_return(0.002)
        assert harness._cycle_count == 10
        assert harness._equity > 100_000


# ═══════════════════════════════════════════════════════════════════════════
# Readiness Checklist (6 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestReadinessChecklist:
    def test_has_items(self):
        items = build_readiness_checklist()
        assert len(items) >= 20

    def test_all_have_required_fields(self):
        for item in build_readiness_checklist():
            assert item.category
            assert item.item
            assert item.status in ("ready", "partial", "not_ready")
            assert item.priority in ("P0", "P1", "P2")

    def test_covers_all_categories(self):
        cats = {i.category for i in build_readiness_checklist()}
        for c in ["Data", "API", "Infra", "Failover", "Monitoring", "Risk", "Capital", "Strategy"]:
            assert c in cats

    def test_has_p0_items(self):
        p0 = [i for i in build_readiness_checklist() if i.priority == "P0"]
        assert len(p0) >= 10

    def test_strategy_items_for_all_five(self):
        strat_items = [i for i in build_readiness_checklist() if i.category == "Strategy"]
        assert len(strat_items) == 5

    def test_mostly_ready(self):
        items = build_readiness_checklist()
        ready = sum(1 for i in items if i.status == "ready")
        assert ready / len(items) > 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Trade Frequency (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestTradeFrequency:
    def test_all_strategies_present(self):
        freq = expected_trade_frequency()
        for sid in StrategyId:
            assert STRATEGY_META[sid]["name"] in freq

    def test_total_present(self):
        freq = expected_trade_frequency()
        assert "TOTAL" in freq

    def test_reasonable_total(self):
        freq = expected_trade_frequency()
        monthly = freq["TOTAL"]["trades_per_month"]
        assert 10 < monthly < 30  # reasonable range

    def test_yearly_computed(self):
        freq = expected_trade_frequency()
        total = freq["TOTAL"]
        assert total["trades_per_year"] == round(total["trades_per_month"] * 12, 0)


# ═══════════════════════════════════════════════════════════════════════════
# Report Generation (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestReport:
    def test_generates_html(self, tmp_path):
        out = tmp_path / "readiness.html"
        generate_readiness_report(str(out))
        assert out.exists()
        c = out.read_text()
        assert "<!DOCTYPE html>" in c
        assert "Production Readiness" in c

    def test_contains_checklist(self, tmp_path):
        out = tmp_path / "r.html"
        generate_readiness_report(str(out))
        assert "Readiness" in out.read_text()

    def test_contains_frequency(self, tmp_path):
        out = tmp_path / "r.html"
        generate_readiness_report(str(out))
        assert "Trades/Mo" in out.read_text()

    def test_contains_failover(self, tmp_path):
        out = tmp_path / "r.html"
        generate_readiness_report(str(out))
        assert "Failover" in out.read_text()


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_zero_equity(self):
        h = PaperTradingHarness(capital=100)
        h._equity = 1.0
        h._peak = 100.0
        assert h.drawdown > 0.9

    def test_negative_return_floor(self):
        h = PaperTradingHarness()
        h.apply_return(-0.99)
        assert h._equity >= 1.0

    def test_signal_dataclass(self):
        s = Signal(StrategyId.EXP1220, "SPY", "bull_put", 3, 0.65, 4.35, 0.80, 21)
        assert s.contracts == 3
        assert s.confidence == 0.80

    def test_allocation_with_unknown_regime(self):
        e = AllocationEngine()
        ms = MarketState("t", 18, 20, 450, 0.01, 0.12, "unknown_regime")
        ps = PortfolioState(100_000, 100_000, 100_000, 0.01, 0, 0, 0)
        d = e.decide(ms, ps)
        assert d.leverage > 0  # should fallback to bull

    def test_readiness_item_dataclass(self):
        r = ReadinessItem("Test", "Item", "ready", "Detail", "P0")
        assert r.status == "ready"
