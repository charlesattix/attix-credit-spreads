"""Tests for OptionsAnalyzer."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from strategy.options_analyzer import OptionsAnalyzer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Build a minimal config for OptionsAnalyzer with no provider configured."""
    config = {
        'strategy': {
            'min_dte': 30,
            'max_dte': 45,
        },
        'data': {
            'provider': '',
        },
    }
    config.update(overrides)
    return config


def _make_chain_df(n_strikes=5, exp_date=None):
    """Create a synthetic options chain DataFrame matching provider output."""
    if exp_date is None:
        exp_date = datetime.now() + timedelta(days=35)
    strikes = np.arange(100, 100 + n_strikes * 5, 5, dtype=float)
    rows = []
    for s in strikes:
        for opt_type in ['call', 'put']:
            rows.append({
                'strike': s,
                'bid': 2.0,
                'ask': 2.5,
                'type': opt_type,
                'expiration': exp_date,
                'iv': 0.25,
                'delta': 0.3 if opt_type == 'call' else -0.3,
                'volume': 100,
            })
    return pd.DataFrame(rows)


def _analyzer_with_polygon(provider_mock):
    """Build an analyzer with a mocked polygon provider attached."""
    analyzer = OptionsAnalyzer(_make_config())
    analyzer.polygon = provider_mock
    return analyzer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetOptionsChain:

    def test_returns_dataframe_on_success(self):
        """get_options_chain returns the provider's chain when configured."""
        provider = MagicMock()
        provider.get_full_chain.return_value = _make_chain_df()
        analyzer = _analyzer_with_polygon(provider)

        result = analyzer.get_options_chain('SPY')

        assert isinstance(result, pd.DataFrame)
        assert len(result) > 0
        provider.get_full_chain.assert_called_once()

    def test_returns_empty_when_provider_returns_empty(self):
        """An empty provider response is returned as-is (caller handles)."""
        provider = MagicMock()
        provider.get_full_chain.return_value = pd.DataFrame()
        analyzer = _analyzer_with_polygon(provider)

        result = analyzer.get_options_chain('NOOPT')

        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_returns_empty_on_provider_exception(self):
        """Provider exceptions are caught and surface as an empty DataFrame."""
        provider = MagicMock()
        provider.get_full_chain.side_effect = Exception("network error")
        analyzer = _analyzer_with_polygon(provider)

        result = analyzer.get_options_chain('ERR')

        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_raises_when_no_provider_configured(self):
        """Without Tradier or Polygon, get_options_chain raises RuntimeError."""
        analyzer = OptionsAnalyzer(_make_config())

        with pytest.raises(RuntimeError, match="No options provider configured"):
            analyzer.get_options_chain('SPY')


class TestCleanOptionsData:

    def test_renames_columns(self):
        """_clean_options_data should rename provider columns to standard names."""
        analyzer = OptionsAnalyzer(_make_config())
        exp_date = datetime.now() + timedelta(days=35)
        df = pd.DataFrame({
            'strike': [100.0],
            'bid': [2.0],
            'ask': [2.5],
            'type': ['call'],
            'expiration': [exp_date],
            'impliedVolatility': [0.25],
        })
        result = analyzer._clean_options_data(df)
        assert 'iv' in result.columns

    def test_removes_zero_bid_ask(self):
        """Rows with zero bid or ask should be filtered out."""
        analyzer = OptionsAnalyzer(_make_config())
        exp_date = datetime.now() + timedelta(days=35)
        df = pd.DataFrame({
            'strike': [100.0, 105.0],
            'bid': [0.0, 2.0],
            'ask': [2.5, 2.5],
            'type': ['call', 'call'],
            'expiration': [exp_date, exp_date],
            'iv': [0.25, 0.30],
        })
        result = analyzer._clean_options_data(df)
        assert len(result) == 1
        assert result.iloc[0]['strike'] == 105.0


class TestCalculateIVRank:

    def test_returns_valid_iv_rank(self):
        """calculate_iv_rank should return a dict with iv_rank key when DataCache is provided."""
        np.random.seed(42)
        dates = pd.date_range('2024-01-01', periods=252, freq='B')
        prices = 100.0 + np.cumsum(np.random.randn(252) * 0.5)

        data_cache = MagicMock()
        data_cache.get_history.return_value = pd.DataFrame(
            {'Close': prices}, index=dates
        )

        analyzer = OptionsAnalyzer(_make_config(), data_cache=data_cache)
        result = analyzer.calculate_iv_rank('SPY', current_iv=25.0)

        assert 'iv_rank' in result
        assert 'iv_percentile' in result
        assert isinstance(result['iv_rank'], float)

    def test_raises_when_data_cache_missing(self):
        """calculate_iv_rank requires a DataCache and raises RuntimeError otherwise."""
        analyzer = OptionsAnalyzer(_make_config())

        with pytest.raises(RuntimeError, match="DataCache is required"):
            analyzer.calculate_iv_rank('SPY', current_iv=25.0)


class TestGetCurrentIV:

    def test_returns_median_iv(self):
        """get_current_iv should return the median IV times 100."""
        analyzer = OptionsAnalyzer(_make_config())
        df = _make_chain_df()
        result = analyzer.get_current_iv(df)
        assert result == pytest.approx(25.0, abs=0.1)

    def test_returns_zero_for_empty_chain(self):
        """get_current_iv should return 0.0 for empty chain."""
        analyzer = OptionsAnalyzer(_make_config())
        result = analyzer.get_current_iv(pd.DataFrame())
        assert result == 0.0
