"""
Tests for EXP-2620 Alpaca paper-trading connector.

These tests mock every Alpaca REST call so the suite runs offline,
without the ALPACA_API_KEY_PAPER env var set, and without touching a
broker. The goal is to exercise:

  * config loading from configs/exp2410_production_paper.yaml
  * RiskDecision → order translation
  * 3% trailing-DD circuit breaker (soft / hard)
  * EXP-2470 patient-window gate
  * EXP-2470 limit-at-mid mid-price computation
  * Rule Zero refusal when no quote is available
  * Telegram-fallback path when env is unset
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

from compass.exp2620_alpaca_connector import (
    AlpacaPaperConnector,
    ConnectorConfig,
    ExecutionConfig,
    OrderRequest,
    compute_mid_limit_price,
    in_patient_window,
)
from compass.portfolio_risk_manager import RiskDecision, CircuitState
from compass.portfolio_risk_manager import Regime


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _stub_cfg(tmp_path: Path) -> ConnectorConfig:
    cfg = ConnectorConfig(config_path=tmp_path / "stub.yaml")
    cfg.api_key = "stub"
    cfg.api_secret = "stub"
    cfg.starting_capital = 100_000
    cfg.state_file  = tmp_path / "state.json"
    cfg.health_file = tmp_path / "health.json"
    cfg.execution = ExecutionConfig(
        use_limit_at_mid=True,
        use_patient_window=False,        # disable for unit tests
        use_route_reallocation=True,
        use_multileg_combo=True,
    )
    return cfg


def _decision(weights: Dict[str, float], leverage: float = 1.0) -> RiskDecision:
    return RiskDecision(
        weights=weights,
        leverage=leverage,
        state=CircuitState.NORMAL,
        regime=Regime.LOW_VOL,
        rebalance=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Config loading
# ─────────────────────────────────────────────────────────────────────────────
def test_config_from_yaml_loads_target_yaml():
    cfg = ConnectorConfig.from_yaml(Path("configs/exp2410_production_paper.yaml"))
    assert cfg.starting_capital == 100_000
    assert cfg.breaker.soft_pct == 0.03
    assert cfg.breaker.hard_pct == 0.06
    assert cfg.commission_free is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. Circuit breaker (3% trailing DD, EXP-2370)
# ─────────────────────────────────────────────────────────────────────────────
def test_breaker_normal(tmp_path):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    state, dd = conn.evaluate_breaker(100_000)
    assert state == CircuitState.NORMAL
    assert dd == 0


def test_breaker_soft_at_3pct(tmp_path):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    conn.evaluate_breaker(100_000)            # set peak
    state, dd = conn.evaluate_breaker(96_900)  # -3.1% from peak
    assert state == CircuitState.WARN
    assert dd == pytest.approx(0.031, abs=1e-3)


def test_breaker_hard_at_6pct(tmp_path):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    conn.evaluate_breaker(100_000)
    state, dd = conn.evaluate_breaker(93_900)  # -6.1%
    assert state == CircuitState.HALT
    assert dd == pytest.approx(0.061, abs=1e-3)


def test_breaker_rolling_peak_only_increases(tmp_path):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    conn.evaluate_breaker(100_000)
    conn.evaluate_breaker(110_000)            # new peak 110k
    state, dd = conn.evaluate_breaker(108_000)  # -1.8% from new peak — still NORMAL
    assert state == CircuitState.NORMAL
    assert conn.state["rolling_peak_equity"] == 110_000


# ─────────────────────────────────────────────────────────────────────────────
# 3. EXP-2470 patient window gate (technique B)
# ─────────────────────────────────────────────────────────────────────────────
def test_patient_window_disabled_returns_true(tmp_path):
    cfg = _stub_cfg(tmp_path)
    cfg.execution.use_patient_window = False
    assert in_patient_window(cfg) is True


def test_patient_window_inside_30min_returns_true(tmp_path):
    cfg = _stub_cfg(tmp_path)
    cfg.execution.use_patient_window = True
    cfg.execution.patient_window_minutes = 30
    fake_close = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    with mock.patch("compass.exp2620_alpaca_connector.get_clock",
                    return_value={"next_close": fake_close}):
        assert in_patient_window(cfg) is True


def test_patient_window_too_early_returns_false(tmp_path):
    cfg = _stub_cfg(tmp_path)
    cfg.execution.use_patient_window = True
    cfg.execution.patient_window_minutes = 30
    fake_close = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    with mock.patch("compass.exp2620_alpaca_connector.get_clock",
                    return_value={"next_close": fake_close}):
        assert in_patient_window(cfg) is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. EXP-2470 limit-at-mid (technique A)
# ─────────────────────────────────────────────────────────────────────────────
def test_mid_limit_price_uses_nbbo_mid(tmp_path):
    cfg = _stub_cfg(tmp_path)
    quote = {"quote": {"bp": 100.0, "ap": 100.20}}
    with mock.patch("compass.exp2620_alpaca_connector.get_quote",
                    return_value=quote):
        mid_buy  = compute_mid_limit_price(cfg, "SPY", "buy")
        mid_sell = compute_mid_limit_price(cfg, "SPY", "sell")
    assert mid_buy  == pytest.approx(100.10)
    assert mid_sell == pytest.approx(100.10)


def test_mid_limit_price_with_offset(tmp_path):
    cfg = _stub_cfg(tmp_path)
    cfg.execution.limit_offset_pct = 0.001    # 10 bps pay-up
    quote = {"quote": {"bp": 100.0, "ap": 100.20}}
    with mock.patch("compass.exp2620_alpaca_connector.get_quote",
                    return_value=quote):
        mid_buy  = compute_mid_limit_price(cfg, "SPY", "buy")
        mid_sell = compute_mid_limit_price(cfg, "SPY", "sell")
    assert mid_buy  > 100.10
    assert mid_sell < 100.10


def test_mid_limit_price_returns_none_when_no_quote(tmp_path):
    cfg = _stub_cfg(tmp_path)
    with mock.patch("compass.exp2620_alpaca_connector.get_quote",
                    return_value=None):
        assert compute_mid_limit_price(cfg, "SPY", "buy") is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. Rule Zero: connector refuses when no quote (no fabricated prices)
# ─────────────────────────────────────────────────────────────────────────────
def test_submit_order_refuses_when_no_quote(tmp_path):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    req = OrderRequest(symbol="SPY", qty=1, side="buy",
                       asset_class="equity", order_type="limit",
                       sleeve_id="exp1220_spy")
    with mock.patch("compass.exp2620_alpaca_connector.compute_mid_limit_price",
                    return_value=None):
        resp = conn.submit_order(req)
    assert resp["status"] == "rejected"
    assert resp["reason"] == "no_quote"


def test_submit_order_passes_through_legs_for_combo(tmp_path):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    legs = [
        {"symbol": "SPY240419P00500000", "qty": 1, "side": "sell"},
        {"symbol": "SPY240419P00495000", "qty": 1, "side": "buy"},
    ]
    req = OrderRequest(symbol="SPY", qty=1, side="sell",
                       asset_class="option", order_type="limit",
                       legs=legs, sleeve_id="exp1220_spy",
                       limit_price=2.0)   # provided to skip mid lookup
    fake_resp = {"id": "ord-123", "status": "accepted"}
    with mock.patch("compass.exp2620_alpaca_connector._alpaca_request",
                    return_value=fake_resp):
        resp = conn.submit_order(req)
    assert resp["status"] == "submitted"
    assert resp["id"] == "ord-123"


# ─────────────────────────────────────────────────────────────────────────────
# 6. apply_decision honours the breaker
# ─────────────────────────────────────────────────────────────────────────────
def test_apply_decision_halts_on_hard_breaker(tmp_path):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    conn.state["rolling_peak_equity"] = 100_000
    decision = _decision({"exp1220": 0.5}, leverage=2.0)

    builder_called = {"n": 0}
    def builder(_target):
        builder_called["n"] += 1
        return []

    fake_account = {"equity": "93000"}            # -7% → HARD
    with mock.patch("compass.exp2620_alpaca_connector.get_account",
                    return_value=fake_account), \
         mock.patch("compass.exp2620_alpaca_connector._alpaca_request",
                    return_value=[]):
        result = conn.apply_decision(decision, {"exp1220": builder})

    assert result["status"] == "halted"
    assert builder_called["n"] == 0
    assert conn.state["circuit_state"] == "halt"


def test_apply_decision_soft_cuts_leverage_in_half(tmp_path):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    conn.state["rolling_peak_equity"] = 100_000
    decision = _decision({"exp1220": 1.0}, leverage=2.0)

    seen_target = {}
    def builder(target_exposure):
        seen_target["v"] = target_exposure
        return []

    fake_account = {"equity": "96900"}            # -3.1% → SOFT
    with mock.patch("compass.exp2620_alpaca_connector.get_account",
                    return_value=fake_account), \
         mock.patch("compass.exp2620_alpaca_connector._alpaca_request",
                    return_value=[]):
        conn.apply_decision(decision, {"exp1220": builder})

    # base target = 1.0 weight × 2.0 leverage × $96,900 equity = 193,800
    # soft halves leverage → 96,900
    assert seen_target["v"] == pytest.approx(96_900, rel=0.01)
    assert conn.state["circuit_state"] == "warn"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Telegram fallback when env unset
# ─────────────────────────────────────────────────────────────────────────────
def test_telegram_alert_falls_back_to_log_when_env_missing(tmp_path, caplog):
    conn = AlpacaPaperConnector(_stub_cfg(tmp_path))
    conn.cfg.telegram_bot_token = ""
    conn.cfg.telegram_chat_id = ""
    with caplog.at_level("INFO"):
        conn._send_alert("WARNING", "test_code", "test message")
    # the alert should appear in the captured log
    assert any("test_code" in r.message and "test message" in r.message
               for r in caplog.records)
