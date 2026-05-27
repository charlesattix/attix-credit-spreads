"""
env_helpers.py — Fail-loud environment variable helpers for the Attix Dashboard.

Background (the outage that motivated this module)
---------------------------------------------------
``os.environ.get("VAR", default)`` only applies ``default`` when ``VAR`` is
*absent*. When ``VAR`` is present but set to an **empty string** (a common
result of a mis-templated Railway variable, a cleared secret, or a trailing
``VAR=`` line), ``.get`` returns ``""`` — NOT the default. Code that expected
the default then silently runs with a blank secret/password/key, which is
exactly how a live deploy can come up "healthy" while being broken.

``getenv_or_default`` closes that gap: an empty (or whitespace-only) value is
treated as missing, the default is applied, and a warning is logged so the
fallback is never silent.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def getenv_or_default(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return ``os.environ[name]``, treating an empty/blank value as missing.

    Unlike :func:`os.environ.get`, this applies ``default`` both when the
    variable is unset *and* when it is set to an empty or whitespace-only
    string. Whenever the default is substituted for a blank-but-present
    variable, a warning is logged (the empty-string footgun is otherwise
    invisible). A genuinely-absent variable is logged at debug level only.

    Args:
        name:    Environment variable name.
        default: Value to fall back to when the variable is missing or blank.

    Returns:
        The variable's value (stripped of surrounding whitespace only for the
        blank-check; the raw value is returned when non-blank), or ``default``.
    """
    raw = os.environ.get(name)
    if raw is None:
        logger.debug("env var %s not set; using default", name)
        return default
    if raw.strip() == "":
        logger.warning(
            "env var %s is set but EMPTY; treating as missing and using default", name
        )
        return default
    return raw


def is_blank(name: str) -> bool:
    """True if env var ``name`` is unset or set to an empty/whitespace string."""
    raw = os.environ.get(name)
    return raw is None or raw.strip() == ""
