"""Tests for sentinel/alerting.py — rate limiting, dedup, delivery."""

import time
import pytest
from unittest.mock import patch, MagicMock

from sentinel.alerting import (
    Alert,
    Severity,
    SEVERITY_LABEL,
    send_alert,
    send_alerts,
    gate_alert,
    halt_alert,
    critical_alert,
    warning_alert,
    clear_rate_limits,
    _is_rate_limited,
    _is_duplicate,
    _record_dedup,
    _record_sent,
    _RATE_LIMIT_PER_HOUR,
)


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset rate limiting and dedup state before each test."""
    clear_rate_limits()
    yield
    clear_rate_limits()


# ---------------------------------------------------------------------------
# Alert model tests
# ---------------------------------------------------------------------------


class TestAlertModel:
    def test_dedup_key(self):
        a = gate_alert(Severity.WARNING, "G8", "EXP-400", "Win rate drifting")
        assert a.dedup_key == "G8:EXP-400:Win rate drifting"

    def test_dedup_key_system(self):
        a = gate_alert(Severity.INFO, "G0", None, "System check")
        assert a.dedup_key == "G0:system:System check"

    def test_to_telegram(self):
        a = halt_alert("G2", "EXP-800", "Config drift detected")
        text = a.to_telegram()
        assert "HALT" in text
        assert "EXP-800" in text
        assert "G2" in text

    def test_to_dict(self):
        a = warning_alert("G9", "EXP-1220", "2 stuck positions")
        d = a.to_dict()
        assert d["severity"] == "WARNING"
        assert d["experiment_id"] == "EXP-1220"
        assert d["gate_id"] == "G9"

    def test_severity_ordering(self):
        assert Severity.INFO < Severity.WARNING < Severity.CRITICAL < Severity.HALT


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_not_limited_initially(self):
        assert not _is_rate_limited("EXP-400")

    def test_limited_after_threshold(self):
        for _ in range(_RATE_LIMIT_PER_HOUR):
            _record_sent("EXP-400")
        assert _is_rate_limited("EXP-400")

    def test_different_experiments_independent(self):
        for _ in range(_RATE_LIMIT_PER_HOUR):
            _record_sent("EXP-400")
        assert _is_rate_limited("EXP-400")
        assert not _is_rate_limited("EXP-800")

    def test_system_alerts_tracked(self):
        for _ in range(_RATE_LIMIT_PER_HOUR):
            _record_sent(None)
        assert _is_rate_limited(None)


# ---------------------------------------------------------------------------
# Dedup tests
# ---------------------------------------------------------------------------


class TestDedup:
    def test_not_duplicate_initially(self):
        a = gate_alert(Severity.WARNING, "G8", "EXP-400", "test")
        assert not _is_duplicate(a)

    def test_duplicate_after_record(self):
        a = gate_alert(Severity.WARNING, "G8", "EXP-400", "test")
        _record_dedup(a)
        assert _is_duplicate(a)

    def test_different_message_not_duplicate(self):
        a1 = gate_alert(Severity.WARNING, "G8", "EXP-400", "test1")
        a2 = gate_alert(Severity.WARNING, "G8", "EXP-400", "test2")
        _record_dedup(a1)
        assert not _is_duplicate(a2)

    def test_different_gate_not_duplicate(self):
        a1 = gate_alert(Severity.WARNING, "G8", "EXP-400", "test")
        a2 = gate_alert(Severity.WARNING, "G9", "EXP-400", "test")
        _record_dedup(a1)
        assert not _is_duplicate(a2)


# ---------------------------------------------------------------------------
# Delivery tests
# ---------------------------------------------------------------------------


class TestDelivery:
    @patch("sentinel.alerting._send_telegram", return_value=True)
    def test_normal_delivery(self, mock_tg):
        a = warning_alert("G8", "EXP-400", "drift")
        assert send_alert(a) is True
        mock_tg.assert_called_once()
        assert a.delivered is True

    @patch("sentinel.alerting._send_telegram", return_value=True)
    def test_dedup_suppresses(self, mock_tg):
        a1 = warning_alert("G8", "EXP-400", "drift")
        assert send_alert(a1) is True
        a2 = warning_alert("G8", "EXP-400", "drift")
        assert send_alert(a2) is False  # suppressed
        assert mock_tg.call_count == 1

    @patch("sentinel.alerting._send_telegram", return_value=True)
    def test_rate_limit_suppresses(self, mock_tg):
        # Fill rate bucket
        for i in range(_RATE_LIMIT_PER_HOUR):
            a = warning_alert("G8", "EXP-400", f"drift-{i}")
            send_alert(a)
        # Next one should be suppressed
        a = warning_alert("G8", "EXP-400", "drift-extra")
        assert send_alert(a) is False

    @patch("sentinel.alerting._send_telegram", return_value=True)
    def test_halt_bypasses_rate_limit(self, mock_tg):
        # Fill rate bucket
        for i in range(_RATE_LIMIT_PER_HOUR):
            _record_sent("EXP-400")
        # HALT should bypass
        a = halt_alert("G2", "EXP-400", "config drift")
        assert send_alert(a) is True

    @patch("sentinel.alerting._send_telegram", return_value=True)
    def test_force_bypasses_dedup(self, mock_tg):
        a1 = critical_alert("G21", "EXP-503", "parity violation")
        send_alert(a1)
        a2 = critical_alert("G21", "EXP-503", "parity violation")
        assert send_alert(a2, force=True) is True

    @patch("sentinel.alerting._send_telegram", return_value=False)
    @patch("sentinel.alerting._log_failed_alert")
    def test_failed_delivery_logs(self, mock_log, mock_tg):
        a = warning_alert("G8", "EXP-400", "drift")
        assert send_alert(a) is False
        mock_log.assert_called_once()
        assert a.delivered is False

    @patch("sentinel.alerting._send_telegram", return_value=True)
    def test_send_alerts_sorted_by_severity(self, mock_tg):
        alerts = [
            warning_alert("G8", "EXP-400", "drift"),
            halt_alert("G2", "EXP-800", "config drift"),
            critical_alert("G21", "EXP-503", "parity"),
        ]
        count = send_alerts(alerts)
        assert count == 3
        # HALT should be sent first (highest severity)
        calls = mock_tg.call_args_list
        assert "HALT" in calls[0][0][0]


# ---------------------------------------------------------------------------
# Convenience constructor tests
# ---------------------------------------------------------------------------


class TestConstructors:
    def test_gate_alert(self):
        a = gate_alert(Severity.WARNING, "G8", "EXP-400", "test", details="extra")
        assert a.severity == Severity.WARNING
        assert a.gate_id == "G8"
        assert a.experiment_id == "EXP-400"
        assert a.details == "extra"

    def test_halt_alert(self):
        a = halt_alert("G2", "EXP-800", "drift")
        assert a.severity == Severity.HALT

    def test_critical_alert(self):
        a = critical_alert("G21", None, "system issue")
        assert a.severity == Severity.CRITICAL
        assert a.experiment_id is None

    def test_warning_alert(self):
        a = warning_alert("G9", "EXP-1220", "stuck")
        assert a.severity == Severity.WARNING
