"""
Tests for SENTINEL FU#5 — sentinel_cli resume EXP-XXX.

Atomic resume command per SENTINEL_TODO_CC1.md:
  - resume on halted experiment flips status, refreshes fingerprint,
    refreshes account_id, stamps resumed_{at,by,reason}, preserves
    backtest_baseline / peak_equity / enrolled_at
  - resume on active experiment is a no-op with warning
  - resume on missing experiment errors cleanly
  - --restart invokes launchctl unload+load (mocked subprocess)
  - rollback on save_state failure: on-disk file unchanged
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Build a minimal project layout: configs/, experiments/registry.json,
    sentinel_state.json. Re-points sentinel.state and sentinel_cli at it.
    """
    # configs/paper_exp503.yaml — content drives the SHA-256 fingerprint
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "paper_exp503.yaml"
    cfg_path.write_text("strategy: credit_spread\nticker: SPY\n")
    fresh_fp = hashlib.sha256(cfg_path.read_bytes()).hexdigest()

    # experiments/registry.json — current account
    reg_dir = tmp_path / "experiments"
    reg_dir.mkdir()
    registry = {
        "schema_version": "3.0",
        "experiments": {
            "EXP-503": {
                "id": "EXP-503",
                "account_id": "PA-NEW-ACCOUNT",
                "paper_config": "configs/paper_exp503.yaml",
            },
        },
    }
    (reg_dir / "registry.json").write_text(json.dumps(registry, indent=2))

    # sentinel_state.json — halted with stale fingerprint + stale account
    state = {
        "version": "1.0",
        "experiments": {
            "EXP-503": {
                "status": "halted",
                "halted": False,
                "halt_reason": "config drift detected",
                "paper_config": "configs/paper_exp503.yaml",
                "config_fingerprint": "STALE_FINGERPRINT_DEADBEEF",
                "account_id": "PA-OLD-ACCOUNT",
                "enrolled_at": "2026-04-12T00:00:00+00:00",
                "live_since": "2026-03-22",
                "backtest_baseline": {"win_rate": 68.0, "mc_worst_dd_pct": 12.5},
                "peak_equity": 105000.0,
            },
            "EXP-400": {
                "status": "active",
                "halted": False,
                "halt_reason": None,
                "paper_config": "configs/paper_exp400.yaml",
                "config_fingerprint": "abc123",
                "account_id": "PA-400",
                "enrolled_at": "2026-04-12T00:00:00+00:00",
            },
        },
    }
    state_path = tmp_path / "sentinel_state.json"
    state_path.write_text(json.dumps(state, indent=2))

    # Re-point sentinel.state STATE_PATH and _PROJECT_ROOT so save_state and
    # compute_fingerprint resolve inside tmp_path.
    import sentinel.state as st_mod
    monkeypatch.setattr(st_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(st_mod, "_PROJECT_ROOT", tmp_path)

    # Re-point sentinel_cli's _PROJECT_ROOT.
    import sentinel_cli
    monkeypatch.setattr(sentinel_cli, "_PROJECT_ROOT", tmp_path)

    return {
        "tmp_path": tmp_path,
        "state_path": state_path,
        "fresh_fp": fresh_fp,
    }


def _resume_args(experiment_id, reason="manual resume", by="tester", restart=False):
    return argparse.Namespace(
        experiment_id=experiment_id,
        reason=reason,
        by=by,
        restart=restart,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResumeHalted:
    def test_resume_halted_flips_status(self, fake_project, capsys):
        from sentinel_cli import cmd_resume

        rc = cmd_resume(_resume_args("EXP-503", reason="apr20 realignment", by="Charles"))
        assert rc == 0

        state = json.loads(fake_project["state_path"].read_text())
        exp = state["experiments"]["EXP-503"]
        assert exp["status"] == "active"
        assert exp["halted"] is False
        assert exp["halt_reason"] is None

    def test_resume_refreshes_fingerprint(self, fake_project):
        from sentinel_cli import cmd_resume

        cmd_resume(_resume_args("EXP-503"))

        state = json.loads(fake_project["state_path"].read_text())
        exp = state["experiments"]["EXP-503"]
        assert exp["config_fingerprint"] == fake_project["fresh_fp"]
        assert exp["config_fingerprint"] != "STALE_FINGERPRINT_DEADBEEF"

    def test_resume_refreshes_account_id_from_registry(self, fake_project):
        from sentinel_cli import cmd_resume

        cmd_resume(_resume_args("EXP-503"))

        state = json.loads(fake_project["state_path"].read_text())
        exp = state["experiments"]["EXP-503"]
        assert exp["account_id"] == "PA-NEW-ACCOUNT"

    def test_resume_stamps_resumed_fields(self, fake_project):
        from sentinel_cli import cmd_resume

        cmd_resume(_resume_args("EXP-503", reason="realignment", by="Charles"))

        state = json.loads(fake_project["state_path"].read_text())
        exp = state["experiments"]["EXP-503"]
        assert exp["resume_reason"] == "realignment"
        assert exp["resumed_by"] == "Charles"
        # ISO 8601 with tz offset
        assert exp["resumed_at"].startswith("20")
        assert "+00:00" in exp["resumed_at"] or exp["resumed_at"].endswith("Z")

    def test_resume_preserves_baseline_and_peak(self, fake_project):
        from sentinel_cli import cmd_resume

        cmd_resume(_resume_args("EXP-503"))

        state = json.loads(fake_project["state_path"].read_text())
        exp = state["experiments"]["EXP-503"]
        assert exp["backtest_baseline"] == {"win_rate": 68.0, "mc_worst_dd_pct": 12.5}
        assert exp["peak_equity"] == 105000.0
        assert exp["enrolled_at"] == "2026-04-12T00:00:00+00:00"
        assert exp["live_since"] == "2026-03-22"

    def test_resume_prints_before_after_diff(self, fake_project, capsys):
        from sentinel_cli import cmd_resume

        cmd_resume(_resume_args("EXP-503"))
        out = capsys.readouterr().out
        assert "EXP-503" in out
        # Diff should reference both old and new key fields.
        assert "halted" in out
        assert "active" in out
        assert "PA-OLD-ACCOUNT" in out
        assert "PA-NEW-ACCOUNT" in out


class TestResumeActiveNoOp:
    def test_resume_active_is_noop_with_warning(self, fake_project, capsys):
        from sentinel_cli import cmd_resume

        before = json.loads(fake_project["state_path"].read_text())

        rc = cmd_resume(_resume_args("EXP-400"))
        assert rc == 0

        out = capsys.readouterr().out + capsys.readouterr().err
        # Some "warning" / "not halted" / "no-op" hint must appear.
        lowered = out.lower()
        assert any(tok in lowered for tok in ("warning", "not halted", "no-op", "noop"))

        after = json.loads(fake_project["state_path"].read_text())
        # state file unchanged (we wrote nothing).
        assert before == after


class TestResumeMissingExperiment:
    def test_resume_missing_returns_nonzero(self, fake_project, capsys):
        from sentinel_cli import cmd_resume

        rc = cmd_resume(_resume_args("EXP-999"))
        assert rc != 0

        captured = capsys.readouterr()
        combined = (captured.out + captured.err).lower()
        assert "exp-999" in combined or "not enrolled" in combined or "not found" in combined


class TestResumeRestartFlag:
    def test_restart_invokes_launchctl(self, fake_project):
        from sentinel_cli import cmd_resume

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            rc = cmd_resume(_resume_args("EXP-503", restart=True))

        assert rc == 0
        # Expect at least 2 calls: unload then load.
        assert mock_run.call_count >= 2
        cmds = [tuple(c.args[0]) for c in mock_run.call_args_list]
        joined = " ".join(" ".join(c) for c in cmds)
        assert "launchctl" in joined
        assert "unload" in joined
        assert "load" in joined
        # Plist path mentions the experiment.
        assert "exp503" in joined.lower() or "503" in joined

    def test_no_restart_skips_launchctl(self, fake_project):
        from sentinel_cli import cmd_resume

        with patch("subprocess.run") as mock_run:
            cmd_resume(_resume_args("EXP-503", restart=False))

        assert mock_run.call_count == 0


class TestResumeRollbackOnSaveFailure:
    def test_save_failure_leaves_state_untouched(self, fake_project):
        from sentinel_cli import cmd_resume

        before_bytes = fake_project["state_path"].read_bytes()

        # Force save_state to raise mid-resume.
        with patch("sentinel.state.save_state", side_effect=OSError("disk full")):
            rc = cmd_resume(_resume_args("EXP-503"))

        # Non-zero exit code on failure.
        assert rc != 0

        after_bytes = fake_project["state_path"].read_bytes()
        assert before_bytes == after_bytes, (
            "sentinel_state.json must be byte-for-byte unchanged when save_state raises"
        )


# ---------------------------------------------------------------------------
# CLI sub-parser registration
# ---------------------------------------------------------------------------


class TestResumeSubParser:
    def test_resume_command_registered(self):
        from sentinel_cli import _build_parser

        parser = _build_parser()
        ns = parser.parse_args([
            "resume", "EXP-503",
            "--reason", "test",
            "--by", "Charles",
        ])
        assert ns.command == "resume"
        assert ns.experiment_id == "EXP-503"
        assert ns.reason == "test"
        assert ns.by == "Charles"
        assert ns.restart is False

    def test_resume_restart_flag_parses(self):
        from sentinel_cli import _build_parser

        parser = _build_parser()
        ns = parser.parse_args([
            "resume", "EXP-503",
            "--reason", "x", "--by", "y",
            "--restart",
        ])
        assert ns.restart is True
