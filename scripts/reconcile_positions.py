#!/usr/bin/env python3
"""
reconcile_positions.py — broker↔DB reconciliation for one experiment.

Replaces the manual one-off `INSERT INTO trades …` block Charles ran
during the EXP-503 backfill on 2026-04-28. Pulls all open positions
from Alpaca, cross-references the experiment's trades DB by OCC symbol
(with tuple fallback on `(ticker, expiration, strike, type)`), and
prints a plan covering:

  - in_sync legs               (broker and DB agree)
  - qty_mismatches             (OCC matches but qty differs)
  - broker_only orphans        (grouped into spreads via spread_width)
  - db_only ghosts             (DB shows open, broker has no position)

Usage:
    python scripts/reconcile_positions.py --experiment EXP-503 --dry-run
    python scripts/reconcile_positions.py --experiment EXP-503 --apply

--dry-run is the default. With --apply, all inserts / updates run inside
a single sqlite transaction (rollback on any error). Metadata schema for
inserted recovery rows matches the live EXP-503 backfill format:

    metadata = {
        "recovery_source": "alpaca_backfill_<YYYY-MM-DD>",
        "short_alpaca_sym": "<short OCC>",
        "long_alpaca_sym": "<long OCC>",
    }

Hard guarantees:
- Never mutates sentinel_state.json
- Never sends an Alpaca order
- Per-experiment trade DB is the only mutation target
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml  # type: ignore[import]

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# OCC parsing
# ---------------------------------------------------------------------------


def parse_occ(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Parse an OCC option symbol into its components.

    Format: <ticker padded to 6><YYMMDD><P|C><strike×1000 padded to 8>
    Example: 'SPY   260508P00685000' → SPY 2026-05-08 P 685.0

    Returns None for non-OCC strings (equity tickers, garbage).
    """
    s = (symbol or "").strip()
    if len(s) < 15:
        return None
    try:
        strike_part = s[-8:]
        if not strike_part.isdigit():
            return None
        strike = int(strike_part) / 1000.0
        cp = s[-9].upper()
        if cp not in ("P", "C"):
            return None
        yymmdd = s[-15:-9]
        if not yymmdd.isdigit():
            return None
        ticker = s[:-15].rstrip()
        if not ticker:
            return None
        return {
            "ticker": ticker,
            "expiration": f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}",
            "type": cp,
            "strike": strike,
            "occ": s,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# IO boundaries (each individually patchable for tests)
# ---------------------------------------------------------------------------


def load_paper_config(exp_id: str) -> Dict[str, Any]:
    """Load paper_config YAML pointed to by sentinel_state.json[experiments][exp_id]."""
    state_path = PROJECT_ROOT / "sentinel_state.json"
    state = json.loads(state_path.read_text())
    exp = state.get("experiments", {}).get(exp_id)
    if not exp:
        raise KeyError(f"{exp_id} not in sentinel_state.json")
    paper_config = exp.get("paper_config")
    if not paper_config:
        raise KeyError(f"{exp_id} has no paper_config in sentinel_state.json")
    cfg_path = PROJECT_ROOT / paper_config
    with open(cfg_path) as f:
        return yaml.safe_load(f) or {}


def _resolve_db_path_arg(exp_id: str, cfg: Dict[str, Any]) -> str:
    db_path = cfg.get("db_path")
    if not db_path:
        raise KeyError(f"{exp_id} paper_config has no db_path")
    p = PROJECT_ROOT / db_path
    return str(p)


def _resolve_env_file(exp_id: str) -> Path:
    numeric = exp_id.removeprefix("EXP-").lower()
    return PROJECT_ROOT / f".env.exp{numeric}"


def get_alpaca_positions(env_file: Path) -> List[Dict[str, Any]]:
    """Load .env.exp{xxx} credentials and pull all open positions from Alpaca."""
    env: Dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    api_key = env.get("ALPACA_API_KEY")
    api_secret = env.get("ALPACA_API_SECRET") or env.get("ALPACA_SECRET_KEY")
    if not api_key or not api_secret:
        raise RuntimeError(f"{env_file}: missing ALPACA_API_KEY / ALPACA_API_SECRET")
    paper = env.get("ALPACA_PAPER", "true").lower() != "false"

    from alpaca.trading.client import TradingClient  # type: ignore[import]

    client = TradingClient(api_key, api_secret, paper=paper)
    positions = client.get_all_positions()
    out: List[Dict[str, Any]] = []
    for p in positions:
        try:
            qty = int(float(p.qty))
        except Exception:
            qty = 0
        out.append({"symbol": str(p.symbol), "qty": qty})
    return out


def fetch_db_legs(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return open trade_legs joined with their parent trade as plain dicts."""
    rows = conn.execute(
        "SELECT t.id AS trade_id, t.ticker, t.short_strike, t.long_strike, "
        "t.expiration, t.contracts, t.status, t.metadata, "
        "tl.id AS leg_id, tl.leg_type, tl.strike, tl.occ_symbol "
        "FROM trade_legs tl "
        "JOIN trades t ON tl.trade_id = t.id "
        "WHERE t.status IN ('open', 'pending_open', 'pending_close')"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Plan construction (pure)
# ---------------------------------------------------------------------------


def _signed_qty_from_leg(leg: Dict[str, Any]) -> Optional[int]:
    """Infer signed contract qty from leg_type + parent trade.contracts."""
    contracts = leg.get("contracts")
    try:
        contracts = int(contracts) if contracts is not None else 0
    except (TypeError, ValueError):
        return None
    if contracts <= 0:
        return None
    lt = (leg.get("leg_type") or "").lower()
    if lt.startswith("short"):
        return -contracts
    if lt.startswith("long"):
        return contracts
    return None


def _leg_type_to_cp(leg_type: Optional[str]) -> Optional[str]:
    lt = (leg_type or "").lower()
    if "put" in lt:
        return "P"
    if "call" in lt:
        return "C"
    return None


def build_plan(
    exp_id: str,
    alpaca_positions: List[Dict[str, Any]],
    db_legs: List[Dict[str, Any]],
    spread_width: float,
) -> Dict[str, Any]:
    """
    Cross-reference broker positions against open DB legs.

    Returns a dict with keys:
      in_sync, qty_mismatches, broker_only, db_only, spreads_inferred
    """
    # --- DB indexes ---
    db_by_occ: Dict[str, Dict[str, Any]] = {}
    db_by_tuple: Dict[Tuple[str, str, float, str], Dict[str, Any]] = {}
    for leg in db_legs:
        occ = leg.get("occ_symbol")
        if occ:
            db_by_occ[str(occ).upper()] = leg
        cp = _leg_type_to_cp(leg.get("leg_type"))
        if cp and leg.get("ticker") and leg.get("expiration") and leg.get("strike") is not None:
            tup = (
                str(leg["ticker"]).upper(),
                str(leg["expiration"]),
                float(leg["strike"]),
                cp,
            )
            db_by_tuple.setdefault(tup, leg)

    # --- Broker pass ---
    in_sync: List[Dict[str, Any]] = []
    qty_mismatches: List[Dict[str, Any]] = []
    broker_only: List[Dict[str, Any]] = []
    matched_leg_ids: set = set()

    for pos in alpaca_positions:
        sym_raw = pos.get("symbol") or ""
        sym = sym_raw.upper()
        broker_qty = int(pos.get("qty", 0) or 0)
        parsed = parse_occ(sym)

        leg = db_by_occ.get(sym)
        if leg is None and parsed is not None:
            tup = (
                parsed["ticker"].upper(),
                parsed["expiration"],
                parsed["strike"],
                parsed["type"],
            )
            leg = db_by_tuple.get(tup)

        if leg is not None:
            matched_leg_ids.add(leg["leg_id"])
            db_qty = _signed_qty_from_leg(leg)
            if db_qty is None or db_qty == broker_qty:
                in_sync.append({"occ": sym, "leg": leg, "broker_qty": broker_qty})
            else:
                qty_mismatches.append({
                    "occ": sym,
                    "leg": leg,
                    "broker_qty": broker_qty,
                    "db_qty": db_qty,
                })
            continue

        broker_only.append({"occ": sym, "qty": broker_qty, "parsed": parsed})

    # --- DB-only (ghosts) ---
    db_only: List[Dict[str, Any]] = []
    for leg in db_legs:
        if leg["leg_id"] in matched_leg_ids:
            continue
        db_only.append({"leg": leg})

    # --- Group orphans into proposed spreads ---
    spreads_inferred = _infer_spreads(broker_only, spread_width)

    return {
        "in_sync": in_sync,
        "qty_mismatches": qty_mismatches,
        "broker_only": broker_only,
        "db_only": db_only,
        "spreads_inferred": spreads_inferred,
    }


def _infer_spreads(
    broker_only: List[Dict[str, Any]], spread_width: float
) -> List[Dict[str, Any]]:
    """Pair short+long orphan legs differing by exactly *spread_width*."""
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for o in broker_only:
        parsed = o.get("parsed")
        if not parsed:
            continue
        key = (parsed["ticker"].upper(), parsed["expiration"], parsed["type"])
        grouped[key].append({**parsed, "qty": o["qty"]})

    out: List[Dict[str, Any]] = []
    for (ticker, exp, cp), legs in grouped.items():
        shorts = sorted([l for l in legs if l["qty"] < 0], key=lambda x: x["strike"])
        longs = sorted([l for l in legs if l["qty"] > 0], key=lambda x: x["strike"])
        used_long_idx: set = set()
        for s in shorts:
            for i, l in enumerate(longs):
                if i in used_long_idx:
                    continue
                if abs(s["strike"] - l["strike"]) == float(spread_width):
                    used_long_idx.add(i)
                    out.append({
                        "ticker": ticker,
                        "expiration": exp,
                        "type": cp,
                        "short_strike": s["strike"],
                        "long_strike": l["strike"],
                        "contracts": abs(int(s["qty"])),
                        "short_alpaca_sym": s["occ"],
                        "long_alpaca_sym": l["occ"],
                    })
                    break
    return out


# ---------------------------------------------------------------------------
# Apply (single sqlite transaction)
# ---------------------------------------------------------------------------


def apply_plan(conn: sqlite3.Connection, plan: Dict[str, Any]) -> Dict[str, int]:
    """Insert spreads, close ghosts, update qty mismatches — atomic."""
    today = date.today().isoformat()
    recovery_source = f"alpaca_backfill_{today}"

    inserted = 0
    closed = 0
    qty_updated = 0

    with conn:  # commits on success, rolls back on exception
        for sp in plan.get("spreads_inferred", []):
            trade_id = str(uuid.uuid4())
            cp = sp["type"]
            if cp == "P" and sp["short_strike"] > sp["long_strike"]:
                strategy_type = "bull_put"
            elif cp == "C" and sp["short_strike"] < sp["long_strike"]:
                strategy_type = "bear_call"
            else:
                strategy_type = f"{cp}_spread"
            metadata = {
                "recovery_source": recovery_source,
                "short_alpaca_sym": sp["short_alpaca_sym"],
                "long_alpaca_sym": sp["long_alpaca_sym"],
            }
            conn.execute(
                "INSERT INTO trades "
                "(id, source, ticker, strategy_type, status, "
                " short_strike, long_strike, expiration, contracts, "
                " entry_date, metadata) "
                "VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)",
                (
                    trade_id, recovery_source, sp["ticker"], strategy_type,
                    sp["short_strike"], sp["long_strike"], sp["expiration"],
                    sp["contracts"], today, json.dumps(metadata),
                ),
            )
            short_leg_type = "short_put" if cp == "P" else "short_call"
            long_leg_type = "long_put" if cp == "P" else "long_call"
            conn.execute(
                "INSERT INTO trade_legs (trade_id, leg_type, strike, occ_symbol) "
                "VALUES (?, ?, ?, ?)",
                (trade_id, short_leg_type, sp["short_strike"], sp["short_alpaca_sym"]),
            )
            conn.execute(
                "INSERT INTO trade_legs (trade_id, leg_type, strike, occ_symbol) "
                "VALUES (?, ?, ?, ?)",
                (trade_id, long_leg_type, sp["long_strike"], sp["long_alpaca_sym"]),
            )
            inserted += 1

        seen_ghost_trades: set = set()
        for ghost in plan.get("db_only", []):
            trade_id = ghost["leg"]["trade_id"]
            if trade_id in seen_ghost_trades:
                continue
            seen_ghost_trades.add(trade_id)
            conn.execute(
                "UPDATE trades SET status = 'closed_external', "
                "exit_reason = ?, exit_date = ?, "
                "updated_at = datetime('now') "
                "WHERE id = ?",
                (recovery_source, today, trade_id),
            )
            closed += 1

        seen_qty_trades: set = set()
        for qm in plan.get("qty_mismatches", []):
            trade_id = qm["leg"]["trade_id"]
            if trade_id in seen_qty_trades:
                continue
            seen_qty_trades.add(trade_id)
            row = conn.execute(
                "SELECT metadata FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            if row is None:
                continue
            existing = row[0]
            try:
                meta = json.loads(existing) if existing else {}
                if not isinstance(meta, dict):
                    meta = {}
            except (TypeError, ValueError, json.JSONDecodeError):
                meta = {}
            recs = meta.get("qty_reconciliations")
            if not isinstance(recs, list):
                recs = []
            recs.append({
                "at": today,
                "broker_qty": qm["broker_qty"],
                "db_qty": qm["db_qty"],
                "source": recovery_source,
            })
            meta["qty_reconciliations"] = recs
            conn.execute(
                "UPDATE trades SET contracts = ?, metadata = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (abs(int(qm["broker_qty"])), json.dumps(meta), trade_id),
            )
            qty_updated += 1

    return {
        "inserted_spreads": inserted,
        "closed_ghosts": closed,
        "qty_updated": qty_updated,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def print_plan(plan: Dict[str, Any]) -> None:
    print(f"  in_sync legs:        {len(plan['in_sync'])}")
    print(f"  qty_mismatches:      {len(plan['qty_mismatches'])}")
    print(f"  broker_only legs:    {len(plan['broker_only'])}")
    print(f"  db_only (ghosts):    {len(plan['db_only'])}")
    print(f"  spreads inferred:    {len(plan['spreads_inferred'])}")

    if plan["spreads_inferred"]:
        print()
        print("  Proposed open rows (recovery_source=alpaca_backfill_<TODAY>):")
        for sp in plan["spreads_inferred"]:
            print(
                f"    {sp['ticker']} {sp['expiration']} {sp['type']} "
                f"short={sp['short_strike']} long={sp['long_strike']} "
                f"qty={sp['contracts']}"
            )

    if plan["qty_mismatches"]:
        print()
        print("  Qty mismatches (broker_qty → db.contracts will be updated):")
        seen: set = set()
        for qm in plan["qty_mismatches"]:
            tid = qm["leg"]["trade_id"]
            if tid in seen:
                continue
            seen.add(tid)
            print(
                f"    {qm['occ']}: broker_qty={qm['broker_qty']} "
                f"db_qty={qm['db_qty']} (trade_id={tid})"
            )

    if plan["db_only"]:
        print()
        print("  Proposed closed_external rows (DB has, broker doesn't):")
        seen2: set = set()
        for g in plan["db_only"]:
            tid = g["leg"]["trade_id"]
            if tid in seen2:
                continue
            seen2.add(tid)
            print(f"    trade_id={tid} ({g['leg']['ticker']})")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile Alpaca positions vs an experiment's trades DB.",
    )
    parser.add_argument("--experiment", required=True, help="EXP-XXX")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true",
                     help="Preview only (default).")
    grp.add_argument("--apply", action="store_true",
                     help="Execute the reconciliation inside one transaction.")
    args = parser.parse_args(argv)

    apply_mode = bool(args.apply)
    exp_id = args.experiment

    cfg = load_paper_config(exp_id)
    db_path = _resolve_db_path_arg(exp_id, cfg)
    spread_width = float(cfg.get("strategy", {}).get("spread_width", 5))
    env_file = _resolve_env_file(exp_id)

    alpaca_positions = get_alpaca_positions(env_file)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        db_legs = fetch_db_legs(conn)
        plan = build_plan(exp_id, alpaca_positions, db_legs, spread_width)

        mode = "APPLY" if apply_mode else "DRY-RUN"
        print(f"Reconciliation plan for {exp_id} ({mode}):")
        print_plan(plan)

        if apply_mode:
            print()
            print("Applying inside single sqlite transaction...")
            stats = apply_plan(conn, plan)
            print(f"  result: {stats}")
        else:
            print()
            print("DRY-RUN — no changes. Re-run with --apply to commit.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
