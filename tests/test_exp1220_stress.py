"""Tests for EXP-1220 stress test results."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "exp1220_stress_test.html"
JSON_PATH = ROOT / "reports" / "exp1220_stress_test.json"


@pytest.fixture(scope="module")
def report_html() -> str:
    assert REPORT_PATH.exists(), f"Report not found: {REPORT_PATH}"
    return REPORT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def results() -> dict:
    assert JSON_PATH.exists(), f"JSON not found: {JSON_PATH}"
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


# --- File existence ---

class TestFileExistence:
    def test_html_exists(self):
        assert REPORT_PATH.exists()

    def test_json_exists(self):
        assert JSON_PATH.exists()

    def test_html_not_empty(self):
        assert REPORT_PATH.stat().st_size > 5_000


# --- North Star threshold ---

class TestNorthStar:
    def test_p5_dd_below_12_pct(self, results):
        assert results["north_star_p5_dd_pct"] <= 12.0, (
            f"P5 DD {results['north_star_p5_dd_pct']}% exceeds 12% threshold"
        )

    def test_north_star_pass_flag(self, results):
        assert results["north_star_pass"] is True

    def test_leverage_is_1_2x(self, results):
        assert results["leverage"] == 1.2


# --- Monte Carlo ---

class TestMonteCarlo:
    def test_n_simulations(self, results):
        assert results["monte_carlo"]["n_simulations"] == 10_000

    def test_block_size(self, results):
        assert results["monte_carlo"]["block_size"] == 5

    def test_prob_profit_high(self, results):
        assert results["monte_carlo"]["prob_profit"] >= 0.95

    def test_prob_ruin_low(self, results):
        assert results["monte_carlo"]["prob_ruin_50pct"] < 0.01

    def test_median_terminal_above_starting(self, results):
        assert results["monte_carlo"]["terminal_wealth"]["median"] > 100_000

    def test_p5_dd_in_percentiles(self, results):
        pcts = results["monte_carlo"]["max_drawdown"]["percentiles_pct"]
        assert "p5" in pcts
        assert "p95" in pcts

    def test_sharpe_percentiles(self, results):
        pcts = results["monte_carlo"]["sharpe_ratio"]["percentiles"]
        assert "p5" in pcts
        assert pcts["p5"] > 0  # positive even at 5th percentile


# --- Crisis scenarios ---

class TestCrisisScenarios:
    def test_four_scenarios(self, results):
        assert len(results["crisis_scenarios"]) == 4

    SCENARIO_NAMES = [
        "COVID Crash (Feb-Mar 2020)",
        "2022 Bear Market",
        "Flash Crash (Single Day)",
        "VIX Spike (15 → 65)",
    ]

    @pytest.mark.parametrize("name", SCENARIO_NAMES)
    def test_scenario_present(self, results, name):
        names = [c["name"] for c in results["crisis_scenarios"]]
        assert name in names

    def test_covid_dd_negative(self, results):
        covid = next(c for c in results["crisis_scenarios"] if "COVID" in c["name"])
        assert covid["portfolio_drawdown_pct"] < 0

    def test_flash_crash_is_1_day(self, results):
        flash = next(c for c in results["crisis_scenarios"] if "Flash" in c["name"])
        assert flash["n_days"] == 1

    def test_all_have_recovery(self, results):
        for c in results["crisis_scenarios"]:
            assert "estimated_recovery_days" in c


# --- Summary ---

class TestSummary:
    def test_risk_rating_exists(self, results):
        assert results["summary"]["risk_rating"] in ("LOW", "MODERATE", "HIGH", "CRITICAL")

    def test_historical_sharpe_positive(self, results):
        assert results["summary"]["historical"]["sharpe"] > 0

    def test_historical_cagr_positive(self, results):
        assert results["summary"]["historical"]["cagr_pct"] > 0

    def test_worst_crisis_identified(self, results):
        assert results["summary"]["worst_crisis"]["name"] != "N/A"


# --- Tail risk metrics ---

class TestTailRisk:
    def test_cvar_95_negative(self, results):
        assert results["tail_risk"]["cvar_95_pct"] < 0

    def test_cvar_99_worse_than_95(self, results):
        assert results["tail_risk"]["cvar_99_pct"] <= results["tail_risk"]["cvar_95_pct"]

    def test_var_95_negative(self, results):
        assert results["tail_risk"]["var_95_pct"] < 0

    def test_max_consecutive_losses(self, results):
        assert results["tail_risk"]["max_consecutive_losses"] >= 1

    def test_longest_dd_duration(self, results):
        assert results["tail_risk"]["longest_dd_duration_days"] >= 1

    def test_skewness_exists(self, results):
        assert "skewness" in results["tail_risk"]

    def test_kurtosis_exists(self, results):
        assert "excess_kurtosis" in results["tail_risk"]


# --- HTML content ---

class TestHTMLContent:
    def test_valid_html(self, report_html):
        assert "<!DOCTYPE html>" in report_html

    def test_title(self, report_html):
        assert "EXP-1220" in report_html
        assert "1.2x" in report_html

    def test_monte_carlo_section(self, report_html):
        assert "Monte Carlo" in report_html
        assert "10,000" in report_html

    def test_crisis_section(self, report_html):
        assert "Crisis Scenario" in report_html
        assert "COVID" in report_html

    def test_sensitivity_section(self, report_html):
        assert "Sensitivity" in report_html

    def test_tail_risk_section(self, report_html):
        assert "Tail Risk" in report_html
        assert "CVaR" in report_html

    def test_north_star_verdict(self, report_html):
        assert "North Star" in report_html

    def test_no_external_deps(self, report_html):
        assert "cdn." not in report_html.lower()
