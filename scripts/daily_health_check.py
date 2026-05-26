#!/usr/bin/env python3
"""
Daily Health Check — Post-market audit for all 5 RC fixes.

Intended to run at 4:30 PM ET after market close (or on demand).
Checks all five root-cause regression conditions and sends a single
Telegram summary message.

Usage:
    python scripts/daily_health_check.py --config configs/paper_champion.yaml
    python scripts/daily_health_check.py --config configs/paper_champion.yaml --env-file .env.champion
    python scripts/daily_health_check.py --config configs/paper_champion.yaml --no-telegram
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import yaml

# Ensure project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config / env helpers (same pattern as daily_report.py)
# ---------------------------------------------------------------------------


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_env_file(env_path: str) -> None:
    p = Path(env_path)
    if not p.exists():
        logger.warning("Env file not found: %s", env_path)
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# Individual RC checks (SQL-based, using shared.database.get_db)
# ---------------------------------------------------------------------------


def _run_rc1_stale_pending_open(conn) -> List[str]:
    """RC#1: pending_open trades older than 6 hours."""
    rows = conn.execute(
        "SELECT id, ticker, created_at FROM trades "
        "WHERE status = 'pending_open' "
        "AND created_at < datetime('now', '-6 hours')"
    ).fetchall()
    if not rows:
        return []
    ids = ", ".join(str(r[0]) for r in rows[:5])
    suffix = f" … (+{len(rows) - 5} more)" if len(rows) > 5 else ""
    return [f"RC#1: {len(rows)} stale pending_open trade(s) [{ids}{suffix}]"]


def _run_rc2_zero_pnl_closes(conn) -> List[str]:
    """RC#2: closed trades today with pnl = 0 and non-zero credit."""
    rows = conn.execute(
        "SELECT id, ticker, credit FROM trades "
        "WHERE status LIKE 'closed_%' "
        "AND pnl = 0 "
        "AND (credit IS NOT NULL AND credit != 0) "
        "AND exit_date >= date('now')"
    ).fetchall()
    if not rows:
        return []
    ids = ", ".join(str(r[0]) for r in rows[:5])
    suffix = f" … (+{len(rows) - 5} more)" if len(rows) > 5 else ""
    return [f"RC#2: {len(rows)} trade(s) closed with $0 PnL today [{ids}{suffix}]"]


def _run_rc4_zombie_records(conn) -> List[str]:
    """RC#4a: open synthetic-monitor-* records."""
    rows = conn.execute(
        "SELECT id, ticker FROM trades "
        "WHERE id LIKE 'synthetic-monitor-%' AND status = 'open'"
    ).fetchall()
    issues = []
    if rows:
        ids = ", ".join(str(r[0]) for r in rows[:3])
        suffix = f" … (+{len(rows) - 3} more)" if len(rows) > 3 else ""
        issues.append(f"RC#4: {len(rows)} zombie/synthetic record(s) [{ids}{suffix}]")
    return issues


def _run_rc4_duplicate_positions(conn) -> List[str]:
    """RC#4b: duplicate (ticker, expiration, short_strike, long_strike) among active trades."""
    rows = conn.execute(
        "SELECT ticker, expiration, short_strike, long_strike, COUNT(*) c "
        "FROM trades "
        "WHERE status IN ('open','pending_open') "
        "GROUP BY ticker, expiration, short_strike, long_strike "
        "HAVING c > 1"
    ).fetchall()
    if not rows:
        return []
    groups = ", ".join(
        f"{r[0]}/{r[1]}/{r[2]}/{r[3]}(x{r[4]})" for r in rows[:3]
    )
    suffix = f" … (+{len(rows) - 3} more)" if len(rows) > 3 else ""
    return [f"RC#4: {len(rows)} duplicate position group(s) [{groups}{suffix}]"]


def _run_rc5_expiration_concentration(conn, max_same_expiration: int) -> List[str]:
    """RC#5: any expiration exceeds max_same_expiration."""
    if max_same_expiration <= 0:
        return []
    rows = conn.execute(
        "SELECT expiration, COUNT(*) cnt FROM trades "
        "WHERE status IN ('open','pending_open') "
        "GROUP BY expiration "
        "HAVING cnt > ?",
        (max_same_expiration,),
    ).fetchall()
    if not rows:
        return []
    groups = ", ".join(f"{r[0]}(x{r[1]})" for r in rows)
    return [
        f"RC#5: {len(rows)} expiration(s) exceed limit of {max_same_expiration} [{groups}]"
    ]


# ---------------------------------------------------------------------------
# Main health check
# ---------------------------------------------------------------------------


def run_health_check(config: dict) -> List[str]:
    """Execute all 5 RC checks and return a list of issue strings.

    Returns an empty list when everything is healthy.
    """
    from shared.database import get_db

    db_path = config.get("db_path") or os.environ.get("PILOTAI_DB_PATH")
    max_positions = config.get("risk", {}).get("max_positions", 0)
    max_same_expiration = (
        config.get("risk", {})
        .get("portfolio_risk", {})
        .get("max_same_expiration", 0)
    )

    conn = get_db(db_path)
    issues: List[str] = []
    try:
        issues += _run_rc1_stale_pending_open(conn)
        issues += _run_rc2_zero_pnl_closes(conn)
        issues += _run_rc4_zombie_records(conn)
        issues += _run_rc4_duplicate_positions(conn)
        issues += _run_rc5_expiration_concentration(conn, max_same_expiration)
    finally:
        conn.close()

    return issues


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------


def send_health_check_result(issues: List[str], experiment_id: str) -> bool:
    """Send the health check summary via Telegram.

    Returns True if the message was sent successfully.
    """
    from shared.telegram_alerts import send_message

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if issues:
        body = "\n".join(f"• {issue}" for issue in issues)
        text = (
            f"🚨 <b>DAILY HEALTH CHECK FAILED</b> [{experiment_id}]\n"
            f"<i>{now_str}</i>\n\n"
            f"{body}"
        )
    else:
        text = (
            f"✅ <b>Daily health check passed</b> [{experiment_id}]\n"
            f"<i>{now_str}</i> — all 5 RCs clear"
        )

    return send_message(text)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Attix Daily Health Check (RC monitoring)")
    parser.add_argument("--config", required=True, help="Path to YAML config file")
    parser.add_argument("--env-file", help="Path to .env file for credentials")
    parser.add_argument(
        "--no-telegram", action="store_true", help="Print result instead of sending Telegram"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.env_file:
        load_env_file(args.env_file)

    config = load_config(args.config)

    db_path = config.get("db_path")
    if db_path:
        os.environ["PILOTAI_DB_PATH"] = db_path

    experiment_id = config.get("experiment_id", "UNKNOWN")
    os.environ.setdefault("EXPERIMENT_ID", experiment_id)

    logger.info("Running daily health check for %s", experiment_id)

    issues = run_health_check(config)

    if issues:
        logger.warning("Health check FAILED — %d issue(s) found:", len(issues))
        for issue in issues:
            logger.warning("  %s", issue)
    else:
        logger.info("Health check PASSED — all 5 RCs clear")

    if args.no_telegram:
        if issues:
            print(f"FAILED ({len(issues)} issues):")
            for issue in issues:
                print(f"  {issue}")
        else:
            print("PASSED — all 5 RCs clear")
        return 1 if issues else 0

    sent = send_health_check_result(issues, experiment_id)
    if not sent:
        logger.warning("Telegram send failed (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
