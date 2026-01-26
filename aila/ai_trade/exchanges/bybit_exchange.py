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

    @retry_async(max_attempts=2)
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "15", limit: int = 100
    ) -> list[list]:
        """Fetch OHLCV candle data (ccxt-compatible format)."""
        # Convert timeframe to Bybit interval format
        interval_map = {
            "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
            "1d": "D", "1w": "W", "1M": "M",
        }
        interval = interval_map.get(timeframe, timeframe)

        result = self._client.get_kline(
            category="linear",
            symbol=symbol,
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
