"""
scheduler/signal_validator.py — Validate EXP-2830 signal payloads before order submission.

Called after generate_all_signals() returns, before writing to disk or submitting orders.
If validation fails, NO orders are submitted.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple


REQUIRED_TOP_FIELDS = {"experiment", "generated_at", "as_of", "market", "signals"}
REQUIRED_SIGNAL_FIELDS = {
    "stream", "action", "underlier", "structure", "direction",
    "target_contracts", "stream_weight"
}
VALID_ACTIONS = {"OPEN", "NO_TRADE", "SKIP", "HOLD_MIN"}
VALID_STREAMS = {
    "exp1220", "xlf_cs", "xli_cs", "gld_cal",
    "slv_cal", "cross_vol", "v5_hedge", "qqq_cs"
}


def validate_signal_payload(payload: dict) -> Tuple[bool, List[str]]:
    """
    Validate a signal payload from generate_all_signals().

    Returns (is_valid, errors).
    If is_valid is False, do NOT submit orders.
    """
    errors: List[str] = []

    # Top-level required fields
    for f in REQUIRED_TOP_FIELDS:
        if f not in payload:
            errors.append(f"Missing top-level field: {f}")

    if errors:
        return False, errors

    # Market snapshot sanity
    market = payload.get("market", {})
    closes = market.get("closes", {})
    vix = market.get("vix_value", None)

    if vix is None or (isinstance(vix, float) and math.isnan(vix)):
        errors.append("VIX value is None or NaN — cannot classify regime")

    spy_close = closes.get("SPY")
    if spy_close is None or spy_close <= 0:
        errors.append(f"SPY close missing or invalid: {spy_close}")

    for ticker in ["QQQ", "XLF", "XLI", "GLD", "SLV"]:
        v = closes.get(ticker)
        if v is None or v <= 0:
            errors.append(f"{ticker} close missing or invalid: {v}")

    # Per-signal validation
    signals = payload.get("signals", [])
    if not signals:
        errors.append("No signals in payload — expected 8")

    seen_streams = set()
    for i, sig in enumerate(signals):
        # Required fields present
        for f in REQUIRED_SIGNAL_FIELDS:
            if f not in sig:
                errors.append(f"Signal[{i}] missing field: {f}")

        # Action is valid
        action = sig.get("action", "")
        if action not in VALID_ACTIONS:
            errors.append(f"Signal[{i}] stream={sig.get('stream')} invalid action: {action!r}")

        # Stream is known
        stream = sig.get("stream", "")
        if stream not in VALID_STREAMS:
            errors.append(f"Signal[{i}] unknown stream: {stream!r}")
        else:
            seen_streams.add(stream)

        # OPEN signals must have strike and expiry
        if action == "OPEN":
            if sig.get("structure") == "put_credit_spread":
                if not sig.get("short_strike") or not sig.get("long_strike"):
                    errors.append(f"Signal[{i}] stream={stream} OPEN but missing strikes")
                if not sig.get("expiry"):
                    errors.append(f"Signal[{i}] stream={stream} OPEN but missing expiry")
            contracts = sig.get("target_contracts", 0)
            if not isinstance(contracts, int) or contracts < 1:
                errors.append(f"Signal[{i}] stream={stream} OPEN but contracts={contracts}")

        # No NaN in numeric fields
        for field in ["short_strike", "long_strike", "width", "limit_price"]:
            v = sig.get(field)
            if isinstance(v, float) and math.isnan(v):
                errors.append(f"Signal[{i}] stream={stream} field {field} is NaN")

        # Weight sanity
        weight = sig.get("stream_weight", 0)
        if not (0 < weight <= 1.0):
            errors.append(f"Signal[{i}] stream={stream} weight={weight} out of range (0,1]")

    # All 8 streams must be present
    missing_streams = VALID_STREAMS - seen_streams
    if missing_streams:
        errors.append(f"Missing signals for streams: {missing_streams}")

    is_valid = len(errors) == 0
    return is_valid, errors
