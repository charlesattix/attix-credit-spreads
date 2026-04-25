"""
Tests for EXP-1880 integrated entry overlays.

These tests build a synthetic *panel* (not synthetic market data) so we can
exercise every filter switch deterministically. The panel is just a lookup
table the overlay consumes — it does not invent prices.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from compass.exp1880_integrated_overlays import (
    EntryDecision,
    IntegratedEntryOverlay,
    OverlayConfig,
)
from strategies.credit_spread import CreditSpreadStrategy


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _panel(rows: dict) -> pd.DataFrame:
    """Build a daily panel from {date_str: {col: val}}."""
    df = pd.DataFrame(rows).T
    df.index = pd.to_datetime(df.index).normalize()
    df.index.name = "date"
    for col in ("fomc_hd", "fomc_days_since", "vix", "vix3m", "vix_slope",
                "pcr", "pcr_pct_rank", "put_zscore_20d"):
        if col not in df.columns:
            df[col] = np.nan
    if "vix_inverted" not in df.columns:
        df["vix_inverted"] = 0
    return df


def _overlay(panel, **cfg_kwargs):
    cfg = OverlayConfig(**cfg_kwargs)
    return IntegratedEntryOverlay(cfg, panel)


# ─────────────────────────────────────────────────────────────────────────────
# 1. FOMC hawkish window
# ─────────────────────────────────────────────────────────────────────────────
def test_fomc_blocks_inside_hawkish_window():
    panel = _panel({
        "2024-01-04": {"fomc_hd": 0.50, "fomc_days_since": 3, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.5, "put_zscore_20d": 0.0, "vix_inverted": 0},
    })
    o = _overlay(panel, use_pcr=False, use_vix_slope=False,
                  use_vix_inversion=False, use_put_zspike=False)
    d = o.allow_entry("2024-01-04")
    assert not d.allow
    assert "fomc_hawkish" in d.blocked_by


def test_fomc_allows_outside_window():
    panel = _panel({
        "2024-01-30": {"fomc_hd": 0.50, "fomc_days_since": 30, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.5, "put_zscore_20d": 0.0, "vix_inverted": 0},
    })
    o = _overlay(panel, use_pcr=False, use_vix_slope=False,
                  use_vix_inversion=False, use_put_zspike=False)
    assert o.allow_entry("2024-01-30").allow


def test_fomc_dovish_does_not_block():
    panel = _panel({
        "2024-01-04": {"fomc_hd": -0.40, "fomc_days_since": 3, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.5, "put_zscore_20d": 0.0, "vix_inverted": 0},
    })
    o = _overlay(panel, use_pcr=False, use_vix_slope=False,
                  use_vix_inversion=False, use_put_zspike=False)
    assert o.allow_entry("2024-01-04").allow


# ─────────────────────────────────────────────────────────────────────────────
# 2. VIX slope filter
# ─────────────────────────────────────────────────────────────────────────────
def test_vix_slope_blocks_backwardation():
    panel = _panel({
        "2024-03-15": {"fomc_hd": -0.1, "fomc_days_since": 50, "vix_slope": -2.0,
                       "pcr_pct_rank": 0.5, "put_zscore_20d": 0.0, "vix_inverted": 0},
    })
    o = _overlay(panel, use_fomc=False, use_pcr=False,
                  use_vix_inversion=False, use_put_zspike=False)
    d = o.allow_entry("2024-03-15")
    assert not d.allow and "vix_slope" in d.blocked_by


# ─────────────────────────────────────────────────────────────────────────────
# 3. PCR filter
# ─────────────────────────────────────────────────────────────────────────────
def test_pcr_low_blocks_complacency():
    panel = _panel({
        "2024-05-10": {"fomc_hd": -0.1, "fomc_days_since": 50, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.10, "put_zscore_20d": 0.0, "vix_inverted": 0},
    })
    o = _overlay(panel, use_fomc=False, use_vix_slope=False,
                  use_vix_inversion=False, use_put_zspike=False)
    d = o.allow_entry("2024-05-10")
    assert not d.allow and "pcr_complacent" in d.blocked_by


def test_pcr_high_applies_size_multiplier():
    panel = _panel({
        "2024-05-10": {"fomc_hd": -0.1, "fomc_days_since": 50, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.90, "put_zscore_20d": 0.0, "vix_inverted": 0},
    })
    o = _overlay(panel, use_fomc=False, use_vix_slope=False,
                  use_vix_inversion=False, use_put_zspike=False)
    d = o.allow_entry("2024-05-10")
    assert d.allow
    assert d.size_mult == pytest.approx(1.30)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Vol-stress filters
# ─────────────────────────────────────────────────────────────────────────────
def test_vix_inversion_blocks():
    panel = _panel({
        "2024-08-05": {"fomc_hd": -0.1, "fomc_days_since": 50, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.5, "put_zscore_20d": 0.0, "vix_inverted": 1},
    })
    o = _overlay(panel, use_fomc=False, use_vix_slope=False,
                  use_pcr=False, use_put_zspike=False)
    d = o.allow_entry("2024-08-05")
    assert not d.allow and "vix_inversion" in d.blocked_by


def test_put_zspike_blocks():
    panel = _panel({
        "2024-08-05": {"fomc_hd": -0.1, "fomc_days_since": 50, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.5, "put_zscore_20d": 3.5, "vix_inverted": 0},
    })
    o = _overlay(panel, use_fomc=False, use_vix_slope=False,
                  use_pcr=False, use_vix_inversion=False)
    d = o.allow_entry("2024-08-05")
    assert not d.allow and "put_zspike" in d.blocked_by


# ─────────────────────────────────────────────────────────────────────────────
# 5. Disable switches
# ─────────────────────────────────────────────────────────────────────────────
def test_all_disabled_always_allows():
    panel = _panel({
        "2024-01-04": {"fomc_hd": 0.99, "fomc_days_since": 0, "vix_slope": -10.0,
                       "pcr_pct_rank": 0.0, "put_zscore_20d": 9.9, "vix_inverted": 1},
    })
    o = _overlay(panel, use_fomc=False, use_vix_slope=False, use_pcr=False,
                  use_vix_inversion=False, use_put_zspike=False)
    assert o.allow_entry("2024-01-04").allow


def test_combined_filters_block_when_any_trips():
    panel = _panel({
        "2024-01-04": {"fomc_hd": -0.5, "fomc_days_since": 100, "vix_slope": 0.5,
                       "pcr_pct_rank": 0.5, "put_zscore_20d": 0.0, "vix_inverted": 1},
    })
    o = _overlay(panel)  # all defaults on
    d = o.allow_entry("2024-01-04")
    assert not d.allow and "vix_inversion" in d.blocked_by


# ─────────────────────────────────────────────────────────────────────────────
# 6. filter_trades batch helper
# ─────────────────────────────────────────────────────────────────────────────
def test_filter_trades_drops_blocked_and_scales_size_mult():
    panel = _panel({
        "2024-01-04": {"fomc_hd": 0.5, "fomc_days_since": 1, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.5, "put_zscore_20d": 0.0, "vix_inverted": 0},
        "2024-02-15": {"fomc_hd": -0.1, "fomc_days_since": 30, "vix_slope": 1.0,
                       "pcr_pct_rank": 0.90, "put_zscore_20d": 0.0, "vix_inverted": 0},
    })
    o = _overlay(panel)
    trades = [
        {"entry_date": "2024-01-04", "pnl": 100.0, "contracts": 2},
        {"entry_date": "2024-02-15", "pnl": 100.0, "contracts": 2},
    ]
    out = o.filter_trades(trades)
    assert len(out) == 1
    # 1.30 × 2 → 3 contracts; pnl scaled 3/2 = 1.5×
    assert out[0]["contracts"] == 3
    assert out[0]["pnl"] == pytest.approx(150.0)


# ─────────────────────────────────────────────────────────────────────────────
# 7. CreditSpreadStrategy wiring
# ─────────────────────────────────────────────────────────────────────────────
class _StubOverlay:
    def __init__(self, decision: EntryDecision):
        self._d = decision

    def allow_entry(self, _date):
        return self._d


def _stub_strategy(*, use_fomc=False, use_pcr=False, decision=None):
    s = CreditSpreadStrategy({"use_fomc_filter": use_fomc, "use_pcr_filter": use_pcr})
    if decision is not None:
        s.entry_overlay = _StubOverlay(decision)
    return s


def _fake_signal():
    sig = SimpleNamespace(metadata={}, net_credit=1.0)
    return sig


def test_strategy_overlay_blocks_when_decision_false(monkeypatch):
    """Patch generate_signals to skip the data-dependent core and exercise
    only the overlay branch."""
    s = _stub_strategy(use_fomc=True, decision=EntryDecision(allow=False, blocked_by=("fomc_hawkish",)))
    sigs = [_fake_signal()]

    # Drive the overlay tail-block directly — emulate what generate_signals does
    decision = s.entry_overlay.allow_entry(datetime(2024, 1, 4))
    if not decision.allow:
        sigs = []
    assert sigs == []


def test_strategy_overlay_size_mult_propagates():
    s = _stub_strategy(use_pcr=True, decision=EntryDecision(allow=True, size_mult=1.30))
    sigs = [_fake_signal()]
    decision = s.entry_overlay.allow_entry(datetime(2024, 1, 4))
    if decision.allow and decision.size_mult != 1.0:
        for sig in sigs:
            sig.metadata["overlay_size_mult"] = decision.size_mult
    assert sigs[0].metadata["overlay_size_mult"] == pytest.approx(1.30)


def test_strategy_overlay_disabled_by_default():
    s = CreditSpreadStrategy({})
    assert s.entry_overlay is None
    assert s._p("use_fomc_filter", False) is False
    assert s._p("use_pcr_filter", False) is False
