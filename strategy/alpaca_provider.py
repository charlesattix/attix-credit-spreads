"""
Alpaca Paper Trading Provider
Submits real option spread orders to Alpaca paper trading API.
Uses alpaca-py SDK for multi-leg option orders.
"""

import functools
import logging
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    ContractType,
    OrderClass,
    OrderSide,
    PositionIntent,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    GetOptionContractsRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    OptionLegRequest,
)

from shared.circuit_breaker import CircuitBreaker, CircuitOpenError
from shared.exceptions import ProviderError

logger = logging.getLogger(__name__)


_NO_RETRY = (ValueError, TypeError, CircuitOpenError)

# HTTP 4xx status codes that indicate a client error that will NEVER succeed on retry.
# These include: invalid symbol (422), insufficient buying power (403/422),
# account not approved for options (403), duplicate client_order_id (409).
# NOTE: 429 (Too Many Requests) is intentionally absent — it IS retryable with backoff.
_NON_RETRYABLE_HTTP_PREFIXES = ("400", "401", "403", "404", "409", "422")

# How long to wait when we get a 429 with no Retry-After header
_DEFAULT_RATE_LIMIT_SLEEP = 30.0


def _normalize_order_status(status) -> str:
    """Normalize Alpaca order status to a lowercase bare string.

    alpaca-py enums serialize as "OrderStatus.FILLED" (or similar). This function
    strips the "OrderStatus." prefix and lowercases the result so downstream
    comparisons like ``status == "filled"`` always work regardless of SDK version.
    """
    if status is None:
        return ""
    s = str(status)
    if "." in s:
        s = s.split(".", 1)[1]
    return s.lower()


def _is_non_retryable(exc: Exception) -> bool:
    """Return True if this exception represents a client error that won't succeed on retry."""
    # Try to detect HTTP status from alpaca-py APIError or similar SDK exceptions.
    # alpaca-py raises exceptions whose string representation includes the HTTP status code.
    exc_str = str(exc)
    for prefix in _NON_RETRYABLE_HTTP_PREFIXES:
        if prefix in exc_str:
            return True
    # Check exception class name for known non-retryable patterns
    cls_name = type(exc).__name__
    return "Forbidden" in cls_name or "Unauthorized" in cls_name or "NotFound" in cls_name


def _is_rate_limited(exc: Exception) -> bool:
    """Return True if this is an HTTP 429 Too Many Requests error."""
    exc_str = str(exc)
    return "429" in exc_str or "too many requests" in exc_str.lower()


def _get_retry_after_seconds(exc: Exception, default: float = _DEFAULT_RATE_LIMIT_SLEEP) -> float:
    """Extract Retry-After seconds from a 429 exception message, or return the default."""
    import re
    m = re.search(r'retry.after[:\s]+(\d+\.?\d*)', str(exc), re.IGNORECASE)
    if m:
        return float(m.group(1))
    return default


def _retry_with_backoff(max_retries: int = 2, base_delay: float = 1.0):
    """Decorator that retries a method with exponential backoff + jitter.

    - Non-retryable 4xx errors (400, 401, 403, 404, 409, 422): re-raised immediately.
    - 429 Too Many Requests: sleeps for Retry-After (or default 30s) then retries.
    - 5xx / network errors: exponential backoff with jitter.
    - Exceptions in ``_NO_RETRY``: re-raised immediately without retry.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except _NO_RETRY:
                    raise
                except Exception as exc:
                    if _is_non_retryable(exc):
                        logger.error(
                            "%s: non-retryable error (client error, will not retry): %s",
                            func.__name__, exc,
                        )
                        raise
                    last_exc = exc
                    if attempt < max_retries:
                        if _is_rate_limited(exc):
                            # Respect Retry-After header; don't use exponential backoff
                            delay = _get_retry_after_seconds(exc)
                            logger.warning(
                                "%s: rate limited (429) — sleeping %.0fs before retry "
                                "(attempt %d/%d)",
                                func.__name__, delay, attempt + 1, max_retries,
                            )
                        else:
                            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
                            logger.warning(
                                "%s attempt %d failed: %s. Retrying in %.2fs...",
                                func.__name__, attempt + 1, exc, delay,
                            )
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


class AlpacaProvider:
    """Submit credit spread orders to Alpaca paper trading."""

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = (
            "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        )
        self.client = TradingClient(api_key, api_secret, paper=paper)
        self._circuit_breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60)
        self._verify_connection()

    def _verify_connection(self):
        """Verify API connectivity and log account info."""
        try:
            acct = self.client.get_account()
            logger.info(
                f"Alpaca connected | Account: ***{str(acct.account_number)[-4:]} | "
                f"Status: {acct.status} | Cash: ${float(acct.cash):,.2f} | "
                f"Options Level: {acct.options_trading_level}"
            )
        except Exception as e:
            logger.error(f"Alpaca connection failed: {e}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_account(self) -> Dict:
        """Get account balance and buying power."""
        try:
            acct = self._circuit_breaker.call(self.client.get_account)
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.error(f"Failed to get Alpaca account info: {e}", exc_info=True)
            raise ProviderError(f"Alpaca get_account failed: {e}") from e
        return {
            "account_number": acct.account_number,
            "status": str(acct.status),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
            "options_buying_power": float(acct.options_buying_power),
            "options_level": acct.options_trading_level,
            "equity": float(acct.equity),
        }

    # ------------------------------------------------------------------
    # Option symbol helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _exp_from_occ_symbol(symbol: str) -> str:
        """Extract expiration date string from OCC symbol (e.g. SPY260402C00685000 → 2026-04-02)."""
        try:
            yymmdd = symbol[6:12]
            return f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
        except (IndexError, ValueError):
            return ""

    def _build_occ_symbol(self, ticker: str, expiration: str, strike: float, option_type: str) -> str:
        """
        Build OCC option symbol: SPY260320C00500000
        ticker (padded to 6), YYMMDD, C/P, strike * 1000 padded to 8.
        """
        # Parse expiration
        if isinstance(expiration, datetime):
            exp_dt = expiration
        else:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    exp_dt = datetime.strptime(expiration.split(" ")[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(f"Cannot parse expiration: {expiration}")

        date_str = exp_dt.strftime("%y%m%d")
        cp = "C" if option_type.lower().startswith("c") else "P"
        strike_int = int(round(strike * 1000))
        return f"{ticker.upper():<6}{date_str}{cp}{strike_int:08d}".replace(" ", "")

    def find_option_symbol(self, ticker: str, expiration: str, strike: float, option_type: str) -> str:
        """
        Look up the actual Alpaca option contract symbol via the API.
        If the target expiration has no contracts (e.g. paper trading only has near-term
        expirations), falls back to the nearest available expiration >= 1 DTE.
        Raises ProviderError if no usable contract can be found.
        """
        import datetime as _dt
        strike = round(float(strike), 2)
        target_exp = expiration.split(" ")[0] if " " in expiration else expiration
        ct = ContractType.CALL if option_type.lower().startswith("c") else ContractType.PUT

        try:
            # 1. Try exact expiration first
            req = GetOptionContractsRequest(
                underlying_symbols=[ticker.upper()],
                expiration_date=target_exp,
                type=ct,
                strike_price_gte=str(strike),
                strike_price_lte=str(strike),
                limit=5,
            )
            resp = self.client.get_option_contracts(req)
            contracts = resp.option_contracts if hasattr(resp, 'option_contracts') else resp
            if contracts:
                return contracts[0].symbol

            # 2. Exact expiration not available — find nearest available expiration >= 1 DTE
            today = _dt.date.today()
            min_exp = str(today + _dt.timedelta(days=1))  # at least 1 DTE
            req2 = GetOptionContractsRequest(
                underlying_symbols=[ticker.upper()],
                expiration_date_gte=min_exp,
                type=ct,
                strike_price_gte=str(strike),
                strike_price_lte=str(strike),
                limit=50,
            )
            resp2 = self.client.get_option_contracts(req2)
            contracts2 = resp2.option_contracts if hasattr(resp2, 'option_contracts') else resp2
            if contracts2:
                # Pick the contract whose expiration is closest to the target
                try:
                    target_date = _dt.date.fromisoformat(target_exp)
                except ValueError:
                    target_date = today + _dt.timedelta(days=30)
                best = min(
                    contracts2,
                    key=lambda c: abs((_dt.date.fromisoformat(str(c.expiration_date)) - target_date).days),
                )
                actual_exp = str(best.expiration_date)
                actual_dte = (_dt.date.fromisoformat(actual_exp) - today).days
                logger.warning(
                    "find_option_symbol: %s %s $%.2f — target exp %s not available; "
                    "substituting nearest exp %s (DTE=%d)",
                    ticker, option_type, strike, target_exp, actual_exp, actual_dte,
                )
                return best.symbol

        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(
                f"Option contract API lookup failed for {ticker} {target_exp} ${strike} {option_type}: {e}"
            ) from e

        raise ProviderError(
            f"No option contract found in Alpaca for {ticker} ${strike} {option_type} "
            f"(target={target_exp}, no alternatives with strike match >= 1 DTE)"
        )

    # ------------------------------------------------------------------
    # Shared MLEG order submission
    # ------------------------------------------------------------------

    def _submit_mleg_order(
        self,
        legs: List[OptionLegRequest],
        contracts: int,
        limit_price: Optional[float],
        client_id: str,
    ) -> "alpaca.trading.models.Order":  # noqa: F821
        """Build and submit a multi-leg option order.

        Args:
            legs: List of OptionLegRequest objects.
            contracts: Number of spreads.
            limit_price: Limit price (positive = credit). None for market.
            client_id: Client order ID for tracking.

        Returns:
            The Alpaca Order object.
        """
        if limit_price is not None:
            order_req = LimitOrderRequest(
                qty=contracts,
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                legs=legs,
                limit_price=round(limit_price, 2),
                client_order_id=client_id,
            )
        else:
            from alpaca.trading.requests import MarketOrderRequest
            order_req = MarketOrderRequest(
                qty=contracts,
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                legs=legs,
                client_order_id=client_id,
            )

        return self._circuit_breaker.call(self.client.submit_order, order_req)

    # ------------------------------------------------------------------
    # Submit credit spread
    # ------------------------------------------------------------------

    @_retry_with_backoff(max_retries=2, base_delay=1.0)
    def submit_credit_spread(
        self,
        ticker: str,
        short_strike: float,
        long_strike: float,
        expiration: str,
        spread_type: str,
        contracts: int = 1,
        limit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict:
        """
        Submit a credit spread as a multi-leg order.

        Args:
            ticker: Underlying symbol (e.g. SPY)
            short_strike: Strike to sell
            long_strike: Strike to buy
            expiration: Expiration date string (YYYY-MM-DD)
            spread_type: 'bear_call' or 'bull_put'
            contracts: Number of spreads
            limit_price: Net credit limit price (positive = credit received).
                         If None, submits as market.

        Returns:
            Dict with order details and status.
        """
        is_call = "call" in spread_type.lower()
        opt_type = "call" if is_call else "put"

        # Resolve option symbols
        short_sym = self.find_option_symbol(ticker, expiration, short_strike, opt_type)
        long_sym = self.find_option_symbol(ticker, expiration, long_strike, opt_type)

        if not short_sym or not long_sym:
            return {"status": "error", "message": "Could not resolve option symbols"}

        logger.info(
            f"Submitting {spread_type} spread: SELL {short_sym} / BUY {long_sym} "
            f"x{contracts} @ limit {limit_price}"
        )

        # Build legs: sell short strike, buy long strike
        # ratio_qty=1 for equal ratio legs, qty on order controls total contracts
        legs = [
            OptionLegRequest(
                symbol=short_sym,
                ratio_qty=1,
                side=OrderSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
            OptionLegRequest(
                symbol=long_sym,
                ratio_qty=1,
                side=OrderSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
        ]

        client_id = client_order_id or f"cs-{ticker}-{uuid.uuid4().hex[:8]}"

        try:
            order = self._submit_mleg_order(legs, contracts, limit_price, client_id)

            actual_expiration = self._exp_from_occ_symbol(short_sym) or expiration
            result = {
                "status": "submitted",
                "order_id": str(order.id),
                "client_order_id": order.client_order_id,
                "order_status": str(order.status),
                "ticker": ticker,
                "spread_type": spread_type,
                "short_strike": short_strike,
                "long_strike": long_strike,
                "short_symbol": short_sym,
                "long_symbol": long_sym,
                "contracts": contracts,
                "limit_price": limit_price,
                "submitted_at": str(order.submitted_at),
                "actual_expiration": actual_expiration,
            }

            logger.info(f"Order submitted: {result['order_id']} status={result['order_status']}")
            return result

        except Exception as e:
            logger.error(f"Order submission failed: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "ticker": ticker,
                "spread_type": spread_type,
            }

    # ------------------------------------------------------------------
    # Close spread position
    # ------------------------------------------------------------------

    @_retry_with_backoff(max_retries=2, base_delay=1.0)
    def close_spread(
        self,
        ticker: str,
        short_strike: float,
        long_strike: float,
        expiration: str,
        spread_type: str,
        contracts: int = 1,
        limit_price: Optional[float] = None,
    ) -> Dict:
        """
        Close an existing credit spread (buy back short, sell long).
        """
        is_call = "call" in spread_type.lower()
        opt_type = "call" if is_call else "put"

        short_sym = self.find_option_symbol(ticker, expiration, short_strike, opt_type)
        long_sym = self.find_option_symbol(ticker, expiration, long_strike, opt_type)

        if not short_sym or not long_sym:
            return {"status": "error", "message": "Could not resolve option symbols for close order"}

        # Reverse legs: buy back short, sell long
        legs = [
            OptionLegRequest(
                symbol=short_sym,
                ratio_qty=1,
                side=OrderSide.BUY,
                position_intent=PositionIntent.BUY_TO_CLOSE,
            ),
            OptionLegRequest(
                symbol=long_sym,
                ratio_qty=1,
                side=OrderSide.SELL,
                position_intent=PositionIntent.SELL_TO_CLOSE,
            ),
        ]

        client_id = f"close-{ticker}-{uuid.uuid4().hex[:8]}"

        try:
            order = self._submit_mleg_order(legs, contracts, limit_price, client_id)
            logger.info(f"Close order submitted: {order.id} status={order.status}")
            return {
                "status": "submitted",
                "order_id": str(order.id),
                "order_status": str(order.status),
            }

        except Exception as e:
            logger.error(f"Close order failed: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Close iron condor (4-leg)
    # ------------------------------------------------------------------

    @_retry_with_backoff(max_retries=2, base_delay=1.0)
    def close_iron_condor(
        self,
        ticker: str,
        put_short_strike: float,
        put_long_strike: float,
        call_short_strike: float,
        call_long_strike: float,
        expiration: str,
        contracts: int = 1,
        limit_price: Optional[float] = None,
    ) -> Dict:
        """Close a 4-leg iron condor (buy back both short legs, sell both long legs).

        Submits a single MLEG order with all 4 legs for atomicity.
        """
        put_short_sym = self.find_option_symbol(ticker, expiration, put_short_strike, "put")
        put_long_sym = self.find_option_symbol(ticker, expiration, put_long_strike, "put")
        call_short_sym = self.find_option_symbol(ticker, expiration, call_short_strike, "call")
        call_long_sym = self.find_option_symbol(ticker, expiration, call_long_strike, "call")

        if not all([put_short_sym, put_long_sym, call_short_sym, call_long_sym]):
            return {"status": "error", "message": "Could not resolve IC option symbols for close"}

        logger.info(
            "Closing IC: BTC %s / STC %s / BTC %s / STC %s x%d",
            put_short_sym, put_long_sym, call_short_sym, call_long_sym, contracts,
        )

        legs = [
            OptionLegRequest(
                symbol=put_short_sym, ratio_qty=1,
                side=OrderSide.BUY, position_intent=PositionIntent.BUY_TO_CLOSE,
            ),
            OptionLegRequest(
                symbol=put_long_sym, ratio_qty=1,
                side=OrderSide.SELL, position_intent=PositionIntent.SELL_TO_CLOSE,
            ),
            OptionLegRequest(
                symbol=call_short_sym, ratio_qty=1,
                side=OrderSide.BUY, position_intent=PositionIntent.BUY_TO_CLOSE,
            ),
            OptionLegRequest(
                symbol=call_long_sym, ratio_qty=1,
                side=OrderSide.SELL, position_intent=PositionIntent.SELL_TO_CLOSE,
            ),
        ]

        client_id = f"close-ic-{ticker}-{uuid.uuid4().hex[:8]}"
        try:
            order = self._submit_mleg_order(legs, contracts, limit_price, client_id)
            logger.info(f"IC close order submitted: {order.id} status={order.status}")
            return {
                "status": "submitted",
                "order_id": str(order.id),
                "order_status": str(order.status),
            }
        except Exception as e:
            logger.error(f"IC close order failed: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Query orders & positions
    # ------------------------------------------------------------------

    def get_orders(self, status: str = "all", limit: int = 50) -> List[Dict]:
        """Get recent orders."""
        status_map = {
            "all": QueryOrderStatus.ALL,
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
        }
        req = GetOrdersRequest(
            status=status_map.get(status, QueryOrderStatus.ALL),
            limit=limit,
        )
        orders = self._circuit_breaker.call(self.client.get_orders, req)
        return [
            {
                "id": str(o.id),
                "client_order_id": o.client_order_id,
                "status": _normalize_order_status(o.status),
                "order_class": str(o.order_class),
                "type": str(o.type),
                "side": str(o.side) if o.side else None,
                "symbol": o.symbol,
                "qty": str(o.qty) if o.qty else None,
                "limit_price": str(o.limit_price) if o.limit_price else None,
                "filled_avg_price": str(o.filled_avg_price) if o.filled_avg_price else None,
                "submitted_at": str(o.submitted_at),
                "filled_at": str(o.filled_at) if o.filled_at else None,
                "legs": [
                    {
                        "symbol": leg.symbol,
                        "side": str(leg.side),
                        "qty": str(leg.qty) if leg.qty else None,
                        "status": str(leg.status),
                    }
                    for leg in (o.legs or [])
                ],
            }
            for o in orders
        ]

    def get_order_status(self, order_id: str) -> Dict:
        """Get status of a specific order."""
        order = self._circuit_breaker.call(self.client.get_order_by_id, order_id)
        return {
            "id": str(order.id),
            "status": _normalize_order_status(order.status),
            "filled_avg_price": str(order.filled_avg_price) if order.filled_avg_price else None,
            "filled_at": str(order.filled_at) if order.filled_at else None,
            "filled_qty": str(order.filled_qty) if order.filled_qty else None,
            "qty": str(order.qty) if order.qty else None,
        }

    def get_market_clock(self) -> Dict:
        """Get the current Alpaca market clock.

        Returns a dict with is_open, next_open, next_close, and timestamp.
        This is the authoritative source for whether the market is currently open.
        """
        try:
            clock = self._circuit_breaker.call(self.client.get_clock)
            return {
                "is_open": bool(clock.is_open),
                "next_open": str(clock.next_open) if clock.next_open else None,
                "next_close": str(clock.next_close) if clock.next_close else None,
                "timestamp": str(clock.timestamp) if clock.timestamp else None,
            }
        except Exception as e:
            logger.warning("get_market_clock failed: %s", e)
            return {"is_open": None}  # None = unknown (don't block)

    def get_positions(self) -> List[Dict]:
        """Get all open positions."""
        positions = self._circuit_breaker.call(self.client.get_all_positions)
        return [
            {
                "symbol": p.symbol,
                "qty": str(p.qty),
                "side": str(p.side),
                "avg_entry_price": str(p.avg_entry_price),
                "current_price": str(p.current_price),
                "market_value": str(p.market_value),
                "unrealized_pl": str(p.unrealized_pl),
                "asset_class": str(p.asset_class),
            }
            for p in positions
        ]

    # ------------------------------------------------------------------
    # Submit single-leg option order (straddle/strangle legs)
    # ------------------------------------------------------------------

    @_retry_with_backoff(max_retries=2, base_delay=1.0)
    def submit_single_leg(
        self,
        ticker: str,
        strike: float,
        expiration: str,
        option_type: str,
        side: str,
        contracts: int = 1,
        limit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict:
        """Submit a single-leg option order (buy or sell a single call/put).

        Used for straddle/strangle opens and closes where each leg is an
        independent order rather than a multi-leg spread.

        Args:
            ticker: Underlying symbol (e.g. SPY).
            strike: Option strike price.
            expiration: Expiration date string (YYYY-MM-DD).
            option_type: 'call' or 'put'.
            side: 'buy' or 'sell'.
            contracts: Number of contracts.
            limit_price: Limit price per contract. None for market order.
            client_order_id: Deterministic ID for idempotency.

        Returns:
            Dict with keys: status, order_id, client_order_id, order_status,
            ticker, strike, option_type, side, contracts, limit_price, submitted_at.
        """
        strike = round(float(strike), 2)
        symbol = self.find_option_symbol(ticker, expiration, strike, option_type)
        if not symbol:
            return {"status": "error", "message": "Could not resolve option symbol"}

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

        # Determine position intent based on side
        if side.lower() == "buy":
            # Could be buy-to-open (new long) or buy-to-close (closing short)
            # Use BUY_TO_OPEN by default; callers closing positions should use
            # the close variant or set intent explicitly.
            intent = PositionIntent.BUY_TO_OPEN
        else:
            intent = PositionIntent.SELL_TO_OPEN

        client_id = client_order_id or f"sl-{ticker}-{uuid.uuid4().hex[:8]}"

        logger.info(
            "Submitting single-leg: %s %s %s $%.2f x%d @ limit %s (client_id=%s)",
            side.upper(), ticker, option_type.upper(), strike,
            contracts, limit_price, client_id,
        )

        try:
            if limit_price is not None:
                order_req = LimitOrderRequest(
                    symbol=symbol,
                    qty=contracts,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(limit_price, 2),
                    client_order_id=client_id,
                    position_intent=intent,
                )
            else:
                from alpaca.trading.requests import MarketOrderRequest
                order_req = MarketOrderRequest(
                    symbol=symbol,
                    qty=contracts,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_id,
                    position_intent=intent,
                )

            order = self._circuit_breaker.call(self.client.submit_order, order_req)

            result = {
                "status": "submitted",
                "order_id": str(order.id),
                "client_order_id": order.client_order_id,
                "order_status": str(order.status),
                "ticker": ticker,
                "strike": strike,
                "option_type": option_type,
                "side": side,
                "symbol": symbol,
                "contracts": contracts,
                "limit_price": limit_price,
                "submitted_at": str(order.submitted_at),
            }

            logger.info("Single-leg order submitted: %s status=%s", result["order_id"], result["order_status"])
            return result

        except Exception as e:
            logger.error("Single-leg order submission failed: %s", e, exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "ticker": ticker,
                "strike": strike,
                "option_type": option_type,
                "side": side,
            }

    @_retry_with_backoff(max_retries=2, base_delay=1.0)
    def close_single_leg(
        self,
        ticker: str,
        strike: float,
        expiration: str,
        option_type: str,
        side: str,
        contracts: int = 1,
        limit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict:
        """Close a single-leg option position.

        Like submit_single_leg but uses CLOSE position intents.

        Args:
            side: 'buy' (buy-to-close a short) or 'sell' (sell-to-close a long).
        """
        strike = round(float(strike), 2)
        symbol = self.find_option_symbol(ticker, expiration, strike, option_type)
        if not symbol:
            return {"status": "error", "message": "Could not resolve option symbol for close"}

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        intent = (
            PositionIntent.BUY_TO_CLOSE
            if side.lower() == "buy"
            else PositionIntent.SELL_TO_CLOSE
        )

        client_id = client_order_id or f"close-sl-{ticker}-{uuid.uuid4().hex[:8]}"

        logger.info(
            "Closing single-leg: %s %s %s $%.2f x%d (client_id=%s)",
            side.upper(), ticker, option_type.upper(), strike, contracts, client_id,
        )

        try:
            if limit_price is not None:
                order_req = LimitOrderRequest(
                    symbol=symbol,
                    qty=contracts,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(limit_price, 2),
                    client_order_id=client_id,
                    position_intent=intent,
                )
            else:
                from alpaca.trading.requests import MarketOrderRequest
                order_req = MarketOrderRequest(
                    symbol=symbol,
                    qty=contracts,
                    side=order_side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_id,
                    position_intent=intent,
                )

            order = self._circuit_breaker.call(self.client.submit_order, order_req)
            logger.info("Single-leg close order submitted: %s status=%s", order.id, order.status)
            return {
                "status": "submitted",
                "order_id": str(order.id),
                "order_status": str(order.status),
            }

        except Exception as e:
            logger.error("Single-leg close order failed: %s", e, exc_info=True)
            return {"status": "error", "message": str(e)}

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        try:
            self._circuit_breaker.call(self.client.cancel_order_by_id, order_id)
            logger.info(f"Order {order_id} cancelled")
            return True
        except Exception as e:
            logger.error(f"Cancel failed: {e}", exc_info=True)
            return False

    def cancel_all_orders(self):
        """Cancel all open orders."""
        self.client.cancel_orders()
        logger.info("All orders cancelled")

    def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict]:
        """Look up an order by our deterministic client_order_id.

        Returns a dict with id, status, filled_avg_price, etc., or None if not found.
        """
        try:
            order = self._circuit_breaker.call(
                self.client.get_order_by_client_id, client_order_id
            )
            return {
                "id": str(order.id),
                "client_order_id": order.client_order_id,
                "status": _normalize_order_status(order.status),
                "filled_avg_price": (
                    str(order.filled_avg_price) if order.filled_avg_price else None
                ),
                "filled_at": str(order.filled_at) if order.filled_at else None,
                "legs": [
                    {
                        "symbol": leg.symbol,
                        "side": str(leg.side),
                        "filled_qty": str(leg.filled_qty) if leg.filled_qty else None,
                    }
                    for leg in (order.legs or [])
                ],
            }
        except Exception as e:
            logger.warning("get_order_by_client_id(%s): %s", client_order_id, e)
            return None

    def get_account_activities(
        self,
        activity_type: str = "FILL",
        since: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Fetch account activities (fills, expirations, etc.) via the Alpaca REST API.

        Args:
            activity_type: Alpaca activity type string, e.g. "FILL" or "OPEXP".
            since: ISO-8601 timestamp — return activities after this time.
            limit: Maximum number of results (page_size).

        Returns:
            List of activity dicts with symbol, qty, price, side, transaction_time, etc.
            Empty list on error.
        """
        import requests as _requests

        url = f"{self._base_url}/v2/account/activities"
        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
        }
        params: Dict = {"activity_types": activity_type, "page_size": str(limit)}
        if since:
            params["after"] = since

        try:
            resp = _requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            results = []
            for a in (data if isinstance(data, list) else []):
                results.append({
                    "id": a.get("id"),
                    "activity_type": a.get("activity_type"),
                    "symbol": a.get("symbol"),
                    "qty": a.get("qty"),
                    "price": a.get("price"),
                    "side": a.get("side"),
                    "transaction_time": a.get("transaction_time"),
                    "net_amount": a.get("net_amount"),
                    "order_id": a.get("order_id"),
                    "client_order_id": a.get("client_order_id"),
                })
            return results
        except Exception as e:
            logger.warning("get_account_activities failed: %s", e)
            return []
