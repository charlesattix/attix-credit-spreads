"""Tests for EXP-1600-max: Comprehensive Experiment Summary Report."""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXP_DIR = ROOT / "experiments" / "EXP-1600-max"
REPORT_PATH = EXP_DIR / "results" / "report.html"
SUMMARY_PATH = EXP_DIR / "results" / "summary.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def report_html() -> str:
    assert REPORT_PATH.exists(), f"Report not found: {REPORT_PATH}"
    return REPORT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def summary() -> dict:
    assert SUMMARY_PATH.exists(), f"Summary not found: {SUMMARY_PATH}"
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------

class TestFileExistence:
    def test_report_html_exists(self):
        assert REPORT_PATH.exists()

    def test_summary_json_exists(self):
        assert SUMMARY_PATH.exists()

    def test_thesis_exists(self):
        assert (EXP_DIR / "THESIS.md").exists()

    def test_status_exists(self):
        assert (EXP_DIR / "STATUS.md").exists()

    def test_report_not_empty(self):
        assert REPORT_PATH.stat().st_size > 10_000, "Report should be >10KB"


# ---------------------------------------------------------------------------
# HTML structure
# ---------------------------------------------------------------------------

class TestHTMLStructure:
    def test_is_valid_html(self, report_html):
        assert report_html.startswith("<!DOCTYPE html>")
        assert "</html>" in report_html

    def test_has_title(self, report_html):
        assert "<title>" in report_html
        assert "Comprehensive Experiment Summary" in report_html

    def test_has_style_section(self, report_html):
        assert "<style>" in report_html

    def test_no_external_dependencies(self, report_html):
        assert "cdn." not in report_html.lower()
        assert 'src="http' not in report_html

    def test_responsive_meta(self, report_html):
        assert 'viewport' in report_html

    def test_print_styles(self, report_html):
        assert "@media print" in report_html


# ---------------------------------------------------------------------------
# Required sections
# ---------------------------------------------------------------------------

class TestRequiredSections:
    REQUIRED_SECTIONS = [
        "Top 10 Strategies",
        "Best Overlays for EXP-880",
        "Failed Experiments",
        "Recommended Paper Trading Portfolio",
        "Next Research Priorities",
        "Core Strategies",
        "Alpha Streams",
        "Signal",
        "Sizing",
        "Validation",
    ]

    @pytest.mark.parametrize("section", REQUIRED_SECTIONS)
    def test_section_present(self, report_html, section):
        assert section in report_html, f"Missing section: {section}"

    def test_table_of_contents(self, report_html):
        assert "Contents" in report_html
        assert 'href="#top10"' in report_html

    def test_footer(self, report_html):
        assert "EXP-1600-max" in report_html
        assert "2026-04-03" in report_html


# ---------------------------------------------------------------------------
# Experiment coverage
# ---------------------------------------------------------------------------

class TestExperimentCoverage:
    """Every experiment from EXP-810 through EXP-1570 must appear."""

    CORE_EXPERIMENTS = [
        "EXP-810", "EXP-820", "EXP-840", "EXP-850", "EXP-860",
        "EXP-870", "EXP-880", "EXP-890", "EXP-900", "EXP-910",
        "EXP-920", "EXP-930", "EXP-940", "EXP-950", "EXP-960",
        "EXP-970", "EXP-980", "EXP-990",
    ]

    ALPHA_EXPERIMENTS = [
        "EXP-1000", "EXP-1010", "EXP-1020", "EXP-1030", "EXP-1040",
        "EXP-1060", "EXP-1070", "EXP-1080", "EXP-1090", "EXP-1100",
    ]

    SIGNAL_EXPERIMENTS = [
        "EXP-1110", "EXP-1120", "EXP-1130", "EXP-1140", "EXP-1150",
        "EXP-1160", "EXP-1170", "EXP-1180", "EXP-1190", "EXP-1200",
        "EXP-1210", "EXP-1220", "EXP-1230", "EXP-1240", "EXP-1250",
        "EXP-1260", "EXP-1270", "EXP-1280", "EXP-1290", "EXP-1300",
    ]

    SIZING_EXPERIMENTS = [
        "EXP-1310", "EXP-1320", "EXP-1330", "EXP-1340", "EXP-1350",
        "EXP-1360", "EXP-1370", "EXP-1380", "EXP-1390", "EXP-1400",
        "EXP-1410", "EXP-1420", "EXP-1430", "EXP-1440", "EXP-1450",
        "EXP-1460", "EXP-1470", "EXP-1480", "EXP-1490", "EXP-1500",
    ]

    DEPLOY_EXPERIMENTS = [
        "EXP-1510", "EXP-1520", "EXP-1530", "EXP-1540", "EXP-1550",
        "EXP-1560", "EXP-1570",
    ]

    ALL_EXPERIMENTS = (
        CORE_EXPERIMENTS + ALPHA_EXPERIMENTS + SIGNAL_EXPERIMENTS
        + SIZING_EXPERIMENTS + DEPLOY_EXPERIMENTS
    )

    @pytest.mark.parametrize("exp_id", ALL_EXPERIMENTS)
    def test_experiment_in_report(self, report_html, exp_id):
        assert exp_id in report_html, f"{exp_id} missing from report"

    def test_total_experiment_count(self, report_html):
        assert "78" in report_html, "Should mention 78 total experiments"


# ---------------------------------------------------------------------------
# Top 10 ranking
# ---------------------------------------------------------------------------

class TestTop10:
    TOP_10_IDS = [
        "EXP-1470", "EXP-910", "EXP-860", "EXP-1000", "EXP-840",
        "EXP-810", "EXP-960", "EXP-880", "EXP-870", "EXP-970",
    ]

    @pytest.mark.parametrize("exp_id", TOP_10_IDS)
    def test_top10_experiment_present(self, report_html, exp_id):
        assert exp_id in report_html

    def test_composite_score_explained(self, report_html):
        assert "Sharpe" in report_html
        assert "CAGR" in report_html
        assert "Max DD" in report_html


# ---------------------------------------------------------------------------
# Overlays section
# ---------------------------------------------------------------------------

class TestOverlays:
    OVERLAY_SOURCES = ["EXP-1230", "EXP-1220", "EXP-1370", "EXP-1270", "EXP-1160"]

    @pytest.mark.parametrize("exp_id", OVERLAY_SOURCES)
    def test_overlay_present(self, report_html, exp_id):
        assert exp_id in report_html

    def test_overlay_impact_metrics(self, report_html):
        assert "+21.4pp" in report_html  # microstructure
        assert "-19.4pp" in report_html  # tail risk
        assert "72.7 bps" in report_html  # VWAP


# ---------------------------------------------------------------------------
# Failed experiments
# ---------------------------------------------------------------------------

class TestFailedExperiments:
    FAILED_IDS = ["EXP-1110", "EXP-1320", "EXP-1420", "EXP-980"]

    @pytest.mark.parametrize("exp_id", FAILED_IDS)
    def test_failed_experiment_listed(self, report_html, exp_id):
        assert exp_id in report_html

    def test_lessons_learned_section(self, report_html):
        assert "Lesson" in report_html or "lesson" in report_html


# ---------------------------------------------------------------------------
# Paper trading portfolio
# ---------------------------------------------------------------------------

class TestPaperTradingPortfolio:
    def test_phase1_present(self, report_html):
        assert "Phase 1" in report_html

    def test_phase2_present(self, report_html):
        assert "Phase 2" in report_html

    def test_production_overlays(self, report_html):
        assert "VWAP" in report_html
        assert "Keltner" in report_html
        assert "Tail Risk" in report_html

    def test_leverage_path(self, report_html):
        assert "Alpaca" in report_html
        assert "IBKR" in report_html


# ---------------------------------------------------------------------------
# Summary JSON validation
# ---------------------------------------------------------------------------

class TestSummaryJSON:
    def test_experiment_field(self, summary):
        assert summary["experiment"] == "EXP-1600-max"

    def test_total_experiments(self, summary):
        assert summary["total_experiments"] == 78

    def test_status_distribution_sums(self, summary):
        dist = summary["status_distribution"]
        total = sum(dist.values())
        assert total == 78, f"Status distribution sums to {total}, expected 78"

    def test_north_star_metrics(self, summary):
        ns = summary["north_star"]
        assert ns["cagr_pct"] == 76.9
        assert ns["sharpe_production"] == 4.97
        assert ns["sharpe_best"] == 12.30
        assert ns["max_dd_pct"] == 10.2
        assert ns["profitable_years"] == 6
        assert ns["validation_tests_passed"] == 7

    def test_cagr_exceeds_target(self, summary):
        ns = summary["north_star"]
        assert ns["cagr_pct"] > ns["cagr_target_pct"]

    def test_dd_below_target(self, summary):
        ns = summary["north_star"]
        assert ns["max_dd_pct"] < ns["max_dd_target_pct"]

    def test_capacity_exceeds_target(self, summary):
        ns = summary["north_star"]
        assert ns["capacity_billions"] > ns["capacity_target_billions"]

    def test_top10_has_10(self, summary):
        assert len(summary["top_10"]) == 10

    def test_top10_sorted_by_score(self, summary):
        scores = [e["score"] for e in summary["top_10"]]
        assert scores == sorted(scores, reverse=True)

    def test_top10_all_have_required_fields(self, summary):
        for entry in summary["top_10"]:
            assert "rank" in entry
            assert "id" in entry
            assert "name" in entry
            assert "score" in entry
            assert "cagr" in entry
            assert "sharpe" in entry
            assert "max_dd" in entry

    def test_best_overlays_count(self, summary):
        assert len(summary["best_overlays"]) == 5

    def test_failed_experiments_count(self, summary):
        assert len(summary["failed_experiments"]) >= 4

    def test_failed_experiments_have_reasons(self, summary):
        for entry in summary["failed_experiments"]:
            assert "id" in entry
            assert "reason" in entry
            assert len(entry["reason"]) > 10


# ---------------------------------------------------------------------------
# North Star metrics in HTML
# ---------------------------------------------------------------------------

class TestNorthStarInHTML:
    def test_production_cagr(self, report_html):
        assert "76.9%" in report_html

    def test_best_sharpe(self, report_html):
        assert "12.30" in report_html

    def test_max_dd(self, report_html):
        assert "10.2%" in report_html

    def test_capacity(self, report_html):
        assert "$3.1B" in report_html

    def test_all_targets_met(self, report_html):
        assert "ALL TARGETS MET" in report_html

    def test_validation_passed(self, report_html):
        assert "7/7" in report_html


# ---------------------------------------------------------------------------
# Badges / status indicators
# ---------------------------------------------------------------------------

class TestStatusBadges:
    def test_validated_badge(self, report_html):
        assert "badge-validated" in report_html

    def test_promising_badge(self, report_html):
        assert "badge-promising" in report_html

    def test_failed_badge(self, report_html):
        assert "badge-failed" in report_html

    def test_infra_badge(self, report_html):
        assert "badge-infra" in report_html
