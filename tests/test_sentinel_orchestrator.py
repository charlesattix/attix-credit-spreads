"""Tests for sentinel/orchestrator.py — Gate 21 config parity + orchestrator."""

import pytest
from unittest.mock import patch

from sentinel.orchestrator import (
    GateResult,
    GateOutcome,
    ExperimentAudit,
    ParityDiff,
    check_config_parity,
    _compute_health_score,
    _get_nested,
    _values_match,
    _run_gate21_config_parity,
    audit_experiment,
    audit_all_experiments,
    format_audit_report,
    RESULT_LABEL,
)


# ---------------------------------------------------------------------------
# Gate 21: Config parity tests
# ---------------------------------------------------------------------------


class TestConfigParity:
    """Test backtest vs paper config comparison."""

    def _bt_config(self, **overrides):
        """Minimal backtest config matching paper_champion.yaml defaults."""
        cfg = {
            "strategy_params": {
                "credit_spread": {
                    "direction": "regime_adaptive",
                    "target_dte": 15,
                    "min_dte": 15,
                    "otm_pct": 0.02,
                    "spread_width": 12.0,
                    "profit_target_pct": 0.55,
                    "stop_loss_multiplier": 1.25,
                    "max_risk_pct": 0.085,
                },
                "iron_condor": {
                    "spread_width": 12.0,
                    "max_risk_pct": 0.035,
                },
            },
        }
        for k, v in overrides.items():
            parts = k.split(".")
            d = cfg
            for p in parts[:-1]:
                d = d[p]
            d[parts[-1]] = v
        return cfg

    def _paper_config(self, **overrides):
        """Minimal paper config matching paper_champion.yaml."""
        cfg = {
            "strategy": {
                "direction": "both",
                "target_dte": 15,
                "min_dte": 15,
                "otm_pct": 0.02,
                "spread_width": 12,
                "iron_condor": {
                    "spread_width": 12,
                    "max_risk_pct": 3.5,
                },
            },
            "risk": {
                "max_risk_per_trade": 8.5,
                "profit_target": 55,
                "stop_loss_multiplier": 1.25,
                "drawdown_cb_pct": 40,
                "max_contracts": 25,
            },
        }
        for k, v in overrides.items():
            parts = k.split(".")
            d = cfg
            for p in parts[:-1]:
                if p not in d:
                    d[p] = {}
                d = d[p]
            d[parts[-1]] = v
        return cfg

    def test_matching_configs_pass(self):
        """Identical configs should produce no diffs."""
        diffs, result = check_config_parity(self._bt_config(), self._paper_config())
        assert result == GateResult.PASS
        assert len(diffs) == 0

    def test_spread_width_mismatch_critical(self):
        """spread_width mismatch is CRITICAL — invalidates backtest."""
        paper = self._paper_config()
        paper["strategy"]["spread_width"] = 5  # EXP-503 scenario: $12 → $5
        diffs, result = check_config_parity(self._bt_config(), paper)
        assert result == GateResult.CRITICAL
        assert any(d.field_name == "spread_width" and d.critical for d in diffs)

    def test_max_risk_mismatch_critical(self):
        """max_risk_per_trade mismatch is CRITICAL."""
        paper = self._paper_config()
        paper["risk"]["max_risk_per_trade"] = 12.0  # 8.5% → 12%
        diffs, result = check_config_parity(self._bt_config(), paper)
        assert result == GateResult.CRITICAL
        assert any(d.field_name == "max_risk_per_trade" and d.critical for d in diffs)

    def test_risk_scaling_correct(self):
        """Backtest uses 0.085, paper uses 8.5 — should match with bt_scale=100."""
        diffs, result = check_config_parity(self._bt_config(), self._paper_config())
        assert result == GateResult.PASS

    def test_direction_alias_matching(self):
        """'regime_adaptive' in backtest == 'both' in paper."""
        diffs, result = check_config_parity(self._bt_config(), self._paper_config())
        assert result == GateResult.PASS
        assert not any(d.field_name == "direction" for d in diffs)

    def test_direction_real_mismatch(self):
        """Different direction should be caught."""
        paper = self._paper_config()
        paper["strategy"]["direction"] = "bull_put"
        diffs, result = check_config_parity(self._bt_config(), paper)
        assert result == GateResult.CRITICAL
        assert any(d.field_name == "direction" for d in diffs)

    def test_profit_target_scaling(self):
        """Backtest 0.55 * 100 = 55, paper = 55. Should match."""
        diffs, result = check_config_parity(self._bt_config(), self._paper_config())
        assert not any(d.field_name == "profit_target" for d in diffs)

    def test_profit_target_mismatch(self):
        """Paper changed to 65% profit target."""
        paper = self._paper_config()
        paper["risk"]["profit_target"] = 65
        diffs, result = check_config_parity(self._bt_config(), paper)
        assert result == GateResult.CRITICAL
        assert any(d.field_name == "profit_target" for d in diffs)

    def test_monitored_field_warning(self):
        """IC spread_width 10% off should produce WARNING not CRITICAL."""
        bt = self._bt_config()
        paper = self._paper_config()
        paper["strategy"]["iron_condor"]["spread_width"] = 14  # 12 → 14 = 16.7% off
        diffs, result = check_config_parity(bt, paper)
        assert result == GateResult.WARNING
        assert any(d.field_name == "ic_spread_width" and not d.critical for d in diffs)

    def test_missing_bt_field_skipped(self):
        """Missing field in backtest config should be skipped, not fail."""
        bt = {"strategy_params": {"credit_spread": {}}}
        paper = self._paper_config()
        diffs, result = check_config_parity(bt, paper)
        assert result == GateResult.PASS

    def test_multiple_diffs(self):
        """Multiple simultaneous diffs."""
        paper = self._paper_config()
        paper["strategy"]["spread_width"] = 5
        paper["risk"]["max_risk_per_trade"] = 12.0
        paper["strategy"]["target_dte"] = 30
        diffs, result = check_config_parity(self._bt_config(), paper)
        assert result == GateResult.CRITICAL
        assert len(diffs) >= 3


# ---------------------------------------------------------------------------
# Health score tests
# ---------------------------------------------------------------------------


class TestHealthScore:
    def test_all_pass(self):
        outcomes = [
            GateOutcome("G0", "Registry", GateResult.PASS, "ok"),
            GateOutcome("G1", "State", GateResult.PASS, "ok"),
            GateOutcome("G2", "Fingerprint", GateResult.PASS, "ok"),
        ]
        assert _compute_health_score(outcomes) == 100

    def test_all_fail(self):
        outcomes = [
            GateOutcome("G0", "Registry", GateResult.HALT, "bad"),
            GateOutcome("G1", "State", GateResult.CRITICAL, "bad"),
        ]
        assert _compute_health_score(outcomes) == 0

    def test_mixed(self):
        outcomes = [
            GateOutcome("G0", "Registry", GateResult.PASS, "ok"),
            GateOutcome("G1", "State", GateResult.WARNING, "meh"),
            GateOutcome("G3", "Alpaca", GateResult.CRITICAL, "bad"),
        ]
        score = _compute_health_score(outcomes)
        assert 0 < score < 100

    def test_empty_is_100(self):
        assert _compute_health_score([]) == 100


# ---------------------------------------------------------------------------
# ExperimentAudit tests
# ---------------------------------------------------------------------------


class TestExperimentAudit:
    def test_worst_result_pass(self):
        audit = ExperimentAudit(experiment_id="EXP-TEST")
        audit.gate_outcomes = [
            GateOutcome("G0", "A", GateResult.PASS, "ok"),
            GateOutcome("G1", "B", GateResult.PASS, "ok"),
        ]
        assert audit.worst_result == GateResult.PASS

    def test_worst_result_critical(self):
        audit = ExperimentAudit(experiment_id="EXP-TEST")
        audit.gate_outcomes = [
            GateOutcome("G0", "A", GateResult.PASS, "ok"),
            GateOutcome("G21", "B", GateResult.CRITICAL, "bad"),
            GateOutcome("G1", "C", GateResult.WARNING, "meh"),
        ]
        assert audit.worst_result == GateResult.CRITICAL

    def test_failures_property(self):
        audit = ExperimentAudit(experiment_id="EXP-TEST")
        audit.gate_outcomes = [
            GateOutcome("G0", "A", GateResult.PASS, "ok"),
            GateOutcome("G1", "B", GateResult.BLOCK, "blocked"),
            GateOutcome("G2", "C", GateResult.HALT, "halted"),
        ]
        assert len(audit.failures) == 2

    def test_to_dict(self):
        audit = ExperimentAudit(experiment_id="EXP-TEST")
        audit.gate_outcomes = [
            GateOutcome("G0", "A", GateResult.PASS, "ok"),
        ]
        audit.health_score = 95
        d = audit.to_dict()
        assert d["experiment_id"] == "EXP-TEST"
        assert d["health_score"] == 95
        assert d["pass_count"] == 1


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_get_nested(self):
        d = {"a": {"b": {"c": 42}}}
        assert _get_nested(d, ("a", "b", "c")) == 42
        assert _get_nested(d, ("a", "x")) is None
        assert _get_nested(d, ("z",)) is None

    def test_values_match_exact(self):
        assert _values_match(12, 12, {"tolerance": 0})
        assert not _values_match(12, 5, {"tolerance": 0})

    def test_values_match_scaled(self):
        assert _values_match(0.085, 8.5, {"bt_scale": 100, "tolerance": 0})

    def test_values_match_alias(self):
        assert _values_match(
            "regime_adaptive", "both",
            {"tolerance": 0, "aliases": {"regime_adaptive": "both"}},
        )

    def test_values_match_tolerance_pct(self):
        assert _values_match(12, 13, {"tolerance_pct": 10})  # 7.7% < 10%
        assert not _values_match(12, 14, {"tolerance_pct": 10})  # 14.3% > 10%

    def test_values_match_both_none(self):
        """Both None → True (no comparison possible)."""
        assert _values_match(None, None, {})

    def test_values_match_one_none(self):
        """One side None, other has value → False (drift detected)."""
        assert not _values_match(None, 42, {})
        assert not _values_match(42, None, {})
        assert not _values_match(None, "bull_put", {})
        assert not _values_match(0.085, None, {})

    def test_gate_outcome_to_dict(self):
        o = GateOutcome("G21", "Config Parity", GateResult.CRITICAL, "bad")
        d = o.to_dict()
        assert d["gate_id"] == "G21"
        assert d["result"] == "CRITICAL"


# ---------------------------------------------------------------------------
# Gate 21 integration test (with mock registry)
# ---------------------------------------------------------------------------


class TestGate21Integration:
    def test_no_backtest_config_warns(self):
        registry = {"experiments": {"EXP-TEST": {"status": "paper_trading"}}}
        outcome = _run_gate21_config_parity("EXP-TEST", registry)
        assert outcome.result == GateResult.WARNING
        assert "No backtest config" in outcome.message

    def test_missing_bt_file_warns(self):
        registry = {
            "experiments": {
                "EXP-TEST": {
                    "backtest_config": "configs/nonexistent.json",
                    "paper_config": "configs/paper_champion.yaml",
                },
            },
        }
        outcome = _run_gate21_config_parity("EXP-TEST", registry)
        assert outcome.result == GateResult.WARNING
        assert "not found" in outcome.message


# ---------------------------------------------------------------------------
# Fix 1: Exception handlers → BLOCK for safety-critical gates
# ---------------------------------------------------------------------------


class TestExceptionHandlerSeverity:
    """Exceptions in safety-critical gates must produce BLOCK, not WARNING."""

    def test_gate_exception_produces_block(self):
        """Generic gate exception in audit_experiment → BLOCK for non-G5 gates."""
        registry = {"experiments": {"EXP-TEST": {"status": "active"}}}
        state = {"experiments": {"EXP-TEST": {"status": "active"}}}

        # Patch all gate runners to raise, except G0 and G1 which run normally
        with patch("sentinel.orchestrator._run_gate_fingerprint", side_effect=RuntimeError("boom")), \
             patch("sentinel.orchestrator._record_gate_runs"):
            audit = audit_experiment("EXP-TEST", registry, state, skip_gates=["G3", "G5", "G8", "G9", "G10", "G11", "G12", "G21"])
            g2_outcomes = [o for o in audit.gate_outcomes if o.gate_id == "G2"]
            assert len(g2_outcomes) == 1
            assert g2_outcomes[0].result == GateResult.BLOCK

    def test_g5_exception_stays_warning(self):
        """G5 (advisory certification) exception → WARNING, not BLOCK."""
        registry = {"experiments": {"EXP-TEST": {"status": "active"}}}
        state = {"experiments": {"EXP-TEST": {"status": "active"}}}

        with patch("sentinel.orchestrator._run_gate_certification", side_effect=RuntimeError("boom")), \
             patch("sentinel.orchestrator._record_gate_runs"):
            audit = audit_experiment("EXP-TEST", registry, state, skip_gates=["G2", "G3", "G8", "G9", "G10", "G11", "G12", "G21"])
            g5_outcomes = [o for o in audit.gate_outcomes if o.gate_id == "G5"]
            assert len(g5_outcomes) == 1
            assert g5_outcomes[0].result == GateResult.WARNING


# ---------------------------------------------------------------------------
# Fix 2: audit_all_experiments returns error audit on load failure
# ---------------------------------------------------------------------------


class TestAuditAllLoadFailure:
    """audit_all_experiments must return error audit, not empty list."""

    @patch("sentinel.orchestrator._load_registry", side_effect=FileNotFoundError("registry.json not found"))
    def test_load_failure_returns_error_audit(self, mock_reg):
        results = audit_all_experiments()
        assert len(results) == 1
        assert results[0].experiment_id == "SYSTEM"
        assert results[0].health_score == 0
        assert results[0].halted is True
        assert results[0].worst_result >= GateResult.CRITICAL

    @patch("sentinel.orchestrator._load_registry", side_effect=FileNotFoundError("registry.json not found"))
    def test_load_failure_report_not_all_clear(self, mock_reg):
        """format_audit_report must NOT say 'All Clear' on load failure."""
        results = audit_all_experiments()
        report = format_audit_report(results)
        assert "All Clear" not in report
        assert "HALTED" in report
