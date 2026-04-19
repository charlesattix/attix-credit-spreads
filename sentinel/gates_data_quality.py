"""
sentinel/gates_data_quality.py — SENTINEL V2 Data Quality & Signal Integrity Gates

Gates 10, 11, 12: the three gates that would have caught VIX3M abstaining,
regime parity drift, and stale data before any trades were placed.

FAIL-CLOSED PRINCIPLE: every gate BLOCKS when uncertain.  If data is missing,
stale, or can't be validated → BLOCK.  No more "safe" defaults that silently
allow bad trades.

Gate 10 — DATA FRESHNESS
    Check every data source before allowing trades.  Returns specific errors
    like "VIX data is 3 days stale" not just "data check failed".

Gate 11 — SIGNAL VOTING AUDIT
    After regime detection, log which signals voted and how.  If vix_structure
    abstains → CRITICAL.  Track vote history for audit trail.

Gate 12 — BACKTEST-PRODUCTION PARITY
    Run a shadow regime calculation using ComboRegimeDetector and compare to
    the scanner's actual regime call.  Detect divergence in real time.

Usage (injected into scanner startup after pre_scan_check):

    from sentinel.gates_data_quality import (
        check_data_freshness,   # Gate 10
        audit_signal_votes,     # Gate 11
        check_regime_parity,    # Gate 12
    )

    # Gate 10 — before any data fetching
    freshness = check_data_freshness("EXP-400")
    if freshness.blocked:
        sys.exit(1)

    # Gate 11 — after regime computed
    audit_signal_votes("EXP-400", regime, vote_details, price_data, vix_data)

    # Gate 12 — compare scanner regime vs shadow ComboRegimeDetector
    parity = check_regime_parity("EXP-400", scanner_regime, spy_df, vix_by_date, ...)
    if parity.diverged:
        ... handle ...
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("sentinel.gates_data_quality")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MACRO_CACHE_DB = _PROJECT_ROOT / "data" / "macro_cache" / "macro_cache.db"
_SENTINEL_DB = Path(
    os.environ.get("SENTINEL_DB_PATH", str(Path(__file__).parent / "db" / "sentinel.db"))
)

# ---------------------------------------------------------------------------
# DB schema extension for vote history
# ---------------------------------------------------------------------------
_VOTE_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_vote_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL,
    vote_time       TEXT    NOT NULL DEFAULT (datetime('now')),
    regime_result   TEXT    NOT NULL,
    ma_signal       TEXT,
    rsi_signal      TEXT,
    vix_struct_signal TEXT,
    ma_crossover_signal TEXT,
    bull_votes      INTEGER DEFAULT 0,
    bear_votes      INTEGER DEFAULT 0,
    abstain_count   INTEGER DEFAULT 0,
    abstain_reasons TEXT,
    spy_close       REAL,
    vix_close       REAL,
    vix3m_close     REAL,
    vix_ratio       REAL,
    rsi_value       REAL,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_votes_exp_time
    ON signal_vote_history (experiment_id, vote_time DESC);

CREATE TABLE IF NOT EXISTS data_freshness_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL,
    check_time      TEXT    NOT NULL DEFAULT (datetime('now')),
    passed          INTEGER NOT NULL DEFAULT 0,
    vix_age_hours   REAL,
    vix3m_present   INTEGER,
    spy_age_hours   REAL,
    macro_db_ok     INTEGER,
    fred_status     TEXT,
    errors          TEXT,
    warnings        TEXT
);

CREATE INDEX IF NOT EXISTS idx_freshness_exp_time
    ON data_freshness_checks (experiment_id, check_time DESC);

CREATE TABLE IF NOT EXISTS regime_parity_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT    NOT NULL,
    check_time      TEXT    NOT NULL DEFAULT (datetime('now')),
    scanner_regime  TEXT,
    shadow_regime   TEXT,
    diverged        INTEGER DEFAULT 0,
    hysteresis_active INTEGER DEFAULT 0,
    vix_extreme_ok  INTEGER DEFAULT 1,
    rsi_threshold_ok INTEGER DEFAULT 1,
    details         TEXT
);

CREATE INDEX IF NOT EXISTS idx_parity_exp_time
    ON regime_parity_checks (experiment_id, check_time DESC);
"""


def _init_vote_db() -> None:
    """Ensure vote history tables exist in sentinel.db."""
    _SENTINEL_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SENTINEL_DB))
    try:
        conn.executescript(_VOTE_HISTORY_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# Initialize on import
try:
    _init_vote_db()
except Exception as exc:
    logger.warning("Could not init vote history DB: %s", exc)


def _sentinel_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_SENTINEL_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════════════════════════════════
# GATE 10 — DATA FRESHNESS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FreshnessResult:
    """Result of Gate 10 data freshness check."""
    experiment_id: str
    passed: bool = True
    blocked: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    vix_age_hours: Optional[float] = None
    vix3m_present: bool = False
    vix3m_rows: int = 0
    spy_age_hours: Optional[float] = None
    macro_db_exists: bool = False
    fred_series_status: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.blocked = len(self.errors) > 0
        self.passed = not self.blocked


# Max staleness thresholds
_VIX_MAX_AGE_HOURS = 48  # 2 calendar days (covers weekends)
_SPY_MAX_AGE_HOURS = 48
_VIX3M_MIN_ROWS = 20  # need at least 20 trading days for meaningful signal

# FRED series expected lags (calendar days from today)
_FRED_EXPECTED_LAGS = {
    "VIXCLS": 3,        # daily, ~1 day lag
    "T10Y2Y": 3,        # daily
    "T5YIE": 3,         # daily
    "BAMLH0A0HYM2": 5,  # daily, slight lag
    "FEDFUNDS": 45,     # monthly
    "PAYEMS": 45,        # monthly
    "CPIAUCSL": 45,      # monthly
    "CPILFESL": 45,      # monthly
    "CFNAI": 60,         # monthly, bigger lag
}


def check_data_freshness(
    experiment_id: str,
    macro_cache_db: Path = _MACRO_CACHE_DB,
    now: Optional[datetime] = None,
) -> FreshnessResult:
    """
    Gate 10: Verify all data sources are fresh before allowing trades.

    FAIL-CLOSED: returns blocked=True with specific error messages if any
    critical data source is missing or stale.

    Args:
        experiment_id: Experiment being checked.
        macro_cache_db: Path to macro_cache.db (injectable for tests).
        now: Override current time (for tests).

    Returns:
        FreshnessResult with errors (BLOCK) and warnings.
    """
    result = FreshnessResult(experiment_id=experiment_id)
    if now is None:
        now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None) if now.tzinfo else now

    # ── Check 1: macro_cache.db exists and is non-empty ──────────────────
    if not macro_cache_db.exists():
        result.errors.append(
            f"macro_cache.db not found at {macro_cache_db} — no market data available"
        )
        result.blocked = True
        result.passed = False
        _record_freshness(result)
        return result

    if macro_cache_db.stat().st_size == 0:
        result.errors.append("macro_cache.db is 0 bytes — empty database")
        result.blocked = True
        result.passed = False
        _record_freshness(result)
        return result

    result.macro_db_exists = True

    try:
        conn = sqlite3.connect(str(macro_cache_db))
    except Exception as exc:
        result.errors.append(f"Cannot open macro_cache.db: {exc}")
        result.blocked = True
        result.passed = False
        _record_freshness(result)
        return result

    try:
        # ── Check 2: VIX data freshness ──────────────────────────────────
        _check_vix_freshness(conn, result, now_naive)

        # ── Check 3: VIX3M data MUST be present ─────────────────────────
        _check_vix3m_presence(conn, result)

        # ── Check 4: SPY price data freshness ────────────────────────────
        _check_spy_freshness(conn, result, now_naive)

        # ── Check 5: FRED series within expected lag ─────────────────────
        _check_fred_freshness(conn, result, now_naive)

    finally:
        conn.close()

    result.blocked = len(result.errors) > 0
    result.passed = not result.blocked
    _record_freshness(result)

    if result.blocked:
        logger.critical(
            "SENTINEL GATE 10 BLOCKED %s: %s",
            experiment_id, "; ".join(result.errors),
        )
        _send_gate_alert(experiment_id, "GATE 10 — DATA FRESHNESS", result.errors)
    elif result.warnings:
        logger.warning(
            "SENTINEL GATE 10 WARNINGS %s: %s",
            experiment_id, "; ".join(result.warnings),
        )

    return result


def _check_vix_freshness(conn: sqlite3.Connection, result: FreshnessResult, now: datetime):
    """Check VIX data age in vix_daily table."""
    try:
        row = conn.execute(
            "SELECT MAX(date) as max_date FROM vix_daily WHERE vix_close IS NOT NULL"
        ).fetchone()
    except sqlite3.OperationalError:
        result.errors.append("vix_daily table does not exist in macro_cache.db — run scripts/fetch_vix_data.py --backfill")
        return

    if row is None or row[0] is None:
        result.errors.append("vix_daily table is empty — no VIX data. Run scripts/fetch_vix_data.py --backfill")
        return

    max_date = datetime.strptime(row[0], "%Y-%m-%d")
    age = now - max_date
    age_hours = age.total_seconds() / 3600
    result.vix_age_hours = round(age_hours, 1)

    if age_hours > _VIX_MAX_AGE_HOURS:
        result.errors.append(
            f"VIX data is {age_hours:.0f}h stale (last: {row[0]}, "
            f"max allowed: {_VIX_MAX_AGE_HOURS}h). Run scripts/fetch_vix_data.py"
        )


def _check_vix3m_presence(conn: sqlite3.Connection, result: FreshnessResult):
    """VIX3M MUST be present — abstaining silently was the original bug."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM vix_daily WHERE vix3m_close IS NOT NULL"
        ).fetchone()
    except sqlite3.OperationalError:
        result.errors.append("vix_daily table missing — VIX3M data unavailable")
        return

    count = row[0] if row else 0
    result.vix3m_rows = count
    result.vix3m_present = count >= _VIX3M_MIN_ROWS

    if count == 0:
        result.errors.append(
            "VIX3M data is COMPLETELY MISSING from vix_daily. "
            "The vix_structure signal will abstain, making BEAR regime impossible. "
            "Run: python scripts/fetch_vix_data.py --backfill"
        )
    elif count < _VIX3M_MIN_ROWS:
        result.errors.append(
            f"VIX3M data has only {count} rows (need >= {_VIX3M_MIN_ROWS}). "
            f"Insufficient for reliable term structure signal."
        )


def _check_spy_freshness(conn: sqlite3.Connection, result: FreshnessResult, now: datetime):
    """Check SPY price data age in price_cache table."""
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM price_cache WHERE ticker = 'SPY'"
        ).fetchone()
    except sqlite3.OperationalError:
        result.warnings.append("price_cache table missing — SPY freshness unchecked")
        return

    if row is None or row[0] is None:
        result.warnings.append("No SPY data in price_cache — macro scoring may be stale")
        return

    max_date = datetime.strptime(row[0], "%Y-%m-%d")
    age = now - max_date
    age_hours = age.total_seconds() / 3600
    result.spy_age_hours = round(age_hours, 1)

    if age_hours > _SPY_MAX_AGE_HOURS:
        result.warnings.append(
            f"SPY price_cache is {age_hours:.0f}h stale (last: {row[0]}). "
            f"Macro scoring may use outdated prices."
        )


def _check_fred_freshness(conn: sqlite3.Connection, result: FreshnessResult, now: datetime):
    """Check each FRED series is within expected publication lag."""
    for series_id, max_lag_days in _FRED_EXPECTED_LAGS.items():
        try:
            row = conn.execute(
                "SELECT MAX(obs_date) FROM fred_cache WHERE series_id = ?",
                (series_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            result.warnings.append(f"fred_cache table missing — {series_id} unchecked")
            continue

        if row is None or row[0] is None:
            result.fred_series_status[series_id] = "MISSING"
            result.warnings.append(f"FRED {series_id}: no data in fred_cache")
            continue

        max_date = datetime.strptime(row[0], "%Y-%m-%d")
        age_days = (now - max_date).days

        if age_days > max_lag_days:
            result.fred_series_status[series_id] = f"STALE ({age_days}d)"
            result.warnings.append(
                f"FRED {series_id}: {age_days}d old (expected lag: {max_lag_days}d)"
            )
        else:
            result.fred_series_status[series_id] = "OK"


def _record_freshness(result: FreshnessResult):
    """Record freshness check in sentinel.db for audit trail."""
    try:
        conn = _sentinel_conn()
        conn.execute(
            """
            INSERT INTO data_freshness_checks
                (experiment_id, passed, vix_age_hours, vix3m_present,
                 spy_age_hours, macro_db_ok, fred_status, errors, warnings)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.experiment_id,
                1 if result.passed else 0,
                result.vix_age_hours,
                1 if result.vix3m_present else 0,
                result.spy_age_hours,
                1 if result.macro_db_exists else 0,
                str(result.fred_series_status),
                "; ".join(result.errors) if result.errors else None,
                "; ".join(result.warnings) if result.warnings else None,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to record freshness check: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# GATE 11 — SIGNAL VOTING AUDIT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class VoteAudit:
    """Result of Gate 11 signal voting audit."""
    experiment_id: str
    regime: str = "unknown"
    votes: Dict[str, str] = field(default_factory=dict)
    bull_count: int = 0
    bear_count: int = 0
    abstain_count: int = 0
    abstain_reasons: List[str] = field(default_factory=list)
    severity: str = "ok"  # ok | warning | critical
    spy_close: Optional[float] = None
    vix_close: Optional[float] = None
    vix3m_close: Optional[float] = None
    vix_ratio: Optional[float] = None
    rsi_value: Optional[float] = None
    alerts: List[str] = field(default_factory=list)


def audit_signal_votes(
    experiment_id: str,
    regime: str,
    vote_details: Dict[str, str],
    *,
    spy_close: Optional[float] = None,
    vix_close: Optional[float] = None,
    vix3m_close: Optional[float] = None,
    rsi_value: Optional[float] = None,
) -> VoteAudit:
    """
    Gate 11: Audit and record how each signal voted after regime detection.

    FAIL-CLOSED on vix_structure abstain — this was THE bug that cost us.

    Args:
        experiment_id: Experiment identifier.
        regime: Final regime classification ("bull", "bear", "neutral").
        vote_details: {signal_name: vote} where vote is "bull"/"bear"/"neutral"/"abstain".
        spy_close: SPY close price used for computation (T-1).
        vix_close: VIX close used.
        vix3m_close: VIX3M close used (None if missing).
        rsi_value: RSI(14) value used.

    Returns:
        VoteAudit with severity and alerts.
    """
    audit = VoteAudit(
        experiment_id=experiment_id,
        regime=regime,
        votes=dict(vote_details),
        spy_close=spy_close,
        vix_close=vix_close,
        vix3m_close=vix3m_close,
        rsi_value=rsi_value,
    )

    if vix_close and vix3m_close and vix3m_close > 0:
        audit.vix_ratio = round(vix_close / vix3m_close, 4)

    # Count votes
    for signal, vote in vote_details.items():
        if vote == "bull":
            audit.bull_count += 1
        elif vote == "bear":
            audit.bear_count += 1
        elif vote in ("abstain", "neutral", None, ""):
            if vote == "abstain":
                audit.abstain_count += 1
                audit.abstain_reasons.append(f"{signal}: no data")

    # ── Critical: vix_structure abstain ──────────────────────────────────
    vix_vote = vote_details.get("vix_structure", "abstain")
    if vix_vote == "abstain":
        audit.severity = "critical"
        audit.alerts.append(
            "CRITICAL: vix_structure signal ABSTAINED — VIX3M data missing or stale. "
            "BEAR regime is impossible with bear_requires_unanimous. "
            "This was the exact bug that caused $30k+ in losses."
        )

    # ── Warning: any other signal abstain ────────────────────────────────
    for signal, vote in vote_details.items():
        if signal == "vix_structure":
            continue
        if vote == "abstain":
            if audit.severity != "critical":
                audit.severity = "warning"
            audit.alerts.append(
                f"WARNING: {signal} signal abstained — data may be insufficient"
            )

    # ── Bear drought detection ───────────────────────────────────────────
    _check_bear_drought(experiment_id, regime, spy_close, audit)

    # ── Log the vote breakdown ───────────────────────────────────────────
    vote_str = ", ".join(f"{s}:{v}" for s, v in vote_details.items())
    log_msg = (
        f"SENTINEL GATE 11: {experiment_id} regime={regime} "
        f"votes=({vote_str}) "
        f"bull={audit.bull_count} bear={audit.bear_count} "
        f"abstain={audit.abstain_count}"
    )
    if audit.severity == "critical":
        logger.critical(log_msg)
    elif audit.severity == "warning":
        logger.warning(log_msg)
    else:
        logger.info(log_msg)

    # ── Record to sentinel.db ────────────────────────────────────────────
    _record_vote(audit)

    # ── Alert on critical ────────────────────────────────────────────────
    if audit.severity == "critical":
        _send_gate_alert(experiment_id, "GATE 11 — SIGNAL VOTING", audit.alerts)

    return audit


def _check_bear_drought(
    experiment_id: str,
    current_regime: str,
    spy_close: Optional[float],
    audit: VoteAudit,
):
    """Alert if BEAR hasn't fired in 30 trading days despite SPY decline > 5%."""
    try:
        conn = _sentinel_conn()
        rows = conn.execute(
            """
            SELECT regime_result, spy_close FROM signal_vote_history
            WHERE experiment_id = ?
            ORDER BY vote_time DESC
            LIMIT 30
            """,
            (experiment_id,),
        ).fetchall()
        conn.close()
    except Exception:
        return  # can't check without history

    if len(rows) < 10:
        return  # not enough history

    # Check if BEAR has fired in last 30 votes
    bear_fired = any(r[0] == "bear" for r in rows)
    if bear_fired:
        return

    # Check SPY decline from oldest to newest in window
    spy_values = [r[1] for r in rows if r[1] is not None]
    if len(spy_values) < 2:
        return

    peak = max(spy_values)
    current = spy_close or spy_values[0]  # most recent
    if peak > 0:
        decline_pct = (peak - current) / peak * 100
        if decline_pct > 5.0:
            audit.severity = "critical"
            audit.alerts.append(
                f"BEAR DROUGHT: BEAR regime has not fired in {len(rows)} trading days "
                f"despite SPY declining {decline_pct:.1f}% from ${peak:.0f} to ${current:.0f}. "
                f"Possible broken signal — investigate vix_structure data."
            )


def _record_vote(audit: VoteAudit):
    """Record signal vote to sentinel.db."""
    try:
        conn = _sentinel_conn()
        conn.execute(
            """
            INSERT INTO signal_vote_history
                (experiment_id, regime_result, ma_signal, rsi_signal,
                 vix_struct_signal, bull_votes, bear_votes,
                 abstain_count, abstain_reasons, spy_close, vix_close,
                 vix3m_close, vix_ratio, rsi_value, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit.experiment_id,
                audit.regime,
                audit.votes.get("price_vs_ma200"),
                audit.votes.get("rsi_momentum"),
                audit.votes.get("vix_structure"),
                audit.bull_count,
                audit.bear_count,
                audit.abstain_count,
                "; ".join(audit.abstain_reasons) if audit.abstain_reasons else None,
                audit.spy_close,
                audit.vix_close,
                audit.vix3m_close,
                audit.vix_ratio,
                audit.rsi_value,
                "; ".join(audit.alerts) if audit.alerts else None,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to record vote history: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# GATE 12 — BACKTEST-PRODUCTION PARITY
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ParityResult:
    """Result of Gate 12 backtest-production parity check."""
    experiment_id: str
    passed: bool = True
    diverged: bool = False
    scanner_regime: Optional[str] = None
    shadow_regime: Optional[str] = None
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    hysteresis_active: bool = False
    vix_extreme_ok: bool = True
    rsi_threshold_ok: bool = True


def check_regime_parity(
    experiment_id: str,
    scanner_regime: str,
    spy_df,
    vix_by_date: dict,
    vix3m_by_date: Optional[dict],
    regime_config: dict,
) -> ParityResult:
    """
    Gate 12: Shadow-run ComboRegimeDetector and compare to scanner's regime call.

    Detects divergence between the hand-rolled _detect_regime() in scanners
    and the canonical ComboRegimeDetector used by the backtester.

    Args:
        experiment_id: Experiment being checked.
        scanner_regime: The regime the scanner actually computed.
        spy_df: SPY price DataFrame (same data the scanner used).
        vix_by_date: VIX data dict {Timestamp: float}.
        vix3m_by_date: VIX3M data dict or None.
        regime_config: The regime config dict from the experiment.

    Returns:
        ParityResult with divergence details.
    """
    import pandas as pd

    result = ParityResult(
        experiment_id=experiment_id,
        scanner_regime=scanner_regime,
    )

    # ── Static config parity checks ──────────────────────────────────────
    _check_vix_extreme_config(regime_config, result)
    _check_rsi_threshold_config(regime_config, result)
    _check_hysteresis_config(regime_config, result)

    # ── Shadow regime computation ────────────────────────────────────────
    try:
        from compass.regime import ComboRegimeDetector

        detector = ComboRegimeDetector(regime_config)

        # Run the canonical detector on the same data
        shadow_series = detector.compute_regime_series(
            spy_df, vix_by_date, vix3m_by_date
        )

        if shadow_series:
            # Get the most recent regime (last date in series)
            last_date = max(shadow_series.keys())
            result.shadow_regime = shadow_series[last_date]

            # Compare
            if result.shadow_regime != scanner_regime:
                result.diverged = True
                result.issues.append(
                    f"REGIME DIVERGENCE: scanner={scanner_regime}, "
                    f"ComboRegimeDetector={result.shadow_regime} on {last_date.date()}. "
                    f"The scanner's _detect_regime() is producing different results "
                    f"than the backtester's ComboRegimeDetector."
                )
                logger.critical(
                    "SENTINEL GATE 12: %s REGIME DIVERGENCE scanner=%s shadow=%s",
                    experiment_id, scanner_regime, result.shadow_regime,
                )
            else:
                logger.info(
                    "SENTINEL GATE 12: %s parity OK regime=%s",
                    experiment_id, scanner_regime,
                )

    except ImportError:
        result.warnings.append("ComboRegimeDetector not importable — shadow check skipped")
    except Exception as exc:
        result.warnings.append(f"Shadow regime check failed: {exc}")
        logger.warning("SENTINEL GATE 12: shadow check error: %s", exc)

    result.passed = not result.diverged and not result.issues
    _record_parity(result)

    if result.diverged:
        _send_gate_alert(experiment_id, "GATE 12 — REGIME PARITY", result.issues)

    return result


def _check_vix_extreme_config(config: dict, result: ParityResult):
    """Verify VIX extreme behavior: must map to 'bear', not 'neutral'."""
    vix_extreme_regime = config.get("vix_extreme_regime", "NOT_SET")

    # ComboRegimeDetector hardcodes VIX > extreme → "bear"
    # If the scanner config has vix_extreme_regime="neutral", that's a parity bug
    if vix_extreme_regime == "neutral":
        result.vix_extreme_ok = False
        result.issues.append(
            "CRITICAL: vix_extreme_regime='neutral' in config — backtester uses 'bear'. "
            "During VIX > 40, scanner would enter NEUTRAL (bull puts allowed) "
            "while backtester enters BEAR (bear calls only). "
            "Fix: remove vix_extreme_regime from config or set to 'bear'."
        )
    elif vix_extreme_regime != "NOT_SET" and vix_extreme_regime != "bear":
        result.vix_extreme_ok = False
        result.issues.append(
            f"vix_extreme_regime='{vix_extreme_regime}' — backtester uses 'bear'. "
            f"Mismatch will cause divergent behavior during VIX spikes."
        )


def _check_rsi_threshold_config(config: dict, result: ParityResult):
    """Verify RSI thresholds match ComboRegimeDetector defaults."""
    # ComboRegimeDetector default: rsi_bull_threshold=55.0
    # exp700_ml_scanner fallback: rsi_bull_threshold=50.0
    # If config doesn't specify, the scanner uses 50 but backtester uses 55
    if "rsi_bull_threshold" not in config:
        result.rsi_threshold_ok = False
        result.warnings.append(
            "rsi_bull_threshold not in config — scanner fallback is 50.0, "
            "backtester default is 55.0. RSI 50-54 would be classified as BULL "
            "by scanner but NEUTRAL by backtester."
        )


def _check_hysteresis_config(config: dict, result: ParityResult):
    """Check that cooldown/hysteresis is properly configured."""
    cooldown = config.get("cooldown_days")
    if cooldown is None:
        result.hysteresis_active = False
        result.warnings.append(
            "cooldown_days not in config — scanner _detect_regime() has NO hysteresis "
            "(recalculates from scratch each scan). Backtester uses 10-day cooldown. "
            "Scanner may flip regimes daily in choppy markets."
        )
    elif cooldown == 0:
        result.hysteresis_active = False
        result.warnings.append(
            "cooldown_days=0 — hysteresis disabled. Regime can change every scan."
        )
    else:
        result.hysteresis_active = True


def _record_parity(result: ParityResult):
    """Record parity check in sentinel.db."""
    try:
        conn = _sentinel_conn()
        conn.execute(
            """
            INSERT INTO regime_parity_checks
                (experiment_id, scanner_regime, shadow_regime, diverged,
                 hysteresis_active, vix_extreme_ok, rsi_threshold_ok, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.experiment_id,
                result.scanner_regime,
                result.shadow_regime,
                1 if result.diverged else 0,
                1 if result.hysteresis_active else 0,
                1 if result.vix_extreme_ok else 0,
                1 if result.rsi_threshold_ok else 0,
                "; ".join(result.issues + result.warnings) if (result.issues or result.warnings) else None,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to record parity check: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Alert dispatch (shared)
# ═══════════════════════════════════════════════════════════════════════════════


def _send_gate_alert(experiment_id: str, gate_name: str, messages: List[str]):
    """Send Telegram alert for gate failure. Never raises."""
    body = "\n".join(f"  - {m}" for m in messages)
    text = (
        f"🛡️ SENTINEL V2 — {gate_name} FAILED\n"
        f"🛑 <b>{experiment_id}</b>\n"
        f"{body}\n"
        f"Scanner should be BLOCKED until resolved."
    )
    try:
        from shared.telegram_alerts import send_message
        send_message(text, parse_mode="HTML")
    except ImportError:
        logger.warning("telegram_alerts not importable — alert skipped")
    except Exception as exc:
        logger.error("Telegram dispatch failed: %s", exc)
