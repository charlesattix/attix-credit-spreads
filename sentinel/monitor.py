"""
SENTINEL — Daily Alpaca health checker.

Checks every registered experiment:
  - API liveness (catches dead keys like EXP-700)
  - Orphan detection (retired accounts with equity still sitting, like EXP-305)
  - Ghost detection (registry says active but Alpaca is dead/unreachable)
  - Stale detection (no trades in 3+ market days while active)
  - Duplicate detection (two experiments pointing to the same Alpaca account)
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Retired accounts with equity above this threshold are flagged as orphans.
ORPHAN_EQUITY_THRESHOLD = 100.0

# Active experiments with no orders for this many weekdays are flagged stale.
STALE_MARKET_DAYS = 3


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ExperimentHealth:
    exp_id: str
    account_id: Optional[str]
    registry_status: str          # paper_trading | retired | in_development | completed
    env_file: Optional[str]

    api_ok: bool = False
    api_error: Optional[str] = None
    equity: Optional[float] = None
    cash: Optional[float] = None
    open_positions: int = 0
    last_order_at: Optional[str] = None
    last_order_age_days: Optional[int] = None

    # Detection flags
    is_orphan: bool = False       # retired but has equity
    is_ghost: bool = False        # paper_trading but Alpaca dead / unreachable
    is_stale: bool = False        # active, API ok, no trades in 3+ market days
    is_duplicate: bool = False    # account_id shared with another experiment

    issues: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_env_file(path: Path) -> Dict[str, str]:
    """Parse a .env file into a plain dict. Never raises."""
    env: Dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def _find_env_file(project_root: Path, exp_id: str) -> Optional[Path]:
    """
    Locate the .env file for an experiment.

    Naming convention: EXP-400 → .env.exp400, EXP-1220 → .env.exp1220
    Special case: EXP-400 also checks .env.champion (legacy name).
    """
    numeric = exp_id.removeprefix("EXP-").lower()
    candidates = [project_root / f".env.exp{numeric}"]
    if exp_id == "EXP-400":
        candidates.append(project_root / ".env.champion")
    for c in candidates:
        if c.exists():
            return c
    return None


def _market_days_since(dt_str: str) -> Optional[int]:
    """
    Count weekday (market) days from *dt_str* to today.

    Returns None if *dt_str* cannot be parsed.
    """
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        start: date = dt.date()
        end: date = datetime.now(timezone.utc).date()
        days = 0
        cursor = start
        while cursor < end:
            if cursor.weekday() < 5:  # Mon–Fri
                days += 1
            cursor += timedelta(days=1)
        return days
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Single-experiment check
# ---------------------------------------------------------------------------


def check_experiment(
    exp_id: str,
    account_id: Optional[str],
    registry_status: str,
    env_file: Optional[Path],
) -> ExperimentHealth:
    """Ping one Alpaca account and return its health status."""
    health = ExperimentHealth(
        exp_id=exp_id,
        account_id=account_id,
        registry_status=registry_status,
        env_file=str(env_file) if env_file else None,
    )

    # Nothing to check without an account
    if not account_id:
        if registry_status == "paper_trading":
            health.issues.append("paper_trading status but no account_id in registry")
        return health

    # Env file must exist
    if not env_file or not env_file.exists():
        expected = f".env.exp{exp_id.removeprefix('EXP-').lower()}"
        health.issues.append(f"env file not found (expected {expected})")
        if registry_status == "paper_trading":
            health.is_ghost = True
        return health

    env = _load_env_file(env_file)
    api_key = env.get("ALPACA_API_KEY")
    api_secret = env.get("ALPACA_API_SECRET") or env.get("ALPACA_SECRET_KEY")

    if not api_key or not api_secret:
        health.api_error = "missing ALPACA_API_KEY / ALPACA_API_SECRET in env file"
        health.issues.append(health.api_error)
        if registry_status == "paper_trading":
            health.is_ghost = True
        return health

    # --- Alpaca ping ---
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        paper = env.get("ALPACA_PAPER", "true").lower() != "false"
        client = TradingClient(api_key, api_secret, paper=paper)

        acct = client.get_account()
        health.api_ok = True
        health.equity = float(acct.equity)
        health.cash = float(acct.cash)

        positions = client.get_all_positions()
        health.open_positions = len(positions)

        # Most recent order — tells us when trading was last active
        try:
            orders = client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.ALL, limit=1)
            )
            if orders:
                health.last_order_at = str(orders[0].submitted_at)
                health.last_order_age_days = _market_days_since(health.last_order_at)
        except Exception as oe:
            logger.debug("%s: order fetch failed: %s", exp_id, oe)

    except Exception as e:
        health.api_ok = False
        err = str(e)
        health.api_error = err[:140]

        if "401" in err or "unauthorized" in err.lower():
            health.issues.append("API keys dead (401 Unauthorized)")
        else:
            health.issues.append(f"API error: {health.api_error}")

        if registry_status == "paper_trading":
            health.is_ghost = True
        return health

    # --- Orphan: retired account with equity ---
    if registry_status == "retired" and health.equity and health.equity > ORPHAN_EQUITY_THRESHOLD:
        health.is_orphan = True
        health.issues.append(
            f"ORPHAN: retired account holds ${health.equity:,.0f} (acct {account_id})"
        )

    # --- Stale: active but no trades in 3+ market days ---
    if registry_status == "paper_trading" and health.api_ok:
        if health.last_order_at is None:
            health.is_stale = True
            health.issues.append("STALE: no order history on this account")
        elif (
            health.last_order_age_days is not None
            and health.last_order_age_days >= STALE_MARKET_DAYS
        ):
            health.is_stale = True
            health.issues.append(
                f"STALE: last order was {health.last_order_age_days} market days ago"
            )

    return health


# ---------------------------------------------------------------------------
# All-experiment sweep
# ---------------------------------------------------------------------------


def check_all_experiments(
    registry: dict,
    project_root: Path,
) -> List[ExperimentHealth]:
    """
    Run health checks for every experiment with an account_id.

    Skips pure in-development / completed-research entries that have no
    Alpaca account.  Adds duplicate-account flags after all checks run.
    """
    results: List[ExperimentHealth] = []
    account_seen: Dict[str, str] = {}  # account_id → first exp_id that claimed it

    experiments = registry.get("experiments", {})

    for exp_id, exp in experiments.items():
        account_id: Optional[str] = exp.get("account_id")
        status: str = exp.get("status", "unknown")

        # Skip pure research / blocked entries that have no Alpaca footprint
        if status in ("in_development", "completed") and not account_id:
            continue

        env_file = _find_env_file(project_root, exp_id)
        health = check_experiment(exp_id, account_id, status, env_file)

        # Duplicate account detection
        if account_id:
            if account_id in account_seen:
                other_exp = account_seen[account_id]
                health.is_duplicate = True
                health.issues.append(
                    f"DUPLICATE: account {account_id} also used by {other_exp}"
                )
                logger.warning(
                    "SENTINEL: duplicate account %s — %s and %s",
                    account_id, exp_id, other_exp,
                )
            else:
                account_seen[account_id] = exp_id

        results.append(health)

        eq_str = f"${health.equity:,.0f}" if health.equity is not None else "N/A"
        logger.info(
            "%-12s [%-15s] api_ok=%-5s equity=%-12s positions=%d issues=%d",
            exp_id, status, str(health.api_ok), eq_str,
            health.open_positions, len(health.issues),
        )

    return results
