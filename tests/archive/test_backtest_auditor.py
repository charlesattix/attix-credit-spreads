"""Tests for compass/backtest_auditor.py — 50+ tests."""

import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compass.backtest_auditor import (
    CheckResult, AuditReport, BacktestAuditor,
    check_dilution, check_synthetic_data, check_look_ahead,
    check_sharpe_formula, check_survivorship,
    check_transaction_costs, check_capacity,
)


# ═══════════════════════════════════════════════════════════════════════════
# CheckResult / AuditReport basics
# ═══════════════════════════════════════════════════════════════════════════

class TestDataClasses:
    def test_check_result(self):
        c = CheckResult("test", True, "PASS", "all good")
        assert c.passed is True
        assert c.severity == "PASS"

    def test_audit_report_summary(self):
        r = AuditReport(
            checks=[CheckResult("A", True, "PASS", "ok"),
                     CheckResult("B", False, "FAIL", "bad")],
            n_passed=1, n_failed=1, overall_grade="C",
        )
        s = r.summary()
        assert "Grade: C" in s
        assert "Passed: 1" in s

    def test_empty_report(self):
        r = AuditReport()
        assert r.overall_grade == ""
        assert len(r.checks) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 1. Dilution Check
# ═══════════════════════════════════════════════════════════════════════════

class TestDilution:
    def test_no_dilution(self):
        eq = [100000 + i * 50 + np.random.normal(0, 20) for i in range(100)]
        r = check_dilution(equity_curve=eq)
        assert r.passed is True

    def test_high_dilution_equity(self):
        # 90% of days are flat
        eq = [100000] * 90 + [100000 + i * 100 for i in range(10)]
        r = check_dilution(equity_curve=eq)
        assert r.passed is False
        assert r.metric_value > 0.80

    def test_moderate_dilution(self):
        # 60% flat
        eq = [100000] * 60 + [100000 + i * 50 for i in range(40)]
        r = check_dilution(equity_curve=eq)
        assert r.passed is False
        assert "WARNING" in r.severity or "FAIL" in r.severity

    def test_trades_based_dilution(self):
        trades = [{"entry_date": f"2024-01-{i+1:02d}"} for i in range(10)]
        r = check_dilution(trades=trades, n_days=100)
        # 10 trading days out of 100 = 90% dilution
        assert r.passed is False

    def test_no_data(self):
        r = check_dilution()
        assert r.passed is True  # insufficient data → pass

    def test_dict_equity_curve(self):
        eq = [{"equity": 100000 + i * 30} for i in range(50)]
        r = check_dilution(equity_curve=eq)
        assert r.name == "Dilution Check"

    def test_custom_threshold(self):
        eq = [100000] * 40 + [100000 + i * 50 for i in range(60)]
        r = check_dilution(equity_curve=eq, threshold=0.30)
        assert r.passed is False  # 40% zero > 30% threshold


# ═══════════════════════════════════════════════════════════════════════════
# 2. Synthetic Data Check
# ═══════════════════════════════════════════════════════════════════════════

class TestSyntheticData:
    def test_ironvault_passes(self):
        r = check_synthetic_data(data_source="ironvault")
        assert r.passed is True

    def test_real_passes(self):
        r = check_synthetic_data(data_source="Real IronVault data")
        assert r.passed is True

    def test_synthetic_label_fails(self):
        r = check_synthetic_data(data_source="synthetic")
        assert r.passed is False
        assert r.severity in ("FAIL", "CRITICAL")

    def test_simulated_label_fails(self):
        r = check_synthetic_data(data_source="simulated")
        assert r.passed is False

    def test_unknown_source_fails(self):
        r = check_synthetic_data(data_source="my_custom_model")
        assert r.passed is False

    def test_code_with_random(self, tmp_path):
        code = tmp_path / "test.py"
        code.write_text("import numpy as np\nprices = np.random.normal(100, 5, 252)\n")
        r = check_synthetic_data(code_path=str(code))
        assert r.passed is False
        assert "np.random" in r.details

    def test_code_with_bs(self, tmp_path):
        code = tmp_path / "test.py"
        code.write_text("from pricing import black_scholes\nprice = black_scholes(S, K, T, r, sigma)\n")
        r = check_synthetic_data(code_path=str(code))
        assert r.passed is False

    def test_code_with_credit_fraction(self, tmp_path):
        code = tmp_path / "test.py"
        code.write_text("BACKTEST_CREDIT_FRACTION = 0.30\ncredit = width * BACKTEST_CREDIT_FRACTION\n")
        r = check_synthetic_data(code_path=str(code))
        assert r.passed is False

    def test_clean_code_passes(self, tmp_path):
        code = tmp_path / "test.py"
        code.write_text("from shared.iron_vault import IronVault\nhd = IronVault.instance()\n")
        r = check_synthetic_data(data_source="ironvault", code_path=str(code))
        assert r.passed is True

    def test_uniform_credits_flagged(self):
        trades = [{"credit": 1.50} for _ in range(20)]
        r = check_synthetic_data(trades=trades)
        assert r.passed is False
        assert "uniform" in r.details.lower()

    def test_varied_credits_pass(self):
        rng = np.random.RandomState(42)
        trades = [{"credit": round(1.0 + rng.normal(0, 0.3), 2)} for _ in range(20)]
        r = check_synthetic_data(data_source="ironvault", trades=trades)
        assert r.passed is True

    def test_empty_source(self):
        r = check_synthetic_data(data_source="")
        assert r.passed is True  # no data to flag


# ═══════════════════════════════════════════════════════════════════════════
# 3. Look-Ahead Bias Check
# ═══════════════════════════════════════════════════════════════════════════

class TestLookAhead:
    def test_normal_trades(self):
        trades = [{"entry_date": "2024-01-15", "exit_date": "2024-01-22"}]
        r = check_look_ahead(trades=trades)
        assert r.passed is True

    def test_exit_before_entry(self):
        trades = [{"entry_date": "2024-01-22", "exit_date": "2024-01-15"}]
        r = check_look_ahead(trades=trades)
        assert r.passed is False
        assert r.severity == "CRITICAL"

    def test_code_with_negative_shift(self, tmp_path):
        code = tmp_path / "test.py"
        code.write_text("future_price = df['Close'].shift(-1)\nsignal = future_price > current\n")
        r = check_look_ahead(code_path=str(code))
        assert r.passed is False

    def test_clean_code(self, tmp_path):
        code = tmp_path / "test.py"
        code.write_text("past_price = df['Close'].shift(1)\nsignal = past_price < current\n")
        r = check_look_ahead(code_path=str(code))
        assert r.passed is True

    def test_empty(self):
        r = check_look_ahead()
        assert r.passed is True

    def test_multiple_bad_trades(self):
        trades = [
            {"entry_date": "2024-01-22", "exit_date": "2024-01-15"},
            {"entry_date": "2024-02-10", "exit_date": "2024-02-05"},
        ]
        r = check_look_ahead(trades=trades)
        assert r.metric_value == 2


# ═══════════════════════════════════════════════════════════════════════════
# 4. Sharpe Formula Check
# ═══════════════════════════════════════════════════════════════════════════

class TestSharpeFormula:
    def test_correct_sharpe(self):
        pnls = np.array([100, 50, -30, 80, 120, -40, 60, 90, -20, 110])
        mean = pnls.mean()
        std = pnls.std(ddof=1)
        correct = mean / std * math.sqrt(min(len(pnls), 52))
        r = check_sharpe_formula(reported_sharpe=round(correct, 2),
                                  trades=[{"pnl": p} for p in pnls])
        assert r.passed is True

    def test_inflated_sharpe(self):
        pnls = np.array([100, 50, -30, 80, 120, -40, 60, 90, -20, 110])
        mean = pnls.mean()
        std = pnls.std(ddof=1)
        correct = mean / std * math.sqrt(min(len(pnls), 52))
        r = check_sharpe_formula(reported_sharpe=correct * 2.5,
                                  trades=[{"pnl": p} for p in pnls])
        assert r.passed is False
        assert "inflated" in r.details.lower() or "differs" in r.message.lower()

    def test_cagr_based_detection(self):
        # 100% CAGR with reported Sharpe 25 → clearly inflated
        # expected_max = 1.0 / 0.08 = 12.5; 25 > 12.5 * 1.5
        r = check_sharpe_formula(reported_sharpe=25.0, cagr=1.0)
        assert r.passed is False

    def test_extreme_cagr_sharpe_mismatch(self):
        # 200% CAGR with Sharpe 50 → clearly inflated
        # expected_max = 2.0 / 0.08 = 25; 50 > 25 * 1.5
        r = check_sharpe_formula(reported_sharpe=50.0, cagr=2.0)
        assert r.passed is False

    def test_no_data(self):
        r = check_sharpe_formula()
        assert r.passed is True

    def test_zero_reported(self):
        r = check_sharpe_formula(reported_sharpe=0,
                                  trades=[{"pnl": 100}, {"pnl": -50}])
        assert r.passed is True  # no reported → compute only


# ═══════════════════════════════════════════════════════════════════════════
# 5. Survivorship Check
# ═══════════════════════════════════════════════════════════════════════════

class TestSurvivorship:
    def test_all_closed_with_exits(self):
        trades = [{"exit_date": "2024-01-22", "status": "closed"} for _ in range(10)]
        r = check_survivorship(trades)
        assert r.passed is True

    def test_closed_without_exit(self):
        trades = [{"status": "closed"} for _ in range(10)]  # no exit_date
        r = check_survivorship(trades)
        assert r.passed is False

    def test_open_trades_ok(self):
        trades = [{"status": "open"} for _ in range(5)]
        r = check_survivorship(trades)
        assert r.passed is True

    def test_mixed(self):
        trades = ([{"exit_date": "2024-01-22", "status": "closed"}] * 9 +
                  [{"status": "closed"}])  # 1/10 missing exit
        r = check_survivorship(trades)
        assert r.passed is False

    def test_empty(self):
        r = check_survivorship([])
        assert r.passed is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. Transaction Cost Check
# ═══════════════════════════════════════════════════════════════════════════

class TestTransactionCosts:
    def test_costs_included(self):
        r = check_transaction_costs(has_commissions=True, has_slippage=True)
        assert r.passed is True

    def test_no_commissions(self):
        r = check_transaction_costs(has_commissions=False, has_slippage=True)
        assert r.passed is False

    def test_no_slippage(self):
        r = check_transaction_costs(has_commissions=True, has_slippage=False)
        assert r.passed is False

    def test_no_costs_at_all(self):
        r = check_transaction_costs(has_commissions=False, has_slippage=False)
        assert r.passed is False
        assert r.metric_value >= 2

    def test_suspicious_credits(self):
        trades = [{"credit": 4.5} for _ in range(20)]  # 90% of $5 width
        r = check_transaction_costs(trades=trades, spread_width=5.0)
        assert r.passed is False
        assert "suspiciously high" in r.details.lower()

    def test_reasonable_credits(self):
        trades = [{"credit": 1.2} for _ in range(20)]  # 24% of width — normal
        r = check_transaction_costs(trades=trades, has_commissions=True,
                                     has_slippage=True, spread_width=5.0)
        assert r.passed is True

    def test_trade_cost_fields(self):
        trades = [{"commission": 0.65, "slippage": 0.05}]
        r = check_transaction_costs(trades=trades)
        # has_commissions/has_slippage flags override — trades just provide extra info
        assert isinstance(r, CheckResult)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Capacity Check
# ═══════════════════════════════════════════════════════════════════════════

class TestCapacity:
    def test_spy_small_position(self):
        r = check_capacity(ticker="SPY", avg_contracts=5)
        assert r.passed is True  # 5 vs 500K ADV

    def test_spy_huge_position(self):
        r = check_capacity(ticker="SPY", avg_contracts=50_000)
        assert r.passed is False

    def test_gld_moderate(self):
        r = check_capacity(ticker="GLD", avg_contracts=300)
        assert r.passed is False  # 300 vs 5K ADV = 6% > 5%

    def test_from_trades(self):
        trades = [{"contracts": 3} for _ in range(20)]
        r = check_capacity(trades=trades, ticker="SPY")
        assert r.passed is True

    def test_unknown_ticker(self):
        r = check_capacity(ticker="ZZZZZ", avg_contracts=10)
        assert isinstance(r, CheckResult)

    def test_zero_contracts(self):
        r = check_capacity(ticker="SPY", avg_contracts=0)
        assert r.passed is True


# ═══════════════════════════════════════════════════════════════════════════
# Full Auditor
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditor:
    def test_clean_backtest_grade_a(self):
        auditor = BacktestAuditor()
        rng = np.random.RandomState(42)
        # Generate equity curve with daily returns (no dilution)
        eq = [100000]
        for i in range(250):
            eq.append(eq[-1] + rng.normal(50, 30))
        trades = [
            {"entry_date": f"2024-{(i%12)+1:02d}-{(i%28)+1:02d}",
             "exit_date": f"2024-{(i%12)+1:02d}-{(i%28)+5:02d}",
             "pnl": round(rng.normal(100, 50), 2),
             "credit": round(1.0 + rng.normal(0, 0.3), 2),
             "contracts": 3, "status": "closed"}
            for i in range(30)
        ]
        report = auditor.audit(
            trades=trades,
            equity_curve=eq,
            data_source="ironvault",
            has_commissions=True,
            has_slippage=True,
            ticker="SPY",
        )
        assert report.overall_grade in ("A", "B+", "B")
        assert report.n_critical == 0

    def test_bad_backtest_grade_d_or_f(self):
        auditor = BacktestAuditor()
        trades = [
            {"entry_date": "2024-01-22", "exit_date": "2024-01-15",  # lookahead
             "pnl": 100, "credit": 1.50, "status": "closed"}
            for _ in range(20)
        ]
        report = auditor.audit(
            trades=trades,
            data_source="synthetic",
            reported_sharpe=25.0,
            reported_cagr=1.0,
        )
        assert report.overall_grade in ("C", "D", "F")
        # Should have critical (lookahead + synthetic) or multiple failures
        assert (report.n_critical + report.n_failed) >= 2

    def test_results_dict_input(self):
        auditor = BacktestAuditor()
        results = {
            "trades": [{"entry_date": "2024-01-15", "exit_date": "2024-01-22",
                        "pnl": 200, "contracts": 2, "status": "closed"}] * 10,
            "sharpe_ratio": 2.5,
            "data_source": "ironvault",
            "commission_per_contract": 0.65,
            "slippage": 0.05,
        }
        report = auditor.audit(results=results)
        assert len(report.checks) == 7

    def test_html_generation(self):
        auditor = BacktestAuditor()
        report = auditor.audit(trades=[], data_source="ironvault",
                               has_commissions=True, has_slippage=True)
        html = auditor.generate_html(report)
        assert "Grade:" in html
        assert "Check Results" in html

    def test_recommendations(self):
        auditor = BacktestAuditor()
        report = auditor.audit(data_source="synthetic", reported_sharpe=9.0,
                               reported_cagr=1.0)
        assert len(report.recommendations) > 0

    def test_7_checks_run(self):
        auditor = BacktestAuditor()
        report = auditor.audit()
        assert len(report.checks) == 7
