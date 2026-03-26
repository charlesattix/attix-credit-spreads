"""
data.py — Data access layer for the paper trading dashboard.

Reads from:
  - experiments/registry.json  (experiment metadata)
  - configs/paper_*.yaml       (db_path resolution)
  - data/*/attix_*.db        (SQLite trade data)

ATTIX_ROOT env var overrides the project root path.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml

# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """Return the attix-credit-spreads root. ATTIX_ROOT env overrides."""
    env = os.environ.get("ATTIX_ROOT")
    if env:
        return Path(env)
    # Default: parent of web_dashboard/
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT     = _project_root()
REGISTRY_PATH    = PROJECT_ROOT / "experiments" / "registry.json"
PUSHED_DATA_PATH = PROJECT_ROOT / "data" / "pushed_dashboard.json"
EXPORT_DATA_PATH = PROJECT_ROOT / "data" / "dashboard_export.json"
STARTING_EQUITY  = float(os.environ.get("STARTING_EQUITY", "100000"))


def load_pushed_data() -> Optional[dict]:
    """
    Load the most recent pushed data snapshot.
    Tries dashboard_export.json (schema 1.2+, has alpaca block) first,
    then pushed_dashboard.json (written by /api/admin/push-data endpoint).
    """
    for path in (EXPORT_DATA_PATH, PUSHED_DATA_PATH):
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                continue
    return None


def get_alpaca_for_exp(exp_id: str, pushed: Optional[dict]) -> Optional[dict]:
    """
    Extract the alpaca block for a given experiment ID from a pushed export.
    Returns None if not available or on error.
    """
    if not pushed or "experiments" not in pushed:
        return None
    for exp in pushed["experiments"]:
        if exp.get("id") == exp_id:
            alp = exp.get("alpaca")
            if alp and alp.get("equity") is not None:
                return alp
    return None

BACKTEST_EXPECTATIONS: dict[str, dict] = {
    "EXP-400": {"avg_return": 32.7,  "max_dd": -12.1, "robust": 0.870},
    "EXP-401": {"avg_return": 40.7,  "max_dd": -7.0,  "robust": None},
    "EXP-503": {"avg_return": None,  "max_dd": None,   "robust": None},
    "EXP-600": {"avg_return": 139.2, "max_dd": -19.4,  "robust": 0.950},
}

CLOSED_STATUSES = (
    "closed_profit", "closed_loss", "closed_manual",
    "closed_expiry", "closed_external",
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"experiments": {}}
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def get_live_experiments(registry: dict | None = None) -> list[dict]:
    if registry is None:
        registry = load_registry()
    exps = [
        e for e in registry["experiments"].values()
        if e.get("status") == "paper_trading"
    ]
    return sorted(exps, key=lambda e: e["id"])


def get_all_experiments(registry: dict | None = None) -> list[dict]:
    if registry is None:
        registry = load_registry()
    return sorted(registry["experiments"].values(), key=lambda e: e["id"])


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

def resolve_db_path(exp: dict) -> Optional[Path]:
    """
    Find the SQLite DB for an experiment.
    Priority:
      1. paper_config yaml → db_path
      2. data/expNNN/attix_expNNN.db
      3. data/attix_expNNN.db
    Returns Path if found (even without trades table), None if nothing exists.
    """
    candidates: list[Path] = []

    paper_cfg = exp.get("paper_config")
    if paper_cfg:
        cfg_file = PROJECT_ROOT / paper_cfg
        if cfg_file.exists():
            try:
                with open(cfg_file) as f:
                    cfg = yaml.safe_load(f)
                db_rel = cfg.get("db_path", "")
                if db_rel:
                    candidates.append(PROJECT_ROOT / db_rel)
            except Exception:
                pass

    num = exp["id"].replace("EXP-", "").lower()
    candidates += [
        PROJECT_ROOT / f"data/exp{num}/attix_exp{num}.db",
        PROJECT_ROOT / f"data/attix_exp{num}.db",
    ]

    first_existing: Optional[Path] = None
    for p in candidates:
        if not p.exists():
            continue
        if first_existing is None:
            first_existing = p
        try:
            conn = sqlite3.connect(str(p))
            conn.execute("SELECT 1 FROM trades LIMIT 1")
            conn.close()
            return p
        except (sqlite3.OperationalError, Exception):
            try:
                conn.close()
            except Exception:
                pass

    return first_existing


# ---------------------------------------------------------------------------
# Per-experiment query
# ---------------------------------------------------------------------------

def _week_start(ref: datetime) -> str:
    monday = ref - timedelta(days=ref.weekday())
    return monday.strftime("%Y-%m-%d")


def query_experiment(exp: dict, report_date: Optional[str] = None) -> dict:
    """Query one experiment's DB. Handles empty / missing DBs gracefully."""
    if report_date is None:
        report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    exp_id   = exp["id"]
    db_path  = resolve_db_path(exp)

    base: dict = {
        "id":          exp_id,
        "name":        exp.get("name", exp_id),
        "ticker":      exp.get("ticker", "SPY"),
        "creator":     exp.get("created_by", "—"),
        "live_since":  exp.get("live_since", "—"),
        "account_id":  exp.get("account_id", "—"),
        "db_path":     str(db_path) if db_path else "NOT FOUND",
        "db_found":    db_path is not None and db_path.exists(),
        "total_closed": 0,
        "wins":         0,
        "losses":       0,
        "win_rate":     0.0,
        "total_pnl":    0.0,
        "max_dd":       0.0,
        "open_count":   0,
        "avg_pnl":      0.0,
        "trades_week":  0,
        "last_trade":   None,
        "strategy_breakdown": {},
        "recent_trades": [],
        "open_trades":  [],
        "error":        None,
    }

    if not db_path or not db_path.exists():
        base["error"] = "Database not found"
        return base

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        placeholders = ",".join("?" * len(CLOSED_STATUSES))

        closed_rows = conn.execute(
            f"SELECT pnl, strategy_type, exit_date, entry_date, ticker, "
            f"       short_strike, long_strike, contracts, credit "
            f"FROM trades WHERE status IN ({placeholders}) ORDER BY exit_date",
            CLOSED_STATUSES,
        ).fetchall()

        pnls   = [float(r["pnl"] or 0) for r in closed_rows]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_pnl = sum(pnls)
        win_rate  = (len(wins) / len(pnls) * 100) if pnls else 0.0
        avg_pnl   = (total_pnl / len(pnls)) if pnls else 0.0

        # Max drawdown (dollar → %)
        cumulative = peak = max_dd_dollars = 0.0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd_dollars:
                max_dd_dollars = dd
        max_dd_pct = max_dd_dollars / STARTING_EQUITY * 100 if max_dd_dollars else 0.0

        # Trades this week
        ref_dt = datetime.strptime(report_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        week_start_str = _week_start(ref_dt)
        trades_week = sum(
            1 for r in closed_rows
            if str(r["exit_date"] or "")[:10] >= week_start_str
        )
        last_trade = str(closed_rows[-1]["exit_date"] or "")[:10] if closed_rows else None

        # Strategy breakdown
        strategy_breakdown: dict[str, dict] = {}
        for r in closed_rows:
            st  = (r["strategy_type"] or "unknown").replace("_", " ").title()
            p   = float(r["pnl"] or 0)
            if st not in strategy_breakdown:
                strategy_breakdown[st] = {"count": 0, "wins": 0, "pnl": 0.0}
            strategy_breakdown[st]["count"] += 1
            if p > 0:
                strategy_breakdown[st]["wins"] += 1
            strategy_breakdown[st]["pnl"] += p

        # Open positions
        open_rows = conn.execute(
            "SELECT ticker, strategy_type, entry_date, expiration, "
            "       short_strike, long_strike, contracts, credit "
            "FROM trades WHERE status = 'open'"
        ).fetchall()

        # Recent 10 closed
        recent = conn.execute(
            f"SELECT pnl, strategy_type, exit_date, entry_date, ticker, "
            f"       short_strike, long_strike, contracts, credit, exit_reason "
            f"FROM trades WHERE status IN ({placeholders}) "
            f"ORDER BY exit_date DESC LIMIT 10",
            CLOSED_STATUSES,
        ).fetchall()

        conn.close()

        base.update({
            "total_closed":       len(pnls),
            "wins":               len(wins),
            "losses":             len(losses),
            "win_rate":           win_rate,
            "total_pnl":          total_pnl,
            "max_dd":             max_dd_pct,
            "open_count":         len(open_rows),
            "avg_pnl":            avg_pnl,
            "trades_week":        trades_week,
            "last_trade":         last_trade,
            "strategy_breakdown": strategy_breakdown,
            "recent_trades":      [dict(r) for r in recent],
            "open_trades":        [dict(r) for r in open_rows],
        })

    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            base["error"] = "No trades yet — awaiting first trade"
        else:
            base["error"] = str(e)
    except Exception as e:
        base["error"] = str(e)

    return base


def query_all_live(report_date: Optional[str] = None) -> List[dict]:
    registry = load_registry()
    results = [
        query_experiment(exp, report_date)
        for exp in get_live_experiments(registry)
    ]
    # If all experiments have no DB (Railway), try pushed data
    pushed = load_pushed_data()
    if not results or all(r.get("error") == "Database not found" for r in results):
        if pushed and "experiments" in pushed:
            # Flatten nested sync format to match query_experiment output
            flattened = []
            for exp in pushed["experiments"]:
                stats = exp.get("stats", {})
                flat = {
                    "id":          exp.get("id"),
                    "name":        exp.get("name"),
                    "ticker":      exp.get("ticker", "SPY"),
                    "creator":     exp.get("creator", "—"),
                    "live_since":  exp.get("live_since", "—"),
                    "account_id":  exp.get("account_id", "—"),
                    "db_path":     "pushed",
                    "db_found":    True,
                    "total_closed": stats.get("total_closed", 0),
                    "wins":         stats.get("wins", 0),
                    "losses":       stats.get("losses", 0),
                    "win_rate":     stats.get("win_rate", 0.0),
                    "total_pnl":    stats.get("total_pnl", 0.0),
                    "max_dd":       stats.get("max_dd_pct", 0.0),
                    "open_count":   stats.get("open_count", 0),
                    "avg_pnl":      stats.get("avg_pnl", 0.0),
                    "trades_week":  stats.get("trades_week", 0),
                    "last_trade":   stats.get("last_trade_date"),
                    "strategy_breakdown": exp.get("strategy_breakdown", {}),
                    "recent_trades": exp.get("recent_trades", []),
                    "open_trades":  exp.get("open_positions", []),
                    "error":        exp.get("error"),
                    "alpaca":       exp.get("alpaca"),
                }
                flattened.append(flat)
            return flattened

    # We have local DBs — augment with alpaca data from pushed export if available
    if pushed and "experiments" in pushed:
        alp_by_id = {
            exp.get("id"): exp.get("alpaca")
            for exp in pushed["experiments"]
        }
        for r in results:
            if r.get("alpaca") is None:
                r["alpaca"] = alp_by_id.get(r["id"])

    return results


# ---------------------------------------------------------------------------
# Detailed trade / position queries (for JSON API endpoints)
# ---------------------------------------------------------------------------

def get_trades(exp: dict, limit: int = 100) -> list[dict]:
    db_path = resolve_db_path(exp)
    if not db_path or not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(CLOSED_STATUSES))
        rows = conn.execute(
            f"SELECT * FROM trades WHERE status IN ({placeholders}) "
            f"ORDER BY exit_date DESC LIMIT ?",
            (*CLOSED_STATUSES, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_positions(exp: dict) -> list[dict]:
    db_path = resolve_db_path(exp)
    if not db_path or not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY entry_date DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def summary_all() -> dict:
    """High-level summary for /api/v1/summary."""
    # Return the pushed summary directly if available (includes alpaca fields)
    pushed = load_pushed_data()
    if pushed and "summary" in pushed:
        return pushed["summary"]
    all_stats = query_all_live()
    total_pnl    = sum(s["total_pnl"]    for s in all_stats)
    total_closed = sum(s["total_closed"] for s in all_stats)
    total_open   = sum(s["open_count"]   for s in all_stats)
    total_wins   = sum(s["wins"]         for s in all_stats)
    combined_wr  = (total_wins / total_closed * 100) if total_closed else 0.0
    max_dd       = max((s["max_dd"] for s in all_stats), default=0.0)
    return {
        "experiments":    len(all_stats),
        "total_pnl":      round(total_pnl, 2),
        "total_pnl_pct":  round(total_pnl / STARTING_EQUITY * 100, 2),
        "total_closed":   total_closed,
        "total_open":     total_open,
        "combined_win_rate": round(combined_wr, 1),
        "max_drawdown_pct":  round(max_dd, 2),
        "experiments_detail": [
            {
                "id":          s["id"],
                "name":        s["name"],
                "ticker":      s["ticker"],
                "total_pnl":   round(s["total_pnl"], 2),
                "win_rate":    round(s["win_rate"], 1),
                "max_dd":      round(s["max_dd"], 2),
                "total_closed": s["total_closed"],
                "open_count":  s["open_count"],
                "error":       s.get("error"),
            }
            for s in all_stats
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
