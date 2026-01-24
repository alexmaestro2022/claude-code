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
        result = self._client.get_tickers(category="linear", symbol=symbol)
        if result["retCode"] == 0 and result["result"]["list"]:
            ticker = result["result"]["list"][0]
            return {
                "symbol": symbol,
                "price": float(ticker["lastPrice"]),
                "bid": float(ticker.get("bid1Price", 0)),
                "ask": float(ticker.get("ask1Price", 0)),
                "volume_24h": float(ticker.get("volume24h", 0)),
            }
        return {}

    @retry_async(max_attempts=2)
    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """Get order book."""
        result = self._client.get_orderbook(
            category="linear", symbol=symbol, limit=limit
        )
        if result["retCode"] == 0:
            return {
                "bids": [(float(b[0]), float(b[1])) for b in result["result"]["b"]],
                "asks": [(float(a[0]), float(a[1])) for a in result["result"]["a"]],
            }
        return {"bids": [], "asks": []}

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
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order."""
        result = self._client.cancel_order(
            category="linear", symbol=symbol, orderId=order_id
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
