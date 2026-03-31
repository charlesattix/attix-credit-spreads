#!/usr/bin/env python3
"""
register_orphan_positions.py — CRITICAL FIX 3

For each active Alpaca account (exp400, exp401, exp503, exp600):
  1. Pull all open option positions from Alpaca API
  2. Cross-reference with the experiment's DB (proper trades + existing stubs)
  3. Create PROPER trade records for any position with no matching DB entry
  4. Upgrade existing orphan stubs (short_strike=0, no exp) with real data

Matching: a DB trade "covers" an Alpaca position if:
  - The position's OCC symbol can be reconstructed from the trade's legs  (proper match)
  - OR  the position's OCC symbol appears in any existing orphan record's
        metadata.alpaca_symbol  (stub match)

For uncovered positions: pair short+long call/put legs into one spread record.
Lone unmatched legs get individual unmanaged records.

Special handling: --order 1c9927db looked up via /v2/orders on the exp401 account.

Usage:
    python3 scripts/register_orphan_positions.py          # dry-run, report only
    python3 scripts/register_orphan_positions.py --fix    # write to DBs
    python3 scripts/register_orphan_positions.py --fix --verbose
"""

import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from dotenv import dotenv_values
except ImportError:
    sys.exit("ERROR: python-dotenv not installed. Run: pip install python-dotenv")

ROOT     = Path(__file__).resolve().parent.parent
BASE_URL = "https://paper-api.alpaca.markets"
TIMEOUT  = 20

# ---------------------------------------------------------------------------
# Account map  (DB path comes from .env when available)
# ---------------------------------------------------------------------------

ACCOUNTS: Dict[str, dict] = {
    "exp400": {
        "env":   ".env.exp400",
        "db":    ROOT / "data" / "pilotai_exp400.db",   # no PILOTAI_DB_PATH in .env
        "label": "EXP-400 The Champion",
    },
    "exp401": {
        "env":   ".env.exp401",
        "label": "EXP-401 The Blend",
    },
    "exp503": {
        "env":   ".env.exp503",
        "label": "EXP-503 ML V2 Aggressive",
    },
    "exp600": {
        "env":   ".env.exp600",
        "label": "EXP-600 IBIT Adaptive",
    },
}

MAXIMUS_ORDER_ID = "1c9927db"          # on exp401

# ---------------------------------------------------------------------------
# Env / DB helpers
# ---------------------------------------------------------------------------

def load_env(env_file: str) -> dict:
    path = ROOT / env_file
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    return dotenv_values(str(path))


def resolve_db(name: str, cfg: dict, env: dict) -> Path:
    """Return the canonical DB path for this account."""
    if "db" in cfg:
        return cfg["db"]
    db_str = env.get("PILOTAI_DB_PATH", "")
    if db_str:
        p = ROOT / db_str
        return p
    raise ValueError(f"Cannot determine DB path for {name}")


# ---------------------------------------------------------------------------
# Alpaca API  (plain urllib, no requests dependency assumed)
# ---------------------------------------------------------------------------

def _alpaca_get(endpoint: str, key: str, secret: str) -> object:
    url = f"{BASE_URL}{endpoint}"
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID":     key,
        "APCA-API-SECRET-KEY": secret,
        "Accept":              "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:200]}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(str(e))


def fetch_positions(key: str, secret: str) -> List[dict]:
    data = _alpaca_get("/v2/positions", key, secret)
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected positions response: {data}")
    # Keep only option positions
    opts = [
        p for p in data
        if (p.get("asset_class", "").lower() in ("us_option", "option")
            or len(p.get("symbol", "")) > 10)
    ]
    return opts


def fetch_order(order_id: str, key: str, secret: str) -> Optional[dict]:
    """Fetch a single order by client_order_id prefix or order id."""
    # Try by order_id first
    try:
        return _alpaca_get(f"/v2/orders/{order_id}", key, secret)
    except RuntimeError:
        pass
    # Try orders list filtered by client_order_id
    try:
        orders = _alpaca_get("/v2/orders?status=all&limit=200", key, secret)
        if isinstance(orders, list):
            for o in orders:
                if (o.get("id", "").startswith(order_id)
                        or o.get("client_order_id", "").startswith(order_id)):
                    return o
    except RuntimeError:
        pass
    return None


# ---------------------------------------------------------------------------
# OCC symbol parsing / reconstruction
# ---------------------------------------------------------------------------

OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def parse_occ(symbol: str) -> Optional[dict]:
    m = OCC_RE.match(symbol.strip().upper())
    if not m:
        return None
    ticker, yymmdd, cp, strike_raw = m.groups()
    return {
        "ticker":      ticker,
        "expiration":  f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}",
        "option_type": "call" if cp == "C" else "put",
        "strike":      int(strike_raw) / 1000.0,
        "symbol":      symbol,
    }


def occ_from_trade(row: dict) -> set:
    """Reconstruct OCC symbols for both legs of a DB trade."""
    ticker  = (row.get("ticker") or "").upper()
    exp_raw = str(row.get("expiration") or "").split("T")[0]
    stype   = str(row.get("strategy_type") or "").lower()
    short   = row.get("short_strike")
    long_   = row.get("long_strike")

    if not ticker or not exp_raw or len(exp_raw) < 10:
        return set()

    yy, mm, dd = exp_raw[2:4], exp_raw[5:7], exp_raw[8:10]
    opt = "P" if "put" in stype else "C"

    syms: set = set()
    for strike in (short, long_):
        if strike is not None:
            syms.add(f"{ticker}{yy}{mm}{dd}{opt}{int(float(strike) * 1000):08d}")
    return syms


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

MANAGED_STATUSES = ("open", "pending_open", "unmanaged")


def load_db_coverage(db_path: Path) -> Tuple[set, set, List[dict]]:
    """
    Returns:
        covered_syms  : OCC symbols covered by proper (non-stub) trades
        stub_syms     : OCC symbols covered by existing orphan stubs
        stub_records  : rows for stubs that need upgrading (short_strike==0 or exp=='')
    """
    covered: set = set()
    stubs:   set = set()
    stub_rows: List[dict] = []

    if not db_path.exists():
        return covered, stubs, stub_rows

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(MANAGED_STATUSES))
    rows = conn.execute(
        f"SELECT id, ticker, strategy_type, status, short_strike, long_strike, "
        f"expiration, contracts, metadata FROM trades WHERE status IN ({placeholders})",
        MANAGED_STATUSES,
    ).fetchall()
    conn.close()

    for row in rows:
        d = dict(row)
        # Try metadata.alpaca_symbol for existing stubs
        meta_sym = None
        try:
            meta = json.loads(d.get("metadata") or "{}")
            meta_sym = meta.get("alpaca_symbol")
        except (json.JSONDecodeError, TypeError):
            pass

        # Is this a stub record (incomplete data)?
        is_stub = (
            (d.get("short_strike") is None or float(d.get("short_strike") or 0) == 0)
            and (d.get("long_strike") is None or float(d.get("long_strike") or 0) == 0)
        )

        if meta_sym:
            stubs.add(meta_sym)
            if is_stub:
                stub_rows.append({**d, "_meta_sym": meta_sym})
        else:
            # Reconstruct OCC symbols from the trade legs
            covered |= occ_from_trade(d)

    return covered, stubs, stub_rows


# ---------------------------------------------------------------------------
# Pairing logic: match short + long legs of same spread
# ---------------------------------------------------------------------------

def pair_legs(positions: List[dict]) -> List[Tuple[Optional[dict], Optional[dict]]]:
    """
    Given a list of Alpaca option positions (same expiration + option_type),
    pair short legs with long legs to form spreads.

    Returns list of (short_pos, long_pos) tuples.
    Lone unmatched legs produce (pos, None) or (None, pos).
    """
    shorts = [p for p in positions if str(p.get("side", "")).lower() == "short"]
    longs  = [p for p in positions if str(p.get("side", "")).lower() == "long"]

    pairs: List[Tuple[Optional[dict], Optional[dict]]] = []
    used_longs = set()

    for sp in shorts:
        sp_info = parse_occ(sp["symbol"])
        if not sp_info:
            pairs.append((sp, None))
            continue

        best_long = None
        for i, lp in enumerate(longs):
            if i in used_longs:
                continue
            lp_info = parse_occ(lp["symbol"])
            if not lp_info:
                continue
            # Same expiration and option type — any width qualifies as a spread pair
            if lp_info["expiration"] == sp_info["expiration"]:
                best_long = (i, lp)
                break   # take first match; if multiple assume closest width

        if best_long:
            used_longs.add(best_long[0])
            pairs.append((sp, best_long[1]))
        else:
            pairs.append((sp, None))

    for i, lp in enumerate(longs):
        if i not in used_longs:
            pairs.append((None, lp))

    return pairs


# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_spread_record(db_path: Path, short_pos: dict, long_pos: dict,
                         trade_id: str, source: str = "register_orphan_v3") -> bool:
    """Insert one proper spread record from two Alpaca legs."""
    sp_info = parse_occ(short_pos["symbol"])
    lp_info = parse_occ(long_pos["symbol"])
    if not sp_info or not lp_info:
        return False

    ticker   = sp_info["ticker"]
    exp      = sp_info["expiration"]
    opt_type = sp_info["option_type"]
    stype    = "bear_call" if opt_type == "call" else "bull_put"

    short_strike = sp_info["strike"]
    long_strike  = lp_info["strike"]
    contracts    = abs(int(short_pos.get("qty") or 0))
    # Net credit: avg_entry_price short leg (received) minus avg_entry_price long leg (paid)
    sp_price = float(short_pos.get("avg_entry_price") or 0)
    lp_price = float(long_pos.get("avg_entry_price") or 0)
    credit   = round(sp_price - lp_price, 4)
    mkt_val  = float(short_pos.get("market_value") or 0) + float(long_pos.get("market_value") or 0)

    meta = json.dumps({
        "alpaca_short_symbol": short_pos["symbol"],
        "alpaca_long_symbol":  long_pos["symbol"],
        "short_qty":           short_pos.get("qty"),
        "long_qty":            long_pos.get("qty"),
        "short_entry_price":   sp_price,
        "long_entry_price":    lp_price,
        "market_value":        mkt_val,
        "registered_by":       "register_orphan_positions.py",
    })

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        INSERT INTO trades
            (id, source, ticker, strategy_type, status,
             short_strike, long_strike, expiration, credit, contracts,
             entry_date, created_at, updated_at, metadata)
        VALUES (?, ?, ?, ?, 'open',
                ?, ?, ?, ?, ?,
                ?, datetime('now'), datetime('now'), ?)
        ON CONFLICT(id) DO UPDATE SET
            strategy_type = excluded.strategy_type,
            status        = excluded.status,
            short_strike  = excluded.short_strike,
            long_strike   = excluded.long_strike,
            expiration    = excluded.expiration,
            credit        = excluded.credit,
            contracts     = excluded.contracts,
            updated_at    = datetime('now'),
            metadata      = excluded.metadata
    """, (
        trade_id, source, ticker, stype,
        short_strike, long_strike, exp, credit, contracts,
        _now_iso(), meta,
    ))
    conn.commit()
    conn.close()
    return True


def insert_single_leg_record(db_path: Path, pos: dict,
                             source: str = "register_orphan_v3") -> bool:
    """Insert a single unmatched leg as an unmanaged record."""
    info = parse_occ(pos["symbol"])
    if not info:
        return False

    side     = str(pos.get("side", "")).lower()
    opt_type = info["option_type"]
    stype    = "bear_call" if opt_type == "call" else "bull_put"
    contracts = abs(int(pos.get("qty") or 0))
    price    = float(pos.get("avg_entry_price") or 0)

    trade_id = f"unmanaged-{pos['symbol']}"

    short_strike = info["strike"] if side == "short" else None
    long_strike  = info["strike"] if side == "long"  else None
    credit       = price if side == "short" else 0.0

    meta = json.dumps({
        "alpaca_symbol":     pos["symbol"],
        "side":              side,
        "avg_entry_price":   price,
        "qty":               pos.get("qty"),
        "market_value":      pos.get("market_value"),
        "registered_by":     "register_orphan_positions.py",
    })

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        INSERT INTO trades
            (id, source, ticker, strategy_type, status,
             short_strike, long_strike, expiration, credit, contracts,
             entry_date, created_at, updated_at, metadata)
        VALUES (?, ?, ?, ?, 'unmanaged',
                ?, ?, ?, ?, ?,
                ?, datetime('now'), datetime('now'), ?)
        ON CONFLICT(id) DO UPDATE SET
            short_strike = excluded.short_strike,
            long_strike  = excluded.long_strike,
            expiration   = excluded.expiration,
            contracts    = excluded.contracts,
            updated_at   = datetime('now'),
            metadata     = excluded.metadata
    """, (
        trade_id, source, info["ticker"], stype,
        short_strike, long_strike, info["expiration"],
        credit, contracts,
        _now_iso(), meta,
    ))
    conn.commit()
    conn.close()
    return True


def upgrade_stub_record(db_path: Path, stub: dict, pos: dict) -> bool:
    """
    Update an existing orphan stub record (short_strike=0, exp='') with
    real data from the Alpaca position.
    """
    info = parse_occ(stub["_meta_sym"])
    if not info:
        return False

    side     = str(pos.get("side", "")).lower()
    contracts = abs(int(pos.get("qty") or 0))
    price    = float(pos.get("avg_entry_price") or 0)

    short_strike = info["strike"] if side == "short" else None
    long_strike  = info["strike"] if side == "long"  else None
    stype_new    = "bear_call" if info["option_type"] == "call" else "bull_put"
    credit       = price if side == "short" else 0.0

    old_meta: dict = {}
    try:
        old_meta = json.loads(stub.get("metadata") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    old_meta.update({
        "avg_entry_price": price,
        "qty":             pos.get("qty"),
        "market_value":    pos.get("market_value"),
        "upgraded_by":     "register_orphan_positions.py",
        "upgraded_at":     _now_iso(),
    })

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        UPDATE trades SET
            strategy_type = ?,
            short_strike  = ?,
            long_strike   = ?,
            expiration    = ?,
            credit        = ?,
            contracts     = ?,
            updated_at    = datetime('now'),
            metadata      = ?
        WHERE id = ?
    """, (
        stype_new,
        short_strike, long_strike, info["expiration"],
        credit, contracts,
        json.dumps(old_meta),
        stub["id"],
    ))
    conn.commit()
    conn.close()
    return True


def insert_order_record(db_path: Path, order: dict, ticker: str,
                        source: str = "register_orphan_v3") -> Optional[str]:
    """
    Insert a trade record from an Alpaca order (multi-leg or single).
    Returns the trade_id inserted, or None if skipped.
    """
    order_id   = order.get("id", "")
    client_id  = order.get("client_order_id", "")
    status     = order.get("status", "")
    legs       = order.get("legs") or []
    symbol     = order.get("symbol", "")

    if status not in ("filled", "partially_filled"):
        return None   # don't register unfilled orders

    # Multi-leg order (mleg)
    if order.get("order_class") == "mleg" and legs:
        short_leg = next((l for l in legs if l.get("side","").lower() in ("sell","sell_short")), None)
        long_leg  = next((l for l in legs if l.get("side","").lower() in ("buy",)), None)
    else:
        # Single-leg — determine from order side
        side = order.get("side", "").lower()
        if side in ("sell", "sell_short"):
            short_leg = order
            long_leg  = None
        else:
            short_leg = None
            long_leg  = order

    sp_info = parse_occ((short_leg or {}).get("symbol", "") if short_leg else "") if short_leg else None
    lp_info = parse_occ((long_leg  or {}).get("symbol", "") if long_leg  else "") if long_leg  else None

    if not sp_info and not lp_info:
        return None

    ref = sp_info or lp_info
    opt_type = ref["option_type"]
    stype    = "bear_call" if opt_type == "call" else "bull_put"
    exp      = ref["expiration"]
    tk       = ref["ticker"] or ticker

    short_strike = sp_info["strike"] if sp_info else None
    long_strike  = lp_info["strike"] if lp_info else None

    contracts = abs(int(order.get("filled_qty") or order.get("qty") or 0))
    sp_price  = float((short_leg or {}).get("filled_avg_price") or 0) if short_leg else 0.0
    lp_price  = float((long_leg  or {}).get("filled_avg_price") or 0) if long_leg  else 0.0
    credit    = round(sp_price - lp_price, 4) if (sp_price and lp_price) else sp_price

    trade_id = f"order-{order_id[:8]}"
    meta = json.dumps({
        "alpaca_order_id":       order_id,
        "alpaca_client_order_id": client_id,
        "order_status":          status,
        "short_leg_symbol":      (short_leg or {}).get("symbol"),
        "long_leg_symbol":       (long_leg  or {}).get("symbol"),
        "registered_by":         "register_orphan_positions.py",
    })

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    # check if already exists by alpaca_client_order_id
    existing = conn.execute(
        "SELECT id FROM trades WHERE alpaca_client_order_id = ?", (client_id,)
    ).fetchone()
    if existing:
        conn.close()
        return None  # already registered

    conn.execute("""
        INSERT INTO trades
            (id, source, ticker, strategy_type, status,
             short_strike, long_strike, expiration, credit, contracts,
             entry_date, created_at, updated_at, metadata,
             alpaca_client_order_id, alpaca_status)
        VALUES (?, ?, ?, ?, 'open',
                ?, ?, ?, ?, ?,
                ?, datetime('now'), datetime('now'), ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
    """, (
        trade_id, source, tk, stype,
        short_strike, long_strike, exp, credit, contracts,
        _now_iso(), meta, client_id, status,
    ))
    conn.commit()
    conn.close()
    return trade_id


# ---------------------------------------------------------------------------
# Per-account processing
# ---------------------------------------------------------------------------

def process_account(name: str, cfg: dict, fix: bool, verbose: bool) -> dict:
    result = {
        "name":     name,
        "label":    cfg["label"],
        "error":    None,
        "alpaca_positions": 0,
        "already_covered": 0,
        "stub_upgrades":   0,
        "new_spreads":     0,
        "new_singles":     0,
        "order_records":   0,
        "actions":         [],
    }

    try:
        env = load_env(cfg["env"])
    except FileNotFoundError as e:
        result["error"] = str(e)
        return result

    key    = env.get("ALPACA_API_KEY", "")
    secret = env.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        result["error"] = f"Missing credentials in {cfg['env']}"
        return result

    try:
        db_path = resolve_db(name, cfg, env)
    except ValueError as e:
        result["error"] = str(e)
        return result

    # ----- Fetch Alpaca positions -----
    try:
        positions = fetch_positions(key, secret)
    except RuntimeError as e:
        result["error"] = f"Alpaca API error: {e}"
        return result

    result["alpaca_positions"] = len(positions)
    if not positions:
        return result

    # ----- Special: handle Maximus order on exp401 -----
    if name == "exp401":
        try:
            order = fetch_order(MAXIMUS_ORDER_ID, key, secret)
            if order and order.get("status") in ("filled", "partially_filled"):
                result["actions"].append({
                    "kind": "order",
                    "order_id": order.get("id",""),
                    "client_order_id": order.get("client_order_id",""),
                    "symbol": order.get("symbol",""),
                    "status": order.get("status",""),
                    "legs": len(order.get("legs") or []),
                })
                if fix:
                    tid = insert_order_record(db_path, order, "SPY")
                    if tid:
                        result["order_records"] += 1
                        result["actions"][-1]["inserted_as"] = tid
                    else:
                        result["actions"][-1]["inserted_as"] = "SKIPPED (already exists or unfilled)"
            elif order:
                result["actions"].append({
                    "kind": "order",
                    "order_id": order.get("id",""),
                    "status": order.get("status",""),
                    "note": "Not filled — skipping",
                })
            else:
                result["actions"].append({
                    "kind": "order",
                    "note": f"Order {MAXIMUS_ORDER_ID} not found on this account",
                })
        except Exception as e:
            result["actions"].append({"kind": "order", "error": str(e)})

    # ----- Load DB coverage -----
    covered_syms, stub_syms, stub_records = load_db_coverage(db_path)

    # Build a symbol → position map from Alpaca
    pos_by_sym: Dict[str, dict] = {p["symbol"]: p for p in positions}

    # ----- Upgrade existing stubs -----
    for stub in stub_records:
        sym = stub["_meta_sym"]
        if sym in pos_by_sym:
            result["actions"].append({
                "kind":       "upgrade_stub",
                "symbol":     sym,
                "stub_id":    stub["id"],
            })
            if fix:
                ok = upgrade_stub_record(db_path, stub, pos_by_sym[sym])
                result["actions"][-1]["ok"] = ok
                if ok:
                    result["stub_upgrades"] += 1

    # ----- Find truly uncovered positions -----
    all_covered = covered_syms | stub_syms
    uncovered = [p for p in positions if p["symbol"] not in all_covered]

    if not uncovered:
        result["already_covered"] = len(positions)
        return result

    result["already_covered"] = len(positions) - len(uncovered)

    if verbose:
        print(f"  [{name}] {len(uncovered)} uncovered position(s): "
              f"{[p['symbol'] for p in uncovered]}")

    # Group uncovered positions by (expiration, option_type) for pairing
    groups: Dict[Tuple[str,str], List[dict]] = {}
    for pos in uncovered:
        info = parse_occ(pos["symbol"])
        if not info:
            result["actions"].append({"kind": "skip", "symbol": pos["symbol"], "reason": "OCC parse failed"})
            continue
        key_ = (info["expiration"], info["option_type"])
        groups.setdefault(key_, []).append(pos)

    for (exp, opt_type), grp in groups.items():
        pairs = pair_legs(grp)
        for short_pos, long_pos in pairs:
            if short_pos and long_pos:
                # Paired spread
                sp_info = parse_occ(short_pos["symbol"])
                lp_info = parse_occ(long_pos["symbol"])
                stype   = "bear_call" if opt_type == "call" else "bull_put"
                width   = abs(lp_info["strike"] - sp_info["strike"])
                trade_id = f"reg-{short_pos['symbol'][:20]}"

                result["actions"].append({
                    "kind":         "new_spread",
                    "stype":        stype,
                    "short_symbol": short_pos["symbol"],
                    "long_symbol":  long_pos["symbol"],
                    "expiration":   exp,
                    "short_strike": sp_info["strike"],
                    "long_strike":  lp_info["strike"],
                    "width":        width,
                    "contracts":    abs(int(short_pos.get("qty") or 0)),
                    "trade_id":     trade_id,
                })
                if fix:
                    ok = insert_spread_record(db_path, short_pos, long_pos, trade_id)
                    result["actions"][-1]["ok"] = ok
                    if ok:
                        result["new_spreads"] += 1

            elif short_pos:
                sym = short_pos["symbol"]
                result["actions"].append({
                    "kind":      "new_single",
                    "symbol":    sym,
                    "side":      "short",
                    "contracts": abs(int(short_pos.get("qty") or 0)),
                })
                if fix:
                    ok = insert_single_leg_record(db_path, short_pos)
                    result["actions"][-1]["ok"] = ok
                    if ok:
                        result["new_singles"] += 1

            elif long_pos:
                sym = long_pos["symbol"]
                result["actions"].append({
                    "kind":      "new_single",
                    "symbol":    sym,
                    "side":      "long",
                    "contracts": abs(int(long_pos.get("qty") or 0)),
                })
                if fix:
                    ok = insert_single_leg_record(db_path, long_pos)
                    result["actions"][-1]["ok"] = ok
                    if ok:
                        result["new_singles"] += 1

    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_result(r: dict, fix: bool, verbose: bool) -> None:
    mark = "✓" if not r["error"] else "✗"
    print(f"\n{'─'*68}")
    print(f"  {mark}  {r['label']}  ({r['name']})")
    print(f"{'─'*68}")

    if r["error"]:
        print(f"  ERROR: {r['error']}")
        return

    print(f"  Alpaca option positions : {r['alpaca_positions']}")
    print(f"  Already covered by DB   : {r['already_covered']}")

    if not r["actions"]:
        print("  → Everything is fully registered. Nothing to do.")
        return

    # Summarise actions
    upgrades = [a for a in r["actions"] if a["kind"] == "upgrade_stub"]
    spreads  = [a for a in r["actions"] if a["kind"] == "new_spread"]
    singles  = [a for a in r["actions"] if a["kind"] == "new_single"]
    orders   = [a for a in r["actions"] if a["kind"] == "order"]
    skips    = [a for a in r["actions"] if a["kind"] == "skip"]

    if upgrades:
        print(f"\n  Stub upgrades ({len(upgrades)} records — fill in missing strike/exp data):")
        for a in upgrades:
            ok_str = ("  ✓ upgraded" if a.get("ok") else "  ↻ would upgrade") if fix else "  → would upgrade"
            print(f"    {ok_str}: {a['symbol']}  (stub id: {a['stub_id']})")

    if spreads:
        print(f"\n  New spread records ({len(spreads)}):")
        for a in spreads:
            prefix = "  ✓" if (fix and a.get("ok")) else ("  ✗ FAILED" if (fix and not a.get("ok")) else "  →")
            print(f"    {prefix} [{a['stype']}] "
                  f"short={a['short_strike']} / long={a['long_strike']}  "
                  f"exp={a['expiration']}  width={a['width']}  contracts={a['contracts']}")
            if verbose:
                print(f"       short_sym: {a['short_symbol']}")
                print(f"       long_sym : {a['long_symbol']}")
                print(f"       trade_id : {a['trade_id']}")

    if singles:
        print(f"\n  New single-leg records ({len(singles)}):")
        for a in singles:
            prefix = "  ✓" if (fix and a.get("ok")) else ("  ✗ FAILED" if fix else "  →")
            print(f"    {prefix} {a['symbol']}  side={a['side']}  contracts={a['contracts']}")

    if orders:
        print(f"\n  Order lookup (Maximus {MAXIMUS_ORDER_ID}):")
        for a in orders:
            if "error" in a:
                print(f"    ✗ Error: {a['error']}")
            elif "note" in a:
                print(f"    ℹ {a['note']}")
            else:
                inserted = a.get("inserted_as", "")
                status_str = f"  status={a.get('status','')}  legs={a.get('legs',0)}"
                print(f"    order_id={a.get('order_id','')}  symbol={a.get('symbol','')}{status_str}")
                if inserted:
                    prefix = "  ✓ inserted as" if not inserted.startswith("SKIPPED") else "  ℹ"
                    print(f"    {prefix}: {inserted}")

    if skips:
        print(f"\n  Skipped ({len(skips)} — OCC parse failures):")
        for a in skips:
            print(f"    {a['symbol']}: {a['reason']}")

    # Totals
    print()
    mode = "APPLIED" if fix else "DRY-RUN (re-run with --fix to write)"
    print(f"  [{mode}]")
    print(f"    Stub upgrades  : {r['stub_upgrades']}")
    print(f"    New spreads    : {r['new_spreads']}")
    print(f"    New singles    : {r['new_singles']}")
    print(f"    Order records  : {r['order_records']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register all orphan Alpaca positions into experiment DBs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--fix",      action="store_true",
                        help="Write records to DBs (default: dry-run, report only)")
    parser.add_argument("--account",  choices=list(ACCOUNTS.keys()),
                        help="Process a single account only")
    parser.add_argument("--verbose",  action="store_true",
                        help="Print OCC symbols and trade IDs for new records")
    args = parser.parse_args()

    run = {args.account: ACCOUNTS[args.account]} if args.account else ACCOUNTS

    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    mode_str = "APPLYING FIXES" if args.fix else "DRY-RUN (report only, use --fix to write)"
    print(f"║  CRITICAL FIX 3 — Register Orphan Positions          {mode_str:<20}║")
    print("╚══════════════════════════════════════════════════════════════════════╝")

    results = []
    for name, cfg in run.items():
        print(f"\nProcessing {name} ({cfg['label']})...", flush=True)
        r = process_account(name, cfg, fix=args.fix, verbose=args.verbose)
        results.append(r)
        print_result(r, fix=args.fix, verbose=args.verbose)

    # Grand summary
    total_new_spreads  = sum(r["new_spreads"]    for r in results)
    total_new_singles  = sum(r["new_singles"]    for r in results)
    total_stub_upg     = sum(r["stub_upgrades"]  for r in results)
    total_order_recs   = sum(r["order_records"]  for r in results)
    errors             = [r for r in results if r["error"]]

    print()
    print("═" * 68)
    print("  GRAND SUMMARY")
    print("═" * 68)
    print(f"  Stub upgrades  : {total_stub_upg}")
    print(f"  New spreads    : {total_new_spreads}")
    print(f"  New singles    : {total_new_singles}")
    print(f"  Order records  : {total_order_recs}")
    if errors:
        print(f"  Errors         : {len(errors)} account(s) — {[r['name'] for r in errors]}")
    print()

    if not args.fix and (total_new_spreads + total_new_singles + total_stub_upg + total_order_recs):
        print("  Re-run with --fix to write these records to the DBs.")
        print()

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
