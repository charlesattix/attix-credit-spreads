# EXP-930-max: Real-Time Signal Pipeline Architecture

## Problem
All prior experiments run in backtest mode. For production deployment we need a real-time pipeline that: ingests streaming market data, computes features without look-ahead, runs ensemble inference with low latency, queues/deduplicates signals, and monitors its own health.

## Architecture
```
DataFeed → FeatureEngine → ModelInference → SignalQueue → LiveTradingBlueprint
                                 ↕
                          HealthMonitor
```

## Components
1. **DataFeed** — Alpaca WebSocket abstraction with replay mode for testing
2. **FeatureEngine** — computes all 23 FEATURE_COLS from rolling buffers, no look-ahead
3. **ModelInference** — ensemble inference with latency tracking, pluggable model function
4. **SignalQueue** — deduplication, expiry, FIFO delivery
5. **HealthMonitor** — stale data, model age, feature drift, latency alerts
6. **RealtimePipeline** — orchestrates all components, regime tracking, metrics

## Verified Properties
- Feature computation uses only past data (no leakage)
- Signal deduplication prevents double-trading
- Graceful degradation when data feed disconnects
- All 23 production features computed in real-time
- Sub-millisecond per-tick latency
