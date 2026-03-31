#!/usr/bin/env python3
"""
merge_split_brain_dbs.py — Merge root-level scanner DBs into LaunchAgent DBs
===========================================================================
Fixes split-brain situation where root-level DBs (data/pilotai_expXXX.db) have
trade history that was never written to the LaunchAgent DBs (data/expXXX/pilotai_expXXX.db).

Merges:
  EXP-503: data/pilotai_exp503.db  →  data/exp503/pilotai_exp503.db
  EXP-600: data/pilotai_exp600.db  →  data/exp600/pilotai_exp600.db

Dedup key: trades.id (text PK, e.g. "cs-28652a62f67a9473")
Conflict resolution: SOURCE WINS — source has updated statuses (closed_profit/closed_external)
  while target may have stale states (open/pending_open).

Tables merged per DB:
  trades              — dedup by id, source wins on conflict
  trade_features      — dedup by trade_id, source wins on conflict
  trade_deviations    — dedup by (trade_id), INSERT OR IGNORE (additive only)
  reconciliation_events — dedup by (trade_id, event_type), INSERT OR IGNORE
  scanner_state       — dedup by key, source wins on conflict
  alert_dedup         — dedup by (ticker, direction, alert_type), source wins
"""

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MERGES = [
    {
        "exp": "EXP-503",
        "source": ROOT / "data" / "pilotai_exp503.db",
        "target": ROOT / "data" / "exp503" / "pilotai_exp503.db",
    },
    {
        "exp": "EXP-600",
        "source": ROOT / "data" / "pilotai_exp600.db",
        "target": ROOT / "data" / "exp600" / "pilotai_exp600.db",
    },
]


# ── helpers ──────────────────────────────────────────────────────────────────

def backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = path.with_name(path.stem + f"_backup_{ts}.db")
    shutil.copy2(path, dst)
    return dst


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def col_names(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]


def count(con: sqlite3.Connection, table: str) -> int:
    if not table_exists(con, table):
        return 0
    return con.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0]


# ── per-table merge strategies ────────────────────────────────────────────────

def merge_trades(src: sqlite3.Connection, tgt: sqlite3.Connection) -> dict:
    """Merge trades table. Source wins on id conflict (updated status/PnL)."""
    if not table_exists(src, "trades"):
        return {"skipped": True}

    before = count(tgt, "trades")
    cols = col_names(src, "trades")
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)

    src_rows = src.execute(f"SELECT {col_list} FROM trades").fetchall()
    if not src_rows:
        return {"inserted": 0, "replaced": 0, "before": before, "after": before}

    # Identify conflicts (same id already in target)
    tgt_ids = {r[0] for r in tgt.execute("SELECT id FROM trades")}
    src_ids = {r[0] for r in src_rows}
    conflicts = tgt_ids & src_ids
    new_ids = src_ids - tgt_ids

    # INSERT OR REPLACE: source row overwrites target on conflict
    tgt.executemany(
        f"INSERT OR REPLACE INTO trades ({col_list}) VALUES ({placeholders})",
        src_rows,
    )
    tgt.commit()

    after = count(tgt, "trades")
    return {
        "before": before,
        "after": after,
        "inserted": len(new_ids),
        "replaced": len(conflicts),
        "conflict_ids": sorted(conflicts),
    }


def ensure_table(src: sqlite3.Connection, tgt: sqlite3.Connection, table: str) -> bool:
    """Create table in target using source DDL if it doesn't exist. Returns True if created."""
    if table_exists(tgt, table):
        return False
    row = src.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row and row[0]:
        tgt.execute(row[0])
        tgt.commit()
        return True
    return False


def merge_trade_features(src: sqlite3.Connection, tgt: sqlite3.Connection) -> dict:
    """Merge trade_features. Dedup by trade_id. Source wins on conflict."""
    if not table_exists(src, "trade_features"):
        return {"skipped": True}

    created = ensure_table(src, tgt, "trade_features")
    before = 0 if created else count(tgt, "trade_features")
    cols = col_names(src, "trade_features")
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)

    src_rows = src.execute(f"SELECT {col_list} FROM trade_features").fetchall()
    if not src_rows:
        return {"inserted": 0, "before": before, "after": before}

    tgt_ids = {r[0] for r in tgt.execute("SELECT trade_id FROM trade_features")}
    src_ids = {r[0] for r in src_rows}  # trade_id is first col
    new_count = len(src_ids - tgt_ids)
    conflict_count = len(src_ids & tgt_ids)

    tgt.executemany(
        f"INSERT OR REPLACE INTO trade_features ({col_list}) VALUES ({placeholders})",
        src_rows,
    )
    tgt.commit()

    return {
        "before": before,
        "after": count(tgt, "trade_features"),
        "inserted": new_count,
        "replaced": conflict_count,
    }


def merge_additive(
    src: sqlite3.Connection,
    tgt: sqlite3.Connection,
    table: str,
    dedup_cols: list[str],
) -> dict:
    """Insert rows from source that don't already exist in target, keyed by dedup_cols."""
    if not table_exists(src, table):
        return {"skipped": True}

    created = ensure_table(src, tgt, table)
    before = 0 if created else count(tgt, table)
    cols = col_names(src, table)
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)

    src_rows = src.execute(f"SELECT {col_list} FROM \"{table}\"").fetchall()
    if not src_rows:
        return {"inserted": 0, "skipped_dupes": 0, "before": before, "after": before}

    # Build dedup key index for target
    dedup_col_list = ", ".join(f'"{c}"' for c in dedup_cols)
    tgt_keys = {
        tuple(r) for r in tgt.execute(f"SELECT {dedup_col_list} FROM \"{table}\"")
    }
    col_index = {c: i for i, c in enumerate(cols)}
    dedup_indices = [col_index[c] for c in dedup_cols]

    new_rows = [
        r for r in src_rows
        if tuple(r[i] for i in dedup_indices) not in tgt_keys
    ]

    if new_rows:
        tgt.executemany(
            f"INSERT INTO \"{table}\" ({col_list}) VALUES ({placeholders})",
            new_rows,
        )
        tgt.commit()

    return {
        "before": before,
        "after": count(tgt, table),
        "inserted": len(new_rows),
        "skipped_dupes": len(src_rows) - len(new_rows),
    }


def merge_scanner_state(src: sqlite3.Connection, tgt: sqlite3.Connection) -> dict:
    """Merge scanner_state key/value pairs. Source wins on conflict."""
    if not table_exists(src, "scanner_state"):
        return {"skipped": True}

    before = count(tgt, "scanner_state")
    src_rows = src.execute("SELECT key, value, updated_at FROM scanner_state").fetchall()
    if not src_rows:
        return {"inserted": 0, "before": before, "after": before}

    tgt_keys = {r[0] for r in tgt.execute("SELECT key FROM scanner_state")}
    new_count = sum(1 for r in src_rows if r[0] not in tgt_keys)
    conflict_count = sum(1 for r in src_rows if r[0] in tgt_keys)

    tgt.executemany(
        "INSERT OR REPLACE INTO scanner_state (key, value, updated_at) VALUES (?, ?, ?)",
        src_rows,
    )
    tgt.commit()

    return {
        "before": before,
        "after": count(tgt, "scanner_state"),
        "inserted": new_count,
        "replaced": conflict_count,
    }


def merge_alert_dedup(src: sqlite3.Connection, tgt: sqlite3.Connection) -> dict:
    """Merge alert_dedup. Source wins on (ticker, direction, alert_type) conflict."""
    if not table_exists(src, "alert_dedup"):
        return {"skipped": True}

    before = count(tgt, "alert_dedup")
    cols = col_names(src, "alert_dedup")
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)

    src_rows = src.execute(f"SELECT {col_list} FROM alert_dedup").fetchall()
    if not src_rows:
        return {"inserted": 0, "before": before, "after": before}

    tgt_keys = {
        (r[0], r[1], r[2])
        for r in tgt.execute("SELECT ticker, direction, alert_type FROM alert_dedup")
    }
    new_count = sum(1 for r in src_rows if (r[0], r[1], r[2]) not in tgt_keys)
    conflict_count = sum(1 for r in src_rows if (r[0], r[1], r[2]) in tgt_keys)

    tgt.executemany(
        f"INSERT OR REPLACE INTO alert_dedup ({col_list}) VALUES ({placeholders})",
        src_rows,
    )
    tgt.commit()

    return {
        "before": before,
        "after": count(tgt, "alert_dedup"),
        "inserted": new_count,
        "replaced": conflict_count,
    }


# ── main merge routine ────────────────────────────────────────────────────────

def run_merge(exp: str, source: Path, target: Path) -> None:
    print(f"\n{'='*60}")
    print(f"  {exp}")
    print(f"  source: {source.relative_to(ROOT)}")
    print(f"  target: {target.relative_to(ROOT)}")
    print(f"{'='*60}")

    if not source.exists():
        print(f"  ERROR: source DB not found: {source}")
        sys.exit(1)
    if not target.exists():
        print(f"  ERROR: target DB not found: {target}")
        sys.exit(1)

    # Backup target before any changes
    bak = backup(target)
    print(f"  Backed up target → {bak.name}")

    src = sqlite3.connect(source)
    tgt = sqlite3.connect(target)

    # ── trades ──────────────────────────────────────────────
    print("\n  [trades]")
    r = merge_trades(src, tgt)
    if r.get("skipped"):
        print("    skipped (table not found)")
    else:
        print(f"    before={r['before']} → after={r['after']}")
        print(f"    inserted={r['inserted']} new, replaced={r['replaced']} conflicts (source won)")
        if r.get("conflict_ids"):
            for cid in r["conflict_ids"]:
                src_row = src.execute("SELECT status, pnl FROM trades WHERE id=?", (cid,)).fetchone()
                tgt_row_new = tgt.execute("SELECT status, pnl FROM trades WHERE id=?", (cid,)).fetchone()
                print(f"      conflict {cid}: source={src_row} → written to target")

    # ── trade_features ──────────────────────────────────────
    print("\n  [trade_features]")
    r = merge_trade_features(src, tgt)
    if r.get("skipped"):
        print("    skipped (table not found in source)")
    else:
        print(f"    before={r['before']} → after={r['after']} (inserted={r['inserted']}, replaced={r['replaced']})")

    # ── trade_deviations ────────────────────────────────────
    print("\n  [trade_deviations]")
    r = merge_additive(src, tgt, "trade_deviations", ["trade_id"])
    if r.get("skipped"):
        print("    skipped")
    else:
        print(f"    before={r['before']} → after={r['after']} (inserted={r['inserted']}, skipped_dupes={r['skipped_dupes']})")

    # ── reconciliation_events ───────────────────────────────
    print("\n  [reconciliation_events]")
    r = merge_additive(src, tgt, "reconciliation_events", ["trade_id", "event_type"])
    if r.get("skipped"):
        print("    skipped")
    else:
        print(f"    before={r['before']} → after={r['after']} (inserted={r['inserted']}, skipped_dupes={r['skipped_dupes']})")

    # ── scanner_state ───────────────────────────────────────
    print("\n  [scanner_state]")
    r = merge_scanner_state(src, tgt)
    if r.get("skipped"):
        print("    skipped")
    else:
        print(f"    before={r['before']} → after={r['after']} (inserted={r['inserted']}, replaced={r['replaced']})")

    # ── alert_dedup ─────────────────────────────────────────
    print("\n  [alert_dedup]")
    r = merge_alert_dedup(src, tgt)
    if r.get("skipped"):
        print("    skipped")
    else:
        print(f"    before={r['before']} → after={r['after']} (inserted={r['inserted']}, replaced={r['replaced']})")

    src.close()
    tgt.close()

    # Final verification
    print("\n  [verification]")
    tgt_v = sqlite3.connect(target)
    src_v = sqlite3.connect(source)
    for table in ["trades", "trade_features", "trade_deviations", "reconciliation_events", "scanner_state", "alert_dedup"]:
        s = count(src_v, table)
        t = count(tgt_v, table)
        ok = "✓" if t >= s else "✗ PROBLEM"
        print(f"    {table}: source={s} target={t} {ok}")
    tgt_v.close()
    src_v.close()


def main():
    print("merge_split_brain_dbs.py")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("\nStrategy: source wins on conflicts (source has settled trade statuses)")

    for m in MERGES:
        run_merge(m["exp"], m["source"], m["target"])

    print("\n\nDone. Both LaunchAgent DBs now contain full trade history.")
    print("Backups saved alongside target DBs (timestamp suffix).")


if __name__ == "__main__":
    main()
