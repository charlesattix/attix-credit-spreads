"""test_vrp_vix_ladder.py — PR-D: VIX ladder live trigger (EXP-V8A VRP).

Scoped to compass/live/vrp_vix_ladder.py. Covers:
  1. Ladder thresholds reproduce the exp2850 backtest exposure mapping.
  2. Feed-failure fallback to a fresh last-known VIX.
  3. Stale-data detection → HALT new entries (no crash).
  4. Signal shape, per-stream coverage, entry/exit gates, bad-input guard.
"""

import json
import time

import pytest

from compass.vix_ladder import VIXLadder
import compass.live.vrp_vix_ladder as mod
from compass.live.vrp_vix_ladder import (
    V8A_STREAMS,
    VixFeedUnavailable,
    get_current_vix,
    resolve_vix_ladder_signal,
    vix_ladder_signal,
)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point the last-known-VIX state file at a temp path for every test."""
    monkeypatch.setenv("VRP_VIX_STATE_PATH", str(tmp_path / "vrp_vix_state.json"))
    yield


# ── 1. Ladder thresholds reproduce exp2850 backtest behaviour ────────────────

# Documented exp2850 mapping (compass/exp2850_v8a_with_vix_ladder.py:12-14):
#   VIX≤20→1.0, 25→0.90, 30→0.75, 35→0.60, 40→0.50, 50→0.35, 60→0.25, 70→0.15, >70→0
@pytest.mark.parametrize("vix,expected", [
    (10.0, 1.00),   # below first breakpoint → clamped to full
    (20.0, 1.00),
    (25.0, 0.90),
    (30.0, 0.75),
    (35.0, 0.60),
    (40.0, 0.50),
    (50.0, 0.35),
    (60.0, 0.25),
    (70.0, 0.15),
    (27.5, 0.825),  # linear interpolation between (25,0.90) and (30,0.75)
])
def test_sizing_multiplier_matches_exp2850_breakpoints(vix, expected):
    assert vix_ladder_signal(vix)["sizing_multiplier"] == pytest.approx(expected, abs=1e-6)


def test_sizing_multiplier_is_identical_to_vixladder_oracle():
    """The live signal must use the exact same EXP-2820 ladder as the backtest."""
    oracle = VIXLadder()  # EXP-2820 default — what exp2850 instantiates
    for vix in [11.0, 22.0, 28.0, 33.0, 42.0, 55.0, 65.0, 80.0, 120.0]:
        assert vix_ladder_signal(vix)["sizing_multiplier"] == pytest.approx(
            float(oracle.exposure_at(vix)), abs=1e-9
        ), f"divergence at VIX={vix}"


def test_above_70_collapses_toward_zero():
    # >70 interpolates toward 0 (last breakpoint is (1e9, 0.0)); ~0.15 just above 70.
    assert vix_ladder_signal(80.0)["sizing_multiplier"] < 0.15
    assert vix_ladder_signal(80.0)["sizing_multiplier"] > 0.0


# ── 2. Feed-failure fallback to fresh last-known ─────────────────────────────

def test_fallback_to_fresh_last_known(monkeypatch):
    # Live feed down, but a fresh last-known value exists → use it, flagged degraded.
    state = mod._state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"vix": 18.0, "ts_epoch": time.time()}))
    monkeypatch.setattr(mod, "_fetch_live_vix", lambda: None)

    assert get_current_vix() == pytest.approx(18.0)

    sig = resolve_vix_ladder_signal()
    assert sig["source"] == "last_known"
    assert sig["degraded"] is True
    assert sig["halted"] is False
    assert sig["entry_gate"] is True          # VIX 18 < 35 → entries allowed
    assert sig["sizing_multiplier"] == pytest.approx(1.0)  # VIX 18 ≤ 20 → full


def test_successful_live_read_persists_state(monkeypatch):
    monkeypatch.setattr(mod, "_fetch_live_vix", lambda: 22.5)
    assert get_current_vix() == pytest.approx(22.5)

    persisted = json.loads(mod._state_path().read_text())
    assert persisted["vix"] == pytest.approx(22.5)

    sig = resolve_vix_ladder_signal()
    assert sig["source"] == "live"
    assert sig["degraded"] is False


# ── 3. Stale-data detection → HALT (no crash) ────────────────────────────────

def test_stale_last_known_raises_for_get_current_vix(monkeypatch):
    state = mod._state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    # 48h old > MAX_STALE_HOURS (26h default)
    state.write_text(json.dumps({"vix": 19.0, "ts_epoch": time.time() - 48 * 3600}))
    monkeypatch.setattr(mod, "_fetch_live_vix", lambda: None)

    with pytest.raises(VixFeedUnavailable):
        get_current_vix()


def test_stale_data_resolves_to_halt_without_crashing(monkeypatch):
    state = mod._state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"vix": 19.0, "ts_epoch": time.time() - 48 * 3600}))
    monkeypatch.setattr(mod, "_fetch_live_vix", lambda: None)

    sig = resolve_vix_ladder_signal()  # must NOT raise
    assert sig["halted"] is True
    assert sig["degraded"] is True
    assert sig["entry_gate"] is False
    assert sig["exit_gate"] is False          # hold existing; do not force-exit on missing data
    assert sig["sizing_multiplier"] == 0.0
    assert all(not s["entry_gate"] for s in sig["per_stream"].values())


def test_no_feed_no_state_resolves_to_halt(monkeypatch):
    monkeypatch.setattr(mod, "_fetch_live_vix", lambda: None)  # no state file written
    sig = resolve_vix_ladder_signal()
    assert sig["halted"] is True
    assert sig["entry_gate"] is False


# ── 4. Signal shape / gates / guards ─────────────────────────────────────────

def test_signal_covers_all_eight_streams():
    sig = vix_ladder_signal(22.0)
    assert set(sig["per_stream"].keys()) == set(V8A_STREAMS)
    assert len(V8A_STREAMS) == 8
    for s in sig["per_stream"].values():
        assert set(s.keys()) == {"sizing_multiplier", "entry_gate", "exit_gate"}


def test_entry_gate_blocks_at_crisis_threshold():
    assert vix_ladder_signal(34.9)["entry_gate"] is True
    assert vix_ladder_signal(35.0)["entry_gate"] is False   # mirrors VIX_CRISIS_BLOCK
    assert vix_ladder_signal(50.0)["entry_gate"] is False


def test_exit_gate_fires_at_emergency_threshold():
    assert vix_ladder_signal(44.9)["exit_gate"] is False
    assert vix_ladder_signal(45.0)["exit_gate"] is True     # mirrors VIX_EMERGENCY_EXIT
    assert vix_ladder_signal(70.0)["exit_gate"] is True


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
def test_bad_vix_raises_valueerror(bad):
    with pytest.raises(ValueError):
        vix_ladder_signal(bad)


def test_regime_labels():
    assert vix_ladder_signal(15.0)["regime"] == "calm"
    assert vix_ladder_signal(22.0)["regime"] == "normal"
    assert vix_ladder_signal(31.0)["regime"] == "elevated"
    assert vix_ladder_signal(36.0)["regime"] == "crisis_block"
    assert vix_ladder_signal(46.0)["regime"] == "emergency"
