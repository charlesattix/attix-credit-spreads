"""Tests for shared.execution_window — EXP-3309 Pre-Close Execution Window."""

from __future__ import annotations

from datetime import datetime

import pytest

from shared.execution_window import (
    DEFAULT_WINDOW,
    is_in_window,
    parse_window,
    should_skip_for_window,
)


def test_parse_window_basic():
    start, end = parse_window("15:30-16:00")
    assert (start.hour, start.minute) == (15, 30)
    assert (end.hour, end.minute) == (16, 0)


def test_parse_window_with_spaces():
    start, end = parse_window(" 09:30 - 10:00 ")
    assert (start.hour, start.minute, end.hour, end.minute) == (9, 30, 10, 0)


@pytest.mark.parametrize("bad", ["", "15:30", "15:30-", "15-16", "abc-def", "15:30:00-16:00"])
def test_parse_window_invalid_raises(bad: str):
    with pytest.raises(ValueError):
        parse_window(bad)


def test_in_window_inside_returns_true():
    now = datetime(2026, 5, 21, 15, 45)  # 15:45
    assert is_in_window(now=now, window="15:30-16:00") is True


def test_in_window_before_returns_false():
    now = datetime(2026, 5, 21, 15, 29)  # 1m before window
    assert is_in_window(now=now, window="15:30-16:00") is False


def test_in_window_after_returns_false():
    now = datetime(2026, 5, 21, 16, 1)  # 1m after window
    assert is_in_window(now=now, window="15:30-16:00") is False


def test_in_window_exact_start_returns_true():
    now = datetime(2026, 5, 21, 15, 30)
    assert is_in_window(now=now, window="15:30-16:00") is True


def test_in_window_exact_end_returns_false_half_open():
    # End boundary is exclusive (matches market-close semantics).
    now = datetime(2026, 5, 21, 16, 0)
    assert is_in_window(now=now, window="15:30-16:00") is False


def test_should_skip_outside_window():
    now = datetime(2026, 5, 21, 10, 0)
    skip, reason = should_skip_for_window(now=now, window="15:30-16:00")
    assert skip is True
    assert "15:30-16:00" in reason


def test_should_skip_inside_window():
    now = datetime(2026, 5, 21, 15, 45)
    skip, reason = should_skip_for_window(now=now, window="15:30-16:00")
    assert skip is False
    assert reason == ""


def test_default_window_matches_instruction_file():
    assert DEFAULT_WINDOW == "15:30-16:00"
