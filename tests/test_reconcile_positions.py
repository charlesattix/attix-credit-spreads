"""
Tests for scripts/reconcile_positions.py (FU#7 — Branch 10).

Covers:
- OCC symbol parser
- build_plan (pure logic): clean state, EXP-503 reproduction, EXP-800 reproduction
- apply_plan (sqlite mutations + transaction discipline)
- CLI argument handling (--dry-run is default, --apply commits)

Mocks Alpaca client + uses tmp_path for sqlite fixtures. Never touches
sentinel_state.json or any real per-experiment DB.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ importable
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Schema helpers (mirror live trades + trade_legs schema)
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE trades (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            ticker TEXT NOT NULL,
            strategy_type TEXT,
            status TEXT DEFAULT 'open',
            short_strike REAL,
            long_strike REAL,
            expiration TEXT,
            credit REAL,
            contracts INTEGER DEFAULT 1,
            entry_date TEXT,
            exit_date TEXT,
            exit_reason TEXT,
            pnl REAL,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE trade_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL REFERENCES trades(id),
            leg_type TEXT NOT NULL,
            strike REAL NOT NULL,
            occ_symbol TEXT,
            status TEXT DEFAULT 'open'
        )
        """
    )
    conn.commit()
    return conn


def _occ(ticker: str, yymmdd: str, cp: str, strike_x1000: int) -> str:
    return f"{ticker.ljust(6)}{yymmdd}{cp.upper()}{strike_x1000:08d}"


def _insert_spread(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    ticker: str,
    expiration: str,
    short_strike: float,
    long_strike: float,
    short_occ: str,
    long_occ: str,
    contracts: int = 1,
    cp: str = "P",
    status: str = "open",
) -> None:
    strategy = (
        "bull_put" if cp == "P" and short_strike > long_strike
        else "bear_call" if cp == "C" and short_strike < long_strike
        else f"{cp}_spread"
    )
    conn.execute(
        "INSERT INTO trades "
        "(id, source, ticker, strategy_type, status, short_strike, long_strike, "
        " expiration, contracts, entry_date) "
        "VALUES (?, 'test', ?, ?, ?, ?, ?, ?, ?, '2026-04-15')",
        (trade_id, ticker, strategy, status, short_strike, long_strike,
         expiration, contracts),
    )
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_type, strike, occ_symbol) "
        "VALUES (?, ?, ?, ?)",
        (trade_id, f"short_{'put' if cp == 'P' else 'call'}", short_strike, short_occ),
    )
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_type, strike, occ_symbol) "
        "VALUES (?, ?, ?, ?)",
        (trade_id, f"long_{'put' if cp == 'P' else 'call'}", long_strike, long_occ),
    )
    conn.commit()


# ===========================================================================
# parse_occ
# ===========================================================================


class TestParseOcc:
    def test_parse_standard_put(self):
        from reconcile_positions import parse_occ
        sym = _occ("SPY", "260508", "P", 685_000)  # SPY 2026-05-08 P 685
        result = parse_occ(sym)
        assert result is not None
        assert result["ticker"] == "SPY"
        assert result["expiration"] == "2026-05-08"
        assert result["type"] == "P"
        assert result["strike"] == 685.0

    def test_parse_call(self):
        from reconcile_positions import parse_occ
        sym = _occ("QQQ", "260620", "C", 450_500)  # 450.5 strike
        result = parse_occ(sym)
        assert result["type"] == "C"
        assert result["strike"] == 450.5

    def test_equity_ticker_returns_none(self):
        from reconcile_positions import parse_occ
        assert parse_occ("SPY") is None
        assert parse_occ("AAPL") is None

    def test_garbage_returns_none(self):
        from reconcile_positions import parse_occ
        assert parse_occ("") is None
        assert parse_occ("not-an-occ-symbol-at-all") is None


# ===========================================================================
# build_plan
# ===========================================================================


class TestBuildPlanCleanState:
    """Alpaca and DB perfectly aligned → zero issues."""

    def test_in_sync_one_spread(self, tmp_path):
        from reconcile_positions import build_plan, fetch_db_legs

        conn = _make_db(tmp_path)
        short_occ = _occ("SPY", "260508", "P", 685_000)
        long_occ = _occ("SPY", "260508", "P", 680_000)
        _insert_spread(
            conn, trade_id="t1", ticker="SPY", expiration="2026-05-08",
            short_strike=685.0, long_strike=680.0,
            short_occ=short_occ, long_occ=long_occ, contracts=1, cp="P",
        )

        alpaca_positions = [
            {"symbol": short_occ, "qty": -1},
            {"symbol": long_occ, "qty": 1},
        ]
        db_legs = fetch_db_legs(conn)
        plan = build_plan("EXP-503", alpaca_positions, db_legs, spread_width=5.0)

        assert len(plan["in_sync"]) == 2
        assert plan["qty_mismatches"] == []
        assert plan["broker_only"] == []
        assert plan["db_only"] == []
        assert plan["spreads_inferred"] == []


class TestExp503Reproduction:
    """
    Today's EXP-503 state: 10 SPY put legs in Alpaca (5 spreads with
    width=5), zero DB rows. build_plan must infer exactly 5 spreads.
    """

    def test_ten_orphan_legs_infer_five_spreads(self, tmp_path):
        from reconcile_positions import build_plan, fetch_db_legs

        conn = _make_db(tmp_path)  # empty DB

        short_strikes = [685, 680, 675, 670, 665]
        spreads_alpaca = []
        for ss in short_strikes:
            ls = ss - 5  # spread_width=5
            spreads_alpaca.append((_occ("SPY", "260508", "P", ss * 1000), -1))
            spreads_alpaca.append((_occ("SPY", "260508", "P", ls * 1000), 1))

        alpaca_positions = [{"symbol": s, "qty": q} for s, q in spreads_alpaca]
        db_legs = fetch_db_legs(conn)
        plan = build_plan("EXP-503", alpaca_positions, db_legs, spread_width=5.0)

        assert plan["in_sync"] == []
        assert plan["qty_mismatches"] == []
        assert len(plan["broker_only"]) == 10
        assert plan["db_only"] == []
        assert len(plan["spreads_inferred"]) == 5
        for sp in plan["spreads_inferred"]:
            assert sp["ticker"] == "SPY"
            assert sp["expiration"] == "2026-05-08"
            assert sp["type"] == "P"
            assert sp["short_strike"] - sp["long_strike"] == 5.0
            assert sp["contracts"] == 1
            assert sp["short_alpaca_sym"].startswith("SPY")
            assert sp["long_alpaca_sym"].startswith("SPY")


class TestExp800Reproduction:
    """
    EXP-800 state: 4 short legs in Alpaca w/ qty=-1 each, but DB has the
    spreads recorded with contracts=2 (legacy mismatch). Expect 4
    qty_mismatches surfaced, no spurious orphan/ghost.
    """

    def test_four_qty_mismatches_no_orphans(self, tmp_path):
        from reconcile_positions import build_plan, fetch_db_legs

        conn = _make_db(tmp_path)
        positions = []
        # Strikes spaced far enough apart that OCCs don't collide.
        for i, ss in enumerate([550, 540, 530, 520]):
            ls = ss - 5
            short_occ = _occ("SPY", "260516", "P", ss * 1000)
            long_occ = _occ("SPY", "260516", "P", ls * 1000)
            _insert_spread(
                conn, trade_id=f"t{i}", ticker="SPY",
                expiration="2026-05-16", short_strike=float(ss), long_strike=float(ls),
                short_occ=short_occ, long_occ=long_occ, contracts=2, cp="P",
            )
            # Broker actually only holds -1/+1 (drift)
            positions.append({"symbol": short_occ, "qty": -1})
            positions.append({"symbol": long_occ, "qty": 1})

        db_legs = fetch_db_legs(conn)
        plan = build_plan("EXP-800", positions, db_legs, spread_width=5.0)

        # Every leg is a mismatch (8 legs total, all mismatch)
        assert len(plan["qty_mismatches"]) == 8
        assert plan["broker_only"] == []
        assert plan["db_only"] == []
        # Mismatch entries carry both broker_qty and db_qty
        for qm in plan["qty_mismatches"]:
            assert abs(qm["broker_qty"]) == 1
            assert abs(qm["db_qty"]) == 2


class TestTupleFallback:
    """
    DB rows where occ_symbol was never populated must still match by
    (ticker, expiration, strike, type) tuple.
    """

    def test_match_by_tuple_when_occ_missing(self, tmp_path):
        from reconcile_positions import build_plan, fetch_db_legs

        conn = _make_db(tmp_path)
        # Insert a trade WITHOUT occ_symbol on its legs
        conn.execute(
            "INSERT INTO trades (id, source, ticker, strategy_type, status, "
            "short_strike, long_strike, expiration, contracts, entry_date) "
            "VALUES ('t1', 'test', 'SPY', 'bull_put', 'open', 685, 680, "
            "'2026-05-08', 1, '2026-04-15')"
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, strike, occ_symbol) "
            "VALUES ('t1', 'short_put', 685.0, NULL)"
        )
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_type, strike, occ_symbol) "
            "VALUES ('t1', 'long_put', 680.0, NULL)"
        )
        conn.commit()

        short_occ = _occ("SPY", "260508", "P", 685_000)
        long_occ = _occ("SPY", "260508", "P", 680_000)
        alpaca_positions = [
            {"symbol": short_occ, "qty": -1},
            {"symbol": long_occ, "qty": 1},
        ]
        db_legs = fetch_db_legs(conn)
        plan = build_plan("EXP-503", alpaca_positions, db_legs, spread_width=5.0)

        assert len(plan["in_sync"]) == 2
        assert plan["broker_only"] == []
        assert plan["db_only"] == []


class TestGhostDetection:
    """DB has open trade, broker has no matching position → ghost."""

    def test_db_only_surfaced_as_ghost(self, tmp_path):
        from reconcile_positions import build_plan, fetch_db_legs

        conn = _make_db(tmp_path)
        short_occ = _occ("SPY", "260508", "P", 685_000)
        long_occ = _occ("SPY", "260508", "P", 680_000)
        _insert_spread(
            conn, trade_id="t1", ticker="SPY", expiration="2026-05-08",
            short_strike=685.0, long_strike=680.0,
            short_occ=short_occ, long_occ=long_occ, contracts=1, cp="P",
        )

        db_legs = fetch_db_legs(conn)
        plan = build_plan("EXP-503", [], db_legs, spread_width=5.0)  # empty broker

        assert plan["in_sync"] == []
        assert plan["broker_only"] == []
        assert len(plan["db_only"]) == 2  # 2 legs


# ===========================================================================
# apply_plan
# ===========================================================================


class TestApplyPlan:
    def test_inserts_inferred_spreads(self, tmp_path):
        from reconcile_positions import apply_plan

        conn = _make_db(tmp_path)
        short_occ = _occ("SPY", "260508", "P", 685_000)
        long_occ = _occ("SPY", "260508", "P", 680_000)
        plan = {
            "in_sync": [],
            "qty_mismatches": [],
            "broker_only": [],
            "db_only": [],
            "spreads_inferred": [{
                "ticker": "SPY",
                "expiration": "2026-05-08",
                "type": "P",
                "short_strike": 685.0,
                "long_strike": 680.0,
                "contracts": 1,
                "short_alpaca_sym": short_occ,
                "long_alpaca_sym": long_occ,
            }],
        }

        stats = apply_plan(conn, plan)
        assert stats["inserted_spreads"] == 1

        rows = conn.execute("SELECT * FROM trades").fetchall()
        assert len(rows) == 1
        meta = json.loads(rows[0]["metadata"])
        assert meta["recovery_source"].startswith("alpaca_backfill_")
        assert meta["short_alpaca_sym"] == short_occ
        assert meta["long_alpaca_sym"] == long_occ

        legs = conn.execute("SELECT * FROM trade_legs").fetchall()
        assert len(legs) == 2

    def test_closes_ghost_trades(self, tmp_path):
        from reconcile_positions import apply_plan

        conn = _make_db(tmp_path)
        short_occ = _occ("SPY", "260508", "P", 685_000)
        long_occ = _occ("SPY", "260508", "P", 680_000)
        _insert_spread(
            conn, trade_id="t-ghost", ticker="SPY", expiration="2026-05-08",
            short_strike=685.0, long_strike=680.0,
            short_occ=short_occ, long_occ=long_occ, contracts=1, cp="P",
        )

        plan = {
            "in_sync": [], "qty_mismatches": [], "broker_only": [],
            "spreads_inferred": [],
            "db_only": [{"leg": dict(conn.execute(
                "SELECT t.id AS trade_id, t.ticker, t.contracts, "
                "tl.id AS leg_id, tl.leg_type, tl.strike, tl.occ_symbol "
                "FROM trade_legs tl JOIN trades t ON tl.trade_id = t.id "
                "WHERE tl.leg_type = 'short_put'"
            ).fetchone())}],
        }

        stats = apply_plan(conn, plan)
        assert stats["closed_ghosts"] >= 1
        row = conn.execute(
            "SELECT status, exit_reason FROM trades WHERE id = ?", ("t-ghost",)
        ).fetchone()
        assert row["status"] == "closed_external"
        assert row["exit_reason"].startswith("alpaca_backfill_")

    def test_qty_update_records_audit(self, tmp_path):
        from reconcile_positions import apply_plan

        conn = _make_db(tmp_path)
        short_occ = _occ("SPY", "260508", "P", 685_000)
        long_occ = _occ("SPY", "260508", "P", 680_000)
        _insert_spread(
            conn, trade_id="t-mismatch", ticker="SPY", expiration="2026-05-08",
            short_strike=685.0, long_strike=680.0,
            short_occ=short_occ, long_occ=long_occ, contracts=2, cp="P",
        )
        leg_row = dict(conn.execute(
            "SELECT t.id AS trade_id, t.contracts, "
            "tl.id AS leg_id, tl.leg_type "
            "FROM trade_legs tl JOIN trades t ON tl.trade_id = t.id "
            "WHERE tl.leg_type = 'short_put'"
        ).fetchone())

        plan = {
            "in_sync": [], "broker_only": [], "db_only": [], "spreads_inferred": [],
            "qty_mismatches": [{
                "occ": short_occ, "leg": leg_row,
                "broker_qty": -1, "db_qty": -2,
            }],
        }
        stats = apply_plan(conn, plan)
        assert stats["qty_updated"] >= 1

        row = conn.execute(
            "SELECT contracts, metadata FROM trades WHERE id = ?",
            ("t-mismatch",),
        ).fetchone()
        assert row["contracts"] == 1
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        assert "qty_reconciliations" in meta
        assert meta["qty_reconciliations"][-1]["broker_qty"] == -1
        assert meta["qty_reconciliations"][-1]["db_qty"] == -2


# ===========================================================================
# CLI
# ===========================================================================


class TestCli:
    def test_dry_run_is_default_no_db_writes(self, tmp_path, monkeypatch):
        """When --apply is not passed, no inserts/updates may occur."""
        import reconcile_positions as rp

        conn = _make_db(tmp_path)
        db_path = str(tmp_path / "trades.db")
        env_file = tmp_path / ".env.exp503"
        env_file.write_text(
            "ALPACA_API_KEY=fake\nALPACA_API_SECRET=fake\n"
        )

        # Stub IO boundaries
        monkeypatch.setattr(rp, "load_paper_config", lambda exp_id: {
            "db_path": db_path, "strategy": {"spread_width": 5},
        })
        short_occ = _occ("SPY", "260508", "P", 685_000)
        long_occ = _occ("SPY", "260508", "P", 680_000)
        monkeypatch.setattr(rp, "get_alpaca_positions", lambda env_file: [
            {"symbol": short_occ, "qty": -1},
            {"symbol": long_occ, "qty": 1},
        ])
        monkeypatch.setattr(rp, "_resolve_db_path_arg", lambda exp_id, cfg: db_path)
        monkeypatch.setattr(rp, "_resolve_env_file", lambda exp_id: env_file)
        conn.close()

        rc = rp.main(["--experiment", "EXP-503"])
        assert rc == 0

        # Re-open and verify zero rows inserted
        conn2 = sqlite3.connect(db_path)
        n_trades = conn2.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn2.close()
        assert n_trades == 0

    def test_apply_commits_writes(self, tmp_path, monkeypatch):
        import reconcile_positions as rp

        conn = _make_db(tmp_path)
        db_path = str(tmp_path / "trades.db")
        env_file = tmp_path / ".env.exp503"
        env_file.write_text("ALPACA_API_KEY=fake\nALPACA_API_SECRET=fake\n")

        monkeypatch.setattr(rp, "load_paper_config", lambda exp_id: {
            "db_path": db_path, "strategy": {"spread_width": 5},
        })
        short_occ = _occ("SPY", "260508", "P", 685_000)
        long_occ = _occ("SPY", "260508", "P", 680_000)
        monkeypatch.setattr(rp, "get_alpaca_positions", lambda env_file: [
            {"symbol": short_occ, "qty": -1},
            {"symbol": long_occ, "qty": 1},
        ])
        monkeypatch.setattr(rp, "_resolve_db_path_arg", lambda exp_id, cfg: db_path)
        monkeypatch.setattr(rp, "_resolve_env_file", lambda exp_id: env_file)
        conn.close()

        rc = rp.main(["--experiment", "EXP-503", "--apply"])
        assert rc == 0

        conn2 = sqlite3.connect(db_path)
        n_trades = conn2.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        n_legs = conn2.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0]
        conn2.close()
        assert n_trades == 1
        assert n_legs == 2
