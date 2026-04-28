"""
Tests for Branch 6 (sentinel/g22-producers-mainpy).

Closes the gap left by sentinel/g22-producers, which only instrumented
scan-once entry points under scripts/. EXP-400/401/503/600 run via
main.py's scheduler hook (scan_and_sync), so this branch wires
emit_heartbeat into:

  1. main.py::_build_account_state — right after the successful
     Alpaca client.get_account() call.
  2. main.py::scan_and_sync — at the end of each branch (SLOT_SCAN,
     SLOT_RETRAIN, SLOT_MACRO_WEEKLY) so no scheduler tick is silent.

Tests are AST-based to avoid importing main.py (heavy: pulls in
strategies, Alpaca client init, scheduler, telegram, etc.).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN_PY = ROOT / "main.py"


def _heartbeat_calls(node: ast.AST) -> list[ast.Call]:
    out: list[ast.Call] = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id == "emit_heartbeat":
            out.append(n)
        elif isinstance(f, ast.Attribute) and f.attr == "emit_heartbeat":
            out.append(n)
    return out


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    """Find a top-level OR nested function definition by name."""
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


@pytest.fixture(scope="module")
def main_tree():
    return ast.parse(MAIN_PY.read_text())


@pytest.fixture(scope="module")
def main_source():
    return MAIN_PY.read_text()


# ---------------------------------------------------------------------------
# Module-level wiring
# ---------------------------------------------------------------------------


class TestImports:
    def test_imports_emit_heartbeat(self, main_source):
        assert "from sentinel.heartbeat import emit_heartbeat" in main_source, (
            "main.py must import sentinel.heartbeat.emit_heartbeat"
        )

    def test_total_call_sites(self, main_tree):
        calls = _heartbeat_calls(main_tree)
        # 1 in _build_account_state + 3 in scan_and_sync (one per slot branch)
        assert len(calls) >= 4, (
            f"main.py expected ≥4 emit_heartbeat call sites, found {len(calls)}"
        )


# ---------------------------------------------------------------------------
# _build_account_state — heartbeat after successful Alpaca get_account()
# ---------------------------------------------------------------------------


class TestBuildAccountStateWiring:
    def test_emit_heartbeat_inside_build_account_state(self, main_tree):
        fn = _find_function(main_tree, "_build_account_state")
        assert fn is not None, "main.py must define _build_account_state"
        calls = _heartbeat_calls(fn)
        assert len(calls) >= 1, (
            "_build_account_state must emit at least one heartbeat "
            "(after the Alpaca get_account success)"
        )

    def test_heartbeat_after_get_account(self, main_source):
        """Heuristic: the heartbeat call site should appear textually after
        the get_account() call inside the same try-block, not before it."""
        idx_get_account = main_source.find("self.alpaca_provider.get_account()")
        assert idx_get_account != -1, "expected the get_account call in main.py"
        # First emit_heartbeat occurrence should be AFTER get_account.
        idx_hb = main_source.find("emit_heartbeat(", idx_get_account)
        assert idx_hb != -1, (
            "no emit_heartbeat call after self.alpaca_provider.get_account() — "
            "heartbeat must be wired in the success path"
        )


# ---------------------------------------------------------------------------
# scan_and_sync — heartbeat at the end of every scheduler tick branch
# ---------------------------------------------------------------------------


class TestScanAndSyncWiring:
    def test_scan_and_sync_function_exists(self, main_tree):
        fn = _find_function(main_tree, "scan_and_sync")
        assert fn is not None, "main.py must define scan_and_sync"

    def test_scan_and_sync_has_heartbeat_per_branch(self, main_tree):
        fn = _find_function(main_tree, "scan_and_sync")
        assert fn is not None
        calls = _heartbeat_calls(fn)
        # 3 branches: SLOT_MACRO_WEEKLY, SLOT_RETRAIN, else (SLOT_SCAN).
        # Each must emit a heartbeat so a scheduler tick never goes silent.
        assert len(calls) >= 3, (
            f"scan_and_sync expected ≥3 emit_heartbeat call sites "
            f"(one per slot branch), found {len(calls)}"
        )

    def test_emits_use_env_experiment_id(self, main_tree):
        """Each emit_heartbeat call inside scan_and_sync should resolve the
        scanner_id from os.environ (EXPERIMENT_ID), so per-experiment
        launchd processes wire to distinct scanner_id rows."""
        fn = _find_function(main_tree, "scan_and_sync")
        assert fn is not None
        # Look for EXPERIMENT_ID env reference inside scan_and_sync's body
        # OR a helper invoked from it (we accept either: the test allows
        # the lookup to be hoisted to a sibling helper as long as the
        # source string is present in the surrounding region).
        src = ast.unparse(fn)
        assert "EXPERIMENT_ID" in src or "_exp_id" in src or "_g22_exp_id" in src, (
            "scan_and_sync's heartbeat path must reference an env-derived "
            "experiment id (EXPERIMENT_ID env var, matching scripts/ pattern)"
        )


# ---------------------------------------------------------------------------
# Behavioral smoke: helper that resolves EXPERIMENT_ID with a fallback
# ---------------------------------------------------------------------------


class TestExperimentIdResolution:
    """The implementation may use a tiny local helper to resolve the env
    var with a sensible fallback. We verify *some* such resolution path
    appears in the source, but don't pin its name."""

    def test_env_lookup_present(self, main_source):
        # `os.environ.get("EXPERIMENT_ID"...)` or `os.environ["EXPERIMENT_ID"]`
        assert "EXPERIMENT_ID" in main_source, (
            "main.py must read EXPERIMENT_ID from environment for heartbeats"
        )
