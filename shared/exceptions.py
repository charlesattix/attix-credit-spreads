"""Custom exception hierarchy for the Attix Credit Spreads system."""


class AttixError(Exception):
    """Base exception for all Attix errors."""


class DataFetchError(AttixError):
    """Raised when data fetching (e.g. yfinance download) fails."""


class ProviderError(AttixError):
    """Raised when a provider API call (Tradier, Polygon, Alpaca) fails."""


class ModelError(AttixError):
    """Raised on ML model errors (training, prediction, loading)."""
