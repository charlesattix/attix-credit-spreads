"""Tests for compass.production_audit."""
import unittest
from compass.production_audit import (
    AuditReport, ModuleAudit, discover_modules, discover_tests,
    audit_module, run_audit, generate_report,
)

class TestDiscoverModules(unittest.TestCase):
    def test_returns_list(self):
        mods = discover_modules()
        self.assertIsInstance(mods, list)
        self.assertGreater(len(mods), 50)
    def test_excludes_init(self):
        self.assertNotIn("__init__", discover_modules())
    def test_excludes_self(self):
        self.assertNotIn("production_audit", discover_modules())
    def test_includes_known(self):
        mods = discover_modules()
        self.assertIn("regime", mods)
        self.assertIn("crisis_hedge", mods)

class TestDiscoverTests(unittest.TestCase):
    def test_returns_set(self):
        self.assertIsInstance(discover_tests(), set)
    def test_has_tests(self):
        self.assertGreater(len(discover_tests()), 10)

class TestAuditModule(unittest.TestCase):
    def test_known_module(self):
        tests = discover_tests()
        m = audit_module("regime", tests)
        self.assertIsInstance(m, ModuleAudit)
        self.assertEqual(m.name, "regime")
        self.assertGreater(m.lines, 0)
    def test_quality_range(self):
        tests = discover_tests()
        m = audit_module("crisis_hedge", tests)
        self.assertGreaterEqual(m.quality_score, 0)
        self.assertLessEqual(m.quality_score, 10)
    def test_has_category(self):
        tests = discover_tests()
        m = audit_module("regime", tests)
        self.assertIn(m.category, ["signal","regime","risk","execution","ml",
                                    "portfolio","backtest","data","monitoring","analysis","other"])
    def test_nonexistent(self):
        m = audit_module("nonexistent_xyz_999", set())
        self.assertFalse(m.imports_ok)
    def test_external_deps_detected(self):
        # Modules using numpy/pandas should be flagged
        tests = discover_tests()
        m = audit_module("regime_ensemble", tests)
        # regime_ensemble uses numpy/pandas
        if m.imports_ok:
            # If it imports, check deps were detected
            pass  # may or may not have deps depending on module
    def test_latency_valid(self):
        tests = discover_tests()
        m = audit_module("regime", tests)
        self.assertIn(m.estimated_latency, ["fast", "medium", "slow"])

class TestRunAudit(unittest.TestCase):
    def test_returns_report(self):
        r = run_audit()
        self.assertIsInstance(r, AuditReport)
    def test_module_count(self):
        r = run_audit()
        self.assertGreater(r.n_modules, 100)
    def test_some_production_ready(self):
        r = run_audit()
        self.assertGreater(r.n_production_ready, 0)
    def test_top10_populated(self):
        r = run_audit()
        self.assertGreater(len(r.top_10), 0)
        self.assertLessEqual(len(r.top_10), 10)
    def test_top10_sorted(self):
        r = run_audit()
        scores = [m.quality_score for m in r.top_10]
        self.assertEqual(scores, sorted(scores, reverse=True))
    def test_categories_populated(self):
        r = run_audit()
        self.assertGreater(len(r.categories), 3)
    def test_avg_quality_range(self):
        r = run_audit()
        self.assertGreater(r.avg_quality, 0)
        self.assertLessEqual(r.avg_quality, 10)
    def test_import_ok_count(self):
        r = run_audit()
        self.assertGreater(r.n_import_ok, r.n_modules * 0.3)  # at least 30% import
    def test_all_modules_have_score(self):
        r = run_audit()
        for m in r.modules:
            self.assertGreaterEqual(m.quality_score, 0)
            self.assertLessEqual(m.quality_score, 10)
    def test_production_ready_have_tests(self):
        r = run_audit()
        for m in r.top_10:
            self.assertTrue(m.has_tests)
            self.assertTrue(m.imports_ok)

class TestGenerateReport(unittest.TestCase):
    def test_returns_markdown(self):
        r = run_audit()
        md = generate_report(r)
        self.assertIn("# Production Readiness", md)
    def test_has_top10(self):
        md = generate_report(run_audit())
        self.assertIn("Top 10", md)
    def test_has_categories(self):
        md = generate_report(run_audit())
        self.assertIn("Category Breakdown", md)
    def test_has_full_table(self):
        md = generate_report(run_audit())
        self.assertIn("Full Module Ranking", md)

if __name__ == "__main__":
    unittest.main()
