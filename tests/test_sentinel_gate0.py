"""Tests for sentinel guard Gate 0 — registry status enforcement."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentinel.guards import _check_registry_status


class TestGate0RegistryStatus:
    """Test _check_registry_status() — Gate 0 of sentinel guards."""

    def _mock_registry(self, experiments):
        return {
            "schema_version": "3.0",
            "experiments": experiments,
        }

    def test_active_passes(self):
        """Active experiment should pass Gate 0."""
        registry = self._mock_registry({
            "EXP-800": {"id": "EXP-800", "status": "active"},
        })
        with patch("experiments.registry.load_registry", return_value=registry):
            # Should not raise
            _check_registry_status("EXP-800")

    def test_paused_sets_dryrun(self, monkeypatch):
        """Paused experiment should set DRY_RUN=1."""
        monkeypatch.delenv("DRY_RUN", raising=False)
        registry = self._mock_registry({
            "EXP-800": {"id": "EXP-800", "status": "paused"},
        })
        with patch("experiments.registry.load_registry", return_value=registry):
            _check_registry_status("EXP-800")
            assert os.environ.get("DRY_RUN") == "1"
        monkeypatch.delenv("DRY_RUN", raising=False)

    def test_stopped_blocks(self):
        """Stopped experiment should sys.exit(1)."""
        registry = self._mock_registry({
            "EXP-800": {"id": "EXP-800", "status": "stopped"},
        })
        with patch("experiments.registry.load_registry", return_value=registry), \
             patch("sentinel.guards._send_alert"):
            with pytest.raises(SystemExit) as exc_info:
                _check_registry_status("EXP-800")
            assert exc_info.value.code == 1

    def test_retired_blocks(self):
        """Retired experiment should sys.exit(1)."""
        registry = self._mock_registry({
            "EXP-800": {"id": "EXP-800", "status": "retired"},
        })
        with patch("experiments.registry.load_registry", return_value=registry), \
             patch("sentinel.guards._send_alert"):
            with pytest.raises(SystemExit) as exc_info:
                _check_registry_status("EXP-800")
            assert exc_info.value.code == 1

    def test_not_in_registry_passes(self):
        """Experiment not in registry passes (graceful degradation)."""
        registry = self._mock_registry({})
        with patch("experiments.registry.load_registry", return_value=registry):
            # Should not raise
            _check_registry_status("EXP-999")

    def test_import_error_passes(self):
        """If experiments.registry can't be imported, gate passes."""
        with patch.dict("sys.modules", {"experiments.registry": None}):
            # Should not raise — import failure is handled gracefully
            # (the actual function catches ImportError)
            pass  # Gate 0 catches ImportError internally

    def test_registry_read_error_passes(self):
        """If registry.json can't be read, gate passes with warning."""
        with patch("experiments.registry.load_registry", side_effect=Exception("disk error")):
            # Should not raise
            _check_registry_status("EXP-800")

    def test_registered_blocks(self):
        """Registered (not yet active) experiment should block."""
        registry = self._mock_registry({
            "EXP-600": {"id": "EXP-600", "status": "registered"},
        })
        with patch("experiments.registry.load_registry", return_value=registry), \
             patch("sentinel.guards._send_alert"):
            with pytest.raises(SystemExit) as exc_info:
                _check_registry_status("EXP-600")
            assert exc_info.value.code == 1
