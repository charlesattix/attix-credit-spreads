"""
Tests for the Sentinel health-score formula and G3 cadence-aware thresholds.

Covers the bugs found in Phase 1:
  - 24h "score cliff" (G3 -10→-30 plus stale-HC -5 = net -25 at boundary)
  - hard-coded G3 threshold literals divorced from actual cron cadence
  - status==halted short-circuit
  - G3 severity double-deduct vs the staleness penalty
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.cadence import (
    DEFAULT_CADENCE_SECONDS,
    StalenessThresholds,
    expected_cadence_seconds,
    staleness_score_penalty,
)
from web_dashboard.html import _compute_health_score


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _exp_with_age(age_hours: float, status: str = "active") -> dict:
    hc = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    return {"status": status, "last_health_check": hc}


def _g3_for_age(age_h: float) -> dict:
    """Replicate the G3 severity classification used in render_sentinel_page."""
    sev = StalenessThresholds.from_cadence().severity_for_age(age_h)
    return {"G3": {"severity": sev, "detail": f"{age_h:.1f}h ago"}}


# ─────────────────────────────────────────────────────────────────────────────
# Score cliff / continuity
# ─────────────────────────────────────────────────────────────────────────────


def test_score_no_cliff_at_24h_boundary():
    """The old bug produced a -25 step exactly at 24h. Now must be smooth."""
    just_under = _compute_health_score(_exp_with_age(23.99), _g3_for_age(23.99))
    just_over = _compute_health_score(_exp_with_age(24.01), _g3_for_age(24.01))
    assert abs(just_under - just_over) <= 5, (
        f"24h cliff regression: {just_under} → {just_over} "
        f"(delta {just_under - just_over})"
    )


def test_score_curve_monotonic_in_age():
    """Score must be non-increasing as staleness grows (no rebound bumps)."""
    last = 101
    for age_h in range(0, 60):
        score = _compute_health_score(_exp_with_age(age_h), _g3_for_age(age_h))
        assert score <= last, f"Non-monotonic at age={age_h}: prev={last}, now={score}"
        last = score


def test_score_max_step_size_across_age_range():
    """No single hour boundary may drop more than 10 points."""
    prev = _compute_health_score(_exp_with_age(0.0), _g3_for_age(0.0))
    for age_h in [x / 2.0 for x in range(0, 120)]:  # 0..60h, half-hour steps
        cur = _compute_health_score(_exp_with_age(age_h), _g3_for_age(age_h))
        assert prev - cur <= 10, (
            f"Cliff at age={age_h}: {prev} → {cur} (drop {prev - cur})"
        )
        prev = cur


# ─────────────────────────────────────────────────────────────────────────────
# Halt short-circuit
# ─────────────────────────────────────────────────────────────────────────────


def test_score_halted_returns_zero():
    score = _compute_health_score(
        {"status": "halted", "last_health_check": datetime.now(timezone.utc).isoformat()},
        {"G0": {"severity": "ok"}},
    )
    assert score == 0


def test_score_gate_halt_returns_zero():
    score = _compute_health_score(
        _exp_with_age(0.5),
        {"G1": {"severity": "halt", "detail": "halted"}},
    )
    assert score == 0


# ─────────────────────────────────────────────────────────────────────────────
# No double-deduction for staleness
# ─────────────────────────────────────────────────────────────────────────────


def test_score_no_double_deduct_for_staleness():
    """
    Old code deducted both via G3 severity AND a separate stale-HC block. With
    only G3 stale and no other gate issues, the score must reflect the SINGLE
    smooth staleness penalty — not warn(-10) + stale(-5) = -15 / crit(-30) +
    stale(-5) = -35.
    """
    age_h = 13.0  # in old "warning" band
    score = _compute_health_score(_exp_with_age(age_h), _g3_for_age(age_h))
    expected = 100 - staleness_score_penalty(age_h)
    assert score == expected, (
        f"G3 must not double-deduct: got {score}, expected single penalty "
        f"to give {expected}"
    )


def test_score_unrelated_gate_critical_combines_with_staleness():
    """A non-G3 critical gate still deducts -30 in addition to the staleness penalty."""
    age_h = 0.5
    gates = {
        **_g3_for_age(age_h),
        "G2": {"severity": "critical", "detail": "config drift"},
    }
    score = _compute_health_score(_exp_with_age(age_h), gates)
    assert score == 100 - 30 - staleness_score_penalty(age_h)


# ─────────────────────────────────────────────────────────────────────────────
# Cadence module
# ─────────────────────────────────────────────────────────────────────────────


def test_default_cadence_is_hourly():
    assert DEFAULT_CADENCE_SECONDS == 3600


def test_expected_cadence_respects_env(monkeypatch):
    monkeypatch.setenv("SENTINEL_CADENCE_SECONDS", "86400")
    assert expected_cadence_seconds() == 86400


def test_expected_cadence_falls_back_on_garbage_env(monkeypatch):
    monkeypatch.setenv("SENTINEL_CADENCE_SECONDS", "not-a-number")
    assert expected_cadence_seconds() == DEFAULT_CADENCE_SECONDS


def test_expected_cadence_falls_back_on_zero(monkeypatch):
    """A misconfigured 0 must NOT silently zero-out staleness detection."""
    monkeypatch.setenv("SENTINEL_CADENCE_SECONDS", "0")
    assert expected_cadence_seconds() == DEFAULT_CADENCE_SECONDS


def test_g3_thresholds_default_cadence():
    """Hourly cadence: ok <2h, warn 2-13h, crit 13-48h, halt >=48h."""
    thr = StalenessThresholds.from_cadence(3600)
    assert thr.severity_for_age(0.5) == "ok"
    assert thr.severity_for_age(1.5) == "ok"
    assert thr.severity_for_age(2.5) == "warning"
    assert thr.severity_for_age(12.5) == "warning"
    assert thr.severity_for_age(13.5) == "critical"
    assert thr.severity_for_age(47.5) == "critical"
    assert thr.severity_for_age(48.5) == "halt"


def test_g3_thresholds_respect_custom_cadence():
    """Daily cadence (86400s): ok <25h, warn 25-36h, crit 36-48h, halt >=48h."""
    thr = StalenessThresholds.from_cadence(86400)
    assert thr.severity_for_age(24.5) == "ok"       # cadence + 0.5h
    assert thr.severity_for_age(26.0) == "warning"  # cadence + 2h
    assert thr.severity_for_age(35.5) == "warning"  # just under cadence + 12h
    assert thr.severity_for_age(40.0) == "critical"  # past warning band
    # halt remains tied to 48h (hard cap)
    assert thr.severity_for_age(50.0) == "halt"


# ─────────────────────────────────────────────────────────────────────────────
# Specific old-bug repros
# ─────────────────────────────────────────────────────────────────────────────


def test_score_at_18h_reflects_warning_band_only():
    """At 18h with no other issues, score should be ≈90 (matching the
    Phase-1 observation) — not 65."""
    score = _compute_health_score(_exp_with_age(18.0), _g3_for_age(18.0))
    assert 80 <= score <= 95


def test_score_at_25h_no_longer_drops_below_70():
    """25h used to drop to 65 (cliff). With smooth gradient it should stay ≥70."""
    score = _compute_health_score(_exp_with_age(25.0), _g3_for_age(25.0))
    assert score >= 70, f"25h still has cliff: score={score}"
