"""Tests for the shared on-disk SQLite bar cache (shared/shared_bar_cache.py)."""
import multiprocessing as mp
import os
import sqlite3
import threading

import numpy as np
import pandas as pd
import pytest

from shared.shared_bar_cache import (
    Freshness,
    SharedBarCache,
    SharedCacheError,
    default_db_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(periods=30, seed=1):
    np.random.seed(seed)
    idx = pd.date_range("2024-01-02", periods=periods, freq="B")
    idx.name = "Date"
    close = 450.0 + np.cumsum(np.random.randn(periods))
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.random.randint(1_000_000, 5_000_000, periods).astype(float),
        },
        index=idx,
        columns=["Open", "High", "Low", "Close", "Volume"],
    )


@pytest.fixture
def cache(tmp_path):
    return SharedBarCache(db_path=str(tmp_path / "bars.db"), fresh_ttl=900, max_stale=3 * 86_400)


# ---------------------------------------------------------------------------
# read / write round-trip
# ---------------------------------------------------------------------------

def test_put_then_get_roundtrip(cache):
    df = _make_df()
    cache.put_bars("SPY", df)
    res = cache.get_bars("SPY")
    assert res.status == Freshness.FRESH
    assert len(res.df) == len(df)
    # OHLCV values survive the round-trip
    pd.testing.assert_series_equal(
        res.df["Close"].reset_index(drop=True),
        df["Close"].reset_index(drop=True),
        check_names=False,
    )
    assert list(res.df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert res.df.index.name == "Date"


def test_ticker_is_case_insensitive(cache):
    cache.put_bars("spy", _make_df())
    assert cache.get_bars("SPY").status == Freshness.FRESH


def test_put_is_idempotent_upsert(cache):
    df = _make_df(periods=20)
    cache.put_bars("TLT", df)
    cache.put_bars("TLT", df)  # second write must not duplicate rows
    res = cache.get_bars("TLT")
    assert len(res.df) == 20


def test_put_updates_existing_and_appends(cache):
    cache.put_bars("SPY", _make_df(periods=10))
    bigger = _make_df(periods=15)
    cache.put_bars("SPY", bigger)
    assert len(cache.get_bars("SPY").df) == 15


def test_empty_frame_is_noop(cache):
    cache.put_bars("SPY", pd.DataFrame())
    assert cache.get_bars("SPY").status == Freshness.MISS


# ---------------------------------------------------------------------------
# freshness / TTL
# ---------------------------------------------------------------------------

def test_miss_when_absent(cache):
    res = cache.get_bars("NOPE")
    assert res.status == Freshness.MISS
    assert res.df is None


def test_fresh_within_ttl(cache):
    cache.put_bars("SPY", _make_df())  # fetch_ts = now
    assert cache.get_bars("SPY").status == Freshness.FRESH


def test_stale_past_ttl(cache):
    import time
    # Write with a fetch timestamp older than fresh_ttl but within max_stale.
    cache.put_bars("SPY", _make_df(), fetch_ts=time.time() - 1000)  # fresh_ttl=900
    res = cache.get_bars("SPY")
    assert res.status == Freshness.STALE
    assert res.df is not None and not res.df.empty  # stale data is still served
    assert res.age > 900


def test_miss_when_older_than_max_stale(cache):
    import time
    cache.put_bars("SPY", _make_df(), fetch_ts=time.time() - (4 * 86_400))  # > max_stale
    res = cache.get_bars("SPY")
    assert res.status == Freshness.MISS
    assert res.df is None
    assert res.age is not None  # we still know how old it was


# ---------------------------------------------------------------------------
# schema / migration
# ---------------------------------------------------------------------------

def test_schema_initialised(tmp_path):
    p = str(tmp_path / "s.db")
    SharedBarCache(db_path=p)
    conn = sqlite3.connect(p)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"daily_bars", "bar_meta", "cache_schema"} <= names
    assert conn.execute("SELECT version FROM cache_schema").fetchone()[0] == 1
    conn.close()


def test_reopen_preserves_data(tmp_path):
    p = str(tmp_path / "s.db")
    c1 = SharedBarCache(db_path=p)
    c1.put_bars("SPY", _make_df(periods=12))
    c1.close()
    c2 = SharedBarCache(db_path=p)  # re-open = migration check path, no data loss
    assert len(c2.get_bars("SPY").df) == 12


def test_migration_from_empty_schema_table(tmp_path):
    """A db with cache_schema but no version row (current==0) gets fully built."""
    p = str(tmp_path / "s.db")
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE cache_schema (version INTEGER NOT NULL)")  # no row
    conn.commit()
    conn.close()
    c = SharedBarCache(db_path=p)  # must create daily_bars/bar_meta + version
    c.put_bars("SPY", _make_df(periods=5))
    assert len(c.get_bars("SPY").df) == 5


# ---------------------------------------------------------------------------
# corruption / fallback
# ---------------------------------------------------------------------------

def test_corrupt_db_raises_shared_cache_error(tmp_path):
    p = str(tmp_path / "corrupt.db")
    with open(p, "wb") as f:
        f.write(b"this is definitely not a sqlite database file " * 50)
    with pytest.raises(SharedCacheError):
        SharedBarCache(db_path=p)


def test_get_bars_raises_on_broken_table(tmp_path):
    """If the table is dropped underneath us, get_bars surfaces SharedCacheError."""
    p = str(tmp_path / "s.db")
    c = SharedBarCache(db_path=p)
    c.put_bars("SPY", _make_df())
    # Corrupt the schema out-of-band.
    conn = sqlite3.connect(p)
    conn.execute("DROP TABLE bar_meta")
    conn.commit()
    conn.close()
    c.close()  # drop cached connection so the next call reopens
    with pytest.raises(SharedCacheError):
        c.get_bars("SPY")


def test_healthy(tmp_path):
    c = SharedBarCache(db_path=str(tmp_path / "s.db"))
    assert c.healthy() is True


# ---------------------------------------------------------------------------
# concurrency (WAL)
# ---------------------------------------------------------------------------

def test_concurrent_readers_while_writing(tmp_path):
    p = str(tmp_path / "concurrent.db")
    writer = SharedBarCache(db_path=p)
    writer.put_bars("SPY", _make_df(periods=40))

    errors = []
    read_ok = []

    def reader():
        try:
            c = SharedBarCache(db_path=p)  # own connection (mimics a subprocess)
            for _ in range(15):
                res = c.get_bars("SPY")
                if res.status in (Freshness.FRESH, Freshness.STALE):
                    read_ok.append(len(res.df))
            c.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def writer_loop():
        try:
            c = SharedBarCache(db_path=p)
            for i in range(15):
                c.put_bars("SPY", _make_df(periods=40, seed=i))
            c.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(6)] + [
        threading.Thread(target=writer_loop)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent access raised: {errors}"
    assert read_ok and all(n == 40 for n in read_ok)


# ---------------------------------------------------------------------------
# advisory lock primitive
# ---------------------------------------------------------------------------

def test_lock_acquire_release_cycle(cache):
    assert cache.try_acquire_fetch_lock("SPY") is True
    assert cache.try_acquire_fetch_lock("SPY") is False     # already held
    assert cache.try_acquire_fetch_lock("spy") is False     # case-normalised
    cache.release_fetch_lock("SPY")
    assert cache.try_acquire_fetch_lock("SPY") is True       # reacquire after release


def test_lock_expired_is_reclaimed(cache):
    import time
    # Hand-insert an expired lock owned by a dead pid.
    conn = cache._conn()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO fetch_locks (lock_key, owner_pid, expires_at) VALUES (?,?,?)",
            ("SPY", 999999, time.time() - 1),
        )
    assert cache.try_acquire_fetch_lock("SPY") is True       # stale lock reclaimed


# ---------------------------------------------------------------------------
# cross-process single-flight (Fix 1) — real subprocesses
# ---------------------------------------------------------------------------

def _mp_fetch_worker(args):
    """Mimic DataCache's coordinated fetch in a separate process.

    Records one byte per *actual* underlying fetch so the parent can assert
    exactly one process fetched.
    """
    db_path, counter_path, key = args
    import time as _t

    import pandas as _pd

    from shared.shared_bar_cache import Freshness as Fresh
    from shared.shared_bar_cache import SharedBarCache as Cache

    c = Cache(db_path=db_path, fresh_ttl=900)
    # initial classify
    if c.get_bars(key).status in (Fresh.FRESH, Fresh.STALE):
        return "cache"
    if c.try_acquire_fetch_lock(key, ttl=30):
        try:
            if c.get_bars(key).status == Fresh.FRESH:   # peer populated first
                return "cache"
            fd = os.open(counter_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            os.write(fd, b"x")                        # one byte == one real fetch
            os.close(fd)
            _t.sleep(1.0)                             # simulate Polygon latency, hold lock
            idx = _pd.date_range("2024-01-02", periods=10, freq="B")
            idx.name = "Date"
            df = _pd.DataFrame(
                {"Open": 1.0, "High": 1.0, "Low": 1.0,
                 "Close": list(range(10)), "Volume": 1.0},
                index=idx, columns=["Open", "High", "Low", "Close", "Volume"],
            )
            c.put_bars(key, df)
            return "fetched"
        finally:
            c.release_fetch_lock(key)
    # loser: wait bounded, re-read
    deadline = _t.monotonic() + 8
    while _t.monotonic() < deadline:
        _t.sleep(0.1)
        if c.get_bars(key).status in (Fresh.FRESH, Fresh.STALE):
            return "cache"
    return "timeout"


def test_cross_process_single_flight(tmp_path):
    db = str(tmp_path / "mp.db")
    counter = str(tmp_path / "fetches.bin")
    SharedBarCache(db_path=db)                          # init schema before forking
    ctx = mp.get_context("spawn")
    args = [(db, counter, "SPY")] * 5
    with ctx.Pool(5) as pool:
        results = pool.map(_mp_fetch_worker, args)

    n_fetches = os.path.getsize(counter) if os.path.exists(counter) else 0
    assert n_fetches == 1, f"expected exactly 1 real fetch, got {n_fetches} (results={results})"
    assert results.count("fetched") == 1
    assert all(r in ("fetched", "cache") for r in results), results  # nobody timed out


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------

def test_default_db_path_env_override(monkeypatch):
    monkeypatch.setenv("SHARED_CACHE_DB", "/tmp/custom_bars.db")
    assert default_db_path() == "/tmp/custom_bars.db"


def test_default_db_path_uses_data_dir(monkeypatch):
    monkeypatch.delenv("SHARED_CACHE_DB", raising=False)
    assert default_db_path().endswith("shared_bars.db")
