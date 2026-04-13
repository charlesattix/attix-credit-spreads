"""
SENTINEL — Cross-experiment portfolio risk aggregation.

Reads open positions from every active experiment's SQLite DB and
reports per-ticker exposure, directional agreement, and expiration
clustering.  Pure read-only: no writes to any DB.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TickerExposure:
    ticker: str
    total_contracts: int = 0
    bull_count: int = 0     # bull put spreads
    bear_count: int = 0     # bear call spreads
    ic_count: int = 0       # iron condors
    other_count: int = 0    # straddles, strangles, etc.
    expirations: List[str] = field(default_factory=list)
    experiments: List[str] = field(default_factory=list)


@dataclass
class PortfolioRisk:
    total_open_positions: int = 0
    tickers: Dict[str, TickerExposure] = field(default_factory=dict)
    # Expirations with 2+ positions across all tickers: [(expiry, count), ...]
    expiration_clusters: List[Tuple[str, int]] = field(default_factory=list)
    # Tickers with bull AND bear open simultaneously
    directional_conflicts: List[str] = field(default_factory=list)
    # Tickers where 5+ experiments overlap
    concentrated_tickers: List[str] = field(default_factory=list)
    # Errors reading individual DBs (non-fatal)
    db_errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path_from_config(config_path: str, project_root: Path) -> Optional[str]:
    """
    Extract db_path from a paper YAML config file.

    Returns an absolute path string, or None if unavailable.
    """
    full = project_root / config_path
    if not full.exists():
        return None
    try:
        with open(full) as f:
            cfg = yaml.safe_load(f)
        db_path = cfg.get("db_path")
        if db_path:
            resolved = project_root / db_path
            return str(resolved)
    except Exception as e:
        logger.debug("Failed to read config %s: %s", config_path, e)
    return None


def _classify_strategy(strategy_type: str) -> str:
    """Map a strategy_type string to one of: bull | bear | ic | other."""
    s = strategy_type.lower()
    if "condor" in s or "iron" in s:
        return "ic"
    if "bull" in s or "put" in s:
        return "bull"
    if "bear" in s or "call" in s:
        return "bear"
    return "other"


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------


def aggregate_portfolio_risk(
    registry: dict,
    project_root: Path,
) -> PortfolioRisk:
    """
    Aggregate open positions across all paper_trading experiments.

    Uses each experiment's SQLite DB (resolved from paper_config YAML).
    Never raises — DB errors are collected in PortfolioRisk.db_errors.
    """
    import sys
    sys.path.insert(0, str(project_root))
    from shared.database import get_db

    result = PortfolioRisk()
    all_expirations: List[str] = []

    for exp_id, exp in registry.get("experiments", {}).items():
        if exp.get("status") != "paper_trading":
            continue
        paper_config = exp.get("paper_config")
        if not paper_config:
            result.db_errors.append(f"{exp_id}: no paper_config in registry")
            continue

        db_path = _db_path_from_config(paper_config, project_root)
        if not db_path:
            result.db_errors.append(
                f"{exp_id}: could not resolve db_path from {paper_config}"
            )
            continue
        if not Path(db_path).exists():
            result.db_errors.append(f"{exp_id}: DB not found at {db_path}")
            continue

        try:
            conn = get_db(db_path)
            try:
                rows = conn.execute(
                    """
                    SELECT ticker,
                           COALESCE(strategy_type, type, '') AS strat,
                           COALESCE(contracts, 1)            AS contracts,
                           expiration
                    FROM   trades
                    WHERE  status IN ('open', 'pending_open')
                    """
                ).fetchall()
            finally:
                conn.close()
        except Exception as e:
            result.db_errors.append(f"{exp_id}: DB read error: {e}")
            continue

        for row in rows:
            ticker = str(row["ticker"] or "?").upper()
            strat = str(row["strat"] or "")
            contracts = int(row["contracts"] or 1)
            expiry = str(row["expiration"] or "").split(" ")[0]

            result.total_open_positions += 1

            if ticker not in result.tickers:
                result.tickers[ticker] = TickerExposure(ticker=ticker)
            te = result.tickers[ticker]
            te.total_contracts += contracts
            if exp_id not in te.experiments:
                te.experiments.append(exp_id)
            if expiry and expiry not in te.expirations:
                te.expirations.append(expiry)
            if expiry:
                all_expirations.append(expiry)

            direction = _classify_strategy(strat)
            if direction == "bull":
                te.bull_count += 1
            elif direction == "bear":
                te.bear_count += 1
            elif direction == "ic":
                te.ic_count += 1
            else:
                te.other_count += 1

    # --- Derived signals ---

    # Expiration clusters: dates shared by 2+ positions
    exp_counts = Counter(all_expirations)
    result.expiration_clusters = sorted(
        [(exp, cnt) for exp, cnt in exp_counts.items() if cnt >= 2],
        key=lambda x: x[1],
        reverse=True,
    )

    # Directional conflicts: same ticker has both bull and bear open (non-IC)
    for ticker, te in result.tickers.items():
        if te.bull_count > 0 and te.bear_count > 0:
            result.directional_conflicts.append(
                f"{ticker}: {te.bull_count} bull / {te.bear_count} bear"
            )

    # Concentrated tickers: 3+ experiments on same ticker
    result.concentrated_tickers = [
        ticker
        for ticker, te in result.tickers.items()
        if len(te.experiments) >= 3
    ]

    return result
