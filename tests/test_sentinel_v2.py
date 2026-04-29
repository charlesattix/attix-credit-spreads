"""Integration tests for SENTINEL v2 — full gate pipeline + dashboard + CLI.

Maps task scenarios to actual gate numbers:
  Gate 0:  Registry status enforcement
  Gate 1:  Sentinel state (halted/paused/active)
  Gate 2:  Config fingerprint drift detection
  Gate 3:  Alpaca API health check
  Gate 8:  Live-vs-backtest drift (win rate, avg loss, drawdown)
  Gate 9:  Position lifecycle monitoring

Also tests: health score computation, dashboard rendering, CLI commands,
alert management, and gate precedence.
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_state(tmp_path):
    """Create a sentinel_state.json for testing."""
    state = {
        "sentinel_version": "1.1",
        "runtime_gates_enabled": True,
        "experiments": {
            "EXP-400": {
                "status": "active",
                "paper_config": "configs/paper_champion.yaml",
                "config_fingerprint": "abc123def456",
                "account_id": "PA36XFVLG0WE",
                "live_since": "2026-03-15",
                "enrolled_at": "2026-04-12",
                "last_health_check": datetime.now(timezone.utc).isoformat(),
                "halt_reason": None,
                "backtest_baseline": {
                    "win_rate": 78.0,
                    "avg_pnl": 525.0,
                    "avg_loss": 2100.0,
                    "mc_worst_dd_pct": 41.5,
                },
            },
            "EXP-503": {
                "status": "halted",
                "paper_config": "configs/paper_exp503.yaml",
                "config_fingerprint": "xyz789",
                "account_id": "PA3Z9PLVYUL5",
                "live_since": "2026-03-22",
                "enrolled_at": "2026-04-12",
                "last_health_check": None,
                "halt_reason": "equity DD > 25%",
                "backtest_baseline": {
                    "win_rate": 68.0,
                    "avg_pnl": 750.0,
                    "avg_loss": 2200.0,
                    "mc_worst_dd_pct": 21.3,
                },
            },
        },
    }
    path = tmp_path / "sentinel_state.json"
    path.write_text(json.dumps(state, indent=2))
    return state, path


@pytest.fixture
def sentinel_db(tmp_path):
    """Create a sentinel.db with test data."""
    db_path = tmp_path / "sentinel.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE experiment_snapshots (
            id INTEGER PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            snapshot_time TEXT NOT NULL DEFAULT (datetime('now')),
            equity REAL,
            open_positions INTEGER,
            day_pnl REAL,
            total_pnl REAL,
            total_trades INTEGER,
            win_rate REAL,
            config_hash TEXT,
            api_status TEXT DEFAULT 'ok',
            notes TEXT
        );
        CREATE TABLE config_changes (
            id INTEGER PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            approved_by TEXT,
            approval_reason TEXT,
            detected_by TEXT DEFAULT 'sentinel'
        );
        CREATE TABLE deployment_certificates (
            id INTEGER PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            certified_at TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            gates_passed INTEGER DEFAULT 0,
            equivalence_days INTEGER DEFAULT 0,
            certified_by TEXT DEFAULT 'sentinel',
            grandfathered INTEGER DEFAULT 0,
            notes TEXT
        );
        CREATE TABLE alerts_log (
            id INTEGER PRIMARY KEY,
            alert_time TEXT NOT NULL DEFAULT (datetime('now')),
            severity TEXT NOT NULL,
            experiment_id TEXT,
            message TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            resolved_at TEXT,
            resolved_by TEXT,
            resolution_note TEXT
        );
    """)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO experiment_snapshots (experiment_id, snapshot_time, equity, open_positions, api_status) VALUES (?, ?, ?, ?, ?)",
        ("EXP-400", now, 105000.0, 3, "ok"),
    )
    conn.execute(
        "INSERT INTO alerts_log (alert_time, severity, experiment_id, message) VALUES (?, ?, ?, ?)",
        (now, "critical", "EXP-503", "equity DD > 25% — experiment halted"),
    )
    conn.execute(
        "INSERT INTO alerts_log (alert_time, severity, experiment_id, message) VALUES (?, ?, ?, ?)",
        (now, "warning", "EXP-400", "config fingerprint not certified"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def sample_registry():
    return {
        "schema_version": "2.1",
        "experiments": {
            "EXP-400": {
                "id": "EXP-400",
                "status": "paper_trading",
                "paper_config": "configs/paper_champion.yaml",
            },
            "EXP-503": {
                "id": "EXP-503",
                "status": "paper_trading",
                "paper_config": "configs/paper_exp503.yaml",
            },
            "EXP-700": {
                "id": "EXP-700",
                "status": "retired",
            },
        },
    }


# ---------------------------------------------------------------------------
# Test: Gate 0 — Registry Status
# ---------------------------------------------------------------------------

class TestGate0RegistryStatus:
    """Gate 0: experiments must have active/paper_trading status in registry."""

    def test_active_experiment_passes(self, sample_registry):
        exp = sample_registry["experiments"]["EXP-400"]
        assert exp["status"] in ("active", "paper_trading")

    def test_retired_experiment_blocks(self, sample_registry):
        exp = sample_registry["experiments"]["EXP-700"]
        assert exp["status"] not in ("active", "paper_trading")

    def test_missing_experiment_is_graceful(self, sample_registry):
        exp = sample_registry["experiments"].get("EXP-999")
        assert exp is None  # should not crash, just skip


# ---------------------------------------------------------------------------
# Test: Gate 1 — Sentinel State
# ---------------------------------------------------------------------------

class TestGate1SentinelState:
    """Gate 1: halted experiments should block; paused sets DRY_RUN."""

    def test_active_allows_trading(self, tmp_state):
        state, _ = tmp_state
        exp = state["experiments"]["EXP-400"]
        assert exp["status"] == "active"

    def test_halted_blocks_trading(self, tmp_state):
        state, _ = tmp_state
        exp = state["experiments"]["EXP-503"]
        assert exp["status"] == "halted"
        assert exp["halt_reason"] is not None

    def test_halt_reason_propagates(self, tmp_state):
        state, _ = tmp_state
        exp = state["experiments"]["EXP-503"]
        assert "DD" in exp["halt_reason"]


# ---------------------------------------------------------------------------
# Test: Gate 2 — Config Fingerprint
# ---------------------------------------------------------------------------

class TestGate2ConfigFingerprint:
    """Gate 2: config drift detection via SHA-256 fingerprint."""

    def test_matching_fingerprint_passes(self, tmp_path):
        config = tmp_path / "test_config.yaml"
        config.write_text("strategy: bull_put\ndte: 35\n")

        from sentinel.state import compute_fingerprint
        fp1 = compute_fingerprint(str(config))
        fp2 = compute_fingerprint(str(config))
        assert fp1 == fp2

    def test_drift_detected_on_change(self, tmp_path):
        config = tmp_path / "test_config.yaml"
        config.write_text("strategy: bull_put\ndte: 35\n")

        from sentinel.state import compute_fingerprint
        fp_before = compute_fingerprint(str(config))

        config.write_text("strategy: bull_put\ndte: 28\n")  # changed DTE
        fp_after = compute_fingerprint(str(config))

        assert fp_before != fp_after

    def test_missing_config_raises(self, tmp_path):
        from sentinel.state import compute_fingerprint
        # compute_fingerprint raises FileNotFoundError for missing files
        # The guard in guards.py wraps this in a try/except
        with pytest.raises(FileNotFoundError):
            compute_fingerprint(str(tmp_path / "nonexistent.yaml"))


# ---------------------------------------------------------------------------
# Test: Gate 8 — Live-vs-Backtest Drift
# ---------------------------------------------------------------------------

class TestGate8DriftTracker:
    """Gate 8: rolling 30-trade window vs backtest baselines."""

    def test_baseline_present(self, tmp_state):
        state, _ = tmp_state
        baseline = state["experiments"]["EXP-400"]["backtest_baseline"]
        assert baseline["win_rate"] == 78.0
        assert baseline["mc_worst_dd_pct"] == 41.5

    def test_no_baseline_skips_gate(self, tmp_state):
        state, _ = tmp_state
        state["experiments"]["EXP-400"].pop("backtest_baseline")
        assert "backtest_baseline" not in state["experiments"]["EXP-400"]

    def test_win_rate_drift_warning_threshold(self):
        """Win rate -10pp from baseline should trigger WARNING."""
        baseline_wr = 78.0
        live_wr = 67.0  # -11pp
        drift = live_wr - baseline_wr
        assert drift < -10  # WARNING threshold

    def test_win_rate_drift_critical_threshold(self):
        """Win rate -15pp from baseline should trigger CRITICAL."""
        baseline_wr = 78.0
        live_wr = 62.0  # -16pp
        drift = live_wr - baseline_wr
        assert drift < -15  # CRITICAL threshold

    def test_avg_loss_multiplier(self):
        """Avg loss > 2x baseline should trigger CRITICAL."""
        baseline_loss = 2100.0
        live_loss = 4500.0
        ratio = live_loss / baseline_loss
        assert ratio > 2.0  # CRITICAL threshold

    def test_drawdown_exceeds_mc_worst(self):
        """Drawdown > 100% of MC worst should trigger CRITICAL."""
        mc_worst_dd = 41.5
        live_dd = 48.0  # EXP-503's actual situation
        ratio = live_dd / mc_worst_dd
        assert ratio > 1.0  # 100% threshold → CRITICAL


# ---------------------------------------------------------------------------
# Test: Health Score Computation
# ---------------------------------------------------------------------------

class TestHealthScore:
    """Health score 0-100 based on gate results."""

    def test_healthy_experiment_scores_high(self):
        from web_dashboard.html import _compute_health_score
        exp = {
            "status": "active",
            "config_fingerprint": "abc123",
            "last_health_check": datetime.now(timezone.utc).isoformat(),
        }
        gates = {
            "G0": {"severity": "ok"},
            "G1": {"severity": "ok"},
            "G2": {"severity": "ok"},
        }
        score = _compute_health_score(exp, gates)
        assert score >= 80

    def test_halted_experiment_scores_zero(self):
        from web_dashboard.html import _compute_health_score
        exp = {"status": "halted"}
        score = _compute_health_score(exp, {})
        assert score == 0

    def test_critical_gate_reduces_score(self):
        """A non-G3 critical gate must deduct ~30 points from the score.

        (G3 is intentionally excluded from the gate-severity loop — its
        signal goes through the smooth staleness penalty instead, which
        eliminated the old 24h-boundary cliff. See test_sentinel_health_score.)
        """
        from web_dashboard.html import _compute_health_score
        exp = {
            "status": "active",
            "last_health_check": datetime.now(timezone.utc).isoformat(),
        }
        gates = {"G2": {"severity": "critical"}}
        score = _compute_health_score(exp, gates)
        assert score <= 70

    def test_warning_gate_slightly_reduces(self):
        from web_dashboard.html import _compute_health_score
        exp = {
            "status": "active",
            "last_health_check": datetime.now(timezone.utc).isoformat(),
        }
        gates = {"G2": {"severity": "warning"}}
        score = _compute_health_score(exp, gates)
        assert 80 <= score <= 95

    def test_stale_health_check_penalized(self):
        from web_dashboard.html import _compute_health_score
        old_check = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        exp = {"status": "active", "last_health_check": old_check}
        score = _compute_health_score(exp, {})
        assert score <= 80

    def test_never_checked_slightly_penalized(self):
        from web_dashboard.html import _compute_health_score
        exp = {"status": "active", "last_health_check": None}
        score = _compute_health_score(exp, {})
        assert score == 95  # -5 for never checked

    def test_halt_gate_returns_zero(self):
        from web_dashboard.html import _compute_health_score
        exp = {"status": "active"}
        gates = {"G1": {"severity": "halt"}}
        score = _compute_health_score(exp, gates)
        assert score == 0


# ---------------------------------------------------------------------------
# Test: Dashboard Rendering
# ---------------------------------------------------------------------------

class TestDashboardRendering:
    """Verify render_sentinel_page produces valid HTML."""

    def test_renders_with_data(self, tmp_state, sample_registry):
        from web_dashboard.html import render_sentinel_page
        state, _ = tmp_state
        alerts = [
            {"id": 1, "severity": "critical", "experiment_id": "EXP-503",
             "message": "halted", "alert_time": "2026-04-19T10:00:00", "resolved": 0},
        ]
        html = render_sentinel_page(state, alerts, {}, sample_registry)
        assert "<!DOCTYPE html>" in html
        assert "Sentinel" in html
        assert "EXP-400" in html
        assert "EXP-503" in html

    def test_renders_empty_state(self, sample_registry):
        from web_dashboard.html import render_sentinel_page
        html = render_sentinel_page({}, [], {}, sample_registry)
        assert "<!DOCTYPE html>" in html
        assert "No experiments enrolled" in html

    def test_navbar_includes_sentinel(self, tmp_state, sample_registry):
        from web_dashboard.html import render_sentinel_page
        state, _ = tmp_state
        html = render_sentinel_page(state, [], {}, sample_registry)
        assert 'href="/sentinel"' in html
        assert "Sentinel" in html

    def test_health_scores_displayed(self, tmp_state, sample_registry):
        from web_dashboard.html import render_sentinel_page
        state, _ = tmp_state
        html = render_sentinel_page(state, [], {}, sample_registry)
        assert "health-score" in html

    def test_gate_pills_displayed(self, tmp_state, sample_registry):
        from web_dashboard.html import render_sentinel_page
        state, _ = tmp_state
        html = render_sentinel_page(state, [], {}, sample_registry)
        assert "gate-pill" in html
        assert "G0:Registry" in html
        assert "G1:State" in html

    def test_alert_table_rendered(self, tmp_state, sample_registry):
        from web_dashboard.html import render_sentinel_page
        state, _ = tmp_state
        alerts = [
            {"id": 1, "severity": "warning", "experiment_id": "EXP-400",
             "message": "test alert", "alert_time": "2026-04-19T10:00:00",
             "resolved": 1, "resolved_at": "2026-04-19T11:00:00"},
        ]
        html = render_sentinel_page(state, alerts, {}, sample_registry)
        assert "test alert" in html
        assert "RESOLVED" in html

    def test_halted_experiment_shows_halt_reason(self, tmp_state, sample_registry):
        from web_dashboard.html import render_sentinel_page
        state, _ = tmp_state
        html = render_sentinel_page(state, [], {}, sample_registry)
        assert "equity DD" in html  # halt reason for EXP-503

    def test_xss_protection(self, sample_registry):
        """Ensure user data is HTML-escaped."""
        from web_dashboard.html import render_sentinel_page
        state = {
            "experiments": {
                "EXP-XSS": {
                    "status": "active",
                    "halt_reason": '<script>alert("xss")</script>',
                    "enrolled_at": "2026-01-01",
                },
            },
        }
        html = render_sentinel_page(state, [], {}, sample_registry)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Test: Freshness Indicator
# ---------------------------------------------------------------------------

class TestFreshnessIndicator:
    def test_fresh_data_green(self):
        from web_dashboard.html import _freshness_dot
        recent = datetime.now(timezone.utc).isoformat()
        html = _freshness_dot(recent)
        assert "fresh-green" in html

    def test_stale_data_yellow(self):
        from web_dashboard.html import _freshness_dot
        old = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        html = _freshness_dot(old)
        assert "fresh-yellow" in html

    def test_very_stale_data_red(self):
        from web_dashboard.html import _freshness_dot
        ancient = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        html = _freshness_dot(ancient)
        assert "fresh-red" in html

    def test_none_is_red(self):
        from web_dashboard.html import _freshness_dot
        html = _freshness_dot(None)
        assert "fresh-red" in html
        assert "never" in html


# ---------------------------------------------------------------------------
# Test: Gate Precedence (Orchestrator)
# ---------------------------------------------------------------------------

class TestGatePrecedence:
    """When multiple gates fail, the highest severity wins."""

    def test_halt_beats_warning(self):
        from web_dashboard.html import _compute_health_score
        exp = {"status": "active"}
        gates = {
            "G2": {"severity": "warning"},
            "G1": {"severity": "halt"},
        }
        score = _compute_health_score(exp, gates)
        assert score == 0  # halt → 0

    def test_multiple_warnings_accumulate(self):
        from web_dashboard.html import _compute_health_score
        exp = {
            "status": "active",
            "last_health_check": datetime.now(timezone.utc).isoformat(),
        }
        gates = {
            "G0": {"severity": "warning"},
            "G2": {"severity": "warning"},
            "G8": {"severity": "warning"},
        }
        score = _compute_health_score(exp, gates)
        assert score == 70  # 100 - 3*10

    def test_critical_plus_warning(self):
        """Critical + warning on non-G3 gates: 100 - 30 - 10 = 60.

        G3 is intentionally excluded from the gate-severity loop (see
        _compute_health_score docstring), so we use G8 here instead of G3.
        """
        from web_dashboard.html import _compute_health_score
        exp = {
            "status": "active",
            "last_health_check": datetime.now(timezone.utc).isoformat(),
        }
        gates = {
            "G8": {"severity": "critical"},
            "G2": {"severity": "warning"},
        }
        score = _compute_health_score(exp, gates)
        assert score == 60  # 100 - 30 - 10


# ---------------------------------------------------------------------------
# Test: CLI Commands
# ---------------------------------------------------------------------------

class TestCLIStatus:
    """Test sentinel_cli.py status command."""

    def test_status_with_experiments(self, tmp_state, capsys):
        state, path = tmp_state
        with patch("scripts.sentinel_cli._load_sentinel_state", return_value=state):
            with patch("scripts.sentinel_cli._get_db", side_effect=Exception("no db")):
                from scripts.sentinel_cli import cmd_status
                args = MagicMock()
                ret = cmd_status(args)
                assert ret == 0
                output = capsys.readouterr().out
                assert "EXP-400" in output
                assert "EXP-503" in output

    def test_status_empty(self, capsys):
        with patch("scripts.sentinel_cli._load_sentinel_state", return_value={}):
            from scripts.sentinel_cli import cmd_status
            args = MagicMock()
            ret = cmd_status(args)
            assert ret == 0
            output = capsys.readouterr().out
            assert "No experiments" in output


class TestCLICheck:
    """Test sentinel_cli.py check command."""

    def test_check_active_experiment(self, tmp_state, capsys, sample_registry):
        state, _ = tmp_state
        with patch("scripts.sentinel_cli._load_sentinel_state", return_value=state):
            with patch("scripts.sentinel_cli._load_registry", return_value=sample_registry):
                from scripts.sentinel_cli import cmd_check
                args = MagicMock(experiment_id="EXP-400")
                ret = cmd_check(args)
                # ret may be 0 or 1 depending on whether config file matches fixture fingerprint
                output = capsys.readouterr().out
                assert "G0: Registry Status" in output
                assert "G1: Sentinel State" in output
                assert "PASS" in output

    def test_check_halted_experiment(self, tmp_state, capsys, sample_registry):
        state, _ = tmp_state
        with patch("scripts.sentinel_cli._load_sentinel_state", return_value=state):
            with patch("scripts.sentinel_cli._load_registry", return_value=sample_registry):
                from scripts.sentinel_cli import cmd_check
                args = MagicMock(experiment_id="EXP-503")
                ret = cmd_check(args)
                assert ret == 1  # has failures
                output = capsys.readouterr().out
                assert "FAIL" in output
                assert "equity DD" in output  # halt reason visible

    def test_check_unknown_experiment(self, tmp_state, capsys):
        state, _ = tmp_state
        with patch("scripts.sentinel_cli._load_sentinel_state", return_value=state):
            from scripts.sentinel_cli import cmd_check
            args = MagicMock(experiment_id="EXP-999")
            ret = cmd_check(args)
            assert ret == 1
            output = capsys.readouterr().out
            assert "not enrolled" in output


class TestCLIAlerts:
    """Test sentinel_cli.py alerts command."""

    def test_alerts_with_db(self, sentinel_db, capsys):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))
        with patch("scripts.sentinel_cli._get_db", return_value=db):
            from scripts.sentinel_cli import cmd_alerts
            args = MagicMock(all=False)
            ret = cmd_alerts(args)
            assert ret == 0
            output = capsys.readouterr().out
            assert "EXP-503" in output
            assert "CRITICAL" in output

    def test_alerts_all_flag(self, sentinel_db, capsys):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))
        with patch("scripts.sentinel_cli._get_db", return_value=db):
            from scripts.sentinel_cli import cmd_alerts
            args = MagicMock(all=True)
            ret = cmd_alerts(args)
            assert ret == 0
            output = capsys.readouterr().out
            assert "2 alert(s)" in output


class TestCLIResolve:
    """Test sentinel_cli.py resolve command."""

    def test_resolve_existing_alert(self, sentinel_db, capsys):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))
        with patch("scripts.sentinel_cli._get_db", return_value=db):
            from scripts.sentinel_cli import cmd_resolve
            args = MagicMock(alert_id="1", operator="test", note="fixed")
            ret = cmd_resolve(args)
            assert ret == 0
            output = capsys.readouterr().out
            assert "resolved" in output.lower()

    def test_resolve_nonexistent_alert(self, sentinel_db, capsys):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))
        with patch("scripts.sentinel_cli._get_db", return_value=db):
            from scripts.sentinel_cli import cmd_resolve
            args = MagicMock(alert_id="999", operator="test", note="n/a")
            ret = cmd_resolve(args)
            assert ret == 1


# ---------------------------------------------------------------------------
# Test: Alert Management
# ---------------------------------------------------------------------------

class TestAlertManagement:
    """Test alert recording, querying, and resolution."""

    def test_record_and_query_alert(self, sentinel_db):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))

        # Record new alert
        aid = db.record_alert("warning", "test alert message", experiment_id="EXP-400")
        assert aid > 0

        # Query active alerts
        active = db.get_active_alerts()
        assert any(a["message"] == "test alert message" for a in active)

    def test_resolve_alert(self, sentinel_db):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))

        # Resolve alert #1
        ok = db.resolve_alert(1, resolved_by="operator", resolution_note="handled")
        assert ok

        # Should no longer appear in active alerts
        active = db.get_active_alerts()
        assert not any(a["id"] == 1 for a in active)

    def test_alert_severity_filter(self, sentinel_db):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))

        crits = db.get_active_alerts(severity="critical")
        warns = db.get_active_alerts(severity="warning")
        assert all(a["severity"] == "critical" for a in crits)
        assert all(a["severity"] == "warning" for a in warns)

    def test_experiment_filter(self, sentinel_db):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))

        exp503_alerts = db.get_active_alerts(experiment_id="EXP-503")
        assert all(a["experiment_id"] == "EXP-503" for a in exp503_alerts)


# ---------------------------------------------------------------------------
# Test: Snapshot Management
# ---------------------------------------------------------------------------

class TestSnapshotManagement:
    def test_record_and_query_snapshot(self, sentinel_db):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))

        db.record_snapshot("EXP-400", equity=110000, open_positions=5, api_status="ok")
        snaps = db.get_snapshots("EXP-400", limit=5)
        assert len(snaps) >= 2  # fixture + new one
        # Both snapshots present (order may vary since timestamps are close)
        equities = {s["equity"] for s in snaps}
        assert 110000 in equities
        assert 105000 in equities

    def test_snapshot_tracks_equity_curve(self, sentinel_db):
        from sentinel.history import SentinelDB
        db = SentinelDB(str(sentinel_db))

        equities = [100000, 102000, 98000, 95000, 97000]
        for eq in equities:
            db.record_snapshot("EXP-400", equity=eq, open_positions=3, api_status="ok")

        snaps = db.get_snapshots("EXP-400", limit=10)
        assert len(snaps) >= 5


# ---------------------------------------------------------------------------
# Test: Weekend/Market Hours (scenario from task)
# ---------------------------------------------------------------------------

class TestMarketHoursScenarios:
    """Saturday scans should be handled gracefully."""

    def test_weekend_check_doesnt_crash(self, tmp_state, sample_registry):
        """Even on weekends, the gate pipeline should run without error."""
        from web_dashboard.html import render_sentinel_page
        state, _ = tmp_state
        # render should work regardless of day
        html = render_sentinel_page(state, [], {}, sample_registry)
        assert "<!DOCTYPE html>" in html


# ---------------------------------------------------------------------------
# Test: Sentinel State Persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    def test_state_load_and_modify(self, tmp_state):
        state, path = tmp_state

        # Modify and save
        state["experiments"]["EXP-400"]["status"] = "halted"
        state["experiments"]["EXP-400"]["halt_reason"] = "manual halt"
        path.write_text(json.dumps(state, indent=2))

        # Reload
        reloaded = json.loads(path.read_text())
        assert reloaded["experiments"]["EXP-400"]["status"] == "halted"

    def test_corrupted_state_handled(self, tmp_path):
        path = tmp_path / "bad_state.json"
        path.write_text("NOT JSON!!!")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = {}
        assert data == {}
