"""Bybit exchange implementation."""

import logging
from typing import Optional

from pybit.unified_trading import HTTP

from ...utils.common import retry_async
from .base_exchange import BaseExchange

logger = logging.getLogger("ai_trade.exchange.bybit")


class BybitExchange(BaseExchange):
    """Bybit exchange via pybit unified trading API."""

    __slots__ = ("_client",)

    def __init__(
        self, api_key: str = "", api_secret: str = "", testnet: bool = False
    ) -> None:
        super().__init__("bybit", api_key, api_secret, testnet)
        self._client = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )

    @retry_async(max_attempts=2)
    async def get_ticker(self, symbol: str) -> dict:
        """Get current price ticker."""
        # Normalize symbol: remove /USDT suffix if present
        bybit_symbol = symbol.replace("/", "") if "/" in symbol else symbol
        result = self._client.get_tickers(category="linear", symbol=bybit_symbol)
        if result["retCode"] == 0 and result["result"]["list"]:
            ticker = result["result"]["list"][0]
            price = float(ticker["lastPrice"])
            return {
                "symbol": symbol,
                "price": price,
                "last": price,  # ccxt-compatible alias
                "bid": float(ticker.get("bid1Price", 0)),
                "ask": float(ticker.get("ask1Price", 0)),
                "volume_24h": float(ticker.get("volume24h", 0)),
            }
        logger.warning(f"No ticker data for {bybit_symbol}")
        return {}

    async def fetch_ticker(self, symbol: str) -> dict:
        """Alias for get_ticker (ccxt-compatible name)."""
        return await self.get_ticker(symbol)

    @retry_async(max_attempts=2)
    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """Get order book."""
        # Normalize symbol: remove /USDT suffix if present
        normalized_symbol = symbol.replace("/USDT", "USDT") if "/" in symbol else symbol
        result = self._client.get_orderbook(
            category="linear", symbol=normalized_symbol, limit=limit
        )
        if result["retCode"] == 0:
            return {
                "bids": [(float(b[0]), float(b[1])) for b in result["result"]["b"]],
                "asks": [(float(a[0]), float(a[1])) for a in result["result"]["a"]],
            }
        return {"bids": [], "asks": []}

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        """Alias for get_orderbook (ccxt-compatible name)."""
        return await self.get_orderbook(symbol, limit)

    @retry_async(max_attempts=2)
    async def get_balance(self, currency: str = "USDT") -> float:
        """Get balance for currency."""
        result = self._client.get_wallet_balance(
            accountType="UNIFIED", coin=currency
        )
        if result["retCode"] == 0:
            for account in result["result"]["list"]:
                for coin in account["coin"]:
                    if coin["coin"] == currency:
                        return float(coin["walletBalance"])
        return 0.0

    @retry_async(max_attempts=2)
    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> dict:
        """Place an order."""
        params: dict = {
            "category": "linear",
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type.capitalize(),
            "qty": str(quantity),
        }
        if price and order_type.lower() == "limit":
            params["price"] = str(price)

        result = self._client.place_order(**params)
        return {
            "success": result["retCode"] == 0,
            "order_id": result["result"].get("orderId"),
            "message": result.get("retMsg"),
        }

    @retry_async(max_attempts=2)
    async def create_market_order(
        self, symbol: str, side: str, amount: float, params: Optional[dict] = None
    ) -> dict:
        """Create a market order (ccxt-compatible)."""
        bybit_symbol = symbol.replace("/", "") if "/" in symbol else symbol
        order_params: dict = {
            "category": "linear",
            "symbol": bybit_symbol,
            "side": side.capitalize(),
            "orderType": "Market",
            "qty": str(amount),
        }
        if params:
            if params.get("reduceOnly"):
                order_params["reduceOnly"] = True
        result = self._client.place_order(**order_params)
        if result["retCode"] == 0:
            return {
                "id": result["result"].get("orderId"),
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "status": "filled",
            }
        logger.error(f"Market order failed: {result.get('retMsg')}")
        return {}

    @retry_async(max_attempts=2)
    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """Create an order with specified type (ccxt-compatible)."""
        bybit_symbol = symbol.replace("/", "") if "/" in symbol else symbol
        # Map ccxt order types to Bybit
        type_map = {
            "stop_market": "Market",
            "take_profit_market": "Market",
            "limit": "Limit",
            "market": "Market",
        }
        bybit_type = type_map.get(order_type.lower(), "Market")

        order_params: dict = {
            "category": "linear",
            "symbol": bybit_symbol,
            "side": side.capitalize(),
            "orderType": bybit_type,
            "qty": str(amount),
        }

        if params:
            if params.get("reduceOnly"):
                order_params["reduceOnly"] = True
            if params.get("stopPrice"):
                order_params["triggerPrice"] = str(params["stopPrice"])
                # Determine trigger direction
                if order_type.lower() == "stop_market":
                    # Stop loss: trigger when price goes against position
                    order_params["triggerDirection"] = 2 if side.lower() == "sell" else 1
                elif order_type.lower() == "take_profit_market":
                    # Take profit: trigger when price goes in favor
                    order_params["triggerDirection"] = 1 if side.lower() == "sell" else 2

        if price and bybit_type == "Limit":
            order_params["price"] = str(price)

        result = self._client.place_order(**order_params)
        if result["retCode"] == 0:
            return {
                "id": result["result"].get("orderId"),
                "symbol": symbol,
                "type": order_type,
                "side": side,
                "amount": amount,
            }
        logger.warning(f"Order creation note: {result.get('retMsg')}")
        return {}

    @retry_async(max_attempts=2)
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        """Fetch open orders (ccxt-compatible)."""
        params: dict = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            bybit_symbol = symbol.replace("/", "") if "/" in symbol else symbol
            params["symbol"] = bybit_symbol
        result = self._client.get_open_orders(**params)
        orders: list[dict] = []
        if result["retCode"] == 0:
            for order in result["result"]["list"]:
                orders.append({
                    "id": order["orderId"],
                    "symbol": order["symbol"],
                    "side": order["side"].lower(),
                    "price": float(order.get("price", 0)),
                    "amount": float(order.get("qty", 0)),
                    "type": order.get("orderType", "").lower(),
                    "status": order.get("orderStatus", "").lower(),
                })
        return orders

    @retry_async(max_attempts=2)
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an order."""
        bybit_symbol = symbol.replace("/", "") if "/" in symbol else symbol
        result = self._client.cancel_order(
            category="linear", symbol=bybit_symbol, orderId=order_id
        )
        return result["retCode"] == 0

    @retry_async(max_attempts=2)
    async def get_positions(self) -> list[dict]:
        """Get open positions."""
        result = self._client.get_positions(
            category="linear", settleCoin="USDT"
        )
        positions: list[dict] = []
        if result["retCode"] == 0:
            for pos in result["result"]["list"]:
                if float(pos["size"]) > 0:
                    positions.append({
                        "symbol": pos["symbol"],
                        "side": pos["side"],
                        "size": float(pos["size"]),
                        "entry_price": float(pos["avgPrice"]),
                        "mark_price": float(pos["markPrice"]),
                        "pnl": float(pos["unrealisedPnl"]),
                        "leverage": pos["leverage"],
                    })
        return positions

    @retry_async(max_attempts=2)
    async def get_funding_rate(self, symbol: str) -> float:
        """Get funding rate for symbol."""
        result = self._client.get_tickers(category="linear", symbol=symbol)
        if result["retCode"] == 0 and result["result"]["list"]:
            return float(result["result"]["list"][0].get("fundingRate", 0))
        return 0.0

    @retry_async(max_attempts=2)
    async def set_leverage(self, leverage: int, symbol: str) -> bool:
        """Set leverage for symbol on Bybit."""
        # Convert symbol from ccxt format (BTC/USDT) to Bybit format (BTCUSDT)
        bybit_symbol = symbol.replace("/", "") if "/" in symbol else symbol
        try:
            result = self._client.set_leverage(
                category="linear",
                symbol=bybit_symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            if result["retCode"] == 0:
                logger.info(f"Leverage set to {leverage}x for {bybit_symbol}")
                return True
            # Code 110043 = leverage not modified (already set to this value)
            if result["retCode"] == 110043:
                logger.debug(f"Leverage already {leverage}x for {bybit_symbol}")
                return True
            logger.warning(f"Failed to set leverage: {result.get('retMsg')}")
            return False
        except Exception as e:
            logger.error(f"Error setting leverage for {bybit_symbol}: {e}")
            return False

    @retry_async(max_attempts=2)
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "15", limit: int = 100
    ) -> list[list]:
        """Fetch OHLCV candle data (ccxt-compatible format)."""
        # Convert symbol from ccxt format (BTC/USDT) to Bybit format (BTCUSDT)
        bybit_symbol = symbol.replace("/", "")

        # Convert timeframe to Bybit interval format
        interval_map = {
            "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
            "1d": "D", "1w": "W", "1M": "M",
        }
        interval = interval_map.get(timeframe, timeframe)

        result = self._client.get_kline(
            category="linear",
            symbol=bybit_symbol,
            interval=interval,
            limit=limit,
        )
        if result["retCode"] == 0:
            # Convert to ccxt format: [timestamp, open, high, low, close, volume]
            candles = []
            for item in reversed(result["result"]["list"]):  # Bybit returns newest first
                candles.append([
                    int(item[0]),      # timestamp
                    float(item[1]),    # open
                    float(item[2]),    # high
                    float(item[3]),    # low
                    float(item[4]),    # close
                    float(item[5]),    # volume
                ])
            return candles
        return []

    @retry_async(max_attempts=2)
    async def fetch_tickers(self) -> dict[str, dict]:
        """Fetch all tickers (ccxt-compatible format)."""
        result = self._client.get_tickers(category="linear")
        tickers = {}
        if result["retCode"] == 0:
            for item in result["result"]["list"]:
                symbol = item["symbol"]
                # Convert to ccxt-like format with /USDT suffix for filtering
                if symbol.endswith("USDT"):
                    ccxt_symbol = symbol[:-4] + "/USDT"
                    tickers[ccxt_symbol] = {
                        "symbol": ccxt_symbol,
                        "last": float(item.get("lastPrice", 0)),
                        "high": float(item.get("highPrice24h", 0)),
                        "low": float(item.get("lowPrice24h", 0)),
                        "quoteVolume": float(item.get("turnover24h", 0)),
                        "percentage": float(item.get("price24hPcnt", 0)) * 100,
                    }
        return tickers

    @retry_async(max_attempts=2)
    async def get_usdt_perpetual_symbols(self) -> list[str]:
        """
        Get all active USDT perpetual symbols from Bybit.
        Filters: status=Trading, quoteCoin=USDT, contractType=LinearPerpetual.
        Returns list of symbols in ccxt format (e.g., BTC/USDT).
        """
        result = self._client.get_instruments_info(category="linear")
        symbols = []
        if result["retCode"] == 0:
            for item in result["result"]["list"]:
                if (
                    item.get("status") == "Trading"
                    and item.get("quoteCoin") == "USDT"
                    and item.get("contractType") == "LinearPerpetual"
                ):
                    # Convert BTCUSDT -> BTC/USDT
                    symbol = item["symbol"]
                    if symbol.endswith("USDT"):
                        ccxt_symbol = symbol[:-4] + "/USDT"
                        symbols.append(ccxt_symbol)
        return sorted(symbols)

    @retry_async(max_attempts=2)
    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        """Get all open orders."""
        params: dict = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        result = self._client.get_open_orders(**params)
        orders: list[dict] = []
        if result["retCode"] == 0:
            for order in result["result"]["list"]:
                orders.append({
                    "order_id": order["orderId"],
                    "symbol": order["symbol"],
                    "side": order["side"],
                    "price": float(order.get("price", 0)),
                    "qty": float(order.get("qty", 0)),
                    "order_type": order.get("orderType"),
                    "status": order.get("orderStatus"),
                })
        return orders

    @retry_async(max_attempts=2)
    async def close_position(self, symbol: str, side: str, size: float) -> dict:
        """Close a position by placing opposite market order."""
        close_side = "Sell" if side.lower() == "buy" else "Buy"
        result = self._client.place_order(
            category="linear",
            symbol=symbol,
            side=close_side,
            orderType="Market",
            qty=str(size),
            reduceOnly=True,
        )
        return {
            "success": result["retCode"] == 0,
            "order_id": result["result"].get("orderId") if result["retCode"] == 0 else None,
            "message": result.get("retMsg"),
        }

    async def close_all_positions(self) -> dict:
        """Close ALL open positions with market orders."""
        positions = await self.get_positions()
        closed = []
        errors = []

        for pos in positions:
            try:
                result = await self.close_position(
                    symbol=pos["symbol"],
                    side=pos["side"],
                    size=pos["size"],
                )
                if result["success"]:
                    closed.append({
                        "symbol": pos["symbol"],
                        "side": pos["side"],
                        "size": pos["size"],
                        "pnl": pos.get("pnl", 0),
                    })
                else:
                    errors.append({
                        "symbol": pos["symbol"],
                        "error": result.get("message"),
                    })
            except Exception as e:
                errors.append({
                    "symbol": pos["symbol"],
                    "error": str(e),
                })

        return {
            "closed_count": len(closed),
            "closed": closed,
            "errors": errors,
            "total_pnl": sum(p.get("pnl", 0) for p in closed),
        }

    async def cancel_all_orders(self) -> dict:
        """Cancel ALL open orders."""
        orders = await self.get_open_orders()
        cancelled = []
        errors = []

        for order in orders:
            try:
                success = await self.cancel_order(
                    symbol=order["symbol"],
                    order_id=order["order_id"],
                )
                if success:
                    cancelled.append({
                        "order_id": order["order_id"],
                        "symbol": order["symbol"],
                    })
                else:
                    errors.append({
                        "order_id": order["order_id"],
                        "symbol": order["symbol"],
                        "error": "Cancel failed",
                    })
            except Exception as e:
                errors.append({
                    "order_id": order["order_id"],
                    "symbol": order["symbol"],
                    "error": str(e),
                })

        return {
            "cancelled_count": len(cancelled),
            "cancelled": cancelled,
            "errors": errors,
        }
