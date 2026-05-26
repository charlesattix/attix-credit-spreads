"""
CBOE CSV Data Provider - reads from cached CSV files instead of Athena.

Implements same interface as CBOEDataProvider but reads from local cache:
  data/cboe_complete/spx/0dte/YYYY-MM.csv.csv.gz

This is 100% Rule Zero compliant - same CBOE data, just pre-cached.
"""
from __future__ import annotations

import gzip
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class CBOECSVProvider:
    """
    Data provider that reads CBOE data from cached CSV files.
    Drop-in replacement for CBOEDataProvider.
    """
    
    def __init__(self, cache_dir: str = None):
        """Initialize with cache directory."""
        if cache_dir is None:
            # Default to data/cboe_complete/spx/0dte/
            root = Path(__file__).parent.parent
            cache_dir = root / "data" / "cboe_complete" / "spx" / "0dte"
        
        self.cache_dir = Path(cache_dir)
        if not self.cache_dir.exists():
            raise ValueError(f"Cache directory not found: {self.cache_dir}")
        
        logger.info(f"Initialized CBOE CSV provider with cache: {self.cache_dir}")
        
        # Pre-load all data into memory (faster for backtesting)
        self._cache = {}
        self._load_cache()
    
    def _load_cache(self):
        """Load all CSV files into memory."""
        logger.info("Loading cached CBOE data...")
        
        csv_files = sorted(self.cache_dir.glob("*.csv.gz"))
        logger.info(f"Found {len(csv_files)} monthly cache files")
        
        for csv_file in csv_files:
            try:
                # Read compressed CSV
                with gzip.open(csv_file, 'rt') as f:
                    df = pd.read_csv(f)
                
                # Parse timestamp
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df['date'] = df['timestamp'].dt.date
                
                # Store by month
                month_key = csv_file.stem.replace('.csv', '')  # e.g., "2023-02"
                self._cache[month_key] = df
                
                logger.info(f"  Loaded {month_key}: {len(df):,} rows")
            except Exception as e:
                logger.warning(f"  Failed to load {csv_file.name}: {e}")
        
        logger.info(f"Cache loaded: {sum(len(df) for df in self._cache.values()):,} total rows")
    
    def _get_month_data(self, date: str) -> Optional[pd.DataFrame]:
        """Get data for a specific month."""
        # Extract YYYY-MM from date string
        month_key = date[:7]  # "2023-01-15" -> "2023-01"
        return self._cache.get(month_key)
    
    def get_underlying_price(self, ticker: str, date: str) -> Optional[float]:
        """Get underlying SPX price for a date."""
        df = self._get_month_data(date)
        if df is None:
            return None
        
        # Filter to this date
        date_obj = pd.to_datetime(date).date()
        date_data = df[df['date'] == date_obj]
        if date_data.empty:
            return None
        
        # Use CBOE's underlying_price column directly (verified 100% valid)
        underlying_prices = date_data['underlying_price'].dropna()
        
        if not underlying_prices.empty:
            # Get most recent non-zero price
            valid_prices = underlying_prices[underlying_prices > 0]
            if not valid_prices.empty:
                underlying = float(valid_prices.iloc[-1])
                logger.debug(f"Got underlying for {date}: ${underlying:.2f}")
                return underlying
        
        logger.warning(f"Could not get underlying price for {date}")
        return None
    
    def get_expirations(
        self,
        ticker: str,
        as_of_date: datetime | str,
        min_dte: int = 0,
        max_dte: int = 0,
    ) -> List[str]:
        """Get available expirations."""
        if isinstance(as_of_date, datetime):
            date_str = as_of_date.strftime("%Y-%m-%d")
        else:
            date_str = as_of_date
        
        df = self._get_month_data(date_str)
        if df is None:
            return []
        
        # Filter to this date
        date_obj = pd.to_datetime(date_str).date()
        date_data = df[df['date'] == date_obj]
        
        if date_data.empty:
            return []
        
        # Get unique expirations
        expirations = date_data['expiration'].unique()
        
        # Filter by DTE
        result = []
        for exp_str in expirations:
            exp_date = pd.to_datetime(exp_str).date()
            dte = (exp_date - date_obj).days
            
            if min_dte <= dte <= max_dte:
                result.append(exp_str)
        
        return sorted(result)
    
    def get_available_strikes(
        self,
        ticker: str,
        expiration: str,
        as_of_date: str,
        option_type: str,
    ) -> List[float]:
        """Get available strikes for an expiration."""
        df = self._get_month_data(as_of_date)
        if df is None:
            return []
        
        date_obj = pd.to_datetime(as_of_date).date()
        date_data = df[
            (df['date'] == date_obj) &
            (df['expiration'] == expiration) &
            (df['option_type'] == option_type)
        ]
        
        if date_data.empty:
            return []
        
        return sorted(date_data['strike'].unique())
    
    def get_greeks(
        self,
        ticker: str,
        strike: float,
        option_type: str,
        expiration: str,
        date: str,
    ) -> Optional[Dict]:
        """Get Greeks for a specific option."""
        df = self._get_month_data(date)
        if df is None:
            return None
        
        date_obj = pd.to_datetime(date).date()
        option_data = df[
            (df['date'] == date_obj) &
            (df['expiration'] == expiration) &
            (df['strike'] == strike) &
            (df['option_type'] == option_type)
        ]
        
        if option_data.empty:
            return None
        
        # Take first row (9:45 AM entry time, or closest available)
        row = option_data.iloc[0]
        
        return {
            'delta': float(row['delta']) if pd.notna(row['delta']) else None,
            'gamma': float(row['gamma']) if pd.notna(row['gamma']) else None,
            'theta': float(row['theta']) if pd.notna(row['theta']) else None,
            'vega': float(row['vega']) if pd.notna(row['vega']) else None,
            'rho': float(row['rho']) if pd.notna(row['rho']) else None,
            'iv': float(row['iv']) if pd.notna(row['iv']) else None,
        }
    
    def get_spread_prices(
        self,
        ticker: str,
        expiration: str,
        short_strike: float,
        long_strike: float,
        option_type: str,
        date: str,
    ) -> Optional[Dict]:
        """Get spread prices (short sell, long buy)."""
        df = self._get_month_data(date)
        if df is None:
            return None
        
        date_obj = pd.to_datetime(date).date()
        
        # Get short leg data
        short_data = df[
            (df['date'] == date_obj) &
            (df['expiration'] == expiration) &
            (df['strike'] == short_strike) &
            (df['option_type'] == option_type)
        ]
        
        # Get long leg data
        long_data = df[
            (df['date'] == date_obj) &
            (df['expiration'] == expiration) &
            (df['strike'] == long_strike) &
            (df['option_type'] == option_type)
        ]
        
        if short_data.empty or long_data.empty:
            return None
        
        short_row = short_data.iloc[0]
        long_row = long_data.iloc[0]
        
        # Short leg: we SELL at bid
        short_bid = float(short_row['bid_close']) if pd.notna(short_row['bid_close']) and short_row['bid_close'] > 0 else None
        short_ask = float(short_row['ask_close']) if pd.notna(short_row['ask_close']) and short_row['ask_close'] > 0 else None
        
        # Long leg: we BUY at ask
        long_bid = float(long_row['bid_close']) if pd.notna(long_row['bid_close']) and long_row['bid_close'] > 0 else None
        long_ask = float(long_row['ask_close']) if pd.notna(long_row['ask_close']) and long_row['ask_close'] > 0 else None
        
        # Handle zero bid (deep OTM): use ask if available, else minimum tick
        if short_bid is None or short_bid == 0:
            if short_ask and short_ask > 0:
                short_bid = short_ask * 0.8  # Conservative: 80% of ask for illiquid options
                logger.debug(f"Zero bid for short {option_type} {short_strike}, using 80% ask: ${short_bid:.2f}")
            else:
                logger.warning(f"No valid price for short {option_type} {short_strike} on {date}")
                return None
        
        if long_ask is None or long_ask == 0:
            if long_bid and long_bid > 0:
                long_ask = long_bid * 1.25  # Conservative: 125% of bid for illiquid options
                logger.debug(f"Zero ask for long {option_type} {long_strike}, using 125% bid: ${long_ask:.2f}")
            else:
                logger.warning(f"No valid price for long {option_type} {long_strike} on {date}")
                return None
        
        # Net credit = sell short - buy long
        spread_value = short_bid - long_ask
        
        return {
            'short_close': short_bid,
            'long_close': long_ask,
            'spread_value': spread_value,
        }
    
    def get_exit_prices(
        self,
        ticker: str,
        expiration: str,
        short_strike: float,
        long_strike: float,
        option_type: str,
        date: str,
    ) -> Optional[Dict]:
        """Get exit prices (to close position)."""
        df = self._get_month_data(date)
        if df is None:
            return None
        
        date_obj = pd.to_datetime(date).date()
        
        # Get short leg data
        short_data = df[
            (df['date'] == date_obj) &
            (df['expiration'] == expiration) &
            (df['strike'] == short_strike) &
            (df['option_type'] == option_type)
        ]
        
        # Get long leg data
        long_data = df[
            (df['date'] == date_obj) &
            (df['expiration'] == expiration) &
            (df['strike'] == long_strike) &
            (df['option_type'] == option_type)
        ]
        
        if short_data.empty or long_data.empty:
            return None
        
        # For exit: take last timestamp of the day (3:00 PM or expiration)
        short_row = short_data.iloc[-1]
        long_row = long_data.iloc[-1]
        
        # To close: BUY back short at ask, SELL long at bid
        short_bid = float(short_row['bid_close']) if pd.notna(short_row['bid_close']) and short_row['bid_close'] > 0 else None
        short_ask = float(short_row['ask_close']) if pd.notna(short_row['ask_close']) and short_row['ask_close'] > 0 else None
        long_bid = float(long_row['bid_close']) if pd.notna(long_row['bid_close']) and long_row['bid_close'] > 0 else None
        long_ask = float(long_row['ask_close']) if pd.notna(long_row['ask_close']) and long_row['ask_close'] > 0 else None
        
        # Handle zero prices (deep OTM at expiration)
        if short_ask is None or short_ask == 0:
            if short_bid and short_bid > 0:
                short_ask = short_bid * 1.25  # Exit slippage for illiquid
            else:
                # Assume worthless at expiration
                short_ask = 0.05
        
        if long_bid is None or long_bid == 0:
            if long_ask and long_ask > 0:
                long_bid = long_ask * 0.8  # Exit slippage for illiquid
            else:
                # Assume worthless at expiration
                long_bid = 0.05
        
        # Cost to close = buy short - sell long
        spread_value = short_ask - long_bid
        
        return {
            'short_close': short_ask,
            'long_close': long_bid,
            'spread_value': spread_value,
        }
