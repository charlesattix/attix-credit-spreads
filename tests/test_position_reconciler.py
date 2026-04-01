"""Tests for compass.position_reconciler — position reconciliation."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from compass.position_reconciler import (
    Discrepancy,
    PositionReconciler,
    PositionRecord,
    ReconcilerConfig,
    ReconciliationReport,
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _pos(sym: str, qty: int, price: float = 2.50) -> PositionRecord:
    return PositionRecord(symbol=sym, quantity=qty, avg_price=price)


def _matching_positions():
    internal = [_pos("SPY_P440", -5, 2.50), _pos("SPY_P435", 5, 1.20)]
    broker = [_pos("SPY_P440", -5, 2.50), _pos("SPY_P435", 5, 1.20)]
    return internal, broker


# ── Clean reconciliation ───────────────────────────────────────────────────
class TestCleanReconciliation:
    def test_matching_is_clean(self):
        internal, broker = _matching_positions()
        r = PositionReconciler().reconcile(internal, broker)
        assert r.is_clean
        assert r.n_discrepancies == 0

    def test_matched_symbols(self):
        internal, broker = _matching_positions()
        r = PositionReconciler().reconcile(internal, broker)
        assert "SPY_P440" in r.matched_symbols
        assert "SPY_P435" in r.matched_symbols

    def test_counts(self):
        internal, broker = _matching_positions()
        r = PositionReconciler().reconcile(internal, broker)
        assert r.n_internal == 2
        assert r.n_broker == 2
        assert r.n_matched == 2

    def test_empty_both(self):
        r = PositionReconciler().reconcile([], [])
        assert r.is_clean
        assert r.n_matched == 0


# ── Missing fill ────────────────────────────────────────────────────────────
class TestMissingFill:
    def test_detected(self):
        internal = [_pos("SPY_P440", -5)]
        broker = []
        r = PositionReconciler().reconcile(internal, broker)
        assert not r.is_clean
        assert r.discrepancies[0].disc_type == "missing_fill"

    def test_minor_auto_corrected(self):
        internal = [_pos("SPY_P440", -1)]  # small qty
        broker = []
        r = PositionReconciler().reconcile(internal, broker)
        assert r.discrepancies[0].auto_corrected

    def test_major_flagged(self):
        internal = [_pos("SPY_P440", -10)]  # large qty > tolerance
        broker = []
        r = PositionReconciler().reconcile(internal, broker)
        assert not r.discrepancies[0].auto_corrected
        assert r.discrepancies[0].severity == "major"

    def test_detail_message(self):
        internal = [_pos("SPY_P440", -3)]
        r = PositionReconciler().reconcile(internal, [])
        assert "SPY_P440" in r.discrepancies[0].detail


# ── Phantom position ───────────────────────────────────────────────────────
class TestPhantomPosition:
    def test_detected(self):
        internal = []
        broker = [_pos("QQQ_C380", 3)]
        r = PositionReconciler().reconcile(internal, broker)
        assert r.discrepancies[0].disc_type == "phantom_position"

    def test_minor_auto_corrected(self):
        broker = [_pos("QQQ_C380", 1)]
        r = PositionReconciler().reconcile([], broker)
        assert r.discrepancies[0].auto_corrected

    def test_major_flagged(self):
        broker = [_pos("QQQ_C380", 20)]
        r = PositionReconciler().reconcile([], broker)
        assert not r.discrepancies[0].auto_corrected


# ── Quantity mismatch ──────────────────────────────────────────────────────
class TestQuantityMismatch:
    def test_detected(self):
        internal = [_pos("SPY_P440", -5)]
        broker = [_pos("SPY_P440", -8)]
        r = PositionReconciler().reconcile(internal, broker)
        assert r.discrepancies[0].disc_type == "quantity_mismatch"

    def test_minor_within_tolerance(self):
        cfg = ReconcilerConfig(qty_tolerance=3)
        internal = [_pos("SPY_P440", -5)]
        broker = [_pos("SPY_P440", -7)]  # diff=2, within tolerance=3
        r = PositionReconciler(cfg).reconcile(internal, broker)
        assert r.discrepancies[0].severity == "minor"
        assert r.discrepancies[0].auto_corrected

    def test_major_beyond_tolerance(self):
        cfg = ReconcilerConfig(qty_tolerance=2)
        internal = [_pos("SPY_P440", -5)]
        broker = [_pos("SPY_P440", -15)]  # diff=10, beyond tolerance
        r = PositionReconciler(cfg).reconcile(internal, broker)
        assert r.discrepancies[0].severity == "major"
        assert not r.discrepancies[0].auto_corrected


# ── Price drift ─────────────────────────────────────────────────────────────
class TestPriceDrift:
    def test_detected(self):
        internal = [_pos("SPY_P440", -5, 2.50)]
        broker = [_pos("SPY_P440", -5, 2.60)]  # 4% drift
        cfg = ReconcilerConfig(price_tolerance_pct=1.0)
        r = PositionReconciler(cfg).reconcile(internal, broker)
        assert r.discrepancies[0].disc_type == "price_drift"

    def test_within_tolerance_matches(self):
        internal = [_pos("SPY_P440", -5, 2.50)]
        broker = [_pos("SPY_P440", -5, 2.51)]  # 0.4% drift
        cfg = ReconcilerConfig(price_tolerance_pct=1.0)
        r = PositionReconciler(cfg).reconcile(internal, broker)
        assert r.is_clean

    def test_minor_drift_corrected(self):
        internal = [_pos("SPY_P440", -5, 2.50)]
        broker = [_pos("SPY_P440", -5, 2.55)]  # 2% drift, minor
        cfg = ReconcilerConfig(price_tolerance_pct=1.0)
        r = PositionReconciler(cfg).reconcile(internal, broker)
        if r.discrepancies:
            assert r.discrepancies[0].severity == "minor"


# ── Dry-run mode ────────────────────────────────────────────────────────────
class TestDryRun:
    def test_no_corrections_applied(self):
        cfg = ReconcilerConfig(dry_run=True)
        internal = [_pos("SPY_P440", -1)]
        r = PositionReconciler(cfg).reconcile(internal, [])
        assert r.dry_run
        # Even minor discrepancies should NOT be auto-corrected
        for d in r.discrepancies:
            assert not d.auto_corrected

    def test_override_per_call(self):
        cfg = ReconcilerConfig(dry_run=False)
        internal = [_pos("SPY_P440", -1)]
        r = PositionReconciler(cfg).reconcile(internal, [], dry_run=True)
        assert r.dry_run
        for d in r.discrepancies:
            assert not d.auto_corrected

    def test_report_still_generated(self):
        cfg = ReconcilerConfig(dry_run=True)
        internal = [_pos("SPY_P440", -5)]
        r = PositionReconciler(cfg).reconcile(internal, [])
        assert r.n_discrepancies == 1


# ── Apply corrections ──────────────────────────────────────────────────────
class TestApplyCorrections:
    def test_removes_missing_fill(self):
        rec = PositionReconciler()
        internal = [_pos("SPY_P440", -1), _pos("SPY_P435", 5, 1.2)]
        broker = [_pos("SPY_P435", 5, 1.2)]
        report = rec.reconcile(internal, broker)
        corrected = rec.apply_corrections(internal, report, broker)
        symbols = {p.symbol for p in corrected}
        assert "SPY_P440" not in symbols
        assert "SPY_P435" in symbols

    def test_adds_phantom(self):
        rec = PositionReconciler()
        internal = []
        broker = [_pos("QQQ_C380", 1, 3.0)]
        report = rec.reconcile(internal, broker)
        corrected = rec.apply_corrections(internal, report, broker)
        assert len(corrected) == 1
        assert corrected[0].symbol == "QQQ_C380"

    def test_adjusts_quantity(self):
        rec = PositionReconciler(ReconcilerConfig(qty_tolerance=3))
        internal = [_pos("SPY_P440", -5)]
        broker = [_pos("SPY_P440", -7)]
        report = rec.reconcile(internal, broker)
        corrected = rec.apply_corrections(internal, report, broker)
        spy = next(p for p in corrected if p.symbol == "SPY_P440")
        assert spy.quantity == -7

    def test_dry_run_no_mutation(self):
        rec = PositionReconciler(ReconcilerConfig(dry_run=True))
        internal = [_pos("SPY_P440", -1)]
        broker = []
        report = rec.reconcile(internal, broker)
        corrected = rec.apply_corrections(internal, report, broker)
        assert len(corrected) == 1  # not removed in dry run

    def test_original_not_mutated(self):
        rec = PositionReconciler()
        internal = [_pos("SPY_P440", -1)]
        broker = []
        report = rec.reconcile(internal, broker)
        original_len = len(internal)
        rec.apply_corrections(internal, report, broker)
        assert len(internal) == original_len  # original unchanged


# ── History ─────────────────────────────────────────────────────────────────
class TestHistory:
    def test_accumulates(self):
        rec = PositionReconciler()
        rec.reconcile([], [])
        rec.reconcile([_pos("A", 1)], [_pos("A", 1)])
        assert len(rec.history) == 2

    def test_independent_copies(self):
        rec = PositionReconciler()
        rec.reconcile([], [])
        h = rec.history
        rec.reconcile([], [])
        assert len(h) == 1  # original copy not affected


# ── Multi-position scenarios ───────────────────────────────────────────────
class TestMultiPosition:
    def test_mix_of_discrepancies(self):
        internal = [_pos("A", -5), _pos("B", 3), _pos("C", -10)]
        broker = [_pos("A", -5), _pos("D", 2)]
        r = PositionReconciler().reconcile(internal, broker)
        types = {d.disc_type for d in r.discrepancies}
        assert "missing_fill" in types     # B, C missing from broker
        assert "phantom_position" in types  # D not in internal
        assert "A" in r.matched_symbols

    def test_all_matched(self):
        positions = [_pos("A", -5), _pos("B", 3), _pos("C", -2)]
        r = PositionReconciler().reconcile(positions, positions)
        assert r.is_clean
        assert r.n_matched == 3


# ── HTML report ─────────────────────────────────────────────────────────────
class TestHTMLReport:
    def test_report_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = PositionReconciler()
            report = rec.reconcile([_pos("A", -5)], [_pos("A", -7)])
            path = rec.generate_report(report, str(Path(tmp) / "r.html"))
            assert path.exists()

    def test_report_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = PositionReconciler()
            report = rec.reconcile([_pos("A", -5)], [])
            path = rec.generate_report(report, str(Path(tmp) / "r.html"))
            html = path.read_text()
            assert "Reconciliation" in html
            assert "Discrepancies" in html
            assert "missing_fill" in html

    def test_clean_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = PositionReconciler()
            report = rec.reconcile([_pos("A", 5)], [_pos("A", 5)])
            path = rec.generate_report(report, str(Path(tmp) / "c.html"))
            html = path.read_text()
            assert "CLEAN" in html

    def test_valid_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = PositionReconciler()
            report = rec.reconcile([], [])
            path = rec.generate_report(report, str(Path(tmp) / "v.html"))
            html = path.read_text()
            assert html.startswith("<!DOCTYPE html>")
            assert "</html>" in html

    def test_dry_run_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = PositionReconciler(ReconcilerConfig(dry_run=True))
            report = rec.reconcile([_pos("A", -1)], [])
            path = rec.generate_report(report, str(Path(tmp) / "d.html"))
            html = path.read_text()
            assert "DRY RUN" in html


# ── Dataclasses ─────────────────────────────────────────────────────────────
class TestDataclasses:
    def test_position_record(self):
        p = PositionRecord("SPY", -5, 2.50)
        assert p.quantity == -5

    def test_discrepancy(self):
        d = Discrepancy("SPY", "missing_fill", "minor", -5, 0, 2.50, 0.0, "test")
        assert d.disc_type == "missing_fill"

    def test_report_defaults(self):
        r = ReconciliationReport(timestamp="now", n_internal=0, n_broker=0,
                                  n_matched=0, n_discrepancies=0,
                                  n_auto_corrected=0, n_flagged=0)
        assert r.is_clean

    def test_config_defaults(self):
        c = ReconcilerConfig()
        assert c.qty_tolerance == 2
        assert c.auto_correct is True
        assert c.dry_run is False
