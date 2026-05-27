"""
auth.py — Session token signing and verification for the Attix Dashboard.

Token format:  <timestamp_hex>.<hmac_sha256_hex>
  - timestamp_hex  : Unix epoch seconds (hex) when the session was created
  - hmac_sha256    : HMAC-SHA256(SECRET_KEY, timestamp_hex)

Verification checks both the HMAC signature (constant-time) and the
24-hour expiry window.  No external dependencies — stdlib only.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from .env_helpers import getenv_or_default

SESSION_COOKIE    = "attix_session"
SESSION_TTL_SECS  = 86_400          # 24 hours

# Read once at import time; must restart to pick up changes.
# getenv_or_default treats an empty-string env var as missing (the empty-string
# footgun) and warns when falling back to these insecure dev defaults.
_SECRET_KEY      = getenv_or_default("SECRET_KEY", "dev-secret-change-me-in-prod").encode()
_DASHBOARD_PASS  = getenv_or_default("DASHBOARD_PASSWORD", "attix-dev-2026!")


def _sign(timestamp_hex: str) -> str:
    return hmac.new(_SECRET_KEY, timestamp_hex.encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    """Create a fresh signed session token valid for SESSION_TTL_SECS."""
    ts = format(int(time.time()), "x")
    return f"{ts}.{_sign(ts)}"


def verify_token(token: str) -> bool:
    """Return True iff the token has a valid signature and is not expired."""
    try:
        ts_hex, sig = token.split(".", 1)
    except ValueError:
        return False
    # Constant-time signature comparison
    if not hmac.compare_digest(_sign(ts_hex), sig):
        return False
    # Expiry
    try:
        age = time.time() - int(ts_hex, 16)
    except ValueError:
        return False
    return 0 <= age < SESSION_TTL_SECS


def check_password(candidate: str) -> bool:
    """Constant-time password comparison."""
    return hmac.compare_digest(_DASHBOARD_PASS, candidate)
