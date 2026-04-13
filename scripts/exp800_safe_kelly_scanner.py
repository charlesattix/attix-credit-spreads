#!/usr/bin/env python3
"""
exp800_safe_kelly_scanner.py — EXP-800 Safe Kelly Paper Trading Scanner

Implements EXP-400 signal generation (dte=15, otm=2%, width=$12) with Safe Kelly
criterion position sizing (bull=9%, neutral=7%, bear=4%) and 3-tier portfolio
drawdown circuit breakers.

Pipeline per scan:
  1. Fetch SPY + VIX price history (200 days via curl/yfinance)
  2. Read live account equity from Alpaca; update Kelly state (HWM, CB tier)
  3. Check circuit breaker — Tier 3 halts new entries for tier3_halt_trades slots
  4. Compute regime (combo detector: MA200, RSI momentum, VIX structure)
  5. Select direction (bull_put / bear_call / iron_condor) from regime
  6. VIX entry gate (block if VIX > vix_max_entry)
  7. Find target expiration (~DTE days out), compute OTM strikes
  8. Fetch live option quotes from Alpaca to get actual credit
  9. Apply Kelly fraction × CB multiplier → size contracts
 10. Submit via ExecutionEngine (DB persist first, then Alpaca order)
 11. Log full decision audit: regime, Kelly fraction, CB tier, DD%, action

Usage:
    python3 scripts/exp800_safe_kelly_scanner.py
    python3 scripts/exp800_safe_kelly_scanner.py --config configs/paper_exp800.yaml
    python3 scripts/exp800_safe_kelly_scanner.py --env-file .env.exp800
    python3 scripts/exp800_safe_kelly_scanner.py --dry-run   # no Alpaca orders

Run via cron at market open (10:00 ET weekdays):
    0 10 * * 1-5 cd /path/to/repo && python3 scripts/exp800_safe_kelly_scanner.py
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
pre_scan_check("EXP-800")  # halts if status=halted; sets DRY_RUN if paused

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("exp800")

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = ROOT / "configs" / "paper_exp800.yaml"
DEFAULT_DB     = ROOT / "data"    / "exp800" / "pilotai_exp800.db"

_EXP400_DEFAULTS = {
    "target_dte":          15,
    "otm_pct":             0.02,
    "spread_width":        12,
    "profit_target":       55,
    "stop_loss_multiplier":1.25,
    "min_credit_pct":      5,
    "vix_max_entry":       40,
    "max_contracts":       25,
    "max_positions":       10,
    "account_size":        100_000,
}

_KELLY_DEFAULTS = {
    "regime_fractions": {"bull": 9.0, "neutral": 7.0, "bear": 4.0},
    "sizing_base":      "current_equity",
    "circuit_breakers": {
        "tier1_dd":          -8.0,
        "tier2_dd":         -10.0,
        "tier3_dd":         -12.0,
        "min_fraction":      2.0,
        "tier3_halt_trades": 30,
        "recovery_dd":       -7.0,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Config / env loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_config(path: Path) -> dict:
    try:
        import yaml
        with open(path) as f:
            raw = yaml.safe_load(f)
        raw_str = json.dumps(raw)
        import re
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
    n = len(closes)
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

    feats["spy_price"] = float(closes.iloc[-1])
    return feats


def _compute_vix_features(vix_df: pd.DataFrame) -> Dict:
    feats: Dict = {}
    if vix_df.empty or "Close" not in vix_df.columns:
        return {"vix": 20.0, "vix_percentile_20d": 50.0, "vix_percentile_50d": 50.0, "vix_percentile_100d": 50.0}
    vix = vix_df["Close"]
    feats["vix"] = float(vix.iloc[-1])
    for w, label in [(20, "20d"), (50, "50d"), (100, "100d")]:
        tail = vix.tail(w)
        feats[f"vix_percentile_{label}"] = float((tail < vix.iloc[-1]).sum() / len(tail) * 100) if len(vix) >= w else 50.0
    return feats


# ─────────────────────────────────────────────────────────────────────────────
# Regime detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_regime(spy_df: pd.DataFrame, vix_feats: Dict, regime_config: Dict) -> str:
    if spy_df.empty or len(spy_df) < 50:
        return "neutral"
    closes  = spy_df["Close"]
    current = float(closes.iloc[-1])
    vix     = vix_feats.get("vix", 20.0)

    vix_extreme = float(regime_config.get("vix_extreme", 40.0))
    if vix >= vix_extreme:
        return regime_config.get("vix_extreme_regime", "neutral").lower()

    band_pct  = float(regime_config.get("ma200_neutral_band_pct", 0.5)) / 100.0
    ma_period = int(regime_config.get("ma_slow_period", 80))
    ma200 = (float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200
             else float(closes.rolling(ma_period).mean().iloc[-1]) if len(closes) >= ma_period
             else current)
    ma200_signal = "bull" if current > ma200 * (1 + band_pct) else "bear" if current < ma200 * (1 - band_pct) else "neutral"

    from shared.indicators import calculate_rsi
    rsi = float(calculate_rsi(closes, int(regime_config.get("rsi_period", 14))).iloc[-1]) if len(closes) >= 14 else 50.0
    rsi_signal = "bull" if rsi > float(regime_config.get("rsi_bull_threshold", 50.0)) else "bear" if rsi < float(regime_config.get("rsi_bear_threshold", 45.0)) else "neutral"

    vix_p50 = vix_feats.get("vix_percentile_50d", 50.0)
    vix_ratio_proxy = 0.9 + (vix_p50 / 100.0) * 0.2
    vix_struct_signal = "bull" if vix_ratio_proxy < float(regime_config.get("vix_structure_bull", 0.95)) else "bear" if vix_ratio_proxy > float(regime_config.get("vix_structure_bear", 1.05)) else "neutral"

    signals = [ma200_signal, rsi_signal, vix_struct_signal]
    if regime_config.get("bear_requires_unanimous", True):
        if all(s == "bear" for s in signals):
            return "bear"
        return "bull" if sum(s == "bull" for s in signals) >= 2 else "neutral"
    from collections import Counter
    return Counter(signals).most_common(1)[0][0]


# ─────────────────────────────────────────────────────────────────────────────
# Option helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_target_expiration(target_dte: int) -> str:
    from datetime import date
    today = date.today()
    target_date = today + timedelta(days=target_dte)
    days_ahead = (4 - target_date.weekday()) % 7
    expiry = target_date + timedelta(days=days_ahead)
    if (expiry - today).days < target_dte:
        expiry += timedelta(weeks=1)
    return expiry.strftime("%Y-%m-%d")


def _round_strike(price: float, step: float = 1.0) -> float:
    return round(round(price / step) * step, 2)


def _estimate_credit_alpaca(provider, ticker: str, expiration: str, short_strike: float, long_strike: float, spread_type: str) -> float:
    try:
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest
        data_client = OptionHistoricalDataClient(
            api_key    = os.environ.get("ALPACA_API_KEY",    ""),
            secret_key = os.environ.get("ALPACA_API_SECRET", ""),
        )
        short_sym = provider.find_option_symbol(ticker, expiration, short_strike, spread_type)
        long_sym  = provider.find_option_symbol(ticker, expiration, long_strike,  spread_type)
        req    = OptionLatestQuoteRequest(symbol_or_symbols=[short_sym, long_sym])
        quotes = data_client.get_option_latest_quote(req)

        def _mid(q) -> float:
            bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
            return (bid + ask) / 2.0 if bid > 0 and ask > 0 else bid or ask

        credit = _mid(quotes[short_sym]) - _mid(quotes[long_sym])
        if credit > 0:
            return credit
    except Exception as exc:
        logger.warning("Live credit fetch failed: %s — using BS estimate", exc)

    return _bs_credit_estimate(ticker, expiration, short_strike, long_strike, spread_type)


def _bs_credit_estimate(ticker: str, expiration: str, short_strike: float, long_strike: float, spread_type: str) -> float:
    try:
        from backtest.backtester import _yf_download_safe
        end    = datetime.today()
        spy_df = _yf_download_safe("SPY", start=(end - timedelta(days=5)).strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        if spy_df.empty:
            return 0.0
        S = float(spy_df["Close"].iloc[-1])
        T = (datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.now().date()).days / 365.0
        if T <= 0:
            return 0.0
        sigma, r = 0.18, 0.05

        def _norm_cdf(x):
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

        def _d1(S, K):
            return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))

        def _put(K):
            d1, d2 = _d1(S, K), _d1(S, K) - sigma * math.sqrt(T)
            return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

        def _call(K):
            d1, d2 = _d1(S, K), _d1(S, K) - sigma * math.sqrt(T)
            return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)

        fn = _call if spread_type == "call" else _put
        credit = fn(short_strike) - fn(long_strike)
        logger.info("BS credit estimate: %.2f (S=%.1f)", credit, S)
        return max(credit, 0.0)
    except Exception as exc:
        logger.warning("BS estimate failed: %s", exc)
        return 0.0


def _size_contracts(equity: float, kelly_pct: float, spread_width: float, credit_per_share: float, max_contracts: int) -> int:
    risk_dollars = equity * kelly_pct / 100.0
    max_loss = (spread_width - credit_per_share) * 100.0
    if max_loss <= 0:
        return 1
    return max(1, min(int(risk_dollars / max_loss), max_contracts))


# ─────────────────────────────────────────────────────────────────────────────
# Kelly state management  (SQLite persistence)
# ─────────────────────────────────────────────────────────────────────────────

_KELLY_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS kelly_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    hwm             REAL    NOT NULL,
    current_equity  REAL    NOT NULL,
    drawdown_pct    REAL    NOT NULL,
    cb_tier         INTEGER NOT NULL DEFAULT 0,
    halt_remaining  INTEGER NOT NULL DEFAULT 0,
    last_updated    TEXT    NOT NULL
)
"""


class KellyStateDB:
    """Persist Kelly high-water mark and circuit-breaker state across scans."""

    def __init__(self, db_path: Path, account_size: float):
        self.db_path      = db_path
        self.account_size = account_size
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(_KELLY_STATE_SCHEMA)
            conn.commit()
            row = conn.execute("SELECT id FROM kelly_state WHERE id=1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO kelly_state (id, hwm, current_equity, drawdown_pct, cb_tier, halt_remaining, last_updated) "
                    "VALUES (1, ?, ?, 0.0, 0, 0, ?)",
                    (account_size, account_size, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                logger.info("KellyStateDB: bootstrapped HWM=%.2f", account_size)

    def load(self) -> Dict:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT hwm, current_equity, drawdown_pct, cb_tier, halt_remaining, last_updated "
                "FROM kelly_state WHERE id=1"
            ).fetchone()
        if row is None:
            return {"hwm": self.account_size, "current_equity": self.account_size,
                    "drawdown_pct": 0.0, "cb_tier": 0, "halt_remaining": 0, "last_updated": ""}
        keys = ["hwm", "current_equity", "drawdown_pct", "cb_tier", "halt_remaining", "last_updated"]
        return dict(zip(keys, row))

    def save(self, state: Dict) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE kelly_state SET hwm=?, current_equity=?, drawdown_pct=?, "
                "cb_tier=?, halt_remaining=?, last_updated=? WHERE id=1",
                (state["hwm"], state["current_equity"], state["drawdown_pct"],
                 state["cb_tier"], state["halt_remaining"],
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def update_equity(self, current_equity: float, cb_cfg: Dict) -> Dict:
        """Update HWM/drawdown/CB tier from live equity. Returns updated state dict."""
        state = self.load()
        hwm   = state["hwm"]

        # Update HWM if equity is at a new high
        if current_equity > hwm:
            hwm = current_equity
            logger.info("KellyState: new HWM=%.2f", hwm)

        dd_pct = (current_equity - hwm) / hwm * 100.0  # negative when below HWM

        tier1_dd   = float(cb_cfg.get("tier1_dd",   -8.0))
        tier2_dd   = float(cb_cfg.get("tier2_dd",  -10.0))
        tier3_dd   = float(cb_cfg.get("tier3_dd",  -12.0))
        recovery   = float(cb_cfg.get("recovery_dd", -7.0))
        halt_count = int(cb_cfg.get("tier3_halt_trades", 30))

        prev_tier          = state["cb_tier"]
        prev_halt          = state["halt_remaining"]

        # Determine new tier
        if dd_pct <= tier3_dd:
            new_tier = 3
            new_halt = halt_count if prev_tier < 3 else prev_halt  # only reset on fresh Tier 3 trigger
        elif dd_pct <= tier2_dd:
            new_tier = 2
            new_halt = prev_halt if prev_tier == 3 else 0
        elif dd_pct <= tier1_dd:
            new_tier = 1
            new_halt = 0
        else:
            # Possible recovery from Tier 2 pause
            if prev_tier >= 2 and dd_pct > recovery:
                new_tier = 0
                new_halt = 0
                logger.info("KellyState: recovered from Tier %d (DD=%.2f%% > %.2f%%)", prev_tier, dd_pct, recovery)
            elif prev_tier == 1 and dd_pct > tier1_dd:
                new_tier = 0
                new_halt = 0
            else:
                new_tier = prev_tier
                new_halt = prev_halt

        if new_tier != prev_tier:
            logger.warning("KellyState: CB tier %d → %d  DD=%.2f%%  equity=%.2f  HWM=%.2f",
                           prev_tier, new_tier, dd_pct, current_equity, hwm)

        state.update({
            "hwm":            hwm,
            "current_equity": current_equity,
            "drawdown_pct":   round(dd_pct, 4),
            "cb_tier":        new_tier,
            "halt_remaining": new_halt,
        })
        self.save(state)
        return state


# ─────────────────────────────────────────────────────────────────────────────
# Kelly fraction + circuit-breaker sizing
# ─────────────────────────────────────────────────────────────────────────────

def _kelly_fraction(regime: str, kelly_cfg: Dict, state: Dict) -> Tuple[float, str]:
    """Return (effective_kelly_pct, sizing_note).  Returns (0.0, reason) to skip."""
    fractions = kelly_cfg.get("regime_fractions", _KELLY_DEFAULTS["regime_fractions"])
    cb_cfg    = kelly_cfg.get("circuit_breakers", _KELLY_DEFAULTS["circuit_breakers"])

    base_frac = float(fractions.get(regime, fractions.get("neutral", 7.0)))
    tier      = state["cb_tier"]
    halt      = state["halt_remaining"]
    min_frac  = float(cb_cfg.get("min_fraction", 2.0))

    if tier >= 3 and halt > 0:
        return 0.0, f"cb_tier3_halted: {halt} slots remaining"

    if tier == 2:
        eff = min_frac
        return eff, f"cb_tier2: floor={min_frac}%"

    if tier == 1:
        eff = base_frac * 0.5
        return eff, f"cb_tier1: 0.5× base={base_frac}% → {eff}%"

    return base_frac, f"cb_tier0: full Kelly={base_frac}%"


def _decrement_halt(state: Dict, kelly_state_db: KellyStateDB) -> None:
    """Decrement halt counter for Tier 3 when a trade slot is consumed."""
    if state["cb_tier"] >= 3 and state["halt_remaining"] > 0:
        state["halt_remaining"] = state["halt_remaining"] - 1
        if state["halt_remaining"] == 0:
            logger.info("KellyState: Tier 3 halt counter exhausted — resuming normal sizing")
        kelly_state_db.save(state)


# ─────────────────────────────────────────────────────────────────────────────
# Main scanner
# ─────────────────────────────────────────────────────────────────────────────

class EXP800Scanner:
    """One-shot Safe Kelly credit spread scanner for EXP-800 paper trading."""

    def __init__(self, config: Dict, dry_run: bool = False):
        self.cfg     = config
        self.dry_run = dry_run
        strat  = config.get("strategy", {})
        risk   = config.get("risk", {})
        kelly  = config.get("kelly", _KELLY_DEFAULTS)

        self.target_dte    = int(strat.get("target_dte",          _EXP400_DEFAULTS["target_dte"]))
        self.otm_pct       = float(strat.get("otm_pct",           _EXP400_DEFAULTS["otm_pct"]))
        self.spread_width  = float(strat.get("spread_width",      _EXP400_DEFAULTS["spread_width"]))
        self.regime_config = strat.get("regime_config", {})
        ic_cfg = strat.get("iron_condor", {})
        self.ic_enabled    = bool(ic_cfg.get("enabled", True))

        self.vix_max_entry = float(risk.get("vix_max_entry",      _EXP400_DEFAULTS["vix_max_entry"]))
        self.max_contracts = int(risk.get("max_contracts",        _EXP400_DEFAULTS["max_contracts"]))
        self.account_size  = float(risk.get("account_size",       _EXP400_DEFAULTS["account_size"]))
        self.min_credit_pct= float(risk.get("min_credit_pct",     _EXP400_DEFAULTS["min_credit_pct"]))
        self.tickers       = config.get("tickers", ["SPY"])

        self.kelly_cfg = kelly
        self.cb_cfg    = kelly.get("circuit_breakers", _KELLY_DEFAULTS["circuit_breakers"])
        self.sizing_base = kelly.get("sizing_base", "current_equity")

        db_path = Path(config.get("db_path", str(DEFAULT_DB)))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path

        self.kelly_db = KellyStateDB(db_path, self.account_size)

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
            self._engine = ExecutionEngine(alpaca_provider=self._alpaca, db_path=str(self.db_path), config=self.cfg)
            logger.info("AlpacaProvider + ExecutionEngine initialised")
        except Exception as exc:
            logger.error("Alpaca init failed: %s — dry-run mode", exc)

    def _get_live_equity(self) -> float:
        """Read portfolio_value from Alpaca; fall back to last known equity."""
        if self._alpaca is not None:
            try:
                account = self._alpaca.get_account()
                equity  = float(account.get("portfolio_value", account.get("equity", 0)))
                if equity > 0:
                    return equity
            except Exception as exc:
                logger.warning("Could not fetch live equity: %s — using DB value", exc)
        # Fall back to last known current_equity from DB
        state = self.kelly_db.load()
        return state["current_equity"]

    def scan(self) -> List[Dict]:
        logger.info("=" * 70)
        logger.info("EXP-800 scan  %s  dry_run=%s", datetime.now(timezone.utc).isoformat(), self.dry_run)

        # Update Kelly state with live equity before scanning any ticker
        live_equity  = self._get_live_equity()
        kelly_state  = self.kelly_db.update_equity(live_equity, self.cb_cfg)

        results = [self._scan_ticker(t, kelly_state) for t in self.tickers]
        for r in results:
            self._print_decision(r)
        return results

    def _scan_ticker(self, ticker: str, kelly_state: Dict) -> Dict:
        d: Dict = {
            "ticker":          ticker,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "action":          "no_trade",
            "reason":          "",
            "regime":          "unknown",
            "vix":             None,
            "kelly_pct":       None,
            "kelly_note":      None,
            "cb_tier":         kelly_state["cb_tier"],
            "drawdown_pct":    kelly_state["drawdown_pct"],
            "equity":          kelly_state["current_equity"],
            "hwm":             kelly_state["hwm"],
            "order_result":    None,
        }

        spy_df = _fetch_price_history(ticker, days=220)
        vix_df = _fetch_price_history("^VIX", days=110)
        if spy_df.empty:
            d["reason"] = "no_price_data"; return d

        spy_feats  = _compute_technicals(spy_df)
        vix_feats  = _compute_vix_features(vix_df)
        current_px = spy_feats["spy_price"]
        vix_level  = vix_feats["vix"]
        d["vix"]   = round(vix_level, 2)

        if self.vix_max_entry > 0 and vix_level > self.vix_max_entry:
            _decrement_halt(kelly_state, self.kelly_db)
            d["reason"] = f"vix_gate: {vix_level:.1f} > {self.vix_max_entry}"; return d

        regime = _detect_regime(spy_df, vix_feats, self.regime_config)
        d["regime"] = regime

        # ── Kelly fraction + circuit-breaker check ────────────────────────────
        kelly_pct, kelly_note = _kelly_fraction(regime, self.kelly_cfg, kelly_state)
        d["kelly_pct"]  = round(kelly_pct, 4) if kelly_pct else 0.0
        d["kelly_note"] = kelly_note

        if kelly_pct == 0.0:
            _decrement_halt(kelly_state, self.kelly_db)
            d["reason"] = kelly_note; return d

        # ── Tier 3 flatten: log but don't place new trades ────────────────────
        if kelly_state["cb_tier"] >= 3:
            _decrement_halt(kelly_state, self.kelly_db)
            d["reason"] = "cb_tier3_flatten_halt"; return d

        spread_type = self._regime_to_spread(regime)
        if spread_type is None:
            d["reason"] = "no_regime_signal"; return d

        expiration    = _find_target_expiration(self.target_dte)
        actual_dte    = (datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.now().date()).days
        short_strike, long_strike = self._compute_strikes(current_px, spread_type)

        logger.info("Candidate: %s %s  exp=%s DTE=%d  short=%.2f  long=%.2f  px=%.2f",
                    ticker, spread_type, expiration, actual_dte, short_strike, long_strike, current_px)

        credit_per_share = self._get_credit(ticker, expiration, short_strike, long_strike, spread_type)
        if credit_per_share <= 0:
            d["reason"] = "no_credit_estimate"; return d

        min_credit = self.spread_width * self.min_credit_pct / 100.0
        if credit_per_share < min_credit:
            d["reason"] = f"credit_too_low: {credit_per_share:.2f} < {min_credit:.2f}"; return d

        # ── Size via Kelly (use current_equity if sizing_base=current_equity) ─
        sizing_equity = kelly_state["current_equity"] if self.sizing_base == "current_equity" else self.account_size
        contracts = _size_contracts(sizing_equity, kelly_pct, self.spread_width, credit_per_share, self.max_contracts)

        opp = {
            "ticker":          ticker,
            "type":            spread_type,
            "expiration":      expiration,
            "short_strike":    short_strike,
            "long_strike":     long_strike,
            "credit":          round(credit_per_share, 2),
            "contracts":       contracts,
            "experiment":      "EXP-800",
            "kelly_pct":       round(kelly_pct, 4),
            "kelly_note":      kelly_note,
            "cb_tier":         kelly_state["cb_tier"],
            "drawdown_pct":    round(kelly_state["drawdown_pct"], 4),
            "source":          "exp800_safe_kelly_scanner",
        }

        if self.dry_run or self._engine is None:
            d["action"]       = "dry_run"
            d["order_result"] = {"status": "dry_run", "opp": opp}
            logger.info("DRY RUN: %s", json.dumps(opp))
        else:
            result = self._engine.submit_opportunity(opp)
            d["action"]       = result.get("status", "error")
            d["order_result"] = result
            logger.info("Order: %s", json.dumps(result))

        return d

    def _regime_to_spread(self, regime: str) -> Optional[str]:
        if regime == "bull":    return "bull_put"
        if regime == "bear":    return "bear_call"
        if regime == "neutral": return "iron_condor" if self.ic_enabled else "bull_put"
        return None

    def _compute_strikes(self, price: float, spread_type: str) -> Tuple[float, float]:
        if spread_type == "bull_put":
            short = _round_strike(price * (1.0 - self.otm_pct))
            return short, _round_strike(short - self.spread_width)
        if spread_type == "bear_call":
            short = _round_strike(price * (1.0 + self.otm_pct))
            return short, _round_strike(short + self.spread_width)
        # iron_condor — return put-side (call side mirrored at submission)
        short = _round_strike(price * (1.0 - self.otm_pct))
        return short, _round_strike(short - self.spread_width)

    def _get_credit(self, ticker: str, expiration: str, short: float, long: float, spread_type: str) -> float:
        opt_type = "call" if spread_type == "bear_call" else "put"
        if self._alpaca is not None:
            return _estimate_credit_alpaca(self._alpaca, ticker, expiration, short, long, opt_type)
        return _bs_credit_estimate(ticker, expiration, short, long, opt_type)

    def _print_decision(self, d: Dict) -> None:
        kelly_str = f"  Kelly={d['kelly_pct']}% ({d['kelly_note']})" if d["kelly_pct"] else ""
        cb_str    = f"  CB=Tier{d['cb_tier']} DD={d['drawdown_pct']:.2f}%"
        print(
            f"[EXP-800] {d['ticker']}  regime={d['regime']}  VIX={d['vix']}"
            f"{cb_str}{kelly_str}  equity={d['equity']:.0f}  HWM={d['hwm']:.0f}"
            f"  → {d['action'].upper()}"
            + (f"  ({d['reason']})" if d["reason"] else "")
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="EXP-800 Safe Kelly paper trading scanner")
    parser.add_argument("--config",   default=str(DEFAULT_CONFIG))
    parser.add_argument("--env-file", default=".env.exp800")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--verbose",  action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    logger.setLevel(logging.INFO)

    _load_env_file(args.env_file)

    cfg_path = Path(args.config)
    config   = _load_config(cfg_path) if cfg_path.exists() else {}

    log_file = config.get("logging", {}).get("file")
    if log_file:
        lp = ROOT / log_file
        lp.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(lp)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S"))
        logging.getLogger().addHandler(fh)

    scanner = EXP800Scanner(config=config, dry_run=args.dry_run)
    results = scanner.scan()

    summary = {"experiment": "EXP-800", "timestamp": datetime.now(timezone.utc).isoformat(),
               "dry_run": args.dry_run, "results": results}
    print("\n--- SUMMARY ---")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
