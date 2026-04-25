"""Tests for compass/events.py — FOMC/CPI/NFP event gate and scaling logic."""

from datetime import date

from compass.events import (
    ALL_FOMC_DATES,
    CPI_SCALING,
    FOMC_SCALING,
    NFP_SCALING,
    _cpi_release_date,
    _first_friday_of_month,
    _iter_months,
    _nfp_release_date,
    compute_composite_scaling,
    get_upcoming_events,
)


class TestFirstFridayOfMonth:
    def test_jan_2026(self):
        assert _first_friday_of_month(2026, 1) == date(2026, 1, 2)

    def test_march_2026(self):
        # March 2026 starts on Sunday, first Friday = March 6
        assert _first_friday_of_month(2026, 3) == date(2026, 3, 6)

    def test_may_2026(self):
        # May 2026 starts on Friday
        assert _first_friday_of_month(2026, 5) == date(2026, 5, 1)

    def test_result_is_friday(self):
        for m in range(1, 13):
            d = _first_friday_of_month(2026, m)
            assert d.weekday() == 4, f"Month {m}: {d} is not Friday"


class TestCPIReleaseDate:
    def test_not_weekend(self):
        for m in range(1, 13):
            d = _cpi_release_date(2026, m)
            assert d.weekday() < 5, f"CPI for 2026-{m:02d} falls on weekend: {d}"

    def test_december_wraps_to_january(self):
        d = _cpi_release_date(2025, 12)
        assert d.year == 2026
        assert d.month == 1

    def test_release_is_next_month(self):
        d = _cpi_release_date(2026, 3)
        assert d.month == 4  # CPI for March released in April


class TestNFPReleaseDate:
    def test_result_is_friday(self):
        for m in range(1, 12):
            d = _nfp_release_date(2026, m)
            assert d.weekday() == 4

    def test_december_wraps(self):
        d = _nfp_release_date(2025, 12)
        assert d.year == 2026
        assert d.month == 1


class TestIterMonths:
    def test_forward(self):
        result = list(_iter_months(date(2026, 10, 1), range(0, 4)))
        assert result == [(2026, 10), (2026, 11), (2026, 12), (2027, 1)]

    def test_backward(self):
        result = list(_iter_months(date(2026, 2, 1), range(-1, 1)))
        assert result == [(2026, 1), (2026, 2)]

    def test_year_boundary(self):
        result = list(_iter_months(date(2026, 1, 15), range(-2, 1)))
        assert result == [(2025, 11), (2025, 12), (2026, 1)]


class TestFOMCDates:
    def test_all_dates_sorted(self):
        assert ALL_FOMC_DATES == sorted(ALL_FOMC_DATES)

    def test_no_duplicates(self):
        assert len(ALL_FOMC_DATES) == len(set(ALL_FOMC_DATES))

    def test_2026_has_8_dates(self):
        dates_2026 = [d for d in ALL_FOMC_DATES if d.year == 2026]
        assert len(dates_2026) == 8

    def test_contains_known_date(self):
        assert date(2026, 1, 29) in ALL_FOMC_DATES


class TestGetUpcomingEvents:
    def test_fomc_day_returns_event(self):
        events = get_upcoming_events(as_of=date(2026, 1, 29), horizon_days=1)
        fomc = [e for e in events if e["event_type"] == "FOMC"]
        assert len(fomc) >= 1
        assert fomc[0]["days_out"] == 0
        assert fomc[0]["scaling_factor"] == FOMC_SCALING[0]  # 0.50

    def test_one_day_before_fomc(self):
        events = get_upcoming_events(as_of=date(2026, 1, 28), horizon_days=2)
        fomc = [e for e in events if e["event_type"] == "FOMC"]
        assert any(e["days_out"] == 1 for e in fomc)

    def test_post_fomc_buffer(self):
        events = get_upcoming_events(as_of=date(2026, 1, 29), horizon_days=2)
        post = [e for e in events if e["event_type"] == "FOMC_POST"]
        assert len(post) >= 1
        assert post[0]["scaling_factor"] == 0.70

    def test_no_events_far_from_dates(self):
        # Feb 10 2026 is far from any FOMC/CPI/NFP
        events = get_upcoming_events(as_of=date(2026, 2, 10), horizon_days=1)
        assert len(events) == 0

    def test_deduplication(self):
        events = get_upcoming_events(as_of=date(2026, 1, 1), horizon_days=60)
        keys = [(e["event_date"], e["event_type"]) for e in events]
        assert len(keys) == len(set(keys))

    def test_cpi_event_included(self):
        # CPI for Jan 2026 released around Feb 12
        events = get_upcoming_events(as_of=date(2026, 2, 10), horizon_days=5)
        cpi = [e for e in events if e["event_type"] == "CPI"]
        assert len(cpi) >= 1

    def test_nfp_event_included(self):
        # NFP for Jan 2026 released first Friday of Feb = Feb 6
        events = get_upcoming_events(as_of=date(2026, 2, 5), horizon_days=2)
        nfp = [e for e in events if e["event_type"] == "NFP"]
        assert len(nfp) >= 1


class TestCompositeScaling:
    def test_empty_events(self):
        assert compute_composite_scaling([]) == 1.0

    def test_single_fomc(self):
        events = [{"event_type": "FOMC", "scaling_factor": 0.50}]
        assert compute_composite_scaling(events) == 0.50

    def test_fomc_and_cpi_takes_min(self):
        events = [
            {"event_type": "FOMC", "scaling_factor": 0.60},
            {"event_type": "CPI", "scaling_factor": 0.65},
        ]
        assert compute_composite_scaling(events) == 0.60

    def test_post_fomc_grouped_with_fomc(self):
        events = [
            {"event_type": "FOMC", "scaling_factor": 0.80},
            {"event_type": "FOMC_POST", "scaling_factor": 0.70},
        ]
        # Both are FOMC category; min = 0.70
        assert compute_composite_scaling(events) == 0.70

    def test_data_only(self):
        events = [
            {"event_type": "CPI", "scaling_factor": 0.75},
            {"event_type": "NFP", "scaling_factor": 0.80},
        ]
        assert compute_composite_scaling(events) == 0.75
