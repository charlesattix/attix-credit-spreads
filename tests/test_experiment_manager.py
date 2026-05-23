"""
tests/test_experiment_manager.py — Tests for ExperimentManager.
"""
import json
import pytest
from pathlib import Path

from experiments.manager import ExperimentManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_REGISTRY = {
    "schema_version": "3.0",
    "last_updated": "2026-01-01",
    "experiments": {
        "EXP-400": {
            "id": "EXP-400",
            "name": "The Champion",
            "created_by": "maximus",
            "status": "active",
            "ticker": "SPY",
            "alpaca_account_id": "PA3ZSXZ5JNEM",
            "env_file": ".env.exp400",
            "config_path": "configs/paper_champion.yaml",
            "tmux_session": "exp400",
            "backtest_baseline": {
                "win_rate": 78.0, "avg_pnl": 525.0,
                "avg_loss": 2100.0, "mc_worst_dd_pct": 41.5,
            },
            "backtest_expectations": {"avg_return": 32.7, "max_dd": -12.1, "robust": 0.870},
        },
        "EXP-401": {
            "id": "EXP-401",
            "name": "The Blend",
            "created_by": "maximus",
            "status": "active",
            "ticker": "SPY",
            "alpaca_account_id": "PA3IPY4E4KPA",
            "env_file": ".env.exp401",
            "config_path": "configs/paper_exp401.yaml",
            "tmux_session": "exp401",
            "backtest_baseline": {
                "win_rate": 72.0, "avg_pnl": 825.0,
                "avg_loss": 2800.0, "mc_worst_dd_pct": 10.5,
            },
        },
        "EXP-600": {
            "id": "EXP-600",
            "name": "IBIT Adaptive",
            "created_by": "charles",
            "status": "paused",
            "ticker": "IBIT",
            "alpaca_account_id": "PA3O14JAJHJ0",
            "env_file": ".env.exp600",
            "config_path": "configs/paper_exp600.yaml",
            "tmux_session": "exp600",
        },
        "EXP-700": {
            "id": "EXP-700",
            "name": "Retired Experiment",
            "created_by": "charles",
            "status": "retired",
            "ticker": "SPY",
            "tmux_session": "exp700",
        },
        "EXP-810-max": {
            "id": "EXP-810-max",
            "name": "Model Ensemble",
            "created_by": "maximus",
            "status": "completed",
            "ticker": "SPY",
            "tmux_session": None,
        },
        "EXP-500": {
            "id": "EXP-500",
            "name": "ML Champion",
            "created_by": "maximus",
            "status": "registered",
            "ticker": "SPY",
            "env_file": None,
            "config_path": None,
            "tmux_session": "exp500",
        },
    },
}


@pytest.fixture
def registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(SAMPLE_REGISTRY, indent=4) + "\n")
    return path


@pytest.fixture
def mgr(registry_file: Path) -> ExperimentManager:
    return ExperimentManager(registry_path=str(registry_file))


# ---------------------------------------------------------------------------
# Tests: get / all
# ---------------------------------------------------------------------------

def test_get_existing(mgr: ExperimentManager):
    exp = mgr.get("EXP-400")
    assert exp is not None
    assert exp["name"] == "The Champion"


def test_get_missing(mgr: ExperimentManager):
    assert mgr.get("EXP-MISSING") is None


def test_all_returns_all(mgr: ExperimentManager):
    all_exps = mgr.all()
    assert len(all_exps) == 6
    assert "EXP-400" in all_exps
    assert "EXP-810-max" in all_exps


# ---------------------------------------------------------------------------
# Tests: status filters
# ---------------------------------------------------------------------------

def test_active(mgr: ExperimentManager):
    active = mgr.active()
    ids = [e["id"] for e in active]
    assert "EXP-400" in ids
    assert "EXP-401" in ids
    assert "EXP-600" not in ids   # paused
    assert "EXP-700" not in ids   # retired


def test_live_includes_paused(mgr: ExperimentManager):
    live = mgr.live()
    ids = [e["id"] for e in live]
    assert "EXP-400" in ids   # active
    assert "EXP-401" in ids   # active
    assert "EXP-600" in ids   # paused is live
    assert "EXP-700" not in ids  # retired is not live


def test_by_status_single(mgr: ExperimentManager):
    retired = mgr.by_status("retired")
    assert len(retired) == 1
    assert retired[0]["id"] == "EXP-700"


def test_by_status_multiple(mgr: ExperimentManager):
    result = mgr.by_status("active", "paused")
    ids = [e["id"] for e in result]
    assert "EXP-400" in ids
    assert "EXP-401" in ids
    assert "EXP-600" in ids
    assert len(ids) == 3


# ---------------------------------------------------------------------------
# Tests: field filters
# ---------------------------------------------------------------------------

def test_by_ticker(mgr: ExperimentManager):
    spy = mgr.by_ticker("SPY")
    ids = [e["id"] for e in spy]
    assert "EXP-400" in ids
    assert "EXP-401" in ids
    assert "EXP-600" not in ids  # IBIT

    ibit = mgr.by_ticker("IBIT")
    assert len(ibit) == 1
    assert ibit[0]["id"] == "EXP-600"


def test_by_creator(mgr: ExperimentManager):
    maximus = mgr.by_creator("maximus")
    ids = [e["id"] for e in maximus]
    assert "EXP-400" in ids
    assert "EXP-401" in ids
    assert "EXP-500" in ids
    assert "EXP-810-max" in ids
    assert "EXP-600" not in ids

    charles = mgr.by_creator("charles")
    ids = [e["id"] for e in charles]
    assert "EXP-600" in ids
    assert "EXP-700" in ids
    assert "EXP-400" not in ids


# ---------------------------------------------------------------------------
# Tests: baseline / accounts_map
# ---------------------------------------------------------------------------

def test_baseline_exists(mgr: ExperimentManager):
    bl = mgr.baseline("EXP-400")
    assert bl is not None
    assert bl["win_rate"] == 78.0
    assert bl["avg_loss"] == 2100.0


def test_baseline_missing(mgr: ExperimentManager):
    assert mgr.baseline("EXP-600") is None


def test_baseline_unknown_exp(mgr: ExperimentManager):
    assert mgr.baseline("EXP-NOPE") is None


def test_baselines_map(mgr: ExperimentManager):
    bmap = mgr.baselines_map()
    assert "EXP-400" in bmap
    assert "EXP-401" in bmap
    assert "EXP-600" not in bmap  # no baseline
    assert bmap["EXP-400"]["win_rate"] == 78.0


def test_accounts_map(mgr: ExperimentManager):
    amap = mgr.accounts_map()
    # Only active experiments
    assert "EXP-400" in amap
    assert "EXP-401" in amap
    assert amap["EXP-400"] == "PA3ZSXZ5JNEM"
    # paused/retired are excluded
    assert "EXP-600" not in amap
    assert "EXP-700" not in amap


# ---------------------------------------------------------------------------
# Tests: field accessors
# ---------------------------------------------------------------------------

def test_env_file(mgr: ExperimentManager):
    assert mgr.env_file("EXP-400") == ".env.exp400"
    assert mgr.env_file("EXP-500") is None
    assert mgr.env_file("EXP-NOPE") is None


def test_tmux_session(mgr: ExperimentManager):
    assert mgr.tmux_session("EXP-400") == "exp400"
    assert mgr.tmux_session("EXP-810-max") is None
    assert mgr.tmux_session("EXP-NOPE") is None


def test_config_path(mgr: ExperimentManager):
    assert mgr.config_path("EXP-400") == "configs/paper_champion.yaml"
    assert mgr.config_path("EXP-500") is None
    assert mgr.config_path("EXP-NOPE") is None


# ---------------------------------------------------------------------------
# Tests: transition
# ---------------------------------------------------------------------------

def test_transition_valid(mgr: ExperimentManager, registry_file: Path):
    exp = mgr.transition("EXP-400", "paused", reason="maintenance")
    assert exp["status"] == "paused"
    # Verify persisted
    with open(registry_file) as f:
        saved = json.load(f)
    assert saved["experiments"]["EXP-400"]["status"] == "paused"


def test_transition_invalid_raises(mgr: ExperimentManager):
    with pytest.raises(ValueError):
        mgr.transition("EXP-400", "completed")  # invalid transition


# ---------------------------------------------------------------------------
# Tests: register
# ---------------------------------------------------------------------------

def test_register_new_experiment(mgr: ExperimentManager, registry_file: Path):
    new_exp = {
        "id": "EXP-999",
        "name": "Test Experiment",
        "created_by": "maximus",
        "status": "registered",
    }
    mgr.register(new_exp)
    assert mgr.get("EXP-999") is not None
    # Verify persisted
    with open(registry_file) as f:
        saved = json.load(f)
    assert "EXP-999" in saved["experiments"]


def test_register_duplicate_raises(mgr: ExperimentManager):
    dup = {"id": "EXP-400", "name": "Dup", "created_by": "maximus", "status": "registered"}
    with pytest.raises(ValueError, match="already exists"):
        mgr.register(dup)


def test_register_without_id_raises(mgr: ExperimentManager):
    with pytest.raises(ValueError, match="id"):
        mgr.register({"name": "No ID"})


# ---------------------------------------------------------------------------
# Tests: update_fields
# ---------------------------------------------------------------------------

def test_update_fields(mgr: ExperimentManager, registry_file: Path):
    mgr.update_fields("EXP-400", notes="updated note", some_flag=True)
    exp = mgr.get("EXP-400")
    assert exp["notes"] == "updated note"
    assert exp["some_flag"] is True
    # Verify persisted
    with open(registry_file) as f:
        saved = json.load(f)
    assert saved["experiments"]["EXP-400"]["notes"] == "updated note"


def test_update_fields_missing_exp_raises(mgr: ExperimentManager):
    with pytest.raises(ValueError, match="not found"):
        mgr.update_fields("EXP-NOPE", foo="bar")


# ---------------------------------------------------------------------------
# Tests: reload
# ---------------------------------------------------------------------------

def test_reload_picks_up_disk_changes(mgr: ExperimentManager, registry_file: Path):
    # Directly mutate registry on disk
    with open(registry_file) as f:
        data = json.load(f)
    data["experiments"]["EXP-400"]["name"] = "Modified On Disk"
    with open(registry_file, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")

    # Before reload, stale data
    assert mgr.get("EXP-400")["name"] == "The Champion"

    mgr.reload()
    assert mgr.get("EXP-400")["name"] == "Modified On Disk"


# ---------------------------------------------------------------------------
# Tests: integration with real registry.json
# ---------------------------------------------------------------------------

def test_real_registry_loads():
    """Smoke test: real registry.json loads and passes basic sanity."""
    mgr = ExperimentManager()
    all_exps = mgr.all()
    assert len(all_exps) >= 36
    active = mgr.active()
    assert len(active) >= 1
    live = mgr.live()
    assert len(live) >= len(active)


def test_real_registry_baselines():
    """Baselines were populated in Phase 0."""
    mgr = ExperimentManager()
    bl = mgr.baseline("EXP-400")
    assert bl is not None
    assert bl["win_rate"] == 78.0


def test_real_registry_tmux_sessions():
    """tmux_sessions were populated in Phase 0."""
    mgr = ExperimentManager()
    assert mgr.tmux_session("EXP-400") == "exp400"
    assert mgr.tmux_session("EXP-810-max") is None
