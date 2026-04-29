"""
Tests for SENTINEL Gate 24 — stale-halt nag (market-day-aware).

Covers:
  1. shared/market_calendar.trading_hours_between — market-day-aware delta
  2. sentinel.runtime.check_stale_halts — 6 mixed-age experiments produce
     the expected alert mix; ack-stale suppresses; legacy halts (no
     halted_at) yield no nag and surface in legacy_halts
  3. sentinel_cli ack-stale — sets halt_acknowledged_stale, ..._by, ..._at
     atomically; errors on missing/non-halted experiment
  4. sentinel_cli resume clears the 3 halt_acknowledged_* fields on resume
  5. cmd_daily wires Gate 24 (AST + smoke)
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


NY = ZoneInfo("America/New_York")
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Section 1 — shared/market_calendar.trading_hours_between
# ---------------------------------------------------------------------------


class TestTradingHoursBetween:
    def _import(self):
        from shared.market_calendar import trading_hours_between
        return trading_hours_between

    def test_same_day_inside_session(self):
        thb = self._import()
        # Tuesday 2026-04-28 (a regular trading day per the spec date)
        start = datetime(2026, 4, 28, 10, 0, tzinfo=NY)
        end = datetime(2026, 4, 28, 11, 0, tzinfo=NY)
        assert thb(start, end) == pytest.approx(1.0, abs=1e-6)

    def test_same_day_clipped_at_open(self):
        thb = self._import()
        # 09:00 → 10:30 ET; only 09:30→10:30 counts = 1.0h
        start = datetime(2026, 4, 28, 9, 0, tzinfo=NY)
        end = datetime(2026, 4, 28, 10, 30, tzinfo=NY)
        assert thb(start, end) == pytest.approx(1.0, abs=1e-6)

    def test_same_day_clipped_at_close(self):
        thb = self._import()
        # 15:00 → 17:00 ET; only 15:00→16:00 counts = 1.0h
        start = datetime(2026, 4, 28, 15, 0, tzinfo=NY)
        end = datetime(2026, 4, 28, 17, 0, tzinfo=NY)
        assert thb(start, end) == pytest.approx(1.0, abs=1e-6)

    def test_same_day_after_hours_only(self):
        thb = self._import()
        start = datetime(2026, 4, 28, 16, 30, tzinfo=NY)
        end = datetime(2026, 4, 28, 18, 0, tzinfo=NY)
        assert thb(start, end) == 0.0

    def test_full_trading_day(self):
        thb = self._import()
        start = datetime(2026, 4, 28, 9, 30, tzinfo=NY)
        end = datetime(2026, 4, 28, 16, 0, tzinfo=NY)
        assert thb(start, end) == pytest.approx(6.5, abs=1e-6)

    def test_full_trading_week(self):
        thb = self._import()
        # Mon 2026-04-27 09:30 ET → Fri 2026-05-01 16:00 ET = 32.5 trading hours
        start = datetime(2026, 4, 27, 9, 30, tzinfo=NY)
        end = datetime(2026, 5, 1, 16, 0, tzinfo=NY)
        assert thb(start, end) == pytest.approx(32.5, abs=1e-6)

    def test_weekend_only_zero(self):
        thb = self._import()
        # Sat 2026-04-25 → Sun 2026-04-26
        start = datetime(2026, 4, 25, 10, 0, tzinfo=NY)
        end = datetime(2026, 4, 26, 18, 0, tzinfo=NY)
        assert thb(start, end) == 0.0

    def test_friday_close_to_monday_open_skips_weekend(self):
        thb = self._import()
        # Fri 2026-04-24 15:00 ET → Mon 2026-04-27 10:30 ET
        # = 1h Friday tail + 1h Monday morning = 2.0h
        start = datetime(2026, 4, 24, 15, 0, tzinfo=NY)
        end = datetime(2026, 4, 27, 10, 30, tzinfo=NY)
        assert thb(start, end) == pytest.approx(2.0, abs=1e-6)

    def test_end_before_start_returns_zero(self):
        thb = self._import()
        start = datetime(2026, 4, 28, 11, 0, tzinfo=NY)
        end = datetime(2026, 4, 28, 10, 0, tzinfo=NY)
        assert thb(start, end) == 0.0

    def test_naive_input_assumed_utc(self):
        """Passing naive datetimes should not raise — they are assumed UTC.

        We only assert it returns a non-negative float; exact value depends
        on UTC→ET conversion which is documented behaviour.
        """
        thb = self._import()
        start = datetime(2026, 4, 28, 14, 0)  # naive == 14:00 UTC == 10:00 ET
        end = datetime(2026, 4, 28, 15, 0)    # naive == 15:00 UTC == 11:00 ET
        result = thb(start, end)
        assert result == pytest.approx(1.0, abs=1e-6)

    def test_input_in_utc_converted_to_et(self):
        thb = self._import()
        # 14:00 UTC = 10:00 ET (EDT in late April), 15:00 UTC = 11:00 ET
        start = datetime(2026, 4, 28, 14, 0, tzinfo=UTC)
        end = datetime(2026, 4, 28, 15, 0, tzinfo=UTC)
        assert thb(start, end) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Section 2 — sentinel.runtime.check_stale_halts
# ---------------------------------------------------------------------------


def _state_with_halt(exp_id: str, *, halted_at_iso=None, ack=False, status="halted"):
    exp = {"status": status, "halt_reason": f"reason for {exp_id}"}
    if halted_at_iso is not None:
        exp["halted_at"] = halted_at_iso
    if ack:
        exp["halt_acknowledged_stale"] = True
        exp["halt_acknowledged_by"] = "Charles"
        exp["halt_acknowledged_at"] = "2026-04-28T17:00:00+00:00"
    return exp


class TestCheckStaleHalts:
    """Build a state dict with 6 halted experiments at mixed market-hour ages
    relative to a fixed `now`, and verify the alert mix matches exactly.

    Threshold semantics (per branch spec):
      - >= 32.5 trading hours → critical
      - >= 6.5 trading hours  → warning
      - <  6.5 trading hours  → no alert
    """

    @pytest.fixture
    def fixed_now_and_state(self):
        # Pin "now" to a Tuesday 12:00 ET so we can compute expected ages
        # cleanly. Tue 2026-04-28 12:00 ET = 16:00 UTC (EDT).
        now = datetime(2026, 4, 28, 12, 0, tzinfo=NY).astimezone(UTC)

        # Helper to subtract N trading-hours from `now` and return ISO UTC.
        def at_age(trading_hours_ago: float) -> str:
            cursor = now.astimezone(NY)
            # Snap cursor onto a trading day inside [09:30, 16:00].
            if cursor.weekday() >= 5:
                days_back = cursor.weekday() - 4
                cursor = (cursor - timedelta(days=days_back)).replace(
                    hour=16, minute=0, second=0, microsecond=0,
                )
            elif (cursor.hour, cursor.minute) < (9, 30):
                prev = cursor - timedelta(days=1)
                while prev.weekday() >= 5:
                    prev -= timedelta(days=1)
                cursor = prev.replace(hour=16, minute=0, second=0, microsecond=0)
            elif cursor.hour >= 16:
                cursor = cursor.replace(hour=16, minute=0, second=0, microsecond=0)

            remaining = trading_hours_ago
            while remaining > 1e-9:
                session_open = cursor.replace(hour=9, minute=30, second=0, microsecond=0)
                avail = (cursor - session_open).total_seconds() / 3600.0
                if avail >= remaining:
                    cursor = cursor - timedelta(hours=remaining)
                    remaining = 0.0
                else:
                    remaining -= avail
                    prev = cursor - timedelta(days=1)
                    while prev.weekday() >= 5:
                        prev -= timedelta(days=1)
                    cursor = prev.replace(hour=16, minute=0, second=0, microsecond=0)
            return cursor.astimezone(UTC).isoformat(timespec="seconds")

        state = {
            "version": "1.0",
            "experiments": {
                "EXP-A": _state_with_halt("EXP-A", halted_at_iso=at_age(0.5)),    # fresh → no alert
                "EXP-B": _state_with_halt("EXP-B", halted_at_iso=at_age(5.0)),    # fresh → no alert
                "EXP-C": _state_with_halt("EXP-C", halted_at_iso=at_age(7.0)),    # warning
                "EXP-D": _state_with_halt("EXP-D", halted_at_iso=at_age(30.0)),   # warning
                "EXP-E": _state_with_halt("EXP-E", halted_at_iso=at_age(35.0)),   # critical
                "EXP-F": _state_with_halt("EXP-F", halted_at_iso=at_age(100.0)),  # critical
                # Acknowledged stale halt — old enough to alert, but suppressed
                "EXP-G": _state_with_halt("EXP-G", halted_at_iso=at_age(50.0), ack=True),
                # Legacy halt — no halted_at, must produce no alert (only legacy)
                "EXP-H": _state_with_halt("EXP-H", halted_at_iso=None),
                # Active experiment — must be ignored
                "EXP-ACTIVE": {"status": "active", "halt_reason": None},
            },
        }
        return now, state

    def test_returns_expected_alert_mix(self, fixed_now_and_state):
        from sentinel.runtime import check_stale_halts

        now, state = fixed_now_and_state
        result = check_stale_halts(state, now=now)

        # Map exp_id → severity
        by_exp = {a["experiment_id"]: a["severity"] for a in result.alerts}
        assert by_exp == {
            "EXP-C": "warning",
            "EXP-D": "warning",
            "EXP-E": "critical",
            "EXP-F": "critical",
        }
        # Acknowledged + legacy must surface separately
        assert "EXP-G" in result.acknowledged
        assert "EXP-H" in result.legacy_halts
        # Active experiment must be ignored entirely
        assert "EXP-ACTIVE" not in by_exp
        assert "EXP-ACTIVE" not in result.acknowledged
        assert "EXP-ACTIVE" not in result.legacy_halts

    def test_alert_message_format_includes_age_and_gate(self, fixed_now_and_state):
        from sentinel.runtime import check_stale_halts

        now, state = fixed_now_and_state
        result = check_stale_halts(state, now=now)

        for a in result.alerts:
            assert "G24" in a["message"]
            assert a["experiment_id"] in a["message"]
            # Severity-correct phrasing
            assert "trading" in a["message"].lower()

    def test_legacy_halt_recommends_why_halted(self, fixed_now_and_state):
        from sentinel.runtime import check_stale_halts

        now, state = fixed_now_and_state
        result = check_stale_halts(state, now=now)

        assert result.legacy_halts == ["EXP-H"]
        assert result.legacy_recommendation  # non-empty string
        assert "why-halted" in result.legacy_recommendation

    def test_no_halts_returns_empty_result(self):
        from sentinel.runtime import check_stale_halts

        state = {"experiments": {"EXP-A": {"status": "active"}}}
        result = check_stale_halts(state, now=datetime.now(UTC))
        assert result.alerts == []
        assert result.acknowledged == []
        assert result.legacy_halts == []


# ---------------------------------------------------------------------------
# Section 3 — sentinel_cli ack-stale
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_state_project(tmp_path, monkeypatch):
    """Minimal project with sentinel_state.json containing one halted experiment."""
    state = {
        "version": "1.0",
        "experiments": {
            "EXP-503": {
                "status": "halted",
                "halt_reason": "config drift detected",
                "halted_at": "2026-04-20T14:00:00+00:00",
                "halted_by": "guards.py:G2",
            },
            "EXP-400": {
                "status": "active",
            },
        },
    }
    state_path = tmp_path / "sentinel_state.json"
    state_path.write_text(json.dumps(state, indent=2))

    reg_dir = tmp_path / "experiments"
    reg_dir.mkdir()
    (reg_dir / "registry.json").write_text(json.dumps({"experiments": {}}))

    # Re-point sentinel.state and sentinel_cli at tmp_path
    import sentinel.state as state_mod
    monkeypatch.setattr(state_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(state_mod, "_PROJECT_ROOT", tmp_path)

    import sentinel_cli as cli_mod
    monkeypatch.setattr(cli_mod, "_PROJECT_ROOT", tmp_path)

    return tmp_path, state_path


class TestAckStaleCli:
    def test_sets_three_ack_fields(self, fake_state_project, capsys):
        tmp_path, state_path = fake_state_project
        import sentinel_cli as cli_mod

        ns = argparse.Namespace(
            experiment_id="EXP-503",
            by="Charles",
            reason="Manual review confirms halt is stale; awaiting Carlos sign-off",
        )
        rc = cli_mod.cmd_ack_stale(ns)
        assert rc == 0

        on_disk = json.loads(state_path.read_text())
        exp = on_disk["experiments"]["EXP-503"]
        assert exp["halt_acknowledged_stale"] is True
        assert exp["halt_acknowledged_by"] == "Charles"
        assert exp["halt_acknowledged_at"]
        # Reason can be stored alongside (informational); halt_acknowledged_reason
        # is acceptable but not required — test only enforces the 3 named fields.

        # Status must be unchanged (ack-stale never resumes)
        assert exp["status"] == "halted"

    def test_errors_on_active_experiment(self, fake_state_project, capsys):
        import sentinel_cli as cli_mod

        ns = argparse.Namespace(
            experiment_id="EXP-400", by="Charles", reason="x"
        )
        rc = cli_mod.cmd_ack_stale(ns)
        assert rc != 0
        captured = capsys.readouterr()
        assert "not halted" in (captured.out + captured.err).lower()

    def test_errors_on_missing_experiment(self, fake_state_project, capsys):
        import sentinel_cli as cli_mod

        ns = argparse.Namespace(
            experiment_id="EXP-NOPE", by="Charles", reason="x"
        )
        rc = cli_mod.cmd_ack_stale(ns)
        assert rc != 0
        captured = capsys.readouterr()
        assert "not enrolled" in (captured.out + captured.err).lower() \
            or "not found" in (captured.out + captured.err).lower()

    def test_subparser_registered(self):
        import sentinel_cli as cli_mod

        parser = cli_mod._build_parser()
        # Subparser registration is internal; assert dispatch and parse work.
        ns = parser.parse_args([
            "ack-stale", "EXP-503", "--by", "Charles", "--reason", "x",
        ])
        assert ns.command == "ack-stale"
        assert ns.experiment_id == "EXP-503"
        assert ns.by == "Charles"
        assert ns.reason == "x"


# ---------------------------------------------------------------------------
# Section 4 — cmd_resume clears halt_acknowledged_* fields
# ---------------------------------------------------------------------------


class TestResumeClearsAckFields:
    def test_resume_clears_three_ack_fields(self, tmp_path, monkeypatch):
        # Set up an acknowledged-stale halted experiment, run resume,
        # confirm the 3 ack fields are cleared in the saved file.
        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir()
        cfg_path = cfg_dir / "paper_exp503.yaml"
        cfg_path.write_text("strategy: credit_spread\n")

        reg_dir = tmp_path / "experiments"
        reg_dir.mkdir()
        (reg_dir / "registry.json").write_text(json.dumps({
            "experiments": {"EXP-503": {"id": "EXP-503", "account_id": "PA-NEW"}},
        }))

        state = {
            "version": "1.0",
            "experiments": {
                "EXP-503": {
                    "status": "halted",
                    "halted": False,
                    "halt_reason": "config drift",
                    "halted_at": "2026-04-20T14:00:00+00:00",
                    "halted_by": "guards.py:G2",
                    "halt_acknowledged_stale": True,
                    "halt_acknowledged_by": "Charles",
                    "halt_acknowledged_at": "2026-04-28T17:00:00+00:00",
                    "paper_config": "configs/paper_exp503.yaml",
                    "config_fingerprint": "OLD",
                    "account_id": "PA-OLD",
                },
            },
        }
        state_path = tmp_path / "sentinel_state.json"
        state_path.write_text(json.dumps(state, indent=2))

        import sentinel.state as state_mod
        monkeypatch.setattr(state_mod, "STATE_PATH", state_path)
        monkeypatch.setattr(state_mod, "_PROJECT_ROOT", tmp_path)

        import sentinel_cli as cli_mod
        monkeypatch.setattr(cli_mod, "_PROJECT_ROOT", tmp_path)

        ns = argparse.Namespace(
            experiment_id="EXP-503",
            reason="reviewed and resuming",
            by="Charles",
            restart=False,
        )
        rc = cli_mod.cmd_resume(ns)
        assert rc == 0

        on_disk = json.loads(state_path.read_text())
        exp = on_disk["experiments"]["EXP-503"]
        # All 3 ack fields cleared
        for field in ("halt_acknowledged_stale", "halt_acknowledged_by", "halt_acknowledged_at"):
            assert exp.get(field) in (None, False), f"{field} still present: {exp.get(field)!r}"
        # Resume otherwise behaved (status flipped)
        assert exp["status"] == "active"


# ---------------------------------------------------------------------------
# Section 5 — cmd_daily wires Gate 24
# ---------------------------------------------------------------------------


class TestCmdDailyWiresG24:
    def test_run_sentinel_imports_check_stale_halts(self):
        """AST-level: run_sentinel.py contains a check_stale_halts call inside cmd_daily."""
        src = (ROOT / "scripts" / "run_sentinel.py").read_text()
        tree = ast.parse(src)

        cmd_daily_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_daily":
                cmd_daily_fn = node
                break
        assert cmd_daily_fn is not None, "cmd_daily not found"

        names_called = set()
        for node in ast.walk(cmd_daily_fn):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    names_called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names_called.add(func.attr)
        assert "check_stale_halts" in names_called, (
            "Gate 24 not wired into cmd_daily — expected a check_stale_halts() call"
        )
