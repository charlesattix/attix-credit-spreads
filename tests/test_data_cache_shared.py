"""Integration tests for DataCache <-> shared cache wiring (USE_SHARED_CACHE).

Verifies:
  * flag OFF  -> identical pre-Phase-1 behaviour (direct provider fetch)
  * flag ON + FRESH  -> provider NOT called, cached frame served
  * flag ON + STALE  -> stale frame served immediately + background revalidate
  * flag ON + MISS   -> provider called once, written through
  * shared cache error -> graceful fallback to provider (no crash)
"""
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from shared.data_cache import DataCache, _env_flag
from shared.shared_bar_cache import BarResult, Freshness, SharedCacheError


def _make_price_df(periods=600, seed=42):
    np.random.seed(seed)
    idx = pd.date_range("2024-01-02", periods=periods, freq="B")
    idx.name = "Date"
    close = 450.0 + np.cumsum(np.random.randn(periods) * 2)
    return pd.DataFrame(
        {
            "Open": close - 0.5, "High": close + 1.0, "Low": close - 1.0,
            "Close": close, "Volume": np.random.randint(1e6, 5e6, periods).astype(float),
        },
        index=idx, columns=["Open", "High", "Low", "Close", "Volume"],
    )


def _provider_returning(df):
    prov = MagicMock()
    prov.get_historical.return_value = df
    return prov


class FakeSharedCache:
    """Controllable stand-in for SharedBarCache.

    ``result`` -> fixed BarResult for every get_bars; ``results`` -> a sequence
    consumed per call (last repeats). ``lock_acquirable`` controls whether this
    "process" wins the cross-process fetch lock.
    """

    def __init__(self, result=None, results=None, raise_on_get=False, lock_acquirable=True):
        self._result = result
        self._results = list(results) if results else None
        self._raise_on_get = raise_on_get
        self._lock_acquirable = lock_acquirable
        self.put_calls = []
        self.put_event = threading.Event()
        self.get_calls = 0
        self.acquire_calls = 0
        self.release_calls = 0

    def get_bars(self, ticker):
        self.get_calls += 1
        if self._raise_on_get:
            raise SharedCacheError("boom")
        if self._results is not None:
            return self._results[min(self.get_calls - 1, len(self._results) - 1)]
        return self._result if self._result is not None else BarResult(Freshness.MISS, None, None, None)

    def put_bars(self, ticker, df, fetch_ts=None):
        self.put_calls.append(ticker)
        self.put_event.set()

    def try_acquire_fetch_lock(self, key, ttl=30.0):
        self.acquire_calls += 1
        return self._lock_acquirable

    def release_fetch_lock(self, key):
        self.release_calls += 1


# ---------------------------------------------------------------------------
# flag parsing
# ---------------------------------------------------------------------------

def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("USE_SHARED_CACHE", raising=False)
    assert _env_flag("USE_SHARED_CACHE") is False
    assert DataCache()._use_shared is False


@pytest.mark.parametrize("val,expected", [("true", True), ("1", True), ("on", True),
                                          ("false", False), ("0", False), ("", False)])
def test_flag_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("USE_SHARED_CACHE", val)
    assert _env_flag("USE_SHARED_CACHE") is expected


# ---------------------------------------------------------------------------
# flag OFF -> unchanged behaviour
# ---------------------------------------------------------------------------

def test_flag_off_uses_direct_provider(monkeypatch):
    monkeypatch.delenv("USE_SHARED_CACHE", raising=False)
    df = _make_price_df()
    dc = DataCache(api_key="x")
    dc._provider = _provider_returning(df)
    # A shared cache is injected but must be ignored when the flag is off.
    sentinel = FakeSharedCache(BarResult(Freshness.FRESH, df, 1.0, time.time()))
    dc._shared_cache = sentinel

    out = dc.get_history("SPY", period="1y")
    assert len(out) == 252
    dc._provider.get_historical.assert_called_once()
    assert sentinel.get_calls == 0  # shared cache never consulted


# ---------------------------------------------------------------------------
# flag ON
# ---------------------------------------------------------------------------

def _enabled_dc(shared, provider_df=None):
    dc = DataCache(api_key="x", shared_cache=shared)
    dc._use_shared = True
    dc._provider = _provider_returning(provider_df if provider_df is not None else _make_price_df())
    dc._indices_provider = dc._provider
    return dc


def test_fresh_serves_cache_without_provider():
    df = _make_price_df()
    shared = FakeSharedCache(BarResult(Freshness.FRESH, df, 10.0, time.time()))
    dc = _enabled_dc(shared)
    out = dc.get_history("SPY", period="1y")
    assert len(out) == 252
    dc._provider.get_historical.assert_not_called()  # served from shared cache
    assert shared.put_calls == []


def test_miss_fetches_and_writes_through():
    df = _make_price_df()
    shared = FakeSharedCache(BarResult(Freshness.MISS, None, None, None))
    dc = _enabled_dc(shared, provider_df=df)
    out = dc.get_history("SPY", period="1y")
    assert len(out) == 252
    dc._provider.get_historical.assert_called_once()
    assert shared.put_calls == ["SPY"]  # write-through happened


def test_stale_serves_immediately_and_refreshes_in_background():
    stale_df = _make_price_df(seed=1)
    fresh_df = _make_price_df(seed=2)
    shared = FakeSharedCache(BarResult(Freshness.STALE, stale_df, 1200.0, time.time() - 1200))
    dc = _enabled_dc(shared, provider_df=fresh_df)

    out = dc.get_history("SPY", period="1y")
    assert len(out) == 252  # stale data returned immediately

    # Background thread should fetch + write-through shortly after.
    assert shared.put_event.wait(timeout=5.0), "background refresh did not run"
    assert shared.put_calls == ["SPY"]
    dc._provider.get_historical.assert_called()


def test_shared_read_error_falls_back_to_provider():
    df = _make_price_df()
    shared = FakeSharedCache(raise_on_get=True)
    dc = _enabled_dc(shared, provider_df=df)
    out = dc.get_history("SPY", period="1y")  # must NOT raise
    assert len(out) == 252
    dc._provider.get_historical.assert_called_once()  # fell back to direct fetch


def test_in_memory_l1_still_short_circuits_shared():
    df = _make_price_df()
    shared = FakeSharedCache(BarResult(Freshness.FRESH, df, 5.0, time.time()))
    dc = _enabled_dc(shared)
    dc.get_history("SPY", period="1y")   # 1st: hits shared
    dc.get_history("SPY", period="6mo")  # 2nd: should hit in-memory L1, not shared again
    assert shared.get_calls == 1


# ---------------------------------------------------------------------------
# cross-process single-flight (Fix 1) — orchestration in DataCache
# ---------------------------------------------------------------------------

def test_miss_winner_acquires_and_releases_lock():
    df = _make_price_df()
    shared = FakeSharedCache(BarResult(Freshness.MISS, None, None, None), lock_acquirable=True)
    dc = _enabled_dc(shared, provider_df=df)
    dc.get_history("SPY", period="1y")
    assert shared.acquire_calls == 1
    assert shared.release_calls == 1            # lock always released
    dc._provider.get_historical.assert_called_once()


def test_winner_double_check_skips_fetch_if_peer_populated():
    """Won the lock, but a peer wrote the data first -> serve cached, no fetch."""
    df = _make_price_df()
    # call 1 (classify) = MISS; call 2 (double-check after acquire) = FRESH
    shared = FakeSharedCache(
        results=[BarResult(Freshness.MISS, None, None, None),
                 BarResult(Freshness.FRESH, df, 1.0, time.time())],
        lock_acquirable=True,
    )
    dc = _enabled_dc(shared, provider_df=df)
    out = dc.get_history("SPY", period="1y")
    assert len(out) == 252
    dc._provider.get_historical.assert_not_called()   # double-check avoided redundant fetch
    assert shared.put_calls == []
    assert shared.release_calls == 1


def test_loser_waits_then_reads_peer_result_without_fetching():
    df = _make_price_df()
    # classify=MISS, then peer's write appears as FRESH on the next read.
    shared = FakeSharedCache(
        results=[BarResult(Freshness.MISS, None, None, None),
                 BarResult(Freshness.FRESH, df, 1.0, time.time())],
        lock_acquirable=False,   # we are the loser
    )
    dc = _enabled_dc(shared, provider_df=df)
    out = dc.get_history("SPY", period="1y")
    assert len(out) == 252
    dc._provider.get_historical.assert_not_called()   # got peer's result, no own fetch
    assert shared.release_calls == 0                  # loser never holds the lock


def test_loser_times_out_then_falls_back_to_direct_fetch():
    df = _make_price_df()
    shared = FakeSharedCache(BarResult(Freshness.MISS, None, None, None), lock_acquirable=False)
    dc = _enabled_dc(shared, provider_df=df)
    dc._shared_wait_secs = 0.4   # keep the test fast
    out = dc.get_history("SPY", period="1y")
    assert len(out) == 252
    dc._provider.get_historical.assert_called_once()  # never blocked the scan


# ---------------------------------------------------------------------------
# Phase 2a — experiment-tagged cache observability
# ---------------------------------------------------------------------------

def test_cache_log_l1_hit_and_direct_fetch(caplog):
    import logging
    df = _make_price_df()
    dc = DataCache(api_key="x")            # flag off
    dc._provider = _provider_returning(df)
    with caplog.at_level(logging.INFO, logger="shared.data_cache"):
        dc.get_history("SPY", period="1y")    # miss -> direct_fetch
        dc.get_history("SPY", period="6mo")   # in-memory L1 hit
    msgs = "\n".join(caplog.messages)
    assert "outcome=direct_fetch" in msgs
    assert "outcome=l1_hit" in msgs
    assert "ticker=SPY" in msgs


def test_cache_log_tags_experiment_id(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("EXPERIMENT_ID", "EXP-3309")
    dc = DataCache(api_key="x")
    dc._provider = _provider_returning(_make_price_df())
    with caplog.at_level(logging.INFO, logger="shared.data_cache"):
        dc.get_history("SPY", period="1y")
    assert "exp=EXP-3309" in "\n".join(caplog.messages)


def test_cache_log_shared_fresh(caplog):
    import logging
    df = _make_price_df()
    shared = FakeSharedCache(BarResult(Freshness.FRESH, df, 5.0, time.time()))
    dc = _enabled_dc(shared)
    with caplog.at_level(logging.INFO, logger="shared.data_cache"):
        dc.get_history("SPY", period="1y")
    assert "outcome=shared_fresh" in "\n".join(caplog.messages)
