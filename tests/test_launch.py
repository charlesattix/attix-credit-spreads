"""
tests/test_launch.py — Tests for experiments/launch.py (atomic launch orchestrator)
plus the registry-driven scanner list in scheduler/main.py.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments.launch import Launcher, LaunchError, detect_mode, status_report

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _registry(status="configuring"):
    return {
        "schema_version": "3.0",
        "last_updated": "2026-01-01",
        "experiments": {
            "EXP-TEST": {
                "id": "EXP-TEST",
                "name": "Test Experiment",
                "created_by": "charles",
                "status": status,
                "ticker": "SPY",
                "alpaca_account_id": "PATEST123",
                "env_file": ".env.test",
                "config_path": "configs/test.yaml",
                "db_path": "data/pilotai_test.db",
                "tmux_session": "exptest",
                "backtest_baseline": {"win_rate": 70.0, "mc_worst_dd_pct": 12.0},
            },
        },
    }


class FakeTmux:
    def __init__(self, available=True):
        self._available = available
        self._sessions: set[str] = set()
        self.started: list[str] = []
        self.killed: list[str] = []

    def available(self):
        return self._available

    def exists(self, session):
        return session in self._sessions

    def start(self, session, config_path, env_file):
        self._sessions.add(session)
        self.started.append(session)

    def kill(self, session):
        self._sessions.discard(session)
        self.killed.append(session)


@pytest.fixture
def project(tmp_path):
    """A minimal project tree with registry, config, env, sentinel state."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "configs" / "test.yaml").write_text("experiment_id: EXP-TEST\npaper_mode: true\n")
    (tmp_path / ".env.test").write_text("EXPERIMENT_ID=EXP-TEST\n")
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(_registry()))
    sent_path = tmp_path / "sentinel_state.json"
    sent_path.write_text(json.dumps({"sentinel_version": "1.1", "experiments": {}}))
    return {
        "root": tmp_path,
        "registry": reg_path,
        "sentinel": sent_path,
        "db": tmp_path / "data" / "pilotai_test.db",
    }


def make_scan(ok=True, create_db=True, db_path=None):
    def _run(config_path, env_file, project_root):
        if create_db and db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(db_path))
            con.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER)")
            con.commit()
            con.close()
        return ok, "scan output"
    return _run


def _build(project, *, mode="local", scan=None, tmux=None, preflight_ok=True, dry_run=False):
    return Launcher(
        exp_id="EXP-TEST",
        mode=mode,
        dry_run=dry_run,
        project_root=project["root"],
        registry_path=str(project["registry"]),
        sentinel_path=project["sentinel"],
        preflight_runner=lambda c, p: (preflight_ok, "" if preflight_ok else "preflight boom"),
        scan_runner=scan if scan is not None else make_scan(db_path=project["db"]),
        tmux_ops=tmux if tmux is not None else FakeTmux(),
    )


def _reg_status(project):
    return json.loads(project["registry"].read_text())["experiments"]["EXP-TEST"]["status"]


def _sent_entry(project):
    return json.loads(project["sentinel"].read_text())["experiments"].get("EXP-TEST")


# ---------------------------------------------------------------------------
# detect_mode
# ---------------------------------------------------------------------------

def test_detect_mode_railway():
    assert detect_mode({"RAILWAY_ENVIRONMENT": "production"}) == "railway"
    assert detect_mode({"RAILWAY_SERVICE_ID": "abc"}) == "railway"


def test_detect_mode_local():
    assert detect_mode({}) == "local"
    assert detect_mode({"HOME": "/x"}) == "local"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_launch_happy_path(project):
    tmux = FakeTmux()
    launcher = _build(project, tmux=tmux)
    result = launcher.launch()

    assert result["ok"] is True
    # registry flipped + live_since stamped
    exp = json.loads(project["registry"].read_text())["experiments"]["EXP-TEST"]
    assert exp["status"] == "active"
    assert exp["live_since"]  # today
    assert exp["last_started_at"]  # set by transition_status
    # sentinel enrolled active with a fingerprint
    sent = _sent_entry(project)
    assert sent["status"] == "active"
    assert sent["halted"] is False
    assert len(sent["config_fingerprint"]) == 64
    assert sent["account_id"] == "PATEST123"
    # worker started + db present
    assert tmux.started == ["exptest"]
    assert project["db"].exists()
    # steps recorded
    step_names = [s["step"] for s in result["steps"]]
    assert step_names == ["validate", "preflight", "transition", "sentinel",
                          "smoke_scan", "worker", "dashboard"]


def test_launch_railway_mode_no_tmux(project):
    tmux = FakeTmux()
    launcher = _build(project, mode="railway", tmux=tmux)
    result = launcher.launch()
    assert result["ok"] is True
    assert tmux.started == []  # railway: scheduler service runs it, no local process
    assert _reg_status(project) == "active"


# ---------------------------------------------------------------------------
# Validation gating (pre-mutation — raises, no rollback needed)
# ---------------------------------------------------------------------------

def test_launch_rejects_non_configuring(project):
    project["registry"].write_text(json.dumps(_registry(status="active")))
    launcher = _build(project)
    with pytest.raises(LaunchError, match="expected 'configuring'"):
        launcher.launch()


def test_launch_missing_env_file_local(project):
    (project["root"] / ".env.test").unlink()
    launcher = _build(project, mode="local")
    with pytest.raises(LaunchError, match="env file not found"):
        launcher.launch()


def test_launch_missing_config(project):
    (project["root"] / "configs" / "test.yaml").unlink()
    launcher = _build(project)
    with pytest.raises(LaunchError, match="config not found"):
        launcher.launch()


def test_launch_preflight_failure(project):
    launcher = _build(project, preflight_ok=False)
    with pytest.raises(LaunchError, match="preflight failed"):
        launcher.launch()
    # nothing mutated
    assert _reg_status(project) == "configuring"
    assert _sent_entry(project) is None


# ---------------------------------------------------------------------------
# Atomic rollback
# ---------------------------------------------------------------------------

def test_rollback_on_scan_failure(project):
    tmux = FakeTmux()
    launcher = _build(project, scan=make_scan(ok=False, create_db=False), tmux=tmux)
    result = launcher.launch()

    assert result["ok"] is False
    assert "smoke scan failed" in result["error"]
    # registry + sentinel fully restored
    assert _reg_status(project) == "configuring"
    assert _sent_entry(project) is None
    # worker never started
    assert tmux.started == []
    # both files were restored
    assert any("registry.json" in r for r in result["rolled_back"])
    assert any("sentinel_state.json" in r for r in result["rolled_back"])


def test_rollback_when_db_not_created(project):
    # scan returns ok but creates no DB → verification fails → rollback
    launcher = _build(project, scan=make_scan(ok=True, create_db=False))
    result = launcher.launch()
    assert result["ok"] is False
    assert "DB was not created" in result["error"]
    assert _reg_status(project) == "configuring"
    assert _sent_entry(project) is None


def test_rollback_kills_tmux_on_late_failure(project):
    tmux = FakeTmux()
    launcher = _build(project, tmux=tmux)
    # Force the final dashboard verification to fail AFTER tmux started.
    launcher.verify_dashboard = lambda: (_ for _ in ()).throw(LaunchError("dash boom"))
    result = launcher.launch()

    assert result["ok"] is False
    assert tmux.started == ["exptest"]
    assert tmux.killed == ["exptest"]          # rolled back
    assert _reg_status(project) == "configuring"
    assert _sent_entry(project) is None


def test_rollback_restores_preexisting_sentinel_entry(project):
    # Sentinel already has a 'configuring' entry; a failed launch must restore it,
    # not leave it 'active'.
    state = {"sentinel_version": "1.1", "experiments": {
        "EXP-TEST": {"status": "configuring", "live_since": None, "halted": False}}}
    project["sentinel"].write_text(json.dumps(state))
    launcher = _build(project, scan=make_scan(ok=False, create_db=False))
    result = launcher.launch()
    assert result["ok"] is False
    assert _sent_entry(project)["status"] == "configuring"


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_makes_no_changes(project):
    tmux = FakeTmux()
    launcher = _build(project, dry_run=True, tmux=tmux)
    result = launcher.launch()
    assert result.get("dry_run") is True
    assert _reg_status(project) == "configuring"
    assert _sent_entry(project) is None
    assert tmux.started == []


# ---------------------------------------------------------------------------
# status_report
# ---------------------------------------------------------------------------

def test_status_report_not_enrolled(project):
    rep = status_report(
        "EXP-TEST", project_root=project["root"],
        registry_path=str(project["registry"]), sentinel_path=project["sentinel"],
    )
    assert rep["status"] == "configuring"
    assert rep["in_live_set"] is False
    assert rep["db"]["exists"] is False
    assert rep["sentinel"]["enrolled"] is False
    assert rep["healthy"] is False


def test_status_report_healthy(project):
    # Make it look fully live: active registry, db with trades, enrolled sentinel.
    project["registry"].write_text(json.dumps(_registry(status="active")))
    project["db"].parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(project["db"]))
    con.execute("CREATE TABLE trades (id INTEGER)")
    con.execute("INSERT INTO trades VALUES (1)")
    con.commit()
    con.close()
    state = {"sentinel_version": "1.1", "experiments": {
        "EXP-TEST": {"status": "active", "halted": False, "live_since": "2026-05-25"}}}
    project["sentinel"].write_text(json.dumps(state))

    rep = status_report(
        "EXP-TEST", project_root=project["root"],
        registry_path=str(project["registry"]), sentinel_path=project["sentinel"],
    )
    assert rep["status"] == "active"
    assert rep["in_live_set"] is True
    assert rep["db"]["exists"] is True
    assert rep["db"]["trades"] == 1
    assert rep["sentinel"]["enrolled"] is True
    assert rep["healthy"] is True


def test_status_report_unknown_experiment(project):
    rep = status_report(
        "EXP-NOPE", project_root=project["root"],
        registry_path=str(project["registry"]), sentinel_path=project["sentinel"],
    )
    assert rep["error"] == "not found in registry"


# ---------------------------------------------------------------------------
# Registry-driven launcher lists (pilotctl + scheduler)
# ---------------------------------------------------------------------------

@pytest.fixture
def live_registry(tmp_path):
    reg = {
        "schema_version": "3.0", "last_updated": "2026-01-01",
        "experiments": {
            "EXP-400": {"id": "EXP-400", "name": "A", "created_by": "maximus",
                        "status": "active", "config_path": "configs/paper_champion.yaml",
                        "env_file": ".env.exp400", "db_path": "data/pilotai_exp400.db",
                        "tmux_session": "exp400"},
            "EXP-600": {"id": "EXP-600", "name": "B", "created_by": "charles",
                        "status": "paused", "config_path": "configs/paper_exp600.yaml",
                        "env_file": ".env.exp600", "db_path": "data/pilotai_exp600.db",
                        "tmux_session": "exp600"},
            "EXP-3311": {"id": "EXP-3311", "name": "C", "created_by": "maximus",
                         "status": "configuring", "config_path": "configs/paper_exp3311.yaml",
                         "env_file": ".env.exp3311", "db_path": "data/pilotai_exp3311.db",
                         "tmux_session": "exp3311"},
            "EXP-700": {"id": "EXP-700", "name": "D", "created_by": "charles",
                        "status": "retired", "tmux_session": "exp700"},
        },
    }
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(reg))
    return p


def test_scheduler_live_jobs_from_registry(monkeypatch, live_registry):
    pytest.importorskip("apscheduler")  # scheduler/main imports it at module load
    import experiments.manager as mgr_mod
    fake_mgr = mgr_mod.ExperimentManager(registry_path=str(live_registry))
    monkeypatch.setattr(mgr_mod, "get_manager", lambda: fake_mgr)

    from scheduler.main import live_experiment_jobs
    jobs = live_experiment_jobs()
    ids = [j[0] for j in jobs]
    assert ids == ["EXP-400", "EXP-600"]            # sorted, live only
    assert jobs[0] == ("EXP-400", "configs/paper_champion.yaml", ".env.exp400")
