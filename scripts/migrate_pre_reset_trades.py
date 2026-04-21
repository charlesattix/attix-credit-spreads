#!/usr/bin/env python3
"""
migrate_pre_reset_trades.py — Mark pre-reset trades and reset peak equity.

For experiments that were reset to fresh $100K Alpaca accounts on 2026-04-20,
this script:
  1. Adds a 'pre_reset' column to the trades table (INTEGER DEFAULT 0)
  2. Flags all trades with entry_date before the reset date as pre_reset=1
  3. Resets peak_equity in scanner_state to $100,000 (the new starting capital)
  4. Logs a reconciliation_event for audit trail

Usage:
    python scripts/migrate_pre_reset_trades.py              # dry run
    python scripts/migrate_pre_reset_trades.py --apply       # apply changes
    python scripts/migrate_pre_reset_trades.py --verify      # verify post-migration
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = PROJECT_ROOT / "experiments" / "registry.json"
STARTING_CAPITAL = 100_000.0


def get_reset_experiments() -> dict:
    """Return {exp_id: {reset_date, old_account, new_account, db_path}} for reset experiments."""
    with open(REGISTRY_PATH) as f:
        registry = json.load(f)

    results = {}
    for exp_id, exp in registry.get("experiments", {}).items():
        history = exp.get("reset_history", [])
        if not history:
            continue

        latest_reset = max(history, key=lambda h: h["date"])
        reset_date = latest_reset["date"]

        # Resolve DB path
        db_path = exp.get("db_path")
        if db_path:
            full_path = PROJECT_ROOT / db_path
            if full_path.exists():
                results[exp_id] = {
                    "reset_date": reset_date,
                    "old_account": latest_reset.get("old_account_id"),
                    "new_account": latest_reset.get("new_account_id"),
                    "db_path": str(full_path),
                    "name": exp.get("name", exp_id),
                }

    return results


def migrate_db(exp_id: str, info: dict, apply: bool = False) -> dict:
    """Migrate one experiment DB. Returns summary dict."""
    db_path = info["db_path"]
    reset_date = info["reset_date"]
    # First trading day is the day after reset (reset was on a Sunday)
    # Use reset_date directly as the cutoff: trades ON reset_date are pre-reset
    # Post-reset trades have entry_date > reset_date
    cutoff = reset_date  # "2026-04-20"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    summary = {
        "exp_id": exp_id,
        "name": info["name"],
        "reset_date": reset_date,
        "db_path": db_path,
    }

    # Check if pre_reset column already exists
    columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    has_column = "pre_reset" in columns
    summary["column_exists"] = has_column

    # Count pre-reset and post-reset trades
    pre_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM trades WHERE entry_date <= ?", (cutoff,)
    ).fetchone()["cnt"]
    post_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM trades WHERE entry_date > ?", (cutoff,)
    ).fetchone()["cnt"]

    # Pre-reset PnL
    pre_pnl_row = conn.execute(
        "SELECT SUM(pnl) AS total FROM trades WHERE entry_date <= ? AND status LIKE 'closed%' AND pnl IS NOT NULL",
        (cutoff,),
    ).fetchone()
    pre_pnl = float(pre_pnl_row["total"]) if pre_pnl_row["total"] else 0.0

    # Post-reset PnL
    post_pnl_row = conn.execute(
        "SELECT SUM(pnl) AS total FROM trades WHERE entry_date > ? AND status LIKE 'closed%' AND pnl IS NOT NULL",
        (cutoff,),
    ).fetchone()
    post_pnl = float(post_pnl_row["total"]) if post_pnl_row["total"] else 0.0

    # Current peak_equity
    peak_row = conn.execute(
        "SELECT value FROM scanner_state WHERE key = 'peak_equity'"
    ).fetchone()
    old_peak = float(peak_row["value"]) if peak_row else None

    summary.update({
        "pre_reset_trades": pre_count,
        "post_reset_trades": post_count,
        "pre_reset_pnl": round(pre_pnl, 2),
        "post_reset_pnl": round(post_pnl, 2),
        "old_peak_equity": old_peak,
        "new_peak_equity": STARTING_CAPITAL,
    })

    if not apply:
        conn.close()
        summary["applied"] = False
        return summary

    # --- APPLY CHANGES ---

    # 1. Add pre_reset column if not exists
    if not has_column:
        conn.execute("ALTER TABLE trades ADD COLUMN pre_reset INTEGER DEFAULT 0")

    # 2. Flag pre-reset trades
    cursor = conn.execute(
        "UPDATE trades SET pre_reset = 1 WHERE entry_date <= ? AND (pre_reset IS NULL OR pre_reset = 0)",
        (cutoff,),
    )
    flagged = cursor.rowcount

    # 3. Ensure post-reset trades are pre_reset=0
    conn.execute(
        "UPDATE trades SET pre_reset = 0 WHERE entry_date > ? AND pre_reset != 0",
        (cutoff,),
    )

    # 4. Reset peak_equity to starting capital
    conn.execute(
        "INSERT OR REPLACE INTO scanner_state (key, value, updated_at) VALUES (?, ?, ?)",
        ("peak_equity", str(STARTING_CAPITAL), datetime.now(timezone.utc).isoformat()),
    )

    # 5. Store reset metadata in scanner_state
    conn.execute(
        "INSERT OR REPLACE INTO scanner_state (key, value, updated_at) VALUES (?, ?, ?)",
        (
            "account_reset_date",
            reset_date,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO scanner_state (key, value, updated_at) VALUES (?, ?, ?)",
        (
            "pre_reset_trades_flagged",
            str(flagged),
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    # 6. Record reconciliation event for audit trail
    conn.execute(
        """
        INSERT INTO reconciliation_events (trade_id, event_type, details, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            f"migration_{exp_id}",
            "pre_reset_migration",
            json.dumps({
                "reset_date": reset_date,
                "pre_reset_trades": pre_count,
                "post_reset_trades": post_count,
                "pre_reset_pnl": round(pre_pnl, 2),
                "post_reset_pnl": round(post_pnl, 2),
                "old_peak_equity": old_peak,
                "new_peak_equity": STARTING_CAPITAL,
                "old_account": info["old_account"],
                "new_account": info["new_account"],
            }),
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    conn.commit()
    conn.close()

    summary["applied"] = True
    summary["flagged"] = flagged
    return summary


def verify_db(exp_id: str, info: dict) -> dict:
    """Verify migration was applied correctly."""
    db_path = info["db_path"]
    reset_date = info["reset_date"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    has_column = "pre_reset" in columns

    if not has_column:
        conn.close()
        return {"exp_id": exp_id, "migrated": False, "error": "pre_reset column missing"}

    flagged = conn.execute("SELECT COUNT(*) AS cnt FROM trades WHERE pre_reset = 1").fetchone()["cnt"]
    unflagged = conn.execute("SELECT COUNT(*) AS cnt FROM trades WHERE pre_reset = 0").fetchone()["cnt"]

    # Post-reset PnL only
    post_pnl_row = conn.execute(
        "SELECT SUM(pnl) AS total FROM trades WHERE pre_reset = 0 AND status LIKE 'closed%' AND pnl IS NOT NULL"
    ).fetchone()
    post_pnl = float(post_pnl_row["total"]) if post_pnl_row["total"] else 0.0

    peak_row = conn.execute("SELECT value FROM scanner_state WHERE key = 'peak_equity'").fetchone()
    peak = float(peak_row["value"]) if peak_row else None

    conn.close()

    return {
        "exp_id": exp_id,
        "migrated": True,
        "pre_reset_flagged": flagged,
        "post_reset_active": unflagged,
        "post_reset_pnl": round(post_pnl, 2),
        "peak_equity": peak,
    }


def main():
    parser = argparse.ArgumentParser(description="Mark pre-reset trades and reset peak equity")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--verify", action="store_true", help="Verify post-migration state")
    args = parser.parse_args()

    experiments = get_reset_experiments()
    if not experiments:
        print("No experiments with reset_history found in registry.")
        return 0

    if args.verify:
        print(f"\n{'='*60}")
        print(f"  PRE-RESET MIGRATION VERIFICATION")
        print(f"{'='*60}\n")
        for exp_id, info in sorted(experiments.items()):
            v = verify_db(exp_id, info)
            if v.get("migrated"):
                print(f"  ✅ {exp_id} ({info['name']})")
                print(f"     Pre-reset flagged: {v['pre_reset_flagged']}")
                print(f"     Post-reset active: {v['post_reset_active']}")
                print(f"     Post-reset PnL:    ${v['post_reset_pnl']:,.2f}")
                print(f"     Peak equity:       ${v['peak_equity']:,.2f}")
            else:
                print(f"  ❌ {exp_id}: {v.get('error', 'unknown')}")
        return 0

    mode = "APPLYING" if args.apply else "DRY RUN"
    print(f"\n{'='*60}")
    print(f"  PRE-RESET TRADE MIGRATION — {mode}")
    print(f"  Starting capital: ${STARTING_CAPITAL:,.0f}")
    print(f"{'='*60}\n")

    for exp_id, info in sorted(experiments.items()):
        print(f"  {exp_id} — {info['name']}")
        print(f"  {'─'*50}")
        summary = migrate_db(exp_id, info, apply=args.apply)
        print(f"  Reset date:        {summary['reset_date']}")
        print(f"  Pre-reset trades:  {summary['pre_reset_trades']}")
        print(f"  Post-reset trades: {summary['post_reset_trades']}")
        print(f"  Pre-reset PnL:     ${summary['pre_reset_pnl']:+,.2f}")
        print(f"  Post-reset PnL:    ${summary['post_reset_pnl']:+,.2f}")
        if summary.get("old_peak_equity"):
            print(f"  Peak equity:       ${summary['old_peak_equity']:,.2f} → ${summary['new_peak_equity']:,.2f}")
        if summary.get("applied"):
            print(f"  ✅ APPLIED — {summary.get('flagged', 0)} trades flagged as pre_reset")
        else:
            print(f"  ⏳ DRY RUN — no changes made")
        print()

    if not args.apply:
        print("  Run with --apply to execute the migration.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
