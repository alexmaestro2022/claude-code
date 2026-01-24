"""Multi-exchange manager — aggregates data from multiple exchanges."""

import asyncio
import logging
from typing import Any, Optional

from ...utils.common import TTLCache
from .base_exchange import BaseExchange

logger = logging.getLogger("ai_trade.multi_exchange")


class MultiExchangeManager:
    """Manages multiple exchanges for price comparison and arbitrage."""

    __slots__ = ("_exchanges", "_cache", "_primary")

    def __init__(self, primary: str = "bybit") -> None:
        self._exchanges: dict[str, BaseExchange] = {}
        self._cache = TTLCache(default_ttl=5.0)
        self._primary = primary

    def add_exchange(self, exchange: BaseExchange) -> None:
        """Add an exchange."""
        self._exchanges[exchange.name] = exchange

    def get_exchange(self, name: str) -> Optional[BaseExchange]:
        """Get exchange by name."""
        return self._exchanges.get(name)

    @property
    def primary(self) -> Optional[BaseExchange]:
        """Primary exchange."""
        return self._exchanges.get(self._primary)

    @property
    def count(self) -> int:
        """Number of connected exchanges."""
        return len(self._exchanges)

    async def get_best_price(self, symbol: str, side: str) -> dict[str, Any]:
        """Find best price across all exchanges. side: 'buy' or 'sell'."""
        cache_key = f"best:{symbol}:{side}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        tasks = [
            self._get_price_safe(name, ex, symbol)
            for name, ex in self._exchanges.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        prices = [r for r in results if isinstance(r, dict) and r]

        if not prices:
            return {}

        best = min(prices, key=lambda x: x["price"]) if side == "buy" \
            else max(prices, key=lambda x: x["price"])

        self._cache.set(cache_key, best)
        return best

    async def get_all_prices(self, symbol: str) -> list[dict[str, Any]]:
        """Get prices from all exchanges."""
        tasks = [
            self._get_price_safe(name, ex, symbol)
            for name, ex in self._exchanges.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, dict) and r]

    async def get_total_balance(self, currency: str = "USDT") -> dict[str, Any]:
        """Get total balance across all exchanges."""
        total = 0.0
        breakdown: dict[str, float] = {}

        for name, exchange in self._exchanges.items():
            try:
                balance = await exchange.get_balance(currency)
                breakdown[name] = balance
                total += balance
            except Exception:
                breakdown[name] = 0.0

        return {"total": total, "breakdown": breakdown}

    async def _get_price_safe(
        self, name: str, exchange: BaseExchange, symbol: str
    ) -> Optional[dict[str, Any]]:
        """Safely get price from exchange."""
        try:
            ticker = await exchange.get_ticker(symbol)
            if ticker:
                return {
                    "exchange": name,
                    "price": ticker["price"],
                    "bid": ticker.get("bid"),
                    "ask": ticker.get("ask"),
                }
        except Exception as e:
            logger.debug(f"Price fetch failed for {name}/{symbol}: {e}")
        return None
