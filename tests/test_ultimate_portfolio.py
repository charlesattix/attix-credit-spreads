"""Tests for Ultimate Portfolio Backtest results."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "ultimate_portfolio.html"
JSON_PATH = ROOT / "reports" / "ultimate_portfolio.json"


@pytest.fixture(scope="module")
def html() -> str:
    assert REPORT_PATH.exists()
    return REPORT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def data() -> dict:
    assert JSON_PATH.exists()
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


class TestFileExistence:
    def test_html(self):
        assert REPORT_PATH.exists()
        assert REPORT_PATH.stat().st_size > 5_000

    def test_json(self):
        assert JSON_PATH.exists()


class TestStrategies:
    def test_four_strategies(self, data):
        assert len(data["strategy_names"]) == 4

    def test_exp1220_present(self, data):
        assert any("1220" in n for n in data["strategy_names"])

    def test_cross_asset_present(self, data):
        assert any("Cross" in n or "Pair" in n for n in data["strategy_names"])

    def test_vol_term_present(self, data):
        assert any("Vol" in n for n in data["strategy_names"])

    def test_tlt_ic_present(self, data):
        assert any("TLT" in n for n in data["strategy_names"])


class TestSoloMetrics:
    def test_exp1220_sharpe_positive(self, data):
        exp = [v for k, v in data["solo_metrics"].items() if "1220" in k][0]
        assert exp["sharpe"] > 3.0

    def test_exp1220_cagr_high(self, data):
        exp = [v for k, v in data["solo_metrics"].items() if "1220" in k][0]
        assert exp["cagr_pct"] > 40

    def test_all_solo_sharpe_positive(self, data):
        for name, m in data["solo_metrics"].items():
            assert m["sharpe"] >= 0, f"{name} has negative Sharpe"


class TestCorrelations:
    def test_correlation_matrix_exists(self, data):
        assert len(data["correlations"]) == 4

    def test_near_zero_correlations(self, data):
        for ni, row in data["correlations"].items():
            for nj, v in row.items():
                if ni != nj:
                    assert abs(v) < 0.5, f"High correlation {ni} vs {nj}: {v}"


class TestWalkForward:
    def test_multiple_windows(self, data):
        assert data["walk_forward"]["n_windows"] >= 4

    def test_walk_forward_sharpe_positive(self, data):
        assert data["walk_forward"]["metrics"]["sharpe"] > 0


class TestPortfolio:
    def test_best_method_selected(self, data):
        assert data["best_portfolio"]["method"] is not None

    def test_weights_sum_to_one(self, data):
        total = sum(data["best_portfolio"]["weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_dd_under_12(self, data):
        assert data["best_portfolio"]["metrics"]["max_dd_pct"] <= 12.0


class TestLeverageSweep:
    def test_multiple_leverage_points(self, data):
        assert len(data["leverage_sweep"]) >= 8

    def test_100_cagr_achievable(self, data):
        cagrs = [l["cagr_pct"] for l in data["leverage_sweep"]]
        assert max(cagrs) >= 100, "100% CAGR not achievable at any leverage"

    def test_target_exists(self, data):
        """At least one leverage point has CAGR >= 100% AND DD <= 12%."""
        hits = [l for l in data["leverage_sweep"]
                if l["cagr_pct"] >= 100 and l["max_dd_pct"] <= 12]
        assert len(hits) >= 1, "No leverage point hits both targets"


class TestMonteCarlo:
    def test_10k_paths(self, data):
        assert data["monte_carlo"]["n_simulations"] == 10_000

    def test_prob_profit_high(self, data):
        assert data["monte_carlo"]["prob_profit"] >= 0.95

    def test_prob_ruin_low(self, data):
        assert data["monte_carlo"]["prob_ruin_50pct"] < 0.01

    def test_p5_dd_reasonable(self, data):
        p5 = abs(data["monte_carlo"]["max_drawdown"]["percentiles_pct"].get("p5", 0))
        assert p5 <= 15, f"P5 DD {p5}% too high"


class TestCrisisScenarios:
    def test_four_scenarios(self, data):
        assert len(data["crisis"]) == 4

    def test_covid_worst(self, data):
        covid = [c for c in data["crisis"] if "COVID" in c["name"]][0]
        assert covid["portfolio_drawdown_pct"] < 0


class TestYearly:
    def test_six_years(self, data):
        assert len(data["yearly"]) == 6

    def test_all_years_present(self, data):
        keys = [str(k) for k in data["yearly"].keys()]
        for yr in ["2020", "2021", "2022", "2023", "2024", "2025"]:
            assert yr in keys, f"Missing year {yr}"


class TestHTML:
    def test_valid_html(self, html):
        assert "<!DOCTYPE html>" in html

    def test_title(self, html):
        assert "Ultimate Portfolio" in html

    def test_monte_carlo_section(self, html):
        assert "Monte Carlo" in html

    def test_crisis_section(self, html):
        assert "Crisis" in html

    def test_leverage_section(self, html):
        assert "Leverage" in html

    def test_correlation_section(self, html):
        assert "Correlation" in html

    def test_walk_forward_section(self, html):
        assert "Walk-Forward" in html
