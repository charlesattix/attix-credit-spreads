"""Tests for ``shared.uw_client.UWClient`` (HTTP-mocked)."""
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import requests

from shared.exceptions import DataFetchError
from shared.uw_client import UWClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, json_data: Optional[dict] = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {"data": []}
    resp.text = text
    return resp


def _make_client(token: str = "tok-uuid-1234") -> UWClient:
    return UWClient(api_token=token, cache_ttl_seconds=0)


# ---------------------------------------------------------------------------
# Constructor / config
# ---------------------------------------------------------------------------


class TestUWClientInit:

    def test_reads_token_from_env(self, monkeypatch):
        monkeypatch.setenv("UW_API_TOKEN", "env-tok-abc")
        client = UWClient()
        assert client._token == "env-tok-abc"

    def test_explicit_token_overrides_env(self, monkeypatch):
        monkeypatch.setenv("UW_API_TOKEN", "env-tok-abc")
        client = UWClient(api_token="explicit-tok")
        assert client._token == "explicit-tok"

    def test_missing_token_raises_on_request(self, monkeypatch):
        monkeypatch.delenv("UW_API_TOKEN", raising=False)
        client = UWClient()
        with pytest.raises(DataFetchError, match="UW_API_TOKEN not configured"):
            client.get_earnings_history("AAPL")


# ---------------------------------------------------------------------------
# get_earnings_history
# ---------------------------------------------------------------------------


class TestGetEarningsHistory:

    def test_happy_path_returns_data_array(self):
        client = _make_client()
        sample = {"data": [
            {"report_date": "2026-07-29", "expected_move_perc": 0.04},
            {"report_date": "2026-04-30", "expected_move_perc": 0.05},
        ]}
        with patch.object(client._session, "get", return_value=_mock_response(200, sample)) as mock_get:
            result = client.get_earnings_history("AAPL")

        assert result == sample["data"]
        url = mock_get.call_args[0][0]
        assert url == "https://api.unusualwhales.com/api/stock/AAPL/earnings"
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok-uuid-1234"
        assert headers["UW-CLIENT-API-ID"] == "100001"

    def test_ticker_is_uppercased_in_url(self):
        client = _make_client()
        with patch.object(client._session, "get", return_value=_mock_response(200, {"data": []})) as mock_get:
            client.get_earnings_history("aapl")
        assert "/api/stock/AAPL/earnings" in mock_get.call_args[0][0]

    def test_empty_data_returns_empty_list(self):
        client = _make_client()
        with patch.object(client._session, "get", return_value=_mock_response(200, {"data": []})):
            assert client.get_earnings_history("AAPL") == []

    def test_missing_data_key_returns_empty_list(self):
        client = _make_client()
        with patch.object(client._session, "get", return_value=_mock_response(200, {})):
            assert client.get_earnings_history("AAPL") == []

    def test_401_raises_data_fetch_error(self):
        client = _make_client()
        with patch.object(client._session, "get", return_value=_mock_response(401, {}, "unauthorized")):
            with pytest.raises(DataFetchError, match="UW HTTP 401"):
                client.get_earnings_history("AAPL")

    def test_403_raises_data_fetch_error(self):
        client = _make_client()
        with patch.object(client._session, "get", return_value=_mock_response(403, {}, "forbidden")):
            with pytest.raises(DataFetchError, match="UW HTTP 403"):
                client.get_earnings_history("AAPL")

    def test_429_retries_then_succeeds(self):
        client = _make_client()
        sample = {"data": [{"report_date": "2026-07-29"}]}
        responses = [
            _mock_response(429, {}, "rate limited"),
            _mock_response(200, sample),
        ]
        with patch.object(client._session, "get", side_effect=responses) as mock_get, \
             patch("shared.uw_client.time.sleep") as mock_sleep:
            result = client.get_earnings_history("AAPL")

        assert result == sample["data"]
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(1)

    def test_429_exhausts_retries_and_raises(self):
        client = _make_client()
        with patch.object(client._session, "get", return_value=_mock_response(429, {}, "rate limited")) as mock_get, \
             patch("shared.uw_client.time.sleep"):
            with pytest.raises(DataFetchError, match="UW HTTP 429"):
                client.get_earnings_history("AAPL")
        assert mock_get.call_count == 3

    def test_500_retries_then_succeeds(self):
        client = _make_client()
        sample = {"data": [{"report_date": "2026-07-29"}]}
        responses = [
            _mock_response(503, {}, "service unavailable"),
            _mock_response(200, sample),
        ]
        with patch.object(client._session, "get", side_effect=responses) as mock_get, \
             patch("shared.uw_client.time.sleep"):
            result = client.get_earnings_history("AAPL")

        assert result == sample["data"]
        assert mock_get.call_count == 2

    def test_network_error_raises_data_fetch_error(self):
        client = _make_client()
        with patch.object(client._session, "get", side_effect=requests.ConnectionError("boom")) as mock_get, \
             patch("shared.uw_client.time.sleep"):
            with pytest.raises(DataFetchError, match="UW request failed"):
                client.get_earnings_history("AAPL")
        # Retries 3 times on network error
        assert mock_get.call_count == 3


# ---------------------------------------------------------------------------
# get_earnings_premarket / get_earnings_afterhours
# ---------------------------------------------------------------------------


class TestGetEarningsPremarket:

    def test_happy_path_no_date(self):
        client = _make_client()
        sample = {"data": [{"ticker": "AAPL", "expected_move_perc": 0.04}]}
        with patch.object(client._session, "get", return_value=_mock_response(200, sample)) as mock_get:
            result = client.get_earnings_premarket()

        assert result == sample["data"]
        assert mock_get.call_args[0][0] == "https://api.unusualwhales.com/api/earnings/premarket"
        # date omitted → no params (or None)
        assert mock_get.call_args.kwargs.get("params") in (None, {})

    def test_happy_path_with_date(self):
        client = _make_client()
        with patch.object(client._session, "get", return_value=_mock_response(200, {"data": []})) as mock_get:
            client.get_earnings_premarket(date="2026-05-22")
        assert mock_get.call_args.kwargs["params"] == {"date": "2026-05-22"}

    def test_429_retries(self):
        client = _make_client()
        responses = [
            _mock_response(429, {}, "rate limited"),
            _mock_response(200, {"data": [{"ticker": "MSFT"}]}),
        ]
        with patch.object(client._session, "get", side_effect=responses), \
             patch("shared.uw_client.time.sleep"):
            result = client.get_earnings_premarket()
        assert result == [{"ticker": "MSFT"}]


class TestGetEarningsAfterhours:

    def test_happy_path_with_date(self):
        client = _make_client()
        sample = {"data": [{"ticker": "NVDA"}]}
        with patch.object(client._session, "get", return_value=_mock_response(200, sample)) as mock_get:
            result = client.get_earnings_afterhours(date="2026-05-22")
        assert result == sample["data"]
        assert mock_get.call_args[0][0] == "https://api.unusualwhales.com/api/earnings/afterhours"
        assert mock_get.call_args.kwargs["params"] == {"date": "2026-05-22"}

    def test_network_error_raises(self):
        client = _make_client()
        with patch.object(client._session, "get", side_effect=requests.Timeout("timeout")), \
             patch("shared.uw_client.time.sleep"):
            with pytest.raises(DataFetchError):
                client.get_earnings_afterhours()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestUWClientCache:

    def test_repeated_call_hits_cache(self):
        client = UWClient(api_token="tok", cache_ttl_seconds=3600)
        sample = {"data": [{"report_date": "2026-07-29"}]}
        with patch.object(client._session, "get", return_value=_mock_response(200, sample)) as mock_get:
            r1 = client.get_earnings_history("AAPL")
            r2 = client.get_earnings_history("AAPL")
        assert r1 == r2 == sample["data"]
        assert mock_get.call_count == 1

    def test_ttl_zero_disables_cache(self):
        client = UWClient(api_token="tok", cache_ttl_seconds=0)
        sample = {"data": []}
        with patch.object(client._session, "get", return_value=_mock_response(200, sample)) as mock_get:
            client.get_earnings_history("AAPL")
            client.get_earnings_history("AAPL")
        assert mock_get.call_count == 2

    def test_clear_cache_forces_refetch(self):
        client = UWClient(api_token="tok", cache_ttl_seconds=3600)
        with patch.object(client._session, "get", return_value=_mock_response(200, {"data": []})) as mock_get:
            client.get_earnings_history("AAPL")
            client.clear_cache()
            client.get_earnings_history("AAPL")
        assert mock_get.call_count == 2

    def test_different_tickers_cached_separately(self):
        client = UWClient(api_token="tok", cache_ttl_seconds=3600)
        with patch.object(client._session, "get", return_value=_mock_response(200, {"data": []})) as mock_get:
            client.get_earnings_history("AAPL")
            client.get_earnings_history("MSFT")
        assert mock_get.call_count == 2
