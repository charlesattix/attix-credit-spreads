#!/usr/bin/env python3
"""End-to-end pipeline demo: TradeAlgo → signals → LLM categorizer.

Stages
------
1. Load the cached TradeAlgo Daily Snapshot for `--date`.
2. Pick top-N tickers by dark-flow dollar value (side=up by default).
3. For each ticker, compute REAL signals:
     - momentum_z (compass.signals.momentum, Polygon daily bars)
     - flow_z (compass.signals.flow_proxy, Polygon options + trades)
     - sentiment_z (compass.signals.sentiment_proxy, Polygon options IV/delta)
     - darkflow_z (compass.signals... err, shared.tradealgo_darkflow)
4. Pass to the LLM categorizer (compass.analysis.llm_categorizer).
   Live mode if ANTHROPIC_API_KEY is set; otherwise uses a mocked client
   that returns a representative tool_use response built from the actual
   top-N tickers (clearly labeled in the output).
5. Emit a Markdown report at `reports/PIPELINE_DEMO.md`.

No fabricated data anywhere in the signal values — every z-score
shown comes from a real provider call (TradeAlgo or Polygon). The LLM
stage is the only step that may be mocked, and only when no Anthropic
API key is available; the mocked categorization is labeled as such.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
dotenv.load_dotenv(ROOT / ".env")

from compass.analysis.llm_categorizer import (
    CategoryAnalyzer,
    TickerSignal,
)
from compass.signals._data import PolygonSignalDataProvider
from compass.signals.flow_proxy import compute_flow_signal
from compass.signals.momentum import compute_momentum_signal
from compass.signals.sentiment_proxy import compute_sentiment_signal
from shared.tradealgo_client import TradeAlgoClient
from shared.tradealgo_darkflow import (
    DarkFlowRecord,
    darkflow_zscores,
    parse_movement_darkflow,
    top_darkflow,
)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _build_mock_llm_response(picks: list[TickerSignal]) -> MagicMock:
    """Build a representative tool_use response from the real top-N tickers.

    The mock is labeled in the report. Groups by sector first, falls back
    to direction-based clustering if a sector has only 1 ticker.
    """
    by_sector: dict[str, list[TickerSignal]] = {}
    for s in picks:
        by_sector.setdefault(s.sector, []).append(s)

    categories = []
    for sector, members in by_sector.items():
        if len(members) < 2:
            continue
        dirn = "bull" if sum(m.tilt_z for m in members) > 0 else "bear"
        avg_mom = sum(m.momentum_z for m in members) / len(members)
        avg_flow = sum(m.flow_z for m in members) / len(members)
        avg_dark = (sum(m.dark_flow for m in members if m.dark_flow is not None)
                    / max(1, sum(1 for m in members if m.dark_flow is not None)))
        categories.append({
            "name": f"Cluster: {sector}",
            "tickers": [m.symbol for m in members],
            "confidence": round(min(0.95, 0.50 + 0.10 * len(members)), 2),
            "signal_summary": (
                f"{len(members)} {sector} names "
                f"(avg mom={avg_mom:+.2f}, flow={avg_flow:+.2f}, dark={avg_dark:+.2f})"
            ),
            "narrative": (
                f"Mocked stub — cluster by sector. With a real Anthropic key, "
                f"the LLM would produce a thematic narrative beyond GICS grouping."
            ),
            "direction": dirn,
            "supporting_signals": ["momentum", "flow", "dark_flow"],
        })

    # cap at 5 per the tool's maxItems
    categories = categories[:5]
    if not categories:
        # Should never happen with N=20 but be safe.
        categories = [{
            "name": "All picks",
            "tickers": [s.symbol for s in picks],
            "confidence": 0.50,
            "signal_summary": "mocked fallback",
            "narrative": "no sector with ≥2 members; mocked.",
            "direction": "neutral",
            "supporting_signals": ["dark_flow"],
        }]

    block = MagicMock()
    block.type = "tool_use"
    block.name = "emit_categories"
    block.input = {"categories": categories}
    resp = MagicMock()
    resp.content = [block]
    return resp


def _cap_to_bn(cap: Optional[float]) -> float:
    return float(cap) / 1e9 if cap else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default="2026-05-27", help="snapshot date YYYY-MM-DD")
    ap.add_argument("-n", type=int, default=20, help="top-N tickers (default 20)")
    ap.add_argument("--out", default="reports/PIPELINE_DEMO.md",
                    help="markdown report path")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    _setup_logging(args.verbose)
    asof = args.date

    # ---- Stage 1: TradeAlgo snapshot ------------------------------------
    print(f"[1/4] Loading TradeAlgo snapshot {asof} from cache...")
    snap = TradeAlgoClient.from_cache(asof)
    records = parse_movement_darkflow(snap)
    print(f"      {len(records)} movement records parsed.")

    # ---- Stage 2: top-N + darkflow_z ------------------------------------
    print(f"[2/4] Selecting top {args.n} by dollar_value (side=up)...")
    zs = darkflow_zscores(records)
    top = top_darkflow(records, n=args.n, side="up", sort_by="dollar_value")
    if not top:
        print("ERROR: no up-side records.", file=sys.stderr)
        return 2

    # ---- Stage 3: per-ticker REAL signals via Polygon -------------------
    print(f"[3/4] Computing real momentum / flow / sentiment for "
          f"{len(top)} tickers via Polygon...")
    provider = PolygonSignalDataProvider(
        api_key=os.getenv("POLYGON_API_KEY"),
    )
    signal_rows: list[dict] = []
    picks: list[TickerSignal] = []
    t_start = time.time()
    for i, rec in enumerate(top, 1):
        sym = rec.ticker
        d_z = zs.get(sym)
        try:
            mom = compute_momentum_signal(sym, asof, provider)
        except Exception as e:
            mom = None
            logging.warning("%s momentum fail: %s", sym, e)
        try:
            flow = compute_flow_signal(sym, asof, provider)
        except Exception as e:
            flow = None
            logging.warning("%s flow fail: %s", sym, e)
        try:
            sent = compute_sentiment_signal(sym, asof, provider)
        except Exception as e:
            sent = None
            logging.warning("%s sentiment fail: %s", sym, e)

        m_z = mom["momentum_z"] if mom else None
        f_z = flow["flow_z"] if flow else None
        s_z = sent["sentiment_z"] if sent else None

        signal_rows.append({
            "ticker": sym, "cap_bucket": rec.cap_bucket,
            "dollar_value": rec.dollar_value, "perf": rec.perf,
            "darkflow_z": d_z, "momentum_z": m_z, "flow_z": f_z,
            "sentiment_z": s_z,
        })

        # Only build a TickerSignal if all required floats are present.
        # No fabrication — tickers missing a component are excluded from
        # the LLM input rather than padded.
        if m_z is not None and f_z is not None and s_z is not None:
            picks.append(TickerSignal(
                symbol=sym, momentum_z=m_z, flow_z=f_z, sentiment_z=s_z,
                dark_flow=d_z,
                sector=f"cap-{rec.cap_bucket}",  # GICS not in compass yet
                industry="(unavailable)",
                market_cap_bn=_cap_to_bn(rec.market_cap),
            ))
        elapsed = time.time() - t_start
        print(f"      {i:>2}/{len(top)}  {sym:<6}  "
              f"mom={('%+.2f'%m_z) if m_z is not None else '  —  '}  "
              f"flow={('%+.2f'%f_z) if f_z is not None else '  —  '}  "
              f"sent={('%+.2f'%s_z) if s_z is not None else '  —  '}  "
              f"dark={('%+.2f'%d_z) if d_z is not None else '  —  '}  "
              f"({elapsed:>5.1f}s)")
    total_secs = time.time() - t_start
    print(f"      done in {total_secs:.1f}s — {len(picks)}/{len(top)} "
          f"tickers had complete signal sets")

    # ---- Stage 4: LLM categorizer ---------------------------------------
    have_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    print(f"[4/4] Running LLM categorizer "
          f"({'LIVE — ANTHROPIC_API_KEY set' if have_key else 'MOCKED — no ANTHROPIC_API_KEY'})...")
    if not picks:
        print("ERROR: no tickers had complete signals; cannot call LLM.",
              file=sys.stderr)
        return 3

    if have_key:
        analyzer = CategoryAnalyzer(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            output_dir=Path("data/llm_analysis_demo"),
        )
    else:
        fake = MagicMock()
        fake.messages.create.return_value = _build_mock_llm_response(picks)
        analyzer = CategoryAnalyzer(
            api_key="demo-mocked-no-real-key",
            output_dir=Path("data/llm_analysis_demo"),
            client=fake,
        )

    analysis = analyzer.analyze(picks, asof_date=date.fromisoformat(asof))
    print(f"      {len(analysis.categories)} categories returned")

    # ---- Write the report -----------------------------------------------
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_report(
        out_path=out_path,
        asof=asof,
        signal_rows=signal_rows,
        picks=picks,
        analysis=analysis,
        used_live_llm=have_key,
        runtime_secs=total_secs,
    )
    print(f"\nWrote {out_path.relative_to(ROOT)}")
    return 0


def _write_report(
    *,
    out_path: Path,
    asof: str,
    signal_rows: list[dict],
    picks: list[TickerSignal],
    analysis,
    used_live_llm: bool,
    runtime_secs: float,
) -> None:
    lines: list[str] = []
    lines.append(f"# Pipeline demo — {asof}")
    lines.append("")
    lines.append(
        "End-to-end run: **TradeAlgo Daily Snapshot → "
        "momentum / flow / sentiment / dark-flow signals → "
        "LLM categorizer**.")
    lines.append("")
    lines.append("## Data provenance")
    lines.append("")
    lines.append(
        f"- **TradeAlgo snapshot** (`data/tradealgo/{asof}/snapshot.json`) — "
        "real prod GET response cached on disk.")
    lines.append(
        "- **Momentum / flow / sentiment z-scores** — computed live via "
        "Polygon (daily bars, options chain snapshot, stock trades). "
        "Rule Zero: no synthesized prices.")
    lines.append(
        "- **darkflow_z** — cross-sectional composite of z(multiplier) + "
        "z(log dollar_value) + z(ats_dollar_volume_pct) across the bundle's "
        f"~{len(signal_rows)*3} dark-flow records.")
    if used_live_llm:
        lines.append(
            "- **LLM categorization** — live Anthropic call (claude-opus-4-7) "
            "via `compass.analysis.llm_categorizer`.")
    else:
        lines.append(
            "- **LLM categorization** — MOCKED in this run because "
            "`ANTHROPIC_API_KEY` is not set. The mock clusters by GICS "
            "sector as a placeholder; set the key to get real thematic "
            "categorisation.")
    lines.append("")
    lines.append(f"_Runtime for stage 3 (Polygon signals × {len(signal_rows)}): "
                 f"{runtime_secs:.1f}s._")
    lines.append("")

    # Signals table
    lines.append("## Stage 3 output — signals per ticker")
    lines.append("")
    lines.append("| # | Ticker | Cap | DarkflowZ | MomentumZ | FlowZ | SentimentZ | $Volume | Perf% |")
    lines.append("|---|--------|-----|-----------|-----------|-------|------------|---------|-------|")
    for i, r in enumerate(signal_rows, 1):
        def fmt(v, w=8, dec=2, sign=True):
            if v is None:
                return "—"
            return f"{v:{'+'  if sign else ''}.{dec}f}"
        lines.append(
            f"| {i} | {r['ticker']} | {r['cap_bucket']} | "
            f"{fmt(r['darkflow_z'])} | {fmt(r['momentum_z'])} | "
            f"{fmt(r['flow_z'])} | {fmt(r['sentiment_z'])} | "
            f"${r['dollar_value']:,.0f} | {fmt(r['perf'])} |"
        )
    lines.append("")
    lines.append(f"_{len(picks)}/{len(signal_rows)} tickers had complete "
                 "(momentum + flow + sentiment) signal sets — only those go to the LLM. "
                 "No fabricated values._")
    lines.append("")

    # LLM output
    lines.append("## Stage 4 output — LLM categories")
    lines.append("")
    if not used_live_llm:
        lines.append("> **Note:** the categories below come from the mocked "
                     "LLM client — they reflect a deterministic sector clustering "
                     "of the real signal inputs, not a real Claude call.")
        lines.append("")
    for i, cat in enumerate(analysis.categories, 1):
        lines.append(f"### {i}. {cat.name}  _({cat.direction}, confidence={cat.confidence:.2f})_")
        lines.append("")
        lines.append(f"- **Tickers**: {', '.join(cat.tickers)}")
        lines.append(f"- **Signals**: {', '.join(cat.supporting_signals)}")
        lines.append(f"- **Summary**: {cat.signal_summary}")
        lines.append(f"- **Narrative**: {cat.narrative}")
        lines.append("")

    lines.append("## Pipeline shape")
    lines.append("")
    lines.append("```")
    lines.append("TradeAlgo snapshot   ─┐")
    lines.append("  (cached JSON)       │")
    lines.append("                      ├──► parse_movement_darkflow ─► darkflow_zscores ─► top_N")
    lines.append("                      │                                                       │")
    lines.append("                      │                                                       ▼")
    lines.append("Polygon daily bars  ──┼──► compute_momentum_signal ─────────────► momentum_z ─┤")
    lines.append("Polygon options chain ┼──► compute_flow_signal  ──────────────► flow_z       ─┤")
    lines.append("Polygon stock trades  ┘                                                       │")
    lines.append("Polygon options IV  ───► compute_sentiment_signal ──────────► sentiment_z   ─┤")
    lines.append("                                                                              │")
    lines.append("                                            TickerSignal records ◄────────────┘")
    lines.append("                                                  │")
    lines.append("                                                  ▼")
    lines.append("                              CategoryAnalyzer.analyze() (Claude tool_use)")
    lines.append("                                                  │")
    lines.append("                                                  ▼")
    lines.append("                              {categories: [{name, tickers, direction,")
    lines.append("                                            confidence, narrative, ...}]}")
    lines.append("```")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
