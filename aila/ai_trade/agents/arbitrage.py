"""ARBITRAGE — finds arbitrage opportunities across exchanges and instruments."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from ...utils.common import TTLCache, retry_async
from .base_agent import BaseAgent

logger = logging.getLogger("ai_trade")


class ArbitrageAgent(BaseAgent):
    """Finds and analyzes arbitrage opportunities: funding, cross-exchange, triangular."""

    __slots__ = ("_cache", "_min_profit_pct", "_exchanges_manager")

    def __init__(
        self, claude_client: Any, knowledge_base: Any, exchanges_manager: Any = None
    ) -> None:
        super().__init__(
            name="ARBITRAGE",
            claude_client=claude_client,
            knowledge_base=knowledge_base,
            log_path="/opt/aila/logs/ai_trade/arbitrage.log",
        )
        self._cache = TTLCache(default_ttl=5.0)
        self._min_profit_pct = 0.1  # Minimum 0.1% net profit
        self._exchanges_manager = exchanges_manager

    async def think(self, context: dict[str, Any]) -> dict[str, Any]:
        """Process arbitrage context."""
        pairs = context.get("pairs", [])
        if not pairs:
            return {"opportunities": {}}
        return await self.find_all_opportunities(pairs)

    async def find_all_opportunities(self, pairs: list[str]) -> dict[str, Any]:
        """Find all arbitrage opportunities in parallel."""
        results = await asyncio.gather(
            self.find_funding_arbitrage(pairs),
            self.find_cross_exchange_arbitrage(pairs[:10]),
            self.find_triangular_arbitrage(),
            return_exceptions=True,
        )

        def _safe(idx: int) -> list:
            return results[idx] if not isinstance(results[idx], Exception) else []

        return {
            "funding": _safe(0),
            "cross_exchange": _safe(1),
            "triangular": _safe(2),
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def find_funding_arbitrage(self, pairs: list[str]) -> list[dict[str, Any]]:
        """
        Funding rate arbitrage.
        Long spot + Short perp when funding is high (positive).
        Long perp + Short spot when funding is negative.
        Example: Funding +0.1% per 8h = 0.3%/day = ~9%/month risk-free.
        """
        tasks = [self._analyze_funding(pair) for pair in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        opportunities: list[dict[str, Any]] = []
        for pair, result in zip(pairs, results):
            if isinstance(result, Exception):
                continue
            if not result or abs(result.get("funding_rate", 0)) < 0.05:
                continue

            rate = result["funding_rate"]
            opportunity: dict[str, Any] = {
                "type": "funding_arbitrage",
                "pair": pair,
                "funding_rate": rate,
                "annual_yield": rate * 3 * 365,  # 3 funding periods per day
                "direction": "short_perp" if rate > 0 else "long_perp",
                "strategy": self._get_funding_strategy(rate),
                "risk_level": "low",
                "mark_price": result.get("mark_price", 0),
                "next_funding_time": result.get("next_funding_time"),
                "timestamp": datetime.utcnow().isoformat(),
            }
            opportunities.append(opportunity)
            self.log(f"Funding arb: {pair} rate={rate:.4f}%", "info")

        return sorted(opportunities, key=lambda x: abs(x["funding_rate"]), reverse=True)

    async def find_cross_exchange_arbitrage(
        self, pairs: list[str]
    ) -> list[dict[str, Any]]:
        """
        Cross-exchange arbitrage.
        Buy cheaper on one exchange, sell higher on another.
        """
        if not self._exchanges_manager:
            return []

        opportunities: list[dict[str, Any]] = []
        for pair in pairs:
            prices = await self._exchanges_manager.get_all_prices(pair)
            if len(prices) < 2:
                continue

            min_ex = min(prices, key=lambda x: x["price"])
            max_ex = max(prices, key=lambda x: x["price"])
            spread_pct = (max_ex["price"] - min_ex["price"]) / min_ex["price"] * 100

            # Account for fees (~0.1% per exchange)
            net_profit = spread_pct - 0.2

            if net_profit >= self._min_profit_pct:
                opportunity: dict[str, Any] = {
                    "type": "cross_exchange",
                    "pair": pair,
                    "buy_exchange": min_ex["exchange"],
                    "buy_price": min_ex["price"],
                    "sell_exchange": max_ex["exchange"],
                    "sell_price": max_ex["price"],
                    "gross_spread_pct": round(spread_pct, 4),
                    "net_profit_pct": round(net_profit, 4),
                    "risk_level": "medium",  # Transfer time risk
                    "timestamp": datetime.utcnow().isoformat(),
                }
                opportunities.append(opportunity)
                self.log(f"Cross-exchange arb: {pair} net={net_profit:.2f}%", "info")

        return sorted(opportunities, key=lambda x: x["net_profit_pct"], reverse=True)

    async def find_triangular_arbitrage(
        self, base: str = "USDT"
    ) -> list[dict[str, Any]]:
        """
        Triangular arbitrage.
        USDT -> BTC -> ETH -> USDT with profit.
        """
        if not self._exchanges_manager:
            return []

        triangles = [
            ["BTC", "ETH", base],
            ["BTC", "SOL", base],
            ["ETH", "SOL", base],
            ["BTC", "XRP", base],
        ]

        opportunities: list[dict[str, Any]] = []
        for triangle in triangles:
            result = await self._calculate_triangle(triangle, base)
            if result and result.get("profit_pct", 0) > self._min_profit_pct:
                opportunities.append(result)
                self.log(
                    f"Triangle arb: {' -> '.join(triangle)} "
                    f"profit={result['profit_pct']:.2f}%",
                    "info",
                )

        return opportunities

    @retry_async(max_attempts=2)
    async def _analyze_funding(self, pair: str) -> dict[str, Any]:
        """Analyze funding rate for a pair."""
        cache_key = f"funding:{pair}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            from pybit.unified_trading import HTTP

            client = HTTP()
            result = client.get_tickers(category="linear", symbol=pair)

            if result["retCode"] == 0 and result["result"]["list"]:
                ticker = result["result"]["list"][0]
                data: dict[str, Any] = {
                    "pair": pair,
                    "funding_rate": float(ticker.get("fundingRate", 0)) * 100,
                    "next_funding_time": ticker.get("nextFundingTime"),
                    "mark_price": float(ticker.get("markPrice", 0)),
                }
                self._cache.set(cache_key, data)
                return data
        except Exception as e:
            self.log(f"Funding check failed for {pair}: {e}", "error")

        return {}

    async def _calculate_triangle(
        self, triangle: list[str], base: str
    ) -> Optional[dict[str, Any]]:
        """Calculate triangular arbitrage profit."""
        if not self._exchanges_manager:
            return None

        primary = self._exchanges_manager.primary
        if not primary:
            return None

        try:
            # Get prices for all legs
            pair_a = f"{triangle[0]}{base}"  # e.g. BTCUSDT
            pair_b = f"{triangle[1]}{base}"  # e.g. ETHUSDT
            pair_c = f"{triangle[1]}{triangle[0]}"  # e.g. ETHBTC

            ticker_a, ticker_b, ticker_c = await asyncio.gather(
                primary.get_ticker(pair_a),
                primary.get_ticker(pair_b),
                primary.get_ticker(pair_c),
                return_exceptions=True,
            )

            if any(isinstance(t, Exception) or not t for t in [ticker_a, ticker_b, ticker_c]):
                return None

            price_a = ticker_a["price"]  # BTC/USDT
            price_b = ticker_b["price"]  # ETH/USDT
            price_c = ticker_c["price"]  # ETH/BTC

            # Path: USDT -> BTC -> ETH -> USDT
            # 1. Buy BTC with USDT
            btc_amount = 1.0 / price_a
            # 2. Buy ETH with BTC (using cross pair)
            eth_amount = btc_amount / price_c
            # 3. Sell ETH for USDT
            final_usdt = eth_amount * price_b

            # Account for fees (0.1% per trade * 3 trades)
            profit_pct = (final_usdt - 1.0) * 100 - 0.3

            if profit_pct > 0:
                return {
                    "type": "triangular",
                    "path": f"{base} -> {triangle[0]} -> {triangle[1]} -> {base}",
                    "legs": [
                        {"pair": pair_a, "action": "buy", "price": price_a},
                        {"pair": pair_c, "action": "buy", "price": price_c},
                        {"pair": pair_b, "action": "sell", "price": price_b},
                    ],
                    "profit_pct": round(profit_pct, 4),
                    "risk_level": "low",
                    "timestamp": datetime.utcnow().isoformat(),
                }
        except Exception as e:
            self.log(f"Triangle calc error {triangle}: {e}", "error")

        return None

    def _get_funding_strategy(self, rate: float) -> str:
        """Get strategy description for funding arbitrage."""
        if rate > 0.1:
            return "Strong short perp + long spot"
        elif rate > 0.05:
            return "Short perp + long spot"
        elif rate < -0.1:
            return "Strong long perp + short spot"
        elif rate < -0.05:
            return "Long perp + short spot"
        return "No action"
