"""
data.py — Data access layer for the paper trading dashboard.

Reads from:
  - Alpaca REST API          (live equity / positions / orders — primary)
  - experiments/registry.json  (experiment metadata)
  - configs/paper_*.yaml       (db_path resolution)
  - data/*/attix_*.db        (SQLite trade data — local dev only)
  - data/pushed_dashboard.json / dashboard_export.json (Railway fallback)

ATTIX_ROOT env var overrides the project root path.

===========================================================================
DATA-SOURCE PRIORITY  (the s["alpaca"] account block on each exp card)
===========================================================================
query_all_live() layers three sources by freshness. Each is tried in turn;
a fresher source OVERRIDES whatever a staler one already wrote. The path that
last populated s["alpaca"] is recorded on the dict as:

    data_source       : "live" | "pushed" | "local-db" | "empty"
    data_age_seconds  : int — age of that alpaca block (now − fetched_at)

  highest priority  ┌───────────────────────────────────────────────┐
        (freshest)  │ 1. LIVE  Alpaca REST API                      │  -> "live"
                    │    alpaca_live.get_all_live_alpaca()          │
                    │    real-time; fetched_at ~ now                │
                    └───────────────────────┬───────────────────────┘
                                  override if │ present
                    ┌───────────────────────▼───────────────────────┐
                    │ 2. PUSHED export (sentinel / worker snapshot)  │  -> "pushed"
                    │    data/dashboard_export.json|pushed_*.json    │
                    │    refreshed by the worker every few minutes   │
                    └───────────────────────┬───────────────────────┘
                                  override if │ present
                    ┌───────────────────────▼───────────────────────┐
                    │ 3. LOCAL on-disk fallback                      │  -> "local-db"
                    │    data/experiment_portfolio/<ID>.json         │
                    │    used only when no live/pushed alpaca exists │
                    └───────────────────────┬───────────────────────┘
         lowest                 nothing set  │
       priority   ┌─────────────────────────▼────────────────────────┐
       (stalest)  │ 4. EMPTY — card shows local-DB trade stats only,  │  -> "empty"
                  │    no Alpaca account block (no badge rendered)    │
                  └───────────────────────────────────────────────────┘

NOTE: local SQLite (resolve_db_path / query_experiment) always supplies the
trade-history stats; the priority above is specifically about which source
fed the live ACCOUNT block (equity/positions). When none does, the tag is
"empty" and html.py renders no freshness badge.
===========================================================================
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from experiments.manager import get_manager
from experiments.registry import LIVE_STATUSES

logger = logging.getLogger(__name__)

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

CLOSED_STATUSES = (
    "closed_profit", "closed_loss", "closed_manual",
    "closed_expiry", "closed_external",
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def get_live_experiments(registry: dict | None = None) -> list[dict]:
    if registry is None:
        exps = [
            e for e in get_manager().all().values()
            if e.get("status") in LIVE_STATUSES
        ]
    else:
        exps = [
            e for e in registry["experiments"].values()
            if e.get("status") in LIVE_STATUSES
        ]
    return sorted(exps, key=lambda e: e["id"])


def get_all_experiments(registry: dict | None = None) -> list[dict]:
    if registry is None:
        return sorted(get_manager().all().values(), key=lambda e: e["id"])
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
        # Try synced trade data from worker push
        try:
            import json as _json
            norm = exp_id.upper().replace("-", "")
            synced_path = PROJECT_ROOT / "data" / "experiment_trades" / f"{norm}.json"
            if synced_path.exists():
                synced = _json.loads(synced_path.read_text())
                trades = synced.get("trades", [])
                closed = [t for t in trades if t.get("status") in ("closed_profit", "closed_loss", "closed_external", "expired")]
                pnls = [float(t.get("pnl", 0) or 0) for t in closed]
                base["total_closed"] = len(closed)
                base["wins"] = sum(1 for p in pnls if p > 0)
                base["losses"] = sum(1 for p in pnls if p <= 0)
                base["total_pnl"] = sum(pnls)
                base["win_rate"] = (base["wins"] / len(pnls) * 100) if pnls else 0.0
                base["open_count"] = len(synced.get("open_trades", []))
                base["open_trades"] = synced.get("open_trades", [])
                base["recent_trades"] = trades[:10]
                base["db_found"] = True
                base["error"] = None
                return base
        except Exception:
            pass
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


def _age_seconds(iso_ts: Optional[str]) -> Optional[int]:
    """
    Seconds elapsed between an ISO-8601 timestamp and now (UTC).
    Returns None if the timestamp is missing or unparseable. Never negative.
    """
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))
    except Exception:
        return None


def query_all_live(report_date: Optional[str] = None) -> List[dict]:
    """
    Assemble per-experiment dashboard rows, layering account data by freshness.

    Each returned dict is tagged with:
      data_source       : "live" | "pushed" | "local-db" | "empty"
      data_age_seconds  : int | None — age of the s["alpaca"] block

    See the module docstring for the full data-source priority diagram.
    """
    results = [
        query_experiment(exp, report_date)
        for exp in get_live_experiments()
    ]

    # Build stats from local DB or fall back to pushed data
    pushed = load_pushed_data()
    if not results or all(r.get("error") == "Database not found" for r in results):
        if pushed and "experiments" in pushed:
            # Flatten nested sync format to match query_experiment output
            flattened = []
            pushed_ids: set[str] = set()
            for exp in pushed["experiments"]:
                pushed_ids.add(exp.get("id", ""))
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
                    "alpaca_equity_history": exp.get("alpaca_equity_history") or [],
                }
                # Provenance: this row's account block came from the pushed export.
                if flat["alpaca"]:
                    flat["data_source"] = "pushed"
                flattened.append(flat)
            # Keep registry-live experiments that aren't in the pushed export
            # so newly launched experiments still appear on the dashboard.
            for r in results:
                if r["id"] not in pushed_ids:
                    flattened.append(r)
            results = flattened
    else:
        # We have local DBs — augment with alpaca data from pushed export if available
        if pushed and "experiments" in pushed:
            by_id = {exp.get("id"): exp for exp in pushed["experiments"]}
            for r in results:
                pushed_exp = by_id.get(r["id"]) or {}
                if r.get("alpaca") is None:
                    r["alpaca"] = pushed_exp.get("alpaca")
                    if r["alpaca"]:
                        r["data_source"] = "pushed"
                # Always propagate equity history from pushed export (local DBs
                # don't store it — it's fetched from Alpaca during sync).
                if not r.get("alpaca_equity_history"):
                    r["alpaca_equity_history"] = pushed_exp.get("alpaca_equity_history") or []

    # --- Live Alpaca data (overrides pushed alpaca block when available) -----
    live_discovered = 0   # how many experiments have ALPACA_API_KEY_EXP* env keys
    live_injected = 0     # how many got usable (error-free) live data merged in
    try:
        from . import alpaca_live
        live_discovered = len(alpaca_live.discover_experiment_keys())
        live = alpaca_live.get_all_live_alpaca()
        if live:
            for r in results:
                norm_id = r["id"].upper().replace("-", "") if r.get("id") else ""
                alpaca_data = live.get(norm_id)
                if alpaca_data and not alpaca_data.get("error"):
                    # Preserve equity history from pushed data — not in live API
                    existing_history = r.get("alpaca_equity_history") or []
                    r["alpaca"] = alpaca_data
                    r["alpaca_equity_history"] = existing_history
                    # Update open_count to reflect live positions count
                    r["open_count"] = len(alpaca_data.get("positions") or [])
                    live_injected += 1
                    # Live API is the freshest source — overrides any pushed tag.
                    r["data_source"] = "live"
    except Exception as exc:
        logger.warning("[data] Live Alpaca fetch failed, using cached/pushed data: %s", exc)

    # Always log how many experiments had keys vs. actually got data so an empty
    # dashboard is diagnosable from a single log line.
    live_skipped = max(live_discovered - live_injected, 0)
    logger.info(
        "[data] live alpaca: discovered=%d injected=%d skipped=%d",
        live_discovered, live_injected, live_skipped,
    )

    # --- Worker-pushed portfolio fallback ------------------------------------
    # For experiments where live Alpaca keys aren't available in the dashboard
    # process, fall back to the portfolio JSON pushed by the worker after each scan.
    _portfolio_dir = PROJECT_ROOT / "data" / "experiment_portfolio"
    if _portfolio_dir.exists():
        for r in results:
            if r.get("alpaca") is not None:
                continue
            norm_id = r["id"].upper().replace("-", "") if r.get("id") else ""
            portfolio_path = _portfolio_dir / f"{norm_id}.json"
            if portfolio_path.exists():
                try:
                    r["alpaca"] = json.loads(portfolio_path.read_text())
                    r["data_source"] = "local-db"
                    r["_portfolio_path"] = portfolio_path
                except Exception:
                    pass

    # --- Diagnostic hint: WHY is Alpaca data empty? (rendered server-side) ----
    # After every fallback has run, classify each experiment still lacking live
    # equity so the HTML can surface a precise reason instead of always blaming
    # "credentials". Categories: keys-missing | exception | cache-empty | no-data.
    for r in results:
        alp = r.get("alpaca")
        if alp and alp.get("equity") is not None and not alp.get("error"):
            continue  # has usable data — no diagnostic needed
        if alp and alp.get("error"):
            r["alpaca_diag"] = "exception"
        elif live_discovered == 0:
            r["alpaca_diag"] = "keys-missing"
        elif alp is None:
            r["alpaca_diag"] = "no-data"
        else:
            r["alpaca_diag"] = "cache-empty"

    # --- Finalize provenance tags --------------------------------------------
    # Stamp data_source / data_age_seconds on every row. Rows whose account
    # block was never populated by any source above are tagged "empty".
    pushed_at = pushed.get("pushed_at") if pushed else None
    for r in results:
        alp = r.get("alpaca")
        src = r.get("data_source")
        if not alp or not src:
            r["data_source"] = "empty"
            r["data_age_seconds"] = None
            r.pop("_portfolio_path", None)
            continue

        age = _age_seconds(alp.get("fetched_at"))
        if age is None and src == "pushed":
            age = _age_seconds(pushed_at)
        if age is None and src == "local-db":
            p = r.pop("_portfolio_path", None)
            if p is not None:
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                    age = max(0, int((datetime.now(timezone.utc) - mtime).total_seconds()))
                except Exception:
                    age = None
        r["data_age_seconds"] = age if age is not None else 0
        r.pop("_portfolio_path", None)

    return results


# ---------------------------------------------------------------------------
# Detailed trade / position queries (for JSON API endpoints)
# ---------------------------------------------------------------------------

def get_trades(exp: dict, limit: int = 100) -> list[dict]:
    """
    Return closed trade history for an experiment.

    Priority:
      1. Local SQLite DB (has full trade detail + P&L)
      2. Alpaca order history (when no DB available on Railway)

    Alpaca orders are normalised to include an `alpaca_order` flag so
    callers can distinguish them from local DB trades.
    """
    db_path = resolve_db_path(exp)
    if db_path and db_path.exists():
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
            db_trades = [dict(r) for r in rows]
            if db_trades:
                return db_trades
        except Exception:
            pass

    # No DB trades — try Alpaca order history
    try:
        from . import alpaca_live
        alpaca_data = alpaca_live.get_live_alpaca(exp["id"])
        if alpaca_data and not alpaca_data.get("error"):
            orders = alpaca_data.get("orders") or []
            # Filter to filled orders only and normalise for API consumers
            filled = [
                {**o, "alpaca_order": True}
                for o in orders
                if o.get("status") == "filled"
            ]
            return filled[:limit]
    except Exception as exc:
        logger.warning("[data] get_trades Alpaca error for %s: %s", exp.get("id"), exc)

    return []


def get_positions(exp: dict) -> list[dict]:
    # Try live Alpaca positions first
    try:
        from . import alpaca_live
        alpaca_data = alpaca_live.get_live_alpaca(exp["id"])
        if alpaca_data and not alpaca_data.get("error"):
            positions = alpaca_data.get("positions") or []
            if positions or alpaca_data.get("equity") is not None:
                # We have a valid Alpaca response; return live positions
                return positions
    except Exception as exc:
        logger.warning("[data] get_positions Alpaca error for %s: %s", exp.get("id"), exc)

    # Fall back to local SQLite
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
    """High-level summary for /api/v1/summary.

    Always computed from query_all_live() so live Alpaca equity is included.
    The old pushed-summary shortcut is removed — live data is better.
    """
    all_stats = query_all_live()
    total_pnl    = sum(s["total_pnl"]    for s in all_stats)
    total_closed = sum(s["total_closed"] for s in all_stats)
    total_open   = sum(s["open_count"]   for s in all_stats)
    total_wins   = sum(s["wins"]         for s in all_stats)
    combined_wr  = (total_wins / total_closed * 100) if total_closed else 0.0
    max_dd       = max((s["max_dd"] for s in all_stats), default=0.0)

    # Live equity from Alpaca
    equities = [
        s["alpaca"]["equity"]
        for s in all_stats
        if s.get("alpaca") and s["alpaca"].get("equity") is not None
    ]
    total_equity      = sum(equities) if equities else None
    unrealized_totals = [
        s["alpaca"].get("unrealized_pl") or 0
        for s in all_stats
        if s.get("alpaca") and s["alpaca"].get("equity") is not None
    ]
    total_unrealized = sum(unrealized_totals) if equities else None

    return {
        "experiments":       len(all_stats),
        "total_pnl":         round(total_pnl, 2),
        "total_pnl_pct":     round(total_pnl / STARTING_EQUITY * 100, 2),
        "total_closed":      total_closed,
        "total_open":        total_open,
        "combined_win_rate": round(combined_wr, 1),
        "max_drawdown_pct":  round(max_dd, 2),
        "total_equity":      round(total_equity, 2) if total_equity is not None else None,
        "total_unrealized_pl": round(total_unrealized, 2) if total_unrealized is not None else None,
        "alpaca_accounts":   len(equities),
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
                "live_equity": s["alpaca"].get("equity") if s.get("alpaca") else None,
                "error":       s.get("error"),
            }
            for s in all_stats
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
