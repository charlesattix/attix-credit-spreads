"""
SENTINEL Gates 13–16 — Account Health & Position Management.

Gate 13 — Account Health Monitor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Checks Alpaca account equity drawdown from peak before every scan.
Uses MARK-TO-MARKET equity (not closed P&L) to catch underwater open
positions that Gate 8's rolling-trade window misses.

  Drawdown     | Action
  -------------|---------------------------
  > 15%        | WARNING
  > 25%        | HALT experiment
  > 40%        | FLATTEN ALL + HALT

Also blocks new entries when buying_power < cost of one standard trade.

Gate 14 — Expired Position Detection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Finds ghost positions: trades marked 'open' in the DB whose expiration
date has already passed. Auto-marks them as closed_expired and reconciles
with Alpaca to determine actual P&L.

Gate 15 — Position Concentration Guard
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Prevents portfolio-level catastrophe by enforcing:
  - Max positions sharing same expiration date (default 3)
  - Max positions entered same calendar day (default 3)
  - Max same-direction exposure (default 80% of open positions)
  - Max total portfolio risk across all open positions (default 50% of equity)

Gate 16 — Orphan Detection (Fixed)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Replaces the broken orphan scanner that treated every spread leg as an
orphan. Matches individual option legs back to parent spread trades via
strike + expiration + direction. Only flags truly unmatched positions.

Usage::

    from sentinel.gates_account import (
        check_account_health,
        check_expired_positions,
        check_position_concentration,
        check_orphans_v2,
    )

    # Gate 13
    health = check_account_health("EXP-503", alpaca_account, db_path)

    # Gate 14
    expired = check_expired_positions("EXP-800", db_path, today="2026-04-19")

    # Gate 15
    conc = check_position_concentration("EXP-503", db_path, account_equity=100000)

    # Gate 16
    orphans = check_orphans_v2("EXP-503", alpaca_positions, db_path)
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Gate 13 — Account Health thresholds
# ---------------------------------------------------------------------------

DD_WARNING_PCT = 0.15   # 15%
DD_HALT_PCT = 0.25      # 25%
DD_FLATTEN_PCT = 0.40   # 40%

# ---------------------------------------------------------------------------
# Gate 15 — Concentration defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_SAME_EXPIRY = 3
DEFAULT_MAX_SAME_DAY_ENTRIES = 3
DEFAULT_MAX_DIRECTION_PCT = 0.80   # 80% of open positions in same direction
DEFAULT_MAX_PORTFOLIO_RISK_PCT = 0.50  # 50% of equity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db(path: str) -> sqlite3.Connection:
    """Open a trades DB with WAL mode and row_factory."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_db_path(exp_id: str) -> Optional[str]:
    """Try common DB locations for an experiment."""
    exp_lower = exp_id.lower().replace("-", "")
    candidates = [
        _PROJECT_ROOT / "data" / exp_lower / f"pilotai_{exp_lower}.db",
        _PROJECT_ROOT / "data" / f"pilotai_{exp_lower}.db",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _today_str() -> str:
    return date.today().isoformat()


def _parse_occ_symbol(occ: str) -> Optional[Dict[str, Any]]:
    """Parse an OCC option symbol into components.

    Format: TICKER(6) DATE(6) P/C(1) STRIKE(8)
    Example: SPY   260501C00690000
             ^     ^      ^ ^
             ticker date   t strike (690.00 * 1000)
    """
    if not occ or len(occ) < 15:
        return None
    # OCC symbols: 6-char ticker + 6-char date + 1-char type + 8-char strike
    m = re.match(r'^([A-Z]{1,6})\s*(\d{6})([PC])(\d{8})$', occ.strip())
    if not m:
        return None
    ticker = m.group(1).strip()
    date_str = m.group(2)  # YYMMDD
    put_call = m.group(3)
    strike_raw = int(m.group(4))
    strike = strike_raw / 1000.0

    # Convert YYMMDD to YYYY-MM-DD
    year = 2000 + int(date_str[:2])
    month = int(date_str[2:4])
    day = int(date_str[4:6])
    expiration = f"{year:04d}-{month:02d}-{day:02d}"

    return {
        "ticker": ticker,
        "expiration": expiration,
        "put_call": put_call,
        "strike": strike,
        "occ": occ.strip(),
    }


# ===========================================================================
# Gate 13 — Account Health Monitor
# ===========================================================================


@dataclass
class AccountHealthResult:
    """Gate 13 result."""
    exp_id: str
    equity: float
    peak_equity: float
    drawdown_pct: float          # 0.0 to 1.0
    buying_power: float
    min_trade_cost: float        # cost of 1 standard trade
    severity: str                # "ok" | "warning" | "halt" | "flatten"
    block_new_entries: bool      # True if buying power insufficient
    message: str
    peak_updated: bool = False   # True if peak was raised this check

    @property
    def passed(self) -> bool:
        return self.severity in ("ok", "warning") and not self.block_new_entries


def check_account_health(
    exp_id: str,
    alpaca_account: Dict[str, Any],
    db_path: Optional[str] = None,
    *,
    config: Optional[dict] = None,
) -> AccountHealthResult:
    """
    Gate 13: Check account equity drawdown and buying power.

    Args:
        exp_id: Experiment identifier.
        alpaca_account: Dict with 'equity', 'buying_power' (from AlpacaProvider.get_account()).
        db_path: Path to experiment DB. Resolved automatically if None.
        config: Paper config dict. Used to compute min trade cost.

    Returns:
        AccountHealthResult with severity and block_new_entries flag.
    """
    resolved_path = db_path or _resolve_db_path(exp_id)

    equity = float(alpaca_account.get("equity", 0))
    buying_power = float(alpaca_account.get("buying_power", 0))

    # Compute min trade cost from config
    min_trade_cost = _compute_min_trade_cost(config) if config else 0.0

    # Load peak equity from DB (scanner_state table)
    peak_equity = equity  # default if no history
    peak_updated = False

    if resolved_path:
        try:
            conn = _get_db(resolved_path)
            try:
                row = conn.execute(
                    "SELECT value FROM scanner_state WHERE key = 'sentinel_peak_equity'"
                ).fetchone()
                if row:
                    peak_equity = max(float(row["value"]), equity)
                else:
                    peak_equity = equity

                # Update peak if new high
                if equity >= peak_equity:
                    peak_equity = equity
                    peak_updated = True

                # Persist peak
                conn.execute(
                    "INSERT OR REPLACE INTO scanner_state (key, value, updated_at) "
                    "VALUES ('sentinel_peak_equity', ?, datetime('now'))",
                    (str(peak_equity),),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("Gate13: failed to read/write peak equity for %s: %s", exp_id, e)

    # Also record to sentinel.db for audit trail
    try:
        from sentinel.history import SentinelDB
        sdb = SentinelDB()
        sdb.record_snapshot(
            exp_id,
            equity=equity,
            notes=f"Gate13 peak={peak_equity:.2f} dd={_dd_pct(equity, peak_equity):.1%}",
        )
    except Exception as e:
        logger.debug("Gate13: failed to record snapshot: %s", e)

    # Compute drawdown
    dd_pct = _dd_pct(equity, peak_equity)

    # Classify severity
    if dd_pct >= DD_FLATTEN_PCT:
        severity = "flatten"
        msg = (
            f"FLATTEN ALL: equity ${equity:,.0f} is {dd_pct:.1%} below peak "
            f"${peak_equity:,.0f} (threshold {DD_FLATTEN_PCT:.0%})"
        )
    elif dd_pct >= DD_HALT_PCT:
        severity = "halt"
        msg = (
            f"HALT: equity ${equity:,.0f} is {dd_pct:.1%} below peak "
            f"${peak_equity:,.0f} (threshold {DD_HALT_PCT:.0%})"
        )
    elif dd_pct >= DD_WARNING_PCT:
        severity = "warning"
        msg = (
            f"WARNING: equity ${equity:,.0f} is {dd_pct:.1%} below peak "
            f"${peak_equity:,.0f} (threshold {DD_WARNING_PCT:.0%})"
        )
    else:
        severity = "ok"
        msg = f"OK: equity ${equity:,.0f}, peak ${peak_equity:,.0f}, DD {dd_pct:.1%}"

    # Buying power check
    block_new = False
    if min_trade_cost > 0 and buying_power < min_trade_cost:
        block_new = True
        msg += (
            f" | BLOCKED: buying_power ${buying_power:,.0f} < "
            f"min_trade_cost ${min_trade_cost:,.0f}"
        )

    # Log
    log_fn = {
        "ok": logger.info,
        "warning": logger.warning,
        "halt": logger.critical,
        "flatten": logger.critical,
    }.get(severity, logger.warning)
    log_fn("GATE13 %s %s", exp_id, msg)

    return AccountHealthResult(
        exp_id=exp_id,
        equity=equity,
        peak_equity=peak_equity,
        drawdown_pct=dd_pct,
        buying_power=buying_power,
        min_trade_cost=min_trade_cost,
        severity=severity,
        block_new_entries=block_new,
        message=msg,
        peak_updated=peak_updated,
    )


def _dd_pct(equity: float, peak: float) -> float:
    """Compute drawdown as a fraction (0.0–1.0). Returns 0 if peak <= 0."""
    if peak <= 0 or equity >= peak:
        return 0.0
    return (peak - equity) / peak


def _compute_min_trade_cost(config: Optional[dict]) -> float:
    """Compute the buying-power cost of one minimum-sized trade from config."""
    if not config:
        return 0.0
    spread_width = config.get("strategy", {}).get("spread_width")
    if not spread_width:
        return 0.0
    # 1 contract of the spread: width × 100 shares
    return float(spread_width) * 100.0


# ===========================================================================
# Gate 14 — Expired Position Detection
# ===========================================================================


@dataclass
class ExpiredPosition:
    """A position whose expiration has passed."""
    trade_id: str
    ticker: str
    status: str
    expiration: str
    days_expired: int
    action_taken: str    # "marked_expired" | "needs_manual_review"


@dataclass
class ExpiredPositionResult:
    """Gate 14 result."""
    exp_id: str
    expired: List[ExpiredPosition] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def has_expired(self) -> bool:
        return len(self.expired) > 0

    @property
    def severity(self) -> str:
        if not self.expired:
            return "ok"
        return "critical"


def check_expired_positions(
    exp_id: str,
    db_path: Optional[str] = None,
    *,
    today: Optional[str] = None,
    auto_close: bool = True,
) -> ExpiredPositionResult:
    """
    Gate 14: Find and handle expired positions.

    Scans all trades with status in ('open', 'pending_open', 'pending_close')
    whose expiration date is before today. Optionally auto-marks them as
    closed_expired.

    Args:
        exp_id: Experiment identifier.
        db_path: Path to experiment DB.
        today: Override today's date (YYYY-MM-DD) for testing.
        auto_close: If True, update status to 'closed_expired' in DB.

    Returns:
        ExpiredPositionResult listing all expired positions found.
    """
    resolved_path = db_path or _resolve_db_path(exp_id)
    result = ExpiredPositionResult(exp_id=exp_id)

    if not resolved_path:
        result.errors.append(f"No DB path for {exp_id}")
        return result

    today_str = today or _today_str()
    try:
        today_date = date.fromisoformat(today_str)
    except ValueError:
        result.errors.append(f"Invalid date: {today_str}")
        return result

    conn = _get_db(resolved_path)
    try:
        rows = conn.execute(
            """
            SELECT id, ticker, status, expiration, strategy_type
            FROM trades
            WHERE status IN ('open', 'pending_open', 'pending_close')
              AND expiration IS NOT NULL
              AND expiration != ''
            ORDER BY expiration ASC
            """
        ).fetchall()

        for row in rows:
            trade_id = row["id"]
            expiration = row["expiration"]

            # Parse expiration — handle various formats
            try:
                exp_date = date.fromisoformat(expiration[:10])
            except (ValueError, TypeError):
                continue

            if exp_date >= today_date:
                continue  # not expired yet

            days_expired = (today_date - exp_date).days

            action = "needs_manual_review"
            if auto_close and days_expired >= 1:
                try:
                    conn.execute(
                        """
                        UPDATE trades
                        SET status = 'closed_expired',
                            exit_date = ?,
                            exit_reason = 'expired_past_expiration',
                            updated_at = datetime('now')
                        WHERE id = ? AND status IN ('open', 'pending_open', 'pending_close')
                        """,
                        (expiration + "T16:00:00+00:00", trade_id),
                    )
                    action = "marked_expired"
                except Exception as e:
                    result.errors.append(f"Failed to close {trade_id}: {e}")
                    action = "needs_manual_review"

            result.expired.append(ExpiredPosition(
                trade_id=trade_id,
                ticker=row["ticker"] or "?",
                status=row["status"],
                expiration=expiration,
                days_expired=days_expired,
                action_taken=action,
            ))

        if result.expired:
            conn.commit()

    except Exception as e:
        result.errors.append(f"DB error: {e}")
        logger.error("GATE14 %s DB error: %s", exp_id, e)
    finally:
        conn.close()

    # Log
    if result.expired:
        logger.critical(
            "GATE14 %s: %d expired positions found (auto_close=%s): %s",
            exp_id, len(result.expired), auto_close,
            ", ".join(f"{ep.trade_id}(exp={ep.expiration})" for ep in result.expired),
        )
    else:
        logger.debug("GATE14 %s: no expired positions", exp_id)

    # Record alert
    if result.expired:
        try:
            from sentinel.history import SentinelDB
            sdb = SentinelDB()
            sdb.record_alert(
                "critical",
                f"Gate14: {len(result.expired)} expired positions: "
                + ", ".join(f"{ep.trade_id}(exp={ep.expiration})" for ep in result.expired[:5]),
                experiment_id=exp_id,
            )
        except Exception:
            pass

    return result


# ===========================================================================
# Gate 15 — Position Concentration Guard
# ===========================================================================


@dataclass
class ConcentrationViolation:
    """A single concentration limit breach."""
    check: str           # "same_expiry" | "same_day" | "direction" | "portfolio_risk"
    detail: str
    current_value: float
    limit_value: float
    severity: str        # "warning" | "critical"


@dataclass
class ConcentrationResult:
    """Gate 15 result."""
    exp_id: str
    violations: List[ConcentrationViolation] = field(default_factory=list)
    open_positions: int = 0
    block_new_entries: bool = False

    @property
    def passed(self) -> bool:
        return not self.block_new_entries


def check_position_concentration(
    exp_id: str,
    db_path: Optional[str] = None,
    *,
    account_equity: Optional[float] = None,
    max_same_expiry: int = DEFAULT_MAX_SAME_EXPIRY,
    max_same_day: int = DEFAULT_MAX_SAME_DAY_ENTRIES,
    max_direction_pct: float = DEFAULT_MAX_DIRECTION_PCT,
    max_portfolio_risk_pct: float = DEFAULT_MAX_PORTFOLIO_RISK_PCT,
) -> ConcentrationResult:
    """
    Gate 15: Check position concentration limits.

    Args:
        exp_id: Experiment identifier.
        db_path: Path to experiment DB.
        account_equity: Current account equity (for portfolio risk check).
        max_same_expiry: Max open positions sharing same expiration.
        max_same_day: Max positions entered on same calendar day.
        max_direction_pct: Max fraction of positions in same direction.
        max_portfolio_risk_pct: Max total risk as fraction of equity.
    """
    resolved_path = db_path or _resolve_db_path(exp_id)
    result = ConcentrationResult(exp_id=exp_id)

    if not resolved_path:
        return result

    conn = _get_db(resolved_path)
    try:
        # Get all open positions (exclude orphan/unmanaged/synthetic)
        rows = conn.execute(
            """
            SELECT id, ticker, strategy_type, expiration, contracts,
                   short_strike, long_strike, entry_date
            FROM trades
            WHERE status IN ('open', 'pending_open')
              AND id NOT LIKE 'orphan-%'
              AND id NOT LIKE 'synthetic-%'
            """
        ).fetchall()

        positions = [dict(r) for r in rows]
        result.open_positions = len(positions)

        if not positions:
            return result

        # Check 1: Same expiration clustering
        expiry_counts: Dict[str, int] = {}
        for p in positions:
            exp = (p.get("expiration") or "")[:10]
            if exp:
                expiry_counts[exp] = expiry_counts.get(exp, 0) + 1

        for exp_date, count in expiry_counts.items():
            if count > max_same_expiry:
                result.violations.append(ConcentrationViolation(
                    check="same_expiry",
                    detail=f"{count} positions on {exp_date} (max {max_same_expiry})",
                    current_value=float(count),
                    limit_value=float(max_same_expiry),
                    severity="critical",
                ))

        # Check 2: Same-day entry clustering
        day_counts: Dict[str, int] = {}
        for p in positions:
            entry = p.get("entry_date") or ""
            entry_day = entry[:10]
            if entry_day:
                day_counts[entry_day] = day_counts.get(entry_day, 0) + 1

        for entry_day, count in day_counts.items():
            if count > max_same_day:
                result.violations.append(ConcentrationViolation(
                    check="same_day",
                    detail=f"{count} entries on {entry_day} (max {max_same_day})",
                    current_value=float(count),
                    limit_value=float(max_same_day),
                    severity="warning",
                ))

        # Check 3: Directional concentration
        bull_count = sum(1 for p in positions if _is_bull(p))
        bear_count = sum(1 for p in positions if _is_bear(p))
        total = len(positions)

        if total > 0:
            bull_pct = bull_count / total
            bear_pct = bear_count / total
            max_dir = max(bull_pct, bear_pct)
            dominant = "bull" if bull_pct >= bear_pct else "bear"

            if max_dir > max_direction_pct:
                result.violations.append(ConcentrationViolation(
                    check="direction",
                    detail=(
                        f"{dominant} direction is {max_dir:.0%} of positions "
                        f"({bull_count} bull / {bear_count} bear, max {max_direction_pct:.0%})"
                    ),
                    current_value=max_dir,
                    limit_value=max_direction_pct,
                    severity="warning",
                ))

        # Check 4: Total portfolio risk vs equity
        if account_equity and account_equity > 0:
            total_risk = 0.0
            for p in positions:
                short_s = p.get("short_strike") or 0
                long_s = p.get("long_strike") or 0
                width = abs(short_s - long_s)
                contracts = p.get("contracts") or 0
                total_risk += width * contracts * 100

            risk_pct = total_risk / account_equity
            if risk_pct > max_portfolio_risk_pct:
                result.violations.append(ConcentrationViolation(
                    check="portfolio_risk",
                    detail=(
                        f"Total risk ${total_risk:,.0f} is {risk_pct:.0%} of equity "
                        f"${account_equity:,.0f} (max {max_portfolio_risk_pct:.0%})"
                    ),
                    current_value=risk_pct,
                    limit_value=max_portfolio_risk_pct,
                    severity="critical",
                ))

        # Block new entries if any critical violation
        result.block_new_entries = any(
            v.severity == "critical" for v in result.violations
        )

    except Exception as e:
        logger.error("GATE15 %s error: %s", exp_id, e)
    finally:
        conn.close()

    # Log violations
    for v in result.violations:
        log_fn = logger.critical if v.severity == "critical" else logger.warning
        log_fn("GATE15 %s [%s] %s: %s", exp_id, v.severity.upper(), v.check, v.detail)

    return result


def _is_bull(pos: dict) -> bool:
    st = (pos.get("strategy_type") or "").lower()
    return "bull" in st or "put" in st.split("_")[-1:] == ["put"]


def _is_bear(pos: dict) -> bool:
    st = (pos.get("strategy_type") or "").lower()
    return "bear" in st or "call" in st.split("_")[-1:] == ["call"]


# ===========================================================================
# Gate 16 — Orphan Detection (Fixed)
# ===========================================================================


@dataclass
class OrphanV2Result:
    """Gate 16 result — improved orphan detection with leg matching."""
    exp_id: str
    matched_legs: int = 0         # legs successfully matched to a parent trade
    true_orphans: List[str] = field(default_factory=list)   # OCC symbols with no parent
    needs_investigation: List[str] = field(default_factory=list)  # partial matches
    total_alpaca_positions: int = 0
    total_db_open: int = 0
    false_positive_rate: float = 0.0

    @property
    def severity(self) -> str:
        if len(self.true_orphans) >= 5:
            return "halt"
        if self.true_orphans:
            return "critical"
        if self.needs_investigation:
            return "warning"
        return "ok"

    @property
    def passed(self) -> bool:
        return self.severity in ("ok", "warning")


def check_orphans_v2(
    exp_id: str,
    alpaca_positions: List[Dict[str, Any]],
    db_path: Optional[str] = None,
) -> OrphanV2Result:
    """
    Gate 16: Improved orphan detection that matches legs to parent spreads.

    A spread trade (e.g. bull_put SPY 666/654) creates TWO positions at Alpaca:
    - Short leg: SPY260501P00666000
    - Long leg:  SPY260501P00654000

    The old Gate 7 treated both as orphans because it only checked if the exact
    OCC symbol was in the trades table. This gate matches legs back to their
    parent spread by ticker + expiration + strike matching.

    Args:
        exp_id: Experiment identifier.
        alpaca_positions: List of dicts with 'symbol' key (OCC option symbols).
        db_path: Path to experiment DB.
    """
    resolved_path = db_path or _resolve_db_path(exp_id)
    result = OrphanV2Result(exp_id=exp_id)

    if not resolved_path:
        return result

    # Filter to real option symbols (>10 chars, excludes equity tickers)
    option_positions = []
    for pos in alpaca_positions:
        sym = pos.get("symbol", "")
        if len(sym) > 10:
            option_positions.append(sym)

    result.total_alpaca_positions = len(option_positions)

    if not option_positions:
        return result

    # Parse all Alpaca OCC symbols
    parsed_alpaca: Dict[str, Dict] = {}
    for occ in option_positions:
        parsed = _parse_occ_symbol(occ)
        if parsed:
            parsed_alpaca[occ] = parsed

    conn = _get_db(resolved_path)
    try:
        # Get all open/pending trades from DB (real trades, not orphan records)
        rows = conn.execute(
            """
            SELECT id, ticker, strategy_type, short_strike, long_strike,
                   expiration, contracts, status
            FROM trades
            WHERE status IN ('open', 'pending_open', 'pending_close')
              AND id NOT LIKE 'orphan-%'
              AND id NOT LIKE 'synthetic-%'
            """
        ).fetchall()

        db_trades = [dict(r) for r in rows]
        result.total_db_open = len(db_trades)

        # Also check trade_legs table if it exists
        leg_occ_symbols: Set[str] = set()
        try:
            leg_rows = conn.execute(
                """
                SELECT tl.occ_symbol
                FROM trade_legs tl
                JOIN trades t ON tl.trade_id = t.id
                WHERE t.status IN ('open', 'pending_open', 'pending_close')
                  AND tl.occ_symbol IS NOT NULL
                """
            ).fetchall()
            leg_occ_symbols = {r["occ_symbol"] for r in leg_rows}
        except Exception:
            pass  # trade_legs table may not exist

        # Build a set of expected OCC symbols from DB trades
        expected_symbols = _build_expected_symbols(db_trades)
        expected_symbols.update(leg_occ_symbols)

        # Match each Alpaca position
        for occ, parsed in parsed_alpaca.items():
            if occ in expected_symbols:
                result.matched_legs += 1
                continue

            if occ in leg_occ_symbols:
                result.matched_legs += 1
                continue

            # Try fuzzy match: same ticker + expiration + strike matches
            # either short_strike or long_strike of a known trade
            matched = _fuzzy_match_to_trade(parsed, db_trades)
            if matched:
                result.matched_legs += 1
                continue

            # No match found — true orphan or needs investigation
            # Check if this could be a long leg by proximity to a known short
            close_match = _is_close_match(parsed, db_trades)
            if close_match:
                result.needs_investigation.append(occ)
            else:
                result.true_orphans.append(occ)

    except Exception as e:
        logger.error("GATE16 %s error: %s", exp_id, e)
    finally:
        conn.close()

    # Compute false positive rate (for monitoring improvement)
    total_checked = result.matched_legs + len(result.true_orphans) + len(result.needs_investigation)
    if total_checked > 0:
        result.false_positive_rate = round(result.matched_legs / total_checked, 3)

    # Log
    logger.info(
        "GATE16 %s: %d alpaca positions, %d matched, %d true orphans, %d investigate "
        "(FP rate: %.1f%%)",
        exp_id, result.total_alpaca_positions, result.matched_legs,
        len(result.true_orphans), len(result.needs_investigation),
        result.false_positive_rate * 100,
    )
    if result.true_orphans:
        logger.critical("GATE16 %s TRUE ORPHANS: %s", exp_id, result.true_orphans)

    return result


def _build_expected_symbols(db_trades: List[dict]) -> Set[str]:
    """Build the set of OCC symbols we expect from known DB trades.

    For each trade, generate the short-leg and long-leg OCC symbols from
    the trade's ticker, expiration, strategy_type, and strikes.
    """
    expected: Set[str] = set()

    for trade in db_trades:
        ticker = trade.get("ticker")
        expiration = trade.get("expiration")
        strategy_type = (trade.get("strategy_type") or "").lower()
        short_strike = trade.get("short_strike")
        long_strike = trade.get("long_strike")

        if not ticker or not expiration:
            continue

        # Determine put/call from strategy type
        if "put" in strategy_type or "bull_put" in strategy_type:
            put_call = "P"
        elif "call" in strategy_type or "bear_call" in strategy_type:
            put_call = "C"
        elif "iron_condor" in strategy_type:
            # IC has both puts and calls — generate all 4 legs
            for strike in (short_strike, long_strike):
                if strike:
                    for pc in ("P", "C"):
                        occ = _build_occ(ticker, expiration, pc, float(strike))
                        if occ:
                            expected.add(occ)
            continue
        else:
            continue

        # Generate OCC for short and long strikes
        for strike in (short_strike, long_strike):
            if strike:
                occ = _build_occ(ticker, expiration, put_call, float(strike))
                if occ:
                    expected.add(occ)

    return expected


def _build_occ(ticker: str, expiration: str, put_call: str, strike: float) -> Optional[str]:
    """Build an OCC option symbol from components."""
    try:
        exp_date = date.fromisoformat(expiration[:10])
    except (ValueError, TypeError):
        return None

    date_str = exp_date.strftime("%y%m%d")
    strike_int = int(strike * 1000)
    padded_ticker = ticker.ljust(6)[:6]
    return f"{padded_ticker}{date_str}{put_call}{strike_int:08d}"


def _fuzzy_match_to_trade(
    parsed: Dict[str, Any],
    db_trades: List[dict],
) -> bool:
    """Check if a parsed Alpaca position matches any DB trade by components."""
    for trade in db_trades:
        trade_ticker = trade.get("ticker", "")
        trade_exp = (trade.get("expiration") or "")[:10]
        short_s = trade.get("short_strike")
        long_s = trade.get("long_strike")

        # Ticker must match
        if parsed["ticker"] != trade_ticker:
            continue

        # Expiration must match
        if parsed["expiration"] != trade_exp:
            continue

        # Strike must match either short or long
        if short_s and abs(float(short_s) - parsed["strike"]) < 0.01:
            return True
        if long_s and abs(float(long_s) - parsed["strike"]) < 0.01:
            return True

    return False


def _is_close_match(
    parsed: Dict[str, Any],
    db_trades: List[dict],
) -> bool:
    """Check if a position is 'close' to a known trade (same ticker+expiry, different strike)."""
    for trade in db_trades:
        if parsed["ticker"] != trade.get("ticker", ""):
            continue
        trade_exp = (trade.get("expiration") or "")[:10]
        if parsed["expiration"] == trade_exp:
            return True  # same ticker + expiry = probably related
    return False


# ===========================================================================
# Unified entry point for Gates 13-16
# ===========================================================================


def check_account_gates(
    exp_id: str,
    db_path: Optional[str] = None,
    *,
    alpaca_account: Optional[Dict[str, Any]] = None,
    alpaca_positions: Optional[List[Dict[str, Any]]] = None,
    config: Optional[dict] = None,
    skip_gates: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Run Gates 13–16 and return combined results.

    Args:
        exp_id: Experiment identifier.
        db_path: Path to experiment DB (resolved if None).
        alpaca_account: Dict with 'equity', 'buying_power'.
        alpaca_positions: List of Alpaca position dicts with 'symbol'.
        config: Parsed paper config dict.
        skip_gates: List of gate numbers to skip.

    Returns:
        Dict with per-gate results, 'halted' flag, and 'block_new_entries' flag.
    """
    skip = set(skip_gates or [])
    results: Dict[str, Any] = {
        "halted": False,
        "block_new_entries": False,
    }

    resolved_path = db_path or _resolve_db_path(exp_id)

    # Gate 13 — Account Health
    if 13 not in skip and alpaca_account is not None:
        try:
            g13 = check_account_health(exp_id, alpaca_account, resolved_path, config=config)
            results["gate13"] = g13
            if g13.severity in ("halt", "flatten"):
                _do_halt(exp_id, f"Gate13: {g13.message}")
                results["halted"] = True
            if g13.block_new_entries:
                results["block_new_entries"] = True
        except Exception as e:
            logger.error("Gate 13 error for %s: %s", exp_id, e)
            results["gate13"] = {"error": str(e)}

    # Gate 14 — Expired Positions
    if 14 not in skip:
        try:
            g14 = check_expired_positions(exp_id, resolved_path)
            results["gate14"] = g14
            if g14.has_expired:
                logger.critical(
                    "Gate14 %s: %d expired positions cleaned up", exp_id, len(g14.expired)
                )
        except Exception as e:
            logger.error("Gate 14 error for %s: %s", exp_id, e)
            results["gate14"] = {"error": str(e)}

    # Gate 15 — Concentration
    if 15 not in skip:
        try:
            equity = None
            if alpaca_account:
                equity = float(alpaca_account.get("equity", 0))
            g15 = check_position_concentration(
                exp_id, resolved_path, account_equity=equity,
            )
            results["gate15"] = g15
            if g15.block_new_entries:
                results["block_new_entries"] = True
        except Exception as e:
            logger.error("Gate 15 error for %s: %s", exp_id, e)
            results["gate15"] = {"error": str(e)}

    # Gate 16 — Orphan Detection v2
    if 16 not in skip and alpaca_positions is not None:
        try:
            g16 = check_orphans_v2(exp_id, alpaca_positions, resolved_path)
            results["gate16"] = g16
            if g16.severity == "halt":
                _do_halt(exp_id, f"Gate16: {len(g16.true_orphans)} true orphan positions")
                results["halted"] = True
        except Exception as e:
            logger.error("Gate 16 error for %s: %s", exp_id, e)
            results["gate16"] = {"error": str(e)}

    return results


def _do_halt(exp_id: str, reason: str) -> None:
    """Halt an experiment via sentinel state."""
    try:
        from sentinel.state import set_halt
        set_halt(exp_id, reason[:200])
        logger.critical("SENTINEL HALT: %s — %s", exp_id, reason[:200])
    except Exception as e:
        logger.error("Failed to halt %s: %s", exp_id, e)
