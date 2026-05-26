"""
Trade notification module — polls Alpaca for real order activity and sends
Telegram notifications for fills, closings, rejections, and daily P&L summaries.

Usage (cron every 5 min during market hours):
    python -m pilotai_signal.trade_notifications

Daily summary (cron at 4:15 PM ET):
    python -m pilotai_signal.trade_notifications --daily-summary

Notifications sent:
  📥 ORDER FILLED     — credit spread opened
  📤 POSITION CLOSED  — spread closed at profit/expiry
  ⚠️  STOP TRIGGERED  — spread closed at a loss
  ❌ ORDER REJECTED   — Alpaca rejected the order
  📊 DAILY SUMMARY    — account P&L, open positions, expiry warnings

Dedup:  every notified order_id is stored in data/trade_notifications.db.
        Re-polling the same order never sends twice.

Env vars (read from .env or environment):
  ALPACA_API_KEY / APCA_API_KEY_ID
  ALPACA_API_SECRET / APCA_API_SECRET_KEY
  ALPACA_PAPER (default: true)
  APCA_API_BASE_URL (optional override)
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from dotenv import load_dotenv

from .alerts import send_telegram

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — resolved from environment
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent

# Load .env only if values aren't already in the environment
load_dotenv(_PROJECT_ROOT / ".env", override=False)

# NOTE: In production, scheduler/jobs.py maps per-experiment keys
# (ALPACA_API_KEY_EXP400 → ALPACA_API_KEY) into subprocess env.
# Generic (non-suffixed) Railway key retired 2026-05-23.
ALPACA_API_KEY: str = (
    os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID", "")
)
ALPACA_API_SECRET: str = (
    os.environ.get("ALPACA_API_SECRET") or os.environ.get("APCA_API_SECRET_KEY", "")
)
ALPACA_PAPER: bool = os.environ.get("ALPACA_PAPER", "true").lower() not in ("false", "0", "no")
ALPACA_BASE_URL: str = os.environ.get("APCA_API_BASE_URL", "")

# Persistent DB for dedup tracking
NOTIFICATIONS_DB: Path = _PROJECT_ROOT / "data" / "trade_notifications.db"

# Look back this many hours when fetching orders (handles polling gaps)
ORDER_LOOKBACK_HOURS: int = 6
# Warn about positions expiring within this many days in the daily summary
EXPIRY_WARNING_DAYS: int = 3

# ---------------------------------------------------------------------------
# Dedup database
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notified_orders (
    order_id        TEXT    PRIMARY KEY,
    order_type      TEXT    NOT NULL,
    ticker          TEXT,
    short_symbol    TEXT,
    credit_received REAL,
    contracts       INTEGER,
    notified_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS daily_summary_log (
    summary_date    DATE    PRIMARY KEY,
    sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or NOTIFICATIONS_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


@contextmanager
def _db(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = _get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _is_notified(conn: sqlite3.Connection, order_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM notified_orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    return row is not None


def _mark_notified(
    conn: sqlite3.Connection,
    order_id: str,
    order_type: str,
    ticker: Optional[str] = None,
    short_symbol: Optional[str] = None,
    credit_received: Optional[float] = None,
    contracts: Optional[int] = None,
) -> None:
    try:
        conn.execute(
            """INSERT OR IGNORE INTO notified_orders
               (order_id, order_type, ticker, short_symbol, credit_received, contracts)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (order_id, order_type, ticker, short_symbol, credit_received, contracts),
        )
    except sqlite3.Error as e:
        logger.error("Failed to mark order %s as notified: %s", order_id, e)


def _get_open_credit(conn: sqlite3.Connection, short_symbol: str) -> Optional[float]:
    """Look up the original credit received when opening a position by its short symbol."""
    row = conn.execute(
        """SELECT credit_received FROM notified_orders
           WHERE short_symbol = ? AND order_type = 'open'
           ORDER BY notified_at DESC LIMIT 1""",
        (short_symbol,),
    ).fetchone()
    return float(row["credit_received"]) if row and row["credit_received"] is not None else None


def _daily_summary_sent_today(conn: sqlite3.Connection, today: date) -> bool:
    row = conn.execute(
        "SELECT 1 FROM daily_summary_log WHERE summary_date = ?",
        (today.isoformat(),),
    ).fetchone()
    return row is not None


def _mark_daily_summary_sent(conn: sqlite3.Connection, today: date) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO daily_summary_log (summary_date) VALUES (?)",
        (today.isoformat(),),
    )


# ---------------------------------------------------------------------------
# OCC symbol parsing
# ---------------------------------------------------------------------------

# Matches both "O:SPY250404P00520000" and "SPY250404P00520000"
_OCC_RE = re.compile(r"^(?:O:)?([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([PC])(\d{8})$")


def parse_occ(symbol: str) -> Optional[Dict]:
    """Parse an OCC option symbol into its components.

    Args:
        symbol: OCC symbol like "O:SPY250404P00520000" or "SPY250404P00520000".
                Case-insensitive — lowercased symbols from alpaca-py are accepted.

    Returns:
        Dict with keys {ticker, expiration (date), option_type, strike (float)}
        or None if the symbol doesn't match the OCC format.
    """
    if not symbol:
        return None
    m = _OCC_RE.match(symbol.strip().upper())
    if not m:
        return None
    ticker, yy, mm, dd, opt_type, strike_str = m.groups()
    try:
        exp = date(2000 + int(yy), int(mm), int(dd))
        strike = int(strike_str) / 1000.0
    except (ValueError, OverflowError):
        return None
    return {
        "ticker": ticker,
        "expiration": exp,
        "option_type": opt_type,  # "P" or "C"
        "strike": strike,
    }


def _exp_str(d: date) -> str:
    """Format expiration date as 'Apr 4' for human-readable messages."""
    return d.strftime("%-m/%-d") if os.name != "nt" else d.strftime("%m/%d").lstrip("0")


# ---------------------------------------------------------------------------
# Order classification helpers
# ---------------------------------------------------------------------------

def _str(val) -> str:
    """Coerce an alpaca enum or string to lowercase string."""
    return str(val).lower() if val is not None else ""


def classify_mleg_order(order) -> str:
    """Return 'open' or 'close' for a multi-leg order based on position_intent of legs.

    Returns 'unknown' if legs are missing or unparseable.
    """
    legs = getattr(order, "legs", None) or []
    for leg in legs:
        intent = _str(getattr(leg, "position_intent", None))
        if "open" in intent:
            return "open"
        if "close" in intent:
            return "close"
    # Fall back to client_order_id prefix
    cid = str(getattr(order, "client_order_id", "") or "")
    if cid.startswith("close-"):
        return "close"
    if cid.startswith("cs-") or cid.startswith("sl-"):
        return "open"
    return "unknown"


def _short_leg(order, action: str = "open"):
    """Return the leg that is the short strike of the spread.

    For open orders: the leg with SELL_TO_OPEN is the short leg.
    For close orders: the leg with BUY_TO_CLOSE is the (was-short) leg.
    """
    legs = getattr(order, "legs", None) or []
    target_intent = "sell_to_open" if action == "open" else "buy_to_close"
    for leg in legs:
        intent = _str(getattr(leg, "position_intent", None))
        if intent == target_intent:
            return leg
    return legs[0] if legs else None


def _long_leg(order, action: str = "open"):
    """Return the long-protection leg of the spread."""
    legs = getattr(order, "legs", None) or []
    target_intent = "buy_to_open" if action == "open" else "sell_to_close"
    for leg in legs:
        intent = _str(getattr(leg, "position_intent", None))
        if intent == target_intent:
            return leg
    return legs[1] if len(legs) > 1 else None


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _contracts(order) -> int:
    try:
        return int(float(getattr(order, "filled_qty", None) or getattr(order, "qty", 1) or 1))
    except (TypeError, ValueError):
        return 1


# ---------------------------------------------------------------------------
# Message formatters
# ---------------------------------------------------------------------------

def format_open_message(order) -> Tuple[str, Optional[str], Optional[float]]:
    """Build a 📥 ORDER FILLED message for an opening credit spread.

    Returns:
        (message, short_symbol, credit_received)
    """
    short = _short_leg(order, "open")
    long_ = _long_leg(order, "open")

    short_sym = _str(getattr(short, "symbol", None)) if short else ""
    long_sym = _str(getattr(long_, "symbol", None)) if long_ else ""

    short_info = parse_occ(short_sym)
    long_info = parse_occ(long_sym)

    credit = _safe_float(getattr(order, "filled_avg_price", None))
    qty = _contracts(order)

    if short_info and long_info:
        ticker = short_info["ticker"]
        exp = _exp_str(short_info["expiration"])
        opt_label = "put" if short_info["option_type"] == "P" else "call"
        msg = (
            f"📥 <b>ORDER FILLED</b>\n"
            f"Opened: Sold {ticker} ${short_info['strike']:.0f}/${long_info['strike']:.0f} "
            f"{opt_label} spread\n"
            f"Credit: <b>${credit:.2f}</b> | Contracts: {qty} | Expires: {exp}"
        )
    else:
        # Fallback: no OCC parse available
        ticker = None
        msg = (
            f"📥 <b>ORDER FILLED</b>\n"
            f"Spread opened | Credit: ${credit:.2f} | Contracts: {qty}"
        )

    return msg, short_sym or None, credit if credit else None


def format_close_message(
    order,
    open_credit: Optional[float] = None,
) -> str:
    """Build a 📤 POSITION CLOSED or ⚠️ STOP TRIGGERED message.

    Args:
        order:        Alpaca Order object for the closing MLEG order.
        open_credit:  Credit originally received when opening (for P&L calc).
    """
    short = _short_leg(order, "close")
    long_ = _long_leg(order, "close")

    short_sym = _str(getattr(short, "symbol", None)) if short else ""
    long_sym = _str(getattr(long_, "symbol", None)) if long_ else ""

    short_info = parse_occ(short_sym)
    long_info = parse_occ(long_sym)

    close_debit = _safe_float(getattr(order, "filled_avg_price", None))
    qty = _contracts(order)

    pnl_line = ""
    emoji = "📤"
    label = "POSITION CLOSED"

    if open_credit is not None:
        pnl_per_spread = (open_credit - close_debit) * 100  # per contract, in dollars
        pnl_total = pnl_per_spread * qty
        pct = (pnl_per_spread / (open_credit * 100) * 100) if open_credit else 0.0
        if pnl_total < 0:
            emoji = "⚠️"
            label = "STOP TRIGGERED"
        pnl_line = f"\nP&L: <b>${pnl_total:+.0f}</b> ({pct:+.0f}% of max)"
    else:
        pnl_line = f"\nClose cost: ${close_debit:.2f}/spread"

    if short_info and long_info:
        ticker = short_info["ticker"]
        exp = _exp_str(short_info["expiration"])
        opt_label = "put" if short_info["option_type"] == "P" else "call"
        return (
            f"{emoji} <b>{label}</b>\n"
            f"Closed: {ticker} ${short_info['strike']:.0f}/${long_info['strike']:.0f} "
            f"{opt_label} spread | Expires: {exp}"
            f"{pnl_line}"
        )
    else:
        return (
            f"{emoji} <b>{label}</b>\n"
            f"Spread closed | Debit paid: ${close_debit:.2f}"
            f"{pnl_line}"
        )


def format_rejected_message(order) -> str:
    """Build an ❌ ORDER REJECTED message."""
    symbol = str(getattr(order, "symbol", "") or "")
    reason = str(getattr(order, "failed_at", "") or "")
    cid = str(getattr(order, "client_order_id", "") or "")
    parsed = parse_occ(symbol)

    desc = f"{parsed['ticker']} option order" if parsed else (symbol or cid or "Option order")
    return (
        f"❌ <b>ORDER REJECTED</b>\n"
        f"{desc}\n"
        f"Reason: insufficient buying power or rejected by broker\n"
        f"Client ID: {cid}"
    )


# ---------------------------------------------------------------------------
# Order poller
# ---------------------------------------------------------------------------

def poll_new_orders(
    client,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> List[str]:
    """Fetch recent orders from Alpaca, send Telegram for unseen ones.

    Args:
        client:  TradingClient instance.
        conn:    Open DB connection (dedup tracking).
        dry_run: Log messages but don't actually send to Telegram.

    Returns:
        List of order_ids that were newly notified.
    """
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    after = datetime.now(timezone.utc) - timedelta(hours=ORDER_LOOKBACK_HOURS)
    try:
        orders = client.get_orders(
            GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                after=after,
                limit=100,
                nested=True,  # include legs in MLEG orders
            )
        )
    except Exception as e:
        logger.error("Failed to fetch orders from Alpaca: %s", e)
        return []

    notified: List[str] = []
    for order in orders:
        order_id = str(order.id)
        status = _str(getattr(order, "status", ""))
        order_class = _str(getattr(order, "order_class", ""))

        if _is_notified(conn, order_id):
            continue

        msg: Optional[str] = None
        order_type = "unknown"
        short_symbol: Optional[str] = None
        credit_received: Optional[float] = None
        qty = _contracts(order)

        if status == "filled" and order_class == "mleg":
            direction = classify_mleg_order(order)
            if direction == "open":
                msg, short_symbol, credit_received = format_open_message(order)
                order_type = "open"
            elif direction == "close":
                # Look up what we received when opening this position
                close_short = _short_leg(order, "close")
                close_short_sym = _str(getattr(close_short, "symbol", None)) if close_short else ""
                open_credit = _get_open_credit(conn, close_short_sym) if close_short_sym else None
                msg = format_close_message(order, open_credit=open_credit)
                order_type = "close"
            else:
                logger.debug("MLEG order %s direction unknown, skipping", order_id)
                continue

        elif status == "filled" and order_class != "mleg":
            # Single-leg option fill — use generic notify
            symbol = str(getattr(order, "symbol", "") or "")
            parsed = parse_occ(symbol)
            side = _str(getattr(order, "side", ""))
            intent = _str(getattr(order, "position_intent", ""))
            price = _safe_float(getattr(order, "filled_avg_price", None))

            if "open" in intent and "sell" in side:
                # Selling to open a single leg
                ticker = parsed["ticker"] if parsed else symbol
                opt_label = ("put" if parsed["option_type"] == "P" else "call") if parsed else "option"
                exp = _exp_str(parsed["expiration"]) if parsed else "?"
                strike = parsed["strike"] if parsed else 0.0
                msg = (
                    f"📥 <b>ORDER FILLED</b>\n"
                    f"Sold {ticker} ${strike:.0f} {opt_label} | "
                    f"Premium: ${price:.2f} | x{qty} | Expires: {exp}"
                )
                order_type = "open"
                short_symbol = symbol
                credit_received = price
            elif "close" in intent or "buy" in side:
                open_credit = _get_open_credit(conn, symbol) if symbol else None
                ticker = parsed["ticker"] if parsed else symbol
                opt_label = ("put" if parsed["option_type"] == "P" else "call") if parsed else "option"
                pnl_line = ""
                emoji = "📤"
                label = "POSITION CLOSED"
                if open_credit is not None:
                    pnl = (open_credit - price) * 100 * qty
                    if pnl < 0:
                        emoji = "⚠️"
                        label = "STOP TRIGGERED"
                    pnl_line = f"\nP&L: <b>${pnl:+.0f}</b>"
                msg = (
                    f"{emoji} <b>{label}</b>\n"
                    f"{ticker} ${parsed['strike']:.0f if parsed else '?'} {opt_label} | "
                    f"Closed at ${price:.2f}"
                    f"{pnl_line}"
                )
                order_type = "close"
            else:
                # BTO or other — track but don't spam
                order_type = "other_fill"
                msg = None

        elif status == "rejected":
            msg = format_rejected_message(order)
            order_type = "rejected"

        elif status == "canceled":
            # Only notify cancellations that were partially filled
            filled_qty = _safe_float(getattr(order, "filled_qty", 0))
            if filled_qty > 0:
                symbol = str(getattr(order, "symbol", "") or "")
                msg = (
                    f"⚠️ <b>ORDER CANCELLED</b> (partial fill: {filled_qty:.0f} contracts)\n"
                    f"Symbol: {symbol or 'multi-leg'}"
                )
                order_type = "cancelled_partial"
            else:
                # Silent cancel — just record for dedup
                order_type = "cancelled"

        # Always mark as notified to prevent re-processing
        _mark_notified(
            conn, order_id, order_type,
            ticker=None, short_symbol=short_symbol,
            credit_received=credit_received, contracts=qty,
        )

        if msg:
            logger.info("Sending notification for order %s (%s): %s", order_id, order_type, msg[:80])
            send_telegram(msg, dry_run=dry_run)
            notified.append(order_id)

    return notified


# ---------------------------------------------------------------------------
# Daily portfolio summary
# ---------------------------------------------------------------------------

def build_daily_summary(account, positions: List) -> str:
    """Build the daily portfolio summary message.

    Args:
        account:   Alpaca TradeAccount object.
        positions: List of Alpaca Position objects.

    Returns:
        Formatted HTML string for Telegram.
    """
    today = date.today()
    equity = _safe_float(getattr(account, "equity", None))
    last_equity = _safe_float(getattr(account, "last_equity", None))
    buying_power = _safe_float(getattr(account, "buying_power", None))
    options_bp = _safe_float(getattr(account, "options_buying_power", None))

    day_pnl = equity - last_equity

    lines = [
        f"📊 <b>Daily Portfolio Summary — {today.isoformat()}</b>",
        "",
        f"<b>Account</b>",
        f"  Equity:        ${equity:>10,.2f}",
        f"  Day P&L:       ${day_pnl:>+10,.2f}",
        f"  Buying Power:  ${buying_power:>10,.2f}",
        f"  Options BP:    ${options_bp:>10,.2f}",
    ]

    option_positions = []
    expiring_soon = []

    for pos in positions:
        symbol = str(getattr(pos, "symbol", "") or "")
        parsed = parse_occ(symbol)
        if not parsed:
            continue  # skip non-option positions
        option_positions.append((pos, parsed))
        days_left = (parsed["expiration"] - today).days
        if days_left <= EXPIRY_WARNING_DAYS:
            expiring_soon.append((pos, parsed, days_left))

    lines += ["", f"<b>Open Option Positions ({len(option_positions)})</b>"]

    if not option_positions:
        lines.append("  (none)")
    else:
        for pos, parsed in option_positions:
            qty = _safe_float(getattr(pos, "qty", 0))
            unreal_pl = _safe_float(getattr(pos, "unrealized_pl", None))
            unreal_plpc = _safe_float(getattr(pos, "unrealized_plpc", None))
            curr_price = _safe_float(getattr(pos, "current_price", None))
            opt_label = "P" if parsed["option_type"] == "P" else "C"
            exp = _exp_str(parsed["expiration"])
            lines.append(
                f"  {parsed['ticker']} ${parsed['strike']:.0f}{opt_label} {exp} "
                f"x{qty:.0f} | UPL: ${unreal_pl:+.0f} ({unreal_plpc*100:+.1f}%) "
                f"@ ${curr_price:.2f}"
            )

    if expiring_soon:
        lines += ["", "⚠️ <b>Expiring within 3 days:</b>"]
        for pos, parsed, days_left in expiring_soon:
            opt_label = "put" if parsed["option_type"] == "P" else "call"
            unreal_pl = _safe_float(getattr(pos, "unrealized_pl", None))
            lines.append(
                f"  🕐 {parsed['ticker']} ${parsed['strike']:.0f} {opt_label} — "
                f"{days_left}d left | UPL: ${unreal_pl:+.0f}"
            )

    return "\n".join(lines)


def send_daily_summary(client, conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    """Fetch account/position data and send the daily portfolio summary.

    Returns True if summary was sent (or dry_run), False if already sent today
    or if Alpaca fetch failed.
    """
    today = date.today()
    if _daily_summary_sent_today(conn, today):
        logger.info("Daily summary already sent for %s", today)
        return False

    try:
        account = client.get_account()
        positions = client.get_all_positions()
    except Exception as e:
        logger.error("Failed to fetch account/positions from Alpaca: %s", e)
        return False

    msg = build_daily_summary(account, positions)
    logger.info("Sending daily summary for %s", today)
    send_telegram(msg, dry_run=dry_run)

    if not dry_run:
        _mark_daily_summary_sent(conn, today)

    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _build_client():
    """Build and return an Alpaca TradingClient from environment credentials."""
    from alpaca.trading.client import TradingClient

    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        raise RuntimeError(
            "Alpaca credentials not set. "
            "Set ALPACA_API_KEY and ALPACA_API_SECRET in .env or environment."
        )
    kwargs = {"paper": ALPACA_PAPER}
    if ALPACA_BASE_URL:
        kwargs["url_override"] = ALPACA_BASE_URL
    return TradingClient(ALPACA_API_KEY, ALPACA_API_SECRET, **kwargs)


def run(
    daily_summary: bool = False,
    dry_run: bool = False,
    db_path: Optional[Path] = None,
) -> Dict:
    """Main entry point for the notification runner.

    Args:
        daily_summary: If True, send the daily portfolio summary (in addition to order poll).
        dry_run:       Log messages but skip Telegram and DB writes.
        db_path:       Override DB path (for testing).

    Returns:
        Summary dict with counts.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    client = _build_client()
    result: Dict = {"orders_notified": 0, "daily_summary_sent": False}

    with _db(db_path) as conn:
        notified = poll_new_orders(client, conn, dry_run=dry_run)
        result["orders_notified"] = len(notified)

        if daily_summary:
            sent = send_daily_summary(client, conn, dry_run=dry_run)
            result["daily_summary_sent"] = sent

    logger.info("Trade notification run complete: %s", result)
    return result


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Poll Alpaca for trade activity and send Telegram notifications."
    )
    parser.add_argument(
        "--daily-summary",
        action="store_true",
        help="Send the daily portfolio summary (in addition to order poll).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log messages without sending to Telegram or writing to DB.",
    )
    args = parser.parse_args()
    run(daily_summary=args.daily_summary, dry_run=args.dry_run)


if __name__ == "__main__":
    _main()
