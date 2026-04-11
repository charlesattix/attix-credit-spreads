#!/usr/bin/env python3
"""
Migration: add close_source and alpaca_close_activity_id columns to trades table.

Discovers DB paths from:
  - .env.exp* files (PILOTAI_DB_PATH)
  - config*.yaml files (db_path key)
  - glob scan of data/**/*.db for pilotai*.db files

Uses ALTER TABLE ADD COLUMN with try/except for idempotency — safe to re-run.
"""

import glob
import os
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

COLUMNS_TO_ADD = [
    ("close_source", "TEXT"),
    ("alpaca_close_activity_id", "TEXT"),
]


def collect_db_paths() -> set[Path]:
    paths: set[Path] = set()

    # 1. .env.exp* files
    for env_file in PROJECT_ROOT.glob(".env.exp*"):
        if env_file.suffix in (".example",) or env_file.name.endswith(".example"):
            continue
        for line in env_file.read_text().splitlines():
            m = re.match(r"PILOTAI_DB_PATH\s*=\s*(.+)", line.strip())
            if m:
                db_val = m.group(1).strip().strip('"').strip("'")
                p = Path(db_val) if Path(db_val).is_absolute() else PROJECT_ROOT / db_val
                paths.add(p)

    # 2. config*.yaml files
    try:
        import yaml
        for cfg_file in PROJECT_ROOT.glob("config*.yaml"):
            try:
                with open(cfg_file) as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    db_val = data.get("db_path") or data.get("database", {}).get("path") if isinstance(data.get("database"), dict) else None
                    if db_val:
                        p = Path(db_val) if Path(db_val).is_absolute() else PROJECT_ROOT / db_val
                        paths.add(p)
            except Exception:
                pass
    except ImportError:
        pass

    # 3. Glob scan for all pilotai*.db in data/
    for db_file in PROJECT_ROOT.glob("data/**/pilotai*.db"):
        paths.add(db_file)
    # Also catch top-level data/pilotai*.db
    for db_file in PROJECT_ROOT.glob("data/pilotai*.db"):
        paths.add(db_file)

    return paths


def has_trades_table(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
    return cur.fetchone() is not None


def migrate_db(db_path: Path) -> tuple[bool, list[str]]:
    """Returns (success, messages)."""
    if not db_path.exists():
        return False, [f"  SKIP  — file not found: {db_path}"]

    msgs = []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        if not has_trades_table(conn):
            conn.close()
            return True, [f"  SKIP  — no trades table: {db_path}"]

        for col_name, col_type in COLUMNS_TO_ADD:
            try:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
                conn.commit()
                msgs.append(f"  ADDED  {col_name} {col_type}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    msgs.append(f"  EXISTS {col_name} (already present)")
                else:
                    msgs.append(f"  ERROR  {col_name}: {e}")
        conn.close()
        return True, msgs
    except Exception as e:
        return False, [f"  FAIL  {db_path}: {e}"]


def main():
    db_paths = collect_db_paths()

    if not db_paths:
        print("No DB paths discovered — check .env.exp* files and data/ directory.")
        sys.exit(1)

    print(f"Discovered {len(db_paths)} candidate DB(s):\n")

    all_ok = True
    for db_path in sorted(db_paths):
        print(f"{db_path}")
        ok, msgs = migrate_db(db_path)
        for m in msgs:
            print(m)
        if not ok:
            all_ok = False
        print()

    if all_ok:
        print("Migration complete.")
    else:
        print("Migration completed with errors (see above).")
        sys.exit(1)


if __name__ == "__main__":
    main()
