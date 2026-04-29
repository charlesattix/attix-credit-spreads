"""
Tests for scripts/sentinel-cron.sh — verify the wrapper runs the sync step
EVEN WHEN run_sentinel.py exits non-zero.

Old behaviour: `set -euo pipefail` + run_sentinel.py returning 1 (issues
found) aborted the wrapper before the sync step ever executed. As a result
the Railway dashboard never received any data — 0/68 sync runs in prod.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CRON_SCRIPT = _PROJECT_ROOT / "scripts" / "sentinel-cron.sh"


@pytest.fixture
def fake_bin(tmp_path):
    """
    Build a directory with stubs of:
      - python3 (records calls + uses programmable exit codes)
      - date    (so timestamp lines are deterministic-ish)
    Returns (bin_dir, daily_calls_file, sync_calls_file).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    daily_calls = tmp_path / "daily_calls.log"
    sync_calls = tmp_path / "sync_calls.log"

    # Python wrapper that branches on first arg path
    py = bin_dir / "python3"
    py.write_text(f"""#!/bin/bash
# Stub /usr/bin/python3 for cron-wrapper test. Uses STUB_* env vars so the
# wrapper script's own local DAILY_EXIT/SYNC_EXIT vars don't shadow them.
script="$1"
case "$script" in
    *run_sentinel.py)
        echo "stub: run_sentinel called with $*" >> "{daily_calls}"
        exit "${{STUB_DAILY_EXIT:-0}}"
        ;;
    *sync_sentinel_data.py)
        echo "stub: sync called with $*" >> "{sync_calls}"
        exit "${{STUB_SYNC_EXIT:-0}}"
        ;;
    *)
        echo "unexpected python invocation: $*" >&2
        exit 99
        ;;
esac
""")
    py.chmod(py.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return bin_dir, daily_calls, sync_calls


def _run_wrapper(bin_dir: Path, **env_overrides) -> subprocess.CompletedProcess:
    """
    Execute sentinel-cron.sh with PATH pointing first at our stub bin/, but
    rewrite the hard-coded /usr/bin/python3 path to just `python3` for the
    duration of the test by piping a modified copy through bash -c.
    """
    script = _CRON_SCRIPT.read_text()
    # Replace /usr/bin/python3 with the stub so the test doesn't need to
    # actually patch /usr/bin. The wrapper runs with the modified script.
    patched = script.replace("/usr/bin/python3", str(bin_dir / "python3"))
    # Replace cd target so the script doesn't fail on exotic CI checkouts.
    patched = patched.replace(
        "cd /Users/charlesbot/projects/pilotai-credit-spreads",
        f"cd {_PROJECT_ROOT}",
    )

    env = {**os.environ, **env_overrides}
    return subprocess.run(
        ["bash", "-c", patched],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_wrapper_runs_sync_when_daily_succeeds(fake_bin):
    bin_dir, daily_calls, sync_calls = fake_bin
    result = _run_wrapper(bin_dir, STUB_DAILY_EXIT="0", STUB_SYNC_EXIT="0")

    assert daily_calls.exists() and daily_calls.read_text().strip()
    assert sync_calls.exists() and sync_calls.read_text().strip()
    assert result.returncode == 0


def test_wrapper_runs_sync_even_when_daily_exits_nonzero(fake_bin):
    """The bug fix: daily=1 must NOT abort the wrapper before sync runs."""
    bin_dir, daily_calls, sync_calls = fake_bin
    result = _run_wrapper(bin_dir, STUB_DAILY_EXIT="1", STUB_SYNC_EXIT="0")

    assert daily_calls.read_text().strip(), "daily was never invoked"
    assert sync_calls.read_text().strip(), (
        "sync was NOT invoked despite daily exit 1 — the regression is back"
    )
    # Worst exit code wins so launchd still sees failure
    assert result.returncode == 1


def test_wrapper_propagates_worst_exit_code(fake_bin):
    """daily=1, sync=2 → wrapper exits 2 (the worst)."""
    bin_dir, daily_calls, sync_calls = fake_bin
    result = _run_wrapper(bin_dir, STUB_DAILY_EXIT="1", STUB_SYNC_EXIT="2")

    assert daily_calls.read_text().strip()
    assert sync_calls.read_text().strip()
    assert result.returncode == 2


def test_wrapper_exits_zero_when_both_succeed(fake_bin):
    bin_dir, _, _ = fake_bin
    result = _run_wrapper(bin_dir, STUB_DAILY_EXIT="0", STUB_SYNC_EXIT="0")
    assert result.returncode == 0


def test_wrapper_logs_each_stage(fake_bin):
    bin_dir, _, _ = fake_bin
    result = _run_wrapper(bin_dir, STUB_DAILY_EXIT="0", STUB_SYNC_EXIT="0")
    out = result.stdout
    assert "Starting Sentinel daily" in out
    assert "Daily run finished" in out
    assert "Syncing to Railway dashboard" in out
    assert "Sync finished" in out
