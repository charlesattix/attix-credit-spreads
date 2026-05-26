"""
Technical Analysis Module
Provides technical indicators and signals for credit spread strategy.
"""

import logging
from typing import Dict

import numpy as np
import pandas as pd

from shared.indicators import calculate_rsi

try:
    import talib
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False

logger = logging.getLogger(__name__)


class TechnicalAnalyzer:
    """
    Technical analysis for determining market conditions.
    """

    def __init__(self, config: Dict):
        """
        Initialize technical analyzer.

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.tech_params = config.get('strategy', {}).get('technical', {})

        logger.info("TechnicalAnalyzer initialized")

    @staticmethod
    def _get_close_series(price_data: pd.DataFrame) -> pd.Series:
        """Extract Close as a flat Series, handling yfinance multi-level columns."""
        close = price_data['Close']
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return close

    def analyze(self, ticker: str, price_data: pd.DataFrame) -> Dict:
        """
        Perform technical analysis on price data.

        Args:
            ticker: Stock ticker
            price_data: DataFrame with OHLCV data

        Returns:
            Dictionary with technical signals
        """
        if price_data.empty or len(price_data) < 50:
            logger.warning(f"Insufficient data for {ticker}")
            return {}

        signals = {
            'ticker': ticker,
            'current_price': float(self._get_close_series(price_data).iloc[-1]),
        }

        # Calculate moving averages
        if self.tech_params.get('use_trend_filter', True):
            trend_signals = self._analyze_trend(price_data)
            signals.update(trend_signals)

        # Calculate RSI
        if self.tech_params.get('use_rsi_filter', True):
            rsi_signals = self._analyze_rsi(price_data)
            signals.update(rsi_signals)

        # Support and resistance
        if self.tech_params.get('use_support_resistance', True):
            sr_signals = self._analyze_support_resistance(price_data)
            signals.update(sr_signals)

        return signals

    def _analyze_trend(self, price_data: pd.DataFrame) -> Dict:
        """
        Analyze trend using moving averages.

        Returns:
            Dictionary with trend signals
        """
        fast_period = self.tech_params['fast_ma']
        slow_period = self.tech_params['slow_ma']

        close_series = self._get_close_series(price_data)

        # Calculate MAs
        ma_fast_series = close_series.rolling(window=fast_period).mean()
        ma_slow_series = close_series.rolling(window=slow_period).mean()

        current_price = float(close_series.iloc[-1])
        ma_fast = float(ma_fast_series.iloc[-1])
        ma_slow = float(ma_slow_series.iloc[-1])

        # Determine trend
        if current_price > ma_fast > ma_slow:
            trend = 'bullish'
        elif current_price < ma_fast < ma_slow:
            trend = 'bearish'
        else:
            trend = 'neutral'

        return {
            'trend': trend,
            'ma_fast': round(ma_fast, 2),
            'ma_slow': round(ma_slow, 2),
            'price_above_ma_fast': current_price > ma_fast,
            'price_above_ma_slow': current_price > ma_slow,
        }

    def _analyze_rsi(self, price_data: pd.DataFrame) -> Dict:
        """
        Calculate RSI indicator.

        Returns:
            Dictionary with RSI signals
        """
        rsi_period = self.tech_params['rsi_period']
        close_series = self._get_close_series(price_data)

        # Calculate RSI
        if HAS_TALIB:
            rsi = pd.Series(talib.RSI(close_series.values, timeperiod=rsi_period), index=price_data.index)
        else:
            rsi = calculate_rsi(close_series, period=rsi_period)
        current_rsi = float(rsi.iloc[-1])

        # RSI conditions
        oversold = current_rsi < self.tech_params['rsi_oversold']
        overbought = current_rsi > self.tech_params['rsi_overbought']

        return {
            'rsi': round(current_rsi, 2),
            'rsi_oversold': oversold,
            'rsi_overbought': overbought,
        }

    def _analyze_support_resistance(self, price_data: pd.DataFrame) -> Dict:
        """
        Identify support and resistance levels.

        Uses pivot points and recent highs/lows.

        Returns:
            Dictionary with support/resistance levels
        """
        current_price = float(self._get_close_series(price_data).iloc[-1])

        # Support: recent lows
        support_levels = self._find_support_levels(price_data)

        # Resistance: recent highs
        resistance_levels = self._find_resistance_levels(price_data)

        # Check if near support or resistance
        PROXIMITY_THRESHOLD = 0.02  # 2% of current price
        near_support = False
        nearest_support = None
        for level in support_levels:
            if abs(current_price - level) / current_price < PROXIMITY_THRESHOLD and level < current_price:
                near_support = True
                nearest_support = level
                break

        near_resistance = False
        nearest_resistance = None
        for level in resistance_levels:
            if abs(current_price - level) / current_price < PROXIMITY_THRESHOLD and level > current_price:
                near_resistance = True
                nearest_resistance = level
                break

        return {
            'support_levels': support_levels[:3],  # Top 3
            'resistance_levels': resistance_levels[:3],
            'near_support': near_support,
            'near_resistance': near_resistance,
            'nearest_support': nearest_support,
            'nearest_resistance': nearest_resistance,
        }

    def _find_support_levels(self, price_data: pd.DataFrame, window: int = 5) -> list:
        """
        Find support levels using local minima.
        """
        low_col = price_data['Low']
        if isinstance(low_col, pd.DataFrame):
            low_col = low_col.iloc[:, 0]
        lows = np.asarray(low_col).flatten()
        support = []

        for i in range(window, len(lows) - window):
            if float(lows[i]) == float(np.min(lows[i - window:i + window + 1])):
                support.append(float(lows[i]))

        # Remove duplicates (within 1% of each other)
        support = self._consolidate_levels(support)
        support.sort(reverse=True)  # Highest first

        return support

    def _find_resistance_levels(self, price_data: pd.DataFrame, window: int = 5) -> list:
        """
        Find resistance levels using local maxima.
        """
        high_col = price_data['High']
        if isinstance(high_col, pd.DataFrame):
            high_col = high_col.iloc[:, 0]
        highs = np.asarray(high_col).flatten()
        resistance = []

        for i in range(window, len(highs) - window):
            if float(highs[i]) == float(np.max(highs[i - window:i + window + 1])):
                resistance.append(float(highs[i]))

        resistance = self._consolidate_levels(resistance)
        resistance.sort()  # Lowest first

        return resistance

    def _consolidate_levels(self, levels: list, threshold: float = 0.01) -> list:
        """
        Consolidate price levels that are within threshold% of each other.
        """
        if not levels:
            return []

        levels = sorted(levels)
        consolidated = [levels[0]]

        for level in levels[1:]:
            if consolidated[-1] != 0 and abs(level - consolidated[-1]) / abs(consolidated[-1]) > threshold:
                consolidated.append(level)
            elif consolidated[-1] == 0:
                consolidated.append(level)

        return consolidated
