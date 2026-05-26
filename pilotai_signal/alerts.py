"""
Alert generator — detects signal changes and sends Telegram notifications.

Alert types:
  NEW       — ticker newly enters signal (or returns after >5-day absence)
  STRONG    — conviction >= threshold for N consecutive days
  EXIT      — ticker drops from all portfolios
  MOVER_UP  — conviction rises >= delta threshold in one day
  MOVER_DOWN — conviction falls >= delta threshold in one day

Also sends a daily digest of top-N tickers.

Filtering (noise suppression):
  - STRONG and MOVER_DOWN are saved to the DB but NOT sent to Telegram.
  - NEW alerts with conviction < ALERT_MIN_CONVICTION_NEW are suppressed.
  - MOVER_UP requires conviction_after >= ALERT_MIN_CONVICTION_MOVER and
    a 7-day per-ticker cooldown (at most one MOVER_UP per ticker per week).
  - Suppressed counts are appended to the daily digest so Carlos sees the
    volume without getting spammed.
"""

import logging
from collections import Counter
from datetime import date
from typing import Dict, List, Optional, Tuple

import requests

from . import config, db

logger = logging.getLogger(__name__)


def _to_date(v) -> date:
    """Normalize SQLite date values (may be str or date object)."""
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


# ── Telegram delivery ─────────────────────────────────────────────────────────

def send_telegram(message: str, dry_run: bool = False) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning("Telegram credentials not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return False

    if dry_run:
        logger.info("[DRY RUN] Telegram message:\n%s", message)
        return True

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error("Telegram send failed: %s", e)
        return False


# ── Alert classification ──────────────────────────────────────────────────────

def _portfolios_for_ticker(conn, ticker: str, snap_date: date) -> List[str]:
    """Return strategy names that hold this ticker today."""
    rows = conn.execute(
        """SELECT DISTINCT ss.strategy_name
           FROM snapshot_holdings sh
           JOIN strategy_snapshots ss ON ss.id = sh.snapshot_id
           WHERE sh.ticker = ? AND ss.snapshot_date = ?
           ORDER BY ss.strategy_name""",
        (ticker, snap_date.isoformat()),
    ).fetchall()
    return [r["strategy_name"] for r in rows]


def classify_alerts(
    today: date,
    today_signals: List[dict],
    yesterday_signals: Dict[str, dict],
    conn,
) -> List[dict]:
    """
    Compare today vs yesterday signals and emit alert dicts.
    """
    alerts = []
    today_by_ticker = {s["ticker"]: s for s in today_signals}

    # ── NEW and MOVER_UP / MOVER_DOWN ─────────────────────────────────────────
    for ticker, sig in today_by_ticker.items():
        prev = yesterday_signals.get(ticker)
        conv_now = sig["conviction"]
        conv_prev = prev["conviction"] if prev else None

        if prev is None:
            # Check if it was absent long enough to re-fire NEW
            history = db.get_ticker_history(conn, ticker, days=config.ALERT_NEW_ABSENCE_DAYS + 2)
            # history is DESC, skip today
            recent_dates = [
                _to_date(r["signal_date"])
                for r in history
                if _to_date(r["signal_date"]) < today
            ]
            is_new = len(recent_dates) == 0 or (
                recent_dates and (today - recent_dates[0]).days > config.ALERT_NEW_ABSENCE_DAYS
            )
            if is_new:
                portfolios = _portfolios_for_ticker(conn, ticker, today)
                short_list = portfolios[:3]
                more = len(portfolios) - 3
                port_str = ", ".join(short_list) + (f" +{more} more" if more > 0 else "")
                alerts.append({
                    "alert_date": today.isoformat(),
                    "alert_type": "NEW",
                    "ticker": ticker,
                    "conviction_before": None,
                    "conviction_after": round(conv_now, 4),
                    "days_in_signal": sig["days_in_signal"],
                    "message": (
                        f"🆕 <b>NEW SIGNAL — ${ticker}</b>\n"
                        f"Conviction: <b>{conv_now:.2f}</b> | "
                        f"Freq: {sig['frequency']}/{sig['total_portfolios']} "
                        f"({sig['freq_pct']*100:.1f}%)\n"
                        f"Portfolios: {port_str}\n"
                        f"Avg weight: {sig['avg_weight']*100:.1f}% | "
                        f"WQ score: {sig['weighted_qscore']:.3f}"
                    ),
                })
        else:
            delta = conv_now - conv_prev
            if delta >= config.ALERT_MOVER_DELTA:
                alerts.append({
                    "alert_date": today.isoformat(),
                    "alert_type": "MOVER_UP",
                    "ticker": ticker,
                    "conviction_before": round(conv_prev, 4),
                    "conviction_after": round(conv_now, 4),
                    "days_in_signal": sig["days_in_signal"],
                    "message": (
                        f"📈 <b>MOVER UP — ${ticker}</b>\n"
                        f"Conviction: {conv_prev:.2f} → <b>{conv_now:.2f}</b> "
                        f"(+{delta:.2f})\n"
                        f"Freq: {sig['frequency']}/{sig['total_portfolios']} | "
                        f"Day {sig['days_in_signal'] + 1}"
                    ),
                })
            elif delta <= -config.ALERT_MOVER_DELTA:
                alerts.append({
                    "alert_date": today.isoformat(),
                    "alert_type": "MOVER_DOWN",
                    "ticker": ticker,
                    "conviction_before": round(conv_prev, 4),
                    "conviction_after": round(conv_now, 4),
                    "days_in_signal": sig["days_in_signal"],
                    "message": (
                        f"📉 <b>MOVER DOWN — ${ticker}</b>\n"
                        f"Conviction: {conv_prev:.2f} → <b>{conv_now:.2f}</b> "
                        f"({delta:.2f})\n"
                        f"Freq: {sig['frequency']}/{sig['total_portfolios']} | "
                        f"Day {sig['days_in_signal'] + 1}"
                    ),
                })

    # ── STRONG conviction ──────────────────────────────────────────────────────
    for ticker, sig in today_by_ticker.items():
        if sig["conviction"] >= config.ALERT_STRONG_CONVICTION_MIN:
            # Check consecutive days above threshold
            history = db.get_ticker_history(conn, ticker, days=config.ALERT_STRONG_CONSECUTIVE_DAYS + 2)
            strong_days = sum(
                1 for r in history
                if _to_date(r["signal_date"]) < today
                and r["conviction"] >= config.ALERT_STRONG_CONVICTION_MIN
            )
            if strong_days >= config.ALERT_STRONG_CONSECUTIVE_DAYS - 1:
                prev = yesterday_signals.get(ticker)
                # Only fire once per ticker per day, skip if we already have MOVER for it
                alerts.append({
                    "alert_date": today.isoformat(),
                    "alert_type": "STRONG",
                    "ticker": ticker,
                    "conviction_before": round(prev["conviction"], 4) if prev else None,
                    "conviction_after": round(sig["conviction"], 4),
                    "days_in_signal": sig["days_in_signal"],
                    "message": (
                        f"🔥 <b>STRONG — ${ticker}</b> (Day {sig['days_in_signal'] + 1})\n"
                        f"Conviction: <b>{sig['conviction']:.2f}</b> "
                        f"{'▲' if prev and sig['conviction'] > prev['conviction'] else '▼' if prev else ''}"
                        f" (was {prev['conviction']:.2f})\n" if prev else "\n"
                        f"Freq: {sig['frequency']}/{sig['total_portfolios']} | "
                        f"Avg Weight: {sig['avg_weight']*100:.1f}%\n"
                        f"Strong for {strong_days + 1}+ consecutive days"
                    ),
                })

    # ── EXIT — ticker was in yesterday's signal but gone today ────────────────
    for ticker, prev_sig in yesterday_signals.items():
        if ticker not in today_by_ticker:
            alerts.append({
                "alert_date": today.isoformat(),
                "alert_type": "EXIT",
                "ticker": ticker,
                "conviction_before": round(prev_sig["conviction"], 4),
                "conviction_after": 0.0,
                "days_in_signal": prev_sig["days_in_signal"],
                "message": (
                    f"🚪 <b>EXIT — ${ticker}</b>\n"
                    f"Dropped from ALL portfolios today\n"
                    f"Was: Conviction {prev_sig['conviction']:.2f} | "
                    f"Freq: {prev_sig['frequency']}/{prev_sig['total_portfolios']}\n"
                    f"Held for: {prev_sig['days_in_signal']} days"
                ),
            })

    return alerts


# ── Alert filtering (noise suppression) ───────────────────────────────────────

def filter_alerts(
    alerts: List[dict],
    mover_cooldown: Dict[str, date],
    today: date,
) -> Tuple[List[dict], Dict[str, int]]:
    """Decide which classified alerts should be sent to Telegram.

    Rules:
      - STRONG / MOVER_DOWN → always suppressed (informational, not actionable)
      - NEW with conviction_after < ALERT_MIN_CONVICTION_NEW → suppressed
      - MOVER_UP with delta < ALERT_MIN_DELTA_MOVER → suppressed
      - MOVER_UP with conviction_after < ALERT_MIN_CONVICTION_MOVER → suppressed
      - MOVER_UP within cooldown window → suppressed

    All classified alerts are still written to the DB by the caller; this
    function only controls what reaches Telegram.

    Args:
        alerts:          Full list from classify_alerts().
        mover_cooldown:  Dict mapping ticker → last MOVER_UP sent date.
        today:           The alert run date (for cooldown comparison).

    Returns:
        (sendable, suppressed_counts) where sendable is the filtered list
        and suppressed_counts maps label → count for the digest footer.
    """
    sendable: List[dict] = []
    suppressed: Counter = Counter()

    for alert in alerts:
        typ = alert["alert_type"]

        # Hard-suppressed types
        if typ in config.ALERT_SUPPRESS_TYPES:
            suppressed[typ] += 1
            continue

        # NEW — conviction gate
        if typ == "NEW":
            if alert["conviction_after"] < config.ALERT_MIN_CONVICTION_NEW:
                suppressed["low-conviction NEW"] += 1
                continue

        # MOVER_UP — delta + conviction + cooldown
        if typ == "MOVER_UP":
            conv_before = alert.get("conviction_before") or 0.0
            delta = alert["conviction_after"] - conv_before
            if delta < config.ALERT_MIN_DELTA_MOVER:
                suppressed["MOVER_UP (low delta)"] += 1
                continue
            if alert["conviction_after"] < config.ALERT_MIN_CONVICTION_MOVER:
                suppressed["MOVER_UP (low conviction)"] += 1
                continue
            ticker = alert["ticker"]
            last_fired = mover_cooldown.get(ticker)
            if last_fired is not None and (today - last_fired).days < config.ALERT_MOVER_COOLDOWN_DAYS:
                suppressed["MOVER_UP (cooldown)"] += 1
                continue

        sendable.append(alert)

    return sendable, suppressed


# ── MOVER_UP cooldown persistence ─────────────────────────────────────────────

def _load_mover_cooldown() -> Dict[str, date]:
    """Load per-ticker last-fired dates from JSON. Returns {} on any error."""
    from shared.io_utils import safe_json_read
    raw = safe_json_read(config.MOVER_COOLDOWN_FILE, default={})
    try:
        return {ticker: date.fromisoformat(d) for ticker, d in raw.items()}
    except (ValueError, AttributeError):
        return {}


def _save_mover_cooldown(cooldown: Dict[str, date]) -> None:
    """Persist per-ticker last-fired dates to JSON."""
    from shared.io_utils import atomic_json_write
    atomic_json_write(config.MOVER_COOLDOWN_FILE, {ticker: d.isoformat() for ticker, d in cooldown.items()})


# ── Daily digest ───────────────────────────────────────────────────────────────

def build_digest(
    today: date,
    signals: List[dict],
    suppressed: Optional[Dict[str, int]] = None,
) -> str:
    """Build the daily digest message.

    Args:
        today:      Digest date.
        signals:    Today's full signal list.
        suppressed: Suppressed alert counts from filter_alerts(), or None.
    """
    equity = [s for s in signals if s["ticker"] not in config.GOLD_TICKERS]
    gold = [s for s in signals if s["ticker"] in config.GOLD_TICKERS]

    lines = [
        f"📊 <b>Attix Signal Digest — {today.isoformat()}</b>",
        "",
        f"<b>EQUITY SIGNAL (Top {config.ALERT_DIGEST_TOP_N}):</b>",
    ]
    for i, s in enumerate(equity[:config.ALERT_DIGEST_TOP_N], 1):
        flag = "🔥" if s["conviction"] >= config.ALERT_STRONG_CONVICTION_MIN else " "
        lines.append(
            f"{flag}{i:>2}. <b>${s['ticker']:<6}</b> "
            f"Conv: {s['conviction']:.2f} | "
            f"F: {s['frequency']}/{s['total_portfolios']} | "
            f"Day: {s['days_in_signal']}"
        )

    if gold:
        lines += ["", "<b>GOLD HEDGE SIGNAL:</b>"]
        for i, s in enumerate(gold[:5], 1):
            lines.append(
                f"  {i}. <b>${s['ticker']:<6}</b> "
                f"Conv: {s['conviction']:.2f} | "
                f"F: {s['frequency']}/{s['total_portfolios']}"
            )

    n_total = len(signals)
    lines += [
        "",
        f"Universe: {n_total} tickers tracked | {today.isoformat()}",
    ]

    # Suppressed summary footer
    if suppressed:
        parts = [f"{count} {label}" for label, count in suppressed.items()]
        lines += ["", f"🔇 Suppressed: {', '.join(parts)}"]

    return "\n".join(lines)


# ── Main alert run ─────────────────────────────────────────────────────────────

def run_alerts(
    alert_date: Optional[date] = None,
    send_digest: bool = True,
    dry_run: bool = False,
) -> Dict:
    """
    Generate and send all alerts for alert_date.
    Returns summary dict.
    """
    today = alert_date or date.today()

    # Load MOVER_UP cooldown state before entering the DB transaction
    mover_cooldown = _load_mover_cooldown()

    with db.transaction() as conn:
        today_signals = db.get_signals_for_date(conn, today)
        if not today_signals:
            logger.warning("No signals for %s — run scorer first", today)
            return {"status": "no_signals", "date": today.isoformat()}

        today_signals = [dict(s) for s in today_signals]

        # Get previous signal date
        prev_date = db.get_previous_signal_date(conn, today)
        yesterday_signals: Dict[str, dict] = {}
        if prev_date:
            prev_rows = db.get_signals_for_date(conn, prev_date)
            yesterday_signals = {r["ticker"]: dict(r) for r in prev_rows}

        # Classify all alerts (unchanged — ALL types generated)
        alerts = classify_alerts(today, today_signals, yesterday_signals, conn)
        logger.info("Generated %d alerts for %s", len(alerts), today)

        # Determine which alerts are sendable (Telegram filter)
        sendable, suppressed = filter_alerts(alerts, mover_cooldown, today)
        sendable_keys = {(a["alert_type"], a["ticker"]) for a in sendable}
        logger.info(
            "Filter: %d sendable, %d suppressed (%s)",
            len(sendable),
            len(alerts) - len(sendable),
            suppressed,
        )

        # Save ALL alerts to DB (for audit trail), send only sendable ones
        sent_count = 0
        for alert in alerts:
            is_new = db.insert_alert(conn, alert)
            should_send = is_new and (alert["alert_type"], alert["ticker"]) in sendable_keys
            if should_send and not dry_run:
                ok = send_telegram(alert["message"], dry_run=dry_run)
                if ok:
                    row = conn.execute(
                        "SELECT id FROM alerts WHERE alert_date=? AND alert_type=? AND ticker=?",
                        (alert["alert_date"], alert["alert_type"], alert["ticker"]),
                    ).fetchone()
                    if row:
                        db.mark_alert_sent(conn, row["id"])
                    sent_count += 1
                    # Record MOVER_UP fires for cooldown tracking
                    if alert["alert_type"] == "MOVER_UP":
                        mover_cooldown[alert["ticker"]] = today

        # Daily digest (always includes suppressed summary)
        if send_digest:
            digest_msg = build_digest(today, today_signals, suppressed)
            send_telegram(digest_msg, dry_run=dry_run)

    # Persist updated cooldown outside the DB transaction
    if not dry_run:
        _save_mover_cooldown(mover_cooldown)

    result = {
        "status": "ok",
        "date": today.isoformat(),
        "alerts_generated": len(alerts),
        "alerts_sent": sent_count,
        "alerts_suppressed": len(alerts) - len(sendable),
        "suppressed_breakdown": suppressed,
        "digest_sent": send_digest,
    }
    logger.info("Alert run complete: %s", result)
    return result
