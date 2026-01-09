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
        """Apply rate limiting."""
        current_time = time.time()
        elapsed = current_time - self._last_request_time

        if elapsed < 1.0:
            self._request_count += 1
            if self._request_count >= self.config.max_requests_per_second:
                sleep_time = 1.0 - elapsed
                time.sleep(sleep_time)
                self._request_count = 0
        else:
            self._request_count = 1

        self._last_request_time = time.time()

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

    def get_balance(self, asset: str = "USDT") -> Balance:
        """
        Get balance for a specific asset.

        Args:
            asset: Asset symbol (default: USDT)

        Returns:
            Balance object
        """
        self._rate_limit()

        response = self.http.get_wallet_balance(accountType="UNIFIED")
        result = self._handle_response(response, "get_balance")

        for account in result.get("list", []):
            for coin in account.get("coin", []):
                if coin.get("coin") == asset:
                    return Balance(
                        asset=asset,
                        total=Decimal(str(coin.get("walletBalance", "0"))),
                        available=Decimal(str(coin.get("availableToWithdraw", "0"))),
                        locked=Decimal(str(coin.get("locked", "0"))),
                        unrealized_pnl=Decimal(str(coin.get("unrealisedPnl", "0"))),
                        equity=Decimal(str(coin.get("equity", "0"))),
                        account_type=self.config.account_type,
                    )

        # Return zero balance if asset not found
        return Balance(
            asset=asset,
            total=Decimal("0"),
            available=Decimal("0"),
            account_type=self.config.account_type,
        )

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
                total = Decimal(str(coin.get("walletBalance", "0")))
                if total > 0:
                    balances.append(
                        Balance(
                            asset=coin.get("coin"),
                            total=total,
                            available=Decimal(str(coin.get("availableToWithdraw", "0"))),
                            locked=Decimal(str(coin.get("locked", "0"))),
                            unrealized_pnl=Decimal(str(coin.get("unrealisedPnl", "0"))),
                            equity=Decimal(str(coin.get("equity", "0"))),
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

    def get_positions(self, symbol: Optional[str] = None) -> list[Position]:
        """
        Get open positions (futures only).

        Args:
            symbol: Optional symbol filter

        Returns:
            List of Position objects
        """
        if self.config.account_type != AccountType.FUTURES:
            return []

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

        return positions

    def _parse_position(self, data: dict) -> Position:
        """Parse position data from API response."""
        side = PositionSide.LONG if data.get("side") == "Buy" else PositionSide.SHORT

        return Position(
            symbol=data.get("symbol", ""),
            side=side,
            size=Decimal(str(data.get("size", "0"))),
            entry_price=Decimal(str(data.get("avgPrice", "0"))),
            leverage=int(float(data.get("leverage", 1))),
            margin_mode=MarginMode(data.get("tradeMode", "cross").lower()),
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
    ) -> pd.DataFrame:
        """
        Get candlestick/kline data.

        Args:
            symbol: Trading pair symbol
            interval: Timeframe (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M)
            limit: Number of candles to fetch (max 1000)
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds

        Returns:
            DataFrame with OHLCV data
        """
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

        return df

    def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """
        Get current ticker for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Ticker object or None if not found
        """
        self._rate_limit()

        category = "linear" if self.config.account_type == AccountType.FUTURES else "spot"

        response = self.http.get_tickers(category=category, symbol=symbol)
        result = self._handle_response(response, "get_ticker")

        tickers = result.get("list", [])
        if not tickers:
            return None

        t = tickers[0]
        return Ticker(
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

    def get_trading_pairs(self, reload: bool = False) -> list[TradingPair]:
        """
        Get available trading pairs.

        Args:
            reload: Force reload from API

        Returns:
            List of TradingPair objects
        """
        if self._trading_pairs and not reload:
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
