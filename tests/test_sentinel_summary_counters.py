"""
Tests for the Sentinel summary counter aggregation.

Old behaviour aggregated by score band, which double-laundered the score
cliff and produced lying counters (e.g. an experiment with two critical
gates that scored 65/100 was counted as a "warning"). Counters must now
aggregate per-gate severity.
"""

from __future__ import annotations

from web_dashboard.html import _classify_experiment_severity


def test_halt_status_overrides_everything():
    bucket = _classify_experiment_severity("halted", {"G1": {"severity": "halt"}})
    assert bucket == "halted"


def test_halted_status_counts_as_halted_not_critical():
    """A halted experiment with critical gates still counts as halted only."""
    bucket = _classify_experiment_severity(
        "halted",
        {
            "G2": {"severity": "critical"},
            "G3": {"severity": "critical"},
        },
    )
    assert bucket == "halted"


def test_halt_severity_gate_alone_promotes_to_halted():
    """No status==halted but a halt severity gate → still halted."""
    bucket = _classify_experiment_severity(
        "active", {"G1": {"severity": "halt"}}
    )
    assert bucket == "halted"


def test_critical_gate_counts_as_critical():
    bucket = _classify_experiment_severity(
        "active",
        {"G0": {"severity": "ok"}, "G2": {"severity": "critical"}},
    )
    assert bucket == "critical"


def test_warning_gate_counts_as_warning():
    bucket = _classify_experiment_severity(
        "active", {"G3": {"severity": "warning"}}
    )
    assert bucket == "warning"


def test_all_ok_counts_as_ok():
    bucket = _classify_experiment_severity(
        "active",
        {"G0": {"severity": "ok"}, "G3": {"severity": "ok"}},
    )
    assert bucket == "ok"


def test_critical_beats_warning():
    bucket = _classify_experiment_severity(
        "active",
        {"G3": {"severity": "warning"}, "G2": {"severity": "critical"}},
    )
    assert bucket == "critical"


def test_halt_beats_critical():
    bucket = _classify_experiment_severity(
        "active",
        {"G2": {"severity": "critical"}, "G1": {"severity": "halt"}},
    )
    assert bucket == "halted"


def test_classifier_orthogonal_to_score():
    """
    The whole point: counters cannot be derived from score bands. Two gate
    sets that yield identical buckets must classify the same regardless of
    what the score function returns.
    """
    a = _classify_experiment_severity("active", {"G2": {"severity": "critical"}})
    b = _classify_experiment_severity(
        "active",
        {"G2": {"severity": "critical"}, "G0": {"severity": "ok"}},
    )
    assert a == b == "critical"


def test_missing_severity_field_defaults_to_ok():
    """A malformed gate entry with no severity must not crash and must not
    masquerade as critical."""
    bucket = _classify_experiment_severity("active", {"G0": {}})
    assert bucket == "ok"
