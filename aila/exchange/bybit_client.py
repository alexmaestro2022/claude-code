"""
AILA - Bybit API Client

Main client for interacting with Bybit exchange API.
Supports both spot and futures trading.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import pandas as pd
import structlog
from pybit.unified_trading import HTTP, WebSocket

from .models import (
    AccountType,
    Balance,
    Candle,
    MarginMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    Ticker,
    TradingPair,
)

logger = structlog.get_logger(__name__)


@dataclass
class BybitConfig:
    """Configuration for Bybit API client."""

    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    account_type: AccountType = AccountType.FUTURES

    # Rate limiting
    max_requests_per_second: int = 10
    retry_attempts: int = 3
    retry_delay: float = 1.0

    # WebSocket settings
    ws_ping_interval: int = 20
    ws_ping_timeout: int = 10

    # Futures specific
    default_leverage: int = 10
    margin_mode: MarginMode = MarginMode.CROSS

    # Leverage presets per symbol
    leverage_presets: dict = field(
        default_factory=lambda: {
            "BTCUSDT": {"max": 100, "default": 10},
            "ETHUSDT": {"max": 100, "default": 10},
            "SOLUSDT": {"max": 50, "default": 5},
        }
    )


class BybitClient:
    """
    Bybit API client for spot and futures trading.

    Provides methods for:
    - Account information and balances
    - Order management (place, cancel, query)
    - Position management (for futures)
    - Market data (candles, tickers)
    - WebSocket subscriptions

    Example:
        config = BybitConfig(
            api_key="your_key",
            api_secret="your_secret",
            testnet=True,
        )
        client = BybitClient(config)

        # Get balance
        balance = await client.get_balance("USDT")

        # Place order
        order = await client.place_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.001"),
        )
    """

    # Cache TTL constants (in seconds) - optimized for 600 req/5s limit
    CACHE_TTL_TICKER = 1  # Ticker data cache - ultra fast
    CACHE_TTL_BALANCE = 2  # Balance cache
    CACHE_TTL_PAIRS = 300  # Trading pairs cache (5 minutes)
    CACHE_TTL_POSITIONS = 1  # Positions cache - ultra fast
    CACHE_TTL_KLINES = 1  # Klines cache - matches 1s scan interval

    def __init__(self, config: BybitConfig):
        """
        Initialize Bybit client.

        Args:
            config: Client configuration
        """
        self.config = config
        self._http: Optional[HTTP] = None
        self._ws: Optional[WebSocket] = None
        self._last_request_time: float = 0
        self._request_count: int = 0
        self._trading_pairs: dict[str, TradingPair] = {}
        self._is_connected: bool = False

        # Cache storage: {key: (data, timestamp)}
        self._cache: dict[str, tuple[Any, float]] = {}

        # API stats tracking
        self._total_requests: int = 0
        self._requests_this_minute: int = 0
        self._minute_start: float = time.time()
        self._last_ping_ms: float = 0
        self._api_rate_limit: int = 600  # Bybit limit: 600 requests per 5 seconds (per IP)

    def _get_cached(self, key: str, ttl: float) -> Optional[Any]:
        """Get cached data if not expired."""
        if key in self._cache:
            data, timestamp = self._cache[key]
            if time.time() - timestamp < ttl:
                return data
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        """Set cached data with current timestamp."""
        self._cache[key] = (data, time.time())

    def clear_cache(self, key: Optional[str] = None) -> None:
        """Clear cache. If key provided, clear only that key."""
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    @property
    def http(self) -> HTTP:
        """Get or create HTTP client."""
        if self._http is None:
            self._http = HTTP(
                testnet=self.config.testnet,
                api_key=self.config.api_key,
                api_secret=self.config.api_secret,
            )
        return self._http

    def connect(self) -> bool:
        """
        Connect to Bybit API and verify credentials.

        Returns:
            True if connection successful
        """
        try:
            # Test connection with a simple request
            result = self.http.get_wallet_balance(accountType="UNIFIED")

            if result.get("retCode") == 0:
                self._is_connected = True
                logger.info("Connected to Bybit API", testnet=self.config.testnet)
                return True
            else:
                logger.error(
                    "Failed to connect to Bybit",
                    error=result.get("retMsg"),
                )
                return False

        except Exception as e:
            logger.error("Connection error", error=str(e))
            return False

    def disconnect(self) -> None:
        """Disconnect from Bybit API."""
        if self._ws is not None:
            try:
                self._ws.exit()
            except Exception as e:
                logger.warning("Error closing WebSocket", error=str(e))
            self._ws = None

        self._http = None
        self._is_connected = False
        logger.info("Disconnected from Bybit API")

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._is_connected

    def _rate_limit(self) -> None:
        """Apply rate limiting and track API stats."""
        current_time = time.time()
        elapsed = current_time - self._last_request_time

        # Track requests per 5 seconds (Bybit's rate limit window)
        if current_time - self._minute_start >= 5:
            self._requests_this_minute = 0  # Actually per 5 seconds now
            self._minute_start = current_time

        self._total_requests += 1
        self._requests_this_minute += 1

        if elapsed < 1.0:
            self._request_count += 1
            if self._request_count >= self.config.max_requests_per_second:
                sleep_time = 1.0 - elapsed
                time.sleep(sleep_time)
                self._request_count = 0
        else:
            self._request_count = 1

        self._last_request_time = time.time()

    def get_api_stats(self) -> dict:
        """Get API usage statistics."""
        return {
            "total_requests": self._total_requests,
            "requests_this_minute": self._requests_this_minute,
            "last_ping_ms": self._last_ping_ms,
            "rate_limit": self._api_rate_limit,
            "rate_usage_percent": min(100, (self._requests_this_minute / self._api_rate_limit) * 100),
        }

    def ping(self) -> float:
        """
        Measure API ping latency.

        Returns:
            Latency in milliseconds
        """
        start = time.time()
        try:
            # Use server time endpoint for ping
            self.http.get_server_time()
            self._last_ping_ms = (time.time() - start) * 1000
        except Exception:
            self._last_ping_ms = -1
        return self._last_ping_ms

    def _handle_response(self, response: dict, operation: str) -> dict:
        """
        Handle API response and check for errors.

        Args:
            response: API response
            operation: Operation name for logging

        Returns:
            Response result

        Raises:
            Exception if API returned an error
        """
        ret_code = response.get("retCode", -1)
        ret_msg = response.get("retMsg", "Unknown error")

        if ret_code != 0:
            logger.error(
                "API error",
                operation=operation,
                code=ret_code,
                message=ret_msg,
            )
            raise Exception(f"Bybit API error: {ret_msg} (code: {ret_code})")

        return response.get("result", {})

    # ========== Account Methods ==========

    def _safe_decimal(self, value: any, default: str = "0") -> Decimal:
        """Safely convert value to Decimal, handling empty strings and invalid values."""
        if value is None or value == "":
            return Decimal(default)
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal(default)

    def get_balance(self, asset: str = "USDT", use_cache: bool = True) -> Balance:
        """
        Get balance for a specific asset.

        Args:
            asset: Asset symbol (default: USDT)
            use_cache: Whether to use cached data (default: True)

        Returns:
            Balance object
        """
        cache_key = f"balance_{asset}"

        # Check cache first
        if use_cache:
            cached = self._get_cached(cache_key, self.CACHE_TTL_BALANCE)
            if cached is not None:
                return cached

        self._rate_limit()

        response = self.http.get_wallet_balance(accountType="UNIFIED")
        result = self._handle_response(response, "get_balance")

        for account in result.get("list", []):
            for coin in account.get("coin", []):
                if coin.get("coin") == asset:
                    balance = Balance(
                        asset=asset,
                        total=self._safe_decimal(coin.get("walletBalance")),
                        available=self._safe_decimal(coin.get("availableToWithdraw")),
                        locked=self._safe_decimal(coin.get("locked")),
                        unrealized_pnl=self._safe_decimal(coin.get("unrealisedPnl")),
                        equity=self._safe_decimal(coin.get("equity")),
                        account_type=self.config.account_type,
                    )
                    self._set_cached(cache_key, balance)
                    return balance

        # Return zero balance if asset not found
        zero_balance = Balance(
            asset=asset,
            total=Decimal("0"),
            available=Decimal("0"),
            account_type=self.config.account_type,
        )
        self._set_cached(cache_key, zero_balance)
        return zero_balance

    def get_all_balances(self) -> list[Balance]:
        """
        Get all non-zero balances.

        Returns:
            List of Balance objects
        """
        self._rate_limit()

        response = self.http.get_wallet_balance(accountType="UNIFIED")
        result = self._handle_response(response, "get_all_balances")

        balances = []
        for account in result.get("list", []):
            for coin in account.get("coin", []):
                total = self._safe_decimal(coin.get("walletBalance"))
                if total > 0:
                    balances.append(
                        Balance(
                            asset=coin.get("coin"),
                            total=total,
                            available=self._safe_decimal(coin.get("availableToWithdraw")),
                            locked=self._safe_decimal(coin.get("locked")),
                            unrealized_pnl=self._safe_decimal(coin.get("unrealisedPnl")),
                            equity=self._safe_decimal(coin.get("equity")),
                            account_type=self.config.account_type,
                        )
                    )

        return balances

    # ========== Order Methods ==========

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        reduce_only: bool = False,
        time_in_force: str = "GTC",
        client_order_id: Optional[str] = None,
    ) -> Order:
        """
        Place a new order.

        Args:
            symbol: Trading pair symbol
            side: Order side (BUY/SELL)
            order_type: Order type (MARKET/LIMIT/etc.)
            quantity: Order quantity
            price: Order price (required for limit orders)
            stop_loss: Stop-loss price
            take_profit: Take-profit price
            reduce_only: Whether order should only reduce position
            time_in_force: Time in force (GTC, IOC, FOK)
            client_order_id: Custom order ID

        Returns:
            Order object
        """
        self._rate_limit()

        # Determine category based on account type
        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        # Build order params
        params = {
            "category": category,
            "symbol": symbol,
            "side": side.value,
            "orderType": order_type.value,
            "qty": str(quantity),
            "timeInForce": time_in_force,
        }

        if price is not None:
            params["price"] = str(price)

        if stop_loss is not None:
            params["stopLoss"] = str(stop_loss)

        if take_profit is not None:
            params["takeProfit"] = str(take_profit)

        if reduce_only and category == "linear":
            params["reduceOnly"] = True

        if client_order_id:
            params["orderLinkId"] = client_order_id

        logger.info("Placing order", **params)

        response = self.http.place_order(**params)
        result = self._handle_response(response, "place_order")

        order = Order(
            order_id=result.get("orderId", ""),
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            status=OrderStatus.NEW,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reduce_only=reduce_only,
            time_in_force=time_in_force,
            exchange_order_id=result.get("orderId"),
            client_order_id=client_order_id or result.get("orderLinkId"),
        )

        logger.info("Order placed", order_id=order.order_id, symbol=symbol)

        # Invalidate caches after order placement
        self.clear_cache("positions_all")
        self.clear_cache(f"positions_{symbol}")
        self.clear_cache("balance_USDT")

        return order

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        Cancel an active order.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        self._rate_limit()

        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        response = self.http.cancel_order(
            category=category,
            symbol=symbol,
            orderId=order_id,
        )

        try:
            self._handle_response(response, "cancel_order")
            logger.info("Order cancelled", order_id=order_id, symbol=symbol)
            return True
        except Exception as e:
            logger.error("Failed to cancel order", order_id=order_id, error=str(e))
            return False

    def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """
        Cancel all active orders.

        Args:
            symbol: Optional symbol to filter (cancels all if not provided)

        Returns:
            Number of orders cancelled
        """
        self._rate_limit()

        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        else:
            # Bybit requires either symbol or settleCoin for cancel_all
            params["settleCoin"] = "USDT"

        response = self.http.cancel_all_orders(**params)
        result = self._handle_response(response, "cancel_all_orders")

        cancelled = len(result.get("list", []))
        logger.info("Cancelled orders", count=cancelled, symbol=symbol)
        return cancelled

    def get_order(self, symbol: str, order_id: str) -> Optional[Order]:
        """
        Get order details.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID

        Returns:
            Order object or None if not found
        """
        self._rate_limit()

        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        response = self.http.get_open_orders(
            category=category,
            symbol=symbol,
            orderId=order_id,
        )

        result = self._handle_response(response, "get_order")

        orders = result.get("list", [])
        if not orders:
            return None

        order_data = orders[0]
        return self._parse_order(order_data)

    def get_open_orders(self, symbol: Optional[str] = None) -> list[Order]:
        """
        Get all open orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of Order objects
        """
        self._rate_limit()

        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        params = {"category": category}
        if symbol:
            params["symbol"] = symbol

        response = self.http.get_open_orders(**params)
        result = self._handle_response(response, "get_open_orders")

        return [self._parse_order(o) for o in result.get("list", [])]

    def _parse_order(self, data: dict) -> Order:
        """Parse order data from API response."""
        status_map = {
            "New": OrderStatus.NEW,
            "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
            "Filled": OrderStatus.FILLED,
            "Cancelled": OrderStatus.CANCELLED,
            "Rejected": OrderStatus.REJECTED,
        }

        return Order(
            order_id=data.get("orderId", ""),
            symbol=data.get("symbol", ""),
            side=OrderSide(data.get("side", "Buy")),
            order_type=OrderType(data.get("orderType", "Market")),
            quantity=Decimal(str(data.get("qty", "0"))),
            price=Decimal(str(data.get("price", "0"))) if data.get("price") else None,
            status=status_map.get(data.get("orderStatus", "New"), OrderStatus.NEW),
            filled_quantity=Decimal(str(data.get("cumExecQty", "0"))),
            average_price=Decimal(str(data.get("avgPrice", "0"))) if data.get("avgPrice") else None,
            stop_loss=Decimal(str(data.get("stopLoss", "0"))) if data.get("stopLoss") else None,
            take_profit=Decimal(str(data.get("takeProfit", "0"))) if data.get("takeProfit") else None,
            reduce_only=data.get("reduceOnly", False),
            time_in_force=data.get("timeInForce", "GTC"),
            exchange_order_id=data.get("orderId"),
            client_order_id=data.get("orderLinkId"),
        )

    # ========== Position Methods ==========

    def get_positions(self, symbol: Optional[str] = None, use_cache: bool = True) -> list[Position]:
        """
        Get open positions (futures only).

        Args:
            symbol: Optional symbol filter
            use_cache: Whether to use cached data (default: True)

        Returns:
            List of Position objects
        """
        if self.config.account_type != AccountType.FUTURES:
            return []

        cache_key = f"positions_{symbol or 'all'}"

        # Check cache first
        if use_cache:
            cached = self._get_cached(cache_key, self.CACHE_TTL_POSITIONS)
            if cached is not None:
                return cached

        self._rate_limit()

        params = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol

        response = self.http.get_positions(**params)
        result = self._handle_response(response, "get_positions")

        positions = []
        for pos in result.get("list", []):
            size = Decimal(str(pos.get("size", "0")))
            if size > 0:
                positions.append(self._parse_position(pos))

        self._set_cached(cache_key, positions)
        return positions

    def _parse_position(self, data: dict) -> Position:
        """Parse position data from API response."""
        side = PositionSide.LONG if data.get("side") == "Buy" else PositionSide.SHORT

        # tradeMode can be int (0=cross, 1=isolated) or string
        trade_mode = data.get("tradeMode", 0)
        if isinstance(trade_mode, int):
            margin_mode = MarginMode.CROSS if trade_mode == 0 else MarginMode.ISOLATED
        else:
            margin_mode = MarginMode(str(trade_mode).lower())

        return Position(
            symbol=data.get("symbol", ""),
            side=side,
            size=Decimal(str(data.get("size", "0"))),
            entry_price=Decimal(str(data.get("avgPrice", "0"))),
            leverage=int(float(data.get("leverage", 1))),
            margin_mode=margin_mode,
            unrealized_pnl=Decimal(str(data.get("unrealisedPnl", "0"))),
            realized_pnl=Decimal(str(data.get("cumRealisedPnl", "0"))),
            liquidation_price=Decimal(str(data.get("liqPrice", "0"))) if data.get("liqPrice") else None,
            stop_loss=Decimal(str(data.get("stopLoss", "0"))) if data.get("stopLoss") else None,
            take_profit=Decimal(str(data.get("takeProfit", "0"))) if data.get("takeProfit") else None,
            position_margin=Decimal(str(data.get("positionIM", "0"))),
            mark_price=Decimal(str(data.get("markPrice", "0"))) if data.get("markPrice") else None,
        )

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for a symbol (futures only).

        Args:
            symbol: Trading pair symbol
            leverage: Leverage value

        Returns:
            True if successful
        """
        if self.config.account_type != AccountType.FUTURES:
            return False

        self._rate_limit()

        # Validate leverage against presets
        preset = self.config.leverage_presets.get(symbol, {"max": 100})
        if leverage > preset.get("max", 100):
            logger.warning(
                "Leverage exceeds maximum",
                symbol=symbol,
                requested=leverage,
                max=preset["max"],
            )
            leverage = preset["max"]

        try:
            response = self.http.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            self._handle_response(response, "set_leverage")
            logger.info("Leverage set", symbol=symbol, leverage=leverage)
            return True
        except Exception as e:
            # May fail if leverage already set
            if "leverage not modified" in str(e).lower():
                return True
            logger.error("Failed to set leverage", symbol=symbol, error=str(e))
            return False

    def set_margin_mode(self, symbol: str, margin_mode: MarginMode) -> bool:
        """
        Set margin mode for a symbol (futures only).

        Args:
            symbol: Trading pair symbol
            margin_mode: CROSS or ISOLATED

        Returns:
            True if successful
        """
        if self.config.account_type != AccountType.FUTURES:
            return False

        self._rate_limit()

        # tradeMode: 0=Cross, 1=Isolated
        trade_mode = 0 if margin_mode == MarginMode.CROSS else 1

        try:
            response = self.http.switch_margin_mode(
                category="linear",
                symbol=symbol,
                tradeMode=trade_mode,
                buyLeverage=str(self.config.default_leverage),
                sellLeverage=str(self.config.default_leverage),
            )
            self._handle_response(response, "set_margin_mode")
            logger.info("Margin mode set", symbol=symbol, mode=margin_mode.value)
            return True
        except Exception as e:
            # May fail if margin mode already set or position exists
            error_str = str(e).lower()
            if "margin mode is not modified" in error_str or "same mode" in error_str:
                return True
            logger.error("Failed to set margin mode", symbol=symbol, error=str(e))
            return False

    def close_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: Optional[Decimal] = None,
    ) -> Optional[Order]:
        """
        Close an open position (futures only).

        Args:
            symbol: Trading pair symbol
            side: Position side to close
            quantity: Quantity to close (closes all if not specified)

        Returns:
            Order object or None if failed
        """
        if self.config.account_type != AccountType.FUTURES:
            return None

        # Get current position
        positions = self.get_positions(symbol)
        position = next((p for p in positions if p.side == side), None)

        if position is None:
            logger.warning("No position to close", symbol=symbol, side=side.value)
            return None

        close_qty = quantity if quantity else position.size
        close_side = OrderSide.SELL if side == PositionSide.LONG else OrderSide.BUY

        return self.place_order(
            symbol=symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            quantity=close_qty,
            reduce_only=True,
        )

    # ========== Market Data Methods ==========

    def get_klines(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 200,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Get candlestick/kline data.

        Args:
            symbol: Trading pair symbol
            interval: Timeframe (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M)
            limit: Number of candles to fetch (max 1000)
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
            use_cache: Use cached data if available (15s TTL)

        Returns:
            DataFrame with OHLCV data
        """
        # Check cache first
        cache_key = f"klines:{symbol}:{interval}:{limit}"
        if use_cache:
            cached = self._get_cached(cache_key, ttl=self.CACHE_TTL_KLINES)
            if cached is not None:
                return cached

        self._rate_limit()

        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000),
        }

        if start_time:
            params["start"] = start_time
        if end_time:
            params["end"] = end_time

        response = self.http.get_kline(**params)
        result = self._handle_response(response, "get_klines")

        candles = result.get("list", [])
        if not candles:
            return pd.DataFrame()

        # Bybit returns newest first, reverse for chronological order
        candles = candles[::-1]

        df = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
        )

        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["turnover"] = df["turnover"].astype(float)

        df.set_index("timestamp", inplace=True)

        # Cache the result
        if use_cache:
            self._set_cached(cache_key, df)

        return df

    def get_ticker(self, symbol: str, use_cache: bool = True) -> Optional[Ticker]:
        """
        Get current ticker for a symbol.

        Args:
            symbol: Trading pair symbol
            use_cache: Whether to use cached data (default: True)

        Returns:
            Ticker object or None if not found
        """
        cache_key = f"ticker_{symbol}"

        # Check cache first
        if use_cache:
            cached = self._get_cached(cache_key, self.CACHE_TTL_TICKER)
            if cached is not None:
                return cached

        self._rate_limit()

        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        response = self.http.get_tickers(category=category, symbol=symbol)
        result = self._handle_response(response, "get_ticker")

        tickers = result.get("list", [])
        if not tickers:
            return None

        t = tickers[0]
        ticker = Ticker(
            symbol=symbol,
            last_price=Decimal(str(t.get("lastPrice", "0"))),
            bid_price=Decimal(str(t.get("bid1Price", "0"))),
            ask_price=Decimal(str(t.get("ask1Price", "0"))),
            high_24h=Decimal(str(t.get("highPrice24h", "0"))),
            low_24h=Decimal(str(t.get("lowPrice24h", "0"))),
            volume_24h=Decimal(str(t.get("volume24h", "0"))),
            turnover_24h=Decimal(str(t.get("turnover24h", "0"))),
            change_24h=float(t.get("price24hPcnt", "0")) * 100,
        )
        self._set_cached(cache_key, ticker)
        return ticker

    def get_trading_pairs(self, reload: bool = False) -> list[TradingPair]:
        """
        Get available trading pairs.

        Args:
            reload: Force reload from API

        Returns:
            List of TradingPair objects
        """
        cache_key = "trading_pairs"

        # Check cache first (unless reload is forced)
        if not reload:
            cached = self._get_cached(cache_key, self.CACHE_TTL_PAIRS)
            if cached is not None:
                return cached

            # Also check in-memory pairs dict
            if self._trading_pairs:
                return list(self._trading_pairs.values())

        self._rate_limit()

        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        response = self.http.get_instruments_info(category=category)
        result = self._handle_response(response, "get_trading_pairs")

        pairs = []
        for item in result.get("list", []):
            pair = TradingPair(
                symbol=item.get("symbol", ""),
                base_asset=item.get("baseCoin", ""),
                quote_asset=item.get("quoteCoin", ""),
                status=item.get("status", "Trading"),
                min_order_qty=Decimal(str(item.get("lotSizeFilter", {}).get("minOrderQty", "0.001"))),
                max_order_qty=Decimal(str(item.get("lotSizeFilter", {}).get("maxOrderQty", "1000000"))),
                qty_step=Decimal(str(item.get("lotSizeFilter", {}).get("qtyStep", "0.001"))),
                tick_size=Decimal(str(item.get("priceFilter", {}).get("tickSize", "0.01"))),
                max_leverage=int(float(item.get("leverageFilter", {}).get("maxLeverage", 100))),
            )
            pairs.append(pair)
            self._trading_pairs[pair.symbol] = pair

        self._set_cached(cache_key, pairs)
        return pairs

    def get_trading_pair(self, symbol: str) -> Optional[TradingPair]:
        """
        Get trading pair info for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            TradingPair object or None if not found
        """
        if symbol not in self._trading_pairs:
            self.get_trading_pairs()

        return self._trading_pairs.get(symbol)

    # ========== PnL Methods ==========

    def get_closed_pnl(
        self,
        symbol: Optional[str] = None,
        limit: int = 50,
        start_time: Optional[int] = None,
    ) -> list[dict]:
        """
        Get closed PnL records.

        Args:
            symbol: Optional symbol filter
            limit: Number of records to fetch (max 100)
            start_time: Start timestamp in milliseconds

        Returns:
            List of closed PnL records with fields:
            - symbol, orderId, side, qty, orderPrice, execType
            - closedSize, cumEntryValue, avgEntryPrice
            - cumExitValue, avgExitPrice, closedPnl
            - createdTime, updatedTime
        """
        if self.config.account_type != AccountType.FUTURES:
            return []

        self._rate_limit()

        params = {"category": "linear", "limit": min(limit, 100)}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time

        response = self.http.get_closed_pnl(**params)
        result = self._handle_response(response, "get_closed_pnl")

        return result.get("list", [])

    def get_symbol_closed_pnl(self, symbol: str, limit: int = 5) -> Optional[dict]:
        """
        Get most recent closed PnL for a specific symbol.

        Args:
            symbol: Trading pair symbol
            limit: Number of records to check

        Returns:
            Most recent closed PnL record or None
        """
        records = self.get_closed_pnl(symbol=symbol, limit=limit)
        if records:
            return records[0]  # Most recent first
        return None

    # ========== Helper Methods ==========

    def validate_connection(self) -> tuple[bool, str]:
        """
        Validate API connection and credentials.

        Returns:
            Tuple of (is_valid, message)
        """
        try:
            balance = self.get_balance("USDT")
            return True, f"Connected. USDT Balance: {balance.total}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def __repr__(self) -> str:
        return (
            f"BybitClient(testnet={self.config.testnet}, "
            f"account_type={self.config.account_type.value}, "
            f"connected={self._is_connected})"
        )
