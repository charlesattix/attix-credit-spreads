# Pipeline demo — 2026-05-27

End-to-end run: **TradeAlgo Daily Snapshot → momentum / flow / sentiment / dark-flow signals → LLM categorizer**.

## Data provenance

- **TradeAlgo snapshot** (`data/tradealgo/2026-05-27/snapshot.json`) — real prod GET response cached on disk.
- **Momentum / flow / sentiment z-scores** — computed live via Polygon (daily bars, options chain snapshot, stock trades). Rule Zero: no synthesized prices.
- **darkflow_z** — cross-sectional composite of z(multiplier) + z(log dollar_value) + z(ats_dollar_volume_pct) across the bundle's ~60 dark-flow records.
- **LLM categorization** — live Anthropic call (claude-opus-4-7) via `compass.analysis.llm_categorizer`.

_Runtime for stage 3 (Polygon signals × 20): 18.7s._

## Stage 3 output — signals per ticker

| # | Ticker | Cap | DarkflowZ | MomentumZ | FlowZ | SentimentZ | $Volume | Perf% |
|---|--------|-----|-----------|-----------|-------|------------|---------|-------|
| 1 | META | large | +0.56 | +0.15 | +7.05 | -3.02 | $6,903,451,023 | +4.26 |
| 2 | IREN | large | +0.44 | +2.02 | -1.76 | -6.06 | $2,703,254,930 | +9.61 |
| 3 | APP | large | +0.46 | +1.81 | +0.32 | -2.11 | $2,587,178,066 | +9.03 |
| 4 | ASTS | large | +0.21 | +3.17 | -0.49 | -8.14 | $2,255,512,352 | +4.19 |
| 5 | ONDS | medium | -0.01 | +0.70 | -1.95 | -3.87 | $542,864,142 | +8.11 |
| 6 | CIFR | medium | +0.03 | +1.92 | -3.29 | -6.41 | $431,796,547 | +8.78 |
| 7 | GM | large | +0.04 | +2.25 | -2.72 | -0.20 | $390,563,400 | +3.72 |
| 8 | TSCO | large | -0.02 | -0.04 | -1.89 | -1.36 | $305,605,921 | +2.94 |
| 9 | NIO | large | -0.27 | -0.78 | -1.84 | -14.81 | $245,753,621 | +10.27 |
| 10 | CLF | medium | -0.02 | +2.04 | -2.69 | -2.49 | $198,971,012 | +7.58 |
| 11 | KEEL | medium | -0.28 | — | — | — | $156,255,043 | +6.69 |
| 12 | ILMN | large | -0.33 | +1.59 | -2.65 | -3.77 | $140,022,501 | +3.62 |
| 13 | YPF | large | -0.03 | +1.63 | -1.52 | -2.58 | $128,222,272 | +6.80 |
| 14 | SQM | large | +0.21 | -1.04 | -2.12 | -5.16 | $100,558,886 | +7.61 |
| 15 | LUMN | medium | -0.39 | +1.58 | -2.38 | -1.54 | $83,472,356 | +7.15 |
| 16 | TVTX | medium | -0.20 | +1.09 | -0.93 | -1.87 | $73,734,087 | +7.55 |
| 17 | ADTN | small | +0.79 | +1.05 | -2.00 | -2.21 | $70,395,355 | +15.49 |
| 18 | COHU | medium | -0.29 | +1.36 | -1.48 | -3.67 | $63,806,985 | +6.76 |
| 19 | UMAC | small | -0.41 | +1.13 | -2.01 | -5.12 | $60,896,053 | +9.44 |
| 20 | SHLS | small | -0.12 | +3.00 | -1.98 | -2.17 | $59,438,780 | +12.60 |

_19/20 tickers had complete (momentum + flow + sentiment) signal sets — only those go to the LLM. No fabricated values._

## Stage 4 output — LLM categories

### 1. AI/Compute & Connectivity Capex Leaders  _(bull, confidence=0.62)_

- **Tickers**: META, APP, ASTS, IREN
- **Signals**: momentum, flow, dark_flow, sentiment
- **Summary**: Strong momentum and positive dark_flow despite deeply negative sentiment_z; META flow_z +7.0 is the standout.
- **Narrative**: Mega-cap and adjacent compute/connectivity names show institutional accumulation (META flow_z +7.05, IREN/ASTS/APP dark_flow positive) and solid price momentum, even as sentiment_z is sharply negative (-2 to -8). The pattern — buying into bearish chatter — is classic smart-money AI capex positioning across social, mobile ad-tech, satellite broadband, and crypto/AI hosting.

### 2. Crypto Miner / HPC Hosting Squeeze  _(bull, confidence=0.45)_

- **Tickers**: IREN, CIFR, CLF
- **Signals**: momentum, sentiment
- **Summary**: High momentum_z (1.9-2.0) paired with very negative sentiment_z (-2.5 to -6.4) and negative flow_z.
- **Narrative**: Low-conviction cluster: IREN and CIFR (miners pivoting to HPC) show strong upside momentum against capitulatory sentiment readings, suggesting a short-covering / contrarian setup. CLF is included as a high-beta cyclical exhibiting the same momentum-up / sentiment-down divergence, though sector fit is loose. Flag: confidence <0.5 due to mixed flow signals.

### 3. EV / Battery Materials Capitulation  _(bear, confidence=0.55)_

- **Tickers**: NIO, SQM, GM
- **Signals**: flow, sentiment
- **Summary**: Uniformly negative flow_z and sentiment_z; NIO sentiment_z -14.8 is the tape's worst.
- **Narrative**: EV ecosystem under coordinated distribution: NIO and SQM (lithium) print extreme negative sentiment with persistent outflows. GM shares the negative flow/sentiment footprint despite positive momentum_z, hinting the rally is being sold into. Bearish read on EV demand and battery-input pricing.

### 4. Small-Cap Momentum w/ Hidden Accumulation  _(bull, confidence=0.50)_

- **Tickers**: ADTN, SHLS, TVTX, COHU, UMAC
- **Signals**: momentum, dark_flow
- **Summary**: Positive momentum_z (1.0-3.0) with negative retail flow but mixed/positive dark_flow on ADTN (+0.79).
- **Narrative**: Cluster of sub-$3B names rallying on momentum while visible flow is negative — ADTN's dark_flow +0.786 is the cleanest tell of off-exchange accumulation. SHLS (solar), COHU (semi-cap), TVTX (biotech), UMAC (drones) span sectors but share the same micro-cap momentum + quiet-bid fingerprint. Borderline conviction given thin liquidity risk.

### 5. Beaten-Down Mid-Caps, No Bid  _(bear, confidence=0.42)_

- **Tickers**: ILMN, LUMN, YPF, TSCO
- **Signals**: flow, sentiment, dark_flow
- **Summary**: Negative flow_z, negative sentiment_z, and negative dark_flow on ILMN/LUMN — outflows across visible and hidden venues.
- **Narrative**: Low-conviction bearish bucket: heterogeneous mid-caps (genomics, telecom, Argentine energy, retail) sharing a profile of negative flow, negative sentiment, and in ILMN/LUMN's case negative dark_flow signaling no institutional defense. Sector mix is too wide for a clean thesis — flagged sub-0.5 confidence — but the distribution signature is consistent.

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
