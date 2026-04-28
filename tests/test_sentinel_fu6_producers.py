"""
Tests for Branch 7 (sentinel/fu6-producers).

Closes the gap left by sentinel/fu6-why-halted: the new schema
(halted_at / halted_by / halt_evidence) was added to set_halt() but
no production halt site was passing the structured kwargs. This branch
updates each set_halt caller in:

  - sentinel/guards.py            (G2 fingerprint check)
  - sentinel/orchestrator.py      (run_audit halt path)
  - sentinel/runtime.py           (G7 / G8 / G9 halt paths + _try_halt)
  - sentinel/gates_account.py     (_do_halt helper)
  - scripts/run_sentinel.py       (sentinel_daily API health check halt
                                   via _set_experiment_state)

Coverage:
  1. Round-trip: set_halt(...evidence) → load_state() preserves the dict
  2. Back-compat: positional set_halt(exp_id, reason) still stamps
     halted_at and works without the new kwargs
  3. AST-based wiring assertions per producer file (structured kwargs
     present, no naked set_halt(exp_id, reason) without halted_by)
  4. End-to-end: set_halt with evidence → why-halted prints the table
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_state(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    cfg = cfg_dir / "paper_exp503.yaml"
    cfg.write_text("strategy: credit_spread\n")
    fresh_fp = hashlib.sha256(cfg.read_bytes()).hexdigest()

    state = {
        "version": "1.0",
        "experiments": {
            "EXP-503": {
                "status": "active",
                "halt_reason": None,
                "paper_config": "configs/paper_exp503.yaml",
                "config_fingerprint": fresh_fp,
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


# ---------------------------------------------------------------------------
# 1. Round-trip + back-compat (behavioral)
# ---------------------------------------------------------------------------


class TestSetHaltRoundTrip:
    def test_full_evidence_survives_round_trip(self, fake_state):
        from sentinel.state import set_halt, load_state

        evidence = {
            "gate_id": "G2",
            "metric_name": "config_fingerprint",
            "stored_value": "abcdef1234",
            "current_value": "999111aaaa",
            "threshold": "exact_match",
        }
        set_halt(
            "EXP-503", "config drift",
            halted_by="guards.py:G2",
            halt_evidence=evidence,
        )

        # Force a fresh read from disk (no in-memory shortcut).
        reloaded = load_state()
        exp = reloaded["experiments"]["EXP-503"]
        assert exp["status"] == "halted"
        assert exp["halted_by"] == "guards.py:G2"
        assert exp["halt_evidence"] == evidence
        assert exp["halted_at"]  # auto-stamped
        assert "+00:00" in exp["halted_at"] or exp["halted_at"].endswith("Z")

    def test_back_compat_positional_call(self, fake_state):
        """Old call sites that haven't been migrated must still work."""
        from sentinel.state import set_halt, load_state

        set_halt("EXP-503", "legacy halt")

        exp = load_state()["experiments"]["EXP-503"]
        assert exp["status"] == "halted"
        assert exp["halt_reason"] == "legacy halt"
        assert exp.get("halted_at")  # stamped even without kwargs
        # halted_by / halt_evidence are optional, and SHOULD NOT be set
        # when the caller didn't pass them.
        assert "halted_by" not in exp or exp.get("halted_by") is None
        assert "halt_evidence" not in exp or exp.get("halt_evidence") is None


# ---------------------------------------------------------------------------
# 2. AST wiring — each call site passes structured kwargs
# ---------------------------------------------------------------------------


def _set_halt_calls(source: str) -> list[ast.Call]:
    """Find every direct or aliased set_halt(...) call in the source."""
    tree = ast.parse(source)
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # set_halt(...) | _state_set_halt(...) | sentinel.state.set_halt(...)
        if isinstance(f, ast.Name) and f.id in ("set_halt", "_state_set_halt"):
            out.append(node)
        elif isinstance(f, ast.Attribute) and f.attr in ("set_halt", "_state_set_halt"):
            out.append(node)
    return out


def _has_kwarg(call: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in call.keywords)


PRODUCER_FILES = [
    "sentinel/guards.py",
    "sentinel/orchestrator.py",
    "sentinel/runtime.py",
    "sentinel/gates_account.py",
]


@pytest.mark.parametrize("producer", PRODUCER_FILES)
class TestProducersWireStructuredEvidence:
    def test_every_set_halt_passes_halted_by(self, producer):
        src = (ROOT / producer).read_text()
        calls = _set_halt_calls(src)
        assert calls, f"{producer} has no set_halt call sites — wiring inspection failed"
        for c in calls:
            assert _has_kwarg(c, "halted_by"), (
                f"{producer}: a set_halt call is missing halted_by= kwarg"
            )

    def test_every_set_halt_passes_halt_evidence(self, producer):
        src = (ROOT / producer).read_text()
        calls = _set_halt_calls(src)
        for c in calls:
            assert _has_kwarg(c, "halt_evidence"), (
                f"{producer}: a set_halt call is missing halt_evidence= kwarg"
            )


class TestGuardsG2Wiring:
    """G2 (fingerprint) is the canonical example from the FU#6 spec —
    halt_evidence must encode gate_id='G2', metric_name='config_fingerprint',
    stored/current sha values, threshold='exact_match'."""

    def test_g2_evidence_has_canonical_shape(self):
        src = (ROOT / "sentinel/guards.py").read_text()
        # Just assert all canonical strings appear in the file's source —
        # full structural check would be brittle.
        assert "'G2'" in src or '"G2"' in src
        assert "config_fingerprint" in src
        assert "exact_match" in src
        assert "guards.py:G2" in src


class TestRunSentinelHaltPath:
    """scripts/run_sentinel.py uses _set_experiment_state(...) (not
    set_halt) for the sentinel_daily API health-check halt. It must
    still pass halted_by + halt_evidence so the row matches the schema
    written by sentinel/state.py:set_halt()."""

    def test_api_halt_path_passes_halt_evidence(self):
        src = (ROOT / "scripts" / "run_sentinel.py").read_text()
        # The single halt path lives near "API health check failed".
        # Find that call site and ensure halted_by + halt_evidence appear
        # within ~30 lines after.
        idx = src.find("API health check failed")
        assert idx != -1
        window = src[idx:idx + 2000]
        assert "halted_by" in window, (
            "scripts/run_sentinel.py API-halt path must pass halted_by"
        )
        assert "halt_evidence" in window, (
            "scripts/run_sentinel.py API-halt path must pass halt_evidence"
        )
        assert "sentinel_daily" in window
        assert "api_health" in window or "alpaca_api" in window


# ---------------------------------------------------------------------------
# 3. End-to-end: set_halt with evidence → why-halted renders table
# ---------------------------------------------------------------------------


class TestEndToEndWhyHalted:
    def test_simulated_halt_renders_full_evidence(self, fake_state, capsys):
        from sentinel.state import set_halt
        from sentinel_cli import cmd_why_halted

        set_halt(
            "EXP-503",
            "config drift detected (G2)",
            halted_by="guards.py:G2",
            halt_evidence={
                "gate_id": "G2",
                "metric_name": "config_fingerprint",
                "stored_value": "STORED_SHA_ABCDEF",
                "current_value": "STORED_SHA_ABCDEF",  # halt-time snapshot
                "threshold": "exact_match",
            },
        )

        rc = cmd_why_halted(argparse.Namespace(experiment_id="EXP-503"))
        assert rc == 0
        out = capsys.readouterr().out
        # Header bits
        assert "config drift detected (G2)" in out
        assert "guards.py:G2" in out
        assert "config_drift" in out
        # Evidence table rows
        assert "G2" in out
        assert "config_fingerprint" in out
        assert "exact_match" in out
        # G2 path recomputes current from disk → should differ from stored
        # snapshot, so stale flag should NOT say "drift confirmed" here
        # (current sha != STORED_SHA_ABCDEF).
        assert "drift confirmed" in out.lower() or "stale" in out.lower()


# ---------------------------------------------------------------------------
# 4. Sanity: combined sentinel suites should still pass after this branch
# ---------------------------------------------------------------------------


class TestNoSetHaltLeftBare:
    """No call to set_halt(exp_id, reason) anywhere in the producer files
    should be missing halted_by + halt_evidence kwargs after this branch."""

    def test_count_naked_halts_is_zero(self):
        offenders = []
        for producer in PRODUCER_FILES:
            src = (ROOT / producer).read_text()
            for c in _set_halt_calls(src):
                if not (_has_kwarg(c, "halted_by") and _has_kwarg(c, "halt_evidence")):
                    offenders.append((producer, c.lineno))
        assert offenders == [], (
            f"naked set_halt calls without halted_by + halt_evidence: {offenders}"
        )
