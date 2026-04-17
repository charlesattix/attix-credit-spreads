"""Tests for shared.portfolio_circuit_breaker — portfolio-level drawdown protection."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.portfolio_circuit_breaker import (
    DEFAULT_CAUTION_PCT,
    DEFAULT_HALT_PCT,
    DEFAULT_PAUSE_PCT,
    DEFAULT_RECOVERY_PCT,
    LEVEL_CAUTION,
    LEVEL_HALT,
    LEVEL_NORMAL,
    LEVEL_PAUSE,
    PortfolioCircuitBreaker,
    _fetch_all_alpaca_equity,
    _level_rank,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def state_file(tmp_path):
    """Return a temp state file path."""
    return tmp_path / "circuit_breaker_state.json"


@pytest.fixture
def alerts():
    """Capture sent alerts."""
    sent = []

    def _alert(msg):
        sent.append(msg)
        return True

    return sent, _alert


@pytest.fixture
def make_cb(state_file, alerts):
    """Factory to create a PortfolioCircuitBreaker with test defaults."""
    sent, alert_fn = alerts

    def _make(hwm=100_000.0, equity=100_000.0, **kwargs):
        cb = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=lambda: equity,
            **kwargs,
        )
        if hwm:
            cb.set_hwm(hwm)
        return cb, sent

    return _make


# ── TestLevelRank ─────────────────────────────────────────────────────────────


class TestLevelRank:
    def test_ordering(self):
        assert _level_rank(LEVEL_NORMAL) < _level_rank(LEVEL_CAUTION)
        assert _level_rank(LEVEL_CAUTION) < _level_rank(LEVEL_PAUSE)
        assert _level_rank(LEVEL_PAUSE) < _level_rank(LEVEL_HALT)

    def test_unknown_defaults_to_zero(self):
        assert _level_rank("unknown") == 0


# ── TestNormalState ───────────────────────────────────────────────────────────


class TestNormalState:
    def test_normal_at_hwm(self, make_cb):
        cb, alerts = make_cb(hwm=100_000, equity=100_000)
        result = cb.check(equity_override=100_000)
        assert result["level"] == LEVEL_NORMAL
        assert result["sizing_multiplier"] == 1.0
        assert result["entry_allowed"] is True
        assert result["drawdown_pct"] == 0.0

    def test_hwm_updated_on_new_high(self, make_cb):
        cb, _ = make_cb(hwm=100_000, equity=105_000)
        result = cb.check(equity_override=105_000)
        assert result["level"] == LEVEL_NORMAL
        assert result["hwm"] == 105_000

    def test_small_drawdown_stays_normal(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        result = cb.check(equity_override=95_000)  # -5% DD
        assert result["level"] == LEVEL_NORMAL
        assert result["sizing_multiplier"] == 1.0
        assert len(alerts) == 0


# ── TestCautionLevel ──────────────────────────────────────────────────────────


class TestCautionLevel:
    def test_caution_at_minus_8pct(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        result = cb.check(equity_override=92_000)  # -8%
        assert result["level"] == LEVEL_CAUTION
        assert result["sizing_multiplier"] == 0.5
        assert result["entry_allowed"] is True
        assert len(alerts) == 1
        assert "CAUTION" in alerts[0]

    def test_caution_at_minus_9pct(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        result = cb.check(equity_override=91_000)  # -9%
        assert result["level"] == LEVEL_CAUTION
        assert result["sizing_multiplier"] == 0.5

    def test_caution_boundary_just_above(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        result = cb.check(equity_override=92_001)  # just above -8%
        assert result["level"] == LEVEL_NORMAL
        assert len(alerts) == 0

    def test_caution_does_not_alert_again_on_same_level(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION (alert 1)
        cb.check(equity_override=91_500)  # stays CAUTION (no alert)
        assert len(alerts) == 1


# ── TestPauseLevel ────────────────────────────────────────────────────────────


class TestPauseLevel:
    def test_pause_at_minus_10pct(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        # First hit caution
        cb.check(equity_override=92_000)
        # Then hit pause
        result = cb.check(equity_override=90_000)
        assert result["level"] == LEVEL_PAUSE
        assert result["sizing_multiplier"] == 0.0
        assert result["entry_allowed"] is False
        assert len(alerts) == 2  # CAUTION + PAUSE

    def test_pause_blocks_entries(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        cb.check(equity_override=90_000)  # → PAUSE
        assert cb.is_entry_allowed() is False
        assert cb.get_sizing_multiplier() == 0.0

    def test_direct_to_pause_from_normal(self, make_cb):
        """DD drops directly past caution into pause range."""
        cb, alerts = make_cb(hwm=100_000)
        result = cb.check(equity_override=90_000)  # -10% directly
        assert result["level"] == LEVEL_PAUSE
        assert len(alerts) == 1  # single transition NORMAL→PAUSE

    def test_pause_boundary_just_above(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        result = cb.check(equity_override=90_001)  # just above -10%
        assert result["level"] == LEVEL_CAUTION


# ── TestHaltLevel ─────────────────────────────────────────────────────────────


class TestHaltLevel:
    def test_halt_at_minus_12pct(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        result = cb.check(equity_override=88_000)  # -12%
        assert result["level"] == LEVEL_HALT
        assert result["sizing_multiplier"] == 0.0
        assert result["entry_allowed"] is False
        assert any("HALT" in a or "HARD STOP" in a for a in alerts)

    def test_halt_no_auto_recovery(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=88_000)  # → HALT
        # Even if equity recovers, stays halted
        result = cb.check(equity_override=100_000)
        assert result["level"] == LEVEL_HALT

    def test_halt_requires_manual_reset(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=88_000)  # → HALT
        cb.reset(reason="operator reviewed")
        result = cb.check(equity_override=100_000)
        assert result["level"] == LEVEL_NORMAL
        assert any("RESET" in a for a in alerts)

    def test_direct_to_halt_from_normal(self, make_cb):
        """Catastrophic drop goes straight to HALT."""
        cb, alerts = make_cb(hwm=100_000)
        result = cb.check(equity_override=85_000)  # -15%
        assert result["level"] == LEVEL_HALT

    def test_halt_boundary_just_above(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        result = cb.check(equity_override=88_001)  # just above -12%
        assert result["level"] != LEVEL_HALT


# ── TestRecovery ──────────────────────────────────────────────────────────────


class TestRecovery:
    def test_caution_recovers_above_recovery_threshold(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        result = cb.check(equity_override=95_000)  # -5%, above recovery (-6%)
        assert result["level"] == LEVEL_NORMAL
        assert len(alerts) == 2  # CAUTION + recovery

    def test_caution_stays_in_dead_zone(self, make_cb):
        """Between caution (-8%) and recovery (-6%): stays at current level."""
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION at -8%
        result = cb.check(equity_override=93_000)  # -7%, between -8% and -6%
        assert result["level"] == LEVEL_CAUTION  # not yet recovered

    def test_pause_recovers_above_recovery_threshold(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=90_000)  # → PAUSE
        result = cb.check(equity_override=95_000)  # -5%, above recovery
        assert result["level"] == LEVEL_NORMAL

    def test_pause_does_not_recover_at_minus_7pct(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=90_000)  # → PAUSE
        result = cb.check(equity_override=93_000)  # -7%, below recovery
        assert result["level"] == LEVEL_PAUSE


# ── TestStatePersistence ──────────────────────────────────────────────────────


class TestStatePersistence:
    def test_state_survives_restart(self, state_file, alerts):
        sent, alert_fn = alerts

        # First instance: trigger CAUTION
        cb1 = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=lambda: 92_000,
        )
        cb1.set_hwm(100_000)
        cb1.check(equity_override=92_000)  # → CAUTION
        assert cb1.get_status()["level"] == LEVEL_CAUTION

        # Second instance: reads persisted state
        cb2 = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=lambda: 92_000,
        )
        assert cb2.get_status()["level"] == LEVEL_CAUTION
        assert cb2.get_sizing_multiplier() == 0.5

    def test_hwm_persists(self, state_file, alerts):
        _, alert_fn = alerts
        cb1 = PortfolioCircuitBreaker(
            state_file=state_file, alert_fn=alert_fn,
            equity_fetcher=lambda: 100_000,
        )
        cb1.set_hwm(200_000)

        cb2 = PortfolioCircuitBreaker(
            state_file=state_file, alert_fn=alert_fn,
            equity_fetcher=lambda: 100_000,
        )
        assert cb2.get_status()["hwm"] == 200_000

    def test_transition_history_persists(self, state_file, alerts):
        _, alert_fn = alerts
        cb = PortfolioCircuitBreaker(
            state_file=state_file, alert_fn=alert_fn,
            equity_fetcher=lambda: 92_000,
        )
        cb.set_hwm(100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        cb.check(equity_override=95_000)  # → NORMAL (recovery)

        data = json.loads(state_file.read_text())
        assert len(data["transition_history"]) == 2

    def test_corrupted_state_file_handled(self, state_file, alerts):
        _, alert_fn = alerts
        state_file.write_text("NOT JSON!!!")
        cb = PortfolioCircuitBreaker(
            state_file=state_file, alert_fn=alert_fn,
            equity_fetcher=lambda: 100_000,
        )
        assert cb.get_status()["level"] == LEVEL_NORMAL

    def test_missing_state_file_handled(self, tmp_path, alerts):
        _, alert_fn = alerts
        missing = tmp_path / "does_not_exist.json"
        cb = PortfolioCircuitBreaker(
            state_file=missing, alert_fn=alert_fn,
            equity_fetcher=lambda: 100_000,
        )
        assert cb.get_status()["level"] == LEVEL_NORMAL


# ── TestAlerts ────────────────────────────────────────────────────────────────


class TestAlerts:
    def test_alert_on_escalation(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        assert len(alerts) == 1
        assert "CAUTION" in alerts[0]

    def test_alert_on_recovery(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        cb.check(equity_override=95_000)  # → NORMAL
        assert len(alerts) == 2
        assert "Recovered" in alerts[1]

    def test_no_alert_when_level_unchanged(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        cb.check(equity_override=91_000)  # stays CAUTION
        cb.check(equity_override=91_500)  # stays CAUTION
        assert len(alerts) == 1

    def test_alert_fn_failure_is_nonfatal(self, state_file):
        def bad_alert(msg):
            raise ConnectionError("telegram down")

        cb = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=bad_alert,
            equity_fetcher=lambda: 92_000,
        )
        cb.set_hwm(100_000)
        # Should not raise
        result = cb.check(equity_override=92_000)
        assert result["level"] == LEVEL_CAUTION

    def test_alert_on_manual_reset(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=88_000)  # → HALT
        cb.reset(reason="test")
        assert any("RESET" in a for a in alerts)


# ── TestEquityFetcher ─────────────────────────────────────────────────────────


class TestEquityFetcher:
    def test_equity_override_skips_fetcher(self, make_cb):
        fetcher_called = []
        cb = PortfolioCircuitBreaker(
            state_file=make_cb.__wrapped__
            if hasattr(make_cb, "__wrapped__")
            else None,
            equity_fetcher=lambda: fetcher_called.append(1) or 100_000,
        )
        cb.set_hwm(100_000)
        cb.check(equity_override=95_000)
        assert len(fetcher_called) == 0

    def test_fetcher_failure_returns_current_state(self, state_file, alerts):
        _, alert_fn = alerts

        def bad_fetcher():
            raise RuntimeError("API down")

        cb = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=bad_fetcher,
        )
        cb.set_hwm(100_000)
        result = cb.check()  # no override → calls bad_fetcher
        assert result["action"] == "fetch_failed"
        assert result["level"] == LEVEL_NORMAL  # unchanged

    def test_zero_equity_skipped(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        result = cb.check(equity_override=0)
        assert result["action"] == "invalid_equity"

    def test_negative_equity_skipped(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        result = cb.check(equity_override=-1000)
        assert result["action"] == "invalid_equity"


# ── TestCustomThresholds ──────────────────────────────────────────────────────


class TestCustomThresholds:
    def test_custom_thresholds(self, state_file, alerts):
        _, alert_fn = alerts
        cb = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=lambda: 100_000,
            caution_pct=-0.05,   # -5%
            pause_pct=-0.07,     # -7%
            halt_pct=-0.10,      # -10%
        )
        cb.set_hwm(100_000)

        result = cb.check(equity_override=95_000)  # -5%
        assert result["level"] == LEVEL_CAUTION

        result = cb.check(equity_override=93_000)  # -7%
        assert result["level"] == LEVEL_PAUSE

        result = cb.check(equity_override=90_000)  # -10%
        assert result["level"] == LEVEL_HALT

    def test_custom_recovery_threshold(self, state_file, alerts):
        _, alert_fn = alerts
        cb = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=lambda: 100_000,
            recovery_pct=-0.03,  # -3% recovery
        )
        cb.set_hwm(100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        # -5%: between recovery (-3%) and caution (-8%)
        result = cb.check(equity_override=95_000)
        assert result["level"] == LEVEL_CAUTION  # not yet at -3%
        result = cb.check(equity_override=98_000)  # -2%, above recovery
        assert result["level"] == LEVEL_NORMAL


# ── TestGetStatus ─────────────────────────────────────────────────────────────


class TestGetStatus:
    def test_status_fields(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        status = cb.get_status()
        assert status["level"] == LEVEL_CAUTION
        assert status["hwm"] == 100_000
        assert status["entry_allowed"] is True
        assert status["sizing_multiplier"] == 0.5
        assert "thresholds" in status
        assert status["thresholds"]["caution"] == DEFAULT_CAUTION_PCT

    def test_status_after_halt(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=88_000)  # → HALT
        status = cb.get_status()
        assert status["level"] == LEVEL_HALT
        assert status["entry_allowed"] is False
        assert status["sizing_multiplier"] == 0.0


# ── TestEscalationPaths ───────────────────────────────────────────────────────


class TestEscalationPaths:
    def test_normal_to_caution_to_pause_to_halt(self, make_cb):
        cb, alerts = make_cb(hwm=100_000)
        r1 = cb.check(equity_override=92_000)  # → CAUTION
        assert r1["level"] == LEVEL_CAUTION
        r2 = cb.check(equity_override=90_000)  # → PAUSE
        assert r2["level"] == LEVEL_PAUSE
        r3 = cb.check(equity_override=88_000)  # → HALT
        assert r3["level"] == LEVEL_HALT
        assert len(alerts) == 3

    def test_cannot_deescalate_without_recovery_threshold(self, make_cb):
        """Going from -9% (CAUTION) to -7.5% shouldn't recover."""
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=91_000)  # → CAUTION (-9%)
        result = cb.check(equity_override=92_500)  # -7.5%, still between caution and recovery
        assert result["level"] == LEVEL_CAUTION

    def test_skip_caution_direct_to_pause(self, make_cb):
        """Large drop skips CAUTION, goes to PAUSE."""
        cb, alerts = make_cb(hwm=100_000)
        result = cb.check(equity_override=90_000)
        assert result["level"] == LEVEL_PAUSE
        # Only one alert (NORMAL → PAUSE, not two separate)
        assert len(alerts) == 1

    def test_skip_to_halt(self, make_cb):
        """Catastrophic drop goes straight to HALT."""
        cb, alerts = make_cb(hwm=100_000)
        result = cb.check(equity_override=85_000)  # -15%
        assert result["level"] == LEVEL_HALT
        assert len(alerts) == 1


# ── TestFetchAllAlpacaEquity (mocked) ────────────────────────────────────────


class TestFetchAllAlpacaEquity:
    @patch("shared.credentials.check_portfolio")
    @patch("shared.credentials.get_all_portfolios")
    def test_sums_all_accounts(self, mock_get, mock_check):
        mock_get.return_value = [
            {"env_file": ".env.exp036", "experiment": "exp036"},
            {"env_file": ".env.exp059", "experiment": "exp059"},
        ]
        mock_check.side_effect = [
            {"ok": True, "equity": 80_000},
            {"ok": True, "equity": 70_000},
        ]
        total = _fetch_all_alpaca_equity()
        assert total == 150_000

    @patch("shared.credentials.check_portfolio")
    @patch("shared.credentials.get_all_portfolios")
    def test_partial_failure_still_sums(self, mock_get, mock_check):
        mock_get.return_value = [
            {"env_file": ".env.exp036", "experiment": "exp036"},
            {"env_file": ".env.exp059", "experiment": "exp059"},
        ]
        mock_check.side_effect = [
            {"ok": True, "equity": 80_000},
            {"ok": False, "error": "API down", "experiment": "exp059"},
        ]
        total = _fetch_all_alpaca_equity()
        assert total == 80_000

    @patch("shared.credentials.check_portfolio")
    @patch("shared.credentials.get_all_portfolios")
    def test_all_fail_raises(self, mock_get, mock_check):
        mock_get.return_value = [
            {"env_file": ".env.exp036", "experiment": "exp036"},
        ]
        mock_check.return_value = {"ok": False, "error": "dead keys", "experiment": "exp036"}
        with pytest.raises(RuntimeError, match="All equity fetches failed"):
            _fetch_all_alpaca_equity()

    @patch("shared.credentials.get_all_portfolios")
    def test_no_portfolios_raises(self, mock_get):
        mock_get.return_value = []
        with pytest.raises(RuntimeError, match="No .env.exp"):
            _fetch_all_alpaca_equity()


# ── TestTransitionHistory ─────────────────────────────────────────────────────


class TestTransitionHistory:
    def test_history_tracks_transitions(self, make_cb):
        cb, _ = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        cb.check(equity_override=95_000)  # → NORMAL (recovery)
        cb.check(equity_override=90_000)  # → PAUSE

        status = json.loads(cb.state_file.read_text())
        history = status["transition_history"]
        assert len(history) == 3
        assert history[0]["from"] == LEVEL_NORMAL
        assert history[0]["to"] == LEVEL_CAUTION
        assert history[1]["to"] == LEVEL_NORMAL
        assert history[2]["to"] == LEVEL_PAUSE

    def test_history_capped_at_50(self, state_file, alerts):
        _, alert_fn = alerts
        cb = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=lambda: 100_000,
        )
        cb.set_hwm(100_000)
        # Generate 60 transitions by oscillating
        for i in range(30):
            cb.check(equity_override=92_000)  # → CAUTION
            cb.check(equity_override=95_000)  # → NORMAL (recovery)

        data = json.loads(state_file.read_text())
        assert len(data["transition_history"]) <= 50


# ── TestExp503Scenario ────────────────────────────────────────────────────────


class TestRealWorldScenarios:
    """Scenarios based on the user's actual situation."""

    def test_exp503_drawdown_scenario(self, state_file, alerts):
        """EXP-503: $100K → $52K = -48% drawdown."""
        _, alert_fn = alerts
        cb = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=lambda: 100_000,
        )
        cb.set_hwm(100_000)

        # Simulate gradual decline
        result = cb.check(equity_override=95_000)   # -5%: NORMAL
        assert result["level"] == LEVEL_NORMAL

        result = cb.check(equity_override=92_000)   # -8%: CAUTION
        assert result["level"] == LEVEL_CAUTION
        assert result["sizing_multiplier"] == 0.5

        result = cb.check(equity_override=90_000)   # -10%: PAUSE
        assert result["level"] == LEVEL_PAUSE
        assert result["entry_allowed"] is False

        result = cb.check(equity_override=88_000)   # -12%: HALT
        assert result["level"] == LEVEL_HALT

        # Continued decline doesn't change state
        result = cb.check(equity_override=52_000)   # -48%
        assert result["level"] == LEVEL_HALT

    def test_portfolio_aggregate_scenario(self, state_file, alerts):
        """4 experiments × $100K = $400K portfolio."""
        _, alert_fn = alerts
        cb = PortfolioCircuitBreaker(
            state_file=state_file,
            alert_fn=alert_fn,
            equity_fetcher=lambda: 400_000,
        )
        cb.set_hwm(400_000)

        # One experiment loses 48% ($52K), others flat
        # Portfolio: $52K + $100K + $100K + $100K = $352K → -12% of $400K
        result = cb.check(equity_override=352_000)
        assert result["level"] == LEVEL_HALT

    def test_v_shaped_recovery(self, make_cb):
        """Drawdown that recovers before hitting HALT."""
        cb, alerts = make_cb(hwm=100_000)
        cb.check(equity_override=92_000)  # → CAUTION
        cb.check(equity_override=90_000)  # → PAUSE
        cb.check(equity_override=91_000)  # still PAUSE (below recovery)
        cb.check(equity_override=95_000)  # → NORMAL (above -6% recovery)
        assert cb.get_status()["level"] == LEVEL_NORMAL
        assert len(alerts) == 3  # CAUTION, PAUSE, recovery
