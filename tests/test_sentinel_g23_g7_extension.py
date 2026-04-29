"""
Tests for SENTINEL Gate 23 — G7-extension (Branch 9).

Three net-new behaviours on top of existing Gate 7:
  1. qty-mismatch detection — broker symbol matches DB open trade but
     qty differs → critical alert distinct from orphan/ghost
  2. halt-bypass — orchestrator-side reconciliation in cmd_daily must
     run G7 even for halted experiments
  3. stale-orphan re-alert — unmanaged DB row older than 24h with a
     live broker counterpart → critical alert each daily run

Plus a bug fix: drop the runtime.py:1144 ``len > 10`` filter that silently
dropped underlying-equity orphans from short-put assignments (the
EXP-800 partial-assignment story).

Reproductions:
  - EXP-503 pre-fix: 10 broker_only OCC symbols, DB empty → 10 orphans
    (and a halt alert per existing _ORPHAN_SIMULTANEOUS_HALT threshold)
  - EXP-800 pre-fix: 4 qty_mismatch on the short legs of 4 spreads
"""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _occ(ticker: str, date: str = "260508", put_call: str = "P", strike: str = "00665000") -> str:
    """Build a fake OCC symbol (>10 chars, options-shaped)."""
    return f"{ticker.ljust(6)}{date}{put_call}{strike}"


def _make_db(path: str) -> sqlite3.Connection:
    """Create a minimal trades + trade_legs schema matching production."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            source TEXT,
            ticker TEXT,
            status TEXT DEFAULT 'open',
            contracts INTEGER DEFAULT 1,
            pnl REAL,
            credit REAL,
            entry_date TEXT,
            exit_date TEXT,
            expiration TEXT,
            short_strike REAL,
            long_strike REAL,
            created_at TEXT,
            updated_at TEXT,
            strategy_type TEXT DEFAULT 'bull_put',
            metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL,
            leg_type TEXT,
            occ_symbol TEXT,
            qty INTEGER,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanner_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT,
            event_type TEXT,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _insert_spread(
    conn: sqlite3.Connection,
    trade_id: str,
    ticker: str,
    short_sym: str,
    long_sym: str,
    contracts: int = 10,
    status: str = "open",
):
    """Insert one credit spread (2 legs: short_put + long_put)."""
    conn.execute(
        "INSERT INTO trades (id, ticker, status, contracts, strategy_type, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'bull_put', "
        "datetime('now'), datetime('now'))",
        (trade_id, ticker, status, contracts),
    )
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol, qty) "
        "VALUES (?, 'short_put', ?, ?)",
        (trade_id, short_sym, -contracts),
    )
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_type, occ_symbol, qty) "
        "VALUES (?, 'long_put', ?, ?)",
        (trade_id, long_sym, contracts),
    )
    conn.commit()


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "trades.db")
    conn = _make_db(db_path)
    yield db_path, conn
    conn.close()


# ---------------------------------------------------------------------------
# Section A — qty-mismatch detection (single trade)
# ---------------------------------------------------------------------------


class TestQtyMismatch:
    def test_short_leg_qty_mismatch_emits_critical(self, tmp_db):
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        short = _occ("SPY", strike="00666000")
        long_ = _occ("SPY", strike="00661000")
        _insert_spread(conn, "T1", "SPY", short, long_, contracts=10)

        # Broker shows -9 on short, +10 on long → mismatch on short only
        positions = [
            {"symbol": short, "qty": "-9"},
            {"symbol": long_, "qty": "10"},
        ]
        result = check_orphan_positions("EXP-X", positions, db_path=db_path)

        # qty_mismatch list populated
        assert hasattr(result, "qty_mismatches")
        keys = {qm["occ_symbol"] for qm in result.qty_mismatches}
        assert short in keys
        assert long_ not in keys

        # Mismatched leg is NOT classified as orphan or ghost
        assert short not in result.orphans
        assert all(g.get("occ_symbol") != short for g in result.ghosts)

        # At least one alert is critical and references qty
        critical_alerts = [a for a in result.alerts if a["severity"] == "critical"]
        assert critical_alerts
        assert any("qty" in a["message"].lower() for a in critical_alerts)
        # Alert mentions broker_qty / db_qty values
        assert any(
            "-9" in a["message"] and "-10" in a["message"]
            for a in critical_alerts
        )

    def test_in_sync_qty_no_alert(self, tmp_db):
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        short = _occ("SPY", strike="00666000")
        long_ = _occ("SPY", strike="00661000")
        _insert_spread(conn, "T1", "SPY", short, long_, contracts=10)

        positions = [
            {"symbol": short, "qty": "-10"},
            {"symbol": long_, "qty": "10"},
        ]
        result = check_orphan_positions("EXP-X", positions, db_path=db_path)
        assert result.qty_mismatches == []
        assert result.orphans == []
        assert result.ghosts == []
        assert result.alerts == []


# ---------------------------------------------------------------------------
# Section B — EXP-503 reproduction (10 broker-only OCC symbols, DB empty)
# ---------------------------------------------------------------------------


class TestExp503Reproduction:
    def test_ten_broker_only_orphans(self, tmp_db):
        from sentinel.runtime import check_orphan_positions

        db_path, _conn = tmp_db
        # 5 spreads × 2 legs = 10 OCC symbols, all broker-only
        positions = []
        for i in range(5):
            positions.append({"symbol": _occ("SPY", strike=f"006{60+i}000"), "qty": "-10"})
            positions.append({"symbol": _occ("SPY", strike=f"006{55+i}000"), "qty": "10"})

        result = check_orphan_positions("EXP-503", positions, db_path=db_path)
        assert len(result.orphans) == 10

        # Existing _ORPHAN_SIMULTANEOUS_HALT = 5 → halt-severity alert present
        sev = {a["severity"] for a in result.alerts}
        assert "halt" in sev or "critical" in sev

        # No qty_mismatch (nothing to match against)
        assert result.qty_mismatches == []


# ---------------------------------------------------------------------------
# Section C — EXP-800 reproduction (4 qty_mismatch alerts)
# ---------------------------------------------------------------------------


class TestExp800Reproduction:
    def test_four_short_legs_with_qty_mismatch(self, tmp_db):
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        # Insert 4 spreads, each contracts=10
        legs = []
        for i in range(4):
            short = _occ("SPY", strike=f"006{70+i}000")
            long_ = _occ("SPY", strike=f"006{65+i}000")
            _insert_spread(conn, f"T{i}", "SPY", short, long_, contracts=10)
            legs.append((short, long_))

        # Broker shows -9 (instead of -10) for each short leg, full +10 for each long leg
        positions = []
        for short, long_ in legs:
            positions.append({"symbol": short, "qty": "-9"})
            positions.append({"symbol": long_, "qty": "10"})

        result = check_orphan_positions("EXP-800", positions, db_path=db_path)

        assert len(result.qty_mismatches) == 4
        # Each qty_mismatch records broker_qty + db_qty
        for qm in result.qty_mismatches:
            assert qm["broker_qty"] == -9
            assert qm["db_qty"] == -10
        # 4 critical alerts (one per leg)
        crits = [a for a in result.alerts if a["severity"] == "critical"]
        assert len(crits) >= 4

        # No false orphans / ghosts since every symbol is on both sides
        assert result.orphans == []
        assert result.ghosts == []


# ---------------------------------------------------------------------------
# Section D — len>10 fix surfaces underlying-equity orphans
# ---------------------------------------------------------------------------


class TestEquityOrphanSurfaces:
    def test_short_equity_symbol_is_no_longer_filtered(self, tmp_db):
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        # DB has matching options (in-sync)
        short = _occ("SPY", strike="00666000")
        long_ = _occ("SPY", strike="00661000")
        _insert_spread(conn, "T1", "SPY", short, long_, contracts=10)

        # Broker has the matching options PLUS 100 shares of SPY (assigned).
        # Pre-fix: SPY (3 chars) was filtered out by `len > 10`.
        # Post-fix: SPY surfaces as a broker_only orphan.
        positions = [
            {"symbol": short, "qty": "-10"},
            {"symbol": long_, "qty": "10"},
            {"symbol": "SPY", "qty": "100"},
        ]

        result = check_orphan_positions("EXP-800", positions, db_path=db_path)
        assert "SPY" in result.orphans, (
            "len>10 filter regression — SPY equity orphan from assignment "
            "is being silently dropped (this is the EXP-800 partial-assignment bug)"
        )
        # Mismatched-options legs should NOT appear; we only inserted in-sync data
        assert result.qty_mismatches == []


# ---------------------------------------------------------------------------
# Section E — stale-orphan re-alert (>24h)
# ---------------------------------------------------------------------------


class TestStaleOrphanReAlert:
    def test_unmanaged_row_older_than_24h_with_live_broker_counterpart(self, tmp_db):
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        sym = _occ("SPY", strike="00666000")

        # Insert an unmanaged row with created_at = 26h ago
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=26)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "INSERT INTO trades (id, source, ticker, status, strategy_type, "
            "metadata, created_at, updated_at) "
            "VALUES (?, 'sentinel', 'SPY', 'unmanaged', 'unknown', ?, ?, ?)",
            (
                f"orphan_{sym}",
                json.dumps({"occ_symbol": sym}),
                old_ts,
                old_ts,
            ),
        )
        conn.commit()

        # Broker still has the same symbol → must trigger stale-orphan re-alert
        positions = [{"symbol": sym, "qty": "-10"}]
        result = check_orphan_positions("EXP-X", positions, db_path=db_path)

        assert hasattr(result, "stale_orphans")
        stale_syms = {s.get("occ_symbol") for s in result.stale_orphans}
        assert sym in stale_syms

        # Critical alert with "stale" or "24h" wording
        crit = [a for a in result.alerts if a["severity"] == "critical"]
        assert any(
            ("stale" in a["message"].lower() or "24h" in a["message"].lower())
            and sym in a["message"]
            for a in crit
        )

    def test_fresh_unmanaged_row_no_stale_alert(self, tmp_db):
        from sentinel.runtime import check_orphan_positions

        db_path, conn = tmp_db
        sym = _occ("SPY", strike="00666000")

        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "INSERT INTO trades (id, source, ticker, status, strategy_type, "
            "metadata, created_at, updated_at) "
            "VALUES (?, 'sentinel', 'SPY', 'unmanaged', 'unknown', ?, ?, ?)",
            (
                f"orphan_{sym}",
                json.dumps({"occ_symbol": sym}),
                fresh_ts,
                fresh_ts,
            ),
        )
        conn.commit()

        positions = [{"symbol": sym, "qty": "-10"}]
        result = check_orphan_positions("EXP-X", positions, db_path=db_path)
        assert result.stale_orphans == []


# ---------------------------------------------------------------------------
# Section F — cmd_daily wires G7 for halted experiments (halt-bypass)
# ---------------------------------------------------------------------------


class TestCmdDailyHaltBypass:
    def test_cmd_daily_calls_check_orphan_positions(self):
        """AST: cmd_daily must contain a check_orphan_positions call (orchestrator-side G7)."""
        src = (ROOT / "scripts" / "run_sentinel.py").read_text()
        tree = ast.parse(src)
        cmd_daily_fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "cmd_daily"),
            None,
        )
        assert cmd_daily_fn is not None
        names = set()
        for node in ast.walk(cmd_daily_fn):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
        assert "check_orphan_positions" in names, (
            "Gate 23 not wired into cmd_daily — expected an orchestrator-side "
            "check_orphan_positions() call so G7 runs even for halted experiments"
        )

    def test_cmd_daily_does_not_filter_g7_to_is_live(self):
        """The G7 invocation must not be inside an `if _is_live(...)` guard.

        We assert by inspection: the call to check_orphan_positions appears
        outside any If-stmt whose test references `_is_live`. Halt-bypass
        means halted experiments still get reconciled.
        """
        src = (ROOT / "scripts" / "run_sentinel.py").read_text()
        tree = ast.parse(src)
        cmd_daily_fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "cmd_daily"),
            None,
        )
        assert cmd_daily_fn is not None

        # Walk and find every `If` node whose test calls `_is_live`. Then
        # check no `check_orphan_positions` call lives inside them.
        for if_node in ast.walk(cmd_daily_fn):
            if not isinstance(if_node, ast.If):
                continue
            test_calls = {
                n.func.id for n in ast.walk(if_node.test)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            if "_is_live" not in test_calls:
                continue
            # Found an `if _is_live(...)` block — assert no G7 call inside
            for sub in ast.walk(if_node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                    assert name != "check_orphan_positions", (
                        "check_orphan_positions is gated by _is_live() — that "
                        "would skip halted experiments, defeating G23 halt-bypass"
                    )

    def test_experimenthealth_carries_positions(self):
        """ExperimentHealth must expose the live position list so cmd_daily
        can run G7 against halted experiments without re-fetching."""
        from sentinel.monitor import ExperimentHealth

        h = ExperimentHealth(
            exp_id="EXP-X", account_id=None, registry_status="paper_trading",
            env_file=None,
        )
        # Default empty list — never None (so callers can iterate safely)
        assert hasattr(h, "positions")
        assert h.positions == []
