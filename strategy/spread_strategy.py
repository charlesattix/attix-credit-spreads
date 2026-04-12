"""
Credit Spread Strategy Engine
Implements bull put spreads and bear call spreads with high probability setups.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from shared.types import IronCondorOpportunity, ScoredSpreadOpportunity, SpreadOpportunity

logger = logging.getLogger(__name__)

# Scoring weights and thresholds for _score_opportunities
SCORING_WEIGHTS = {
    "credit_max": 25,           # Max points for credit component
    "credit_scale": 0.5,        # Scalar: credit_pct * this value
    "risk_reward_max": 25,      # Max points for risk/reward component
    "risk_reward_scale": 8,     # Scalar: risk_reward * this value
    "pop_max": 25,              # Max points for probability-of-profit component
    "pop_baseline": 85,         # POP % that earns full points
    "technical_max": 15,        # Max points for technical alignment component
    "tech_strong_signal": 10,   # Points for strong directional alignment
    "tech_neutral_signal": 5,   # Points for neutral alignment
    "tech_support_resistance": 5,  # Bonus for near support/resistance
    "iv_max": 10,               # Max points for IV component
    "iv_divisor": 10,           # Divisor applied to iv_rank
    # Iron condor specific
    "condor_tech_neutral": 10,  # Points for neutral trend alignment
    "condor_tech_regime": 5,    # Points for mean-reverting regime
    "condor_tech_rsi_range": 5, # Points for RSI in 40-60 range
}


class CreditSpreadStrategy:
    """
    Main strategy class for credit spreads.
    Handles both bull put spreads and bear call spreads.
    """

    def __init__(self, config: Dict):
        """
        Initialize strategy with configuration.

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.strategy_params = config['strategy']
        self.risk_params = config['risk']

        # Dynamic spread width thresholds
        self.spread_width_high_iv = self.strategy_params.get('spread_width_high_iv', 15)
        self.spread_width_low_iv = self.strategy_params.get('spread_width_low_iv', 10)
        self.default_spread_width = self.strategy_params.get('spread_width', 10)

        # Regime detection mode: 'combo' (v2 rule-based) or 'hmm' (legacy ML)
        self.regime_mode = self.strategy_params.get('regime_mode', 'combo')
        self._combo_regime_detector = None
        if self.regime_mode == 'combo':
            try:
                from compass.regime import ComboRegimeDetector
                regime_config = self.strategy_params.get('regime_config', {})
                self._combo_regime_detector = ComboRegimeDetector(regime_config)
                logger.info("CreditSpreadStrategy: using ComboRegimeDetector v2 (BULL/BEAR/NEUTRAL)")
            except Exception as e:
                logger.warning("CreditSpreadStrategy: ComboRegimeDetector init failed, falling back to hmm: %s", e)
                self.regime_mode = 'hmm'

        logger.info("CreditSpreadStrategy initialized (regime_mode=%s)", self.regime_mode)

    def evaluate_spread_opportunity(
        self,
        ticker: str,
        option_chain: pd.DataFrame,
        technical_signals: Dict,
        iv_data: Dict,
        current_price: float,
        as_of_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Evaluate potential credit spread opportunities.

        Args:
            ticker: Stock ticker symbol
            option_chain: Options chain data
            technical_signals: Technical analysis signals
            iv_data: IV rank and percentile data
            current_price: Current underlying price
            as_of_date: Optional date to use for DTE calculation (for backtesting).
                        Defaults to datetime.now() for live scanning.

        Returns:
            List of scored spread opportunities
        """
        opportunities: List[ScoredSpreadOpportunity] = []

        # Filter by DTE (use as_of_date for backtesting, datetime.now() for live)
        valid_expirations = self._filter_by_dte(option_chain, as_of_date=as_of_date)

        # Determine direction flags once, outside the expiration loop.
        # In combo mode, direction is decided SOLELY by ComboRegimeDetector output
        # (technical_signals['combo_regime']), mirroring backtester.py:
        #   _want_puts = (regime == 'bull')
        #   _want_calls = (regime == 'bear')
        # neutral → ICs only; no directional spreads opened.
        # In non-combo mode, fall back to _check_bullish/_check_bearish_conditions.
        combo_regime = technical_signals.get('combo_regime')
        if self.regime_mode == 'combo' and combo_regime is not None:
            want_bull_put = (combo_regime == 'bull')
            want_bear_call = (combo_regime == 'bear')
        else:
            want_bull_put = self._check_bullish_conditions(technical_signals, iv_data)
            want_bear_call = self._check_bearish_conditions(technical_signals, iv_data)

        for expiration in valid_expirations:
            exp_chain = option_chain[option_chain['expiration'] == expiration]

            # Evaluate bull put spreads
            if want_bull_put:
                bull_puts = self._find_bull_put_spreads(
                    ticker, exp_chain, current_price, expiration, iv_data,
                    as_of_date=as_of_date,
                )
                opportunities.extend(bull_puts)

            # Evaluate bear call spreads
            if want_bear_call:
                bear_calls = self._find_bear_call_spreads(
                    ticker, exp_chain, current_price, expiration, iv_data,
                    as_of_date=as_of_date,
                )
                opportunities.extend(bear_calls)

        # Find iron condor opportunities (both legs on same expiration)
        condor_config = self.strategy_params.get('iron_condor', {})
        if condor_config.get('enabled', True):
            # Pass regime from technical_signals (injected by caller for combo mode)
            current_regime = technical_signals.get('combo_regime')
            condors = self.find_iron_condors(
                ticker, option_chain, current_price, technical_signals, iv_data,
                as_of_date=as_of_date,
                current_regime=current_regime,
            )

            # Include condors alongside directional spreads — let scoring decide
            opportunities.extend(condors)

        # Score and rank opportunities
        scored_opportunities = self._score_opportunities(
            opportunities, technical_signals, iv_data
        )

        return scored_opportunities

    def _select_spread_width(self, iv_data: Dict) -> int:
        """Select spread width based on IV environment."""
        iv_rank = iv_data.get('iv_rank', 0)
        if iv_rank >= 50:
            return self.spread_width_high_iv
        elif iv_rank >= 25:
            return self.spread_width_low_iv
        else:
            return self.default_spread_width

    def _filter_by_dte(self, option_chain: pd.DataFrame, as_of_date: Optional[datetime] = None) -> List[datetime]:
        """Filter expirations by DTE range and return the single preferred expiration.

        Matches backtester behavior: _nearest_weekday_expiration selects ONE date
        closest to target_dte, preferring Fridays.  Returning multiple expirations
        caused the live scanner to open concurrent positions at different expirations
        on the same scan, over-trading relative to the backtester.

        Args:
            option_chain: Options chain DataFrame
            as_of_date: Date to calculate DTE from. Defaults to now() for live scanning.
                        Pass the simulated date when backtesting historical data.

        Returns:
            List with at most one expiration — the nearest-to-target, Friday-preferred
            date within [min_dte, max_dte].  Returns empty list if none qualify.
        """
        today = as_of_date or datetime.now(timezone.utc)
        if not hasattr(today, 'tzinfo') or today.tzinfo is None:
            today = today.replace(tzinfo=timezone.utc)
        target_dte = int(self.strategy_params.get('target_dte', 35))
        valid_expirations = []

        for exp in option_chain['expiration'].unique():
            exp_aware = exp if hasattr(exp, 'tzinfo') and exp.tzinfo else (exp.replace(tzinfo=timezone.utc) if isinstance(exp, datetime) else exp)
            dte = (exp_aware - today).days
            if self.strategy_params['min_dte'] <= dte <= self.strategy_params['max_dte']:
                valid_expirations.append(exp)

        if not valid_expirations:
            return []

        # Pick ONE expiration matching backtester's _nearest_weekday_expiration:
        # sort key: (distance from target_dte, not-Friday) so the closest Friday wins.
        def _pref(exp):
            exp_aware = exp if hasattr(exp, 'tzinfo') and exp.tzinfo else (exp.replace(tzinfo=timezone.utc) if isinstance(exp, datetime) else exp)
            dte = (exp_aware - today).days
            is_not_friday = 0 if exp.weekday() == 4 else 1  # prefer Friday (weekday=4)
            return (abs(dte - target_dte), is_not_friday)

        return [min(valid_expirations, key=_pref)]

    def _check_bullish_conditions(
        self,
        technical_signals: Dict,
        iv_data: Dict
    ) -> bool:
        """
        Check if conditions favor bull put spreads.

        Args:
            technical_signals: Technical analysis signals
            iv_data: IV rank/percentile data

        Returns:
            True if conditions are favorable
        """
        # IV gate — only applied when config explicitly sets a non-zero floor.
        # Default 0 = disabled, matching backtester iv_rank_min_entry=0 default.
        _min_iv_rank = self.strategy_params.get('min_iv_rank', 0)
        _min_iv_pct = self.strategy_params.get('min_iv_percentile', 0)
        if _min_iv_rank > 0 or _min_iv_pct > 0:
            iv_check = (
                iv_data.get('iv_rank', 0) >= _min_iv_rank or
                iv_data.get('iv_percentile', 0) >= _min_iv_pct
            )
            if not iv_check:
                return False

        # Technical conditions for bull put spreads
        tech_params = self.strategy_params['technical']

        bullish = True

        if tech_params['use_trend_filter']:
            # Price above moving averages or uptrend
            bullish = bullish and technical_signals.get('trend', '') in ['bullish', 'neutral']

        if tech_params['use_rsi_filter']:
            # RSI not overbought
            rsi = technical_signals.get('rsi', 50)
            bullish = bullish and rsi < tech_params['rsi_overbought']

        return bullish

    def _check_bearish_conditions(
        self,
        technical_signals: Dict,
        iv_data: Dict
    ) -> bool:
        """Check if conditions favor bear call spreads."""
        # IV gate — only applied when config explicitly sets a non-zero floor.
        # Default 0 = disabled, matching backtester iv_rank_min_entry=0 default.
        _min_iv_rank = self.strategy_params.get('min_iv_rank', 0)
        _min_iv_pct = self.strategy_params.get('min_iv_percentile', 0)
        if _min_iv_rank > 0 or _min_iv_pct > 0:
            iv_check = (
                iv_data.get('iv_rank', 0) >= _min_iv_rank or
                iv_data.get('iv_percentile', 0) >= _min_iv_pct
            )
            if not iv_check:
                return False

        tech_params = self.strategy_params['technical']

        bearish = True

        if tech_params['use_trend_filter']:
            bearish = bearish and technical_signals.get('trend', '') in ['bearish', 'neutral']

        if tech_params['use_rsi_filter']:
            rsi = technical_signals.get('rsi', 50)
            bearish = bearish and rsi > tech_params['rsi_oversold']

        return bearish

    def _find_bull_put_spreads(
        self,
        ticker: str,
        option_chain: pd.DataFrame,
        current_price: float,
        expiration: datetime,
        iv_data: Optional[Dict] = None,
        as_of_date: Optional[datetime] = None,
    ) -> List[Dict]:
        """Find bull put spread opportunities (thin wrapper)."""
        return self._find_spreads(ticker, option_chain, current_price, expiration, 'bull_put', iv_data=iv_data, as_of_date=as_of_date)

    def _find_bear_call_spreads(
        self,
        ticker: str,
        option_chain: pd.DataFrame,
        current_price: float,
        expiration: datetime,
        iv_data: Optional[Dict] = None,
        as_of_date: Optional[datetime] = None,
    ) -> List[Dict]:
        """Find bear call spread opportunities (thin wrapper)."""
        return self._find_spreads(ticker, option_chain, current_price, expiration, 'bear_call', iv_data=iv_data, as_of_date=as_of_date)

    def find_iron_condors(
        self,
        ticker: str,
        option_chain: pd.DataFrame,
        current_price: float,
        technical_signals: Dict,
        iv_data: Dict,
        as_of_date: Optional[datetime] = None,
        current_regime: Optional[str] = None,
    ) -> List[IronCondorOpportunity]:
        """Find iron condor opportunities (bull put + bear call on same expiration).

        Iron condors work in neutral/range-bound markets. We reuse the existing
        _find_spreads() logic for each wing, then pair them.

        Args:
            current_regime: Regime label (bull/bear/neutral) from ComboRegimeDetector.
                           If ic_neutral_regime_only=true in config, ICs are blocked
                           unless regime == neutral.
        """
        condor_config = self.strategy_params.get('iron_condor', {})

        # P1 Fix 5: IC-in-NEUTRAL-only regime gating
        if condor_config.get('ic_neutral_regime_only', False) and current_regime is not None:
            if current_regime != 'neutral':
                logger.info(
                    "find_iron_condors: IC blocked for %s — regime=%s (neutral required)",
                    ticker, current_regime,
                )
                return []

        rsi_min = condor_config.get('rsi_min', 30)
        rsi_max = condor_config.get('rsi_max', 70)
        min_combined_credit_pct = condor_config.get('min_combined_credit_pct', 25)
        condor_config.get('prefer_in_low_iv', True)
        condor_config.get('low_iv_threshold', 30)

        iv_rank = iv_data.get('iv_rank', 0)

        # IV gate — only applied when config explicitly sets a non-zero floor.
        # Default 0 = disabled, matching backtester iv_rank_min_entry=0 default.
        _min_iv_rank = self.strategy_params.get('min_iv_rank', 0)
        _min_iv_pct = self.strategy_params.get('min_iv_percentile', 0)
        if _min_iv_rank > 0 or _min_iv_pct > 0:
            iv_check = (
                iv_rank >= _min_iv_rank or
                iv_data.get('iv_percentile', 0) >= _min_iv_pct
            )
            if not iv_check:
                return []

        # Trend filter for condors:
        # - Low IV (< threshold): allow any trend (condors are the primary strategy)
        # - High IV: allow any trend too — the RSI filter (30-70) already ensures
        #   range-bound conditions, and high IV means rich premium on both sides.
        #   No need to also require neutral trend, which is too restrictive.
        # The RSI filter below is the real guard against directional markets.

        # RSI must be in range-bound zone
        rsi = technical_signals.get('rsi', 50)
        if not (rsi_min <= rsi <= rsi_max):
            return []

        condors: List[IronCondorOpportunity] = []
        valid_expirations = self._filter_by_dte(option_chain, as_of_date=as_of_date)
        # Dynamic spread width: wider in high-IV, narrower in low-IV — now matches backtester
        spread_width = self._select_spread_width(iv_data or {})

        for expiration in valid_expirations:
            exp_chain = option_chain[option_chain['expiration'] == expiration]

            # Find both wings — relaxed credit filter for individual legs
            bull_puts = self._find_spreads(ticker, exp_chain, current_price, expiration, 'bull_put', iv_data=iv_data, for_condor=True, as_of_date=as_of_date)
            bear_calls = self._find_spreads(ticker, exp_chain, current_price, expiration, 'bear_call', iv_data=iv_data, for_condor=True, as_of_date=as_of_date)

            if not bull_puts or not bear_calls:
                continue

            # Limit to top 3 of each wing by credit to avoid O(N^2) explosion
            bull_puts_top = sorted(bull_puts, key=lambda x: x['credit'], reverse=True)[:3]
            bear_calls_top = sorted(bear_calls, key=lambda x: x['credit'], reverse=True)[:3]

            # Pair best bull put with best bear call on this expiration
            for bp in bull_puts_top:
                for bc in bear_calls_top:
                    # Validate non-overlapping: put short strike < call short strike
                    if bp['short_strike'] >= bc['short_strike']:
                        continue

                    combined_credit = round(bp['credit'] + bc['credit'], 2)

                    # Max loss = both wings' width minus combined credit
                    # (worst case: both sides ITM simultaneously — matches backtester line 1505)
                    max_loss = round(2 * spread_width - combined_credit, 2)

                    if max_loss <= 0:
                        continue

                    # Check minimum combined credit — denominator is 2×width (total IC risk)
                    # matching backtester semantics: combined_credit / (2 * spread_width)
                    if (combined_credit / (2 * spread_width)) * 100 < min_combined_credit_pct:
                        continue

                    # Combined POP: probability that NEITHER wing is breached
                    # P(profit) ≈ 1 - P(put breach) - P(call breach)
                    put_breach_prob = (100 - bp['pop']) / 100
                    call_breach_prob = (100 - bc['pop']) / 100
                    combined_pop = round((1 - put_breach_prob - call_breach_prob) * 100, 2)
                    combined_pop = max(combined_pop, 0)

                    distance_to_put_short = abs(current_price - bp['short_strike'])
                    distance_to_call_short = abs(bc['short_strike'] - current_price)

                    condor = {
                        'ticker': ticker,
                        'type': 'iron_condor',
                        'expiration': expiration,
                        'dte': bp['dte'],
                        # Put side (reuse existing fields)
                        'short_strike': bp['short_strike'],
                        'long_strike': bp['long_strike'],
                        'put_credit': bp['credit'],
                        # Call side
                        'call_short_strike': bc['short_strike'],
                        'call_long_strike': bc['long_strike'],
                        'call_credit': bc['credit'],
                        # Combined
                        'credit': combined_credit,
                        'max_loss': max_loss,
                        'max_profit': combined_credit,
                        'profit_target': round(combined_credit * self.risk_params['profit_target'] / 100, 2),
                        'stop_loss': round(combined_credit * self.risk_params['stop_loss_multiplier'], 2),
                        'spread_width': spread_width,
                        'current_price': current_price,
                        'distance_to_put_short': distance_to_put_short,
                        'distance_to_call_short': distance_to_call_short,
                        'distance_to_short': min(distance_to_put_short, distance_to_call_short),
                        'short_delta': round((bp['short_delta'] + bc['short_delta']) / 2, 4),
                        'pop': combined_pop,
                        'risk_reward': round(combined_credit / max_loss, 2) if max_loss > 0 else 0,
                    }
                    condors.append(condor)

        return condors

    def _find_spreads(
        self,
        ticker: str,
        option_chain: pd.DataFrame,
        current_price: float,
        expiration: datetime,
        spread_type: str,
        iv_data: Optional[Dict] = None,
        for_condor: bool = False,
        as_of_date: Optional[datetime] = None,
    ) -> List[SpreadOpportunity]:
        """
        Find credit spread opportunities.

        Args:
            ticker: Stock ticker symbol
            option_chain: Options chain data
            current_price: Current underlying price
            expiration: Option expiration date
            spread_type: 'bull_put' or 'bear_call'
            iv_data: IV rank/percentile data for dynamic width selection
            for_condor: If True, relax min credit (combined credit checked later)
            as_of_date: Date to use for DTE calculation (for backtesting).
                        Defaults to datetime.now() for live scanning.

        Returns:
            List of spread opportunity dicts
        """
        spreads: List[SpreadOpportunity] = []

        if spread_type == 'bull_put':
            option_type = 'put'
            spread_label = 'bull_put_spread'
        else:
            option_type = 'call'
            spread_label = 'bear_call_spread'

        # Filter by option type
        legs = option_chain[option_chain['type'] == option_type].copy()

        if legs.empty:
            return spreads

        use_delta_selection = self.strategy_params.get('use_delta_selection', False)

        if use_delta_selection:
            # Find single strike closest to target delta (e.g. 12-delta)
            from shared.strike_selector import select_delta_strike
            target_delta = float(self.strategy_params.get('target_delta', 0.12))
            ot_char = 'P' if spread_type == 'bull_put' else 'C'
            chain_rows = legs[['strike', 'delta']].to_dict('records')
            best_strike = select_delta_strike(chain_rows, ot_char, target_delta=target_delta)
            if best_strike is None:
                return spreads
            short_candidates = legs[legs['strike'] == best_strike]
        else:
            # Legacy delta range filter
            target_delta_min = self.strategy_params['min_delta']
            target_delta_max = self.strategy_params['max_delta']
            if spread_type == 'bull_put':
                short_candidates = legs[
                    (legs['delta'] >= -target_delta_max) &
                    (legs['delta'] <= -target_delta_min)
                ]
            else:
                short_candidates = legs[
                    (legs['delta'] >= target_delta_min) &
                    (legs['delta'] <= target_delta_max)
                ]

        # Dynamic spread width: wider in high-IV, narrower in low-IV — now matches backtester
        spread_width = self._select_spread_width(iv_data or {})

        for _, short_leg in short_candidates.iterrows():
            short_strike = short_leg['strike']

            # Long strike direction differs
            if spread_type == 'bull_put':
                long_strike = short_strike - spread_width
            else:
                long_strike = short_strike + spread_width

            # Find corresponding long leg
            long_leg = legs[legs['strike'] == long_strike]

            if long_leg.empty:
                continue

            long_leg = long_leg.iloc[0]

            # Calculate credit and risk
            credit = short_leg['bid'] - long_leg['ask']

            # Apply entry slippage — matches backtester _find_real_spread:
            #   slippage = prices.get("slippage", self.slippage); credit -= slippage
            # Live reads from backtest.slippage (same config key as backtester).
            _slippage = float(self.config.get('backtest', {}).get('slippage', 0.0))
            credit -= _slippage

            # Reject crossed markets (negative credit after slippage)
            if credit <= 0:
                continue

            max_loss = spread_width - credit

            # Reject spreads where max_loss is non-positive (credit exceeds width)
            if max_loss <= 0:
                continue

            # Check minimum credit requirement
            # For condor legs, use a low floor (combined credit checked later)
            if for_condor:
                if credit < 0.01:
                    continue
            else:
                min_credit = spread_width * (self.risk_params['min_credit_pct'] / 100)
                if credit < min_credit:
                    continue

            exp_aware = expiration if expiration.tzinfo else expiration.replace(tzinfo=timezone.utc)
            ref_date = as_of_date or datetime.now(timezone.utc)
            if not hasattr(ref_date, 'tzinfo') or ref_date.tzinfo is None:
                ref_date = ref_date.replace(tzinfo=timezone.utc)
            dte = (exp_aware - ref_date).days

            # Distance to short strike (always positive — how far price
            # must move adversely to reach the short strike)
            distance_to_short = abs(current_price - short_strike)

            spread = {
                'ticker': ticker,
                'type': spread_label,
                'expiration': expiration,
                'dte': dte,
                'short_strike': short_strike,
                'long_strike': long_strike,
                'short_delta': abs(short_leg['delta']),
                'credit': round(credit, 2),
                'max_loss': round(max_loss, 2),
                'max_profit': round(credit, 2),
                'profit_target': round(credit * self.risk_params['profit_target'] / 100, 2),
                'stop_loss': round(credit * self.risk_params['stop_loss_multiplier'], 2),
                'spread_width': spread_width,
                'current_price': current_price,
                'distance_to_short': distance_to_short,
                'pop': self._calculate_pop(short_leg['delta']),
                'risk_reward': round(credit / max_loss, 2) if max_loss > 0 else 0,
            }

            spreads.append(spread)

        return spreads

    def _calculate_pop(self, delta: float) -> float:
        """
        Calculate probability of profit (approximation).
        POP ≈ 1 - |delta|
        """
        return round((1 - abs(delta)) * 100, 2)

    def _score_opportunities(
        self,
        opportunities: List[Dict],
        technical_signals: Dict,
        iv_data: Dict
    ) -> List[Dict]:
        """
        Score and rank spread opportunities.

        Scoring criteria:
        - Higher credit (better)
        - Better risk/reward ratio
        - Higher probability of profit
        - Technical alignment
        - IV rank/percentile
        """
        w = SCORING_WEIGHTS

        for opp in opportunities:
            score = 0

            # Credit score (0-credit_max points)
            # Higher credit as % of spread width is better
            credit_pct = (opp['credit'] / opp['spread_width']) * 100
            score += min(credit_pct * w["credit_scale"], w["credit_max"])

            # Risk/reward score (0-risk_reward_max points)
            # Better than 1:3 risk/reward gets full points
            rr_score = min(opp['risk_reward'] * w["risk_reward_scale"], w["risk_reward_max"])
            score += rr_score

            # POP score (0-pop_max points)
            # POP > pop_baseline% gets full points
            pop_score = min((opp['pop'] / w["pop_baseline"]) * w["pop_max"], w["pop_max"])
            score += pop_score

            # Technical alignment (0-technical_max points)
            tech_score = 0
            if opp['type'] == 'iron_condor':
                # Iron condors thrive in neutral markets
                if technical_signals.get('trend') == 'neutral':
                    tech_score += w["condor_tech_neutral"]
                if technical_signals.get('regime') == 'mean_reverting':
                    tech_score += w["condor_tech_regime"]
                rsi = technical_signals.get('rsi', 50)
                if 40 <= rsi <= 60:
                    tech_score += w["condor_tech_rsi_range"]
            elif opp['type'] == 'bull_put_spread':
                if technical_signals.get('trend') == 'bullish':
                    tech_score += w["tech_strong_signal"]
                elif technical_signals.get('trend') == 'neutral':
                    tech_score += w["tech_neutral_signal"]
            else:  # bear_call_spread
                if technical_signals.get('trend') == 'bearish':
                    tech_score += w["tech_strong_signal"]
                elif technical_signals.get('trend') == 'neutral':
                    tech_score += w["tech_neutral_signal"]

            # Support/resistance alignment (not applicable to condors)
            if opp['type'] != 'iron_condor':
                if technical_signals.get('near_support') and opp['type'] == 'bull_put_spread':
                    tech_score += w["tech_support_resistance"]
                if technical_signals.get('near_resistance') and opp['type'] == 'bear_call_spread':
                    tech_score += w["tech_support_resistance"]

            score += min(tech_score, w["technical_max"])

            # IV score (0-iv_max points)
            iv_rank = iv_data.get('iv_rank', 0)
            iv_score = min(iv_rank / w["iv_divisor"], w["iv_max"])
            score += iv_score

            opp['score'] = round(score, 2)

        # Sort by score (highest first)
        opportunities.sort(key=lambda x: x['score'], reverse=True)

        return opportunities

