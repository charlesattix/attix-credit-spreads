"""CI lint: prevent new ``import yfinance`` usages from creeping into the codebase.

D4 (BACKTEST_MIGRATION_PROPOSAL.md, Phases 6-10) migrated the backtest data
path off Yahoo Finance and onto Polygon via ``backtest.market_history``. The
``backtest/`` module is now yfinance-free and must stay that way.

The rest of the codebase (compass/, experiments/, many scripts/) still pulls
from Yahoo and is being migrated in subsequent task waves. Those files are
captured in ``ALLOWED_YFINANCE_IMPORTERS`` as a *transitional* allowlist:

* Any file that imports ``yfinance`` MUST be on the list.
* When a file is migrated, remove it from the list — the test then guards
  against a regression that would re-introduce the import.
* When a NEW file imports ``yfinance``, this test fails and the author must
  use ``backtest.market_history.load_market_history`` (or another Polygon
  shim) instead.

Tests under ``tests/`` are exempt: test fixtures may legitimately import
``yfinance`` to mock it.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files that are KNOWN to still import yfinance as of D4 close-out.
# These are scheduled for migration in follow-up task waves.
# DO NOT add new files to this list — migrate them instead.
ALLOWED_YFINANCE_IMPORTERS: frozenset[str] = frozenset({
    # compass/ — research workbench, separate migration wave
    "compass/archive/adaptive_1dte.py",
    "compass/archive/benchmark_tier1_features.py",
    "compass/archive/cadence_optimization.py",
    "compass/archive/crisis_alpha_production.py",
    "compass/archive/crisis_alpha_v2.py",
    "compass/archive/crypto_vol_strategy.py",
    "compass/archive/discovery_round3.py",
    "compass/archive/dispersion.py",
    "compass/archive/dispersion_strategy.py",
    "compass/archive/earnings_crush.py",
    "compass/archive/earnings_iv_crush.py",
    "compass/archive/exp1770_commodity_spreads.py",
    "compass/archive/exp1880_integrated_overlays.py",
    "compass/archive/exp1980_dynamic_hedge.py",
    "compass/archive/exp2000_triple_overlay.py",
    "compass/archive/exp2010_tail_convexity.py",
    "compass/archive/exp2060_cross_vol_arb_v2.py",
    "compass/archive/exp2070_term_structure.py",
    "compass/archive/exp2120_triple_overlay.py",
    "compass/archive/exp2500_true_net_backtest.py",
    "compass/archive/exp2550_net_sharpe_recovery.py",
    "compass/archive/exp2580_spy_weekly_cs.py",
    "compass/archive/exp2610_spy_weekly_integration.py",
    "compass/archive/exp2640_vix_stress_hardening.py",
    "compass/archive/exp2650_multi_expiry_capacity.py",
    "compass/archive/exp2660_aum_capacity_scaling.py",
    "compass/archive/exp2710_xle_integration.py",
    "compass/archive/exp2720_dd_recovery.py",
    "compass/archive/exp2820_flash_crash_protection.py",
    "compass/archive/exp2910_tlt_credit_spreads.py",
    "compass/archive/exp2920_tlt_ivrv_arb.py",
    "compass/archive/exp2950_sector_momentum.py",
    "compass/archive/hedge_cost_reality.py",
    "compass/archive/intraday_mr.py",
    "compass/archive/momentum_rotation.py",
    "compass/archive/multi_asset_portfolio_v2.py",
    "compass/archive/multi_strategy_portfolio.py",
    "compass/archive/new_strategy_explorer.py",
    "compass/archive/north_star_stress_test.py",
    "compass/archive/overnight_drift.py",
    "compass/archive/sector_pairs.py",
    "compass/archive/spy_only_portfolio.py",
    "compass/archive/strategy_discovery_r2.py",
    "compass/archive/strategy_discovery_r3.py",
    "compass/archive/strategy_discovery_r4.py",
    "compass/archive/trade_cadence_analyzer.py",
    "compass/archive/vol_term_structure_deep_dive.py",
    "compass/archive/zero_dte_ic.py",
    "compass/crisis_alpha.py",
    "compass/crisis_alpha_v3.py",
    "compass/exp1220_standalone.py",
    "compass/exp1660_vrp_deepening.py",
    "compass/exp1740_sentiment_filter.py",
    "compass/exp1750_putcall_overlay.py",
    "compass/exp1770_commodity_calendars.py",
    "compass/exp1960_skew_alpha.py",
    "compass/exp1970_vol_of_vol.py",
    "compass/exp2020_cross_vol_arb.py",
    "compass/exp2160_high_capacity_alts.py",
    "compass/exp2200_north_star_v6.py",
    "compass/exp2240_qqq_iwm_credit_spreads.py",
    "compass/exp2690_signal_generators.py",
    "compass/exp2830_paper_signal_generator.py",
    "compass/experiments/killed/exp1910_intraday_breakout.py",
    "compass/experiments/killed/exp1920_carry_trade.py",
    "compass/experiments/killed/exp1930_vvix_signal.py",
    "compass/experiments/killed/exp2030_seasonality_overlay.py",
    "compass/experiments/killed/exp2100_vf_true_integration.py",
    "compass/experiments/killed/exp2150_higher_frequency.py",
    "compass/experiments/killed/exp2250_north_star_v7.py",
    "compass/experiments/killed/exp2350_slv_replacement_v2.py",
    "compass/vix_ladder.py",
    # engine/, experiments/, scheduler/ — separate migration wave
    "engine/portfolio_backtester.py",
    "experiments/EXP-1270-real/backtest.py",
    "experiments/EXP-1320-real/backtest.py",
    "experiments/EXP-1650-max/backtest.py",
    "scheduler/data_providers.py",
    # scripts/ — research / experiment runners, separate migration wave
    "scripts/dynamic_leverage_audit.py",
    "scripts/exp600_trade_flow_debug.py",
    "scripts/exp700_yearly_walkforward.py",
    "scripts/harden_exp1710.py",
    "scripts/live_readiness_check.py",
    "scripts/paper_trading_deviation.py",
    "scripts/retrain_exp700_20260401.py",
    "scripts/run_exp1880_backtest.py",
    "scripts/safe_kelly_backtest.py",
    "scripts/validate_signal_alignment.py",
    # shared/, strategy/ — separate migration wave
    "shared/earnings_calendar.py",
    "strategy/options_analyzer.py",
})

# Directories whose .py files are exempt from the lint entirely.
EXEMPT_DIRS = ("tests/",)

# Match `import yfinance`, `from yfinance ...`, `import yfinance as yf` —
# leading whitespace permitted (conditional imports inside functions count too).
_YF_IMPORT_RE = re.compile(r"^\s*(?:import\s+yfinance|from\s+yfinance\b)", re.MULTILINE)


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(EXEMPT_DIRS):
            continue
        if "__pycache__" in rel:
            continue
        files.append(p)
    return files


def test_no_new_yfinance_imports():
    """Every file importing yfinance must be on ``ALLOWED_YFINANCE_IMPORTERS``.

    New imports → fail. Stale allowlist entries (file migrated or deleted) → fail.
    """
    offenders: list[str] = []
    importers: set[str] = set()
    for path in _iter_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _YF_IMPORT_RE.search(text):
            rel = path.relative_to(ROOT).as_posix()
            importers.add(rel)
            if rel not in ALLOWED_YFINANCE_IMPORTERS:
                offenders.append(rel)

    stale = sorted(ALLOWED_YFINANCE_IMPORTERS - importers)

    msg_parts: list[str] = []
    if offenders:
        msg_parts.append(
            "NEW yfinance imports detected (not on allowlist). "
            "Use backtest.market_history.load_market_history instead:\n  "
            + "\n  ".join(sorted(offenders))
        )
    if stale:
        msg_parts.append(
            "Stale entries on ALLOWED_YFINANCE_IMPORTERS (file no longer imports "
            "yfinance — remove from allowlist):\n  " + "\n  ".join(stale)
        )
    assert not msg_parts, "\n\n".join(msg_parts)


def test_backtest_module_is_yfinance_free():
    """``backtest/`` is the D4 acceptance boundary — zero yfinance imports."""
    backtest_dir = ROOT / "backtest"
    offenders: list[str] = []
    for path in backtest_dir.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _YF_IMPORT_RE.search(text):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, (
        "backtest/ regressed — yfinance imports re-introduced:\n  "
        + "\n  ".join(offenders)
    )
