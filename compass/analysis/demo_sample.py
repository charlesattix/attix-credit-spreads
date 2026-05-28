"""End-to-end demo of compass.analysis.llm_categorizer.

Runs against a mocked Anthropic client so the demo is reproducible and
doesn't require an API key. The mock is fed a realistic tool_use response
shaped exactly like what claude-opus-4-7 would return for the input
signals below.

Usage:
    python3 -m compass.analysis.demo_sample
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from compass.analysis.llm_categorizer import CategoryAnalyzer, TickerSignal

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


SAMPLE_SIGNALS: list[TickerSignal] = [
    # AI infrastructure cluster
    TickerSignal("NVDA",  2.84,  2.61,  1.92,  3.45, "Information Technology", "Semiconductors", 3120.0),
    TickerSignal("AVGO",  2.41,  1.97,  1.55,  2.10, "Information Technology", "Semiconductors", 840.0),
    TickerSignal("AMD",   2.18,  2.05,  1.83,  1.78, "Information Technology", "Semiconductors", 330.0),
    TickerSignal("MU",    2.03,  1.78,  1.61,  1.55, "Information Technology", "Semiconductors", 145.0),
    TickerSignal("VRT",   1.94,  1.62,  1.41,  1.22, "Industrials",            "Electrical Components", 32.0),
    TickerSignal("SMCI",  1.81,  1.55,  2.10,  1.94, "Information Technology", "Tech Hardware", 42.0,
                 notes="REGIME_GATE_ON"),
    TickerSignal("ANET",  1.72,  1.43,  1.28,  1.10, "Information Technology", "Comm Equipment", 110.0),
    TickerSignal("DLR",   1.55,  1.31,  1.04,  0.95, "Real Estate",            "Data-Center REITs", 50.0),
    TickerSignal("EQIX",  1.50,  1.22,  1.01,  0.88, "Real Estate",            "Data-Center REITs", 78.0),

    # Defensive rotation cluster
    TickerSignal("KO",   -1.20, -0.40, -0.85,  None, "Consumer Staples", "Beverages",      280.0),
    TickerSignal("PG",   -0.95, -0.30, -0.70,  None, "Consumer Staples", "Household",      380.0),
    TickerSignal("WMT",  -0.88, -0.22, -0.55,  None, "Consumer Staples", "Discount Retail", 540.0),
    TickerSignal("COST", -0.75, -0.18, -0.42,  None, "Consumer Staples", "Discount Retail", 380.0,
                 notes="EARN_THIS_WK"),
    TickerSignal("JNJ",  -1.05, -0.35, -0.78,  None, "Health Care",      "Pharma",          410.0),

    # Energy breakout cluster
    TickerSignal("XOM",   1.62,  1.95,  1.24,  1.41, "Energy", "Integrated Oil & Gas", 510.0),
    TickerSignal("CVX",   1.58,  1.88,  1.19,  1.35, "Energy", "Integrated Oil & Gas", 290.0),
    TickerSignal("SLB",   1.49,  1.66,  1.08,  1.28, "Energy", "Oil Services",          61.0),
    TickerSignal("OXY",   1.41,  1.55,  1.02,  1.18, "Energy", "Exploration",           48.0),

    # Misc tail (won't make a cluster)
    TickerSignal("TSLA",  0.95,  0.62,  0.41,  1.05, "Consumer Discretionary", "Auto Makers", 820.0),
    TickerSignal("BA",   -0.42, -0.21, -0.18,  None, "Industrials",            "Aerospace",    140.0,
                 notes="THIN_LIQUIDITY"),
]


_MOCK_TOOL_RESPONSE = {
    "categories": [
        {
            "name": "AI Infrastructure Capex",
            "tickers": ["NVDA", "AVGO", "AMD", "MU", "VRT", "SMCI", "ANET", "DLR", "EQIX"],
            "confidence": 0.88,
            "signal_summary": (
                "9 names with momentum_z > 1.5 and flow_z > 1.2, "
                "spanning semis, networking, thermal/power, and data-center REITs."
            ),
            "narrative": (
                "Strong cross-sector coherence — the AI capex story is "
                "expressing across the entire supply chain, not just chip "
                "designers. Semis (NVDA/AVGO/AMD/MU), networking (ANET), "
                "thermal/power (VRT/SMCI), and data-center REITs (DLR/EQIX) "
                "all show momentum + flow + sentiment three-way agreement. "
                "Highest-confidence theme of the day."
            ),
            "direction": "bull",
            "supporting_signals": ["momentum", "flow", "sentiment", "dark_flow"],
        },
        {
            "name": "Energy Breakout",
            "tickers": ["XOM", "CVX", "SLB", "OXY"],
            "confidence": 0.74,
            "signal_summary": (
                "4 Energy names with momentum_z 1.4-1.6 and the largest "
                "flow_z cluster outside Tech (1.5-2.0)."
            ),
            "narrative": (
                "GICS-coherent (all four are pure Energy), with the flow signal "
                "leading momentum — usually a sign of new positioning rather than "
                "trend continuation. Three-way signal agreement; moderate confidence "
                "given the cluster is small."
            ),
            "direction": "bull",
            "supporting_signals": ["momentum", "flow", "sentiment"],
        },
        {
            "name": "Defensive Rotation",
            "tickers": ["KO", "PG", "WMT", "COST", "JNJ"],
            "confidence": 0.66,
            "signal_summary": (
                "5 staples + pharma names with momentum_z -0.75 to -1.20 and "
                "negative sentiment_z; flow is weakly negative but coherent."
            ),
            "narrative": (
                "Classic defensive-rotation signature — coordinated weakness "
                "across Consumer Staples (KO/PG/WMT/COST) plus a Health Care "
                "anchor (JNJ). The negative direction here means 'these names "
                "are being sold,' which is the natural counterpart to the AI "
                "capex bid in category 1 (risk-on flow leaving defensives)."
            ),
            "direction": "bear",
            "supporting_signals": ["momentum", "sentiment"],
        },
    ]
}


def _build_mock_response():
    """Construct an object that quacks like an Anthropic Messages response."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "emit_categories"
    block.input = _MOCK_TOOL_RESPONSE

    resp = MagicMock()
    resp.content = [block]
    return resp


def main(output_dir: Path = Path("data/llm_analysis")) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _build_mock_response()

    analyzer = CategoryAnalyzer(
        api_key="demo-mocked-no-real-key",
        output_dir=output_dir,
        client=fake_client,
    )

    asof = date(2026, 5, 27)
    print(f"\n=== INPUT: {len(SAMPLE_SIGNALS)} ticker signals for {asof} ===")
    for s in SAMPLE_SIGNALS[:5]:
        print(f"  {s.symbol:6s}  mom={s.momentum_z:+.2f}  flow={s.flow_z:+.2f}  "
              f"sent={s.sentiment_z:+.2f}  sector={s.sector}")
    print(f"  ... ({len(SAMPLE_SIGNALS) - 5} more)")

    analysis = analyzer.analyze(SAMPLE_SIGNALS, asof_date=asof)

    print(f"\n=== OUTPUT: {len(analysis.categories)} categories ===")
    for i, c in enumerate(analysis.categories, 1):
        print(f"\n  [{i}] {c.name}  ({c.direction}, confidence={c.confidence:.2f})")
        print(f"      tickers: {', '.join(c.tickers)}")
        print(f"      summary: {c.signal_summary}")
        print(f"      signals: {', '.join(c.supporting_signals)}")

    print(f"\n=== FILES WRITTEN ===")
    out = output_dir / f"{asof.isoformat()}.json"
    meta = output_dir / f"{asof.isoformat()}.meta.json"
    raw = output_dir / "raw"
    print(f"  output:    {out}")
    print(f"  meta:      {meta}")
    print(f"  raw audit: {raw}/")
    print()


if __name__ == "__main__":
    main()
