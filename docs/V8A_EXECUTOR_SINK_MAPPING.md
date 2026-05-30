# V8A → Executor Sink — Interface Mapping (Phase 1)

Drop-in replacement plan for `AlpacaOrderSink`, routing V8A's VRP intents
through the Executor REST service (which talks to IBKR paper) instead of the
Alpaca SDK.

## OrderSink contract V8A already expects

`compass/live/vrp_contracts.py`:

```python
class OrderSink(Protocol):
    def submit(self, intent: OrderIntent) -> Dict[str, object]: ...
```

`OrderIntent` (broker-agnostic) carries:
- `stream` (e.g. `"exp1220"`), `symbol` (e.g. `"SPY"`), `structure`
  (`"bull_put" | "bear_call" | …`), `contracts`, `est_credit`, `est_max_loss`,
  `rationale`, `meta`, and a tuple of `OrderLeg(side, sec_type, symbol, qty,
  strike, expiration, right)`.

`AlpacaOrderSink.submit(intent)` (`compass/live/vrp_sinks.py`):
- Asserts `structure ∈ {bull_put, bear_call}` (PR-B scope).
- Pulls `short_strike` (`side="sell"`), `long_strike` (`side="buy"`),
  `expiration` from the legs.
- Computes a deterministic `client_order_id`:
  `vrp-{stream}-{symbol}-{exp}-{shortK}-{longK}` — e.g.
  `vrp-exp1220-SPY-2026-06-26-717-712`. **Idempotency seam** for retries +
  PnL attribution.
- Calls `AlpacaProvider.submit_credit_spread(ticker, short_strike,
  long_strike, expiration, spread_type, contracts, limit_price=est_credit,
  client_order_id=coid)` → dict `{status, order_id, client_order_id,
  order_status, ticker, spread_type, short_strike, long_strike, short_symbol,
  long_symbol, contracts, limit_price, submitted_at, actual_expiration, …}`.

## Executor endpoint we hit

`POST /v1/orders/spread` (auth `X-API-Key`).

### Body — `SpreadOrderRequest`
```jsonc
{
  "account_id": "ibkr_tafintech-p11-paper",   // EXECUTOR_ACCOUNT_ID
  "account_type": "paper",                    // EXECUTOR_ACCOUNT_TYPE (default paper)
  "strategy": "bull_put_spread",              // see structure → strategy table below
  "legs": [
    {
      "symbol": "SPY",                        // intent.symbol
      "option_type": "put",                   // bull_put → put | bear_call → call
      "strike": 717.0,                        // sell leg strike
      "expiration": "2026-06-26",
      "side": "sell_to_open",
      "quantity": <intent.contracts>
    },
    {
      "symbol": "SPY",
      "option_type": "put",
      "strike": 712.0,                        // buy leg strike
      "expiration": "2026-06-26",
      "side": "buy_to_open",
      "quantity": <intent.contracts>
    }
  ],
  "order_type": "limit",                      // "market" when est_credit is None
  "net_credit": <intent.est_credit>,          // positive = credit received
  "time_in_force": "day",
  "source": {                                 // optional but useful — attribution
    "model": "vrp_v8a",
    "signal_id": <intent.stream>,
    "metadata": {"stream": <stream>, "rationale": <intent.rationale>}
  },
  "idempotency_key": "<sanitized coid>"       // pattern ^[a-zA-Z0-9_-]+$, max 255
}
```

### Field mapping

| OrderIntent field            | SpreadOrderRequest field                          |
|------------------------------|---------------------------------------------------|
| `intent.symbol`              | `legs[*].symbol`                                  |
| `intent.structure`           | `strategy` + `option_type` (see table)            |
| `intent.contracts`           | `legs[*].quantity`                                |
| `intent.est_credit` (or None)| `net_credit` (or `null`) + `order_type`           |
| sell-side `leg.strike`       | `legs[0].strike` + `side="sell_to_open"`          |
| buy-side `leg.strike`        | `legs[1].strike` + `side="buy_to_open"`           |
| any `leg.expiration`         | `legs[*].expiration`                              |
| `stream_client_order_id()`   | `idempotency_key` (sanitized: `.` → `_`)          |

Structure → strategy / option_type:

| `intent.structure` | `strategy`         | `option_type` |
|--------------------|--------------------|---------------|
| `bull_put`         | `bull_put_spread`  | `put`         |
| `bear_call`        | `bear_call_spread` | `call`        |

(Future PR-D structures — `long_short_shares`, `rel_value_vol`,
`etf_future_basis` — raise `NotImplementedError`, same as `AlpacaOrderSink`.)

### Response — `OrderResponse`
```jsonc
{ "success": true, "order_id": "…", "broker_order_id": "…",
  "message": "…", "status": "open"|"filled"|…,
  "symbol": "SPY", "quantity": N, "filled_quantity": 0,
  "average_fill_price": null, "commission": null,
  "timestamp": "ISO8601", "leg_con_ids": [..] }
```

`ExecutorOrderSink.submit` normalizes to the same dict shape callers of
`AlpacaOrderSink` already accept (adding `stream` + `client_order_id` so the
runner log line at `vrp_runner.py:132` keeps printing `placed=N streams=…`).

## Other executor endpoints we wire

| Sink method                  | Executor call                                                      |
|------------------------------|--------------------------------------------------------------------|
| `cancel_order(order_id)`     | `DELETE /v1/orders/{order_id}?account_id=…`                        |
| `get_status(order_id)`       | `GET /v1/orders/{order_id}/status?account_id=…`                    |
| `get_positions()`            | `GET /v1/portfolio/positions?account_id=…`                         |
| `get_balance()`              | `GET /v1/portfolio/balance?account_id=…` → `{total_equity, cash, buying_power, …}` |
| `set_callback_url(url)`      | `PATCH /v1/gateways/accounts/{account_id}/callback-url`            |

## Webhook fill payload (executor → V8A worker)

Source: `executor/order_events/source_notifier.py::notify_source` — payload
is fixed and matches the registered account `callback_url`:

```jsonc
{
  "broker_order_id": "12345",
  "status": "filled" | "partially_filled",
  "filled_quantity": 5,
  "avg_fill_price": 0.97,
  "remaining_quantity": 0,
  "symbol": "SPY",
  "side": "sell" | "buy",
  "event_data": { /* raw broker event */ }
}
```

Retry: up to `settings.resilience.callback_retry.max_attempts` with
exponential backoff. Receiver MUST return a 2xx JSON body or executor logs
retry/DLQ.

## Wire points in V8A

- `compass/live/vrp_runner.py::run_vrp_cycle` chooses sink today (line 121).
  Phase 2b: dispatch on `SINK_TYPE` env var (`alpaca` default, `executor`
  opt-in) → import + construct `ExecutorOrderSink` instead of
  `AlpacaOrderSink`. Equity for sizing flips from
  `alpaca_provider.get_account()['equity']` to
  `ExecutorOrderSink.get_balance()['total_equity']`.
- `main.py:1219` scheduler check is unchanged; only the runner internals shift.

## Env vars (Phase 2)

| Var                  | Default                        | Meaning                                  |
|----------------------|--------------------------------|------------------------------------------|
| `SINK_TYPE`          | `alpaca`                       | `alpaca` or `executor` — picks the sink  |
| `EXECUTOR_BASE_URL`  | `http://localhost:38002`       | Executor REST root                       |
| `EXECUTOR_API_KEY`   | _required if SINK_TYPE=executor_ | sent as `X-API-Key`                    |
| `EXECUTOR_ACCOUNT_ID`| `ibkr_tafintech-p11-paper`     | passed to all executor calls             |
| `EXECUTOR_ACCOUNT_TYPE` | `paper`                     | `paper` or `live`                        |
| `EXECUTOR_TIMEOUT_S` | `15.0`                         | HTTP timeout                             |
| `EXECUTOR_WEBHOOK_URL` | _none_                       | If set, registered via PATCH callback-url at boot |
