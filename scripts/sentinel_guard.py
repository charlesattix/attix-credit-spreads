"""
scripts/sentinel_guard.py — Importable sentinel guard for scanner scripts.

Thin wrapper around sentinel.guards.pre_scan_check().  Provides the same
function under both import paths:

    from sentinel.guards import pre_scan_check       # preferred (package)
    from scripts.sentinel_guard import pre_scan_check  # fallback (scripts/)

Also usable as a standalone CLI check:

    python3 scripts/sentinel_guard.py EXP-800
    python3 scripts/sentinel_guard.py EXP-800 --config configs/paper_exp800.yaml
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path so sentinel package is importable
# (handles the case where this script is run directly without the package installed)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sentinel.guards import (  # noqa: E402 — must come after sys.path setup
    pre_scan_check,
    _compute_fingerprint,
    _load_state,
    _send_alert,
)

__all__ = ["pre_scan_check"]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the SENTINEL pre-scan guard check for an experiment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes:
  0  Guard passed — scanner is clear to run.
  1  Guard failed — experiment is halted, config drifted, or API key dead.

Examples:
  python3 scripts/sentinel_guard.py EXP-800
  python3 scripts/sentinel_guard.py EXP-307 --config configs/paper_exp307.yaml
""",
    )
    parser.add_argument("experiment_id", help="Experiment ID (e.g. EXP-800)")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config file for fingerprint verification",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable DEBUG logging",
    )

    args = parser.parse_args()

    import logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    pre_scan_check(args.experiment_id, config_path=args.config)
    print(f"✅  SENTINEL guard passed for {args.experiment_id}")
    sys.exit(0)
