"""Tests for shared/feature_logger.py — trade feature logging and extraction."""

import pytest
from shared.database import init_db
from shared.feature_logger import FeatureLogger, _extract_features_from_opportunity


class TestFeatureLoggerInit:
    def test_creates_table(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)
        assert fl.db_path == db


class TestLogEntry:
    def test_log_and_retrieve(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)

        fl.log_entry("trade-001", {
            "ticker": "SPY", "strategy_type": "bull_put",
            "vix": 18.5, "iv_rank": 35.0, "rsi": 52.0,
            "dte": 30, "spread_width": 5.0, "credit_received": 1.50,
        })

        stats = fl.get_stats()
        assert stats["total_features"] == 1

    def test_multiple_entries(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)

        for i in range(5):
            fl.log_entry(f"trade-{i:03d}", {"ticker": "SPY", "vix": 15.0 + i})

        stats = fl.get_stats()
        assert stats["total_features"] == 5

    def test_upsert_overwrites(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)

        fl.log_entry("trade-dup", {"ticker": "SPY", "vix": 18.0})
        fl.log_entry("trade-dup", {"ticker": "SPY", "vix": 22.0})  # overwrite

        stats = fl.get_stats()
        assert stats["total_features"] == 1

    def test_empty_features_no_crash(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)
        fl.log_entry("trade-empty", {})
        assert fl.get_stats()["total_features"] == 1


class TestLogOutcome:
    def test_outcome_updates_record(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)

        fl.log_entry("trade-out", {"ticker": "SPY", "vix": 20.0})
        fl.log_outcome("trade-out", outcome="win", pnl_pct=45.0, hold_days=18.0)

        stats = fl.get_stats()
        assert stats["class_balance"].get("win") == 1

    def test_outcome_nonexistent_trade_no_crash(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)
        fl.log_outcome("nonexistent", "loss", -30.0, 5.0)  # should not raise


class TestGetStats:
    def test_empty_db(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)
        stats = fl.get_stats()
        assert stats["total_features"] == 0
        assert stats["first_timestamp"] is None

    def test_class_balance(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        fl = FeatureLogger(db_path=db)

        fl.log_entry("t1", {"ticker": "SPY"})
        fl.log_entry("t2", {"ticker": "SPY"})
        fl.log_entry("t3", {"ticker": "SPY"})
        fl.log_outcome("t1", "win", 50.0, 10.0)
        fl.log_outcome("t2", "loss", -80.0, 3.0)
        # t3 has no outcome → "pending"

        stats = fl.get_stats()
        assert stats["class_balance"]["win"] == 1
        assert stats["class_balance"]["loss"] == 1
        assert stats["class_balance"]["pending"] == 1


class TestExtractFeatures:
    def test_basic_extraction(self):
        opp = {
            "ticker": "SPY", "type": "bull_put",
            "short_strike": 540.0, "long_strike": 535.0,
            "credit": 1.50, "spread_width": 5.0, "dte": 30, "score": 82,
        }
        ctx = {"vix": 18.5, "iv_rank": 35, "rsi": 55, "current_price": 560.0}
        features = _extract_features_from_opportunity(opp, ctx)

        assert features["ticker"] == "SPY"
        assert features["direction"] == "bullish"  # "put" in type
        assert features["vix"] == 18.5
        assert features["credit_received"] == 1.50
        assert features["spread_width"] == 5.0
        assert features["score"] == 82

    def test_otm_pct_computed(self):
        opp = {"short_strike": 540.0, "credit": 1.0}
        ctx = {"current_price": 560.0}
        features = _extract_features_from_opportunity(opp, ctx)
        # OTM = |560 - 540| / 560 * 100 = 3.57%
        assert features["otm_pct"] is not None
        assert abs(features["otm_pct"] - 3.5714) < 0.01

    def test_max_loss_fallback(self):
        opp = {"spread_width": 5.0, "credit": 1.50, "type": "bull_put"}
        features = _extract_features_from_opportunity(opp)
        # max_loss = (5.0 - 1.50) * 100 = 350
        assert features["max_loss"] == 350.0

    def test_bearish_direction(self):
        opp = {"type": "bear_call", "credit": 1.0}
        features = _extract_features_from_opportunity(opp)
        assert features["direction"] == "bearish"

    def test_neutral_direction(self):
        opp = {"type": "iron_condor", "credit": 2.0}
        features = _extract_features_from_opportunity(opp)
        assert features["direction"] == "neutral"

    def test_ml_features_from_opp(self):
        opp = {
            "ticker": "SPY", "type": "bull_put", "credit": 1.0,
            "_ml_features": {"vix": 25.0, "regime": "high_vol"},
        }
        features = _extract_features_from_opportunity(opp)
        assert features["vix"] == 25.0
        assert features["regime"] == "high_vol"
