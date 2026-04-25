"""Tests for compass/honest_dashboard.py."""

import pytest
from compass.honest_dashboard import generate_dashboard


class TestDashboard:
    def test_generates_html(self, tmp_path):
        out = tmp_path / "dash.html"
        p = generate_dashboard(str(out))
        assert out.exists()
        c = out.read_text()
        assert "<!DOCTYPE html>" in c

    def test_contains_north_star(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        c = out.read_text()
        assert "North Star" in c
        assert "100%" in c  # CAGR target
        assert "6.0" in c   # Sharpe target

    def test_contains_real_numbers(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        c = out.read_text()
        assert "3.4%" in c       # real CAGR
        assert "3.12" in c       # real Sharpe
        assert "91%" in c        # real win rate
        assert "287" in c        # real trade count

    def test_contains_hedge_cost(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        c = out.read_text()
        assert "4.36%" in c      # real hedge cost
        assert "2.2x" in c       # ratio vs assumed

    def test_contains_data_gaps(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        c = out.read_text()
        assert "GLD" in c
        assert "QQQ" in c
        assert "GAP" in c

    def test_contains_path_forward(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        c = out.read_text()
        assert "Phase 1" in c
        assert "Phase 2" in c
        assert "Phase 3" in c

    def test_contains_bottom_line(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        assert "Bottom Line" in out.read_text()

    def test_white_background(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        assert "background: #fff" in out.read_text()

    def test_contains_inflated_section(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        c = out.read_text()
        assert "Inflated" in c
        assert "77%" in c        # the inflated claim
        assert "synthetic" in c.lower() or "Synthetic" in c

    def test_contains_cadence(self, tmp_path):
        out = tmp_path / "d.html"
        generate_dashboard(str(out))
        c = out.read_text()
        assert "7d" in c
        assert "$21,312" in c
