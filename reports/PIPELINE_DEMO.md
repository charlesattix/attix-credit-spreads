# Pipeline demo — 2026-05-27

End-to-end run: **TradeAlgo Daily Snapshot → momentum / flow / sentiment / dark-flow signals → LLM categorizer**.

## Data provenance

- **TradeAlgo snapshot** (`data/tradealgo/2026-05-27/snapshot.json`) — real prod GET response cached on disk.
- **Momentum / flow / sentiment z-scores** — computed live via Polygon (daily bars, options chain snapshot, stock trades). Rule Zero: no synthesized prices.
- **darkflow_z** — cross-sectional composite of z(multiplier) + z(log dollar_value) + z(ats_dollar_volume_pct) across the bundle's ~60 dark-flow records.
- **LLM categorization** — MOCKED in this run because `ANTHROPIC_API_KEY` is not set. The mock clusters by GICS sector as a placeholder; set the key to get real thematic categorisation.

_Runtime for stage 3 (Polygon signals × 20): 19.2s._

## Stage 3 output — signals per ticker

| # | Ticker | Cap | DarkflowZ | MomentumZ | FlowZ | SentimentZ | $Volume | Perf% |
|---|--------|-----|-----------|-----------|-------|------------|---------|-------|
| 1 | META | large | +0.56 | +0.15 | +7.05 | -3.01 | $6,903,451,023 | +4.26 |
| 2 | IREN | large | +0.44 | +2.02 | -1.76 | -6.05 | $2,703,254,930 | +9.61 |
| 3 | APP | large | +0.46 | +1.81 | +0.32 | -2.11 | $2,587,178,066 | +9.03 |
| 4 | ASTS | large | +0.21 | +3.17 | -0.49 | -8.13 | $2,255,512,352 | +4.19 |
| 5 | ONDS | medium | -0.01 | +0.70 | -1.95 | -3.90 | $542,864,142 | +8.11 |
| 6 | CIFR | medium | +0.03 | +1.92 | -3.29 | -6.41 | $431,796,547 | +8.78 |
| 7 | GM | large | +0.04 | +2.25 | -2.72 | -0.19 | $390,563,400 | +3.72 |
| 8 | TSCO | large | -0.02 | -0.04 | -1.89 | -1.36 | $305,605,921 | +2.94 |
| 9 | NIO | large | -0.27 | -0.78 | -1.84 | -14.79 | $245,753,621 | +10.27 |
| 10 | CLF | medium | -0.02 | +2.04 | -2.69 | -2.49 | $198,971,012 | +7.58 |
| 11 | KEEL | medium | -0.28 | — | — | — | $156,255,043 | +6.69 |
| 12 | ILMN | large | -0.33 | +1.59 | -2.65 | -3.77 | $140,022,501 | +3.62 |
| 13 | YPF | large | -0.03 | +1.63 | -1.52 | -2.58 | $128,222,272 | +6.80 |
| 14 | SQM | large | +0.21 | -1.04 | -2.12 | -5.16 | $100,558,886 | +7.61 |
| 15 | LUMN | medium | -0.39 | +1.58 | -2.38 | -1.54 | $83,472,356 | +7.15 |
| 16 | TVTX | medium | -0.20 | +1.09 | -0.93 | -1.89 | $73,734,087 | +7.55 |
| 17 | ADTN | small | +0.79 | +1.05 | -2.00 | -2.21 | $70,395,355 | +15.49 |
| 18 | COHU | medium | -0.29 | +1.36 | -1.48 | -3.67 | $63,806,985 | +6.76 |
| 19 | UMAC | small | -0.41 | +1.13 | -2.01 | -5.12 | $60,896,053 | +9.44 |
| 20 | SHLS | small | -0.12 | +3.00 | -1.98 | -2.17 | $59,438,780 | +12.60 |

_19/20 tickers had complete (momentum + flow + sentiment) signal sets — only those go to the LLM. No fabricated values._

## Stage 4 output — LLM categories

> **Note:** the categories below come from the mocked LLM client — they reflect a deterministic sector clustering of the real signal inputs, not a real Claude call.

### 1. Cluster: cap-large  _(bear, confidence=0.95)_

- **Tickers**: META, IREN, APP, ASTS, GM, TSCO, NIO, ILMN, YPF, SQM
- **Signals**: momentum, flow, dark_flow
- **Summary**: 10 cap-large names (avg mom=+1.08, flow=-0.76, dark=+0.13)
- **Narrative**: Mocked stub — cluster by sector. With a real Anthropic key, the LLM would produce a thematic narrative beyond GICS grouping.

### 2. Cluster: cap-medium  _(bear, confidence=0.95)_

- **Tickers**: ONDS, CIFR, CLF, LUMN, TVTX, COHU
- **Signals**: momentum, flow, dark_flow
- **Summary**: 6 cap-medium names (avg mom=+1.45, flow=-2.12, dark=-0.15)
- **Narrative**: Mocked stub — cluster by sector. With a real Anthropic key, the LLM would produce a thematic narrative beyond GICS grouping.

### 3. Cluster: cap-small  _(bear, confidence=0.80)_

- **Tickers**: ADTN, UMAC, SHLS
- **Signals**: momentum, flow, dark_flow
- **Summary**: 3 cap-small names (avg mom=+1.73, flow=-2.00, dark=+0.09)
- **Narrative**: Mocked stub — cluster by sector. With a real Anthropic key, the LLM would produce a thematic narrative beyond GICS grouping.

## Pipeline shape

```
TradeAlgo snapshot   ─┐
  (cached JSON)       │
                      ├──► parse_movement_darkflow ─► darkflow_zscores ─► top_N
                      │                                                       │
                      │                                                       ▼
Polygon daily bars  ──┼──► compute_momentum_signal ─────────────► momentum_z ─┤
Polygon options chain ┼──► compute_flow_signal  ──────────────► flow_z       ─┤
Polygon stock trades  ┘                                                       │
Polygon options IV  ───► compute_sentiment_signal ──────────► sentiment_z   ─┤
                                                                              │
                                            TickerSignal records ◄────────────┘
                                                  │
                                                  ▼
                              CategoryAnalyzer.analyze() (Claude tool_use)
                                                  │
                                                  ▼
                              {categories: [{name, tickers, direction,
                                            confidence, narrative, ...}]}
```
