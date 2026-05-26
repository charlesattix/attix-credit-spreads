"""
CLI entry point for the Attix Signal Service.

Usage:
    python -m pilotai_signal <command> [options]

Commands:
    init          Create DB schema
    collect       Fetch all strategies and store snapshots
    score         Compute ticker signals from stored snapshots
    alerts        Generate and send alerts
    run           Full pipeline: collect → score → alerts (default for cron)
    digest        Send daily digest to Telegram only
    show          Print current top signals to stdout
    status        Show last N collection runs
    history       Show conviction history for a specific ticker
    rebuild       Recompute all historical signals (after formula changes)
"""

import argparse
import logging
import sys
from datetime import date

from . import config, db
from .alerts import build_digest, run_alerts, send_telegram
from .collector import run_collection
from .scorer import compute_signals, rebuild_all_signals


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
        stream=sys.stdout,
    )


def cmd_init(args) -> int:
    db.init_db()
    print(f"✓ Database initialized at {config.DB_PATH}")
    return 0


def cmd_collect(args) -> int:
    snap_date = date.fromisoformat(args.date) if args.date else date.today()
    result = run_collection(
        snapshot_date=snap_date,
        force=args.force,
        dry_run=args.dry_run,
    )
    print(f"Collection: {result}")
    return 0 if result.get("status") in ("SUCCESS", "PARTIAL", "skipped") else 1


def cmd_score(args) -> int:
    snap_date = date.fromisoformat(args.date) if args.date else date.today()
    signals = compute_signals(snap_date, dry_run=args.dry_run)
    print(f"Scored {len(signals)} tickers for {snap_date}")
    return 0


def cmd_alerts(args) -> int:
    alert_date = date.fromisoformat(args.date) if args.date else date.today()
    result = run_alerts(
        alert_date=alert_date,
        send_digest=not args.no_digest,
        dry_run=args.dry_run,
    )
    print(f"Alerts: {result}")
    return 0


def cmd_run(args) -> int:
    """Full pipeline: collect → score → alerts."""
    snap_date = date.fromisoformat(args.date) if args.date else date.today()

    print(f"─── Attix Signal Service — {snap_date} ───")

    # 1. Collect
    print("1/3 Collecting strategy snapshots...")
    col = run_collection(snapshot_date=snap_date, force=args.force, dry_run=args.dry_run)
    if col.get("status") == "failed":
        print(f"✗ Collection failed: {col.get('error')}")
        return 1
    print(f"  ✓ {col}")

    # 2. Score
    print("2/3 Computing conviction scores...")
    signals = compute_signals(snap_date, dry_run=args.dry_run)
    print(f"  ✓ {len(signals)} ticker signals computed")

    # 3. Alerts
    print("3/3 Generating and sending alerts...")
    alerts = run_alerts(
        alert_date=snap_date,
        send_digest=not args.no_digest,
        dry_run=args.dry_run,
    )
    print(f"  ✓ {alerts}")

    print("─── Complete ───")
    return 0


def cmd_digest(args) -> int:
    today = date.fromisoformat(args.date) if args.date else date.today()
    with db.transaction() as conn:
        signals = db.get_signals_for_date(conn, today)
        if not signals:
            print(f"No signals for {today}. Run 'collect' and 'score' first.")
            return 1
        signals = [dict(s) for s in signals]
    msg = build_digest(today, signals)
    print(msg)
    if not args.dry_run:
        ok = send_telegram(msg)
        print("✓ Sent to Telegram" if ok else "✗ Telegram send failed")
    return 0


def cmd_show(args) -> int:
    today = date.fromisoformat(args.date) if args.date else date.today()
    n = args.top or 30

    with db.transaction() as conn:
        signals = db.get_signals_for_date(conn, today)
        if not signals:
            print(f"No signals found for {today}.")
            return 1

    equity = [s for s in signals if s["ticker"] not in config.GOLD_TICKERS]
    gold = [s for s in signals if s["ticker"] in config.GOLD_TICKERS]

    print(f"\n── Attix Conviction Signal — {today} ──")
    print(f"{'Rank':<5} {'Ticker':<8} {'Conv':>7} {'Freq':>6} {'FreqPct':>8} {'AvgW%':>7} {'Days':>6}")
    print("-" * 58)
    for i, s in enumerate(equity[:n], 1):
        flag = "🔥" if s["conviction"] >= config.ALERT_STRONG_CONVICTION_MIN else " "
        print(
            f"{flag}{i:<4} {s['ticker']:<8} {s['conviction']:>7.4f} "
            f"{s['frequency']:>6} {s['freq_pct']*100:>7.1f}% "
            f"{s['avg_weight']*100:>6.1f}% {s['days_in_signal']:>6}"
        )

    if gold:
        print("\n── Gold Hedge Signal ──")
        for i, s in enumerate(gold[:5], 1):
            print(f"  {i}. ${s['ticker']:<6} Conv: {s['conviction']:.4f} | F: {s['frequency']}/{s['total_portfolios']}")

    return 0


def cmd_status(args) -> int:
    n = args.last or 10
    with db.transaction() as conn:
        logs = db.get_collection_log(conn, limit=n)

    if not logs:
        print("No collection runs recorded.")
        return 0

    print(f"\n── Last {n} Collection Runs ──")
    print(f"{'Date':<12} {'Status':<10} {'OK':>4} {'Fail':>5} {'Dur(s)':>8}")
    print("-" * 45)
    for log in logs:
        print(
            f"{log['run_date']:<12} {log['status']:<10} "
            f"{log['strategies_ok']:>4} {log['strategies_fail']:>5} "
            f"{log['duration_sec']:>8.1f}"
        )
    return 0


def cmd_history(args) -> int:
    ticker = args.ticker.upper()
    days = args.days or 30

    with db.transaction() as conn:
        rows = db.get_ticker_history(conn, ticker, days=days)

    if not rows:
        print(f"No history for ${ticker}")
        return 1

    print(f"\n── ${ticker} Signal History (last {days} days) ──")
    print(f"{'Date':<12} {'Conv':>7} {'Freq':>6} {'Days':>6}")
    print("-" * 35)
    for r in rows:
        print(
            f"{r['signal_date']:<12} {r['conviction']:>7.4f} "
            f"{r['frequency']:>6} {r['days_in_signal']:>6}"
        )
    return 0


def cmd_rebuild(args) -> int:
    print("Rebuilding all historical signals (this may take a moment)...")
    n = rebuild_all_signals(dry_run=args.dry_run)
    print(f"✓ Rebuilt signals for {n} dates")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pilotai_signal",
        description="Attix Signal Service",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Create database schema")

    # collect
    p_collect = sub.add_parser("collect", help="Fetch strategy snapshots")
    p_collect.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_collect.add_argument("--force", action="store_true", help="Re-fetch even if already collected")
    p_collect.add_argument("--dry-run", action="store_true")

    # score
    p_score = sub.add_parser("score", help="Compute ticker signals from snapshots")
    p_score.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_score.add_argument("--dry-run", action="store_true")

    # alerts
    p_alerts = sub.add_parser("alerts", help="Generate and send alerts")
    p_alerts.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_alerts.add_argument("--no-digest", action="store_true", help="Skip daily digest")
    p_alerts.add_argument("--dry-run", action="store_true")

    # run (full pipeline)
    p_run = sub.add_parser("run", help="Full pipeline: collect → score → alerts")
    p_run.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--no-digest", action="store_true")
    p_run.add_argument("--dry-run", action="store_true")

    # digest
    p_digest = sub.add_parser("digest", help="Send daily digest to Telegram")
    p_digest.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_digest.add_argument("--dry-run", action="store_true")

    # show
    p_show = sub.add_parser("show", help="Print top signals to stdout")
    p_show.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p_show.add_argument("--top", type=int, default=30)

    # status
    p_status = sub.add_parser("status", help="Show collection run history")
    p_status.add_argument("--last", type=int, default=10)

    # history
    p_hist = sub.add_parser("history", help="Show conviction history for a ticker")
    p_hist.add_argument("ticker", help="Ticker symbol (e.g. ATI)")
    p_hist.add_argument("--days", type=int, default=30)

    # rebuild
    p_rebuild = sub.add_parser("rebuild", help="Recompute all historical signals")
    p_rebuild.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    # Ensure DB exists
    if args.command != "init":
        db.init_db()

    dispatch = {
        "init": cmd_init,
        "collect": cmd_collect,
        "score": cmd_score,
        "alerts": cmd_alerts,
        "run": cmd_run,
        "digest": cmd_digest,
        "show": cmd_show,
        "status": cmd_status,
        "history": cmd_history,
        "rebuild": cmd_rebuild,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
