#!/usr/bin/env python3
"""
exp700_ml_scanner.py — EXP-700 ML-Filtered Paper Trading Scanner

Implements EXP-400 signal generation (dte=15, otm=2%, width=$12) gated by
the EXP-305 ensemble ML filter (AUC=0.793, threshold=0.65, XGB+RF+ET ensemble).

Pipeline per scan:
  1. Fetch SPY + VIX price history (200 days via curl/yfinance)
  2. Compute regime (combo detector: MA200, RSI momentum, VIX structure)
  3. Select direction (bull_put / bear_call / iron_condor) from regime
  4. VIX entry gate (block if VIX > vix_max_entry)
  5. Find target expiration (~DTE days out), compute OTM strikes
  6. Fetch live option quotes from Alpaca to get actual credit
  7. Build 35-feature vector (matching ensemble training schema)
  8. Run ensemble ML prediction — skip if probability < threshold
  9. Submit via ExecutionEngine (DB persist first, then Alpaca order)
 10. Log full decision audit: regime, ML score, action, order status

Usage:
    python3 scripts/exp700_ml_scanner.py
    python3 scripts/exp700_ml_scanner.py --config configs/paper_exp700.yaml
    python3 scripts/exp700_ml_scanner.py --env-file .env.exp700
    python3 scripts/exp700_ml_scanner.py --dry-run   # no Alpaca orders

Run via cron at market open (10:00 ET weekdays):
    0 10 * * 1-5 cd /path/to/repo && python3 scripts/exp700_ml_scanner.py
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Logging setup (before any imports that log) ──────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("exp700")

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = ROOT / "configs" / "paper_exp700.yaml"
DEFAULT_DB = ROOT / "data" / "exp700" / "pilotai_exp700.db"

_EXP400_DEFAULTS = {
    "target_dte": 15,
    "otm_pct": 0.02,
    "spread_width": 12,
    "max_risk_per_trade": 8.5,
    "profit_target": 55,
    "stop_loss_multiplier": 1.25,
    "min_credit_pct": 5,
    "vix_max_entry": 40,
    "drawdown_cb_pct": 40,
    "max_contracts": 25,
    "account_size": 100_000,
}

_ML_DEFAULTS = {
    "probability_threshold": 0.65,
    "fail_open": True,
    "log_features": True,
}

_DEFAULT_MODEL_PATH = ROOT / "ml" / "models" / "ensemble_model_20260324.joblib"
_DEFAULT_STATS_PATH = ROOT / "ml" / "models" / "ensemble_model_20260324.feature_stats.json"


# ─────────────────────────────────────────────────────────────────────────────
# Config loading
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

    for period, key in [(20, "ma20"), (50, "ma50")]:
        if n >= period + 5:
            ma_now  = float(closes.rolling(period).mean().iloc[-1])
            ma_prev = float(closes.rolling(period).mean().iloc[-6])
            feats[f"{key}_slope_ann_pct"] = float((ma_now / ma_prev - 1) * 252 / 5 * 100)
        else:
            feats[f"{key}_slope_ann_pct"] = 0.0

    log_rets = np.log(closes / closes.shift(1)).dropna()
    for w, label in [(5, "5d"), (10, "10d"), (20, "20d")]:
        feats[f"realized_vol_{label}"] = float(log_rets.tail(w).std() * math.sqrt(252) * 100) if len(log_rets) >= w else 15.0

    if n >= 20:
        atr = pd.concat([highs - lows, (highs - closes.shift(1)).abs(), (lows - closes.shift(1)).abs()], axis=1).max(axis=1).rolling(20).mean()
        feats["realized_vol_atr20"] = float(atr.iloc[-1] / closes.iloc[-1] * 100)
    else:
        feats["realized_vol_atr20"] = 1.0

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
    closes = spy_df["Close"]
    current = float(closes.iloc[-1])
    vix = vix_feats.get("vix", 20.0)

    vix_extreme = float(regime_config.get("vix_extreme", 40.0))
    if vix >= vix_extreme:
        return regime_config.get("vix_extreme_regime", "neutral").lower()

    band_pct  = float(regime_config.get("ma200_neutral_band_pct", 0.5)) / 100.0
    ma_period = int(regime_config.get("ma_slow_period", 80))
    ma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else float(closes.rolling(ma_period).mean().iloc[-1]) if len(closes) >= ma_period else current
    ma200_signal = "bull" if current > ma200 * (1 + band_pct) else "bear" if current < ma200 * (1 - band_pct) else "neutral"

    from shared.indicators import calculate_rsi
    rsi = float(calculate_rsi(closes, int(regime_config.get("rsi_period", 14))).iloc[-1]) if len(closes) >= 14 else 50.0
    rsi_signal = "bull" if rsi > float(regime_config.get("rsi_bull_threshold", 50.0)) else "bear" if rsi < float(regime_config.get("rsi_bear_threshold", 45.0)) else "neutral"

    vix_p50  = vix_feats.get("vix_percentile_50d", 50.0)
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
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_API_SECRET", ""),
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
        end = datetime.today()
        spy_df = _yf_download_safe("SPY", start=(end - timedelta(days=5)).strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        if spy_df.empty:
            return 0.0
        S = float(spy_df["Close"].iloc[-1])
        T = (datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.now().date()).days / 365.0
        if T <= 0:
            return 0.0
        sigma, r = 0.18, 0.05

        def _norm_cdf(x):
            import math
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


# ─────────────────────────────────────────────────────────────────────────────
# Feature vector
# ─────────────────────────────────────────────────────────────────────────────

def _build_feature_vector(
    *, dte: int, hold_days: int, day_of_week: int, days_since_last_trade: int,
    spy_feats: Dict, vix_feats: Dict, iv_rank: float,
    net_credit_dollars: float, spread_width: float, otm_pct: float,
    contracts: int, spread_type: str, regime: str,
    feature_names: List[str], feature_means: List[float],
) -> np.ndarray:
    """Build 35-feature input array matching ensemble_model_20260324 training schema."""
    is_bull_put = 1 if spread_type == "bull_put"    else 0
    is_bear_call= 1 if spread_type == "bear_call"   else 0
    is_ic       = 1 if spread_type == "iron_condor" else 0
    max_loss_dollars = (spread_width - net_credit_dollars / 100.0) * 100.0

    live_vals = {
        "dte_at_entry":                    float(dte),
        "hold_days":                       float(hold_days),
        "day_of_week":                     float(day_of_week),
        "days_since_last_trade":           float(days_since_last_trade),
        "rsi_14":                          spy_feats.get("rsi_14", 50.0),
        "momentum_5d_pct":                 spy_feats.get("momentum_5d_pct", 0.0),
        "momentum_10d_pct":                spy_feats.get("momentum_10d_pct", 0.0),
        "vix":                             vix_feats.get("vix", 20.0),
        "vix_percentile_20d":              vix_feats.get("vix_percentile_20d", 50.0),
        "vix_percentile_50d":              vix_feats.get("vix_percentile_50d", 50.0),
        "vix_percentile_100d":             vix_feats.get("vix_percentile_100d", 50.0),
        "iv_rank":                         iv_rank,
        "spy_price":                       spy_feats.get("spy_price", 500.0),
        "dist_from_ma20_pct":              spy_feats.get("dist_from_ma20_pct", 0.0),
        "dist_from_ma50_pct":              spy_feats.get("dist_from_ma50_pct", 0.0),
        "dist_from_ma80_pct":              spy_feats.get("dist_from_ma80_pct", 0.0),
        "dist_from_ma200_pct":             spy_feats.get("dist_from_ma200_pct", 0.0),
        "ma20_slope_ann_pct":              spy_feats.get("ma20_slope_ann_pct", 0.0),
        "ma50_slope_ann_pct":              spy_feats.get("ma50_slope_ann_pct", 0.0),
        "realized_vol_atr20":              spy_feats.get("realized_vol_atr20", 1.0),
        "realized_vol_5d":                 spy_feats.get("realized_vol_5d", 15.0),
        "realized_vol_10d":                spy_feats.get("realized_vol_10d", 15.0),
        "realized_vol_20d":                spy_feats.get("realized_vol_20d", 15.0),
        "net_credit":                      net_credit_dollars,
        "spread_width":                    float(spread_width * 100),
        "max_loss_per_unit":               max_loss_dollars,
        "otm_pct":                         otm_pct,
        "contracts":                       float(contracts),
        "regime_neutral":                  1.0 if regime == "neutral" else 0.0,
        "strategy_type_bear_call_spread":  float(is_bear_call),
        "strategy_type_bull_put_spread":   float(is_bull_put),
        "strategy_type_iron_condor":       float(is_ic),
        "spread_type_call":                float(1 if spread_type == "bear_call" else 0),
        "spread_type_ic":                  float(is_ic),
        "spread_type_put":                 float(1 if spread_type == "bull_put" else 0),
    }
    means_lookup = dict(zip(feature_names, feature_means))
    row = [live_vals.get(name, means_lookup.get(name, 0.0)) for name in feature_names]
    return np.array(row, dtype=float)


def _days_since_last_trade(db_path: Path) -> int:
    try:
        import sqlite3
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.execute("SELECT MAX(entry_date) FROM trades WHERE status NOT IN ('rejected','cancelled','failed_open')")
            row = cur.fetchone()
            if row and row[0]:
                return (datetime.now().date() - datetime.fromisoformat(row[0]).date()).days
    except Exception:
        pass
    return 1


def _size_contracts(account_size: float, risk_pct: float, spread_width: float, credit_per_share: float, max_contracts: int) -> int:
    risk_dollars = account_size * risk_pct / 100.0
    max_loss = (spread_width - credit_per_share) * 100.0
    if max_loss <= 0:
        return 1
    return max(1, min(int(risk_dollars / max_loss), max_contracts))


# ─────────────────────────────────────────────────────────────────────────────
# ML filter
# ─────────────────────────────────────────────────────────────────────────────

class EnsembleFilter:
    def __init__(self, model_path: Path, stats_path: Path, threshold: float, fail_open: bool):
        self.threshold = threshold
        self.fail_open = fail_open
        self._model = None
        self._feature_names: List[str] = []
        self._feature_means: List[float] = []
        self._loaded = False
        self._load(model_path, stats_path)

    def _load(self, model_path: Path, stats_path: Path) -> None:
        try:
            from compass.ensemble_signal_model import EnsembleSignalModel
            m = EnsembleSignalModel(model_dir=str(model_path.parent))
            if m.load(model_path.name):
                self._model = m
                self._feature_names = list(m.feature_names)
                self._loaded = True
                logger.info("EnsembleFilter loaded: %d features, %d models", len(self._feature_names), len(m.calibrated_models))
        except Exception as exc:
            logger.error("EnsembleFilter load error: %s", exc)

        try:
            with open(stats_path) as f:
                stats = json.load(f)
            self._feature_names = stats.get("feature_names", self._feature_names)
            self._feature_means  = stats.get("feature_means", [])
        except Exception as exc:
            logger.warning("Feature stats load failed: %s", exc)

    @property
    def feature_names(self) -> List[str]:
        return self._feature_names

    @property
    def feature_means(self) -> List[float]:
        return self._feature_means

    def predict(self, feature_vec: np.ndarray, log_features: bool = False) -> Tuple[float, bool]:
        if not self._loaded or self._model is None:
            logger.warning("EnsembleFilter: model unavailable — fail_open=%s", self.fail_open)
            return (0.5, self.fail_open)
        try:
            feat_df = pd.DataFrame(feature_vec.reshape(1, -1), columns=self._feature_names)
            prob = float(self._model.predict_batch(feat_df)[0])
            approved = prob >= self.threshold
            if log_features:
                logger.info("ML features: %s", json.dumps({k: round(v, 4) for k, v in zip(self._feature_names, feature_vec.tolist())}))
            logger.info("EnsembleFilter: prob=%.4f threshold=%.2f → %s", prob, self.threshold, "APPROVED" if approved else "REJECTED")
            return (prob, approved)
        except Exception as exc:
            logger.error("EnsembleFilter predict error: %s", exc, exc_info=True)
            return (0.5, self.fail_open)


# ─────────────────────────────────────────────────────────────────────────────
# Main scanner
# ─────────────────────────────────────────────────────────────────────────────

class EXP700Scanner:
    """One-shot ML-filtered credit spread scanner for EXP-700 paper trading."""

    def __init__(self, config: Dict, dry_run: bool = False):
        self.cfg      = config
        self.dry_run  = dry_run
        strat = config.get("strategy", {})
        risk  = config.get("risk", {})
        ml    = config.get("ml_filter", {})

        self.target_dte    = int(strat.get("target_dte",         _EXP400_DEFAULTS["target_dte"]))
        self.otm_pct       = float(strat.get("otm_pct",          _EXP400_DEFAULTS["otm_pct"]))
        self.spread_width  = float(strat.get("spread_width",     _EXP400_DEFAULTS["spread_width"]))
        self.regime_config = strat.get("regime_config", {})
        ic_cfg = strat.get("iron_condor", {})
        self.ic_enabled    = bool(ic_cfg.get("enabled", True))
        self.ic_risk_pct   = float(ic_cfg.get("risk_per_trade",  3.5))

        self.vix_max_entry = float(risk.get("vix_max_entry",     _EXP400_DEFAULTS["vix_max_entry"]))
        self.max_risk_pct  = float(risk.get("max_risk_per_trade",_EXP400_DEFAULTS["max_risk_per_trade"]))
        self.max_contracts = int(risk.get("max_contracts",       _EXP400_DEFAULTS["max_contracts"]))
        self.account_size  = float(risk.get("account_size",      _EXP400_DEFAULTS["account_size"]))
        self.min_credit_pct= float(risk.get("min_credit_pct",    _EXP400_DEFAULTS["min_credit_pct"]))
        self.tickers       = config.get("tickers", ["SPY"])

        db_path = Path(config.get("db_path", str(DEFAULT_DB)))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path

        model_path = Path(ml.get("model_path", str(_DEFAULT_MODEL_PATH)))
        stats_path = Path(ml.get("feature_stats_path", str(_DEFAULT_STATS_PATH)))
        if not model_path.is_absolute():
            model_path = ROOT / model_path
        if not stats_path.is_absolute():
            stats_path = ROOT / stats_path

        self.ml_filter    = EnsembleFilter(model_path, stats_path,
                                           float(ml.get("probability_threshold", _ML_DEFAULTS["probability_threshold"])),
                                           bool(ml.get("fail_open", _ML_DEFAULTS["fail_open"])))
        self.log_features = bool(ml.get("log_features", _ML_DEFAULTS["log_features"]))

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

    def scan(self) -> List[Dict]:
        logger.info("=" * 70)
        logger.info("EXP-700 scan  %s  dry_run=%s", datetime.now(timezone.utc).isoformat(), self.dry_run)
        results = [self._scan_ticker(t) for t in self.tickers]
        for r in results:
            self._print_decision(r)
        return results

    def _scan_ticker(self, ticker: str) -> Dict:
        d = {"ticker": ticker, "timestamp": datetime.now(timezone.utc).isoformat(),
             "action": "no_trade", "reason": "", "regime": "unknown",
             "vix": None, "ml_probability": None, "ml_approved": None, "order_result": None}

        spy_df = _fetch_price_history(ticker, days=220)
        vix_df = _fetch_price_history("^VIX", days=110)
        if spy_df.empty:
            d["reason"] = "no_price_data"; return d

        spy_feats  = _compute_technicals(spy_df)
        vix_feats  = _compute_vix_features(vix_df)
        current_px = spy_feats["spy_price"]
        vix_level  = vix_feats["vix"]
        d["vix"] = round(vix_level, 2)

        if self.vix_max_entry > 0 and vix_level > self.vix_max_entry:
            d["reason"] = f"vix_gate: {vix_level:.1f} > {self.vix_max_entry}"; return d

        regime = _detect_regime(spy_df, vix_feats, self.regime_config)
        d["regime"] = regime

        spread_type = self._regime_to_spread(regime)
        if spread_type is None:
            d["reason"] = "no_regime_signal"; return d

        expiration   = _find_target_expiration(self.target_dte)
        actual_dte   = (datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.now().date()).days
        short_strike, long_strike = self._compute_strikes(current_px, spread_type)

        logger.info("Candidate: %s %s  exp=%s DTE=%d  short=%.2f  long=%.2f  px=%.2f",
                    ticker, spread_type, expiration, actual_dte, short_strike, long_strike, current_px)

        risk_pct = self.ic_risk_pct if spread_type == "iron_condor" else self.max_risk_pct
        credit_per_share = self._get_credit(ticker, expiration, short_strike, long_strike, spread_type)
        if credit_per_share <= 0:
            d["reason"] = "no_credit_estimate"; return d

        min_credit = self.spread_width * self.min_credit_pct / 100.0
        if credit_per_share < min_credit:
            d["reason"] = f"credit_too_low: {credit_per_share:.2f} < {min_credit:.2f}"; return d

        contracts = _size_contracts(self.account_size, risk_pct, self.spread_width, credit_per_share, self.max_contracts)
        iv_rank   = vix_feats.get("vix_percentile_20d", 25.0)

        feature_vec = _build_feature_vector(
            dte=actual_dte, hold_days=actual_dte,
            day_of_week=datetime.now().weekday(),
            days_since_last_trade=_days_since_last_trade(self.db_path),
            spy_feats=spy_feats, vix_feats=vix_feats, iv_rank=iv_rank,
            net_credit_dollars=credit_per_share * 100.0,
            spread_width=self.spread_width, otm_pct=self.otm_pct,
            contracts=contracts, spread_type=spread_type, regime=regime,
            feature_names=self.ml_filter.feature_names,
            feature_means=self.ml_filter.feature_means,
        )

        ml_prob, ml_approved = self.ml_filter.predict(feature_vec, log_features=self.log_features)
        d["ml_probability"] = round(ml_prob, 4)
        d["ml_approved"]    = ml_approved

        if not ml_approved:
            d["reason"] = f"ml_rejected: prob={ml_prob:.4f} < {self.ml_filter.threshold:.2f}"; return d

        opp = {"ticker": ticker, "type": spread_type, "expiration": expiration,
               "short_strike": short_strike, "long_strike": long_strike,
               "credit": round(credit_per_share, 2), "contracts": contracts,
               "experiment": "EXP-700", "ml_prob": round(ml_prob, 4),
               "ml_threshold": self.ml_filter.threshold, "source": "exp700_ml_scanner"}

        if self.dry_run or self._engine is None:
            d["action"] = "dry_run"
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
        ml_str = (f"  ML={d['ml_probability']:.4f} ({'PASS' if d['ml_approved'] else 'FAIL'})"
                  if d["ml_probability"] is not None else "")
        print(f"[EXP-700] {d['ticker']}  regime={d['regime']}  VIX={d['vix']}"
              f"{ml_str}  → {d['action'].upper()}"
              + (f"  ({d['reason']})" if d["reason"] else ""))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="EXP-700 ML-filtered paper trading scanner")
    parser.add_argument("--config",   default=str(DEFAULT_CONFIG))
    parser.add_argument("--env-file", default=".env.exp700")
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

    scanner = EXP700Scanner(config=config, dry_run=args.dry_run)
    results = scanner.scan()

    summary = {"experiment": "EXP-700", "timestamp": datetime.now(timezone.utc).isoformat(),
               "dry_run": args.dry_run, "results": results}
    print("\n--- SUMMARY ---")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
