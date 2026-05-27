"""
test_env_helpers.py — Fail-loud env handling (FIX #4).

Covers the empty-string footgun that caused a silent outage:
``os.environ.get(VAR, default)`` does NOT apply the default when VAR="".
"""

import os

import pytest

from web_dashboard.env_helpers import getenv_or_default, is_blank

_VAR = "ATTIX_TEST_ENV_VAR_XYZ"


@pytest.fixture(autouse=True)
def _clean_var():
    os.environ.pop(_VAR, None)
    yield
    os.environ.pop(_VAR, None)


def test_unset_returns_default():
    assert getenv_or_default(_VAR, "fallback") == "fallback"
    assert getenv_or_default(_VAR) is None


def test_empty_string_returns_default():
    """The footgun: present-but-empty must fall back to the default."""
    os.environ[_VAR] = ""
    assert getenv_or_default(_VAR, "fallback") == "fallback"


def test_whitespace_only_returns_default():
    os.environ[_VAR] = "   "
    assert getenv_or_default(_VAR, "fallback") == "fallback"


def test_real_value_passes_through():
    os.environ[_VAR] = "real-value"
    assert getenv_or_default(_VAR, "fallback") == "real-value"


def test_is_blank():
    assert is_blank(_VAR) is True            # unset
    os.environ[_VAR] = ""
    assert is_blank(_VAR) is True            # empty
    os.environ[_VAR] = "\t \n"
    assert is_blank(_VAR) is True            # whitespace
    os.environ[_VAR] = "x"
    assert is_blank(_VAR) is False           # real value


def test_validate_required_env_flags_missing(monkeypatch):
    """validate_required_env returns problems for blank required vars."""
    # app import requires DASHBOARD_API_KEY at module load.
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-key")
    import web_dashboard.app as appmod

    # Ensure the security singletons (other than the set API key) are blank.
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)

    problems = appmod.validate_required_env()
    # SECRET_KEY + DASHBOARD_PASSWORD are blank → flagged.
    assert any("SECRET_KEY" in p for p in problems)
    assert any("DASHBOARD_PASSWORD" in p for p in problems)
    # DASHBOARD_API_KEY is set → not flagged.
    assert not any("DASHBOARD_API_KEY" in p for p in problems)
