"""Tests for shared.entry_gate — EXP-3311 NFP Entry Filter."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from shared.entry_gate import (
    load_nfp_dates,
    should_skip_entry_for_nfp,
)


def test_skip_when_tomorrow_is_nfp():
    nfp = [date(2026, 6, 5), date(2026, 7, 2)]
    skip, reason = should_skip_entry_for_nfp(today=date(2026, 6, 4), nfp_dates=nfp)
    assert skip is True
    assert "2026-06-05" in reason


def test_no_skip_when_tomorrow_is_not_nfp():
    nfp = [date(2026, 6, 5)]
    skip, reason = should_skip_entry_for_nfp(today=date(2026, 6, 3), nfp_dates=nfp)
    assert skip is False
    assert reason == ""


def test_no_skip_on_nfp_day_itself():
    # The filter blocks day-BEFORE entries; the day-of is NOT gated.
    nfp = [date(2026, 6, 5)]
    skip, _ = should_skip_entry_for_nfp(today=date(2026, 6, 5), nfp_dates=nfp)
    assert skip is False


def test_empty_blacklist_is_permissive():
    skip, reason = should_skip_entry_for_nfp(today=date(2026, 6, 4), nfp_dates=[])
    assert skip is False
    assert reason == ""


def test_load_nfp_dates_from_file(tmp_path: Path):
    blob = {"nfp_dates": ["2026-06-05", "2026-07-02"]}
    p = tmp_path / "event_blacklist.json"
    p.write_text(json.dumps(blob))
    out = load_nfp_dates(str(p))
    assert out == [date(2026, 6, 5), date(2026, 7, 2)]


def test_load_nfp_dates_missing_file_returns_empty(tmp_path: Path):
    missing = tmp_path / "does_not_exist.json"
    assert load_nfp_dates(str(missing)) == []


def test_load_nfp_dates_skips_invalid_entries(tmp_path: Path):
    blob = {"nfp_dates": ["2026-06-05", "not-a-date", None, "2026-12-04"]}
    p = tmp_path / "event_blacklist.json"
    p.write_text(json.dumps(blob))
    out = load_nfp_dates(str(p))
    assert out == [date(2026, 6, 5), date(2026, 12, 4)]


def test_load_nfp_dates_handles_malformed_json(tmp_path: Path):
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    assert load_nfp_dates(str(p)) == []


def test_repo_blacklist_loads_seven_2026_dates():
    # The shipped configs/event_blacklist.json must contain the 7 BLS-published
    # 2026 NFP dates that EXP-3311 gates against.
    dates = load_nfp_dates("configs/event_blacklist.json")
    expected = [
        date(2026, 6, 5),
        date(2026, 7, 2),
        date(2026, 8, 7),
        date(2026, 9, 4),
        date(2026, 10, 2),
        date(2026, 11, 6),
        date(2026, 12, 4),
    ]
    assert dates == expected
