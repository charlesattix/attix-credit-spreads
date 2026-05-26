"""
Options Analysis Module
Handles options chain data, Greeks calculation, and IV analysis.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional

import numpy as np
import pandas as pd

from shared.constants import DEFAULT_RISK_FREE_RATE
from shared.indicators import calculate_iv_rank as _shared_iv_rank
from shared.provider_protocol import DataProvider

logger = logging.getLogger(__name__)


class OptionsAnalyzer:
    """
    Analyze options chains and calculate metrics.
    """

    def __init__(self, config: Dict, data_cache=None):
        """
        Initialize options analyzer.

        Args:
            config: Configuration dictionary
            data_cache: Optional DataCache instance for shared data retrieval.
        """
        self.config = config
        self.data_cache = data_cache
        self.tradier: Optional[DataProvider] = None
        self.polygon: Optional[DataProvider] = None

        # Initialize data provider
        data_config = config.get('data', {})
        provider = data_config.get('provider', '')

        if provider == 'tradier':
            tradier_config = data_config.get('tradier', {})
            api_key = tradier_config.get('api_key', '')
            if api_key and api_key != 'YOUR_TRADIER_API_KEY':
                from strategy.tradier_provider import TradierProvider
                sandbox = tradier_config.get('sandbox', True)
                self.tradier = TradierProvider(api_key, sandbox=sandbox)
                logger.info("Using Tradier for real-time data")
        elif provider == 'polygon':
            polygon_config = data_config.get('polygon', {})
            api_key = polygon_config.get('api_key', '') or os.environ.get('POLYGON_API_KEY', '')
            if api_key:
                from strategy.polygon_provider import PolygonProvider
                self.polygon = PolygonProvider(api_key)
                logger.info("Using Polygon for real-time data")

        logger.info("OptionsAnalyzer initialized")

    def get_options_chain(self, ticker: str) -> pd.DataFrame:
        """
        Retrieve options chain for ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            DataFrame with options chain data
        """
        if self.tradier:
            return self._get_chain_from_provider(self.tradier, "Tradier", ticker)
        if self.polygon:
            return self._get_chain_from_provider(self.polygon, "Polygon", ticker)
        raise RuntimeError(
            f"No options provider configured for {ticker}. "
            f"Set data.provider to 'tradier' or 'polygon' in config.yaml."
        )

    def _get_chain_from_provider(self, provider: DataProvider, provider_name: str, ticker: str) -> pd.DataFrame:
        """Get options chain from a provider (Tradier or Polygon).

        Args:
            provider: Provider instance satisfying the :class:`DataProvider` protocol.
            provider_name: Human-readable name used in log messages.
            ticker: Stock ticker symbol.

        Returns:
            DataFrame with options chain data. Empty if the provider returns
            no usable data; the caller is expected to handle that.
        """
        try:
            min_dte = max(self.config['strategy'].get('min_dte', 30) - 5, 0)
            max_dte = self.config['strategy'].get('max_dte', 45) + 5
            chain = provider.get_full_chain(ticker, min_dte=min_dte, max_dte=max_dte)

            if chain.empty:
                logger.warning(f"{provider_name} returned no data for {ticker}")
                return chain

            if 'bid' in chain.columns and 'ask' in chain.columns:
                valid_pricing = ((chain['bid'] > 0.05) & (chain['ask'] > 0.05)).sum()
                if valid_pricing < len(chain) * 0.1:
                    logger.warning(
                        f"{provider_name} returned {len(chain)} options but insufficient "
                        f"pricing data for {ticker} (only {valid_pricing} with bid/ask > $0.05)"
                    )

            logger.info(f"Retrieved {len(chain)} options for {ticker} via {provider_name} (real-time)")
            return chain
        except Exception as e:
            logger.error(f"{provider_name} error for {ticker}: {e}", exc_info=True)
            return pd.DataFrame()

    def _clean_options_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and standardize options data.
        """
        # Rename columns to standard names
        column_mapping = {
            'lastTradeDate': 'last_trade_date',
            'impliedVolatility': 'iv',
            'inTheMoney': 'itm',
            'contractSymbol': 'contract_symbol',
            'lastPrice': 'last',
        }

        df = df.rename(columns=column_mapping)

        # Ensure required columns exist
        required_cols = ['strike', 'bid', 'ask', 'type', 'expiration']
        for col in required_cols:
            if col not in df.columns:
                logger.error(f"Missing required column: {col}")
                return pd.DataFrame()

        # Calculate mid price
        df['mid'] = (df['bid'] + df['ask']) / 2

        # Ensure delta exists (calculate if missing)
        if 'delta' not in df.columns:
            df['delta'] = self._estimate_delta(df)

        # Remove rows with zero bid/ask
        df = df[(df['bid'] > 0) & (df['ask'] > 0)].copy()

        return df

    def _estimate_delta(self, df: pd.DataFrame, current_price: float = None) -> pd.Series:
        """
        Estimate delta using vectorized Black-Scholes approximation.
        """
        from scipy.stats import norm

        logger.warning("Delta not available in data, using estimates")

        spot = current_price if current_price is not None else df['strike'].median()
        now = datetime.now(timezone.utc)
        risk_free = DEFAULT_RISK_FREE_RATE

        K = df['strike'].values.astype(float)
        exp_col = pd.to_datetime(df['expiration']).dt.tz_localize('UTC') if pd.to_datetime(df['expiration']).dt.tz is None else pd.to_datetime(df['expiration'])
        T = np.maximum((exp_col - now).dt.days.values / 365.0, 1/365)
        iv = df['iv'].fillna(0.20).values.astype(float)
        iv = np.where(iv <= 0, 0.20, iv)

        d1 = (np.log(spot / K) + (risk_free + 0.5 * iv**2) * T) / (iv * np.sqrt(T))
        call_delta = norm.cdf(d1)
        put_delta = call_delta - 1

        is_call = (df['type'] == 'call').values
        delta = np.where(is_call, call_delta, put_delta)

        return pd.Series(np.round(delta, 4), index=df.index)

    def calculate_iv_rank(self, ticker: str, current_iv: float) -> Dict:
        """
        Calculate IV rank and IV percentile.

        IV Rank = (Current IV - 52-week Low IV) / (52-week High IV - 52-week Low IV)

        Args:
            ticker: Stock ticker
            current_iv: Current implied volatility

        Returns:
            Dictionary with IV metrics
        """
        if self.data_cache is None:
            raise RuntimeError("DataCache is required for calculate_iv_rank()")
        try:
            hist = self.data_cache.get_history(ticker, period='1y')

            if hist.empty:
                logger.warning(f"No historical data for {ticker}")
                return {'iv_rank': 0, 'iv_percentile': 0, 'current_iv': current_iv}

            # Calculate historical volatility as proxy for IV range
            returns = hist['Close'].pct_change().dropna()
            hv_values = returns.rolling(window=20).std() * np.sqrt(252) * 100
            hv_values = hv_values.dropna()

            if len(hv_values) == 0:
                return {'iv_rank': 0, 'iv_percentile': 0, 'current_iv': current_iv}

            # Delegate to shared implementation
            shared_result = _shared_iv_rank(hv_values, current_iv)

            return {
                'iv_rank': shared_result['iv_rank'],
                'iv_percentile': shared_result['iv_percentile'],
                'current_iv': round(current_iv, 2),
                'iv_min_52w': shared_result['iv_min'],
                'iv_max_52w': shared_result['iv_max'],
            }

        except Exception as e:
            logger.error(f"Error calculating IV rank for {ticker}: {e}", exc_info=True)
            return {'iv_rank': 0, 'iv_percentile': 0, 'current_iv': current_iv}

    def get_current_iv(self, options_chain: pd.DataFrame) -> float:
        """
        Get current implied volatility from options chain.

        Uses ATM options for most accurate reading.
        """
        if options_chain.empty:
            return 0.0

        # Get ATM options (closest to current price)
        if 'iv' not in options_chain.columns:
            return 0.0

        # Average IV of near-dated ATM options
        iv_values = options_chain['iv'].dropna()

        if len(iv_values) == 0:
            return 0.0

        current_iv = iv_values.median() * 100  # Convert to percentage

        return round(current_iv, 2)
