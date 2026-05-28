"""LLM-backed market-theme categorizer.

Takes the signal-ranked top-N tickers (with momentum_z, flow_z, sentiment_z,
dark_flow, sector, industry) and asks Claude to label 3-5 coherent market
themes. Output is structured JSON enforced via tool_use; the LLM never sees
prices and never picks trades — it labels signals the engine already
computed.

Design rationale + integration flow: see ``reports/LLM_CATEGORY_ANALYSIS.md``.

Key invariants
--------------
- Rule Zero respected: the LLM consumes z-scores that were computed from
  real market data; it never invents prices.
- Off the trading critical path: ``run_daily()`` fails degraded (logs and
  exits non-zero) rather than blocking trade entries.
- Idempotent per (asof_date, prompt_hash): same input → cached output, no
  duplicate API spend.
- API key resolution is ``api_key`` arg → ``ANTHROPIC_API_KEY`` env →
  raise. The key value is never logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_OUTPUT_DIR = Path("data/llm_analysis")
DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "categorize_v1.txt"
MAX_TOKENS_OUT = 2000
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (1, 4, 16)
SUPPORTED_SIGNAL_NAMES = ("momentum", "flow", "sentiment", "dark_flow")

EMIT_CATEGORIES_TOOL: dict[str, Any] = {
    "name": "emit_categories",
    "description": "Emit the day's 3-5 market themes as structured records.",
    "input_schema": {
        "type": "object",
        "properties": {
            "categories": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "name":             {"type": "string", "maxLength": 60},
                        "tickers":          {"type": "array", "items": {"type": "string"}, "minItems": 2},
                        "confidence":       {"type": "number", "minimum": 0, "maximum": 1},
                        "signal_summary":   {"type": "string", "maxLength": 200},
                        "narrative":        {"type": "string", "maxLength": 600},
                        "direction":        {"type": "string", "enum": ["bull", "bear", "neutral"]},
                        "supporting_signals": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(SUPPORTED_SIGNAL_NAMES)},
                            "minItems": 1,
                        },
                    },
                    "required": [
                        "name", "tickers", "confidence", "signal_summary",
                        "narrative", "direction", "supporting_signals",
                    ],
                },
            },
        },
        "required": ["categories"],
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM API is unavailable after all retries."""


class CategoryValidationError(ValueError):
    """Raised when the LLM response fails post-parse validation."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickerSignal:
    symbol: str
    momentum_z: float
    flow_z: float
    sentiment_z: float
    dark_flow: Optional[float]
    sector: str
    industry: str
    market_cap_bn: float
    notes: str = ""

    @property
    def tilt_z(self) -> float:
        """Composite per the Dual-Path spec (weights placeholder pre-tuning)."""
        return 0.45 * self.momentum_z + 0.30 * self.flow_z + 0.25 * self.sentiment_z


@dataclass(frozen=True)
class Category:
    name: str
    tickers: tuple[str, ...]
    confidence: float
    signal_summary: str
    narrative: str
    direction: Literal["bull", "bear", "neutral"]
    supporting_signals: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # tuples → lists for JSON round-trip
        d["tickers"] = list(self.tickers)
        d["supporting_signals"] = list(self.supporting_signals)
        return d


@dataclass(frozen=True)
class CategoryAnalysis:
    asof_date: date
    generated_at_utc: datetime
    model: str
    n_input_tickers: int
    categories: tuple[Category, ...]
    prompt_hash: str
    raw_response_path: Optional[Path] = field(default=None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "asof_date": self.asof_date.isoformat(),
            "generated_at_utc": self.generated_at_utc.isoformat(),
            "model": self.model,
            "n_input_tickers": self.n_input_tickers,
            "prompt_hash": self.prompt_hash,
            "raw_response_path": str(self.raw_response_path) if self.raw_response_path else None,
            "categories": [c.as_dict() for c in self.categories],
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class CategoryAnalyzer:
    """Run the daily LLM categorisation pass.

    The Anthropic client is constructed lazily in ``_get_client`` so tests
    can inject a mock via the ``client`` constructor kwarg without ever
    touching the real SDK or env vars.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        prompt_path: Path = DEFAULT_PROMPT_PATH,
        max_tokens_out: int = MAX_TOKENS_OUT,
        client: Any = None,
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if self._api_key is None and client is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set and no api_key/client provided; "
                "CategoryAnalyzer cannot run."
            )
        self.model = model
        self.output_dir = Path(output_dir)
        self.prompt_path = Path(prompt_path)
        self.max_tokens_out = max_tokens_out
        self._client = client  # may be None → lazy

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, signals: list[TickerSignal], asof_date: date) -> CategoryAnalysis:
        """Run the categorisation; return a ``CategoryAnalysis``.

        Idempotent: identical (asof_date, prompt_hash) on disk → cached
        result returned without an API call.
        """
        if not signals:
            raise ValueError("analyze() received empty signals list")

        prompt = self._build_prompt(signals)
        prompt_hash = _sha1(prompt)

        cached = self._load_cache(asof_date, prompt_hash)
        if cached is not None:
            logger.info("LLM category cache hit for %s (hash=%s)", asof_date, prompt_hash[:8])
            return cached

        raw_response = self._call_llm(prompt)
        raw_path = self._write_raw_audit(asof_date, prompt_hash, prompt, raw_response)

        categories = self._parse_response(raw_response)
        self._validate(categories, {s.symbol for s in signals})

        analysis = CategoryAnalysis(
            asof_date=asof_date,
            generated_at_utc=datetime.now(timezone.utc),
            model=self.model,
            n_input_tickers=len(signals),
            categories=tuple(categories),
            prompt_hash=prompt_hash,
            raw_response_path=raw_path,
        )
        self._write_output(analysis)
        return analysis

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _build_prompt(self, signals: list[TickerSignal]) -> str:
        rows = [
            "symbol,momentum_z,flow_z,sentiment_z,dark_flow,sector,industry,market_cap_bn,notes",
        ]
        for s in signals:
            dark = "" if s.dark_flow is None else f"{s.dark_flow:.3f}"
            rows.append(
                f"{s.symbol},{s.momentum_z:.3f},{s.flow_z:.3f},{s.sentiment_z:.3f},"
                f"{dark},{s.sector},{s.industry},{s.market_cap_bn:.1f},{s.notes}"
            )

        asof = signals[0].notes  # placeholder; the real asof_date is passed via the user-msg header below
        del asof  # unused — kept to flag the param explicitly in code review
        return (
            f"TICKERS: {len(signals)} signal-ranked symbols, descending by |tilt_z|.\n\n"
            "Signal table (CSV, one ticker per row):\n"
            + "\n".join(rows)
            + "\n\nNotes column legend:\n"
            "  EARN_THIS_WK   = earnings within 5 trading days\n"
            "  REGIME_GATE_ON = composite-stress gate is firing today\n"
            "  THIN_LIQUIDITY = 21d ADV below universe median\n\n"
            "Produce 3-5 categories via the `emit_categories` tool."
        )

    def _load_system_prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # API call (with retry)
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # imported lazily so tests without the SDK still work
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _call_llm(self, user_prompt: str) -> Any:
        system_prompt = self._load_system_prompt()
        last_exc: Optional[Exception] = None
        for attempt, backoff in enumerate(RETRY_BACKOFF_SECONDS, start=1):
            try:
                client = self._get_client()
                resp = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens_out,
                    system=system_prompt,
                    tools=[EMIT_CATEGORIES_TOOL],
                    tool_choice={"type": "tool", "name": "emit_categories"},
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return resp
            except Exception as exc:  # noqa: BLE001 — retry on any SDK exception
                last_exc = exc
                logger.warning(
                    "LLM call attempt %d/%d failed: %s",
                    attempt, MAX_RETRIES, type(exc).__name__,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(backoff)
        raise LLMUnavailableError(
            f"All {MAX_RETRIES} LLM attempts failed; last error: {type(last_exc).__name__}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Response handling
    # ------------------------------------------------------------------

    def _parse_response(self, response: Any) -> list[Category]:
        """Extract the tool-use block and convert to ``Category`` records."""
        tool_input = _extract_tool_input(response, expected_name="emit_categories")
        raw_categories = tool_input.get("categories")
        if not isinstance(raw_categories, list):
            raise CategoryValidationError(
                "LLM tool call missing 'categories' list or wrong type"
            )

        out: list[Category] = []
        for i, raw in enumerate(raw_categories):
            try:
                out.append(Category(
                    name=str(raw["name"]),
                    tickers=tuple(str(t).upper() for t in raw["tickers"]),
                    confidence=float(raw["confidence"]),
                    signal_summary=str(raw["signal_summary"]),
                    narrative=str(raw["narrative"]),
                    direction=raw["direction"],
                    supporting_signals=tuple(str(s) for s in raw["supporting_signals"]),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise CategoryValidationError(
                    f"category[{i}] failed structural parse: {exc}"
                ) from exc
        return out

    def _validate(self, categories: list[Category], input_symbols: set[str]) -> None:
        if not categories:
            raise CategoryValidationError("LLM returned zero categories")
        upper_input = {s.upper() for s in input_symbols}
        for i, c in enumerate(categories):
            if not (0.0 <= c.confidence <= 1.0):
                raise CategoryValidationError(
                    f"category[{i}] confidence {c.confidence} outside [0,1]"
                )
            if c.direction not in ("bull", "bear", "neutral"):
                raise CategoryValidationError(
                    f"category[{i}] direction {c.direction!r} not in enum"
                )
            unknown = set(c.tickers) - upper_input
            if unknown:
                raise CategoryValidationError(
                    f"category[{i}] {c.name!r} references unknown tickers: {sorted(unknown)}"
                )
            if not c.tickers:
                raise CategoryValidationError(
                    f"category[{i}] {c.name!r} has empty ticker list"
                )
            bad_signals = set(c.supporting_signals) - set(SUPPORTED_SIGNAL_NAMES)
            if bad_signals:
                raise CategoryValidationError(
                    f"category[{i}] {c.name!r} has unknown supporting_signals: {sorted(bad_signals)}"
                )

    # ------------------------------------------------------------------
    # Disk I/O
    # ------------------------------------------------------------------

    def _output_path(self, asof_date: date) -> Path:
        return self.output_dir / f"{asof_date.isoformat()}.json"

    def _cache_meta_path(self, asof_date: date) -> Path:
        return self.output_dir / f"{asof_date.isoformat()}.meta.json"

    def _load_cache(self, asof_date: date, prompt_hash: str) -> Optional[CategoryAnalysis]:
        out_path = self._output_path(asof_date)
        meta_path = self._cache_meta_path(asof_date)
        if not (out_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("prompt_hash") != prompt_hash:
                return None
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            return _analysis_from_dict(payload)
        except Exception as exc:  # noqa: BLE001 — bad cache → refetch
            logger.warning("cache load failed (%s); refetching", exc)
            return None

    def _write_output(self, analysis: CategoryAnalysis) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._output_path(analysis.asof_date)
        meta_path = self._cache_meta_path(analysis.asof_date)
        out_path.write_text(json.dumps(analysis.as_dict(), indent=2), encoding="utf-8")
        meta_path.write_text(
            json.dumps({
                "prompt_hash": analysis.prompt_hash,
                "model": analysis.model,
                "generated_at_utc": analysis.generated_at_utc.isoformat(),
            }, indent=2),
            encoding="utf-8",
        )
        logger.info("wrote %s (%d categories)", out_path, len(analysis.categories))

    def _write_raw_audit(
        self, asof_date: date, prompt_hash: str, prompt: str, raw_response: Any,
    ) -> Path:
        raw_dir = self.output_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{asof_date.isoformat()}_{prompt_hash[:8]}.json"
        payload = {
            "prompt_hash": prompt_hash,
            "prompt": prompt,
            "response": _response_to_jsonable(raw_response),
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _extract_tool_input(response: Any, expected_name: str) -> dict[str, Any]:
    """Pull the tool_use block from an Anthropic Messages response.

    Accepts either a real ``anthropic.types.Message`` or a duck-typed mock
    with a ``content`` list of blocks (each with ``type`` and ``input``).
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if not content:
        raise CategoryValidationError("LLM response has no content blocks")
    for block in content:
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else None)
        if btype == "tool_use" and bname == expected_name:
            binput = getattr(block, "input", None)
            if binput is None and isinstance(block, dict):
                binput = block.get("input")
            if not isinstance(binput, dict):
                raise CategoryValidationError(
                    f"tool_use block {expected_name!r} has non-dict input"
                )
            return binput
    raise CategoryValidationError(
        f"no tool_use block named {expected_name!r} in LLM response"
    )


def _response_to_jsonable(response: Any) -> Any:
    """Best-effort serialise an SDK response for the raw audit log."""
    if hasattr(response, "model_dump"):
        try:
            return response.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(response, "to_dict"):
        try:
            return response.to_dict()
        except Exception:  # noqa: BLE001
            pass
    return repr(response)


def _analysis_from_dict(payload: dict[str, Any]) -> CategoryAnalysis:
    cats = tuple(
        Category(
            name=c["name"],
            tickers=tuple(c["tickers"]),
            confidence=float(c["confidence"]),
            signal_summary=c["signal_summary"],
            narrative=c["narrative"],
            direction=c["direction"],
            supporting_signals=tuple(c["supporting_signals"]),
        )
        for c in payload["categories"]
    )
    return CategoryAnalysis(
        asof_date=date.fromisoformat(payload["asof_date"]),
        generated_at_utc=datetime.fromisoformat(payload["generated_at_utc"]),
        model=payload["model"],
        n_input_tickers=int(payload["n_input_tickers"]),
        categories=cats,
        prompt_hash=payload["prompt_hash"],
        raw_response_path=Path(payload["raw_response_path"]) if payload.get("raw_response_path") else None,
    )


# ---------------------------------------------------------------------------
# One-shot convenience + scheduler entry-point
# ---------------------------------------------------------------------------


def analyze_top_tickers(
    signals: Iterable[TickerSignal],
    asof_date: date,
    *,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> CategoryAnalysis:
    """One-shot helper for callers that don't need to hold the analyzer."""
    analyzer = CategoryAnalyzer(
        api_key=api_key, model=model, output_dir=output_dir,
    )
    return analyzer.analyze(list(signals), asof_date=asof_date)


def run_daily() -> int:
    """Scheduler entry-point.

    Looks for ``data/signals/{today}/tilt.parquet`` and runs the
    categorizer. Returns 0 on success, non-zero on any failure
    (fail-degraded — does NOT block trade entries).
    """
    import pandas as pd  # heavy import; only at scheduler time
    from zoneinfo import ZoneInfo

    asof_date = datetime.now(ZoneInfo("America/New_York")).date()
    tilt_path = Path("data/signals") / asof_date.isoformat() / "tilt.parquet"
    if not tilt_path.exists():
        logger.warning("no tilt.parquet for %s at %s — exiting 0", asof_date, tilt_path)
        return 0

    df = pd.read_parquet(tilt_path)
    df = df.head(100)
    signals = [
        TickerSignal(
            symbol=row["symbol"],
            momentum_z=float(row["momentum_z"]),
            flow_z=float(row["flow_z"]),
            sentiment_z=float(row["sentiment_z"]),
            dark_flow=float(row["dark_flow"]) if pd.notna(row.get("dark_flow")) else None,
            sector=str(row.get("sector", "")),
            industry=str(row.get("industry", "")),
            market_cap_bn=float(row.get("market_cap_bn", 0.0)),
            notes=str(row.get("notes", "")),
        )
        for _, row in df.iterrows()
    ]
    try:
        analyze_top_tickers(signals, asof_date=asof_date)
        return 0
    except LLMUnavailableError as exc:
        logger.error("LLM unavailable: %s — failing degraded", exc)
        return 2
    except CategoryValidationError as exc:
        logger.error("LLM response failed validation: %s", exc)
        return 3
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM categorizer unhandled error: %s", exc)
        return 1
