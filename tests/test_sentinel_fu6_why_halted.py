"""
Tests for SENTINEL FU#6 — sentinel_cli why-halted EXP-XXX + halt_evidence schema.

Covers:
  1. set_halt() now records halted_at, halted_by, halt_evidence
  2. cmd_why_halted prints halt_reason, halted_at + age, halted_by, reason_class,
     and a per-gate markdown table
  3. Legacy halts without halted_at show "halted_at unknown (pre-2026-04-28)"
  4. G2 (fingerprint) gate: stored vs current is recomputed; stale flag flips
     when current matches stored (drift cleared)
  5. CLI sub-parser registration for why-halted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "paper_exp503.yaml"
    cfg_path.write_text("strategy: credit_spread\nticker: SPY\n")
    fresh_fp = hashlib.sha256(cfg_path.read_bytes()).hexdigest()

    reg_dir = tmp_path / "experiments"
    reg_dir.mkdir()
    (reg_dir / "registry.json").write_text(json.dumps({
        "experiments": {
            "EXP-503": {"id": "EXP-503", "account_id": "PA-X"},
        },
    }))

    state = {
        "version": "1.0",
        "experiments": {
            # Modern halted (with halted_at + evidence)
            "EXP-503": {
                "status": "halted",
                "halt_reason": "config drift detected (G2)",
                "halted_at": "2026-04-25T14:30:00+00:00",
                "halted_by": "guards.py:G2",
                "halt_evidence": {
                    "gate_id": "G2",
                    "metric_name": "config_fingerprint",
                    "stored_value": "STALE_FINGERPRINT_ABCDEF",
                    "current_value": "STALE_FINGERPRINT_ABCDEF",
                    "threshold": "exact_match",
                },
                "paper_config": "configs/paper_exp503.yaml",
                "config_fingerprint": "STALE_FINGERPRINT_ABCDEF",
            },
            # Legacy halted (pre-FU#6 — no halted_at / halted_by / evidence)
            "EXP-800": {
                "status": "halted",
                "halt_reason": "Non-functional — 0 completed trades since launch",
                "paper_config": "configs/paper_exp800.yaml",
            },
            # Active (so why-halted should refuse)
            "EXP-400": {
                "status": "active",
                "halt_reason": None,
            },
        },
    }
    state_path = tmp_path / "sentinel_state.json"
    state_path.write_text(json.dumps(state, indent=2))

    import sentinel.state as st_mod
    monkeypatch.setattr(st_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(st_mod, "_PROJECT_ROOT", tmp_path)

    import sentinel_cli
    monkeypatch.setattr(sentinel_cli, "_PROJECT_ROOT", tmp_path)

    return {"tmp_path": tmp_path, "state_path": state_path, "fresh_fp": fresh_fp}


def _ns(experiment_id):
    return argparse.Namespace(experiment_id=experiment_id)


# ---------------------------------------------------------------------------
# set_halt schema
# ---------------------------------------------------------------------------


class TestSetHaltSchema:
    def test_set_halt_records_halted_at(self, fake_project):
        from sentinel.state import set_halt, load_state

        set_halt("EXP-400", "test halt")
        exp = load_state()["experiments"]["EXP-400"]
        assert exp["status"] == "halted"
        assert exp["halt_reason"] == "test halt"
        assert exp["halted_at"]  # tz-aware ISO
        assert "+00:00" in exp["halted_at"] or exp["halted_at"].endswith("Z")

    def test_set_halt_records_halted_by(self, fake_project):
        from sentinel.state import set_halt, load_state

        set_halt("EXP-400", "drift", halted_by="guards.py:G2")
        exp = load_state()["experiments"]["EXP-400"]
        assert exp["halted_by"] == "guards.py:G2"

    def test_set_halt_records_halt_evidence(self, fake_project):
        from sentinel.state import set_halt, load_state

        evidence = {
            "gate_id": "G2",
            "metric_name": "config_fingerprint",
            "stored_value": "abc123",
            "current_value": "def456",
            "threshold": "exact_match",
        }
        set_halt("EXP-400", "drift", halted_by="guards.py:G2", halt_evidence=evidence)
        exp = load_state()["experiments"]["EXP-400"]
        assert exp["halt_evidence"] == evidence

    def test_set_halt_back_compat_without_kwargs(self, fake_project):
        """Old callers passing only (exp_id, reason) must still work."""
        from sentinel.state import set_halt, load_state

        set_halt("EXP-400", "legacy call")
        exp = load_state()["experiments"]["EXP-400"]
        assert exp["status"] == "halted"
        assert exp["halt_reason"] == "legacy call"
        # halted_at gets stamped automatically
        assert exp.get("halted_at")


# ---------------------------------------------------------------------------
# cmd_why_halted
# ---------------------------------------------------------------------------


class TestWhyHaltedHappyPath:
    def test_prints_halt_reason(self, fake_project, capsys):
        from sentinel_cli import cmd_why_halted

        rc = cmd_why_halted(_ns("EXP-503"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "config drift detected (G2)" in out

    def test_prints_halted_at_with_age(self, fake_project, capsys):
        from sentinel_cli import cmd_why_halted

        cmd_why_halted(_ns("EXP-503"))
        out = capsys.readouterr().out
        assert "2026-04-25" in out
        # "X days ago" or "ago" reference present
        assert "ago" in out.lower()

    def test_prints_halted_by(self, fake_project, capsys):
        from sentinel_cli import cmd_why_halted

        cmd_why_halted(_ns("EXP-503"))
        out = capsys.readouterr().out
        assert "guards.py:G2" in out

    def test_prints_reason_class(self, fake_project, capsys):
        from sentinel_cli import cmd_why_halted

        cmd_why_halted(_ns("EXP-503"))
        out = capsys.readouterr().out.lower()
        # G2 fingerprint halt → config_drift class
        assert "config_drift" in out or "config drift" in out

    def test_prints_evidence_table(self, fake_project, capsys):
        from sentinel_cli import cmd_why_halted

        cmd_why_halted(_ns("EXP-503"))
        out = capsys.readouterr().out
        # Table headers
        assert "Gate" in out
        assert "Metric" in out
        assert "Stored" in out
        assert "Threshold" in out
        assert "Current" in out
        # Row content
        assert "G2" in out
        assert "config_fingerprint" in out


class TestWhyHaltedFingerprintRecompute:
    def test_g2_recomputes_current_fingerprint(self, fake_project, capsys):
        """When halt_evidence.gate_id=='G2', current_value is recomputed
        from paper_config on disk so an operator can see whether drift
        still applies."""
        from sentinel_cli import cmd_why_halted

        cmd_why_halted(_ns("EXP-503"))
        out = capsys.readouterr().out
        # Fixture's stored is STALE_FINGERPRINT_ABCDEF; on-disk yaml hashes
        # to fake_project["fresh_fp"], which differs → drift cleared since halt.
        # Output should show the fresh fingerprint somewhere (possibly
        # truncated to 12 chars).
        fresh = fake_project["fresh_fp"]
        assert fresh[:12] in out or fresh in out

    def test_g2_stale_flag_says_stale_when_drift_cleared(self, fake_project, capsys):
        """Stored fingerprint != current → halt was real then; if current
        now equals stored we'd be 'drift confirmed', otherwise 'stale'.
        Fixture: stored != current → output should indicate 'stale' /
        'drift cleared' not 'drift confirmed'."""
        from sentinel_cli import cmd_why_halted

        cmd_why_halted(_ns("EXP-503"))
        out = capsys.readouterr().out.lower()
        assert "stale" in out or "cleared" in out


class TestWhyHaltedLegacyBackfill:
    def test_legacy_halt_shows_pre_marker(self, fake_project, capsys):
        from sentinel_cli import cmd_why_halted

        rc = cmd_why_halted(_ns("EXP-800"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "pre-2026-04-28" in out
        assert "unknown" in out.lower()


class TestWhyHaltedNonHalted:
    def test_active_returns_warning(self, fake_project, capsys):
        from sentinel_cli import cmd_why_halted

        rc = cmd_why_halted(_ns("EXP-400"))
        assert rc != 0
        out = capsys.readouterr().out + capsys.readouterr().err
        assert "not halted" in out.lower() or "active" in out.lower()

    def test_missing_experiment_errors(self, fake_project, capsys):
        from sentinel_cli import cmd_why_halted

        rc = cmd_why_halted(_ns("EXP-XYZ"))
        assert rc != 0


# ---------------------------------------------------------------------------
# CLI sub-parser registration
# ---------------------------------------------------------------------------


class TestWhyHaltedSubParser:
    def test_subparser_registered(self):
        from sentinel_cli import _build_parser

        parser = _build_parser()
        ns = parser.parse_args(["why-halted", "EXP-503"])
        assert ns.command == "why-halted"
        assert ns.experiment_id == "EXP-503"
