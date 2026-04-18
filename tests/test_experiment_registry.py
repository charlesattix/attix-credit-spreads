"""Tests for experiments/registry.py — the experiment registry library."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments.registry import (
    VALID_STATUSES,
    VALID_TRANSITIONS,
    find_orphan_dbs,
    find_orphan_env_files,
    get_active_experiments,
    get_experiment,
    get_experiments_by_status,
    is_research_entry,
    load_registry,
    migrate_v2_to_v3,
    save_registry,
    transition_status,
    validate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_registry(tmp_path):
    """Create a minimal registry for testing."""
    registry = {
        "schema_version": "3.0",
        "last_updated": "2026-04-18",
        "experiments": {
            "EXP-400": {
                "id": "EXP-400",
                "name": "The Champion",
                "created_by": "maximus",
                "status": "active",
                "config_path": "configs/paper_champion.yaml",
                "env_file": ".env.exp400",
                "db_path": "data/exp400/pilotai_exp400.db",
                "alpaca_account_id": "PA36XFVLG0WE",
                "created_at": "2026-03-05T00:00:00+00:00",
                "updated_at": "2026-04-18T00:00:00+00:00",
            },
            "EXP-700": {
                "id": "EXP-700",
                "name": "ML-Filtered Champion",
                "created_by": "charles",
                "status": "retired",
                "retired_date": "2026-04-10",
                "created_at": "2026-04-09T00:00:00+00:00",
                "updated_at": "2026-04-10T00:00:00+00:00",
            },
            "EXP-810-max": {
                "id": "EXP-810-max",
                "name": "Model Ensemble",
                "created_by": "maximus",
                "status": "completed",
                "created_at": "2026-03-31T00:00:00+00:00",
                "updated_at": "2026-03-31T00:00:00+00:00",
            },
            "EXP-600": {
                "id": "EXP-600",
                "name": "IBIT Adaptive",
                "created_by": "charles",
                "status": "registered",
                "created_at": "2026-03-22T00:00:00+00:00",
                "updated_at": "2026-03-22T00:00:00+00:00",
            },
        },
    }
    reg_path = tmp_path / "experiments" / "registry.json"
    reg_path.parent.mkdir(parents=True)
    reg_path.write_text(json.dumps(registry, indent=2))
    return registry, reg_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResearchEntry:
    def test_max_suffix(self):
        assert is_research_entry("EXP-810-max") is True

    def test_real_suffix(self):
        assert is_research_entry("EXP-1220-real") is True

    def test_regular_id(self):
        assert is_research_entry("EXP-400") is False

    def test_paper_suffix(self):
        assert is_research_entry("EXP-880-paper") is True


class TestQueryFunctions:
    def test_get_experiment_found(self, sample_registry):
        registry, _ = sample_registry
        exp = get_experiment("EXP-400", registry)
        assert exp is not None
        assert exp["name"] == "The Champion"

    def test_get_experiment_not_found(self, sample_registry):
        registry, _ = sample_registry
        assert get_experiment("EXP-999", registry) is None

    def test_get_active_experiments(self, sample_registry):
        registry, _ = sample_registry
        active = get_active_experiments(registry)
        assert len(active) == 1
        assert active[0]["id"] == "EXP-400"

    def test_get_experiments_by_status(self, sample_registry):
        registry, _ = sample_registry
        retired = get_experiments_by_status("retired", registry=registry)
        assert len(retired) == 1
        assert retired[0]["id"] == "EXP-700"

    def test_get_experiments_multiple_statuses(self, sample_registry):
        registry, _ = sample_registry
        exps = get_experiments_by_status("active", "registered", registry=registry)
        assert len(exps) == 2
        ids = {e["id"] for e in exps}
        assert ids == {"EXP-400", "EXP-600"}


class TestStatusTransitions:
    def test_valid_transition(self, sample_registry):
        registry, reg_path = sample_registry
        with patch("experiments.registry.REGISTRY_PATH", reg_path):
            exp = transition_status("EXP-400", "paused", reason="testing", registry=registry)
        assert exp["status"] == "paused"
        assert exp.get("last_stopped_at") is not None

    def test_invalid_transition_from_retired(self, sample_registry):
        registry, reg_path = sample_registry
        with pytest.raises(ValueError, match="Cannot transition"), \
             patch("experiments.registry.REGISTRY_PATH", reg_path):
            transition_status("EXP-700", "active", registry=registry)

    def test_invalid_status(self, sample_registry):
        registry, reg_path = sample_registry
        with pytest.raises(ValueError, match="Invalid status"), \
             patch("experiments.registry.REGISTRY_PATH", reg_path):
            transition_status("EXP-400", "banana", registry=registry)

    def test_not_found(self, sample_registry):
        registry, reg_path = sample_registry
        with pytest.raises(ValueError, match="not found"), \
             patch("experiments.registry.REGISTRY_PATH", reg_path):
            transition_status("EXP-999", "active", registry=registry)

    def test_activate_sets_last_started(self, sample_registry):
        registry, reg_path = sample_registry
        with patch("experiments.registry.REGISTRY_PATH", reg_path):
            exp = transition_status("EXP-600", "configuring", registry=registry)
            assert exp["status"] == "configuring"
            exp = transition_status("EXP-600", "active", registry=registry)
        assert exp["status"] == "active"
        assert exp.get("last_started_at") is not None

    def test_retire_sets_date(self, sample_registry):
        registry, reg_path = sample_registry
        with patch("experiments.registry.REGISTRY_PATH", reg_path):
            exp = transition_status("EXP-400", "retired", reason="superseded", registry=registry)
        assert exp["status"] == "retired"
        assert exp.get("retired_date") is not None
        assert exp.get("retired_reason") == "superseded"


class TestValidation:
    def test_valid_registry(self, sample_registry):
        registry, _ = sample_registry
        errors = validate(registry)
        assert errors == []

    def test_missing_required_field(self, sample_registry):
        registry, _ = sample_registry
        del registry["experiments"]["EXP-400"]["name"]
        errors = validate(registry)
        assert any("Missing required field: 'name'" in e for e in errors)

    def test_invalid_creator(self, sample_registry):
        registry, _ = sample_registry
        registry["experiments"]["EXP-400"]["created_by"] = "unknown"
        errors = validate(registry)
        assert any("Invalid created_by" in e for e in errors)

    def test_invalid_status(self, sample_registry):
        registry, _ = sample_registry
        registry["experiments"]["EXP-400"]["status"] = "banana"
        errors = validate(registry)
        assert any("Invalid status" in e for e in errors)

    def test_id_mismatch(self, sample_registry):
        registry, _ = sample_registry
        registry["experiments"]["EXP-400"]["id"] = "EXP-999"
        errors = validate(registry)
        assert any("does not match" in e or "!=" in e for e in errors)


class TestTransitionGraph:
    """Verify the complete transition graph is correct."""

    def test_all_statuses_have_transitions(self):
        for status in VALID_STATUSES:
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"

    def test_terminal_states_have_no_transitions(self):
        assert VALID_TRANSITIONS["retired"] == set()
        assert VALID_TRANSITIONS["completed"] == set()

    def test_active_can_pause_stop_retire_fail(self):
        assert VALID_TRANSITIONS["active"] == {"paused", "stopped", "retired", "failed"}


class TestMigration:
    def test_migrate_statuses(self):
        registry = {
            "schema_version": "2.1",
            "last_updated": "2026-04-12",
            "experiments": {
                "EXP-400": {
                    "id": "EXP-400",
                    "name": "Test",
                    "created_by": "maximus",
                    "status": "paper_trading",
                    "created_date": "2026-03-05",
                    "paper_config": "configs/test.yaml",
                    "account_id": "PA123",
                },
            },
        }
        migrated = migrate_v2_to_v3(registry)
        exp = migrated["experiments"]["EXP-400"]
        assert exp["status"] == "active"
        assert exp.get("config_path") == "configs/test.yaml"
        assert exp.get("alpaca_account_id") == "PA123"
        assert migrated["schema_version"] == "3.0"
        assert exp.get("created_at") == "2026-03-05T00:00:00+00:00"

    def test_migrate_in_development(self):
        registry = {
            "schema_version": "2.1",
            "last_updated": "2026-04-12",
            "experiments": {
                "EXP-500": {
                    "id": "EXP-500",
                    "name": "ML",
                    "created_by": "maximus",
                    "status": "in_development",
                    "created_date": "2026-03-12",
                },
            },
        }
        migrated = migrate_v2_to_v3(registry)
        assert migrated["experiments"]["EXP-500"]["status"] == "registered"


class TestOrphanDetection:
    def test_find_orphan_env_files(self, tmp_path):
        """Orphan .env file should be detected."""
        registry = {
            "experiments": {
                "EXP-400": {"id": "EXP-400"},
            }
        }
        # Create .env.exp999 as orphan
        (tmp_path / ".env.exp999").touch()
        (tmp_path / ".env.exp400").touch()
        (tmp_path / ".env.exp999.example").touch()  # should be ignored

        with patch("experiments.registry.PROJECT_ROOT", tmp_path):
            orphans = find_orphan_env_files(registry)

        assert ".env.exp999" in orphans
        assert ".env.exp400" not in orphans
        assert ".env.exp999.example" not in orphans

    def test_find_orphan_dbs(self, tmp_path):
        """Orphan DB directory should be detected."""
        registry = {
            "experiments": {
                "EXP-400": {"id": "EXP-400"},
            }
        }
        # Create orphan DB
        db_dir = tmp_path / "data" / "exp999"
        db_dir.mkdir(parents=True)
        (db_dir / "pilotai_exp999.db").touch()

        # Create registered DB
        reg_dir = tmp_path / "data" / "exp400"
        reg_dir.mkdir(parents=True)
        (reg_dir / "pilotai_exp400.db").touch()

        with patch("experiments.registry.PROJECT_ROOT", tmp_path):
            orphans = find_orphan_dbs(registry)

        assert len(orphans) == 1
        assert "exp999" in orphans[0]
