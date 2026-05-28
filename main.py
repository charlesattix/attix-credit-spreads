#!/usr/bin/env python3
"""
Credit Spread Trading System
Main entry point for the trading system.

Usage:
    python main.py scan          # Scan for new opportunities
    python main.py scheduler     # Run scans on market-hours schedule (14x/day)
    python main.py backtest      # Run backtest
    python main.py dashboard     # Display P&L dashboard
    python main.py alerts        # Generate alerts only

Position tracking, P&L, and trade management go through Alpaca API only.
"""

import argparse
import logging
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from compass.macro_db import (
    LIQUID_SECTOR_ETFS,
    get_current_macro_score,
    get_sector_rankings,
)
from shared.types import AppConfig

try:
    import sentry_sdk
    sentry_dsn = os.environ.get('SENTRY_DSN')
    if sentry_dsn:
        sentry_sdk.init(dsn=sentry_dsn, traces_sample_rate=0.1)
except ImportError:
    pass
except Exception as e:
    logging.getLogger(__name__).error(f"Sentry initialization failed: {e}", exc_info=True)

from alerts import AlertGenerator, TelegramBot
from alerts.alert_position_sizer import AlertPositionSizer
from alerts.alert_router import AlertRouter
from alerts.formatters.telegram import TelegramAlertFormatter
from compass.risk_gate import RiskGate
from backtest import Backtester, HistoricalOptionsData, PerformanceMetrics
from shared.data_cache import DataCache
from shared.database import get_trades, insert_alert, save_scanner_state, load_scanner_state
from shared.metrics import metrics
from shared.provider_protocol import DataProvider  # noqa: F401 – ARCH-PY-06
from strategy import CreditSpreadStrategy, OptionsAnalyzer, TechnicalAnalyzer
# Alias for unified strategy path
LegacyCreditSpreadStrategy = CreditSpreadStrategy
from shared.strategy_factory import build_strategy_list
from shared.snapshot_builder import build_live_market_snapshot, reprice_signals_from_chain
from shared.signal_scorer import score_signal
from shared.strategy_adapter import signal_to_opportunity
from tracker import PnLDashboard, TradeTracker
from utils import load_config, setup_logging, validate_config
from sentinel.heartbeat import emit_heartbeat

logger = logging.getLogger(__name__)


def _g22_exp_id() -> str:
    """Resolve the experiment id for SENTINEL Gate 22 heartbeats.

    Each live experiment runs main.py under its own .env.exp{NNN}, which
    sets EXPERIMENT_ID=EXP-NNN. Falls back to "EXP-MAIN" when the env
    var is unset (e.g. ad-hoc invocations).
    """
    return os.environ.get("EXPERIMENT_ID", "EXP-MAIN")

# Defaults for ML/rules blending and event risk — overridable via config.yaml
_DEFAULT_ML_SCORE_WEIGHT = 0.6
_DEFAULT_EVENT_RISK_THRESHOLD = 0.7


class CreditSpreadSystem:
    """
    Main credit spread trading system.
    """

    def __init__(
        self,
        config: AppConfig,
        strategy: Optional[LegacyCreditSpreadStrategy] = None,
        technical_analyzer: Optional[TechnicalAnalyzer] = None,
        options_analyzer: Optional[OptionsAnalyzer] = None,
        alert_generator: Optional[AlertGenerator] = None,
        telegram_bot: Optional[TelegramBot] = None,
        tracker: Optional[TradeTracker] = None,
        data_cache: Optional[DataCache] = None,
        ml_pipeline=None,
    ):
        """
        Initialize the trading system.

        Args:
            config: Configuration dictionary (already loaded & validated).
            strategy: Pre-built CreditSpreadStrategy or None for default.
            technical_analyzer: Pre-built TechnicalAnalyzer or None for default.
            options_analyzer: Pre-built OptionsAnalyzer or None for default.
            alert_generator: Pre-built AlertGenerator or None for default.
            telegram_bot: Pre-built TelegramBot or None for default.
            tracker: Pre-built TradeTracker or None for default.
            data_cache: Pre-built DataCache or None for default.
            ml_pipeline: Pre-built ML pipeline or None for default.
        """
        self.config = config
        # Track peak equity across calls for drawdown CB (P2 Fix 8).
        # Load persisted value from DB so restarts don't reset the high-water mark.
        starting_capital_init = float(self.config.get('risk', {}).get('account_size', 100_000))
        try:
            _db_path = os.environ.get('ATTIX_DB_PATH')
            _persisted = load_scanner_state("peak_equity", path=_db_path)
            self._peak_equity = float(_persisted) if _persisted is not None else starting_capital_init
        except Exception:
            self._peak_equity = starting_capital_init

        logger.info("=" * 80)
        logger.info("Credit Spread Trading System Starting")
        logger.info("=" * 80)

        # Initialize components — use injected instances when provided
        self.data_cache = data_cache or DataCache()
        # Legacy strategy kept for backward compat (config key reads, regime_mode)
        self.strategy = strategy or LegacyCreditSpreadStrategy(self.config)
        # NEW unified strategies — same classes used by backtester
        self.unified_strategies = build_strategy_list(self.config)
        self.technical_analyzer = technical_analyzer or TechnicalAnalyzer(self.config)
        self.options_analyzer = options_analyzer or OptionsAnalyzer(self.config, data_cache=self.data_cache)
        self.alert_generator = alert_generator or AlertGenerator(self.config)
        self.telegram_bot = telegram_bot or TelegramBot(self.config)
        self.tracker = tracker or TradeTracker(self.config)
        self.dashboard = PnLDashboard(self.config, self.tracker)

        # ML pipeline: disconnected from scan path — not validated in backtesting.
        # The backtester uses rules-based scoring + combo regime + COMPASS macro signals.
        # Reconnect only after the ML model has been backtested end-to-end.
        self.ml_pipeline = None  # intentionally unused; keep attribute for future wiring

        # AlpacaProvider — wired when alpaca.enabled=true in config (P0 Fix 1)
        self.alpaca_provider = None
        alpaca_cfg = self.config.get('alpaca', {})
        if alpaca_cfg.get('enabled', False):
            try:
                from strategy.alpaca_provider import AlpacaProvider
                api_key = alpaca_cfg.get('api_key', '')
                api_secret = alpaca_cfg.get('api_secret', '')
                # Resolve ${ENV_VAR} references
                if api_key.startswith('${') and api_key.endswith('}'):
                    api_key = os.environ.get(api_key[2:-1], '')
                if api_secret.startswith('${') and api_secret.endswith('}'):
                    api_secret = os.environ.get(api_secret[2:-1], '')
                self.alpaca_provider = AlpacaProvider(
                    api_key=api_key,
                    api_secret=api_secret,
                    paper=alpaca_cfg.get('paper', True),
                )
                logger.info("AlpacaProvider initialized (paper=%s)", alpaca_cfg.get('paper', True))
            except Exception as e:
                logger.critical(
                    "AlpacaProvider init FAILED — cannot start with alpaca.enabled=true: %s", e,
                    exc_info=True,
                )
                raise RuntimeError(
                    f"AlpacaProvider init failed (alpaca.enabled=true): {e}"
                ) from e

        # ExecutionEngine (P0 Fix 1)
        from execution.execution_engine import ExecutionEngine
        self.execution_engine = ExecutionEngine(
            alpaca_provider=self.alpaca_provider,
            db_path=os.environ.get('ATTIX_DB_PATH'),
        )

        # MASTERPLAN alert router pipeline (P0 Fix 1: now accepts execution_engine)
        self.alert_router = AlertRouter(
            risk_gate=RiskGate(config=self.config),
            position_sizer=AlertPositionSizer(config=self.config),
            telegram_bot=self.telegram_bot,
            formatter=TelegramAlertFormatter(),
            execution_engine=self.execution_engine,
            config=self.config,
        )

        logger.info("All components initialized successfully")

    def _get_compass_universe(self) -> list:
        """Return list of (ticker, direction_override, rrg_quadrant) tuples.

        SPY is always first with no override.  Sector ETFs are added when their
        current RRG quadrant meets the configured ``compass.min_leading_pct``
        threshold (mapped to RRG quadrant categories):

        - min_leading_pct >= 0.65 (strict): only ``Leading`` quadrant qualifies
          for bull-put spreads.
        - min_leading_pct  < 0.65 (relaxed): ``Leading`` or ``Improving``
          qualify.

        Bear-macro veto (macro_score < 45): sector ETFs are suppressed; only
        SPY is scanned.

        On any DB error the method falls back to the static ``config.tickers``
        list with no overrides, so existing behaviour is preserved.
        """
        compass_cfg = self.config.get('compass', {})
        min_leading_pct = compass_cfg.get('min_leading_pct', 0.65)

        # Strict mode: only "Leading"; relaxed: "Leading" or "Improving"
        if min_leading_pct >= 0.65:
            bull_quadrants = {'Leading'}
        else:
            bull_quadrants = {'Leading', 'Improving'}

        universe: list = [('SPY', None, None)]

        try:
            macro_score = get_current_macro_score()
            if macro_score < 45:
                logger.info(
                    "COMPASS: bear-macro veto (score=%.1f < 45) — scanning SPY only",
                    macro_score,
                )
                return universe

            rankings = get_sector_rankings()
            for item in rankings:
                ticker = item['ticker']
                if ticker not in LIQUID_SECTOR_ETFS:
                    continue
                quadrant = item.get('rrg_quadrant') or ''
                if quadrant in bull_quadrants:
                    universe.append((ticker, 'bull_put', quadrant))
                elif quadrant in ('Lagging', 'Weakening'):
                    universe.append((ticker, 'bear_call', quadrant))

            logger.info(
                "COMPASS universe (%d tickers): %s",
                len(universe),
                [(t, d) for t, d, _ in universe],
            )

        except Exception as e:
            logger.warning(
                "COMPASS universe selection failed — falling back to config tickers: %s", e
            )
            return [(t, None, None) for t in self.config.get('tickers', ['SPY'])]

        return universe

    def _augment_with_compass_state(self, state: dict) -> None:
        """Augment *state* (in-place) with COMPASS macro data when enabled.

        Adds:
            macro_score       float  — current overall macro score (0-100)
            rrg_quadrants     dict   — {ticker: rrg_quadrant} from latest snapshot
            macro_sizing_flag str    — 'boost' | 'neutral' | 'reduce'

        Only runs when ``compass.universe_enabled`` or ``compass.rrg_filter``
        is true in config.  Silently skips on DB errors so live scanning is
        never hard-blocked by a stale or missing macro_state.db.
        """
        compass_cfg = self.config.get('compass', {})
        if not (compass_cfg.get('universe_enabled', False) or compass_cfg.get('rrg_filter', False)):
            return
        try:
            macro_score = get_current_macro_score()
            rankings = get_sector_rankings()
            state['macro_score'] = macro_score
            state['rrg_quadrants'] = {
                r['ticker']: r.get('rrg_quadrant', '') for r in rankings
            }
            if macro_score < 45:
                state['macro_sizing_flag'] = 'reduce'
            elif macro_score > 75:
                state['macro_sizing_flag'] = 'boost'
            else:
                state['macro_sizing_flag'] = 'neutral'
            logger.debug(
                "COMPASS macro_score=%.1f sizing_flag=%s",
                macro_score,
                state['macro_sizing_flag'],
            )
        except Exception as e:
            logger.warning("COMPASS macro state fetch failed (non-fatal): %s", e)

    def scan_opportunities(self):
        """
        Scan for credit spread opportunities across all tickers.
        """
        logger.info("Starting opportunity scan")

        # EXP-3311 — NFP Entry Filter. Skip new entries on the day BEFORE
        # an NFP release when entry_gate.nfp_filter is enabled in config.
        entry_gate_cfg = self.config.get('entry_gate', {}) or {}
        if entry_gate_cfg.get('nfp_filter', False):
            from datetime import date as _date
            from shared.entry_gate import should_skip_entry_for_nfp
            blacklist_path = entry_gate_cfg.get(
                'blacklist_path', 'configs/event_blacklist.json'
            )
            skip, reason = should_skip_entry_for_nfp(
                today=_date.today(), blacklist_path=blacklist_path
            )
            if skip:
                logger.info("Entry gate active — %s", reason)
                metrics.inc('scans_skipped_nfp')
                return []

        # EXP-3309 — Pre-Close Execution Window. When execution.window_only is
        # true, skip scans outside execution.window so all new entries land in
        # the pre-close liquidity surge.
        execution_cfg = self.config.get('execution', {}) or {}
        if execution_cfg.get('window_only', False):
            from shared.execution_window import should_skip_for_window
            window = execution_cfg.get('window', '15:30-16:00')
            tz = execution_cfg.get('timezone', 'America/New_York')
            skip, reason = should_skip_for_window(window=window, tz=tz)
            if skip:
                logger.info("Execution window gate active — %s", reason)
                metrics.inc('scans_skipped_window')
                return []

        # EXP-3303b — Composite-stress regime gate. When the live
        # composite-stress reading exceeds theta (default 2.5, per the
        # backtest sweep), skip the scan for the day. Matches the
        # NFP/window-gate pattern above. Opt-in via config so existing
        # experiments do not change behaviour without explicit enable.
        regime_gate_cfg = self.config.get('regime_gate', {}) or {}
        if regime_gate_cfg.get('enabled', False):
            from shared.regime_gate import RegimeGate
            gate = RegimeGate.from_env()
            if gate.should_gate_spx_streams():
                logger.info(
                    "Composite-stress regime gate active — composite=%s "
                    "exceeds theta=%.2f; skipping scan",
                    gate.current_stress(), gate.theta,
                )
                metrics.inc('scans_skipped_regime_gate')
                return []

        all_opportunities = []

        # Build scan universe: static list or dynamic COMPASS selection
        compass_cfg = self.config.get('compass', {})
        if compass_cfg.get('universe_enabled', False):
            ticker_universe = self._get_compass_universe()
        else:
            ticker_universe = [(t, None, None) for t in self.config['tickers']]

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(
                    self._analyze_ticker, ticker, direction_override, rrg_quadrant
                ): ticker
                for ticker, direction_override, rrg_quadrant in ticker_universe
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    opportunities = future.result()
                    all_opportunities.extend(opportunities)
                except Exception as e:
                    logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
                    try:
                        from shared.telegram_alerts import notify_api_failure
                        notify_api_failure(
                            error_msg=str(e),
                            context=f"scan_ticker ({ticker})",
                        )
                    except Exception as _alert_err:
                        logger.warning("notify_api_failure itself failed: %s", _alert_err)

        if not all_opportunities:
            logger.info("No opportunities found")
            return []

        # Sort by score
        all_opportunities.sort(key=lambda x: x.get('score', 0), reverse=True)

        metrics.inc('scans_completed')

        # Display top opportunities
        logger.info(f"Found {len(all_opportunities)} total opportunities")
        logger.info("Top 5 opportunities:")

        for i, opp in enumerate(all_opportunities[:5], 1):
            logger.info(f"{i}. {opp.get('ticker', '?')} {opp.get('type', '?')} - Score: {opp.get('score', 0):.1f}")

        # Generate alerts
        self._generate_alerts(all_opportunities)

        return all_opportunities

    def _analyze_ticker(
        self,
        ticker: str,
        direction_override: Optional[str] = None,
        rrg_quadrant: Optional[str] = None,
    ) -> list:
        """Analyze a single ticker for opportunities.

        Args:
            ticker: Stock ticker symbol.
            direction_override: When set by COMPASS universe selection, one of
                ``'bull_put'`` or ``'bear_call'``.  Sector ETFs get their
                regime injected directly from the RRG quadrant instead of
                running ComboRegimeDetector.
            rrg_quadrant: The sector's current RRG quadrant string (e.g.
                ``'Leading'``).  Stored in technical_signals for downstream
                consumers (alert router, formatters).

        Returns:
            List of opportunity dicts.
        """
        try:
            # Get price data
            # Use 2y window when combo regime is active — MA200 needs ~200 days of warmup
            _period = '2y' if self.strategy.regime_mode == 'combo' else '1y'
            price_data = self.data_cache.get_history(ticker, period=_period)

            if price_data.empty:
                logger.warning(f"No price data for {ticker}")
                return []

            current_price = float(price_data['Close'].iloc[-1])

            # Get options chain
            options_chain = self.options_analyzer.get_options_chain(ticker)

            if options_chain.empty:
                logger.warning(f"No options data for {ticker}")
                return []

            # Technical analysis
            technical_signals = self.technical_analyzer.analyze(ticker, price_data)

            # P1 Fix 4: Inject combo regime into technical_signals for spread_strategy.
            #
            # COMPASS sector ETFs (direction_override set): skip ComboRegimeDetector and
            # derive regime directly from the RRG quadrant so each sector gets its own
            # independent signal rather than inheriting SPY's macro regime.
            if direction_override is not None:
                rrg_regime = 'bull' if direction_override == 'bull_put' else 'bear'
                technical_signals['combo_regime'] = rrg_regime
                if rrg_quadrant:
                    technical_signals['compass_rrg_quadrant'] = rrg_quadrant
                logger.info(
                    "%s: COMPASS RRG regime = %s (quadrant=%s)", ticker, rrg_regime, rrg_quadrant
                )
            elif self.strategy.regime_mode == 'combo' and self.strategy._combo_regime_detector:
                try:
                    vix_data = self.data_cache.get_history('^VIX', period='2y')
                    vix_by_date = {}
                    vix3m_by_date = {}
                    if not vix_data.empty:
                        for ts, row in vix_data.iterrows():
                            vix_by_date[ts] = float(row['Close'])
                    try:
                        vix3m_data = self.data_cache.get_history('^VIX3M', period='2y')
                        if not vix3m_data.empty:
                            for ts, row in vix3m_data.iterrows():
                                vix3m_by_date[ts] = float(row['Close'])
                    except Exception:
                        pass
                    regime_series = self.strategy._combo_regime_detector.compute_regime_series(
                        price_data=price_data,
                        vix_by_date=vix_by_date,
                        vix3m_by_date=vix3m_by_date,
                    )
                    if regime_series:
                        last_key = max(regime_series.keys())
                        current_regime = regime_series[last_key]
                        technical_signals['combo_regime'] = current_regime
                        logger.info("%s: ComboRegime = %s", ticker, current_regime)
                except Exception as e:
                    logger.error("ComboRegimeDetector failed for %s: %s — blocking trading", ticker, e)
                    # Do NOT default to 'neutral' — missing regime must block all entries.
                    # Previous default silently allowed bull puts and ICs.
                    technical_signals['combo_regime'] = None

            # IV analysis
            current_iv = self.options_analyzer.get_current_iv(options_chain)
            iv_data = self.options_analyzer.calculate_iv_rank(ticker, current_iv)

            logger.info(f"{ticker}: Price=${current_price:.2f}, IV Rank={iv_data.get('iv_rank', 0):.1f}%")

            # --- Unified entry path (same strategy classes as backtester) ---
            regime = technical_signals.get('combo_regime', None)

            # Block trading when regime detection failed (combo mode)
            if regime is None and hasattr(self.strategy, '_combo_regime_detector') and self.strategy._combo_regime_detector:
                logger.error("%s: regime=None with combo mode active — skipping all entries", ticker)
                return []

            # EXP-3303b — Per-Stream Selective Regime Gate.  Skip new entries
            # for SPX-sensitive tickers (SPY, QQQ) when regime is in a gated
            # state (transition / high_stress); leave sector ETFs untouched.
            from shared.regime_gate import should_gate_for_regime
            _gate_skip, _gate_reason = should_gate_for_regime(
                regime=regime, ticker=ticker, config=self.config
            )
            if _gate_skip:
                logger.info("%s: %s", ticker, _gate_reason)
                metrics.inc('scans_skipped_regime_gate')
                return []

            # VIX data for snapshot (already fetched for regime detector)
            vix_data = None
            try:
                vix_data = self.data_cache.get_history('^VIX', period='2y')
            except Exception:
                pass

            # Economic calendar events (needed for straddle/strangle strategy)
            upcoming_events: list = []
            recent_events: list = []
            try:
                from shared.economic_calendar import EconomicCalendar
                econ = EconomicCalendar()
                upcoming_events = econ.get_upcoming_events(days_ahead=2)
                recent_events = econ.get_recent_events(days_back=2)
            except Exception as e:
                logger.debug("Economic calendar unavailable: %s", e)

            snapshot = build_live_market_snapshot(
                ticker=ticker,
                price_data=price_data,
                current_price=current_price,
                iv_data=iv_data,
                technical_signals=technical_signals,
                regime=regime,
                vix_data=vix_data,
                upcoming_events=upcoming_events,
                recent_events=recent_events,
            )

            # Generate signals from all enabled strategies
            all_signals = []
            for strat in self.unified_strategies:
                try:
                    signals = strat.generate_signals(snapshot)
                    all_signals.extend(signals)
                except Exception as e:
                    logger.warning(
                        "%s: %s.generate_signals() failed: %s",
                        ticker, strat.__class__.__name__, e,
                    )

            # Score each signal using the 5-component scoring system
            for sig in all_signals:
                sig.score = score_signal(sig, iv_rank=iv_data.get('iv_rank', 25.0), technical_signals=technical_signals)

            # Re-price from real options chain when available
            if not options_chain.empty:
                slippage = float(self.config.get("risk", {}).get("slippage", 0.05))
                all_signals = reprice_signals_from_chain(all_signals, options_chain, slippage=slippage)

            # Convert Signal objects → opportunity dicts for AlertRouter / paper trader
            opportunities = []
            for sig in all_signals:
                try:
                    opp = signal_to_opportunity(sig, current_price)
                    opportunities.append(opp)
                except Exception as e:
                    logger.warning("signal_to_opportunity failed for %s: %s", sig.ticker, e)

            return opportunities

        except Exception as e:
            logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
            return []

    def _generate_alerts(self, opportunities: list):
        """
        Generate and send alerts.

        Args:
            opportunities: List of opportunities
        """
        logger.info("Generating alerts...")

        # Generate alert outputs
        outputs = self.alert_generator.generate_alerts(opportunities)

        for output_type, output_path in outputs.items():
            logger.info(f"{output_type.upper()} alerts: {output_path}")

        # Persist all opportunities to SQLite so the web dashboard can read them
        for opp in opportunities:
            try:
                insert_alert(opp)
            except Exception as e:
                logger.warning(f"Failed to persist alert for {opp.get('ticker')}: {e}")

        # Send Telegram alerts if enabled (legacy flow — backward compatible)
        if self.telegram_bot.enabled:
            top_opportunities = [
                opp for opp in opportunities
                if opp.get('score', 0) >= 60
            ][:5]

            sent = self.telegram_bot.send_alerts(top_opportunities, self.alert_generator)
            logger.info(f"Sent {sent} Telegram alerts")

        # New MASTERPLAN alert router pipeline (runs alongside legacy flow)
        try:
            account_state = self._build_account_state()
            routed = self.alert_router.route_opportunities(opportunities, account_state)
            logger.info(f"Alert router dispatched {len(routed)} alerts")
        except Exception as e:
            logger.warning(f"Alert router pipeline failed (non-fatal): {e}")

    def _build_account_state(self) -> Dict:
        """Build account_state dict from real Alpaca positions and account info.

        When AlpacaProvider is available, reads real portfolio value, open
        positions, and closed-trade P&L so RiskGate can enforce live limits.
        Falls back to config-based static values if Alpaca is unavailable.
        """
        starting_capital = float(self.config.get('risk', {}).get('account_size', 100_000))

        if not self.alpaca_provider:
            # Alert-only mode: static skeleton — risk gate still functions
            state = {
                "account_value": starting_capital,
                "peak_equity": starting_capital,
                "open_positions": [],
                "daily_pnl_pct": 0.0,
                "weekly_pnl_pct": 0.0,
                "recent_stops": [],
            }
            self._augment_with_compass_state(state)
            return state

        try:
            account = self.alpaca_provider.get_account()
            # SENTINEL G22: confirmed-alive Alpaca call → emit heartbeat.
            emit_heartbeat(_g22_exp_id(), notes="account ok")
            account_value = float(account['portfolio_value'])

            alpaca_positions = self.alpaca_provider.get_positions()

            # Load open trades from SQLite for metadata
            db_positions = get_trades(status="open", path=os.environ.get('ATTIX_DB_PATH'))

            # Build enriched position list from DB metadata + Alpaca market values
            open_positions = []
            for db_pos in db_positions:
                ticker = db_pos.get('ticker', '')
                matching = [p for p in alpaca_positions if ticker in p.get('symbol', '')]
                unrealized_pl = sum(float(p.get('unrealized_pl', 0)) for p in matching)
                max_loss = db_pos.get('pnl') or 0  # fallback
                # Estimate max_loss from spread geometry if possible
                short_strike = db_pos.get('short_strike') or 0
                long_strike = db_pos.get('long_strike') or 0
                credit = float(db_pos.get('credit') or 0)
                contracts = int(db_pos.get('contracts') or 1)
                if short_strike and long_strike:
                    max_loss = abs(short_strike - long_strike) * contracts * 100
                risk_pct = (max_loss / account_value) if account_value > 0 else 0
                open_positions.append({
                    "id": db_pos.get('id'),
                    "ticker": ticker,
                    "direction": db_pos.get('strategy_type', ''),
                    "entry_time": db_pos.get('entry_date'),
                    "credit": credit,
                    "contracts": contracts,
                    "unrealized_pl": unrealized_pl,
                    "risk_pct": risk_pct,
                    "expiration": str(db_pos.get('expiration') or ''),
                })

            # Daily / weekly realized P&L from closed trades
            now = datetime.now(timezone.utc)
            db_path = os.environ.get('ATTIX_DB_PATH')
            closed_all = (
                get_trades(status="closed_profit", path=db_path) +
                get_trades(status="closed_loss", path=db_path)
            )

            def _days_ago(trade, days):
                exit_str = trade.get('exit_date') or ''
                try:
                    t = datetime.fromisoformat(exit_str)
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    return (now - t).days < days
                except (ValueError, TypeError):
                    return False

            daily_pnl = sum(t.get('pnl') or 0 for t in closed_all if _days_ago(t, 1))
            weekly_pnl = sum(t.get('pnl') or 0 for t in closed_all if _days_ago(t, 7))
            daily_pnl_pct = (daily_pnl / account_value) if account_value > 0 else 0.0
            weekly_pnl_pct = (weekly_pnl / account_value) if account_value > 0 else 0.0

            recent_stops = [
                {"ticker": t.get('ticker'), "stopped_at": t.get('exit_date')}
                for t in closed_all
                if t.get('exit_reason') == 'stop_loss' and _days_ago(t, 7)
            ]

            if account_value > self._peak_equity:
                self._peak_equity = account_value
                try:
                    db_path = os.environ.get('ATTIX_DB_PATH')
                    save_scanner_state("peak_equity", str(self._peak_equity), path=db_path)
                except Exception as _pe_err:
                    logger.warning("_build_account_state: could not persist peak_equity: %s", _pe_err)
            peak_equity = self._peak_equity

            state = {
                "account_value": account_value,
                "peak_equity": peak_equity,
                "open_positions": open_positions,
                "daily_pnl_pct": daily_pnl_pct,
                "weekly_pnl_pct": weekly_pnl_pct,
                "recent_stops": recent_stops,
            }
            # Populate current_vix for RiskGate rule 7.5 (vix_max_entry hard block).
            try:
                vix_hist = self.data_cache.get_history('^VIX', period='5d')
                if not vix_hist.empty:
                    state['current_vix'] = float(vix_hist['Close'].iloc[-1])
            except Exception as _vix_err:
                logger.debug("_build_account_state: VIX fetch skipped: %s", _vix_err)
            self._augment_with_compass_state(state)
            return state

        except Exception as e:
            logger.error("_build_account_state: Alpaca query failed, using static fallback: %s", e)
            # circuit_breaker=True tells the risk gate to block ALL new trades when we
            # cannot verify current portfolio exposure (PARTIAL #12 fix).
            state = {
                "account_value": starting_capital,
                "peak_equity": starting_capital,
                "open_positions": [],
                "daily_pnl_pct": 0.0,
                "weekly_pnl_pct": 0.0,
                "recent_stops": [],
                "circuit_breaker": True,
            }
            self._augment_with_compass_state(state)
            return state

    def run_backtest(self, ticker: str = 'SPY', lookback_days: int = 365, clear_cache: bool = False):
        """
        Run backtest on historical data.

        Args:
            ticker: Ticker to backtest
            lookback_days: Days of history to test
            clear_cache: If True, clear the options price cache before running
        """
        logger.info(f"Starting backtest for {ticker}")

        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=lookback_days)

        # Build HistoricalOptionsData if Polygon API key is available
        historical_data = None
        polygon_key = os.environ.get('POLYGON_API_KEY', '')
        if not polygon_key:
            polygon_cfg = self.config.get('data', {}).get('polygon', {})
            polygon_key = polygon_cfg.get('api_key', '')
            # Resolve env var references like "${POLYGON_API_KEY}"
            if polygon_key.startswith('${') and polygon_key.endswith('}'):
                polygon_key = os.environ.get(polygon_key[2:-1], '')

        if polygon_key:
            historical_data = HistoricalOptionsData(polygon_key)
            if clear_cache:
                historical_data.clear_cache()
            logger.info("Using real Polygon historical options data")
        else:
            logger.warning("No POLYGON_API_KEY — falling back to heuristic backtester")

        backtester = Backtester(self.config, historical_data=historical_data)
        results = backtester.run_backtest(ticker, start_date, end_date)

        if historical_data:
            historical_data.close()

        if not results:
            logger.error("Backtest failed")
            return

        # Display results
        perf = PerformanceMetrics(self.config)
        perf.print_summary(results)

        # Generate report
        report_file = perf.generate_report(results)
        logger.info(f"Backtest report saved to: {report_file}")

        # Write to canonical path for web dashboard API
        import json

        from shared.constants import OUTPUT_DIR
        canonical_path = os.path.join(OUTPUT_DIR, 'backtest_results.json')
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        try:
            with open(canonical_path, 'w') as f:
                json.dump(results, f, indent=2, default=str)
        except OSError as e:
            logger.warning(f"Failed to write canonical backtest results: {e}")

        return results

    def show_dashboard(self):
        """
        Display P&L dashboard.
        """
        self.dashboard.display_dashboard()

    def generate_alerts_only(self):
        """
        Generate and send alerts from stored alert data in the database.
        Does NOT run a new scan — reads previously saved alerts.
        """
        from shared.database import get_latest_alerts

        logger.info("Generating alerts from stored data")

        stored_alerts = get_latest_alerts(limit=50)
        if not stored_alerts:
            logger.info("No stored alerts found. Run a scan first.")
            return

        logger.info(f"Found {len(stored_alerts)} stored alerts")
        self._generate_alerts(stored_alerts)


def _validate_paper_mode_safety(config: dict) -> None:
    """Safety check: when paper_mode=true, reject any configuration that points at live Alpaca.

    Rules enforced:
      1. alpaca.paper must be True (not False)
      2. alpaca.base_url (if set) must contain the substring "paper"

    These checks prevent a paper-trading config from accidentally hitting the
    live brokerage API (which would place real orders with real money).

    Raises:
        ValueError: if paper_mode=true and the Alpaca config looks live.
    """
    if not config.get("paper_mode", False):
        return  # live mode — no constraint, skip

    alpaca_cfg = config.get("alpaca", {})

    # Rule 1: alpaca.paper must be True
    if not alpaca_cfg.get("paper", True):
        raise ValueError(
            "SAFETY: paper_mode=true but alpaca.paper=false — "
            "this would submit orders to the live Alpaca brokerage. "
            "Set alpaca.paper: true or remove paper_mode from your config."
        )

    # Rule 2: base_url (if explicitly set) must contain "paper"
    base_url = alpaca_cfg.get("base_url", "")
    if base_url and "paper" not in base_url.lower():
        raise ValueError(
            f"SAFETY: paper_mode=true but alpaca.base_url='{base_url}' does not "
            "contain 'paper' — this looks like a live endpoint. "
            "Use https://paper-api.alpaca.markets or remove base_url."
        )

    logger.info(
        "Paper-mode safety check PASSED (alpaca.paper=%s base_url=%s)",
        alpaca_cfg.get("paper"), base_url or "(default)",
    )


def create_system(config_file: str = 'config.yaml', env_file: str = None) -> CreditSpreadSystem:
    """Factory function that loads config and builds a CreditSpreadSystem.

    This preserves the original single-argument construction workflow:
    load config, validate, set up logging, then instantiate all components
    via the default ``None`` paths inside ``CreditSpreadSystem.__init__``.

    Args:
        config_file: Path to the YAML configuration file.
        env_file: Optional path to a .env file (e.g. .env.exp154). Defaults to .env.

    Returns:
        A fully initialised CreditSpreadSystem.
    """
    config = load_config(config_file, env_file=env_file)
    validate_config(config)
    _validate_paper_mode_safety(config)

    # Bug #1 fix: honour YAML db_path for experiment isolation.
    # CLI arg (--db-path) takes priority → already in env.  YAML is next.
    if not os.environ.get('ATTIX_DB_PATH') and config.get('db_path'):
        os.environ['ATTIX_DB_PATH'] = config['db_path']
        logger.info("Using db_path from config: %s", config['db_path'])

    setup_logging(config)

    system = CreditSpreadSystem(config=config)

    # P2 Fix 9: Run position reconciliation on startup
    if system.alpaca_provider:
        try:
            from shared.reconciler import PositionReconciler
            reconciler = PositionReconciler(
                alpaca=system.alpaca_provider,
                db_path=os.environ.get('ATTIX_DB_PATH'),
            )
            result = reconciler.reconcile()
            logger.info("Startup reconciliation complete: %s", result)
        except Exception as e:
            logger.warning("Startup reconciliation failed (non-fatal): %s", e)

    # Pre-warm the data cache with commonly used tickers
    system.data_cache.pre_warm(['SPY', '^VIX', 'TLT'])

    return system


def main():
    """
    Main entry point.
    """
    parser = argparse.ArgumentParser(
        description='Credit Spread Trading System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py scan              # Run one scan now
  python main.py scheduler         # Run scans on market-hours schedule (14x/day)
  python main.py backtest          # Run backtest on SPY
  python main.py backtest --ticker QQQ --days 180
  python main.py dashboard         # Show P&L dashboard
  python main.py alerts            # Generate alerts only
        Note: position tracking and P&L are managed via Alpaca API directly.
        """
    )

    parser.add_argument(
        'command',
        choices=['scan', 'scheduler', 'backtest', 'dashboard', 'alerts'],
        help='Command to run'
    )

    parser.add_argument(
        '--ticker',
        default='SPY',
        help='Ticker for backtest (default: SPY)'
    )

    parser.add_argument(
        '--days',
        type=int,
        default=365,
        help='Lookback days for backtest (default: 365)'
    )

    parser.add_argument(
        '--clear-cache',
        action='store_true',
        default=False,
        help='Clear options price cache before backtesting'
    )

    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Config file path (default: config.yaml)'
    )

    parser.add_argument(
        '--env-file',
        default=None,
        dest='env_file',
        help='Path to .env file to load (default: .env in cwd)'
    )

    parser.add_argument(
        '--db',
        default=None,
        dest='db_path',
        help='Path to SQLite database file (default: data/pilotai.db)'
    )

    args = parser.parse_args()

    # Register graceful shutdown handlers
    def _shutdown_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logging.getLogger(__name__).info(
            f"Received shutdown signal ({sig_name}), exiting gracefully..."
        )
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    try:
        # Set custom DB path before any database imports use the default.
        # CLI --db takes priority; fall back to db_path field in the YAML config.
        if args.db_path:
            os.environ['ATTIX_DB_PATH'] = args.db_path
        else:
            try:
                import yaml as _yaml
                with open(args.config or 'config.yaml') as _f:
                    _raw = _yaml.safe_load(_f)
                _cfg_db = (_raw or {}).get('db_path')
                if _cfg_db:
                    os.environ['ATTIX_DB_PATH'] = _cfg_db
            except Exception:
                pass

        # Initialize system
        system = create_system(config_file=args.config, env_file=args.env_file)

        # Execute command
        if args.command == 'scan':
            # ── SENTINEL pre-scan guard ───────────────────────────────────────
            try:
                import yaml as _sentinel_yaml
                with open(args.config or 'config.yaml') as _sf:
                    _sentinel_cfg = _sentinel_yaml.safe_load(_sf) or {}
                _sentinel_exp_id = _sentinel_cfg.get('experiment_id', '')
                if _sentinel_exp_id:
                    from sentinel.guards import pre_scan_check
                    pre_scan_check(_sentinel_exp_id)
            except SystemExit:
                raise
            except Exception as _sg_exc:
                logging.getLogger(__name__).warning(
                    "SENTINEL guard skipped (config unreadable): %s", _sg_exc
                )
            # ─────────────────────────────────────────────────────────────────

            # Per-experiment file lock: prevent two overlapping scan processes
            # for the same experiment (e.g. cron fires while previous run is
            # still executing).  Lock path: /tmp/attix_{exp_id}.lock
            # Lock is held for the lifetime of this process; OS releases it on exit.
            import fcntl as _fcntl
            _db_for_lock = args.db_path or os.environ.get("ATTIX_DB_PATH", "")
            _lock_base = os.path.basename(_db_for_lock).replace("attix_", "").replace(".db", "")
            _exp_id_lock = _lock_base if _lock_base else "unk"
            _lock_path = f"/tmp/attix_{_exp_id_lock}.lock"
            _lock_fh = open(_lock_path, "w")
            try:
                _fcntl.flock(_lock_fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                logger.info("Acquired scan lock for %s: %s", _exp_id_lock, _lock_path)
            except BlockingIOError:
                logger.warning(
                    "Scanner already running for %s (lock held: %s) — exiting without scan",
                    _exp_id_lock, _lock_path,
                )
                sys.exit(0)

            system.scan_opportunities()
            # Run one position-monitor cycle after every scan so stop-loss /
            # profit-target exits are evaluated even in cron (one-shot) mode.
            # In scheduler mode PositionMonitor runs as a background thread;
            # here we call _check_positions() directly for the same effect.
            if system.alpaca_provider:
                from execution.position_monitor import PositionMonitor
                _pm = PositionMonitor(
                    alpaca_provider=system.alpaca_provider,
                    config=system.config,
                    db_path=os.environ.get('ATTIX_DB_PATH'),
                )
                _pm._check_positions()

        elif args.command == 'scheduler':
            import threading
            import time as _time

            from execution.position_monitor import PositionMonitor
            from shared.scheduler import SLOT_MACRO_WEEKLY, SLOT_RETRAIN, SLOT_SCAN, ScanScheduler

            def _run_macro_weekly_with_retry(max_attempts: int = 5) -> None:
                """Run the weekly macro snapshot with exponential backoff retries.

                Backoff schedule (seconds between attempts):
                  attempt 1 → fail → wait 5min
                  attempt 2 → fail → wait 10min
                  attempt 3 → fail → wait 20min
                  attempt 4 → fail → wait 40min
                  attempt 5 → fail → Telegram alert, give up
                """
                from scripts.run_macro_snapshot import run_weekly as _run_weekly
                from shared.telegram_alerts import send_message as _tg_send
                _BACKOFF_SECS = [300, 600, 1200, 2400]  # waits after attempts 1-4
                for attempt in range(1, max_attempts + 1):
                    try:
                        logger.info("Macro weekly snapshot — attempt %d/%d", attempt, max_attempts)
                        _run_weekly()
                        logger.info(
                            "MACRO WEEKLY SNAPSHOT SUCCEEDED (attempt %d/%d) — "
                            "macro_state.db updated, API serving fresh data",
                            attempt, max_attempts,
                        )
                        return
                    except Exception:
                        logger.exception(
                            "Macro weekly snapshot FAILED on attempt %d/%d",
                            attempt, max_attempts,
                        )
                        if attempt < max_attempts:
                            delay = _BACKOFF_SECS[attempt - 1]
                            logger.info(
                                "Retrying macro weekly snapshot in %ds (%.0fmin)...",
                                delay, delay / 60,
                            )
                            _time.sleep(delay)
                logger.error(
                    "MACRO WEEKLY SNAPSHOT FAILED after %d attempts — "
                    "manual run required: python3 scripts/run_macro_snapshot.py --weekly",
                    max_attempts,
                )
                _tg_send(
                    "⚠️ <b>MACRO WEEKLY SNAPSHOT FAILED</b>\n\n"
                    f"All {max_attempts} attempts exhausted. "
                    "macro_state.db is stale.\n\n"
                    "Manual fix: <code>python3 scripts/run_macro_snapshot.py --weekly</code>"
                )

            def _write_heartbeat():
                """Write a heartbeat file after each scan cycle for watchdog monitoring."""
                try:
                    _hb_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
                    os.makedirs(_hb_dir, exist_ok=True)
                    # Derive experiment_id from db_path (e.g. data/attix_champion.db → champion)
                    # or fall back to config file name
                    _exp_id = "unknown"
                    _db = args.db_path or os.environ.get("ATTIX_DB_PATH", "")
                    if _db:
                        _base = os.path.basename(_db).replace("attix_", "").replace(".db", "")
                        if _base:
                            _exp_id = _base
                    _hb_path = os.path.join(_hb_dir, f".last_scan_{_exp_id}")
                    with open(_hb_path, "w") as _hf:
                        _hf.write(datetime.now(timezone.utc).isoformat())
                    logger.debug("Heartbeat written: %s", _hb_path)
                except Exception as _hb_exc:
                    logger.warning("Failed to write heartbeat: %s", _hb_exc)

            # ── Phase 4: RetrainScheduler for SLOT_RETRAIN (16:30 ET daily) ──
            from compass.archive.retrain_scheduler import RetrainScheduler
            from compass.ensemble_signal_model import EnsembleSignalModel

            _model_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "models")
            _retrain_scheduler = RetrainScheduler(
                model_dir=_model_dir,
                model_class=EnsembleSignalModel,
                telegram_bot=getattr(system, 'telegram_bot', None),
            )

            def _run_retrain_check() -> None:
                """Load recent closed trades, build features, and run retrain check."""
                import numpy as np
                import pandas as pd
                try:
                    db_path = args.db_path or os.environ.get("ATTIX_DB_PATH")
                    trades = get_trades(status="closed", path=db_path)
                    if not trades or len(trades) < 30:
                        logger.info(
                            "SLOT_RETRAIN: only %d closed trades (need ≥30), skipping retrain check",
                            len(trades) if trades else 0,
                        )
                        return
                    trades_df = pd.DataFrame(trades)

                    # Build feature matrix via FeaturePipeline if available
                    try:
                        from compass.feature_pipeline import FeaturePipeline
                        pipeline = FeaturePipeline()
                        features_df = pipeline.transform(trades_df)
                    except Exception as feat_exc:
                        logger.warning("FeaturePipeline failed (%s), passing raw trades_df", feat_exc)
                        features_df = trades_df

                    # Extract labels: "win" column (1=profitable, 0=loss)
                    if "win" in trades_df.columns:
                        labels = trades_df["win"].values.astype(int)
                    elif "pnl" in trades_df.columns:
                        labels = (trades_df["pnl"] > 0).astype(int).values
                    else:
                        logger.warning("SLOT_RETRAIN: no 'win' or 'pnl' column, skipping")
                        return

                    result = _retrain_scheduler.run_retrain_check(
                        features_df, labels, current_model=None,
                    )
                    logger.info(
                        "SLOT_RETRAIN complete: triggered=%s retrained=%s",
                        result.trigger.triggered, result.retrained,
                    )
                except Exception:
                    logger.exception("SLOT_RETRAIN failed")

            def scan_and_sync(slot_type=SLOT_SCAN):
                # SENTINEL G22 producer wiring — every scheduler tick emits
                # a heartbeat for the active EXPERIMENT_ID, so the consumer
                # in sentinel/runtime.py never sees a silent slot.
                _exp_id = _g22_exp_id()
                if slot_type == SLOT_MACRO_WEEKLY:
                    _run_macro_weekly_with_retry()
                    emit_heartbeat(_exp_id, notes="macro weekly snapshot")
                elif slot_type == SLOT_RETRAIN:
                    _run_retrain_check()
                    _write_heartbeat()
                    emit_heartbeat(_exp_id, notes="retrain check complete")
                else:
                    system.scan_opportunities()
                    _write_heartbeat()
                    emit_heartbeat(_exp_id, notes="scan complete")

                # Sync trade data to dashboard (separate volume)
                try:
                    import requests as _req
                    _dashboard_url = os.environ.get("RAILWAY_SERVICE_ATTIX_DASHBOARD_URL", "") or os.environ.get("RAILWAY_SERVICE_ATTIX_CREDIT_SPREADS_URL", "")
                    _api_key = os.environ.get("DASHBOARD_API_KEY", "")
                    _db_path = args.db_path or os.environ.get("ATTIX_DB_PATH", "")
                    if _dashboard_url and _api_key and _db_path and os.path.exists(_db_path):
                        _base = _dashboard_url if _dashboard_url.startswith("http") else f"https://{_dashboard_url}"
                        import sqlite3 as _sql
                        _conn = _sql.connect(f"file:{_db_path}?mode=ro", uri=True)
                        _conn.row_factory = _sql.Row
                        _trades = [dict(r) for r in _conn.execute(
                            "SELECT * FROM trades ORDER BY entry_date DESC LIMIT 50"
                        ).fetchall()]
                        _open = [dict(r) for r in _conn.execute(
                            "SELECT * FROM trades WHERE status = 'open'"
                        ).fetchall()]
                        _conn.close()
                        _req.post(
                            f"{_base.rstrip('/')}/api/v1/experiments/{_exp_id}/sync-trades",
                            json={"trades": _trades, "open_trades": _open},
                            headers={"X-API-Key": _api_key},
                            timeout=5,
                        )
                except Exception:
                    pass  # non-fatal

                # Sentinel v3: push scan heartbeat to dashboard API so the
                # API-based watchdog can check scan recency without filesystem access.
                try:
                    import requests as _req
                    _dashboard_url = os.environ.get("RAILWAY_SERVICE_ATTIX_DASHBOARD_URL", "") or os.environ.get("RAILWAY_SERVICE_ATTIX_CREDIT_SPREADS_URL", "")
                    _api_key = os.environ.get("DASHBOARD_API_KEY", "")
                    if _dashboard_url and _api_key:
                        _base = _dashboard_url if _dashboard_url.startswith("http") else f"https://{_dashboard_url}"
                        _req.post(
                            f"{_base.rstrip('/')}/api/v1/experiments/{_exp_id}/heartbeat",
                            json={
                                "scan_slot": slot_type,
                                "scan_time": datetime.now(timezone.utc).isoformat(),
                                "status": "ok",
                            },
                            headers={"X-API-Key": _api_key},
                            timeout=5,
                        )
                except Exception:
                    pass  # non-fatal

                # Push Alpaca portfolio snapshot to dashboard
                try:
                    import requests as _req
                    _dashboard_url = os.environ.get("RAILWAY_SERVICE_ATTIX_DASHBOARD_URL", "") or os.environ.get("RAILWAY_SERVICE_ATTIX_CREDIT_SPREADS_URL", "")
                    _api_key = os.environ.get("DASHBOARD_API_KEY", "")
                    _alpaca_key = os.environ.get("ALPACA_API_KEY", "")
                    _alpaca_secret = os.environ.get("ALPACA_API_SECRET", "")
                    if _dashboard_url and _api_key and _alpaca_key and _alpaca_secret:
                        _base = _dashboard_url if _dashboard_url.startswith("http") else f"https://{_dashboard_url}"
                        _alpaca_base = "https://paper-api.alpaca.markets"
                        _alpaca_headers = {
                            "APCA-API-KEY-ID": _alpaca_key,
                            "APCA-API-SECRET-KEY": _alpaca_secret,
                        }
                        _account = _req.get(f"{_alpaca_base}/v2/account", headers=_alpaca_headers, timeout=5).json()
                        _positions = _req.get(f"{_alpaca_base}/v2/positions", headers=_alpaca_headers, timeout=5).json()
                        _orders = _req.get(f"{_alpaca_base}/v2/orders?status=open&limit=50", headers=_alpaca_headers, timeout=5).json()
                        # PR2: include the durable equity curve the PositionMonitor
                        # scan persists to this experiment's DB, so the dashboard
                        # (a separate service/volume) can render the accruing chart.
                        try:
                            from shared.database import get_equity_history
                            _eq_history = get_equity_history(
                                _exp_id, limit=365,
                                path=os.environ.get("ATTIX_DB_PATH"),
                            )
                        except Exception:
                            _eq_history = []
                        _req.post(
                            f"{_base.rstrip('/')}/api/v1/experiments/{_exp_id}/push-portfolio",
                            json={
                                "equity": float(_account.get("equity") or 0),
                                "cash": float(_account.get("cash") or 0),
                                "buying_power": float(_account.get("buying_power") or 0),
                                "unrealized_pl": float(_account.get("unrealized_pl") or 0),
                                "positions": _positions if isinstance(_positions, list) else [],
                                "orders": _orders if isinstance(_orders, list) else [],
                                "equity_history": _eq_history,
                            },
                            headers={"X-API-Key": _api_key},
                            timeout=5,
                        )
                except Exception:
                    pass  # non-fatal

            scheduler = ScanScheduler(scan_fn=scan_and_sync)

            # P0 Fix 2: Start PositionMonitor as background daemon thread
            position_monitor = None
            alpaca_enabled = system.config.get('alpaca', {}).get('enabled', False)
            if system.alpaca_provider:
                position_monitor = PositionMonitor(
                    alpaca_provider=system.alpaca_provider,
                    config=system.config,
                    db_path=args.db_path,
                )
                # Register unified strategies for manage_position() dispatch
                position_monitor.register_strategies(system.unified_strategies)
                monitor_thread = threading.Thread(
                    target=position_monitor.start,
                    daemon=True,
                    name="PositionMonitor",
                )
                monitor_thread.start()
                logger.info("PositionMonitor started as background thread")
            elif alpaca_enabled:
                raise RuntimeError(
                    "PositionMonitor cannot start: alpaca.enabled=true but AlpacaProvider is None. "
                    "Positions would be completely unmonitored. Fix AlpacaProvider init first."
                )
            else:
                logger.info("PositionMonitor skipped — alpaca not enabled in config")

            # Let SIGTERM/SIGINT stop cleanly
            def _stop_scheduler(signum, frame):
                sig_name = signal.Signals(signum).name
                logger.info("Received %s — stopping scheduler", sig_name)
                if position_monitor:
                    position_monitor.stop()
                scheduler.stop()

            signal.signal(signal.SIGTERM, _stop_scheduler)
            signal.signal(signal.SIGINT, _stop_scheduler)

            logger.info("Starting scan scheduler (14 scans/day, ET weekdays)")
            scheduler.run_forever()

        elif args.command == 'backtest':
            system.run_backtest(ticker=args.ticker, lookback_days=args.days, clear_cache=args.clear_cache)

        elif args.command == 'dashboard':
            system.show_dashboard()

        elif args.command == 'alerts':
            system.generate_alerts_only()

        logger.info("Command completed successfully")

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
