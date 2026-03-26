"""
Backtesting Engine
Tests credit spread strategies against historical data using real option prices.
"""

import json
import logging
import math
import os
import random
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from shared.scheduler import MARKET_SCAN_TIMES as SCAN_TIMES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Price data helpers using system curl (bypasses Python's LibreSSL TLS 1.3 issue)
# ---------------------------------------------------------------------------
# Yahoo Finance requires TLS 1.3.  Python 3.9 on macOS ships with LibreSSL 2.8.3
# which cannot negotiate TLS 1.3, causing SSL handshakes to hang indefinitely.
# Python socket timeouts and SIGALRM cannot interrupt C-level LibreSSL SSL_read().
#
# Fix: use subprocess.run(['curl', ...]) with subprocess timeout.  curl on macOS
# uses the native SecureTransport stack and handles TLS 1.3 correctly.
# Cookie file is shared across calls so the rate-limit cookie is reused.

_YF_COOKIE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "yf_cookies.txt"
)


def _curl_yf_chart(ticker_encoded: str, period1: int, period2: int, *, timeout_secs: int = 30) -> dict:
    """Fetch Yahoo Finance v8/finance/chart JSON via system curl.

    Uses a persistent cookie file so the initial rate-limit cookie is reused on
    subsequent calls.  Returns empty dict on any failure (caller handles gracefully).
    """
    os.makedirs(os.path.dirname(_YF_COOKIE_FILE), exist_ok=True)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_encoded}"
        f"?period1={period1}&period2={period2}&interval=1d&includeAdjustedClose=true"
    )
    cmd = [
        "curl", "-s", "--max-time", str(timeout_secs),
        "-c", _YF_COOKIE_FILE, "-b", _YF_COOKIE_FILE,
        "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "-H", "Accept: application/json",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_secs + 5)
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}
        return json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        logger.warning("curl timed out fetching %s", ticker_encoded)
        return {}
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("curl yf fetch failed for %s: %s", ticker_encoded, e)
        return {}


def _yf_chart_to_df(chart_data: dict) -> pd.DataFrame:
    """Convert Yahoo Finance v8 chart JSON to a pandas DataFrame (yfinance-compatible)."""
    try:
        results = chart_data.get("chart", {}).get("result", [])
        if not results:
            return pd.DataFrame()
        r = results[0]
        timestamps = r.get("timestamp", [])
        if not timestamps:
            return pd.DataFrame()

        quote = r.get("indicators", {}).get("quote", [{}])[0]
        adjclose_list = r.get("indicators", {}).get("adjclose", [{}])
        closes = (adjclose_list[0].get("adjclose") or []) if adjclose_list else []
        if not closes or all(v is None for v in closes):
            closes = quote.get("close", [])

        # Unix seconds → tz-naive DatetimeIndex at midnight (trading date)
        idx = pd.to_datetime(timestamps, unit="s").normalize()

        df = pd.DataFrame({
            "Open":   quote.get("open",   [None] * len(timestamps)),
            "High":   quote.get("high",   [None] * len(timestamps)),
            "Low":    quote.get("low",    [None] * len(timestamps)),
            "Close":  closes,
            "Volume": quote.get("volume", [0]    * len(timestamps)),
        }, index=idx)
        return df.dropna(subset=["Close"])
    except Exception as e:
        logger.warning("Failed to parse yf chart data: %s", e)
        return pd.DataFrame()


def _yf_download_safe(
    ticker: str,
    start: str,
    end: str,
    *,
    timeout_secs: int = 30,
    **_kwargs,   # absorb progress=, auto_adjust=, etc. from old call sites
) -> pd.DataFrame:
    """Download Yahoo Finance data via system curl (TLS 1.3 compatible).

    Retries once on empty response — the first call establishes the Yahoo Finance
    session cookie; the second call uses it and typically succeeds.
    """
    try:
        p1 = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
        p2 = int(datetime.strptime(end,   "%Y-%m-%d").timestamp())
    except ValueError as e:
        logger.warning("_yf_download_safe: invalid date: %s", e)
        return pd.DataFrame()

    ticker_enc = ticker.replace("^", "%5E")
    chart = _curl_yf_chart(ticker_enc, p1, p2, timeout_secs=timeout_secs)

    # First call may be rate-limited (sets cookie); retry once
    if not chart.get("chart", {}).get("result"):
        logger.debug("yf first call empty for %s — retrying once (cookie set)", ticker)
        chart = _curl_yf_chart(ticker_enc, p1, p2, timeout_secs=timeout_secs)

    df = _yf_chart_to_df(chart)
    if df.empty:
        logger.warning("yf download returned no data for %s (%s–%s)", ticker, start, end)
    else:
        logger.debug("yf download: %s  %d bars", ticker, len(df))
    return df


def _yf_history_safe(
    ticker: str,
    *,
    start: datetime,
    end: datetime,
    timeout_secs: int = 30,
) -> pd.DataFrame:
    """yf.Ticker.history() replacement using system curl."""
    return _yf_download_safe(
        ticker,
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
        timeout_secs=timeout_secs,
    )


# Scan times that have actual option bars (9:15 is pre-open; bars start at 9:30)
_FIRST_BAR_HOUR = 9
_FIRST_BAR_MINUTE = 30


def _nearest_friday_expiration(
    date: datetime, target_dte: int = 35, min_dte: int = 25
) -> datetime:
    """Return the nearest Friday options expiration around *target_dte* days out.

    Options expire on Fridays (weeklies / monthlies).  A naive
    ``date + timedelta(35)`` usually lands on a weekday with no contracts,
    causing ``get_available_strikes`` to return nothing.  This function snaps
    to the Friday closest to the target, ensuring at least *min_dte* days of
    time value remain.

    Args:
        date: Entry / evaluation date.
        target_dte: Desired days-to-expiration (default 35).
        min_dte: Minimum acceptable DTE (default 25).

    Returns:
        A datetime set to midnight of the target Friday.
    """
    target = date + timedelta(days=target_dte)
    # (weekday - 4) % 7: number of days since the most-recent Friday
    days_since_friday = (target.weekday() - 4) % 7
    friday_before = target - timedelta(days=days_since_friday)
    friday_after = friday_before + timedelta(days=7)

    min_exp = date + timedelta(days=min_dte)

    # Prefer the closer Friday; fall through to friday_after if too soon
    if days_since_friday <= 3 and friday_before >= min_exp:
        return friday_before
    return friday_after


def _nearest_mwf_expiration(
    date: datetime, target_dte: int = 35, min_dte: int = 25
) -> datetime:
    """Return the nearest Mon/Wed/Fri expiration around *target_dte* days out.

    SPY, QQQ, and IWM have three weekly expiration cycles (Mon, Wed, Fri).
    This supersedes the Friday-only logic so we capture all available dates.
    The Polygon API naturally enforces historical availability — if a Monday
    expiration didn't exist in 2020, no contracts will be returned and the
    trade is skipped.

    Args:
        date: Entry / evaluation date.
        target_dte: Desired days-to-expiration (default 35).
        min_dte: Minimum acceptable DTE (default 25).

    Returns:
        A datetime set to midnight of the nearest Mon/Wed/Fri.
    """
    _MWF = {0, 2, 4}  # Monday=0, Wednesday=2, Friday=4
    target = date + timedelta(days=target_dte)
    min_exp = date + timedelta(days=min_dte)

    # Nearest MWF at or before target (max 2 days back)
    before = target
    for _ in range(3):
        if before.weekday() in _MWF:
            break
        before -= timedelta(days=1)

    # Nearest MWF at or after target (max 3 days forward)
    after = target
    for _ in range(4):
        if after.weekday() in _MWF:
            break
        after += timedelta(days=1)

    days_back = (target - before).days
    days_fwd  = (after - target).days

    # Prefer the closer candidate; if both are equidistant take the later one
    # (more time value), but only if it meets min_dte.
    if before >= min_exp and days_back <= days_fwd:
        return before
    if after >= min_exp:
        return after

    # Neither candidate meets min_dte — advance to next MWF past min_exp
    candidate = min_exp
    for _ in range(7):
        if candidate.weekday() in _MWF:
            return candidate
        candidate += timedelta(days=1)
    return after  # unreachable in practice


# SPY added Tuesday/Thursday weekly expirations in September 2022.
# From this date forward, we target all 5 weekdays (Mon-Fri) instead of
# only Mon/Wed/Fri.  Polygon enforces historical availability — any Tue/Thu
# expiration that didn't exist yet returns no contracts and the trade is skipped.
_SPY_TUETHU_START = datetime(2022, 9, 12)


def _nearest_weekday_expiration(
    date: datetime, target_dte: int = 35, min_dte: int = 25
) -> datetime:
    """Return the nearest Mon–Fri expiration around *target_dte* days out.

    Used for SPY post-2022-09-12 when Tue/Thu weeklies are available.
    All 5 weekdays are candidates; Polygon enforces which ones actually
    had listed contracts on a given date.

    Args:
        date: Entry / evaluation date.
        target_dte: Desired days-to-expiration (default 35).
        min_dte: Minimum acceptable DTE (default 25).

    Returns:
        A datetime set to midnight of the nearest weekday expiration.
    """
    _WEEKDAYS = {0, 1, 2, 3, 4}  # Mon=0 … Fri=4
    target = date + timedelta(days=target_dte)
    min_exp = date + timedelta(days=min_dte)

    before = target
    for _ in range(5):
        if before.weekday() in _WEEKDAYS:
            break
        before -= timedelta(days=1)

    after = target
    for _ in range(5):
        if after.weekday() in _WEEKDAYS:
            break
        after += timedelta(days=1)

    days_back = (target - before).days
    days_fwd  = (after - target).days

    if before >= min_exp and days_back <= days_fwd:
        return before
    if after >= min_exp:
        return after

    candidate = min_exp
    for _ in range(7):
        if candidate.weekday() in _WEEKDAYS:
            return candidate
        candidate += timedelta(days=1)
    return after  # unreachable in practice


class Backtester:
    """
    Backtest credit spread strategies on historical data.

    When an ``HistoricalOptionsData`` instance is provided, real Polygon
    option prices are used for entry credits, daily marks, and exit P&L.
    Otherwise falls back to the legacy heuristic mode (for quick testing).
    """

    def __init__(self, config: Dict, historical_data=None, otm_pct: float = 0.05, seed: Optional[int] = None):
        """
        Initialize backtester.

        Args:
            config: Configuration dictionary
            historical_data: Optional HistoricalOptionsData instance for
                             real pricing.  None = legacy heuristic mode.
            otm_pct: How far OTM the short strike is as a fraction of price
                     (default 0.05 = 5% OTM).  Applies to both puts and calls.
        """
        self.config = config
        self.backtest_config = config['backtest']
        self.strategy_params = config['strategy']
        self.risk_params = config['risk']

        # Safety warning: recommended max 5% for live trading (CRITIQUE §5).
        # Backtesting at higher risk is allowed to understand return profiles.
        # Cap only applies if backtest.risk_cap is explicitly set (default: no cap for research).
        _LIVE_RISK_GUIDELINE = 5.0
        _MAX_RISK_CAP = self.backtest_config.get('risk_cap', 25.0)  # hard ceiling
        _configured_risk = self.risk_params.get('max_risk_per_trade', 2.0)
        if _configured_risk > _LIVE_RISK_GUIDELINE:
            logger.warning(
                "max_risk_per_trade=%.1f%% exceeds %.1f%% live-trading guideline (CRITIQUE §5) — "
                "acceptable for backtesting research only",
                _configured_risk, _LIVE_RISK_GUIDELINE,
            )
        if _configured_risk > _MAX_RISK_CAP:
            logger.warning(
                "max_risk_per_trade=%.1f%% exceeds hard ceiling %.1f%% — capping",
                _configured_risk, _MAX_RISK_CAP,
            )
            self.risk_params = {**self.risk_params, 'max_risk_per_trade': _MAX_RISK_CAP}

        self.starting_capital = self.backtest_config['starting_capital']
        self.commission = self.backtest_config['commission_per_contract']
        self.slippage = self.backtest_config['slippage']
        # Phase 2: compounding + flat-risk sizing
        # compound=True → use current equity (self.capital) for position sizing
        # sizing_mode='flat' → use max_risk_per_trade% directly (bypasses IV-scaled sizer)
        self._compound = self.backtest_config.get('compound', False)
        self._sizing_mode = self.backtest_config.get('sizing_mode', 'iv_scaled')
        # Additional friction when closing at a stop loss (adverse market conditions).
        # Entry slippage is already modeled via bar high-low; exit at stop happens in
        # fast markets where bid/ask is wider and fills are worse.
        self.exit_slippage = self.backtest_config.get('exit_slippage', 0.10)
        # P2: Slippage brutality tests — multiply all slippage (entry + exit) by this factor.
        # 1.0 = baseline, 2.0 = 2x brutality, 3.0 = 3x brutality.
        self._slippage_multiplier: float = float(self.backtest_config.get('slippage_multiplier', 1.0))
        self.otm_pct = otm_pct

        self.historical_data = historical_data
        self._use_real_data = historical_data is not None

        # Delta-based strike selection (replaces static OTM% when enabled)
        self._use_delta_selection = self.strategy_params.get("use_delta_selection", False)
        self._target_delta = float(self.strategy_params.get("target_delta", 0.12))

        # IV-scaled sizing state (Upgrade 3) — updated per trading day in run_backtest
        self._iv_rank_by_date: dict = {}
        self._current_iv_rank: float = 25.0       # default = standard regime
        self._current_portfolio_risk: float = 0.0

        # Raw VIX level — populated alongside IV rank, used for regime filter
        self._vix_by_date: dict = {}
        self._vix3m_by_date: dict = {}            # VIX3M for term structure ratio (combo regime v2)
        self._current_vix: float = 20.0           # default = low-vol regime

        # Seasonal sizing overlay: {month_str: multiplier} e.g. {"5": 1.2, "12": 0.8}
        # Applied to trade_dollar_risk in both flat and iv_scaled sizing modes.
        self._seasonal_sizing: dict = self.strategy_params.get('seasonal_sizing', {})
        self._current_seasonal_mult: float = 1.0

        # COMPASS: Composite Macro Position & Sector Signal
        # compass_enabled: apply risk_appetite sizing multiplier (r=-0.250 vs forward returns)
        #   <30→1.2x, <45→1.1x, >75→0.85x, >65→0.95x, else 1.0x
        # compass_rrg_filter: block bull puts when XLI AND XLF both Lagging/Weakening
        self._compass_enabled: bool = bool(self.strategy_params.get('compass_enabled', False))
        self._compass_rrg_filter: bool = bool(self.strategy_params.get('compass_rrg_filter', False))
        self._compass_risk_appetite_by_date: dict = {}  # Timestamp → risk_appetite score (0-100)
        self._compass_rrg_xli_by_date: dict = {}        # Timestamp → XLI rrg_quadrant str
        self._compass_rrg_xlf_by_date: dict = {}        # Timestamp → XLF rrg_quadrant str
        self._current_compass_mult: float = 1.0         # updated daily from forward-filled weekly data
        self._current_compass_rrg_block: bool = False   # True when XLI+XLF both Lagging/Weakening

        # Realized-vol state (fixes constant σ=25% bias in delta selection)
        self._realized_vol_by_date: dict = {}
        self._current_realized_vol: float = 0.25  # fallback = 25%

        # Combo regime detector — multi-signal direction filter (Phase 6)
        # regime_mode='combo' → ComboRegimeDetector (MANDATORY default for all experiments)
        # regime_mode='ma'    → legacy single-MA behavior (backward compat only)
        self._regime_mode: str = self.strategy_params.get('regime_mode', 'combo')
        self._regime_config: dict = self.strategy_params.get('regime_config', {})
        self._regime_by_date: dict = {}  # populated by _build_combo_regime_series

        # DTE targeting — configurable for optimization sweep
        self._target_dte: int = int(self.strategy_params.get('target_dte', 35))
        self._min_dte: int = int(self.strategy_params.get('min_dte', 25))

        # Monte Carlo randomization config.
        # mode='dte'  (legacy) → only DTE is randomized (original behaviour, default).
        # mode='full' → DTE + slippage jitter + entry-day shift + trade skip + credit jitter.
        _mc = self.backtest_config.get('monte_carlo', {})
        self._mc_mode: str = _mc.get('mode', 'dte')
        self._mc_dte_lo: int = int(_mc.get('dte_lo', 28))
        self._mc_dte_hi: int = int(_mc.get('dte_hi', 42))
        # ── Full-mode jitter knobs (all default to 0 = disabled) ──────────
        # slippage_jitter_pct: ±fraction applied to entry AND exit slippage each trade.
        #   0.50 = ±50 % of the configured value → uniform factor in [0.50, 1.50].
        self._mc_slippage_jitter: float = float(_mc.get('slippage_jitter_pct', 0.0))
        # entry_day_jitter: ±calendar days added to the expiration reference date.
        #   2 → expiration computed as if entering up to 2 days earlier or later.
        self._mc_entry_day_jitter: int = int(_mc.get('entry_day_jitter', 0))
        # trade_skip_pct: probability [0, 1) that a qualifying trade is randomly dropped.
        #   0.15 = skip 15 % of trades; tests whether results depend on every single entry.
        self._mc_trade_skip_pct: float = float(_mc.get('trade_skip_pct', 0.0))
        # credit_jitter: ±absolute addition to BACKTEST_CREDIT_FRACTION (heuristic mode only).
        #   0.10 → credit fraction drawn from U(0.25, 0.45) when baseline is 0.35.
        self._mc_credit_jitter: float = float(_mc.get('credit_jitter', 0.0))
        self._rng: Optional[random.Random] = random.Random(seed) if seed is not None else None
        self._current_trade_dte: int = self._target_dte
        self._current_trade_min_dte: int = self._min_dte
        # Entry-day shift sampled once per trading day in full MC mode (days offset).
        self._mc_entry_day_shift: int = 0

        # Profit target — configurable (config stores as %, e.g. 50 → close at 50% of credit)
        self._profit_target_pct: float = float(self.risk_params.get('profit_target', 50)) / 100.0

        # Direction filter — "both" | "bull_put" | "bear_call"
        # Controls which spread types are entered during the scan loop.
        self._direction: str = self.strategy_params.get('direction', 'both')

        # Trend MA period for direction filter (default 20).
        # Use 50 to avoid whipsawing on short-term MA crosses (e.g. brief 2024 dips).
        self._trend_ma_period: int = int(self.strategy_params.get('trend_ma_period', 20))
        # Trend MA type: 'sma' (simple) or 'ema' (exponential).
        self._trend_ma_type: str = self.strategy_params.get('trend_ma_type', 'sma')

        # P1: Portfolio-level exposure constraint — sum of max losses across all open
        # positions as a % of current equity.  Default 100% = no constraint.
        # Set to e.g. 30 to cap combined exposure at 30% of equity.
        self._max_portfolio_exposure_pct: float = float(
            self.backtest_config.get('max_portfolio_exposure_pct', 100.0)
        )

        # P7: Outlier month exclusion — list of "YYYY-MM" strings.
        # On days falling within these months, NO new entries are opened.
        # Existing positions are still managed (stops, profit targets, expiration).
        # Example: ["2020-03", "2023-01"] strips COVID crash + Jan 2023 spike.
        _exc = self.backtest_config.get('exclude_months', [])
        self._exclude_months: set = set(_exc) if _exc else set()

        # Liquidity framework: volume gate + adaptive sizing
        # volume_gate: hard reject when min_vol < min_vol_ratio × contracts
        # volume_size_cap_pct: cap contracts at this fraction of min daily volume
        # oi_gate: disabled by default — OI data absent on standard Polygon tier
        self._volume_gate:    bool  = self.backtest_config.get('volume_gate', False)
        self._min_vol_ratio:  float = float(self.backtest_config.get('min_volume_ratio', 50))
        self._vol_size_cap:   float = float(self.backtest_config.get('volume_size_cap_pct', 0.02))
        self._oi_gate:        bool  = self.backtest_config.get('oi_gate', False)
        self._oi_min_factor:  float = float(self.backtest_config.get('oi_min_factor', 2))
        self._volume_skipped: int   = 0   # exposed in results dict

        # Trade history
        self.trades = []
        self.equity_curve = []
        # P1-D: ruin stop — set True when capital ≤ 0; blocks all new entries thereafter
        self._ruin_triggered: bool = False
        # P5a: Friday fallback trigger counter (incremented during run_backtest)
        self._friday_fallback_count = 0

        mode = "real data" if self._use_real_data else "no historical data"
        logger.info("Backtester initialized (%s mode, delta_selection=%s)",
                    mode, self._use_delta_selection)

    def _vix_scaled_exit_slippage(self) -> float:
        """Return exit slippage adjusted for the current VIX regime.

        During low-vol regimes (VIX ≤ 20) exit slippage equals the base value.
        During stress (VIX rising toward 40) spreads widen so buy-back friction
        increases proportionally, capped at 3× to avoid implausible estimates.

        Formula: base * slippage_multiplier * min(3.0, 1 + max(0, (VIX − 20) × 0.1))
          VIX=20 → 1.0×   VIX=30 → 2.0×   VIX=40+ → 3.0× (cap)

        Design note: slippage_multiplier and vix_scale compound multiplicatively.
        At multiplier=2, VIX=30: result is 4× base (not 2×).  This is intentional —
        brutality tests probe "how bad could it get"; VIX stress further amplifies
        that, making the test more conservative.  Use multiplier=1 to isolate
        pure VIX scaling.
        """
        vix_scale = min(3.0, 1.0 + max(0.0, (self._current_vix - 20.0) * 0.1))
        base = self.exit_slippage * self._slippage_multiplier * vix_scale
        if self._rng is not None and self._mc_mode == 'full' and self._mc_slippage_jitter > 0:
            factor = self._rng.uniform(
                1.0 - self._mc_slippage_jitter, 1.0 + self._mc_slippage_jitter
            )
            base = max(0.0, base * factor)
        return base

    def _mc_jitter_slippage(self, base: float) -> float:
        """Return entry slippage jittered by ±_mc_slippage_jitter in full MC mode.

        Only active when a seed has been provided (self._rng is not None) and
        mc_mode == 'full'.  Falls through unchanged in all other cases so that
        legacy dte-only runs are unaffected.
        """
        if self._rng is None or self._mc_mode != 'full' or self._mc_slippage_jitter <= 0:
            return base
        factor = self._rng.uniform(
            1.0 - self._mc_slippage_jitter, 1.0 + self._mc_slippage_jitter
        )
        return max(0.0, base * factor)

    def _mc_should_skip_trade(self) -> bool:
        """Return True if this trade should be randomly dropped (full MC mode only).

        Probability is self._mc_trade_skip_pct.  Used to test whether strategy
        returns depend on capturing every qualifying trade.
        """
        if self._rng is None or self._mc_mode != 'full' or self._mc_trade_skip_pct <= 0:
            return False
        return self._rng.random() < self._mc_trade_skip_pct

    def run_backtest(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Dict:
        """
        Run backtest for a ticker over date range.

        Args:
            ticker: Stock ticker
            start_date: Start date for backtest
            end_date: End date for backtest

        Returns:
            Dictionary with backtest results
        """
        logger.info(f"Starting backtest for {ticker}: {start_date} to {end_date}")

        # Warmup window: MA_PERIOD trading days ≈ MA_PERIOD * 1.4 calendar days.
        # MA50 needs ~70 calendar days; MA20 needs ~28.  Add 15-day buffer.
        _MA_WARMUP_DAYS = max(30, int(self._trend_ma_period * 1.4) + 15)
        data_fetch_start = start_date - timedelta(days=_MA_WARMUP_DAYS)

        # Get historical price data (with MA warmup prefix).
        # Crypto mode: use adapter's underlying prices instead of Yahoo Finance.
        from backtest.crypto_data_adapter import CryptoDataAdapter
        from compass.crypto.risk_gate import check_all_gates
        _is_crypto = isinstance(self.historical_data, CryptoDataAdapter)

        if _is_crypto:
            price_data = self.historical_data.get_underlying_prices(
                ticker, data_fetch_start, end_date
            )
        else:
            price_data = self._get_historical_data(ticker, data_fetch_start, end_date)

        if price_data.empty:
            logger.error(f"No historical data for {ticker}")
            return {}

        # Build per-date IV Rank lookup from VIX data (Upgrade 3: IV-scaled sizing).
        # Skip for crypto: no VIX series available; IV rank gate is handled by risk gate.
        if _is_crypto:
            self._iv_rank_by_date = {}
        else:
            # Uses a 252-trading-day rolling IV Rank so the backtester sizes positions
            # the same way the live scanner does — small in low-vol, large in high-vol.
            self._iv_rank_by_date = self._build_iv_rank_series(data_fetch_start, end_date)

        # Build per-date realized vol for delta strike selection (fixes σ=25% constant)
        self._realized_vol_by_date = self._build_realized_vol_series(price_data)

        # Phase 6: Combo regime — build direction-label series after VIX data is available
        if self._regime_mode == 'combo':
            self._build_combo_regime_series(price_data)
        elif self._regime_mode == 'crypto_composite':
            self._build_crypto_regime_series(price_data)

        # COMPASS: load macro score + sector RRG series from macro_state.db if enabled
        if self._compass_enabled or self._compass_rrg_filter:
            self._build_compass_series(start_date, end_date)

        # Store price data for expiration fallback (underlying-based settlement when
        # option price data is missing — avoids false max-loss recording)
        self._price_data = price_data

        # Strip timezone for consistent date-only comparison
        start_date = start_date.replace(tzinfo=None) if hasattr(start_date, 'tzinfo') and start_date.tzinfo else start_date
        end_date = end_date.replace(tzinfo=None) if hasattr(end_date, 'tzinfo') and end_date.tzinfo else end_date

        # Initialize portfolio
        self.capital = self.starting_capital
        self._peak_capital = self.starting_capital   # high-water mark for CB
        self.trades = []
        self.equity_curve = [(start_date, self.capital)]
        # P5a: reset fallback counter at the start of each backtest run
        self._friday_fallback_count = 0
        # Reset volume gate skip counter
        self._volume_skipped = 0
        # P1-D: reset ruin flag
        self._ruin_triggered = False

        open_positions = []

        if price_data.index.tz is not None:
            price_data.index = price_data.index.tz_localize(None)
        trading_dates = set(price_data.index)

        # Simulate trading day by day — start at backtest start, not the warmup prefix
        current_date = start_date

        def _prev_trading_val(d, before, default):
            """Return the most recent value in dict d with key strictly < before.

            Using max(k < today) handles weekends correctly: on Monday, date-1 gives
            Sunday which is absent from the trading-day-keyed dict.  This finds Friday.
            """
            keys = [k for k in d if k < before]
            return d[max(keys)] if keys else default

        while current_date <= end_date:
            lookup_date = pd.Timestamp(current_date.date())

            if lookup_date not in trading_dates:
                current_date += timedelta(days=1)
                continue

            current_price = float(price_data.loc[lookup_date, 'Close'])

            # Set current IV Rank + VIX level + portfolio heat for sizing calculations.
            # Fix: use the most recent prior trading day's close to avoid lookahead.
            # At 9:30 AM entry time, today's VIX/IV-rank is unknown (set at 4:00 PM).
            # max(k < today) handles Mondays correctly (skips non-trading Saturday/Sunday).
            self._current_iv_rank = _prev_trading_val(self._iv_rank_by_date, lookup_date, 25.0)
            self._current_vix = _prev_trading_val(self._vix_by_date, lookup_date, 20.0)
            self._current_realized_vol = _prev_trading_val(self._realized_vol_by_date, lookup_date, 0.25)
            if self._seasonal_sizing:
                self._current_seasonal_mult = float(
                    self._seasonal_sizing.get(str(current_date.month),
                    self._seasonal_sizing.get(current_date.month, 1.0))
                )

            # COMPASS: forward-fill weekly risk_appetite + RRG quadrants to daily granularity.
            # max(k <= today) gives the most recent weekly snapshot not after today.
            if self._compass_enabled or self._compass_rrg_filter:
                _compass_keys = [k for k in self._compass_risk_appetite_by_date if k <= lookup_date]
                if _compass_keys:
                    _ck = max(_compass_keys)
                    # Signal A: risk_appetite sizing (r=-0.250 vs forward returns — dominant signal)
                    ra = self._compass_risk_appetite_by_date[_ck]
                    if ra < 30:                              # extreme fear: IV richest, sell more
                        self._current_compass_mult = 1.2
                    elif ra < 45:                            # elevated fear
                        self._current_compass_mult = 1.1
                    elif ra > 75:                            # complacency: r=-0.250 predicts lower returns
                        self._current_compass_mult = 0.85
                    elif ra > 65:                            # mild complacency
                        self._current_compass_mult = 0.95
                    else:                                    # neutral zone
                        self._current_compass_mult = 1.0
                    # Signal B: XLI+XLF dual-Lagging block (genuine economic deterioration)
                    xli = self._compass_rrg_xli_by_date.get(_ck, "Unknown")
                    xlf = self._compass_rrg_xlf_by_date.get(_ck, "Unknown")
                    self._current_compass_rrg_block = (
                        xli in ("Lagging", "Weakening") and xlf in ("Lagging", "Weakening")
                    )

            logger.debug(
                "%s  price=%.2f  open_positions=%d  ivr=%.0f  vix=%.1f",
                current_date.strftime("%Y-%m-%d"), current_price,
                len(open_positions), self._current_iv_rank, self._current_vix,
            )

            # Check existing positions
            open_positions = self._manage_positions(
                open_positions, current_date, current_price, ticker
            )

            # Portfolio risk computed AFTER managing positions so that same-day
            # expirations/stops are excluded from new-entry sizing calculations.
            self._current_portfolio_risk = sum(
                p.get('max_loss', 0) * p.get('contracts', 1) * 100
                for p in open_positions
            )

            # VIX spike: force-close ALL open positions when VIX crosses exit threshold.
            # Models a managed risk-off exit (e.g. VIX 20→30 in a crash).
            #
            # PnL assumption: -50% of max loss per position.
            # Rationale: when VIX first crosses the threshold the spread has widened
            # significantly but underlying hasn't moved to full-stop territory yet.
            # Empirically, closing credit spreads when VIX first spikes ~25-30 typically
            # costs 1–2× the original credit received (i.e. 20-40% of max loss on a
            # 5-wide spread with 8% credit). -50% of max loss is a conservative mid-point
            # that avoids both the "free exit" (PnL=0) and "full stop-out" extremes.
            _vix_close_all = self.strategy_params.get('vix_close_all', 0)
            if _vix_close_all > 0 and self._current_vix > _vix_close_all and open_positions:
                logger.debug(
                    "%s  VIX %.1f > close_all %.0f — force-closing %d positions at -50%% max loss",
                    current_date.strftime("%Y-%m-%d"), self._current_vix,
                    _vix_close_all, len(open_positions),
                )
                for _pos in list(open_positions):
                    _max_loss_dollars = _pos.get('max_loss', 0) * _pos.get('contracts', 1) * 100
                    # P1-A fix: include exit commission — consistent with every other exit path.
                    _pnl = -0.50 * _max_loss_dollars - _pos.get('commission', 0)
                    self._record_close(_pos, current_date, _pnl, 'vix_close_all')
                open_positions = []
                # _current_portfolio_risk is stale (reflects pre-close positions) after
                # vix_close_all. In practice vix_close_all VIX levels also trigger
                # _vix_too_high, which sets _skip_new_entries=True and blocks new entries
                # before any sizing call that reads _current_portfolio_risk.

            # Look for new opportunities.
            # Real-data mode: simulate all 14 intraday scan times per trading day.
            # Heuristic mode: one scan per week on Monday (backward compat).

            # Drawdown circuit breaker: pause NEW entries when equity drops too far.
            # In compound mode: uses high-water mark (peak equity) as the reference.
            # In fixed mode: uses starting capital as the reference (original behavior).
            # Threshold is configurable via risk.drawdown_cb_pct (default -20%).
            _cb_threshold = -abs(self.risk_params.get('drawdown_cb_pct', 20)) / 100
            if self._compound:
                self._peak_capital = max(self._peak_capital, self.capital)
                _drawdown_pct = (self.capital - self._peak_capital) / self._peak_capital
            else:
                _drawdown_pct = (self.capital - self.starting_capital) / self.starting_capital

            # IV Rank entry gate: only sell premium when implied vol overstates realized vol
            # (market is pricing in fear → favorable conditions for premium selling).
            # Set iv_rank_min_entry=0 (default) to disable; 20-25 is a sensible floor.
            _iv_rank_min = self.strategy_params.get('iv_rank_min_entry', 0)
            _iv_too_low  = _iv_rank_min > 0 and self._current_iv_rank < _iv_rank_min

            # VIX regime entry gate: block new entries when raw VIX exceeds threshold.
            # vix_max_entry=0 (default) disables the filter.
            # Typical values: 20 (strict), 25 (moderate), 30 (permissive).
            _vix_max = self.strategy_params.get('vix_max_entry', 0)
            _vix_too_high = _vix_max > 0 and self._current_vix > _vix_max

            # P7: Outlier month exclusion — skip new entries but still manage positions
            _month_str = current_date.strftime("%Y-%m")
            _excluded_month = _month_str in self._exclude_months

            _skip_new_entries = (
                _drawdown_pct < _cb_threshold
                or _iv_too_low
                or _vix_too_high
                or _excluded_month
                or self._ruin_triggered  # P1-D: halt all entries after capital reaches zero
            )

            # Crypto risk gate: additional entry filters for crypto ETF backtests.
            # Evaluated after the standard gates so it can only add blocks, not remove them.
            if not _skip_new_entries and self._regime_mode == 'crypto_composite':
                _idx = price_data.index.searchsorted(lookup_date, side='left') - 1
                _prev_close = float(price_data.iloc[_idx]['Close']) if _idx >= 0 else current_price
                _gate_result = check_all_gates({
                    'btc_price_now': current_price,
                    'etf_close_price': _prev_close,
                    'day_of_week': current_date.weekday(),
                    'dte': self._current_trade_dte,
                })
                if not _gate_result['allowed']:
                    _skip_new_entries = True
                    logger.debug(
                        "%s  Crypto risk gate blocked: %s",
                        current_date.strftime("%Y-%m-%d"),
                        [g['reason'] for g in _gate_result['gates'] if g['blocked']],
                    )

            _ic_enabled = self.strategy_params.get('iron_condor', {}).get('enabled', False)

            _want_puts  = self._direction in ('both', 'bull_put')
            _want_calls = self._direction in ('both', 'bear_call')

            # Phase 6: Combo regime override — replaces single-MA gate in opportunity finders
            if self._regime_mode == 'combo':
                _regime_today = self._regime_by_date.get(pd.Timestamp(current_date.date()), 'neutral')
                _ic_neutral_only = self.strategy_params.get('iron_condor', {}).get('neutral_regime_only', False)
                if _ic_neutral_only:
                    # IC-in-NEUTRAL mode: BULL→puts only, NEUTRAL→IC only, BEAR→calls only
                    # ic_vix_min: if VIX < threshold on a NEUTRAL day, fall back to bull puts
                    _ic_vix_min = self.strategy_params.get('iron_condor', {}).get('vix_min', 0)
                    _ic_vix_ok = (self._current_vix >= _ic_vix_min) if _ic_vix_min > 0 else True
                    _want_puts  = (_regime_today == 'bull') or (_regime_today == 'neutral' and not _ic_vix_ok)
                    _want_calls = _regime_today == 'bear'
                    _ic_enabled = _regime_today == 'neutral' and _ic_vix_ok
                else:
                    _want_puts  = _regime_today in ('bull', 'neutral')
                    _want_calls = _regime_today == 'bear'
                    # ic_vix_min gates IC fallback in BULL regime only — blocks dangerous
                    # BULL-regime fallback ICs in low-vol fast-recovery markets (e.g. 2024)
                    # while retaining ICs in NEUTRAL/BEAR regimes where they are appropriate.
                    _ic_vix_min_bull = self.strategy_params.get('iron_condor', {}).get('vix_min', 0)
                    if (_ic_vix_min_bull > 0 and _regime_today == 'bull'
                            and self._current_vix < _ic_vix_min_bull):
                        _ic_enabled = False

            # COMPASS RRG filter: block bull puts when economic backbone deteriorates AND
            # the combo regime detector independently confirms a BEAR regime.
            # Regime confirmation prevents false blocks during bull-market sector rotations
            # (e.g. 2021 Nov-Dec when XLF lagged XLK but SPY was at all-time highs).
            # Without confirmation: 2020=42% block, 2021=38% block → return drag.
            # With confirmation: only fires ~15-20% of weeks during genuine bear regimes.
            if self._compass_rrg_filter and self._current_compass_rrg_block:
                _rrg_block = True
                if self._regime_mode == 'combo':
                    # _regime_today is guaranteed to exist here: set in the combo block above
                    _rrg_block = (_regime_today == 'bear')
                if _rrg_block:
                    _want_puts = False

            if self._use_real_data:
                # Track (expiration, short_strike, option_type) already entered today.
                # Prevents multiple intraday scans from opening duplicate positions on the
                # same (expiration, strike) when a single daily entry is intended.
                _entered_today: set = set()

                # Build a count dict of (expiration, short_strike, type) for ALL currently
                # open positions.  max_positions_per_expiration=N (default 1) allows up to N
                # stacked identical positions — useful when SPY barely moves day-to-day so the
                # same strike recurs.  N=1 reproduces the original one-per-key behaviour.
                _max_per_key = int(self.strategy_params.get('max_positions_per_expiration', 1))
                _open_key_counts: dict = {}
                for _op in open_positions:
                    _exp = _op.get('expiration')
                    if _op.get('type') == 'iron_condor':
                        _k = (_exp, _op['short_strike'], _op.get('call_short_strike'), 'IC')
                    else:
                        _t = 'C' if _op.get('option_type') == 'C' else 'P'
                        _k = (_exp, _op['short_strike'], _t)
                    _open_key_counts[_k] = _open_key_counts.get(_k, 0) + 1

                def _exposure_ok(pos) -> bool:
                    """Return False if adding pos would exceed portfolio max-loss exposure cap."""
                    if self._max_portfolio_exposure_pct >= 100.0:
                        return True
                    current_max_loss = sum(p['max_loss'] * p['contracts'] * 100 for p in open_positions)
                    new_max_loss = pos['max_loss'] * pos['contracts'] * 100
                    # current_value is 0 at entry and updated to (credit−spread_value)×contracts×100
                    # by _manage_positions on subsequent days.  Same-day entries already in
                    # open_positions appear here with current_value=0, making total_equity
                    # slightly conservative (earlier intraday entries aren't counted as equity).
                    # This is intentional — the bias is safe and avoids lookahead.
                    total_equity = self.capital + sum(p.get('current_value', 0) for p in open_positions)
                    equity = max(total_equity, 1.0)
                    return (current_max_loss + new_max_loss) / equity * 100 <= self._max_portfolio_exposure_pct

                # Monte Carlo per-day sampling — sampled once so all entries on
                # the same day target the same expiration cycle.
                if self._rng is not None:
                    # DTE randomization (both 'dte' and 'full' modes).
                    _sampled_dte = self._rng.randint(self._mc_dte_lo, self._mc_dte_hi)
                    self._current_trade_dte = _sampled_dte
                    self._current_trade_min_dte = max(20, _sampled_dte - 10)
                    # Entry-day shift: only active in 'full' mode with jitter > 0.
                    if self._mc_mode == 'full' and self._mc_entry_day_jitter > 0:
                        self._mc_entry_day_shift = self._rng.randint(
                            -self._mc_entry_day_jitter, self._mc_entry_day_jitter
                        )
                    else:
                        self._mc_entry_day_shift = 0
                else:
                    # VIX-gated DTE: when VIX is below threshold, use a longer DTE to
                    # capture more premium in low-vol environments (e.g. 2024).
                    _vix_dte_thresh = self.strategy_params.get('vix_dte_threshold', 0)
                    _dte_low_vix = self.strategy_params.get('dte_low_vix', self._target_dte)
                    _min_dte_low_vix = self.strategy_params.get('min_dte_low_vix', self._min_dte)
                    if _vix_dte_thresh > 0 and self._current_vix < _vix_dte_thresh:
                        self._current_trade_dte = _dte_low_vix
                        self._current_trade_min_dte = _min_dte_low_vix
                    else:
                        self._current_trade_dte = self._target_dte
                        self._current_trade_min_dte = self._min_dte

                for scan_hour, scan_minute in SCAN_TIMES:
                    if _skip_new_entries:
                        break
                    if len(open_positions) >= self.risk_params['max_positions']:
                        break
                    if _want_puts:
                        new_position = self._find_backtest_opportunity(
                            ticker, current_date, current_price, price_data,
                            scan_hour=scan_hour, scan_minute=scan_minute,
                        )
                        if new_position:
                            if self._mc_should_skip_trade():
                                self.capital += new_position.get('commission', 0)
                                continue
                            _key = (new_position.get('expiration'), new_position['short_strike'], 'P')
                            if _key not in _entered_today and _open_key_counts.get(_key, 0) < _max_per_key:
                                if _exposure_ok(new_position):
                                    if self._mc_should_skip_trade():
                                        self.capital += new_position.get('commission', 0)
                                        logger.debug("MC trade-skip: dropped bull_put %s", _key)
                                        continue
                                    open_positions.append(new_position)
                                    _entered_today.add(_key)
                                    _open_key_counts[_key] = _open_key_counts.get(_key, 0) + 1
                                    continue  # entered put: skip bear call + IC for this scan time
                                else:
                                    self.capital += new_position.get('commission', 0)
                                    logger.debug("Portfolio exposure cap — skipping bull_put %s", _key)
                                    # fall through: try IC on this scan time
                            else:
                                # Duplicate/stack-limit — refund commission.
                                # In pure BULL regime (no calls wanted), preserve original behaviour:
                                # skip IC fallback so a deduped put doesn't open an IC whose call
                                # leg is immediately challenged by the rising market.
                                self.capital += new_position.get('commission', 0)
                                logger.debug("Duplicate key — refunding commission for bull_put %s", _key)
                                if not _want_calls:
                                    continue  # BULL-only: deduped put → skip IC this scan time
                    if len(open_positions) >= self.risk_params['max_positions']:
                        break
                    if _want_calls:
                        bear_call = self._find_bear_call_opportunity(
                            ticker, current_date, current_price, price_data,
                            scan_hour=scan_hour, scan_minute=scan_minute,
                        )
                        if bear_call:
                            if self._mc_should_skip_trade():
                                self.capital += bear_call.get('commission', 0)
                                continue
                            _key = (bear_call.get('expiration'), bear_call['short_strike'], 'C')
                            if _key not in _entered_today and _open_key_counts.get(_key, 0) < _max_per_key:
                                if _exposure_ok(bear_call):
                                    if self._mc_should_skip_trade():
                                        self.capital += bear_call.get('commission', 0)
                                        logger.debug("MC trade-skip: dropped bear_call %s", _key)
                                        continue
                                    open_positions.append(bear_call)
                                    _entered_today.add(_key)
                                    _open_key_counts[_key] = _open_key_counts.get(_key, 0) + 1
                                    continue  # entered call: skip IC for this scan time
                                else:
                                    self.capital += bear_call.get('commission', 0)
                                    logger.debug("Portfolio exposure cap — skipping bear_call %s", _key)
                                    # fall through: try IC on this scan time
                            else:
                                self.capital += bear_call.get('commission', 0)
                                logger.debug("Duplicate key — refunding commission for bear_call %s", _key)
                                # fall through: try IC on this scan time
                    # Iron condor fallback — only if enabled in config
                    if _ic_enabled and len(open_positions) < self.risk_params['max_positions']:
                        condor = self._find_iron_condor_opportunity(
                            ticker, current_date, current_price, scan_hour, scan_minute,
                        )
                        if condor:
                            if self._mc_should_skip_trade():
                                self.capital += condor.get('commission', 0)
                                continue
                            _ic_key = (
                                condor.get('expiration'),
                                condor['short_strike'],
                                condor['call_short_strike'],
                                'IC',
                            )
                            if _ic_key not in _entered_today and _open_key_counts.get(_ic_key, 0) < _max_per_key:
                                if _exposure_ok(condor):
                                    if self._mc_should_skip_trade():
                                        self.capital += condor.get('commission', 0)
                                        logger.debug("MC trade-skip: dropped IC %s", _ic_key)
                                        continue
                                    open_positions.append(condor)
                                    _entered_today.add(_ic_key)
                                    _open_key_counts[_ic_key] = _open_key_counts.get(_ic_key, 0) + 1
                                else:
                                    self.capital += condor.get('commission', 0)
                                    logger.debug("Portfolio exposure cap — skipping IC %s", _ic_key)
                            else:
                                self.capital += condor.get('commission', 0)
                                logger.debug("Duplicate key — refunding commission for IC %s", _ic_key)
            else:
                # Heuristic mode: one opportunity scan per week on Monday
                if current_date.weekday() == 0 and not _skip_new_entries:
                    if len(open_positions) < self.risk_params['max_positions']:
                        new_position = None
                        if _want_puts:
                            new_position = self._find_backtest_opportunity(
                                ticker, current_date, current_price, price_data
                            )
                        if new_position:
                            if self._mc_should_skip_trade():
                                self.capital += new_position.get('commission', 0)
                                new_position = None
                            else:
                                open_positions.append(new_position)

                        if not new_position and _want_calls and len(open_positions) < self.risk_params['max_positions']:
                            bear_call = self._find_bear_call_opportunity(
                                ticker, current_date, current_price, price_data
                            )
                            if bear_call:
                                if self._mc_should_skip_trade():
                                    self.capital += bear_call.get('commission', 0)
                                else:
                                    open_positions.append(bear_call)
                            # Note: IC fallback is not available in heuristic mode —
                            # _find_iron_condor_opportunity requires real data.

            # Record equity
            position_value = sum(pos.get('current_value', 0) for pos in open_positions)
            total_equity = self.capital + position_value
            self.equity_curve.append((current_date, total_equity))

            current_date += timedelta(days=1)

        # Close any remaining positions at backtest end
        for pos in open_positions:
            self._close_at_expiration_real(pos, end_date)

        # Calculate performance metrics
        results = self._calculate_results()

        _api_calls = getattr(self.historical_data, 'api_calls_made', 0) if self.historical_data else 0
        logger.info(
            "Backtest complete. Total trades: %d, API calls: %d",
            len(self.trades), _api_calls,
        )

        return results

    def _get_historical_data(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """Retrieve historical price data."""
        data = _yf_history_safe(ticker, start=start_date, end=end_date)
        if data.empty:
            logger.error("No historical price data for %s (%s–%s)", ticker, start_date.date(), end_date.date())
        return data

    def _build_iv_rank_series(
        self, start_date: datetime, end_date: datetime
    ) -> dict:
        """Build a {pd.Timestamp: iv_rank} lookup for IV-scaled sizing (Upgrade 3).

        Downloads VIX daily closes from yfinance and computes a 252-trading-day
        rolling IV Rank (current VIX vs trailing year min/max).  Fetches 300
        extra calendar days before start_date to guarantee a full 252-bar window.

        Falls back gracefully: any date without data receives iv_rank=25 (standard
        regime = 2% base risk), so sizing is never broken by VIX data gaps.
        """
        from shared.indicators import calculate_iv_rank as _calc_ivr
        try:
            fetch_start = start_date - timedelta(days=300)
            raw = _yf_download_safe(
                "^VIX",
                fetch_start.strftime("%Y-%m-%d"),
                (end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
            )
            if raw.empty:
                logger.warning("VIX data unavailable — using default iv_rank=25")
                return {}

            # Flatten MultiIndex if present (yfinance >= 0.2)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)

            vix = raw["Close"].dropna()
            if vix.index.tz is not None:
                vix.index = vix.index.tz_localize(None)

            iv_rank_map = {}
            for ts in vix.index:
                # Rolling 252-day window ending on this date
                window = vix.loc[:ts].tail(252)
                if len(window) < 20:
                    iv_rank_map[ts] = 25.0
                    continue
                result = _calc_ivr(window, float(vix.loc[ts]))
                iv_rank_map[ts] = result["iv_rank"]

            # Store raw VIX closes for regime filter (vix_max_entry / vix_close_all)
            self._vix_by_date = {ts: float(vix.loc[ts]) for ts in vix.index}

            # Fetch VIX3M for term structure ratio (combo regime v2 — vix_structure signal)
            try:
                raw3m = _yf_download_safe(
                    "^VIX3M",
                    fetch_start.strftime("%Y-%m-%d"),
                    (end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                )
                if not raw3m.empty:
                    if isinstance(raw3m.columns, pd.MultiIndex):
                        raw3m.columns = raw3m.columns.get_level_values(0)
                    vix3m = raw3m["Close"].dropna()
                    if vix3m.index.tz is not None:
                        vix3m.index = vix3m.index.tz_localize(None)
                    self._vix3m_by_date = {ts: float(vix3m.loc[ts]) for ts in vix3m.index}
                    logger.debug("VIX3M series fetched: %d dates", len(self._vix3m_by_date))
                else:
                    logger.warning("VIX3M data unavailable — vix_structure signal will abstain")
            except Exception as e3m:
                logger.warning("Failed to fetch VIX3M: %s — vix_structure signal will abstain", e3m)

            logger.debug(
                "IV rank series built: %d dates, range %.0f–%.0f  VIX range %.1f–%.1f",
                len(iv_rank_map),
                min(iv_rank_map.values()) if iv_rank_map else 0,
                max(iv_rank_map.values()) if iv_rank_map else 0,
                min(self._vix_by_date.values()) if self._vix_by_date else 0,
                max(self._vix_by_date.values()) if self._vix_by_date else 0,
            )
            return iv_rank_map
        except Exception as e:
            logger.warning("Failed to build IV rank series: %s — using default 25", e)
            return {}

    def _build_realized_vol_series(self, price_data: pd.DataFrame) -> dict:
        """Build {pd.Timestamp: realized_vol} from OHLCV data already in memory.

        Uses 20-day ATR normalized to annualized vol as a proxy for current IV.
        This replaces the constant σ=25% previously used in delta-based strike
        selection, which caused systematic strike misplacement in non-average-vol
        regimes (too near ATM in high-IV, too far OTM in low-IV).

        Formula: σ = ATR(20) / Close × √252
        ATR uses full True Range: max(H-L, |H-PrevClose|, |L-PrevClose|)

        Result is clipped to [0.10, 1.00] and NaNs filled with 0.25.
        """
        try:
            high = price_data['High']
            low = price_data['Low']
            close = price_data['Close']
            prev_close = close.shift(1)

            tr = pd.concat([
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ], axis=1).max(axis=1)

            atr20 = tr.rolling(20, min_periods=5).mean()
            rv = (atr20 / close * math.sqrt(252)).clip(lower=0.10, upper=1.00)
            rv = rv.fillna(0.25)

            if rv.index.tz is not None:
                rv.index = rv.index.tz_localize(None)

            logger.debug(
                "Realized vol series: %d dates, range %.0f%%–%.0f%%",
                len(rv), rv.min() * 100, rv.max() * 100,
            )
            return rv.to_dict()
        except Exception as e:
            logger.warning("Failed to build realized vol series: %s — using 0.25", e)
            return {}

    def _build_combo_regime_series(self, price_data: pd.DataFrame) -> None:
        """Build self._regime_by_date using ComboRegimeDetector (Phase 6).

        Called after _build_iv_rank_series so self._vix_by_date is populated.
        Logs a breakdown of BULL/BEAR/NEUTRAL day counts for diagnostics.
        """
        from compass.regime import ComboRegimeDetector
        detector = ComboRegimeDetector(self._regime_config)
        self._regime_by_date = detector.compute_regime_series(
            price_data, self._vix_by_date, self._vix3m_by_date
        )
        logger.info(
            "Combo regime series built: %d dates, bull=%d bear=%d neutral=%d",
            len(self._regime_by_date),
            sum(1 for v in self._regime_by_date.values() if v == 'bull'),
            sum(1 for v in self._regime_by_date.values() if v == 'bear'),
            sum(1 for v in self._regime_by_date.values() if v == 'neutral'),
        )

    def _build_crypto_regime_series(self, price_data: pd.DataFrame) -> None:
        """Build self._regime_by_date using CryptoRegimeDetector (Phase 2).

        Uses an MA-based regime (BULL/NEUTRAL/BEAR) suited for short crypto ETF
        history (default MA50 instead of MA200).  Called instead of
        _build_combo_regime_series when regime_mode == 'crypto_composite'.
        """
        from ml.crypto_regime_detector import CryptoRegimeDetector
        detector = CryptoRegimeDetector(self._regime_config)
        self._regime_by_date = detector.compute_regime_series(price_data)

    def _build_compass_series(self, start_date: datetime, end_date: datetime) -> None:
        """Load COMPASS risk_appetite + XLI/XLF RRG quadrants from macro_state.db.

        Populates:
          self._compass_risk_appetite_by_date : Timestamp → risk_appetite score (0-100)
          self._compass_rrg_xli_by_date       : Timestamp → XLI rrg_quadrant string
          self._compass_rrg_xlf_by_date       : Timestamp → XLF rrg_quadrant string

        Signal design (v2 — empirically validated on 323 weeks):
          - risk_appetite: r=-0.250 vs 4w forward SPY returns (vs r=-0.106 for overall)
          - XLI+XLF dual-Lagging: genuine economic deterioration (~15-20% block rate)
            vs the old 50%-breadth filter which was structurally random (48-54% block rate)

        The run_backtest loop forward-fills these weekly values to daily granularity
        using max(k <= today) lookup, identical to the seasonal sizing pattern.
        """
        try:
            from compass.macro_db import get_db
            conn = get_db()
            # Fetch all macro scores in backtest window (+ 90-day buffer for warmup)
            fetch_start = (start_date - timedelta(days=90)).strftime("%Y-%m-%d")
            fetch_end = end_date.strftime("%Y-%m-%d")

            # Signal A: risk_appetite score (dominant signal, r=-0.250)
            rows = conn.execute(
                "SELECT date, risk_appetite FROM macro_score WHERE date >= ? AND date <= ? ORDER BY date",
                (fetch_start, fetch_end),
            ).fetchall()
            for r in rows:
                ts = pd.Timestamp(r["date"])
                if r["risk_appetite"] is not None:
                    val = float(r["risk_appetite"])
                    # Clamp to valid range (guard against NaN propagation in score engine)
                    if 0.0 <= val <= 100.0:
                        self._compass_risk_appetite_by_date[ts] = val

            # Signal B: XLI and XLF RRG quadrants (economic backbone deterioration filter)
            xli_rows = conn.execute(
                "SELECT date, rrg_quadrant FROM sector_rs WHERE ticker='XLI' AND date >= ? AND date <= ? ORDER BY date",
                (fetch_start, fetch_end),
            ).fetchall()
            for r in xli_rows:
                if r["rrg_quadrant"]:
                    self._compass_rrg_xli_by_date[pd.Timestamp(r["date"])] = r["rrg_quadrant"]

            xlf_rows = conn.execute(
                "SELECT date, rrg_quadrant FROM sector_rs WHERE ticker='XLF' AND date >= ? AND date <= ? ORDER BY date",
                (fetch_start, fetch_end),
            ).fetchall()
            for r in xlf_rows:
                if r["rrg_quadrant"]:
                    self._compass_rrg_xlf_by_date[pd.Timestamp(r["date"])] = r["rrg_quadrant"]

            conn.close()
            logger.info(
                "COMPASS v2 series built: %d risk_appetite weeks, %d XLI weeks, %d XLF weeks",
                len(self._compass_risk_appetite_by_date),
                len(self._compass_rrg_xli_by_date),
                len(self._compass_rrg_xlf_by_date),
            )
        except Exception as exc:
            logger.warning("COMPASS series build failed: %s — COMPASS disabled for this run", exc)
            self._compass_enabled = False
            self._compass_rrg_filter = False

    # ------------------------------------------------------------------
    # Opportunity finding
    # ------------------------------------------------------------------

    def _compute_trend_ma(self, closes: pd.Series) -> float:
        """Compute trend MA (SMA or EMA) from a Close price series."""
        _mp = self._trend_ma_period
        if self._trend_ma_type == 'ema':
            return closes.ewm(span=_mp, min_periods=max(10, _mp // 2)).mean().iloc[-1]
        return closes.rolling(_mp, min_periods=max(10, _mp // 2)).mean().iloc[-1]

    def _find_backtest_opportunity(
        self,
        ticker: str,
        date: datetime,
        price: float,
        price_data: pd.DataFrame,
        scan_hour: Optional[int] = None,
        scan_minute: Optional[int] = None,
    ) -> Optional[Dict]:
        """Find a bull put spread opportunity."""
        _mp = self._trend_ma_period
        # P1-A fix: exclude today's close — a real system only knows prior-day closes
        # when placing an entry order. loc[:date] is inclusive and leaks today's close.
        _prev_date = pd.Timestamp((date - timedelta(days=1)).date())
        recent_data = price_data.loc[:_prev_date].tail(_mp + 20)

        if len(recent_data) < min(20, _mp):
            return None

        trend_ma = self._compute_trend_ma(recent_data['Close'])

        # Combo mode: direction already decided by regime at the outer gate — skip MA filter
        if self._regime_mode != 'combo' and price < trend_ma:
            return None

        # Optional short-term momentum filter: skip if price fell > X% in the past 10 days.
        # Prevents bull put entries during rapid sell-offs (e.g. early 2022 bear, COVID Feb 2020).
        _mom_filter = self.strategy_params.get('momentum_filter_pct', None)
        if _mom_filter is not None:
            _lookback = min(10, len(recent_data) - 1)
            if _lookback > 0:
                price_10d_ago = recent_data['Close'].iloc[-_lookback - 1]
                mom_pct = (price - price_10d_ago) / price_10d_ago * 100
                if mom_pct < -abs(_mom_filter):
                    return None

        # Expiration selection:
        # - MC mode → Friday directly (cached, liquid, avoids new API calls per DTE value)
        # - Post-2022-09-12 → all 5 weekdays (Mon–Fri) since SPY added Tue/Thu weeklies
        # - Before 2022-09-12 → Mon/Wed/Fri only
        if self._rng is not None:
            # Entry-day shift: expiration is computed from a date offset by ±N days
            # (full MC mode only).  This models entering 1–2 days early or late.
            _ref_date = date + timedelta(days=self._mc_entry_day_shift) if self._mc_entry_day_shift else date
            expiration = _nearest_friday_expiration(_ref_date, self._current_trade_dte, self._current_trade_min_dte)
        elif date >= _SPY_TUETHU_START:
            expiration = _nearest_weekday_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
        else:
            expiration = _nearest_mwf_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
        date_str = date.strftime("%Y-%m-%d")
        spread_width = self.strategy_params['spread_width']

        if self._use_real_data:
            result = self._find_real_spread(
                ticker, date, date_str, price, expiration,
                spread_width, option_type="P",
                scan_hour=scan_hour, scan_minute=scan_minute,
            )
            # Friday fallback (non-MC mode only): if primary expiration has no data,
            # try the nearest Friday. This catches pre-2022 Mon/Wed gaps and post-2022
            # Tue/Thu expirations that may not have liquid options.
            if result is None and self._rng is None:
                friday_exp = _nearest_friday_expiration(
                    date, self._current_trade_dte, self._current_trade_min_dte
                )
                if friday_exp != expiration:
                    self._friday_fallback_count += 1
                    result = self._find_real_spread(
                        ticker, date, date_str, price, friday_exp,
                        spread_width, option_type="P",
                        scan_hour=scan_hour, scan_minute=scan_minute,
                    )
            return result
        return None  # no historical data — skip trade

    def _find_bear_call_opportunity(
        self,
        ticker: str,
        date: datetime,
        price: float,
        price_data: pd.DataFrame,
        scan_hour: Optional[int] = None,
        scan_minute: Optional[int] = None,
    ) -> Optional[Dict]:
        """Find a bear call spread opportunity (bearish/neutral trend)."""
        _mp = self._trend_ma_period
        # P1-A fix: exclude today's close (same as bull put fix above)
        _prev_date = pd.Timestamp((date - timedelta(days=1)).date())
        recent_data = price_data.loc[:_prev_date].tail(_mp + 20)

        if len(recent_data) < min(20, _mp):
            return None

        trend_ma = self._compute_trend_ma(recent_data['Close'])

        # Combo mode: direction already decided by regime at the outer gate — skip MA filter
        if self._regime_mode != 'combo' and price > trend_ma:
            # Price above MA — bullish, skip bear calls
            return None

        if self._rng is not None:
            expiration = _nearest_friday_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
        elif date >= _SPY_TUETHU_START:
            expiration = _nearest_weekday_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
        else:
            expiration = _nearest_mwf_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
        date_str = date.strftime("%Y-%m-%d")
        spread_width = self.strategy_params['spread_width']

        if self._use_real_data:
            result = self._find_real_spread(
                ticker, date, date_str, price, expiration,
                spread_width, option_type="C",
                scan_hour=scan_hour, scan_minute=scan_minute,
            )
            if result is None and self._rng is None:
                friday_exp = _nearest_friday_expiration(
                    date, self._current_trade_dte, self._current_trade_min_dte
                )
                if friday_exp != expiration:
                    self._friday_fallback_count += 1
                    result = self._find_real_spread(
                        ticker, date, date_str, price, friday_exp,
                        spread_width, option_type="C",
                        scan_hour=scan_hour, scan_minute=scan_minute,
                    )
            return result
        return None  # no historical data — skip trade

    def _find_iron_condor_opportunity(
        self,
        ticker: str,
        date: datetime,
        price: float,
        scan_hour: Optional[int] = None,
        scan_minute: Optional[int] = None,
    ) -> Optional[Dict]:
        """Find an iron condor (put spread + call spread) as a fallback.

        No MA20 direction check — condors are direction-neutral.  Used only
        when neither a bull put nor bear call passes its individual credit
        minimum.  Requires real data mode.
        """
        if not self._use_real_data:
            return None

        if self._rng is not None:
            # MC mode: target Friday directly — no Mon/Wed API calls for uncached expirations.
            # Apply entry-day shift to reference date (full MC mode, ±N days).
            _ref_date = date + timedelta(days=self._mc_entry_day_shift) if self._mc_entry_day_shift else date
            expiration = _nearest_friday_expiration(_ref_date, self._current_trade_dte, self._current_trade_min_dte)
            friday_exp = expiration
        elif date >= _SPY_TUETHU_START:
            expiration = _nearest_weekday_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
            friday_exp = _nearest_friday_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
        else:
            expiration = _nearest_mwf_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
            friday_exp = _nearest_friday_expiration(date, self._current_trade_dte, self._current_trade_min_dte)
        date_str = date.strftime("%Y-%m-%d")
        spread_width = self.strategy_params['spread_width']

        # Fetch each leg — bypass individual min_credit (checked on combined below).
        # Friday fallback: if primary MWF expiration has no data (Mon/Wed unavailable),
        # retry with nearest Friday expiration.
        def _get_leg(opt_type: str):
            leg = self._find_real_spread(
                ticker, date, date_str, price, expiration,
                spread_width, option_type=opt_type,
                scan_hour=scan_hour, scan_minute=scan_minute,
                min_credit_override=0.0, skip_commission=True,
            )
            if leg is None and friday_exp != expiration:
                leg = self._find_real_spread(
                    ticker, date, date_str, price, friday_exp,
                    spread_width, option_type=opt_type,
                    scan_hour=scan_hour, scan_minute=scan_minute,
                    min_credit_override=0.0, skip_commission=True,
                )
            return leg

        put_leg = _get_leg("P")
        if put_leg is None:
            return None

        # Use same expiration for both legs (whichever resolved for put)
        call_leg = self._find_real_spread(
            ticker, date, date_str, price, put_leg['expiration'],
            spread_width, option_type="C",
            scan_hour=scan_hour, scan_minute=scan_minute,
            min_credit_override=0.0, skip_commission=True,
        )
        if call_leg is None:
            return None

        # Validate non-overlapping strikes
        if put_leg['short_strike'] >= call_leg['short_strike']:
            logger.debug(
                "IC legs overlap: put_short=%.0f >= call_short=%.0f on %s — skipping",
                put_leg['short_strike'], call_leg['short_strike'], date_str,
            )
            return None

        put_credit = put_leg['credit']   # already net of slippage
        call_credit = call_leg['credit']  # already net of slippage
        combined_credit = put_credit + call_credit

        # Combined credit minimum check
        min_combined_credit_pct = self.strategy_params.get('iron_condor', {}).get(
            'min_combined_credit_pct', 20
        )
        # Denominator is 2×spread_width (total IC risk) so the pct is intuitive:
        # e.g. 20% of a 5-wide IC = $2.00 combined credit required.
        min_combined_credit = (2 * spread_width) * (min_combined_credit_pct / 100)
        if combined_credit < min_combined_credit:
            logger.debug(
                "IC combined credit $%.2f below minimum $%.2f (%.0f%% of $%.0f IC risk) on %s — skipping",
                combined_credit, min_combined_credit, min_combined_credit_pct,
                2 * spread_width, date_str,
            )
            return None

        stop_loss_multiplier = self.risk_params['stop_loss_multiplier']
        # P0-C fix: worst case is BOTH wings simultaneously ITM (gap open / flash crash).
        # One-wing assumption leads to ~2x oversizing and miscalibrated stop thresholds.
        max_loss = (2 * spread_width) - combined_credit

        risk_per_spread = max_loss * 100
        if risk_per_spread <= 0:
            return None

        # P0-B fix: mirror _find_real_spread sizing logic exactly —
        # use compound-aware account_base and respect sizing_mode (flat vs iv_scaled).
        from compass.sizing import calculate_dynamic_risk, get_contract_size
        account_base = self.capital if self._compound else self.starting_capital
        max_contracts_cap = self.risk_params.get('max_contracts', 999)
        if self._sizing_mode == 'flat':
            # ic_risk_per_trade overrides max_risk_per_trade for iron condor entries
            _ic_risk_override = self.strategy_params.get('iron_condor', {}).get('risk_per_trade', None)
            flat_risk_pct = (_ic_risk_override if _ic_risk_override is not None
                             else self.risk_params.get('max_risk_per_trade', 2.0)) / 100.0
            # Mirror single-spread vix_dynamic_sizing: scale position when VIX is elevated.
            _vds = self.strategy_params.get('vix_dynamic_sizing', {})
            if _vds:
                _vix = self._current_vix
                _full = _vds.get('full_below', 18)
                _half = _vds.get('half_below', 22)
                _qtr  = _vds.get('quarter_below', 25)
                if _vix < _full:
                    _vix_scale = 1.0
                elif _vix < _half:
                    _vix_scale = 0.5
                elif _vix < _qtr:
                    _vix_scale = 0.25
                else:
                    _vix_scale = 0.0
                flat_risk_pct *= _vix_scale
            trade_dollar_risk = account_base * flat_risk_pct
        else:
            current_portfolio_risk = getattr(self, '_current_portfolio_risk', 0.0)
            iv_rank = getattr(self, '_current_iv_rank', 25.0)
            _max_risk = self.risk_params.get('max_risk_per_trade')
            trade_dollar_risk = calculate_dynamic_risk(
                account_base, iv_rank, current_portfolio_risk,
                max_risk_pct=_max_risk,
            )
        trade_dollar_risk *= self._current_seasonal_mult
        # NEW-1 fix: IC worst-case is both wings ITM simultaneously (= 2×spread_width).
        # get_contract_size recomputes max_loss_per_contract from its width argument,
        # so we must pass the effective IC width (2× single wing) to get correct sizing.
        contracts = max(1, get_contract_size(
            trade_dollar_risk, spread_width * 2, combined_credit,
            max_contracts=max_contracts_cap,
        ))
        commission_cost = self.commission * 4 * contracts  # 4 legs × contracts

        scan_time_mins = (scan_hour or 0) * 60 + (scan_minute or 0)
        market_open_mins = _FIRST_BAR_HOUR * 60 + _FIRST_BAR_MINUTE
        use_intraday = (
            scan_hour is not None
            and scan_minute is not None
            and scan_time_mins >= market_open_mins
        )
        slippage_applied = (
            put_leg.get('slippage_applied', 0.0) + call_leg.get('slippage_applied', 0.0)
        )
        commission_cost = self.commission * 4 * contracts  # 4 legs × contracts

        position = {
            'ticker': ticker,
            'type': 'iron_condor',
            'entry_date': date,
            # NEW-2 fix: use the expiration that the put leg actually resolved to.
            # On Friday fallback, put_leg['expiration'] is friday_exp, not the original
            # Monday/Wednesday target. Storing the wrong date causes _manage_positions
            # to close the IC 4 days early against incorrect option data.
            'expiration': put_leg['expiration'],
            # Put spread leg (backward compat with _record_close)
            'short_strike': put_leg['short_strike'],
            'long_strike': put_leg['long_strike'],
            # Call spread leg
            'call_short_strike': call_leg['short_strike'],
            'call_long_strike': call_leg['long_strike'],
            # Credits
            'put_credit': put_credit,
            'call_credit': call_credit,
            'credit': combined_credit,
            'contracts': contracts,
            'max_loss': max_loss,
            'profit_target': combined_credit * self._profit_target_pct,
            'stop_loss': combined_credit * stop_loss_multiplier,
            'commission': commission_cost,  # round-trip: 4 legs at entry (deducted at line 1107) + 4 legs at exit (deducted from PnL in _record_close) = 8 legs total
            'status': 'open',
            'option_type': 'IC',
            'current_value': 0,  # unrealized PnL at entry ≈ 0; _manage_positions updates each day
            # If either leg fell back to daily close prices, entry_scan_time is None
            # so _check_intraday_exits doesn't try to guard against a stale scan time.
            'entry_scan_time': (f"{scan_hour:02d}:{scan_minute:02d}"
                                if use_intraday and put_leg.get('entry_scan_time') is not None
                                else None),
            'slippage_applied': slippage_applied,
        }

        self.capital -= commission_cost  # entry-side commission (4 legs, deducted from capital now)

        logger.debug(
            "Opened iron_condor: %s put=%s/%s call=%s/%s credit=$%.2f (%d contracts)%s",
            ticker,
            put_leg['short_strike'], put_leg['long_strike'],
            call_leg['short_strike'], call_leg['long_strike'],
            combined_credit, contracts,
            f" @ {position['entry_scan_time']} ET" if use_intraday else "",
        )

        return position

    def _find_real_spread(
        self,
        ticker: str,
        date: datetime,
        date_str: str,
        price: float,
        expiration: datetime,
        spread_width: float,
        option_type: str,
        scan_hour: Optional[int] = None,
        scan_minute: Optional[int] = None,
        min_credit_override: Optional[float] = None,
        skip_commission: bool = False,
    ) -> Optional[Dict]:
        """Find a spread using real historical option prices from Polygon.

        When scan_hour/scan_minute are provided, uses 5-min intraday bars for
        entry pricing and models slippage from the actual bar bid/ask spread
        width (bar high - bar low).  Falls back to daily close when no scan
        time is given (legacy daily mode).

        skip_commission: if True, do not deduct entry commission from capital.
            Used by _find_iron_condor_opportunity so each leg fetch doesn't
            charge commission separately; the IC charges one 4-leg commission
            for the full position.
        """
        exp_str = expiration.strftime("%Y-%m-%d")
        ot = option_type[0].upper()

        # Select short strike — delta-based or OTM% depending on config
        if self._use_delta_selection:
            from shared.strike_selector import select_delta_strike
            chain = self.historical_data.get_strikes_with_approx_delta(
                ticker, expiration, price, date_str, option_type=ot,
                iv_estimate=self._current_realized_vol,
            )
            if not chain:
                logger.debug("No strikes for delta selection: %s exp %s on %s",
                             ticker, exp_str, date_str)
                return None
            short_strike = select_delta_strike(chain, ot, target_delta=self._target_delta)
            if short_strike is None:
                return None
        else:
            strikes = self.historical_data.get_available_strikes(
                ticker, exp_str, date_str, option_type=ot,
            )
            if not strikes:
                logger.debug("No strikes available for %s exp %s on %s",
                             ticker, exp_str, date_str)
                return None
            # Pick short strike OTM by self.otm_pct (default 5%)
            if ot == "P":
                target_short = price * (1 - self.otm_pct)
                candidates = [s for s in strikes if s <= target_short]
                if not candidates:
                    return None
                short_strike = max(candidates)
            else:
                target_short = price * (1 + self.otm_pct)
                candidates = [s for s in strikes if s >= target_short]
                if not candidates:
                    return None
                short_strike = min(candidates)

        if ot == "P":
            long_strike = short_strike - spread_width
            spread_type = "bull_put_spread"
        else:
            long_strike = short_strike + spread_width
            spread_type = "bear_call_spread"

        # Use intraday pricing only when a scan time is given AND options are open
        # (options market opens at 9:30 ET; the 9:15 scan runs pre-open)
        scan_time_mins = (scan_hour or 0) * 60 + (scan_minute or 0)
        market_open_mins = _FIRST_BAR_HOUR * 60 + _FIRST_BAR_MINUTE  # 9:30 = 570
        use_intraday = (
            scan_hour is not None
            and scan_minute is not None
            and scan_time_mins >= market_open_mins
        )

        def _get_prices(ss: float, ls: float, daily: bool = False) -> Optional[Dict]:
            """Fetch spread prices.  daily=True forces the option_daily cache regardless of use_intraday."""
            if use_intraday and not daily:
                return self.historical_data.get_intraday_spread_prices(
                    ticker, expiration, ss, ls, ot,
                    date_str, scan_hour, scan_minute,
                )
            return self.historical_data.get_spread_prices(
                ticker, expiration, ss, ls, ot, date_str,
            )

        def _try_offsets(daily: bool = False):
            """Try adjacent strikes ±1, ±2.  Returns (prices, short_strike, long_strike)."""
            for offset in [1, -1, 2, -2]:
                alt_short = short_strike + offset
                alt_long = alt_short - spread_width if ot == "P" else alt_short + spread_width
                p = _get_prices(alt_short, alt_long, daily=daily)
                if p is not None:
                    return p, alt_short, alt_long
            return None, short_strike, long_strike

        prices = _get_prices(short_strike, long_strike)

        if prices is None:
            prices, short_strike, long_strike = _try_offsets()

        # When intraday data is unavailable (e.g. pre-2024 history purged from Polygon),
        # fall back to daily close prices from the option_daily cache.
        # This uses real Polygon OHLCV data — NOT synthetic/heuristic pricing.
        _used_daily_fallback = False
        if prices is None and use_intraday:
            _used_daily_fallback = True
            prices, short_strike, long_strike = _try_offsets(daily=True)

        if prices is None:
            logger.debug(
                "No %s price data for spread %s %s/%s on %s",
                "intraday" if use_intraday else "daily",
                ticker, short_strike, long_strike, date_str,
            )
            return None

        credit = prices["spread_value"]

        # MC credit jitter: model fill-price uncertainty (applies in both real and mock data)
        if self._rng is not None and self._mc_mode == 'full' and self._mc_credit_jitter > 0:
            credit += self._rng.uniform(-self._mc_credit_jitter, self._mc_credit_jitter)
            credit = max(0.0, credit)

        if credit <= 0:
            return None

        # Minimum credit filter
        if min_credit_override is not None:
            min_credit = min_credit_override
        else:
            min_credit_pct = self.strategy_params.get('min_credit_pct', 15) / 100
            min_credit = spread_width * min_credit_pct
        if credit < min_credit:
            scan_tag = f" [{scan_hour:02d}:{scan_minute:02d} ET]" if use_intraday else ""
            logger.debug(
                "Credit $%.2f below minimum $%.2f on %s%s — skipping",
                credit, min_credit, date_str, scan_tag,
            )
            return None

        # Slippage: use bid/ask-modeled value from intraday bar, or config flat value.
        # Apply slippage_multiplier for brutality tests (P2: 2x or 3x slippage scenarios).
        slippage = prices.get("slippage", self.slippage) * self._slippage_multiplier
        credit -= slippage
        if credit <= 0:
            return None

        max_loss = spread_width - credit

        risk_per_spread = max_loss * 100
        if risk_per_spread <= 0:
            return None
        # Position sizing — Phase 2: compound + flat-risk support
        # account_base: current equity when compounding, starting capital otherwise
        from compass.sizing import calculate_dynamic_risk, get_contract_size
        account_base = self.capital if self._compound else self.starting_capital
        max_contracts_cap = self.risk_params.get('max_contracts', 999)
        if self._sizing_mode == 'flat':
            # Flat risk: always risk exactly max_risk_per_trade % of account_base,
            # optionally scaled down by VIX level (vix_dynamic_sizing config).
            flat_risk_pct = self.risk_params.get('max_risk_per_trade', 2.0) / 100.0
            # VIX dynamic sizing: scale position size based on current VIX level.
            # Config: {"full_below": 18, "half_below": 22, "quarter_below": 25}
            # Above quarter_below → 0 (blocked by vix_max_entry gate before reaching here).
            _vds = self.strategy_params.get('vix_dynamic_sizing', {})
            if _vds:
                _vix = self._current_vix
                _full  = _vds.get('full_below', 18)
                _half  = _vds.get('half_below', 22)
                _qtr   = _vds.get('quarter_below', 25)
                if _vix < _full:
                    _vix_scale = 1.0
                elif _vix < _half:
                    _vix_scale = 0.5
                elif _vix < _qtr:
                    _vix_scale = 0.25
                else:
                    _vix_scale = 0.0
                flat_risk_pct *= _vix_scale
            trade_dollar_risk = account_base * flat_risk_pct
        else:
            # IV-scaled sizing (Upgrade 3): risk budget varies with IV Rank.
            # P0-A fix: pass max_risk_per_trade as ceiling so configured risk is respected.
            current_portfolio_risk = getattr(self, '_current_portfolio_risk', 0.0)
            iv_rank = getattr(self, '_current_iv_rank', 25.0)
            _max_risk = self.risk_params.get('max_risk_per_trade')
            trade_dollar_risk = calculate_dynamic_risk(
                account_base, iv_rank, current_portfolio_risk,
                max_risk_pct=_max_risk,
            )
        trade_dollar_risk *= self._current_seasonal_mult
        # COMPASS macro score multiplier: buy fear (score<45→1.1x), reduce complacency (score>70→0.8x)
        if self._compass_enabled:
            trade_dollar_risk *= self._current_compass_mult
        contracts = max(1, get_contract_size(trade_dollar_risk, spread_width, credit, max_contracts=max_contracts_cap))

        # ── Volume gate + adaptive sizing (real-data mode only) ──────────────
        # Only runs when volume_gate=True is explicitly configured.
        # vol_size_cap is a sub-feature of the gate, not a standalone trigger.
        # Skipping this block by default avoids spurious Polygon cache-miss lookups
        # during normal backtesting where liquidity filtering is not needed.
        if self._use_real_data and self._volume_gate:
            short_sym = self.historical_data.build_occ_symbol(ticker, expiration, short_strike, ot)
            long_sym  = self.historical_data.build_occ_symbol(ticker, expiration, long_strike, ot)
            sv = self.historical_data.get_prev_daily_volume(short_sym, date_str)
            lv = self.historical_data.get_prev_daily_volume(long_sym, date_str)

            if sv is not None and lv is not None:
                min_vol = min(sv, lv)
                # Hard reject: need min_vol_ratio × contracts in daily volume
                if self._volume_gate and min_vol < self._min_vol_ratio * contracts:
                    logger.debug(
                        "vol-gate: skip %s %s/%s  vol=%d < %.0f×%d",
                        ot, short_strike, long_strike, min_vol,
                        self._min_vol_ratio, contracts,
                    )
                    self._volume_skipped += 1
                    return None
                # Adaptive cap: contracts ≤ volume_size_cap_pct × min_vol
                if self._vol_size_cap > 0:
                    vol_cap = max(1, int(min_vol * self._vol_size_cap))
                    contracts = min(contracts, vol_cap)
            else:
                # P1-C fix: log at WARNING so cache misses are visible; support fail-closed.
                _on_miss = self.backtest_config.get('volume_gate_on_miss', 'open')
                if _on_miss == 'closed' and self._volume_gate:
                    logger.warning(
                        "vol-gate: cache miss for %s or %s on %s — fail-CLOSED "
                        "(volume_gate_on_miss=closed)",
                        short_sym, long_sym, date_str,
                    )
                    self._volume_skipped += 1
                    return None
                else:
                    logger.warning(
                        "vol-gate: cache miss for %s or %s on %s — fail-open "
                        "(set volume_gate_on_miss=closed to reject on miss)",
                        short_sym, long_sym, date_str,
                    )

        # ── OI gate (disabled by default — data rarely available on standard tier) ──
        if self._use_real_data and self._oi_gate:
            short_sym = self.historical_data.build_occ_symbol(ticker, expiration, short_strike, ot)
            oi = self.historical_data.get_prev_daily_oi(short_sym, date_str)
            if oi is not None and oi < self._oi_min_factor * contracts:
                logger.debug("oi-gate: skip %s %s  oi=%d < %.0f×%d",
                             ot, short_strike, oi, self._oi_min_factor, contracts)
                self._volume_skipped += 1
                return None

        commission_cost = self.commission * 2 * contracts  # Two legs × contracts

        position = {
            'ticker': ticker,
            'type': spread_type,
            'entry_date': date,
            'expiration': expiration,
            'short_strike': short_strike,
            'long_strike': long_strike,
            'credit': credit,
            'contracts': contracts,
            'max_loss': max_loss,
            'profit_target': credit * self._profit_target_pct,
            'stop_loss': credit * self.risk_params['stop_loss_multiplier'],
            'commission': commission_cost,
            'status': 'open',
            'current_value': 0,  # unrealized PnL at entry ≈ 0; _manage_positions updates each day
            'option_type': ot,
            'entry_scan_time': (f"{scan_hour:02d}:{scan_minute:02d}"
                                if use_intraday and not _used_daily_fallback else None),
            'slippage_applied': slippage,
        }

        if not skip_commission:
            self.capital -= commission_cost

        logger.debug(
            "Opened %s: %s %s/%s credit=$%.2f slippage=$%.3f (%d contracts)%s",
            spread_type, ticker, short_strike, long_strike, credit, slippage, contracts,
            f" @ {position['entry_scan_time']} ET" if use_intraday else "",
        )

        return position

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def _check_intraday_exits(
        self,
        pos: Dict,
        current_date: datetime,
        date_str: str,
    ) -> Optional[Tuple]:
        """Check 30-min intraday scan times for stop/profit triggers.

        Mirrors the live scanner's 30-min cadence (SCAN_TIMES) so backtest
        exit granularity matches live trading behavior.

        On the entry day, bars at or before entry_scan_time are skipped to
        avoid acting on data that predates the position's opening.

        Returns:
            ('profit_target'|'stop_loss', spread_value) if triggered
            ('no_trigger', last_spread_value)            if data found but no trigger
            None                                         if no intraday data (fall back to daily close)
        """
        entry_scan_time = pos.get('entry_scan_time')  # e.g. "10:30"
        is_entry_day = current_date.date() == pos['entry_date'].date()

        entry_mins = None
        if entry_scan_time and is_entry_day:
            h, m = entry_scan_time.split(':')
            entry_mins = int(h) * 60 + int(m)

        had_any_data = False
        last_spread_value = None

        for scan_hour, scan_minute in SCAN_TIMES:
            # Skip 9:15 — options don't open until 9:30
            if scan_hour == _FIRST_BAR_HOUR and scan_minute < _FIRST_BAR_MINUTE:
                continue

            # On entry day, skip scan times at or before the entry scan time
            if entry_mins is not None:
                if scan_hour * 60 + scan_minute <= entry_mins:
                    continue

            if pos['type'] == 'iron_condor':
                put_prices = self.historical_data.get_intraday_spread_prices(
                    pos['ticker'], pos['expiration'],
                    pos['short_strike'], pos['long_strike'], 'P',
                    date_str, scan_hour, scan_minute,
                )
                call_prices = self.historical_data.get_intraday_spread_prices(
                    pos['ticker'], pos['expiration'],
                    pos['call_short_strike'], pos['call_long_strike'], 'C',
                    date_str, scan_hour, scan_minute,
                )
                if put_prices is None or call_prices is None:
                    continue
                spread_value = put_prices['spread_value'] + call_prices['spread_value']
            else:
                ot = pos.get('option_type', 'P')
                prices = self.historical_data.get_intraday_spread_prices(
                    pos['ticker'], pos['expiration'],
                    pos['short_strike'], pos['long_strike'], ot,
                    date_str, scan_hour, scan_minute,
                )
                if prices is None:
                    continue
                spread_value = prices['spread_value']

            had_any_data = True
            last_spread_value = spread_value

            if pos['credit'] - spread_value >= pos['profit_target']:
                return ('profit_target', spread_value)
            if spread_value - pos['credit'] >= pos['stop_loss']:
                return ('stop_loss', spread_value)

        if not had_any_data:
            return None  # No intraday data — caller falls back to daily close
        return ('no_trigger', last_spread_value)

    def _manage_positions(
        self,
        positions: List[Dict],
        current_date: datetime,
        current_price: float,
        ticker: str = "",
    ) -> List[Dict]:
        """Manage open positions — check for exits."""
        remaining_positions = []

        for pos in positions:
            # Check if expired
            if current_date >= pos['expiration']:
                self._close_at_expiration_real(pos, current_date)
                continue

            # Check spread value
            date_str = current_date.strftime("%Y-%m-%d")

            if self._use_real_data:
                # Try intraday exits first (30-min scan granularity matching live scanner)
                intraday_result = self._check_intraday_exits(pos, current_date, date_str)
                if intraday_result is not None:
                    reason, spread_value = intraday_result
                    if reason in ('profit_target', 'stop_loss'):
                        # Apply exit slippage on ALL exits: buying back at a worse price
                        # than mid is realistic whether the fill is favorable or adverse.
                        # IC closes two separate spreads — each incurs bid-ask slippage.
                        _slip_legs = 2 if pos['type'] == 'iron_condor' else 1
                        exit_cost = spread_value + _slip_legs * self._vix_scaled_exit_slippage()
                        pnl = (pos['credit'] - exit_cost) * pos['contracts'] * 100 - pos['commission']
                        self._record_close(pos, current_date, pnl, reason)
                        continue
                    # 'no_trigger' — had intraday data but no exit; skip daily close check
                    pos['current_value'] = (pos['credit'] - spread_value) * pos['contracts'] * 100
                    remaining_positions.append(pos)
                    continue

                # No intraday data available — fall back to daily close check
                if pos['type'] == 'iron_condor':
                    put_prices = self.historical_data.get_spread_prices(
                        pos['ticker'], pos['expiration'],
                        pos['short_strike'], pos['long_strike'], 'P', date_str,
                    )
                    call_prices = self.historical_data.get_spread_prices(
                        pos['ticker'], pos['expiration'],
                        pos['call_short_strike'], pos['call_long_strike'], 'C', date_str,
                    )
                    if put_prices is None or call_prices is None:
                        # No price data for one or both wings today — carry forward prior
                        # day's current_value mark.  Equity curve may be slightly stale
                        # on data-gap days; this is expected behaviour, not a bug.
                        remaining_positions.append(pos)
                        continue
                    current_spread_value = put_prices['spread_value'] + call_prices['spread_value']
                else:
                    ot = pos.get('option_type', 'P')
                    prices = self.historical_data.get_spread_prices(
                        pos['ticker'], pos['expiration'],
                        pos['short_strike'], pos['long_strike'],
                        ot, date_str,
                    )

                    if prices is None:
                        # No data for today — keep position, don't mark
                        remaining_positions.append(pos)
                        continue

                    current_spread_value = prices["spread_value"]
            else:
                # No historical data — carry position forward without marking
                remaining_positions.append(pos)
                continue

            # P&L check: profit = credit - current spread value (daily close fallback)
            profit = pos['credit'] - current_spread_value

            if profit >= pos['profit_target']:
                # Apply exit slippage on profit-target exits — buying back costs more than mid.
                # IC closes two separate spreads — each incurs bid-ask slippage.
                _slip_legs = 2 if pos['type'] == 'iron_condor' else 1
                exit_cost = current_spread_value + _slip_legs * self._vix_scaled_exit_slippage()
                pnl = (pos['credit'] - exit_cost) * pos['contracts'] * 100 - pos['commission']
                self._record_close(pos, current_date, pnl, 'profit_target')
                continue

            loss = current_spread_value - pos['credit']
            if loss >= pos['stop_loss']:
                _slip_legs = 2 if pos['type'] == 'iron_condor' else 1
                exit_cost = current_spread_value + _slip_legs * self._vix_scaled_exit_slippage()
                pnl = (pos['credit'] - exit_cost) * pos['contracts'] * 100 - pos['commission']
                self._record_close(pos, current_date, pnl, 'stop_loss')
                continue

            # Update current value — unrealized PnL = credit collected minus current buyback cost
            pos['current_value'] = (pos['credit'] - current_spread_value) * pos['contracts'] * 100

            remaining_positions.append(pos)

        return remaining_positions

    def _close_at_expiration_real(self, pos: Dict, expiration_date: datetime):
        """Close a position at expiration using real prices (or underlying intrinsic if unavailable)."""
        date_str = expiration_date.strftime("%Y-%m-%d")

        # When no historical options data is available (e.g. test fixtures without real data),
        # fall through to the underlying-price intrinsic path below by treating prices as None.
        if self.historical_data is None:
            underlying_price = self._get_underlying_price_at(expiration_date)
            if underlying_price is not None:
                ot = pos.get('option_type', 'P')
                short_strike = pos['short_strike']
                long_strike  = pos['long_strike']
                if ot == 'P':
                    if underlying_price >= short_strike:
                        pnl = pos['credit'] * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_profit'
                    elif underlying_price <= long_strike:
                        pnl = -pos['max_loss'] * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_no_data'
                    else:
                        intrinsic = short_strike - underlying_price
                        pnl = (pos['credit'] - intrinsic) * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_no_data'
                else:  # 'C'
                    if underlying_price <= short_strike:
                        pnl = pos['credit'] * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_profit'
                    elif underlying_price >= long_strike:
                        pnl = -pos['max_loss'] * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_no_data'
                    else:
                        intrinsic = underlying_price - short_strike
                        pnl = (pos['credit'] - intrinsic) * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_no_data'
            else:
                pnl = -pos['max_loss'] * pos['contracts'] * 100 - pos['commission']
                reason = 'expiration_no_data'
            self._record_close(pos, expiration_date, pnl, reason)
            return

        if pos['type'] == 'iron_condor':
            put_prices = self.historical_data.get_spread_prices(
                pos['ticker'], pos['expiration'],
                pos['short_strike'], pos['long_strike'], 'P', date_str,
            )
            call_prices = self.historical_data.get_spread_prices(
                pos['ticker'], pos['expiration'],
                pos['call_short_strike'], pos['call_long_strike'], 'C', date_str,
            )
            if put_prices is not None and call_prices is not None:
                closing_spread_value = put_prices['spread_value'] + call_prices['spread_value']
                # 0.10 = 2 × 0.05 per-wing threshold: treat IC as worthless only when
                # BOTH wings are effectively worthless (consistent with single-spread logic).
                if closing_spread_value > 0.10:
                    # IC closes two separate spreads — each incurs bid-ask slippage.
                    exit_cost = closing_spread_value + 2 * self._vix_scaled_exit_slippage()
                    pnl = (pos['credit'] - exit_cost) * pos['contracts'] * 100 - pos['commission']
                    reason = 'expiration_loss' if pnl < 0 else 'expiration_profit'
                else:
                    # Expired worthless — position lapses, no buy-back transaction needed
                    pnl = pos['credit'] * pos['contracts'] * 100 - pos['commission']
                    reason = 'expiration_profit'
            else:
                # P0-D fix: one or both legs have no price data at expiration.
                # Use underlying-price intrinsic settlement for each wing independently,
                # mirroring the individual-spread fallback (lines below).
                underlying_price = self._get_underlying_price_at(expiration_date)
                if underlying_price is not None:
                    put_short = pos['short_strike']
                    put_long  = pos['long_strike']
                    if underlying_price >= put_short:
                        put_intrinsic = 0.0
                    elif underlying_price <= put_long:
                        put_intrinsic = put_short - put_long  # spread_width, full put loss
                    else:
                        put_intrinsic = put_short - underlying_price

                    call_short = pos['call_short_strike']
                    call_long  = pos['call_long_strike']
                    if underlying_price <= call_short:
                        call_intrinsic = 0.0
                    elif underlying_price >= call_long:
                        call_intrinsic = call_long - call_short  # spread_width, full call loss
                    else:
                        call_intrinsic = underlying_price - call_short

                    total_intrinsic = put_intrinsic + call_intrinsic
                    pnl = (pos['credit'] - total_intrinsic) * pos['contracts'] * 100 - pos['commission']
                    reason = 'expiration_no_data'
                    logger.debug(
                        "IC no option data for %s put=%s/%s call=%s/%s exp %s, "
                        "underlying=%.2f → put_intr=%.2f call_intr=%.2f pnl=%.2f",
                        pos['ticker'], put_short, put_long, call_short, call_long,
                        date_str, underlying_price, put_intrinsic, call_intrinsic, pnl,
                    )
                else:
                    # No underlying data either — conservative: record as max loss
                    pnl = -pos['max_loss'] * pos['contracts'] * 100 - pos['commission']
                    reason = 'expiration_no_data'
                    logger.warning(
                        "IC no expiration data (option or underlying) for %s exp %s "
                        "— recording as max loss (conservative)",
                        pos['ticker'], date_str,
                    )
            self._record_close(pos, expiration_date, pnl, reason)
            return

        ot = pos.get('option_type', 'P')
        prices = self.historical_data.get_spread_prices(
            pos['ticker'], pos['expiration'],
            pos['short_strike'], pos['long_strike'],
            ot, date_str,
        )

        if prices is not None:
            closing_spread_value = prices["spread_value"]
            # If short leg still has value > 0.05, it must be bought back — costs bid/ask
            if closing_spread_value > 0.05:
                # P1-B fix: apply VIX-scaled exit slippage on expiration buy-back
                exit_cost = closing_spread_value + self._vix_scaled_exit_slippage()
                pnl = (pos['credit'] - exit_cost) * pos['contracts'] * 100 - pos['commission']
                reason = 'expiration_loss' if pnl < 0 else 'expiration_profit'
            else:
                # Expired worthless — position lapses, no buy-back transaction needed
                pnl = pos['credit'] * pos['contracts'] * 100 - pos['commission']
                reason = 'expiration_profit'
        else:
            # No option price data at expiration — use underlying close price to determine
            # whether the spread expired OTM (worthless → full profit) or ITM (loss).
            # This is more accurate than blindly assuming max loss, while still being
            # conservative for edge cases where the underlying price is also unavailable.
            underlying_price = self._get_underlying_price_at(expiration_date)
            ot = pos.get('option_type', 'P')

            if underlying_price is not None:
                short_strike = pos['short_strike']
                long_strike  = pos['long_strike']

                if ot == 'P':
                    if underlying_price >= short_strike:
                        # Both put legs expired OTM — full profit
                        pnl    = pos['credit'] * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_profit'
                    elif underlying_price <= long_strike:
                        # Both put legs deep ITM — max loss
                        pnl    = -pos['max_loss'] * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_no_data'
                    else:
                        # Short put ITM, long put OTM — partial loss
                        intrinsic = short_strike - underlying_price
                        pnl    = (pos['credit'] - intrinsic) * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_no_data'
                else:  # 'C'
                    if underlying_price <= short_strike:
                        # Both call legs expired OTM — full profit
                        pnl    = pos['credit'] * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_profit'
                    elif underlying_price >= long_strike:
                        # Both call legs deep ITM — max loss
                        pnl    = -pos['max_loss'] * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_no_data'
                    else:
                        # Short call ITM, long call OTM — partial loss
                        intrinsic = underlying_price - short_strike
                        pnl    = (pos['credit'] - intrinsic) * pos['contracts'] * 100 - pos['commission']
                        reason = 'expiration_no_data'

                logger.debug(
                    "No option data for %s %s/%s exp %s, underlying=%.2f → %s (pnl=%.2f)",
                    pos['ticker'], pos['short_strike'], pos['long_strike'],
                    pos['expiration'].strftime('%Y-%m-%d') if hasattr(pos['expiration'], 'strftime') else pos['expiration'],
                    underlying_price, reason, pnl,
                )
            else:
                # No underlying data either — fall back to conservative max loss
                pnl    = -pos['max_loss'] * pos['contracts'] * 100 - pos['commission']
                reason = 'expiration_no_data'
                logger.warning(
                    "No expiration data for %s %s/%s exp %s — recording as max loss (conservative)",
                    pos['ticker'], pos['short_strike'], pos['long_strike'],
                    pos['expiration'].strftime('%Y-%m-%d') if hasattr(pos['expiration'], 'strftime') else pos['expiration'],
                )

        self._record_close(pos, expiration_date, pnl, reason)

    def _get_underlying_price_at(self, date: datetime) -> Optional[float]:
        """Look up the SPY close price on or before *date* from stored price_data.

        Used as a fallback for expiration settlement when option price data is
        unavailable — allows us to determine whether puts/calls expired OTM
        (worthless) or ITM (loss) without needing Polygon option data.

        Returns None if price_data is not available or date is out of range.
        """
        pd_obj = getattr(self, '_price_data', None)
        if pd_obj is None or pd_obj.empty:
            return None
        try:
            ts = pd.Timestamp(date.date()) if hasattr(date, 'date') else pd.Timestamp(date)
            if ts in pd_obj.index:
                return float(pd_obj.loc[ts, 'Close'])
            # Search for nearest prior trading day
            valid = pd_obj.index[pd_obj.index <= ts]
            if len(valid) == 0:
                return None
            return float(pd_obj.loc[valid[-1], 'Close'])
        except Exception:
            return None

    def _record_close(self, pos: Dict, exit_date: datetime, pnl: float, reason: str):
        """Record a closed position (used by real-data mode)."""
        self.capital += pnl

        # P1-D: ruin stop — if capital reaches zero or below, block all future entries
        if self.capital <= 0 and not self._ruin_triggered:
            self._ruin_triggered = True
            _date_str = exit_date.strftime("%Y-%m-%d") if hasattr(exit_date, 'strftime') else str(exit_date)
            logger.warning(
                "RUIN EVENT on %s: capital dropped to $%.2f — halting all new entries",
                _date_str, self.capital,
            )

        max_risk = pos['max_loss'] * pos['contracts'] * 100
        _pos_type = pos.get('type', '')
        trade = {
            'ticker': pos['ticker'],
            'type': _pos_type,
            'entry_date': pos['entry_date'],
            'exit_date': exit_date,
            'exit_reason': reason,
            'expiration': pos.get('expiration'),
            'short_strike': pos['short_strike'],
            'long_strike': pos['long_strike'],
            'option_type': 'C' if _pos_type == 'bear_call_spread' else ('IC' if _pos_type == 'iron_condor' else 'P'),
            'credit': pos['credit'],
            'contracts': pos['contracts'],
            'pnl': pnl,
            'return_pct': (pnl / max_risk) * 100 if max_risk != 0 else 0,
            'entry_scan_time': pos.get('entry_scan_time'),
            'slippage_applied': pos.get('slippage_applied', 0.0),
        }

        self.trades.append(trade)
        logger.debug("Closed position: %s, P&L: $%.2f", reason, pnl)

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def _calculate_results(self) -> Dict:
        """Calculate backtest performance metrics."""
        if not self.trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'max_drawdown': 0,
                'sharpe_ratio': 0,
                'starting_capital': self.starting_capital,
                'ending_capital': self.capital,
                'return_pct': 0,
                'trades': [],
                'equity_curve': [],
                'bull_put_trades': 0,
                'bear_call_trades': 0,
                'bull_put_win_rate': 0,
                'bear_call_win_rate': 0,
                'iron_condor_trades': 0,
                'iron_condor_win_rate': 0,
                'monthly_pnl': {},
                'max_win_streak': 0,
                'max_loss_streak': 0,
                'friday_fallback_count': self._friday_fallback_count,
                'volume_skipped': self._volume_skipped,
                'ruin_triggered': self._ruin_triggered,
            }

        trades_df = pd.DataFrame(self.trades)

        # Basic stats
        total_trades = len(trades_df)
        winners = trades_df[trades_df['pnl'] > 0]
        losers = trades_df[trades_df['pnl'] < 0]

        win_rate = (len(winners) / total_trades) * 100 if total_trades > 0 else 0

        total_pnl = trades_df['pnl'].sum()
        avg_win = winners['pnl'].mean() if len(winners) > 0 else 0
        avg_loss = abs(losers['pnl'].mean()) if len(losers) > 0 else 0

        # Per-strategy breakdown
        bull_puts = trades_df[trades_df['type'] == 'bull_put_spread']
        bear_calls = trades_df[trades_df['type'] == 'bear_call_spread']
        iron_condors = trades_df[trades_df['type'] == 'iron_condor']

        bull_put_winners = bull_puts[bull_puts['pnl'] > 0] if len(bull_puts) > 0 else pd.DataFrame()
        bear_call_winners = bear_calls[bear_calls['pnl'] > 0] if len(bear_calls) > 0 else pd.DataFrame()
        iron_condor_winners = iron_condors[iron_condors['pnl'] > 0] if len(iron_condors) > 0 else pd.DataFrame()

        bull_put_wr = (len(bull_put_winners) / len(bull_puts)) * 100 if len(bull_puts) > 0 else 0
        bear_call_wr = (len(bear_call_winners) / len(bear_calls)) * 100 if len(bear_calls) > 0 else 0
        iron_condor_wr = (len(iron_condor_winners) / len(iron_condors)) * 100 if len(iron_condors) > 0 else 0

        # Equity curve analysis
        equity_df = pd.DataFrame(self.equity_curve, columns=['date', 'equity'])
        equity_df['returns'] = equity_df['equity'].pct_change()

        # Max drawdown
        equity_df['cummax'] = equity_df['equity'].cummax()
        equity_df['drawdown'] = (equity_df['equity'] - equity_df['cummax']) / equity_df['cummax']
        max_drawdown = equity_df['drawdown'].min() * 100

        # Sharpe ratio (annualized)
        returns = equity_df['returns'].dropna()
        if len(returns) > 0:
            sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0
        else:
            sharpe = 0

        # Profit factor (capped at 999.99 to avoid JSON-invalid Infinity)
        winning_total = winners['pnl'].sum() if len(winners) > 0 else 0
        losing_total = losers['pnl'].sum() if len(losers) > 0 else 0
        if losing_total != 0:
            profit_factor = round(abs(winning_total / losing_total), 2)
        elif winning_total > 0:
            profit_factor = 999.99
        else:
            profit_factor = 0

        # Return percentage
        if self.starting_capital != 0:
            return_pct = round(((self.capital - self.starting_capital) / self.starting_capital) * 100, 2)
        else:
            return_pct = 0

        # Monthly P&L breakdown (required for regime diversity overfit check)
        try:
            trades_df['_exit_month'] = pd.to_datetime(
                trades_df['exit_date'].apply(lambda d: d if isinstance(d, str) else str(d)[:10])
            ).dt.to_period('M')
            _monthly = (
                trades_df.groupby('_exit_month')
                .agg(_pnl=('pnl', 'sum'), _trades=('pnl', 'count'),
                     _wins=('pnl', lambda x: (x > 0).sum()))
                .reset_index()
            )
            _monthly['win_rate'] = (_monthly['_wins'] / _monthly['_trades']).round(3)
            monthly_pnl = {
                str(row['_exit_month']): {
                    'pnl': round(row['_pnl'], 2),
                    'trades': int(row['_trades']),
                    'wins': int(row['_wins']),
                    'win_rate': float(row['win_rate']),
                }
                for _, row in _monthly.iterrows()
            }
        except Exception:
            monthly_pnl = {}

        # Win/loss streak tracking (for overfit check F)
        max_win_streak = max_loss_streak = cur_win = cur_loss = 0
        for is_win in (trades_df['pnl'] > 0):
            if is_win:
                cur_win += 1
                cur_loss = 0
                max_win_streak = max(max_win_streak, cur_win)
            else:
                cur_loss += 1
                cur_win = 0
                max_loss_streak = max(max_loss_streak, cur_loss)

        # Bootstrap sampling (full MC mode only): resample per-trade PnL with replacement
        # to test whether a few lucky trades are driving the return.
        # Uses 200 bootstrap iterations — lightweight, just needs the pnl column.
        bootstrap_stats: dict = {}
        if self._rng is not None and self._mc_mode == 'full' and total_trades >= 5:
            pnl_vals = trades_df['pnl'].tolist()
            n = len(pnl_vals)
            _bs_returns = []
            for _ in range(200):
                sample = [pnl_vals[self._rng.randrange(n)] for _ in range(n)]
                bs_total_pnl = sum(sample)
                bs_ret = (bs_total_pnl / self.starting_capital) * 100
                _bs_returns.append(bs_ret)
            _bs_sorted = sorted(_bs_returns)
            _pct = lambda p: _bs_sorted[min(len(_bs_sorted) - 1, int(len(_bs_sorted) * p / 100))]
            bootstrap_stats = {
                'P5': round(_pct(5), 1),
                'P25': round(_pct(25), 1),
                'P50': round(_pct(50), 1),
                'P75': round(_pct(75), 1),
                'P95': round(_pct(95), 1),
                'pct_profitable': round(sum(1 for r in _bs_returns if r > 0) / len(_bs_returns) * 100, 1),
            }

        results = {
            'total_trades': total_trades,
            'winning_trades': len(winners),
            'losing_trades': len(losers),
            'win_rate': round(win_rate, 2),
            'total_pnl': round(total_pnl, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'profit_factor': profit_factor,
            'max_drawdown': round(max_drawdown, 2),
            'sharpe_ratio': round(sharpe, 2),
            'starting_capital': self.starting_capital,
            'ending_capital': round(self.capital, 2),
            'return_pct': return_pct,
            'trades': trades_df.to_dict('records'),
            'equity_curve': equity_df.to_dict('records'),
            'bull_put_trades': len(bull_puts),
            'bear_call_trades': len(bear_calls),
            'bull_put_win_rate': round(bull_put_wr, 2),
            'bear_call_win_rate': round(bear_call_wr, 2),
            'iron_condor_trades': len(iron_condors),
            'iron_condor_win_rate': round(iron_condor_wr, 2),
            'monthly_pnl': monthly_pnl,
            'max_win_streak': max_win_streak,
            'max_loss_streak': max_loss_streak,
            # P5a: how often did Mon/Wed targets have no data and fall back to Friday?
            'friday_fallback_count': self._friday_fallback_count,
            # Liquidity framework: spreads rejected by volume gate
            'volume_skipped': self._volume_skipped,
            # P1-D: whether capital reached zero during the backtest
            'ruin_triggered': self._ruin_triggered,
            # Bootstrap resampling stats (full MC mode only — empty dict otherwise)
            'bootstrap': bootstrap_stats,
        }

        # Bootstrap stats: resample per-trade PnL to get return distribution
        if self._mc_mode == 'full' and total_trades >= 5 and self._rng is not None:
            pnls = trades_df['pnl'].tolist()
            n = len(pnls)
            n_boot = 200
            boot_returns = []
            for _ in range(n_boot):
                sample = [pnls[self._rng.randint(0, n - 1)] for _ in range(n)]
                boot_returns.append(sum(sample) / self.starting_capital * 100)
            boot_returns.sort()
            idx_p5 = max(0, int(0.05 * n_boot) - 1)
            idx_p50 = max(0, int(0.50 * n_boot) - 1)
            idx_p95 = max(0, int(0.95 * n_boot) - 1)
            results['bootstrap'] = {
                'P5': round(boot_returns[idx_p5], 2),
                'P50': round(boot_returns[idx_p50], 2),
                'P95': round(boot_returns[idx_p95], 2),
                'pct_profitable': round(sum(1 for r in boot_returns if r > 0) / n_boot * 100, 1),
            }

        return results
