"""Tests for shared/deviation_tracker.py — per-trade deviation and rolling alignment."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from shared.database import init_db
from shared.deviation_tracker import (
    _compute_overall_status,
    check_deviation_alerts,
    get_deviation_history,
    get_latest_deviation,
    get_rolling_alignment,
    record_deviation,
)


def _setup_db(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    return db


def _make_trade(**overrides):
    base = {
        "id": "test-trade-001",
        "ticker": "SPY",
        "strategy_type": "bull_put",
        "credit": 1.50,
        "contracts": 2,
        "short_strike": 540.0,
        "long_strike": 535.0,
        "entry_date": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


# ── record_deviation ─────────────────────────────────────────────────────


class TestRecordDeviation:
    def test_winning_trade(self, tmp_path):
        db = _setup_db(tmp_path)
        trade = _make_trade()
        rec = record_deviation(trade, pnl=150.0, fill_price=0.75, db_path=db)

        assert rec is not None
        assert rec["paper_outcome"] == "win"
        assert rec["paper_credit"] == 1.50
        assert rec["expected_credit"] == 5.0 * 0.35  # spread_width * default ratio
        assert 0 <= rec["deviation_score"] <= 2.0

    def test_losing_trade(self, tmp_path):
        db = _setup_db(tmp_path)
        trade = _make_trade()
        rec = record_deviation(trade, pnl=-500.0, fill_price=4.00, db_path=db)

        assert rec["paper_outcome"] == "loss"
        assert rec["expected_outcome"] == "win"
        assert rec["deviation_score"] > 0  # outcome mismatch contributes

    def test_scratch_trade(self, tmp_path):
        db = _setup_db(tmp_path)
        trade = _make_trade()
        rec = record_deviation(trade, pnl=0.0, fill_price=1.50, db_path=db)
        assert rec["paper_outcome"] == "scratch"

    def test_no_trade_id_returns_none(self, tmp_path):
        db = _setup_db(tmp_path)
        trade = _make_trade(id="")
        assert record_deviation(trade, pnl=100, fill_price=0.5, db_path=db) is None

    def test_no_spread_width_returns_none(self, tmp_path):
        db = _setup_db(tmp_path)
        trade = _make_trade(short_strike=0, long_strike=0)
        assert record_deviation(trade, pnl=100, fill_price=0.5, db_path=db) is None

    def test_custom_config(self, tmp_path):
        db = _setup_db(tmp_path)
        trade = _make_trade()
        cfg = {"backtest": {"credit_ratio": 0.40, "expected_hold_days": 14},
               "risk": {"profit_target": 60}}
        rec = record_deviation(trade, pnl=100, fill_price=0.75, db_path=db, config=cfg)
        assert rec["expected_credit"] == 5.0 * 0.40
        assert rec["expected_hold_days"] == 14.0

    def test_persists_to_db(self, tmp_path):
        db = _setup_db(tmp_path)
        trade = _make_trade()
        record_deviation(trade, pnl=100, fill_price=0.75, db_path=db)

        alignment = get_rolling_alignment(db_path=db)
        assert alignment["trade_count"] == 1


# ── get_rolling_alignment ────────────────────────────────────────────────


class TestGetRollingAlignment:
    def test_empty_db(self, tmp_path):
        db = _setup_db(tmp_path)
        result = get_rolling_alignment(db_path=db)
        assert result["alignment_score"] == 1.0
        assert result["trade_count"] == 0

    def test_all_matching(self, tmp_path):
        db = _setup_db(tmp_path)
        for i in range(5):
            trade = _make_trade(id=f"t-{i}")
            record_deviation(trade, pnl=100, fill_price=0.75, db_path=db)

        result = get_rolling_alignment(db_path=db, window=10)
        assert result["alignment_score"] == 1.0  # all wins match expected win
        assert result["trade_count"] == 5

    def test_mixed_outcomes(self, tmp_path):
        db = _setup_db(tmp_path)
        # 3 wins + 2 losses → alignment = 3/5 = 0.6
        for i in range(3):
            record_deviation(_make_trade(id=f"w-{i}"), pnl=100, fill_price=0.75, db_path=db)
        for i in range(2):
            record_deviation(_make_trade(id=f"l-{i}"), pnl=-500, fill_price=4.0, db_path=db)

        result = get_rolling_alignment(db_path=db, window=10)
        assert result["alignment_score"] == 0.6
        assert result["trade_count"] == 5

    def test_credit_deviation_computed(self, tmp_path):
        db = _setup_db(tmp_path)
        # Expected credit = 5.0 * 0.35 = 1.75; paper_credit = 1.50
        # deviation = |1.50 - 1.75| / 1.75 ≈ 0.143
        record_deviation(_make_trade(), pnl=100, fill_price=0.75, db_path=db)

        result = get_rolling_alignment(db_path=db)
        assert result["credit_deviation"] > 0


# ── _compute_overall_status ──────────────────────────────────────────────


class TestComputeOverallStatus:
    def test_fail_dominates(self):
        assert _compute_overall_status([
            {"status": "PASS"}, {"status": "FAIL"}, {"status": "WARN"}
        ]) == "FAIL"

    def test_warn_without_fail(self):
        assert _compute_overall_status([
            {"status": "PASS"}, {"status": "WARN"}
        ]) == "WARN"

    def test_all_pass(self):
        assert _compute_overall_status([{"status": "PASS"}]) == "PASS"

    def test_info_only(self):
        assert _compute_overall_status([{"status": "INFO"}]) == "INFO"

    def test_empty(self):
        assert _compute_overall_status([]) == "INFO"


# ── check_deviation_alerts ───────────────────────────────────────────────


class TestCheckDeviationAlerts:
    def test_no_alerts_on_pass(self):
        snapshot = {"details": {"comparisons": [{"status": "PASS", "metric": "win_rate"}]}}
        assert check_deviation_alerts(snapshot) == []

    def test_warn_generates_alert(self):
        snapshot = {"details": {"comparisons": [
            {"status": "WARN", "metric": "win_rate", "live_str": "75%", "backtest_str": "88%"}
        ]}}
        alerts = check_deviation_alerts(snapshot)
        assert len(alerts) == 1
        assert "win_rate" in alerts[0]

    def test_fail_generates_alert(self):
        snapshot = {"details": {"comparisons": [
            {"status": "FAIL", "metric": "max_drawdown", "live_str": "18%", "backtest_str": "8%"}
        ]}}
        alerts = check_deviation_alerts(snapshot)
        assert len(alerts) == 1
        assert "FAIL" in alerts[0]

    def test_empty_snapshot(self):
        assert check_deviation_alerts({}) == []
        assert check_deviation_alerts(None) == []

    def test_no_comparisons(self):
        assert check_deviation_alerts({"details": {}}) == []


# ── get_deviation_history / get_latest_deviation ─────────────────────────


class TestDeviationHistory:
    def test_empty_history(self, tmp_path):
        db = _setup_db(tmp_path)
        history = get_deviation_history(days=30, db_path=db)
        assert history == []

    def test_latest_returns_none_when_empty(self, tmp_path):
        db = _setup_db(tmp_path)
        assert get_latest_deviation(db_path=db) is None
