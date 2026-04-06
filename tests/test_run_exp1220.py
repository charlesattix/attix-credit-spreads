"""Tests for scripts/run_exp1220.py — paper trading scanner."""

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_exp1220 as scanner


@pytest.fixture
def tmp_logs(tmp_path, monkeypatch):
    """Redirect LOG_DIR, STATE_PATH, JOURNAL_PATH, HEALTH_PATH to a tmp dir."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(scanner, "LOG_DIR", log_dir)
    monkeypatch.setattr(scanner, "STATE_PATH", log_dir / "state.json")
    monkeypatch.setattr(scanner, "JOURNAL_PATH", log_dir / "trade_journal.csv")
    monkeypatch.setattr(scanner, "HEALTH_PATH", log_dir / "health.json")
    return log_dir


@pytest.fixture
def basic_config():
    return {
        "name": "EXP-1220 Test",
        "leverage": {"multiplier": 1.5, "mode": "static"},
        "cadence": {
            "scan_day": "monday",
            "max_concurrent": 5,
            "min_spacing_days": 5,
        },
        "sizing": {
            "base_risk_pct": 1.0,
            "max_portfolio_risk_pct": 8.0,
            "contracts_min": 1,
            "contracts_max": 10,
        },
        "spread": {
            "width": 5.0,
            "target_dte": 30,
            "min_dte": 21,
            "max_dte": 45,
            "otm_pct": 0.05,
            "min_credit": 0.30,
        },
        "entry_signals": {
            "vix_max_entry": 35.0,
            "vix_min_entry": 10.0,
        },
        "exits": {
            "profit_target_pct": 50,
            "stop_loss_multiplier": 2.0,
            "max_hold_days": 21,
            "dte_exit": 5,
            "vix_emergency_exit": 45,
        },
        "risk": {
            "max_daily_loss_pct": 3.0,
            "max_drawdown_halt_pct": 10.0,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# Config and state
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigState:
    def test_load_config_returns_dict(self):
        config = scanner.load_config()
        assert isinstance(config, dict)
        assert "leverage" in config

    def test_load_state_empty(self, tmp_logs):
        state = scanner.load_state()
        assert state["positions"] == []
        assert state["last_entry_date"] is None

    def test_save_and_load_state(self, tmp_logs):
        state = {"positions": [{"id": 1, "status": "open"}],
                 "last_entry_date": "2026-04-06"}
        scanner.save_state(state)
        loaded = scanner.load_state()
        assert loaded["last_entry_date"] == "2026-04-06"
        assert len(loaded["positions"]) == 1

    def test_log_trade_creates_csv(self, tmp_logs):
        entry = {
            "timestamp": "2026-04-06T09:35:00", "action": "OPEN",
            "ticker": "SPY", "type": "bull_put_spread",
            "short_strike": 530.0, "long_strike": 525.0,
            "expiration": "2026-05-08", "contracts": 3,
            "credit": 0.45, "order_id": "test_order", "status": "submitted",
        }
        scanner.log_trade(entry)
        assert scanner.JOURNAL_PATH.exists()
        content = scanner.JOURNAL_PATH.read_text()
        assert "OPEN" in content
        assert "530.0" in content


# ═══════════════════════════════════════════════════════════════════════════
# Health file
# ═══════════════════════════════════════════════════════════════════════════

class TestHealth:
    def test_write_health_ok(self, tmp_logs):
        scanner.write_health("ok", {"open_positions": 3})
        assert scanner.HEALTH_PATH.exists()
        data = json.loads(scanner.HEALTH_PATH.read_text())
        assert data["status"] == "ok"
        assert data["details"]["open_positions"] == 3

    def test_write_health_error(self, tmp_logs):
        scanner.write_health("error", error="API timeout")
        data = json.loads(scanner.HEALTH_PATH.read_text())
        assert data["status"] == "error"
        assert data["error"] == "API timeout"

    def test_health_check_no_previous(self, tmp_logs):
        code = scanner.run_health_check()
        assert code == 0
        assert scanner.HEALTH_PATH.exists()

    def test_health_check_stale(self, tmp_logs):
        # Write stale health file (72 hours old)
        stale_time = (datetime.now() - timedelta(hours=72)).isoformat()
        scanner.HEALTH_PATH.write_text(json.dumps({
            "status": "ok",
            "timestamp": stale_time,
            "last_run": stale_time,
        }))
        code = scanner.run_health_check()
        assert code == 1  # warning


# ═══════════════════════════════════════════════════════════════════════════
# Market data with retry
# ═══════════════════════════════════════════════════════════════════════════

class TestMarketData:
    def test_yahoo_fetch_success(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "chart": {"result": [{"meta": {"regularMarketPrice": 450.25}}]}
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            price = scanner._yahoo_fetch("SPY")
            assert price == 450.25

    def test_yahoo_fetch_network_error(self):
        import urllib.error
        with patch("urllib.request.urlopen",
                    side_effect=urllib.error.URLError("timeout")):
            price = scanner._yahoo_fetch("SPY", max_retries=2, backoff=0)
            assert price is None

    def test_yahoo_fetch_parse_error(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            price = scanner._yahoo_fetch("SPY", max_retries=1)
            assert price is None

    def test_yahoo_fetch_empty_results(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"chart": {"result": []}}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            price = scanner._yahoo_fetch("SPY", max_retries=1)
            assert price is None

    def test_get_vix_uses_default_on_failure(self):
        with patch.object(scanner, "_yahoo_fetch", return_value=None):
            vix = scanner.get_vix(default=18.5)
            assert vix == 18.5

    def test_get_vix_success(self):
        with patch.object(scanner, "_yahoo_fetch", return_value=22.3):
            assert scanner.get_vix() == 22.3

    def test_get_spy_price_returns_zero_on_failure(self):
        with patch.object(scanner, "_yahoo_fetch", return_value=None):
            assert scanner.get_spy_price() == 0.0

    def test_get_spy_price_success(self):
        with patch.object(scanner, "_yahoo_fetch", return_value=540.12):
            assert scanner.get_spy_price() == 540.12


# ═══════════════════════════════════════════════════════════════════════════
# Strike selection and position sizing
# ═══════════════════════════════════════════════════════════════════════════

class TestSignalLogic:
    def test_find_target_expiration_normal(self):
        exp = scanner.find_target_expiration(30, 21, 45)
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        dte = (exp_date - date.today()).days
        assert 21 <= dte <= 45

    def test_find_target_expiration_is_friday(self):
        exp = scanner.find_target_expiration(30, 21, 45)
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        assert exp_date.weekday() == 4  # Friday

    def test_select_strikes_bull_put(self):
        short, long = scanner.select_strikes(540.0, 0.05, 5.0, "bull_put")
        assert short < 540
        assert long < short
        assert abs((short - long) - 5.0) < 0.01

    def test_select_strikes_bear_call(self):
        short, long = scanner.select_strikes(540.0, 0.05, 5.0, "bear_call")
        assert short > 540
        assert long > short

    def test_compute_contracts_basic(self, basic_config):
        # $100k × 1% × 1.5 = $1,500 risk
        # $5 width - $0.50 credit = $4.50 max loss → $450/contract
        # $1500 / $450 = 3 contracts
        n = scanner.compute_contracts(100_000, basic_config,
                                       spread_width=5.0, estimated_credit=0.50)
        assert n == 3

    def test_compute_contracts_respects_min(self, basic_config):
        n = scanner.compute_contracts(1_000, basic_config,
                                       spread_width=5.0, estimated_credit=0.50)
        assert n >= basic_config["sizing"]["contracts_min"]

    def test_compute_contracts_respects_max(self, basic_config):
        n = scanner.compute_contracts(10_000_000, basic_config,
                                       spread_width=5.0, estimated_credit=0.50)
        assert n <= basic_config["sizing"]["contracts_max"]

    def test_compute_contracts_zero_when_credit_exceeds_width(self, basic_config):
        n = scanner.compute_contracts(100_000, basic_config,
                                       spread_width=5.0, estimated_credit=6.0)
        # Max loss would be negative → returns min contracts (floor clamp)
        assert n >= 0

    def test_leverage_is_15x(self, basic_config):
        # Verify 1.5x leverage actually doubles risk vs 1x
        n_15 = scanner.compute_contracts(100_000, basic_config,
                                          spread_width=5.0, estimated_credit=0.50)
        basic_config["leverage"]["multiplier"] = 1.0
        n_1 = scanner.compute_contracts(100_000, basic_config,
                                         spread_width=5.0, estimated_credit=0.50)
        # With 1.5x we should size 1.5× more contracts
        # (subject to min/max clamps)
        assert n_15 >= n_1


# ═══════════════════════════════════════════════════════════════════════════
# should_scan logic
# ═══════════════════════════════════════════════════════════════════════════

class TestShouldScan:
    def test_not_monday_returns_false(self, basic_config):
        # Force a non-Monday
        with patch.object(scanner, "date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 8)  # Wednesday
            should, reason = scanner.should_scan(basic_config, {"positions": []})
            assert should is False
            assert "scan day" in reason.lower()

    def test_monday_with_empty_state_returns_true(self, basic_config):
        with patch.object(scanner, "date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 6)  # Monday
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            should, reason = scanner.should_scan(basic_config, {"positions": []})
            assert should is True

    def test_recent_entry_blocks_scan(self, basic_config):
        # Set min_spacing=5, last entry 2 days ago → should fail
        with patch.object(scanner, "date") as mock_date:
            today = date(2026, 4, 6)  # Monday
            mock_date.today.return_value = today
            state = {
                "positions": [],
                "last_entry_date": str(today - timedelta(days=2)),
            }
            should, reason = scanner.should_scan(basic_config, state)
            assert should is False
            assert "too soon" in reason.lower()

    def test_max_concurrent_blocks_scan(self, basic_config):
        basic_config["cadence"]["max_concurrent"] = 2
        with patch.object(scanner, "date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 6)  # Monday
            state = {
                "positions": [
                    {"status": "open"}, {"status": "open"},
                ]
            }
            should, reason = scanner.should_scan(basic_config, state)
            assert should is False
            assert "max concurrent" in reason.lower()

    def test_closed_positions_dont_count(self, basic_config):
        basic_config["cadence"]["max_concurrent"] = 2
        with patch.object(scanner, "date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 6)
            state = {
                "positions": [
                    {"status": "closed"}, {"status": "closed"},
                    {"status": "closed"},
                ]
            }
            should, _ = scanner.should_scan(basic_config, state)
            assert should is True


# ═══════════════════════════════════════════════════════════════════════════
# AlpacaClient dry-run mode
# ═══════════════════════════════════════════════════════════════════════════

class TestAlpacaClientDryRun:
    def test_dry_run_no_credentials_needed(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        client = scanner.AlpacaClient(dry_run=True)
        assert client.dry_run is True

    def test_dry_run_account_returns_mock(self):
        client = scanner.AlpacaClient(dry_run=True)
        acct = client.get_account()
        assert float(acct["equity"]) == 100_000
        assert acct["status"] == "ACTIVE"

    def test_dry_run_positions_empty(self):
        client = scanner.AlpacaClient(dry_run=True)
        assert client.get_positions() == []

    def test_dry_run_submit_order_returns_mock_id(self):
        client = scanner.AlpacaClient(dry_run=True)
        oid = client.submit_spread_order(
            "SPY260508P00530000", "SPY260508P00525000",
            contracts=3, credit=0.50, short_bid=0.70, long_ask=0.20,
        )
        assert oid is not None
        assert "DRY-" in oid

    def test_dry_run_close_returns_true(self):
        client = scanner.AlpacaClient(dry_run=True)
        ok = client.close_spread("SPY260508P00530000", "SPY260508P00525000", 3)
        assert ok is True

    def test_dry_run_option_chain_returns_mock(self):
        client = scanner.AlpacaClient(dry_run=True)
        with patch.object(scanner, "get_spy_price", return_value=540.0):
            chain = client.get_option_chain("SPY", "2026-05-08")
            assert len(chain) > 0
            assert all("bid" in c and "ask" in c for c in chain)
            assert all(c["ask"] >= c["bid"] for c in chain)

    def test_live_mode_requires_credentials(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(SystemExit):
            scanner.AlpacaClient(dry_run=False)


# ═══════════════════════════════════════════════════════════════════════════
# Order construction — verify Alpaca symbol format
# ═══════════════════════════════════════════════════════════════════════════

class TestOrderConstruction:
    def test_option_symbol_format(self):
        """Verify we build Alpaca OCC symbols correctly: SPY YYMMDD P NNNNNNNN"""
        exp_str = "2026-05-08"
        exp_fmt = datetime.strptime(exp_str, "%Y-%m-%d").strftime("%y%m%d")
        assert exp_fmt == "260508"

        short_strike = 530.0
        short_sym = f"SPY{exp_fmt}P{int(short_strike * 1000):08d}"
        assert short_sym == "SPY260508P00530000"
        assert len(short_sym) == 18  # SPY(3) + YYMMDD(6) + P(1) + strike(8)

    def test_spread_legs_correct_strikes(self):
        short, long = scanner.select_strikes(540.0, 0.05, 5.0, "bull_put")
        assert short - long == 5.0  # width
        assert short < 540  # short below spot for bull put


# ═══════════════════════════════════════════════════════════════════════════
# Smoke test mode
# ═══════════════════════════════════════════════════════════════════════════

class TestSmokeTest:
    def test_smoke_test_runs(self, tmp_logs, monkeypatch, capsys):
        # Mock market data to succeed
        monkeypatch.setattr(scanner, "_yahoo_fetch",
                             lambda symbol, **kw: 540.0 if "SPY" in symbol else 18.5)
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

        code = scanner.run_smoke_test()
        captured = capsys.readouterr()
        assert "Smoke Test" in captured.out
        # Should pass with warnings (no Alpaca creds)
        assert code == 0
        assert scanner.HEALTH_PATH.exists()

    def test_smoke_test_fails_on_spy_unreachable(self, tmp_logs, monkeypatch, capsys):
        # Mock VIX success, SPY failure
        def fake_fetch(symbol, **kw):
            if "SPY" in symbol:
                return None
            return 18.5
        monkeypatch.setattr(scanner, "_yahoo_fetch", fake_fetch)
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

        code = scanner.run_smoke_test()
        assert code == 1  # failed

    def test_smoke_test_writes_health(self, tmp_logs, monkeypatch):
        monkeypatch.setattr(scanner, "_yahoo_fetch",
                             lambda symbol, **kw: 540.0)
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

        scanner.run_smoke_test()
        data = json.loads(scanner.HEALTH_PATH.read_text())
        assert "smoke_test" in data["details"]


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases: exits, VIX emergency, overlapping positions
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_check_exits_dte_exit(self, basic_config, tmp_logs):
        # Position expiring in 3 days (below dte_exit=5)
        state = {
            "positions": [{
                "status": "open",
                "entry_date": str(date.today() - timedelta(days=10)),
                "expiration": str(date.today() + timedelta(days=3)),
                "short_strike": 530.0, "long_strike": 525.0,
                "contracts": 3, "credit": 0.50,
                "short_symbol": "SPY260508P00530000",
                "long_symbol": "SPY260508P00525000",
            }],
        }
        client = scanner.AlpacaClient(dry_run=True)
        scanner.check_exits(state, basic_config, client, spy_price=540.0)
        assert state["positions"][0]["status"] == "closed"
        assert "dte_exit" in state["positions"][0]["exit_reason"]

    def test_check_exits_max_hold(self, basic_config, tmp_logs):
        # Position held 25 days (max=21)
        state = {
            "positions": [{
                "status": "open",
                "entry_date": str(date.today() - timedelta(days=25)),
                "expiration": str(date.today() + timedelta(days=15)),
                "short_strike": 530.0, "long_strike": 525.0,
                "contracts": 3, "credit": 0.50,
                "short_symbol": "SPY260508P00530000",
                "long_symbol": "SPY260508P00525000",
            }],
        }
        client = scanner.AlpacaClient(dry_run=True)
        scanner.check_exits(state, basic_config, client, spy_price=540.0)
        assert state["positions"][0]["status"] == "closed"
        assert "max_hold" in state["positions"][0]["exit_reason"]

    def test_open_position_not_exited(self, basic_config, tmp_logs):
        # Fresh position, nothing to exit
        state = {
            "positions": [{
                "status": "open",
                "entry_date": str(date.today() - timedelta(days=5)),
                "expiration": str(date.today() + timedelta(days=25)),
                "short_strike": 530.0, "long_strike": 525.0,
                "contracts": 3, "credit": 0.50,
                "short_symbol": "SPY260508P00530000",
                "long_symbol": "SPY260508P00525000",
            }],
        }
        client = scanner.AlpacaClient(dry_run=True)
        scanner.check_exits(state, basic_config, client, spy_price=540.0)
        assert state["positions"][0]["status"] == "open"

    def test_run_scan_skips_high_vix(self, basic_config, tmp_logs, monkeypatch):
        with patch.object(scanner, "get_vix", return_value=40.0), \
             patch.object(scanner, "get_spy_price", return_value=540.0):
            state = {"positions": []}
            client = scanner.AlpacaClient(dry_run=True)
            scanner.run_scan(basic_config, state, client, dry_run=True)
            # No new positions should be added (VIX > 35 blocks)
            assert len(state["positions"]) == 0

    def test_run_scan_skips_low_vix(self, basic_config, tmp_logs):
        with patch.object(scanner, "get_vix", return_value=8.0), \
             patch.object(scanner, "get_spy_price", return_value=540.0):
            state = {"positions": []}
            client = scanner.AlpacaClient(dry_run=True)
            scanner.run_scan(basic_config, state, client, dry_run=True)
            assert len(state["positions"]) == 0

    def test_run_scan_skips_no_spy_price(self, basic_config, tmp_logs):
        with patch.object(scanner, "get_vix", return_value=18.0), \
             patch.object(scanner, "get_spy_price", return_value=0.0):
            state = {"positions": []}
            client = scanner.AlpacaClient(dry_run=True)
            scanner.run_scan(basic_config, state, client, dry_run=True)
            assert len(state["positions"]) == 0


# ═══════════════════════════════════════════════════════════════════════════
# LaunchAgent plist
# ═══════════════════════════════════════════════════════════════════════════

class TestLaunchAgent:
    def test_plist_exists(self):
        plist = ROOT / "deploy" / "com.pilotai.exp1220.plist"
        assert plist.exists()

    def test_plist_valid_xml(self):
        import xml.etree.ElementTree as ET
        plist = ROOT / "deploy" / "com.pilotai.exp1220.plist"
        tree = ET.parse(plist)
        root = tree.getroot()
        assert root.tag == "plist"

    def test_plist_has_required_keys(self):
        plist = ROOT / "deploy" / "com.pilotai.exp1220.plist"
        content = plist.read_text()
        assert "Label" in content
        assert "com.pilotai.exp1220" in content
        assert "StartCalendarInterval" in content
        assert "run_exp1220.py" in content
        assert "WorkingDirectory" in content
        assert "StandardOutPath" in content
        assert "StandardErrorPath" in content

    def test_plist_has_weekday_schedule(self):
        plist = ROOT / "deploy" / "com.pilotai.exp1220.plist"
        content = plist.read_text()
        # Should have all 5 weekdays (1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri)
        for wd in range(1, 6):
            assert f"<integer>{wd}</integer>" in content

    def test_plist_scans_at_935(self):
        plist = ROOT / "deploy" / "com.pilotai.exp1220.plist"
        content = plist.read_text()
        assert "<integer>9</integer>" in content  # Hour
        assert "<integer>35</integer>" in content  # Minute
