#!/usr/bin/env python3
"""
scripts/registry_cli.py — Unified CLI for the Experiment Registry.

Replaces register_experiment.py, list_experiments.py, validate_registry.py
with a single tool.

Commands:
    register   — Add a new experiment
    activate   — Set status to active
    pause      — Pause (sets DRY_RUN in scanners)
    stop       — Stop an experiment
    retire     — Retire permanently
    list       — Show experiments by status
    validate   — Check registry integrity
    status     — Detailed info for one experiment
    sync       — Detect orphans and unregistered resources

Usage:
    python scripts/registry_cli.py list
    python scripts/registry_cli.py list --all
    python scripts/registry_cli.py status EXP-800
    python scripts/registry_cli.py activate EXP-800
    python scripts/registry_cli.py pause EXP-800 --reason "investigating drift"
    python scripts/registry_cli.py register --id EXP-602 --creator charles --name "New Strategy"
    python scripts/registry_cli.py validate --strict
    python scripts/registry_cli.py sync
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.registry import (  # noqa: E402
    CREATOR_RANGES,
    REGISTRY_PATH,
    VALID_CREATORS,
    VALID_STATUSES,
    VALID_TRANSITIONS,
    find_active_not_running,
    find_orphan_dbs,
    find_orphan_env_files,
    find_orphan_processes,
    get_active_experiments,
    get_experiment,
    get_experiments_by_status,
    is_research_entry,
    load_registry,
    save_registry,
    transition_status,
    validate,
)


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _col_widths(rows: list[list[str]], headers: list[str]) -> list[int]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    return widths


def _print_table(title: str, headers: list[str], rows: list[list[str]]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  (none)\n")
        return
    widths = _col_widths(rows, headers)
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    fmt = "| " + " | ".join(f"{{:<{w}}}" for w in widths) + " |"
    print(sep)
    print(fmt.format(*headers))
    print(sep)
    for row in rows:
        print(fmt.format(*row))
    print(sep)
    print(f"  {len(rows)} experiment(s)\n")


def _trunc(s: str, maxlen: int = 50) -> str:
    if len(s) > maxlen:
        return s[: maxlen - 3] + "..."
    return s


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    registry = load_registry()
    all_exps = sorted(
        registry["experiments"].values(),
        key=lambda e: (e.get("status", ""), e.get("id", "")),
    )

    if args.filter == "active":
        exps = [e for e in all_exps if e["status"] == "active"]
        headers = ["ID", "Name", "Creator", "Ticker", "Account", "Config", "Started"]
        rows = [
            [
                e["id"],
                _trunc(e["name"], 25),
                e["created_by"],
                e.get("ticker") or "—",
                e.get("alpaca_account_id") or e.get("account_id") or "—",
                _trunc(e.get("config_path") or e.get("paper_config") or "—", 35),
                (e.get("last_started_at") or e.get("live_since") or "—")[:10],
            ]
            for e in exps
        ]
        _print_table("Active Experiments", headers, rows)

    elif args.filter == "registered":
        exps = [e for e in all_exps if e["status"] in ("registered", "configuring")]
        headers = ["ID", "Name", "Creator", "Status", "Notes"]
        rows = [
            [e["id"], _trunc(e["name"], 25), e["created_by"],
             e["status"], _trunc(e.get("notes") or "—", 40)]
            for e in exps
        ]
        _print_table("Registered / Configuring", headers, rows)

    elif args.filter == "retired":
        exps = [e for e in all_exps if e["status"] == "retired"]
        headers = ["ID", "Name", "Creator", "Retired", "Reason"]
        rows = [
            [e["id"], _trunc(e["name"], 25), e["created_by"],
             e.get("retired_date") or "—",
             _trunc(e.get("retired_reason") or e.get("notes") or "—", 50)]
            for e in exps
        ]
        _print_table("Retired Experiments", headers, rows)

    elif args.filter == "research":
        exps = [e for e in all_exps if is_research_entry(e["id"])]
        headers = ["ID", "Name", "Status", "Verdict"]
        rows = [
            [e["id"], _trunc(e["name"], 30), e["status"],
             _trunc(e.get("verdict") or "—", 40)]
            for e in exps
        ]
        _print_table("Research Experiments", headers, rows)

    else:  # --all
        for status_group, title in [
            (("active",), "Active"),
            (("paused",), "Paused"),
            (("stopped",), "Stopped"),
            (("registered", "configuring"), "Registered / Configuring"),
            (("failed",), "Failed"),
            (("retired",), "Retired"),
        ]:
            exps = [e for e in all_exps if e["status"] in status_group and not is_research_entry(e["id"])]
            if exps:
                headers = ["ID", "Name", "Creator", "Status", "Ticker"]
                rows = [
                    [e["id"], _trunc(e["name"], 30), e["created_by"],
                     e["status"], e.get("ticker") or "—"]
                    for e in exps
                ]
                _print_table(title, headers, rows)

        # Summary
        total = len([e for e in all_exps if not is_research_entry(e["id"])])
        research = len([e for e in all_exps if is_research_entry(e["id"])])
        print(f"  Total: {total} experiments + {research} research entries")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    exp = get_experiment(args.exp_id)
    if not exp:
        print(f"ERROR: {args.exp_id} not found in registry.", file=sys.stderr)
        return 1

    print(f"\n{'=' * 60}")
    print(f"  Experiment: {exp['id']}  —  {exp['name']}")
    print(f"{'=' * 60}")

    fields = [
        ("Status", exp.get("status")),
        ("Created by", exp.get("created_by")),
        ("Created at", exp.get("created_at") or exp.get("created_date")),
        ("Updated at", exp.get("updated_at")),
        ("Ticker", exp.get("ticker")),
        ("Strategy", exp.get("strategy_type")),
        ("Config", exp.get("config_path") or exp.get("paper_config")),
        ("Env file", exp.get("env_file")),
        ("DB path", exp.get("db_path")),
        ("Account ID", exp.get("alpaca_account_id") or exp.get("account_id")),
        ("Live since", exp.get("live_since")),
        ("Last started", exp.get("last_started_at")),
        ("Last stopped", exp.get("last_stopped_at")),
        ("Git branch", exp.get("git_branch")),
    ]

    for label, val in fields:
        if val:
            print(f"  {label:<16} {val}")

    # Notes, description
    if exp.get("description"):
        print(f"\n  Description: {exp['description']}")
    if exp.get("notes"):
        print(f"  Notes: {exp['notes']}")

    # Retired info
    if exp.get("status") == "retired":
        if exp.get("retired_date"):
            print(f"  Retired: {exp['retired_date']}")
        if exp.get("retired_reason"):
            print(f"  Reason: {exp['retired_reason']}")
        if exp.get("superseded_by"):
            print(f"  Superseded by: {exp['superseded_by']}")
        if exp.get("lessons_learned"):
            print(f"  Lessons: {exp['lessons_learned']}")

    # Valid transitions
    current = exp.get("status", "registered")
    allowed = VALID_TRANSITIONS.get(current, set())
    if allowed:
        print(f"\n  Allowed transitions: {', '.join(sorted(allowed))}")
    else:
        print(f"\n  Terminal state — no transitions allowed")

    print()
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    registry = load_registry()
    experiments = registry.get("experiments", {})

    # Validate
    exp_id = args.id.strip().upper()
    if not re.match(r"^EXP-\d+$", exp_id):
        print(f"ERROR: ID must be EXP-NNN format. Got: '{exp_id}'", file=sys.stderr)
        return 1
    if exp_id in experiments:
        print(f"ERROR: {exp_id} already exists in registry.", file=sys.stderr)
        return 1

    creator = args.creator.strip().lower()
    if creator not in VALID_CREATORS:
        print(f"ERROR: creator must be one of {sorted(VALID_CREATORS)}.", file=sys.stderr)
        return 1

    # ID range check
    num = int(exp_id.split("-")[1])
    if exp_id not in {"EXP-1220"}:
        lo, hi = CREATOR_RANGES[creator]
        if not (lo <= num <= hi):
            print(f"ERROR: {exp_id} out of range for '{creator}' (allowed: EXP-{lo:03d} to EXP-{hi}).",
                  file=sys.stderr)
            return 1

    now = datetime.now(timezone.utc)
    entry = {
        "id": exp_id,
        "name": args.name,
        "created_by": creator,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "status": "registered",
        "ticker": args.ticker,
        "config_path": args.config,
        "env_file": args.env_file,
        "db_path": args.db_path,
        "alpaca_account_id": args.account_id,
        "strategy_type": args.strategy_type,
        "description": args.description,
        "notes": args.notes,
    }

    # Validate config and env exist if provided
    if args.config and not (ROOT / args.config).exists():
        print(f"WARNING: config file not found: {args.config}", file=sys.stderr)
    if args.env_file and not (ROOT / args.env_file).exists():
        print(f"WARNING: env file not found: {args.env_file}", file=sys.stderr)

    # Auto-discover if not provided
    if not entry["env_file"]:
        from experiments.registry import _env_file_for_exp
        entry["env_file"] = _env_file_for_exp(exp_id)
    if not entry["db_path"]:
        from experiments.registry import _db_path_for_exp
        entry["db_path"] = _db_path_for_exp(exp_id)

    # Clean None values
    entry = {k: v for k, v in entry.items() if v is not None}

    experiments[exp_id] = entry
    save_registry(registry)
    print(f"Registered {exp_id} — '{args.name}' (status: registered)")
    return 0


def cmd_transition(args: argparse.Namespace) -> int:
    """Handle activate/pause/stop/retire commands."""
    try:
        exp = transition_status(
            args.exp_id,
            args.target_status,
            reason=getattr(args, "reason", "") or "",
        )
        print(f"{args.exp_id} → {exp['status']}")
        return 0
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def cmd_validate(args: argparse.Namespace) -> int:
    registry = load_registry()
    errors = validate(registry, strict=args.strict)

    exp_count = len(registry.get("experiments", {}))
    research_count = sum(1 for eid in registry.get("experiments", {}) if is_research_entry(eid))

    if errors:
        print(f"registry.json VALIDATION FAILED ({len(errors)} error(s)):\n")
        for e in errors:
            print(f"  \u2717 {e}")
        return 1
    else:
        print(f"registry.json OK — {exp_count} experiments ({exp_count - research_count} real + {research_count} research)")
        return 0


def cmd_sync(args: argparse.Namespace) -> int:
    registry = load_registry()
    issues = 0

    # Orphan .env files
    orphan_envs = find_orphan_env_files(registry)
    if orphan_envs:
        print(f"\nOrphan .env files (no registry entry):")
        for f in orphan_envs:
            print(f"  \u2717 {f}")
        issues += len(orphan_envs)

    # Orphan DBs
    orphan_dbs = find_orphan_dbs(registry)
    if orphan_dbs:
        print(f"\nOrphan databases (no registry entry):")
        for f in orphan_dbs:
            print(f"  \u2717 {f}")
        issues += len(orphan_dbs)

    # Orphan processes
    orphan_procs = find_orphan_processes()
    if orphan_procs:
        print(f"\nOrphan processes (running for non-active experiments):")
        for p in orphan_procs:
            print(f"  \u2717 {p['exp_id']}: {_trunc(p['process'], 80)}")
        issues += len(orphan_procs)

    # Active but not running
    missing = find_active_not_running(registry)
    if missing:
        print(f"\nActive but no running process detected:")
        for eid in missing:
            print(f"  \u26a0 {eid}")
        issues += len(missing)

    # Experiments with missing files
    print(f"\nFile integrity check:")
    file_issues = 0
    for exp_id, exp in registry.get("experiments", {}).items():
        if is_research_entry(exp_id) or exp.get("status") in ("retired", "completed"):
            continue
        if exp.get("status") not in ("active", "paused", "stopped", "configuring"):
            continue

        cfg = exp.get("config_path") or exp.get("paper_config")
        if cfg and not (ROOT / cfg).exists():
            print(f"  \u2717 {exp_id}: config not found: {cfg}")
            file_issues += 1

        env = exp.get("env_file")
        if env and not (ROOT / env).exists():
            print(f"  \u2717 {exp_id}: env file not found: {env}")
            file_issues += 1

    if file_issues == 0:
        print("  All referenced files exist.")
    issues += file_issues

    if issues == 0:
        print("\nRegistry sync: all clean.")
    else:
        print(f"\nRegistry sync: {issues} issue(s) found.")

    return 1 if issues > 0 else 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="registry_cli",
        description="Experiment Registry CLI — single source of truth for all experiments.",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # list
    p_list = sub.add_parser("list", help="List experiments")
    p_list.add_argument(
        "--all", dest="filter", action="store_const", const="all", default="active",
        help="Show all experiments",
    )
    p_list.add_argument(
        "--active", dest="filter", action="store_const", const="active",
        help="Show active experiments (default)",
    )
    p_list.add_argument(
        "--registered", dest="filter", action="store_const", const="registered",
        help="Show registered/configuring experiments",
    )
    p_list.add_argument(
        "--retired", dest="filter", action="store_const", const="retired",
        help="Show retired experiments",
    )
    p_list.add_argument(
        "--research", dest="filter", action="store_const", const="research",
        help="Show research experiments (EXP-*-max)",
    )

    # status
    p_status = sub.add_parser("status", help="Detailed status for one experiment")
    p_status.add_argument("exp_id", help="Experiment ID (e.g. EXP-800)")

    # register
    p_reg = sub.add_parser("register", help="Register a new experiment")
    p_reg.add_argument("--id", required=True, help="Experiment ID")
    p_reg.add_argument("--creator", required=True, choices=sorted(VALID_CREATORS))
    p_reg.add_argument("--name", required=True, help="Short name")
    p_reg.add_argument("--ticker", default=None, help="Ticker symbol")
    p_reg.add_argument("--config", default=None, help="Path to config YAML")
    p_reg.add_argument("--env-file", default=None, help="Path to .env file")
    p_reg.add_argument("--db-path", default=None, help="Path to experiment DB")
    p_reg.add_argument("--account-id", default=None, help="Alpaca account ID")
    p_reg.add_argument("--strategy-type", default=None, help="Strategy type")
    p_reg.add_argument("--description", default=None, help="Description")
    p_reg.add_argument("--notes", default=None)

    # activate / pause / stop / retire
    for cmd_name, target, help_text in [
        ("activate", "active", "Activate an experiment"),
        ("pause", "paused", "Pause an experiment"),
        ("stop", "stopped", "Stop an experiment"),
        ("retire", "retired", "Retire an experiment permanently"),
    ]:
        p = sub.add_parser(cmd_name, help=help_text)
        p.add_argument("exp_id", help="Experiment ID")
        p.add_argument("--reason", default="", help="Reason for status change")
        p.set_defaults(target_status=target)

    # validate
    p_val = sub.add_parser("validate", help="Validate registry integrity")
    p_val.add_argument("--strict", action="store_true", help="Enable strict checks")

    # sync
    sub.add_parser("sync", help="Detect orphans and unregistered resources")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "list": cmd_list,
        "status": cmd_status,
        "register": cmd_register,
        "activate": cmd_transition,
        "pause": cmd_transition,
        "stop": cmd_transition,
        "retire": cmd_transition,
        "validate": cmd_validate,
        "sync": cmd_sync,
    }

    handler = handlers.get(args.command)
    if handler:
        return handler(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
