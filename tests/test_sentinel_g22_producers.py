"""
Tests for SENTINEL G22 producer wiring (sentinel/g22-producers).

Verifies:
  1. sentinel.heartbeat.emit_heartbeat — DB call + exception swallow
  2. Each live scanner under scripts/ contains an emit_heartbeat call site
     with the correct experiment_id (regression-protects against accidental
     removal of the producer wiring).

The per-scanner test reads the source file and AST-walks for
``emit_heartbeat("EXP-XXX", ...)`` invocations rather than importing the
scanner (which has heavy side effects: registry load, Alpaca client init,
sentinel halt-check, etc).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentinel.history import SentinelDB  # noqa: E402


# ---------------------------------------------------------------------------
# emit_heartbeat helper
# ---------------------------------------------------------------------------


class TestEmitHeartbeat:
    def test_writes_to_db(self, tmp_path, monkeypatch):
        from sentinel.heartbeat import emit_heartbeat

        monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "s.db"))
        emit_heartbeat("scan-EXP-503", notes="hello")

        db = SentinelDB(str(tmp_path / "s.db"))
        rows = db.get_heartbeats()
        assert len(rows) == 1
        assert rows[0]["scanner_id"] == "scan-EXP-503"
        assert rows[0]["last_status"] == "ok"
        assert rows[0]["notes"] == "hello"

    def test_swallows_db_exceptions(self, monkeypatch):
        from sentinel.heartbeat import emit_heartbeat

        class BoomDB:
            def __init__(self, *a, **kw):
                raise RuntimeError("db dead")

        monkeypatch.setattr("sentinel.heartbeat.SentinelDB", BoomDB, raising=False)
        # Inject the failing class into the lazy import path.
        with patch("sentinel.history.SentinelDB", BoomDB):
            # Should not raise.
            emit_heartbeat("scan-X")

    def test_default_status_ok(self, tmp_path, monkeypatch):
        from sentinel.heartbeat import emit_heartbeat

        monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "s.db"))
        emit_heartbeat("scan-EXP-700")

        db = SentinelDB(str(tmp_path / "s.db"))
        rows = db.get_heartbeats()
        assert rows[0]["last_status"] == "ok"


# ---------------------------------------------------------------------------
# Per-scanner static wiring assertions
# ---------------------------------------------------------------------------


SCANNER_EXPECTATIONS = [
    # (scanner_path, expected_scanner_id, min_call_sites)
    ("scripts/exp700_ml_scanner.py",        "EXP-700",  1),
    ("scripts/exp307_sector_etf_scanner.py","EXP-307",  1),
    ("scripts/exp800_safe_kelly_scanner.py","EXP-800",  2),  # after get_account + end of scan
    ("scripts/run_exp1220.py",              "EXP-1220", 2),  # after get_account + end of main
]


def _heartbeat_calls(source: str) -> list[ast.Call]:
    """Return all ast.Call nodes that look like emit_heartbeat(...)."""
    tree = ast.parse(source)
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # emit_heartbeat(...) or sentinel.heartbeat.emit_heartbeat(...)
        if isinstance(func, ast.Name) and func.id == "emit_heartbeat":
            out.append(node)
        elif isinstance(func, ast.Attribute) and func.attr == "emit_heartbeat":
            out.append(node)
    return out


def _first_arg_str(call: ast.Call) -> str | None:
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


@pytest.mark.parametrize(
    "scanner_path,expected_id,min_calls",
    SCANNER_EXPECTATIONS,
)
class TestScannerHeartbeatWiring:
    def test_imports_heartbeat_helper(self, scanner_path, expected_id, min_calls):
        source = (ROOT / scanner_path).read_text()
        assert "from sentinel.heartbeat import emit_heartbeat" in source, (
            f"{scanner_path} must import emit_heartbeat"
        )

    def test_has_expected_call_count(self, scanner_path, expected_id, min_calls):
        source = (ROOT / scanner_path).read_text()
        calls = _heartbeat_calls(source)
        assert len(calls) >= min_calls, (
            f"{scanner_path} expected ≥{min_calls} emit_heartbeat call(s), found {len(calls)}"
        )

    def test_uses_correct_experiment_id(self, scanner_path, expected_id, min_calls):
        source = (ROOT / scanner_path).read_text()
        calls = _heartbeat_calls(source)
        ids = {_first_arg_str(c) for c in calls if _first_arg_str(c) is not None}
        assert expected_id in ids, (
            f"{scanner_path} must emit heartbeat for {expected_id} "
            f"(found ids={ids})"
        )
