"""Tests for per-experiment USE_SHARED_CACHE scoping in railway_worker (Phase 2b)."""
from railway_worker import build_subprocess_env, exp_env_suffix


def test_per_experiment_flag_scopes_to_one_experiment(monkeypatch):
    monkeypatch.delenv("USE_SHARED_CACHE", raising=False)
    monkeypatch.setenv("USE_SHARED_CACHE_EXP3309", "true")

    on = build_subprocess_env({"id": "EXP-3309"})
    off = build_subprocess_env({"id": "EXP-400"})

    assert on["USE_SHARED_CACHE"] == "true"          # migrated experiment
    assert off.get("USE_SHARED_CACHE") != "true"     # peers untouched
    assert on["EXPERIMENT_ID"] == "EXP-3309"


def test_override_can_disable_for_rollback(monkeypatch):
    # Global on, but one experiment explicitly rolled back to false.
    monkeypatch.setenv("USE_SHARED_CACHE", "true")
    monkeypatch.setenv("USE_SHARED_CACHE_EXP400", "false")
    env = build_subprocess_env({"id": "EXP-400"})
    assert env["USE_SHARED_CACHE"] == "false"


def test_global_flag_inherited_when_no_override(monkeypatch):
    monkeypatch.setenv("USE_SHARED_CACHE", "false")
    monkeypatch.delenv("USE_SHARED_CACHE_EXP503", raising=False)
    env = build_subprocess_env({"id": "EXP-503"})
    assert env["USE_SHARED_CACHE"] == "false"


def test_suffix_convention_for_alpha_ids():
    # The env var name uses the dash-stripped, upper-cased suffix.
    assert exp_env_suffix("EXP-3309") == "EXP3309"
    assert exp_env_suffix("EXP-3303b") == "EXP3303B"
    assert exp_env_suffix("EXP-V8A") == "EXPV8A"
