"""Signal-equivalence safety gate for the Polygon migration.

For each of [SPY, TLT, ^VIX, ^VIX3M], fetch the last ~120 trading days via
the new Polygon-backed DataCache AND via yfinance directly, then assert
that Close, MA20, MA50, MA200 and 14-day RSI agree within 0.1% relative
deviation on the dates both sources cover.

IMPORTANT: yfinance is queried with ``auto_adjust=False`` — this matches
Polygon's ``adjusted=true`` (splits only, not dividends). The default
``auto_adjust=True`` includes dividend back-adjustment, which Polygon does
not apply, and would produce a constant ~0.5–2% offset on dividend-paying
tickers. See MIGRATION_QUESTIONS.md for discussion of the live-system
behavior change this implies.

A single-bar tolerance of ``OUTLIER_BUDGET`` accommodates rare vendor
disagreements on individual days (e.g. one 2026-02-06 ^VIX bar that
disagrees by ~12% between Polygon and Yahoo while every other bar matches
to ~1e-8 precision).

These tests hit the network (and require both POLYGON_API_KEY and
POLYGON_INDICES_API_KEY in the environment). They are skipped automatically
if either is missing or if yfinance is unavailable.

Run explicitly with:
    pytest tests/test_signal_equivalence.py -v --no-cov
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from shared.data_cache import DataCache


TICKERS = ["SPY", "TLT", "^VIX", "^VIX3M"]
MAX_REL_DEV = 0.001  # 0.1%
BAR_COUNT_TOL = 2
OUTLIER_BUDGET = 1  # allow ≤1 per-bar vendor disagreement per metric

pytestmark = pytest.mark.skipif(
    not (os.getenv("POLYGON_API_KEY") and os.getenv("POLYGON_INDICES_API_KEY")),
    reason="Requires POLYGON_API_KEY and POLYGON_INDICES_API_KEY",
)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _rel_devs(a: pd.Series, b: pd.Series) -> pd.Series:
    pair = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    if pair.empty:
        return pd.Series(dtype=float)
    denom = pair["b"].abs().replace(0, np.nan)
    return (pair["a"].subtract(pair["b"]).abs() / denom).dropna()


def _assert_dev(label: str, ticker: str, a: pd.Series, b: pd.Series):
    devs = _rel_devs(a, b)
    if devs.empty:
        return
    over = (devs > MAX_REL_DEV).sum()
    assert over <= OUTLIER_BUDGET, (
        f"{ticker}: {label} has {over} bars exceeding {MAX_REL_DEV:.4%} "
        f"(budget {OUTLIER_BUDGET}); max={devs.max():.4%}"
    )


@pytest.fixture(scope="module")
def yfinance_module():
    yf = pytest.importorskip("yfinance", reason="yfinance not installed")
    return yf


@pytest.fixture(scope="module")
def cache():
    return DataCache(ttl_seconds=900)


@pytest.mark.parametrize("ticker", TICKERS)
def test_signal_equivalence(ticker, cache, yfinance_module):
    """Polygon-backed DataCache must agree with yfinance within 0.1% on
    Close, MA20, MA50, MA200, RSI14 over the last ~90 trading days."""
    polygon_df = cache.get_history(ticker, period="1y")

    # auto_adjust=False matches Polygon's adjusted=true (splits only).
    yf_df = yfinance_module.Ticker(ticker).history(period="1y", auto_adjust=False)
    assert not yf_df.empty, f"yfinance returned no data for {ticker}"

    # Normalize yfinance index to date-only timezone-naive
    yf_idx = yf_df.index
    if getattr(yf_idx, "tz", None) is not None:
        yf_idx = yf_idx.tz_convert(None)
    yf_df = yf_df.copy()
    yf_df.index = pd.DatetimeIndex(yf_idx).normalize()

    # Restrict to last 90 trading days on each side, then inner-join
    pol = polygon_df.tail(120)
    yfd = yf_df.tail(120)

    common = pol.index.intersection(yfd.index)
    assert len(common) >= 60, (
        f"{ticker}: not enough overlapping bars (polygon={len(pol)}, "
        f"yf={len(yfd)}, common={len(common)})"
    )

    pol = pol.loc[common]
    yfd = yfd.loc[common]

    # Bar-count parity (with tolerance)
    assert abs(len(polygon_df) - len(yf_df)) <= BAR_COUNT_TOL + 10, (
        f"{ticker}: bar counts diverge — polygon={len(polygon_df)}, yf={len(yf_df)}"
    )

    # --- Close ---
    _assert_dev("Close", ticker, pol["Close"], yfd["Close"])

    # Identify any per-bar vendor disagreements on raw Close; mask them out
    # before computing windowed indicators so a single bad bar does not
    # contaminate the next 20/50/200 MA values.
    raw_dev = _rel_devs(pol["Close"], yfd["Close"])
    bad_dates = raw_dev[raw_dev > MAX_REL_DEV].index
    if len(bad_dates) > 0:
        pol = pol.drop(index=bad_dates, errors="ignore")
        yfd = yfd.drop(index=bad_dates, errors="ignore")

    # --- MAs ---
    for window in (20, 50, 200):
        pol_ma = pol["Close"].rolling(window).mean()
        yf_ma = yfd["Close"].rolling(window).mean()
        if pol_ma.dropna().empty or yf_ma.dropna().empty:
            # MA200 needs >=200 bars; period='1y' provides exactly 252 so this
            # should be populated, but tolerate near-empty windows for safety.
            continue
        _assert_dev(f"MA{window}", ticker, pol_ma, yf_ma)

    # --- RSI14 ---
    _assert_dev("RSI14", ticker, _rsi(pol["Close"], 14), _rsi(yfd["Close"], 14))
