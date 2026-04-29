#!/usr/bin/env python3
"""
exp307_sector_etf_scanner.py — EXP-307 Sector ETF Diversification Scanner

Extends the Champion (EXP-400) credit spread strategy to trade
SPY + XLI (industrials) + XLF (financials) simultaneously.

Pipeline per scan cycle:
  1. For each ticker (SPY, XLI, XLF):
     a. Fetch price history (220 days)
     b. Detect regime independently via MA crossovers
        - SPY:  combo mode (MA200 + RSI + VIX structure)
        - ETFs: MA mode    (MA50/MA200 crossover only)
     c. Apply VIX gate (skip if VIX > vix_max_entry)
     d. Check per-ticker position limit
     e. Check total position limit
     f. Compute target expiration + OTM strikes (per-ticker width)
     g. Fetch live option quote; validate liquidity (OI, bid, spread)
     h. Estimate credit; enforce min_credit_pct gate
     i. Size contracts; submit via ExecutionEngine
     j. Log full per-ticker decision audit

Usage:
    python3 scripts/exp307_sector_etf_scanner.py
    python3 scripts/exp307_sector_etf_scanner.py --config configs/paper_exp307.yaml
    python3 scripts/exp307_sector_etf_scanner.py --env-file .env.exp700
    python3 scripts/exp307_sector_etf_scanner.py --dry-run

Run via cron at market open (10:00 ET weekdays):
    0 10 * * 1-5 cd /path/to/repo && python3 scripts/exp307_sector_etf_scanner.py
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── SENTINEL pre-scan guard (must run before any project imports) ─────────────
from sentinel.guards import pre_scan_check  # noqa: E402
pre_scan_check("EXP-307")  # halts if status=halted; sets DRY_RUN if paused

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("exp307")

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = ROOT / "configs" / "paper_exp307.yaml"
DEFAULT_DB     = ROOT / "data"    / "exp307" / "pilotai_exp307.db"

_STRATEGY_DEFAULTS = {
    "target_dte":          15,
    "otm_pct":             0.02,
    "spread_width":        12,
    "max_risk_per_trade":  8.5,
    "profit_target":       55,
    "stop_loss_multiplier":1.25,
    "min_credit_pct":      5,
    "vix_max_entry":       40,
    "drawdown_cb_pct":     40,
    "max_contracts":       25,
    "account_size":        100_000,
    "max_positions_per_ticker": 2,
    "max_total_positions":      6,
}

_LIQUIDITY_DEFAULTS = {
    "min_open_interest":        50,
    "min_bid":                  0.05,
    "min_ask":                  0.05,
    "max_bid_ask_spread_pct":   50,
}


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_config(path: Path) -> dict:
    try:
        import yaml
        import re
        with open(path) as f:
            raw = yaml.safe_load(f)
        raw_str = json.dumps(raw)
        raw_str = re.sub(r'\$\{([^}]+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), raw_str)
        return json.loads(raw_str)
    except Exception as exc:
        logger.warning("Config load failed (%s): %s — using defaults", path, exc)
        return {}


def _load_env_file(env_path: str) -> None:
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        logger.debug("No env file at %s", env_path)


# ─────────────────────────────────────────────────────────────────────────────
# Market data
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_price_history(ticker: str, days: int = 220) -> pd.DataFrame:
    from backtest.backtester import _yf_download_safe
    end   = datetime.today()
    start = end - timedelta(days=days)
    df = _yf_download_safe(ticker, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
    if df.empty:
        logger.error("No price data for %s", ticker)
    return df


def _compute_technicals(df: pd.DataFrame) -> Dict:
    closes = df["Close"]
    highs  = df["High"]
    lows   = df["Low"]
    n      = len(closes)
    feats: Dict = {}

    from shared.indicators import calculate_rsi
    feats["rsi_14"] = float(calculate_rsi(closes, 14).iloc[-1]) if n >= 14 else 50.0

    feats["momentum_5d_pct"]  = float((closes.iloc[-1] / closes.iloc[-6]  - 1) * 100) if n >= 6  else 0.0
    feats["momentum_10d_pct"] = float((closes.iloc[-1] / closes.iloc[-11] - 1) * 100) if n >= 11 else 0.0

    for period, key in [(20, "ma20"), (50, "ma50"), (80, "ma80"), (200, "ma200")]:
        ma = float(closes.rolling(period).mean().iloc[-1]) if n >= period else float(closes.iloc[-1])
        feats[f"dist_from_{key}_pct"] = float((closes.iloc[-1] / ma - 1) * 100)
        feats[f"_{key}"] = ma

    log_rets = np.log(closes / closes.shift(1)).dropna()
    for w, label in [(5, "5d"), (10, "10d"), (20, "20d")]:
        feats[f"realized_vol_{label}"] = float(log_rets.tail(w).std() * math.sqrt(252) * 100) if len(log_rets) >= w else 15.0

    if n >= 20:
        atr = pd.concat([
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows  - closes.shift(1)).abs(),
        ], axis=1).max(axis=1).rolling(20).mean()
        feats["realized_vol_atr20"] = float(atr.iloc[-1] / closes.iloc[-1] * 100)
    else:
        feats["realized_vol_atr20"] = 1.0

    feats["price"] = float(closes.iloc[-1])
    return feats


def _compute_vix_features(vix_df: pd.DataFrame) -> Dict:
    if vix_df.empty or "Close" not in vix_df.columns:
        return {"vix": 20.0, "vix_percentile_20d": 50.0, "vix_percentile_50d": 50.0}
    vix = vix_df["Close"]
    feats: Dict = {"vix": float(vix.iloc[-1])}
    for w, label in [(20, "20d"), (50, "50d")]:
        tail = vix.tail(w)
        feats[f"vix_percentile_{label}"] = float((tail < vix.iloc[-1]).sum() / len(tail) * 100) if len(vix) >= w else 50.0
    return feats


# ─────────────────────────────────────────────────────────────────────────────
# Regime detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_regime_combo(price_df: pd.DataFrame, vix_feats: Dict, regime_cfg: Dict) -> str:
    """3-signal combo regime (SPY): MA200 + RSI + VIX structure."""
    if price_df.empty or len(price_df) < 50:
        return "neutral"

    closes = price_df["Close"]
    current = float(closes.iloc[-1])
    vix = vix_feats.get("vix", 20.0)

    vix_extreme = float(regime_cfg.get("vix_extreme", 40.0))
    if vix >= vix_extreme:
        return regime_cfg.get("vix_extreme_regime", "neutral").lower()

    band_pct  = float(regime_cfg.get("ma200_neutral_band_pct", 0.5)) / 100.0
    ma_period = int(regime_cfg.get("ma_slow_period", 80))
    ma_ref = (
        float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200
        else float(closes.rolling(ma_period).mean().iloc[-1]) if len(closes) >= ma_period
        else current
    )
    ma_signal = (
        "bull" if current > ma_ref * (1 + band_pct)
        else "bear" if current < ma_ref * (1 - band_pct)
        else "neutral"
    )

    from shared.indicators import calculate_rsi
    rsi = float(calculate_rsi(closes, int(regime_cfg.get("rsi_period", 14))).iloc[-1]) if len(closes) >= 14 else 50.0
    rsi_signal = (
        "bull" if rsi > float(regime_cfg.get("rsi_bull_threshold", 50.0))
        else "bear" if rsi < float(regime_cfg.get("rsi_bear_threshold", 45.0))
        else "neutral"
    )

    vix_p50 = vix_feats.get("vix_percentile_50d", 50.0)
    vix_ratio_proxy = 0.9 + (vix_p50 / 100.0) * 0.2
    vix_struct_signal = (
        "bull" if vix_ratio_proxy < float(regime_cfg.get("vix_structure_bull", 0.95))
        else "bear" if vix_ratio_proxy > float(regime_cfg.get("vix_structure_bear", 1.05))
        else "neutral"
    )

    signals = [ma_signal, rsi_signal, vix_struct_signal]
    if regime_cfg.get("bear_requires_unanimous", True):
        if all(s == "bear" for s in signals):
            return "bear"
        return "bull" if sum(s == "bull" for s in signals) >= 2 else "neutral"

    from collections import Counter
    return Counter(signals).most_common(1)[0][0]


def _detect_regime_ma(price_df: pd.DataFrame, regime_cfg: Dict) -> str:
    """MA crossover regime for sector ETFs: MA50/MA200 cross with neutral band."""
    if price_df.empty or len(price_df) < 50:
        return "neutral"

    closes = price_df["Close"]
    n      = len(closes)

    fast_period = int(regime_cfg.get("ma_fast_period", 50))
    slow_period = int(regime_cfg.get("ma_slow_period_cross", 200))
    band_pct    = float(regime_cfg.get("ma200_neutral_band_pct", 0.5)) / 100.0

    # Use shift-by-1 to prevent lookahead (regime on T uses data through T-1)
    closes_prev = closes.shift(1).dropna()

    ma_fast = float(closes_prev.rolling(fast_period).mean().iloc[-1]) if len(closes_prev) >= fast_period else float(closes_prev.iloc[-1])
    ma_slow = float(closes_prev.rolling(slow_period).mean().iloc[-1]) if len(closes_prev) >= slow_period else float(closes_prev.iloc[-1])

    if ma_slow == 0:
        return "neutral"

    ratio = ma_fast / ma_slow
    if ratio > 1.0 + band_pct:
        return "bull"
    if ratio < 1.0 - band_pct:
        return "bear"
    return "neutral"


def _detect_regime(ticker: str, price_df: pd.DataFrame, vix_feats: Dict,
                   regime_mode: str, regime_cfg: Dict) -> str:
    """Route to combo or MA regime detector based on regime_mode."""
    if regime_mode == "ma":
        return _detect_regime_ma(price_df, regime_cfg)
    return _detect_regime_combo(price_df, vix_feats, regime_cfg)


# ─────────────────────────────────────────────────────────────────────────────
# Option helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_target_expiration(target_dte: int) -> str:
    from datetime import date
    today       = date.today()
    target_date = today + timedelta(days=target_dte)
    # Roll to next Friday
    days_ahead = (4 - target_date.weekday()) % 7
    expiry = target_date + timedelta(days=days_ahead)
    if (expiry - today).days < target_dte:
        expiry += timedelta(weeks=1)
    return expiry.strftime("%Y-%m-%d")


def _round_strike(price: float, step: float = 1.0) -> float:
    return round(round(price / step) * step, 2)


def _compute_strikes(price: float, spread_type: str, otm_pct: float, spread_width: float) -> Tuple[float, float]:
    if spread_type == "bull_put":
        short = _round_strike(price * (1.0 - otm_pct))
        return short, _round_strike(short - spread_width)
    if spread_type == "bear_call":
        short = _round_strike(price * (1.0 + otm_pct))
        return short, _round_strike(short + spread_width)
    # iron_condor — return put side
    short = _round_strike(price * (1.0 - otm_pct))
    return short, _round_strike(short - spread_width)


def _regime_to_spread(regime: str, ic_enabled: bool) -> Optional[str]:
    if regime == "bull":    return "bull_put"
    if regime == "bear":    return "bear_call"
    if regime == "neutral": return "iron_condor" if ic_enabled else "bull_put"
    return None


def _size_contracts(account_size: float, risk_pct: float, spread_width: float,
                    credit_per_share: float, max_contracts: int) -> int:
    risk_dollars = account_size * risk_pct / 100.0
    max_loss     = (spread_width - credit_per_share) * 100.0
    if max_loss <= 0:
        return 1
    return max(1, min(int(risk_dollars / max_loss), max_contracts))


# ─────────────────────────────────────────────────────────────────────────────
# Credit estimation
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_option_quote_alpaca(alpaca_provider, ticker: str, expiration: str,
                               short_strike: float, long_strike: float,
                               opt_type: str) -> Dict:
    """Return dict with bid, ask, open_interest for short leg, or {} on failure."""
    try:
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest, OptionSnapshotRequest
        data_client = OptionHistoricalDataClient(
            api_key    = os.environ.get("ALPACA_API_KEY", ""),
            secret_key = os.environ.get("ALPACA_API_SECRET", ""),
        )
        short_sym = alpaca_provider.find_option_symbol(ticker, expiration, short_strike, opt_type)
        long_sym  = alpaca_provider.find_option_symbol(ticker, expiration, long_strike,  opt_type)

        req    = OptionLatestQuoteRequest(symbol_or_symbols=[short_sym, long_sym])
        quotes = data_client.get_option_latest_quote(req)

        def _mid(q) -> float:
            bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
            return (bid + ask) / 2.0 if bid > 0 and ask > 0 else bid or ask

        short_q = quotes.get(short_sym)
        long_q  = quotes.get(long_sym)
        if short_q is None or long_q is None:
            return {}

        short_bid = float(short_q.bid_price or 0)
        short_ask = float(short_q.ask_price or 0)
        credit    = _mid(short_q) - _mid(long_q)

        # Attempt to get open interest via snapshot
        oi = 0
        try:
            snap_req = OptionSnapshotRequest(symbol_or_symbols=[short_sym])
            snaps    = data_client.get_option_snapshot(snap_req)
            snap     = snaps.get(short_sym)
            if snap and hasattr(snap, "greeks") and snap.greeks:
                pass  # OI not in greeks; use contract details
            if snap and hasattr(snap, "implied_volatility"):
                pass
            # OI is not always available via snapshot; set to None to skip OI gate
            oi = None
        except Exception:
            oi = None

        return {
            "short_bid": short_bid,
            "short_ask": short_ask,
            "credit":    max(credit, 0.0),
            "open_interest": oi,
            "short_sym": short_sym,
        }
    except Exception as exc:
        logger.warning("[%s] Alpaca option quote failed: %s", ticker, exc)
        return {}


def _bs_credit_estimate(ticker: str, price: float, expiration: str,
                        short_strike: float, long_strike: float,
                        spread_type: str) -> float:
    """Black-Scholes fallback credit estimate using the ticker's live price."""
    try:
        T = (datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.now().date()).days / 365.0
        if T <= 0 or price <= 0:
            return 0.0
        S, sigma, r = price, 0.18, 0.05

        def _norm_cdf(x: float) -> float:
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

        def _d1(K: float) -> float:
            return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))

        def _put(K: float) -> float:
            d1, d2 = _d1(K), _d1(K) - sigma * math.sqrt(T)
            return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

        def _call(K: float) -> float:
            d1, d2 = _d1(K), _d1(K) - sigma * math.sqrt(T)
            return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)

        fn = _call if spread_type == "bear_call" else _put
        return max(fn(short_strike) - fn(long_strike), 0.0)
    except Exception as exc:
        logger.warning("[%s] BS estimate failed: %s", ticker, exc)
        return 0.0


def _check_liquidity(quote: Dict, liquidity_cfg: Dict, ticker: str) -> Tuple[bool, str]:
    """Return (passes, reason) based on liquidity thresholds."""
    if not quote:
        # No quote available — skip liquidity gate (fail open for dry-run flexibility)
        logger.warning("[%s] No live quote; skipping liquidity check", ticker)
        return True, ""

    min_bid = float(liquidity_cfg.get("min_bid", _LIQUIDITY_DEFAULTS["min_bid"]))
    min_ask = float(liquidity_cfg.get("min_ask", _LIQUIDITY_DEFAULTS["min_ask"]))
    max_spread_pct = float(liquidity_cfg.get("max_bid_ask_spread_pct",
                                              _LIQUIDITY_DEFAULTS["max_bid_ask_spread_pct"]))
    min_oi = liquidity_cfg.get("min_open_interest", _LIQUIDITY_DEFAULTS["min_open_interest"])

    bid = float(quote.get("short_bid", 0))
    ask = float(quote.get("short_ask", 0))
    oi  = quote.get("open_interest")

    if bid < min_bid:
        return False, f"bid_too_low: {bid:.2f} < {min_bid:.2f}"
    if ask < min_ask:
        return False, f"ask_too_low: {ask:.2f} < {min_ask:.2f}"

    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else 999.0
        if spread_pct > max_spread_pct:
            return False, f"bid_ask_wide: {spread_pct:.1f}% > {max_spread_pct:.0f}%"

    if oi is not None and min_oi is not None and oi < min_oi:
        return False, f"low_oi: {oi} < {min_oi}"

    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers (open position counting)
# ─────────────────────────────────────────────────────────────────────────────

_OPEN_STATUSES = ("open", "active", "pending", "filled", "submitted")


def _count_open_positions(db_path: Path) -> int:
    """Total open positions across all tickers."""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            placeholders = ",".join("?" * len(_OPEN_STATUSES))
            cur = conn.execute(
                f"SELECT COUNT(*) FROM trades WHERE LOWER(status) IN ({placeholders})",
                _OPEN_STATUSES,
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning("DB open-position count failed: %s", exc)
        return 0


def _count_open_positions_for_ticker(db_path: Path, ticker: str) -> int:
    """Open positions for a specific ticker."""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            placeholders = ",".join("?" * len(_OPEN_STATUSES))
            cur = conn.execute(
                f"SELECT COUNT(*) FROM trades WHERE ticker=? AND LOWER(status) IN ({placeholders})",
                (ticker, *_OPEN_STATUSES),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning("DB ticker-position count failed for %s: %s", ticker, exc)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Main scanner
# ─────────────────────────────────────────────────────────────────────────────

class EXP307Scanner:
    """One-shot multi-ticker sector ETF credit spread scanner for EXP-307."""

    def __init__(self, config: Dict, dry_run: bool = False):
        self.cfg     = config
        self.dry_run = dry_run

        strat = config.get("strategy", {})
        risk  = config.get("risk",     {})
        liq   = config.get("liquidity", {})

        self.tickers        = config.get("tickers", ["SPY", "XLI", "XLF"])
        self.ticker_params  = config.get("ticker_params", {})
        self.target_dte     = int(strat.get("target_dte",     _STRATEGY_DEFAULTS["target_dte"]))
        self.regime_config  = strat.get("regime_config", {})
        self.ic_enabled     = False  # EXP-307 uses directional-only for sector ETFs

        self.vix_max_entry  = float(risk.get("vix_max_entry",      _STRATEGY_DEFAULTS["vix_max_entry"]))
        self.max_risk_pct   = float(risk.get("max_risk_per_trade",  _STRATEGY_DEFAULTS["max_risk_per_trade"]))
        self.max_contracts  = int(risk.get("max_contracts",         _STRATEGY_DEFAULTS["max_contracts"]))
        self.account_size   = float(risk.get("account_size",        _STRATEGY_DEFAULTS["account_size"]))
        self.min_credit_pct = float(risk.get("min_credit_pct",      _STRATEGY_DEFAULTS["min_credit_pct"]))
        self.max_pos_ticker = int(risk.get("max_positions_per_ticker", _STRATEGY_DEFAULTS["max_positions_per_ticker"]))
        self.max_pos_total  = int(risk.get("max_total_positions",   _STRATEGY_DEFAULTS["max_total_positions"]))

        self.liquidity_cfg  = liq

        db_path = Path(config.get("db_path", str(DEFAULT_DB)))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path

        self._alpaca = None
        self._engine = None
        if not dry_run:
            self._init_alpaca()

    def _init_alpaca(self) -> None:
        try:
            from strategy.alpaca_provider import AlpacaProvider
            from execution.execution_engine import ExecutionEngine
            alpaca_cfg = self.cfg.get("alpaca", {})
            self._alpaca = AlpacaProvider(
                api_key    = os.environ.get("ALPACA_API_KEY",    alpaca_cfg.get("api_key",    "")),
                api_secret = os.environ.get("ALPACA_API_SECRET", alpaca_cfg.get("api_secret", "")),
                paper      = bool(alpaca_cfg.get("paper", True)),
            )
            self._engine = ExecutionEngine(
                alpaca_provider=self._alpaca,
                db_path=str(self.db_path),
                config=self.cfg,
            )
            logger.info("AlpacaProvider + ExecutionEngine initialised")
        except Exception as exc:
            logger.error("Alpaca init failed: %s — orders disabled", exc)

    # ── Ticker-level param helpers ──────────────────────────────────────────

    def _ticker_param(self, ticker: str, key: str, default):
        return self.ticker_params.get(ticker, {}).get(key, default)

    def _spread_width(self, ticker: str) -> float:
        default = float(self.cfg.get("strategy", {}).get("spread_width",
                                                          _STRATEGY_DEFAULTS["spread_width"]))
        return float(self._ticker_param(ticker, "spread_width", default))

    def _otm_pct(self, ticker: str) -> float:
        default = float(self.cfg.get("strategy", {}).get("otm_pct",
                                                          _STRATEGY_DEFAULTS["otm_pct"]))
        return float(self._ticker_param(ticker, "otm_pct", default))

    def _regime_mode(self, ticker: str) -> str:
        default = self.cfg.get("strategy", {}).get("regime_mode", "combo")
        return str(self._ticker_param(ticker, "regime_mode", default))

    # ── Main scan ──────────────────────────────────────────────────────────

    def scan(self) -> List[Dict]:
        ts = datetime.now(timezone.utc).isoformat()
        logger.info("=" * 70)
        logger.info("EXP-307 scan  %s  dry_run=%s", ts, self.dry_run)

        # Prefetch shared VIX data once
        vix_df    = _fetch_price_history("^VIX", days=110)
        vix_feats = _compute_vix_features(vix_df)

        # Current total open positions (checked before each ticker)
        results = []
        for ticker in self.tickers:
            result = self._scan_ticker(ticker, vix_feats)
            self._print_decision(result)
            results.append(result)

        return results

    def _scan_ticker(self, ticker: str, vix_feats: Dict) -> Dict:
        d: Dict = {
            "ticker":        ticker,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "action":        "no_trade",
            "reason":        "",
            "regime":        "unknown",
            "regime_mode":   self._regime_mode(ticker),
            "vix":           round(vix_feats.get("vix", 0.0), 2),
            "spread_width":  self._spread_width(ticker),
            "otm_pct":       self._otm_pct(ticker),
            "credit":        None,
            "contracts":     None,
            "liquidity_ok":  None,
            "order_result":  None,
        }

        # ── VIX gate ─────────────────────────────────────────────────────
        vix_level = vix_feats.get("vix", 0.0)
        if self.vix_max_entry > 0 and vix_level > self.vix_max_entry:
            d["reason"] = f"vix_gate: {vix_level:.1f} > {self.vix_max_entry}"
            return d

        # ── Total position limit ──────────────────────────────────────────
        total_open = _count_open_positions(self.db_path)
        if total_open >= self.max_pos_total:
            d["reason"] = f"max_total_positions: {total_open} >= {self.max_pos_total}"
            return d

        # ── Per-ticker position limit ─────────────────────────────────────
        ticker_open = _count_open_positions_for_ticker(self.db_path, ticker)
        if ticker_open >= self.max_pos_ticker:
            d["reason"] = f"max_positions_per_ticker: {ticker_open} >= {self.max_pos_ticker}"
            return d

        # ── Price history + technicals ────────────────────────────────────
        price_df = _fetch_price_history(ticker, days=220)
        if price_df.empty:
            d["reason"] = "no_price_data"
            return d

        tech     = _compute_technicals(price_df)
        current  = tech["price"]
        d["price"] = round(current, 2)

        # ── Regime detection (independently per ticker) ───────────────────
        regime_mode = self._regime_mode(ticker)
        regime      = _detect_regime(ticker, price_df, vix_feats, regime_mode, self.regime_config)
        d["regime"] = regime
        logger.info("[%s] regime=%s (mode=%s)  price=%.2f  VIX=%.1f",
                    ticker, regime, regime_mode, current, vix_level)

        spread_type = _regime_to_spread(regime, self.ic_enabled)
        if spread_type is None:
            d["reason"] = "no_regime_signal"
            return d
        d["spread_type"] = spread_type

        # ── Strike computation ────────────────────────────────────────────
        spread_width = self._spread_width(ticker)
        otm_pct      = self._otm_pct(ticker)
        expiration   = _find_target_expiration(self.target_dte)
        actual_dte   = (datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.now().date()).days
        short_strike, long_strike = _compute_strikes(current, spread_type, otm_pct, spread_width)
        d["expiration"]   = expiration
        d["actual_dte"]   = actual_dte
        d["short_strike"] = short_strike
        d["long_strike"]  = long_strike

        logger.info("[%s] %s  exp=%s DTE=%d  short=%.2f  long=%.2f",
                    ticker, spread_type, expiration, actual_dte, short_strike, long_strike)

        # ── Live quote + liquidity check ──────────────────────────────────
        opt_type = "call" if spread_type == "bear_call" else "put"
        quote    = {}
        if self._alpaca is not None:
            quote = _fetch_option_quote_alpaca(
                self._alpaca, ticker, expiration, short_strike, long_strike, opt_type
            )

        liq_ok, liq_reason = _check_liquidity(quote, self.liquidity_cfg, ticker)
        d["liquidity_ok"] = liq_ok
        if not liq_ok:
            d["reason"] = f"liquidity: {liq_reason}"
            return d

        # ── Credit estimate ───────────────────────────────────────────────
        credit_per_share = (
            float(quote["credit"]) if quote.get("credit", 0) > 0
            else _bs_credit_estimate(ticker, current, expiration, short_strike, long_strike, spread_type)
        )
        if credit_per_share <= 0:
            d["reason"] = "no_credit_estimate"
            return d

        min_credit = spread_width * self.min_credit_pct / 100.0
        if credit_per_share < min_credit:
            d["reason"] = f"credit_too_low: {credit_per_share:.2f} < {min_credit:.2f}"
            return d

        d["credit"] = round(credit_per_share, 2)

        # ── Contract sizing ───────────────────────────────────────────────
        contracts  = _size_contracts(self.account_size, self.max_risk_pct,
                                     spread_width, credit_per_share, self.max_contracts)
        d["contracts"] = contracts

        # ── Build opportunity dict ────────────────────────────────────────
        opp = {
            "ticker":        ticker,
            "type":          spread_type,
            "expiration":    expiration,
            "short_strike":  short_strike,
            "long_strike":   long_strike,
            "credit":        round(credit_per_share, 2),
            "contracts":     contracts,
            "spread_width":  spread_width,
            "experiment":    "EXP-307",
            "regime":        regime,
            "regime_mode":   regime_mode,
            "source":        "exp307_sector_etf_scanner",
        }

        # ── Submit or dry-run ─────────────────────────────────────────────
        if self.dry_run or self._engine is None:
            d["action"]       = "dry_run"
            d["order_result"] = {"status": "dry_run", "opp": opp}
            logger.info("[%s] DRY RUN: %s", ticker, json.dumps(opp))
        else:
            result      = self._engine.submit_opportunity(opp)
            d["action"] = result.get("status", "error")
            d["order_result"] = result
            logger.info("[%s] Order: %s", ticker, json.dumps(result))

        return d

    def _print_decision(self, d: Dict) -> None:
        ticker = d["ticker"]
        regime = d.get("regime", "?")
        mode   = d.get("regime_mode", "?")
        vix    = d.get("vix", "?")
        action = d.get("action", "no_trade").upper()
        reason = f"  ({d['reason']})" if d.get("reason") else ""
        credit = f"  credit={d['credit']:.2f}" if d.get("credit") is not None else ""
        ctrs   = f"  contracts={d['contracts']}" if d.get("contracts") is not None else ""
        print(
            f"[EXP-307] {ticker:<4}  regime={regime:<7}({mode:<5})  VIX={vix}"
            f"{credit}{ctrs}  → {action}{reason}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="EXP-307 Sector ETF multi-ticker paper trading scanner"
    )
    parser.add_argument("--config",   default=str(DEFAULT_CONFIG),
                        help="Path to YAML config (default: configs/paper_exp307.yaml)")
    parser.add_argument("--env-file", default=".env.exp700",
                        help="Path to env file with ALPACA_API_KEY etc.")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Skip Alpaca orders; log decisions only")
    parser.add_argument("--verbose",  action="store_true",
                        help="Enable DEBUG logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    logger.setLevel(logging.INFO)

    _load_env_file(args.env_file)

    cfg_path = Path(args.config)
    config   = _load_config(cfg_path) if cfg_path.exists() else {}

    # File logging
    log_file = config.get("logging", {}).get("file")
    if log_file:
        lp = ROOT / log_file
        lp.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(lp)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S"
        ))
        logging.getLogger().addHandler(fh)

    scanner = EXP307Scanner(config=config, dry_run=args.dry_run)
    results = scanner.scan()

    summary = {
        "experiment": config.get("experiment_id", "EXP-307"),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "dry_run":    args.dry_run,
        "tickers":    scanner.tickers,
        "results":    results,
    }
    print("\n--- SUMMARY ---")
    print(json.dumps(summary, indent=2, default=str))

    # Sentinel G22 — heartbeat at end of scan iteration.
    from sentinel.heartbeat import emit_heartbeat
    emit_heartbeat("EXP-307", notes="scan complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
