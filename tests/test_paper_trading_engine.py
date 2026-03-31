"""Tests for compass/paper_trading_engine.py — paper trading engine."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.paper_trading_engine import (
    ClosedTrade, DailyPnL, EngineConfig, Fill, FillSimulator,
    PaperTradingEngine, PerformanceSummary, Position, RiskBreachEvent,
    RiskMonitor, Signal,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _sig(**kw):
    defaults = dict(strategy="EXP-400", ticker="SPY", contracts=2,
                    net_credit=1.5, max_loss=3.5, spread_width=5.0,
                    dte=30, confidence=0.7, regime="bull",
                    timestamp="2024-06-03T10:00:00+00:00")
    defaults.update(kw)
    return Signal(**defaults)

def _engine(**kw):
    return PaperTradingEngine(EngineConfig(**kw))

def _make_trades_df(n=50, seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({
        "entry_date": dates,
        "exit_date": dates + pd.Timedelta(days=7),
        "strategy_type": rng.choice(["CS", "SS"], n),
        "net_credit": rng.uniform(0.5, 3.0, n).round(4),
        "max_loss_per_unit": rng.uniform(2.0, 5.0, n).round(4),
        "spread_width": 5.0,
        "contracts": rng.randint(1, 5, n),
        "dte_at_entry": rng.randint(7, 45, n),
        "pnl": rng.normal(50, 200, n),
        "win": (rng.random(n) > 0.4).astype(int),
        "regime": rng.choice(["bull", "bear", "neutral"], n),
        "exit_reason": rng.choice(["profit_target", "stop_loss", "expiration"], n),
    })

# ── Dataclass tests ──────────────────────────────────────────────────────

class TestDataclasses:
    def test_signal_auto_id(self):
        s = Signal(strategy="A")
        assert s.signal_id.startswith("SIG-")
    def test_signal_auto_timestamp(self):
        s = Signal()
        assert len(s.timestamp) > 0
    def test_engine_config_defaults(self):
        c = EngineConfig()
        assert c.starting_capital == 100_000
        assert c.slippage_per_contract == 0.04
    def test_fill_fields(self):
        f = Fill("F1", "S1", "strat", "SPY", "open", 2, 1.5, 0.08, 2.6, "2024-01-01")
        assert f.contracts == 2
    def test_position_fields(self):
        p = Position("P1", "strat", "SPY", "short", "bull_put", 2, 1.4,
                     3.5, 5.0, 1000, "2024-01-01", "2024-02-01", 4.9, 0.7)
        assert p.margin_required == 1000
    def test_closed_trade_fields(self):
        t = ClosedTrade("P1", "s", "SPY", "short", "bp", 2, 1.4, 0.5,
                        180, 0.12, 0.08, 2.6, "2024-01-01", "2024-01-15",
                        "profit_target", 14, "bull", 0.7, True)
        assert t.win is True
    def test_daily_pnl_fields(self):
        d = DailyPnL("2024-01-01", 500, 300, 200, 3, 1500, 100500, -0.01, {"A": 500})
        assert d.n_positions == 3
    def test_risk_breach_fields(self):
        b = RiskBreachEvent("2024-01-01", "max_dd", 0.12, 0.15, "circuit_breaker")
        assert b.breach_type == "max_dd"
    def test_performance_summary_fields(self):
        p = PerformanceSummary(1000, 0.01, 0.6, 50, 30, 2.0, 2.5, -0.05,
                               3.0, 1.5, 20, 50, -30, 10, 100, 200, {}, {})
        assert p.sharpe == pytest.approx(2.0)

# ── FillSimulator tests ──────────────────────────────────────────────────

class TestFillSimulator:
    def test_fill_returns_fill(self):
        fs = FillSimulator(EngineConfig(fill_rate=1.0))
        f = fs.simulate_fill(_sig(), "open")
        assert f is not None
        assert f.contracts == 2
    def test_fill_has_slippage(self):
        fs = FillSimulator(EngineConfig(fill_rate=1.0, slippage_per_contract=0.05))
        f = fs.simulate_fill(_sig(net_credit=2.0), "open")
        assert f.slippage > 0
    def test_open_fill_reduces_credit(self):
        fs = FillSimulator(EngineConfig(fill_rate=1.0, slippage_per_contract=0.05))
        f = fs.simulate_fill(_sig(net_credit=2.0), "open")
        assert f.price < 2.0  # slippage reduces received credit
    def test_close_fill_adds_cost(self):
        fs = FillSimulator(EngineConfig(fill_rate=1.0, slippage_per_contract=0.05))
        f = fs.simulate_fill(_sig(net_credit=2.0), "close")
        assert f.price > 0  # costs money to close
    def test_zero_fill_rate(self):
        fs = FillSimulator(EngineConfig(fill_rate=0.0))
        f = fs.simulate_fill(_sig(), "open")
        assert f is None
    def test_commission_calculated(self):
        fs = FillSimulator(EngineConfig(fill_rate=1.0))
        f = fs.simulate_fill(_sig(contracts=3), "open")
        assert f.commission == pytest.approx(0.65 * 3 * 2)

# ── RiskMonitor tests ────────────────────────────────────────────────────

class TestRiskMonitor:
    def test_allows_normal_position(self):
        rm = RiskMonitor(EngineConfig())
        ok, reason = rm.check_new_position(_sig(), [], 100_000, 100_000)
        assert ok is True
    def test_rejects_at_max_positions(self):
        rm = RiskMonitor(EngineConfig(max_positions=2))
        positions = [Position(f"P{i}", "s", "SPY", "short", "bp", 1, 1, 3, 5,
                              500, "2024-01-01", "2024-02-01", 3.5, 0.5) for i in range(2)]
        ok, reason = rm.check_new_position(_sig(), positions, 100_000, 100_000)
        assert not ok
        assert "max positions" in reason.lower()
    def test_rejects_per_strategy_limit(self):
        rm = RiskMonitor(EngineConfig(max_position_per_strategy=1))
        positions = [Position("P1", "EXP-400", "SPY", "short", "bp", 1, 1, 3, 5,
                              500, "2024-01-01", "2024-02-01", 3.5, 0.5)]
        ok, _ = rm.check_new_position(_sig(strategy="EXP-400"), positions, 100_000, 100_000)
        assert not ok
    def test_drawdown_circuit_breaker(self):
        rm = RiskMonitor(EngineConfig(max_drawdown_pct=0.10))
        ok, _ = rm.check_new_position(_sig(), [], 85_000, 100_000)
        assert not ok
        assert rm.circuit_breaker_active
    def test_circuit_breaker_blocks(self):
        rm = RiskMonitor(EngineConfig())
        rm.circuit_breaker_active = True
        ok, _ = rm.check_new_position(_sig(), [], 100_000, 100_000)
        assert not ok
    def test_reset_circuit_breaker(self):
        rm = RiskMonitor(EngineConfig())
        rm.circuit_breaker_active = True
        rm.reset_circuit_breaker()
        assert not rm.circuit_breaker_active
    def test_rejects_low_confidence(self):
        rm = RiskMonitor(EngineConfig())
        ok, _ = rm.check_new_position(_sig(confidence=0.1), [], 100_000, 100_000)
        assert not ok
    def test_margin_limit(self):
        rm = RiskMonitor(EngineConfig(margin_per_spread=60_000))
        ok, _ = rm.check_new_position(_sig(contracts=2), [], 100_000, 100_000)
        assert not ok
    def test_daily_loss_check(self):
        rm = RiskMonitor(EngineConfig(max_daily_loss=1000))
        assert rm.check_daily_loss(-1500) is True
    def test_daily_loss_ok(self):
        rm = RiskMonitor(EngineConfig(max_daily_loss=5000))
        assert rm.check_daily_loss(-500) is False
    def test_breach_recorded(self):
        rm = RiskMonitor(EngineConfig(max_drawdown_pct=0.05))
        rm.check_new_position(_sig(), [], 90_000, 100_000)
        assert len(rm.breaches) >= 1

# ── Engine: signal submission ────────────────────────────────────────────

class TestSubmitSignal:
    def test_submit_creates_position(self):
        e = _engine(fill_rate=1.0); accepted, pid = e.submit_signal(_sig())
        assert accepted; assert len(e.positions) == 1; e.close()
    def test_submit_returns_position_id(self):
        e = _engine(fill_rate=1.0); ok, pid = e.submit_signal(_sig())
        assert pid.startswith("POS-"); e.close()
    def test_submit_deducts_commission(self):
        e = _engine(fill_rate=1.0); e.submit_signal(_sig(contracts=2))
        assert e.capital < 100_000; e.close()
    def test_rejected_signal(self):
        e = _engine(fill_rate=1.0, max_positions=0)
        ok, reason = e.submit_signal(_sig())
        assert not ok; e.close()
    def test_unfilled_signal(self):
        e = _engine(fill_rate=0.0)
        ok, reason = e.submit_signal(_sig())
        assert not ok; assert "fill" in reason.lower(); e.close()
    def test_multiple_signals(self):
        e = _engine(fill_rate=1.0)
        e.submit_signal(_sig(strategy="A")); e.submit_signal(_sig(strategy="B"))
        assert len(e.positions) == 2; e.close()

# ── Engine: step / daily ─────────────────────────────────────────────────

class TestStep:
    def test_step_returns_daily_pnl(self):
        e = _engine(fill_rate=1.0); e.submit_signal(_sig())
        snap = e.step("2024-06-10")
        assert isinstance(snap, DailyPnL); e.close()
    def test_step_closes_expired(self):
        e = _engine(fill_rate=1.0)
        e.submit_signal(_sig(dte=5, timestamp="2024-06-03T10:00:00+00:00"))
        e.step("2024-06-10")  # 7 days later > 5 DTE
        assert len(e.positions) == 0; assert len(e.closed_trades) == 1; e.close()
    def test_step_closes_profit_target(self):
        e = _engine(fill_rate=1.0)
        e.submit_signal(_sig(dte=30, profit_target_pct=0.50,
                             timestamp="2024-06-01T10:00:00+00:00"))
        # Step far enough for theta decay to cross profit target
        e.step("2024-06-25")
        assert len(e.closed_trades) >= 1; e.close()
    def test_daily_pnl_recorded(self):
        e = _engine(fill_rate=1.0); e.submit_signal(_sig())
        e.step("2024-06-05"); e.step("2024-06-06")
        assert len(e.daily_pnl) == 2; e.close()
    def test_step_updates_unrealised(self):
        e = _engine(fill_rate=1.0); e.submit_signal(_sig())
        snap = e.step("2024-06-05")
        assert snap.unrealised_pnl != 0 or snap.n_positions > 0; e.close()

# ── Engine: close position ───────────────────────────────────────────────

class TestClosePosition:
    def test_close_records_trade(self):
        e = _engine(fill_rate=1.0); e.submit_signal(_sig(dte=2, timestamp="2024-06-01T10:00:00+00:00"))
        e.step("2024-06-05")
        assert len(e.closed_trades) == 1
        t = e.closed_trades[0]
        assert t.exit_reason == "expiration"; e.close()
    def test_close_updates_capital(self):
        e = _engine(fill_rate=1.0); cap_before = e.capital
        e.submit_signal(_sig(dte=2, timestamp="2024-06-01T10:00:00+00:00"))
        e.step("2024-06-05")
        assert e.capital != cap_before; e.close()
    def test_close_removes_from_positions(self):
        e = _engine(fill_rate=1.0)
        e.submit_signal(_sig(dte=2, timestamp="2024-06-01T10:00:00+00:00"))
        e.step("2024-06-05")
        assert len(e.positions) == 0; e.close()

# ── Engine: replay ───────────────────────────────────────────────────────

class TestReplay:
    def test_replay_returns_summary(self):
        e = _engine(fill_rate=1.0)
        df = _make_trades_df(20)
        perf = e.replay(df)
        assert isinstance(perf, PerformanceSummary)
        assert perf.n_trades > 0; e.close()
    def test_replay_populates_closed(self):
        e = _engine(fill_rate=1.0)
        perf = e.replay(_make_trades_df(30))
        assert len(e.closed_trades) > 0; e.close()
    def test_replay_slippage_nonzero(self):
        e = _engine(fill_rate=1.0, slippage_per_contract=0.05)
        perf = e.replay(_make_trades_df(20))
        assert perf.total_slippage > 0; e.close()

# ── Performance summary ──────────────────────────────────────────────────

class TestPerformance:
    def test_empty_engine(self):
        e = _engine()
        perf = e.get_performance()
        assert perf.n_trades == 0; e.close()
    def test_win_rate_range(self):
        e = _engine(fill_rate=1.0)
        e.replay(_make_trades_df(30))
        perf = e.get_performance()
        assert 0 <= perf.win_rate <= 1; e.close()
    def test_by_strategy_populated(self):
        e = _engine(fill_rate=1.0)
        e.replay(_make_trades_df(30))
        perf = e.get_performance()
        assert len(perf.by_strategy) > 0; e.close()
    def test_by_regime_populated(self):
        e = _engine(fill_rate=1.0)
        e.replay(_make_trades_df(30))
        perf = e.get_performance()
        assert len(perf.by_regime) > 0; e.close()
    def test_strategy_pnl_sums_to_total(self):
        e = _engine(fill_rate=1.0)
        e.replay(_make_trades_df(30))
        perf = e.get_performance()
        strat_total = sum(d["pnl"] for d in perf.by_strategy.values())
        assert strat_total == pytest.approx(perf.total_pnl, abs=1); e.close()

# ── JSON export ──────────────────────────────────────────────────────────

class TestExportJSON:
    def test_export_returns_dict(self):
        e = _engine(fill_rate=1.0); e.replay(_make_trades_df(10))
        data = e.export_json()
        assert "performance" in data; e.close()
    def test_export_writes_file(self, tmp_path):
        e = _engine(fill_rate=1.0); e.replay(_make_trades_df(10))
        path = str(tmp_path / "out.json")
        e.export_json(path)
        import json
        with open(path) as f:
            data = json.load(f)
        assert data["performance"]["n_trades"] > 0; e.close()

# ── HTML report ──────────────────────────────────────────────────────────

class TestReport:
    def test_generates_html(self, tmp_path):
        e = _engine(fill_rate=1.0); e.replay(_make_trades_df(20))
        path = e.generate_report(str(tmp_path / "r.html"))
        c = open(path).read()
        assert "<!DOCTYPE html>" in c and "Paper Trading" in c; e.close()
    def test_report_sections(self, tmp_path):
        e = _engine(fill_rate=1.0); e.replay(_make_trades_df(20))
        path = e.generate_report(str(tmp_path / "r.html"))
        c = open(path).read()
        assert "Equity" in c and "Strategy" in c and "Regime" in c; e.close()
    def test_report_charts(self, tmp_path):
        e = _engine(fill_rate=1.0); e.replay(_make_trades_df(20))
        path = e.generate_report(str(tmp_path / "r.html"))
        assert "data:image/png;base64," in open(path).read(); e.close()
    def test_report_default_path(self):
        e = _engine(fill_rate=1.0); e.replay(_make_trades_df(10))
        path = e.generate_report()
        assert "paper_engine.html" in path; e.close()

# ── SQLite persistence ───────────────────────────────────────────────────

class TestPersistence:
    def test_position_persisted(self):
        e = _engine(fill_rate=1.0); e.submit_signal(_sig())
        row = e._conn.execute("SELECT * FROM positions").fetchone()
        assert row is not None; e.close()
    def test_trade_persisted(self):
        e = _engine(fill_rate=1.0)
        e.submit_signal(_sig(dte=2, timestamp="2024-06-01T10:00:00+00:00"))
        e.step("2024-06-05")
        row = e._conn.execute("SELECT * FROM trades").fetchone()
        assert row is not None; e.close()
    def test_position_status_updated(self):
        e = _engine(fill_rate=1.0)
        e.submit_signal(_sig(dte=2, timestamp="2024-06-01T10:00:00+00:00"))
        e.step("2024-06-05")
        row = e._conn.execute("SELECT status FROM positions").fetchone()
        assert row[0] == "closed"; e.close()
