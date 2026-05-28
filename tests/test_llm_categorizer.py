"""Tests for compass.analysis.llm_categorizer.

All API interaction mocked — no live Anthropic calls in CI.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from compass.analysis.llm_categorizer import (
    CategoryAnalyzer,
    CategoryValidationError,
    LLMUnavailableError,
    TickerSignal,
    _extract_tool_input,
    analyze_top_tickers,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _signals() -> list[TickerSignal]:
    return [
        TickerSignal("NVDA", 2.84, 2.61, 1.92, 3.45, "Information Technology", "Semiconductors", 3120.0),
        TickerSignal("AVGO", 2.41, 1.97, 1.55, 2.10, "Information Technology", "Semiconductors", 840.0),
        TickerSignal("AMD",  2.18, 2.05, 1.83, 1.78, "Information Technology", "Semiconductors", 330.0),
        TickerSignal("VRT",  1.94, 1.62, 1.41, 1.22, "Industrials", "Electrical Components", 32.0),
        TickerSignal("SMCI", 1.81, 1.55, 2.10, 1.94, "Information Technology", "Tech Hardware", 42.0,
                     notes="REGIME_GATE_ON"),
        TickerSignal("KO",   -1.20, -0.40, -0.85, None, "Consumer Staples", "Beverages", 280.0),
        TickerSignal("PG",   -0.95, -0.30, -0.70, None, "Consumer Staples", "Household", 380.0),
    ]


def _make_tool_response(categories: list[dict]) -> MagicMock:
    """Build a duck-typed Anthropic SDK Messages response."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "emit_categories"
    block.input = {"categories": categories}

    resp = MagicMock()
    resp.content = [block]
    return resp


def _valid_categories_payload() -> list[dict]:
    return [
        {
            "name": "AI Infrastructure Capex",
            "tickers": ["NVDA", "AVGO", "AMD", "VRT", "SMCI"],
            "confidence": 0.86,
            "signal_summary": "5 names with momentum_z > 1.5 and flow_z > 1.4.",
            "narrative": "Cross-sector AI capex bid across semis and power.",
            "direction": "bull",
            "supporting_signals": ["momentum", "flow", "sentiment"],
        },
        {
            "name": "Defensive Rotation",
            "tickers": ["KO", "PG"],
            "confidence": 0.62,
            "signal_summary": "2 staples names with mom_z < -0.9.",
            "narrative": "Staples are bid as a defensive hedge.",
            "direction": "bear",
            "supporting_signals": ["momentum", "sentiment"],
        },
    ]


@pytest.fixture
def analyzer(tmp_path):
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _make_tool_response(
        _valid_categories_payload()
    )
    return CategoryAnalyzer(
        api_key="fake-key",
        output_dir=tmp_path / "out",
        client=fake_client,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_no_key_no_client_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            CategoryAnalyzer()

    def test_env_var_resolves(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        an = CategoryAnalyzer()
        assert an._api_key == "sk-test"

    def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        an = CategoryAnalyzer(api_key="sk-arg")
        assert an._api_key == "sk-arg"

    def test_client_kwarg_bypasses_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        an = CategoryAnalyzer(client=MagicMock())
        assert an._api_key is None  # client provided, no need to resolve a key


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestPromptBuild:
    def test_prompt_contains_every_input_ticker(self, analyzer):
        sigs = _signals()
        prompt = analyzer._build_prompt(sigs)
        for s in sigs:
            assert s.symbol in prompt
        assert "Notes column legend" in prompt
        assert "emit_categories" in prompt

    def test_prompt_csv_order_preserved(self, analyzer):
        sigs = _signals()
        prompt = analyzer._build_prompt(sigs)
        positions = [prompt.index(s.symbol) for s in sigs]
        assert positions == sorted(positions), "CSV order must follow input order"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_parse_valid_response(self, analyzer):
        resp = _make_tool_response(_valid_categories_payload())
        cats = analyzer._parse_response(resp)
        assert len(cats) == 2
        assert cats[0].name == "AI Infrastructure Capex"
        assert cats[0].tickers == ("NVDA", "AVGO", "AMD", "VRT", "SMCI")
        assert cats[0].direction == "bull"

    def test_no_tool_block_raises(self, analyzer):
        resp = MagicMock()
        resp.content = [MagicMock(type="text")]
        with pytest.raises(CategoryValidationError, match="no tool_use"):
            analyzer._parse_response(resp)

    def test_empty_content_raises(self, analyzer):
        resp = MagicMock()
        resp.content = []
        with pytest.raises(CategoryValidationError, match="no content"):
            analyzer._parse_response(resp)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidate:
    def test_unknown_ticker_rejected(self, analyzer):
        payload = _valid_categories_payload()
        payload[0]["tickers"].append("FAKE")
        resp = _make_tool_response(payload)
        cats = analyzer._parse_response(resp)
        with pytest.raises(CategoryValidationError, match="unknown tickers"):
            analyzer._validate(cats, {s.symbol for s in _signals()})

    def test_confidence_out_of_range_rejected(self, analyzer):
        payload = _valid_categories_payload()
        payload[0]["confidence"] = 1.5
        resp = _make_tool_response(payload)
        cats = analyzer._parse_response(resp)
        with pytest.raises(CategoryValidationError, match="outside"):
            analyzer._validate(cats, {s.symbol for s in _signals()})

    def test_empty_categories_rejected(self, analyzer):
        with pytest.raises(CategoryValidationError, match="zero categories"):
            analyzer._validate([], {s.symbol for s in _signals()})

    def test_bad_supporting_signal_rejected(self, analyzer):
        payload = _valid_categories_payload()
        payload[0]["supporting_signals"] = ["momentum", "moon_phase"]
        resp = _make_tool_response(payload)
        cats = analyzer._parse_response(resp)
        with pytest.raises(CategoryValidationError, match="unknown supporting_signals"):
            analyzer._validate(cats, {s.symbol for s in _signals()})


# ---------------------------------------------------------------------------
# End-to-end analyze()
# ---------------------------------------------------------------------------


class TestAnalyze:
    def test_writes_output_files(self, analyzer, tmp_path):
        result = analyzer.analyze(_signals(), asof_date=date(2026, 5, 27))
        out = tmp_path / "out" / "2026-05-27.json"
        meta = tmp_path / "out" / "2026-05-27.meta.json"
        raw_dir = tmp_path / "out" / "raw"
        assert out.exists()
        assert meta.exists()
        assert raw_dir.exists() and any(raw_dir.iterdir())
        payload = json.loads(out.read_text())
        assert payload["n_input_tickers"] == 7
        assert len(payload["categories"]) == 2
        assert result.model == "claude-opus-4-7"

    def test_idempotent_for_same_inputs(self, analyzer):
        """Second analyze() with identical inputs must hit cache, not API."""
        analyzer.analyze(_signals(), asof_date=date(2026, 5, 27))
        first_call_count = analyzer._client.messages.create.call_count
        analyzer.analyze(_signals(), asof_date=date(2026, 5, 27))
        assert analyzer._client.messages.create.call_count == first_call_count

    def test_empty_signals_raises(self, analyzer):
        with pytest.raises(ValueError, match="empty signals"):
            analyzer.analyze([], asof_date=date(2026, 5, 27))


# ---------------------------------------------------------------------------
# Retry & failure
# ---------------------------------------------------------------------------


class TestRetryAndFailure:
    def test_retries_then_fails_degraded(self, tmp_path, monkeypatch):
        # Make sleeps instant so the test runs in milliseconds.
        monkeypatch.setattr("compass.analysis.llm_categorizer.time.sleep", lambda *_: None)

        flaky = MagicMock()
        flaky.messages.create.side_effect = ConnectionError("boom")
        an = CategoryAnalyzer(
            api_key="fake-key",
            output_dir=tmp_path / "out",
            client=flaky,
        )
        with pytest.raises(LLMUnavailableError):
            an.analyze(_signals(), asof_date=date(2026, 5, 27))
        # Final attempt count = MAX_RETRIES = 3
        assert flaky.messages.create.call_count == 3

    def test_recovers_after_transient_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("compass.analysis.llm_categorizer.time.sleep", lambda *_: None)

        client = MagicMock()
        good = _make_tool_response(_valid_categories_payload())
        client.messages.create.side_effect = [ConnectionError("blip"), good]
        an = CategoryAnalyzer(
            api_key="fake-key",
            output_dir=tmp_path / "out",
            client=client,
        )
        result = an.analyze(_signals(), asof_date=date(2026, 5, 27))
        assert len(result.categories) == 2
        assert client.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# API-key privacy
# ---------------------------------------------------------------------------


class TestKeyPrivacy:
    def test_key_not_in_logs_on_failure(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr("compass.analysis.llm_categorizer.time.sleep", lambda *_: None)
        secret = "sk-ant-SUPER-SECRET-DO-NOT-LEAK"
        flaky = MagicMock()
        flaky.messages.create.side_effect = RuntimeError("API blew up")
        an = CategoryAnalyzer(
            api_key=secret,
            output_dir=tmp_path / "out",
            client=flaky,
        )
        with pytest.raises(LLMUnavailableError):
            an.analyze(_signals(), asof_date=date(2026, 5, 27))
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert secret not in joined


# ---------------------------------------------------------------------------
# tilt_z derived property
# ---------------------------------------------------------------------------


class TestTickerSignal:
    def test_tilt_z_formula(self):
        s = TickerSignal("X", 1.0, 1.0, 1.0, None, "Tech", "Software", 100.0)
        # 0.45 + 0.30 + 0.25 = 1.0
        assert s.tilt_z == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Convenience entry-point
# ---------------------------------------------------------------------------


class TestOneShotHelper:
    def test_analyze_top_tickers_writes_files(self, tmp_path, monkeypatch):
        # Patch the Analyzer's client to a mock by monkeypatching _get_client.
        from compass.analysis import llm_categorizer as mod

        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_tool_response(
            _valid_categories_payload()
        )

        def _fake_get_client(self):
            return fake_client
        monkeypatch.setattr(mod.CategoryAnalyzer, "_get_client", _fake_get_client)

        result = analyze_top_tickers(
            _signals(),
            asof_date=date(2026, 5, 27),
            api_key="fake-key",
            output_dir=tmp_path / "out",
        )
        assert len(result.categories) == 2
        assert (tmp_path / "out" / "2026-05-27.json").exists()


# ---------------------------------------------------------------------------
# Tool extractor
# ---------------------------------------------------------------------------


class TestExtractToolInput:
    def test_dict_response_supported(self):
        response = {
            "content": [
                {"type": "tool_use", "name": "emit_categories",
                 "input": {"categories": []}},
            ],
        }
        out = _extract_tool_input(response, expected_name="emit_categories")
        assert out == {"categories": []}

    def test_wrong_tool_name_raises(self):
        response = {
            "content": [
                {"type": "tool_use", "name": "wrong_tool", "input": {}},
            ],
        }
        with pytest.raises(CategoryValidationError, match="no tool_use"):
            _extract_tool_input(response, expected_name="emit_categories")
