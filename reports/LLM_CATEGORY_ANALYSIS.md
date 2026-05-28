# LLM Category Analysis Layer — Design Spec

**Date:** 2026-05-28
**Author:** CC3
**Status:** Design proposal / pre-build
**Builds on:** `reports/MULTI_SIGNAL_PROPOSAL.md`, `reports/MULTI_SIGNAL_EXECUTION_PLAN.md`, `reports/MULTI_SIGNAL_DUAL_PATH.md`
**Scope:** A daily Claude-API pass over the top-100 signal-ranked tickers that emits 3-5 structured market themes, consumable by both Dual-Path backtests and live signal selection.

---

## 0. TL;DR

- Top 100 ticker rows (symbol, momentum_z, flow_z, sentiment_z, dark_flow, sector) → Claude Sonnet 4.6 → structured JSON with 3-5 categories.
- Each category: `name`, `tickers`, `confidence`, `signal_summary`, `narrative`, `direction` (`bull`/`bear`/`neutral`).
- Output written to `data/tradealgo/YYYY-MM-DD/llm_analysis.json`, also tee'd to `data/llm_categories/YYYY-MM-DD.json` for paths that don't depend on TradeAlgo.
- Runs once per trading day at **16:40 ET**, after the TradeAlgo snapshot (16:30) and after the signal-engine pass (16:35). Cron via existing `scheduler/` machinery.
- LLM is an **analyst, not a trader.** Output is a *labeling* layer: it names what's already in the numerical signal. The trade-selection layer still ranks on `tilt_z`; the LLM categories feed **portfolio construction** (theme diversification, narrative filters) and **explainability** (signal log lines reference category names).
- Cost ceiling: ~$0.05/day, $15/year. One call/day, ~6K input tokens + ~1.5K output tokens. No live-trading hot-path dependency on the LLM — if the call fails, sizing falls back to raw `tilt_z` (fail-degraded, not fail-closed).

---

## 1. Why an LLM layer (and why now)

The Dual-Path proposal compresses three signal families (momentum, flow, sentiment) into a single `tilt_z` score per ticker. That number is great for **ranking**; it is bad for **interpretation**. A daily list of 100 tilt-ranked tickers does not answer:

- "Are the top 20 names actually one story (e.g. *AI capex narrative*) or 20 unrelated bets?"
- "Is the flow signal concentrated in a single sub-theme (e.g. *uranium*, *defense primes*, *GLP-1*) that GICS Level-1 sectoring misses?"
- "Is today's signal a regime shift (broad rotation into defensives) or noise (one mega-cap dragging its sector basket)?"

These are the questions a portfolio manager asks at 4:45 PM. An LLM is well-suited to that pattern-recognition pass: structured signal table in, narrative labels out. **Crucially, the LLM never sees prices and never picks trades** — it sees the same z-scores the engine already computed, and produces labels.

This is also why the LLM call is **not in the trading hot path.** It runs after the numerical signals are frozen; if it fails, the engine falls back to ranking on `tilt_z` directly. Rule Zero is preserved — the LLM does not synthesize data, it labels it.

---

## 2. Module — `compass/analysis/llm_categorizer.py`

### 2.1 Public surface

```python
from compass.analysis.llm_categorizer import (
    CategoryAnalyzer,
    Category,
    CategoryAnalysis,
    analyze_top_tickers,        # one-shot convenience
)
```

### 2.2 Data model (dataclasses)

```python
@dataclass(frozen=True)
class TickerSignal:
    symbol: str
    momentum_z: float
    flow_z: float
    sentiment_z: float
    dark_flow: float            # optional — None when TradeAlgo not yet available
    sector: str                 # GICS Level 1
    industry: str               # GICS Level 2
    market_cap_bn: float        # for context only, NOT used as a sort key
    notes: str = ""             # free-text from upstream (earnings flag, etc.)

@dataclass(frozen=True)
class Category:
    name: str                    # e.g. "AI Infrastructure Capex"
    tickers: list[str]           # subset of input symbols, ordered by relevance
    confidence: float            # 0.0–1.0, model self-rated
    signal_summary: str          # one-sentence quantitative summary
    narrative: str               # 2-3 sentence thesis
    direction: Literal["bull", "bear", "neutral"]
    supporting_signals: list[str]  # which signal families drive it: ["momentum","flow",...]

@dataclass(frozen=True)
class CategoryAnalysis:
    asof_date: date              # trading date the signals are FROM
    generated_at_utc: datetime
    model: str                   # e.g. "claude-sonnet-4-6"
    n_input_tickers: int
    categories: list[Category]   # 3-5 entries
    raw_response_path: Path      # for audit; full LLM response stored verbatim
```

### 2.3 Class skeleton

```python
class CategoryAnalyzer:
    def __init__(
        self,
        api_key: str | None = None,         # falls back to ANTHROPIC_API_KEY env var
        model: str = "claude-sonnet-4-6",   # default per Anthropic SDK
        output_dir: Path = Path("data/llm_categories"),
        cache_ttl_hours: int = 20,          # one trading day
        max_tokens_out: int = 2000,
    ): ...

    def analyze(self, signals: list[TickerSignal], asof_date: date) -> CategoryAnalysis:
        """Run the categorisation. Idempotent for the same asof_date."""

    def _build_prompt(self, signals: list[TickerSignal]) -> str: ...

    def _parse_response(self, raw: str) -> list[Category]: ...

    def _validate(self, categories: list[Category], input_symbols: set[str]) -> None:
        """Reject categories that reference tickers not in input set,
        confidence outside [0,1], direction not in the literal set, or
        empty ticker lists."""
```

### 2.4 Key implementation rules

- **Anthropic SDK** (`anthropic.Anthropic()`), Messages API, `tool_use` block for structured output. Schema enforced via the tool's `input_schema` — no fragile regex on free text.
- **API key resolution:** `api_key` arg → `ANTHROPIC_API_KEY` env var → raise `RuntimeError`. No fallback to a hard-coded key, no logging of the key value.
- **Retry policy:** 3 attempts, exponential backoff (1s, 4s, 16s). On final failure, raise `LLMUnavailableError` — caller decides whether to fail-degraded or fail-closed.
- **Disk cache:** `data/llm_categories/{asof_date}.json` keyed by `(asof_date, model, sha1(prompt))`. Identical inputs on the same day return the cached result, no API call.
- **Audit log:** full request + response written to `data/llm_categories/raw/{asof_date}_{hash}.json` for after-the-fact debugging. Never logged through stdout (PII-safe, key-safe).
- **No live-trading dependency:** the trade-selection path imports this module *opportunistically* — see §6. A missing or stale `llm_analysis.json` does NOT block any trade.

---

## 3. Prompt template

Stored as `compass/analysis/prompts/categorize_v1.txt`. Versioned so a prompt change is reviewable.

### 3.1 System prompt (verbatim)

```
You are a quantitative-equity analyst labeling the day's signal-ranked tickers
with the market themes that explain them. You receive structured numerical
signals only — you NEVER see prices, charts, or news.

Your job is pattern recognition over the signal table:
  1. Cluster the top-100 tickers into 3–5 coherent market themes.
  2. For each theme, name it concisely (a phrase a PM would use, e.g.
     "AI Infrastructure Capex" or "Defense Primes Breakout").
  3. State the THESIS in 2–3 sentences, grounded in the supplied signals.
  4. Mark direction: bull / bear / neutral.
  5. Self-rate confidence in [0,1] based on within-cluster signal coherence.

Hard rules:
  - Only reference tickers from the input table.
  - Do not invent prices, news, or quantitative claims not in the signals.
  - If the input is incoherent (e.g. no clusters >3 tickers), say so and
    return fewer categories. Never pad to hit 5.
  - Confidence < 0.5 must be flagged in the narrative as low-conviction.
  - GICS sector is provided — use it as one input, but the most interesting
    themes usually CROSS GICS sectors (e.g. AI capex spans XLK + XLC + XLU).

Output exclusively via the `emit_categories` tool. Do not write prose
outside the tool call.
```

### 3.2 User-message template

```
ASOF: {asof_date}
TICKERS: {n_input} signal-ranked symbols, descending by |tilt_z|.

Signal table (CSV, one ticker per row):
symbol,momentum_z,flow_z,sentiment_z,dark_flow,sector,industry,market_cap_bn,notes
{rows}

Notes column legend:
  EARN_THIS_WK   = earnings within 5 trading days
  REGIME_GATE_ON = composite-stress gate is firing today
  THIN_LIQUIDITY = 21d ADV below universe median

Produce 3–5 categories via the `emit_categories` tool.
```

### 3.3 Tool schema (forces structured output)

```python
EMIT_CATEGORIES_TOOL = {
    "name": "emit_categories",
    "description": "Emit the day's 3–5 market themes as structured records.",
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
                            "items": {"type": "string", "enum": ["momentum", "flow", "sentiment", "dark_flow"]},
                            "minItems": 1,
                        },
                    },
                    "required": ["name", "tickers", "confidence", "signal_summary",
                                 "narrative", "direction", "supporting_signals"],
                },
            },
        },
        "required": ["categories"],
    },
}
```

---

## 4. Example I/O

### 4.1 Example input — first 5 rows of a 100-row table

```csv
symbol,momentum_z,flow_z,sentiment_z,dark_flow,sector,industry,market_cap_bn,notes
NVDA,2.84,2.61,1.92,3.45,Information Technology,Semiconductors,3120,
AVGO,2.41,1.97,1.55,2.10,Information Technology,Semiconductors,840,
AMD,2.18,2.05,1.83,1.78,Information Technology,Semiconductors,330,
VRT,1.94,1.62,1.41,1.22,Industrials,Electrical Components,32,
SMCI,1.81,1.55,2.10,1.94,Information Technology,Tech Hardware,42,REGIME_GATE_ON
...
```

### 4.2 Example output (one category, JSON)

```json
{
  "asof_date": "2026-05-27",
  "generated_at_utc": "2026-05-27T20:40:14Z",
  "model": "claude-sonnet-4-6",
  "n_input_tickers": 100,
  "categories": [
    {
      "name": "AI Infrastructure Capex",
      "tickers": ["NVDA", "AVGO", "AMD", "VRT", "SMCI", "ANET", "MU", "DLR", "EQIX"],
      "confidence": 0.86,
      "signal_summary": "9 names with momentum_z > 1.5 AND flow_z > 1.4, clustered across semis, networking, and data-center REITs.",
      "narrative": "Cross-sector signal coherence: semiconductors (NVDA/AVGO/AMD/MU), networking (ANET), thermal/power (VRT/SMCI), and data-center REITs (DLR/EQIX) are all bid on identical signal vectors. This is the canonical AI-capex story expressing through the full supply chain, not just chip designers. Both flow and sentiment confirm momentum — three-way signal agreement.",
      "direction": "bull",
      "supporting_signals": ["momentum", "flow", "sentiment"]
    },
    {
      "name": "Defensive Rotation",
      "tickers": ["XLP-constituent: KO, PG, WMT, COST, JNJ, MDLZ"],
      ...
    }
  ]
}
```

---

## 5. Integration flow

```
16:30 ET — TradeAlgo snapshot lands (shared/tradealgo_client.py)
16:35 ET — Signal engine runs (compass/signals/composite_tilt.py)
              → emits data/signals/{asof_date}/tilt.parquet (100 rows top-N)
16:40 ET — LLM categorizer fires (NEW — this spec)
              ↓
       compass/analysis/llm_categorizer.py:analyze()
              ↓
       reads tilt.parquet, builds prompt, calls Claude API
              ↓
       writes data/llm_categories/{asof_date}.json
              ↓
       tees to data/tradealgo/{asof_date}/llm_analysis.json
              ↓
       writes raw audit to data/llm_categories/raw/{asof_date}_{hash}.json
16:45 ET — Trade-selection pass (next morning's positions queued)
              opportunistically reads llm_analysis.json for theme tags
```

The path between 16:35 and 16:45 is the *only* point where the LLM can run. It is deliberately **off the critical path** for any live trade — by the time the next trading day opens, the categories are 18+ hours old and serve as a *labeling overlay*, not a *signal generator*.

---

## 6. Use cases — how categories feed downstream

### 6.1 Theme diversification cap (portfolio construction)
Cap concurrent positions per *category* at 3, not per GICS sector. GICS would let you load up on NVDA+AVGO+AMD+MU as four separate "Semiconductors" positions; the LLM category catches that they are *one bet* on the AI capex theme. Cap saves max-loss concentration risk.

### 6.2 Narrative filter (entry suppression)
If a top-tilt ticker is in a category with `confidence < 0.4` OR `direction = "neutral"`, drop the position from the daily entry list. Low LLM coherence + high numerical tilt is a known noise pattern (one-off flow spike, not a theme).

### 6.3 Explainability layer (signal log lines, Telegram alerts)
Every entry alert prints the LLM category alongside the numerical signal:
```
ENTRY  NVDA  PCS 1200/1190  tilt_z=+2.84  cat="AI Infrastructure Capex" (0.86)
```
This is the operator's sanity check — when the engine fires, they see *why* in the same line.

### 6.4 Backtest enrichment (post-hoc analysis)
For the Dual-Path backtest (Path A vs Path B), tag each historical trade with its category at entry. The retrospective question "which themes did Path A capture vs Path B miss?" becomes answerable, not hand-wavy.

### 6.5 Regime overlay diagnostic
When `should_gate_spx_streams()` fires, the LLM category log gives a *human* explanation of what the engine just stepped out of: "Gate ON; top category yesterday was 'Defensive Rotation' (bear, 0.78) — gate consistent with category."

---

## 7. Daily schedule (scheduler integration)

Add a single line to the existing `scheduler/jobs.py`:

```python
SCHEDULE = [
    # ... existing jobs ...
    Job(
        name="llm_categorize",
        cron="40 16 * * 1-5",                   # 16:40 ET, weekdays
        timezone="America/New_York",
        entrypoint="compass.analysis.llm_categorizer:run_daily",
        max_runtime_seconds=120,                 # SLA: never blocks > 2 min
        on_failure="log_only",                   # fail-degraded, NOT fail-closed
    ),
]
```

`run_daily()` is a thin entry-point that:
1. Resolves `asof_date = today_us_eastern()` (or `--date` flag for backfill)
2. Loads `data/signals/{asof_date}/tilt.parquet` — if missing, exit 0 (the signal engine didn't run, so categorization is a no-op)
3. Calls `CategoryAnalyzer().analyze(...)`
4. Writes both output paths
5. Telegram-notifies on success (one-line summary: top category name + confidence)

---

## 8. Unit tests (`tests/test_llm_categorizer.py`)

| Test | What it asserts |
|---|---|
| `test_prompt_contains_all_signals` | Generated prompt includes every row of input table, in CSV order |
| `test_parse_valid_response` | A canned valid LLM response parses into 3 `Category` dataclasses |
| `test_reject_unknown_tickers` | LLM returning `tickers=["FAKE"]` → validation raises, no JSON written |
| `test_reject_confidence_out_of_range` | LLM returning `confidence=1.5` → validation raises |
| `test_reject_empty_categories` | LLM returning `[]` → validation raises (must produce ≥1) |
| `test_disk_cache_hit_skips_api` | Second call with same `(asof_date, prompt_hash)` does NOT hit the API mock |
| `test_failure_is_fail_degraded` | API mock raises `APIError` → `LLMUnavailableError` raised, no partial file written |
| `test_no_anthropic_key_raises` | `ANTHROPIC_API_KEY` unset + no arg → clear `RuntimeError` at construction |
| `test_redacts_api_key_in_logs` | Caplog never contains the key string, even on error paths |
| `test_idempotent_for_same_date` | Two runs on same date produce byte-identical output files |

All API interaction mocked via `unittest.mock`, `respx`, or a fake `anthropic.Anthropic` client. **No live API calls in CI** — the LLM is a hard external dependency and must be stubbed.

---

## 9. Cost & latency

| Metric | Estimate |
|---|---|
| Input tokens per call | ~6,000 (100 rows × ~50 tokens + system + schema) |
| Output tokens per call | ~1,500 (5 categories × ~300 tokens) |
| Cost per call (Sonnet 4.6) | ~$0.05 (input + output combined) |
| Calls per year | ~252 trading days |
| **Annual cost** | **~$13** |
| Latency p50 | ~8 seconds |
| Latency p99 | ~25 seconds |
| SLA budget | 120 s (16:40 → 16:42) |

Effectively free at portfolio scale. The cost ceiling for "promote to Opus" if Sonnet output quality is insufficient is ~$60/year — still trivially affordable; the question is quality, not cost.

---

## 10. Open questions / risks

| # | Risk | Mitigation |
|---|---|---|
| Q1 | LLM hallucinates a category that doesn't match the signals | Tool-schema enforcement + post-parse validation; ticker set must be subset of input; numerical claims in narrative are *labels*, never sizing inputs |
| Q2 | Category names drift day-over-day ("AI Capex" Mon, "Semis Bid" Tue, "Tech Strength" Wed) | Track category-name stability week-over-week; if Jaccard similarity of top category's tickers across consecutive days > 0.7, prefer the prior day's name for continuity (post-processing layer, not LLM responsibility) |
| Q3 | LLM is wrong about direction (says "bull" when flow is actually a hedge) | Direction is advisory only; numerical signals still drive entry side. Direction mismatches > 20%/month flag the prompt for revision |
| Q4 | Prompt injection via ticker `notes` field | Notes field is enum-restricted at the upstream signal engine; no free-text passes through |
| Q5 | API outage / rate limit at 16:40 | `on_failure="log_only"`; next-day entries fall back to raw `tilt_z` ordering with `cat=None` log lines |
| Q6 | Model upgrade (Sonnet 4.6 → 5.0) silently changes outputs | Pin model in code; capture model name in `CategoryAnalysis.model`; A/B compare before promoting a new model |
| Q7 | Anthropic key gets committed (per CC3's earlier dead-keys audit) | Key resolution via env var only; pre-commit hook scans for `sk-ant-` prefix; CI fails on key in source |

---

## 11. Decision gate

Approve this design to proceed with build (est. 2-3 days for module + tests + scheduler wiring + dry-run). Open items requiring Carlos sign-off:

1. **Model choice:** Sonnet 4.6 (recommended) vs Opus 4.6. Sonnet at $13/yr; Opus at ~$60/yr; quality delta unknown until A/B.
2. **Output write paths:** is `data/tradealgo/{asof_date}/llm_analysis.json` the right teed location given TradeAlgo historical-data gap may still be unresolved? Alternative: only write to `data/llm_categories/{asof_date}.json`.
3. **Telegram notification:** include category list, or one-liner only? Recommend one-liner; full details land in the JSON.
4. **Backfill scope:** if approved, run on the last 30 trading days of `tilt.parquet` (if available) for retrospective tagging in the Dual-Path backtest. Cost: ~$1.50.

— end —
