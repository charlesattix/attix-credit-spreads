#!/usr/bin/env python3
"""
SENTINEL CLI — Experiment governance and health check runner.

Modes:
  --daily        Full daily health run: Alpaca pings + portfolio risk + Telegram report

Usage:
    python scripts/run_sentinel.py --daily
    python scripts/run_sentinel.py --daily --no-telegram
    python scripts/run_sentinel.py --daily --experiment EXP-800
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_registry(root: Path) -> dict:
    path = root / "experiments" / "registry.json"
    with open(path) as f:
        return json.load(f)


def _load_env_file(env_path: str) -> None:
    """Load a .env file into os.environ (setdefault — never overwrites)."""
    p = Path(env_path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# --daily mode
# ---------------------------------------------------------------------------


def run_daily(args) -> int:
    from sentinel.monitor import check_all_experiments
    from sentinel.portfolio import aggregate_portfolio_risk
    from sentinel.alerter import build_daily_report, send_daily_report

    registry = _load_registry(ROOT)

    # Optionally narrow to one experiment
    if args.experiment:
        exp_id = args.experiment.upper()
        if exp_id not in registry.get("experiments", {}):
            logger.error("Experiment %s not found in registry", exp_id)
            return 1
        registry = dict(registry)
        registry["experiments"] = {exp_id: registry["experiments"][exp_id]}

    # 1. Alpaca health check
    logger.info("SENTINEL: checking Alpaca accounts (%d total)", len(registry["experiments"]))
    monitor_results = check_all_experiments(registry, ROOT)

    # 2. Portfolio risk aggregation
    logger.info("SENTINEL: aggregating portfolio risk...")
    portfolio = aggregate_portfolio_risk(registry, ROOT)

    if portfolio.db_errors:
        for err in portfolio.db_errors:
            logger.warning("portfolio: %s", err)

    # 3. Build report
    report = build_daily_report(monitor_results, portfolio)

    # 4. Summarise to log
    criticals = sum(
        1 for h in monitor_results
        if h.is_orphan or h.is_ghost or h.is_duplicate
    )
    warnings = sum(1 for h in monitor_results if h.is_stale)
    logger.info(
        "SENTINEL: %d experiments checked | %d CRITICAL | %d WARNING",
        len(monitor_results), criticals, warnings,
    )

    # 5. Output
    if args.no_telegram:
        print(report)
        return 1 if (criticals + warnings) > 0 else 0

    sent = send_daily_report(report)
    if not sent:
        logger.warning("SENTINEL: Telegram send failed — printing report to stdout")
        print(report)

    return 1 if (criticals + warnings) > 0 else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SENTINEL — PilotAI experiment governance CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--daily", action="store_true",
        help="Run the full daily health check (Alpaca + portfolio + Telegram)",
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="Print the report to stdout instead of sending via Telegram",
    )
    parser.add_argument(
        "--experiment",
        help="Limit check to a single experiment ID (e.g. EXP-800)",
    )
    parser.add_argument(
        "--env-file",
        help="Optional .env file to load credentials from",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.env_file:
        _load_env_file(args.env_file)

    if args.daily:
        sys.exit(run_daily(args))

    parser.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()
