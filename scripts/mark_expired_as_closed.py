#!/usr/bin/env python3
"""Mark expired unmanaged orphan rows as closed with pnl=0.

Background
----------
Reconciler-inserted `orphan-*` and sentinel-inserted `orphan_*` placeholder
rows accumulate in per-experiment DBs when broker activities can't be
matched to a real trade record. These rows have:
  - status        = 'unmanaged'
  - expiration    = '' or NULL  (the actual expiration is encoded in the
                                 OCC symbol inside `id`)
  - short_strike / long_strike / contracts = 0 or NULL
  - source        = 'reconciler' (or 'sentinel' for orphan_*)

When the expiration has passed, the broker has already settled the
position; there is no real residual exposure. This script marks each
such row as `status='closed', pnl=0.0` with audit metadata, idempotently.

Scope (option (i) per P3 Carlos directive 2026-05-20)
------------------------------------------------------
Touches the "safer set" — both registry-active per-experiment DBs AND
the older root-level DBs that audit tools may still read:

  data/pilotai_exp503.db                (root-level, NOT registry-active)
  data/exp503/pilotai_exp503.db         (registry-active)
  data/pilotai_exp600.db                (root-level, NOT registry-active)
  data/exp600/pilotai_exp600.db         (registry-active)

Skips backup DBs (`*backup*`).

Usage
-----
    # Dry-run (default): print SQL + row-by-row preview, make no changes.
    python3 scripts/mark_expired_as_closed.py

    # Apply: same preview followed by actual UPDATEs in a single transaction.
    python3 scripts/mark_expired_as_closed.py --apply
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Targets (safer set — option (i))
TARGET_DBS = [
    PROJECT_ROOT / "data" / "pilotai_exp503.db",
    PROJECT_ROOT / "data" / "exp503" / "pilotai_exp503.db",
    PROJECT_ROOT / "data" / "pilotai_exp600.db",
    PROJECT_ROOT / "data" / "exp600" / "pilotai_exp600.db",
]

# OCC symbol parser — id form: `orphan-SPY260508P00682000` or `orphan_IBIT260501P00034000`
# Captures: underlying, YYMMDD, C/P, strike*1000.
OCC_RE = re.compile(
    r"^orphan[-_]([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$"
)

EXIT_REASON = "broker_expired"
EXCLUSION_REASON = "broker_expired_unmanaged"


def parse_expiration(trade_id: str) -> date | None:
    """Extract expiration date from an OCC-embedded orphan id, or None."""
    m = OCC_RE.match(trade_id or "")
    if not m:
        return None
    yy, mm, dd = m.group(2), m.group(3), m.group(4)
    try:
        return date(2000 + int(yy), int(mm), int(dd))
    except ValueError:
        return None


def has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(
        r[1] == col for r in conn.execute(f"PRAGMA table_info({table})")
    )


def collect_targets(conn: sqlite3.Connection, today: date) -> list[dict]:
    """Find unmanaged orphan rows with expiration < today.

    Returns list of dicts with id, ticker, contracts, parsed_expiration.
    """
    rows = conn.execute(
        "SELECT id, ticker, status, expiration, contracts, short_strike, long_strike "
        "FROM trades WHERE status = 'unmanaged'"
    ).fetchall()

    targets = []
    for row in rows:
        tid = row[0]
        exp_date = parse_expiration(tid)
        if exp_date is None:
            continue  # not an OCC-shaped orphan id — leave it alone
        if exp_date >= today:
            continue  # not yet expired
        targets.append({
            "id": tid,
            "ticker": row[1],
            "expiration": exp_date,
            "contracts": row[4],
            "short_strike": row[5],
            "long_strike": row[6],
        })
    return targets


def apply_migration(
    db_path: Path, today: date, dry_run: bool
) -> dict:
    """Process a single DB. Returns a result summary dict."""
    summary = {
        "db": str(db_path),
        "exists": db_path.exists(),
        "targets": [],
        "applied": False,
        "skipped_reason": None,
    }
    if not summary["exists"]:
        summary["skipped_reason"] = "db not found"
        return summary

    conn = sqlite3.connect(str(db_path))
    try:
        # Schema guard
        if not any(r[0] == "trades" for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )):
            summary["skipped_reason"] = "no trades table"
            return summary

        targets = collect_targets(conn, today)
        summary["targets"] = targets

        if not targets:
            return summary

        # Honour the cc3/cc4 column if present (PR #21 / cc3 PR)
        has_excl = has_column(conn, "trades", "excluded_from_metrics")
        has_excl_reason = has_column(conn, "trades", "exclusion_reason")

        if dry_run:
            return summary

        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor = conn.cursor()
        cursor.execute("BEGIN")
        try:
            for t in targets:
                exit_date = t["expiration"].isoformat()
                if has_excl and has_excl_reason:
                    cursor.execute(
                        "UPDATE trades SET status=?, pnl=?, exit_date=?, "
                        "exit_reason=?, expiration=?, "
                        "excluded_from_metrics=1, exclusion_reason=?, "
                        "updated_at=? WHERE id=? AND status='unmanaged'",
                        (
                            "closed", 0.0, exit_date,
                            EXIT_REASON, exit_date,
                            EXCLUSION_REASON,
                            now_iso, t["id"],
                        ),
                    )
                else:
                    cursor.execute(
                        "UPDATE trades SET status=?, pnl=?, exit_date=?, "
                        "exit_reason=?, expiration=?, updated_at=? "
                        "WHERE id=? AND status='unmanaged'",
                        (
                            "closed", 0.0, exit_date,
                            EXIT_REASON, exit_date,
                            now_iso, t["id"],
                        ),
                    )
            conn.commit()
            summary["applied"] = True
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
    return summary


def format_preview(summaries: list[dict], dry_run: bool) -> None:
    total_rows = sum(len(s["targets"]) for s in summaries)
    mode = "DRY-RUN" if dry_run else "APPLY"

    print(f"\n=== mark_expired_as_closed [{mode}] ===")
    print(f"today: {date.today().isoformat()}")
    print(f"target DBs: {len(summaries)}, total candidate rows: {total_rows}\n")

    for s in summaries:
        print(f"--- {s['db']} ---")
        if s["skipped_reason"]:
            print(f"  SKIP — {s['skipped_reason']}\n")
            continue
        if not s["targets"]:
            print("  no candidate rows\n")
            continue
        for t in s["targets"]:
            print(
                f"  {'WOULD ' if dry_run else ''}UPDATE "
                f"id={t['id']} ticker={t['ticker']} "
                f"expiration={t['expiration'].isoformat()} → "
                f"status=closed, pnl=0.0, exit_reason={EXIT_REASON}"
            )
        if not dry_run:
            print(f"  applied={s['applied']}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the UPDATEs (default is dry-run).",
    )
    parser.add_argument(
        "--today",
        default=None,
        help="Override 'today' (YYYY-MM-DD) for tests. Default: today.",
    )
    args = parser.parse_args(argv)

    today = (
        date.fromisoformat(args.today) if args.today else date.today()
    )
    summaries = [
        apply_migration(db, today, dry_run=not args.apply)
        for db in TARGET_DBS
    ]
    format_preview(summaries, dry_run=not args.apply)

    rc = 0 if all(
        s["skipped_reason"] is None or s["skipped_reason"] == "db not found"
        for s in summaries
    ) else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
